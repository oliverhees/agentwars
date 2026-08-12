"""Welche Modelle gibt es beim jeweiligen Anbieter wirklich?

Modellnamen von Hand einzutippen geht schief – 'gpt-4.6' statt 'gpt-4.6-turbo'
und der Agent fällt mitten im Meeting aus. Sobald ein Key hinterlegt ist,
holen wir die Liste beim Anbieter selbst und bieten sie zur Auswahl an.

Anbieter, die keine Liste haben (Claude Code), bekommen die bekannten
Kurznamen als Vorschlag – frei eintippen bleibt überall möglich, weil jede
Liste veraltet sein kann.
"""
import asyncio
import re

import httpx

from . import settings
from .preflight import chat_models

# Claude Code kennt keine Modell-API. Diese Kurznamen versteht die CLI immer;
# leer bedeutet "was Claude Code selbst für richtig hält".
CLAUDE_CODE_ALIASES = ["", "opus", "sonnet", "haiku"]

# OpenAI liefert alles im selben Topf – Bild, Sprache, Embeddings.
OPENAI_NICHT_CHAT = re.compile(
    r"(embedding|whisper|tts|audio|dall-e|moderation|realtime|transcribe|"
    r"image|search|similarity|edit|davinci|babbage|curie|ada)", re.IGNORECASE)


async def _openai() -> tuple[list[str], str]:
    key = settings.get("openai_api_key")
    if not key:
        return [], ""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        return [], f"OpenAI nicht erreichbar ({exc})."
    if resp.status_code != 200:
        return [], f"OpenAI antwortet mit {resp.status_code}."
    entries = resp.json().get("data", [])
    namen = [e["id"] for e in entries
             if isinstance(e, dict) and isinstance(e.get("id"), str)
             and not OPENAI_NICHT_CHAT.search(e["id"])]
    return sorted(namen), ""


async def _anthropic() -> tuple[list[str], str]:
    key = settings.get("anthropic_api_key")
    if not key:
        return [], ""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": key,
                         "anthropic-version": "2023-06-01"})
    except httpx.HTTPError as exc:
        return [], f"Anthropic nicht erreichbar ({exc})."
    if resp.status_code != 200:
        return [], f"Anthropic antwortet mit {resp.status_code}."
    entries = resp.json().get("data", [])
    return [e["id"] for e in entries
            if isinstance(e, dict) and isinstance(e.get("id"), str)], ""


async def _hostyourai() -> tuple[list[str], str]:
    from .preflight import router_models
    katalog, fehler = await router_models()
    return chat_models(katalog), fehler


async def _claude_code() -> tuple[list[str], str]:
    """Kurznamen plus – falls ein Anthropic-Key da ist – die echten IDs."""
    namen, _ = await _anthropic()
    return CLAUDE_CODE_ALIASES + [n for n in namen if n not in CLAUDE_CODE_ALIASES], ""


QUELLEN = {
    "openai": _openai,
    "anthropic": _anthropic,
    "hostyourai": _hostyourai,
    "claude-code": _claude_code,
}


async def catalog() -> dict:
    """Für jeden Provider die Modellliste – parallel, Fehler pro Provider."""
    async def eine(name: str) -> tuple[str, dict]:
        try:
            namen, fehler = await QUELLEN[name]()
        except Exception as exc:
            return name, {"models": [], "error": str(exc)[:200]}
        return name, {"models": namen, "error": fehler}

    ergebnisse = await asyncio.gather(*[eine(name) for name in QUELLEN])
    return dict(ergebnisse)
