"""
Faktura.ru (ЦФТ) private HTTP/JSON API client — read-only statement access.

Reverse-engineered from the official 1C extension (Faktura-DirectBank). Talks to
the same endpoints the extension uses, on host ``business.faktura.ru`` under the
``/erpws/2.0`` prefix, authenticated by a short-lived JWT (Bearer).

Used by ``services/faktura_service.py`` for the 4×/day statement auto-sync.
READ surface (auth + accounts + motions) is verified live. WRITE surface
(``validate_payment`` / ``save_payment``) creates a payment DRAFT (черновик
платёжки) — the document is still signed by a human in the bank afterwards.

⚠ WRITE ENDPOINTS ARE UNVERIFIED. The paths/payload below are reconstructed from
the 1C ``Faktura-DirectBank`` extension and have NOT been exercised against the
live bank (no sandbox). They MUST be confirmed with the bank before the first
live ``save_payment`` (do a 1₽ self-transfer test). ``save_payment`` is an
IRREVERSIBLE real-money mutation — call only behind an explicit ``confirm`` gate.

Auth model (login/password):
    POST /erpws/2.0/login/authByUsernamePassword
        Content-Type: application/x-www-form-urlencoded
        username=f2b-<login>&password=<password>
      → {"token": "<JWT>"}            # use as Authorization: Bearer <JWT>

Statement:
    GET /erpws/2.0/accounts                                  → {"accountList": [...]}
    GET /erpws/2.0/accounts/{id}/motions?fromDate&toDate&skipRecords
      → {"data": {"operationInfo": [...]}, "hasNext": bool}  # paginate on hasNext
"""

from __future__ import annotations

import logging
from datetime import date
from urllib.parse import quote

import httpx

logger = logging.getLogger("dds.faktura")

DEFAULT_HOST = "business.faktura.ru"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_MOTIONS_PAGE_GUARD = 1000  # hard cap on pagination loops (safety against bad hasNext)


class FakturaApiError(RuntimeError):
    """Raised on a non-2xx response or malformed payload from Faktura."""


class FakturaApiClient:
    """Async client for the Faktura.ru statement API (one login/password set)."""

    def __init__(self, login: str, password: str, host: str = DEFAULT_HOST):
        # The bank expects the login prefixed with "f2b-" (see the 1C extension).
        self._login = login if login.startswith("f2b-") else f"f2b-{login}"
        self._password = password
        self._host = host
        self._base = f"https://{host}/erpws/2.0"
        self._token: str | None = None
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> FakturaApiClient:
        # httpx verifies TLS against its bundled certifi store — no macOS CA gap.
        self._client = httpx.AsyncClient(timeout=_TIMEOUT)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def _headers(self) -> dict[str, str]:
        if not self._token:
            raise FakturaApiError("Not authenticated — call authenticate() first")
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    async def authenticate(self) -> str:
        """POST credentials, store and return the JWT. Raises FakturaApiError on failure."""
        assert self._client is not None, "use FakturaApiClient as an async context manager"
        # data=dict → httpx form-urlencodes (correctly escapes special chars in the
        # password, e.g. '&'/'=') and sets the application/x-www-form-urlencoded header.
        resp = await self._client.post(
            f"{self._base}/login/authByUsernamePassword",
            data={"username": self._login, "password": self._password},
        )
        if resp.status_code != 200:
            # Body may contain an error message but never the password we sent.
            raise FakturaApiError(f"auth failed: HTTP {resp.status_code} {resp.text[:200]}")
        token = (resp.json() or {}).get("token")
        if not token:
            raise FakturaApiError("auth failed: no token in response")
        self._token = token
        return token

    async def get_accounts(self) -> list[dict]:
        """GET the list of accounts visible to this login (``accountList``)."""
        assert self._client is not None
        resp = await self._client.get(f"{self._base}/accounts", headers=self._headers)
        if resp.status_code != 200:
            raise FakturaApiError(f"accounts failed: HTTP {resp.status_code} {resp.text[:200]}")
        return (resp.json() or {}).get("accountList", [])

    async def get_motions(self, account_id: str | int, date_from: date, date_to: date) -> list[dict]:
        """GET all statement operations for one account in [date_from, date_to].

        Paginates via ``skipRecords`` until ``hasNext`` is false. Returns the flat
        list of ``data.operationInfo`` dicts (raw, as the bank returns them).
        """
        assert self._client is not None
        # account_id is bank-sourced, but encode it so an unexpected value can't
        # inject path segments / querystring (no '/', '?', '#', '..').
        acc = quote(str(account_id), safe="")
        operations: list[dict] = []
        skip = 0
        for _ in range(_MOTIONS_PAGE_GUARD):
            resp = await self._client.get(
                f"{self._base}/accounts/{acc}/motions",
                params={
                    "fromDate": date_from.isoformat(),
                    "toDate": date_to.isoformat(),
                    "skipRecords": str(skip),
                },
                headers=self._headers,
            )
            if resp.status_code != 200:
                raise FakturaApiError(
                    f"motions failed (account={account_id}): HTTP {resp.status_code} {resp.text[:200]}"
                )
            payload = resp.json() or {}
            page = (payload.get("data") or {}).get("operationInfo") or []
            operations.extend(page)
            skip += len(page)
            if not payload.get("hasNext") or not page:
                break
        else:
            logger.warning("motions pagination hit guard (%d pages) for account %s", _MOTIONS_PAGE_GUARD, account_id)
        return operations

    # ─── WRITE surface (payment draft) — UNVERIFIED, see module docstring ─────

    async def validate_payment(self, payment: dict) -> list[dict]:
        """POST a payment for bank-side validation. Returns the list of ``errors`` (empty = OK).

        Does NOT create anything — safe to call. Raises FakturaApiError on a non-2xx
        HTTP response (transport/auth failure, distinct from business validation errors).
        """
        assert self._client is not None, "use FakturaApiClient as an async context manager"
        resp = await self._client.post(
            f"{self._base}/payments/validate", json=payment, headers=self._headers
        )
        if resp.status_code not in (200, 201):
            raise FakturaApiError(f"payment validate failed: HTTP {resp.status_code} {resp.text[:200]}")
        return (resp.json() or {}).get("errors", []) or []

    async def save_payment(self, payment: dict) -> dict:
        """POST a payment to create a DRAFT in the bank. IRREVERSIBLE real-money mutation.

        The ``payment`` dict MUST carry a stable client ``guid`` so a retry after a
        lost response does not create a duplicate draft (bank-side idempotency).
        Returns the bank response (document id/status). Raises FakturaApiError on non-2xx.
        """
        assert self._client is not None, "use FakturaApiClient as an async context manager"
        resp = await self._client.post(
            f"{self._base}/payments/save", json=payment, headers=self._headers
        )
        if resp.status_code not in (200, 201):
            raise FakturaApiError(f"payment save failed: HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json() or {}
