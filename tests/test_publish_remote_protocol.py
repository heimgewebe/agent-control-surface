from unittest.mock import MagicMock, patch

from panel.app import get_remote_protocol, https_remote_to_ssh, execute_publish, PublishOptions
from panel.runner import CmdResult


def has_pr_create_head(call_args_list, expected_head, expected_base="main"):
    for call in call_args_list:
        cmd = call.args[0]
        if cmd[:3] != ["gh", "pr", "create"]:
            continue
        if "--base" not in cmd or "--head" not in cmd:
            continue
        if cmd[cmd.index("--base") + 1] != expected_base:
            continue
        if cmd[cmd.index("--head") + 1] != expected_head:
            continue
        return True
    return False


def has_rev_list_count(call_args_list, expected_range):
    for call in call_args_list:
        cmd = call.args[0]
        if cmd[:3] != ["git", "rev-list", "--count"]:
            continue
        if len(cmd) > 3 and cmd[3] == expected_range:
            return True
    return False


def test_get_remote_protocol_detection() -> None:
    assert get_remote_protocol("https://github.com/org/repo.git") == "https"
    assert get_remote_protocol("http://github.com/org/repo.git") == "https"
    assert get_remote_protocol("git@github.com:org/repo.git") == "ssh"
    assert get_remote_protocol("ssh://git@github.com/org/repo.git") == "ssh"
    assert get_remote_protocol("file:///tmp/repo") == "unknown"


def test_https_remote_to_ssh_github_only() -> None:
    assert https_remote_to_ssh("https://github.com/org/repo.git") == "git@github.com:org/repo.git"
    assert https_remote_to_ssh("https://github.com/org/repo") == "git@github.com:org/repo.git"
    assert https_remote_to_ssh("https://github.com/org/repo/") == "git@github.com:org/repo.git"
    assert https_remote_to_ssh("https://gitlab.com/org/repo.git") is None


def test_execute_publish_no_commits_aborts_before_pr_create() -> None:
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
            return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))
        if cmd[:3] == ["git", "rev-list", "--count"]:
            return CmdResult(code=0, stdout="0\n", stderr="", cmd=list(cmd))
        return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))

    with patch("panel.app.get_repo") as mock_get_repo, \
         patch("panel.app.is_valid_branch_name", return_value=True), \
         patch("panel.app.get_git_state", return_value=("feature", "abc123")), \
         patch("panel.app.git_status_porcelain", return_value=[]), \
         patch("panel.app.run", side_effect=run_side_effect) as mock_run, \
         patch("panel.app.record_job_result") as mock_record:
        mock_get_repo.return_value = MagicMock(key="metarepo", path="/tmp/mock")

        req = PublishOptions(branch="feature")
        execute_publish("job-1", "corr-1", "metarepo", req)

        results = [call.args[1] for call in mock_record.call_args_list]
        assert any(result.action == "git.pr.precheck" and not result.ok for result in results)
        assert any(
            call.args[0][:5] == [
                "git",
                "fetch",
                "origin",
                "main:refs/remotes/origin/main",
                "feature:refs/remotes/origin/feature",
            ]
            for call in mock_run.call_args_list
        )
        assert not any(
            call.args[0][:3] == ["gh", "pr", "create"]
            for call in mock_run.call_args_list
        )


def test_precheck_uses_origin_refs_and_fetches() -> None:
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
            return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))
        if cmd[:3] == ["git", "rev-list", "--count"]:
            return CmdResult(code=0, stdout="2\n", stderr="", cmd=list(cmd))
        if cmd[:3] == ["gh", "pr", "create"]:
            return CmdResult(code=1, stdout="", stderr="no pr", cmd=list(cmd))
        return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))

    with patch("panel.app.get_repo") as mock_get_repo, \
         patch("panel.app.is_valid_branch_name", return_value=True), \
         patch("panel.app.get_git_state", return_value=("feature", "abc123")), \
         patch("panel.app.git_status_porcelain", return_value=[]), \
         patch("panel.app.run", side_effect=run_side_effect) as mock_run, \
         patch("panel.app.find_existing_pr_url", return_value=None), \
         patch("panel.app.record_job_result"):
        mock_get_repo.return_value = MagicMock(key="metarepo", path="/tmp/mock")

        req = PublishOptions(branch="feature")
        execute_publish("job-1", "corr-1", "metarepo", req)

        assert any(
            call.args[0][:5] == [
                "git",
                "fetch",
                "origin",
                "main:refs/remotes/origin/main",
                "feature:refs/remotes/origin/feature",
            ]
            for call in mock_run.call_args_list
        )


