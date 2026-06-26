# ruff: noqa: RUF002, RUF003
"""
Tests: Faktura payment-draft payload — закрепляют ПРОВЕРЕННЫЙ контракт /payments/validate
(scripts/faktura_validate_probe.py) и выбор счёта-плательщика. Чистые функции, без БД/сети.
"""

from decimal import Decimal

import pytest

from backend.models.payment_request import PaymentRequest
from backend.services.faktura_payment import _build_payment_body, _resolve_payer

_PAYER = {
    "id": "ACC-123",
    "number": "40702810111111111111",
    "currency": "RUB",
    "owner": {"name": "ООО Наша Компания", "inn": "7711111111", "kpp": "771101001"},
    "bank": {"bic": "044525000", "correspondentAccountNumber": "30101810400000000225"},
}


def _pr() -> PaymentRequest:
    pr = PaymentRequest(
        number="ОПЛ-00007",
        amount=Decimal("8929.50"),
        currency="RUB",
        payee_inn="7700000001",
        payee_kpp="770001001",
        payee_account="40702810900000000001",
        payee_bik="044525225",
        payee_corr_account="30101810000000000225",
        payee_name="ООО Перевозчик",
        purpose="Транспортные услуги",
    )
    pr.id = 7
    return pr


def test_build_payment_body_matches_proven_contract():
    body = _build_payment_body(_pr(), _PAYER, "guid-1")

    # Имена полей — строго как в проверенной /payments/validate пробе.
    assert body["payeeAccountNumber"] == "40702810900000000001"
    assert body["payeeBankBic"] == "044525225"
    assert body["payeeBankAccount"] == "30101810000000000225"
    assert body["payeeName"] == "ООО Перевозчик"
    assert body["payeeInn"] == "7700000001"
    assert body["payeeKpp"] == "770001001"
    assert body["payerAccountId"] == "ACC-123"   # внутренний id счёта, НЕ номер
    assert body["payerName"] == "ООО Наша Компания"
    assert body["payerKpp"] == "771101001"
    assert body["queue"] == 5
    assert body["uip"] == "0"
    assert body["urgent"] == "false"
    assert body["guid"] == "guid-1"
    assert body["docNumber"] == "00007"
    # amount — число (банк ждёт numeric), не строка.
    assert isinstance(body["amount"], float)
    assert body["amount"] == pytest.approx(8929.50)

    # Старые НЕВЕРНЫЕ имена не должны просочиться.
    for bad in ("payeeAccount", "payeeBik", "payeeCorrAccount", "payeeBankName", "payerAccount", "payerBik"):
        assert bad not in body


def test_resolve_payer_does_not_guess_when_ambiguous():
    two_rub = [
        {"id": "1", "number": "a", "currency": "RUB"},
        {"id": "2", "number": "b", "currency": "RUB"},
    ]
    with pytest.raises(ValueError):
        _resolve_payer(two_rub, None)  # 2 RUB-счёта и нет явного — НЕ угадываем

    assert _resolve_payer(two_rub, "2")["id"] == "2"
    assert _resolve_payer(two_rub, "a")["id"] == "1"  # матч по номеру тоже


def test_resolve_payer_single_account_ok():
    one = [{"id": "9", "number": "x", "currency": "RUB"}]
    assert _resolve_payer(one, None)["id"] == "9"


def test_resolve_payer_no_accounts_raises():
    with pytest.raises(ValueError):
        _resolve_payer([], None)


def test_extract_doc_id_various_shapes():
    from backend.services.faktura_payment import _extract_doc_id

    assert _extract_doc_id({"id": "123"}) == "123"
    assert _extract_doc_id({"docId": 456}) == "456"
    assert _extract_doc_id({"documentId": "D-1"}) == "D-1"
    assert _extract_doc_id({"data": {"id": "nested-7"}}) == "nested-7"
    assert _extract_doc_id({"document": {"number": "WH-9"}}) == "WH-9"
    assert _extract_doc_id({"status": "ok"}) is None  # ни одного известного поля
    assert _extract_doc_id(None) is None
    assert _extract_doc_id("not-a-dict") is None


def test_resolve_payer_dict_currency_rur():
    """Faktura отдаёт currency СЛОВАРЁМ с кодом RUR (не строкой RUB) — не падать, матчить."""
    accs = [
        {"id": "271554598943", "number": "40702810800000001893", "currency": {"shortName": "р.", "code": "RUR"}},
        {"id": "271554598912", "number": "40702810500001001752", "currency": {"shortName": "р.", "code": "RUR"}},
    ]
    # два рублёвых счёта без явного id — не угадываем (но и не падаем на dict-валюте)
    with pytest.raises(ValueError):
        _resolve_payer(accs, None)
    # с явным payer_account_id — выбирает нужный
    assert _resolve_payer(accs, "271554598943")["id"] == "271554598943"
    # одиночный dict-RUR счёт — ок
    assert _resolve_payer([accs[0]], None)["id"] == "271554598943"
