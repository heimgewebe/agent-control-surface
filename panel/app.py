from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request

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


class GitBranchReq(BaseModel):
    repo: str
    name: str


class GitCommitReq(BaseModel):
    repo: str
    message: str


class GitPushReq(BaseModel):
    repo: str


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
    out = run(["jules", "remote", "diff", "--session", session_id], cwd=target.path, timeout=60)
    return combine_output(out)


@app.get("/api/sessions/{session_id}/diff/download", response_class=PlainTextResponse)
def api_session_diff_download(session_id: str, repo: str = Query(...)) -> PlainTextResponse:
    diff_text = api_session_diff(session_id, repo)
    filename = f"jules-session-{session_id}.diff"
    return PlainTextResponse(
        diff_text,
        headers={"Content-Disposition": f"attachment; filename=\"{filename}\""},
    )


@app.post("/api/patch/apply", response_class=PlainTextResponse)
def api_patch_apply(req: ApplyPatchReq) -> str:
    target = get_repo(req.repo)
    assert_branch_guard(target.path)
    if not req.patch.strip():
        raise HTTPException(status_code=400, detail="Patch is empty")
    check_cmd = ["git", "apply", "--check"]
    if req.three_way:
        check_cmd.append("--3way")
    check_cmd.append("-")
    check = run(check_cmd, cwd=target.path, timeout=60, input_text=req.patch)
    if check.code != 0:
        raise HTTPException(status_code=409, detail=combine_output(check))
    apply_cmd = ["git", "apply"]
    if req.three_way:
        apply_cmd.append("--3way")
    apply_cmd.append("-")
    out = run(apply_cmd, cwd=target.path, timeout=60, input_text=req.patch)
    return combine_output(out)


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


def combine_output(result: Any) -> str:
    output = result.stdout or ""
    if result.stderr:
        output = f"{output}\n{result.stderr}" if output else result.stderr
    return output


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
