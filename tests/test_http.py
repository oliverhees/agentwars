"""Der Türsteher: kommt wirklich niemand ohne Session rein?"""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import auth, main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "PASSWORD", "geheim")
    monkeypatch.setattr(auth, "ALLOW_ANONYMOUS", False)
    monkeypatch.setattr(auth, "_failures", {})
    return TestClient(main.app)


def test_ohne_passwort_verweigert_die_app_den_dienst(monkeypatch):
    monkeypatch.setattr(auth, "PASSWORD", "")
    monkeypatch.setattr(auth, "ALLOW_ANONYMOUS", False)
    resp = TestClient(main.app).get("/api/team")
    assert resp.status_code == 503
    assert "BOARDROOM_PASSWORD" in resp.json()["error"]


@pytest.mark.parametrize("path", ["/api/team", "/api/preflight"])
def test_api_ohne_session_ist_dicht(client, path):
    assert client.get(path).status_code == 401


def test_start_ohne_session_ist_dicht(client):
    resp = client.post("/api/start", json={"briefing": "egal"})
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


def test_fremdes_repo_wird_vor_dem_start_abgelehnt(client, monkeypatch):
    from app import security
    monkeypatch.setattr(security, "REPO_ALLOWLIST", ["github.com"])
    client.post("/api/login", json={"password": "geheim"})
    resp = client.post("/api/start", json={"briefing": "Review bitte",
                                           "git_url": "https://evil.com/x.git"})
    assert resp.status_code == 422
    assert "REPO_ALLOWLIST" in resp.json()["error"]
