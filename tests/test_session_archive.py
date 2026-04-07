import json
from pathlib import Path

import pytest

from panel.session_archive import (
    agent_sessions_dir,
    extract_jules_session_id,
    list_session_records,
    normalize_tags,
    patch_memory_record,
    reconcile_pending_record,
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
    pytest.importorskip("fastapi")
    monkeypatch.setenv("ACS_STATE_DIR", str(tmp_path))
    from fastapi.testclient import TestClient

    from panel import app as app_module
    from panel.runner import CmdResult

    captured: dict = {}

    def fake_run_ok(cmd, cwd, timeout=60, env=None, input_text=None):
        captured["input_text"] = input_text
        return CmdResult(
            0,
            "ok\nsession_id: 99999999-aaaa-bbbb-cccc-dddddddddddd\n",
            "",
            list(cmd),
        )

    monkeypatch.setattr(app_module, "run", fake_run_ok)
    client = TestClient(app_module.app)
    r = client.post("/api/jules/prompt", json={"repo": "metarepo", "prompt": "line one\nrest\n "})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["session_id"] == "99999999-aaaa-bbbb-cccc-dddddddddddd"
    assert data["title"] == "line one"
    # Full prompt must be forwarded to jules new via stdin
    assert captured["input_text"] == "line one\nrest\n "
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
    pytest.importorskip("fastapi")
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


def test_write_jules_session_record_pending_flag(tmp_state: Path) -> None:
    """Records without a recognized session ID must carry is_pending=True."""
    rec, path = write_jules_session_record(
        repo_key="demo",
        title="pending task",
        prompt="do something",
        combined_output="no id in here",
        exit_code=0,
    )
    assert rec["is_pending"] is True
    assert rec["session_id"] is None
    assert path.name.startswith("_pending_")
    assert rec["status"] == "running"


def test_write_jules_session_record_not_pending_when_id_found(tmp_state: Path) -> None:
    """Records with a recognized session ID must carry is_pending=False."""
    sid = "12345678-1234-1234-1234-1234567890ab"
    rec, path = write_jules_session_record(
        repo_key="demo",
        title="known task",
        prompt=None,
        combined_output=f"done {sid}",
        exit_code=0,
    )
    assert rec["is_pending"] is False
    assert rec["session_id"] == sid
    assert path.name == f"{sid}.json"


def test_reconcile_pending_record(tmp_state: Path) -> None:
    """reconcile_pending_record migrates _pending_*.json to <session_id>.json."""
    rec, pending_path = write_jules_session_record(
        repo_key="demo",
        title="t",
        prompt="my prompt",
        combined_output="no uuid here",
        exit_code=0,
    )
    assert rec["is_pending"] is True
    real_sid = "aabbccdd-1111-2222-3333-444444444444"
    result = reconcile_pending_record("jules", pending_path, real_sid)
    assert result is not None
    updated_rec, new_path = result
    assert updated_rec["session_id"] == real_sid
    assert updated_rec["is_pending"] is False
    assert updated_rec["status"] == "new"
    assert new_path.name == f"{real_sid}.json"
    assert new_path.exists()
    assert not pending_path.exists()


def test_reconcile_pending_record_no_op_if_session_id_present(tmp_state: Path) -> None:
    """reconcile_pending_record returns None when record already has a session_id."""
    sid = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    _, path = write_jules_session_record(
        repo_key="demo",
        title="t",
        prompt=None,
        combined_output=f"id {sid}",
        exit_code=0,
    )
    result = reconcile_pending_record("jules", path, "00000000-0000-0000-0000-000000000001")
    assert result is None


def test_list_session_records_tolerates_stat_failure(tmp_state: Path) -> None:
    """list_session_records must not raise when stat() fails on a file."""
    base = agent_sessions_dir("jules")
    sid = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    combined = f"ok {sid}\n"
    write_jules_session_record(
        repo_key="demo",
        title="t",
        prompt=None,
        combined_output=combined,
        exit_code=0,
    )
    # Simulate a stat failure by monkeypatching Path.stat to raise for .json files
    original_stat = Path.stat

    def flaky_stat(self, *args, **kwargs):
        if self.suffix == ".json":
            raise OSError("simulated stat failure")
        return original_stat(self, *args, **kwargs)

    import unittest.mock as mock

    with mock.patch.object(Path, "stat", flaky_stat):
        rows = list_session_records("jules", repo_key="demo")
    # Should not raise; files with failed stat get mtime=0.0 and are still listed
    assert isinstance(rows, list)
