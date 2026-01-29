from pathlib import Path


def test_ui_error_formatting_has_safe_stringify_fallback() -> None:
    html = Path("panel/templates/index.html").read_text(encoding="utf-8")
    assert "JSON.stringify" in html
    assert "String(" in html
    assert "safeStringify(" in html
    assert "truncateText(" in html
    assert '"[Circular]"' in html
    assert "throw new Error(String(" in html
    assert "constructor.name" in html or "constructor" in html
    assert "Object.keys" in html
