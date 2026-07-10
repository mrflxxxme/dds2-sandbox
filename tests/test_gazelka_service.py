# ruff: noqa: RUF001, RUF002, RUF003
"""
Tests for Gazelka service mapping: options, prefill, payload (no DB, no network).
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.integrations.gazelka_client import ApplyForm, DeliveryPlace, SchedulePlan
from backend.schemas.gazelka import GazelkaSendRequest
from backend.services.gazelka_service import (
    GazelkaServiceError,
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
    _validate_schedule,
    _values_from_plan,
)

# Казань WB (place 25): грузят Пн/Ср, доставка Вт/Чт, путь 1 день.
# У Ozon (place 26) склад называется так же, но график другой — этим и проверяем матч по mpid.
_KAZAN_WB = SchedulePlan(loading_days=[1, 3], delivery_days=[2, 4], eta_days=1, active=True)
_KAZAN_OZON = SchedulePlan(loading_days=[5], delivery_days=[6], eta_days=1, active=True)
_TULA_WB = SchedulePlan(loading_days=None, delivery_days=None, eta_days=2, active=True)


def _form() -> ApplyForm:
    return ApplyForm(
        selects={
            "entity_id": [("", "Выбор организации"), ("6596", "ПЛЮС ВАЙБ ООО")],
            "payer_id": [("", "Выбор организации"), ("6596", "ПЛЮС ВАЙБ ООО")],
            "price_id": [("5", "Симферополь"), ("1", "Иваново")],
            "marketplace_id": [("0", "Выбрать Маркетплейс"), ("1", "Ozon"), ("4", "WildBerries")],
            "monomix": [("0", "Тип поставки"), ("1", "Моно"), ("2", "Микс")],
            "daily_delivery_timeslot": [("", "Время"), ("Вечером", "Вечером")],
        },
        inputs={"customer_phone": "+79203491330"},
        hidden={"action": "save_plan"},
        # price_id="1" (Иваново) — именно selected-опция, а не первая в разметке
        defaults={"entity_id": "6596", "price_id": "1", "volume": "0", "weight2": "0", "notes": ""},
        places=[
            DeliveryPlace(value="Тула", label="Тула", place_id="18", marketplace_id="4"),
            DeliveryPlace(value="Казань", label="Казань", place_id="25", marketplace_id="4"),
            DeliveryPlace(value="Казань", label="Казань", place_id="26", marketplace_id="1"),
            # Названия, которые не совпадают с именами WB-складов буквально
            DeliveryPlace(value="Санкт-Петербург (Шушары)", label="Санкт-Петербург (Шушары)", place_id="28", marketplace_id="4"),
            DeliveryPlace(value="Санкт-Петербург (Уткина Заводь)", label="Санкт-Петербург (Уткина Заводь)", place_id="29", marketplace_id="4"),
            DeliveryPlace(value="Екатеринбург (Перспективный)", label="Екатеринбург (Перспективный)", place_id="30", marketplace_id="4"),
            DeliveryPlace(value="Екатеринбург (Испытателей)", label="Екатеринбург (Испытателей)", place_id="31", marketplace_id="4"),
            DeliveryPlace(value="Домодедово 2", label="Домодедово 2", place_id="32", marketplace_id="4"),
            DeliveryPlace(value="Владимир FBO (Воршинское)", label="Владимир FBO (Воршинское)", place_id="33", marketplace_id="4"),
            DeliveryPlace(value="Владимир FBS (Воршинское)", label="Владимир FBS (Воршинское)", place_id="34", marketplace_id="4"),
            DeliveryPlace(value="Чехов", label="Чехов", place_id="35", marketplace_id="4"),
            DeliveryPlace(value="Чехов-2", label="Чехов-2", place_id="36", marketplace_id="4"),
        ],
        schedule={
            "1-18": _TULA_WB,
            "1-25": _KAZAN_WB,
            "1-26": _KAZAN_OZON,
            **{f"1-{pid}": _TULA_WB for pid in ("28", "29", "30", "31", "32", "33", "34", "35", "36")},
        },
        min_departure=date(2026, 7, 10),
        min_delivery=date(2026, 7, 11),
    )


# ─── Options ──────────────────────────────────────────────────────────────────


def test_options_drop_placeholders_and_dedupe():
    opts = _options_from_form(_form())
    assert [o.value for o in opts.entities] == ["6596"]
    # маркетплейс-плейсхолдер "0" отброшен
    assert all(o.value != "0" for o in opts.marketplaces)
    # monomix без плейсхолдера "0"
    assert {o.value for o in opts.supply_types} == {"1", "2"}


def test_warehouse_options_keep_same_name_across_marketplaces():
    """Одноимённые склады разных маркетплейсов — разные опции: у них разные графики."""
    opts = _options_from_form(_form())
    kazan = [o for o in opts.delivery_warehouses if o.value == "Казань"]
    assert {(o.place_id, o.marketplace_id) for o in kazan} == {("25", "4"), ("26", "1")}


def test_options_expose_only_active_schedule():
    form = _form()
    form.schedule["1-99"] = SchedulePlan(loading_days=[1], delivery_days=[2], eta_days=1, active=False)
    opts = _options_from_form(form)
    assert "1-99" not in opts.schedule
    assert opts.schedule["1-25"].loading_days == [1, 3]
    assert opts.min_departure_date == date(2026, 7, 10)


def test_default_price_id_is_home_city_not_first_option():
    """Наш город отгрузки — Иваново. Первая опция портала (Симферополь) не дефолт."""
    opts = _options_from_form(_form())
    assert opts.price_lists[0].value == "5"  # Симферополь — первая в разметке
    assert opts.default_price_id == "1"  # Иваново
    assert opts.default_entity_id == "6596"


def test_default_price_id_prefers_home_city_over_portal_selection():
    form = _form()
    form.defaults["price_id"] = "5"  # портал выбрал Симферополь — всё равно берём Иваново
    assert _options_from_form(form).default_price_id == "1"


def test_default_price_id_falls_back_when_home_city_absent():
    form = _form()
    form.selects["price_id"] = [("5", "Симферополь"), ("2", "Кострома")]
    assert _options_from_form(form).default_price_id == "1"  # selected портала
    form.defaults.pop("price_id")
    assert _options_from_form(form).default_price_id == "5"  # первая опция


def test_default_marketplace_picks_wb():
    opts = _options_from_form(_form())
    assert _default_marketplace(opts) == "4"


def test_match_warehouse_exact_and_tokens():
    opts = _options_from_form(_form())
    assert _match_warehouse("Казань", opts, "4") == "Казань"
    assert _match_warehouse("казань", opts, "4") == "Казань"
    assert _match_warehouse("Казань WB", opts, "4") == "Казань"
    assert _match_warehouse("Москва", opts, "4") is None
    assert _match_warehouse(None, opts, "4") is None


def test_match_warehouse_ignores_other_marketplace_warehouses():
    """«Тула» есть только у WB — для Ozon её предлагать нельзя."""
    opts = _options_from_form(_form())
    assert _match_warehouse("Тула", opts, "1") is None
    assert _match_warehouse("Тула", opts, "4") == "Тула"


def test_match_warehouse_drops_noise_words_and_declensions():
    """Реальные имена WB-складов не совпадают с их дропдауном буквально."""
    opts = _options_from_form(_form())
    assert _match_warehouse("Склад Шушары", opts, "4") == "Санкт-Петербург (Шушары)"
    assert _match_warehouse("Екатеринбург - Перспективная 14", opts, "4") == "Екатеринбург (Перспективный)"
    assert _match_warehouse("СЦ Домодедово М4", opts, "4") == "Домодедово 2"


def test_match_warehouse_prefers_fbo_over_fbs():
    """Наши поставки — FBO; одноимённый FBS-склад брать нельзя."""
    opts = _options_from_form(_form())
    assert _match_warehouse("Владимир Воршинское", opts, "4") == "Владимир FBO (Воршинское)"
    assert _match_warehouse("Владимир FBS Воршинское", opts, "4") == "Владимир FBS (Воршинское)"


def test_match_warehouse_prefers_exact_over_numbered_twin():
    opts = _options_from_form(_form())
    assert _match_warehouse("Чехов", opts, "4") == "Чехов"


def test_match_warehouse_skips_warehouse_without_schedule_for_price():
    """Склад без активного графика из нашего города подставлять бессмысленно."""
    opts = _options_from_form(_form())
    assert _match_warehouse("Казань", opts, "4", "1") == "Казань"
    assert _match_warehouse("Казань", opts, "4", "5") is None  # Симферополь → графика нет


# ─── График (слоты сдачи) ─────────────────────────────────────────────────────


def _sched_req(**kw) -> GazelkaSendRequest:
    base = dict(
        entity_id="6596",
        payer_id="6596",
        price_id="1",
        is_marketplace="yes",
        marketplace_id="4",
        delivery_address="Казань",
        departure_date=date(2026, 7, 13),  # понедельник — день погрузки
        delivery_date=date(2026, 7, 14),  # вторник — день доставки, +1 день пути
    )
    base.update(kw)
    return GazelkaSendRequest(**base)


def test_validate_schedule_passes_on_allowed_days():
    _validate_schedule(_form(), _sched_req())  # не бросает


def test_validate_schedule_rejects_departure_on_non_loading_day():
    with pytest.raises(GazelkaServiceError) as e:
        # вторник — Казань WB грузят Пн/Ср
        _validate_schedule(_form(), _sched_req(departure_date=date(2026, 7, 14), delivery_date=date(2026, 7, 16)))
    assert "грузят по Пн/Ср" in str(e.value)
    assert e.value.status_code == 400


def test_validate_schedule_rejects_delivery_on_non_delivery_day():
    with pytest.raises(GazelkaServiceError) as e:
        # среда — Казань WB принимает Вт/Чт
        _validate_schedule(_form(), _sched_req(departure_date=date(2026, 7, 13), delivery_date=date(2026, 7, 15)))
    assert "принимает доставку по Вт/Чт" in str(e.value)


def test_validate_schedule_rejects_past_departure():
    """Причина 500 в проде: даты сборки протухли, портал их не принимает."""
    with pytest.raises(GazelkaServiceError) as e:
        _validate_schedule(_form(), _sched_req(departure_date=date(2026, 6, 30), delivery_date=date(2026, 7, 4)))
    assert "10.07.2026" in str(e.value)


def test_validate_schedule_rejects_delivery_before_eta():
    with pytest.raises(GazelkaServiceError) as e:
        # доставка в тот же вторник, что и погрузка... но путь 1 день от Пн 13-го → 14-е ок;
        # берём Ср 15-го как отправку? нет — проверяем доставку 14-го при отправке 15-го (Ср)
        _validate_schedule(_form(), _sched_req(departure_date=date(2026, 7, 15), delivery_date=date(2026, 7, 14)))
    assert "не раньше 16.07.2026" in str(e.value)


def test_validate_schedule_rejects_unserved_direction():
    with pytest.raises(GazelkaServiceError) as e:
        _validate_schedule(_form(), _sched_req(price_id="5"))  # Симферополь → Казань не в графике
    assert "не обслуживается" in str(e.value)


def test_validate_schedule_rejects_warehouse_of_other_marketplace():
    with pytest.raises(GazelkaServiceError) as e:
        _validate_schedule(_form(), _sched_req(marketplace_id="1", delivery_address="Тула"))
    assert "не обслуживается" in str(e.value)


def test_validate_schedule_fails_open_when_portal_markup_unparsed():
    """Пустой график = «портал сменил разметку», а не «направления нет» — не блокируем WB-поток."""
    form = _form()
    form.schedule = {}
    _validate_schedule(form, _sched_req(departure_date=date(2026, 6, 30)))


def test_validate_schedule_skipped_for_non_marketplace_delivery():
    """Обычная доставка (не маркетплейс) идёт свободными датами — график не применяем."""
    _validate_schedule(_form(), _sched_req(is_marketplace="no", departure_date=date(2026, 7, 14)))


def test_validate_schedule_allows_any_day_when_unrestricted():
    """Тула без ограничений по дням: любой день ≥ min, доставка ≥ отправка + 2."""
    _validate_schedule(
        _form(),
        _sched_req(delivery_address="Тула", departure_date=date(2026, 7, 14), delivery_date=date(2026, 7, 16)),
    )


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


def test_prefill_pulls_stale_dates_forward_to_schedule():
    """Даты сборки (1–3 июля) протухли: предлагаем ближайшие Пн погрузки и Вт доставки."""
    form = _form()
    pre = _prefill_from_assembly(_assembly(), form, _options_from_form(form))
    assert pre.departure_date == date(2026, 7, 13)  # пн, день погрузки Казани
    assert pre.delivery_date == date(2026, 7, 14)  # вт, +1 день пути


def test_prefill_without_fbo_supply_falls_back_to_manual_name():
    form = _form()
    opts = _options_from_form(form)
    ar = _assembly(wb_fbo_supply=None, wb_warehouse_name_manual="Тула")
    pre = _prefill_from_assembly(ar, form, opts)
    assert pre.supply_id is None
    assert pre.delivery_address == "Тула"
    assert pre.delivery_address_x2 == "Тула"
    # Тула без ограничений по дням: отправка — с нижней границы портала, доставка +2 дня
    assert pre.departure_date == date(2026, 7, 10)
    assert pre.delivery_date == date(2026, 7, 12)


# ─── Payload ──────────────────────────────────────────────────────────────────


def _send_req(**kw) -> GazelkaSendRequest:
    base = dict(entity_id="6596", payer_id="6596", price_id="1")
    base.update(kw)
    return GazelkaSendRequest(**base)


def test_payload_keeps_none_and_formats_dates():
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
    assert payload["palleting"] == "on"  # чекбокс присутствует только когда True
    assert payload["is_marketplace"] == "yes"  # дефолт
    # None остаётся ключом: клиент подставит дефолт формы, а не выкинет поле из POST
    assert payload["marketplace_id"] is None
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
    row = _row_from_plan(_PLAN, _MKTS, {"313621": (42, "ASM-431", "SHIPPED")}, editable=True)
    assert row.linked_assembly_id == 42
    assert row.linked_assembly_number == "ASM-431"
    assert row.linked_assembly_status == "Отгружена"  # статус нашей сборки


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
    row = _row_from_plan(_PLAN, _MKTS, {"313621": (1, "ASM-1", "READY")}, editable=True)
    _attach_suggestion(row, _PLAN, {"40299154": (7, "ASM-700")})
    assert row.suggested_assembly_id is None  # уже связана — подсказку не даём
