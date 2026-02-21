# agent-control-surface (acs)

Local-only web UI to drive Jules CLI sessions and a safe, step-by-step Git workflow. This is a control surface (Lenkrad), **not** an autopilot.

## Zweck & Nicht-Zweck

**Zweck**
- Jules-Sessions listen, anlegen und Diff anzeigen/kopieren.
- Diffs/Patches aus ChatGPT einfügen und sicher anwenden.
- Git-Wizard: Branch → Status → Diff → Commit → Push → (optional) PR vorbereiten.

**Nicht-Zweck**
- Kein Jules-Fork, kein Jules-Ersatz.
- Kein Automations-Bot.
- Keine Remote-Ports, kein Reverse Proxy, kein TLS, kein Auth-Layer.

## Sicherheitsmodell (warum kein Login nötig ist)

- Der Server bindet ausschließlich an `127.0.0.1`.
- Zugriff erfolgt nur über einen SSH-Tunnel (LocalForward).
- Alle Befehle werden als Argument-Arrays ausgeführt (kein `shell=True`).
- Repos sind hart allow-listed und liegen unter `~/repos/heimgewebe/...`.
- Schreibende Aktionen blockieren `main`/`master`.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Start lokal:

```bash
uvicorn panel.app:app --host 127.0.0.1 --port 8099
```

## systemd User Service

```bash
./scripts/install-user-service.sh
```

Die Unit liegt in `systemd/agent-control-surface.service` und startet das Panel lokal auf `127.0.0.1:8099`.

**Annahme:** Die `.venv` liegt in `~/repos/heimgewebe/agent-control-surface/.venv` und `jules` ist im PATH des systemd-Users verfügbar (z.B. via `~/.local/bin`). Bei Änderungen am venv-Pfad muss die ExecStart-Zeile in der Service-Datei angepasst werden.

## iPad Zugriff via SSH-Tunnel (Blink Beispiel)

In der Blink-SSH-Config:

```
Host acs
  HostName 10.7.0.1
  User alex
  LocalForward 8099 127.0.0.1:8099
  ExitOnForwardFailure yes
  ServerAliveInterval 30
  ServerAliveCountMax 3
  RequestTTY no
  RemoteCommand /bin/true
```

Nutzung:

- `ssh acs`
- iPad Safari: `http://127.0.0.1:8099`

## Warnhinweise (Pflicht)

- **Branch-Blockade:** keine schreibenden Aktionen auf `main`/`master`.
- **Bewusste Schritte:** jeder Git-Schritt ist manuell und explizit.
- **Allowlist:** keine freien Repo-Pfade, nur definierte Ziele.

## Publish (Push+PR)

- Ein neuer Button im PR-Wizard führt `git add/commit`, `git push` und `gh pr create` in einem Job aus.
- Der Endpoint `POST /api/git/publish` startet einen Background-Job und liefert `{ job_id, correlation_id }`.
- Der Job-Status ist via `GET /api/jobs/{job_id}` abrufbar; die Ergebnisse enthalten strukturierte `ActionResult`-Einträge inklusive `stdout/stderr`, `error_kind` und `pr_url`.
- Actions werden optional als JSONL nach `~/.local/state/agent-control-surface/logs/YYYY-MM-DD.jsonl` geloggt (aktivieren via `ACS_ACTION_LOG=1`, Secrets werden redacted).
- Hinweis: Ein PR entsteht erst nach einem erfolgreichen Push; der Publish-Flow bündelt Push + PR in einem Schritt.
- Alle Aktionen setzen ein explizit ausgewähltes Repo aus der UI voraus (Keys aus der Allowlist in `panel/repos.py`); solange kein Repo gesetzt ist, sind die Buttons deaktiviert.

Siehe `docs/publish.md` für curl-Beispiele.

## Repo-Layout

