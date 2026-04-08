from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TAG_PART_PATTERN = re.compile(r"^[a-zA-Z0-9._-]{1,50}$")

UUID_RE = re.compile(
    r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    re.IGNORECASE,
)
SESSION_HINT_RE = re.compile(
    r"(?:session|session_id|id)[:=#\s]+([0-9a-f-]{12,40})\b",
    re.IGNORECASE,
)


def state_root() -> Path:
    raw = os.getenv("ACS_STATE_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path("~/.local/state/agent-control-surface").expanduser().resolve()


def agent_sessions_dir(agent: str) -> Path:
    import re
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", agent)
    d = state_root() / "sessions" / safe
    d.mkdir(parents=True, exist_ok=True)
    return d


def title_from_prompt(prompt: str, max_len: int = 200) -> str:
    lines = (prompt or "").strip().splitlines()
    base = lines[0].strip() if lines else ""
    if not base:
        return "untitled"
    if len(base) > max_len:
        return base[: max_len - 1].rstrip() + "…"
    return base


def extract_jules_session_id(text: str) -> str | None:
    if not text:
        return None
    for line in text.splitlines():
        m = SESSION_HINT_RE.search(line)
        if m:
            cand = m.group(1).strip().strip('"').strip("'")
            if len(cand) >= 8:
                return cand.lower()
    m = UUID_RE.search(text)
    if m:
        return m.group(1).lower()
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def normalize_record(data: dict[str, Any]) -> dict[str, Any]:
    data.setdefault("tags", [])
    if not isinstance(data.get("tags"), list):
        data["tags"] = []
    data.setdefault("summary", None)
    data.setdefault("deleted", False)
    data.setdefault("pinned_context", None)
    data.setdefault("is_pending", data.get("session_id") is None)
    return data


def normalize_tags(tags: Any, *, max_tags: int = 20) -> list[str]:
    if tags is None:
        return []
    if not isinstance(tags, list):
        raise ValueError("tags must be a list of strings")
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags[:max_tags]:
        s = str(raw).strip()
        if not s or s in seen:
            continue
        if not TAG_PART_PATTERN.match(s):
            raise ValueError(f"invalid tag: {s!r} (use letters, digits, ._- max 50 chars)")
        seen.add(s)
        out.append(s)
    return out


def summary_heading(text: str | None, max_words: int = 24, max_chars: int = 200) -> str | None:
    """Cheap one-line hint from free text (not an LLM summary)."""
    if not text or not str(text).strip():
        return None
    words = str(text).split()
    if not words:
        return None
    snippet = " ".join(words[:max_words])
    if len(snippet) > max_chars:
        snippet = snippet[: max_chars - 1].rstrip() + "…"
    return snippet


def locate_session_paths(
    agent: str,
    session_id: str,
    repo_key: str | None = None,
) -> list[Path]:
    base = agent_sessions_dir(agent)
    normalized_session_id = session_id.strip().lower()
    paths: list[Path] = []
    for p in base.glob("*.json"):
        stem_match = p.stem.strip().lower() == normalized_session_id
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rec = normalize_record(rec)
        if not stem_match and str(rec.get("session_id") or "").strip().lower() != normalized_session_id:
            continue
        if repo_key and rec.get("repo_key") != repo_key:
            continue
        paths.append(p)
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in paths:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(p)
    return unique


def read_memory_record(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return normalize_record(data)


def write_jules_session_record(
    *,
    repo_key: str,
    title: str,
    prompt: str | None,
    combined_output: str,
    exit_code: int,
) -> tuple[dict[str, Any], Path]:
    session_id = extract_jules_session_id(combined_output)
    agent = "jules"
    base_dir = agent_sessions_dir(agent)
    # When no session ID is found the record is stored under a stable pending key.
    # A later call to reconcile_pending_record() can rename and update it once the
    # real session ID becomes available (e.g. from a subsequent `jules remote list`).
    is_pending = session_id is None
    if session_id:
        path = base_dir / f"{session_id}.json"
    else:
        path = base_dir / f"_pending_{uuid.uuid4().hex}.json"
    status = "new" if exit_code == 0 and session_id else ("running" if exit_code == 0 else "failed")
    record: dict[str, Any] = {
        "agent": agent,
        "repo_key": repo_key,
        "title": title,
        "prompt": prompt,
        "session_id": session_id,
        "is_pending": is_pending,
        "status": status,
        "pulled": False,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "jules_exit_code": exit_code,
        "jules_output": combined_output,
        "tags": [],
        "summary": summary_heading(prompt) if prompt else None,
        "deleted": False,
        "pinned_context": None,
    }
    _atomic_write_json(path, record)
    return record, path


def update_session_pulled(agent: str, session_id: str, repo_key: str) -> bool:
    paths = locate_session_paths(agent, session_id, repo_key)
    if len(paths) != 1:
        return False
    path = paths[0]
    try:
        record = read_memory_record(path)
    except (OSError, json.JSONDecodeError):
        return False
    if record.get("repo_key") != repo_key:
        return False
    record["pulled"] = True
    if record.get("status") != "applied":
        record["status"] = "pulled"
    record["updated_at"] = _now_iso()
    _atomic_write_json(path, record)
    return True


def mark_session_applied(agent: str, session_id: str, repo_key: str) -> bool:
    paths = locate_session_paths(agent, session_id, repo_key)
    if len(paths) != 1:
        return False
    path = paths[0]
    try:
        record = read_memory_record(path)
    except (OSError, json.JSONDecodeError):
        return False
    if record.get("repo_key") != repo_key:
        return False
    record["pulled"] = True
    record["status"] = "applied"
    record["updated_at"] = _now_iso()
    _atomic_write_json(path, record)
    return True


def reconcile_pending_record(
    agent: str,
    pending_path: Path,
    real_session_id: str,
) -> tuple[dict[str, Any], Path] | None:
    """Migrate a _pending_*.json record to its canonical <session_id>.json path.

    Call this once a session ID becomes known (e.g. from a subsequent
    ``jules remote list`` poll).  Returns the updated record and new path, or
    None if the pending file no longer exists or already has a session_id.
    """
    try:
        record = read_memory_record(pending_path)
    except (OSError, json.JSONDecodeError):
        return None
    if record.get("session_id"):
        return None
    if not re.match(r"^[0-9a-fA-F-]{8,64}$", real_session_id):
        raise ValueError("invalid session id")
    real_session_id = real_session_id.lower()

    # Check if pending_path parent is plausible
    base_dir = pending_path.parent
    try:
        base_dir.resolve().relative_to(agent_sessions_dir(agent))
    except ValueError:
        return None

    new_path = base_dir / f"{real_session_id}.json"
    record["session_id"] = real_session_id
    record["is_pending"] = False
    if record.get("status") == "running":
        record["status"] = "new"
    record["updated_at"] = _now_iso()
    _atomic_write_json(new_path, record)
    try:
        pending_path.unlink(missing_ok=True)
    except OSError:
        pass
    return record, new_path


def patch_memory_record(
    agent: str,
    session_id: str,
    repo_key: str | None,
    *,
    summary: str | None = None,
    tags: list[str] | None = None,
    pinned_context: str | None = None,
    deleted: bool | None = None,
    unset_summary: bool = False,
) -> dict[str, Any]:
    paths = locate_session_paths(agent, session_id, repo_key)
    if not paths:
        raise FileNotFoundError("session not found")
    if len(paths) > 1:
        raise ValueError("ambiguous session; pass repo to disambiguate")
    path = paths[0]
    record = read_memory_record(path)
    if repo_key and record.get("repo_key") != repo_key:
        raise ValueError("repo mismatch")
    if unset_summary:
        record["summary"] = None
    elif summary is not None:
        record["summary"] = summary.strip() or None
    if tags is not None:
        record["tags"] = normalize_tags(tags)
    if pinned_context is not None:
        pc = pinned_context.strip()
        record["pinned_context"] = pc or None
    if deleted is not None:
        record["deleted"] = bool(deleted)
    record["updated_at"] = _now_iso()
    _atomic_write_json(path, record)
    return record


def _safe_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def list_session_records(
    agent: str,
    repo_key: str | None = None,
    *,
    include_deleted: bool = False,
) -> list[dict[str, Any]]:
    base = agent_sessions_dir(agent)
    out: list[dict[str, Any]] = []
    for p in sorted(base.glob("*.json"), key=_safe_mtime, reverse=True):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rec = normalize_record(rec)
        if not include_deleted and rec.get("deleted"):
            continue
        if repo_key and rec.get("repo_key") != repo_key:
            continue
        root = state_root()
        try:
            rel = str(p.resolve().relative_to(root))
        except ValueError:
            rel = p.name
        rec = dict(rec)
        rec["archive_relative_path"] = rel
        rec["display_status"] = display_status(rec)
        rec.pop("jules_output", None)
        out.append(rec)
    return out


def display_status(rec: dict[str, Any]) -> str:
    st = rec.get("status") or "unknown"
    if rec.get("deleted"):
        return "deleted"
    if st == "failed":
        return "failed (Jules)"
    if st == "running":
        return "running / pending id"
    if st == "new":
        return "new (noch diff)"
    if st == "pulled":
        return "diff geholt"
    if st == "applied":
        return "Patch angewendet"
    return str(st)