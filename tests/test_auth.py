"""Session-Cookie und Brute-Force-Bremse."""
import time

import pytest

from app import auth


@pytest.fixture(autouse=True)
def mit_passwort(monkeypatch):
    monkeypatch.setattr(auth, "PASSWORD", "geheim")
    monkeypatch.setattr(auth, "ALLOW_ANONYMOUS", False)
    monkeypatch.setattr(auth, "_failures", {})


def test_frisches_token_ist_gueltig():
    assert auth.verify_token(auth.issue_token())


@pytest.mark.parametrize("token", [
    None, "", "kaputt", "1.2.3", "abc.def",
])
def test_muell_wird_abgelehnt(token):
    assert not auth.verify_token(token)


def test_manipulierte_signatur_faellt_durch():
    payload, _, signature = auth.issue_token().partition(".")
    assert not auth.verify_token(f"{payload}.{'0' * len(signature)}")


def test_abgelaufenes_token_faellt_durch(monkeypatch):
    monkeypatch.setattr(auth, "SESSION_TTL", -10)
    assert not auth.verify_token(auth.issue_token())


def test_verlaengern_ohne_neu_zu_signieren_geht_nicht():
    payload, _, signature = auth.issue_token().partition(".")
    weiter = str(int(payload) + 99999)
    assert not auth.verify_token(f"{weiter}.{signature}")


def test_falsches_passwort_zaehlt_und_sperrt():
    assert not auth.locked_out("1.2.3.4")
    for _ in range(auth.LOCKOUT_AFTER):
        assert not auth.check_password("falsch", "1.2.3.4")
    assert auth.locked_out("1.2.3.4")


def test_richtiges_passwort_raeumt_die_sperre_ab():
    for _ in range(3):
        auth.check_password("falsch", "5.6.7.8")
    assert auth.check_password("geheim", "5.6.7.8")
    assert not auth.locked_out("5.6.7.8")


def test_alte_fehlversuche_verfallen():
    auth._failures["9.9.9.9"] = [time.time() - auth.LOCKOUT_WINDOW - 1] * 50
    assert not auth.locked_out("9.9.9.9")


def test_ohne_passwort_ist_die_app_fehlkonfiguriert(monkeypatch):
    monkeypatch.setattr(auth, "PASSWORD", "")
    assert auth.misconfigured()
    monkeypatch.setattr(auth, "ALLOW_ANONYMOUS", True)
    assert not auth.misconfigured()
