# Boardroom – deine KI-Agentur

Sechs KI-Spezialisten zerlegen dein Projekt live in einem Team-Chat, du sitzt mit am Tisch, und die Ergebnisse landen automatisch als Issues in Plane.

## Das Team

Die Startaufstellung – **alles davon ist unter `/settings` änderbar**: Modell, Provider, Prompt, Farbe, Rolle.

| Agent | Rolle | Modell | Quelle |
|---|---|---|---|
| Claude | Senior Dev + Chairman | claude-sonnet-4-6 | Claude Code (Subscription) |
| GPT | Senior Dev | gpt-5.2 | OpenAI API |
| Kimi | Senior Dev | Kimi K3 | HostYourAI |
| Qwen | Architektur & Tooling | Qwen3.5 | HostYourAI |
| DeepSeek | Security & Reasoning | DeepSeek V4 Pro | HostYourAI |
| GLM | Devil's Advocate | GLM 5.2 | HostYourAI |

## Einstellungsseite

Unter **`/settings`** wird das System konfiguriert, ohne die `.env` anzufassen:

- **Pro Agent:** Provider (Claude Code, Anthropic, OpenAI, HostYourAI), Modell, eigener Prompt, Name, Farbe, aktiv/inaktiv, Teilnahme am Kreuzverhör, wer Chairman ist
- **Grundregeln:** der Text, der *vor* jedem Agenten-Prompt steht – dort setzt du den Ton fürs ganze Board. Die @Mention-Regeln stehen daneben, die Handles setzt das System selbst ein
- **Zugänge:** alle API-Keys, Base-URLs und der Memory-Proxy
- **Plane & Coolify:** URL, Token, Workspace, Projekt – mit Verbindungstest
- **Limits:** Kontextbudget, Tokenbudgets, Diskussionsrunden
- **„Modelle vom Router laden"** holt den echten Modellkatalog von HostYourAI in die Auswahlfelder – Schluss mit geratenen Slugs

Die Reihenfolge der Wahrheit ist **Datenbank → `.env` → Default**. Bestehende Deployments laufen also unverändert weiter, bis du im UI etwas überschreibst. Geheimnisse verlassen den Server nie im Klartext: die API meldet nur „gesetzt: ja/nein", ein leeres Feld heißt „unverändert lassen".

## Projekte – GitHub ist das Gate

Ein Projekt ohne Repo gibt es nicht. Beim Anlegen gilt:

- **Repo vorhanden** → `owner/repo` angeben, der Boardroom prüft es und klont es fürs Meeting
- **Kein Repo** → Haken bei „Repo neu anlegen", der Boardroom legt es privat an. Das Board erkennt das leere Repo und startet mit „worum geht's?" statt mit einer Analyse

Dafür braucht die `.env` einen `GITHUB_TOKEN` (Personal Access Token, Scope `repo`). Der Token landet nur in der Klon-URL und wird in Chat und Logs geschwärzt.

Projekte, Meetings und der komplette Verlauf liegen in SQLite (`BOARDROOM_DB`, im Container auf dem Volume `/data`). Ein Neustart verliert nichts mehr – ältere Meetings lassen sich über `/api/projects/{id}/meetings` und `/api/meetings/{id}/events` nachlesen.

## Der Ablauf (ein "Board-Meeting")

1. **Briefing** – du wählst ein Projekt (oder legst eins mit Repo an) und schreibst das Briefing
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

### Zugang

Der Boardroom ist **fail-closed**: Ohne `BOARDROOM_PASSWORD` in der `.env` antwortet die App auf jede Anfrage mit `503` und einem Hinweis. Das ist Absicht – der Dienst klont Repos und startet Claude Code mit Dateizugriff, offen im Netz wäre das ein Fernzugriff auf deinen Host.

- Login-Seite mit Passwort, danach ein signiertes HttpOnly-Cookie (7 Tage, per `BOARDROOM_SESSION_HOURS` einstellbar)
- Das Cookie deckt auch den WebSocket ab
- 8 Fehlversuche pro IP in 5 Minuten → Sperre
- `BOARDROOM_SECRET` setzen, wenn ein Redeploy dich nicht ausloggen soll
- Nur wenn die App nachweislich ausschließlich lokal erreichbar ist: `BOARDROOM_ALLOW_ANONYMOUS=1`

