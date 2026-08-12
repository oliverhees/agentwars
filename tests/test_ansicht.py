"""Die Ansicht folgt der Phase.

Sechs Gutachten, gleichzeitig geschrieben, als Chatverlauf gelesen – das war
kein Gespräch, sondern ein Stapel. Jede Nachricht sagt jetzt, welche Form sie
bekommt: Spalte, Chat oder Dokument.
"""
from pathlib import Path

import pytest

from app import mentions, pipeline, settings, store
from app.bus import bus

SEITE = (Path(__file__).parent.parent / "app/static/index.html").read_text()

from tests.test_retry import antworten, lauf  # noqa: E402  (gleiche Attrappe)


@pytest.fixture(autouse=True)
def db(tmp_path):
    store.reset_for_tests(str(tmp_path / "ansicht.db"))
    settings.invalidate()
    bus.history = []
    bus.clients = set()
    bus.meeting_id = None
    yield
    settings.invalidate()


@pytest.fixture
def board(monkeypatch):
    settings.set_many({"retry_attempts": "1", "hyai_api_key": "sk-test"})
    acompletion, _ = antworten("Beitrag.")
    monkeypatch.setattr(pipeline.litellm, "acompletion", acompletion)
    m = pipeline.Meeting()
    m.reload_team()
    # Sonst startet der Claude-Agent die echte CLI – auf einer Maschine mit
    # installiertem Claude Code hängt der Test dann am Subprozess.
    monkeypatch.setattr(m, "_use_claude_code", lambda _agent_id: False)
    return m


def formen(typ="msg_start"):
    return [e.get("view") for e in bus.history if e.get("type") == typ]


def test_ohne_phase_bleibt_es_chat(board):
    lauf(board._stream_agent("kimi", "Los."))
    assert formen() == ["chat"]


def test_einzelbeitraege_sind_spalten(board):
    with board.ansicht("columns"):
        lauf(board._stream_agent("kimi", "Los."))
    assert formen() == ["columns"]
    assert formen("msg_end") == ["columns"]


def test_synthese_ist_ein_dokument(board):
    with board.ansicht("doc"):
        lauf(board._stream_agent("claude", "Los."))
    assert formen() == ["doc"]


def test_die_form_wird_danach_zurueckgesetzt(board):
    with board.ansicht("columns"):
        pass
    assert board.view == "chat"


def test_die_phase_steht_an_der_nachricht(board):
    board.phase = "gutachten"
    with board.ansicht("columns"):
        lauf(board._stream_agent("kimi", "Los."))
    start = [e for e in bus.history if e["type"] == "msg_start"][0]
    assert start["phase"] == "gutachten"


def test_direktantwort_an_den_gruender_bleibt_chat(board, monkeypatch):
    """Auch wenn sie mitten in der Spaltenphase ausgelöst wird."""
    gesehen = []

    async def merken(agent_id, prompt, max_tokens=None):
        gesehen.append(board.view)
        return ""

    monkeypatch.setattr(board, "_stream_agent", merken)
    with board.ansicht("columns"):
        lauf(mentions.answer_user_mention(board, "kimi", "Was meinst du?"))
    assert gesehen == ["chat"]


# ---------------------------------------------------------------- UI
def test_ui_kennt_alle_drei_formen():
    for form in ('className = view === "columns" ? "cols" : "docs"',
                 'if (view === "chat")', 'PHASENTITEL'):
        assert form in SEITE


def test_spalten_stehen_nebeneinander():
    """auto-fit: so viele Spalten wie passen, danach Umbruch statt
    Seitwaertsscrollen."""
    assert "repeat(auto-fit,minmax(300px,1fr))" in SEITE


def test_auf_dem_handy_untereinander():
    assert "grid-template-columns:1fr" in SEITE


def test_phasenwechsel_schliesst_den_block():
    assert "abschnitt = null;   // neue Phase, neuer Block" in SEITE
