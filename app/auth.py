"""Zugangsschutz: Passwort-Login + signiertes Session-Cookie.

Warum überhaupt: `/api/start` klont Repos und startet Claude Code mit
Dateizugriff, und jedes Meeting kostet Tokens bzw. zieht auf dein Abo.
Ein offener Endpoint wäre damit ein Remote-Code-Execution-Hebel auf deinem
Coolify-Host. Deshalb ist AgentWars fail-closed: ohne gesetztes
Passwort antwortet die App gar nicht erst.

Session-Cookie statt Basic-Auth, weil der Browser das Cookie automatisch
an den WebSocket-Handshake mitschickt – Header kann die WebSocket-API nicht.
"""
import hashlib
import hmac
import secrets
import time

from .config import env, env_any

PASSWORD = env_any("AGENTWARS_PASSWORD", "BOARDROOM_PASSWORD")
ALLOW_ANONYMOUS = env_any("AGENTWARS_ALLOW_ANONYMOUS",
                          "BOARDROOM_ALLOW_ANONYMOUS") == "1"
SESSION_TTL = int(env_any("AGENTWARS_SESSION_HOURS",
                          "BOARDROOM_SESSION_HOURS", default="168")) * 3600
COOKIE_NAME = "agentwars_session"

# Ohne festen Secret werden Sessions bei jedem Neustart ungültig – für einen
# Ein-Container-Dienst völlig ok, aber setzbar, damit Redeploys nicht ausloggen.
_SECRET = (env_any("AGENTWARS_SECRET", "BOARDROOM_SECRET").encode()
           or secrets.token_bytes(32))

# Brute-Force-Bremse: Fehlversuche je Client-IP im Zeitfenster.
LOCKOUT_AFTER = 8
LOCKOUT_WINDOW = 300
_failures: dict[str, list[float]] = {}


def enabled() -> bool:
    """True, wenn Anfragen ein gültiges Session-Cookie brauchen."""
    return bool(PASSWORD)


def misconfigured() -> bool:
    """Kein Passwort und kein bewusstes Opt-out → App verweigert den Dienst."""
    return not PASSWORD and not ALLOW_ANONYMOUS


def _sign(payload: str) -> str:
    return hmac.new(_SECRET, payload.encode(), hashlib.sha256).hexdigest()


def issue_token() -> str:
    payload = str(int(time.time()) + SESSION_TTL)
    return f"{payload}.{_sign(payload)}"


def verify_token(token: str | None) -> bool:
    if not enabled():
        return True
    if not token or "." not in token:
        return False
    payload, _, signature = token.partition(".")
    if not hmac.compare_digest(_sign(payload), signature):
        return False
    try:
        return int(payload) > time.time()
    except ValueError:
        return False


# ---------------------------------------------------------------- Login
def locked_out(client_ip: str) -> bool:
    now = time.time()
    recent = [t for t in _failures.get(client_ip, []) if now - t < LOCKOUT_WINDOW]
    _failures[client_ip] = recent
    return len(recent) >= LOCKOUT_AFTER


def check_password(candidate: str, client_ip: str) -> bool:
    if not enabled():
        return True
    ok = hmac.compare_digest(candidate or "", PASSWORD)
    if ok:
        _failures.pop(client_ip, None)
    else:
        _failures.setdefault(client_ip, []).append(time.time())
    return ok
