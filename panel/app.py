from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.requests import Request

from .logging import log_action, redact_secrets
from .repos import Repo, allowed_repos, repo_by_key
from .runner import assert_not_main_branch, run

app = FastAPI(title="agent-control-surface")
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))
JOB_EXECUTOR = ThreadPoolExecutor(max_workers=2)
JOB_LOCK = threading.Lock()
JOBS: dict[str, "JobState"] = {}
JOB_CREATED_AT: dict[str, float] = {}
JOB_MAX_AGE_SECONDS = 24 * 60 * 60
JOB_MAX_ENTRIES = 200
MAX_JOB_LOG_LINES = 1000
MAX_LOG_LINE_CHARS = 4000
MAX_STDOUT_CHARS = 50000
LAST_APPLY_CONTEXT: dict[str, dict[str, str]] = {}
BRANCH_HEAD_PREFIX = "# branch.head "
BRANCH_OID_PREFIX = "# branch.oid "


class JobState(BaseModel):
    job_id: str
    status: str
    results: list["ActionResult"] = Field(default_factory=list)
    log_lines: list[str] = Field(default_factory=list)


class NewSessionReq(BaseModel):
    repo: str
    title: str


class ApplyPatchReq(BaseModel):
    repo: str
    patch: str
    three_way: bool = False
    session_id: str | None = None
    source: str | None = None


class GitBranchReq(BaseModel):
    repo: str
    name: str


class GitCommitReq(BaseModel):
    repo: str
    message: str


class GitPushReq(BaseModel):
    repo: str


class ActionResult(BaseModel):
    ok: bool
    action: str
    repo: str
    branch: str | None = None
    head: str | None = None
    changed: bool | None = None
    files: list[str] | None = None
    pr_url: str | None = None
    stdout: str = ""
    stderr: str = ""
    code: int | None = None
    error_kind: str | None = None
    message: str | None = None
    ts: str
    duration_ms: int | None = None
    correlation_id: str


class PublishOptions(BaseModel):
    branch: str | None = None
    commit_message: str | None = None
    pr_title: str | None = None
    pr_body: str | None = None
    base: str = "main"
    draft: bool = True
    include_diffstat: bool = True


class PublishReq(PublishOptions):
    """Backwards-compatible alias for legacy imports."""




class PublishJobResponse(BaseModel):
    job_id: str
    correlation_id: str


class JobStatusResponse(BaseModel):
    status: str
    results: list[ActionResult]
    log_tail: str


# Fix forward references for JobState (which uses ActionResult)
JobState.model_rebuild()


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        "index.html",
        {
            "request": request,
            "repos": allowed_repos(),
        },
    )


@app.get("/api/sessions", response_class=PlainTextResponse)
def api_sessions(repo: str = Query(...)) -> str:
    target = get_repo(repo)
    out = run(["jules", "remote", "list", "--session"], cwd=target.path, timeout=30)
    return combine_output(out)


@app.post("/api/sessions/new", response_class=PlainTextResponse)
def api_sessions_new(req: NewSessionReq) -> str:
    target = get_repo(req.repo)
    out = run(["jules", "new", req.title], cwd=target.path, timeout=60)
    return combine_output(out)


@app.get("/api/sessions/{session_id}/diff", response_class=PlainTextResponse)
def api_session_diff(session_id: str, repo: str = Query(...)) -> str:
    target = get_repo(repo)
    # Jules: `remote pull` prints the patch to stdout. Without `--apply` this is a safe preview.
    out = run(["jules", "remote", "pull", "--session", session_id], cwd=target.path, timeout=180)
    if out.code != 0:
        raise HTTPException(status_code=500, detail=combine_output(out))
    txt = normalize_patch_output(combine_output(out))
    if not txt.strip():
        raise HTTPException(status_code=404, detail="No patch returned for this session.")
    return txt


@app.get("/api/sessions/{session_id}/diff/download", response_class=PlainTextResponse)
def api_session_diff_download(session_id: str, repo: str = Query(...)) -> PlainTextResponse:
    diff_text = api_session_diff(session_id, repo)
    filename = f"jules-session-{session_id}.diff"
    return PlainTextResponse(
        diff_text,
        headers={"Content-Disposition": f"attachment; filename=\"{filename}\""},
    )


@app.post("/api/patch/apply")
def api_patch_apply(req: ApplyPatchReq, response_format: str = Query("text")) -> Response:
    result, status_code = apply_patch_action(req)
    return build_action_response(result, response_format, status_code=status_code)


@app.post("/api/patch/apply.json", response_class=JSONResponse)
def api_patch_apply_json(req: ApplyPatchReq) -> JSONResponse:
    result, status_code = apply_patch_action(req)
    return JSONResponse(result.model_dump(), status_code=status_code)


@app.post("/api/git/branch", response_class=PlainTextResponse)
def api_git_branch(req: GitBranchReq) -> str:
    target = get_repo(req.repo)
    if not is_valid_branch_name(req.name):
        raise HTTPException(status_code=400, detail="Invalid branch name")
    out = run(["git", "checkout", "-b", req.name], cwd=target.path, timeout=30)
    return combine_output(out)


@app.get("/api/git/status", response_class=PlainTextResponse)
def api_git_status(repo: str = Query(...)) -> str:
    target = get_repo(repo)
    out = run(["git", "status", "--porcelain=v1", "-b"], cwd=target.path, timeout=30)
    return combine_output(out)


@app.get("/api/git/diff", response_class=PlainTextResponse)
def api_git_diff(repo: str = Query(...)) -> str:
    target = get_repo(repo)
    out = run(["git", "diff"], cwd=target.path, timeout=60)
    return combine_output(out)


@app.post("/api/git/commit", response_class=PlainTextResponse)
def api_git_commit(req: GitCommitReq) -> str:
    target = get_repo(req.repo)
    assert_branch_guard(target.path)
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Commit message required")
    run(["git", "add", "-A"], cwd=target.path, timeout=60)
    out = run(["git", "commit", "-m", req.message], cwd=target.path, timeout=60)
    return combine_output(out)


