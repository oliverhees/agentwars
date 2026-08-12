"""Projekt einlesen: Git-Repo klonen, Dateibaum + relevante Dateien packen."""
import os
import shutil
import subprocess
import tempfile

from . import settings
from .security import redact, validate_repo_url

SKIP_DIRS = {
    ".git", "node_modules", ".next", "dist", "build", "__pycache__",
    ".venv", "venv", "vendor", ".cache", "coverage", ".turbo", ".idea",
}
SKIP_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".woff",
    ".woff2", ".ttf", ".eot", ".mp4", ".mp3", ".zip", ".gz", ".pdf",
    ".lock", ".map", ".min.js", ".min.css",
}
# Reihenfolge = Priorität beim Befüllen des Kontextbudgets
PRIORITY_NAMES = ["readme", "package.json", "pyproject", "docker", "compose"]
CODE_EXT = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".svelte", ".go", ".rs",
    ".php", ".rb", ".java", ".kt", ".sql", ".prisma", ".css", ".scss",
    ".html", ".yml", ".yaml", ".toml", ".json", ".md", ".env.example",
}


def _git_env() -> dict:
    """git darf beim Klonen nicht interaktiv werden und keine exotischen
    Transporte benutzen – sonst hängt der Request oder führt Code aus."""
    e = os.environ.copy()
    e["GIT_TERMINAL_PROMPT"] = "0"     # kein Passwort-Prompt → kein Hänger
    e["GIT_ASKPASS"] = "/bin/true"
    e["GIT_CONFIG_NOSYSTEM"] = "1"
    return e


def clone_repo(git_url: str) -> str:
    """Klont ein Repo aus der Allowlist. Wirft RepoUrlError bei fremden Quellen."""
    url = validate_repo_url(git_url)
    target = tempfile.mkdtemp(prefix="review_")
    try:
        subprocess.run(
            [
                "git",
                # ext::/ führt beliebige Shell-Kommandos aus, wenn ein Repo
                # sie in .gitmodules o. ä. unterjubelt – hart abschalten.
                "-c", "protocol.ext.allow=never",
                "-c", "protocol.file.allow=never",
                "-c", "credential.helper=",
                "clone", "--depth", "1", "--no-tags",
                "--single-branch", "--recurse-submodules=no",
                "--", url, target,          # '--' beendet die Optionsliste
            ],
            check=True, capture_output=True, timeout=180, env=_git_env(),
        )
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(target, ignore_errors=True)
        detail = (exc.stderr or b"").decode(errors="replace").strip()[-300:]
        raise RuntimeError(f"git clone fehlgeschlagen ({redact(detail)})") from exc
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def _wanted(path: str) -> bool:
    lower = path.lower()
    if any(lower.endswith(ext) for ext in SKIP_EXT):
        return False
    _, ext = os.path.splitext(lower)
    return ext in CODE_EXT or any(n in os.path.basename(lower) for n in PRIORITY_NAMES)


def _score(path: str) -> int:
    lower = os.path.basename(path).lower()
    for i, name in enumerate(PRIORITY_NAMES):
        if name in lower:
            return i
    depth = path.count(os.sep)
    return 10 + depth  # flach liegende Dateien zuerst


def measure_repo(root: str) -> dict:
    """Umfang des Repos: Dateien, Codezeilen, Bytes.

    Gezählt wird nur, was auch ins Kontextpaket dürfte – node_modules und
    Binärdateien würden die Zahl sonst beliebig machen. Damit lässt sich
    zwischen zwei Meetings sehen, wie das Projekt gewachsen ist.
    """
    dateien = zeilen = bytes_gesamt = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), root)
            if not _wanted(rel):
                continue
            voll = os.path.join(dirpath, fn)
            try:
                groesse = os.path.getsize(voll)
                if groesse > 2_000_000:      # generierte Riesendateien
                    continue
                with open(voll, "rb") as fh:
                    inhalt = fh.read()
            except OSError:
                continue
            dateien += 1
            bytes_gesamt += groesse
            zeilen += inhalt.count(b"\n") + (1 if inhalt and not
                                              inhalt.endswith(b"\n") else 0)
    return {"repo_files": dateien, "repo_lines": zeilen,
            "repo_bytes": bytes_gesamt}


def pack_project(root: str) -> str:
    """Baut ein Textpaket: Dateibaum + Dateiinhalte bis zum Budget."""
    budget = settings.get_int("context_char_budget")
    tree_lines, files = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel_dir = os.path.relpath(dirpath, root)
        indent = "" if rel_dir == "." else "  " * rel_dir.count(os.sep) + "  "
        if rel_dir != ".":
            tree_lines.append(f"{indent}{os.path.basename(dirpath)}/")
        for fn in sorted(filenames):
            rel = os.path.normpath(os.path.join(rel_dir, fn))
            tree_lines.append(f"{indent}  {fn}")
            full = os.path.join(dirpath, fn)
            if _wanted(rel) and os.path.getsize(full) < 200_000:
                files.append((rel, full))

    files.sort(key=lambda t: _score(t[0]))
    parts = ["## Dateibaum\n" + "\n".join(tree_lines[:800])]
    used = len(parts[0])
    for rel, full in files:
        if used >= budget:
            parts.append("\n[Kontextbudget erreicht – weitere Dateien ausgelassen]")
            break
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                content = fh.read(20_000)
        except OSError:
            continue
        block = f"\n## Datei: {rel}\n```\n{content}\n```"
        parts.append(block)
        used += len(block)
    return "\n".join(parts)


def cleanup(root: str) -> None:
    shutil.rmtree(root, ignore_errors=True)
