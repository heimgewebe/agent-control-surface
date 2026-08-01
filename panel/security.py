from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit

from starlette.requests import Request

MUTATING_HTTP_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
ACTOR_TOKEN_HEADER = "x-acs-actor-token"
CSRF_HEADER = "x-acs-csrf"
CSRF_COOKIE = "acs_csrf"
CSRF_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
FETCH_SITE_VALUES = frozenset({"same-origin", "same-site", "cross-site", "none"})
MAX_SECURITY_VALUE_LENGTH = 4096


class MutationAuthorizationError(ValueError):
    """A mutation request did not provide unambiguous authorization evidence."""


@dataclass(frozen=True)
class Origin:
    scheme: str
    host: str
    port: int


def mutation_shared_secret() -> str | None:
    """Return the actor secret without exposing it to callers or logs.

    ACS_ROUTINES_SHARED_SECRET remains a compatibility fallback for existing actor
    clients. New deployments should use ACS_MUTATION_SHARED_SECRET.
    """

    configured = os.getenv("ACS_MUTATION_SHARED_SECRET")
    if configured is None:
        configured = os.getenv("ACS_ROUTINES_SHARED_SECRET")
    if configured is None or not _well_formed_secret(configured):
        return None
    return configured


def authorize_mutation_request(request: Request) -> None:
    """Authorize an unsafe HTTP request using actor or browser evidence.

    Actor requests use one shared-secret header. Requests carrying browser evidence
    must also pass strict double-submit CSRF, same-origin, and Fetch Metadata checks;
    an actor token never bypasses those browser checks.
    """

    actor_token = _single_header(request, ACTOR_TOKEN_HEADER)
    csrf_header = _single_header(request, CSRF_HEADER)
    origin_value = _single_header(request, "origin")
    referer_value = _single_header(request, "referer")
    fetch_site = _single_header(request, "sec-fetch-site")
    _validate_optional_fetch_header(request, "sec-fetch-mode")
    _validate_optional_fetch_header(request, "sec-fetch-dest")
    csrf_cookie = _csrf_cookie(request)

    actor_valid = False
    if actor_token is not None:
        if not _well_formed_secret(actor_token):
            raise MutationAuthorizationError("Mutation authorization failed.")
        secret = mutation_shared_secret()
        if secret is None or not secrets.compare_digest(actor_token, secret):
            raise MutationAuthorizationError("Mutation authorization failed.")
        actor_valid = True

    browser_evidence = any(
        value is not None
        for value in (csrf_header, csrf_cookie, origin_value, referer_value, fetch_site)
    )
    if browser_evidence:
        _authorize_browser(
            request,
            csrf_header=csrf_header,
            csrf_cookie=csrf_cookie,
            origin_value=origin_value,
            referer_value=referer_value,
            fetch_site=fetch_site,
        )
        return

    if actor_valid:
        return

    raise MutationAuthorizationError("Mutation authorization required.")


def _authorize_browser(
    request: Request,
    *,
    csrf_header: str | None,
    csrf_cookie: str | None,
    origin_value: str | None,
    referer_value: str | None,
    fetch_site: str | None,
) -> None:
    if (
        csrf_header is None
        or csrf_cookie is None
        or CSRF_TOKEN_RE.fullmatch(csrf_header) is None
        or CSRF_TOKEN_RE.fullmatch(csrf_cookie) is None
        or not secrets.compare_digest(csrf_header, csrf_cookie)
    ):
        raise MutationAuthorizationError("Browser mutation authorization failed.")

    if origin_value is None and referer_value is None:
        raise MutationAuthorizationError("Same-origin evidence required.")

    expected = _expected_origin(request)
    supplied: list[Origin] = []
    if origin_value is not None:
        supplied.append(_parse_origin_header(origin_value))
    if referer_value is not None:
        supplied.append(_parse_referer_header(referer_value))
    if any(value != expected for value in supplied):
        raise MutationAuthorizationError("Same-origin evidence rejected.")
    if len(set(supplied)) != 1:
        raise MutationAuthorizationError("Conflicting same-origin evidence.")

    if fetch_site is not None:
        normalized = fetch_site.lower()
        if normalized not in FETCH_SITE_VALUES or normalized != "same-origin":
            raise MutationAuthorizationError("Cross-site mutation rejected.")


