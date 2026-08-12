"""Team-Aufbau, Mention-Parser und der Issue-Block des Chairmans."""
import pytest

from app import settings, store
from app.config import DEFAULT_AGENTS, build_team
from app.mentions import extract_mentions
from app.pipeline import Meeting


@pytest.fixture(autouse=True)
def db(tmp_path):
    store.reset_for_tests(str(tmp_path / "board.db"))
    settings.invalidate()
    yield
    settings.invalidate()


def test_standardaufstellung_wird_gesaet():
    team = build_team()
    assert set(team) == {a["id"] for a in DEFAULT_AGENTS}
    assert settings.chairman_id() == "claude"


def test_handles_folgen_dem_team():
    assert extract_mentions("@gpt schau mal", "claude") == ["gpt"]
    settings.save_agent("gpt", {"enabled": False})
    assert extract_mentions("@gpt schau mal", "claude") == []


def test_prompt_setzt_sich_aus_grundregeln_und_rolle_zusammen():
    settings.set_many({"base_prompt": "GRUNDREGEL",
                       "mention_rules": "HANDLES: {handles}"})
    settings.save_agent("qwen", {"system_prompt": "ROLLE"})
    prompt = build_team()["qwen"].system_prompt
    assert prompt.startswith("GRUNDREGEL")
    assert "ROLLE" in prompt
    assert "@qwen" in prompt   # Platzhalter wurde ersetzt


def test_modell_bekommt_den_provider_praefix():
    settings.save_agent("kimi", {"provider": "openai", "model": "gpt-test"})
    assert build_team()["kimi"].model == "openai/gpt-test"


def test_memory_proxy_leitet_nur_router_modelle_um():
    settings.set_many({"memory_proxy_base_url": "http://memory:8088/v1",
                       "openai_api_key": "sk-test"})
    team = build_team()
    assert team["kimi"].api_base == "http://memory:8088/v1"
    assert team["gpt"].api_base is None


def test_nur_ein_chairman():
    settings.save_agent("glm", {"is_chairman": True})
    assert settings.chairman_id() == "glm"
    assert sum(a["is_chairman"] for a in settings.agents()) == 1


def test_zuruecksetzen_stellt_die_standardprompts_wieder_her():
    settings.save_agent("glm", {"system_prompt": "kaputt", "enabled": False})
    settings.reset_agents()
    glm = [a for a in settings.agents() if a["id"] == "glm"][0]
    assert glm["enabled"] == 1
    assert "Devil's Advocate" in glm["system_prompt"]


def test_selbsterwaehnung_faellt_raus():
    assert extract_mentions("@claude ich meine mich", "claude") == []


def test_dedupliziert_und_deckelt_bei_zwei():
    assert extract_mentions("@gpt @kimi @gpt @qwen @glm", "claude") == ["gpt", "kimi"]


def test_gross_kleinschreibung_egal():
    assert extract_mentions("@DeepSeek was meinst du?", "user") == ["deepseek"]


def test_kein_treffer_bei_teilwort():
    assert extract_mentions("schreib an @gptx", "claude") == []


def test_mail_adresse_ist_keine_erwaehnung():
    assert extract_mentions("melde dich bei team@glmx.de", "claude") == []


def test_issues_aus_json_block():
    text = ('Roadmap …\n\n```json\n'
            '[{"name": "Auth einbauen", "priority": "urgent"}]\n```')
    assert Meeting._extract_issues(text) == [
        {"name": "Auth einbauen", "priority": "urgent"}]


def test_issues_ohne_codefence_am_ende():
    assert Meeting._extract_issues('Text\n[{"name": "X"}]') == [{"name": "X"}]


def test_kaputtes_json_ergibt_leere_liste():
    assert Meeting._extract_issues('```json\n[{"name": ]\n```') == []


def test_kein_json_ergibt_leere_liste():
    assert Meeting._extract_issues("Nur Prosa, kein Block.") == []


# ---------------------------------------------------------------- Standards
def test_arbeitsstandards_stehen_in_jedem_prompt():
    """Ticketformat und Handwerk gelten fürs ganze Team, nicht je Rolle."""
    for spec in build_team().values():
        assert "Arbeitsstandards" in spec.system_prompt
        assert "Fertig ist es, wenn" in spec.system_prompt


def test_standards_stehen_vor_der_rolle():
    settings.set_many({"base_prompt": "GRUND", "standards": "STANDARD"})
    settings.save_agent("qwen", {"system_prompt": "ROLLE"})
    prompt = build_team()["qwen"].system_prompt
    assert prompt.index("GRUND") < prompt.index("STANDARD") < prompt.index("ROLLE")


def test_standards_sind_editierbar():
    settings.set_many({"standards": "NUR DAS HIER"})
    assert "NUR DAS HIER" in build_team()["glm"].system_prompt
    assert "Arbeitsstandards" not in build_team()["glm"].system_prompt


def test_ticketvertrag_verlangt_pruefbares_kriterium():
    from app.config import CHAIRMAN_JSON_CONTRACT
    assert "Fertig ist es, wenn" in CHAIRMAN_JSON_CONTRACT
    assert "urgent nur bei akuter Gefahr" in CHAIRMAN_JSON_CONTRACT
