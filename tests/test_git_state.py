from unittest.mock import patch
from pathlib import Path
from panel.app import get_git_state
from panel.runner import CmdResult

MOCK_REPO_PATH = Path("/tmp/mock/repo")

@patch("panel.app.run")
def test_normal_branch(mock_run):
    mock_run.return_value = CmdResult(
        code=0,
        stdout="# branch.oid abc1234567890\n# branch.head main\n# branch.upstream origin/main",
        stderr="",
        cmd=[]
    )
    branch, head = get_git_state(MOCK_REPO_PATH)
    assert branch == "main"
    assert head == "abc1234567890"

@patch("panel.app.run")
def test_detached_head(mock_run):
    mock_run.return_value = CmdResult(
        code=0,
        stdout="# branch.oid abc1234567890\n# branch.head (detached)\n",
        stderr="",
        cmd=[]
    )
    branch, head = get_git_state(MOCK_REPO_PATH)
    assert branch == "HEAD"
    assert head == "abc1234567890"

@patch("panel.app.run")
def test_unborn_branch(mock_run):
    # Initial commit state: branch exists (e.g. main/master) but no commit (oid initial)
    mock_run.return_value = CmdResult(
        code=0,
        stdout="# branch.oid (initial)\n# branch.head main\n",
        stderr="",
        cmd=[]
    )
    branch, head = get_git_state(MOCK_REPO_PATH)
    assert branch == "main"
    assert head is None

@patch("panel.app.run")
def test_error_code(mock_run):
    mock_run.return_value = CmdResult(
        code=128,
        stdout="",
        stderr="fatal: not a git repository",
        cmd=[]
    )
    branch, head = get_git_state(MOCK_REPO_PATH)
    assert branch is None
    assert head is None

@patch("panel.app.run")
def test_partial_output(mock_run):
    # Only OID present, no branch head info
    mock_run.return_value = CmdResult(
        code=0,
        stdout="# branch.oid abc123\n",
        stderr="",
        cmd=[]
    )
    branch, head = get_git_state(MOCK_REPO_PATH)
    # Should fallback to HEAD for branch because branch is missing
    assert branch == "HEAD"
    assert head == "abc123"

@patch("panel.app.run")
def test_unknown_branch_state(mock_run):
    # branch.head is (unknown)
    mock_run.return_value = CmdResult(
        code=0,
        stdout="# branch.oid abc123456\n# branch.head (unknown)\n",
        stderr="",
        cmd=[]
    )
    branch, head = get_git_state(MOCK_REPO_PATH)
    assert branch == "HEAD"
    assert head == "abc123456"
