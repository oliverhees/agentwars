"""Einstellungen: Vorrang, Geheimnis-Behandlung und die Endpoints."""
import pytest
from fastapi.testclient import TestClient

from app import auth, main, settings, store


@pytest.fixture(autouse=True)
def db(tmp_path):
    store.reset_for_tests(str(tmp_path / "settings.db"))
    settings.invalidate()


@pytest.fixture
def angemeldet(monkeypatch):
    monkeypatch.setattr(auth, "PASSWORD", "geheim")
    monkeypatch.setattr(auth, "ALLOW_ANONYMOUS", False)
    monkeypatch.setattr(auth, "_failures", {})
    client = TestClient(main.app)
    client.post("/api/login", json={"password": "geheim"})
    return client


# ---------------------------------------------------------------- Vorrang
def test_default_gilt_wenn_nichts_gesetzt_ist():
    assert settings.get("hyai_base_url") == "https://hostyourai.com/api/v1"


def test_env_schlaegt_default(monkeypatch):
    monkeypatch.setenv("HYAI_BASE_URL", "https://aus-der-env")
    settings.invalidate()
    assert settings.get("hyai_base_url") == "https://aus-der-env"


def test_datenbank_schlaegt_env(monkeypatch):
    monkeypatch.setenv("HYAI_BASE_URL", "https://aus-der-env")
    settings.set_many({"hyai_base_url": "https://aus-der-db"})
    assert settings.get("hyai_base_url") == "https://aus-der-db"


def test_zahlen_werden_robust_gelesen():
    settings.set_many({"max_tokens_review": "4500"})
    assert settings.get_int("max_tokens_review") == 4500
    settings.set_many({"max_tokens_review": "keine zahl"})
    assert settings.get_int("max_tokens_review") == 3000  # Default


def test_unbekannte_schluessel_werden_ignoriert():
    settings.set_many({"gibt_es_nicht": "wert"})
    assert settings.get("gibt_es_nicht") == ""


# ---------------------------------------------------------------- Geheimnisse
def test_geheimnis_verlaesst_den_server_nicht():
    settings.set_many({"plane_api_key": "plane_geheim"})
    eintrag = [e for e in settings.public_view() if e["key"] == "plane_api_key"][0]
    assert eintrag["value"] == ""
    assert eintrag["is_set"] is True


def test_leeres_geheimnis_laesst_den_wert_stehen():
    settings.set_many({"plane_api_key": "plane_geheim"})
    settings.set_many({"plane_api_key": ""})
    assert settings.get("plane_api_key") == "plane_geheim"


def test_geheimnis_kann_ausdruecklich_geleert_werden():
    settings.set_many({"plane_api_key": "plane_geheim"})
    settings.set_many({"plane_api_key": settings.CLEAR})
    assert settings.get("plane_api_key") == ""


def test_normales_feld_darf_geleert_werden():
    settings.set_many({"plane_workspace": "team"})
    settings.set_many({"plane_workspace": ""})
    assert settings.get("plane_workspace") == ""


# ---------------------------------------------------------------- Endpoints
def test_einstellungen_brauchen_eine_session(monkeypatch):
    monkeypatch.setattr(auth, "PASSWORD", "geheim")
    monkeypatch.setattr(auth, "ALLOW_ANONYMOUS", False)
    client = TestClient(main.app)
    assert client.get("/api/settings").status_code == 401
    assert client.put("/api/settings", json={"values": {}}).status_code == 401
    assert client.get("/api/agents").status_code == 401


def test_einstellungen_lesen_und_schreiben(angemeldet):
    daten = angemeldet.get("/api/settings").json()
    assert "Plane" in daten["groups"]
    angemeldet.put("/api/settings",
                   json={"values": {"plane_workspace": "mein-team"}})
    assert settings.get("plane_workspace") == "mein-team"


def test_geheimnis_taucht_in_der_antwort_nie_auf(angemeldet):
    angemeldet.put("/api/settings",
                   json={"values": {"plane_api_key": "plane_geheim"}})
    text = angemeldet.get("/api/settings").text
    assert "plane_geheim" not in text


def test_agent_wird_gespeichert(angemeldet):
    resp = angemeldet.put("/api/agents/qwen",
                          json={"model": "qwen-neu", "system_prompt": "Andere Rolle"})
    assert resp.status_code == 200
    qwen = [a for a in settings.agents() if a["id"] == "qwen"][0]
    assert qwen["model"] == "qwen-neu"
    assert qwen["system_prompt"] == "Andere Rolle"


def test_unbekannter_agent_ist_404(angemeldet):
    assert angemeldet.put("/api/agents/niemand",
                          json={"model": "x"}).status_code == 404


