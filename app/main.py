"""FastAPI: WebSocket-Chat, Meeting-Start, statisches UI – hinter Login."""
import asyncio
import os

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import auth, preflight
from .bus import bus
from .config import PHASES, build_team, env
from .ingest import cleanup, clone_repo, pack_project
from .mentions import answer_user_mention, extract_mentions
from .pipeline import meeting
from .security import RepoUrlError, redact, validate_repo_url

app = FastAPI(title="KI-Agentur Boardroom")
STATIC = os.path.join(os.path.dirname(__file__), "static")

# Diese Pfade müssen ohne Session erreichbar sein, sonst kommt niemand rein.
PUBLIC_PATHS = {"/api/login", "/healthz"}

MISCONFIG_HINT = (
    "Boardroom ist nicht konfiguriert: BOARDROOM_PASSWORD fehlt. "
    "Setz ein Passwort in der .env – oder BOARDROOM_ALLOW_ANONYMOUS=1, "
    "wenn die App nachweislich nur lokal erreichbar ist."
)


def _cookie_secure(request: Request) -> bool:
    setting = env("BOARDROOM_COOKIE_SECURE", "auto").lower()
    if setting in {"1", "true", "yes"}:
        return True
    if setting in {"0", "false", "no"}:
        return False
    forwarded = request.headers.get("x-forwarded-proto", "")
    return request.url.scheme == "https" or forwarded.startswith("https")


class StartRequest(BaseModel):
    briefing: str
    git_url: str | None = None


class LoginRequest(BaseModel):
    password: str


# ---------------------------------------------------------------- Guard
@app.middleware("http")
async def require_session(request: Request, call_next):
    if auth.misconfigured():
        # Fail-closed: lieber gar nicht antworten als offen im Netz stehen.
        return JSONResponse({"error": MISCONFIG_HINT}, status_code=503)
    path = request.url.path
    if path in PUBLIC_PATHS or not auth.enabled():
        return await call_next(request)
    if auth.verify_token(request.cookies.get(auth.COOKIE_NAME)):
        return await call_next(request)
    if path == "/":
        return FileResponse(os.path.join(STATIC, "login.html"))
    return JSONResponse({"error": "Nicht angemeldet."}, status_code=401)


# ---------------------------------------------------------------- Auth
@app.post("/api/login")
async def login(req: LoginRequest, request: Request) -> JSONResponse:
    client_ip = request.client.host if request.client else "unbekannt"
    if auth.locked_out(client_ip):
        return JSONResponse(
            {"error": "Zu viele Fehlversuche. Warte ein paar Minuten."},
            status_code=429)
    if not auth.check_password(req.password, client_ip):
        await asyncio.sleep(0.6)  # Rate-Limit für Rateversuche
        return JSONResponse({"error": "Falsches Passwort."}, status_code=401)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        auth.COOKIE_NAME, auth.issue_token(),
        max_age=auth.SESSION_TTL, httponly=True, samesite="lax",
        secure=_cookie_secure(request), path="/",
    )
    return resp


@app.post("/api/logout")
async def logout() -> JSONResponse:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE_NAME, path="/")
    return resp


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"ok": True, "auth": auth.enabled(),
                         "configured": not auth.misconfigured()})


# ---------------------------------------------------------------- App
@app.get("/")
async def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/api/team")
async def team() -> JSONResponse:
    specs = build_team()
    return JSONResponse({
        "agents": [{"id": s.id, "name": s.name, "tagline": s.tagline,
                    "color": s.color} for s in specs.values()],
        "phases": PHASES,
        "running": meeting.running,
    })


@app.get("/api/preflight")
async def preflight_check() -> JSONResponse:
    """Sagt dir vor dem Meeting, welche Modelle wirklich antworten – und
    welche Slugs der Router tatsächlich kennt."""
    return JSONResponse(await preflight.check())


@app.post("/api/start")
async def start(req: StartRequest) -> JSONResponse:
    if meeting.running:
        return JSONResponse({"error": "Es läuft bereits ein Board-Meeting."},
                            status_code=409)
    briefing = req.briefing.strip()
    if not briefing:
        return JSONResponse({"error": "Briefing fehlt."}, status_code=422)

    git_url = (req.git_url or "").strip()
    if git_url:
        # Vor dem Start prüfen, damit der Fehler im Formular landet und
        # nicht erst mitten im Meeting als Chatzeile.
        try:
            git_url = validate_repo_url(git_url)
        except RepoUrlError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    project_pack = "(Kein Repo übergeben – Analyse basiert nur auf dem Briefing.)"
    repo_dir = None
    if git_url:
        await bus.system(f"Klone Repo: {redact(git_url)}")
        try:
            repo_dir = await asyncio.to_thread(clone_repo, git_url)
            project_pack = await asyncio.to_thread(pack_project, repo_dir)
            await bus.system("Repo eingelesen und für das Board verpackt.")
        except Exception as exc:
            await bus.system(f"Repo-Fehler: {exc} – Meeting läuft nur mit Briefing.")

    async def runner() -> None:
        try:
            await meeting.run(briefing, project_pack, repo_dir)
        finally:
            if repo_dir:
                cleanup(repo_dir)

    asyncio.create_task(runner())
    return JSONResponse({"ok": True})


@app.websocket("/ws")
async def websocket(ws: WebSocket) -> None:
    # Middleware greift bei WebSockets nicht – hier selbst prüfen.
    if auth.misconfigured() or not auth.verify_token(ws.cookies.get(auth.COOKIE_NAME)):
        await ws.close(code=1008)
        return
    await ws.accept()
    await bus.register(ws)
    try:
        while True:
            text = (await ws.receive_text()).strip()
            if text:
                await bus.push_user_message(text)
                for target in extract_mentions(text, author_id="user"):
                    asyncio.create_task(
                        answer_user_mention(meeting, target, text))
    except WebSocketDisconnect:
        bus.unregister(ws)
    except Exception:
        bus.unregister(ws)
