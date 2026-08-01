from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from panel.app import app
from panel.runner import CmdResult
from panel.security import MUTATING_HTTP_METHODS

MUTATING_ROUTE_INVENTORY = {
    ("POST", "/api/sessions/new"),
    ("POST", "/api/jules/prompt"),
    ("PATCH", "/api/memory/sessions/{session_id}"),
    ("DELETE", "/api/memory/sessions/{session_id}"),
    ("POST", "/api/patch/apply"),
    ("POST", "/api/patch/apply.json"),
    ("POST", "/api/git/branch"),
    ("POST", "/api/git/commit"),
    ("POST", "/api/git/commit.json"),
    ("POST", "/api/git/push"),
    ("POST", "/api/git/push.json"),
    ("POST", "/api/git/publish"),
    ("POST", "/api/git/health/diagnose"),
    ("POST", "/api/git/health/repair/stage-a"),
    ("POST", "/api/git/health/repair/stage-b"),
    ("POST", "/api/git/health/repair/stage-c"),
    ("POST", "/api/audit/git"),
    ("POST", "/api/routine/preview"),
    ("POST", "/api/routine/apply"),
}


def _mutating_routes() -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods or set()
        if method in MUTATING_HTTP_METHODS
    }


def _browser_headers(token: str) -> dict[str, str]:
    return {
        "X-ACS-CSRF": token,
        "Origin": "http://testserver",
        "Sec-Fetch-Site": "same-origin",
    }


def test_route_inventory_and_global_boundary() -> None:
    assert _mutating_routes() == MUTATING_ROUTE_INVENTORY
    client = TestClient(app)
    for method, route_path in sorted(MUTATING_ROUTE_INVENTORY):
        path = route_path.replace("{session_id}", "inventory-session")
        response = client.request(method, path)
        assert response.status_code == 403, (method, route_path, response.text)


@pytest.mark.parametrize(
    ("headers", "cookie"),
    [
        ({}, None),
        ({"X-ACS-Actor-Token": "wrong"}, None),
        ({"X-ACS-Actor-Token": " expected"}, None),
        ({"X-ACS-Actor-Token": "expected,expected"}, None),
        ({"X-ACS-CSRF": "not-hex", "Origin": "http://testserver"}, "not-hex"),
        ({"X-ACS-CSRF": "a" * 32, "Origin": "null"}, "a" * 32),
        ({"X-ACS-CSRF": "a" * 32, "Origin": "http://testserver/"}, "a" * 32),
    ],
)
def test_missing_wrong_or_malformed_authorization_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
    cookie: str | None,
) -> None:
    monkeypatch.setenv("ACS_MUTATION_SHARED_SECRET", "expected")
    client = TestClient(app)
    if cookie is not None:
        client.cookies.set("acs_csrf", cookie)
    response = client.post("/api/git/branch", headers=headers)
    assert response.status_code == 403


@pytest.mark.parametrize(
    "headers",
    [
        {"X-ACS-CSRF": "a" * 32},
        {"X-ACS-CSRF": "a" * 32, "Origin": "https://attacker.example"},
        {"X-ACS-CSRF": "a" * 32, "Referer": "http://testserver.evil/path"},
        {
            "X-ACS-CSRF": "a" * 32,
            "Origin": "http://testserver",
            "Referer": "https://attacker.example/path",
        },
        {
            "X-ACS-CSRF": "a" * 32,
            "Origin": "http://testserver",
            "Sec-Fetch-Site": "cross-site",
        },
        {
            "X-ACS-CSRF": "a" * 32,
            "Origin": "http://testserver",
            "Sec-Fetch-Site": "same-site",
        },
    ],
)
def test_browser_origin_referer_and_fetch_metadata_fail_closed(
    headers: dict[str, str],
) -> None:
    client = TestClient(app)
    client.cookies.set("acs_csrf", "a" * 32)
    response = client.post("/api/git/branch", headers=headers)
    assert response.status_code == 403


def test_valid_browser_evidence_reaches_route_validation() -> None:
    token = "b" * 32
    client = TestClient(app)
    client.cookies.set("acs_csrf", token)
    response = client.post("/api/git/branch", headers=_browser_headers(token))
    assert response.status_code == 422


def test_valid_referer_fallback_reaches_route_validation() -> None:
    token = "c" * 32
    client = TestClient(app)
    client.cookies.set("acs_csrf", token)
    response = client.post(
        "/api/git/branch",
        headers={"X-ACS-CSRF": token, "Referer": "http://testserver/ui"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "headers",
    [
        [
            ("X-ACS-CSRF", "d" * 32),
            ("X-ACS-CSRF", "d" * 32),
            ("Origin", "http://testserver"),
        ],
        [
            ("X-ACS-CSRF", "d" * 32),
            ("Origin", "http://testserver"),
            ("Origin", "http://testserver"),
        ],
        [
            ("X-ACS-Actor-Token", "expected"),
            ("X-ACS-Actor-Token", "expected"),
        ],
    ],
)
def test_duplicate_security_headers_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    headers: list[tuple[str, str]],
) -> None:
    monkeypatch.setenv("ACS_MUTATION_SHARED_SECRET", "expected")
    client = TestClient(app)
    client.cookies.set("acs_csrf", "d" * 32)
    response = client.post("/api/git/branch", headers=headers)
    assert response.status_code == 403


def test_duplicate_csrf_cookie_is_rejected() -> None:
    token = "e" * 32
    client = TestClient(app)
    response = client.post(
        "/api/git/branch",
        headers={
            **_browser_headers(token),
            "Cookie": f"acs_csrf={token}; acs_csrf={token}",
        },
    )
    assert response.status_code == 403


def test_wrong_actor_token_cannot_fall_back_to_valid_browser_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACS_MUTATION_SHARED_SECRET", "expected")
    token = "f" * 32
    client = TestClient(app)
    client.cookies.set("acs_csrf", token)
    response = client.post(
        "/api/git/branch",
        headers={**_browser_headers(token), "X-ACS-Actor-Token": "wrong"},
    )
    assert response.status_code == 403


def test_actor_secret_uses_constant_time_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACS_MUTATION_SHARED_SECRET", "expected")
    compared: list[tuple[str, str]] = []

    def compare_digest(left: str, right: str) -> bool:
        compared.append((left, right))
        return False

    monkeypatch.setattr("panel.security.secrets.compare_digest", compare_digest)
    response = TestClient(app).post(
        "/api/git/branch",
        headers={"X-ACS-Actor-Token": "wrong"},
    )
    assert response.status_code == 403
    assert compared == [("wrong", "expected")]


def test_actor_secret_is_not_logged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "do-not-log-this-actor-secret"
    monkeypatch.setenv("ACS_MUTATION_SHARED_SECRET", "different")
    response = TestClient(app).post(
        "/api/git/branch",
        headers={"X-ACS-Actor-Token": secret},
    )
    assert response.status_code == 403
    assert secret not in caplog.text


def test_read_route_remains_accessible_without_mutation_evidence() -> None:
    target = MagicMock(path="/tmp/repo")
    with (
        patch("panel.app.get_repo", return_value=target),
        patch(
            "panel.app.run",
            return_value=CmdResult(0, "## feature\n", "", ["git", "status"]),
        ),
    ):
        response = TestClient(app).get("/api/git/status?repo=metarepo")
    assert response.status_code == 200
    assert response.text == "## feature\n"
