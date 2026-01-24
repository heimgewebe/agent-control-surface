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

Der Diff-Endpoint nutzt aktuell `jules remote diff --session <id>`. Falls Jules hier einen anderen Subcommand verlangt, wird nur diese Zeile ersetzt.
