from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Repo:
    key: str
    path: Path
    display: str


def allowed_repos() -> list[Repo]:
    base = Path.home() / "repos" / "heimgewebe"
    return [
        Repo(key="metarepo", path=base / "metarepo", display="heimgewebe/metarepo"),
        Repo(key="wgx", path=base / "wgx", display="heimgewebe/wgx"),
        Repo(key="sichter", path=base / "sichter", display="heimgewebe/sichter"),
    ]


def repo_by_key(key: str, repos: Iterable[Repo] | None = None) -> Repo:
    for repo in repos or allowed_repos():
        if repo.key == key:
            return repo
    raise KeyError(f"Repo not allowed: {key}")
