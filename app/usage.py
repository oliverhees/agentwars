"""Verbrauch und Kosten: wer hat wie viele Token verbrannt und was kostet das?

Drei Quellen, unterschiedlich genau – und das steht auch so im Dashboard:

1. **Claude Code** meldet Tokenzahlen und Kosten selbst im result-Event. Das
   ist exakt. Bezahlt wird es trotzdem über deine Subscription, deshalb läuft
   es als "im Abo" und nicht als Rechnungsposten.
2. **API-Modelle über LiteLLM** liefern meist eine Usage-Angabe mit. Preise
   rechnet LiteLLM für bekannte Modelle selbst aus.
3. **Router-Modelle** (HostYourAI & Co.) kennt LiteLLM preislich nicht. Dafür
   hinterlegst du die Preise unter /settings – ohne sie zählen wir Token, aber
   keine Kosten, und sagen das auch.
"""
import asyncio
import re
import time

from . import settings, store

# "kimi-k3 = 0.30 / 1.20"  → 0,30 USD je 1M Eingabe-, 1,20 je 1M Ausgabetoken
PRICE_LINE = re.compile(
    r"^\s*([^=\s]+)\s*=\s*([0-9.]+)\s*[/,]\s*([0-9.]+)\s*$")


def price_table() -> dict[str, tuple[float, float]]:
    table: dict[str, tuple[float, float]] = {}
    for line in settings.get("model_prices").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = PRICE_LINE.match(line)
        if match:
            table[match.group(1).lower()] = (float(match.group(2)),
                                             float(match.group(3)))
    return table


def _bare(model: str) -> str:
    return model.split("/", 1)[1] if "/" in model else model


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """USD für diesen Aufruf. Eigene Preise schlagen die von LiteLLM, weil
    der Router andere Konditionen hat als der Modellhersteller."""
    eigene = price_table().get(_bare(model).lower())
    if eigene:
        rein, raus = eigene
        return input_tokens / 1_000_000 * rein + output_tokens / 1_000_000 * raus
    try:
        import litellm
        rein, raus = litellm.cost_per_token(
            model=model, prompt_tokens=input_tokens,
            completion_tokens=output_tokens)
        return float(rein or 0) + float(raus or 0)
    except Exception:
        # Unbekanntes Modell: lieber 0 als eine erfundene Zahl. Das Dashboard
        # weist Modelle ohne Preis ausdrücklich aus.
        return 0.0


def eur(amount_usd: float) -> float:
    try:
        rate = float(settings.get("eur_per_usd") or 0.92)
    except ValueError:
        rate = 0.92
    return amount_usd * rate


# ---------------------------------------------------------------- Schreiben
def _record(row: dict) -> None:
    conn = store.connect()
    conn.execute(
        "INSERT INTO usage (ts, project_id, meeting_id, agent_id, agent_name,"
        " phase, provider, model, input_tokens, output_tokens, cache_read,"
        " cache_write, cost_usd, billed, estimated)"
        " VALUES (:ts, :project_id, :meeting_id, :agent_id, :agent_name,"
        " :phase, :provider, :model, :input_tokens, :output_tokens,"
        " :cache_read, :cache_write, :cost_usd, :billed, :estimated)", row)
    conn.commit()


async def record(*, project_id: str, meeting_id: str, agent_id: str,
                 agent_name: str, phase: str, provider: str, model: str,
                 input_tokens: int, output_tokens: int, cache_read: int = 0,
                 cache_write: int = 0, cost_usd: float | None = None,
                 estimated: bool = False) -> None:
    if cost_usd is None:
        cost_usd = compute_cost(model, input_tokens, output_tokens)
    row = {
        "ts": time.time(), "project_id": project_id or "",
        "meeting_id": meeting_id or "", "agent_id": agent_id,
        "agent_name": agent_name, "phase": phase, "provider": provider,
        "model": _bare(model), "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens), "cache_read": int(cache_read),
        "cache_write": int(cache_write), "cost_usd": float(cost_usd),
        # Claude Code läuft über die Subscription – die Kosten sind echt
        # gerechnet, aber sie stehen auf keiner zusätzlichen Rechnung.
        "billed": 0 if provider == "claude-code" else 1,
        "estimated": 1 if estimated else 0,
    }
    try:
        await asyncio.to_thread(_record, row)
    except Exception:
        pass  # Buchhaltung darf niemals ein Meeting killen


# ---------------------------------------------------------------- Lesen
SUMS = ("SUM(input_tokens) AS input_tokens,"
        " SUM(output_tokens) AS output_tokens,"
        " SUM(cache_read) AS cache_read, SUM(cache_write) AS cache_write,"
        " SUM(CASE WHEN billed = 1 THEN cost_usd ELSE 0 END) AS billed_usd,"
        " SUM(CASE WHEN billed = 0 THEN cost_usd ELSE 0 END) AS included_usd,"
        " MAX(estimated) AS has_estimates, COUNT(*) AS calls")


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in store.connect().execute(sql, params).fetchall()]