```
.
├─ README.md
├─ pyproject.toml
├─ panel/
│  ├─ __init__.py
│  ├─ app.py
│  ├─ runner.py
│  ├─ repos.py
│  └─ templates/
│     └─ index.html
├─ systemd/
│  └─ agent-control-surface.service
└─ scripts/
   └─ install-user-service.sh
```

## Hinweise zur Jules-Diff-CLI

Der Diff-Endpoint nutzt aktuell `jules remote pull --session <id>`. Falls Jules hier einen anderen Subcommand verlangt, wird nur diese Zeile ersetzt.

## Ops / Git Health (Audit & Routinen)

Das ACS bietet eine Integration für den `wgx`-Leitstand (externes CLI-Tool), um Git-Probleme (dangling refs, detached head, missing upstream) zu diagnostizieren und zu reparieren.

### Konfiguration

- **`ACS_CORS_ALLOW_ORIGINS`** (Env): Komma-getrennte Liste erlaubter Origins für CORS.
  - Default: leer (kein CORS).
  - Beispiel für Leitstand + Local Dev: `http://localhost:5173,http://127.0.0.1:5173`
- **`ACS_ENABLE_ROUTINES`** (Env): Aktiviert mutierende Ops-Endpunkte.
  - Default: `false`.
  - Setzen auf `true` aktiviert `/api/routine/preview` und `/api/routine/apply`.
  - **Sicherheitshinweis:** Nur aktivieren, wenn ACS in einem gesicherten Netz läuft oder hinter einem Auth-Proxy steht. Routinen führen Shell-Kommandos im Kontext des Users aus.
- **`ACS_ROUTINES_SHARED_SECRET`** (Env): Shared Secret für Actor-Endpunkte.
  - Erforderlich, wenn Routinen aktiviert sind.
  - `/api/routine/preview` und `/api/routine/apply` erwarten den Header `X-ACS-Actor-Token: <secret>`.
  - Dient zur Absicherung gegen unbefugte Aufrufe (z.B. CSRF).
  - **Empfehlung:** Ein langes, zufälliges Secret verwenden (z.B. via `openssl rand -hex 32`).

> **Wichtig:** Confirm-Tokens werden aktuell **in-memory** (pro Prozess) gespeichert. Bei einem Deployment mit mehreren Workern/Pods ist ein Token ungültig, wenn Preview und Apply auf unterschiedlichen Instanzen landen.

### Endpunkte

- `GET /api/audit/git/sync`: Führt `wgx audit git` synchron aus und liefert das Ergebnis (bevorzugt für Viewer).
- `GET /api/audit/git/latest`: Liefert das letzte gespeicherte Audit-Artefakt.
- `POST /api/audit/git`: Startet Audit als Background-Job.
- `POST /api/routine/preview`: Startet Dry-Run für eine Routine (liefert `confirm_token` und `preview_hash`).
- `POST /api/routine/apply`: Führt Routine aus (benötigt `confirm_token` und `preview_hash` aus dem Preview-Schritt).

### Semantik der API-Antworten

Bei Audit-Jobs (`/api/audit/git`) gilt:
- **`ActionResult.ok`**: Beschreibt den technischen Erfolg der *Ausführung* (Tool lief durch, Artefakt erstellt). Auch bei Audit-Fehlern ("Findings") ist `ok=true`.
- **`result.audit.status`**: Enthält das eigentliche Audit-Ergebnis (`ok`, `warn`, `error`).
- **Job Status**: Wird auf `error` gesetzt, wenn das Audit Findings (`status=error`) meldet oder ein technischer Fehler auftrat. Das bedeutet, `error` kann Findings ODER technische Fehler signalisieren. Unterscheidung über `ActionResult.ok`.
  - **Unterscheidungsregel**: Wenn `job.status == "error"`:
    - `ActionResult.ok == true` ⇒ Audit Findings (logical error)
    - `ActionResult.ok == false` ⇒ Technical Failure (execution error)
