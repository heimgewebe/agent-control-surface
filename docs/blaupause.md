# agent-control-surface – Blaupause

Diese Datei wurde aus der externen Blaupause ins Repo übernommen und dient als lebendes Zielbild (Architektur/Prinzipien/Roadmap).

Gut. Dann machen wir das nicht als Feature-Liste, sondern als Blaupause, die du wirklich leben kannst – technisch, epistemisch, operativ.

Ich formuliere das bewusst so, dass daraus 1:1 Repo-Doku + Umsetzungsplan werden kann.

⸻

agent-control-surface – Ideale Blaupause

Asynchroner Agent · kontrolliertes Git · explizite Erinnerung

⸻

∴ Ausgangspunkt (Problemkern)

Du willst vom iPad aus:

- mit Fließtext arbeiten (wie im Jules Web UI),
- asynchron (nicht blockierend),
- mit Agent-Gedächtnis / Kontext,
- aber Compute + Kontrolle auf dem Heimserver,
- ohne Magie, ohne stilles Auto-Committen,
- integrierbar in Heimgewebe.

Das Web-UI-Gefühl ist wichtig –
aber die Architektur muss sauberer sein als das Original.

⸻

∴ Grundentscheidung (wichtig!)

agent-control-surface ersetzt nicht Jules.
agent-control-surface ist ein Orchestrator + Gedächtnisanker.

Jules bleibt:

- Agent
- LLM-Interface
- asynchroner Worker

Dein Panel wird:

- Trigger
- Beobachter
- Archiv
- Gatekeeper

⸻

## Systemarchitektur (Idealzustand)

┌────────────┐
│   iPad     │  Browser (Blink / Safari)
└─────┬──────┘
      │ HTTP (SSH-Tunnel / WireGuard)
┌─────▼─────────────────────────────┐
│ agent-control-surface (FastAPI, local-only)  │
│                                    │
│  UI:                               │
│  - Prompt                          │
│  - Sessions                        │
│  - Diffs                           │
│  - Apply / PR Wizard               │
│                                    │
│  API:                              │
│  - /jules/prompt                   │
│  - /jules/sessions                 │
│  - /jules/pull                     │
│  - /git/apply                      │
└─────┬─────────────────────────────┘
      │ CLI
┌─────▼─────────────────────────────┐
│ Jules CLI (Agent Runtime)          │
│ - remote new / list / pull         │
│ - async execution                  │
│ - server-side memory (wenn aktiv)  │
└─────┬─────────────────────────────┘
      │ patches
