"""Tests laufen hermetisch: keine echten Keys aus der Umgebung.

settings.get() fällt bewusst auf die .env zurück. In einer Entwicklungs-
oder CI-Umgebung mit gesetzten Variablen würden Tests dadurch je nach
Maschine unterschiedlich ausgehen – deshalb hier alles wegräumen.
"""
import pytest

from app import settings

LEAKY_ENV = [entry["env"] for entry in settings.SETTINGS_SPEC if entry["env"]] + [
    "REPO_ALLOWLIST", "BOARDROOM_PASSWORD", "BOARDROOM_SECRET",
    "BOARDROOM_ALLOW_ANONYMOUS", "BOARDROOM_DB",
]


@pytest.fixture(autouse=True)
def hermetische_umgebung(monkeypatch):
    for key in LEAKY_ENV:
        monkeypatch.delenv(key, raising=False)
    settings.invalidate()
    yield
    settings.invalidate()
