import json
import pytest
from pathlib import Path
from panel.ops import run_wgx_audit_git, run_wgx_routine_preview, run_wgx_routine_apply, create_token, AuditGit, get_latest_audit_artifact, extract_json_from_stdout
from panel.runner import CmdResult
from fastapi import HTTPException
from fastapi.testclient import TestClient
from panel.app import app

# Mock JSON responses matching WGX output
MOCK_AUDIT_JSON = json.dumps({
    "kind": "audit.git",
    "schema_version": "v1",
    "ts": "2023-10-27T10:00:00Z",
    "repo": "mock_repo",
    "cwd": "/tmp/mock_repo",
    "status": "ok",
    "facts": {
        "head_sha": "abcdef123456",
        "head_ref": "refs/heads/feature",
        "is_detached_head": False,
        "local_branch": "feature",
        "upstream": {"name": "origin/main", "exists_locally": True},
        "remotes": ["origin"],
        "remote_default_branch": "main",
        "remote_refs": {"origin_main": True, "origin_head": True, "origin_upstream": True},
        "working_tree": {"is_clean": True, "staged": 0, "unstaged": 0, "untracked": 0},
        "ahead_behind": {"ahead": 0, "behind": 0}
    },
    "checks": [
        {"id": "git.repo.present", "status": "ok", "message": "Repo present"}
    ],
    "uncertainty": {
        "level": 0.0,
        "causes": [],
        "meta": "productive"
    },
    "suggested_routines": [],
    "correlation_id": "test-correlation-id"
})

MOCK_PREVIEW_JSON = json.dumps({
    "kind": "routine.preview",
    "id": "git.repair.remote-head",
    "mode": "dry-run",
    "mutating": True,
    "risk": "low",
    "steps": [{"cmd": "git remote set-head origin --auto", "why": "Restore origin/HEAD"}],
    "expected_effect": "origin/HEAD restored"
})

MOCK_RESULT_JSON = json.dumps({
    "kind": "routine.result",
    "id": "git.repair.remote-head",
    "mode": "apply",
    "mutating": True,
    "ok": True,
    "state_hash": {"before": "aaa", "after": "bbb"},
    "stdout": "Fixed."
})

@pytest.fixture
def mock_run_wgx(monkeypatch):
    def _run(cmd, cwd, timeout=60, **kwargs):
        # Robust list-based matching
        # Check for audit command
        if cmd[:3] == ["wgx", "audit", "git"] and "--repo" in cmd:
             repo_idx = cmd.index("--repo") + 1
             repo = cmd[repo_idx]

             if repo == "mock_repo":
                 return CmdResult(0, MOCK_AUDIT_JSON, "", cmd)
             elif repo == "fail_repo":
                 return CmdResult(1, MOCK_AUDIT_JSON.replace('"status": "ok"', '"status": "error"'), "some stderr", cmd)
             elif repo == "metarepo": # For API tests using metarepo
                 return CmdResult(0, MOCK_AUDIT_JSON, "", cmd)

        # Check for routine preview
        if "routine" in cmd and "preview" in cmd:
            if "git.repair.remote-head" in cmd:
                return CmdResult(0, MOCK_PREVIEW_JSON, "", cmd)

        # Check for routine apply
        if "routine" in cmd and "apply" in cmd:
            if "git.repair.remote-head" in cmd:
                return CmdResult(0, MOCK_RESULT_JSON, "", cmd)
            if "fail.test" in cmd:
                 return CmdResult(1, MOCK_RESULT_JSON, "some stderr", cmd)

        return CmdResult(1, "", f"Unknown command: {cmd}", cmd)

    monkeypatch.setattr("panel.ops.run", _run)

def test_run_wgx_audit_git(mock_run_wgx):
    # Tests stdout-json mode (default mock runner behavior above covers this for 'mock_repo' if we allow stdout_json parsing)
    # But run_wgx_audit_git defaults stdout_json=False.
    # So we need to ensure either we pass stdout_json=True, or fix mock to return a file path.
    # Let's test explicit stdout_json=True here.
    repo_path = Path("/tmp/mock_repo")
    result = run_wgx_audit_git("mock_repo", repo_path, "corr-1", stdout_json=True)

    assert isinstance(result, AuditGit)
    assert result.repo == "mock_repo"
    assert result.status == "ok"
    # assert result.correlation_id == "test-correlation-id" # Taken from JSON
    assert result.correlation_id == "corr-1" # Override check

