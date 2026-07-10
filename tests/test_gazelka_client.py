# ruff: noqa: RUF001, RUF002, RUF003
"""
Tests for the Gazelka (gazelka.space) HTML form parser + result heuristics.

Uses fixture tests/fixtures/gazelka_apply_sample.html (representative apply form).
No network: only pure parsing/heuristic functions.
"""

from datetime import date
from pathlib import Path

import httpx
import pytest

from backend.integrations.gazelka_client import (
    GazelkaApiError,
    _extract_apply_form,
    _extract_json_array,
    _hidden_inputs,
    _login_ok,
    _merge_payload,
    _normalize_host,
    _parse_apply_page,
    _parse_create_result,
    _redact,
    _selects,
    _validate_customer_id,
    _validate_plan_id,
    _visible_input_values,
    decode_days,
)

FIXTURE = Path(__file__).parent / "fixtures" / "gazelka_apply_sample.html"


@pytest.fixture
def apply_html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture
def apply_block(apply_html: str) -> str:
    return _extract_apply_form(apply_html)


# ─── Form extraction ──────────────────────────────────────────────────────────


def test_extract_apply_form_stops_at_own_close(apply_block: str):
    """Блок главной формы не должен затягивать поля сиблинг-формы pickupForm."""
    assert 'id="apply-form"' in apply_block
    assert "OTHER_TOKEN" not in apply_block
    assert "WRONG_FORM" not in apply_block


def test_hidden_inputs_csrf_and_action(apply_block: str):
    hidden = _hidden_inputs(apply_block)
    assert hidden["j927b4gtyf0m"] == "abc123def456token"
    assert hidden["action"] == "save_plan"
    assert "OTHER_TOKEN" not in hidden  # из чужой формы не утекло


def test_visible_inputs_capture_account_phone(apply_block: str):
    inputs = _visible_input_values(apply_block)
    assert inputs.get("customer_phone") == "+79203491330"
    # submit/checkbox/hidden исключены
    assert "dosubmit" not in inputs
    assert "palleting" not in inputs
    assert "action" not in inputs


def test_selects_parse_options(apply_block: str):
    selects = _selects(apply_block)
    assert ("6596", "ПЛЮС ВАЙБ ООО") in selects["entity_id"]
    assert ("4", "WildBerries") in selects["marketplace_id"]
    assert ("1", "Иваново") in selects["price_id"]
    # значение 999 из чужой формы не попало в entity_id
    assert all(v != "999" for v, _ in selects["entity_id"])


# ─── customer_id guard ────────────────────────────────────────────────────────


def test_validate_customer_id_ok():
    assert _validate_customer_id("4988") == "4988"
    assert _validate_customer_id(4988) == "4988"


@pytest.mark.parametrize("bad", ["", "4988/../x", "abc", "49 88"])
def test_validate_customer_id_rejects_non_digit(bad):
    with pytest.raises(GazelkaApiError):
        _validate_customer_id(bad)


# ─── Host allowlist (SSRF-guard) ──────────────────────────────────────────────


def test_normalize_host_allows_gazelka():
    assert _normalize_host("gazelka.space") == "https://gazelka.space"
    assert _normalize_host("https://gazelka.space/") == "https://gazelka.space"
    assert _normalize_host("") == "https://gazelka.space"  # дефолт


@pytest.mark.parametrize(
    "bad",
    [
        "http://gazelka.space",  # не https
        "https://evil.com",  # чужой домен
        "https://gazelka.space.evil.com",  # суффикс-обман
        "https://gazelka.space:8080",  # порт
        "https://user:pw@gazelka.space",  # userinfo
        "https://gazelka.space/path",  # путь
    ],
)
def test_normalize_host_rejects(bad):
    with pytest.raises(GazelkaApiError):
        _normalize_host(bad)


# ─── PII-редакция ─────────────────────────────────────────────────────────────


def test_redact_strips_email_and_phone():
    out = _redact("contact ohotnikova1010@gmail.com tel +79203491330 done")
    assert "@" not in out
    assert "79203491330" not in out
    assert "[email]" in out and "[phone]" in out


# ─── Login heuristic (follow_redirects=False → по 3xx Location) ────────────────


def _redirect(location: str) -> httpx.Response:
    return httpx.Response(
        302, headers={"location": location}, request=httpx.Request("POST", "https://gazelka.space/")
    )


def _page(text: str = "", path: str = "/customer/apply/4988") -> httpx.Response:
    return httpx.Response(200, text=text, request=httpx.Request("POST", f"https://gazelka.space{path}"))


def test_login_ok_on_customer_redirect():
    assert _login_ok(_redirect("/customer/orders/4988")) is True


def test_login_ok_on_absolute_location():
    assert _login_ok(_redirect("https://gazelka.space/customer/orders/4988")) is True


def test_login_fail_when_no_redirect():
    # неверный логин → портал ре-рендерит форму (200, без Location)
    assert _login_ok(_page("<form>Вход</form>", path="/")) is False


