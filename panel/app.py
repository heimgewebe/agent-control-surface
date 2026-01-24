from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.requests import Request

from .logging import log_action
from .repos import Repo, allowed_repos, repo_by_key
from .runner import assert_not_main_branch, run

app = FastAPI(title="jules-panel")
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))


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
    details: str
    changed: bool
    stderr: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


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


@app.post("/api/patch/apply", response_class=PlainTextResponse)
def api_patch_apply(req: ApplyPatchReq, format: str = Query("text")) -> Response:
    try:
        target = get_repo(req.repo)
    except HTTPException as exc:
        result = ActionResult(
            ok=False,
            action="patch.apply",
            repo=req.repo,
            details=str(exc.detail),
            changed=False,
            meta=build_patch_meta(req),
        )
        log_action_result(result)
        return build_action_response(result, format, status_code=exc.status_code)
    branch_guard_error = check_branch_guard(target.path)
    if branch_guard_error:
        result = ActionResult(
            ok=False,
            action="patch.apply",
            repo=target.key,
            details=branch_guard_error,
            changed=False,
            meta=build_patch_meta(req),
        )
        log_action_result(result)
        return build_action_response(result, format, status_code=409)
    result_meta = build_patch_meta(req)
    if not req.patch.strip():
        result = ActionResult(
            ok=False,
            action="patch.apply",
            repo=target.key,
            details="Patch is empty",
            changed=False,
            meta=result_meta,
        )
        log_action_result(result)
        return build_action_response(result, format, status_code=400)
    try:
        before_status = run(["git", "status", "--porcelain=v1"], cwd=target.path, timeout=60)
        check_cmd = ["git", "apply", "--check"]
        if req.three_way:
            check_cmd.append("--3way")
        check_cmd.append("-")
        check = run(check_cmd, cwd=target.path, timeout=60, input_text=req.patch)
        if check.code != 0:
            result = ActionResult(
                ok=False,
                action="patch.apply",
                repo=target.key,
                details=combine_output(check),
                changed=False,
                stderr=check.stderr or None,
                meta=result_meta,
            )
            log_action_result(result)
            return build_action_response(result, format, status_code=409)
        apply_cmd = ["git", "apply"]
        if req.three_way:
            apply_cmd.append("--3way")
        apply_cmd.append("-")
        out = run(apply_cmd, cwd=target.path, timeout=60, input_text=req.patch)
        if out.code != 0:
            # Patch passed --check but failed to apply; treat as conflict/state issue.
            result = ActionResult(
                ok=False,
                action="patch.apply",
                repo=target.key,
                details=combine_output(out),
                changed=False,
                stderr=out.stderr or None,
                meta=result_meta,
            )
            log_action_result(result)
            return build_action_response(result, format, status_code=409)
        after_status = run(["git", "status", "--porcelain=v1"], cwd=target.path, timeout=60)
    except Exception as exc:
        result = ActionResult(
            ok=False,
            action="patch.apply",
            repo=target.key,
            details=str(exc),
            changed=False,
            meta=result_meta,
        )
        log_action_result(result)
        return build_action_response(result, format, status_code=500)
    changed = before_status.stdout != after_status.stdout
    details = combine_output(out).strip()
    if not details:
        details = "Patch applied." if changed else "Patch applied, but no changes."
    result = ActionResult(
        ok=True,
        action="patch.apply",
        repo=target.key,
        details=details,
        changed=changed,
        stderr=out.stderr or None,
        meta=result_meta,
    )
    log_action_result(result)
    return build_action_response(result, format, status_code=200)


@app.post("/api/git/branch", response_class=PlainTextResponse)
def api_git_branch(req: GitBranchReq) -> str:
    target = get_repo(req.repo)
    if not req.name or " " in req.name:
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


@app.post("/api/git/push", response_class=PlainTextResponse)
def api_git_push(req: GitPushReq) -> str:
    target = get_repo(req.repo)
    assert_branch_guard(target.path)
    out = run(["git", "push", "-u", "origin", "HEAD"], cwd=target.path, timeout=120)
    return combine_output(out)


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


# NOTE on error handling:
# - /api/patch/apply returns a structured ActionResult and makes no-ops explicit.
# - Other git endpoints currently expose stdout/stderr as part of an interactive wizard flow.
#   A future PR can normalize this into structured responses + non-2xx statuses.
def combine_output(result: Any) -> str:
    output = result.stdout or ""
    if result.stderr:
        output = f"{output}\n{result.stderr}" if output else result.stderr
    return output


def build_patch_meta(req: ApplyPatchReq) -> dict[str, Any]:
    patch_hash = hashlib.sha256(req.patch.encode("utf-8")).hexdigest()
    files = sorted(extract_patch_files(req.patch))
    meta: dict[str, Any] = {}
    if include_patch_hash():
        meta["patch_hash"] = patch_hash
    if files:
        meta["files"] = files
        meta["files_changed"] = len(files)
    if req.session_id:
        meta["session_id"] = req.session_id
        meta["source"] = req.source or "jules"
    elif req.source:
        meta["source"] = req.source
    return meta


def extract_patch_files(patch: str) -> set[str]:
    files: set[str] = set()
    for line in patch.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        path = parts[3]
        if path.startswith("b/"):
            path = path[2:]
        files.add(path)
    return files


def log_action_result(result: ActionResult) -> None:
    log_action(
        {
            "action": result.action,
            "repo": result.repo,
            "ok": result.ok,
            "changed": result.changed,
            "session_id": result.meta.get("session_id"),
            "stderr": result.stderr,
        }
    )


def include_patch_hash() -> bool:
    value = os.getenv("ACS_ACTION_LOG_INCLUDE_HASH", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def check_branch_guard(path: Path) -> str | None:
    try:
        assert_not_main_branch(path)
    except RuntimeError as exc:
        return str(exc)
    return None


def format_action_result(result: ActionResult) -> str:
    files_changed = result.meta.get("files_changed")
    if result.ok and result.changed:
        suffix = f" ({files_changed} Dateien geändert)" if files_changed else ""
        status_line = f"✔ Patch angewendet{suffix}."
    elif result.ok and not result.changed:
        status_line = "⚠ Patch angewendet, aber keine Änderungen."
    else:
        error_summary = (result.details or "Unbekannter Fehler").splitlines()[0]
        status_line = f"❌ Patch fehlgeschlagen: {error_summary}"
    details = result.details.strip()
    if not details or details == status_line:
        return status_line
    return f"{status_line}\n\n{details}"


def build_action_response(result: ActionResult, format: str, status_code: int) -> Response:
    if format == "json":
        return JSONResponse(result.model_dump(), status_code=status_code)
    if format != "text":
        error = ActionResult(
            ok=False,
            action=result.action,
            repo=result.repo,
            details=f"Unknown format: {format}",
            changed=False,
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
