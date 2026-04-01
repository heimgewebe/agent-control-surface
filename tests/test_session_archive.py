import json
from pathlib import Path

import pytest

from panel.session_archive import (
    agent_sessions_dir,
    extract_jules_session_id,
    list_session_records,
    normalize_tags,
    patch_memory_record,
    state_root,
    summary_heading,
    title_from_prompt,
    update_session_pulled,
    write_jules_session_record,
)


@pytest.fixture
def tmp_state(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("ACS_STATE_DIR", str(tmp_path))
    return tmp_path


def test_state_root_env(tmp_state: Path) -> None:
    assert state_root() == tmp_state.resolve()


def test_title_from_prompt() -> None:
    assert title_from_prompt("") == "untitled"
    assert title_from_prompt("  \n\t  ") == "untitled"
    assert title_from_prompt("Fix CI\nmore") == "Fix CI"
    long = "x" * 250
    t = title_from_prompt(long, max_len=200)
    assert len(t) <= 200
    assert t.endswith("…")


def test_extract_jules_session_id_uuid() -> None:
    text = "created session a1b2c3d4-e5f6-7890-abcd-ef1234567890 ok"
    sid = extract_jules_session_id(text)
    assert sid == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"


def test_extract_jules_session_id_hint() -> None:
    text = 'Session: abcdef1234567890\n'
    sid = extract_jules_session_id(text)
    assert sid == "abcdef1234567890"


def test_write_and_list_jules_record(tmp_state: Path) -> None:
    combined = "ok\nsession: 11111111-2222-3333-4444-555555555555\n"
    rec, path = write_jules_session_record(
        repo_key="demo",
        title="Hi",
        prompt="Hello\nworld",
        combined_output=combined,
        exit_code=0,
    )
    assert rec["session_id"] == "11111111-2222-3333-4444-555555555555"
    assert path.is_file()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["prompt"] == "Hello\nworld"
    assert loaded["tags"] == []
    assert loaded["summary"] == summary_heading("Hello\nworld")
    listed = list_session_records("jules", repo_key="demo")
    assert len(listed) == 1
    assert listed[0]["repo_key"] == "demo"
    assert "jules_output" not in listed[0]
    assert listed[0]["display_status"]


def test_update_session_pulled(tmp_state: Path) -> None:
    sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    combined = f"done {sid}\n"
    _, path = write_jules_session_record(
        repo_key="demo",
        title="t",
        prompt=None,
        combined_output=combined,
        exit_code=0,
    )
    assert path.name == f"{sid}.json"
    assert update_session_pulled("jules", sid, "demo") is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["pulled"] is True
    assert data["status"] == "pulled"


def test_agent_sessions_dir_sanitizes_agent(tmp_state: Path) -> None:
    d = agent_sessions_dir("weird/name..")
    assert d.parent.name == "sessions"
    assert ".." not in d.name and "/" not in d.name


def test_api_jules_prompt_and_memory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ACS_STATE_DIR", str(tmp_path))
    from fastapi.testclient import TestClient

    from panel import app as app_module
    from panel.runner import CmdResult

    def fake_run_ok(cmd, cwd, timeout=60, env=None, input_text=None):
        return CmdResult(
            0,
            "ok\nsession_id: 99999999-aaaa-bbbb-cccc-dddddddddddd\n",
            "",
            list(cmd),
        )

    monkeypatch.setattr(app_module, "run", fake_run_ok)
    client = TestClient(app_module.app)
    r = client.post("/api/jules/prompt", json={"repo": "metarepo", "prompt": "line one\nrest"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["session_id"] == "99999999-aaaa-bbbb-cccc-dddddddddddd"
    assert data["title"] == "line one"
    mem = client.get("/api/memory/sessions", params={"repo": "metarepo"})
    assert mem.status_code == 200
    body = mem.json()
    assert len(body["sessions"]) == 1
    assert body["sessions"][0]["has_prompt"] is True


def test_normalize_tags_dedupe_and_limit() -> None:
    assert normalize_tags(["a", "a", "b"]) == ["a", "b"]
    with pytest.raises(ValueError):
        normalize_tags("not-a-list")  # type: ignore[arg-type]


def test_list_session_records_skips_missing_jules_output(tmp_state: Path) -> None:
    """Hand-written archive rows without jules_output must not break listing."""
    base = agent_sessions_dir("jules")
    minimal = {
        "agent": "jules",
        "repo_key": "demo",
        "title": "x",
        "session_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        "status": "new",
        "pulled": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "jules_exit_code": 0,
    }
    (base / "manual.json").write_text(json.dumps(minimal), encoding="utf-8")
    rows = list_session_records("jules", repo_key="demo")
    assert len(rows) == 1
    assert "jules_output" not in rows[0]


def test_patch_and_soft_delete(tmp_state: Path) -> None:
    sid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    combined = f"x {sid}\n"
    _, path = write_jules_session_record(
        repo_key="demo",
        title="t",
        prompt=None,
        combined_output=combined,
        exit_code=0,
    )
    rec = patch_memory_record(
        "jules",
        sid,
        "demo",
        summary="Done",
        tags=["wgx", "ci"],
    )
    assert rec["summary"] == "Done"
    assert rec["tags"] == ["wgx", "ci"]
    patch_memory_record("jules", sid, "demo", deleted=True)
    listed = list_session_records("jules", repo_key="demo", include_deleted=False)
    assert len(listed) == 0
    listed_del = list_session_records("jules", repo_key="demo", include_deleted=True)
    assert len(listed_del) == 1
    assert listed_del[0]["deleted"] is True


def test_api_jules_prompt_jules_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ACS_STATE_DIR", str(tmp_path))
    from fastapi.testclient import TestClient

    from panel import app as app_module
    from panel.runner import CmdResult

    monkeypatch.setattr(
        app_module,
        "run",
        lambda *a, **k: CmdResult(1, "", "jules crashed", ["jules", "new", "t"]),
    )
    client = TestClient(app_module.app)
    r = client.post("/api/jules/prompt", json={"repo": "metarepo", "prompt": "do work"})
    assert r.status_code == 502
    assert r.json()["ok"] is False