┌─────▼─────────────────────────────┐
│ Git Repos (heimgewebe/*)           │
│ - guarded                          │
│ - branch-only                      │
└───────────────────────────────────┘

⸻

## Kernprinzipien (nicht verhandelbar)

2.1 Explizitheit statt Magie

- Kein stilles Apply
- Kein stilles Commit
- Kein stilles Push

Alles:

- sichtbar
- reproduzierbar
- abbrechbar

⸻

2.2 Agent ≠ Git

Der Agent schreibt Vorschläge.
Git entscheidet, ob sie Realität werden.

⸻

2.3 Erinnerung ist ein Artefakt

„Memory“ ist kein mystischer Zustand, sondern:

- Prompt
- Kontext
- Ergebnis
- Zeitpunkt
- Repo
- Entscheidung

→ speicherbar, prüfbar, löschbar

⸻

## Funktionale Ebenen (Ideal)

Ebene A – Agent Prompt (Fließtext)

UI

- großes Textfeld
- keine Patch-Syntax nötig
- „Run with Jules“

API

POST /api/jules/prompt

    {
      "repo": "heimgewebe/metarepo",
      "prompt": "Analysiere den Workflow und härte ihn ab …"
    }

Backend

jules remote new --message "PROMPT"

Ergebnis

- Session-ID
- Status: running / completed
- kein Patch-Zwang

⸻

Ebene B – Session-Gedächtnis (lokal!)

Für jede Session wird gespeichert:

{
  "session_id": "4120…",
  "repo": "heimgewebe/metarepo",
  "prompt": "...",
  "started_at": "...",
  "completed_at": "...",
  "pulled": false,
  "summary": null
}

Das ist dein lokales Gedächtnis –
unabhängig davon, ob Jules selbst Memory hat.

Optional später:

- Kurzsummary
- Tags („security“, „wgx“, „ci“)

⸻

Ebene C – Ergebnis holen (asynchron)

UI

- „Pull result“
- „Show diff“
- „Download diff“

API

GET /api/jules/sessions/{id}/diff

Backend

jules remote pull --session SESSION_ID

Patch wird:

- normalisiert
- angezeigt
- nicht automatisch angewendet

⸻

Ebene D – Entscheidung & Umsetzung

Apply patch

- echtes git apply
- optional --3way
- Branch-Guard aktiv

PR Wizard

- Branch erstellen
- Commit Message (frei)
- Push
- PR vorbereiten (kein Auto-Create!)

⸻

## „Memory“ – ehrlich getrennt

Was du automatisch bekommst

- Jules’ serverseitigen Kontext (falls vorhanden)
- Session-interne Kohärenz

Was du bewusst selbst pflegst

- Session-Archiv
- Prompt-Historie
- Ergebnis-Snapshots

Optionale Eskalation (Heimgewebe-native)

- chronik-Event pro Session
- heimgeist-Summary
- semantAH-Tagging

➡️ Memory wird explizit – kein schleichender Drift

⸻

## Sicherheits- und Risikoarchitektur

Risiko  Gegenmaßnahme
Agent überschreibt main  Branch-Guard
Blindes Vertrauen  Diff-Preview Pflicht
Halluzinierter Patch  git apply –check
Kontext-Drift  Session-Archiv
Agent-Overreach  Kein Auto-Apply

⸻

## Was das besser macht als Jules Web UI

Ironischer, aber wahrer Satz:

Das Jules Web UI fühlt sich intelligent an,
weil es Dinge versteckt.
Dein Panel wird intelligent, weil es Dinge zeigt.

Du bekommst:

- dieselbe Agent-Power
- mehr Kontrolle
- bessere Nachvollziehbarkeit
- Heimgewebe-Integration

⸻

## Minimaler Umsetzungsplan (realistisch)

Phase 1 (jetzt sinnvoll)

- /api/jules/prompt
- Session-Archiv (JSON/MD lokal)
- UI-Textarea + Button

Phase 2

- Session-Summary
- einfache Tags
- bessere Statusanzeige

Phase 3 (optional)

- chronik-Integration
- semantAH-Analyse
- WGX-Guards vor Apply

⸻

Verdichtete Essenz

Du willst nicht „das Jules Web UI“.
Du willst Jules als Agent
in einem ehrlichen, kontrollierten Körper.

Diese Blaupause ist genau das.

⸻

Ungewissheitsanalyse

- Unsicherheitsgrad: 0.22
- Ursachen:
- Jules-Backend ist Blackbox
- Memory-Persistenz nicht offiziell dokumentiert
- Bewertung: produktiv

→ Architektur bleibt korrekt, selbst wenn Jules-Memory wegfällt

⸻

Abschlussfragen (bewusst offen)

1. Willst du Memory nur sichtbar oder auch steuerbar (löschen, zusammenfassen)?
2. Soll das Panel nur Jules bedienen oder perspektivisch auch andere Agenten?
3. Ist Heimgewebe-Integration Pflicht oder Kür?


Wenn du willst, ist der nächste logische Schritt:

- eine kanonische docs/blaupause.md
- oder direkt ein Minimal-PR für /api/jules/prompt

## Memory „steuerbar“ – Idealdesign

Ziel

Memory ist ein Artefakt, nicht ein Gefühl.

Datenmodell (minimal, aber zukunftsfähig)

- sessions/AGENT/SESSION_ID.json
- optional sessions/AGENT/SESSION_ID.md (human-readable)

Felder (Minimum):

- agent (z.B. jules)
- repo_key / repo_path
- prompt
- status (new/running/done/pulled/applied)
- created_at, updated_at
- summary (optional)
- tags (optional)
- pinned_context (optional: “das ist wichtig, immer wieder reinfüttern”)
- deleted (soft delete, optional)

Steuer-Operationen (UI & API)

- Anzeigen: GET /api/memory/sessions?agent=jules
- Details: GET /api/memory/sessions/{id}?agent=jules
- Editieren (z.B. Tags/Summary): PATCH /api/memory/sessions/{id}
- Löschen: DELETE /api/memory/sessions/{id} (soft/hard)
- Verdichten: POST /api/memory/sessions/{id}/summarize (optional – kann später)

Wichtig: Memory-Edit darf nie still heimlich die Repo-Wahrheit verändern. Memory ist Metadaten-Schicht.

⸻

## Multi-Agent – sinnvoll, aber sauber eingefädelt

Warum sinnvoll

- Du wirst Agenten wechseln/ergänzen (Copilot Agent, Codex, OpenAI, lokaler Agent, irgendwas mit MCP).
- Das Panel ist ohnehin: UI + Jobsteuerung + Artefakt-Archiv + Git-Gates. Das ist agent-unabhängig.

Aber: Kein Framework-Bau jetzt

Statt „plugin system“ sofort: kleine Agent-Abstraktion, z.B.:

- agents/base.py mit Interface:
  - list_sessions(repo_path)
  - new_session(repo_path, prompt)
  - pull_patch(repo_path, session_id)  → liefert diff-text
  - optional status(session_id)

Und dann:

- agents/jules.py implementiert das via CLI (jules remote list --session, jules new, jules remote pull --session ...)

Der Rest (Git apply, branch guard, memory store) bleibt identisch (Agent-unabhängig).
