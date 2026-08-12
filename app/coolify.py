"""Coolify-Anbindung: der Realitätscheck nach der Umsetzung.

Bewusst die REST-API und nicht MCP: der Boardroom ist selbst ein Server,
der HTTP sprechen kann. Ein MCP-Server dazwischen wäre ein zusätzlicher
Prozess, eine zusätzliche Auth-Schicht und ein zusätzlicher Ausfallpunkt
für drei Endpunkte, die wir direkt aufrufen können.

Zugangsdaten kommen aus den Einstellungen (Coolify → Keys & Tokens → API).
"""
import httpx

from . import settings


class CoolifyError(RuntimeError):
    """Fehler, dessen Text direkt beim Nutzer landen darf."""


class CoolifyClient:
    @property
    def enabled(self) -> bool:
        return bool(settings.get("coolify_base_url")
                    and settings.get("coolify_token"))

    @property
    def base(self) -> str:
        return f"{settings.get('coolify_base_url').rstrip('/')}/api/v1"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {settings.get('coolify_token')}",
                "Accept": "application/json"}

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if not self.enabled:
            raise CoolifyError("Coolify ist nicht konfiguriert.")
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                return await client.request(method, f"{self.base}{path}",
                                            headers=self._headers(), **kwargs)
            except httpx.HTTPError as exc:
                raise CoolifyError(f"Coolify nicht erreichbar ({exc}).") from exc

    @staticmethod
    def _fail(resp: httpx.Response, was: str) -> CoolifyError:
        try:
            message = resp.json().get("message", "")
        except ValueError:
            message = ""
        return CoolifyError(f"{was} fehlgeschlagen ({resp.status_code} {message}).")

    async def check(self) -> tuple[bool, str]:
        """Verbindungstest für die Einstellungsseite."""
        if not self.enabled:
            return False, "Nicht vollständig konfiguriert."
        try:
            resp = await self._request("GET", "/version")
        except CoolifyError as exc:
            return False, str(exc)
        if resp.status_code >= 400:
            return False, f"Coolify antwortet mit {resp.status_code}."
        return True, f"Verbindung steht (Coolify {resp.text.strip()[:40]})."

    async def applications(self) -> list[dict]:
        resp = await self._request("GET", "/applications")
        if resp.status_code != 200:
            raise self._fail(resp, "Anwendungsliste")
        payload = resp.json()
        entries = payload if isinstance(payload, list) else payload.get("data", [])
        return [
            {"uuid": a.get("uuid", ""), "name": a.get("name", ""),
             "fqdn": a.get("fqdn") or "", "status": a.get("status") or ""}
            for a in entries if isinstance(a, dict)
        ]

    async def deploy(self, app_uuid: str, force: bool = False) -> dict:
        """Stößt ein Deployment an und gibt die Deployment-Referenz zurück.
        Die Anwendung kommt vom Projekt – eine globale Standard-App gibt es
        bewusst nicht, jedes Projekt deployt sich selbst."""
        uuid = (app_uuid or "").strip()
        if not uuid:
            raise CoolifyError("Keine Coolify-Anwendung angegeben.")
        resp = await self._request(
            "GET", "/deploy", params={"uuid": uuid,
                                      "force": "true" if force else "false"})
        if resp.status_code >= 400:
            raise self._fail(resp, "Deployment starten")
        return resp.json()

    async def deployment(self, deployment_uuid: str) -> dict:
        resp = await self._request("GET", f"/deployments/{deployment_uuid}")
        if resp.status_code != 200:
            raise self._fail(resp, "Deployment-Status")
        return resp.json()


coolify = CoolifyClient()
