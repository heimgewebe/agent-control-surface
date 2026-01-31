import pytest
from pathlib import Path
from panel.ops import run_git_audit, AuditGit
from panel.runner import CmdResult

# Mock data
MOCK_HEAD_SHA = "abcdef123456"
MOCK_HEAD_REF = "refs/heads/feature-branch"
MOCK_REMOTES = "origin\nupstream"
MOCK_ORIGIN_HEAD = "refs/remotes/origin/main"
MOCK_UPSTREAM = "origin/main"

@pytest.fixture
def mock_run(monkeypatch):
    def _run(cmd, cwd, timeout=60, **kwargs):
        cmd_str = " ".join(cmd)
        if "rev-parse HEAD" in cmd_str:
            return CmdResult(0, MOCK_HEAD_SHA, "", cmd)
        if "symbolic-ref -q HEAD" in cmd_str:
            return CmdResult(0, MOCK_HEAD_REF, "", cmd)
        if "remote" in cmd_str and "get-url" not in cmd_str:
            return CmdResult(0, MOCK_REMOTES, "", cmd)
        if "fetch" in cmd_str:
            return CmdResult(0, "", "", cmd)
        if "show-ref --verify --quiet refs/remotes/origin/HEAD" in cmd_str:
            return CmdResult(0, "", "", cmd)
        if "show-ref --verify --quiet refs/remotes/origin/main" in cmd_str:
            return CmdResult(0, "", "", cmd)
        if "symbolic-ref refs/remotes/origin/HEAD" in cmd_str:
            return CmdResult(0, MOCK_ORIGIN_HEAD, "", cmd)
        if "rev-parse --abbrev-ref --symbolic-full-name @{u}" in cmd_str:
            return CmdResult(0, MOCK_UPSTREAM, "", cmd)
        if "rev-list --left-right --count" in cmd_str:
            return CmdResult(0, "1 2", "", cmd) # 1 behind, 2 ahead
        if "diff --cached --name-only" in cmd_str:
            return CmdResult(0, "", "", cmd)
        if "diff --name-only" in cmd_str:
            return CmdResult(0, "", "", cmd)
        if "ls-files --others" in cmd_str:
            return CmdResult(0, "", "", cmd)

        return CmdResult(1, "", "unknown command", cmd)

    monkeypatch.setattr("panel.ops.run", _run)

def test_run_git_audit_structure(mock_run):
    repo_path = Path("/tmp/mock_repo")
    result = run_git_audit("mock_repo", repo_path, "correlation-123")

    assert isinstance(result, AuditGit)
    assert result.repo == "mock_repo"
    assert result.facts.head_sha == MOCK_HEAD_SHA
    assert result.facts.local_branch == "feature-branch"
    assert result.facts.upstream["name"] == MOCK_UPSTREAM
    assert result.facts.ahead_behind["ahead"] == 2
    assert result.facts.ahead_behind["behind"] == 1
    assert result.status == "ok"
    assert result.facts.remote_refs["origin_head"] is True

def test_run_git_audit_missing_origin(monkeypatch):
    def _run(cmd, cwd, timeout=60, **kwargs):
        # Default fail for everything to simulate empty/broken repo or missing remote
        return CmdResult(1, "", "error", cmd)
    monkeypatch.setattr("panel.ops.run", _run)

    repo_path = Path("/tmp/mock_repo")
    result = run_git_audit("mock_repo", repo_path, "correlation-123")

    assert result.status == "error"
    # Should check if remote origin check failed
    assert any(c.id == "git.remote.origin.present" and c.status == "error" for c in result.checks)
