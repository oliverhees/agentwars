"""Wohin die Roadmap des Chairmans als Tickets wandert.

Ein Ziel, zwei mögliche Systeme – die Pipeline muss davon nichts wissen:

* **Plane** (Standard): Planungsdaten bleiben auf deiner Instanz, also DSGVO-
  konform. Jedes Boardroom-Projekt zeigt auf sein eigenes Plane-Projekt.
* **GitHub Issues**: enger am Code, `Fixes #12` in einem PR schließt das
  Ticket von selbst. Dafür liegen die Planungsdaten bei GitHub.

Beides gleichzeitig geht auch – dann ist Plane die Wahrheit und GitHub die
Arbeitsansicht.
"""
from . import settings
from .github import GitHubError, github
from .plane import PlaneError, plane

# Plane kennt Prioritäten nativ, GitHub nur Labels.
GITHUB_PRIORITY_LABELS = {
    "urgent": "prio:urgent", "high": "prio:high",
    "medium": "prio:medium", "low": "prio:low",
}

TARGETS = {
    "plane": "Plane (Standard, bleibt auf deiner Instanz)",
    "github": "GitHub Issues (eng am Code)",
    "both": "Beides – Plane führend, GitHub als Arbeitsansicht",
    "off": "Keine Tickets anlegen",
}


def target() -> str:
    value = settings.get("ticket_target")
    return value if value in TARGETS else "plane"


def _plane_project(project: dict) -> str:
    """Projekt-eigenes Plane-Projekt, sonst der globale Rückfall."""
    return project.get("plane_project_id") or settings.get("plane_project_id")


async def _to_plane(issue: dict, project: dict) -> str:
    created = await plane.create_issue(
        issue.get("name", "Unbenannt"),
        issue.get("description", ""),
        issue.get("priority", "medium"),
        project_id=_plane_project(project),
    )
    return f"Plane: {issue.get('name')} (#{created.get('sequence_id', '?')})"


async def _to_github(issue: dict, project: dict) -> str:
    repo = project.get("repo_full_name", "")
    if not repo:
        raise GitHubError("Projekt hat kein Repo.")
    label = GITHUB_PRIORITY_LABELS.get(issue.get("priority", "medium"))
    created = await github.create_issue(
        repo, issue.get("name", "Unbenannt"), issue.get("description", ""),
        labels=[label] if label else None)
    return f"GitHub: {issue.get('name')} (#{created.get('number', '?')})"


async def sync(issues: list[dict], project: dict) -> tuple[list[str], list[str]]:
    """Legt die Tickets an. Gibt (Erfolge, Fehler) als lesbare Zeilen zurück."""
    where = target()
    if where == "off" or not issues:
        return [], []

    systems = []
    if where in ("plane", "both"):
        systems.append(("Plane", _to_plane))
    if where in ("github", "both"):
        systems.append(("GitHub", _to_github))

    done: list[str] = []
    failed: list[str] = []
    for issue in issues:
        for label, handler in systems:
            try:
                done.append(await handler(issue, project))
            except (PlaneError, GitHubError) as exc:
                failed.append(f"{label} – '{issue.get('name')}': {exc}")
            except Exception as exc:  # Netzwerk, unerwartete Antwort …
                failed.append(f"{label} – '{issue.get('name')}': {exc}")
    return done, failed


def readiness(project: dict) -> str:
    """Warum es (nicht) losgehen kann – als Satz für den Chat."""
    where = target()
    if where == "off":
        return "Ticket-Sync ist ausgeschaltet."
    fehlt = []
    if where in ("plane", "both"):
        if not plane.configured:
            fehlt.append("Plane ist nicht konfiguriert")
        elif not _plane_project(project):
            fehlt.append("dem Projekt fehlt ein Plane-Projekt")
    if where in ("github", "both"):
        if not github.enabled:
            fehlt.append("GitHub-Token fehlt")
        elif not project.get("repo_full_name"):
            fehlt.append("dem Projekt fehlt ein Repo")
    return "; ".join(fehlt)
