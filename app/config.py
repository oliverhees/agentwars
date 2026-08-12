"""Zentrale Konfiguration: Provider, Standard-Team, Prompt-Bausteine.

Die *Werte* liegen zur Laufzeit in settings.py (Datenbank vor .env vor
Default). Hier stehen nur noch die Defaults und die Regeln, wie aus einem
Agenten-Datensatz ein aufrufbares Modell wird.
"""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


# ---------------------------------------------------------------- Provider
# prefix  = was LiteLLM vor den Modellnamen braucht
# key     = Settings-Schlüssel des API-Keys
# base    = Settings-Schlüssel der Base-URL (optional)
# routed  = läuft über den HostYourAI-Router, kann also durch den
#           Memory-Proxy geschleift werden
PROVIDERS = {
    "claude-code": {"label": "Claude Code (Subscription)", "prefix": "",
                    "key": "", "base": "", "routed": False},
    "anthropic": {"label": "Anthropic API", "prefix": "anthropic/",
                  "key": "anthropic_api_key", "base": "", "routed": False},
    "openai": {"label": "OpenAI API", "prefix": "openai/",
               "key": "openai_api_key", "base": "", "routed": False},
    "hostyourai": {"label": "HostYourAI Router", "prefix": "openai/",
                   "key": "hyai_api_key", "base": "hyai_base_url",
                   "routed": True},
}

DEFAULT_BASE_PROMPT = (
    "Du bist Teil eines KI-Review-Boards, das ein Software-Projekt "
    "gnadenlos ehrlich analysiert, damit es maximal erfolgreich wird. "
    "Antworte auf Deutsch. Sei konkret: nenne Dateien, Zeilen, Muster. "
    "Keine Höflichkeitsfloskeln, kein Weichspülen. Struktur: "
    "1) Stärken (kurz) 2) Kritische Schwächen (ausführlich, priorisiert) "
    "3) Konkrete Verbesserungen (umsetzbar formuliert)."
)

# {handles} wird beim Zusammenbauen durch die echten @Handles ersetzt.
DEFAULT_MENTION_RULES = (
    "Im Team-Chat kannst du Kollegen direkt ansprechen: {handles}. "
    "Nutze eine @Erwähnung NUR, wenn du von genau dieser Person eine "
    "Antwort brauchst (Widerspruch, Rückfrage, Bestätigung einer These). "
    "Maximal zwei @Erwähnungen pro Beitrag. Wirst du selbst erwähnt, "
    "antworte kurz, direkt und in der Sache."
)

# Startaufstellung. Ab dem ersten Start editierbar – die Datenbank gewinnt.
DEFAULT_AGENTS = [
    {"id": "claude", "name": "Claude", "tagline": "Senior Dev · Anthropic",
     "color": "#8B7CF6", "provider": "claude-code",
     "model": env("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
     "is_dev": 1, "is_chairman": 1,
     "system_prompt": "Dein Fokus: Code-Qualität, Wartbarkeit, "
     "Architektur-Entscheidungen und ob das Projekt hält, was es verspricht."},
    {"id": "gpt", "name": "GPT", "tagline": "Senior Dev · OpenAI",
     "color": "#4FB6A2", "provider": "openai",
     "model": env("OPENAI_MODEL", "gpt-5.2"),
     "is_dev": 1, "is_chairman": 0,
     "system_prompt": "Dein Fokus: Robustheit, Edge-Cases, "
     "Fehlerbehandlung, Testbarkeit und Developer Experience."},
    {"id": "kimi", "name": "Kimi", "tagline": "Senior Dev · Moonshot",
     "color": "#E8618C", "provider": "hostyourai",
     "model": env("HYAI_MODEL_KIMI", "kimi-k3"),
     "is_dev": 1, "is_chairman": 0,
     "system_prompt": "Dein Fokus: Long-Horizon-Sicht auf die Codebasis, "
     "Performance und ob die Struktur skalierbar ist."},
    {"id": "qwen", "name": "Qwen", "tagline": "Architektur & Tooling",
     "color": "#5A9CF8", "provider": "hostyourai",
     "model": env("HYAI_MODEL_QWEN", "qwen3.5"),
     "is_dev": 0, "is_chairman": 0,
     "system_prompt": "Dein Fokus: Systemarchitektur, Abhängigkeiten, "
     "Deployment, Tool- und API-Design."},
    {"id": "deepseek", "name": "DeepSeek", "tagline": "Security & Reasoning",
     "color": "#E5A445", "provider": "hostyourai",
     "model": env("HYAI_MODEL_DEEPSEEK", "deepseek-v4-pro"),
     "is_dev": 0, "is_chairman": 0,
     "system_prompt": "Dein Fokus: Sicherheit, Datenschutz, Secrets-Handling, "
     "Auth, Input-Validierung und logische Lücken."},
    {"id": "glm", "name": "GLM", "tagline": "Devil's Advocate",
     "color": "#C75B5B", "provider": "hostyourai",
     "model": env("HYAI_MODEL_GLM", "glm-5.2"),
     "is_dev": 0, "is_chairman": 0,
     "system_prompt": "Deine Rolle: Devil's Advocate. Greif die Grundannahmen "
     "des Projekts an: Braucht das jemand? Was killt es am Markt? Wo lügt "
     "sich der Gründer in die Tasche? Sei unbequem, aber fair."},
]

PHASES = [
    {"id": "briefing", "label": "Briefing"},
    {"id": "gutachten", "label": "Einzelgutachten"},
    {"id": "kreuzverhoer", "label": "Kreuzverhör"},
    {"id": "synthese", "label": "Chairman-Synthese"},
    {"id": "plane", "label": "Plane-Sync"},
]


@dataclass
class AgentSpec:
    id: str
    name: str
    tagline: str          # kurze Rollen-Beschreibung fürs UI
    color: str            # UI-Farbe
    model: str            # LiteLLM-Modellstring
    provider: str = "hostyourai"
    api_key: str = ""
    api_base: str | None = None
    system_prompt: str = ""
    is_dev: bool = False  # nimmt am Kreuzverhör teil
    extra: dict = field(default_factory=dict)


def build_team() -> dict[str, AgentSpec]:
    """Baut das Board aus den gespeicherten Agenten-Datensätzen."""
    from . import settings

    records = settings.active_agents()
    handles = ", ".join("@" + r["id"] for r in records)
    base = settings.get("base_prompt").strip()
    rules = settings.get("mention_rules").strip().replace("{handles}", handles)
    memory_proxy = settings.get("memory_proxy_base_url")

    team: dict[str, AgentSpec] = {}
    for record in records:
        provider = PROVIDERS.get(record["provider"], PROVIDERS["hostyourai"])
        api_base = settings.get(provider["base"]) if provider["base"] else ""
        if provider["routed"] and memory_proxy:
            api_base = memory_proxy
        prompt = "\n\n".join(part for part in
                             (base, record["system_prompt"].strip(), rules)
                             if part)
        team[record["id"]] = AgentSpec(
            id=record["id"], name=record["name"], tagline=record["tagline"],
            color=record["color"], provider=record["provider"],
            model=provider["prefix"] + record["model"],
            api_key=settings.get(provider["key"]) if provider["key"] else "",
            api_base=api_base or None,
            system_prompt=prompt,
            is_dev=bool(record["is_dev"]),
        )
    return team