def test_execute_publish_origin_upstream_uses_upstream_branch() -> None:
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
            return CmdResult(code=0, stdout="origin/feature-remote\n", stderr="", cmd=list(cmd))
        if cmd[:2] == ["git", "fetch"]:
            return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))
        if cmd[:3] == ["git", "rev-list", "--count"]:
            return CmdResult(code=0, stdout="1\n", stderr="", cmd=list(cmd))
        if cmd[:3] == ["gh", "pr", "create"]:
            return CmdResult(code=1, stdout="", stderr="no pr", cmd=list(cmd))
        return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))

    with patch("panel.app.get_repo") as mock_get_repo, \
         patch("panel.app.is_valid_branch_name", return_value=True), \
         patch("panel.app.get_git_state", return_value=("feature-local", "abc123")), \
         patch("panel.app.git_status_porcelain", return_value=[]), \
         patch("panel.app.run", side_effect=run_side_effect) as mock_run, \
         patch("panel.app.find_existing_pr_url", return_value=None), \
         patch("panel.app.record_job_result"):
        mock_get_repo.return_value = MagicMock(key="metarepo", path="/tmp/mock")

        req = PublishOptions(branch="feature-local")
        execute_publish("job-1", "corr-1", "metarepo", req)

        assert any(
            call.args[0][:5] == [
                "git",
                "fetch",
                "origin",
                "main:refs/remotes/origin/main",
                "feature-remote:refs/remotes/origin/feature-remote",
            ]
            for call in mock_run.call_args_list
        )
        assert has_pr_create_head(mock_run.call_args_list, "feature-remote")
        assert has_rev_list_count(mock_run.call_args_list, "origin/main..origin/feature-remote")


def test_execute_publish_upstream_fallback_uses_head_branch() -> None:
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
            return CmdResult(code=1, stdout="", stderr="no upstream", cmd=list(cmd))
        if cmd[:2] == ["git", "fetch"]:
            return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))
        if cmd[:3] == ["git", "rev-list", "--count"]:
            return CmdResult(code=0, stdout="1\n", stderr="", cmd=list(cmd))
        if cmd[:3] == ["gh", "pr", "create"]:
            return CmdResult(code=1, stdout="", stderr="no pr", cmd=list(cmd))
        return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))

    with patch("panel.app.get_repo") as mock_get_repo, \
         patch("panel.app.is_valid_branch_name", return_value=True), \
         patch("panel.app.get_git_state", return_value=("feature", "abc123")), \
         patch("panel.app.git_status_porcelain", return_value=[]), \
         patch("panel.app.run", side_effect=run_side_effect) as mock_run, \
         patch("panel.app.find_existing_pr_url", return_value=None), \
         patch("panel.app.record_job_result") as mock_record:
        mock_get_repo.return_value = MagicMock(key="metarepo", path="/tmp/mock")

        req = PublishOptions(branch="feature")
        execute_publish("job-1", "corr-1", "metarepo", req)

        results = [call.args[1] for call in mock_record.call_args_list]
        assert any(
            result.action == "git.branch.upstream"
            and not result.ok
            and result.error_kind == "upstream_unavailable"
            and "Upstream not available" in result.message
            for result in results
        )
        assert any(
            call.args[0][:5] == [
                "git",
                "fetch",
                "origin",
                "main:refs/remotes/origin/main",
                "feature:refs/remotes/origin/feature",
            ]
            for call in mock_run.call_args_list
        )


