"""Verbrauch und Kosten – die Zahlen im Dashboard müssen stimmen."""
import asyncio

import pytest

from app import settings, store, usage


@pytest.fixture(autouse=True)
def db(tmp_path):
    store.reset_for_tests(str(tmp_path / "usage.db"))
    settings.invalidate()


def _run(coro):
    return asyncio.run(coro)


def _buchen(**kwargs):
    vorgabe = dict(project_id="p1", meeting_id="m1", agent_id="kimi",
                   agent_name="Kimi", phase="gutachten", provider="hostyourai",
                   model="openai/kimi-k3", input_tokens=1000,
                   output_tokens=500)
    _run(usage.record(**{**vorgabe, **kwargs}))


# ---------------------------------------------------------------- Preise
def test_preistabelle_wird_geparst():
    settings.set_many({"model_prices":
                       "# Kommentar\nkimi-k3 = 0.30 / 1.20\n\nglm-5.2 = 0.5,2.0"})
    tabelle = usage.price_table()
    assert tabelle["kimi-k3"] == (0.30, 1.20)
    assert tabelle["glm-5.2"] == (0.5, 2.0)


def test_muellzeilen_werden_ignoriert():
    settings.set_many({"model_prices": "voelliger unsinn\n= = =\n"})
    assert usage.price_table() == {}


def test_eigene_preise_werden_gerechnet():
    settings.set_many({"model_prices": "kimi-k3 = 1.00 / 2.00"})
    # 1 Mio rein zu 1,00 + 0,5 Mio raus zu 2,00 = 2,00
    assert usage.compute_cost("openai/kimi-k3", 1_000_000, 500_000) == \
        pytest.approx(2.0)


def test_unbekanntes_modell_kostet_null_statt_geraten():
    assert usage.compute_cost("openai/voellig-erfunden-xyz", 1000, 500) == 0.0


def test_praefix_stoert_die_preiszuordnung_nicht():
    settings.set_many({"model_prices": "kimi-k3 = 1.00 / 1.00"})
    mit = usage.compute_cost("openai/kimi-k3", 1_000_000, 0)
    ohne = usage.compute_cost("kimi-k3", 1_000_000, 0)
    assert mit == ohne == pytest.approx(1.0)


def test_euro_umrechnung_folgt_dem_kurs():
    settings.set_many({"eur_per_usd": "0.5"})
    assert usage.eur(10) == 5.0
    settings.set_many({"eur_per_usd": "kaputt"})
    assert usage.eur(10) == pytest.approx(9.2)   # Default


# ---------------------------------------------------------------- Buchen
def test_verbrauch_landet_in_der_summe():
    settings.set_many({"model_prices": "kimi-k3 = 1.00 / 1.00"})
    _buchen()
    gesamt = _run(usage.report())["total"]
    assert gesamt["input_tokens"] == 1000
    assert gesamt["output_tokens"] == 500
    assert gesamt["calls"] == 1
    assert gesamt["billed_usd"] == pytest.approx(0.0015)


def test_claude_code_zaehlt_als_abo_nicht_als_rechnung():
    _buchen(provider="claude-code", model="claude-sonnet-4-6",
            agent_id="claude", agent_name="Claude", cost_usd=0.42)
    gesamt = _run(usage.report())["total"]
    assert gesamt["billed_usd"] == 0
    assert gesamt["included_usd"] == pytest.approx(0.42)


def test_geschaetzte_werte_werden_markiert():
    _buchen(estimated=True)
    assert _run(usage.report())["total"]["has_estimates"] == 1


def test_gemessene_werte_sind_nicht_markiert():
    _buchen()
    assert _run(usage.report())["total"]["has_estimates"] == 0


# ---------------------------------------------------------------- Auswerten
def test_aufschluesselung_je_agent():
    _buchen(agent_id="kimi", agent_name="Kimi", output_tokens=500)
    _buchen(agent_id="glm", agent_name="GLM", output_tokens=900)
    je_agent = {a["agent_id"]: a for a in _run(usage.report())["by_agent"]}
    assert je_agent["kimi"]["output_tokens"] == 500
    assert je_agent["glm"]["output_tokens"] == 900


def test_aufschluesselung_je_phase():
    _buchen(phase="gutachten")
    _buchen(phase="synthese")
    _buchen(phase="synthese")
    je_phase = {p["phase"]: p["calls"] for p in _run(usage.report())["by_phase"]}
    assert je_phase == {"gutachten": 1, "synthese": 2}


def test_projektfilter_trennt_sauber():
    _buchen(project_id="p1", output_tokens=100)
    _buchen(project_id="p2", output_tokens=900)
    assert _run(usage.report("p1"))["total"]["output_tokens"] == 100
    assert _run(usage.report())["total"]["output_tokens"] == 1000


def test_projektansicht_listet_die_meetings():
    projekt = store._create_project("P", "", repo_full_name="o/r")
    meeting = store._create_meeting(projekt["id"], "briefing")
    _buchen(project_id=projekt["id"], meeting_id=meeting["id"])
    meetings = _run(usage.report(projekt["id"]))["meetings"]
    assert len(meetings) == 1
    assert meetings[0]["output_tokens"] == 500


def test_gesamtansicht_nennt_projektnamen():
    projekt = store._create_project("Content Factory", "", repo_full_name="o/r")
    _buchen(project_id=projekt["id"])
    je_projekt = _run(usage.report())["by_project"]
    assert je_projekt[0]["name"] == "Content Factory"


def test_leerer_bericht_stuerzt_nicht_ab():
    bericht = _run(usage.report())
    assert bericht["total"]["calls"] == 0
    assert bericht["by_agent"] == []


def test_buchung_verschluckt_fehler_statt_das_meeting_zu_killen(monkeypatch):
    def kaputt(row):
        raise RuntimeError("Datenbank weg")
    monkeypatch.setattr(usage, "_record", kaputt)
    _buchen()   # darf nicht werfen
