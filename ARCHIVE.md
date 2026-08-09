# Retirement von agent-control-surface

Status: `retired-reference`

## Entscheidung

Die eigenständige ACS-Control-Plane wird stillgelegt. Aktuelle Operatorausführung liegt bei `heimgewebe/grabowski`; Aufgaben- und Claimzustand liegt bei `heimgewebe/bureau`.

## Closeout-Belege

Zum Retirement-Zeitpunkt gilt:

- der lokale Checkout war sauber und mit `origin/main` synchron,
- es gab keinen offenen Pull Request im Repository,
- `agent-control-surface.service` war auf dem Heim-PC nicht vorhanden,
- `acs-tunnel.service` war auf dem Heim-PC nicht vorhanden,
- Leitstand besitzt keine aktive ACS-Datenquellen- oder Environment-Schnittstelle mehr,
- Grabowski stellt die aktuelle lokale Git-/Repo-/Worktree-/Service-/Deployment-Autorität bereit.

Diese Beobachtungen sind punktuelle Closeout-Evidenz; sie begründen keine zukünftige Runtimewahrheit.

## Erhaltene Evidenz

Das Repository bleibt lesbar für frühere:

- Jules- und Session-UX,
- Patch-/Diff-Workflows,
- manuelle Git-Wizards,
- Sicherheitsgrenzen der lokalen Weboberfläche,
- WGX-/Git-Routinen und Designentscheidungen.

## Harte Grenze

Keine Datei dieses Repositories ist aktuelle Operator-, Task-, Git- oder Runtime-Autorität. Eine spätere Bedienoberfläche soll die aktuellen Grabowski-Verträge konsumieren, statt ACS als zweite Control-Plane wiederzubeleben.
