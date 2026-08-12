"""Persistenz: Projekte, Meetings und der komplette Verlauf.

Bisher lebte alles im RAM – Neustart hieß: Chat weg, Projekt weg, kein
Nachschlagen was das Board vor drei Wochen gesagt hat. Eine Fabrik braucht
Projekte, die Wochen leben, also liegt der Zustand jetzt in SQLite.

Kein ORM und keine Migrations-Maschinerie: eine Datei, drei Tabellen,
Schreibzugriffe laufen über asyncio.to_thread, damit der Event-Loop nicht
blockiert.
"""
import asyncio
import json
import os
import sqlite3
import time
import uuid

from .config import env

DB_PATH = env("BOARDROOM_DB", "data/boardroom.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    briefing       TEXT NOT NULL DEFAULT '',
    repo_full_name TEXT NOT NULL DEFAULT '',
    repo_url       TEXT NOT NULL DEFAULT '',
    created_at     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meetings (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    briefing   TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'running',
    started_at REAL NOT NULL,
    ended_at   REAL
);
CREATE TABLE IF NOT EXISTS events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id TEXT NOT NULL REFERENCES meetings(id),
    ts         REAL NOT NULL,
    payload    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agents (
    id            TEXT PRIMARY KEY,
    position      INTEGER NOT NULL DEFAULT 0,
    name          TEXT NOT NULL,
    tagline       TEXT NOT NULL DEFAULT '',
    color         TEXT NOT NULL DEFAULT '#888888',
    provider      TEXT NOT NULL DEFAULT 'hostyourai',
    model         TEXT NOT NULL DEFAULT '',
    system_prompt TEXT NOT NULL DEFAULT '',
    is_dev        INTEGER NOT NULL DEFAULT 0,
    is_chairman   INTEGER NOT NULL DEFAULT 0,
    enabled       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_meetings_project ON meetings(project_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_meeting ON events(meeting_id, seq);
"""

_conn: sqlite3.Connection | None = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    directory = os.path.dirname(DB_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL, damit Lesen (UI blättert im Verlauf) und Schreiben (laufendes
    # Meeting) sich nicht gegenseitig blockieren.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    _conn = conn
    return conn


def connect() -> sqlite3.Connection:
    """Gemeinsame Verbindung – settings.py legt seine Tabellen daneben."""
    return _connect()


def reset_for_tests(path: str) -> None:
    """Tests bekommen eine eigene Datei statt der echten Datenbank."""
    global _conn, DB_PATH
    if _conn is not None:
        _conn.close()
    _conn = None
    DB_PATH = path


# ---------------------------------------------------------------- Projekte
def _create_project(name: str, briefing: str, repo_full_name: str,
                    repo_url: str) -> dict:
    project = {
        "id": uuid.uuid4().hex[:12],
        "name": name.strip()[:200],
        "briefing": briefing.strip(),
        "repo_full_name": repo_full_name.strip(),
        "repo_url": repo_url.strip(),
        "created_at": time.time(),
    }
    conn = _connect()
    conn.execute(
        "INSERT INTO projects (id, name, briefing, repo_full_name, repo_url,"
        " created_at) VALUES (:id, :name, :briefing, :repo_full_name,"
        " :repo_url, :created_at)", project)
    conn.commit()
    return project


def _list_projects() -> list[dict]:
    rows = _connect().execute(
        "SELECT p.*,"
        " (SELECT COUNT(*) FROM meetings m WHERE m.project_id = p.id) AS meetings,"
        " (SELECT MAX(started_at) FROM meetings m WHERE m.project_id = p.id) AS last_meeting"
        " FROM projects p ORDER BY p.created_at DESC").fetchall()
    return [dict(row) for row in rows]


def _get_project(project_id: str) -> dict | None:
    row = _connect().execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------- Meetings
def _create_meeting(project_id: str, briefing: str) -> dict:
    meeting = {
        "id": uuid.uuid4().hex[:12],
        "project_id": project_id,
        "briefing": briefing,
        "status": "running",
        "started_at": time.time(),
    }
    conn = _connect()
    conn.execute(
        "INSERT INTO meetings (id, project_id, briefing, status, started_at)"
        " VALUES (:id, :project_id, :briefing, :status, :started_at)", meeting)
    conn.commit()
    return meeting


def _finish_meeting(meeting_id: str, status: str) -> None:
    conn = _connect()
    conn.execute("UPDATE meetings SET status = ?, ended_at = ? WHERE id = ?",
                 (status, time.time(), meeting_id))
    conn.commit()


def _list_meetings(project_id: str, limit: int = 50) -> list[dict]:
    rows = _connect().execute(
        "SELECT * FROM meetings WHERE project_id = ?"
        " ORDER BY started_at DESC LIMIT ?", (project_id, limit)).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------- Events
def _append_event(meeting_id: str, event: dict) -> None:
    conn = _connect()
    conn.execute("INSERT INTO events (meeting_id, ts, payload) VALUES (?, ?, ?)",
                 (meeting_id, event.get("ts") or time.time(),
                  json.dumps(event, ensure_ascii=False)))
    conn.commit()


def _load_events(meeting_id: str, limit: int = 2000) -> list[dict]:
    rows = _connect().execute(
        "SELECT payload FROM events WHERE meeting_id = ?"
        " ORDER BY seq LIMIT ?", (meeting_id, limit)).fetchall()
    out = []
    for row in rows:
        try:
            out.append(json.loads(row["payload"]))
        except json.JSONDecodeError:
            continue
    return out


# ---------------------------------------------------------------- Async-Fassade
async def create_project(name: str, briefing: str, repo_full_name: str,
                         repo_url: str) -> dict:
    return await asyncio.to_thread(
        _create_project, name, briefing, repo_full_name, repo_url)


async def list_projects() -> list[dict]:
    return await asyncio.to_thread(_list_projects)


async def get_project(project_id: str) -> dict | None:
    return await asyncio.to_thread(_get_project, project_id)


async def create_meeting(project_id: str, briefing: str) -> dict:
    return await asyncio.to_thread(_create_meeting, project_id, briefing)


async def finish_meeting(meeting_id: str, status: str) -> None:
    await asyncio.to_thread(_finish_meeting, meeting_id, status)


async def list_meetings(project_id: str, limit: int = 50) -> list[dict]:
    return await asyncio.to_thread(_list_meetings, project_id, limit)


async def append_event(meeting_id: str, event: dict) -> None:
    await asyncio.to_thread(_append_event, meeting_id, event)


async def load_events(meeting_id: str, limit: int = 2000) -> list[dict]:
    return await asyncio.to_thread(_load_events, meeting_id, limit)
