"""Mention-Parser, Team-Konsistenz und der Issue-Block des Chairmans."""
from app.config import AGENT_IDS, build_team
from app.mentions import HANDLES, extract_mentions
from app.pipeline import Meeting


def test_handles_decken_das_team_ab():
    assert set(HANDLES) == set(build_team()) == set(AGENT_IDS)


def test_mention_wird_erkannt():
    assert extract_mentions("@gpt bestätigst du das?", "claude") == ["gpt"]


def test_selbsterwaehnung_faellt_raus():
    assert extract_mentions("@claude ich meine mich", "claude") == []


def test_dedupliziert_und_deckelt_bei_zwei():
    text = "@gpt @kimi @gpt @qwen @glm"
    assert extract_mentions(text, "claude") == ["gpt", "kimi"]


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