def test_unbekannter_provider_wird_abgelehnt(angemeldet):
    resp = angemeldet.put("/api/agents/qwen", json={"provider": "erfunden"})
    assert resp.status_code == 422
    assert "erfunden" in resp.json()["error"]


def test_leere_aenderung_ist_422(angemeldet):
    assert angemeldet.put("/api/agents/qwen", json={}).status_code == 422


def test_zuruecksetzen_ueber_die_api(angemeldet):
    angemeldet.put("/api/agents/glm", json={"system_prompt": "kaputt"})
    assert angemeldet.post("/api/agents/reset").status_code == 200
    glm = [a for a in settings.agents() if a["id"] == "glm"][0]
    assert "Devil's Advocate" in glm["system_prompt"]


def test_team_endpoint_folgt_den_einstellungen(angemeldet):
    angemeldet.put("/api/agents/glm", json={"enabled": False})
    ids = [a["id"] for a in angemeldet.get("/api/team").json()["agents"]]
    assert "glm" not in ids and "claude" in ids


# ---------------------------------------------------------------- Modellsuche
from app.preflight import chat_models, suggest   # noqa: E402

KATALOG = [
    "moonshotai/Kimi-K3", "nvidia/DAM-3B", "Qwen/Qwen3.5-397B-A17B",
    "qwen3.5-27b", "qwen3.5-9b", "deepseek-ai/DeepSeek-V4-Pro",
    "deepseek-v3-0324", "zai-org/GLM-5.2", "zai-org/GLM-5.2-FP8",
    "BAAI/bge-m3", "openai/whisper-large-v3", "black-forest-labs/FLUX.1-dev",
    "nvidia/parakeet-tdt-1.1b", "Qwen/Qwen3-Embedding-8B", "zai-org/GLM-OCR",
    "hexgrad/Kokoro-82M", "nvidia/audio-codec-22khz",
]


@pytest.mark.parametrize("wunsch,erwartet", [
    ("openai/kimi-k3", "moonshotai/Kimi-K3"),
    ("openai/deepseek-v4-pro", "deepseek-ai/DeepSeek-V4-Pro"),
    ("openai/glm-5.2", "zai-org/GLM-5.2"),
])
def test_der_richtige_slug_steht_vorn(wunsch, erwartet):
    """difflib schlug für 'kimi-k3' vorher 'nvidia/DAM-3B' vor."""
    assert suggest(wunsch, KATALOG)[0] == erwartet


def test_teiltreffer_schlaegt_nur_aehnliche_zeichen():
    treffer = suggest("openai/qwen3.5", KATALOG)
    assert all("qwen3.5" in t.lower() for t in treffer)


def test_kein_treffer_bei_voellig_fremdem_namen():
    assert suggest("openai/voellig-erfunden", KATALOG) == []


def test_nicht_chat_modelle_fliegen_raus():
    gefiltert = chat_models(KATALOG)
    assert "moonshotai/Kimi-K3" in gefiltert
    for weg in ["BAAI/bge-m3", "openai/whisper-large-v3",
                "black-forest-labs/FLUX.1-dev", "Qwen/Qwen3-Embedding-8B",
                "zai-org/GLM-OCR", "hexgrad/Kokoro-82M",
                "nvidia/audio-codec-22khz", "nvidia/parakeet-tdt-1.1b"]:
        assert weg not in gefiltert


def test_vorschlaege_kommen_nie_aus_dem_nicht_chat_bereich():
    assert "openai/whisper-large-v3" not in suggest("openai/whisper", KATALOG)


# ---------------------------------------------------------------- Claude Code
def _diagnose(probe=False):
    import asyncio

    from app import claude_code
    return asyncio.run(claude_code.diagnose(probe))


def test_diagnose_zeigt_jede_stufe_einzeln():
    """'Token fehlt' allein verrät nicht, ob die CLI installiert ist."""
    bericht = _diagnose()
    assert set(bericht) >= {"cli", "token", "connection", "ready"}


def test_fehlender_token_wird_benannt():
    bericht = _diagnose()
    assert bericht["token"]["ok"] is False
    assert "Nicht hinterlegt" in bericht["token"]["detail"]


def test_api_key_statt_subscription_token_wird_erkannt():
    """Häufigster Fehler: sk-ant-api… eingetragen, das geht hier nicht."""
    settings.set_many({"claude_code_oauth_token": "sk-ant-api03-abcdef"})
    token = _diagnose()["token"]
    assert token["ok"] is False
    assert "API-Key" in token["detail"]


