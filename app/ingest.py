"""Projekt einlesen: Git-Repo klonen, Dateibaum + relevante Dateien packen."""
import os
import shutil
import subprocess
import tempfile

from .config import CONTEXT_CHAR_BUDGET

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


def clone_repo(git_url: str) -> str:
    target = tempfile.mkdtemp(prefix="review_")
    subprocess.run(
        ["git", "clone", "--depth", "1", git_url, target],
        check=True, capture_output=True, timeout=180,
    )
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


def pack_project(root: str) -> str:
    """Baut ein Textpaket: Dateibaum + Dateiinhalte bis zum Budget."""
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
        if used >= CONTEXT_CHAR_BUDGET:
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
