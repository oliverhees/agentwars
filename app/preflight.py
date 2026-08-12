"""Preflight: antwortet jedes Modell wirklich, bevor das Meeting startet?

Hintergrund: Die Modell-Slugs stehen in der .env und sind erst mal Annahmen.
Stimmt einer nicht, fällt der Agent bisher erst mitten im Meeting aus und
schreibt ein '⚠️ Ausfall' in den Chat – da hast du schon bezahlt und wartest.
Der Preflight macht pro Agent einen Vier-Token-Call und sagt vorher Bescheid.
Dazu holt er den Modellkatalog des Routers, damit du den richtigen Slug
nicht raten musst.
"""
import asyncio
import difflib
import shutil

import httpx
import litellm

from . import claude_code, settings
from .config import AgentSpec, build_team, env

PROBE_TIMEOUT = int(env("PREFLIGHT_TIMEOUT", "30"))
CHECK_ON_START = env("PREFLIGHT_ON_START", "1") == "1"


def _bare_model(model: str) -> str:
    """'openai/kimi-k3' -> 'kimi-k3' – der Router kennt nur den nackten Slug."""
    return model.split("/", 1)[1] if "/" in model else model


async def router_models() -> tuple[list[str], str]:
    """Verfügbare Slugs beim HostYourAI-Router. (Liste, Fehlertext)."""
    key = settings.get("hyai_api_key")
    if not key:
        return [], "HostYourAI API-Key ist nicht gesetzt."
    url = f"{settings.get('hyai_base_url').rstrip('/')}/models"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                url, headers={"Authorization": f"Bearer {key}"})
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:
        return [], f"Modellkatalog nicht abrufbar ({exc})."
    entries = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return [], "Modellkatalog hat ein unerwartetes Format."
    slugs = [
        e.get("id") for e in entries
        if isinstance(e, dict) and isinstance(e.get("id"), str)
    ]
    return sorted(slugs), ""


async def _probe_claude_code(deep: bool = True) -> tuple[bool, str]:
    """Ist Claude Code installiert UND verbunden?

    Die drei Stufen bauen aufeinander auf: CLI vorhanden, Token hinterlegt,
    echte Anfrage kommt durch. Nur die dritte beweist, dass die Subscription
    wirklich greift – `--version` fasst das Netz nicht an.
    """
    if not shutil.which(claude_code.CLAUDE_BIN):
        return False, f"CLI '{claude_code.CLAUDE_BIN}' nicht im PATH."
    if not settings.get("claude_code_oauth_token"):
        return False, ("Claude-Code-Token fehlt – auf deinem Rechner "
                       "'claude setup-token' ausführen und hinterlegen.")
    try:
        proc = await asyncio.create_subprocess_exec(
            claude_code.CLAUDE_BIN, "--version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
    except Exception as exc:
        return False, f"CLI nicht startbar ({exc})."
    if proc.returncode != 0:
        return False, f"CLI beendet sich mit Code {proc.returncode}."
    version = out.decode(errors="replace").strip()

    if not deep:
        return True, f"CLI installiert ({version}) – Verbindung ungeprüft."
    try:
        ok, detail = await claude_code.ping()
    except Exception as exc:
        return False, f"Verbindungstest fehlgeschlagen ({exc})."
    return ok, f"{version} · {detail}"


async def _probe_model(spec: AgentSpec) -> tuple[bool, str]:
    if not spec.api_key:
        return False, "Kein API-Key gesetzt."
    kwargs: dict = {
        "model": spec.model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 4,
        "api_key": spec.api_key,
    }
    if spec.api_base:
        kwargs["api_base"] = spec.api_base
    try:
        await asyncio.wait_for(litellm.acompletion(**kwargs),
                               timeout=PROBE_TIMEOUT)
    except asyncio.TimeoutError:
        return False, f"Keine Antwort innerhalb von {PROBE_TIMEOUT}s."
    except Exception as exc:
        return False, str(exc)[:300]
    return True, "antwortet."


async def check(deep: bool = True) -> dict:
    """Prüft alle Agenten parallel und schlägt bei falschen Slugs Alternativen vor.

    deep=False überspringt den echten Claude-Code-Call – für den automatischen
    Check vor jedem Meeting, wenn du kein Abo-Kontingent dafür ausgeben willst.
    """
    team = build_team()
    catalog, catalog_error = await router_models()

    async def one(spec: AgentSpec) -> dict:
        uses_cli = (spec.provider == "claude-code"
                    and settings.get("claude_transport") != "api")
        if uses_cli:
            ok, detail = await _probe_claude_code(deep)
            model_label = "Claude Code (Subscription)"
        else:
            ok, detail = await _probe_model(spec)
            model_label = _bare_model(spec.model)
        hint = ""
        if not ok and not uses_cli and catalog and spec.api_base:
            close = difflib.get_close_matches(
                _bare_model(spec.model), catalog, n=3, cutoff=0.4)
            if close:
                hint = "Passt vielleicht: " + ", ".join(close)
        return {"id": spec.id, "name": spec.name, "model": model_label,
                "ok": ok, "detail": detail, "hint": hint}

    agents = await asyncio.gather(*[one(s) for s in team.values()])
    return {
        "agents": list(agents),
        "router_models": catalog,
        "router_error": catalog_error,
        "ready": all(a["ok"] for a in agents),
    }
