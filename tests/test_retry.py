"""Was passiert, wenn ein Modell schweigt oder ausfällt.

Kimi ist im ersten echten Meeting stumm durchgelaufen – ohne Fehler, ohne
Meldung, ohne Beitrag. Genau das darf nicht mehr unbemerkt passieren.
"""
import asyncio

import pytest

from app import pipeline, settings, store
from app.bus import bus
from app.config import build_team


@pytest.fixture(autouse=True)
def db(tmp_path):
    store.reset_for_tests(str(tmp_path / "retry.db"))
    settings.invalidate()
    bus.history = []
    bus.clients = set()
    bus.meeting_id = None
    yield
    settings.invalidate()


def lauf(coro):
    return asyncio.run(coro)


class FakeChunk:
    def __init__(self, text):
        delta = type("D", (), {"content": text})()
        self.choices = [type("C", (), {"delta": delta})()]
        self.usage = None


class FakeStream:
    def __init__(self, text):
        self._chunks = [FakeChunk(text)] if text else []

    def __aiter__(self):
        async def gen():
            for chunk in self._chunks:
                yield chunk
        return gen()


def antworten(*folge):
    """Baut ein acompletion, das der Reihe nach liefert bzw. scheitert.

    Nach dem letzten Eintrag wiederholt sich dieser – so muss ein Test nur
    beschreiben, was ihn interessiert.
    """
    aufrufe = []

    async def acompletion(**kwargs):
        aufrufe.append(kwargs["model"])
        wert = folge[min(len(aufrufe) - 1, len(folge) - 1)]
        if isinstance(wert, Exception):
            raise wert
        return FakeStream(wert)

    return acompletion, aufrufe


@pytest.fixture
def board(monkeypatch):
    settings.set_many({"retry_attempts": "3", "retry_backoff": "0",
                       "hyai_api_key": "sk-test"})
    m = pipeline.Meeting()
    m.reload_team()
    # Sonst startet der Claude-Agent die echte CLI – auf einer Maschine mit
    # installiertem Claude Code hängt der Test dann am Subprozess.
    monkeypatch.setattr(m, "_use_claude_code", lambda _agent_id: False)
    return m


def systemzeilen():
    return [e["text"] for e in bus.history if e.get("type") == "system"]


def test_erster_versuch_reicht(board, monkeypatch):
    acompletion, aufrufe = antworten("Fertiges Gutachten.")
    monkeypatch.setattr(pipeline.litellm, "acompletion", acompletion)
    text = lauf(board._stream_agent("kimi", "Los."))
    assert text == "Fertiges Gutachten."
    assert len(aufrufe) == 1


def test_leere_antwort_wird_wiederholt(board, monkeypatch):
    """Kein Fehler, nur Schweigen – trotzdem ein Ausfall."""
    acompletion, aufrufe = antworten("", "", "Beim dritten Mal.")
    monkeypatch.setattr(pipeline.litellm, "acompletion", acompletion)
    text = lauf(board._stream_agent("kimi", "Los."))
    assert text == "Beim dritten Mal."
    assert len(aufrufe) == 3
    assert any("Versuch 1/3" in z for z in systemzeilen())


def test_fehler_wird_wiederholt(board, monkeypatch):
    acompletion, aufrufe = antworten(RuntimeError("503"), "Doch noch was.")
    monkeypatch.setattr(pipeline.litellm, "acompletion", acompletion)
    assert lauf(board._stream_agent("kimi", "Los.")) == "Doch noch was."
    assert len(aufrufe) == 2


def test_fallback_modell_uebernimmt(board, monkeypatch):
    settings.save_agent("kimi", {"provider": "openai", "model": "stumm",
                                 "fallback_model": "redselig"})
    board.reload_team()
    acompletion, aufrufe = antworten("", "", "", "Fallback liefert.")
    monkeypatch.setattr(pipeline.litellm, "acompletion", acompletion)
    text = lauf(board._stream_agent("kimi", "Los."))
    assert text == "Fallback liefert."
    assert aufrufe[:3] == ["openai/stumm"] * 3
    assert aufrufe[3] == "openai/redselig"
    assert any("Wechsel auf Fallback" in z for z in systemzeilen())


def test_ohne_fallback_bleibt_es_bei_den_versuchen(board, monkeypatch):
    acompletion, aufrufe = antworten("")
    monkeypatch.setattr(pipeline.litellm, "acompletion", acompletion)
    text = lauf(board._stream_agent("kimi", "Los."))
    assert len(aufrufe) == 3
    assert "ausgefallen" in text
    assert any("nach 3 Versuchen" in z for z in systemzeilen())


def test_fehlversuch_wird_im_chat_verworfen(board, monkeypatch):
    """Sonst klebt das Fragment des Fehlversuchs vor der echten Antwort."""
    acompletion, _ = antworten("halbe Sac", "Ganze Antwort.")
    monkeypatch.setattr(pipeline.litellm, "acompletion", acompletion)
    lauf(board._stream_agent("kimi", "Los."))
    # Erster Versuch liefert Text -> kein Reset. Jetzt der echte Fall:
    bus.history = []
    acompletion, _ = antworten("", "Ganze Antwort.")
    monkeypatch.setattr(pipeline.litellm, "acompletion", acompletion)
    lauf(board._stream_agent("kimi", "Los."))
    assert any(e["type"] == "msg_reset" for e in bus.history)


def test_versuche_lassen_sich_abschalten(board, monkeypatch):
    settings.set_many({"retry_attempts": "1"})
    acompletion, aufrufe = antworten(RuntimeError("aus"))
    monkeypatch.setattr(pipeline.litellm, "acompletion", acompletion)
    lauf(board._stream_agent("kimi", "Los."))
    assert len(aufrufe) == 1


def test_fallback_bekommt_den_provider_praefix():
    settings.save_agent("kimi", {"provider": "openai", "model": "a",
                                 "fallback_model": "b"})
    assert build_team()["kimi"].fallback_model == "openai/b"


def test_ohne_fallback_bleibt_das_feld_leer():
    assert build_team()["kimi"].fallback_model == ""


def test_fallback_feld_steht_in_den_einstellungen():
    from pathlib import Path
    seite = (Path(__file__).parent.parent / "app/static/settings.html").read_text()
    assert "f-fallback" in seite
    assert "fallback_model:" in seite
