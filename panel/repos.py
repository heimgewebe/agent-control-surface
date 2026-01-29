from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Repo:
    key: str
    path: Path
    display: str


_REPOS_CACHE: list[Repo] | None = None
_REPO_MAP: dict[str, Repo] | None = None


def _ensure_repos_loaded() -> tuple[list[Repo], dict[str, Repo]]:
    global _REPOS_CACHE, _REPO_MAP
    if _REPOS_CACHE is not None and _REPO_MAP is not None:
        return _REPOS_CACHE, _REPO_MAP

    base = Path.home() / "repos" / "heimgewebe"
    repos = [
        Repo(key="metarepo", path=base / "metarepo", display="heimgewebe/metarepo"),
        Repo(key="wgx", path=base / "wgx", display="heimgewebe/wgx"),
        Repo(key="sichter", path=base / "sichter", display="heimgewebe/sichter"),
    ]
    repo_map = {repo.key: repo for repo in repos}
    _REPOS_CACHE = repos
    _REPO_MAP = repo_map
    return repos, repo_map


def allowed_repos() -> list[Repo]:
    repos, _ = _ensure_repos_loaded()
    return list(repos)


def repo_by_key(key: str, repos: Iterable[Repo] | None = None) -> Repo:
    if repos is None:
        _, repo_map = _ensure_repos_loaded()
        if key in repo_map:
            return repo_map[key]
        raise KeyError(f"Repo not allowed: {key}")

    for repo in repos:
        if repo.key == key:
            return repo
    raise KeyError(f"Repo not allowed: {key}")
