from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
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


@dataclass(frozen=True)
class ActionLogConfig:
    enabled: bool
    path: Path | None


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


def resolve_daily_log_path() -> Path:
    date_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return DEFAULT_LOG_DIR / f"{date_tag}.jsonl"


def log_action(record: dict[str, Any], *, job_id: str | None = None) -> None:
    config = resolve_action_log_config()
    if not config.enabled:
        return
    log_path = config.path or resolve_daily_log_path()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **redact_record(record),
        }
        if job_id:
            payload["job_id"] = job_id
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        return


def redact_record(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: redact_record(val) for key, val in value.items()}
    if isinstance(value, list):
        return [redact_record(item) for item in value]
    if isinstance(value, str):
        return redact_secrets(value)
    return value


@lru_cache(maxsize=1)
def _get_sensitive_env_values() -> list[str]:
    values = []
    for key in SENSITIVE_ENV_KEYS:
        env_value = os.getenv(key)
        if env_value:
            values.append(env_value)
    return values


def redact_secrets(text: str) -> str:
    redacted = text
    for env_value in _get_sensitive_env_values():
        redacted = redacted.replace(env_value, "[redacted]")
    redacted = re.sub(r"ghp_[A-Za-z0-9]{20,}", "[redacted]", redacted)
    redacted = re.sub(r"github_pat_[A-Za-z0-9_]{20,}", "[redacted]", redacted)
    # Redact token= and access_token= in any text (URL or not), but avoid matching my_token=
    redacted = re.sub(r"(?<![A-Za-z0-9_])(token|access_token)=[^&\s]+", r"\1=[redacted]", redacted)
    return redacted