### Erlaubte Repo-Quellen

`REPO_ALLOWLIST` bestimmt, von welchen Hosts geklont werden darf – leer heißt: gar nicht. Alles andere wird schon im Startformular abgelehnt, nicht erst im Meeting:

```
REPO_ALLOWLIST=github.com,git.deine-domain.de
```

Geprüft wird auf Schema (`https`/`ssh`), Host-Zugehörigkeit, rohe IP-Adressen und Option-Injection. `git` läuft ohne Credential-Prompts, ohne `ext::`- und `file::`-Transporte und ohne Submodule.

### Preflight – antworten die Modelle überhaupt?

Die Modell-Slugs in der `.env` sind Annahmen, bis sie jemand prüft. Stimmt einer nicht, fiel der Agent bisher erst mitten im Meeting aus.

- **„TEAM PRÜFEN"** im Startdialog: ein Vier-Token-Call pro Agent, plus der echte Modellkatalog deines HostYourAI-Routers. Bei einem falschen Slug schlägt der Preflight passende Kandidaten aus dem Katalog vor.
- Vor jedem Meeting läuft der Check automatisch und meldet Ausfälle im Chat (`PREFLIGHT_ON_START=0` schaltet das ab).

### Claude über deine Max-Subscription (Claude Code)

Der Claude-Agent läuft standardmäßig über die Claude-Code-CLI statt über die API – dein Abo zahlt, nicht die Token-Uhr. Bonus: Bei Repo-Analysen bekommt Claude Code das geklonte Repo als Arbeitsverzeichnis und durchsucht den Code mit seinen eigenen Tools.

1. Auf deinem Rechner (wo du in Claude Code eingeloggt bist): `claude setup-token`
2. Den erzeugten `sk-ant-oat01-...`-Token als `CLAUDE_CODE_OAUTH_TOKEN` in die `.env`
3. Fertig – der Container bringt die Claude-Code-CLI schon mit (siehe Dockerfile)

Hinweise:
- Der Token gilt ~1 Jahr und zieht auf deine Abo-Rate-Limits ein
- `ANTHROPIC_API_KEY` leer lassen, wenn alles über die Subscription laufen soll; falls gesetzt, filtert der Boardroom ihn für den Claude-Code-Prozess automatisch raus (Print-Modus würde sonst den API-Key bevorzugen)
- Fällt Claude Code aus, greift automatisch der API-Fallback (sofern Key gesetzt)
- Toolrechte kommen als **Profil pro Aufruf**: Reviews laufen mit `CLAUDE_CODE_REVIEW_TOOLS` (`Read,Grep,Glob,LS,NotebookRead`) – lesen und suchen, nichts ändern, nichts ausführen. Was nicht in der Liste steht, lehnt Claude Code headless ab. Das Profil `build` (mit `Edit,Write,Bash`) ist für die spätere Umsetzungsphase vorbereitet und wird heute von keinem Codepfad angefordert.
- Im Chat siehst du live, welche Datei Claude gerade liest (`⌁ Claude: Read app/main.py`)

### .env ausfüllen – Checkliste

- [ ] `BOARDROOM_PASSWORD` – **Pflicht**, sonst startet die App nicht
- [ ] `GITHUB_TOKEN` – **Pflicht**, ohne GitHub kein Projekt
- [ ] `REPO_ALLOWLIST` – **Pflicht für Repo-Analysen**, z. B. `github.com`
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

> **Kein Host-Port.** Die `docker-compose.yml` nutzt `expose` statt `ports` – Coolify routet über Traefik intern auf Port 8000. Ein Host-Mapping würde mit allem kollidieren, was auf dem Server schon auf 8000 läuft (`Bind for 0.0.0.0:8000 failed: port is already allocated`). Lokal veröffentlicht die `docker-compose.override.yml` den Port; Coolify lädt Overrides nicht, weil es die Compose-Datei ausdrücklich mit `-f` angibt.