def test_run_wgx_audit_git_nonzero_exit_with_json(mock_run_wgx):
    repo_path = Path("/tmp/fail_repo")
    result = run_wgx_audit_git("fail_repo", repo_path, "corr-2", stdout_json=True)

    assert isinstance(result, AuditGit)
    assert result.status == "error"

def test_run_wgx_audit_git_stdout_flag(monkeypatch):
    repo_path = Path("/tmp/mock_repo")
    called_with_flag = False

    def _run(cmd, cwd, timeout=60, **kwargs):
        nonlocal called_with_flag
        if "--stdout-json" in cmd:
            called_with_flag = True
        return CmdResult(0, MOCK_AUDIT_JSON, "", cmd)

    monkeypatch.setattr("panel.ops.run", _run)
    # This should succeed by parsing the mocked MOCK_AUDIT_JSON as stdout
    result = run_wgx_audit_git("mock_repo", repo_path, "corr-1", stdout_json=True)
    assert called_with_flag
    assert result.status == "ok"

def test_token_mismatch_deletes_token(mock_run_wgx):
    """Test that token validation mismatch deletes the token to prevent brute-forcing."""
    repo_path = Path("/tmp/mock_repo")
    repo_key = "mock_repo"
    routine_id = "git.repair.remote-head"

    # Generate token
    _, token, p_hash = run_wgx_routine_preview(repo_key, repo_path, routine_id)

    # Try to use token with wrong repo
    with pytest.raises(HTTPException) as excinfo:
        run_wgx_routine_apply("wrong_repo", repo_path, routine_id, token, p_hash)
    assert excinfo.value.status_code == 403

    # Try again with CORRECT repo - should fail because token was deleted
    with pytest.raises(HTTPException) as excinfo:
        run_wgx_routine_apply(repo_key, repo_path, routine_id, token, p_hash)
    assert excinfo.value.status_code == 403

def test_run_wgx_routine_flow(mock_run_wgx):
    repo_path = Path("/tmp/mock_repo")
    repo_key = "mock_repo"
    routine_id = "git.repair.remote-head"

    # 1. Preview
    preview, token, p_hash = run_wgx_routine_preview(repo_key, repo_path, routine_id)
    assert preview["kind"] == "routine.preview"
    assert token is not None
    assert p_hash is not None

    # 2. Apply with valid token
    result = run_wgx_routine_apply(repo_key, repo_path, routine_id, token, p_hash)
    assert result["kind"] == "routine.result"
    assert result["ok"] is True

def test_run_wgx_routine_apply_invalid_token(mock_run_wgx):
    repo_path = Path("/tmp/mock_repo")
    repo_key = "mock_repo"
    routine_id = "git.repair.remote-head"
    dummy_hash = "0" * 64

    with pytest.raises(HTTPException) as excinfo:
        run_wgx_routine_apply(repo_key, repo_path, routine_id, "invalid-token", dummy_hash)

    assert excinfo.value.status_code == 403

def test_run_wgx_routine_apply_token_reuse_fails(mock_run_wgx):
    repo_path = Path("/tmp/mock_repo")
    repo_key = "mock_repo"
    routine_id = "git.repair.remote-head"

    preview, token, p_hash = run_wgx_routine_preview(repo_key, repo_path, routine_id)

    # Use once -> OK
    run_wgx_routine_apply(repo_key, repo_path, routine_id, token, p_hash)

    # Use again -> Fail
    with pytest.raises(HTTPException) as excinfo:
        run_wgx_routine_apply(repo_key, repo_path, routine_id, token, p_hash)

    assert excinfo.value.status_code == 403