@app.post("/api/git/commit.json", response_class=JSONResponse)
def api_git_commit_json(req: GitCommitReq) -> JSONResponse:
    result, status_code = commit_action(req)
    return JSONResponse(result.model_dump(), status_code=status_code)


@app.post("/api/git/push", response_class=PlainTextResponse)
def api_git_push(req: GitPushReq) -> str:
    target = get_repo(req.repo)
    assert_branch_guard(target.path)
    out = run(["git", "push", "-u", "origin", "HEAD"], cwd=target.path, timeout=120)
    return combine_output(out)


@app.post("/api/git/push.json", response_class=JSONResponse)
def api_git_push_json(req: GitPushReq) -> JSONResponse:
    result, status_code = push_action(req)
    return JSONResponse(result.model_dump(), status_code=status_code)


@app.get("/api/git/pr-prepare", response_class=PlainTextResponse)
def api_git_pr_prepare(repo: str = Query(...)) -> str:
    target = get_repo(repo)
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=target.path).stdout.strip()
    if branch in {"main", "master"}:
        return "Create a feature branch first before preparing a PR."
    return (
        "PR preparation only. Suggested commands:\n"
        "  gh pr create --fill\n"
        "  (or open the remote in the browser and create PR manually)"
    )


@app.post("/api/git/publish", response_class=JSONResponse)
def api_git_publish(repo: str = Query(...), req: PublishOptions = Body(...)) -> JSONResponse:
    # Body is required to keep OpenAPI accurate; clients can still send {} for defaults.
    correlation_id = new_correlation_id()
    job_id = str(uuid.uuid4())
    job_state = JobState(job_id=job_id, status="queued")
    with JOB_LOCK:
        purge_jobs_locked()
        JOBS[job_id] = job_state
        JOB_CREATED_AT[job_id] = time.time()
    JOB_EXECUTOR.submit(run_publish_job, job_id, correlation_id, repo, req)
    payload = PublishJobResponse(job_id=job_id, correlation_id=correlation_id)
    return JSONResponse(payload.model_dump(), status_code=202)


@app.get("/api/jobs/{job_id}", response_class=JSONResponse)
def api_job_status(job_id: str) -> JSONResponse:
    with JOB_LOCK:
        purge_jobs_locked()
        job_state = JOBS.get(job_id)
    if not job_state:
        raise HTTPException(status_code=404, detail="Unknown job id")
    payload = JobStatusResponse(
        status=job_state.status,
        results=job_state.results,
        log_tail=tail_job_logs(job_state.log_lines),
    )
    return JSONResponse(payload.model_dump())


# NOTE on error handling:
# - /api/patch/apply returns a structured ActionResult and makes no-ops explicit.
# - Other git endpoints currently expose stdout/stderr as part of an interactive wizard flow.
#   A future PR can normalize this into structured responses + non-2xx statuses.
def combine_output(result: Any) -> str:
    output = result.stdout or ""
    if result.stderr:
        output = f"{output}\n{result.stderr}" if output else result.stderr
    return output


def get_remote_protocol(remote_url: str) -> str:
    remote_url = remote_url.strip()
    if not remote_url:
        return "unknown"
    if remote_url.startswith(("http://", "https://")):
        return "https"
    if remote_url.startswith("ssh://"):
        return "ssh"
    if re.match(r"^[^@]+@[^:]+:.+", remote_url):
        return "ssh"
    return "unknown"


def https_remote_to_ssh(remote_url: str) -> str | None:
    if not remote_url.startswith(("http://", "https://")):
        return None
    parsed = urlparse(remote_url)
    if parsed.hostname != "github.com":
        return None
    repo_path = parsed.path.lstrip("/").rstrip("/")
    if not repo_path:
        return None
    if not repo_path.endswith(".git"):
        repo_path = f"{repo_path}.git"
    return f"git@github.com:{repo_path}"


def allow_remote_rewrite() -> bool:
    value = os.getenv("ACS_PUBLISH_REWRITE_REMOTE", "1")
    value = value.strip().lower()
    return value not in {"0", "false", "no", "off"}


def extract_patch_files(patch: str) -> set[str]:
    files: set[str] = set()
    for line in patch.splitlines():
        if not line.startswith("diff --git "):
            continue
        marker = " b/"
        if marker not in line:
            continue
        _, tail = line.split(marker, 1)
        path = tail.strip()
        if not path:
            continue
        files.add(path)
    return files


def log_action_result(result: ActionResult, job_id: str | None = None) -> None:
    log_action(result.model_dump(), job_id=job_id)


def _redact_action_result(r: ActionResult) -> ActionResult:
    return r.model_copy(update={
        "stdout": redact_secrets(r.stdout) if r.stdout else r.stdout,
        "stderr": redact_secrets(r.stderr) if r.stderr else r.stderr,
        "message": redact_secrets(r.message) if r.message else r.message,
        "pr_url": redact_secrets(r.pr_url) if r.pr_url else r.pr_url,
    })


def record_job_result(job_id: str, result: ActionResult) -> None:
    # Cap stdout/stderr to avoid excessive memory usage
    updates = {}
    if len(result.stdout) > MAX_STDOUT_CHARS:
        updates["stdout"] = result.stdout[:MAX_STDOUT_CHARS] + "... (truncated)"
    if len(result.stderr) > MAX_STDOUT_CHARS:
        updates["stderr"] = result.stderr[:MAX_STDOUT_CHARS] + "... (truncated)"

    # Create truncated copy first (if needed), then redacted copy
    truncated_result = result.model_copy(update=updates) if updates else result

    # Create redacted copy for in-memory storage (API safety)
    safe_result = _redact_action_result(truncated_result)

    line = json.dumps(safe_result.model_dump(), ensure_ascii=False)
    if len(line) > MAX_LOG_LINE_CHARS:
        line = line[:MAX_LOG_LINE_CHARS] + "... (truncated)"

    with JOB_LOCK:
        job_state = JOBS.get(job_id)
        if job_state:
            job_state.results.append(safe_result)
            job_state.log_lines.append(line)
            if len(job_state.log_lines) > MAX_JOB_LOG_LINES:
                job_state.log_lines.pop(0)
    log_action_result(safe_result, job_id=job_id)