### Landet die Domain auf Coolifys Fallback-Seite?

Dann läuft der Container, aber Traefik hat keinen passenden Router – ein reines Routing-Problem, kein App-Fehler. Zwei Stellen:

1. **Die Domain muss am Service `boardroom` hängen**, nicht an der Ressource allgemein. In Coolify: Ressource → Tab **Domains** → Zeile `boardroom`.
2. **Der Port gehört in die Domain:** `https://board.deine-domain.de:8000`. Ohne den Port-Suffix muss Coolify raten. Die `SERVICE_FQDN_BOARDROOM_8000`-Variable in der Compose-Datei sagt es zusätzlich ausdrücklich.

Prüfen lässt sich das ohne Domain: In Coolify das Terminal der Ressource öffnen und `curl -fsS http://127.0.0.1:8000/healthz` laufen lassen. Kommt `{"ok":true,…}`, ist die App gesund und es ist definitiv Traefik.

> `BOARDROOM_PASSWORD` und `BOARDROOM_SECRET` gehören in Coolify als **geheime** Environment Variables, nicht ins Repo. Gleiches gilt für `CLAUDE_CODE_OAUTH_TOKEN` – der Token gewährt vollen Zugriff auf deine Subscription.

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```

Abgedeckt sind die Stellen, an denen ein Fehler teuer wird: Repo-URL-Validierung, Session-Tokens und Login-Sperre, der Türsteher vor HTTP und WebSocket, das GitHub-Gate beim Projektanlegen, die Persistenz über einen Neustart hinweg, der Mention-Parser und die Issue-Extraktion aus der Chairman-Antwort.

## Coolify als Realitätscheck (optional)

Base-URL und API-Token unter `/settings` hinterlegen (Coolify → Keys & Tokens → API tokens). Damit kann der Boardroom Anwendungen auflisten (`GET /api/coolify/applications`) und ein Deployment auslösen (`POST /api/coolify/deploy`).

Bewusst die REST-API und nicht MCP: der Boardroom ist selbst ein Server, der HTTP spricht. Ein MCP-Server dazwischen wäre ein zusätzlicher Prozess, eine zusätzliche Auth-Schicht und ein zusätzlicher Ausfallpunkt für drei Endpunkte, die wir direkt aufrufen können.

## TencentDB Agent Memory (optional, empfohlen ab v1.1)

Das Team-Gedächtnis: [TencentCloud/TencentDB-Agent-Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory) (MIT, self-hosted, Docker).

1. Nach deren README als eigenen Dienst deployen (läuft mit lokalem SQLite, keine externe API nötig)
2. In der `.env` die Zeile `MEMORY_PROXY_BASE_URL` auf den Memory-Proxy zeigen lassen
3. Ab dann laufen die HostYourAI-Agenten durch das Gedächtnis – das Board erinnert sich über Meetings hinweg an dein Projekt

## Architektur (bewusst schmal)

```
Browser (Login → Projekt wählen → Live-Chat, WebSocket)
   │
Session-Guard (HTTP + WS)
   │
FastAPI ── GitHub API (Repo prüfen / anlegen / klonen)
   │
   ├── SQLite (Projekte, Meetings, Verlauf)
   │
   └── Event-Bus ── Pipeline (4 Phasen)
          │            │
          │         Claude Code (Subscription, Toolprofil "review")
       Preflight       │
                    LiteLLM ──► Anthropic / OpenAI / HostYourAI
                       │              (optional via Memory-Proxy)
       Plane REST API ◄┘
```

Kein Framework-Ballast, ein Container, eine SQLite-Datei. Der Live-Chat läuft über den Event-Bus, parallel wandert jedes Ereignis in die Datenbank – der Verlauf überlebt Neustarts.

## Kosten im Blick

Ein volles Meeting = ~10 LLM-Aufrufe (6 Gutachten + 3 Kreuzverhöre + 1 Synthese). Die HostYourAI-Modelle sind günstig; teuer sind Claude & GPT. Mit `MAX_TOKENS_REVIEW` und `CONTEXT_CHAR_BUDGET` in der `.env` steuerst du das Budget.
