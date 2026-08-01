from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .runner import CmdResult, run

MAX_STAGING_PATHS = 512
MAX_STAGING_PATH_LENGTH = 4096
SECRET_BASENAMES = frozenset(
    {
        ".env",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials",
        "credentials.json",
        "id_dsa",
        "id_ed25519",
        "id_ecdsa",
        "id_rsa",
        "secrets.json",
    }
)
SECRET_SUFFIXES = (
    ".jks",
    ".key",
    ".keystore",
    ".p12",
    ".pfx",
    ".pem",
)
SECRET_DATA_SUFFIXES = frozenset(
    {"", ".cfg", ".conf", ".ini", ".json", ".toml", ".txt", ".yaml", ".yml"}
)
SECRET_NAME_RE = re.compile(
    r"(?:^|[._-])(?:api[_-]?key|credentials?|password|passwd|secrets?|tokens?)(?:$|[._-])",
    re.IGNORECASE,
)


class StagingError(ValueError):
    """The requested commit scope cannot be staged safely."""


@dataclass(frozen=True)
class StageResult:
    paths: tuple[str, ...]
    previously_staged: frozenset[str]
    command_result: CmdResult


@dataclass(frozen=True)
class CommitResult:
    paths: tuple[str, ...]
    stage_result: StageResult
    command_result: CmdResult


def canonicalize_repo_paths(
    repo_path: Path,
    paths: Iterable[str],
    *,
    reject_secret_like: bool = True,
) -> tuple[str, ...]:
    """Return unique literal repo-relative paths after fail-closed validation."""

    root = Path(repo_path).resolve(strict=True)
    raw_paths = list(paths)
    if not raw_paths:
        raise StagingError("An explicit non-empty path scope is required.")
    if len(raw_paths) > MAX_STAGING_PATHS:
        raise StagingError("The path scope is too large.")

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_path in raw_paths:
        if not isinstance(raw_path, str):
            raise StagingError("Every staged path must be a string.")
        if (
            not raw_path
            or len(raw_path) > MAX_STAGING_PATH_LENGTH
            or "\\" in raw_path
            or any(ord(char) < 32 or ord(char) == 127 for char in raw_path)
        ):
            raise StagingError("A staged path is malformed.")

        pure = PurePosixPath(raw_path)
        parts = pure.parts
        if pure.is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
            raise StagingError("A staged path escapes its repository scope.")
        if parts[0] == ".git":
            raise StagingError("Repository metadata cannot be staged.")

        relative = pure.as_posix()
        if relative != raw_path:
            raise StagingError("A staged path is not canonical.")
        if relative in seen:
            raise StagingError("Duplicate staged paths are not allowed.")
        if reject_secret_like and is_secret_like_path(relative):
            raise StagingError("Secret-like files cannot be staged by this operation.")

        current = root
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise StagingError("Symlinked staging paths are not allowed.")
        try:
            current.resolve(strict=False).relative_to(root)
        except (OSError, ValueError) as exc:
            raise StagingError("A staged path escapes its repository scope.") from exc
        if current.exists() and current.is_dir():
            raise StagingError("Directory staging scopes are not allowed.")

        seen.add(relative)
        normalized.append(relative)

    return tuple(sorted(normalized))


def is_secret_like_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    for part in parts:
        lowered = part.lower()
        if lowered in SECRET_BASENAMES or lowered.startswith(".env."):
            return True
        if lowered.endswith(SECRET_SUFFIXES):
            return True
        if Path(lowered).suffix in SECRET_DATA_SUFFIXES and SECRET_NAME_RE.search(lowered):
            return True
    return False


def changed_paths(repo_path: Path, paths: Iterable[str]) -> set[str]:
    canonical = canonicalize_repo_paths(repo_path, paths, reject_secret_like=False)
    commands = (
        [
            "git",
            "--literal-pathspecs",
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            "--",
            *canonical,
        ],
        [
            "git",
            "--literal-pathspecs",
            "diff",
            "--cached",
            "--name-only",
            "-z",
            "--no-renames",
            "--",
            *canonical,
        ],
        [
            "git",
            "--literal-pathspecs",
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            *canonical,
        ],
    )
    changed: set[str] = set()
    for command in commands:
        result = run(command, cwd=Path(repo_path), timeout=60)
        if result.code != 0:
            raise StagingError("Unable to inspect the requested path scope.")
        changed.update(_nul_paths(result.stdout))
    return changed


