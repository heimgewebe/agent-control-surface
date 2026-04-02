from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any, TypedDict

from .runner import run

# Maximum characters for combined stdout/stderr output truncation.
MAX_OUTPUT_CHARS = 50000

class GitRefError(TypedDict):
    error_kind: str
    hint: str
    affected_ref: str | None


def classify_git_ref_error(stderr: str) -> GitRefError | None:
    if not stderr:
        return None
    match = re.search(r"cannot lock ref '([^']+)'", stderr)
    if match:
        return {
            "error_kind": "ref_lock",
            "hint": "Unable to lock local ref; remote tracking refs may be inconsistent.",
            "affected_ref": match.group(1),
        }
    match = re.search(r"unable to resolve reference '([^']+)'", stderr)
    if match:
        return {
            "error_kind": "resolve_ref_failed",
            "hint": "Unable to resolve local ref; remote tracking refs may be inconsistent.",
            "affected_ref": match.group(1),
        }
    match = re.search(r"([A-Za-z0-9._/-]+) has become dangling", stderr)
    if match:
        return {
            "error_kind": "dangling_ref",
            "hint": "Local ref has become dangling; remote tracking refs may be inconsistent.",
            "affected_ref": match.group(1),
        }
    lowered = stderr.lower()
    if "packed refs" in lowered and "corrupt" in lowered:
        return {
            "error_kind": "ref_repair_failed",
            "hint": "Packed refs appear corrupt; repacking refs may be required.",
            "affected_ref": None,
        }
    return None


def format_command_line(cmd: list[str]) -> str:
    return f"$ {shlex.join(cmd)}"


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… (truncated)"


def combine_output(result: Any) -> str:
    output = result.stdout or ""
    if result.stderr:
        output = f"{output}\n{result.stderr}" if output else result.stderr
    return output


def is_valid_branch_name(name: str) -> bool:
    if not name or " " in name:
        return False
    if name.startswith("-") or name.endswith(".lock"):
        return False
    if ".." in name or "@" in name or "~" in name or ":" in name or "\\" in name:
        return False
    if "//" in name or "/." in name or "./" in name:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9._/-]+", name))


def run_git_command_sequence(
    path: Path,
    commands: list[list[str]],
    *,
    timeout: int,
    allow_failures: set[int] | None = None,
    allow_failure_cmds: set[tuple[str, ...]] | None = None,
    stop_on_error: bool = False,
) -> tuple[bool, str, str, int | None, list[str]]:
    """Run a list of commands, allowing optional failures by index (legacy) or command."""
    allow_failures = allow_failures or set()
    allow_failure_cmds = allow_failure_cmds or set()
    combined_stdout: list[str] = []
    combined_stderr: list[str] = []
    optional_failures: list[str] = []
    ok = True
    last_code: int | None = None
    for idx, cmd in enumerate(commands):
        result = run(cmd, cwd=path, timeout=timeout)
        last_code = result.code
        cmd_line = format_command_line(list(cmd))
        cmd_key = tuple(cmd)
        stdout_lines = [cmd_line]
        stdout_value = truncate_text(result.stdout.strip(), 20000) if result.stdout else ""
        if stdout_value:
            stdout_lines.append(stdout_value)
        combined_stdout.append("\n".join(stdout_lines))
        stderr_value = truncate_text(result.stderr.strip(), 20000) if result.stderr else ""
        if stderr_value:
            combined_stderr.append("\n".join([cmd_line, stderr_value]))
        if result.code != 0:
            if idx in allow_failures or cmd_key in allow_failure_cmds:
                optional_failures.append(cmd_line)
            else:
                ok = False
                if stop_on_error:
                    break
    stdout_combined = truncate_text("\n\n".join(combined_stdout), MAX_OUTPUT_CHARS)
    stderr_combined = truncate_text("\n\n".join(combined_stderr), MAX_OUTPUT_CHARS)
    return ok, stdout_combined, stderr_combined, last_code, optional_failures
