"""Repo-Namen und die Klon-URL – hier steckt der Token drin."""
import pytest

from app import github as gh_mod
from app.github import GitHubError, slugify_repo_name, valid_full_name
from app.security import redact


@pytest.mark.parametrize("name,gueltig", [
    ("oliverhees/agentwars", True),
    ("Ac-me_1/repo.name", True),
    ("nurname", False),
    ("zu/viele/teile", False),
    ("owner/", False),
    ("owner/repo;rm -rf", False),
    ("owner/repo repo", False),
    ("", False),
])
def test_full_name_validierung(name, gueltig):
    assert valid_full_name(name) is gueltig


@pytest.mark.parametrize("eingabe,erwartet", [
    ("Content Factory", "content-factory"),
    ("Meine App!", "meine-app"),
    ("  ", "projekt"),
    ("Ümläüte", "ml-te"),
])
def test_repo_namen_werden_slugifiziert(eingabe, erwartet):
    assert slugify_repo_name(eingabe) == erwartet


def test_ohne_token_ist_der_client_aus(monkeypatch):
    monkeypatch.setattr(gh_mod, "GITHUB_TOKEN", "")
    client = gh_mod.GitHubClient()
    assert not client.enabled
    with pytest.raises(GitHubError, match="GITHUB_TOKEN"):
        client.clone_url("owner/repo")


def test_klon_url_traegt_den_token(monkeypatch):
    monkeypatch.setattr(gh_mod, "GITHUB_TOKEN", "ghp_geheim")
    url = gh_mod.GitHubClient().clone_url("owner/repo")
    assert url == "https://x-access-token:ghp_geheim@github.com/owner/repo.git"


def test_klon_url_wird_fuer_die_ausgabe_geschwaerzt(monkeypatch):
    monkeypatch.setattr(gh_mod, "GITHUB_TOKEN", "ghp_geheim")
    url = gh_mod.GitHubClient().clone_url("owner/repo")
    assert "ghp_geheim" not in redact(url)


def test_klon_url_passiert_die_allowlist(monkeypatch):
    from app import security
    monkeypatch.setattr(security, "REPO_ALLOWLIST", ["github.com"])
    monkeypatch.setattr(gh_mod, "GITHUB_TOKEN", "ghp_geheim")
    url = gh_mod.GitHubClient().clone_url("owner/repo")
    assert security.validate_repo_url(url) == url
