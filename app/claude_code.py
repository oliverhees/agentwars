"""Claude-Code-Transport: nutzt deine Max-Subscription statt der API.

Läuft headless: claude -p --output-format stream-json --verbose
                       --include-partial-messages
Auth: CLAUDE_CODE_OAUTH_TOKEN (via `claude setup-token` erzeugt).

Zwei Sicherheitsentscheidungen, die hier drinstecken:

* Der Prompt geht über **stdin**, nicht über argv. Ein Prompt mit führendem
  '-' wäre sonst eine Kommandozeilenoption, und das Kontextpaket sprengt
  irgendwann ohnehin das argv-Limit.
* Die Toolrechte kommen **pro Aufruf** als Profil rein. Reviews laufen
  lesend (Profil "review"); wenn das Board später Code schreiben soll,
  bekommt dieser Aufruf das Profil "build" – statt global
  `--dangerously-skip-permissions` zu setzen und zu hoffen.

`ANTHROPIC_API_KEY` wird für den Subprozess entfernt, weil der Print-Modus
einen gesetzten API-Key sonst der Subscription vorzieht.
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

# Toolprofile. "review" darf nur lesen – das ist der Modus, in dem heute
# jedes Board-Meeting läuft. "build" ist für die spätere Umsetzungsphase
# vorbereitet und wird aktuell von keinem Codepfad angefordert.
TOOL_PROFILES = {
    "review": env("CLAUDE_CODE_REVIEW_TOOLS", "Read,Grep,Glob,LS,NotebookRead"),
    "build": env("CLAUDE_CODE_BUILD_TOOLS",
                 "Read,Grep,Glob,LS,NotebookRead,Edit,Write,Bash"),
}


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


def _extract_tool_uses(event: dict) -> list[str]:
    """Werkzeuggriffe fürs Live-Protokoll: 'Read src/app.py'."""
    if event.get("type") != "assistant":
        return []
    content = ((event.get("message") or {}).get("content")) or []
    uses = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        args = block.get("input") or {}
        target = (args.get("file_path") or args.get("pattern")
                  or args.get("path") or args.get("command") or "")
        uses.append(f"{block.get('name', 'Tool')} {str(target)[:80]}".strip())
    return uses


async def stream(prompt: str, *, msg_id: str, agent_id: str,
                 cwd: str | None = None, profile: str | None = None) -> str:
    """Streamt eine Claude-Code-Antwort Token für Token in den Team-Chat.

    profile="review" + cwd=Repo → Claude Code darf den Code lesen und
    durchsuchen, aber nichts verändern und nichts ausführen.
    """
    cmd = [CLAUDE_BIN, "-p",
           "--output-format", "stream-json",
           "--verbose", "--include-partial-messages"]
    if CLAUDE_CODE_MODEL:
        cmd += ["--model", CLAUDE_CODE_MODEL]
    if profile and cwd:
        tools = TOOL_PROFILES.get(profile)
        if not tools:
            raise ValueError(f"Unbekanntes Claude-Code-Toolprofil: {profile}")
        # Alles, was nicht hier steht, lehnt Claude Code im Headless-Modus
        # ab – es kann ja niemanden fragen.
        cmd += ["--allowedTools", tools]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd or None,
        env=_subprocess_env(),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    full, result_text, stderr_tail = "", None, b""

    async def feed_stdin() -> None:
        assert proc.stdin
        try:
            proc.stdin.write(prompt.encode())
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            try:
                proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError):
                pass

    async def read_stderr() -> None:
        """Mitlesen, nicht erst am Ende – sonst blockiert ein volles
        Pipe-Buffer den Subprozess und der Timeout schlägt grundlos zu."""
        nonlocal stderr_tail
        assert proc.stderr
        while True:
            chunk = await proc.stderr.read(4096)
            if not chunk:
                break
            stderr_tail = (stderr_tail + chunk)[-2000:]

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
            for use in _extract_tool_uses(event):
                await bus.emit({"type": "tool", "agent": agent_id, "text": use})
            final = _extract_result(event)
            if final is not None:
                result_text = final
        if buffer:
            await bus.emit({"type": "token", "id": msg_id,
                            "agent": agent_id, "text": buffer})

    try:
        await asyncio.wait_for(
            asyncio.gather(feed_stdin(), read_stdout(), read_stderr()),
            timeout=TIMEOUT_SECONDS,
        )
        await proc.wait()
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"Claude Code Timeout nach {TIMEOUT_SECONDS}s")

    if proc.returncode not in (0, None) and not full and not result_text:
        detail = stderr_tail.decode(errors="replace")[-500:]
        raise RuntimeError(f"Claude Code Exit {proc.returncode}: {detail}")

    # Falls keine Partial-Deltas kamen, aber ein Result: nachreichen
    if not full and result_text:
        full = result_text
        await bus.emit({"type": "token", "id": msg_id,
                        "agent": agent_id, "text": full})
    return full or (result_text or "")
