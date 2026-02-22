import pytest
from panel.utils import format_command_line, truncate_text, combine_output, classify_git_ref_error

def test_format_command_line():
    assert format_command_line(["ls", "-l"]) == "$ ls -l"
    assert format_command_line(["echo", "hello world"]) == "$ echo 'hello world'"
    assert format_command_line(["git", "commit", "-m", "feat: something"]) == "$ git commit -m 'feat: something'"
    assert format_command_line([]) == "$ "


def test_truncate_text():
    # Shorter than limit
    assert truncate_text("hello", 10) == "hello"
    # Equal to limit
    assert truncate_text("hello", 5) == "hello"
    # Longer than limit
    assert truncate_text("hello world", 5) == "hello… (truncated)"


def test_combine_output():
    class MockResult:
        def __init__(self, stdout, stderr):
            self.stdout = stdout
            self.stderr = stderr

    # Both present
    assert combine_output(MockResult("out", "err")) == "out\nerr"
    # Only stdout
    assert combine_output(MockResult("out", "")) == "out"
    # Only stderr
    assert combine_output(MockResult("", "err")) == "err"
    # Neither
    assert combine_output(MockResult("", "")) == ""
    # None values
    assert combine_output(MockResult(None, "err")) == "err"


def test_classify_git_ref_error():
    # ref_lock
    err_lock = "error: cannot lock ref 'refs/heads/main': is at 123 but expected 456"
    res_lock = classify_git_ref_error(err_lock)
    assert res_lock is not None
    assert res_lock["error_kind"] == "ref_lock"
    assert res_lock["affected_ref"] == "refs/heads/main"

    # resolve_ref_failed
    err_resolve = "fatal: unable to resolve reference 'refs/heads/feature/bad'"
    res_resolve = classify_git_ref_error(err_resolve)
    assert res_resolve is not None
    assert res_resolve["error_kind"] == "resolve_ref_failed"
    assert res_resolve["affected_ref"] == "refs/heads/feature/bad"

    # dangling_ref
    err_dangling = "error: refs/heads/dangling has become dangling"
    res_dangling = classify_git_ref_error(err_dangling)
    assert res_dangling is not None
    assert res_dangling["error_kind"] == "dangling_ref"
    assert res_dangling["affected_ref"] == "refs/heads/dangling"

    # ref_repair_failed
    err_repair = "error: packed refs appear corrupt"
    res_repair = classify_git_ref_error(err_repair)
    assert res_repair is not None
    assert res_repair["error_kind"] == "ref_repair_failed"
    assert res_repair["affected_ref"] is None

    # No match
    assert classify_git_ref_error("some other error") is None
    assert classify_git_ref_error("") is None
