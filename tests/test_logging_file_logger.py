import json
import pytest
from pathlib import Path
from panel.logging import FileLogger

def test_file_logger_append(tmp_path):
    """Test that multiple writes append correctly to the file."""
    logger = FileLogger()
    log_file = tmp_path / "test.jsonl"

    logger.log({"a": 1}, log_file)
    logger.log({"b": 2}, log_file)

    content = log_file.read_text(encoding="utf-8")
    lines = content.strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"a": 1}
    assert json.loads(lines[1]) == {"b": 2}

def test_file_logger_rotation(tmp_path):
    """Test that rotation occurs when path changes."""
    logger = FileLogger()
    path1 = tmp_path / "log1.jsonl"
    path2 = tmp_path / "log2.jsonl"

    logger.log({"msg": "file1"}, path1)

    # Switch path
    logger.log({"msg": "file2"}, path2)

    # Verify contents
    assert json.loads(path1.read_text(encoding="utf-8").strip()) == {"msg": "file1"}
    assert json.loads(path2.read_text(encoding="utf-8").strip()) == {"msg": "file2"}

def test_file_logger_retry_on_oserror(tmp_path, monkeypatch):
    """Test that retry logic works when OSError occurs once during write."""
    logger = FileLogger()
    log_file = tmp_path / "retry.jsonl"

    # Initialize logger
    logger.log({"init": True}, log_file)

    original_write = logger._write
    call_count = 0

    def mock_write(line):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OSError("Simulated write failure")
        original_write(line)

    # Use monkeypatch on the instance
    monkeypatch.setattr(logger, "_write", mock_write)

    logger.log({"retry": True}, log_file)

    # Verify it retried (called twice: once failed, once succeeded)
    assert call_count == 2

    # Verify content was written eventually
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1]) == {"retry": True}

def test_file_logger_serialization_error():
    """Test that serialization errors are ignored (best-effort)."""
    logger = FileLogger()

    class Unserializable:
        pass

    try:
        logger.log({"obj": Unserializable()}, Path("dummy"))
    except Exception as e:
        pytest.fail(f"Logger raised exception on serialization error: {e}")
