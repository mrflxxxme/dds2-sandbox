# ruff: noqa: RUF001, RUF002, RUF003
"""
Tests for Gazelka service mapping: options, prefill, payload (no DB, no network).
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.integrations.gazelka_client import ApplyForm, DeliveryPlace, GazelkaCreateResult, SchedulePlan
from backend.models import GazelkaOrderStatus
from backend.schemas.gazelka import GazelkaSendRequest
from backend.services import gazelka_service
from backend.services.gazelka_service import (
    GazelkaServiceError,
    _attach_suggestion,
    _clean_date,
    _default_marketplace,
    _extract_logistics,
    _extract_wb_numbers,
    _find_created_plan,
    _match_warehouse,
    _options_from_form,
    _parse_rate,
    _payload_from_request,
    _plan_ids,
    _plan_matches_payload,
    _prefill_from_assembly,
    _row_from_plan,
    _split_driver_name,
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
            DeliveryPlace(value="Самара (Новосемейкино)", label="Самара (Новосемейкино)", place_id="37", marketplace_id="4"),
            DeliveryPlace(value="Новосибирск (Петухова)", label="Новосибирск (Петухова)", place_id="38", marketplace_id="4"),
            DeliveryPlace(value="Воронеж РЦ (Новоусманский)", label="Воронеж РЦ (Новоусманский)", place_id="39", marketplace_id="4"),
            DeliveryPlace(value="Воронеж СГТ", label="Воронеж СГТ", place_id="40", marketplace_id="4"),
        ],
        schedule={
            "1-18": _TULA_WB,
            "1-25": _KAZAN_WB,
            "1-26": _KAZAN_OZON,
            **{f"1-{pid}": _TULA_WB for pid in ("28", "29", "30", "31", "32", "33", "34", "35", "36", "37", "38", "39", "40")},
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


def test_match_warehouse_stem_does_not_glue_different_cities():
    """«Новосемейкино» и «Новосибирск» делят префикс «ново» — это разные слова."""
    opts = _options_from_form(_form())
    assert _match_warehouse("Новосемейкино", opts, "4") == "Самара (Новосемейкино)"


def test_match_warehouse_skips_spec_warehouse():
    """СГТ — спец-склад крупногабарита: подставлять его вместо обычного нельзя."""
    opts = _options_from_form(_form())
    assert _match_warehouse("Воронеж", opts, "4") == "Воронеж РЦ (Новоусманский)"
    assert _match_warehouse("Воронеж СГТ", opts, "4") == "Воронеж СГТ"


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


# ─── Подтверждение создания по «Запланированным» ─────────────────────────────
# У портала нет машинного признака успеха save_plan (в отличие от migfull/«Натали»,
# где Livewire отдаёт redirect с GUID) — успех подтверждаем новой строкой списка.


def _sent_payload(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "supply_id": "40299154",
        "departure_date": "2026-07-15",
        "delivery_date": "2026-07-16",
        "pallets": "3",
    }
    base.update(over)
    return base


def _portal_plan(pid: str, **over: object) -> dict[str, object]:
    plan: dict[str, object] = {
        "id": pid,
        "supply_id": "&quot;Казань 40299154&quot; PVB-0000266",
        "departure_date": "2026-07-15 00:00:00",
        "delivery_date": "2026-07-16",
        "pallets": "3",
    }
    plan.update(over)
    return plan


def test_plan_ids_collects_ids():
    assert _plan_ids({"plans": [{"id": 101}, {"id": "102"}, {"x": 1}]}) == {"101", "102"}
    assert _plan_ids({}) == set()


def test_plan_matches_payload_by_our_fields():
    # supply_id — по вхождению (портал хранит его с обвязкой), даты — по дню
    assert _plan_matches_payload(_portal_plan("1"), _sent_payload()) is True


def test_plan_matches_payload_rejects_foreign_order():
    assert _plan_matches_payload(_portal_plan("1", supply_id="88888888"), _sent_payload()) is False
    assert _plan_matches_payload(_portal_plan("1", departure_date="2026-07-14"), _sent_payload()) is False
    assert _plan_matches_payload(_portal_plan("1", pallets="2"), _sent_payload()) is False


def test_plan_matches_payload_skips_unset_fields():
    # None-поля payload = дефолт формы — не сравниваем, матч по остальным
    plan = _portal_plan("1", supply_id="")
    assert _plan_matches_payload(plan, _sent_payload(supply_id=None, delivery_date=None)) is True


def test_find_created_plan_returns_new_matching_id():
    after = {
        "plans": [
            _portal_plan("100"),  # была до POST — не считается
            _portal_plan("101"),  # наша новая
            _portal_plan("102", supply_id="99", pallets="1"),  # чужая одновременная
        ]
    }
    assert _find_created_plan({"100"}, after, _sent_payload()) == "101"


def test_find_created_plan_none_when_nothing_new():
    after = {"plans": [_portal_plan("100")]}
    assert _find_created_plan({"100"}, after, _sent_payload()) is None


def test_find_created_plan_ignores_foreign_new_order():
    after = {"plans": [_portal_plan("102", supply_id="99", pallets="1")]}
    assert _find_created_plan(set(), after, _sent_payload()) is None


def test_find_created_plan_picks_latest_of_duplicates():
    after = {"plans": [_portal_plan("101"), _portal_plan("103")]}
    assert _find_created_plan(set(), after, _sent_payload()) == "103"


# ─── send_order: ложная ошибка редирект-эвристики → подтверждение по списку ──


class _FakeGazelkaClient:
    """Клиент, у которого POST реально создаёт заявку, но портал не редиректит."""

    def __init__(self, before: list[dict], after: list[dict], outcome: object):
        self._pages = [before, after]
        self._outcome = outcome  # GazelkaCreateResult | Exception

    async def __aenter__(self) -> "_FakeGazelkaClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def authenticate(self) -> None:
        return None

    async def fetch_apply_form(self) -> ApplyForm:
        return _form()

    async def fetch_planned(self) -> dict:
        return {"plans": self._pages.pop(0), "marketplaces": []}

    async def create_order(self, fields: dict, form: ApplyForm | None = None) -> GazelkaCreateResult:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        assert isinstance(self._outcome, GazelkaCreateResult)
        return self._outcome


def _db_for_send() -> MagicMock:
    key = SimpleNamespace(warehouse_id=7, config={"login": "user@x"}, project_id=4)
    assembly = SimpleNamespace(id=55, warehouse_id=7)
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalar_one_or_none=lambda: key),  # _get_key
            SimpleNamespace(scalar_one_or_none=lambda: assembly),  # _load_assembly
        ]
    )
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _send_req_confirm() -> GazelkaSendRequest:
    return _send_req(
        is_marketplace="no",  # график не валидируем — тест про подтверждение исхода
        supply_id="40299154",
        departure_date=date(2026, 7, 15),
        delivery_date=date(2026, 7, 16),
        pallets=3,
        force_resend=True,
    )


async def test_send_order_confirms_by_planned_list_when_no_redirect(monkeypatch):
    """Портал вернул форму без редиректа (ok=False), но заявка появилась в списке → SENT."""
    uncertain = GazelkaCreateResult(ok=False, ref=None, message="подтверждение не получено", excerpt="")
    client = _FakeGazelkaClient(before=[_portal_plan("100")], after=[_portal_plan("100"), _portal_plan("101")], outcome=uncertain)
    monkeypatch.setattr(gazelka_service, "_client_from_key", lambda key: client)

    db = _db_for_send()
    result = await gazelka_service.send_order(db, 4, 55, _send_req_confirm(), actor="t@t")

    assert result.ok is True
    assert result.ref == "101"
    order = db.add.call_args.args[0]
    assert order.status == GazelkaOrderStatus.SENT
    assert order.gazelka_ref == "101"


async def test_send_order_failed_when_confirmed_absent(monkeypatch):
    """Портал вернул форму, заявка в списке НЕ появилась → FAILED (повтор безопасен)."""
    uncertain = GazelkaCreateResult(ok=False, ref=None, message="подтверждение не получено", excerpt="err")
    client = _FakeGazelkaClient(before=[_portal_plan("100")], after=[_portal_plan("100")], outcome=uncertain)
    monkeypatch.setattr(gazelka_service, "_client_from_key", lambda key: client)

    db = _db_for_send()
    result = await gazelka_service.send_order(db, 4, 55, _send_req_confirm(), actor="t@t")

    assert result.ok is False
    order = db.add.call_args.args[0]
    assert order.status == GazelkaOrderStatus.FAILED  # точно не создана, не UNCERTAIN


async def test_send_order_confirms_even_when_post_raises(monkeypatch):
    """POST упал 5xx, но заявка появилась в списке → SENT, дубль не спровоцирован."""
    client = _FakeGazelkaClient(
        before=[_portal_plan("100")],
        after=[_portal_plan("100"), _portal_plan("101")],
        outcome=ValueError("Gazelka create 500"),
    )
    monkeypatch.setattr(gazelka_service, "_client_from_key", lambda key: client)

    db = _db_for_send()
    result = await gazelka_service.send_order(db, 4, 55, _send_req_confirm(), actor="t@t")

    assert result.ok is True
    assert result.ref == "101"
    order = db.add.call_args.args[0]
    assert order.status == GazelkaOrderStatus.SENT


# ─── Синк «машина назначена» из Газельки (READY → VEHICLE_ASSIGNED) ──────────


def _order_row(**kw) -> "gazelka_service.GazelkaOrderRow":
    from backend.schemas.gazelka import GazelkaOrderRow

    base = {"gazelka_id": "1", "status": "3", "status_label": "Принята в работу"}
    base.update(kw)
    return GazelkaOrderRow(**base)


def test_vehicle_assigned_ids_requires_link_and_vehicle_or_driver():
    rows = [
        _order_row(linked_assembly_id=1, vehicle="Мерседес Х392РМ37"),  # машина есть
        _order_row(linked_assembly_id=2, driver_name="Дейнекин А.Г."),  # только водитель — тоже сигнал
        _order_row(linked_assembly_id=3),  # связана, но маршрут без машины
        _order_row(vehicle="Газель А111АА37"),  # машина есть, но заявка не связана
    ]
    assert gazelka_service._vehicle_assigned_ids(rows) == {1, 2}


async def test_promote_vehicle_assigned_moves_ready_only(monkeypatch):
    from backend.models.assembly import AssemblyStatus

    ready = SimpleNamespace(id=646, status=AssemblyStatus.READY.value, vehicle_assigned_at=None)
    db = MagicMock()
    # select(...).scalars().all() → БД отдаёт только READY-строки (фильтр в SQL)
    db.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [ready])))
    db.add = MagicMock()
    db.commit = AsyncMock()
    monkeypatch.setattr(gazelka_service, "invalidate_cache", AsyncMock())

    promoted = await gazelka_service._promote_vehicle_assigned(db, 4, {646, 999})

    assert promoted == {646}
    assert ready.status == AssemblyStatus.VEHICLE_ASSIGNED
    assert ready.vehicle_assigned_at is not None
    history = db.add.call_args.args[0]  # запись в историю статусов
    assert history.new_status == AssemblyStatus.VEHICLE_ASSIGNED
    assert history.changed_by == "gazelka"
    db.commit.assert_awaited_once()


async def test_promote_vehicle_assigned_noop_without_candidates(monkeypatch):
    db = MagicMock()
    db.execute = AsyncMock()
    assert await gazelka_service._promote_vehicle_assigned(db, 4, set()) == set()
    db.execute.assert_not_awaited()  # пустой вход — ни одного запроса в БД


# ─── Фоновый синк статусов (машина назначена / пропуск / авто-шип) ───────────


def test_split_driver_name_last_first_order():
    # Кабинет отдаёт «Фамилия Имя Отчество» — first = имя, last = фамилия
    assert _split_driver_name("Дейнекин Андрей Геннадьевич") == ("Андрей", "Дейнекин")
    assert _split_driver_name("Иванов") == (None, "Иванов")
    assert _split_driver_name("  ") == (None, None)
    assert _split_driver_name(None) == (None, None)


def test_parse_rate_strips_thousand_separators():
    assert _parse_rate("6 500") == Decimal("6500")
    assert _parse_rate("6\xa0500,50") == Decimal("6500.50")
    assert _parse_rate("") is None
    assert _parse_rate(None) is None


def _active_joins() -> dict:
    return {
        "routes": {"5": {"id": "5", "driver_id": "9", "vehicle_id": "3", "carrier_id": "1", "date": "2026-07-23"}},
        "drivers": {"9": {"id": "9", "name": "Дейнекин Андрей Геннадьевич", "phone": "+79001234567"}},
        "vehicles": {"3": {"id": "3", "vehicle_make": "Мерседес", "vehicle_number": "Х392РМ37"}},
        "carriers": {"1": {"id": "1", "organization": "ИП Иванов"}},
    }


def test_extract_logistics_pulls_driver_vehicle_rate():
    plan = {"id": "77", "status": "31", "route_id": "5", "rate": "6 500", "delivery_date": "2026-07-24"}
    info = _extract_logistics(plan, _active_joins())
    assert info.car_number == "Х392РМ37"
    assert info.car_model == "Мерседес"
    assert (info.driver_first, info.driver_last) == ("Андрей", "Дейнекин")
    assert info.driver_phone == "+79001234567"
    assert info.carrier == "ИП Иванов"
    assert info.pickup_cost == Decimal("6500")
    assert info.delivery_date == date(2026, 7, 24)
    assert info.has_vehicle is True


def test_extract_logistics_no_route_no_vehicle():
    info = _extract_logistics({"id": "77", "status": "2"}, _active_joins())
    assert info.has_vehicle is False
    assert info.car_number is None


class _FakeActiveClient:
    """Клиент Газельки для синка: async-контекст + authenticate + fetch_active/planned."""

    def __init__(self, active: dict, planned: dict | None = None):
        self._active = active
        self._planned = planned or {"plans": [], "marketplaces": []}

    async def __aenter__(self) -> "_FakeActiveClient":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def authenticate(self) -> None:
        return None

    async def fetch_active(self) -> dict:
        return self._active

    async def fetch_planned(self) -> dict:
        return self._planned


def _patch_sync(
    monkeypatch,
    plan: dict,
    linked: dict,
    *,
    planned_plans: list | None = None,
    supply_idx: dict | None = None,
) -> tuple:
    """Замокать окружение sync_gazelka_states, вернуть моки downstream-вызовов."""
    from backend.services import wb_supply_service
    from backend.services.assembly import status as assembly_status

    # fetch_active отдаёт справочники СПИСКАМИ (joins строит sync сам из data.get(k))
    lists = {k: list(v.values()) for k, v in _active_joins().items()}
    active = {"plans": [plan], **lists, "marketplaces": []}
    planned = {"plans": planned_plans or [], "marketplaces": []}
    monkeypatch.setattr(gazelka_service, "_get_key_or_none", AsyncMock(return_value=object()))
    monkeypatch.setattr(gazelka_service, "_client_from_key", lambda key: _FakeActiveClient(active, planned))
    monkeypatch.setattr(gazelka_service, "_linked_map", AsyncMock(return_value=linked))
    monkeypatch.setattr(gazelka_service, "_assembly_supply_index", AsyncMock(return_value=supply_idx or {}))

    apply_mock = AsyncMock(return_value="VEHICLE_ASSIGNED")
    ship_mock = AsyncMock()
    pass_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(assembly_status, "apply_gazelka_logistics", apply_mock)
    monkeypatch.setattr(assembly_status, "ship_request", ship_mock)
    monkeypatch.setattr(wb_supply_service, "try_autopush_pass_by_assembly", pass_mock)
    return apply_mock, ship_mock, pass_mock


async def test_sync_states_ships_and_pushes_pass_on_in_route(monkeypatch):
    """«В маршруте» (31): реквизиты+пропуск+тариф зеркалятся, сборка отгружается."""
    plan = {"id": "77", "status": "31", "route_id": "5", "rate": "6 500", "delivery_date": "2026-07-24"}
    apply_mock, ship_mock, pass_mock = _patch_sync(monkeypatch, plan, {"77": (646, "ASM-1", "READY")})

    stats = await gazelka_service.sync_gazelka_states(MagicMock(), 4)

    assert stats == {"autolinked": 0, "assigned": 1, "passed": 1, "shipped": 1}
    kw = apply_mock.await_args.kwargs
    assert kw["car_number"] == "Х392РМ37"
    assert kw["car_model"] == "Мерседес"
    assert kw["carrier_name"] == "ИП Иванов"  # перевозчик уедет в отгрузку (не «—»)
    assert kw["pickup_cost"] == Decimal("6500")
    assert kw["promote"] is True
    ship_mock.assert_awaited_once()
    assert ship_mock.await_args.kwargs.get("allow_gazelka_ready") is True
    pass_mock.assert_awaited_once()


async def test_sync_states_assigns_but_no_ship_on_accepted(monkeypatch):
    """«Принята в работу» (3): машина назначается + пропуск, но НЕ отгружаем."""
    plan = {"id": "77", "status": "3", "route_id": "5", "rate": "6 500"}
    apply_mock, ship_mock, pass_mock = _patch_sync(monkeypatch, plan, {"77": (646, "ASM-1", "READY")})

    stats = await gazelka_service.sync_gazelka_states(MagicMock(), 4)

    assert stats == {"autolinked": 0, "assigned": 1, "passed": 1, "shipped": 0}
    apply_mock.assert_awaited_once()
    ship_mock.assert_not_awaited()


async def test_sync_states_skips_unlinked_orders(monkeypatch):
    """Заявка портала без связанной сборки — не трогаем ничего."""
    plan = {"id": "77", "status": "31", "route_id": "5", "rate": "6 500"}
    apply_mock, ship_mock, pass_mock = _patch_sync(monkeypatch, plan, {})  # linked пуст

    stats = await gazelka_service.sync_gazelka_states(MagicMock(), 4)

    assert stats == {"autolinked": 0, "assigned": 0, "passed": 0, "shipped": 0}
    apply_mock.assert_not_awaited()
    ship_mock.assert_not_awaited()


async def test_sync_states_ship_idempotent_swallows_value_error(monkeypatch):
    """Повторный синк уже отгруженной: ship_request кидает ValueError → глушим, синк живёт."""
    plan = {"id": "77", "status": "31", "route_id": "5", "rate": "6 500"}
    apply_mock, ship_mock, pass_mock = _patch_sync(monkeypatch, plan, {"77": (646, "ASM-1", "READY")})
    ship_mock.side_effect = ValueError("Invalid status transition: SHIPPED -> SHIPPED")

    stats = await gazelka_service.sync_gazelka_states(MagicMock(), 4)

    assert stats["shipped"] == 0  # отгрузка не засчитана, но исключение не всплыло
    ship_mock.assert_awaited_once()


async def test_sync_states_no_integration_returns_zero(monkeypatch):
    monkeypatch.setattr(gazelka_service, "_get_key_or_none", AsyncMock(return_value=None))
    stats = await gazelka_service.sync_gazelka_states(MagicMock(), 4)
    assert stats == {"autolinked": 0, "assigned": 0, "passed": 0, "shipped": 0}


async def test_sync_states_autolinks_by_wb_supply_number(monkeypatch):
    """Несвязанная заявка с № поставки WB в supply_id → авто-связь MATCHED (без клика)."""
    # Запланированная заявка (код 2, без маршрута) — авто-связь есть, reconcile её не трогает.
    planned = {"id": "330662", "status": "2", "supply_id": "Невинномысск 40842600 PVB-000"}
    apply_mock, ship_mock, pass_mock = _patch_sync(
        monkeypatch,
        {"id": "999", "status": "2"},  # активная — без матча/машины
        {},  # ничего не связано
        planned_plans=[planned],
        supply_idx={"40842600": (945, "ASM-774")},
    )
    added: list = []
    db = MagicMock()
    db.add = MagicMock(side_effect=lambda o: added.append(o))
    db.commit = AsyncMock()

    stats = await gazelka_service.sync_gazelka_states(db, 4)

    assert stats["autolinked"] == 1
    order = next(o for o in added if getattr(o, "gazelka_ref", None) == "330662")
    assert order.status == GazelkaOrderStatus.MATCHED
    assert order.assembly_request_id == 945
    assert order.payload == {"_autolink": True}
    # Запланированную не отгружаем и машину не назначаем
    ship_mock.assert_not_awaited()


async def test_sync_states_autolink_skips_already_linked_assembly(monkeypatch):
    """Сборка уже связана с другой заявкой → авто-связь НЕ переклеивает на новую."""
    planned = {"id": "330662", "status": "2", "supply_id": "40842600"}
    _patch_sync(
        monkeypatch,
        {"id": "999", "status": "2"},
        {"555": (945, "ASM-774", None)},  # 945 уже связана с заявкой 555
        planned_plans=[planned],
        supply_idx={"40842600": (945, "ASM-774")},
    )
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()

    stats = await gazelka_service.sync_gazelka_states(db, 4)

    assert stats["autolinked"] == 0
    db.add.assert_not_called()


# ─── Перевозчик Газельки по имени (ИНН нет — в листе оплаты не «—») ───────────


async def test_resolve_carrier_by_name_reuses_existing_carrier():
    from backend.services.assembly.status import _resolve_carrier_by_name

    existing = SimpleNamespace(id=12, primary_type="CARRIER", name="ИП Иванов")
    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [existing])))
    db.add = MagicMock()
    db.flush = AsyncMock()

    cid = await _resolve_carrier_by_name(db, 4, "ИП Иванов")

    assert cid == 12
    db.add.assert_not_called()  # существующего не дублируем


async def test_resolve_carrier_by_name_creates_when_absent():
    from backend.services.assembly.status import _resolve_carrier_by_name

    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [])))
    created: dict = {}

    def _add(obj):
        obj.id = 99
        created["cp"] = obj

    db.add = MagicMock(side_effect=_add)
    db.flush = AsyncMock()

    cid = await _resolve_carrier_by_name(db, 4, "  ИП Новый  ")

    assert cid == 99
    assert created["cp"].name == "ИП Новый"  # trimmed
    assert created["cp"].primary_type == "CARRIER"


async def test_resolve_carrier_by_name_empty_returns_none():
    from backend.services.assembly.status import _resolve_carrier_by_name

    db = MagicMock()
    db.execute = AsyncMock()
    assert await _resolve_carrier_by_name(db, 4, "  ") is None
    db.execute.assert_not_awaited()
