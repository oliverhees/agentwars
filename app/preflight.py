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
import re
import shutil

import httpx
import litellm

from . import claude_code, settings
from .config import AgentSpec, build_team, env

PROBE_TIMEOUT = int(env("PREFLIGHT_TIMEOUT", "30"))
CHECK_ON_START = env("PREFLIGHT_ON_START", "1") == "1"


def _bare_model(model: str) -> str:
    """'openai/kimi-k3' -> 'kimi-k3' – der Präfix ist nur für LiteLLM."""
    return model.split("/", 1)[1] if "/" in model else model


# Der Katalog des Routers enthält alles: Sprachmodelle, Embeddings, Whisper,
# Bildgeneratoren, Codecs. Für die Modellauswahl im Board ist davon nur ein
# Bruchteil brauchbar – der Rest macht die Liste unlesbar.
NICHT_CHAT = re.compile(
    r"(embed|rerank|whisper|/tts|-tts|tts-|asr|speech|audio|codec|voice|"
    r"kokoro|jukebox|parakeet|canary|diffusion|flux|sdxl|sd-turbo|stable-|"
    r"cogvideo|cogview|wan2|qwen-image|-image$|/glm-image|imagereward|"
    r"clip|siglip|radio|tokenizer|ocr|parse|docling|guard|tapas|reformer|"
    r"segvol|/bge-|onnx|gguf|-webnn|litert)", re.IGNORECASE)


def chat_models(catalog: list[str]) -> list[str]:
    """Katalog auf plausible Chat-Modelle eindampfen."""
    return [slug for slug in catalog if not NICHT_CHAT.search(slug)]


def suggest(wanted: str, catalog: list[str], n: int = 3) -> list[str]:
    """Passende Slugs zu einem Wunschmodell finden.

    Reine Zeichenähnlichkeit (difflib) schlägt hier fehl: für 'kimi-k3' kam
    'nvidia/DAM-3B' vor 'moonshotai/Kimi-K3'. Deshalb wird auf den
    Modellnamen ohne Anbieter und ohne Sonderzeichen verglichen, und exakte
    Treffer sowie Präfixe gewinnen gegen bloße Ähnlichkeit.
    """
    def norm(text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", text.lower())

    ziel = norm(_bare_model(wanted))
    if not ziel:
        return []
    bewertet = []
    for slug in chat_models(catalog):
        basis = norm(slug.split("/")[-1])
        if basis == ziel:
            punkte = 100.0
        elif basis.startswith(ziel) or ziel.startswith(basis):
            # 'qwen35' trifft 'qwen3527b' – je kleiner der Rest, desto besser
            punkte = 90 - min(abs(len(basis) - len(ziel)), 20)
        elif ziel in basis or basis in ziel:
            punkte = 70.0
        else:
            punkte = difflib.SequenceMatcher(None, ziel, basis).ratio() * 60
        bewertet.append((punkte, slug))
    bewertet.sort(key=lambda eintrag: (-eintrag[0], len(eintrag[1])))
    return [slug for punkte, slug in bewertet[:n] if punkte >= 50]


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
            close = suggest(spec.model, catalog)
            if close:
                hint = "Probier: " + ", ".join(close)
        return {"id": spec.id, "name": spec.name, "model": model_label,
                "ok": ok, "detail": detail, "hint": hint}

    agents = await asyncio.gather(*[one(s) for s in team.values()])
    brauchbar = chat_models(catalog)
    return {
        "agents": list(agents),
        "router_models": brauchbar,
        "router_total": len(catalog),
        "router_error": catalog_error,
        "ready": all(a["ok"] for a in agents),
    }
