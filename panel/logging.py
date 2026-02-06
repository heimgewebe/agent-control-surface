from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT_LOG_DIR = Path("~/.local/state/agent-control-surface/logs").expanduser()
SENSITIVE_ENV_KEYS = [
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "COHERE_API_KEY",
]

GHP_PATTERN = re.compile(r"ghp_[A-Za-z0-9]{20,}")
GITHUB_PAT_PATTERN = re.compile(r"github_pat_[A-Za-z0-9_]{20,}")
# Redact token= and access_token= in any text (URL or not), but avoid matching my_token=
TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9_])(token|access_token)=[^&\s]+")


@dataclass(frozen=True)
class ActionLogConfig:
    enabled: bool
    path: Path | None


@lru_cache(maxsize=1)
def resolve_action_log_config() -> ActionLogConfig:
    env_value = os.getenv("ACS_ACTION_LOG", "").strip()
    if not env_value:
        return ActionLogConfig(enabled=False, path=None)
    normalized = env_value.lower()
    if normalized in {"0", "false", "no", "off"}:
        return ActionLogConfig(enabled=False, path=None)
    if normalized in {"1", "true", "yes", "on"}:
        return ActionLogConfig(enabled=True, path=None)
    return ActionLogConfig(enabled=True, path=Path(env_value).expanduser())


# Cache a small window of dates to tolerate day rollover/tests/backfills in long-running processes.
@lru_cache(maxsize=8)
def _get_log_path_for_date(date_obj: date) -> Path:
    date_tag = date_obj.strftime("%Y-%m-%d")
    return DEFAULT_LOG_DIR / f"{date_tag}.jsonl"


def resolve_daily_log_path() -> Path:
    return _get_log_path_for_date(datetime.now(timezone.utc).date())


class FileLogger:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current_path: Path | None = None
        self._file_handle: Any = None

    def log(self, payload: dict[str, Any], path: Path) -> None:
        try:
            line = json.dumps(payload, ensure_ascii=False) + "\n"
        except (TypeError, ValueError):
            return  # Best-effort: ignore serialization errors

        with self._lock:
            if path != self._current_path:
                self._rotate(path)

            if self._file_handle:
                try:
                    self._write(line)
                except OSError:
                    # Try to recover once
                    try:
                        self._rotate(path)
                        if self._file_handle:
                            self._write(line)
                    except OSError:
                        # Logging is best-effort; ignore if retry fails.
                        pass

    def _write(self, line: str) -> None:
        self._file_handle.write(line)
        self._file_handle.flush()

    def _rotate(self, new_path: Path) -> None:
        if self._file_handle:
            try:
                self._file_handle.close()
            except OSError:
                # Ignore close errors; handle is reset and reopened later.
                pass
            self._file_handle = None

        self._current_path = new_path
        try:
            new_path.parent.mkdir(parents=True, exist_ok=True)
            self._file_handle = new_path.open("a", encoding="utf-8")
        except OSError:
            self._file_handle = None
            self._current_path = None  # Reset so we try again next time


_LOGGER = FileLogger()


def log_action(record: dict[str, Any], *, job_id: str | None = None) -> None:
    config = resolve_action_log_config()
    if not config.enabled:
        return
    log_path = config.path or resolve_daily_log_path()

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **redact_record(record),
    }
    if job_id:
        payload["job_id"] = job_id

    _LOGGER.log(payload, log_path)


def redact_record(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: redact_record(val) for key, val in value.items()}
    if isinstance(value, list):
        return [redact_record(item) for item in value]
    if isinstance(value, str):
        return redact_secrets(value)
    return value


@lru_cache(maxsize=1)
def _get_sensitive_env_values() -> tuple[str, ...]:
    values = []
    for key in SENSITIVE_ENV_KEYS:
        env_value = os.getenv(key)
        if env_value:
            values.append(env_value)
    # Deduplicate and sort by length descending to handle substring overlaps
    # (e.g. ensure "token123" is redacted before "token")
    unique_values = list(dict.fromkeys(values))
    unique_values.sort(key=len, reverse=True)
    return tuple(unique_values)


def redact_secrets(text: str) -> str:
    redacted = text
    for env_value in _get_sensitive_env_values():
        redacted = redacted.replace(env_value, "[redacted]")
    redacted = GHP_PATTERN.sub("[redacted]", redacted)
    redacted = GITHUB_PAT_PATTERN.sub("[redacted]", redacted)
    redacted = TOKEN_PATTERN.sub(r"\1=[redacted]", redacted)
    return redacted
