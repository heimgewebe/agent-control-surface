from pathlib import Path
from unittest.mock import MagicMock, patch

from panel.app import (
    PublishOptions,
    classify_git_ref_error,
    execute_publish,
    git_remote_repair_stage_a,
    git_remote_repair_stage_b,
    git_remote_repair_stage_c,
)
from panel.runner import CmdResult


def test_classify_git_ref_error_patterns() -> None:
    lock = "fatal: cannot lock ref 'refs/remotes/origin/HEAD': unable to resolve reference"
    assert classify_git_ref_error(lock) == {
        "error_kind": "ref_lock",
        "hint": "Unable to lock local ref; remote tracking refs may be inconsistent.",
        "affected_ref": "refs/remotes/origin/HEAD",
    }
    resolve = "unable to resolve reference 'refs/remotes/origin/HEAD'"
    assert classify_git_ref_error(resolve) == {
        "error_kind": "resolve_ref_failed",
        "hint": "Unable to resolve local ref; remote tracking refs may be inconsistent.",
        "affected_ref": "refs/remotes/origin/HEAD",
    }
    dangling = "refs/remotes/origin/HEAD has become dangling"
    assert classify_git_ref_error(dangling) == {
        "error_kind": "dangling_ref",
        "hint": "Local ref has become dangling; remote tracking refs may be inconsistent.",
        "affected_ref": "refs/remotes/origin/HEAD",
    }
    packed = "fatal: packed refs are corrupt"
    assert classify_git_ref_error(packed) == {
        "error_kind": "ref_repair_failed",
        "hint": "Packed refs appear corrupt; repacking refs may be required.",
        "affected_ref": None,
    }


def test_repair_stage_a_runs_prune_and_fetch() -> None:
    target = MagicMock(key="metarepo", path=Path("/tmp/mock"))
    with patch("panel.app.run") as mock_run:
        mock_run.side_effect = [
            CmdResult(code=0, stdout="pruned", stderr="", cmd=[]),
            CmdResult(code=0, stdout="fetched", stderr="", cmd=[]),
        ]
        result = git_remote_repair_stage_a(target, "corr-1")

    assert result.ok
    assert mock_run.call_args_list[0].args[0] == ["git", "remote", "prune", "origin"]
    assert mock_run.call_args_list[1].args[0] == ["git", "fetch", "--prune", "origin"]


def test_repair_stage_b_allows_missing_refs() -> None:
    target = MagicMock(key="metarepo", path=Path("/tmp/mock"))
    with patch("panel.app.run") as mock_run:
        mock_run.side_effect = [
            CmdResult(code=1, stdout="", stderr="missing", cmd=[]),
            CmdResult(code=1, stdout="", stderr="missing", cmd=[]),
            CmdResult(code=0, stdout="fetched", stderr="", cmd=[]),
        ]
        result = git_remote_repair_stage_b(target, "corr-1", "main", True)

    assert result.ok
    assert mock_run.call_args_list[0].args[0] == [
        "git",
        "update-ref",
        "-d",
        "refs/remotes/origin/HEAD",
    ]
    assert mock_run.call_args_list[1].args[0] == [
        "git",
        "update-ref",
        "-d",
        "refs/remotes/origin/main",
    ]
    assert mock_run.call_args_list[2].args[0] == ["git", "fetch", "--prune", "origin"]


def test_repair_stage_b_rejects_invalid_base_branch() -> None:
    target = MagicMock(key="metarepo", path=Path("/tmp/mock"))
    with patch("panel.app.run") as mock_run:
        result = git_remote_repair_stage_b(target, "corr-1", "invalid branch", True)

    assert not result.ok
    assert result.error_kind == "invalid_input"
    assert result.action == "git.repair.stage_b"
    assert result.duration_ms is not None
    mock_run.assert_not_called()


def test_repair_stage_c_runs_pack_refs_and_fetch() -> None:
    target = MagicMock(key="metarepo", path=Path("/tmp/mock"))
    with patch("panel.app.run") as mock_run:
        mock_run.side_effect = [
            CmdResult(code=0, stdout="packed", stderr="", cmd=[]),
            CmdResult(code=0, stdout="fetched", stderr="", cmd=[]),
        ]
        result = git_remote_repair_stage_c(target, "corr-1")

    assert result.ok
    assert mock_run.call_args_list[0].args[0] == ["git", "pack-refs", "--all", "--prune"]
    assert mock_run.call_args_list[1].args[0] == ["git", "fetch", "--prune", "origin"]


def test_publish_fetch_ref_lock_sets_error_kind() -> None:
    def run_side_effect(cmd, cwd, timeout=60, env=None, input_text=None):
        if cmd[:3] == ["git", "ls-remote", "--heads"]:
            return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))
        if cmd[:2] == ["gh", "--version"]:
            return CmdResult(code=0, stdout="gh version 2.0.0", stderr="", cmd=list(cmd))
        if cmd[:3] == ["gh", "auth", "status"]:
            return CmdResult(code=0, stdout="logged in", stderr="", cmd=list(cmd))
        if cmd[:3] == ["git", "remote", "get-url"]:
            return CmdResult(code=0, stdout="git@github.com:org/repo.git\n", stderr="", cmd=list(cmd))
        if cmd[:2] == ["git", "push"]:
            return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))
        if cmd[:4] == ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name"]:
            return CmdResult(code=0, stdout="origin/feature\n", stderr="", cmd=list(cmd))
        if cmd[:2] == ["git", "fetch"]:
            return CmdResult(
                code=1,
                stdout="",
                stderr="fatal: cannot lock ref 'refs/remotes/origin/HEAD': "
                "unable to resolve reference",
                cmd=list(cmd),
            )
        return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))

    with patch("panel.app.get_repo") as mock_get_repo, \
         patch("panel.app.is_valid_branch_name", return_value=True), \
         patch("panel.app.get_git_state", return_value=("feature", "abc123")), \
         patch("panel.app.git_status_porcelain", return_value=[]), \
         patch("panel.app.run", side_effect=run_side_effect), \
         patch("panel.app.record_job_result") as mock_record:
        mock_get_repo.return_value = MagicMock(key="metarepo", path="/tmp/mock")

        req = PublishOptions(branch="feature")
        execute_publish("job-1", "corr-1", "metarepo", req)

        results = [call.args[1] for call in mock_record.call_args_list]
        fetch_result = next(res for res in results if res.action == "git.fetch")
        assert fetch_result.error_kind == "ref_lock"
