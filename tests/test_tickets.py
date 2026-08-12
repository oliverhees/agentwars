"""Wohin die Tickets gehen – und was passiert, wenn das Ziel nicht steht."""
import pytest

from app import github as gh_mod
from app import plane as plane_mod
from app import settings, store, tickets

PROJEKT = {"id": "p1", "name": "P", "repo_full_name": "oliverhees/content",
           "plane_project_id": "plane-uuid-1", "coolify_app_uuid": ""}

ISSUES = [{"name": "Auth einbauen", "description": "warum", "priority": "urgent"},
          {"name": "Tests", "description": "wie", "priority": "low"}]


@pytest.fixture(autouse=True)
def db(tmp_path):
    store.reset_for_tests(str(tmp_path / "tickets.db"))
    settings.invalidate()


@pytest.fixture
def plane_faehig(monkeypatch):
    gesehen = []

    async def anlegen(self, name, description_md, priority="medium", project_id=""):
        gesehen.append((name, priority, project_id))
        return {"sequence_id": len(gesehen)}

    monkeypatch.setattr(plane_mod.PlaneClient, "create_issue", anlegen)
    settings.set_many({"plane_base_url": "https://plane.test",
                       "plane_api_key": "k", "plane_workspace": "w"})
    return gesehen


@pytest.fixture
def github_faehig(monkeypatch):
    gesehen = []

    async def anlegen(self, full_name, title, body, labels=None):
        gesehen.append((full_name, title, labels))
        return {"number": len(gesehen)}

    monkeypatch.setattr(gh_mod.GitHubClient, "create_issue", anlegen)
    settings.set_many({"github_token": "ghp_test"})
    return gesehen


# ---------------------------------------------------------------- Ziel
def test_plane_ist_der_standard():
    assert tickets.target() == "plane"


def test_unbekanntes_ziel_faellt_auf_plane_zurueck():
    settings.set_many({"ticket_target": "irgendwas"})
    assert tickets.target() == "plane"


# ---------------------------------------------------------------- Sync
def _run(coro):
    """Kein pytest-asyncio nötig – die Tests rufen genau eine Coroutine auf."""
    import asyncio
    return asyncio.run(coro)


def test_tickets_gehen_ans_projekteigene_plane_projekt(plane_faehig):
    done, failed = _run(tickets.sync(ISSUES, PROJEKT))
    assert not failed and len(done) == 2
    assert {eintrag[2] for eintrag in plane_faehig} == {"plane-uuid-1"}


def test_ohne_projekteigenes_plane_projekt_greift_der_rueckfall(plane_faehig):
    settings.set_many({"plane_project_id": "global-uuid"})
    _run(tickets.sync(ISSUES[:1], {**PROJEKT, "plane_project_id": ""}))
    assert plane_faehig[0][2] == "global-uuid"


def test_github_bekommt_prioritaet_als_label(github_faehig):
    settings.set_many({"ticket_target": "github"})
    done, failed = _run(tickets.sync(ISSUES, PROJEKT))
    assert not failed and len(done) == 2
    assert github_faehig[0] == ("oliverhees/content", "Auth einbauen",
                                ["prio:urgent"])


def test_beides_legt_jedes_ticket_zweimal_an(plane_faehig, github_faehig):
    settings.set_many({"ticket_target": "both"})
    done, _ = _run(tickets.sync(ISSUES, PROJEKT))
    assert len(done) == 4
    assert len(plane_faehig) == 2 and len(github_faehig) == 2


def test_aus_legt_nichts_an(plane_faehig):
    settings.set_many({"ticket_target": "off"})
    assert _run(tickets.sync(ISSUES, PROJEKT)) == ([], [])
    assert plane_faehig == []


def test_ein_fehler_stoppt_die_uebrigen_nicht(monkeypatch, plane_faehig):
    async def manchmal_kaputt(self, name, description_md, priority="medium",
                              project_id=""):
        if name == "Tests":
            raise plane_mod.PlaneError("Plane sagt nein")
        return {"sequence_id": 1}

    monkeypatch.setattr(plane_mod.PlaneClient, "create_issue", manchmal_kaputt)
    done, failed = _run(tickets.sync(ISSUES, PROJEKT))
    assert len(done) == 1 and len(failed) == 1
    assert "Plane sagt nein" in failed[0]


# ---------------------------------------------------------------- Readiness
def test_readiness_meldet_fehlendes_plane():
    assert "nicht konfiguriert" in tickets.readiness(PROJEKT)


def test_readiness_meldet_fehlendes_projekt(plane_faehig):
    assert "Plane-Projekt" in tickets.readiness({**PROJEKT, "plane_project_id": ""})


def test_readiness_ist_leer_wenn_alles_steht(plane_faehig):
    assert tickets.readiness(PROJEKT) == ""


def test_readiness_meldet_fehlendes_repo_bei_github(github_faehig):
    settings.set_many({"ticket_target": "github"})
    assert "Repo" in tickets.readiness({**PROJEKT, "repo_full_name": ""})


def test_readiness_bei_ausgeschaltetem_sync():
    settings.set_many({"ticket_target": "off"})
    assert "ausgeschaltet" in tickets.readiness(PROJEKT)


# ---------------------------------------------------------------- Plane-Kürzel
@pytest.mark.parametrize("name,kuerzel", [
    ("Content Factory", "CONFAC"[:5]),
    ("Boardroom", "BOARD"),
    ("", "PROJ"),
])
def test_plane_kuerzel(name, kuerzel):
    assert plane_mod.identifier_from(name) == kuerzel
