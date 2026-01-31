from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

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
        if now - entry["created_at"] > TOKEN_TTL_SECONDS:
            del TOKEN_STORE[token]
            return False

        data = entry["data"]
        if data.get("repo") != repo or data.get("routine_id") != routine_id:
            return False

        del TOKEN_STORE[token]
        return True


# ------------------------------------------------------------------------------
# Models
# ------------------------------------------------------------------------------

class AuditFacts(BaseModel):
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
    id: str
    status: Literal["ok", "warn", "error"]
    message: str
    evidence: dict[str, Any] | None = None


class SuggestedRoutine(BaseModel):
    id: str
    risk: Literal["low", "medium", "high"]
    mutating: bool
    dry_run_supported: bool
    reason: str
    requires: list[str] = Field(default_factory=list)


class Uncertainty(BaseModel):
    level: float
    causes: list[dict[str, str]]
    meta: Literal["productive", "avoidable", "systemic"]


class AuditGit(BaseModel):
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

def run_wgx_audit_git(repo_key: str, repo_path: Path, correlation_id: str) -> AuditGit:
    """
    Executes `wgx audit git --repo ...` via the runner.
    Parses the output (JSON path or JSON) and returns a validated AuditGit object.
    """
    cmd = ["wgx", "audit", "git", "--repo", repo_key, "--correlation-id", correlation_id]

    res = run(cmd, cwd=repo_path, timeout=60)

    if res.code != 0:
        # If wgx fails, we try to parse stdout/stderr to see if it emitted a JSON error
        # But mostly we'll just raise or return a synthetic error.
        raise RuntimeError(f"WGX audit failed (code {res.code}): {res.stderr or res.stdout}")

    output = res.stdout.strip()

    # WGX might return the path to the JSON file, or the JSON itself.
    # The blueprint says: "|--> audit.git.json" and "writes artifact".
    # It also says: "run(['wgx','audit','git','--json', ...]) ... liest den zurückgegebenen Pfad (stdout) oder .wgx/out/audit.git.v1.json"

    json_path = Path(output) if output.endswith(".json") else None

    audit_data = None

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
        # Maybe stdout IS the JSON?
        try:
            audit_data = json.loads(output)
        except json.JSONDecodeError:
            # Fallback: check default location .wgx/out/audit.git.v1.json
            default_path = repo_path / ".wgx/out/audit.git.v1.json"
            if default_path.exists():
                try:
                    with open(default_path, "r", encoding="utf-8") as f:
                        audit_data = json.load(f)
                except Exception as e:
                    raise RuntimeError(f"Failed to read default audit artifact: {e}")
            else:
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