def set_job_status(job_id: str, status: str) -> None:
    with JOB_LOCK:
        job_state = JOBS.get(job_id)
        if job_state:
            job_state.status = status


def purge_jobs_locked() -> None:
    now_ts = time.time()
    expired = [
        job_id
        for job_id, created in JOB_CREATED_AT.items()
        if now_ts - created > JOB_MAX_AGE_SECONDS
    ]
    for job_id in expired:
        JOBS.pop(job_id, None)
        JOB_CREATED_AT.pop(job_id, None)
    if len(JOBS) <= JOB_MAX_ENTRIES:
        return
    ordered = sorted(JOB_CREATED_AT.items(), key=lambda item: item[1])
    while len(ordered) > JOB_MAX_ENTRIES:
        job_id, _ = ordered.pop(0)
        JOBS.pop(job_id, None)
        JOB_CREATED_AT.pop(job_id, None)


def tail_job_logs(lines: list[str], max_lines: int = 20, max_chars: int = 4000) -> str:
    if not lines:
        return ""
    tail = "\n".join(lines[-max_lines:])
    if len(tail) <= max_chars:
        return tail
    return tail[-max_chars:]


def check_branch_guard(path: Path) -> str | None:
    try:
        assert_not_main_branch(path)
    except RuntimeError as exc:
        return str(exc)
    return None


def git_diff_signature(path: Path) -> str:
    unstaged = run(["git", "diff", "--no-ext-diff"], cwd=path, timeout=60).stdout
    staged = run(["git", "diff", "--cached", "--no-ext-diff"], cwd=path, timeout=60).stdout
    signature = hashlib.sha256((unstaged + staged).encode("utf-8")).hexdigest()
    return signature


def git_status_porcelain(path: Path) -> list[str]:
    out = run(["git", "status", "--porcelain"], cwd=path, timeout=30)
    return [line for line in out.stdout.splitlines() if line.strip()]


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


def get_status_files(lines: list[str]) -> list[str]:
    files: list[str] = []
    for line in lines:
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        if path:
            files.append(path)
    return sorted(set(files))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_correlation_id() -> str:
    return str(uuid.uuid4())


def get_git_state(path: Path) -> tuple[str | None, str | None]:
    try:
        res = run(["git", "status", "--porcelain=v2", "--branch", "-uno"], cwd=path, timeout=20)
        if res.code != 0:
            return None, None

        branch = None
        head = None

        for line in res.stdout.splitlines():
            if line.startswith(BRANCH_HEAD_PREFIX):
                branch = line[len(BRANCH_HEAD_PREFIX) :].strip()
            elif line.startswith(BRANCH_OID_PREFIX):
                head = line[len(BRANCH_OID_PREFIX) :].strip()
            if branch and head:
                break

        if not branch or branch == "(detached)" or branch == "(unknown)" or branch.startswith("(detached"):
            branch = "HEAD"

        if head == "(initial)":
            head = None
            if not branch:
                branch = "HEAD"

        return branch or None, head or None
    except Exception:
        return None, None


def build_action_result(
    *,
    ok: bool,
    action: str,
    repo: str,
    correlation_id: str,
    stdout: str = "",
    stderr: str = "",
    code: int | None = None,
    error_kind: str | None = None,
    message: str | None = None,
    changed: bool | None = None,
    files: list[str] | None = None,
    pr_url: str | None = None,
    branch: str | None = None,
    head: str | None = None,
    duration_ms: int | None = None,
    repo_path: Path | None = None,
) -> ActionResult:
    if repo_path and (branch is None or head is None):
        branch, head = get_git_state(repo_path)
    return ActionResult(
        ok=ok,
        action=action,
        repo=repo,
        branch=branch,
        head=head,
        changed=changed,
        files=files,
        pr_url=pr_url,
        stdout=stdout,
        stderr=stderr,
        code=code,
        error_kind=error_kind,
        message=message,
        ts=now_iso(),
        duration_ms=duration_ms,
        correlation_id=correlation_id,
    )


def get_apply_context(repo: str) -> dict[str, str]:
    with JOB_LOCK:
        return dict(LAST_APPLY_CONTEXT.get(repo, {}))


def set_apply_context(repo: str, signature: str, session_id: str) -> None:
    with JOB_LOCK:
        LAST_APPLY_CONTEXT[repo] = {
            "signature": signature,
            "session_id": session_id,
        }


def format_action_result(result: ActionResult) -> str:
    if result.action == "patch.apply":
        files_in_patch = len(result.files or [])
        if result.ok and result.changed:
            suffix = f" ({files_in_patch} Dateien im Patch)" if files_in_patch else ""
            status_line = f"✔ Patch angewendet{suffix}."
        elif result.ok and result.changed is False:
            status_line = "⚠ Patch angewendet, aber keine Änderungen."
        else:
            error_summary = (result.message or "Unbekannter Fehler").splitlines()[0]
            status_line = f"❌ Patch fehlgeschlagen: {error_summary}"
        details = (result.stdout or "").strip()
        if not details or details == status_line:
            return status_line
        return f"{status_line}\n\n{details}"
    if result.ok:
        return result.message or (result.stdout or "").strip() or "OK"
    error_summary = (result.message or result.stderr or result.stdout or "Unbekannter Fehler").splitlines()[0]
    return f"❌ {error_summary}"


