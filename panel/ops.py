from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .logging import redact_secrets
from .runner import run

# ------------------------------------------------------------------------------
# Token Store (In-Memory)
# ------------------------------------------------------------------------------

TOKEN_STORE: dict[str, dict[str, Any]] = {}
TOKEN_TTL_SECONDS = 600  # 10 minutes
TOKEN_LOCK = threading.Lock()


def create_token(data: dict[str, Any]) -> str:
    token = str(uuid.uuid4())
    now = time.time()
    with TOKEN_LOCK:
        # Cleanup expired
        # Note: We perform a full scan to ensure correctness regardless of insertion order
        expired = [k for k, v in TOKEN_STORE.items() if now - v["created_at"] > TOKEN_TTL_SECONDS]
        for k in expired:
            del TOKEN_STORE[k]

        TOKEN_STORE[token] = {"created_at": now, "data": data}
    return token


def validate_and_consume_token(
    token: str, repo_key: str, routine_id: str, preview_hash: str | None = None
) -> bool:
    now = time.time()
    with TOKEN_LOCK:
        if token not in TOKEN_STORE:
            return False
        entry = TOKEN_STORE[token]

        # Cleanup if expired
        if now - entry["created_at"] > TOKEN_TTL_SECONDS:
            del TOKEN_STORE[token]
            return False

        data = entry["data"]
        # Mismatch -> delete token to prevent brute-forcing
        # Use 'repo_key' consistently
        if data.get("repo_key") != repo_key or data.get("routine_id") != routine_id:
            del TOKEN_STORE[token]
            return False

        # Check preview hash if available/required
        stored_hash = data.get("preview_hash")
        if stored_hash and preview_hash != stored_hash:
            del TOKEN_STORE[token]
            return False

        # Valid usage -> delete token (consume)
        del TOKEN_STORE[token]
        return True


# ------------------------------------------------------------------------------
# Models
# ------------------------------------------------------------------------------

class AuditFacts(BaseModel):
    model_config = ConfigDict(extra='ignore')
    head_sha: str | None
    head_ref: str | None
    is_detached_head: bool
    local_branch: str | None
    upstream: dict[str, Any] | None
    remotes: list[str]
    remote_default_branch: str | None
    remote_refs: dict[str, bool]
    working_tree: dict[str, int | bool]
    ahead_behind: dict[str, int]


class AuditCheck(BaseModel):
    model_config = ConfigDict(extra='ignore')
    id: str
    status: Literal["ok", "warn", "error"]
    message: str
    evidence: dict[str, Any] | None = None


class SuggestedRoutine(BaseModel):
    model_config = ConfigDict(extra='ignore')
    id: str
    risk: Literal["low", "medium", "high"]
    mutating: bool
    dry_run_supported: bool
    reason: str
    requires: list[str] = Field(default_factory=list)


class Uncertainty(BaseModel):
    model_config = ConfigDict(extra='ignore')
    level: float
    causes: list[dict[str, str]]
    meta: Literal["productive", "avoidable", "systemic"]


class AuditGit(BaseModel):
    model_config = ConfigDict(extra='ignore')
    kind: Literal["audit.git"] = "audit.git"
    schema_version: Literal["v1"] = "v1"
    ts: str
    repo: str
    cwd: str
    status: Literal["ok", "warn", "error"]
    facts: AuditFacts
    checks: list[AuditCheck]
    uncertainty: Uncertainty
    suggested_routines: list[SuggestedRoutine]
    correlation_id: str | None = None


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------

