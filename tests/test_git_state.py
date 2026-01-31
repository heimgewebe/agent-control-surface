import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
from panel.app import get_git_state
from panel.runner import CmdResult

class TestGitState(unittest.TestCase):
    def setUp(self):
        self.path = Path("/tmp/mock/repo")

    @patch("panel.app.run")
    def test_normal_branch(self, mock_run):
        mock_run.return_value = CmdResult(
            code=0,
            stdout="# branch.oid abc1234567890\n# branch.head main\n# branch.upstream origin/main",
            stderr="",
            cmd=[]
        )
        branch, head = get_git_state(self.path)
        self.assertEqual(branch, "main")
        self.assertEqual(head, "abc1234567890")

    @patch("panel.app.run")
    def test_detached_head(self, mock_run):
        mock_run.return_value = CmdResult(
            code=0,
            stdout="# branch.oid abc1234567890\n# branch.head (detached)\n",
            stderr="",
            cmd=[]
        )
        branch, head = get_git_state(self.path)
        self.assertEqual(branch, "HEAD")
        self.assertEqual(head, "abc1234567890")

    @patch("panel.app.run")
    def test_unborn_branch(self, mock_run):
        # Initial commit state: branch exists (e.g. main/master) but no commit (oid initial)
        mock_run.return_value = CmdResult(
            code=0,
            stdout="# branch.oid (initial)\n# branch.head main\n",
            stderr="",
            cmd=[]
        )
        branch, head = get_git_state(self.path)
        self.assertEqual(branch, "main")
        self.assertIsNone(head)

    @patch("panel.app.run")
    def test_error_code(self, mock_run):
        mock_run.return_value = CmdResult(
            code=128,
            stdout="",
            stderr="fatal: not a git repository",
            cmd=[]
        )
        branch, head = get_git_state(self.path)
        self.assertIsNone(branch)
        self.assertIsNone(head)

    @patch("panel.app.run")
    def test_partial_output(self, mock_run):
        # Only OID present, no branch head info
        mock_run.return_value = CmdResult(
            code=0,
            stdout="# branch.oid abc123\n",
            stderr="",
            cmd=[]
        )
        branch, head = get_git_state(self.path)
        # Should fallback to HEAD for branch because branch is missing
        self.assertEqual(branch, "HEAD")
        self.assertEqual(head, "abc123")

    @patch("panel.app.run")
    def test_unknown_branch_state(self, mock_run):
        # branch.head is (unknown)
        mock_run.return_value = CmdResult(
            code=0,
            stdout="# branch.oid abc123456\n# branch.head (unknown)\n",
            stderr="",
            cmd=[]
        )
        branch, head = get_git_state(self.path)
        self.assertEqual(branch, "HEAD")
        self.assertEqual(head, "abc123456")

if __name__ == "__main__":
    unittest.main()
