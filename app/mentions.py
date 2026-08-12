"""@-Mentions: Agenten sprechen sich gegenseitig an, du sprichst sie direkt an.

Zwei Wege:
1. Agent → Agent: Nach Gutachten und Kreuzverhör werden @mentions eingesammelt.
   Erwähnte antworten in Diskussionsrunden (begrenzt, damit kein Endlos-Loop).
2. Du → Agent: "@deepseek Wie siehst du das?" im Chat → der Agent antwortet
   sofort mit dem bisherigen Gesprächsverlauf als Kontext, auch mitten im Meeting.
"""
import re

from .bus import bus
from .config import env

MAX_MENTION_ROUNDS = int(env("MAX_MENTION_ROUNDS", "2"))
MAX_TOKENS_MENTION = int(env("MAX_TOKENS_MENTION", "900"))

HANDLES = ["claude", "gpt", "kimi", "qwen", "deepseek", "glm"]
MENTION_RE = re.compile(r"@(" + "|".join(HANDLES) + r")\b", re.IGNORECASE)

MENTION_RULES = (
    "\n\nIm Team-Chat kannst du Kollegen direkt ansprechen: "
    + ", ".join("@" + h for h in HANDLES) +
    ". Nutze eine @Erwähnung NUR, wenn du von genau dieser Person eine "
    "Antwort brauchst (Widerspruch, Rückfrage, Bestätigung einer These). "
    "Maximal zwei @Erwähnungen pro Beitrag. Wirst du selbst erwähnt, "
    "antworte kurz, direkt und in der Sache."
)


def extract_mentions(text: str, author_id: str) -> list[str]:
    """Alle gültigen @mentions eines Beitrags – ohne Selbst-Erwähnung, dedupliziert."""
    seen: list[str] = []
    for match in MENTION_RE.finditer(text or ""):
        handle = match.group(1).lower()
        if handle != author_id and handle not in seen:
            seen.append(handle)
    return seen[:2]


def transcript(team: dict, limit: int = 14, max_chars_each: int = 1200) -> str:
    """Baut den jüngsten Gesprächsverlauf aus der Bus-History (für Kontext)."""
    lines: list[str] = []
    for event in bus.history:
        if event.get("type") == "msg_end":
            name = team[event["agent"]].name if event.get("agent") in team \
                else event.get("agent", "?")
            lines.append(f"{name}: {event.get('text', '')[:max_chars_each]}")
        elif event.get("type") == "user_msg":
            lines.append(f"Gründer: {event.get('text', '')[:max_chars_each]}")
    return "\n\n".join(lines[-limit:])


async def run_discussion(meeting, sources: dict[str, str],
                         base_context: str) -> None:
    """Diskussionsrunden: sources = {agent_id: beitrag}. Erwähnte antworten,
    deren Antworten können wieder erwähnen – bis MAX_MENTION_ROUNDS."""
    import asyncio

    pending: list[tuple[str, str, str]] = []  # (von, an, beitrag)
    for author, text in sources.items():
        for target in extract_mentions(text, author):
            pending.append((author, target, text))

    rounds = 0
    while pending and rounds < MAX_MENTION_ROUNDS:
        rounds += 1
        await bus.system(f"Diskussionsrunde {rounds} – "
                         f"{len(pending)} direkte Ansprache(n).")
        # Pro Runde antwortet jeder Agent höchstens einmal (auf alle Pings gesammelt)
        by_target: dict[str, list[tuple[str, str]]] = {}
        for author, target, text in pending:
            by_target.setdefault(target, []).append((author, text))

        async def reply(target: str, pings: list[tuple[str, str]]) -> tuple[str, str]:
            quoted = "\n\n".join(
                f"### {meeting.team[a].name} hat dich angesprochen:\n{t[-2500:]}"
                for a, t in pings
            )
            prompt = (
                base_context[:6000] +
                f"\n\n{quoted}\n\nDu wurdest direkt angesprochen. Antworte kurz "
                "und in der Sache auf die Punkte, die dich betreffen."
            )
            answer = await meeting._stream_agent(
                target, prompt, max_tokens=MAX_TOKENS_MENTION)
            return target, answer

        results = await asyncio.gather(
            *[reply(t, p) for t, p in by_target.items()])

        pending = []
        if rounds < MAX_MENTION_ROUNDS:
            for author, text in results:
                for target in extract_mentions(text, author):
                    # keine Ping-Pong-Schleife: nicht zurück an jemanden,
                    # der in dieser Runde schon geantwortet hat
                    if target not in by_target:
                        pending.append((author, target, text))
    if rounds:
        await bus.system("Diskussion abgeschlossen.")


async def answer_user_mention(meeting, target: str, question: str) -> None:
    """Direktantwort an den Gründer – sofort, mit Verlauf als Kontext."""
    context = transcript(meeting.team)
    prompt = (
        ("## Bisheriger Team-Chat\n" + context + "\n\n" if context else "") +
        f"## Direkte Frage vom Gründer an dich\n{question}\n\n"
        "Antworte dem Gründer direkt, konkret und ohne Floskeln."
    )
    await meeting._stream_agent(target, prompt, max_tokens=MAX_TOKENS_MENTION)
