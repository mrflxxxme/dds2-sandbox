# ruff: noqa: RUF001, RUF002, RUF003
"""Справочник банков (БИК → корр. счёт) + эндпоинт авто-заполнения."""

import pytest

from backend.services.bank_directory import _BANKS, resolve_bank


def test_resolve_known_bank():
    b = resolve_bank("044525104")
    assert b is not None
    assert b["bic"] == "044525104"
    assert b["corr_account"] == "30101810745374525104"
    assert "Точка" in b["name"]


def test_resolve_unknown_bank_returns_none():
    assert resolve_bank("044599999") is None  # нет в наборе


@pytest.mark.parametrize("bad", ["", "123", "12345678", "1234567890", "abcdefghi", None])
def test_resolve_invalid_bic(bad):
    assert resolve_bank(bad) is None


def test_every_seeded_corr_account_is_structurally_valid():
    """Каждый к/с в наборе: 20 цифр, префикс 30101810, последние 3 == последние 3 БИК.
    Ловит опечатку в значении (иначе resolve_bank его молча отбросит → авто-заполнение не сработает)."""
    for bic in _BANKS:
        b = resolve_bank(bic)
        assert b is not None, f"к/с для БИК {bic} не прошёл структурную валидацию"
        assert b["corr_account"][-3:] == bic[-3:]


@pytest.mark.asyncio
async def test_bank_endpoint_known_and_unknown(client, auth_headers):
    ok = await client.get("/api/v1/payment-requests/banks/044525104", headers=auth_headers)
    assert ok.status_code == 200
    assert ok.json()["corr_account"] == "30101810745374525104"

    nf = await client.get("/api/v1/payment-requests/banks/044599999", headers=auth_headers)
    assert nf.status_code == 404
