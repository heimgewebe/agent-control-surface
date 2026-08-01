from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from panel.app import (
    ApplyPatchReq,
    GitCommitReq,
    apply_patch_action,
    commit_action,
    extract_patch_files,
)
from panel.git_staging import (
    StagingError,
    canonicalize_repo_paths,
    commit_intended_paths,
    stage_intended_paths,
    staged_paths,
)
from panel.repos import Repo


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "feature")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "ACS Tests")
    for name in ("intended.txt", "unrelated.txt", "pre-staged.txt"):
        (repo / name).write_text(f"initial {name}\n", encoding="utf-8")
    _git(repo, "add", "--", "intended.txt", "unrelated.txt", "pre-staged.txt")
    _git(repo, "commit", "-m", "initial")
    return repo


def test_explicit_staging_leaves_unrelated_and_secret_like_files_untouched(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    (repo / "intended.txt").write_text("intended change\n", encoding="utf-8")
    (repo / "unrelated.txt").write_text("unrelated change\n", encoding="utf-8")
    (repo / "notes.txt").write_text("untracked notes\n", encoding="utf-8")
    (repo / ".env").write_text("API_TOKEN=should-not-stage\n", encoding="utf-8")

    result = stage_intended_paths(repo, ["intended.txt"])

    assert result.paths == ("intended.txt",)
    assert staged_paths(repo) == {"intended.txt"}
    status = _git(repo, "status", "--short")
    assert " M unrelated.txt" in status
    assert "?? notes.txt" in status
    assert "?? .env" in status


def test_scoped_commit_preserves_pre_staged_and_untracked_files(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "pre-staged.txt").write_text("user staged\n", encoding="utf-8")
    _git(repo, "add", "--", "pre-staged.txt")
    (repo / "intended.txt").write_text("operation change\n", encoding="utf-8")
    (repo / "unrelated.txt").write_text("user worktree change\n", encoding="utf-8")
    (repo / "credentials.json").write_text('{"token":"untouched"}\n', encoding="utf-8")

    result = commit_intended_paths(repo, "scoped commit", ["intended.txt"])

    assert result.command_result.code == 0
    assert set(_git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").split()) == {
        "intended.txt"
    }
    assert staged_paths(repo) == {"pre-staged.txt"}
    status = _git(repo, "status", "--short")
    assert " M unrelated.txt" in status
    assert "?? credentials.json" in status


def test_failed_commit_restores_index_and_preserves_user_staging(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "pre-staged.txt").write_text("user staged\n", encoding="utf-8")
    _git(repo, "add", "--", "pre-staged.txt")
    (repo / "intended.txt").write_text("operation change\n", encoding="utf-8")

    result = commit_intended_paths(repo, "", ["intended.txt"])

    assert result.command_result.code != 0
    assert staged_paths(repo) == {"pre-staged.txt"}
    assert " M intended.txt" in _git(repo, "status", "--short")


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../outside.txt",
        "/etc/passwd",
        ".git/config",
        ".env",
        "credentials.json",
        "private.pem",
        "directory",
        "./intended.txt",
        "nested//file.txt",
    ],
)
def test_canonicalization_rejects_escape_metadata_secret_and_directory_paths(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    repo = _repo(tmp_path)
    (repo / "directory").mkdir()
    (repo / ".env").write_text("SECRET=value\n", encoding="utf-8")
    (repo / "credentials.json").write_text("{}\n", encoding="utf-8")
    (repo / "private.pem").write_text("private\n", encoding="utf-8")

    with pytest.raises(StagingError):
        canonicalize_repo_paths(repo, [unsafe_path])


def test_symlinked_path_is_rejected_without_reading_outside_target(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("outside secret\n", encoding="utf-8")
    (repo / "linked-outside").symlink_to(outside)

    with pytest.raises(StagingError, match="Symlinked"):
        stage_intended_paths(repo, ["linked-outside"])

    assert staged_paths(repo) == set()
    assert outside.read_text(encoding="utf-8") == "outside secret\n"


def test_git_pathspec_magic_is_treated_as_a_literal_filename(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    magic_name = ":(glob)*.txt"
    (repo / magic_name).write_text("literal magic path\n", encoding="utf-8")
    (repo / "other.txt").write_text("must remain untracked\n", encoding="utf-8")

    stage_intended_paths(repo, [magic_name])

    assert staged_paths(repo) == {magic_name}
    assert "?? other.txt" in _git(repo, "status", "--short")


def test_delete_and_rename_scopes_commit_exact_paths(tmp_path: Path) -> None:
    delete_repo = _repo(tmp_path / "delete")
    (delete_repo / "intended.txt").unlink()
    deleted = commit_intended_paths(delete_repo, "delete intended", ["intended.txt"])
    assert deleted.command_result.code == 0
    assert deleted.paths == ("intended.txt",)

    rename_repo = _repo(tmp_path / "rename")
    (rename_repo / "intended.txt").rename(rename_repo / "renamed.txt")
    renamed = commit_intended_paths(
        rename_repo,
        "rename intended",
        ["intended.txt", "renamed.txt"],
    )
    assert renamed.command_result.code == 0
    assert renamed.paths == ("intended.txt", "renamed.txt")


def test_patch_inventory_includes_quoted_rename_source_and_destination() -> None:
    patch_text = 'diff --git "a/old name.txt" "b/new name.txt"\n'

    assert extract_patch_files(patch_text) == {"old name.txt", "new name.txt"}


def test_duplicate_and_unchanged_scopes_fail_without_index_mutation(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    with pytest.raises(StagingError, match="Duplicate"):
        stage_intended_paths(repo, ["intended.txt", "intended.txt"])
    with pytest.raises(StagingError, match="pending file change"):
        stage_intended_paths(repo, ["intended.txt"])

    assert staged_paths(repo) == set()


def test_apply_context_commits_only_patch_paths_and_preserves_other_state(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "intended.txt").write_text("from patch\n", encoding="utf-8")
    patch_text = _git(repo, "diff", "--", "intended.txt")
    _git(repo, "restore", "--", "intended.txt")

    (repo / "pre-staged.txt").write_text("pre-staged user change\n", encoding="utf-8")
    _git(repo, "add", "--", "pre-staged.txt")
    (repo / "unrelated.txt").write_text("unrelated user change\n", encoding="utf-8")
    (repo / ".env.local").write_text("TOKEN=untouched\n", encoding="utf-8")
    target = Repo(key="scoped-test", path=repo, display="scoped-test")

    with patch("panel.app.get_repo", return_value=target):
        apply_result, apply_status = apply_patch_action(
            ApplyPatchReq(repo=target.key, patch=patch_text)
        )
        commit_result, commit_status = commit_action(
            GitCommitReq(repo=target.key, message="commit patch scope")
        )

    assert apply_status == 200
    assert apply_result.files == ["intended.txt"]
    assert commit_status == 200
    assert commit_result.files == ["intended.txt"]
    assert staged_paths(repo) == {"pre-staged.txt"}
    status = _git(repo, "status", "--short")
    assert " M unrelated.txt" in status
    assert "?? .env.local" in status


def test_apply_rejects_escaping_patch_path_without_touching_outside_file(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside original\n", encoding="utf-8")
    malicious_patch = """diff --git a/../outside.txt b/../outside.txt
--- a/../outside.txt
+++ b/../outside.txt
@@ -1 +1 @@
-outside original
+compromised
"""
    target = Repo(key="escape-test", path=repo, display="escape-test")

    with patch("panel.app.get_repo", return_value=target):
        result, status = apply_patch_action(ApplyPatchReq(repo=target.key, patch=malicious_patch))

    assert status == 409
    assert result.error_kind == "unsafe_staging_scope"
    assert outside.read_text(encoding="utf-8") == "outside original\n"
