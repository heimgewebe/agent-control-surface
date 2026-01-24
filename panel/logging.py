from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_LOG_PATH = Path("~/.local/state/agent-control-surface/actions.log").expanduser()


@dataclass(frozen=True)
class ActionLogConfig:
    enabled: bool
    path: Path | None


def resolve_action_log_config() -> ActionLogConfig:
    env_value = os.getenv("ACS_ACTION_LOG")
    if not env_value:
        return ActionLogConfig(enabled=False, path=None)
    normalized = env_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return ActionLogConfig(enabled=True, path=DEFAULT_LOG_PATH)
    return ActionLogConfig(enabled=True, path=Path(env_value).expanduser())


def log_action(record: dict[str, Any]) -> None:
    config = resolve_action_log_config()
    if not config.enabled or config.path is None:
        return
    config.path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **record,
    }
    with config.path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
