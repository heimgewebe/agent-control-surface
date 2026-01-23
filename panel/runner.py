from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


@dataclass
class CmdResult:
    code: int
    stdout: str
    stderr: str
    cmd: list[str]


def run(
    cmd: Sequence[str],
    cwd: Path,
    timeout: int = 60,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
) -> CmdResult:
    result = subprocess.run(
        list(cmd),
        cwd=str(cwd),
        text=True,
        input=input_text,
        capture_output=True,
        timeout=timeout,
        check=False,
        env=None if env is None else dict(env),
    )
    return CmdResult(
        code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        cmd=list(cmd),
    )


def assert_not_main_branch(repo_dir: Path) -> None:
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir).stdout.strip()
    if branch in {"main", "master"}:
        raise RuntimeError("Refusing to operate on main/master. Create a branch first.")
