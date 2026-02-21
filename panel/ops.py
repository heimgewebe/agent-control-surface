from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, ConfigDict

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


def validate_and_consume_token(token: str, repo_key: str, routine_id: str, preview_hash: str | None = None) -> bool:
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
    return datetime.now(timezone.utc).isoformat()


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
    try:
        if path.is_absolute():
            return path if path.exists() else None
        resolved = base_path / path
        return resolved if resolved.exists() else None
    except OSError:
        # Handles "File name too long" or other OS-level issues for invalid/massive tokens
        return None


def extract_path_from_stdout(stdout: str, base_path: Path) -> Path | None:
    """Attempts to find a valid file path in stdout (e.g., ending in .json)."""
    stripped = stdout.strip()

    # Check if the whole line is a path
    if 0 < len(stripped) < 4096 and stripped.endswith(".json"):
        resolved = _resolve_existing(Path(stripped), base_path)
        if resolved:
            return resolved

    # Look for tokens ending in .json using finditer for memory efficiency
    # \S* matches 0 or more non-whitespace chars, ensuring we match even just ".json"
    for match in re.finditer(r'\S*\.json(?!\S)', stdout):
        token = match.group(0)
        resolved = _resolve_existing(Path(token), base_path)
        if resolved:
            return resolved

    return None


