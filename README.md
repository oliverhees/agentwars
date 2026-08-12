# Boardroom – deine KI-Agentur

Sechs KI-Spezialisten nehmen sich dein Projekt live in einem Team-Chat vor, du sitzt mit am Tisch, und die Ergebnisse landen automatisch als Tickets in Plane oder GitHub.

## Das Team

Die Startaufstellung – **alles davon ist unter `/settings` änderbar**: Modell, Provider, Prompt, Farbe, Rolle.

| Agent | Rolle | Modell | Quelle |
|---|---|---|---|
| Claude | Lead Engineer · Chairman | claude-sonnet-4-6 | Claude Code (Subscription) |
| GPT | Robustheit & Testbarkeit | gpt-5.2 | OpenAI API |
| Kimi | Skalierung & Datenmodell | Kimi K3 | HostYourAI |
| Qwen | Architektur & Betrieb | Qwen3.5 | HostYourAI |
| DeepSeek | Sicherheit & Datenschutz | DeepSeek V4 Pro | HostYourAI |
| GLM | Devil's Advocate · Markt | GLM 5.2 | HostYourAI |

Jede Rolle ist für **beide Lagen** ausformuliert: bei bestehendem Code prüft sie, auf der grünen Wiese entwirft sie. Wer die Prompts aus einer älteren Version übernimmt, holt sich die neuen mit „AUF STANDARD ZURÜCKSETZEN" auf der Einstellungsseite.

## Einstellungsseite

Unter **`/settings`** wird das System konfiguriert, ohne die `.env` anzufassen:

- **Pro Agent:** Provider (Claude Code, Anthropic, OpenAI, HostYourAI), Modell, eigener Prompt, Name, Farbe, aktiv/inaktiv, Teilnahme am Kreuzverhör, wer Chairman ist
- **Grundregeln:** der Text, der *vor* jedem Agenten-Prompt steht – dort setzt du den Ton fürs ganze Board
- **Lage:** zwei getrennte Texte für „bestehendes Projekt" (prüfen) und „grüne Wiese" (entwerfen). Der passende wird automatisch vor die Phasenanweisung gesetzt
- **Phasenanweisungen** für Einzelbeiträge, Kreuzverhör und Synthese. Das JSON-Format für die Tickets hängt das System selbst an – das kannst du nicht kaputt machen
- **@Mention-Regeln**, die Handles setzt das System selbst ein
- **Zugänge:** alle API-Keys, Base-URLs und der Memory-Proxy
- **Plane & Coolify:** URL und Token – mit Verbindungstest. Das konkrete Plane-Projekt und die Coolify-Anwendung hängen am Boardroom-Projekt, nicht global
- **Limits:** Kontextbudget, Tokenbudgets, Diskussionsrunden
- **Modell-Auswahl statt Tippen:** Sobald ein API-Key hinterlegt ist, holt der Boardroom die Modellliste beim Anbieter selbst (OpenAI, Anthropic, HostYourAI) und bietet sie im Agenten als Dropdown an. Ein falsch geschriebener Modellname fällt so gar nicht erst auf. Freitext bleibt über „＋ Eigenes Modell eintippen" möglich, weil jede Liste veralten kann.
- **„Team prüfen & Modelle neu laden"** aktualisiert die Listen und fragt anschließend jeden Agenten wirklich an

Die Reihenfolge der Wahrheit ist **Datenbank → `.env` → Default**. Bestehende Deployments laufen also unverändert weiter, bis du im UI etwas überschreibst. Geheimnisse verlassen den Server nie im Klartext: die API meldet nur „gesetzt: ja/nein", ein leeres Feld heißt „unverändert lassen".

## Projekte – GitHub ist das Gate

Ein Projekt ohne Repo gibt es nicht. Beim Anlegen wählst du aus drei Dropdowns:

| Verknüpfung | Auswahl | Pflicht |
|---|---|---|
| **GitHub-Repo** | vorhandenes wählen oder neues privat anlegen | ja |
| **Plane-Projekt** | vorhandenes wählen oder neues anlegen | nein |
| **Coolify-Anwendung** | vorhandene wählen | nein |

Das Repo ist der Anker und wird nachträglich nicht getauscht. Plane-Projekt und Coolify-Anwendung lassen sich später jederzeit ändern – die Auswahl wirkt sofort.

