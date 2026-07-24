# ruff: noqa: RUF001, RUF002, RUF003
"""
Tests for mprocket («Нитропак») wiring into the generic fulfillment service:
stock normalizer mapping + provider registration (FF_SERVICES / labels).
"""

import pytest

from backend.services import fulfillment_service as fs

# TDD-хвост: mprocket-клиент написан (integrations/mprocket_client.py), но провайдер
# ещё не зареган в fulfillment_service (FF_SERVICES/нормалайзеры) — заявки на портале
# отдают 403, кредов нет (см. recon). Снять skip при доводке интеграции.
pytestmark = pytest.mark.skip(reason="WIP: mprocket не зарегистрирован в fulfillment_service")


def test_mprocket_registered_as_ff_provider():
    assert "mprocket" in fs.FF_SERVICES
    assert "mprocket" in fs.SYNCABLE_FF_SERVICES
    assert fs._provider_human("mprocket") == "Нитропак (mprocket)"


def test_normalize_mprocket_stock_maps_contract():
    row = {"barcode": "2049757911663", "name": "Ковер · серый", "qty_stock": 120, "qty_shipping": 30}
    out = fs._normalize_mprocket_stock(row)
    assert out == {
        "barcode": "2049757911663",
        "name": "Ковер · серый",
        "vendor_code": None,
        "external_product_id": None,
        "qty_good": 120,  # «Количество на складе» → good
        "qty_reserve": 30,  # «Количество в отгрузке» → reserve
        "qty_defect": 0,
        "qty_nominal": 0,
    }


def test_normalize_mprocket_stock_handles_missing_fields():
    out = fs._normalize_mprocket_stock({"barcode": "2043160630135"})
    assert out["barcode"] == "2043160630135"
    assert out["qty_good"] is None  # _apply_stocks коэрсит None→0 при агрегации
    assert out["qty_reserve"] is None


def test_normalize_mprocket_request_maps_to_mirror_row():
    from backend.models.fulfillment import FfRequestKind

    row = {
        "external_id": "17006",
        "number": "17006",
        "type_name": "MP Rocket — Отгрузка транспортом клиента",
        "status_title": "ШК коробов отправлены",
        "date_str": "2026-07-09",
        "total_qty": 354,
        "notes": "Вяткин Ковры 300526",
    }
    out = fs._normalize_mprocket_request(row, FfRequestKind.ASSEMBLY.value)
    assert out["external_id"] == "17006"
    assert out["kind"] == FfRequestKind.ASSEMBLY.value
    assert out["status"] == "ШК коробов отправлены"
    assert out["stage_title"] == "ШК коробов отправлены"
    assert out["total_qty"] == 354
    assert out["external_created_at"].isoformat() == "2026-07-09"  # _parse_date(ISO)
    assert out["is_completed"] is False
    assert out["raw"]["_provider"] == "mprocket"


def test_normalize_mprocket_request_inbound_kind():
    from backend.models.fulfillment import FfRequestKind

    out = fs._normalize_mprocket_request({"external_id": "5039", "date_str": None}, FfRequestKind.INBOUND.value)
    assert out["kind"] == FfRequestKind.INBOUND.value
    assert out["external_created_at"] is None
