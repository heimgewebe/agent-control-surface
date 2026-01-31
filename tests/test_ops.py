import json
import pytest
from pathlib import Path
from panel.ops import run_wgx_audit_git, run_wgx_routine_preview, run_wgx_routine_apply, create_token, AuditGit
from panel.runner import CmdResult
from fastapi import HTTPException

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
        cmd_str = " ".join(cmd)
        # Updated match pattern for new CLI args
        if "wgx audit git --repo mock_repo --correlation-id corr-1" in cmd_str:
            return CmdResult(0, MOCK_AUDIT_JSON, "", cmd)
        if "wgx routine git.repair.remote-head preview" in cmd_str:
            return CmdResult(0, MOCK_PREVIEW_JSON, "", cmd)
        if "wgx routine git.repair.remote-head apply" in cmd_str:
            return CmdResult(0, MOCK_RESULT_JSON, "", cmd)

        # Test case: Non-zero exit but valid JSON output (e.g. routine failure reported as structured result)
        if "wgx routine fail.test apply" in cmd_str:
             return CmdResult(1, MOCK_RESULT_JSON, "some stderr", cmd)

        return CmdResult(1, "", f"Unknown command: {cmd_str}", cmd)

    monkeypatch.setattr("panel.ops.run", _run)

def test_run_wgx_audit_git(mock_run_wgx):
    repo_path = Path("/tmp/mock_repo")
    result = run_wgx_audit_git("mock_repo", repo_path, "corr-1")

    assert isinstance(result, AuditGit)
    assert result.repo == "mock_repo"
    assert result.status == "ok"
    assert result.correlation_id == "test-correlation-id"

def test_run_wgx_routine_flow(mock_run_wgx):
    repo_path = Path("/tmp/mock_repo")
    repo_key = "mock_repo"
    routine_id = "git.repair.remote-head"

    # 1. Preview
    preview, token = run_wgx_routine_preview(repo_key, repo_path, routine_id)
    assert preview["kind"] == "routine.preview"
    assert token is not None

    # 2. Apply with valid token
    result = run_wgx_routine_apply(repo_key, repo_path, routine_id, token)
    assert result["kind"] == "routine.result"
    assert result["ok"] is True

def test_run_wgx_routine_apply_invalid_token(mock_run_wgx):
    repo_path = Path("/tmp/mock_repo")
    repo_key = "mock_repo"
    routine_id = "git.repair.remote-head"

    with pytest.raises(HTTPException) as excinfo:
        run_wgx_routine_apply(repo_key, repo_path, routine_id, "invalid-token")

    assert excinfo.value.status_code == 403

def test_run_wgx_routine_apply_token_reuse_fails(mock_run_wgx):
    repo_path = Path("/tmp/mock_repo")
    repo_key = "mock_repo"
    routine_id = "git.repair.remote-head"

    preview, token = run_wgx_routine_preview(repo_key, repo_path, routine_id)

    # Use once -> OK
    run_wgx_routine_apply(repo_key, repo_path, routine_id, token)

    # Use again -> Fail
    with pytest.raises(HTTPException) as excinfo:
        run_wgx_routine_apply(repo_key, repo_path, routine_id, token)

    assert excinfo.value.status_code == 403

def test_run_wgx_routine_apply_handles_nonzero_exit_with_json(mock_run_wgx):
    repo_path = Path("/tmp/mock_repo")
    repo_key = "mock_repo"
    routine_id = "fail.test"

    preview, token = run_wgx_routine_preview(repo_key, repo_path, "git.repair.remote-head")
    # Use a token for a known ID to bypass token check, then call the failing routine
    # Wait, token is bound to ID. Need to create a token for "fail.test"
    # But run_wgx_routine_preview calls run() which needs to be mocked for fail.test too if we want a valid token?
    # Actually create_token is internal.

    # Manually create valid token for test
    token = create_token({"repo": repo_key, "routine_id": routine_id})

    result = run_wgx_routine_apply(repo_key, repo_path, routine_id, token)
    assert result["kind"] == "routine.result"
    assert result["ok"] is True # Our mock JSON says True, even if exit code was 1. Logic should just parse JSON.
