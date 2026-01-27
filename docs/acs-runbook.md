# Agent Control Surface (ACS) – Runbook (ideal)

Stand: 2026-01  
Repo: heimgewebe/agent-control-surface  
Ziel: ACS jederzeit reproduzierbar starten, updaten und erreichen – ohne Nebenwirkungen beim Start.

## Grundprinzipien (Invarianten)
1. Start ≠ Update: Start installiert keine Dependencies.
2. ACS bindet nur an localhost: 127.0.0.1:8099.
3. Zugriff nur per SSH-Tunnel (PC persistent, iPad manuell).
4. systemd --user ist maßgeblich; Logs im Journal.

## Architektur
Pop!_OS/iPad nutzen `http://127.0.0.1:8099` lokal — per SSH Port-Forward auf Heimserver `127.0.0.1:8099`.

## Heimserver
### Service
`systemd/agent-control-surface.service` startet `scripts/acs-run`.

### Start (read-only)
`scripts/acs-run` startet Uvicorn nur, wenn `.venv` vollständig ist.

### Update (einziger Ort für Dependency-Änderungen)
`scripts/acs-up`:
- auto-stash bei lokalen Änderungen
- `git pull --ff-only`
- venv sicherstellen
- `pip install -e .`
- restart service
- health-retry auf `/api/health`

### Installation
Einmalig:
- `./scripts/acs-install`

## Pop!_OS (empfohlen)
### SSH Config
`~/.ssh/config`:
- Host `acs`
- `LocalForward 8099 127.0.0.1:8099`

### Persistenter Tunnel (systemd --user)
`~/.config/systemd/user/acs-tunnel.service` startet `ssh -N acs`.

### Komfort
- `acs`: Tunnel + Service start + Browser öffnen
- `acs-up`: Update remote + Browser öffnen

## iPad (direkt)
Tunnel-App (Blink/Termius/Prompt):
`ssh -N -L 8099:127.0.0.1:8099 alex@heimserver`

Safari:
`http://127.0.0.1:8099`

## Diagnose
Server:
- `systemctl --user status agent-control-surface.service`
- `journalctl --user -u agent-control-surface.service -n 200 --no-pager`

Client:
- `systemctl --user status acs-tunnel.service`
- `ss -ltn | grep 8099 || true`

## Verdichtete Essenz
ACS ist lokal. Zugriff ist getunnelt. Update ist explizit. Start ist deterministisch.
