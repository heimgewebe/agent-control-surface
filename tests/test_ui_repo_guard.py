from pathlib import Path


def test_repo_placeholder_option_present() -> None:
    html = Path("panel/templates/index.html").read_text(encoding="utf-8")
    assert 'option value=""' in html
    assert "Repo auswählen" in html


def test_publish_button_requires_repo_flag() -> None:
    html = Path("panel/templates/index.html").read_text(encoding="utf-8")
    assert 'data-repo-required="true">Publish (Push + PR)' in html


def test_publish_uses_repo_query_param() -> None:
    html = Path("panel/templates/index.html").read_text(encoding="utf-8")
    assert "/api/git/publish?repo=" in html
    assert "ensureRepo" in html
