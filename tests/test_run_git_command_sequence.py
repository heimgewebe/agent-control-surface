from pathlib import Path
from unittest.mock import patch

from panel.runner import CmdResult
from panel.utils import MAX_OUTPUT_CHARS, run_git_command_sequence


def test_run_git_command_sequence_success():
    path = Path("/tmp/test_repo")
    commands = [["git", "status"], ["git", "diff"]]

    mock_results = [
        CmdResult(code=0, stdout="On branch main", stderr="", cmd=["git", "status"]),
        CmdResult(code=0, stdout="diff content", stderr="", cmd=["git", "diff"]),
    ]

    with patch("panel.utils.run", side_effect=mock_results) as mock_run:
        ok, stdout, stderr, code, optional_failures = run_git_command_sequence(
            path, commands, timeout=10
        )

        assert ok is True
        assert "On branch main" in stdout
        assert "diff content" in stdout
        assert stderr == ""
        assert code == 0
        assert optional_failures == []
        assert mock_run.call_count == 2

def test_run_git_command_sequence_failure():
    path = Path("/tmp/test_repo")
    commands = [["git", "status"], ["git", "invalid"]]

    mock_results = [
        CmdResult(code=0, stdout="On branch main", stderr="", cmd=["git", "status"]),
        CmdResult(code=1, stdout="", stderr="error: invalid command", cmd=["git", "invalid"]),
    ]

    with patch("panel.utils.run", side_effect=mock_results):
        ok, stdout, stderr, code, optional_failures = run_git_command_sequence(
            path, commands, timeout=10
        )

        assert ok is False
        assert "On branch main" in stdout
        assert "error: invalid command" in stderr
        assert code == 1
        assert optional_failures == []

def test_run_git_command_sequence_stop_on_error():
    path = Path("/tmp/test_repo")
    commands = [["git", "status"], ["git", "invalid"], ["git", "diff"]]

    mock_results = [
        CmdResult(code=0, stdout="On branch main", stderr="", cmd=["git", "status"]),
        CmdResult(code=1, stdout="", stderr="error: invalid command", cmd=["git", "invalid"]),
        CmdResult(code=0, stdout="should not run", stderr="", cmd=["git", "diff"]),
    ]

    with patch("panel.utils.run", side_effect=mock_results) as mock_run:
        # stop_on_error=True
        ok, stdout, stderr, code, optional_failures = run_git_command_sequence(
            path, commands, timeout=10, stop_on_error=True
        )

        assert ok is False
        assert mock_run.call_count == 2
        assert "should not run" not in stdout

def test_run_git_command_sequence_continue_on_error():
    path = Path("/tmp/test_repo")
    commands = [["git", "status"], ["git", "invalid"], ["git", "diff"]]

    mock_results = [
        CmdResult(code=0, stdout="On branch main", stderr="", cmd=["git", "status"]),
        CmdResult(code=1, stdout="", stderr="error: invalid command", cmd=["git", "invalid"]),
        CmdResult(code=0, stdout="still ran", stderr="", cmd=["git", "diff"]),
    ]

    with patch("panel.utils.run", side_effect=mock_results) as mock_run:
        # stop_on_error=False (default)
        ok, stdout, stderr, code, optional_failures = run_git_command_sequence(
            path, commands, timeout=10, stop_on_error=False
        )

        assert ok is False
        assert mock_run.call_count == 3
        assert "still ran" in stdout
        assert code == 0  # last code is from git diff

def test_run_git_command_sequence_allow_failures_index():
    path = Path("/tmp/test_repo")
    commands = [["git", "status"], ["git", "optional"]]

    mock_results = [
        CmdResult(code=0, stdout="On branch main", stderr="", cmd=["git", "status"]),
        CmdResult(code=1, stdout="", stderr="optional failure", cmd=["git", "optional"]),
    ]

    with patch("panel.utils.run", side_effect=mock_results):
        ok, stdout, stderr, code, optional_failures = run_git_command_sequence(
            path, commands, timeout=10, allow_failures={1}
        )

        assert ok is True
        assert optional_failures == ["$ git optional"]
        assert code == 1

def test_run_git_command_sequence_allow_failures_command():
    path = Path("/tmp/test_repo")
    commands = [["git", "status"], ["git", "optional"]]

    mock_results = [
        CmdResult(code=0, stdout="On branch main", stderr="", cmd=["git", "status"]),
        CmdResult(code=1, stdout="", stderr="optional failure", cmd=["git", "optional"]),
    ]

    with patch("panel.utils.run", side_effect=mock_results):
        ok, stdout, stderr, code, optional_failures = run_git_command_sequence(
            path, commands, timeout=10, allow_failure_cmds={("git", "optional")}
        )

        assert ok is True
        assert optional_failures == ["$ git optional"]
        assert code == 1

def test_run_git_command_sequence_per_command_truncation():
    path = Path("/tmp/test_repo")
    commands = [["git", "long"]]

    long_output = "a" * 25000
    mock_results = [
        CmdResult(code=0, stdout=long_output, stderr="", cmd=["git", "long"]),
    ]

    with patch("panel.utils.run", side_effect=mock_results):
        ok, stdout, stderr, code, optional_failures = run_git_command_sequence(
            path, commands, timeout=10
        )

        assert "a" * 20000 in stdout
        assert "… (truncated)" in stdout
        assert len(stdout) < 25000

def test_run_git_command_sequence_combined_truncation():
    path = Path("/tmp/test_repo")
    # MAX_OUTPUT_CHARS is 50000
    commands = [["git", "cmd1"], ["git", "cmd2"], ["git", "cmd3"]]

    out1 = "a" * 20000
    out2 = "b" * 20000
    out3 = "c" * 20000

    mock_results = [
        CmdResult(code=0, stdout=out1, stderr="", cmd=["git", "cmd1"]),
        CmdResult(code=0, stdout=out2, stderr="", cmd=["git", "cmd2"]),
        CmdResult(code=0, stdout=out3, stderr="", cmd=["git", "cmd3"]),
    ]

    with patch("panel.utils.run", side_effect=mock_results):
        ok, stdout, stderr, code, optional_failures = run_git_command_sequence(
            path, commands, timeout=10
        )

        assert len(stdout) <= MAX_OUTPUT_CHARS + 64
        assert "… (truncated)" in stdout

def test_run_git_command_sequence_empty():
    path = Path("/tmp/test_repo")
    commands = []

    ok, stdout, stderr, code, optional_failures = run_git_command_sequence(
        path, commands, timeout=10
    )

    assert ok is True
    assert stdout == ""
    assert stderr == ""
    assert code is None
    assert optional_failures == []
