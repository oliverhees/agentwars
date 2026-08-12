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

from . import (claude_code, mentions, preflight, settings, store,
               tickets, usage)
from .bus import bus
from .config import CHAIRMAN_JSON_CONTRACT, build_team

litellm.drop_params = True  # unbekannte Params still verwerfen (Router-Kompatibilität)


class Meeting:
    """Genau ein Board-Meeting läuft gleichzeitig."""

    def __init__(self) -> None:
        self.running = False
        self._team: dict = {}
        self.repo_dir: str | None = None
        self.project: dict = {}
        self.phase = "briefing"   # für die Verbrauchsbuchung

    @property
    def team(self) -> dict:
        """Wird beim ersten Zugriff aufgebaut – auch außerhalb eines Meetings,
        damit @Erwähnungen im Leerlauf funktionieren."""
        if not self._team:
            self._team = build_team()
        return self._team

    def reload_team(self) -> None:
        """Aufstellung frisch aus den Einstellungen ziehen."""
        self._team = build_team()

    def _use_claude_code(self, agent_id: str) -> bool:
        """Nicht mehr an einen festen Agenten gebunden: wer als Provider
        'claude-code' eingestellt hat, läuft über die Subscription."""
        spec = self.team.get(agent_id)
        if not spec or spec.provider != "claude-code":
            return False
        if settings.get("claude_transport") == "api":
            return False
        return claude_code.available()

    # ------------------------------------------------------------ LLM-Call
    def _versuchsplan(self, spec) -> tuple[list[str], int, int]:
        """Welche Modelle in welcher Reihenfolge, wie oft, mit welcher Pause.

        Der claude-code-Provider trägt keinen LiteLLM-Präfix; für den Weg
        über die API muss er zu anthropic/ umgeschrieben werden.
        """
        praefix = "anthropic/" if spec.provider == "claude-code" else ""
        modelle = [praefix + spec.model]
        if spec.fallback_model:
            zweit = praefix + spec.fallback_model
            if zweit not in modelle:
                modelle.append(zweit)
        versuche = max(1, settings.get_int("retry_attempts") or 1)
        pause = max(0, settings.get_int("retry_backoff"))
        return modelle, versuche, pause

    async def _stream_once(self, spec, model: str, msg_id: str,
                           user_content: str, max_tokens: int) -> tuple[str, object]:
        """Ein einzelner Streaming-Versuch. Wirft weiter, wenn er scheitert."""
        api_key = (settings.get("anthropic_api_key")
                   if spec.provider == "claude-code" else spec.api_key)
        kwargs: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": spec.system_prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": max_tokens,
            "stream": True,
            # Viele OpenAI-kompatible Endpunkte liefern die Abrechnung nur,
            # wenn man ausdrücklich danach fragt. Wer es nicht kann, ignoriert
            # den Parameter (litellm.drop_params).
            "stream_options": {"include_usage": True},
        }
        if api_key:
            kwargs["api_key"] = api_key
        if spec.api_base:
            kwargs["api_base"] = spec.api_base

        full, gemeldet, buffer = "", None, ""
        stream = await litellm.acompletion(**kwargs)
        async for chunk in stream:
            gezaehlt = getattr(chunk, "usage", None)
            if gezaehlt:
                gemeldet = gezaehlt
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
                                "agent": spec.id, "text": buffer})
                buffer = ""
        if buffer:
            await bus.emit({"type": "token", "id": msg_id,
                            "agent": spec.id, "text": buffer})
        return full, gemeldet

    async def _stream_agent(self, agent_id: str, user_content: str,
                            max_tokens: int | None = None) -> str:
        """Streamt eine Antwort Token für Token in den Team-Chat.

        Schweigt ein Modell oder fällt es aus, wird der Versuch wiederholt –
        und danach auf das hinterlegte Fallback-Modell gewechselt. Eine leere
        Antwort zählt dabei ausdrücklich als Ausfall: genau so ist Kimi im
        ersten Meeting stumm durchgelaufen, ohne dass es jemand gemerkt hätte.
        """
        if max_tokens is None:
            max_tokens = settings.get_int("max_tokens_review")
        spec = self.team[agent_id]
        msg_id = uuid.uuid4().hex[:12]
        await bus.agent_status(agent_id, "typing")
        await bus.emit({"type": "msg_start", "id": msg_id, "agent": agent_id})

        # ---- Claude läuft über Claude Code (deine Max-Subscription) ----
        if self._use_claude_code(agent_id):
            text = await self._claude_code_pfad(spec, msg_id, user_content)
            if text is not None:
                await bus.emit({"type": "msg_end", "id": msg_id,
                                "agent": agent_id, "text": text})
                await bus.agent_status(agent_id, "idle")
                return text

        modelle, versuche, pause = self._versuchsplan(spec)
        full, gemeldet, model = "", None, modelle[0]
        letzter_fehler = ""
        for nummer, model in enumerate(modelle):
            for versuch in range(1, versuche + 1):
                letzter = nummer == len(modelle) - 1 and versuch == versuche
                try:
                    full, gemeldet = await self._stream_once(
                        spec, model, msg_id, user_content, max_tokens)
                    if full.strip():
                        letzter_fehler = ""
                        break
                    letzter_fehler = "leere Antwort"
                except Exception as exc:   # Agent fällt aus, Meeting läuft weiter
                    full, gemeldet = "", None
                    letzter_fehler = str(exc)
                if letzter:
                    break
                await self._neuer_versuch(spec, msg_id, model, letzter_fehler,
                                          versuch, versuche, modelle, nummer)
                if pause:
                    await asyncio.sleep(pause)
            if full.strip():
                break

        if not full.strip():
            full = f"\n\n[{spec.name} ist ausgefallen: {letzter_fehler}]"
            await bus.emit({"type": "token", "id": msg_id, "agent": agent_id,
                            "text": f"⚠️ Ausfall: {letzter_fehler}"})
            await bus.system(
                f"{spec.name} liefert nach {versuche} Versuchen"
                + (f" und {len(modelle) - 1} Fallback-Modell(en)"
                   if len(modelle) > 1 else "")
                + f" nichts: {letzter_fehler}.")
        await self._record_usage(spec, spec.system_prompt + user_content,
                                 full, None, gemeldet, model)
        await bus.emit({"type": "msg_end", "id": msg_id,
                        "agent": agent_id, "text": full})
        await bus.agent_status(agent_id, "idle")
        return full

    async def _neuer_versuch(self, spec, msg_id: str, model: str, fehler: str,
                             versuch: int, versuche: int,
                             modelle: list[str], nummer: int) -> None:
        """Angefangene Ausgabe verwerfen und den Wechsel sichtbar machen."""
        await bus.emit({"type": "msg_reset", "id": msg_id, "agent": spec.id})
        naechstes = (modelle[nummer + 1] if versuch == versuche
                     and nummer + 1 < len(modelle) else model)
        if naechstes != model:
            await bus.system(f"{spec.name}: {model} antwortet nicht ({fehler}) – "
                             f"Wechsel auf Fallback {naechstes}.")
        else:
            await bus.system(f"{spec.name}: Versuch {versuch}/{versuche} "
                             f"gescheitert ({fehler}) – neuer Anlauf.")

    async def _claude_code_pfad(self, spec, msg_id: str,
                                user_content: str) -> str | None:
        """Antwort über die Subscription. None heisst: bitte über die API."""
        prompt = spec.system_prompt + "\n\n" + user_content
        if self.repo_dir:
            prompt += ("\n\nDas Projekt-Repository liegt in deinem "
                       "Arbeitsverzeichnis. Nutze deine Tools, um den "
                       "Code selbst zu durchsuchen, bevor du urteilst. "
                       "Du hast Lesezugriff – du änderst nichts.")
        _, versuche, pause = self._versuchsplan(spec)
        for versuch in range(1, versuche + 1):
            try:
                full, gemessen = await claude_code.stream(
                    prompt, msg_id=msg_id, agent_id=spec.id,
                    cwd=self.repo_dir,
                    profile="review" if self.repo_dir else None,
                )
                if full.strip():
                    await self._record_usage(spec, prompt, full, gemessen)
                    return full
                grund = "leere Antwort"
            except Exception as exc:
                grund = str(exc)
            if versuch < versuche:
                await bus.emit({"type": "msg_reset", "id": msg_id,
                                "agent": spec.id})
                await bus.system(f"{spec.name}: Claude Code Versuch "
                                 f"{versuch}/{versuche} gescheitert ({grund}).")
                if pause:
                    await asyncio.sleep(pause)
            else:
                await bus.emit({"type": "msg_reset", "id": msg_id,
                                "agent": spec.id})
                await bus.system(f"Claude Code liefert nichts ({grund}) – "
                                 f"{spec.name} fällt auf die Anthropic-API zurück.")
        return None

    async def _record_usage(self, spec, prompt: str, answer: str,
                            claude_usage: dict | None = None,
                            gemeldet=None, model: str = "") -> None:
        """Verbrauch buchen – gemessen wenn möglich, sonst geschätzt.

        Reihenfolge der Genauigkeit: Claude Code rechnet selbst ab, danach die
        Usage-Angabe des Anbieters, zuletzt eine Schätzung über den Tokenizer.
        Geschätzte Werte werden im Dashboard als solche markiert.
        """
        modell = model or spec.model
        if claude_usage:
            await usage.record(
                project_id=self.project.get("id", ""),
                meeting_id=bus.meeting_id or "", agent_id=spec.id,
                agent_name=spec.name, phase=self.phase, provider=spec.provider,
                model=modell, cost_usd=claude_usage["cost_usd"],
                input_tokens=claude_usage["input_tokens"],
                output_tokens=claude_usage["output_tokens"],
                cache_read=claude_usage["cache_read"],
                cache_write=claude_usage["cache_write"])
            return

        rein = getattr(gemeldet, "prompt_tokens", 0) or 0
        raus = getattr(gemeldet, "completion_tokens", 0) or 0
        geschaetzt = False
        if not rein and not raus:
            geschaetzt = True
            try:
                rein = litellm.token_counter(model=modell, text=prompt)
                raus = litellm.token_counter(model=modell, text=answer)
            except Exception:
                # Grobe Faustregel, wenn selbst der Tokenizer fehlt.
                rein, raus = len(prompt) // 4, len(answer) // 4
        await usage.record(
            project_id=self.project.get("id", ""),
            meeting_id=bus.meeting_id or "", agent_id=spec.id,
            agent_name=spec.name, phase=self.phase, provider=spec.provider,
            model=modell, input_tokens=rein, output_tokens=raus,
            estimated=geschaetzt)

    # ------------------------------------------------------------ Helfer
    async def _report_preflight(self) -> None:
        """Sagt vor dem ersten teuren Call, wer heute überhaupt mitspielt."""
        try:
            report = await preflight.check()
        except Exception as exc:
            await bus.system(f"Preflight übersprungen ({exc}).")
            return
        broken = [a for a in report["agents"] if not a["ok"]]
        if not broken:
            await bus.system(f"Preflight: alle {len(report['agents'])} Agenten antworten.")
            return
        for agent in broken:
            hint = f" · {agent['hint']}" if agent["hint"] else ""
            await bus.system(
                f"Preflight-Warnung – {agent['name']} ({agent['model']}): "
                f"{agent['detail']}{hint}")
        await bus.system(
            f"{len(broken)} von {len(report['agents'])} Agenten fallen "
            "voraussichtlich aus. Das Meeting läuft trotzdem weiter.")

    async def _founder_notes(self) -> str:
        notes = await bus.drain_user_messages()
        if not notes:
            return ""
        joined = "\n- ".join(notes)
        return f"\n\n## Anweisungen vom Gründer (unbedingt berücksichtigen)\n- {joined}"

    # ------------------------------------------------------------ Ablauf
    async def run(self, briefing: str, project_pack: str,
                  repo_dir: str | None = None,
                  project: dict | None = None) -> None:
        self.running = True
        self.repo_dir = repo_dir
        self.project = project or {}
        # Aufstellung, Prompts und Modelle können sich seit dem letzten
        # Meeting geändert haben – frisch laden statt zwischenspeichern.
        self.reload_team()
        chairman = settings.chairman_id()
        via_cli = [self.team[a].name for a in self.team
                   if self._use_claude_code(a)]
        if via_cli:
            await bus.system(f"{', '.join(via_cli)} läuft über Claude Code – "
                             "deine Subscription ist im Einsatz.")
        try:
            if preflight.CHECK_ON_START:
                await self._report_preflight()
            await bus.phase("briefing", "done")
            # Grüne Wiese oder bestehender Code? Davon hängt ab, ob das Board
            # prüft oder entwirft – die Rollen bleiben, der Auftrag dreht sich.
            greenfield = repo_dir is None
            lage = settings.get(
                "mode_greenfield" if greenfield else "mode_existing")
            await bus.system("Lage: grüne Wiese – das Board entwirft."
                             if greenfield else
                             "Lage: bestehendes Projekt – das Board prüft.")
            base_context = (
                f"{lage}\n\n"
                f"## Projekt-Briefing vom Gründer\n{briefing}\n\n"
                f"## Projekt-Snapshot\n{project_pack}"
            )

            # ---------------- Phase 1: Einzelgutachten (alle parallel)
            self.phase = "gutachten"
            await bus.phase("gutachten", "active")
            await bus.system(f"Phase 1 – Einzelbeiträge: Alle {len(self.team)} "
                             "nehmen sich das Projekt vor.")
            notes = await self._founder_notes()
            prompt1 = base_context + notes + "\n\n" + settings.get("phase_review")
            results = await asyncio.gather(
                *[self._stream_agent(aid, prompt1) for aid in self.team]
            )
            gutachten = dict(zip(self.team.keys(), results))
            await mentions.run_discussion(self, gutachten, base_context)
            await bus.phase("gutachten", "done")

            # ---------------- Phase 2: Kreuzverhör der Devs
            self.phase = "kreuzverhoer"
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
                    f"\n\n## Beiträge deiner Kollegen\n\n{others}\n\n"
                    + settings.get("phase_cross")
                )
                return await self._stream_agent(aid, prompt)

            cross_results = await asyncio.gather(*[cross(a) for a in devs])
            kreuz = dict(zip(devs, cross_results))
            await mentions.run_discussion(self, kreuz, base_context)
            await bus.phase("kreuzverhoer", "done")

            # ---------------- Phase 3: Chairman-Synthese
            self.phase = "synthese"
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
                + settings.get("phase_chairman")
                + CHAIRMAN_JSON_CONTRACT   # nicht editierbar, sonst keine Tickets
            )
            roadmap = await self._stream_agent(
                chairman, chairman_prompt,
                max_tokens=settings.get_int("max_tokens_chairman")
            )
            await bus.phase("synthese", "done")

            # ---------------- Phase 4: Tickets
            self.phase = "plane"
            await bus.phase("plane", "active")
            issues = self._extract_issues(roadmap)
            blocker = tickets.readiness(self.project)
            if not issues:
                await bus.system("Chairman hat keinen JSON-Issue-Block geliefert – "
                                 "nichts zu synchronisieren.")
            elif blocker:
                await bus.system(f"{len(issues)} Tickets NICHT angelegt: {blocker}.")
            else:
                done, failed = await tickets.sync(issues, self.project)
                for line in done:
                    await bus.emit({"type": "plane", "text": line})
                for line in failed:
                    await bus.system(f"Ticket-Fehler – {line}")
                await bus.system(
                    f"Ticket-Sync fertig: {len(done)} angelegt"
                    + (f", {len(failed)} fehlgeschlagen." if failed else "."))
                if bus.meeting_id:
                    await store.update_meeting(bus.meeting_id,
                                               {"tickets": len(done)})
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
