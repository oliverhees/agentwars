"""Validierung der Repo-URL, bevor irgendwas geklont wird.

Der Klon-Endpoint nimmt eine URL vom Client entgegen und gibt sie an `git`
weiter – das ist die klassische Stelle für Option-Injection (`--upload-pack=…`),
für `file://`-Zugriffe auf den Host und für SSRF ins interne Netz. Deshalb:
Allowlist statt Blocklist, und alles was nicht sauber parst, fliegt raus.
"""
import ipaddress
import re
from urllib.parse import urlsplit

from .config import env

# Kommaseparierte Hosts, z. B. "github.com,git.deine-domain.de".
# Leer = Klonen komplett deaktiviert (fail-closed).
REPO_ALLOWLIST = [
    host.strip().lower().lstrip(".")
    for host in env("REPO_ALLOWLIST").split(",")
    if host.strip()
]

ALLOWED_SCHEMES = {"https", "ssh"}
# scp-artige Kurzform: git@host:owner/repo.git
SCP_LIKE = re.compile(r"^(?P<user>[A-Za-z0-9._-]+)@(?P<host>[A-Za-z0-9.-]+):(?P<path>[^\s]+)$")


class RepoUrlError(ValueError):
    """Die URL darf nicht geklont werden – die Nachricht geht an den Nutzer."""


def _host_allowed(host: str) -> bool:
    host = host.lower()
    return any(host == entry or host.endswith("." + entry)
               for entry in REPO_ALLOWLIST)


def _reject_ip_literal(host: str) -> None:
    """Rohe IPs erlauben SSRF ins interne Netz, auch wenn jemand sie
    versehentlich in die Allowlist schreibt."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return
    raise RepoUrlError("Rohe IP-Adressen sind als Repo-Quelle nicht erlaubt.")


def validate_repo_url(raw: str) -> str:
    """Gibt die geprüfte URL zurück oder wirft RepoUrlError."""
    url = (raw or "").strip()
    if not url:
        raise RepoUrlError("Keine Repo-URL angegeben.")
    if not REPO_ALLOWLIST:
        raise RepoUrlError(
            "Repo-Klonen ist deaktiviert: REPO_ALLOWLIST ist leer. "
            "Trag in der .env die erlaubten Hosts ein, z. B. REPO_ALLOWLIST=github.com")
    if len(url) > 2000:
        raise RepoUrlError("Repo-URL ist unplausibel lang.")
    if any(ch in url for ch in "\r\n\t\0 "):
        raise RepoUrlError("Repo-URL enthält unerlaubte Zeichen.")
    if url.startswith("-"):
        # git würde das als Kommandozeilen-Option lesen
        raise RepoUrlError("Repo-URL darf nicht mit '-' beginnen.")

    scp = SCP_LIKE.match(url)
    if scp:
        host = scp.group("host")
    else:
        parts = urlsplit(url)
        if parts.scheme.lower() not in ALLOWED_SCHEMES:
            raise RepoUrlError(
                f"Nur {'/'.join(sorted(ALLOWED_SCHEMES))} sind erlaubt – "
                f"'{parts.scheme or 'kein Schema'}' nicht.")
        host = parts.hostname or ""
        if not host:
            raise RepoUrlError("Repo-URL hat keinen Host.")

    _reject_ip_literal(host)
    if not _host_allowed(host):
        raise RepoUrlError(
            f"Host '{host}' steht nicht in REPO_ALLOWLIST "
            f"({', '.join(REPO_ALLOWLIST)}).")
    return url


def redact(url: str) -> str:
    """URL fürs Log/den Chat – ein eingebettetes Token darf nirgends auftauchen."""
    return re.sub(r"//[^/@\s]+@", "//***@", url or "")
