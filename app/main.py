"""FastAPI: Projekte, WebSocket-Chat, Meeting-Start, statisches UI – hinter Login."""
import asyncio
import os

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import auth, claude_code, preflight, settings, store, usage
from .bus import bus
from .config import PHASES, PROVIDERS, build_team, env
from .coolify import CoolifyError, coolify
from .github import GitHubError, github, slugify_repo_name, valid_full_name
from .ingest import cleanup, clone_repo, pack_project
from .mentions import answer_user_mention, extract_mentions
from .pipeline import meeting
from .plane import PlaneError, plane
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
EMPTY_REPO_PACK = (
    "(Das Repository ist leer – es gibt noch keinen Code. Das Board startet "
    "auf der grünen Wiese: Klärt zuerst, worum es geht, und entwerft dann "
    "Strategie und Architektur von Grund auf.)"
)


def page(name: str) -> FileResponse:
    """HTML-Seite ohne Zwischenspeicher ausliefern.

    Wichtig, weil unter `/` je nach Session zwei verschiedene Dokumente
    liegen: Login oder Board. Ohne Cache-Control darf der Browser die
    Antwort heuristisch cachen (Last-Modified ist gesetzt) – dann zeigt er
    nach dem Anmelden die gespeicherte Login-Seite, statt neu zu laden.
    Für den Nutzer sieht das so aus, als würde die Weiterleitung fehlen.
    """
    return FileResponse(os.path.join(STATIC, name),
                        headers={"Cache-Control": "no-store"})


def _cookie_secure(request: Request) -> bool:
    setting = env("BOARDROOM_COOKIE_SECURE", "auto").lower()
    if setting in {"1", "true", "yes"}:
        return True
    if setting in {"0", "false", "no"}:
        return False
    forwarded = request.headers.get("x-forwarded-proto", "")
    return request.url.scheme == "https" or forwarded.startswith("https")


class LoginRequest(BaseModel):
    password: str


class ProjectRequest(BaseModel):
    name: str
    briefing: str = ""
    repo_full_name: str = ""
    create_repo: bool = False
    private: bool = True
    plane_project_id: str = ""
    create_plane_project: bool = False
    coolify_app_uuid: str = ""


class ProjectLinksRequest(BaseModel):
    briefing: str | None = None
    plane_project_id: str | None = None
    coolify_app_uuid: str | None = None


class StartRequest(BaseModel):
    project_id: str
    briefing: str = ""


class SettingsRequest(BaseModel):
    values: dict[str, str]


class AgentRequest(BaseModel):
    name: str | None = None
    tagline: str | None = None
    color: str | None = None
    provider: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    is_dev: bool | None = None
    is_chairman: bool | None = None
    enabled: bool | None = None


class DeployRequest(BaseModel):
    project_id: str = ""
    app_uuid: str = ""
    force: bool = False


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
        return page("login.html")
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
    return page("index.html")


@app.get("/settings")
async def settings_page() -> FileResponse:
    return page("settings.html")


# ---------------------------------------------------------------- Einstellungen
@app.get("/api/settings")
async def read_settings() -> JSONResponse:
    entries = await settings.a_public_view()
    groups: list[str] = []
    for entry in entries:
        if entry["group"] not in groups:
            groups.append(entry["group"])
    return JSONResponse({"settings": entries, "groups": groups})


@app.put("/api/settings")
async def write_settings(req: SettingsRequest) -> JSONResponse:
    await settings.a_set_many(req.values)
    meeting.reload_team()   # geänderte Prompts/Modelle sofort übernehmen
    return JSONResponse({"ok": True})


@app.get("/api/agents")
async def read_agents() -> JSONResponse:
    return JSONResponse({
        "agents": await settings.a_agents(),
        "providers": [{"id": key, "label": value["label"]}
                      for key, value in PROVIDERS.items()],
    })