def test_gueltiger_token_wird_akzeptiert_und_nie_ausgegeben():
    settings.set_many({"claude_code_oauth_token": "sk-ant-oat01-supergeheim"})
    token = _diagnose()["token"]
    assert token["ok"] is True
    assert "supergeheim" not in token["detail"]
    assert token["source"] == "Einstellungen"


def test_verbindung_wird_ohne_knopfdruck_nicht_geprueft():
    assert _diagnose()["connection"]["detail"] == "Auf Knopfdruck prüfbar."


def test_diagnose_braucht_eine_session(monkeypatch):
    monkeypatch.setattr(auth, "PASSWORD", "geheim")
    monkeypatch.setattr(auth, "ALLOW_ANONYMOUS", False)
    assert TestClient(main.app).get("/api/claude-code").status_code == 401


def test_diagnose_ueber_die_api(angemeldet):
    bericht = angemeldet.get("/api/claude-code").json()
    assert "cli" in bericht and "token" in bericht


# ---------------------------------------------------------------- Modellkatalog
def _katalog():
    import asyncio

    from app import models
    return asyncio.run(models.catalog())


def test_katalog_kennt_alle_provider():
    assert set(_katalog()) == {"openai", "anthropic", "hostyourai", "claude-code"}


def test_ohne_key_bleibt_die_liste_leer_ohne_fehler():
    """Kein Key ist kein Fehler – das Feld bleibt einfach Freitext."""
    eintrag = _katalog()["openai"]
    assert eintrag["models"] == [] and eintrag["error"] == ""


def test_claude_code_bietet_immer_die_kurznamen():
    modelle = _katalog()["claude-code"]["models"]
    assert "sonnet" in modelle and "opus" in modelle
    assert "" in modelle          # leer = Standard der CLI


def test_openai_hilfsmodelle_werden_gefiltert():
    from app.models import OPENAI_NICHT_CHAT
    for weg in ["text-embedding-3-large", "whisper-1", "dall-e-3",
                "tts-1-hd", "omni-moderation-latest"]:
        assert OPENAI_NICHT_CHAT.search(weg), weg
    for bleibt in ["gpt-5.2", "gpt-4.6", "o3-pro"]:
        assert not OPENAI_NICHT_CHAT.search(bleibt), bleibt


def test_katalog_braucht_eine_session(monkeypatch):
    monkeypatch.setattr(auth, "PASSWORD", "geheim")
    monkeypatch.setattr(auth, "ALLOW_ANONYMOUS", False)
    assert TestClient(main.app).get("/api/models").status_code == 401


def test_katalog_ueber_die_api(angemeldet):
    daten = angemeldet.get("/api/models").json()
    assert "claude-code" in daten["providers"]


def test_ein_kaputter_provider_reisst_die_anderen_nicht_mit(monkeypatch):
    from app import models

    async def kaputt():
        raise RuntimeError("Netz weg")
    monkeypatch.setitem(models.QUELLEN, "openai", kaputt)
    katalog = _katalog()
    assert "Netz weg" in katalog["openai"]["error"]
    assert katalog["claude-code"]["models"]


# ---------------------------------------------------------------- Umbenennung
def test_alte_env_namen_gelten_weiter(monkeypatch):
    """Das System hieß Boardroom. Ein Update darf niemanden aussperren."""
    from app.config import env_any
    monkeypatch.delenv("AGENTWARS_PASSWORD", raising=False)
    monkeypatch.setenv("BOARDROOM_PASSWORD", "alt")
    assert env_any("AGENTWARS_PASSWORD", "BOARDROOM_PASSWORD") == "alt"


def test_neuer_name_schlaegt_den_alten(monkeypatch):
    from app.config import env_any
    monkeypatch.setenv("BOARDROOM_PASSWORD", "alt")
    monkeypatch.setenv("AGENTWARS_PASSWORD", "neu")
    assert env_any("AGENTWARS_PASSWORD", "BOARDROOM_PASSWORD") == "neu"


def test_vorhandene_alte_datenbank_wird_weiterbenutzt(tmp_path, monkeypatch):
    """Wer schon Projekte hat, soll sie nach dem Update wiederfinden."""
    from app import store
    alt = tmp_path / "boardroom.db"
    alt.touch()
    monkeypatch.setenv("AGENTWARS_DB", str(tmp_path / "agentwars.db"))
    assert store._resolve_db_path() == str(alt)


def test_ohne_alte_datenbank_gilt_der_neue_pfad(tmp_path, monkeypatch):
    from app import store
    neu = tmp_path / "agentwars.db"
    monkeypatch.setenv("AGENTWARS_DB", str(neu))
    assert store._resolve_db_path() == str(neu)