def test_execute_publish_non_origin_upstream_message_uses_head_branch() -> None:
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
            return CmdResult(code=0, stdout="upstream/feature\n", stderr="", cmd=list(cmd))
        if cmd[:2] == ["git", "fetch"]:
            return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))
        if cmd[:3] == ["git", "rev-list", "--count"]:
            return CmdResult(code=0, stdout="1\n", stderr="", cmd=list(cmd))
        if cmd[:3] == ["gh", "pr", "create"]:
            return CmdResult(code=1, stdout="", stderr="no pr", cmd=list(cmd))
        return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))

    with patch("panel.app.get_repo") as mock_get_repo, \
         patch("panel.app.is_valid_branch_name", return_value=True), \
         patch("panel.app.get_git_state", return_value=("feature", "abc123")), \
         patch("panel.app.git_status_porcelain", return_value=[]), \
         patch("panel.app.run", side_effect=run_side_effect) as mock_run, \
         patch("panel.app.find_existing_pr_url", return_value=None), \
         patch("panel.app.record_job_result") as mock_record:
        mock_get_repo.return_value = MagicMock(key="metarepo", path="/tmp/mock")

        req = PublishOptions(branch="feature")
        execute_publish("job-1", "corr-1", "metarepo", req)

        results = [call.args[1] for call in mock_record.call_args_list]
        assert any(
            result.action == "git.branch.upstream"
            and result.ok
            and result.error_kind == "upstream_non_origin"
            and "non-origin" in result.message
            for result in results
        )
        assert any(
            call.args[0][:5] == [
                "git",
                "fetch",
                "origin",
                "main:refs/remotes/origin/main",
                "feature:refs/remotes/origin/feature",
            ]
            for call in mock_run.call_args_list
        )


def test_execute_publish_empty_upstream_message() -> None:
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
            return CmdResult(code=0, stdout="\n", stderr="", cmd=list(cmd))
        if cmd[:2] == ["git", "fetch"]:
            return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))
        if cmd[:3] == ["git", "rev-list", "--count"]:
            return CmdResult(code=0, stdout="1\n", stderr="", cmd=list(cmd))
        if cmd[:3] == ["gh", "pr", "create"]:
            return CmdResult(code=1, stdout="", stderr="no pr", cmd=list(cmd))
        return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))

    with patch("panel.app.get_repo") as mock_get_repo, \
         patch("panel.app.is_valid_branch_name", return_value=True), \
         patch("panel.app.get_git_state", return_value=("feature", "abc123")), \
         patch("panel.app.git_status_porcelain", return_value=[]), \
         patch("panel.app.run", side_effect=run_side_effect), \
         patch("panel.app.find_existing_pr_url", return_value=None), \
         patch("panel.app.record_job_result") as mock_record:
        mock_get_repo.return_value = MagicMock(key="metarepo", path="/tmp/mock")

        req = PublishOptions(branch="feature")
        execute_publish("job-1", "corr-1", "metarepo", req)

        results = [call.args[1] for call in mock_record.call_args_list]
        assert any(
            result.action == "git.branch.upstream"
            and result.ok
            and result.error_kind == "upstream_missing"
            and "No upstream configured" in result.message
            for result in results
        )


def test_execute_publish_rewrites_https_remote() -> None:
    def run_side_effect(cmd, cwd, timeout=60, env=None, input_text=None):
        if cmd[:3] == ["git", "ls-remote", "--heads"]:
            return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))
        if cmd[:2] == ["gh", "--version"]:
            return CmdResult(code=0, stdout="gh version 2.0.0", stderr="", cmd=list(cmd))
        if cmd[:3] == ["gh", "auth", "status"]:
            return CmdResult(code=0, stdout="logged in", stderr="", cmd=list(cmd))
        if cmd[:3] == ["git", "remote", "get-url"]:
            return CmdResult(code=0, stdout="https://github.com/org/repo.git\n", stderr="", cmd=list(cmd))
        if cmd[:3] == ["git", "remote", "set-url"]:
            return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))
        if cmd[:2] == ["git", "push"]:
            return CmdResult(code=1, stdout="", stderr="push failed", cmd=list(cmd))
        return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))

    with patch("panel.app.get_repo") as mock_get_repo, \
         patch("panel.app.is_valid_branch_name", return_value=True), \
         patch("panel.app.get_git_state", return_value=("feature", "abc123")), \
         patch("panel.app.git_status_porcelain", return_value=[]), \
         patch("panel.app.run", side_effect=run_side_effect) as mock_run, \
         patch("panel.app.record_job_result") as mock_record:
        mock_get_repo.return_value = MagicMock(key="metarepo", path="/tmp/mock")

        req = PublishOptions(branch="feature")
        execute_publish("job-1", "corr-1", "metarepo", req)

        calls = [call.args[1] for call in mock_record.call_args_list]
        assert any(result.action == "git.remote.rewrite" and result.ok for result in calls)
        assert any(result.action == "git.push" and not result.ok for result in calls)
        assert any(call.args[0] == ["git", "remote", "set-url", "origin", "git@github.com:org/repo.git"]
                   for call in mock_run.call_args_list)


