"""Plane-Integration: Roadmap-Findings werden zu Issues, Updates zu Kommentaren."""
import html

import httpx

from .config import PLANE_API_KEY, PLANE_BASE_URL, PLANE_PROJECT_ID, PLANE_WORKSPACE

PRIORITIES = {"urgent", "high", "medium", "low", "none"}


class PlaneClient:
    def __init__(self) -> None:
        self.enabled = bool(PLANE_BASE_URL and PLANE_API_KEY
                            and PLANE_WORKSPACE and PLANE_PROJECT_ID)
        self.base = (
            f"{PLANE_BASE_URL.rstrip('/')}/api/v1/workspaces/"
            f"{PLANE_WORKSPACE}/projects/{PLANE_PROJECT_ID}"
        )
        self.headers = {"X-API-Key": PLANE_API_KEY,
                        "Content-Type": "application/json"}

    async def create_issue(self, name: str, description_md: str,
                           priority: str = "medium") -> dict:
        if priority not in PRIORITIES:
            priority = "medium"
        body = {
            "name": name[:250],
            "description_html": "<p>" + html.escape(description_md)
            .replace("\n", "<br/>") + "</p>",
            "priority": priority,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{self.base}/issues/",
                                     json=body, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def add_comment(self, issue_id: str, comment_md: str) -> None:
        body = {"comment_html": "<p>" + html.escape(comment_md)
                .replace("\n", "<br/>") + "</p>"}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{self.base}/issues/{issue_id}/comments/",
                                     json=body, headers=self.headers)
            resp.raise_for_status()


plane = PlaneClient()