def test_run_wgx_routine_apply_handles_nonzero_exit_with_json(mock_run_wgx):
    """
    Test that a non-zero exit code is tolerated if valid JSON with 'ok' field is returned.
    """
    repo_path = Path("/tmp/mock_repo")
    repo_key = "mock_repo"
    routine_id = "fail.test"

    # Manually create valid token for test
    token = create_token({"repo": repo_key, "routine_id": routine_id, "preview_hash": "abc"})

    # fail.test mock returns MOCK_RESULT_JSON which has "ok": True
    result = run_wgx_routine_apply(repo_key, repo_path, routine_id, token, "abc")
    assert result["kind"] == "routine.result"
    assert result["ok"] is True
    assert result.get("_exit_code") == 1

def test_run_wgx_routine_apply_nonzero_exit_without_ok(monkeypatch, mock_run_wgx):
    """
    Test that non-zero exit code raises Error if JSON lacks 'ok' field.
    """
    repo_path = Path("/tmp/mock_repo")
    repo_key = "mock_repo"
    routine_id = "crash.test"

    bad_json = json.dumps({"kind": "error", "message": "Crash"}) # No 'ok'

    def _run(cmd, cwd, timeout=60, **kwargs):
        if "crash.test" in cmd:
            return CmdResult(1, bad_json, "stderr logs", cmd)
        return CmdResult(0, "{}", "", cmd)

    monkeypatch.setattr("panel.ops.run", _run)
    token = create_token({"repo": repo_key, "routine_id": routine_id, "preview_hash": "abc"})

    with pytest.raises(RuntimeError) as excinfo:
        run_wgx_routine_apply(repo_key, repo_path, routine_id, token, "abc")

    assert "lacks 'ok' field" in str(excinfo.value)

def test_get_latest_audit_artifact(tmp_path):
    # Setup .wgx/out structure
    out_dir = tmp_path / ".wgx" / "out"
    out_dir.mkdir(parents=True)

    # Old file
    old = out_dir / "audit.git.v1.old.json"
    old.write_text(MOCK_AUDIT_JSON)
    # Force older mtime
    import os
    os.utime(old, (100, 100))

    # New file
    new = out_dir / "audit.git.v1.new.json"
    # Robustly modify JSON instead of string replace
    data = json.loads(MOCK_AUDIT_JSON)
    data["status"] = "warn"
    new.write_text(json.dumps(data))

    result = get_latest_audit_artifact(tmp_path)
    assert result is not None
    assert result.status == "warn" # Should pick the new one

def test_run_wgx_audit_git_file_mode(tmp_path, monkeypatch):
    """Test that file artifact mode works by reading the file returned in stdout."""
    repo_path = tmp_path

    # Create the artifact file that wgx would create
    out_dir = repo_path / ".wgx" / "out"
    out_dir.mkdir(parents=True)
    artifact_path = out_dir / "audit.git.v1.test.json"
    artifact_path.write_text(MOCK_AUDIT_JSON)

    # Mock run to return the path relative to repo
    # Note: the real code now resolves this path absolutely.
    # If the mock returns a relative path, extract_path_from_stdout will resolve it against repo_path.
    def _run(cmd, cwd, timeout=60, **kwargs):
        # Must return the path relative to cwd (repo_path)
        return CmdResult(0, ".wgx/out/audit.git.v1.test.json", "", cmd)

    monkeypatch.setattr("panel.ops.run", _run)

    result = run_wgx_audit_git("mock_repo", repo_path, "corr-test", stdout_json=False)
    assert isinstance(result, AuditGit)
    assert result.status == "ok"

def test_run_wgx_audit_git_stdout_noise_info(monkeypatch):
    """Test robust JSON extraction when stdout contains [INFO] tags which are brackets."""
    repo_path = Path("/tmp/mock_repo")

    noisy_output = f"[INFO] Starting audit\n{MOCK_AUDIT_JSON}\n[DEBUG] Cleanup done"

    def _run(cmd, cwd, timeout=60, **kwargs):
        if "--stdout-json" in cmd:
             return CmdResult(0, noisy_output, "", cmd)
        return CmdResult(1, "", "fail", cmd)

    monkeypatch.setattr("panel.ops.run", _run)

    result = run_wgx_audit_git("mock_repo", repo_path, "corr-test", stdout_json=True)
    assert isinstance(result, AuditGit)
    assert result.status == "ok"
    assert result.correlation_id == "corr-test"

