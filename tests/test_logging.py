import pytest
from panel.logging import redact_secrets, _get_sensitive_env_values

@pytest.fixture(autouse=True)
def clear_env_cache():
    _get_sensitive_env_values.cache_clear()
    yield
    _get_sensitive_env_values.cache_clear()

def test_redact_secrets(monkeypatch):
    # Test env var redaction using monkeypatch
    monkeypatch.setenv("GH_TOKEN", "secret_gh_token")
    assert redact_secrets("Using GH_TOKEN=secret_gh_token here") == "Using GH_TOKEN=[redacted] here"

    # Test patterns
    assert redact_secrets("ghp_12345678901234567890") == "[redacted]"
    assert redact_secrets("github_pat_12345678901234567890_123456") == "[redacted]"

    # Test query params (URL context)
    assert redact_secrets("https://api.example.com?token=abcdef123") == "https://api.example.com?token=[redacted]"
    assert redact_secrets("https://api.example.com?access_token=xyz987&other=1") == "https://api.example.com?access_token=[redacted]&other=1"

    # Test text context (non-URL)
    assert redact_secrets("token=123 value") == "token=[redacted] value"
    assert redact_secrets("access_token=secret") == "access_token=[redacted]"

    # Test prefix safety (my_token should NOT be redacted)
    assert redact_secrets("my_token=123") == "my_token=123"
    assert redact_secrets("x_access_token=secret") == "x_access_token=secret"

    # Mixed
    text = "url?token=abc&key=ghp_12345678901234567890"
    redacted = redact_secrets(text)
    assert "token=[redacted]" in redacted
    assert "key=[redacted]" in redacted

def test_redact_secrets_substring_overlap(monkeypatch):
    # Test that longer secrets are redacted before shorter ones to prevent partial leaks
    # GH_TOKEN="abc"
    # OPENAI_API_KEY="abc12345"
    monkeypatch.setenv("GH_TOKEN", "abc")
    monkeypatch.setenv("OPENAI_API_KEY", "abc12345")

    text = "Here is the long secret: abc12345 and the short one: abc"
    redacted = redact_secrets(text)

    # If "abc" was replaced first, we might get "[redacted]12345" for the long one.
    # We expect both to be fully redacted.
    # Note: Since they both map to "[redacted]", the output might look identical
    # regardless of order IF the replacement string was different, but since it's the same,
    # let's verify no secret characters leak.

    assert "abc12345" not in redacted
    assert "abc" not in redacted

    # To be absolutely sure about order, let's look at the result.
    # "abc12345" -> "[redacted]" (1 replacement)
    # "abc" -> "[redacted]" (1 replacement)
    # expected: "Here is the long secret: [redacted] and the short one: [redacted]"
    assert redacted == "Here is the long secret: [redacted] and the short one: [redacted]"

def test_redact_secrets_deduplication(monkeypatch):
    # Ensure duplication doesn't cause issues
    monkeypatch.setenv("GH_TOKEN", "same_secret")
    monkeypatch.setenv("OPENAI_API_KEY", "same_secret")

    text = "value=same_secret"
    redacted = redact_secrets(text)
    assert redacted == "value=[redacted]"
