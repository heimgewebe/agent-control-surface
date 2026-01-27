from unittest.mock import patch, MagicMock
from panel.app import extract_pr_url, find_existing_pr_url, Path
import json

def test_extract_pr_url():
    # Standard output from gh pr create
    text = "https://github.com/user/repo/pull/123"
    assert extract_pr_url(text) == "https://github.com/user/repo/pull/123"

    # With surrounding text
    text = "Created PR: https://github.com/user/repo/pull/456 in background."
    assert extract_pr_url(text) == "https://github.com/user/repo/pull/456"

    # With punctuation
    text = "See https://github.com/user/repo/pull/789."
    assert extract_pr_url(text) == "https://github.com/user/repo/pull/789"

    # None
    assert extract_pr_url("No URL here") is None
    assert extract_pr_url("") is None
    assert extract_pr_url(None) is None

def test_find_existing_pr_url():
    with patch("panel.app.run") as mock_run:
        # Success case
        mock_output = MagicMock()
        mock_output.code = 0
        mock_output.stdout = json.dumps([{"url": "https://github.com/u/r/pull/999"}])
        mock_run.return_value = mock_output

        url = find_existing_pr_url(Path("/tmp"), "feature", "main")
        assert url == "https://github.com/u/r/pull/999"

        # Verify call args
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "gh"
        assert args[1] == "pr"
        assert "feature" in args
        assert "main" in args

        # No PR found
        mock_run.reset_mock()
        mock_output.stdout = "[]"
        mock_run.return_value = mock_output
        assert find_existing_pr_url(Path("/tmp"), "feature", "main") is None

        # Error case
        mock_run.reset_mock()
        mock_output.code = 1
        mock_run.return_value = mock_output
        assert find_existing_pr_url(Path("/tmp"), "feature", "main") is None

        # Invalid JSON
        mock_run.reset_mock()
        mock_output.code = 0
        mock_output.stdout = "not json"
        mock_run.return_value = mock_output
        assert find_existing_pr_url(Path("/tmp"), "feature", "main") is None
