# Boardroom – deine KI-Agentur

Sechs KI-Spezialisten zerlegen dein Projekt live in einem Team-Chat, du sitzt mit am Tisch, und die Ergebnisse landen automatisch als Issues in Plane.

## Das Team

| Agent | Rolle | Modell | Quelle |
|---|---|---|---|
| Claude | Senior Dev + Chairman | claude-sonnet-4-6 | Anthropic API |
| GPT | Senior Dev | (in .env eintragen) | OpenAI API |
| Kimi | Senior Dev | Kimi K3 | HostYourAI |
| Qwen | Architektur & Tooling | Qwen3.5 | HostYourAI |
| DeepSeek | Security & Reasoning | DeepSeek V4 Pro | HostYourAI |
| GLM | Devil's Advocate | GLM 5.2 | HostYourAI |

## Der Ablauf (ein "Board-Meeting")

1. **Briefing** – du gibst Repo-URL + Briefing ein
2. **Einzelgutachten** – alle 6 analysieren parallel, live gestreamt
3. **Kreuzverhör** – die 3 Senior Devs zerlegen die Gutachten der anderen
4. **Chairman-Synthese** – Claude priorisiert alles zur Roadmap
5. **Plane-Sync** – die Roadmap wird zu Issues in deinem Plane-Projekt

**Du kannst jederzeit reinschreiben.** Deine Nachrichten werden an der nächsten Phasengrenze als "Anweisungen vom Gründer" in den Kontext aller Agenten injiziert.

## @-Mentions – das Team redet miteinander

- **Agent → Agent:** Jeder Agent kann Kollegen mit `@gpt`, `@kimi` usw. direkt ansprechen. Nach Gutachten und Kreuzverhör laufen automatische Diskussionsrunden: Erwähnte antworten, deren Antworten können wieder erwähnen – begrenzt auf `MAX_MENTION_ROUNDS` (Default 2), damit kein Endlos-Ping-Pong entsteht.
- **Du → Agent:** Schreib `@deepseek Wie riskant ist das?` in den Chat und der Agent antwortet **sofort** mit dem bisherigen Gesprächsverlauf als Kontext – auch mitten im Meeting. Tipp aufs Teammitglied in der Leiste fügt die @Erwähnung automatisch ein.
- Mentions werden im Chat farbig in der Agentenfarbe hervorgehoben.
- Handles: `@claude` `@gpt` `@kimi` `@qwen` `@deepseek` `@glm`

## Setup

```bash
cp .env.example .env   # Keys eintragen (siehe unten)
docker compose up --build
# → http://localhost:8000
```

### Claude über deine Max-Subscription (Claude Code)

Der Claude-Agent läuft standardmäßig über die Claude-Code-CLI statt über die API – dein Abo zahlt, nicht die Token-Uhr. Bonus: Bei Repo-Analysen bekommt Claude Code das geklonte Repo als Arbeitsverzeichnis und durchsucht den Code mit seinen eigenen Tools.

1. Auf deinem Rechner (wo du in Claude Code eingeloggt bist): `claude setup-token`
2. Den erzeugten `sk-ant-oat01-...`-Token als `CLAUDE_CODE_OAUTH_TOKEN` in die `.env`
3. Fertig – der Container bringt die Claude-Code-CLI schon mit (siehe Dockerfile)

Hinweise:
- Der Token gilt ~1 Jahr und zieht auf deine Abo-Rate-Limits ein
- `ANTHROPIC_API_KEY` leer lassen, wenn alles über die Subscription laufen soll; falls gesetzt, filtert der Boardroom ihn für den Claude-Code-Prozess automatisch raus (Print-Modus würde sonst den API-Key bevorzugen)
- Fällt Claude Code aus, greift automatisch der API-Fallback (sofern Key gesetzt)
- Claude Code läuft bei Repo-Analysen mit `--dangerously-skip-permissions` – im ephemeren Container auf einem frisch geklonten Repo ist das ok, auf deinem Laptop wäre es das nicht

### .env ausfüllen – Checkliste

- [ ] `CLAUDE_CODE_OAUTH_TOKEN` – per `claude setup-token` (empfohlen)
- [ ] `ANTHROPIC_API_KEY` – nur als Fallback nötig
- [ ] `OPENAI_API_KEY` + `OPENAI_MODEL` – Modellnamen im OpenAI-Dashboard prüfen
- [ ] `HYAI_API_KEY` – dein hostyourai-Key (beginnt mit `hyai-`)
- [ ] `HYAI_MODEL_*` – **wichtig:** exakte Modell-Slugs im HostYourAI Model Garden nachschauen und ggf. anpassen
- [ ] `PLANE_*` – siehe nächster Abschnitt

### Plane verbinden

1. In Plane: Profil-Einstellungen → API Tokens → Token erstellen → `PLANE_API_KEY`
2. `PLANE_BASE_URL` = die URL deiner Coolify-Plane-Instanz
3. `PLANE_WORKSPACE` = der Slug aus der URL (`plane.dev/DEIN-SLUG/...`)
4. `PLANE_PROJECT_ID` = Projekt öffnen → Settings → die UUID aus der URL kopieren

Ohne Plane-Konfiguration läuft alles trotzdem – der Sync wird dann einfach übersprungen und im Chat vermerkt.

## Deploy auf Coolify

1. Repo in dein Git (z. B. dein Forgejo) pushen
2. Coolify → New Resource → **Docker Compose** → Repo verbinden
3. Die Variablen aus `.env.example` als Environment Variables in Coolify eintragen
4. Domain zuweisen – **HTTPS aktivieren** (WebSockets laufen dann automatisch über WSS)
5. Deploy

> Hinweis: v1 hat keinen Login. Häng die App hinter Coolify-Basic-Auth oder deinen Auth-Proxy, bevor du sie öffentlich erreichbar machst.

## TencentDB Agent Memory (optional, empfohlen ab v1.1)

Das Team-Gedächtnis: [TencentCloud/TencentDB-Agent-Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory) (MIT, self-hosted, Docker).

1. Nach deren README als eigenen Dienst deployen (läuft mit lokalem SQLite, keine externe API nötig)
2. In der `.env` die Zeile `MEMORY_PROXY_BASE_URL` auf den Memory-Proxy zeigen lassen
3. Ab dann laufen die HostYourAI-Agenten durch das Gedächtnis – das Board erinnert sich über Meetings hinweg an dein Projekt

## Architektur (bewusst schmal)

```
Browser (Live-Chat, WebSocket)
   │
FastAPI ── Event-Bus ── Pipeline (4 Phasen)
   │                        │
   │                     LiteLLM ──► Anthropic / OpenAI / HostYourAI
   │                        │              (optional via Memory-Proxy)
   └── Plane REST API ◄─────┘
```

Kein Framework-Ballast, keine Datenbank, ein Container. Der Chat-Verlauf lebt im Speicher (Neustart = leerer Chat; die Ergebnisse sind ja in Plane gesichert).

## Kosten im Blick

Ein volles Meeting = ~10 LLM-Aufrufe (6 Gutachten + 3 Kreuzverhöre + 1 Synthese). Die HostYourAI-Modelle sind günstig; teuer sind Claude & GPT. Mit `MAX_TOKENS_REVIEW` und `CONTEXT_CHAR_BUDGET` in der `.env` steuerst du das Budget.