def build_action_response(
    result: ActionResult, response_format: str, status_code: int
) -> Response:
    if response_format == "json":
        return JSONResponse(result.model_dump(), status_code=status_code)
    if response_format != "text":
        error = build_action_result(
            ok=False,
            action=result.action,
            repo=result.repo,
            correlation_id=result.correlation_id,
            message=f"Unknown format: {response_format}",
            error_kind="internal",
            code=1,
        )
        return PlainTextResponse(format_action_result(error), status_code=400)
    return PlainTextResponse(format_action_result(result), status_code=status_code)


def normalize_patch_output(output: str) -> str:
    if not output:
        return output
    lines = output.splitlines()
    for idx, line in enumerate(lines):
        if line.startswith("diff --git"):
            return "\n".join(lines[idx:]).strip()
    return ""


def apply_patch_action(req: ApplyPatchReq) -> tuple[ActionResult, int]:
    correlation_id = new_correlation_id()
    start = time.monotonic()
    files = sorted(extract_patch_files(req.patch))
    try:
        target = get_repo(req.repo)
    except HTTPException as exc:
        result = build_action_result(
            ok=False,
            action="patch.apply",
            repo=req.repo,
            correlation_id=correlation_id,
            message=str(exc.detail),
            error_kind="invalid_repo",
            code=1,
            files=files or None,
        )
        log_action_result(result)
        return result, exc.status_code
    branch_guard_error = check_branch_guard(target.path)
    if branch_guard_error:
        result = build_action_result(
            ok=False,
            action="patch.apply",
            repo=target.key,
            correlation_id=correlation_id,
            message=branch_guard_error,
            error_kind="branch_guard",
            code=1,
            files=files or None,
            repo_path=target.path,
        )
        log_action_result(result)
        return result, 409
    if not req.patch.strip():
        result = build_action_result(
            ok=False,
            action="patch.apply",
            repo=target.key,
            correlation_id=correlation_id,
            message="Patch is empty",
            error_kind="invalid_input",
            code=1,
            files=files or None,
            repo_path=target.path,
        )
        log_action_result(result)
        return result, 400
    try:
        before_diff = git_diff_signature(target.path)
        check_cmd = ["git", "apply", "--check"]
        if req.three_way:
            check_cmd.append("--3way")
        check_cmd.append("-")
        check = run(check_cmd, cwd=target.path, timeout=60, input_text=req.patch)
        if check.code != 0:
            result = build_action_result(
                ok=False,
                action="patch.apply",
                repo=target.key,
                correlation_id=correlation_id,
                stdout=check.stdout,
                stderr=check.stderr,
                code=check.code,
                error_kind="git_failed",
                message=combine_output(check).strip(),
                files=files or None,
                repo_path=target.path,
            )
            log_action_result(result)
            return result, 409
        apply_cmd = ["git", "apply"]
        if req.three_way:
            apply_cmd.append("--3way")
        apply_cmd.append("-")
        out = run(apply_cmd, cwd=target.path, timeout=60, input_text=req.patch)
        if out.code != 0:
            result = build_action_result(
                ok=False,
                action="patch.apply",
                repo=target.key,
                correlation_id=correlation_id,
                stdout=out.stdout,
                stderr=out.stderr,
                code=out.code,
                error_kind="git_failed",
                message=combine_output(out).strip(),
                files=files or None,
                repo_path=target.path,
            )
            log_action_result(result)
            return result, 409
        after_diff = git_diff_signature(target.path)
    except Exception as exc:
        result = build_action_result(
            ok=False,
            action="patch.apply",
            repo=target.key,
            correlation_id=correlation_id,
            message=str(exc),
            error_kind="internal",
            code=1,
            files=files or None,
            repo_path=target.path,
        )
        log_action_result(result)
        return result, 500
    changed = before_diff != after_diff
    message = "Patch applied." if changed else "Patch applied, but no changes."
    result = build_action_result(
        ok=True,
        action="patch.apply",
        repo=target.key,
        correlation_id=correlation_id,
        stdout=out.stdout,
        stderr=out.stderr,
        code=out.code,
        message=message,
        changed=changed,
        files=files or None,
        repo_path=target.path,
    )
    result.duration_ms = int((time.monotonic() - start) * 1000)
    log_action_result(result)
    set_apply_context(target.key, after_diff, req.session_id or "")
    return result, 200


def commit_action(req: GitCommitReq) -> tuple[ActionResult, int]:
    correlation_id = new_correlation_id()
    start = time.monotonic()
    try:
        target = get_repo(req.repo)
    except HTTPException as exc:
        result = build_action_result(
            ok=False,
            action="git.commit",
            repo=req.repo,
            correlation_id=correlation_id,
            message=str(exc.detail),
            error_kind="invalid_repo",
            code=1,
        )
        log_action_result(result)
        return result, exc.status_code
    branch_guard_error = check_branch_guard(target.path)
    if branch_guard_error:
        result = build_action_result(
            ok=False,
            action="git.commit",
            repo=target.key,
            correlation_id=correlation_id,
            message=branch_guard_error,
            error_kind="branch_guard",
            code=1,
            repo_path=target.path,
        )
        log_action_result(result)
        return result, 409
    if not req.message.strip():
        result = build_action_result(
            ok=False,
            action="git.commit",
            repo=target.key,
            correlation_id=correlation_id,
            message="Commit message required",
            error_kind="git_failed",
            code=1,
            repo_path=target.path,
        )
        log_action_result(result)
        return result, 400
    status_lines = git_status_porcelain(target.path)
    if not status_lines:
        result = build_action_result(
            ok=False,
            action="git.commit",
            repo=target.key,
            correlation_id=correlation_id,
            message="Nothing to commit",
            error_kind="nothing_to_commit",
            code=1,
            repo_path=target.path,
        )
        log_action_result(result)
        return result, 409
    add = run(["git", "add", "-A"], cwd=target.path, timeout=60)
    if add.code != 0:
        result = build_action_result(
            ok=False,
            action="git.commit",
            repo=target.key,
            correlation_id=correlation_id,
            stdout=add.stdout,
            stderr=add.stderr,
            code=add.code,
            error_kind="git_failed",
            message=combine_output(add).strip(),
            repo_path=target.path,
        )
        log_action_result(result)
        return result, 500
    staged = run(["git", "diff", "--cached", "--name-only"], cwd=target.path, timeout=30)
    files = [line.strip() for line in staged.stdout.splitlines() if line.strip()]
    if not files:
        result = build_action_result(
            ok=False,
            action="git.commit",
            repo=target.key,
            correlation_id=correlation_id,
            message="Nothing to commit",
            error_kind="nothing_to_commit",
            code=1,
            repo_path=target.path,
        )
        log_action_result(result)
        return result, 409
    out = run(["git", "commit", "-m", req.message], cwd=target.path, timeout=60)
    ok = out.code == 0
    error_kind = None if ok else "git_failed"
    message = "Commit created." if ok else combine_output(out).strip()
    if not ok and "nothing to commit" in message.lower():
        error_kind = "nothing_to_commit"
    result = build_action_result(
        ok=ok,
        action="git.commit",
        repo=target.key,
        correlation_id=correlation_id,
        stdout=out.stdout,
        stderr=out.stderr,
        code=out.code,
        error_kind=error_kind,
        message=message,
        changed=ok,
        files=files,
        repo_path=target.path,
    )
    result.duration_ms = int((time.monotonic() - start) * 1000)
    log_action_result(result)
    status_code = 200 if ok else 409 if error_kind == "nothing_to_commit" else 500
    return result, status_code


