"""Claude-Code-Transport: nutzt deine Max-Subscription statt der API.

Läuft headless: claude -p --output-format stream-json --verbose
                       --include-partial-messages
Auth: CLAUDE_CODE_OAUTH_TOKEN (via `claude setup-token` erzeugt).
Wichtig: ANTHROPIC_API_KEY wird für den Subprozess entfernt, weil der
Print-Modus einen gesetzten API-Key sonst der Subscription vorzieht.
"""
import asyncio
import json
import os
import shutil

from .bus import bus
from .config import env

CLAUDE_BIN = env("CLAUDE_CODE_BIN", "claude")
CLAUDE_CODE_MODEL = env("CLAUDE_CODE_MODEL", "")  # leer = Claude-Code-Default
TIMEOUT_SECONDS = int(env("CLAUDE_CODE_TIMEOUT", "900"))


def available() -> bool:
    return shutil.which(CLAUDE_BIN) is not None


def _subprocess_env() -> dict:
    e = os.environ.copy()
    e.pop("ANTHROPIC_API_KEY", None)  # Subscription erzwingen
    return e


def _extract_delta(event: dict) -> str:
    """Token-Delta aus einem stream-json-Event ziehen (defensiv)."""
    if event.get("type") == "stream_event":
        delta = (event.get("event") or {}).get("delta") or {}
        return delta.get("text") or ""
    return ""


def _extract_result(event: dict) -> str | None:
    if event.get("type") == "result":
        return event.get("result") or ""
    return None


async def stream(prompt: str, *, msg_id: str, agent_id: str,
                 cwd: str | None = None, allow_tools: bool = False) -> str:
    """Streamt eine Claude-Code-Antwort Token für Token in den Team-Chat.
    allow_tools=True + cwd=Repo → Claude Code durchsucht den Code selbst."""
    cmd = [CLAUDE_BIN, "-p", prompt,
           "--output-format", "stream-json",
           "--verbose", "--include-partial-messages"]
    if CLAUDE_CODE_MODEL:
        cmd += ["--model", CLAUDE_CODE_MODEL]
    if allow_tools and cwd:
        # Ephemerer Container + frisch geklontes Repo → Tools freigeben,
        # damit Claude Code den Code aktiv lesen/durchsuchen kann.
        cmd += ["--dangerously-skip-permissions"]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd or None,
        env=_subprocess_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    full, result_text = "", None

    async def read_stdout() -> None:
        nonlocal full, result_text
        assert proc.stdout
        buffer = ""
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            line = line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            delta = _extract_delta(event)
            if delta:
                full += delta
                buffer += delta
                if len(buffer) >= 24:
                    await bus.emit({"type": "token", "id": msg_id,
                                    "agent": agent_id, "text": buffer})
                    buffer = ""
            final = _extract_result(event)
            if final is not None:
                result_text = final
        if buffer:
            await bus.emit({"type": "token", "id": msg_id,
                            "agent": agent_id, "text": buffer})

    try:
        await asyncio.wait_for(read_stdout(), timeout=TIMEOUT_SECONDS)
        await proc.wait()
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"Claude Code Timeout nach {TIMEOUT_SECONDS}s")

    if proc.returncode not in (0, None) and not full and not result_text:
        stderr = (await proc.stderr.read()).decode(errors="replace")[:500]
        raise RuntimeError(f"Claude Code Exit {proc.returncode}: {stderr}")

    # Falls keine Partial-Deltas kamen, aber ein Result: nachreichen
    if not full and result_text:
        full = result_text
        await bus.emit({"type": "token", "id": msg_id,
                        "agent": agent_id, "text": full})
    return full or (result_text or "")
