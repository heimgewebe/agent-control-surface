import os
import timeit
from datetime import datetime, timezone
from pathlib import Path

from panel.logging import (
    DEFAULT_LOG_DIR,
    ActionLogConfig,
    _get_sensitive_env_values,
    resolve_action_log_config,
    resolve_daily_log_path,
)

def run_benchmark():
    # Setup Environment
    os.environ["ACS_ACTION_LOG"] = "true"
    os.environ["GH_TOKEN"] = "DUMMY_TOKEN_FOR_BENCHMARK"

    # Clear caches to ensure clean start
    resolve_action_log_config.cache_clear()
    _get_sensitive_env_values.cache_clear()

    print("=== Performance Benchmark: panel.logging ===\n")

    # --- 1. Action Log Config Resolution ---

    def resolve_action_log_config_legacy() -> ActionLogConfig:
        """Uncached version (Legacy) for comparison."""
        env_value = os.getenv("ACS_ACTION_LOG", "").strip()
        if not env_value:
            return ActionLogConfig(enabled=False, path=None)
        normalized = env_value.lower()
        if normalized in {"0", "false", "no", "off"}:
            return ActionLogConfig(enabled=False, path=None)
        if normalized in {"1", "true", "yes", "on"}:
            return ActionLogConfig(enabled=True, path=None)
        return ActionLogConfig(enabled=True, path=Path(env_value).expanduser())

    print("--- resolve_action_log_config (100,000 runs) ---")
    t_config_legacy = timeit.timeit(resolve_action_log_config_legacy, number=100000)
    t_config_current = timeit.timeit(resolve_action_log_config, number=100000)

    print(f"Legacy (Uncached): {t_config_legacy:.4f} s")
    print(f"Current (Cached):  {t_config_current:.4f} s")
    if t_config_current > 0:
        print(f"Speedup:           {t_config_legacy / t_config_current:.2f}x")


    # --- 2. Daily Log Path Resolution ---

    def resolve_daily_log_path_legacy() -> Path:
        """Uncached version (Legacy) for comparison."""
        date_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return DEFAULT_LOG_DIR / f"{date_tag}.jsonl"

    print("\n--- resolve_daily_log_path (100,000 runs) ---")
    t_daily_legacy = timeit.timeit(resolve_daily_log_path_legacy, number=100000)
    t_daily_current = timeit.timeit(resolve_daily_log_path, number=100000)

    print(f"Legacy (Uncached): {t_daily_legacy:.4f} s")
    print(f"Current (Cached):  {t_daily_current:.4f} s")
    if t_daily_current > 0:
        print(f"Speedup:           {t_daily_legacy / t_daily_current:.2f}x")

if __name__ == "__main__":
    run_benchmark()
