import os
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from panel.app import app
from panel.runner import run as runner_run

def _mk_repo(tmp_path: Path, name: str = "repo") -> Path:
    p = tmp_path / name
    p.mkdir(parents=True, exist_ok=True)
    runner_run(["git", "init"], cwd=p)
    runner_run(["git", "config", "user.email", "you@example.com"], cwd=p)
    runner_run(["git", "config", "user.name", "Your Name"], cwd=p)
    # Create an initial commit so we are not on an unborn branch
    (p / "initial").touch()
    runner_run(["git", "add", "initial"], cwd=p)
    runner_run(["git", "commit", "-m", "initial"], cwd=p)
    # Switch to a feature branch to bypass branch guard
    runner_run(["git", "checkout", "-b", "feature"], cwd=p)
    return p

@pytest.fixture
def mock_get_repo(monkeypatch, tmp_path):
    from panel.repos import Repo
    repo_path = _mk_repo(tmp_path, "test-repo")
    def _get_repo(key):
        if key == "test-repo":
            return Repo(key=key, path=repo_path, display=f"mock/{key}")
        raise KeyError(key)

    monkeypatch.setattr("panel.app.get_repo", _get_repo)
    return repo_path

def test_git_commit_safety(mock_get_repo):
    client = TestClient(app)
    repo_path = mock_get_repo

    # 1. Create a tracked file and modify it
    tracked_file = repo_path / "tracked.txt"
    tracked_file.write_text("initial content")
    runner_run(["git", "add", "tracked.txt"], cwd=repo_path)
    runner_run(["git", "commit", "-m", "add tracked"], cwd=repo_path)

    tracked_file.write_text("modified content")

    # 2. Create an untracked file (the "secret")
    secret_file = repo_path / ".env"
    secret_file.write_text("SECRET_TOKEN=12345")

    # 3. Call the commit API without explicit files
    response = client.post(
        "/api/git/commit",
        json={"repo": "test-repo", "message": "Commit modified but not untracked"}
    )
    assert response.status_code == 200

    # 4. Verify that the tracked file is committed (no longer shows as modified)
    status = runner_run(["git", "status", "--porcelain"], cwd=repo_path).stdout
    # Modified tracked file should be gone from status (if committed)
    # Untracked file should still be there as ??
    assert "?? .env" in status
    assert "M tracked.txt" not in status

    # 5. Now try to commit the secret explicitly
    response = client.post(
        "/api/git/commit",
        json={"repo": "test-repo", "message": "Explicitly commit secret", "files": [".env"]}
    )
    assert response.status_code == 200

    # 6. Verify that the secret is now committed
    status = runner_run(["git", "status", "--porcelain"], cwd=repo_path).stdout
    assert "?? .env" not in status
    assert ".env" not in status # Should be clean
