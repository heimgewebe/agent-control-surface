# Publish (Push + PR)

Dieses Dokument zeigt ein paar **Smoke-Curls** für die neue Publish-API.

Logging ist standardmäßig deaktiviert. Aktivieren via `ACS_ACTION_LOG=1` (oder ein eigener Pfad mit `ACS_ACTION_LOG=/path/log.jsonl`).

## Beispiel: Erfolg

```bash
curl -sS -X POST http://127.0.0.1:8099/api/git/publish \
  -H 'Content-Type: application/json' \
  -d '{
    "repo": "metarepo",
    "branch": "acs/metarepo-20250101-1200-abcd12",
    "commit_message": "acs: publish metarepo",
    "pr_title": "Update metarepo",
    "pr_body": "Kurzbeschreibung der Änderungen",
    "base": "main",
    "draft": true,
    "include_diffstat": true
  }'
```

Response (Job-Start):

```json
{
  "job_id": "8d2f0f02-8a7a-4c44-a37a-0b111e0c8e6c",
  "correlation_id": "7b4a4f0a-8a7c-4f38-9c08-0f2b0a1b7b9f"
}
```

Job abfragen:

```bash
curl -sS http://127.0.0.1:8099/api/jobs/8d2f0f02-8a7a-4c44-a37a-0b111e0c8e6c
```

Erfolgsantwort (gekürzt):

```json
{
  "status": "done",
  "results": [
    {
      "ok": true,
      "action": "git.push",
      "repo": "metarepo",
      "branch": "acs/metarepo-20250101-1200-abcd12",
      "head": "b23c0d2...",
      "changed": null,
      "files": null,
      "pr_url": null,
      "stdout": "...",
      "stderr": "",
      "code": 0,
      "error_kind": null,
      "message": "Push completed.",
      "ts": "2025-01-01T12:01:22.000000+00:00",
      "duration_ms": 912,
      "correlation_id": "7b4a4f0a-8a7c-4f38-9c08-0f2b0a1b7b9f"
    },
    {
      "ok": true,
      "action": "git.publish",
      "repo": "metarepo",
      "branch": "acs/metarepo-20250101-1200-abcd12",
      "head": "b23c0d2...",
      "changed": null,
      "files": null,
      "pr_url": "https://github.com/heimgewebe/metarepo/pull/123",
      "stdout": "",
      "stderr": "",
      "code": 0,
      "error_kind": null,
      "message": "Publish completed.",
      "ts": "2025-01-01T12:01:40.000000+00:00",
      "duration_ms": 18042,
      "correlation_id": "7b4a4f0a-8a7c-4f38-9c08-0f2b0a1b7b9f"
    }
  ],
  "log_tail": "{...}"
}
```

## Beispiel: Fehler (branch_guard)

```bash
curl -sS -X POST http://127.0.0.1:8099/api/git/publish \
  -H 'Content-Type: application/json' \
  -d '{"repo":"metarepo"}'
```

```json
{
  "status": "error",
  "results": [
    {
      "ok": false,
      "action": "git.branch",
      "repo": "metarepo",
      "branch": "main",
      "head": "b23c0d2...",
      "changed": null,
      "files": null,
      "pr_url": null,
      "stdout": "",
      "stderr": "",
      "code": 1,
      "error_kind": "branch_guard",
      "message": "Refusing to operate on main/master. Create a branch first.",
      "ts": "2025-01-01T12:05:00.000000+00:00",
      "duration_ms": null,
      "correlation_id": "2b42bca9-1c0a-4f11-b0c1-8aa4d3e1fd0a"
    }
  ],
  "log_tail": "{...}"
}
```
