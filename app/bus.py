"""Event-Bus: Pipeline -> WebSockets (Echtzeit) + Chat-Verlauf im Speicher."""
import asyncio
import json
import time


class Bus:
    def __init__(self) -> None:
        self.clients: set = set()
        self.history: list[dict] = []
        self.user_inbox: list[dict] = []   # Nachrichten von dir an das Team
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------- WebSockets
    async def register(self, ws) -> None:
        self.clients.add(ws)
        # Verlauf nachliefern, damit ein Reload nichts verliert
        for event in self.history[-500:]:
            try:
                await ws.send_text(json.dumps(event))
            except Exception:
                break

    def unregister(self, ws) -> None:
        self.clients.discard(ws)

    async def emit(self, event: dict) -> None:
        event.setdefault("ts", time.time())
        # Token-Events nicht in die History fluten – die fertige Nachricht
        # kommt als msg_end mit Volltext.
        if event.get("type") != "token":
            self.history.append(event)
        data = json.dumps(event, ensure_ascii=False)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.unregister(ws)

    # ---------------------------------------------------------- Convenience
    async def system(self, text: str) -> None:
        await self.emit({"type": "system", "text": text})

    async def phase(self, phase_id: str, status: str) -> None:
        await self.emit({"type": "phase", "phase": phase_id, "status": status})

    async def agent_status(self, agent_id: str, status: str) -> None:
        await self.emit({"type": "agent_status", "agent": agent_id, "status": status})

    # ---------------------------------------------------------- User-Input
    async def push_user_message(self, text: str) -> None:
        async with self._lock:
            self.user_inbox.append({"text": text, "ts": time.time()})
        await self.emit({"type": "user_msg", "text": text})

    async def drain_user_messages(self) -> list[str]:
        """Holt alle bisher ungelesenen Nachrichten von dir ab –
        wird an jeder Phasengrenze aufgerufen und in den Kontext injiziert."""
        async with self._lock:
            msgs = [m["text"] for m in self.user_inbox]
            self.user_inbox.clear()
        return msgs


bus = Bus()