# ------------------------------------------------------------------------------
# Operations (WGX Wrappers)
# ------------------------------------------------------------------------------

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

    res = run(cmd, cwd=repo_path, timeout=60)

    output = res.stdout.strip()
    audit_data = None

    if stdout_json:
        audit_data = extract_json_from_stdout(output)

        if audit_data:
            # If we got JSON, accept it even if exit code is non-zero (diagnostic info)
            if isinstance(audit_data, dict):
                audit_data["_exit_code"] = res.code
        else:
            if res.code != 0:
                detail = res.stderr or output[:200]
                raise RuntimeError(f"WGX audit failed (code {res.code}) and stdout contains no valid JSON: {detail}")
            raise RuntimeError(f"WGX audit returned invalid JSON on stdout: {output[:200]}")
    else:
        # File artifact mode (default) - STRICTER fallback logic
        # 1. Try to find path in output
        target_file = extract_path_from_stdout(output, repo_path)

        if target_file:
            try:
                with open(target_file, "r", encoding="utf-8") as f:
                    audit_data = json.load(f)
            except Exception as e:
                raise RuntimeError(f"Failed to read audit artifact at {target_file}: {e}")
        else:
            # 2. Check canonical default location or correlation-specific file
            default_path = repo_path / ".wgx" / "out" / "audit.git.v1.json"
            specific_path = repo_path / ".wgx" / "out" / f"audit.git.v1.{correlation_id}.json"

            # Prefer specific artifact to avoid reading stale generic file
            path_to_read = specific_path if specific_path.exists() else (default_path if default_path.exists() else None)

            if path_to_read:
                try:
                    with open(path_to_read, "r", encoding="utf-8") as f:
                        audit_data = json.load(f)
                except Exception as e:
                    raise RuntimeError(f"Failed to read audit artifact at {path_to_read}: {e}")
            else:
                if res.code != 0:
                    detail = res.stderr or output[:200]
                    raise RuntimeError(f"WGX audit failed (code {res.code}) and no JSON artifact found: {detail}")
                raise RuntimeError(f"Could not locate valid JSON output from wgx. Stdout: {output[:200]}")

    # Validate with Pydantic
    try:
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
            with open(cand.path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if repo_key and data.get("repo") != repo_key:
                    continue
                return AuditGit.model_validate(data)
        except Exception:
            continue

    # Then generic
    for cand in generic:
        try:
            with open(cand.path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if repo_key and data.get("repo") != repo_key:
                    continue
                return AuditGit.model_validate(data)
        except Exception:
            continue

    return None


def run_wgx_routine_preview(repo_key: str, repo_path: Path, routine_id: str) -> tuple[dict[str, Any], str, str]:
    """
    Runs `wgx routine <id> preview`.
    Returns (preview_json, confirm_token, preview_hash).
    """
    # Removed --stdout-json assumption to align with likely CLI contract
    cmd = ["wgx", "routine", routine_id, "preview"]

    res = run(cmd, cwd=repo_path, timeout=60)

    output = res.stdout.strip()
    # Try stdout extraction first as some routines might output JSON
    preview_data = extract_json_from_stdout(output)

    # If we got JSON, accept it even if exit code is non-zero
    if preview_data:
        if isinstance(preview_data, dict):
            preview_data["_exit_code"] = res.code

    if preview_data is None:
        if res.code != 0:
             raise RuntimeError(f"Routine preview failed: {res.stderr or output[:200]}")

        # Fallback to checking default file if CLI didn't output JSON
        # Strategy: check if stdout is a path, otherwise try default location
        path_candidate = extract_path_from_stdout(output, repo_path)
        if path_candidate:
             try:
                with open(path_candidate, "r", encoding="utf-8") as f:
                    preview_data = json.load(f)
             except Exception:
                pass

        if preview_data is None:
            default_path = repo_path / ".wgx/out/routine.preview.json"
            if default_path.exists():
                try:
                    with open(default_path, "r", encoding="utf-8") as f:
                        preview_data = json.load(f)
                except Exception:
                    pass

        if preview_data is None:
            detail = res.stderr or output[:200]
            raise RuntimeError(f"Could not parse routine preview output: {detail}")

    # Calculate canonical hash of the preview content
    canonical = json.dumps(preview_data, sort_keys=True, separators=(",", ":"))
    preview_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    token = create_token({"repo_key": repo_key, "routine_id": routine_id, "preview_hash": preview_hash})
    return preview_data, token, preview_hash


def run_wgx_routine_apply(repo_key: str, repo_path: Path, routine_id: str, token: str, preview_hash: str) -> dict[str, Any]:
    """
    Validates token, runs `wgx routine <id> apply`.
    Returns result json.
    """
    if not validate_and_consume_token(token, repo_key, routine_id, preview_hash):
        raise HTTPException(status_code=403, detail="Invalid, expired, or mismatched confirmation token.")

    # Removed --stdout-json assumption
    cmd = ["wgx", "routine", routine_id, "apply"]

    res = run(cmd, cwd=repo_path, timeout=300)

    output = res.stdout.strip()
    result_data = extract_json_from_stdout(output)

    if result_data is None:
        # Fallback logic
        path_candidate = extract_path_from_stdout(output, repo_path)
        if path_candidate:
             try:
                with open(path_candidate, "r", encoding="utf-8") as f:
                    result_data = json.load(f)
             except Exception:
                pass

        if result_data is None:
            default_path = repo_path / ".wgx/out/routine.result.json"
            if default_path.exists():
                try:
                    with open(default_path, "r", encoding="utf-8") as f:
                        result_data = json.load(f)
                except Exception:
                    pass

    if result_data:
        if isinstance(result_data, dict):
            result_data["_exit_code"] = res.code

    if result_data is None:
        if res.code != 0:
             raise RuntimeError(f"Routine apply failed and no JSON output found: {res.stderr or output[:200]}")
        else:
             raise RuntimeError(f"Routine apply succeeded (exit 0) but no JSON output found: {output[:200]}")

    # Semantics check: non-zero exit but valid JSON?
    if res.code != 0:
        if "ok" in result_data:
            # Tolerable: CLI returned error code but also structured result (e.g. partial success or structured failure)
            pass
        else:
            # Fatal: CLI failed and JSON doesn't look like a standard result (no 'ok' field)
            detail = res.stderr or output[:200]
            raise RuntimeError(f"Routine apply failed (code {res.code}) and JSON result lacks 'ok' field: {detail}")

    return result_data