def test_extract_json_from_stdout_nested_brackets():
    """Test parsing JSON objects that contain brackets/braces in strings."""
    complex_json = json.dumps({"key": "value with { braces }", "list": [1, 2, 3]})
    noisy = f"Some text {complex_json} trailing text"
    result = extract_json_from_stdout(noisy)
    assert result is not None
    assert result["key"] == "value with { braces }"
    assert result["list"] == [1, 2, 3]

def test_run_wgx_routine_stdout_fallback_file_path(tmp_path, monkeypatch):
    """
    Test that if wgx routine outputs a file path instead of JSON (because no --stdout-json flag),
    the code correctly reads that file.
    """
    repo_path = tmp_path
    repo_key = "mock_repo"
    routine_id = "git.repair.remote-head"

    # Setup artifact file
    out_dir = repo_path / ".wgx" / "out"
    out_dir.mkdir(parents=True)
    artifact_path = out_dir / "routine.preview.json"
    artifact_path.write_text(MOCK_PREVIEW_JSON)

    # Mock run to return path (relative)
    def _run(cmd, cwd, timeout=60, **kwargs):
        return CmdResult(0, ".wgx/out/routine.preview.json", "", cmd)

    monkeypatch.setattr("panel.ops.run", _run)

    preview, token, p_hash = run_wgx_routine_preview(repo_key, repo_path, routine_id)
    assert preview["kind"] == "routine.preview"

# API & Sync Fallback Tests

@pytest.fixture
def mock_get_repo(monkeypatch, tmp_path):
    """Patches get_repo to always return a Repo pointing to tmp_path for CI stability."""
    from panel.repos import Repo
    def _get_repo(key):
        return Repo(key=key, path=tmp_path, display=f"mock/{key}")

    monkeypatch.setattr("panel.app.get_repo", _get_repo)
    return tmp_path

def test_api_audit_git_sync_fallback(monkeypatch, mock_get_repo):
    """Test that sync audit endpoint falls back to file mode if stdout fails."""
    client = TestClient(app)

    # Setup artifact file for fallback
    out_dir = mock_get_repo / ".wgx" / "out"
    out_dir.mkdir(parents=True)
    artifact_path = out_dir / "audit.git.v1.json"
    artifact_path.write_text(MOCK_AUDIT_JSON)

    call_count = 0

    def _run(cmd, cwd, timeout=60, **kwargs):
        nonlocal call_count
        call_count += 1

        # 1. Stdout attempt -> Fail
        if "--stdout-json" in cmd:
            return CmdResult(1, "invalid json", "error", cmd)

        # 2. File mode attempt -> Succeed
        # Return path to the file we created
        return CmdResult(0, ".wgx/out/audit.git.v1.json", "", cmd)

    monkeypatch.setattr("panel.ops.run", _run)

    response = client.get("/api/audit/git/sync?repo=metarepo")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert call_count == 2 # Should have tried twice

def test_routines_safety_gate(monkeypatch, mock_get_repo):
    """Test that routine endpoints are disabled by default."""
    client = TestClient(app)

    # Default: disabled -> 403
    monkeypatch.delenv("ACS_ENABLE_ROUTINES", raising=False)

    res = client.post("/api/routine/preview", json={"repo": "metarepo", "id": "test"})
    assert res.status_code == 403, res.text
    assert "disabled" in res.json()["detail"]

    # Payload must be valid to reach 403
    res = client.post("/api/routine/apply", json={"repo": "metarepo", "id": "test", "confirm_token": "x", "preview_hash": "dummy"})
    assert res.status_code == 403, res.text

def test_routines_safety_gate_enabled_with_mock_run(monkeypatch, mock_run_wgx, mock_get_repo):
    """Test that routine endpoints work when enabled."""
    client = TestClient(app)

    monkeypatch.setenv("ACS_ENABLE_ROUTINES", "true")

    # Preview
    res = client.post("/api/routine/preview", json={"repo": "metarepo", "id": "git.repair.remote-head"})
    assert res.status_code == 200
    assert "confirm_token" in res.json()