@app.put("/api/agents/{agent_id}")
async def write_agent(agent_id: str, req: AgentRequest) -> JSONResponse:
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        return JSONResponse({"error": "Nichts zu ändern."}, status_code=422)
    if fields.get("provider") and fields["provider"] not in PROVIDERS:
        return JSONResponse({"error": f"Unbekannter Provider "
                                      f"'{fields['provider']}'."},
                            status_code=422)
    if not await settings.a_save_agent(agent_id, fields):
        return JSONResponse({"error": "Agent unbekannt."}, status_code=404)
    meeting.reload_team()
    return JSONResponse({"ok": True})


@app.post("/api/agents/reset")
async def reset_agents() -> JSONResponse:
    await settings.a_reset_agents()
    meeting.reload_team()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------- Integrationen
@app.get("/api/integrations")
async def integrations() -> JSONResponse:
    """Ein Klick, drei Verbindungstests – damit klar ist, was wirklich hängt."""
    async def github_check() -> dict:
        if not github.enabled:
            return {"ok": False, "detail": "Kein Token hinterlegt."}
        try:
            user = await github.me()
        except GitHubError as exc:
            return {"ok": False, "detail": str(exc)}
        return {"ok": True, "detail": f"Angemeldet als {user.get('login', '?')}."}

    plane_ok, plane_detail = await plane.check()
    coolify_ok, coolify_detail = await coolify.check()
    return JSONResponse({
        "github": await github_check(),
        "plane": {"ok": plane_ok, "detail": plane_detail},
        "coolify": {"ok": coolify_ok, "detail": coolify_detail},
    })


@app.get("/api/coolify/applications")
async def coolify_applications() -> JSONResponse:
    try:
        return JSONResponse({"applications": await coolify.applications()})
    except CoolifyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)


@app.post("/api/coolify/deploy")
async def coolify_deploy(req: DeployRequest) -> JSONResponse:
    """Deployt die Anwendung des angegebenen Projekts – es gibt bewusst
    keine globale Standard-Anwendung mehr, jedes Projekt deployt sich selbst."""
    app_uuid = req.app_uuid.strip()
    if not app_uuid and req.project_id.strip():
        project = await store.get_project(req.project_id.strip())
        if not project:
            return JSONResponse({"error": "Projekt unbekannt."}, status_code=404)
        app_uuid = project["coolify_app_uuid"]
    if not app_uuid:
        return JSONResponse(
            {"error": "Dem Projekt ist keine Coolify-Anwendung zugeordnet."},
            status_code=422)
    try:
        result = await coolify.deploy(app_uuid, req.force)
    except CoolifyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    await bus.system(f"Coolify-Deployment ausgelöst: {result}")
    return JSONResponse({"ok": True, "result": result})


@app.get("/api/team")
async def team() -> JSONResponse:
    specs = build_team()
    return JSONResponse({
        "agents": [{"id": s.id, "name": s.name, "tagline": s.tagline,
                    "color": s.color} for s in specs.values()],
        "phases": PHASES,
        "running": meeting.running,
        "github": github.enabled,
    })


@app.get("/api/preflight")
async def preflight_check(deep: bool = True) -> JSONResponse:
    """Sagt dir vor dem Meeting, welche Modelle wirklich antworten – und
    welche Slugs der Router tatsächlich kennt.

    deep=true macht bei Claude Code einen echten Mini-Call, der Auth und
    Erreichbarkeit belegt. deep=false prüft nur, ob die CLI da ist."""
    return JSONResponse(await preflight.check(deep))


@app.get("/api/claude-code")
async def claude_code_status(probe: bool = False) -> JSONResponse:
    """Getrennte Diagnose für CLI, Token und Verbindung.

    'Token fehlt' allein sagt nicht, ob die CLI überhaupt installiert ist –
    hier steht jede Stufe für sich.
    """
    return JSONResponse(await claude_code.diagnose(probe))


# ---------------------------------------------------------------- Verbrauch
@app.get("/usage")
async def usage_page() -> FileResponse:
    return page("usage.html")


