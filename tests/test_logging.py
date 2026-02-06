import pytest
from pathlib import Path
from unittest.mock import patch

from panel.logging import (
    redact_secrets,
    _get_sensitive_env_values,
    resolve_action_log_config,
    ActionLogConfig,
)

@pytest.fixture(autouse=True)
def clear_env_cache():
    _get_sensitive_env_values.cache_clear()
    resolve_action_log_config.cache_clear()
    yield
    _get_sensitive_env_values.cache_clear()
    resolve_action_log_config.cache_clear()


def test_redact_secrets(monkeypatch):
    # Test env var redaction
    monkeypatch.setenv("GH_TOKEN", "secret_gh_token")
    assert redact_secrets("Using GH_TOKEN=secret_gh_token here") == "Using GH_TOKEN=[redacted] here"

    # Test token patterns
    assert redact_secrets("ghp_12345678901234567890") == "[redacted]"
    assert redact_secrets("github_pat_12345678901234567890_123456") == "[redacted]"

    # Test query params (URL context)
    assert redact_secrets("https://api.example.com?token=abcdef123") == \
        "https://api.example.com?token=[redacted]"
    assert redact_secrets("https://api.example.com?access_token=xyz987&other=1") == \
        "https://api.example.com?access_token=[redacted]&other=1"

    # Test non-URL context
    assert redact_secrets("token=123 value") == "token=[redacted] value"
    assert redact_secrets("access_token=secret") == "access_token=[redacted]"

    # Prefix safety (should NOT redact)
    assert redact_secrets("my_token=123") == "my_token=123"
    assert redact_secrets("x_access_token=secret") == "x_access_token=secret"

    # Mixed
    text = "url?token=abc&key=ghp_12345678901234567890"
    redacted = redact_secrets(text)
    assert "token=[redacted]" in redacted
    assert "key=[redacted]" in redacted


def test_redact_secrets_substring_overlap(monkeypatch):
    # Ensure longer secrets are redacted first
    monkeypatch.setenv("GH_TOKEN", "abc")
    monkeypatch.setenv("OPENAI_API_KEY", "abc12345")

    text = "Here is the long secret: abc12345 and the short one: abc"
    redacted = redact_secrets(text)

    assert "abc12345" not in redacted
    assert "abc" not in redacted
    assert redacted.count("[redacted]") == 2


def test_redact_secrets_deduplication(monkeypatch):
    # Same secret in multiple env vars should not cause issues
    monkeypatch.setenv("GH_TOKEN", "same_secret")
    monkeypatch.setenv("OPENAI_API_KEY", "same_secret")

    text = "value=same_secret"
    redacted = redact_secrets(text)
    assert redacted == "value=[redacted]"


def test_resolve_action_log_config(monkeypatch):
    # Default: disabled
    monkeypatch.delenv("ACS_ACTION_LOG", raising=False)
    resolve_action_log_config.cache_clear()
    assert resolve_action_log_config() == ActionLogConfig(enabled=False, path=None)

    # Explicit disabled values
    for val in ["0", "false", "no", "off"]:
        monkeypatch.setenv("ACS_ACTION_LOG", val)
        resolve_action_log_config.cache_clear()
        assert resolve_action_log_config() == ActionLogConfig(enabled=False, path=None)

    # Explicit enabled values
    for val in ["1", "true", "yes", "on"]:
        monkeypatch.setenv("ACS_ACTION_LOG", val)
        resolve_action_log_config.cache_clear()
        assert resolve_action_log_config() == ActionLogConfig(enabled=True, path=None)

    # Enabled with path
    monkeypatch.setenv("ACS_ACTION_LOG", "/tmp/log.jsonl")
    resolve_action_log_config.cache_clear()
    assert resolve_action_log_config() == ActionLogConfig(
        enabled=True,
        path=Path("/tmp/log.jsonl"),
    )


def test_resolve_action_log_config_caching(monkeypatch):
    # Initial state
    monkeypatch.setenv("ACS_ACTION_LOG", "true")
    resolve_action_log_config.cache_clear()
    assert resolve_action_log_config().enabled is True

    # Change env without clearing cache -> cached value must remain
    monkeypatch.setenv("ACS_ACTION_LOG", "false")
    assert resolve_action_log_config().enabled is True

    # After cache clear -> new value must be picked up
    resolve_action_log_config.cache_clear()
    assert resolve_action_log_config().enabled is False