def _single_header(request: Request, name: str) -> str | None:
    encoded_name = name.encode("ascii")
    values = [
        value.decode("latin-1")
        for key, value in request.scope.get("headers", [])
        if key.lower() == encoded_name
    ]
    if not values:
        return None
    if len(values) != 1:
        raise MutationAuthorizationError("Duplicate security evidence rejected.")
    value = values[0]
    if not _well_formed_header_value(value):
        raise MutationAuthorizationError("Malformed security evidence rejected.")
    return value


def _validate_optional_fetch_header(request: Request, name: str) -> None:
    value = _single_header(request, name)
    if value is None:
        return
    if not re.fullmatch(r"[a-z0-9-]+", value.lower()):
        raise MutationAuthorizationError("Malformed Fetch Metadata rejected.")


def _csrf_cookie(request: Request) -> str | None:
    raw_cookie_headers = [
        value.decode("latin-1")
        for key, value in request.scope.get("headers", [])
        if key.lower() == b"cookie"
    ]
    if not raw_cookie_headers:
        return None
    if len(raw_cookie_headers) != 1:
        raise MutationAuthorizationError("Duplicate cookie evidence rejected.")

    csrf_values: list[str] = []
    for part in raw_cookie_headers[0].split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        name, value = item.split("=", 1)
        if name.strip() == CSRF_COOKIE:
            csrf_values.append(value)
    if not csrf_values:
        return None
    if len(csrf_values) != 1:
        raise MutationAuthorizationError("Duplicate CSRF cookie rejected.")
    value = csrf_values[0]
    if not _well_formed_header_value(value):
        raise MutationAuthorizationError("Malformed CSRF cookie rejected.")
    return value


def _expected_origin(request: Request) -> Origin:
    public_origin = os.getenv("ACS_PUBLIC_ORIGIN")
    if public_origin:
        configured = public_origin.strip()
        if configured != public_origin:
            raise MutationAuthorizationError("ACS_PUBLIC_ORIGIN is malformed.")
        return _parse_origin(configured, allow_root_path=True)

    # Host is security-sensitive when no fixed public origin is configured.
    host = _single_header(request, "host")
    if host is None:
        raise MutationAuthorizationError("Host evidence required.")
    return _parse_origin(str(request.base_url).rstrip("/"), allow_root_path=True)


def _parse_origin_header(value: str) -> Origin:
    return _parse_origin(value, allow_root_path=False)


def _parse_referer_header(value: str) -> Origin:
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise MutationAuthorizationError("Malformed Referer rejected.") from exc
    if parsed.fragment:
        raise MutationAuthorizationError("Malformed Referer rejected.")
    return _origin_from_split(parsed)


def _parse_origin(value: str, *, allow_root_path: bool) -> Origin:
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise MutationAuthorizationError("Malformed Origin rejected.") from exc
    allowed_paths = {"", "/"} if allow_root_path else {""}
    if parsed.path not in allowed_paths or parsed.query or parsed.fragment:
        raise MutationAuthorizationError("Malformed Origin rejected.")
    return _origin_from_split(parsed)


def _origin_from_split(parsed: SplitResult) -> Origin:
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.netloc:
        raise MutationAuthorizationError("Malformed origin evidence rejected.")
    if parsed.username is not None or parsed.password is not None:
        raise MutationAuthorizationError("Malformed origin evidence rejected.")
    try:
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise MutationAuthorizationError("Malformed origin evidence rejected.") from exc
    if not host or any(ord(char) < 33 for char in host):
        raise MutationAuthorizationError("Malformed origin evidence rejected.")
    normalized_port = port if port is not None else (443 if scheme == "https" else 80)
    return Origin(scheme=scheme, host=host.lower().rstrip("."), port=normalized_port)


def _well_formed_header_value(value: str) -> bool:
    if not value or value != value.strip() or len(value) > MAX_SECURITY_VALUE_LENGTH:
        return False
    return all(32 <= ord(char) != 127 for char in value)


def _well_formed_secret(value: str) -> bool:
    return (
        _well_formed_header_value(value)
        and "," not in value
        and not any(char.isspace() for char in value)
    )
