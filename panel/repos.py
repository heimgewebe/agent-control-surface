from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Repo:
    key: str
    path: Path
    display: str


@lru_cache(maxsize=1)
def _load_repos() -> tuple[list[Repo], dict[str, Repo]]:
    base = Path.home() / "repos" / "heimgewebe"
    repos = [
        Repo(key="metarepo", path=base / "metarepo", display="heimgewebe/metarepo"),
        Repo(key="wgx", path=base / "wgx", display="heimgewebe/wgx"),
        Repo(key="sichter", path=base / "sichter", display="heimgewebe/sichter"),
    ]
    repo_map = {repo.key: repo for repo in repos}
    return repos, repo_map


def allowed_repos() -> list[Repo]:
    repos, _ = _load_repos()
    # Return a copy to prevent mutation of the cached list
    return list(repos)


def repo_by_key(key: str, repos: Iterable[Repo] | None = None) -> Repo:
    if repos is None:
        _, repo_map = _load_repos()
        try:
            return repo_map[key]
        except KeyError:
            raise KeyError(f"Repo not allowed: {key}") from None

    for repo in repos:
        if repo.key == key:
            return repo
    raise KeyError(f"Repo not allowed: {key}")
