"""Plane-Integration: Roadmap-Findings werden zu Issues, Updates zu Kommentaren.

Die Zugangsdaten kommen zur Laufzeit aus den Einstellungen, damit Plane
auf der Einstellungsseite hinterlegt werden kann statt nur in der `.env`.
"""
import html

import httpx

from . import settings

PRIORITIES = {"urgent", "high", "medium", "low", "none"}


def _md_to_html(text: str) -> str:
    return "<p>" + html.escape(text or "").replace("\n", "<br/>") + "</p>"


class PlaneClient:
    @property
    def enabled(self) -> bool:
        return all(settings.get(key) for key in
                   ("plane_base_url", "plane_api_key",
                    "plane_workspace", "plane_project_id"))

    @property
    def base(self) -> str:
        return (f"{settings.get('plane_base_url').rstrip('/')}"
                f"/api/v1/workspaces/{settings.get('plane_workspace')}"
                f"/projects/{settings.get('plane_project_id')}")

    @property
    def headers(self) -> dict:
        return {"X-API-Key": settings.get("plane_api_key"),
                "Content-Type": "application/json"}

    async def create_issue(self, name: str, description_md: str,
                           priority: str = "medium") -> dict:
        if priority not in PRIORITIES:
            priority = "medium"
        body = {"name": name[:250],
                "description_html": _md_to_html(description_md),
                "priority": priority}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{self.base}/issues/",
                                     json=body, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def add_comment(self, issue_id: str, comment_md: str) -> None:
        body = {"comment_html": _md_to_html(comment_md)}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{self.base}/issues/{issue_id}/comments/",
                                     json=body, headers=self.headers)
            resp.raise_for_status()

    async def check(self) -> tuple[bool, str]:
        """Verbindungstest für die Einstellungsseite."""
        if not self.enabled:
            return False, "Nicht vollständig konfiguriert."
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(f"{self.base}/issues/",
                                        params={"per_page": 1},
                                        headers=self.headers)
        except httpx.HTTPError as exc:
            return False, f"Plane nicht erreichbar ({exc})."
        if resp.status_code >= 400:
            return False, f"Plane antwortet mit {resp.status_code}."
        return True, "Verbindung steht."


plane = PlaneClient()
