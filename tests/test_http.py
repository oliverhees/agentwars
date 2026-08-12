"""Der Türsteher: kommt wirklich niemand ohne Session rein?"""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import auth, github, main, settings, store


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "PASSWORD", "geheim")
    monkeypatch.setattr(auth, "ALLOW_ANONYMOUS", False)
    monkeypatch.setattr(auth, "_failures", {})
    store.reset_for_tests(str(tmp_path / "http.db"))
    return TestClient(main.app)


@pytest.fixture
def angemeldet(client):
    client.post("/api/login", json={"password": "geheim"})
    return client


def test_ohne_passwort_verweigert_die_app_den_dienst(monkeypatch):
    monkeypatch.setattr(auth, "PASSWORD", "")
    monkeypatch.setattr(auth, "ALLOW_ANONYMOUS", False)
    resp = TestClient(main.app).get("/api/team")
    assert resp.status_code == 503
    assert "BOARDROOM_PASSWORD" in resp.json()["error"]


@pytest.mark.parametrize("path", ["/api/team", "/api/preflight",
                                  "/api/projects", "/api/github"])
def test_api_ohne_session_ist_dicht(client, path):
    assert client.get(path).status_code == 401


def test_start_ohne_session_ist_dicht(client):
    resp = client.post("/api/start", json={"project_id": "x", "briefing": "egal"})
    assert resp.status_code == 401


def test_wurzel_zeigt_den_login(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "ANMELDEN" in resp.text


def test_falsches_passwort_setzt_kein_cookie(client):
    resp = client.post("/api/login", json={"password": "daneben"})
    assert resp.status_code == 401
    assert auth.COOKIE_NAME not in resp.cookies


def test_login_oeffnet_die_tuer(client):
    assert client.post("/api/login", json={"password": "geheim"}).status_code == 200
    assert client.get("/api/team").status_code == 200
    assert client.get("/").status_code == 200


def test_logout_schliesst_wieder_ab(client):
    client.post("/api/login", json={"password": "geheim"})
    client.post("/api/logout")
    assert client.get("/api/team").status_code == 401


def test_zu_viele_fehlversuche_sperren(client):
    for _ in range(auth.LOCKOUT_AFTER):
        client.post("/api/login", json={"password": "daneben"})
    resp = client.post("/api/login", json={"password": "geheim"})
    assert resp.status_code == 429


def test_healthz_ist_oeffentlich(client):
    assert client.get("/healthz").json() == {
        "ok": True, "auth": True, "configured": True}


def test_websocket_ohne_session_wird_zugemacht(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws"):
            pass


def test_websocket_mit_session_verbindet(client):
    client.post("/api/login", json={"password": "geheim"})
    with client.websocket_connect("/ws"):
        pass


def test_projektliste_startet_leer(angemeldet):
    assert angemeldet.get("/api/projects").json() == {"projects": []}


def test_ohne_github_kein_projekt(angemeldet):
    resp = angemeldet.post("/api/projects", json={"name": "Content Factory"})
    assert resp.status_code == 422
    assert "/settings" in resp.json()["error"]


def test_projekt_ohne_repo_wird_abgelehnt(angemeldet):
    settings.set_many({"github_token": "ghp_test"})
    resp = angemeldet.post("/api/projects", json={"name": "Ohne Repo"})
    assert resp.status_code == 422
    assert "owner/name" in resp.json()["error"]


def test_projekt_mit_unbekanntem_repo_wird_abgelehnt(angemeldet, monkeypatch):
    async def kein_repo(self, full_name):
        return None
    settings.set_many({"github_token": "ghp_test"})
    monkeypatch.setattr(github.GitHubClient, "get_repo", kein_repo)
    resp = angemeldet.post("/api/projects", json={
        "name": "P", "repo_full_name": "oliverhees/gibtsnicht"})
    assert resp.status_code == 404


def test_projekt_mit_vorhandenem_repo_wird_angelegt(angemeldet, monkeypatch):
    async def repo_da(self, full_name):
        return {"full_name": full_name}
    settings.set_many({"github_token": "ghp_test"})
    monkeypatch.setattr(github.GitHubClient, "get_repo", repo_da)
    resp = angemeldet.post("/api/projects", json={
        "name": "Content Factory", "briefing": "Los geht's",
        "repo_full_name": "oliverhees/content"})
    assert resp.status_code == 200
    projekt = resp.json()["project"]
    assert projekt["repo_full_name"] == "oliverhees/content"
    assert angemeldet.get("/api/projects").json()["projects"][0]["id"] == projekt["id"]


def test_repo_wird_bei_bedarf_angelegt(angemeldet, monkeypatch):
    gesehen = {}

    async def anlegen(self, name, description="", private=True):
        gesehen["name"] = name
        gesehen["private"] = private
        return {"full_name": f"oliverhees/{name}"}

    settings.set_many({"github_token": "ghp_test"})
    monkeypatch.setattr(github.GitHubClient, "create_repo", anlegen)
    resp = angemeldet.post("/api/projects", json={
        "name": "Content Factory", "create_repo": True})
    assert resp.status_code == 200
    assert gesehen == {"name": "content-factory", "private": True}
    assert resp.json()["project"]["repo_full_name"] == "oliverhees/content-factory"


def test_start_ohne_projekt_ist_404(angemeldet):
    resp = angemeldet.post("/api/start", json={"project_id": "gibtsnicht"})
    assert resp.status_code == 404


def test_start_ohne_briefing_ist_422(angemeldet):
    projekt = store._create_project("P", "", repo_full_name="o/r")
    resp = angemeldet.post("/api/start", json={"project_id": projekt["id"]})
    assert resp.status_code == 422


def test_meetings_eines_unbekannten_projekts_sind_404(angemeldet):
    assert angemeldet.get("/api/projects/gibtsnicht/meetings").status_code == 404


def test_archivierter_verlauf_wird_geliefert(angemeldet):
    projekt = store._create_project("P", "", repo_full_name="o/r")
    meeting = store._create_meeting(projekt["id"], "b")
    store._append_event(meeting["id"], {"type": "system", "text": "archiviert"})
    events = angemeldet.get(f"/api/meetings/{meeting['id']}/events").json()["events"]
    assert events[0]["text"] == "archiviert"


def test_plane_projekte_ohne_konfiguration(angemeldet):
    daten = angemeldet.get("/api/plane/projects").json()
    assert daten["configured"] is False
    assert daten["projects"] == []


def test_verknuepfungen_lassen_sich_nachtragen(angemeldet):
    projekt = store._create_project("P", "", repo_full_name="o/r")
    resp = angemeldet.put(f"/api/projects/{projekt['id']}",
                          json={"plane_project_id": "plane-uuid",
                                "coolify_app_uuid": "app-uuid"})
    assert resp.status_code == 200
    aktualisiert = resp.json()["project"]
    assert aktualisiert["plane_project_id"] == "plane-uuid"
    assert aktualisiert["coolify_app_uuid"] == "app-uuid"


def test_verknuepfung_eines_unbekannten_projekts_ist_404(angemeldet):
    assert angemeldet.put("/api/projects/gibtsnicht",
                          json={"plane_project_id": "x"}).status_code == 404


def test_deploy_ohne_zugeordnete_anwendung_ist_422(angemeldet):
    projekt = store._create_project("P", "", repo_full_name="o/r")
    resp = angemeldet.post("/api/coolify/deploy",
                           json={"project_id": projekt["id"]})
    assert resp.status_code == 422
    assert "Coolify-Anwendung" in resp.json()["error"]
