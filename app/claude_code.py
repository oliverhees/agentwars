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

from . import settings
from .bus import bus
from .config import env

# Deployment-Sache, nicht Einstellungssache: Pfad, Timeout und Toolrechte
# bleiben in der .env, damit sie niemand über das UI aufweichen kann.
CLAUDE_BIN = env("CLAUDE_CODE_BIN", "claude")
TIMEOUT_SECONDS = int(env("CLAUDE_CODE_TIMEOUT", "900"))
CONFIG_DIR = env("CLAUDE_CONFIG_DIR", "/data/claude")

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
    # Token kann aus den Einstellungen kommen statt aus der Prozess-Umgebung.
    oauth = settings.get("claude_code_oauth_token")
    if oauth:
        e["CLAUDE_CODE_OAUTH_TOKEN"] = oauth
    # Die CLI legt Zustand ab (Onboarding, Trust-Entscheidungen). Ohne
    # beschreibbares Verzeichnis bricht sie ab; auf dem Volume überlebt der
    # Zustand außerdem den nächsten Redeploy.
    e.setdefault("CLAUDE_CONFIG_DIR", CONFIG_DIR)
    e.setdefault("HOME", os.path.dirname(CONFIG_DIR) or "/tmp")
    try:
        os.makedirs(e["CLAUDE_CONFIG_DIR"], exist_ok=True)
    except OSError:
        pass
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


def _extract_usage(event: dict) -> dict | None:
    """Claude Code rechnet selbst ab: das result-Event trägt Tokenzahlen und
    die Kosten in USD. Das ist genauer als alles, was wir schätzen könnten –
    auch wenn es über die Subscription läuft und dich nichts extra kostet."""
    if event.get("type") != "result":
        return None
    usage = event.get("usage") or {}
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_write": int(usage.get("cache_creation_input_tokens") or 0),
        "cache_read": int(usage.get("cache_read_input_tokens") or 0),
        "cost_usd": float(event.get("total_cost_usd") or 0.0),
        "estimated": 0,
    }


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


# Der Subscription-Token aus `claude setup-token`. Ein API-Key beginnt mit
# sk-ant-api… und funktioniert hier NICHT – das ist die häufigste Verwechslung.
TOKEN_PREFIX = "sk-ant-oat"