def staged_paths(repo_path: Path) -> set[str]:
    result = run(
        ["git", "diff", "--cached", "--name-only", "-z", "--no-renames"],
        cwd=Path(repo_path),
        timeout=30,
    )
    if result.code != 0:
        raise StagingError("Unable to inspect the staged path set.")
    return _nul_paths(result.stdout)


def stage_intended_paths(repo_path: Path, paths: Iterable[str]) -> StageResult:
    """Stage only literal intended files and verify the exact index delta."""

    root = Path(repo_path)
    canonical = canonicalize_repo_paths(root, paths)
    allowed = set(canonical)
    before = staged_paths(root)
    if before & allowed:
        raise StagingError("The operation scope already contains staged changes.")

    changed = changed_paths(root, canonical)
    if changed != allowed:
        raise StagingError("Every allowed path must have exactly one pending file change.")

    add = run(
        ["git", "--literal-pathspecs", "add", "--", *canonical],
        cwd=root,
        timeout=60,
    )
    if add.code != 0:
        raise StagingError("Explicit staging failed.")

    after = staged_paths(root)
    expected = before | allowed
    if after != expected:
        _unstage_paths(root, canonical)
        raise StagingError("The staged path set did not match the operation scope.")
    return StageResult(
        paths=canonical,
        previously_staged=frozenset(before),
        command_result=add,
    )


def commit_intended_paths(repo_path: Path, message: str, paths: Iterable[str]) -> CommitResult:
    """Commit only the verified scope and preserve unrelated index/worktree state."""

    root = Path(repo_path)
    staged = stage_intended_paths(root, paths)
    commit = run(
        [
            "git",
            "--literal-pathspecs",
            "commit",
            "--only",
            "-m",
            message,
            "--",
            *staged.paths,
        ],
        cwd=root,
        timeout=60,
    )
    if commit.code != 0:
        _unstage_paths(root, staged.paths)
        if staged_paths(root) != set(staged.previously_staged):
            raise StagingError("Unable to restore the index after a failed commit.")
        return CommitResult(paths=staged.paths, stage_result=staged, command_result=commit)

    committed = _committed_head_paths(root)
    remaining_staged = staged_paths(root)
    if committed != set(staged.paths) or remaining_staged != set(staged.previously_staged):
        raise StagingError("Post-commit path verification failed.")
    return CommitResult(paths=staged.paths, stage_result=staged, command_result=commit)


def paths_signature(repo_path: Path, paths: Iterable[str]) -> str:
    """Hash all tracked and untracked state in an explicit operation scope."""

    root = Path(repo_path)
    canonical = canonicalize_repo_paths(root, paths, reject_secret_like=False)
    digest = hashlib.sha256()
    for command in (
        [
            "git",
            "--literal-pathspecs",
            "diff",
            "--binary",
            "--no-ext-diff",
            "--no-renames",
            "--",
            *canonical,
        ],
        [
            "git",
            "--literal-pathspecs",
            "diff",
            "--cached",
            "--binary",
            "--no-ext-diff",
            "--no-renames",
            "--",
            *canonical,
        ],
    ):
        result = run(command, cwd=root, timeout=60)
        if result.code != 0:
            raise StagingError("Unable to fingerprint the operation scope.")
        digest.update(result.stdout.encode("utf-8", errors="surrogateescape"))

    untracked = run(
        [
            "git",
            "--literal-pathspecs",
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            *canonical,
        ],
        cwd=root,
        timeout=30,
    )
    if untracked.code != 0:
        raise StagingError("Unable to fingerprint untracked operation files.")
    for relative in sorted(_nul_paths(untracked.stdout)):
        hashed = run(["git", "hash-object", "--", relative], cwd=root, timeout=60)
        if hashed.code != 0:
            raise StagingError("Unable to fingerprint an operation file.")
        digest.update(relative.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(hashed.stdout.strip().encode("ascii"))
    return digest.hexdigest()


def _committed_head_paths(repo_path: Path) -> set[str]:
    result = run(
        [
            "git",
            "diff-tree",
            "--root",
            "--no-commit-id",
            "--name-only",
            "-r",
            "-z",
            "--no-renames",
            "HEAD",
        ],
        cwd=repo_path,
        timeout=30,
    )
    if result.code != 0:
        raise StagingError("Unable to verify the committed path set.")
    return _nul_paths(result.stdout)


def _unstage_paths(repo_path: Path, paths: tuple[str, ...]) -> None:
    run(
        ["git", "--literal-pathspecs", "reset", "--quiet", "--", *paths],
        cwd=repo_path,
        timeout=30,
    )


def _nul_paths(output: str) -> set[str]:
    return {path for path in output.split("\0") if path}
