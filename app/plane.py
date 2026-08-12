"""Plane-Integration: Roadmap-Findings werden zu Issues, Updates zu Kommentaren.

Plane ist das Standard-Ziel, weil die Planungsdaten damit auf deiner eigenen
Instanz bleiben. Jedes Boardroom-Projekt zeigt auf sein **eigenes**
Plane-Projekt – die globale Projekt-UUID in den Einstellungen ist nur noch
der Rückfall für Projekte, die keine eigene haben.
"""
import html
import re

import httpx

from . import settings

PRIORITIES = {"urgent", "high", "medium", "low", "none"}


class PlaneError(RuntimeError):
    """Fehler, dessen Text direkt beim Nutzer landen darf."""


def _md_to_html(text: str) -> str:
    return "<p>" + html.escape(text or "").replace("\n", "<br/>") + "</p>"


def identifier_from(name: str) -> str:
    """Plane will ein kurzes Kürzel je Projekt: 'Content Factory' -> 'CONFAC'."""
    words = re.findall(r"[A-Za-z0-9]+", name or "")
    if not words:
        return "PROJ"
    if len(words) == 1:
        return words[0][:5].upper()
    return "".join(w[:3] for w in words[:2]).upper()[:5]


class PlaneClient:
    @property
    def configured(self) -> bool:
        """Workspace erreichbar – reicht zum Auflisten und Anlegen."""
        return all(settings.get(key) for key in
                   ("plane_base_url", "plane_api_key", "plane_workspace"))

    @property
    def enabled(self) -> bool:
        """Zusätzlich ein Ziel-Projekt vorhanden."""
        return self.configured and bool(settings.get("plane_project_id"))

    @property
    def workspace_base(self) -> str:
        return (f"{settings.get('plane_base_url').rstrip('/')}"
                f"/api/v1/workspaces/{settings.get('plane_workspace')}")

    def project_base(self, project_id: str = "") -> str:
        target = project_id or settings.get("plane_project_id")
        if not target:
            raise PlaneError("Kein Plane-Projekt hinterlegt.")
        return f"{self.workspace_base}/projects/{target}"

    @property
    def headers(self) -> dict:
        return {"X-API-Key": settings.get("plane_api_key"),
                "Content-Type": "application/json"}

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        if not self.configured:
            raise PlaneError("Plane ist nicht konfiguriert.")
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                return await client.request(method, url,
                                            headers=self.headers, **kwargs)
            except httpx.HTTPError as exc:
                raise PlaneError(f"Plane nicht erreichbar ({exc}).") from exc

    @staticmethod
    def _fail(resp: httpx.Response, was: str) -> PlaneError:
        try:
            payload = resp.json()
            message = payload.get("error") or payload.get("detail") or ""
        except ValueError:
            message = ""
        return PlaneError(f"{was} fehlgeschlagen ({resp.status_code} {message}).")

    # ------------------------------------------------------------ Projekte
    async def projects(self) -> list[dict]:
        """Für das Dropdown beim Anlegen eines Boardroom-Projekts."""
        resp = await self._request("GET", f"{self.workspace_base}/projects/")
        if resp.status_code != 200:
            raise self._fail(resp, "Projektliste")
        payload = resp.json()
        entries = payload.get("results") if isinstance(payload, dict) else payload
        return [
            {"id": p.get("id", ""), "name": p.get("name", ""),
             "identifier": p.get("identifier", "")}
            for p in (entries or []) if isinstance(p, dict)
        ]

    async def create_project(self, name: str, identifier: str = "") -> dict:
        body = {"name": name[:250],
                "identifier": (identifier or identifier_from(name)).upper()[:12]}
        resp = await self._request("POST", f"{self.workspace_base}/projects/",
                                   json=body)
        if resp.status_code not in (200, 201):
            raise self._fail(resp, "Projekt anlegen")
        return resp.json()

    # ------------------------------------------------------------ Issues
    async def create_issue(self, name: str, description_md: str,
                           priority: str = "medium",
                           project_id: str = "") -> dict:
        if priority not in PRIORITIES:
            priority = "medium"
        body = {"name": name[:250],
                "description_html": _md_to_html(description_md),
                "priority": priority}
        resp = await self._request(
            "POST", f"{self.project_base(project_id)}/issues/", json=body)
        if resp.status_code not in (200, 201):
            raise self._fail(resp, "Issue anlegen")
        return resp.json()

    async def add_comment(self, issue_id: str, comment_md: str,
                          project_id: str = "") -> None:
        resp = await self._request(
            "POST", f"{self.project_base(project_id)}/issues/{issue_id}/comments/",
            json={"comment_html": _md_to_html(comment_md)})
        if resp.status_code not in (200, 201):
            raise self._fail(resp, "Kommentar anlegen")

    # ------------------------------------------------------------ Test
    async def check(self) -> tuple[bool, str]:
        """Verbindungstest für die Einstellungsseite."""
        if not self.configured:
            return False, "Nicht vollständig konfiguriert."
        try:
            found = await self.projects()
        except PlaneError as exc:
            return False, str(exc)
        return True, f"Verbindung steht ({len(found)} Projekte sichtbar)."


plane = PlaneClient()