def push_action(req: GitPushReq) -> tuple[ActionResult, int]:
    correlation_id = new_correlation_id()
    start = time.monotonic()
    try:
        target = get_repo(req.repo)
    except HTTPException as exc:
        result = build_action_result(
            ok=False,
            action="git.push",
            repo=req.repo,
            correlation_id=correlation_id,
            message=str(exc.detail),
            error_kind="invalid_repo",
            code=1,
        )
        log_action_result(result)
        return result, exc.status_code
    branch_guard_error = check_branch_guard(target.path)
    if branch_guard_error:
        result = build_action_result(
            ok=False,
            action="git.push",
            repo=target.key,
            correlation_id=correlation_id,
            message=branch_guard_error,
            error_kind="branch_guard",
            code=1,
            repo_path=target.path,
        )
        log_action_result(result)
        return result, 409
    out = run(["git", "push", "-u", "origin", "HEAD"], cwd=target.path, timeout=120)
    ok = out.code == 0
    result = build_action_result(
        ok=ok,
        action="git.push",
        repo=target.key,
        correlation_id=correlation_id,
        stdout=out.stdout,
        stderr=out.stderr,
        code=out.code,
        error_kind=None if ok else "push_failed",
        message="Push completed." if ok else combine_output(out).strip(),
        repo_path=target.path,
    )
    result.duration_ms = int((time.monotonic() - start) * 1000)
    log_action_result(result)
    return result, 200 if ok else 500


def run_publish_job(job_id: str, correlation_id: str, repo: str, req: PublishOptions) -> None:
    set_job_status(job_id, "running")
    ok = False
    try:
        ok = execute_publish(job_id, correlation_id, repo, req)
    except Exception as exc:
        result = build_action_result(
            ok=False,
            action="git.publish",
            repo=repo,
            correlation_id=correlation_id,
            message=str(exc),
            error_kind="internal",
            code=1,
        )
        record_job_result(job_id, result)
    set_job_status(job_id, "done" if ok else "error")