@app.get("/api/usage")
async def usage_report(project_id: str = "") -> JSONResponse:
    """Tokens und Kosten – gesamt oder für ein Projekt."""
    data = await usage.report(project_id.strip())
    data["projects"] = await store.list_projects()
    data["project_id"] = project_id.strip()
    return JSONResponse(data)


# ---------------------------------------------------------------- GitHub
@app.get("/api/github")
async def github_status() -> JSONResponse:
    """Login-Check plus die zuletzt bespielten Repos für die Auswahl."""
    if not github.enabled:
        return JSONResponse({"enabled": False,
                             "error": "Kein GitHub-Token hinterlegt – trag ihn unter /settings ein (Abschnitt GitHub)."})
    try:
        user = await github.me()
    except GitHubError as exc:
        return JSONResponse({"enabled": True, "error": str(exc)})
    # Manche Tokens dürfen sich anmelden, aber keine Repo-Liste ziehen
    # (fein granulierte Tokens, Enterprise-Policies). Kein Grund zu blockieren –
    # owner/repo lässt sich weiterhin von Hand eintippen.
    repos, note = [], ""
    try:
        repos = await github.list_repos()
    except GitHubError as exc:
        note = f"Repo-Liste nicht verfügbar: {exc}"
    return JSONResponse({"enabled": True, "login": user.get("login", ""),
                         "repos": repos, "note": note, "error": ""})


# ---------------------------------------------------------------- Projekte
@app.get("/api/projects")
async def projects() -> JSONResponse:
    return JSONResponse({"projects": await store.list_projects()})


@app.post("/api/projects")
async def create_project(req: ProjectRequest) -> JSONResponse:
    """Ein Projekt ohne Repo gibt es nicht: entweder ein vorhandenes
    angeben oder eins anlegen lassen."""
    name = req.name.strip()
    if not name:
        return JSONResponse({"error": "Projektname fehlt."}, status_code=422)
    if not github.enabled:
        return JSONResponse(
            {"error": "Kein GitHub-Token hinterlegt – trag ihn unter /settings ein (Abschnitt GitHub)."},
            status_code=422)

    full_name = req.repo_full_name.strip()
    try:
        if req.create_repo:
            if full_name and not valid_full_name(full_name):
                return JSONResponse(
                    {"error": f"'{full_name}' ist kein gültiges owner/repo."},
                    status_code=422)
            repo_name = full_name.split("/")[-1] if full_name else slugify_repo_name(name)
            created = await github.create_repo(
                repo_name, description=req.briefing[:200], private=req.private)
            full_name = created.get("full_name", repo_name)
        else:
            if not valid_full_name(full_name):
                return JSONResponse(
                    {"error": "Gib ein Repo als owner/name an – oder lass eins anlegen."},
                    status_code=422)
            if await github.get_repo(full_name) is None:
                return JSONResponse(
                    {"error": f"Repo '{full_name}' existiert nicht oder der "
                              "Token sieht es nicht."},
                    status_code=404)
    except GitHubError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)

    # Plane-Projekt: vorhandenes wählen oder eins anlegen. Optional – ohne
    # bleibt der Ticket-Sync für dieses Projekt einfach aus.
    plane_project_id = req.plane_project_id.strip()
    if req.create_plane_project:
        try:
            created_plane = await plane.create_project(name)
            plane_project_id = created_plane.get("id", "")
        except PlaneError as exc:
            return JSONResponse({"error": f"Plane: {exc}"}, status_code=422)

    project = await store.create_project(
        name, req.briefing,
        repo_full_name=full_name,
        repo_url=github.web_url(full_name),
        plane_project_id=plane_project_id,
        coolify_app_uuid=req.coolify_app_uuid.strip(),
    )
    return JSONResponse({"project": project})


@app.put("/api/projects/{project_id}")
async def update_project(project_id: str, req: ProjectLinksRequest) -> JSONResponse:
    """Plane-Projekt und Coolify-App kommen oft erst später dazu."""
    if not await store.get_project(project_id):
        return JSONResponse({"error": "Projekt unbekannt."}, status_code=404)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not await store.update_project(project_id, fields):
        return JSONResponse({"error": "Nichts zu ändern."}, status_code=422)
    return JSONResponse({"project": await store.get_project(project_id)})


