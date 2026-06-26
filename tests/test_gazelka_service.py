# ruff: noqa: RUF001, RUF002, RUF003
"""
Tests for Gazelka service mapping: options, prefill, payload (no DB, no network).
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from backend.integrations.gazelka_client import ApplyForm
from backend.schemas.gazelka import GazelkaSendRequest
from backend.services.gazelka_service import (
    _attach_suggestion,
    _clean_date,
    _default_marketplace,
    _extract_wb_numbers,
    _match_warehouse,
    _options_from_form,
    _payload_from_request,
    _prefill_from_assembly,
    _row_from_plan,
    _status_label,
    _values_from_plan,
)


def _form() -> ApplyForm:
    return ApplyForm(
        selects={
            "entity_id": [("", "Выбор организации"), ("6596", "ПЛЮС ВАЙБ ООО")],
            "payer_id": [("", "Выбор организации"), ("6596", "ПЛЮС ВАЙБ ООО")],
            "price_id": [("5", "Симферополь"), ("1", "Иваново")],
            "marketplace_id": [("0", "Выбрать Маркетплейс"), ("1", "Ozon"), ("4", "WildBerries")],
            "delivery_address": [("", "На какой склад?"), ("Тула", "Тула"), ("Казань", "Казань"), ("Казань", "Казань")],
            "monomix": [("0", "Тип поставки"), ("1", "Моно"), ("2", "Микс")],
            "daily_delivery_timeslot": [("", "Время"), ("Вечером", "Вечером")],
        },
        inputs={"customer_phone": "+79203491330"},
        hidden={"action": "save_plan"},
    )


# ─── Options ──────────────────────────────────────────────────────────────────


def test_options_drop_placeholders_and_dedupe():
    opts = _options_from_form(_form())
    assert [o.value for o in opts.entities] == ["6596"]
    # маркетплейс-плейсхолдер "0" отброшен
    assert all(o.value != "0" for o in opts.marketplaces)
    # дубль "Казань" схлопнут
    assert [o.value for o in opts.delivery_warehouses] == ["Тула", "Казань"]
    # monomix без плейсхолдера "0"
    assert {o.value for o in opts.supply_types} == {"1", "2"}


def test_default_marketplace_picks_wb():
    opts = _options_from_form(_form())
    assert _default_marketplace(opts) == "4"


def test_match_warehouse_exact_and_contains():
    opts = _options_from_form(_form())
    assert _match_warehouse("Казань", opts) == "Казань"
    assert _match_warehouse("казань", opts) == "Казань"
    assert _match_warehouse("Казань WB", opts) == "Казань"  # contains
    assert _match_warehouse("Москва", opts) is None
    assert _match_warehouse(None, opts) is None


# ─── Prefill ──────────────────────────────────────────────────────────────────


def _assembly(**kw) -> SimpleNamespace:
    base = dict(
        wb_fbo_supply=SimpleNamespace(warehouse_name="Казань", wb_supply_id="WB-12345"),
        wb_warehouse_name_manual=None,
        pallets_count=3,
        pallet_weight_kg=Decimal("100.00"),
        pickup_date=date(2026, 7, 1),
        delivery_date=date(2026, 7, 3),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_prefill_maps_assembly_fields():
    form = _form()
    opts = _options_from_form(form)
    pre = _prefill_from_assembly(_assembly(), form, opts)
    assert pre.supply_id == "WB-12345"
    assert pre.delivery_address == "Казань"
    assert pre.delivery_address_x2 == "Казань"
    assert pre.weight == Decimal("300.00")  # 3 × 100
    assert pre.pallets == 3
    assert pre.customer_phone == "+79203491330"
    assert pre.marketplace_id == "4"
    assert pre.departure_date == date(2026, 7, 1)
    assert pre.delivery_date == date(2026, 7, 3)


def test_prefill_without_fbo_supply_falls_back_to_manual_name():
    form = _form()
    opts = _options_from_form(form)
    ar = _assembly(wb_fbo_supply=None, wb_warehouse_name_manual="Тула")
    pre = _prefill_from_assembly(ar, form, opts)
    assert pre.supply_id is None
    assert pre.delivery_address == "Тула"
    assert pre.delivery_address_x2 == "Тула"


# ─── Payload ──────────────────────────────────────────────────────────────────


def _send_req(**kw) -> GazelkaSendRequest:
    base = dict(entity_id="6596", payer_id="6596", price_id="1")
    base.update(kw)
    return GazelkaSendRequest(**base)


def test_payload_drops_none_and_formats_dates():
    req = _send_req(
        delivery_date=date(2026, 7, 3),
        departure_date=date(2026, 7, 1),
        pallets=3,
        weight=Decimal("300.00"),
        marketplace_id=None,
        palleting=True,
    )
    payload = _payload_from_request(req)
    assert payload["delivery_date"] == "2026-07-03"
    assert payload["departure_date"] == "2026-07-01"
    assert payload["pallets"] == "3"
    assert payload["weight"] == "300.00"
    assert payload["palleting"] == "1"  # чекбокс присутствует только когда True
    assert payload["is_marketplace"] == "yes"  # дефолт
    assert "marketplace_id" not in payload  # None отброшен
    # action добавляет клиент, не сервис
    assert "action" not in payload


def test_payload_omits_palleting_when_false():
    payload = _payload_from_request(_send_req(palleting=False))
    assert "palleting" not in payload
    assert payload["length"] == "60"  # дефолты габаритов
    assert payload["height"] == "40"
    assert payload["width"] == "40"


# ─── Списки заявок портала: маппинг строк ────────────────────────────────────

_PLAN = {
    "id": "313621", "status": "2", "departure_date": "2026-06-27", "delivery_date": "2026-06-30",
    "delivery_address": "Казань", "marketplace_id": "4", "monomix": "2",
    "pallets": "0", "boxes": "5", "weight": "25", "rate": "1860", "volume": "120000",
    "supply_id": "&quot;Казань 40299154&quot; PVB-0000266", "entity": "ПЛЮС ВАЙБ ООО",
    "entity_id": "6596", "payer_id": "6596", "price_id": "1", "customer_phone": "+79203491330",
    "length": "60", "height": "40", "width": "50", "palleting": "f", "notes": "",
}
_MKTS = [{"id": "4", "name": "WildBerries"}]


def test_status_label():
    assert _status_label("2") == "Запланирована"
    assert _status_label("31") == "В маршруте"
    assert _status_label("99") == "Статус 99"


def test_clean_date_drops_epoch():
    assert _clean_date("2026-06-30") == "2026-06-30"
    assert _clean_date("1970-01-01") is None
    assert _clean_date("") is None


def test_row_from_plan_planned():
    row = _row_from_plan(_PLAN, _MKTS, {}, editable=True)
    assert row.gazelka_id == "313621"
    assert row.status_label == "Запланирована"
    assert row.marketplace == "WildBerries"
    assert row.monomix == "Микс"
    assert row.boxes == 5
    assert row.rate == "1860"
    assert '"Казань 40299154"' in (row.supply_id or "")  # &quot; → "
    assert row.editable is True
    assert row.linked_assembly_number is None


def test_row_from_plan_linked_badge():
    row = _row_from_plan(_PLAN, _MKTS, {"313621": (42, "ASM-431")}, editable=True)
    assert row.linked_assembly_id == 42
    assert row.linked_assembly_number == "ASM-431"


def test_row_from_plan_active_joins():
    plan = {**_PLAN, "status": "31", "route_id": "17807"}
    joins = {
        "routes": {"17807": {"id": "17807", "date": "2026-06-24", "driver_id": "5",
                             "vehicle_id": "9", "carrier_id": "3", "finish_time": ""}},
        "drivers": {"5": {"id": "5", "name": "Иванов И.", "phone": "+700", "passport": "п"}},
        "vehicles": {"9": {"id": "9", "vehicle_make": "Volvo", "vehicle_number": "а001аа"}},
        "carriers": {"3": {"id": "3", "organization": "Газель-Ка"}},
        "places": {},
    }
    row = _row_from_plan(plan, _MKTS, {}, editable=False, joins=joins)
    assert row.status_label == "В маршруте"
    assert row.route_number == "17807"
    assert row.driver_name == "Иванов И."
    assert row.driver_phone == "+700"
    assert row.vehicle == "Volvo а001аа"
    assert row.carrier == "Газель-Ка"


def test_values_from_plan_for_edit():
    v = _values_from_plan(_PLAN)
    assert v.entity_id == "6596"
    assert v.price_id == "1"
    assert v.is_marketplace == "yes"
    assert v.marketplace_id == "4"
    assert v.pallets == 0
    assert v.boxes == 5
    assert str(v.departure_date) == "2026-06-27"
    assert v.monomix == "2"
    assert v.palleting is False


# ─── Матчинг: авто-подсказка по № поставки WB ────────────────────────────────


def test_extract_wb_numbers():
    nums = _extract_wb_numbers("&quot;Казань 40299154&quot; PVB-0000266")
    assert "40299154" in nums  # № поставки WB вытащен из сырого supply_id


def test_attach_suggestion_by_wb_number():
    row = _row_from_plan(_PLAN, _MKTS, {}, editable=True)  # supply_id содержит 40299154
    _attach_suggestion(row, _PLAN, {"40299154": (7, "ASM-700")})
    assert row.suggested_assembly_id == 7
    assert row.suggested_assembly_number == "ASM-700"


def test_attach_suggestion_skipped_when_already_linked():
    row = _row_from_plan(_PLAN, _MKTS, {"313621": (1, "ASM-1")}, editable=True)
    _attach_suggestion(row, _PLAN, {"40299154": (7, "ASM-700")})
    assert row.suggested_assembly_id is None  # уже связана — подсказку не даём