def test_login_fail_on_redirect_to_login():
    assert _login_ok(_redirect("/?error=1")) is False


# ─── Create-order heuristic ──────────────────────────────────────────────────

APPLY_PATH = "/customer/apply/4988"


def test_create_result_success_on_redirect_away():
    res = _parse_create_result(_redirect("/customer/orders/4988"), APPLY_PATH)
    assert res.ok is True
    assert res.ref == "4988"


def test_create_result_uncertain_when_form_returns():
    resp = _page("<form id='apply-form'>Ошибка валидации</form>")
    res = _parse_create_result(resp, APPLY_PATH)
    assert res.ok is False
    assert "Ошибка" in res.excerpt or "подтверждение" in res.message.lower()


# ─── Встроенный JSON (Tabulator-данные) ──────────────────────────────────────


def test_extract_json_array_balances_brackets_in_strings():
    # значение history само содержит [ ] — не должно ломать баланс скобок
    page = 'x "plans":[{"id":"1","history":"[{\\"x\\":1}]","addr":"A&quot;B"}] y'
    rows = _extract_json_array(page, "plans")
    assert len(rows) == 1
    assert rows[0]["id"] == "1"
    assert rows[0]["history"] == '[{"x":1}]'
    assert rows[0]["addr"] == "A&quot;B"  # HTML-сущность остаётся (unescape — у потребителя)


def test_extract_json_array_missing_key_returns_empty():
    assert _extract_json_array("ничего тут нет", "plans") == []


# ─── Снимок формы: дефолты, склады, график ───────────────────────────────────


def test_form_defaults_mirror_browser_submission(apply_html: str):
    """Браузер шлёт ВСЕ поля формы; портал 500-ит, если поля нет в теле POST."""
    form = _parse_apply_page(apply_html)
    # прешитые нули — именно их не хватало в POST
    assert form.defaults["volume"] == "0"
    assert form.defaults["weight2"] == "0"
    assert form.defaults["notes"] == ""
    # select без selected → первая опция, с selected → выбранная.
    # Порядок опций у портала произвольный: Симферополь(5) первый, выбран Иваново(1).
    assert form.selects["price_id"][0][0] == "5"
    assert form.defaults["price_id"] == "1"
    assert form.defaults["marketplace_id"] == "0"
    # чекбоксы/submit/hidden браузер в дефолтах не шлёт
    assert "palleting" not in form.defaults
    assert "dosubmit" not in form.defaults
    assert "action" not in form.defaults
    # сиблинг-форма не протекает
    assert form.defaults["entity_id"] == "6596"


def test_parse_apply_page_reads_min_dates(apply_html: str):
    form = _parse_apply_page(apply_html)
    assert form.min_departure == date(2026, 7, 10)
    assert form.min_delivery == date(2026, 7, 11)


def test_delivery_places_keep_marketplace_twins(apply_html: str):
    """«Казань» есть у WB (place 25) и у Ozon (26) — это разные направления."""
    places = _parse_apply_page(apply_html).places
    assert ("Казань", "25", "4") in [(p.value, p.place_id, p.marketplace_id) for p in places]
    assert ("Казань", "26", "1") in [(p.value, p.place_id, p.marketplace_id) for p in places]
    assert all(p.value for p in places)  # плейсхолдер без value отброшен


def test_parse_schedule_decodes_days_and_active(apply_html: str):
    schedule = _parse_apply_page(apply_html).schedule
    kazan = schedule["1-25"]
    assert kazan.loading_days == [1, 3]  # Пн/Ср
    assert kazan.delivery_days == [2, 4]  # Вт/Чт
    assert kazan.eta_days == 1
    assert kazan.active is True
    assert schedule["1-18"].loading_days is None  # код «2» = любой день
    assert schedule["1-99"].active is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("151113", [5, 1, 3]),  # Пт/Пн/Ср
        ("161214", [6, 2, 4]),
        ("15", [5]),
        ("2", None),  # любой день
        ("3", None),
        ("0", None),
        ("", None),
        ("111116", [0, 1, 2, 3, 4, 5]),  # все, кроме Сб
        ("мусор", None),
    ],
)
def test_decode_days(raw, expected):
    assert decode_days(raw) == expected


def test_merge_payload_fills_defaults_and_keeps_csrf(apply_html: str):
    form = _parse_apply_page(apply_html)
    payload = _merge_payload(form, {"pallets": "2", "notes": None, "weight": "370"})
    assert payload["action"] == "save_plan"
    assert payload["j927b4gtyf0m"] == "abc123def456token"
    assert payload["pallets"] == "2"  # наше значение поверх дефолта
    assert payload["notes"] == ""  # None → дефолт формы, поле НЕ исчезает
    assert payload["volume"] == "0"  # поле, которого не было в старом POST
    assert payload["weight"] == "370"


def test_validate_plan_id():
    assert _validate_plan_id("313621") == "313621"
    assert _validate_plan_id(313621) == "313621"
    with pytest.raises(GazelkaApiError):
        _validate_plan_id("abc")
