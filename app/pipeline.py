"""Der Board-Meeting-Ablauf.

Phase 1  Einzelgutachten   – alle 6 Agenten parallel, live gestreamt
Phase 2  Kreuzverhör       – die 3 Senior Devs zerlegen die Gutachten der anderen
Phase 3  Chairman-Synthese – Claude priorisiert und baut die Roadmap (+ JSON)
Phase 4  Plane-Sync        – Roadmap-Items werden Issues in Plane

Deine Chat-Nachrichten werden an jeder Phasengrenze eingesammelt und den
Agenten als "Anweisungen vom Gründer" in den Kontext gelegt.
"""
import asyncio
import json
import re
import uuid

import litellm

from . import claude_code, mentions
from .bus import bus
from .config import (CHAIRMAN_ID, CLAUDE_TRANSPORT, MAX_TOKENS_CHAIRMAN,
                     MAX_TOKENS_REVIEW, build_team)
from .plane import plane

litellm.drop_params = True  # unbekannte Params still verwerfen (Router-Kompatibilität)


class Meeting:
    """Genau ein Board-Meeting läuft gleichzeitig."""

    def __init__(self) -> None:
        self.running = False
        self.team = build_team()
        self.repo_dir: str | None = None

    def _use_claude_code(self, agent_id: str) -> bool:
        if agent_id != CHAIRMAN_ID:
            return False
        if CLAUDE_TRANSPORT == "api":
            return False
        return claude_code.available()

    # ------------------------------------------------------------ LLM-Call
    async def _stream_agent(self, agent_id: str, user_content: str,
                            max_tokens: int = MAX_TOKENS_REVIEW) -> str:
        """Streamt eine Antwort Token für Token in den Team-Chat."""
        spec = self.team[agent_id]
        msg_id = uuid.uuid4().hex[:12]
        await bus.agent_status(agent_id, "typing")
        await bus.emit({"type": "msg_start", "id": msg_id, "agent": agent_id})

        # ---- Claude läuft über Claude Code (deine Max-Subscription) ----
        if self._use_claude_code(agent_id):
            try:
                prompt = spec.system_prompt + "\n\n" + user_content
                if self.repo_dir:
                    prompt += ("\n\nDas Projekt-Repository liegt in deinem "
                               "Arbeitsverzeichnis. Nutze deine Tools, um den "
                               "Code selbst zu durchsuchen, bevor du urteilst.")
                full = await claude_code.stream(
                    prompt, msg_id=msg_id, agent_id=agent_id,
                    cwd=self.repo_dir, allow_tools=bool(self.repo_dir),
                )
                await bus.emit({"type": "msg_end", "id": msg_id,
                                "agent": agent_id, "text": full})
                await bus.agent_status(agent_id, "idle")
                return full
            except Exception as exc:
                await bus.system(f"Claude Code nicht erreichbar ({exc}) – "
                                 "Claude fällt auf die API zurück.")

        full = ""
        try:
            kwargs: dict = {
                "model": spec.model,
                "messages": [
                    {"role": "system", "content": spec.system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "max_tokens": max_tokens,
                "stream": True,
            }
            if spec.api_key:
                kwargs["api_key"] = spec.api_key
            if spec.api_base:
                kwargs["api_base"] = spec.api_base
            stream = await litellm.acompletion(**kwargs)
            buffer = ""
            async for chunk in stream:
                delta = ""
                try:
                    delta = chunk.choices[0].delta.content or ""
                except (AttributeError, IndexError):
                    delta = ""
                if not delta:
                    continue
                full += delta
                buffer += delta
                if len(buffer) >= 24:  # kleine Pakete bündeln, UI bleibt flüssig
                    await bus.emit({"type": "token", "id": msg_id,
                                    "agent": agent_id, "text": buffer})
                    buffer = ""
            if buffer:
                await bus.emit({"type": "token", "id": msg_id,
                                "agent": agent_id, "text": buffer})
        except Exception as exc:  # Agent fällt aus, Meeting läuft weiter
            full += f"\n\n[{spec.name} ist ausgefallen: {exc}]"
            await bus.emit({"type": "token", "id": msg_id, "agent": agent_id,
                            "text": f"⚠️ Ausfall: {exc}"})
        await bus.emit({"type": "msg_end", "id": msg_id,
                        "agent": agent_id, "text": full})
        await bus.agent_status(agent_id, "idle")
        return full

    # ------------------------------------------------------------ Helfer
    async def _founder_notes(self) -> str:
        notes = await bus.drain_user_messages()
        if not notes:
            return ""
        joined = "\n- ".join(notes)
        return f"\n\n## Anweisungen vom Gründer (unbedingt berücksichtigen)\n- {joined}"

    # ------------------------------------------------------------ Ablauf
    async def run(self, briefing: str, project_pack: str,
                  repo_dir: str | None = None) -> None:
        self.running = True
        self.repo_dir = repo_dir
        if self._use_claude_code(CHAIRMAN_ID):
            await bus.system("Claude läuft über Claude Code – "
                             "deine Subscription ist im Einsatz.")
        try:
            await bus.phase("briefing", "done")
            base_context = (
                f"## Projekt-Briefing vom Gründer\n{briefing}\n\n"
                f"## Projekt-Snapshot\n{project_pack}"
            )

            # ---------------- Phase 1: Einzelgutachten (alle parallel)
            await bus.phase("gutachten", "active")
            await bus.system("Phase 1 – Einzelgutachten: Alle sechs nehmen sich das Projekt vor.")
            notes = await self._founder_notes()
            prompt1 = base_context + notes + (
                "\n\nErstelle jetzt dein unabhängiges Gutachten zu diesem Projekt "
                "aus Sicht deiner Rolle."
            )
            results = await asyncio.gather(
                *[self._stream_agent(aid, prompt1) for aid in self.team]
            )
            gutachten = dict(zip(self.team.keys(), results))
            await mentions.run_discussion(self, gutachten, base_context)
            await bus.phase("gutachten", "done")

            # ---------------- Phase 2: Kreuzverhör der Devs
            await bus.phase("kreuzverhoer", "active")
            await bus.system("Phase 2 – Kreuzverhör: Die Senior Devs reviewen sich gegenseitig.")
            notes = await self._founder_notes()
            devs = [aid for aid, s in self.team.items() if s.is_dev]

            async def cross(aid: str) -> str:
                others = "\n\n".join(
                    f"### Gutachten von {self.team[o].name}\n{gutachten[o]}"
                    for o in self.team if o != aid
                )
                prompt = (
                    base_context + notes +
                    f"\n\nHier sind die Gutachten deiner Kollegen:\n\n{others}\n\n"
                    "Dein Auftrag: 1) Wo liegen die Kollegen falsch oder übertreiben? "
                    "2) Welche ihrer Punkte sind Gold wert? 3) Was hat das gesamte "
                    "Board übersehen? Sei direkt und begründe hart am Projekt."
                )
                return await self._stream_agent(aid, prompt)

            cross_results = await asyncio.gather(*[cross(a) for a in devs])
            kreuz = dict(zip(devs, cross_results))
            await mentions.run_discussion(self, kreuz, base_context)
            await bus.phase("kreuzverhoer", "done")

            # ---------------- Phase 3: Chairman-Synthese
            await bus.phase("synthese", "active")
            await bus.system("Phase 3 – Der Chairman fasst zusammen und priorisiert.")
            notes = await self._founder_notes()
            alle = "\n\n".join(
                f"### Gutachten {self.team[a].name}\n{g}" for a, g in gutachten.items()
            ) + "\n\n" + "\n\n".join(
                f"### Kreuzverhör {self.team[a].name}\n{k}" for a, k in kreuz.items()
            )
            chairman_prompt = (
                base_context + notes +
                f"\n\n## Alle Board-Ergebnisse\n{alle}\n\n"
                "Du bist der Chairman. Erstelle:\n"
                "1) Eine priorisierte Roadmap als Markdown: Was killt das Projekt "
                "gerade, was macht es zur Bombe, in welcher Reihenfolge fixen.\n"
                "2) GANZ AM ENDE einen Block ```json mit einem Array von Issues "
                "für das Projektmanagement, Format:\n"
                '[{"name": "Kurztitel", "description": "Was & warum & wie", '
                '"priority": "urgent|high|medium|low"}]\n'
                "Maximal 12 Issues, nach Wichtigkeit sortiert."
            )
            roadmap = await self._stream_agent(
                CHAIRMAN_ID, chairman_prompt, max_tokens=MAX_TOKENS_CHAIRMAN
            )
            await bus.phase("synthese", "done")

            # ---------------- Phase 4: Plane-Sync
            await bus.phase("plane", "active")
            issues = self._extract_issues(roadmap)
            if not plane.enabled:
                await bus.system("Plane ist nicht konfiguriert (.env) – "
                                 f"{len(issues)} Issues wurden NICHT synchronisiert.")
            elif not issues:
                await bus.system("Chairman hat keinen JSON-Issue-Block geliefert – "
                                 "nichts zu synchronisieren.")
            else:
                ok = 0
                for issue in issues:
                    try:
                        created = await plane.create_issue(
                            issue.get("name", "Unbenannt"),
                            issue.get("description", ""),
                            issue.get("priority", "medium"),
                        )
                        ok += 1
                        await bus.emit({"type": "plane", "text":
                                        f"Issue angelegt: {issue.get('name')}",
                                        "issue_id": created.get("id", "")})
                    except Exception as exc:
                        await bus.system(f"Plane-Fehler bei '{issue.get('name')}': {exc}")
                await bus.system(f"Plane-Sync fertig: {ok}/{len(issues)} Issues angelegt.")
            await bus.phase("plane", "done")
            await bus.system("Board-Meeting beendet. Du kannst ein neues starten.")
        finally:
            self.running = False

    @staticmethod
    def _extract_issues(text: str) -> list[dict]:
        match = re.search(r"```json\s*(\[.*?\])\s*```", text, re.DOTALL)
        if not match:
            match = re.search(r"(\[\s*\{.*?\}\s*\])\s*$", text, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(1))
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []


meeting = Meeting()
