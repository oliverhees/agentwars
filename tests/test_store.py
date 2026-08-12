"""Projekte, Meetings und Verlauf müssen den Neustart überleben."""
import pytest

from app import store


@pytest.fixture(autouse=True)
def db(tmp_path):
    store.reset_for_tests(str(tmp_path / "test.db"))
    yield
    store.reset_for_tests(str(tmp_path / "unused.db"))


def test_projekt_anlegen_und_wiederfinden():
    projekt = store._create_project("Content Factory", "Briefing",
                                    repo_full_name="oliverhees/content",
                                    repo_url="https://x/y")
    geladen = store._get_project(projekt["id"])
    assert geladen["name"] == "Content Factory"
    assert geladen["repo_full_name"] == "oliverhees/content"


def test_unbekanntes_projekt_ist_none():
    assert store._get_project("gibtsnicht") is None


def test_liste_zeigt_meeting_zaehler():
    projekt = store._create_project("P", "", repo_full_name="o/r")
    assert store._list_projects()[0]["meetings"] == 0
    store._create_meeting(projekt["id"], "b")
    store._create_meeting(projekt["id"], "b")
    eintrag = store._list_projects()[0]
    assert eintrag["meetings"] == 2
    assert eintrag["last_meeting"] is not None


def test_meeting_wird_abgeschlossen():
    projekt = store._create_project("P", "", repo_full_name="o/r")
    meeting = store._create_meeting(projekt["id"], "briefing")
    assert store._list_meetings(projekt["id"])[0]["status"] == "running"
    store._finish_meeting(meeting["id"], "done")
    fertig = store._list_meetings(projekt["id"])[0]
    assert fertig["status"] == "done"
    assert fertig["ended_at"] is not None


def test_events_kommen_in_reihenfolge_zurueck():
    projekt = store._create_project("P", "", repo_full_name="o/r")
    meeting = store._create_meeting(projekt["id"], "b")
    for i in range(3):
        store._append_event(meeting["id"], {"type": "system", "text": f"e{i}"})
    texte = [e["text"] for e in store._load_events(meeting["id"])]
    assert texte == ["e0", "e1", "e2"]


def test_events_sind_pro_meeting_getrennt():
    projekt = store._create_project("P", "", repo_full_name="o/r")
    a = store._create_meeting(projekt["id"], "b")
    b = store._create_meeting(projekt["id"], "b")
    store._append_event(a["id"], {"type": "system", "text": "nur a"})
    assert store._load_events(b["id"]) == []
    assert len(store._load_events(a["id"])) == 1


def test_verlauf_ueberlebt_den_neustart(tmp_path):
    pfad = str(tmp_path / "persist.db")
    store.reset_for_tests(pfad)
    projekt = store._create_project("P", "", repo_full_name="o/r")
    meeting = store._create_meeting(projekt["id"], "b")
    store._append_event(meeting["id"], {"type": "system", "text": "bleibt"})

    store.reset_for_tests(pfad)  # simuliert den Container-Neustart
    assert store._get_project(projekt["id"])["name"] == "P"
    assert store._load_events(meeting["id"])[0]["text"] == "bleibt"


def test_alte_datenbank_bekommt_die_neuen_spalten(tmp_path):
    """Sein laufendes Deployment hat die Tabelle ohne plane_project_id und
    coolify_app_uuid – CREATE TABLE IF NOT EXISTS fasst die nicht an."""
    import sqlite3
    pfad = str(tmp_path / "alt.db")
    alt = sqlite3.connect(pfad)
    alt.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL,"
                " briefing TEXT NOT NULL DEFAULT '',"
                " repo_full_name TEXT NOT NULL DEFAULT '',"
                " repo_url TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL)")
    alt.execute("INSERT INTO projects VALUES ('alt1','Altprojekt','b','o/r','u',1.0)")
    alt.commit()
    alt.close()

    store.reset_for_tests(pfad)
    geladen = store._get_project("alt1")
    assert geladen["name"] == "Altprojekt"
    assert geladen["plane_project_id"] == ""
    assert geladen["coolify_app_uuid"] == ""
    assert store._update_project("alt1", {"plane_project_id": "neu"})
    assert store._get_project("alt1")["plane_project_id"] == "neu"