def execute_publish(job_id: str, correlation_id: str, repo: str, req: PublishOptions) -> bool:
    start = time.monotonic()
    try:
        target = get_repo(repo)
    except HTTPException as exc:
        result = build_action_result(
            ok=False,
            action="git.publish",
            repo=repo,
            correlation_id=correlation_id,
            message=str(exc.detail),
            error_kind="invalid_repo",
            code=1,
        )
        record_job_result(job_id, result)
        return False
    branch_name = (req.branch or "").strip() or generate_branch_name(target.key)
    if not is_valid_branch_name(branch_name):
        result = build_action_result(
            ok=False,
            action="git.branch",
            repo=target.key,
            correlation_id=correlation_id,
            message="Invalid branch name",
            error_kind="invalid_input",
            code=1,
            repo_path=target.path,
        )
        record_job_result(job_id, result)
        return False
    branch, _ = get_git_state(target.path)
    if branch != branch_name:
        checkout = checkout_branch(target.path, branch_name)
        checkout_result = build_action_result(
            ok=checkout.code == 0,
            action="git.branch",
            repo=target.key,
            correlation_id=correlation_id,
            stdout=checkout.stdout,
            stderr=checkout.stderr,
            code=checkout.code,
            error_kind=None if checkout.code == 0 else "git_failed",
            message="Switched branch." if checkout.code == 0 else combine_output(checkout).strip(),
            repo_path=target.path,
        )
        record_job_result(job_id, checkout_result)
        if checkout.code != 0:
            return False
    branch, _ = get_git_state(target.path)
    if branch in {"main", "master"}:
        result = build_action_result(
            ok=False,
            action="git.branch",
            repo=target.key,
            correlation_id=correlation_id,
            message="Refusing to operate on main/master. Create a branch first.",
            error_kind="branch_guard",
            code=1,
            repo_path=target.path,
        )
        record_job_result(job_id, result)
        return False
    remote_check = run(["git", "ls-remote", "--heads", "origin"], cwd=target.path, timeout=60)
    remote_result = build_action_result(
        ok=remote_check.code == 0,
        action="git.remote",
        repo=target.key,
        correlation_id=correlation_id,
        stdout=remote_check.stdout,
        stderr=remote_check.stderr,
        code=remote_check.code,
        error_kind=None if remote_check.code == 0 else "push_failed",
        message="Remote origin reachable." if remote_check.code == 0 else combine_output(remote_check).strip(),
        repo_path=target.path,
    )
    record_job_result(job_id, remote_result)
    if remote_check.code != 0:
        return False
    gh_version = run(["gh", "--version"], cwd=target.path, timeout=30)
    gh_version_result = build_action_result(
        ok=gh_version.code == 0,
        action="gh.version",
        repo=target.key,
        correlation_id=correlation_id,
        stdout=gh_version.stdout,
        stderr=gh_version.stderr,
        code=gh_version.code,
        error_kind=None if gh_version.code == 0 else "gh_missing",
        message=(
            "gh available."
            if gh_version.code == 0
            else "gh is missing. Install via apt (recommended) and ensure systemd user services have PATH."
        ),
        repo_path=target.path,
    )
    record_job_result(job_id, gh_version_result)
    if gh_version.code != 0:
        return False
    auth_check = run(["gh", "auth", "status", "--hostname", "github.com"], cwd=target.path, timeout=30)
    auth_result = build_action_result(
        ok=auth_check.code == 0,
        action="gh.auth",
        repo=target.key,
        correlation_id=correlation_id,
        stdout=auth_check.stdout,
        stderr=auth_check.stderr,
        code=auth_check.code,
        error_kind=None if auth_check.code == 0 else "gh_not_auth",
        message="gh auth ok." if auth_check.code == 0 else combine_output(auth_check).strip(),
        repo_path=target.path,
    )
    record_job_result(job_id, auth_result)
    if auth_check.code != 0:
        return False
    remote_url = run(["git", "remote", "get-url", "origin"], cwd=target.path, timeout=30)
    remote_url_value = remote_url.stdout.strip()
    remote_url_result = build_action_result(
        ok=remote_url.code == 0 and bool(remote_url_value),
        action="git.remote.url",
        repo=target.key,
        correlation_id=correlation_id,
        stdout=remote_url.stdout,
        stderr=remote_url.stderr,
        code=remote_url.code,
        error_kind=None if remote_url.code == 0 and remote_url_value else "git_failed",
        message="Remote origin URL resolved." if remote_url.code == 0 and remote_url_value else combine_output(remote_url).strip(),
        repo_path=target.path,
    )
    record_job_result(job_id, remote_url_result)
    if remote_url.code != 0 or not remote_url_value:
        return False
    remote_protocol = get_remote_protocol(remote_url_value)
    if remote_protocol == "https":
        if not allow_remote_rewrite():
            result = build_action_result(
                ok=False,
                action="git.remote.protocol",
                repo=target.key,
                correlation_id=correlation_id,
                message=(
                    "Remote uses HTTPS. SSH is required for non-interactive publish. "
                    "Run: git remote set-url origin git@github.com:<org>/<repo>.git"
                ),
                error_kind="push_failed",
                code=1,
                repo_path=target.path,
            )
            record_job_result(job_id, result)
            return False
        ssh_url = https_remote_to_ssh(remote_url_value)
        if not ssh_url:
            result = build_action_result(
                ok=False,
                action="git.remote.protocol",
                repo=target.key,
                correlation_id=correlation_id,
                message=(
                    "Remote uses HTTPS. SSH is required for non-interactive publish. "
                    "Run: git remote set-url origin git@github.com:<org>/<repo>.git"
                ),
                error_kind="push_failed",
                code=1,
                repo_path=target.path,
            )
            record_job_result(job_id, result)
            return False
        rewrite = run(["git", "remote", "set-url", "origin", ssh_url], cwd=target.path, timeout=30)
        rewrite_result = build_action_result(
            ok=rewrite.code == 0,
            action="git.remote.rewrite",
            repo=target.key,
            correlation_id=correlation_id,
            stdout=rewrite.stdout,
            stderr=rewrite.stderr,
            code=rewrite.code,
            error_kind=None if rewrite.code == 0 else "git_failed",
            message=(
                f"origin: {remote_url_value} -> {ssh_url}"
                if rewrite.code == 0
                else combine_output(rewrite).strip()
            ),
            repo_path=target.path,
        )
        record_job_result(job_id, rewrite_result)
        if rewrite.code != 0:
            return False
    elif remote_protocol != "ssh":
        result = build_action_result(
            ok=False,
            action="git.remote.protocol",
            repo=target.key,
            correlation_id=correlation_id,
            message=(
                "Remote uses an unsupported protocol. SSH is required for non-interactive publish. "
                "Run: git remote set-url origin git@github.com:<org>/<repo>.git"
            ),
            error_kind="push_failed",
            code=1,
            repo_path=target.path,
        )
        record_job_result(job_id, result)
        return False
    status_lines = git_status_porcelain(target.path)
    if status_lines:
        signature = git_diff_signature(target.path)
        expected_signature = get_apply_context(target.key).get("signature")
        if not expected_signature:
            result = build_action_result(
                ok=False,
                action="git.status",
                repo=target.key,
                correlation_id=correlation_id,
                message=(
                    "Working tree has changes, but no apply context is available. "
                    "Please re-apply the patch via ACS or commit manually."
                ),
                error_kind="unexpected_changes_no_context",
                code=1,
                files=get_status_files(status_lines),
                repo_path=target.path,
            )
            record_job_result(job_id, result)
            return False
        if signature != expected_signature:
            result = build_action_result(
                ok=False,
                action="git.status",
                repo=target.key,
                correlation_id=correlation_id,
                message="Working tree has unexpected changes. Apply or commit first.",
                error_kind="git_failed",
                code=1,
                files=get_status_files(status_lines),
                repo_path=target.path,
            )
            record_job_result(job_id, result)
            return False
        commit_message = (req.commit_message or "").strip() or build_default_commit_message(target.key)
        add = run(["git", "add", "-A"], cwd=target.path, timeout=60)
        add_result = build_action_result(
            ok=add.code == 0,
            action="git.add",
            repo=target.key,
            correlation_id=correlation_id,
            stdout=add.stdout,
            stderr=add.stderr,
            code=add.code,
            error_kind=None if add.code == 0 else "git_failed",
            message="Staged changes." if add.code == 0 else combine_output(add).strip(),
            repo_path=target.path,
        )
        record_job_result(job_id, add_result)
        if add.code != 0:
            return False
        staged = run(["git", "diff", "--cached", "--name-only"], cwd=target.path, timeout=30)
        staged_files = [line.strip() for line in staged.stdout.splitlines() if line.strip()]
        if not staged_files:
            result = build_action_result(
                ok=False,
                action="git.commit",
                repo=target.key,
                correlation_id=correlation_id,
                message="Nothing to commit",
                error_kind="nothing_to_commit",
                code=1,
                repo_path=target.path,
            )
            record_job_result(job_id, result)
            return False
        commit = run(["git", "commit", "-m", commit_message], cwd=target.path, timeout=60)
        commit_ok = commit.code == 0
        commit_result = build_action_result(
            ok=commit_ok,
            action="git.commit",
            repo=target.key,
            correlation_id=correlation_id,
            stdout=commit.stdout,
            stderr=commit.stderr,
            code=commit.code,
            error_kind=None if commit_ok else "git_failed",
            message="Commit created." if commit_ok else combine_output(commit).strip(),
            changed=commit_ok,
            files=staged_files,
            repo_path=target.path,
        )
        record_job_result(job_id, commit_result)
        if not commit_ok:
            return False
    push = run(["git", "push", "-u", "origin", "HEAD"], cwd=target.path, timeout=120)
    push_ok = push.code == 0
    push_result = build_action_result(
        ok=push_ok,
        action="git.push",
        repo=target.key,
        correlation_id=correlation_id,
        stdout=push.stdout,
        stderr=push.stderr,
        code=push.code,
        error_kind=None if push_ok else "push_failed",
        message="Push completed." if push_ok else combine_output(push).strip(),
        repo_path=target.path,
    )
    record_job_result(job_id, push_result)
    if not push_ok:
        return False
    head_branch, _ = get_git_state(target.path)
    if not head_branch or head_branch == "HEAD":
        result = build_action_result(
            ok=False,
            action="gh.pr.create",
            repo=target.key,
            correlation_id=correlation_id,
            message="Unable to determine head branch for PR creation (detached HEAD).",
            error_kind="git_failed",
            code=1,
            repo_path=target.path,
        )
        record_job_result(job_id, result)
        return False
    base_branch = (req.base or "main").strip()
    upstream_branch = None
    upstream = run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=target.path,
        timeout=20,
    )
    upstream_value = upstream.stdout.strip()
    if upstream.code == 0 and upstream_value:
        remote_name, _, branch_name = upstream_value.partition("/")
        if remote_name == "origin" and branch_name:
            upstream_branch = branch_name
    if upstream.code != 0:
        upstream_message = "Upstream not available; falling back to local branch for origin lookup."
        raw_stderr = upstream.stderr or ""
        if raw_stderr:
            sanitized_stderr = re.sub(r"\s+", " ", raw_stderr).strip()
            if len(sanitized_stderr) > 80:
                sanitized_stderr = f"{sanitized_stderr[:77]}..."
            if sanitized_stderr:
                upstream_message = f"{upstream_message} (git: {sanitized_stderr})"
        upstream_error_kind = "upstream_unavailable"
    elif not upstream_value:
        upstream_message = "No upstream configured; falling back to local branch for origin lookup."
        upstream_error_kind = "upstream_missing"
    elif upstream_branch:
        upstream_message = f"Using upstream origin/{upstream_branch}."
        upstream_error_kind = None
    else:
        sanitized_upstream = re.sub(r"\s+", " ", upstream_value).strip()
        if len(sanitized_upstream) > 80:
            sanitized_upstream = f"{sanitized_upstream[:77]}..."
        upstream_message = (
            f"Upstream is '{sanitized_upstream}' (non-origin); using local branch for origin lookup."
        )
        upstream_error_kind = "upstream_non_origin"
    upstream_result = build_action_result(
        ok=upstream.code == 0,
        action="git.branch.upstream",
        repo=target.key,
        correlation_id=correlation_id,
        stdout=upstream.stdout,
        stderr=upstream.stderr,
        code=upstream.code,
        error_kind=upstream_error_kind,
        message=upstream_message,
        repo_path=target.path,
    )
    record_job_result(job_id, upstream_result)
    head_ref_name = upstream_branch or head_branch
    pr_head_branch = head_ref_name
    fetch = run(
        [
            "git",
            "fetch",
            "origin",
            f"{base_branch}:refs/remotes/origin/{base_branch}",
            f"{head_ref_name}:refs/remotes/origin/{head_ref_name}",
            "--prune",
        ],
        cwd=target.path,
        timeout=60,
    )
    fetch_error = ""
    fetch_error_kind = None
    if fetch.code != 0:
        stderr = fetch.stderr or ""
        if f"couldn't find remote ref {base_branch}" in stderr:
            fetch_error_kind = "base_missing"
            fetch_error = f"Remote base branch '{base_branch}' not found."
        elif f"couldn't find remote ref {head_ref_name}" in stderr:
            fetch_error_kind = "head_missing"
            fetch_error = f"Remote head branch '{head_ref_name}' not found."
        else:
            fetch_error_kind = "git_failed"
            fetch_error = combine_output(fetch).strip()
    fetch_result = build_action_result(
        ok=fetch.code == 0,
        action="git.fetch",
        repo=target.key,
        correlation_id=correlation_id,
        stdout=fetch.stdout,
        stderr=fetch.stderr,
        code=fetch.code,
        error_kind=None if fetch.code == 0 else fetch_error_kind,
        message="Fetched remote refs." if fetch.code == 0 else fetch_error,
        repo_path=target.path,
    )
    record_job_result(job_id, fetch_result)
    if fetch.code != 0:
        return False
    pr_title = (req.pr_title or "").strip() or build_default_pr_title(req, target.key)
    pr_body = (req.pr_body or "").strip() or build_default_pr_body(
        target.path,
        correlation_id,
        req.include_diffstat,
    )
    base_ref = f"origin/{base_branch}"
    head_ref = f"origin/{head_ref_name}"
    commit_count = run(
        ["git", "rev-list", "--count", f"{base_ref}..{head_ref}"],
        cwd=target.path,
        timeout=30,
    )
    commit_count_ok = commit_count.code == 0
    commit_total = 0
    if commit_count_ok:
        try:
            commit_total = int(commit_count.stdout.strip() or 0)
        except ValueError:
            commit_count_ok = False
    precheck_result = build_action_result(
        ok=commit_count_ok and commit_total > 0,
        action="git.pr.precheck",
        repo=target.key,
        correlation_id=correlation_id,
        stdout=commit_count.stdout,
        stderr=commit_count.stderr,
        code=commit_count.code,
        error_kind=(
            None
            if commit_count_ok and commit_total > 0
            else "git_failed"
            if not commit_count_ok
            else "no_commits"
        ),
        message=(
            f"Found {commit_total} commit(s) between {base_ref} and {head_ref}."
            if commit_count_ok and commit_total > 0
            else f"No commits between {base_ref} and {head_ref}; PR cannot be created."
            if commit_count_ok
            else combine_output(commit_count).strip()
        ),
        repo_path=target.path,
    )
    record_job_result(job_id, precheck_result)
    if not precheck_result.ok:
        return False
    pr_cmd = [
        "gh",
        "pr",
        "create",
        "--title",
        pr_title,
        "--body",
        pr_body,
        "--base",
        base_branch,
        "--head",
        pr_head_branch,
    ]
    if req.draft:
        pr_cmd.append("--draft")
    pr = run(pr_cmd, cwd=target.path, timeout=120)
    pr_ok = pr.code == 0
    pr_url = extract_pr_url(pr.stdout or pr.stderr)
    existing_pr = False
    if not pr_ok:
        existing_url = find_existing_pr_url(target.path, pr_head_branch, base_branch)
        if existing_url:
            pr_ok = True
            existing_pr = True
            pr_url = existing_url
    pr_error = combine_output(pr).strip()
    pr_error_kind = None
    if not pr_ok:
        pr_error_kind = "no_commits" if "No commits between" in pr_error else "gh_failed"
    pr_result = build_action_result(
        ok=pr_ok,
        action="gh.pr.ensure" if existing_pr else "gh.pr.create",
        repo=target.key,
        correlation_id=correlation_id,
        stdout=pr.stdout,
        stderr=pr.stderr,
        code=pr.code,
        error_kind=pr_error_kind,
        message="PR already exists." if existing_pr else "PR created." if pr_ok else pr_error,
        pr_url=pr_url,
        repo_path=target.path,
    )
    record_job_result(job_id, pr_result)
    if not pr_ok:
        return False
    publish_result = build_action_result(
        ok=True,
        action="git.publish",
        repo=target.key,
        correlation_id=correlation_id,
        message="Publish completed.",
        pr_url=pr_url,
        code=0,
        duration_ms=int((time.monotonic() - start) * 1000),
        repo_path=target.path,
    )
    record_job_result(job_id, publish_result)
    return True


