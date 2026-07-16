# ruff: noqa: RUF001, RUF002, RUF003
"""build_supply_discrepancy_message — HTML-сводка расхождений WB-поставок для TG."""

from backend.services.fulfillment_notify import build_supply_discrepancy_message


def _row(**over):
    base = {
        "assembly_id": 1,
        "number": "ASM-643",
        "status": "VEHICLE_ASSIGNED",
        "source_warehouse_name": "натали",
        "warehouse_name": "Коледино",
        "delivery_date": "2026-07-20",
        "planned_date": "2026-07-15",
        "date_diff_days": 5,
        "pallets_count": 5,
        "pass_pallets": None,
        "wb_supply_id": "WB-1",
        "wb_status": "ON_DELIVERY",
        "sync_status": None,
        "date_mismatch": False,
        "pallet_mismatch": False,
        "pass_missing": False,
    }
    base.update(over)
    return base


def test_empty_returns_none():
    assert build_supply_discrepancy_message("acme", []) is None


def test_no_active_flags_returns_none():
    assert build_supply_discrepancy_message("acme", [_row()]) is None


def test_date_mismatch_line():
    msg = build_supply_discrepancy_message("acme", [_row(date_mismatch=True)])
    assert msg is not None
    assert "ASM-643" in msg
    assert "натали" in msg and "Коледино" in msg
    assert "сдача 20.07" in msg and "бронь WB 15.07" in msg
    assert "(+5 дн)" in msg
    assert "машина назначена" in msg
    assert "/p/acme/warehouse/assembly/analytics" in msg


def test_date_mismatch_negative_diff():
    msg = build_supply_discrepancy_message("acme", [_row(date_mismatch=True, date_diff_days=-2)])
    assert "(-2 дн)" in msg


def test_pallet_mismatch_line():
    msg = build_supply_discrepancy_message("acme", [_row(pallet_mismatch=True, pallets_count=8, pass_pallets=6)])
    assert "паллеты: у нас <b>8</b>, в пропуске <b>6</b>" in msg


def test_pass_missing_line():
    msg = build_supply_discrepancy_message("acme", [_row(pass_missing=True)])
    assert "пропуск WB не оформлен" in msg


def test_all_three_flags_on_one_row():
    msg = build_supply_discrepancy_message(
        "acme", [_row(date_mismatch=True, pallet_mismatch=True, pass_pallets=6, pass_missing=True)]
    )
    assert "сдача 20.07" in msg
    assert "паллеты" in msg
    assert "пропуск WB не оформлен" in msg
    assert msg.count("ASM-643") == 1  # одна строка-сборка, три пояснения


def test_shipped_status_label():
    msg = build_supply_discrepancy_message("acme", [_row(status="SHIPPED", pass_missing=True)])
    assert "в пути" in msg


def test_missing_wb_warehouse_omits_arrow():
    msg = build_supply_discrepancy_message("acme", [_row(warehouse_name=None, pass_missing=True)])
    assert "→" not in msg
    assert "натали" in msg


def test_html_escaped():
    msg = build_supply_discrepancy_message("acme", [_row(number="A<b>&", pass_missing=True)])
    assert "A&lt;b&gt;&amp;" in msg
    assert "A<b>&" not in msg


def test_cap_and_overflow_note():
    rows = [_row(assembly_id=i, number=f"ASM-{i}", pass_missing=True) for i in range(20)]
    msg = build_supply_discrepancy_message("acme", rows)
    assert "(20)" in msg  # заголовок считает все активные
    assert "… и ещё 5" in msg
    assert "ASM-14" in msg and "ASM-15" not in msg  # показаны первые 15


def test_no_slug_omits_link():
    msg = build_supply_discrepancy_message(None, [_row(pass_missing=True)])
    assert "Открыть" not in msg
    assert "ASM-643" in msg
