from __future__ import annotations

import json
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
        expired = [k for k, v in TOKEN_STORE.items() if now - v["created_at"] > TOKEN_TTL_SECONDS]
        for k in expired:
            del TOKEN_STORE[k]

        TOKEN_STORE[token] = {"created_at": now, "data": data}
    return token


def validate_and_consume_token(token: str, repo: str, routine_id: str) -> bool:
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
        if data.get("repo") != repo or data.get("routine_id") != routine_id:
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

    # Even if res.code != 0, we attempt to read the artifact or stdout,
    # because WGX might report "partial failure" (e.g. check failed) as structured JSON.
    output = res.stdout.strip()

    # WGX might return the path to the JSON file, or the JSON itself.

    audit_data = None
    json_path = None

    # If --stdout-json is requested, we assume stdout is the JSON.
    if stdout_json:
        try:
            audit_data = json.loads(output)
        except json.JSONDecodeError:
            # Robust parsing fallback: find first { and last }
            start = output.find("{")
            end = output.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    audit_data = json.loads(output[start : end + 1])
                except json.JSONDecodeError:
                    pass

            if audit_data is None:
                if res.code != 0:
                    raise RuntimeError(f"WGX audit failed (code {res.code}) and stdout is not valid JSON: {res.stderr or output[:200]}")
                raise RuntimeError(f"WGX audit returned invalid JSON on stdout: {output[:200]}")
    else:
        # File artifact mode
        json_path = Path(output) if output.endswith(".json") else None

        if json_path and (repo_path / json_path).exists():
            # It's a relative path in the repo
            try:
                with open(repo_path / json_path, "r", encoding="utf-8") as f:
                    audit_data = json.load(f)
            except Exception as e:
                raise RuntimeError(f"Failed to read audit artifact at {json_path}: {e}")
        elif json_path and Path(output).exists():
             # Absolute path? Unlikely but possible
            try:
                with open(Path(output), "r", encoding="utf-8") as f:
                    audit_data = json.load(f)
            except Exception as e:
                raise RuntimeError(f"Failed to read audit artifact at {output}: {e}")
        else:
            # Fallback: check default location .wgx/out/audit.git.v1.json
            # Or try parsing output as JSON if it's not a path (fallback behavior)
            try:
                audit_data = json.loads(output)
            except json.JSONDecodeError:
                default_path = repo_path / ".wgx/out/audit.git.v1.json"
                if default_path.exists():
                    try:
                        with open(default_path, "r", encoding="utf-8") as f:
                            audit_data = json.load(f)
                    except Exception as e:
                        raise RuntimeError(f"Failed to read default audit artifact: {e}")
                else:
                    # Only raise if we really have no JSON and exit code was error
                    if res.code != 0:
                        raise RuntimeError(f"WGX audit failed (code {res.code}) and no JSON artifact found: {res.stderr or output[:200]}")
                    raise RuntimeError(f"Could not locate valid JSON output from wgx. Stdout: {output[:200]}")

    # Validate with Pydantic
    try:
        # Override correlation_id with ours if needed, or trust theirs?
        # The contract says we pass correlation_id potentially? WGX might generate its own.
        # Let's ensure repo matches.
        audit = AuditGit.model_validate(audit_data)
        # We can update correlation_id to match the job one if we want consistency,
        # but let's respect what WGX produced if it did.
        if not audit.correlation_id:
             audit.correlation_id = correlation_id
        return audit
    except Exception as e:
        raise RuntimeError(f"Audit artifact validation failed: {e}")


