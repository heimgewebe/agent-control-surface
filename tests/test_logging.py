import os
from panel.logging import redact_secrets

def test_redact_secrets():
    # Test env var redaction
    os.environ["GH_TOKEN"] = "secret_gh_token"
    assert redact_secrets("Using GH_TOKEN=secret_gh_token here") == "Using GH_TOKEN=[redacted] here"
    del os.environ["GH_TOKEN"]

    # Test patterns
    assert redact_secrets("ghp_12345678901234567890") == "[redacted]"
    assert redact_secrets("github_pat_12345678901234567890_123456") == "[redacted]"

    # Test query params
    assert redact_secrets("https://api.example.com?token=abcdef123") == "https://api.example.com?token=[redacted]"
    assert redact_secrets("https://api.example.com?access_token=xyz987&other=1") == "https://api.example.com?access_token=[redacted]&other=1"
    assert redact_secrets("token=123 value") == "token=[redacted] value"

    # Mixed
    text = "url?token=abc&key=ghp_12345678901234567890"
    redacted = redact_secrets(text)
    assert "token=[redacted]" in redacted
    assert "key=[redacted]" in redacted
