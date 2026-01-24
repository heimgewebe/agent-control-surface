# agent-control-surface RUNBOOK (Heimserver)

Dieses Dokument ist die kanonische Betriebsanleitung für agent-control-surface (acs).
Ziel: reproduzierbar starten, erreichbar machen, Fehler schnell eingrenzen.

## 0) Voraussetzungen (Checkliste)

- Repo liegt auf dem Heimserver unter: `~/repos/heimgewebe/agent-control-surface`
- Python vorhanden: `python3 --version`
- Jules CLI vorhanden (für Session/Diff): `which jules && jules --help | head`
- Allowlist-Repos existieren (oder anpassen): siehe `panel/repos.py`

## 1) Erstinstallation (Heimserver)

```bash
cd ~/repos/heimgewebe/agent-control-surface

python3 -m venv .venv
source .venv/bin/activate

python -m pip install -U pip
pip install -e .

# Quick sanity checks:
python -c "import panel.app; print('import ok')"
python -c "from pathlib import Path; import panel.app as a; p=Path(a.__file__).parent/'templates'/'index.html'; print(p.exists())"
```

## 2) Manuell starten (Debug-Modus)

```bash
cd ~/repos/heimgewebe/agent-control-surface
source .venv/bin/activate
python -m uvicorn panel.app:app --host 127.0.0.1 --port 8099
```

Checks auf dem Heimserver:

```bash
ss -ltnp | grep 8099 || echo "nichts auf 8099"
curl -sS http://127.0.0.1:8099/ | head
```

## 3) Zugriff von Pop!_OS / iPad (SSH-Tunnel)

Wichtig: agent-control-surface bindet an 127.0.0.1 des Heimservers.
Du musst daher von außen tunneln.

Pop!_OS → Heimserver:

```bash
ssh -N -L 8099:127.0.0.1:8099 alex@heimserver
```

Dann lokal auf Pop!_OS:

```bash
curl -sS http://127.0.0.1:8099/ | head
```

Wenn du “Connection refused” siehst:

- entweder läuft uvicorn nicht
- oder er bindet nicht an 127.0.0.1:8099
- oder du tunnelst auf die falsche Zielmaschine

iPad (Blink) Beispiel-Host:

```ssh-config
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
- Safari: http://127.0.0.1:8099

## 4) Updates nach Merge (Heimserver)

```bash
cd ~/repos/heimgewebe/agent-control-surface
git pull --ff-only
source .venv/bin/activate
pip install -e .
```

Wenn uvicorn/systemd läuft: neu starten (siehe unten).

## 5) systemd –user Service (Dauerbetrieb)

Installieren:

```bash
cd ~/repos/heimgewebe/agent-control-surface
./scripts/install-user-service.sh
```

Status/Logs:

```bash
systemctl --user status agent-control-surface.service --no-pager
journalctl --user -u agent-control-surface.service -n 200 --no-pager
```

Typischer Stolperstein: jules im PATH (NVM)

Wenn jules unter ~/.nvm/... liegt, hat systemd das oft nicht im PATH.
Symptom: UI läuft, aber Sessions/Diff liefern Fehler/Help.

Schnelle Fix-Optionen:

1. In der systemd Unit Environment=PATH=... setzen (inkl. ~/.nvm/.../bin)
2. oder ExecStart= über ein Wrapper-Skript laufen lassen, das NVM lädt
3. oder jules nach ~/.local/bin symlinken, damit PATH stabil ist

(Aus Sicherheitsgründen bevorzugt: ~/.local/bin/jules Symlink + expliziter PATH.)

## 6) Funktionstest (UI)

- “List sessions” muss Output liefern (oder leere Liste)
- “Show diff” muss einen Patch liefern, der mit diff --git beginnt
- “Apply patch” nutzt git apply --check und wendet dann an

## 7) Debug: wenn “Show diff” nur Help-Text zeigt

Ursachen:

- falscher Jules-Subcommand/Argumente
- kein Patch für die Session
- Jules liefert Logs/Prelude und kein diff

Soforttest im Terminal:

```bash
jules remote list --session
jules remote pull --session <ID> | head
```

Wenn kein diff --git kommt: kein Patch vorhanden oder Jules-Output anders.

## 8) Allowlist-Repos

In panel/repos.py sind Repos fest verdrahtet.
Wenn auf dem Heimserver ~/repos/heimgewebe/metarepo etc. fehlen:

- entweder Repos clonen
- oder allowlist auf vorhandene Repos anpassen

---

## Risikoabschätzung

**Niedrig**, aber wichtig: In dem Runbook musst du den **NVM/systemd-PATH**-Punkt drinlassen,
sonst stolperst du jedes Mal wieder rein.

---

## Verdichtete Essenz

Eine Runbook-Datei macht den Betrieb deterministisch: Start, Tunnel, Checks, Logs, PATH-Fallen.

---

## ∴ Ungewissheitsursachenanalyse

**Unsicherheitsgrad:** 0.10

**Ursachen:**

- systemd-Umgebungen variieren (PATH/NVM), ohne dass man es „sieht“
- Jules-CLI Output/Flags sind nicht garantiert stabil
