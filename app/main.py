"""FastAPI: WebSocket-Chat, Meeting-Start, statisches UI."""
import asyncio
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .bus import bus
from .config import PHASES, build_team
from .ingest import cleanup, clone_repo, pack_project
from .pipeline import meeting
from .mentions import answer_user_mention, extract_mentions

app = FastAPI(title="KI-Agentur Boardroom")
STATIC = os.path.join(os.path.dirname(__file__), "static")


class StartRequest(BaseModel):
    briefing: str
    git_url: str | None = None


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


@app.post("/api/start")
async def start(req: StartRequest) -> JSONResponse:
    if meeting.running:
        return JSONResponse({"error": "Es läuft bereits ein Board-Meeting."},
                            status_code=409)
    briefing = req.briefing.strip()
    if not briefing:
        return JSONResponse({"error": "Briefing fehlt."}, status_code=422)

    project_pack = "(Kein Repo übergeben – Analyse basiert nur auf dem Briefing.)"
    repo_dir = None
    if req.git_url:
        await bus.system(f"Klone Repo: {req.git_url}")
        try:
            repo_dir = await asyncio.to_thread(clone_repo, req.git_url.strip())
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
