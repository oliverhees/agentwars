"""Einstellungen zur Laufzeit – Agenten, Prompts, Modelle, Integrationen.

Alles, was vorher nur in der `.env` stand, ist jetzt im System einstellbar:
jeder Agent hat sein eigenes Modell und seinen eigenen Prompt, die
Grundregeln stehen als eigener Text daneben, und Plane, GitHub und Coolify
werden auf der Einstellungsseite hinterlegt.

Reihenfolge der Wahrheit: **Datenbank vor .env vor Default**. Bestehende
Deployments laufen also unverändert weiter, bis jemand einen Wert im UI
überschreibt.

Geheimnisse (Tokens, Keys) verlassen den Server nie im Klartext – die API
liefert nur "gesetzt: ja/nein" zurück.
"""
import asyncio
import threading

from . import store
from .config import (DEFAULT_AGENTS, DEFAULT_BASE_PROMPT,
                     DEFAULT_MENTION_RULES, DEFAULT_MODE_EXISTING,
                     DEFAULT_MODE_GREENFIELD, DEFAULT_PHASE_CHAIRMAN,
                     DEFAULT_PHASE_CROSS, DEFAULT_PHASE_REVIEW, env)

SECRET = "secret"


def _spec(key, env_key, label, group, kind="text", default="", help_=""):
    return {"key": key, "env": env_key, "label": label, "group": group,
            "kind": kind, "default": default, "help": help_}