def checkout_branch(path: Path, branch_name: str) -> Any:
    exists = run(
        ["git", "show-ref", "--verify", f"refs/heads/{branch_name}"],
        cwd=path,
        timeout=20,
    )
    if exists.code == 0:
        return run(["git", "checkout", branch_name], cwd=path, timeout=30)
    return run(["git", "checkout", "-b", branch_name], cwd=path, timeout=30)


def generate_branch_name(repo: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    short_id = uuid.uuid4().hex[:6]
    return f"acs/{repo}-{timestamp}-{short_id}"


def build_default_commit_message(repo: str) -> str:
    session_id = get_apply_context(repo).get("session_id")
    if session_id:
        return f"acs: publish {repo} ({session_id})"
    return f"acs: publish {repo}"


def build_default_pr_title(req: PublishOptions, repo: str) -> str:
    return (req.commit_message or "").strip() or build_default_commit_message(repo)


def build_default_pr_body(
    repo_path: Path, correlation_id: str, include_diffstat: bool
) -> str:
    summary = "Automated publish via agent-control-surface."
    body_lines = [summary]
    if include_diffstat:
        diffstat = run(["git", "show", "--stat", "--oneline", "-1"], cwd=repo_path, timeout=30)
        if diffstat.stdout.strip():
            body_lines.extend(["", "Diffstat:", "```", diffstat.stdout.strip(), "```"])
    body_lines.extend(
        [
            "",
            f"correlation_id: {correlation_id}",
            "",
            "Hinweise:",
            "- local-only",
            "- branch-guard aktiv",
            "- repo allowlist aktiv",
        ]
    )
    return "\n".join(body_lines)


def extract_pr_url(text: str | None) -> str | None:
    if not text:
        return None
    match = re.search(
        r"https://github\.com/[^/\s]+/[^/\s]+/pull/\d+",
        text,
    )
    if not match:
        return None
    return match.group(0).rstrip(").,")


def find_existing_pr_url(path: Path, head_branch: str, base_branch: str) -> str | None:
    list_cmd = [
        "gh",
        "pr",
        "list",
        "--head",
        head_branch,
        "--base",
        base_branch,
        "--json",
        "url",
        "--limit",
        "1",
    ]
    out = run(list_cmd, cwd=path, timeout=30)
    if out.code != 0:
        return None
    try:
        payload = json.loads(out.stdout or "[]")
    except json.JSONDecodeError:
        return None
    if isinstance(payload, list) and payload:
        url = payload[0].get("url")
        if isinstance(url, str) and url:
            return url
    return None


def get_repo(key: str) -> Repo:
    try:
        return repo_by_key(key)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Repo not allowed: {key}") from exc


def assert_branch_guard(path: Path) -> None:
    try:
        assert_not_main_branch(path)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def main() -> None:
    import uvicorn

    uvicorn.run("panel.app:app", host="127.0.0.1", port=8099, reload=False)


if __name__ == "__main__":
    main()
