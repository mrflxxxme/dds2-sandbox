"""
Tests for backend/main.py — _extract_user_id_from_headers (AuditLogMiddleware).

Regression for bug observed 2026-06-22: JWT `sub` is `str(user_id)` (see
backend/auth.py:create_access_token), and the middleware returned it verbatim.
The audit_log.user_id column is INTEGER → asyncpg raised DataError
("'str' object cannot be interpreted as an integer") on every audited mutation,
so audit logging silently failed. The extractor must return an `int`.
"""

import pytest

from backend.auth import create_access_token
from backend.main import _extract_user_id_from_headers


def _auth_headers(token: str) -> dict[bytes, bytes]:
    return {b"authorization": b"Bearer " + token.encode()}


def test_extract_user_id_returns_int_not_str():
    token = create_access_token(7, "alice")
    uid = _extract_user_id_from_headers(_auth_headers(token))
    assert uid == 7
    assert isinstance(uid, int)  # not "7" — INTEGER column rejects str


def test_extract_user_id_no_auth_returns_none():
    assert _extract_user_id_from_headers({}) is None


def test_extract_user_id_garbage_token_returns_none():
    assert _extract_user_id_from_headers({b"authorization": b"Bearer not.a.jwt"}) is None