# ---------------------------------------------------------------- Plane
@app.get("/api/plane/projects")
async def plane_projects() -> JSONResponse:
    """Für das Dropdown beim Anlegen eines Boardroom-Projekts."""
    if not plane.configured:
        return JSONResponse({"configured": False, "projects": [],
                             "error": "Plane ist nicht konfiguriert "
                                      "(/settings, Abschnitt Plane)."})
    try:
        return JSONResponse({"configured": True,
                             "projects": await plane.projects(), "error": ""})
    except PlaneError as exc:
        return JSONResponse({"configured": True, "projects": [],
                             "error": str(exc)})


@app.get("/api/projects/{project_id}/meetings")
async def project_meetings(project_id: str) -> JSONResponse:
    if not await store.get_project(project_id):
        return JSONResponse({"error": "Projekt unbekannt."}, status_code=404)
    return JSONResponse({"meetings": await store.list_meetings(project_id)})


@app.get("/api/meetings/{meeting_id}/events")
async def meeting_events(meeting_id: str) -> JSONResponse:
    """Archivierter Verlauf – überlebt Neustarts, anders als der RAM-Chat."""
    return JSONResponse({"events": await store.load_events(meeting_id)})


# ---------------------------------------------------------------- Meeting
@app.post("/api/start")
async def start(req: StartRequest) -> JSONResponse:
    if meeting.running:
        return JSONResponse({"error": "Es läuft bereits ein Board-Meeting."},
                            status_code=409)
    project = await store.get_project(req.project_id.strip())
    if not project:
        return JSONResponse({"error": "Projekt unbekannt."}, status_code=404)

    briefing = (req.briefing.strip() or project["briefing"]).strip()
    if not briefing:
        return JSONResponse({"error": "Briefing fehlt."}, status_code=422)

    record = await store.create_meeting(project["id"], briefing)
    await bus.open_meeting(record["id"], project)
    await bus.system(f"Projekt: {project['name']} · Repo: "
                     f"{project['repo_full_name'] or '—'}")

    project_pack, repo_dir = await _ingest(project)

    async def runner() -> None:
        status = "done"
        try:
            await meeting.run(briefing, project_pack, repo_dir, project)
        except Exception as exc:
            status = "failed"
            await bus.system(f"Meeting abgebrochen: {exc}")
        finally:
            if repo_dir:
                cleanup(repo_dir)
            await store.finish_meeting(record["id"], status)
            bus.close_meeting()

    asyncio.create_task(runner())
    return JSONResponse({"ok": True, "meeting_id": record["id"]})


async def _ingest(project: dict) -> tuple[str, str | None]:
    """Repo holen und fürs Board verpacken. Leeres Repo ist kein Fehler,
    sondern der Start auf der grünen Wiese."""
    full_name = project["repo_full_name"]
    if not full_name or not github.enabled:
        return "(Kein Repo verfügbar – Analyse basiert nur auf dem Briefing.)", None
    try:
        if await github.is_empty(full_name):
            await bus.system(f"Repo {full_name} ist leer – grüne Wiese.")
            return EMPTY_REPO_PACK, None
        url = validate_repo_url(github.clone_url(full_name))
    except (GitHubError, RepoUrlError) as exc:
        await bus.system(f"Repo-Fehler: {exc} – Meeting läuft nur mit Briefing.")
        return f"(Repo nicht lesbar: {exc})", None

    await bus.system(f"Klone {full_name} …")
    try:
        repo_dir = await asyncio.to_thread(clone_repo, url)
        pack = await asyncio.to_thread(pack_project, repo_dir)
        await bus.system("Repo eingelesen und für das Board verpackt.")
        return pack, repo_dir
    except Exception as exc:
        await bus.system(f"Repo-Fehler: {redact(str(exc))} – "
                         "Meeting läuft nur mit Briefing.")
        return "(Repo konnte nicht geklont werden.)", None


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