def _projekt_kennzahlen(project_id: str) -> dict:
    """Was dieses Projekt bisher gekostet hat – in Zeit, Umfang und Geld."""
    zeile = _rows(
        "SELECT COUNT(*) AS meetings,"
        " COALESCE(SUM(CASE WHEN ended_at IS NOT NULL"
        "   THEN ended_at - started_at ELSE 0 END), 0) AS seconds,"
        " COALESCE(SUM(tickets), 0) AS tickets,"
        " MIN(started_at) AS first_meeting, MAX(started_at) AS last_meeting"
        " FROM meetings WHERE project_id = ?", (project_id,))[0]

    # Umfang: der jüngste gemessene Stand und der erste, für das Wachstum.
    stand = _rows(
        "SELECT repo_files, repo_lines, repo_bytes, started_at FROM meetings"
        " WHERE project_id = ? AND repo_lines > 0"
        " ORDER BY started_at DESC", (project_id,))
    zeile["repo_files"] = stand[0]["repo_files"] if stand else 0
    zeile["repo_lines"] = stand[0]["repo_lines"] if stand else 0
    zeile["repo_bytes"] = stand[0]["repo_bytes"] if stand else 0
    zeile["lines_growth"] = (stand[0]["repo_lines"] - stand[-1]["repo_lines"]
                             if len(stand) > 1 else 0)
    return zeile


def _report(project_id: str = "") -> dict:
    where = "WHERE project_id = ?" if project_id else ""
    params = (project_id,) if project_id else ()

    gesamt = _rows(f"SELECT {SUMS} FROM usage {where}", params)[0]
    je_agent = _rows(
        f"SELECT agent_id, agent_name, provider, {SUMS} FROM usage {where}"
        " GROUP BY agent_id, agent_name, provider"
        " ORDER BY billed_usd DESC, output_tokens DESC", params)
    je_modell = _rows(
        f"SELECT model, provider, {SUMS} FROM usage {where}"
        " GROUP BY model, provider ORDER BY billed_usd DESC", params)
    je_phase = _rows(
        f"SELECT phase, {SUMS} FROM usage {where}"
        " GROUP BY phase ORDER BY billed_usd DESC", params)

    if project_id:
        je_projekt = []
        meetings = _rows(
            "SELECT m.id, m.started_at, m.ended_at, m.status, m.briefing,"
            " m.repo_files, m.repo_lines, m.repo_bytes, m.tickets,"
            " COALESCE(SUM(u.input_tokens), 0) AS input_tokens,"
            " COALESCE(SUM(u.output_tokens), 0) AS output_tokens,"
            " COUNT(u.seq) AS calls,"
            " COALESCE(SUM(CASE WHEN u.billed = 1 THEN u.cost_usd ELSE 0 END), 0) AS billed_usd,"
            " COALESCE(SUM(CASE WHEN u.billed = 0 THEN u.cost_usd ELSE 0 END), 0) AS included_usd"
            " FROM meetings m LEFT JOIN usage u ON u.meeting_id = m.id"
            " WHERE m.project_id = ? GROUP BY m.id"
            " ORDER BY m.started_at DESC LIMIT 50", (project_id,))
        # Dauer und Wachstum gegenüber dem vorherigen Meeting
        for i, m in enumerate(meetings):
            m["seconds"] = max(0.0, (m["ended_at"] or 0) - m["started_at"]) \
                if m["ended_at"] else 0.0
            aelter = meetings[i + 1] if i + 1 < len(meetings) else None
            m["lines_delta"] = (m["repo_lines"] - aelter["repo_lines"]
                                if aelter and aelter["repo_lines"] else None)
    else:
        je_projekt = _rows(
            f"SELECT p.id, p.name, p.repo_full_name, {SUMS} FROM usage u"
            " JOIN projects p ON p.id = u.project_id"
            " GROUP BY p.id, p.name, p.repo_full_name"
            " ORDER BY billed_usd DESC")
        meetings = []

    def mit_euro(entries: list[dict]) -> list[dict]:
        for e in entries:
            e["billed_eur"] = round(eur(e.get("billed_usd") or 0), 4)
            e["included_eur"] = round(eur(e.get("included_usd") or 0), 4)
        return entries

    gesamt = mit_euro([gesamt])[0]
    if project_id:
        gesamt.update(_projekt_kennzahlen(project_id))
    return {
        "total": gesamt,
        "by_agent": mit_euro(je_agent),
        "by_model": mit_euro(je_modell),
        "by_phase": mit_euro(je_phase),
        "by_project": mit_euro(je_projekt),
        "meetings": mit_euro(meetings),
        "priced_models": sorted(price_table()),
    }


async def report(project_id: str = "") -> dict:
    return await asyncio.to_thread(_report, project_id)