**Leeres Repo ist kein Fehler, sondern ein anderer Auftrag:** Enthält das Repo Code, *prüft* das Board. Ist es leer, *entwirft* es – dieselben Rollen, anderer Auftrag (siehe „Lage" unter `/settings`).

Projekte, Meetings und der komplette Verlauf liegen in SQLite (`BOARDROOM_DB`, im Container auf dem Volume `/data`). Ein Neustart verliert nichts mehr – ältere Meetings lassen sich über `/api/projects/{id}/meetings` und `/api/meetings/{id}/events` nachlesen.

## Tickets: Plane oder GitHub Issues

`ticket_target` unter `/settings` bestimmt, wohin die Roadmap des Chairmans wandert:

- **`plane`** (Standard) – die Planungsdaten bleiben auf deiner Instanz, also DSGVO-konform. Jedes Boardroom-Projekt schreibt in sein eigenes Plane-Projekt.
- **`github`** – enger am Code: ein `Fixes #12` im PR schließt das Ticket von selbst. Dafür liegen die Planungsdaten bei GitHub. Prioritäten werden zu `prio:*`-Labels.
- **`both`** – Plane führt, GitHub ist die Arbeitsansicht.
- **`off`** – nichts anlegen.

> Ein Hinweis, falls du auf GitHub umstellst: GitHub bremst schreibende Zugriffe (Secondary Rate Limits). Issues eignen sich für **Entscheidungen**, nicht für ein Schritt-für-Schritt-Protokoll – das gehört als Markdown ins Repo.

## Der Ablauf (ein "Board-Meeting")

1. **Briefing** – du wählst ein Projekt (oder legst eins mit Repo an) und schreibst das Briefing
2. **Einzelbeiträge** – alle sechs arbeiten parallel, live gestreamt
3. **Kreuzverhör** – die Devs zerlegen die Beiträge der anderen
4. **Chairman-Synthese** – der Chairman entscheidet und baut die Roadmap
5. **Tickets** – die Roadmap wird zu Issues in Plane und/oder GitHub

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

Die Modell-Slugs sind Annahmen, bis sie jemand prüft. Stimmt einer nicht, fiel der Agent sonst erst mitten im Meeting aus.

- **„TEAM PRÜFEN"** im Startdialog: ein Vier-Token-Call pro Agent, plus der echte Modellkatalog deines HostYourAI-Routers. Bei einem falschen Slug schlägt der Preflight passende Kandidaten aus dem Katalog vor.
- **Claude Code wird echt getestet**, nicht nur auf Anwesenheit: `claude --version` fasst das Netz nicht an, ein abgelaufener Token bestünde diesen Test. Der Preflight schickt deshalb eine echte Mini-Anfrage („antworte mit OK") über die Subscription – ein paar Token für die Gewissheit, dass Installation *und* Verbindung stehen. Über `GET /api/preflight?deep=false` bleibt der schnelle Check ohne Call verfügbar.
- Vor jedem Meeting läuft der Check automatisch und meldet Ausfälle im Chat (`PREFLIGHT_ON_START=0` schaltet das ab).

## Verbrauch und Kosten

Die Seite **`/usage`** zeigt, wer wie viel verbrannt hat – gesamt oder je Projekt:

- Kosten als **Rechnungsposten** (API-Modelle) getrennt von **im Abo enthalten** (Claude Code). Beides in Euro, Kurs unter `/settings` → Kosten.
- Aufschlüsselung je Agent, je Modell, je Phase und je Projekt; im Projektfilter zusätzlich je Meeting.
- Tokens getrennt nach Ein- und Ausgabe, plus was aus dem Cache kam.

Drei Genauigkeitsstufen, und das Dashboard sagt dir welche:

| Quelle | Genauigkeit |
|---|---|
| Claude Code | exakt – die CLI meldet Tokens und Kosten selbst |
| API-Modelle über LiteLLM | die vom Anbieter gemeldete Usage |
| Kein Usage-Feld | über den Tokenizer geschätzt, im Dashboard als „geschätzt" markiert |

**Router-Modelle brauchen deine Preise.** LiteLLM kennt die Konditionen von HostYourAI nicht, also stehen sie sonst mit 0 € da (und das Dashboard sagt es dir). Unter `/settings` → Kosten eine Zeile je Modell:

```
kimi-k3      = 0.30 / 1.20
deepseek-v4-pro = 0.55 / 2.20
```

Links steht der Preis je 1 Mio. Eingabetoken, rechts je 1 Mio. Ausgabetoken, in USD. Deine Angaben schlagen die von LiteLLM – der Router rechnet anders ab als der Modellhersteller.

### Claude über deine Max-Subscription (Claude Code)

Der Claude-Agent läuft standardmäßig über die Claude-Code-CLI statt über die API – dein Abo zahlt, nicht die Token-Uhr. Bonus: Bei Repo-Analysen bekommt Claude Code das geklonte Repo als Arbeitsverzeichnis und durchsucht den Code mit seinen eigenen Tools.

Die CLI ist **bereits installiert** – der Container bringt Node 20 und
`@anthropic-ai/claude-code` mit. Es fehlt nur der Token.

1. Ein Terminal öffnen. Entweder auf deinem Rechner, oder direkt im Container:
   **Coolify → diese Ressource → Terminal**.
2. `claude setup-token` ausführen und dem angezeigten Link folgen.
3. Den Token (`sk-ant-oat01-…`) unter `/settings` → *Zugänge* ins Feld
   **Claude-Code-Token** einsetzen und speichern.
4. Auf derselben Seite unter **Claude Code** auf „Verbindung wirklich testen".

> **Nicht verwechseln:** Ein API-Key (`sk-ant-api…`) funktioniert hier nicht –
> der läuft über die Token-Abrechnung statt über dein Abo. Die Diagnose erkennt
> das und sagt es dir.

**Warum es kein Terminal im Boardroom gibt:** Eine Shell im Web-UI wäre
beliebige Befehlsausführung auf deinem Coolify-Host, abgesichert durch ein
einziges Passwort. Wer die Session bekommt, bekommt den Server. Coolify hat
für genau diesen Zweck bereits ein Terminal je Ressource – dort gehört es hin.

### Diagnose statt Rätselraten

`/settings` zeigt unter **Claude Code** drei getrennte Zeilen: CLI installiert,
Token hinterlegt (samt Quelle – Einstellungen oder `.env`), Verbindung. Vorher
las man nur „Token fehlt" und wusste nicht, ob die CLI überhaupt da ist.

Hinweise:
- Der Token gilt ~1 Jahr und zieht auf deine Abo-Rate-Limits ein
- `ANTHROPIC_API_KEY` leer lassen, wenn alles über die Subscription laufen soll; falls gesetzt, filtert der Boardroom ihn für den Claude-Code-Prozess automatisch raus (Print-Modus würde sonst den API-Key bevorzugen)
- Fällt Claude Code aus, greift automatisch der API-Fallback (sofern Key gesetzt)
- Toolrechte kommen als **Profil pro Aufruf**: Reviews laufen mit `CLAUDE_CODE_REVIEW_TOOLS` (`Read,Grep,Glob,LS,NotebookRead`) – lesen und suchen, nichts ändern, nichts ausführen. Was nicht in der Liste steht, lehnt Claude Code headless ab. Das Profil `build` (mit `Edit,Write,Bash`) ist für die spätere Umsetzungsphase vorbereitet und wird heute von keinem Codepfad angefordert.
- Im Chat siehst du live, welche Datei Claude gerade liest (`⌁ Claude: Read app/main.py`)

### .env ausfüllen – Checkliste

**Diese gehören in die `.env` (bzw. in Coolify als Environment Variables), weil sie nicht im UI stehen:**

- [ ] `BOARDROOM_PASSWORD` – **Pflicht**, sonst antwortet die App gar nicht
- [ ] `REPO_ALLOWLIST` – **Pflicht für Repo-Analysen**, z. B. `github.com`
- [ ] `BOARDROOM_SECRET` – empfohlen, sonst loggt dich jeder Redeploy aus
- [ ] `BOARDROOM_COOKIE_SECURE` – nur bei Cookie-Problemen (`0` erzwingt HTTP)
- [ ] `PREFLIGHT_ON_START`, `PREFLIGHT_TIMEOUT` – nur wenn du den Vorabcheck anders willst
- [ ] `CLAUDE_CODE_REVIEW_TOOLS` / `CLAUDE_CODE_BUILD_TOOLS` / `CLAUDE_CODE_TIMEOUT` – Toolrechte bleiben bewusst außerhalb des UI

**Alles andere gehört nach `/settings`** – Keys, Modelle, Prompts, Plane, Coolify. Die `.env` kann sie als Startbelegung mitbringen, muss aber nicht.

> `BOARDROOM_DB` im Container **nicht** setzen: das Dockerfile zeigt schon auf `/data/boardroom.db`, also aufs Volume. Ein relativer Pfad in der `.env` überschreibt das, und die Datenbank landet im Container – nach dem nächsten Redeploy wäre sie weg.
>
> Die `*_MODEL`-Variablen (`ANTHROPIC_MODEL`, `OPENAI_MODEL`, `HYAI_MODEL_*`) werden **nur beim allerersten Start** gelesen, um die Agenten-Tabelle zu befüllen. Danach kommen die Modelle aus der Datenbank – Änderungen dort bleiben wirkungslos, ändere sie unter `/settings`.

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

Base-URL und API-Token unter `/settings` hinterlegen (Coolify → Keys & Tokens → API tokens). Die **Anwendung wird pro Projekt** gewählt – eine globale Standard-Anwendung gibt es bewusst nicht, jedes Projekt deployt sich selbst. `POST /api/coolify/deploy` mit `project_id` stößt das Deployment der zugeordneten Anwendung an.

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
