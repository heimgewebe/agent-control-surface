from __future__ import annotations
import re
import shlex
from typing import Any, TypedDict

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