# Die Einstellungsseite wird aus dieser Liste generiert.
SETTINGS_SPEC = [
    # ---- Zugänge
    _spec("claude_transport", "CLAUDE_TRANSPORT", "Claude-Transport", "Zugänge",
          "text", "claude-code",
          "claude-code nutzt deine Subscription, api zahlt pro Token."),
    _spec("claude_code_oauth_token", "CLAUDE_CODE_OAUTH_TOKEN",
          "Claude-Code-Token", "Zugänge", SECRET, "",
          "Auf deinem Rechner: claude setup-token"),
    _spec("claude_code_model", "CLAUDE_CODE_MODEL", "Claude-Code-Modell",
          "Zugänge", "text", "", "Leer = Standardmodell der CLI."),
    _spec("anthropic_api_key", "ANTHROPIC_API_KEY", "Anthropic API-Key",
          "Zugänge", SECRET, "", "Nur als Fallback nötig."),
    _spec("openai_api_key", "OPENAI_API_KEY", "OpenAI API-Key",
          "Zugänge", SECRET),
    _spec("hyai_api_key", "HYAI_API_KEY", "HostYourAI API-Key",
          "Zugänge", SECRET),
    _spec("hyai_base_url", "HYAI_BASE_URL", "HostYourAI Base-URL",
          "Zugänge", "text", "https://hostyourai.com/api/v1"),
    _spec("memory_proxy_base_url", "MEMORY_PROXY_BASE_URL",
          "Memory-Proxy Base-URL", "Zugänge", "text", "",
          "Optional: TencentDB Agent Memory vor die Router-Modelle schalten."),

    # ---- GitHub
    _spec("github_token", "GITHUB_TOKEN", "GitHub Token", "GitHub", SECRET,
          "", "Personal Access Token mit Scope 'repo'."),
    _spec("github_api", "GITHUB_API", "GitHub API-URL", "GitHub", "text",
          "https://api.github.com", "Nur für GitHub Enterprise ändern."),
    _spec("github_host", "GITHUB_HOST", "GitHub Host", "GitHub", "text",
          "github.com"),

    # ---- Tickets
    _spec("ticket_target", "TICKET_TARGET", "Tickets anlegen in", "Tickets",
          "text", "plane",
          "plane · github · both · off. Plane ist Standard, weil die "
          "Planungsdaten dann auf deiner Instanz bleiben."),

    # ---- Plane
    _spec("plane_base_url", "PLANE_BASE_URL", "Plane Base-URL", "Plane",
          "text", "", "z. B. https://plane.deine-domain.de"),
    _spec("plane_api_key", "PLANE_API_KEY", "Plane API-Key", "Plane", SECRET,
          "", "Plane → Profil-Einstellungen → API Tokens"),
    _spec("plane_workspace", "PLANE_WORKSPACE", "Workspace-Slug", "Plane",
          "text", "", "Der Teil aus der URL: plane.dev/DEIN-SLUG/…"),
    _spec("plane_project_id", "PLANE_PROJECT_ID", "Rückfall-Projekt (UUID)",
          "Plane", "text", "",
          "Wird nur benutzt, wenn ein Boardroom-Projekt kein eigenes "
          "Plane-Projekt hat. Normalerweise leer lassen."),

    # ---- Coolify
    _spec("coolify_base_url", "COOLIFY_BASE_URL", "Coolify Base-URL",
          "Coolify", "text", "", "z. B. https://coolify.deine-domain.de"),
    _spec("coolify_token", "COOLIFY_TOKEN", "Coolify API-Token", "Coolify",
          SECRET, "", "Coolify → Keys & Tokens → API tokens"),

    # ---- Prompts
    _spec("base_prompt", "", "Grundregeln für alle Agenten", "Prompts",
          "textarea", DEFAULT_BASE_PROMPT,
          "Steht vor jedem Agenten-Prompt. Hier wird der Ton gesetzt."),
    _spec("mode_existing", "", "Lage: bestehendes Projekt", "Prompts",
          "textarea", DEFAULT_MODE_EXISTING,
          "Wird eingesetzt, wenn das Repo Code enthält – dann wird geprüft."),
    _spec("mode_greenfield", "", "Lage: grüne Wiese", "Prompts",
          "textarea", DEFAULT_MODE_GREENFIELD,
          "Wird eingesetzt, wenn das Repo leer ist – dann wird entworfen."),
    _spec("phase_review", "", "Phase 1: Einzelbeiträge", "Prompts",
          "textarea", DEFAULT_PHASE_REVIEW),
    _spec("phase_cross", "", "Phase 2: Kreuzverhör", "Prompts",
          "textarea", DEFAULT_PHASE_CROSS),
    _spec("phase_chairman", "", "Phase 3: Chairman-Synthese", "Prompts",
          "textarea", DEFAULT_PHASE_CHAIRMAN,
          "Das JSON-Format für die Tickets hängt das System selbst an – "
          "das kannst du hier nicht kaputt machen."),
    _spec("mention_rules", "", "Regeln für @Erwähnungen", "Prompts",
          "textarea", DEFAULT_MENTION_RULES,
          "Wird automatisch angehängt. Die Handles setzt das System ein."),

    # ---- Limits
    _spec("context_char_budget", "CONTEXT_CHAR_BUDGET", "Kontextbudget (Zeichen)",
          "Limits", "number", "160000"),
    _spec("max_tokens_review", "MAX_TOKENS_REVIEW", "Tokens pro Gutachten",
          "Limits", "number", "3000"),
    _spec("max_tokens_chairman", "MAX_TOKENS_CHAIRMAN", "Tokens für die Synthese",
          "Limits", "number", "6000"),
    _spec("max_tokens_mention", "MAX_TOKENS_MENTION", "Tokens pro Antwort",
          "Limits", "number", "900"),
    _spec("max_mention_rounds", "MAX_MENTION_ROUNDS", "Diskussionsrunden",
          "Limits", "number", "2"),
]

BY_KEY = {entry["key"]: entry for entry in SETTINGS_SPEC}
SECRET_KEYS = {e["key"] for e in SETTINGS_SPEC if e["kind"] == SECRET}
CLEAR = "__CLEAR__"   # ausdrückliches Leeren eines Geheimnisses

_cache: dict[str, str] = {}
_lock = threading.Lock()


def invalidate() -> None:
    with _lock:
        _cache.clear()


def _db_values() -> dict[str, str]:
    with _lock:
        if _cache:
            return dict(_cache)
    rows = store.connect().execute("SELECT key, value FROM settings").fetchall()
    values = {row["key"]: row["value"] for row in rows}
    with _lock:
        _cache.clear()
        _cache.update(values)
    return values


def get(key: str) -> str:
    """Datenbank vor .env vor Default."""
    entry = BY_KEY.get(key)
    value = _db_values().get(key, "")
    if value:
        return value
    if entry and entry["env"]:
        from_env = env(entry["env"])
        if from_env:
            return from_env
    return entry["default"] if entry else ""


