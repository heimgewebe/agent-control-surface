# agent-control-surface — historische manuelle Steuerfläche

> **Status: außer Betrieb / historische Referenz.** `agent-control-surface` (ACS) ist kein aktiver Git-, Jules- oder Operator-Control-Plane mehr. Aktuelle lokale Repo-, Git-, Worktree-, Prozess-, Service-, Review- und Deploymentausführung gehört zu [`heimgewebe/grabowski`](https://github.com/heimgewebe/grabowski); Aufgaben- und Claimwahrheit gehört zu [`heimgewebe/bureau`](https://github.com/heimgewebe/bureau).

ACS bleibt erhalten, damit frühere UI-, Jules- und manuelle Git-Workflow-Entscheidungen nachvollziehbar bleiben. Der frühere lokale Webdienst und der ACS-Tunnel sind keine aktuellen Runtimepfade.

## Historische Rolle

ACS stellte früher eine lokale Weboberfläche bereit für:

- Jules-Sessions und Promptübergabe,
- Diff-/Patch-Anzeige und -Anwendung,
- manuelle Branch-, Commit-, Push- und PR-Schritte,
- begrenzte WGX-/Git-Diagnostik und Routinen.

Diese Funktionen begründen heute **keine eigene Ausführungsautorität** mehr. Historische Endpunkte, Secrets, Tunnel- oder systemd-Anweisungen sind nicht als aktuelle Betriebsanleitung zu verwenden.

## Aktuelle Zuständigkeiten

- **lokale Ausführung und Git-/Worktree-Effekte:** `heimgewebe/grabowski`
- **Task-, Claim- und Completion-Wahrheit:** `heimgewebe/bureau`
- **read-only Operatorstatus:** `heimgewebe/leitstand`
- **Systemrollen und stabile Beziehungen:** `heimgewebe/systemkatalog`
- **dieses Repository:** historische Provenienz und UI-/Workflow-Referenz

Falls künftig wieder eine menschliche Web- oder iPad-Steuerfläche sinnvoll wird, soll sie als dünner Client auf den aktuellen Grabowski-Verträgen aufsetzen und **keine zweite Git-, Task- oder Runtime-Autorität** einführen.

## Historische Inhalte

Der vorhandene Quellcode, die Dokumentation und Tests bleiben lesbar, um frühere Sicherheits- und UX-Entscheidungen nachvollziehen zu können. Neue Produktentwicklung gehört nicht mehr in dieses Repository.

Siehe [`ARCHIVE.md`](ARCHIVE.md) für den Retirement-Closeout.