def extract_json_from_stdout(stdout: str) -> Any | None:
    """Find and parse the first valid JSON object/array embedded in noisy stdout."""
    s = stdout.strip()
    if not s:
        return None

    # 1) Fast path: whole stdout is JSON
    try:
        return json.loads(s)
    except Exception:
        pass

    # 2) Balanced scanner to find embedded JSON
    def find_balanced(start_ch: str, end_ch: str) -> Any | None:
        starts = [i for i, ch in enumerate(s) if ch == start_ch]
        # Cap attempts to prevent excessive CPU on massive logs
        for start in starts[:50]:
            depth = 0
            in_str = False
            esc = False
            for i in range(start, len(s)):
                ch = s[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                else:
                    if ch == '"':
                        in_str = True
                        continue
                    if ch == start_ch:
                        depth += 1
                    elif ch == end_ch:
                        depth -= 1
                        if depth == 0:
                            candidate = s[start : i + 1]
                            try:
                                return json.loads(candidate)
                            except Exception:
                                break  # matched brackets but invalid JSON? try next start
            # If loop finishes without depth==0, this start was unmatched
        return None

    # Prefer object, then array
    obj = find_balanced("{", "}")
    if obj is not None:
        return obj
    arr = find_balanced("[", "]")
    if arr is not None:
        return arr

    return None


def _resolve_existing(path: Path, base_path: Path) -> Path | None:
    """Safely resolves a path relative to base_path, preventing traversal."""
    try:
        base_abs = base_path.resolve()
        if path.is_absolute():
            resolved = path.resolve()
        else:
            resolved = (base_abs / path).resolve()

        if resolved.is_relative_to(base_abs):
            return resolved if resolved.exists() else None
    except (ValueError, OSError):
        pass
    return None


def extract_path_from_stdout(stdout: str, base_path: Path) -> Path | None:
    """Attempts to find a valid file path in stdout (e.g., ending in .json)."""
    stripped = stdout.strip()

    # Check if the whole line is a path
    if stripped.endswith(".json"):
        resolved = _resolve_existing(Path(stripped), base_path)
        if resolved:
            return resolved

    # Look for tokens ending in .json
    tokens = re.split(r'\s+', stdout)
    for token in tokens:
        if token.endswith(".json"):
            resolved = _resolve_existing(Path(token), base_path)
            if resolved:
                return resolved

    return None


# ------------------------------------------------------------------------------
# Operations (WGX Wrappers)
# ------------------------------------------------------------------------------

def _run_wgx_command(
    cmd: list[str],
    cwd: Path,
    timeout: int,
    fallback_paths: list[Path] | None = None,
    try_stdout_json: bool = True,
    try_stdout_path: bool = True,
) -> tuple[Any, int, str]:
    """
    Common wrapper for executing wgx commands and parsing their JSON output.
    Returns (data, exit_code, details).
    """
    res = run(cmd, cwd=cwd, timeout=timeout)
    output = res.stdout.strip()

    # Create rich diagnostic details from both streams
    stdout_snip = output[:200].replace("\n", "\\n")
    stderr_snip = (res.stderr or "").strip().replace("\n", "\\n")[:200]
    details = redact_secrets(f"stdout='{stdout_snip}' stderr='{stderr_snip}'")

    data = None
    if try_stdout_json:
        data = extract_json_from_stdout(output)

    if data is None and try_stdout_path:
        path_candidate = extract_path_from_stdout(output, cwd)
        if path_candidate:
            try:
                with open(path_candidate, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                pass

    if data is None and fallback_paths:
        for p in fallback_paths:
            if p.exists():
                try:
                    with open(p, encoding="utf-8") as f:
                        data = json.load(f)
                        if data is not None:
                            break
                except (OSError, json.JSONDecodeError):
                    continue

    if data is not None:
        return data, res.code, details

    # Failure handling
    if res.code != 0:
        raise RuntimeError(
            f"WGX command failed (code {res.code}) and no JSON output found: {details}"
        )

    raise RuntimeError(f"Could not locate valid JSON output from wgx. {details}")


def run_wgx_audit_git(
    repo_key: str, repo_path: Path, correlation_id: str, stdout_json: bool = False
) -> AuditGit:
    """
    Executes `wgx audit git --repo ...` via the runner.
    Parses the output (JSON path or JSON) and returns a validated AuditGit object.
    """
    cmd = ["wgx", "audit", "git", "--repo", repo_key, "--correlation-id", correlation_id]
    if stdout_json:
        cmd.append("--stdout-json")

    fallback_paths = [
        repo_path / ".wgx" / "out" / f"audit.git.v1.{correlation_id}.json",
        repo_path / ".wgx" / "out" / "audit.git.v1.json",
    ]

    audit_data, exit_code, _ = _run_wgx_command(
        cmd=cmd,
        cwd=repo_path,
        timeout=60,
        fallback_paths=None if stdout_json else fallback_paths,
        try_stdout_json=True,
        try_stdout_path=True,
    )

    # Validate with Pydantic
    try:
        if isinstance(audit_data, dict):
            audit_data["_exit_code"] = exit_code
        audit = AuditGit.model_validate(audit_data)
        # Force correlation_id to match the request for consistent tracking
        audit.correlation_id = correlation_id
        return audit
    except Exception as e:
        raise RuntimeError(f"Audit artifact validation failed: {e}")


def get_latest_audit_artifact(repo_path: Path, repo_key: str | None = None) -> AuditGit | None:
    """
    Scans .wgx/out/ for the most recent audit.git.v1.*.json artifact.
    Prioritizes specific correlation-id files over the generic copy if both exist.
    Optional: filters by repo key found inside the artifact.
    """
    out_dir = repo_path / ".wgx" / "out"
    if not out_dir.exists():
        return None

    candidates = []
    try:
        with os.scandir(out_dir) as it:
            for entry in it:
                if entry.name.startswith("audit.git.v1") and entry.name.endswith(".json"):
                    try:
                        if entry.is_file():
                            candidates.append(entry)
                    except OSError:
                        continue
    except (FileNotFoundError, NotADirectoryError):
        return None

    if not candidates:
        return None

    # Sort by modification time descending
    # Use cached stat from DirEntry; handle potential race condition if file deleted
    def safe_mtime(entry: os.DirEntry[str]) -> float:
        try:
            return entry.stat().st_mtime
        except OSError:
            return 0.0

    candidates.sort(key=safe_mtime, reverse=True)

    generic_name = "audit.git.v1.json"
    specific = [c for c in candidates if c.name != generic_name]
    generic = [c for c in candidates if c.name == generic_name]

    # Check specifics first
    for cand in specific:
        try:
            with open(cand.path, encoding="utf-8") as f:
                data = json.load(f)
                if repo_key and data.get("repo") != repo_key:
                    continue
                return AuditGit.model_validate(data)
        except Exception:
            continue

    # Then generic
    for cand in generic:
        try:
            with open(cand.path, encoding="utf-8") as f:
                data = json.load(f)
                if repo_key and data.get("repo") != repo_key:
                    continue
                return AuditGit.model_validate(data)
        except Exception:
            continue

    return None


def run_wgx_routine_preview(
    repo_key: str, repo_path: Path, routine_id: str
) -> tuple[dict[str, Any], str, str]:
    """
    Runs `wgx routine <id> preview`.
    Returns (preview_json, confirm_token, preview_hash).
    """
    # Removed --stdout-json assumption to align with likely CLI contract
    cmd = ["wgx", "routine", routine_id, "preview"]

    preview_data, exit_code, _ = _run_wgx_command(
        cmd=cmd,
        cwd=repo_path,
        timeout=60,
        fallback_paths=[repo_path / ".wgx/out/routine.preview.json"],
    )

    if isinstance(preview_data, dict):
        preview_data["_exit_code"] = exit_code

    # Calculate canonical hash of the preview content
    canonical = json.dumps(preview_data, sort_keys=True, separators=(",", ":"))
    preview_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    token = create_token(
        {"repo_key": repo_key, "routine_id": routine_id, "preview_hash": preview_hash}
    )
    return preview_data, token, preview_hash


def run_wgx_routine_apply(
    repo_key: str, repo_path: Path, routine_id: str, token: str, preview_hash: str
) -> dict[str, Any]:
    """
    Validates token, runs `wgx routine <id> apply`.
    Returns result json.
    """
    if not validate_and_consume_token(token, repo_key, routine_id, preview_hash):
        raise HTTPException(
            status_code=403, detail="Invalid, expired, or mismatched confirmation token."
        )

    # Removed --stdout-json assumption
    cmd = ["wgx", "routine", routine_id, "apply"]

    result_data, exit_code, details = _run_wgx_command(
        cmd=cmd,
        cwd=repo_path,
        timeout=300,
        fallback_paths=[repo_path / ".wgx/out/routine.result.json"],
    )

    if isinstance(result_data, dict):
        result_data["_exit_code"] = exit_code

    # Semantics check: non-zero exit but valid JSON?
    if exit_code != 0:
        if isinstance(result_data, dict) and "ok" in result_data:
            # Tolerable: CLI returned error code but also structured result
            pass
        else:
            # Fatal: CLI failed and JSON doesn't look like a standard result (no 'ok' field)
            raise RuntimeError(
                f"Routine apply failed (code {exit_code}) and JSON "
                f"result lacks 'ok' field: {details}"
            )

    return result_data
