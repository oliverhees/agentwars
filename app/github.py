"""GitHub-Anbindung: das Repo ist der Anker jedes Projekts.

Regel in AgentWars: Ein Projekt ohne Repo gibt es nicht. Ist eins da, wird
es analysiert. Ist keins da, legt AgentWars es an und das Board fängt
bei "leeres Repo, worum geht's?" an.

Nur ein Personal Access Token nötig (`GITHUB_TOKEN`, Scope `repo`).
Der Token wird ausschließlich hier und in der Klon-URL benutzt und taucht
nirgends im Chat oder im Log auf – dafür sorgt security.redact().
"""
import re

import httpx

from . import settings


def token() -> str:
    return settings.get("github_token")


def api_url() -> str:
    return settings.get("github_api").rstrip("/")


def host() -> str:
    return settings.get("github_host")

# owner/repo – GitHub erlaubt in beiden Teilen nur diese Zeichen.
FULL_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
REPO_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class GitHubError(RuntimeError):
    """Fehler, dessen Text direkt beim Nutzer landen darf."""


def valid_full_name(full_name: str) -> bool:
    return bool(FULL_NAME_RE.match((full_name or "").strip()))


def slugify_repo_name(name: str) -> str:
    """'Meine Content Factory!' -> 'meine-content-factory'."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip()).strip("-.")
    return (slug or "projekt").lower()[:100]


class GitHubClient:
    @property
    def enabled(self) -> bool:
        return bool(token())

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _require(self) -> None:
        if not self.enabled:
            raise GitHubError(
                "Kein GitHub-Token hinterlegt – trag ihn unter /settings ein (Abschnitt GitHub).")

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        self._require()
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                return await client.request(
                    method, f"{api_url()}{path}",
                    headers=self._headers(), **kwargs)
            except httpx.HTTPError as exc:
                raise GitHubError(f"GitHub nicht erreichbar ({exc}).") from exc

    @staticmethod
    def _fail(resp: httpx.Response, was: str) -> GitHubError:
        try:
            message = resp.json().get("message", "")
        except ValueError:
            message = ""
        return GitHubError(f"{was} fehlgeschlagen ({resp.status_code} {message}).")

    # ------------------------------------------------------------ Lesen
    async def me(self) -> dict:
        resp = await self._request("GET", "/user")
        if resp.status_code != 200:
            raise self._fail(resp, "GitHub-Login")
        return resp.json()

    async def get_repo(self, full_name: str) -> dict | None:
        """None heißt: gibt es (für diesen Token) nicht."""
        if not valid_full_name(full_name):
            raise GitHubError(f"'{full_name}' ist kein gültiges owner/repo.")
        resp = await self._request("GET", f"/repos/{full_name}")
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise self._fail(resp, "Repo lesen")
        return resp.json()

    async def list_repos(self, limit: int = 50) -> list[dict]:
        resp = await self._request(
            "GET", "/user/repos",
            params={"per_page": min(limit, 100), "sort": "pushed"})
        if resp.status_code != 200:
            raise self._fail(resp, "Repo-Liste")
        return [
            {"full_name": r.get("full_name", ""),
             "private": bool(r.get("private")),
             "description": r.get("description") or "",
             "pushed_at": r.get("pushed_at") or ""}
            for r in resp.json()
        ]

    async def is_empty(self, full_name: str) -> bool:
        """Frisch angelegtes Repo ohne Commits – dann startet das Board
        nicht mit einer Analyse, sondern mit 'worum geht's?'."""
        resp = await self._request("GET", f"/repos/{full_name}/commits",
                                   params={"per_page": 1})
        if resp.status_code == 409:   # GitHub-Code für "Repository is empty"
            return True
        if resp.status_code != 200:
            return False
        return not resp.json()

    # ------------------------------------------------------------ Schreiben
    async def create_repo(self, name: str, description: str = "",
                          private: bool = True) -> dict:
        if not REPO_NAME_RE.match(name or ""):
            raise GitHubError(f"'{name}' ist kein gültiger Repo-Name.")
        resp = await self._request(
            "POST", "/user/repos",
            json={"name": name, "description": description[:350],
                  "private": private, "auto_init": True})
        if resp.status_code != 201:
            raise self._fail(resp, "Repo anlegen")
        return resp.json()

    async def create_issue(self, full_name: str, title: str, body: str,
                           labels: list[str] | None = None) -> dict:
        if not valid_full_name(full_name):
            raise GitHubError(f"'{full_name}' ist kein gültiges owner/repo.")
        payload: dict = {"title": title[:250], "body": body[:60000]}
        if labels:
            payload["labels"] = labels
        resp = await self._request("POST", f"/repos/{full_name}/issues",
                                   json=payload)
        if resp.status_code != 201:
            raise self._fail(resp, "Issue anlegen")
        return resp.json()

    async def comment_issue(self, full_name: str, number: int,
                            body: str) -> None:
        resp = await self._request(
            "POST", f"/repos/{full_name}/issues/{number}/comments",
            json={"body": body[:60000]})
        if resp.status_code != 201:
            raise self._fail(resp, "Kommentar anlegen")

    # ------------------------------------------------------------ Klonen
    def clone_url(self, full_name: str) -> str:
        """URL mit Token, damit auch private Repos geklont werden können.
        Niemals ungefiltert loggen – security.redact() nutzen."""
        self._require()
        return f"https://x-access-token:{token()}@{host()}/{full_name}.git"

    @staticmethod
    def web_url(full_name: str) -> str:
        return f"https://{host()}/{full_name}"


github = GitHubClient()