def get_latest_audit_artifact(repo_path: Path) -> AuditGit | None:
    """
    Scans .wgx/out/ for the most recent audit.git.v1.*.json artifact.
    Prioritizes specific correlation-id files over the generic copy if both exist.
    """
    out_dir = repo_path / ".wgx" / "out"
    if not out_dir.exists():
        return None

    # Pattern: audit.git.v1*.json to catch audit.git.v1.json and audit.git.v1.<correlation_id>.json
    candidates = list(out_dir.glob("audit.git.v1*.json"))
    if not candidates:
        return None

    # Sort by modification time descending
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    # Prefer non-generic names (i.e. those with a correlation ID suffix)
    # The generic name is usually 'audit.git.v1.json'
    generic_name = "audit.git.v1.json"

    # Try to find a non-generic candidate first, even if slightly older?
    # Or just return the newest valid one.
    # The prompt suggests: "Priorisiere correlation-spezifische Dateien vor dem 'latest pointer'"
    # So we split candidates.

    specific = [c for c in candidates if c.name != generic_name]
    generic = [c for c in candidates if c.name == generic_name]

    # Check specifics first (already sorted by time)
    for cand in specific:
        try:
            with open(cand, "r", encoding="utf-8") as f:
                data = json.load(f)
                return AuditGit.model_validate(data)
        except Exception:
            continue

    # Then generic
    for cand in generic:
        try:
            with open(cand, "r", encoding="utf-8") as f:
                data = json.load(f)
                return AuditGit.model_validate(data)
        except Exception:
            continue

    return None


def run_wgx_routine_preview(repo_key: str, repo_path: Path, routine_id: str) -> tuple[dict[str, Any], str]:
    """
    Runs `wgx routine <id> preview` (or similar).
    Returns (preview_json, confirm_token).
    """
    # CLI syntax assumed: wgx routine <id> preview
    cmd = ["wgx", "routine", routine_id, "preview"]

    res = run(cmd, cwd=repo_path, timeout=60)

    if res.code != 0:
        raise RuntimeError(f"Routine preview failed: {res.stderr or res.stdout}")

    output = res.stdout.strip()
    # Logic similar to audit: read file or json
    json_path = Path(output) if output.endswith(".json") else None
    preview_data = None

    if json_path and (repo_path / json_path).exists():
        with open(repo_path / json_path, "r", encoding="utf-8") as f:
            preview_data = json.load(f)
    elif json_path and Path(output).exists():
        with open(Path(output), "r", encoding="utf-8") as f:
            preview_data = json.load(f)
    else:
        try:
            preview_data = json.loads(output)
        except json.JSONDecodeError:
             # Fallback default path?
            default_path = repo_path / ".wgx/out/routine.preview.json"
            if default_path.exists():
                with open(default_path, "r", encoding="utf-8") as f:
                    preview_data = json.load(f)
            else:
                raise RuntimeError("Could not parse routine preview output.")

    # Create Token
    token = create_token({"repo": repo_key, "routine_id": routine_id})

    return preview_data, token


def run_wgx_routine_apply(repo_key: str, repo_path: Path, routine_id: str, token: str) -> dict[str, Any]:
    """
    Validates token, runs `wgx routine <id> apply`.
    Returns result json.
    """
    if not validate_and_consume_token(token, repo_key, routine_id):
        raise HTTPException(status_code=403, detail="Invalid or expired confirmation token.")

    cmd = ["wgx", "routine", routine_id, "apply"]

    res = run(cmd, cwd=repo_path, timeout=300) # Apply might take longer

    if res.code != 0:
        # Even if failed, we try to get the result artifact if it exists
        pass

    output = res.stdout.strip()
    json_path = Path(output) if output.endswith(".json") else None
    result_data = None

    if json_path and (repo_path / json_path).exists():
        with open(repo_path / json_path, "r", encoding="utf-8") as f:
            result_data = json.load(f)
    elif json_path and Path(output).exists():
        with open(Path(output), "r", encoding="utf-8") as f:
            result_data = json.load(f)
    else:
        try:
            result_data = json.loads(output)
        except json.JSONDecodeError:
            default_path = repo_path / ".wgx/out/routine.result.json"
            if default_path.exists():
                with open(default_path, "r", encoding="utf-8") as f:
                    result_data = json.load(f)
            else:
                # If everything fails, return raw output wrapper
                if res.code != 0:
                     raise RuntimeError(f"Routine apply failed and no JSON output found: {res.stderr}")
                else:
                     raise RuntimeError(f"Routine apply succeeded (exit 0) but no JSON output found: {output}")

    return result_data