async def diagnose(probe: bool = False) -> dict:
    """Alle drei Stufen einzeln, statt bei der ersten Lücke abzubrechen.

    Sonst liest man 'Token fehlt' und weiß nicht, ob die CLI überhaupt da ist.
    """
    pfad = shutil.which(CLAUDE_BIN)
    cli = {"ok": bool(pfad), "detail": pfad or f"'{CLAUDE_BIN}' nicht im PATH.",
           "version": ""}
    if pfad:
        try:
            proc = await asyncio.create_subprocess_exec(
                CLAUDE_BIN, "--version",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
            cli["version"] = out.decode(errors="replace").strip()
            cli["ok"] = proc.returncode == 0
            if not cli["ok"]:
                cli["detail"] = f"CLI beendet sich mit Code {proc.returncode}."
        except Exception as exc:
            cli = {"ok": False, "detail": f"CLI nicht startbar ({exc}).",
                   "version": ""}

    aus_db = bool(settings._db_values().get("claude_code_oauth_token"))
    wert = settings.get("claude_code_oauth_token")
    if not wert:
        token = {"ok": False, "detail": "Nicht hinterlegt.", "source": ""}
    elif not wert.startswith(TOKEN_PREFIX):
        # Häufigster Fehler: API-Key statt Subscription-Token eingetragen.
        token = {"ok": False, "source": "Einstellungen" if aus_db else ".env",
                 "detail": f"Beginnt nicht mit '{TOKEN_PREFIX}' – das sieht "
                           "nach einem API-Key aus, nicht nach dem Token aus "
                           "'claude setup-token'."}
    else:
        token = {"ok": True, "source": "Einstellungen" if aus_db else ".env",
                 "detail": f"Hinterlegt ({wert[:14]}… , {len(wert)} Zeichen)."}

    verbindung = {"ok": False, "detail": "Nicht geprüft."}
    if not probe:
        verbindung["detail"] = "Auf Knopfdruck prüfbar."
    elif not (cli["ok"] and token["ok"]):
        verbindung["detail"] = "Übersprungen – erst CLI und Token in Ordnung."
    else:
        try:
            ok, detail = await ping()
        except Exception as exc:
            ok, detail = False, str(exc)[:300]
        verbindung = {"ok": ok, "detail": detail}

    return {"cli": cli, "token": token, "connection": verbindung,
            "ready": cli["ok"] and token["ok"]
            and (verbindung["ok"] or not probe)}


async def ping(timeout: int = 90) -> tuple[bool, str]:
    """Echter Verbindungstest: ein Mini-Call über die Subscription.

    `claude --version` sagt nur, dass die CLI da ist – es fasst das Netz nicht
    an. Ein abgelaufener oder vertippter Token besteht diesen Test und fliegt
    dann mitten im Meeting auf. Hier läuft deshalb eine echte Anfrage, deren
    Antwort auf ein Wort begrenzt ist: ein paar Token Abo-Kontingent für die
    Gewissheit, dass Auth und Erreichbarkeit stimmen.
    """
    if not available():
        return False, f"CLI '{CLAUDE_BIN}' nicht im PATH."
    proc = await asyncio.create_subprocess_exec(
        CLAUDE_BIN, "-p", "--output-format", "json",
        env=_subprocess_env(),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(b"Antworte ausschliesslich mit dem Wort: OK"),
            timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return False, f"Keine Antwort innerhalb von {timeout}s."

    # Claude Code schreibt seine Fehler als JSON nach stdout, nicht nach
    # stderr – wer nur stderr liest, bekommt "Exit 1" ohne jede Erklärung.
    stdout = out.decode(errors="replace").strip()
    stderr = err.decode(errors="replace").strip()
    payload = {}
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        payload = {}

    if payload.get("is_error") or (proc.returncode != 0 and payload.get("result")):
        return False, str(payload.get("result") or payload.get("error"))[:400]
    if proc.returncode != 0:
        detail = stderr or stdout or "keine Ausgabe"
        return False, f"Exit {proc.returncode}: {detail[-400:]}"
    if not payload:
        return False, f"Unerwartete Antwort: {(stdout or stderr)[:300] or 'leer'}"

    usage = _extract_usage(payload) or {}
    verbraucht = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
    return True, (f"Verbindung steht – Antwort erhalten "
                  f"({verbraucht} Token verbraucht).")


async def stream(prompt: str, *, msg_id: str, agent_id: str,
                 cwd: str | None = None, profile: str | None = None,
                 timeout: int | None = None) -> tuple[str, dict | None]:
    """Streamt eine Claude-Code-Antwort Token für Token in den Team-Chat.

    Gibt (Text, Verbrauch) zurück. Der Verbrauch kommt aus dem result-Event
    von Claude Code selbst, ist also keine Schätzung.

    profile="review" + cwd=Repo → Claude Code darf den Code lesen und
    durchsuchen, aber nichts verändern und nichts ausführen.
    """
    cmd = [CLAUDE_BIN, "-p",
           "--output-format", "stream-json",
           "--verbose", "--include-partial-messages"]
    model = settings.get("claude_code_model")
    if model:
        cmd += ["--model", model]
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

    full, result_text, stderr_tail, usage = "", None, b"", None

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
        nonlocal full, result_text, usage
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
            gemessen = _extract_usage(event)
            if gemessen is not None:
                usage = gemessen
        if buffer:
            await bus.emit({"type": "token", "id": msg_id,
                            "agent": agent_id, "text": buffer})

    frist = timeout or TIMEOUT_SECONDS
    try:
        await asyncio.wait_for(
            asyncio.gather(feed_stdin(), read_stdout(), read_stderr()),
            timeout=frist,
        )
        await proc.wait()
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"Claude Code Timeout nach {frist}s")

    if proc.returncode not in (0, None) and not full and not result_text:
        detail = stderr_tail.decode(errors="replace")[-500:]
        raise RuntimeError(f"Claude Code Exit {proc.returncode}: {detail}")

    # Falls keine Partial-Deltas kamen, aber ein Result: nachreichen
    if not full and result_text:
        full = result_text
        await bus.emit({"type": "token", "id": msg_id,
                        "agent": agent_id, "text": full})
    return full or (result_text or ""), usage