def get_int(key: str) -> int:
    try:
        return int(float(get(key)))
    except (TypeError, ValueError):
        entry = BY_KEY.get(key)
        return int(entry["default"]) if entry and entry["default"] else 0


def set_many(values: dict[str, str]) -> None:
    """Leerer String heißt 'unverändert lassen' – so kann das UI Geheimnisse
    maskiert anzeigen, ohne sie beim Speichern zu überschreiben."""
    conn = store.connect()
    for key, raw in values.items():
        if key not in BY_KEY:
            continue
        value = "" if raw is None else str(raw)
        if value == CLEAR:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            continue
        if not value.strip() and key in SECRET_KEYS:
            continue  # Geheimnis nicht angefasst
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value.strip()))
    conn.commit()
    invalidate()


def public_view() -> list[dict]:
    """Alles für die Einstellungsseite – Geheimnisse nur als Ja/Nein."""
    out = []
    for entry in SETTINGS_SPEC:
        item = dict(entry)
        item.pop("env", None)
        if entry["kind"] == SECRET:
            item["value"] = ""
            item["is_set"] = bool(get(entry["key"]))
        else:
            item["value"] = get(entry["key"])
            item["is_set"] = bool(item["value"])
        out.append(item)
    return out


# ---------------------------------------------------------------- Agenten
AGENT_FIELDS = ("name", "tagline", "color", "provider", "model",
                "system_prompt", "is_dev", "is_chairman", "enabled")


def _seed_agents() -> None:
    conn = store.connect()
    if conn.execute("SELECT COUNT(*) AS n FROM agents").fetchone()["n"]:
        return
    for position, agent in enumerate(DEFAULT_AGENTS):
        conn.execute(
            "INSERT INTO agents (id, position, name, tagline, color, provider,"
            " model, system_prompt, is_dev, is_chairman, enabled)"
            " VALUES (:id, :position, :name, :tagline, :color, :provider,"
            " :model, :system_prompt, :is_dev, :is_chairman, 1)",
            {**agent, "position": position})
    conn.commit()


def agents() -> list[dict]:
    _seed_agents()
    rows = store.connect().execute(
        "SELECT * FROM agents ORDER BY position, id").fetchall()
    return [dict(row) for row in rows]


def active_agents() -> list[dict]:
    return [a for a in agents() if a["enabled"]]


def agent_ids() -> list[str]:
    return [a["id"] for a in active_agents()]


def chairman_id() -> str:
    for agent in active_agents():
        if agent["is_chairman"]:
            return agent["id"]
    active = active_agents()
    return active[0]["id"] if active else ""


def save_agent(agent_id: str, fields: dict) -> bool:
    _seed_agents()
    updates = {k: fields[k] for k in AGENT_FIELDS if k in fields}
    if not updates:
        return False
    for flag in ("is_dev", "is_chairman", "enabled"):
        if flag in updates:
            updates[flag] = 1 if updates[flag] else 0
    conn = store.connect()
    assignments = ", ".join(f"{k} = :{k}" for k in updates)
    cursor = conn.execute(f"UPDATE agents SET {assignments} WHERE id = :id",
                          {**updates, "id": agent_id})
    if updates.get("is_chairman"):
        # Genau ein Chairman, sonst schreibt niemand die Synthese.
        conn.execute("UPDATE agents SET is_chairman = 0 WHERE id != ?",
                     (agent_id,))
    conn.commit()
    return cursor.rowcount > 0


def reset_agents() -> None:
    conn = store.connect()
    conn.execute("DELETE FROM agents")
    conn.commit()
    _seed_agents()


# ---------------------------------------------------------------- Async
async def a_set_many(values: dict[str, str]) -> None:
    await asyncio.to_thread(set_many, values)


async def a_public_view() -> list[dict]:
    return await asyncio.to_thread(public_view)


async def a_agents() -> list[dict]:
    return await asyncio.to_thread(agents)


async def a_save_agent(agent_id: str, fields: dict) -> bool:
    return await asyncio.to_thread(save_agent, agent_id, fields)


async def a_reset_agents() -> None:
    await asyncio.to_thread(reset_agents)
