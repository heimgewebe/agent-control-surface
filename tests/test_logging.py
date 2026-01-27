from panel.logging import redact_secrets

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
