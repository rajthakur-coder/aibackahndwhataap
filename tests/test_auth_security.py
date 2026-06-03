import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.config import settings
from app.shared.tenant import reset_current_tenant_id, set_current_tenant_id, strict_tenant_id
from app.utils import create_token, decode_token, get_cookie_options


def _request_with_cookie(token: str | None, tenant_header: str | None = None) -> Request:
    headers = []
    if token:
        headers.append((b"cookie", f"access_token={token}".encode()))
    if tenant_header:
        headers.append((b"x-tenant-id", tenant_header.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/knowledge-base",
            "headers": headers,
        }
    )


def test_jwt_round_trip_keeps_user_id():
    token = create_token({"id": "user-1"})

    payload = decode_token(token)

    assert payload["id"] == "user-1"


def test_auth_cookie_uses_configured_security_flags():
    options = get_cookie_options("access_token", "token-value", max_age=60)

    assert options["httponly"] is True
    assert options["samesite"] == settings.COOKIE_SAMESITE
    assert options["secure"] is (settings.COOKIE_SECURE or settings.COOKIE_SAMESITE == "none")
    assert options["path"] == "/"
    assert options["max_age"] == 60


def test_strict_tenant_requires_token():
    with pytest.raises(HTTPException) as exc:
        strict_tenant_id(_request_with_cookie(None))

    assert exc.value.status_code == 401


def test_strict_tenant_rejects_header_mismatch():
    token = set_current_tenant_id("tenant-a")
    try:
        with pytest.raises(HTTPException) as exc:
            strict_tenant_id(_request_with_cookie("token", tenant_header="tenant-b"), x_tenant_id="tenant-b")

        assert exc.value.status_code == 403
    finally:
        reset_current_tenant_id(token)


def test_strict_tenant_accepts_matching_header():
    token = set_current_tenant_id("tenant-a")
    try:
        tenant_id = strict_tenant_id(_request_with_cookie("token", tenant_header="tenant-a"), x_tenant_id="tenant-a")

        assert tenant_id == "tenant-a"
    finally:
        reset_current_tenant_id(token)