def test_api_routine_apply_fails_conflict(monkeypatch, mock_run_wgx, mock_get_repo):
    """Test that api_routine_apply returns 409 if the routine reports ok=False."""
    client = TestClient(app)
    monkeypatch.setenv("ACS_ENABLE_ROUTINES", "true")

    # We need a valid token first
    _, token, p_hash = run_wgx_routine_preview("metarepo", mock_get_repo, "git.repair.remote-head")

    # Mock result with ok=False
    mock_fail_json = json.dumps({
        "kind": "routine.result",
        "id": "fail.test",
        "mode": "apply",
        "mutating": True,
        "ok": False,
        "stdout": "Oops."
    })

    def _run(cmd, cwd, timeout=60, **kwargs):
        if "fail.test" in cmd:
            return CmdResult(0, mock_fail_json, "", cmd)
        return CmdResult(0, MOCK_RESULT_JSON, "", cmd)

    monkeypatch.setattr("panel.ops.run", _run)

    # Register token manually with hash
    real_token = create_token({"repo": "metarepo", "routine_id": "fail.test", "preview_hash": "abc"})

    res = client.post("/api/routine/apply", json={"repo": "metarepo", "id": "fail.test", "confirm_token": real_token, "preview_hash": "abc"})
    assert res.status_code == 409
    assert res.json()["ok"] is False

def test_api_routine_apply_fails_missing_ok_field(monkeypatch, mock_run_wgx, mock_get_repo):
    """Test that api_routine_apply returns 500 if the routine output lacks 'ok' field."""
    client = TestClient(app)
    monkeypatch.setenv("ACS_ENABLE_ROUTINES", "true")

    # Mock result without 'ok' field (invalid result structure)
    mock_invalid_json = json.dumps({
        "kind": "routine.result",
        "id": "invalid.test",
        "mode": "apply",
        "mutating": True,
        # "ok": is missing
        "stdout": "Weird result."
    })

    def _run(cmd, cwd, timeout=60, **kwargs):
        if "invalid.test" in cmd:
            # Exit code 0 so ops layer passes it through, but content is invalid for API
            return CmdResult(0, mock_invalid_json, "", cmd)
        return CmdResult(0, MOCK_RESULT_JSON, "", cmd)

    monkeypatch.setattr("panel.ops.run", _run)

    real_token = create_token({"repo": "metarepo", "routine_id": "invalid.test", "preview_hash": "abc"})

    res = client.post("/api/routine/apply", json={"repo": "metarepo", "id": "invalid.test", "confirm_token": real_token, "preview_hash": "abc"})
    assert res.status_code == 500
    assert "missing 'ok' field" in res.json()["detail"]

def test_routines_safety_gate_secret(monkeypatch, mock_run_wgx, mock_get_repo):
    """Test that X-ACS-Actor-Token is required if secret is set."""
    client = TestClient(app)
    monkeypatch.setenv("ACS_ENABLE_ROUTINES", "true")
    monkeypatch.setenv("ACS_ROUTINES_SHARED_SECRET", "supersecret")

    # 1. Missing header -> 403
    res = client.post("/api/routine/preview", json={"repo": "metarepo", "id": "git.repair.remote-head"})
    assert res.status_code == 403
    assert "X-ACS-Actor-Token" in res.json()["detail"]

    # 2. Wrong header -> 403
    res = client.post("/api/routine/preview", json={"repo": "metarepo", "id": "git.repair.remote-head"}, headers={"X-ACS-Actor-Token": "wrong"})
    assert res.status_code == 403

    # 3. Correct header -> 200
    res = client.post("/api/routine/preview", json={"repo": "metarepo", "id": "git.repair.remote-head"}, headers={"X-ACS-Actor-Token": "supersecret"})
    assert res.status_code == 200
    assert "confirm_token" in res.json()

def test_api_routine_validation_invalid_id(monkeypatch, mock_get_repo):
    """Test that invalid routine IDs are rejected."""
    client = TestClient(app)
    monkeypatch.setenv("ACS_ENABLE_ROUTINES", "true")

    # Invalid ID (spaces) -> 422
    res = client.post("/api/routine/preview", json={"repo": "metarepo", "id": "invalid id with spaces"})
    assert res.status_code == 422

    # Invalid ID (shell chars) -> 422
    res = client.post("/api/routine/preview", json={"repo": "metarepo", "id": "id; rm -rf /"})
    assert res.status_code == 422
