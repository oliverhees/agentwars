"""Zentrale Konfiguration: Agenten-Team, Modelle, Env."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


# ---------------------------------------------------------------- Provider
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY")
# "claude-code" (Default, nutzt deine Subscription wenn die CLI da ist)
# oder "api" (erzwingt die Anthropic-API für Claude)
CLAUDE_TRANSPORT = env("CLAUDE_TRANSPORT", "claude-code")
OPENAI_API_KEY = env("OPENAI_API_KEY")

# HostYourAI EU-Router (OpenAI-kompatibel)
HYAI_BASE_URL = env("HYAI_BASE_URL", "https://hostyourai.com/api/v1")
HYAI_API_KEY = env("HYAI_API_KEY")

# Optional: TencentDB Agent Memory Proxy davorschalten (OpenAI-Protokoll).
# Wenn gesetzt, laufen die HostYourAI-Agenten durch den Memory-Proxy.
MEMORY_PROXY_BASE_URL = env("MEMORY_PROXY_BASE_URL")

# ---------------------------------------------------------------- Plane
PLANE_BASE_URL = env("PLANE_BASE_URL")          # z. B. https://plane.deine-domain.de
PLANE_API_KEY = env("PLANE_API_KEY")            # Personal API Token (X-API-Key)
PLANE_WORKSPACE = env("PLANE_WORKSPACE")        # Workspace-Slug
PLANE_PROJECT_ID = env("PLANE_PROJECT_ID")      # Projekt-UUID

# ---------------------------------------------------------------- Limits
CONTEXT_CHAR_BUDGET = int(env("CONTEXT_CHAR_BUDGET", "160000"))
MAX_TOKENS_REVIEW = int(env("MAX_TOKENS_REVIEW", "3000"))
MAX_TOKENS_CHAIRMAN = int(env("MAX_TOKENS_CHAIRMAN", "6000"))


# ---------------------------------------------------------------- Agenten
@dataclass
class AgentSpec:
    id: str
    name: str
    tagline: str          # kurze Rollen-Beschreibung fürs UI
    color: str            # UI-Farbe
    model: str            # LiteLLM-Modellstring
    api_key: str = ""
    api_base: str | None = None
    system_prompt: str = ""
    is_dev: bool = False  # nimmt am Kreuzverhör teil
    extra: dict = field(default_factory=dict)


def _hyai_base() -> str:
    return MEMORY_PROXY_BASE_URL or HYAI_BASE_URL


def build_team() -> dict[str, AgentSpec]:
    """Das Board. Modellnamen kommen aus der .env, damit du sie ohne
    Code-Änderung an den HostYourAI Model Garden anpassen kannst."""
    common = (
        "Du bist Teil eines KI-Review-Boards, das ein Software-Projekt "
        "gnadenlos ehrlich analysiert, damit es maximal erfolgreich wird. "
        "Antworte auf Deutsch. Sei konkret: nenne Dateien, Zeilen, Muster. "
        "Keine Höflichkeitsfloskeln, kein Weichspülen. Struktur: "
        "1) Stärken (kurz) 2) Kritische Schwächen (ausführlich, priorisiert) "
        "3) Konkrete Verbesserungen (umsetzbar formuliert)."
    )
    from .mentions import MENTION_RULES
    common += MENTION_RULES
    team = {
        "claude": AgentSpec(
            id="claude", name="Claude", tagline="Senior Dev · Anthropic",
            color="#8B7CF6",
            model=f"anthropic/{env('ANTHROPIC_MODEL', 'claude-sonnet-4-6')}",
            api_key=ANTHROPIC_API_KEY, is_dev=True,
            system_prompt=common + " Dein Fokus: Code-Qualität, Wartbarkeit, "
            "Architektur-Entscheidungen und ob das Projekt hält, was es verspricht.",
        ),
        "gpt": AgentSpec(
            id="gpt", name="GPT", tagline="Senior Dev · OpenAI",
            color="#4FB6A2",
            model=f"openai/{env('OPENAI_MODEL', 'gpt-5.2')}",
            api_key=OPENAI_API_KEY, is_dev=True,
            system_prompt=common + " Dein Fokus: Robustheit, Edge-Cases, "
            "Fehlerbehandlung, Testbarkeit und Developer Experience.",
        ),
        "kimi": AgentSpec(
            id="kimi", name="Kimi", tagline="Senior Dev · Moonshot",
            color="#E8618C",
            model=f"openai/{env('HYAI_MODEL_KIMI', 'kimi-k3')}",
            api_key=HYAI_API_KEY, api_base=_hyai_base(), is_dev=True,
            system_prompt=common + " Dein Fokus: Long-Horizon-Sicht auf die "
            "Codebasis, Performance und ob die Struktur skalierbar ist.",
        ),
        "qwen": AgentSpec(
            id="qwen", name="Qwen", tagline="Architektur & Tooling",
            color="#5A9CF8",
            model=f"openai/{env('HYAI_MODEL_QWEN', 'qwen3.5')}",
            api_key=HYAI_API_KEY, api_base=_hyai_base(),
            system_prompt=common + " Dein Fokus: Systemarchitektur, "
            "Abhängigkeiten, Deployment, Tool- und API-Design.",
        ),
        "deepseek": AgentSpec(
            id="deepseek", name="DeepSeek", tagline="Security & Reasoning",
            color="#E5A445",
            model=f"openai/{env('HYAI_MODEL_DEEPSEEK', 'deepseek-v4-pro')}",
            api_key=HYAI_API_KEY, api_base=_hyai_base(),
            system_prompt=common + " Dein Fokus: Sicherheit, Datenschutz, "
            "Secrets-Handling, Auth, Input-Validierung und logische Lücken.",
        ),
        "glm": AgentSpec(
            id="glm", name="GLM", tagline="Devil's Advocate",
            color="#C75B5B",
            model=f"openai/{env('HYAI_MODEL_GLM', 'glm-5.2')}",
            api_key=HYAI_API_KEY, api_base=_hyai_base(),
            system_prompt=common + " Deine Rolle: Devil's Advocate. Greif die "
            "Grundannahmen des Projekts an: Braucht das jemand? Was killt es am "
            "Markt? Wo lügt sich der Gründer in die Tasche? Sei unbequem, aber fair.",
        ),
    }
    return team


CHAIRMAN_ID = "claude"

PHASES = [
    {"id": "briefing", "label": "Briefing"},
    {"id": "gutachten", "label": "Einzelgutachten"},
    {"id": "kreuzverhoer", "label": "Kreuzverhör"},
    {"id": "synthese", "label": "Chairman-Synthese"},
    {"id": "plane", "label": "Plane-Sync"},
]
