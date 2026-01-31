from __future__ import annotations

import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .runner import run


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
    correlation_id: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_git_audit(repo_key: str, repo_path: Path, correlation_id: str) -> AuditGit:
    cwd = str(repo_path)

    # 1. Facts Gathering

    # HEAD info
    head_sha_res = run(["git", "rev-parse", "HEAD"], cwd=repo_path)
    head_sha = head_sha_res.stdout.strip() if head_sha_res.code == 0 else None

    head_ref_res = run(["git", "symbolic-ref", "-q", "HEAD"], cwd=repo_path)
    head_ref = head_ref_res.stdout.strip() if head_ref_res.code == 0 else None
    is_detached_head = head_ref_res.code != 0

    local_branch = None
    if not is_detached_head and head_ref and head_ref.startswith("refs/heads/"):
        local_branch = head_ref[11:]

    # Remotes
    remotes_res = run(["git", "remote"], cwd=repo_path)
    remotes = [r for r in remotes_res.stdout.splitlines() if r.strip()]
    origin_present = "origin" in remotes

    # Fetch (non-blocking for audit, but we try)
    fetch_ok = False
    if origin_present:
        fetch_res = run(["git", "fetch", "origin", "--prune"], cwd=repo_path, timeout=30)
        fetch_ok = fetch_res.code == 0

    # Remote Refs
    origin_head_res = run(["git", "show-ref", "--verify", "--quiet", "refs/remotes/origin/HEAD"], cwd=repo_path)
    origin_head_exists = origin_head_res.code == 0

    origin_main_res = run(["git", "show-ref", "--verify", "--quiet", "refs/remotes/origin/main"], cwd=repo_path)
    origin_main_exists = origin_main_res.code == 0

    remote_default_branch = None
    if origin_head_exists:
        # Resolves refs/remotes/origin/HEAD -> refs/remotes/origin/main
        sym_res = run(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], cwd=repo_path)
        if sym_res.code == 0:
            remote_default_branch = sym_res.stdout.strip().replace("refs/remotes/", "")

    # Upstream
    upstream_info = None
    upstream_exists = False
    ahead = 0
    behind = 0

    if local_branch:
        upstream_res = run(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=repo_path)
        if upstream_res.code == 0:
            upstream_name = upstream_res.stdout.strip()
            upstream_exists = True
            upstream_info = {"name": upstream_name, "exists_locally": True}

            # Ahead/Behind
            ab_res = run(["git", "rev-list", "--left-right", "--count", f"{upstream_name}...HEAD"], cwd=repo_path)
            if ab_res.code == 0:
                parts = ab_res.stdout.split()
                if len(parts) >= 2:
                    behind, ahead = int(parts[0]), int(parts[1])

    # Working Tree
    # Staged
    staged_res = run(["git", "diff", "--cached", "--name-only"], cwd=repo_path)
    staged_count = len([l for l in staged_res.stdout.splitlines() if l.strip()])

    # Unstaged
    unstaged_res = run(["git", "diff", "--name-only"], cwd=repo_path)
    unstaged_count = len([l for l in unstaged_res.stdout.splitlines() if l.strip()])

    # Untracked
    untracked_res = run(["git", "ls-files", "--others", "--exclude-standard"], cwd=repo_path)
    untracked_count = len([l for l in untracked_res.stdout.splitlines() if l.strip()])

    is_clean = (staged_count == 0 and unstaged_count == 0 and untracked_count == 0)

    # 2. Checks & Logic

    checks = []
    routines = []

    # Check: Repo Present (Implicitly true if we are running here, but good to record)
    checks.append(AuditCheck(id="git.repo.present", status="ok", message="Repo is present."))

    # Check: Origin Present
    if origin_present:
        checks.append(AuditCheck(id="git.remote.origin.present", status="ok", message="Remote 'origin' is configured."))
    else:
        checks.append(AuditCheck(id="git.remote.origin.present", status="error", message="Remote 'origin' is missing."))

    # Check: Fetch
    if origin_present:
        if fetch_ok:
             checks.append(AuditCheck(id="git.fetch.ok", status="ok", message="Fetched remote refs successfully."))
        else:
             checks.append(AuditCheck(id="git.fetch.ok", status="warn", message="git fetch failed. Remote state may be stale."))

    # Check: Origin HEAD
    if origin_head_exists:
        checks.append(AuditCheck(id="git.remote_head.discoverable", status="ok", message=f"origin/HEAD is present ({remote_default_branch or 'unknown'})."))
    else:
        checks.append(AuditCheck(id="git.remote_head.discoverable", status="error", message="origin/HEAD is missing or dangling."))
        routines.append(SuggestedRoutine(
            id="git.repair.remote-head",
            risk="low",
            mutating=True,
            dry_run_supported=True,
            reason="origin/HEAD missing/dangling; restore remote head + refs.",
            requires=["git"]
        ))

    # Check: Origin Main (Reference check)
    if origin_main_exists:
        checks.append(AuditCheck(id="git.origin_main.present", status="ok", message="refs/remotes/origin/main exists."))
    else:
        checks.append(AuditCheck(id="git.origin_main.present", status="warn", message="refs/remotes/origin/main missing."))
        # Only suggest repair if we haven't already
        if not any(r.id == "git.repair.remote-head" for r in routines):
             routines.append(SuggestedRoutine(
                id="git.repair.remote-head",
                risk="low",
                mutating=True,
                dry_run_supported=True,
                reason="origin/main missing; likely remote head/ref tracking broken locally.",
                requires=["git"]
            ))

    # Overall Status
    overall_status: Literal["ok", "warn", "error"] = "ok"
    if any(c.status == "error" for c in checks):
        overall_status = "error"
    elif any(c.status == "warn" for c in checks):
        overall_status = "warn"

    # Uncertainty
    # If fetch failed or origin missing, uncertainty is high
    u_level = 0.1
    u_meta: Literal["productive", "avoidable", "systemic"] = "productive"
    u_causes = []

    if not origin_present or not fetch_ok:
        u_level = 0.35
        u_meta = "systemic"
        u_causes.append({"kind": "environment_variance", "note": "Remote or network/tooling state prevents reliable ref discovery."})
    else:
        u_causes.append({"kind": "remote_ref_inconsistency", "note": "Remote tracking refs may be incomplete or pruned unexpectedly."})

    # Construct Artifact
    return AuditGit(
        ts=now_iso(),
        repo=repo_key,
        cwd=cwd,
        status=overall_status,
        facts=AuditFacts(
            head_sha=head_sha,
            head_ref=head_ref,
            is_detached_head=is_detached_head,
            local_branch=local_branch,
            upstream=upstream_info,
            remotes=remotes,
            remote_default_branch=remote_default_branch,
            remote_refs={
                "origin_main": origin_main_exists,
                "origin_head": origin_head_exists,
                "origin_upstream": upstream_exists # approximation
            },
            working_tree={
                "is_clean": is_clean,
                "staged": staged_count,
                "unstaged": unstaged_count,
                "untracked": untracked_count
            },
            ahead_behind={
                "ahead": ahead,
                "behind": behind
            }
        ),
        checks=checks,
        uncertainty=Uncertainty(
            level=u_level,
            causes=u_causes,
            meta=u_meta
        ),
        suggested_routines=routines,
        correlation_id=correlation_id
    )
