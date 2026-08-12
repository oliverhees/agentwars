"""Die Repo-URL kommt vom Client und landet in einem git-Aufruf –
das ist die Stelle, an der Fehler teuer werden."""
import pytest

from app import security
from app.security import RepoUrlError, redact, validate_repo_url


@pytest.fixture(autouse=True)
def allowlist(monkeypatch):
    monkeypatch.setattr(security, "REPO_ALLOWLIST", ["github.com", "git.example.de"])


@pytest.mark.parametrize("url", [
    "https://github.com/oliverhees/agentwars.git",
    "https://sub.github.com/team/repo.git",
    "ssh://git@github.com/team/repo.git",
    "git@github.com:team/repo.git",
    "https://user:token@github.com/team/private.git",
])
def test_erlaubte_urls(url):
    assert validate_repo_url(url) == url


@pytest.mark.parametrize("url,grund", [
    ("", "leer"),
    ("--upload-pack=/bin/sh", "option-injection"),
    ("file:///etc/passwd", "falsches schema"),
    ("/srv/lokales-repo", "kein schema"),
    ("https://evil.com/team/repo.git", "host nicht in allowlist"),
    ("https://notgithub.com/team/repo.git", "suffix-trick"),
    ("https://github.com.evil.com/repo.git", "praefix-trick"),
    ("https://192.168.1.10/repo.git", "ip-literal"),
    ("https://github.com/repo.git\nrm -rf /", "zeilenumbruch"),
    ("ftp://github.com/repo.git", "exotisches schema"),
])
def test_abgelehnte_urls(url, grund):
    with pytest.raises(RepoUrlError):
        validate_repo_url(url)


def test_leere_allowlist_blockt_alles(monkeypatch):
    monkeypatch.setattr(security, "REPO_ALLOWLIST", [])
    with pytest.raises(RepoUrlError, match="REPO_ALLOWLIST"):
        validate_repo_url("https://github.com/team/repo.git")


def test_redact_entfernt_token():
    out = redact("https://oliver:ghp_geheim@github.com/team/repo.git")
    assert "ghp_geheim" not in out
    assert out == "https://***@github.com/team/repo.git"