def test_execute_publish_fetch_failure_aborts() -> None:
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
            return CmdResult(code=1, stdout="", stderr="fetch failed", cmd=list(cmd))
        return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))

    with patch("panel.app.get_repo") as mock_get_repo, \
         patch("panel.app.is_valid_branch_name", return_value=True), \
         patch("panel.app.get_git_state", return_value=("feature", "abc123")), \
         patch("panel.app.git_status_porcelain", return_value=[]), \
         patch("panel.app.run", side_effect=run_side_effect) as mock_run, \
         patch("panel.app.record_job_result") as mock_record:
        mock_get_repo.return_value = MagicMock(key="metarepo", path="/tmp/mock")

        req = PublishOptions(branch="feature")
        execute_publish("job-1", "corr-1", "metarepo", req)

        results = [call.args[1] for call in mock_record.call_args_list]
        assert any(result.action == "git.fetch" and not result.ok for result in results)
        assert not any(
            call.args[0][:3] == ["gh", "pr", "create"]
            for call in mock_run.call_args_list
        )


def test_execute_publish_missing_gh() -> None:
    def run_side_effect(cmd, cwd, timeout=60, env=None, input_text=None):
        if cmd[:3] == ["git", "ls-remote", "--heads"]:
            return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))
        if cmd[:2] == ["gh", "--version"]:
            return CmdResult(code=127, stdout="", stderr="command not found", cmd=list(cmd))
        return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))

    with patch("panel.app.get_repo") as mock_get_repo, \
         patch("panel.app.is_valid_branch_name", return_value=True), \
         patch("panel.app.get_git_state", return_value=("feature", "abc123")), \
         patch("panel.app.run", side_effect=run_side_effect), \
         patch("panel.app.record_job_result") as mock_record:
        mock_get_repo.return_value = MagicMock(key="metarepo", path="/tmp/mock")

        req = PublishOptions(branch="feature")
        execute_publish("job-1", "corr-1", "metarepo", req)

        calls = [call.args[1] for call in mock_record.call_args_list]
        assert any(result.action == "gh.version" and not result.ok for result in calls)


def test_execute_publish_https_remote_rewrite_disabled(monkeypatch) -> None:
    monkeypatch.setenv("ACS_PUBLISH_REWRITE_REMOTE", "0")

    def run_side_effect(cmd, cwd, timeout=60, env=None, input_text=None):
        if cmd[:3] == ["git", "ls-remote", "--heads"]:
            return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))
        if cmd[:2] == ["gh", "--version"]:
            return CmdResult(code=0, stdout="gh 2.0.0", stderr="", cmd=list(cmd))
        if cmd[:3] == ["gh", "auth", "status"]:
            return CmdResult(code=0, stdout="logged in", stderr="", cmd=list(cmd))
        if cmd[:3] == ["git", "remote", "get-url"]:
            return CmdResult(code=0, stdout="https://github.com/org/repo.git\n", stderr="", cmd=list(cmd))
        return CmdResult(code=0, stdout="", stderr="", cmd=list(cmd))

    with patch("panel.app.get_repo") as mock_get_repo, \
         patch("panel.app.is_valid_branch_name", return_value=True), \
         patch("panel.app.get_git_state", return_value=("feature", "abc123")), \
         patch("panel.app.git_status_porcelain", return_value=[]), \
         patch("panel.app.run", side_effect=run_side_effect) as mock_run, \
         patch("panel.app.record_job_result") as mock_record:
        mock_get_repo.return_value = MagicMock(key="metarepo", path="/tmp/mock")

        req = PublishOptions(branch="feature")
        execute_publish("job-1", "corr-1", "metarepo", req)

        results = [call.args[1] for call in mock_record.call_args_list]
        assert any(result.action == "git.remote.protocol" and not result.ok for result in results)
        assert not any(
            call.args[0][:3] == ["git", "remote", "set-url"]
            for call in mock_run.call_args_list
        )
