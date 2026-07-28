"""
Tests for the daily WB measurements digest (Telegram, 09:00 MSK).

Covers:
- Pure helpers: _ru_plural, build_measurement_digest_text (format, empty, attention).
- warehouse_digest_data: subject grouping, ≥10% deviation-from-card detection,
  project isolation and period filtering.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from backend.models.cost import Nomenclature
from backend.models.wb_finance import WbFinanceRow
from backend.models.wb_measurements import WbMeasurementPenalty, WbWarehouseMeasurement
from backend.services import measurements_service as m


# ── Pure helpers ─────────────────────────────────────────────────────────────


class TestRuPlural:
    def test_one(self):
        assert m._ru_plural(1, "замер", "замера", "замеров") == "замер"

    def test_few(self):
        assert m._ru_plural(3, "замер", "замера", "замеров") == "замера"

    def test_many(self):
        assert m._ru_plural(5, "замер", "замера", "замеров") == "замеров"

    def test_teens_are_many(self):
        # 11–14 — исключение: всегда «замеров», хотя оканчиваются на 1..4
        assert m._ru_plural(11, "замер", "замера", "замеров") == "замеров"
        assert m._ru_plural(12, "замер", "замера", "замеров") == "замеров"

    def test_21_is_one(self):
        assert m._ru_plural(21, "замер", "замера", "замеров") == "замер"


class TestBuildDigestText:
    def _data(self):
        return {
            "total": 6,
            "subjects": [("Пледы", 3), ("Чехлы для мебели", 2), ("Ковры", 1)],
            "attention": [
                {"nm_id": 946288655, "subject": "Диван", "meas": Decimal("62.5"),
                 "card": Decimal("48.0"), "dev": Decimal("30.2")},
            ],
        }

    def test_empty_returns_none(self):
        assert m.build_measurement_digest_text(date(2026, 7, 9), date(2026, 7, 10), {"total": 0}) is None

    def test_header_and_period(self):
        text = m.build_measurement_digest_text(date(2026, 7, 9), date(2026, 7, 10), self._data())
        assert "Замеры WB за 09.07 – 10.07" in text
        assert "Поступило <b>6</b> замеров по <b>3</b> предметам" in text

    def test_subject_lines_and_plural(self):
        text = m.build_measurement_digest_text(date(2026, 7, 9), date(2026, 7, 10), self._data())
        assert "• Пледы — <b>3</b> замера" in text
        assert "• Ковры — <b>1</b> замер" in text

    def test_attention_block(self):
        text = m.build_measurement_digest_text(date(2026, 7, 9), date(2026, 7, 10), self._data())
        assert "Отклонение от карточки ≥10%: 1 замер" in text
        assert "<code>946288655</code>" in text
        assert "62.5 л vs 48 л (+30%)" in text

    def test_no_attention_block_when_empty(self):
        data = self._data()
        data["attention"] = []
        text = m.build_measurement_digest_text(date(2026, 7, 9), date(2026, 7, 10), data)
        assert "Отклонение от карточки" not in text

    def test_html_escaped_subject(self):
        data = {"total": 1, "subjects": [("A & B <x>", 1)], "attention": []}
        text = m.build_measurement_digest_text(date(2026, 7, 9), date(2026, 7, 10), data)
        assert "A &amp; B &lt;x&gt;" in text


# ── DB-backed: warehouse_digest_data ─────────────────────────────────────────

_DF = datetime(2026, 7, 9, 0, 0, tzinfo=timezone.utc)
_DT = datetime(2026, 7, 10, 23, 59, 59, tzinfo=timezone.utc)
_IN_PERIOD = datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc)
_OUT_PERIOD = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)


def _meas(project_id, dim_id, nm_id, subject, volume, measured_at):
    return WbWarehouseMeasurement(
        project_id=project_id, dim_id=dim_id, nm_id=nm_id,
        subject_name=subject, volume=Decimal(str(volume)), measured_at=measured_at,
    )


@pytest.mark.asyncio
async def test_digest_data_grouping_and_period(db_session, project):
    db_session.add_all([
        _meas(project.id, 1, 101, "Ковры", 10.0, _IN_PERIOD),
        _meas(project.id, 2, 102, "Пледы", 20.0, _IN_PERIOD),
        _meas(project.id, 3, 103, "Пледы", 21.0, _IN_PERIOD),
        _meas(project.id, 4, 104, "Ковры", 11.0, _OUT_PERIOD),  # вне периода — не считается
    ])
    await db_session.commit()

    data = await m.warehouse_digest_data(db_session, project.id, _DF, _DT)
    assert data["total"] == 3
    # Пледы (2) впереди Ковров (1)
    assert data["subjects"][0] == ("Пледы", 2)
    assert dict(data["subjects"]) == {"Пледы": 2, "Ковры": 1}


@pytest.mark.asyncio
async def test_digest_data_attention_threshold(db_session, project):
    # Карточка 100 л. Замер 130 л = +30% (≥10 → attention); замер 105 л = +5% (нет).
    db_session.add_all([
        Nomenclature(project_id=project.id, barcode=f"bc-big-{project.id}", article_wb=201, volume_l=Decimal("100")),
        Nomenclature(project_id=project.id, barcode=f"bc-ok-{project.id}", article_wb=202, volume_l=Decimal("100")),
        _meas(project.id, 11, 201, "Диван", 130.0, _IN_PERIOD),
        _meas(project.id, 12, 202, "Кресло", 105.0, _IN_PERIOD),
    ])
    await db_session.commit()

    data = await m.warehouse_digest_data(db_session, project.id, _DF, _DT)
    assert data["total"] == 2
    nm_ids = [a["nm_id"] for a in data["attention"]]
    assert nm_ids == [201]  # только превышение ≥10%
    assert data["attention"][0]["dev"] == pytest.approx(Decimal("30"))


@pytest.mark.asyncio
async def test_digest_data_project_isolation(db_session, project, other_project):
    db_session.add_all([
        _meas(project.id, 21, 301, "Ковры", 10.0, _IN_PERIOD),
        _meas(other_project.id, 22, 302, "Пледы", 20.0, _IN_PERIOD),  # чужой проект
    ])
    await db_session.commit()

    data = await m.warehouse_digest_data(db_session, project.id, _DF, _DT)
    assert data["total"] == 1
    assert dict(data["subjects"]) == {"Ковры": 1}


def _pen(project_id, dim_id, nm_id, subject, amount):
    return WbMeasurementPenalty(
        project_id=project_id, dim_id=dim_id, nm_id=nm_id, subject_name=subject,
        penalty_amount=Decimal(str(amount)), reversal_amount=Decimal("0"),
        penalty_date=_IN_PERIOD,
    )


@pytest.mark.asyncio
async def test_summary_subject_fallback(db_session, project):
    """Удержание без предмета → добираем из замера склада, затем из карточки."""
    db_session.add_all([
        _pen(project.id, 51, 501, None, 100),                       # добор из замера
        _meas(project.id, 61, 501, "Ковры", 10.0, _IN_PERIOD),
        _pen(project.id, 52, 502, None, 50),                        # добор из карточки
        Nomenclature(project_id=project.id, barcode=f"bc-sub-{project.id}", article_wb=502, subject="Пледы"),
        _pen(project.id, 53, 503, "Чехлы", 30),                     # свой предмет
        _pen(project.id, 54, 504, "", 20),                          # пустая строка = как None
        _meas(project.id, 64, 504, "Покрывала", 5.0, _IN_PERIOD),
    ])
    await db_session.commit()

    items, _totals = await m.summarize_penalties_by_article(db_session, project.id)
    by_nm = {i["nm_id"]: i["subject_name"] for i in items}
    assert by_nm[501] == "Ковры"       # из замера склада
    assert by_nm[502] == "Пледы"       # из карточки (замера нет)
    assert by_nm[503] == "Чехлы"       # собственный не тронут
    assert by_nm[504] == "Покрывала"   # пустая строка тоже добирается


def test_day_bounds_are_msk():
    """Границы периода — по МСК, не по UTC (иначе теряются ранне-утренние замеры)."""
    df, dt = m._day_bounds(date(2026, 7, 23), date(2026, 7, 23))
    # МСК-полночь 23.07 = 22.07 21:00 UTC; конец дня = 23.07 20:59:59 UTC
    assert df.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M") == "2026-07-22T21:00"
    assert dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M") == "2026-07-23T20:59"


@pytest.mark.asyncio
async def test_list_warehouse_early_msk_included(db_session, project):
    """Замер в 01:00 МСК (= 22:00 UTC пред. суток) при фильтре на его МСК-дату — виден."""
    early = datetime(2026, 7, 22, 22, 0, tzinfo=timezone.utc)  # 23.07 01:00 МСК
    prev = datetime(2026, 7, 22, 20, 0, tzinfo=timezone.utc)   # 22.07 23:00 МСК
    db_session.add_all([
        _meas(project.id, 71, 701, "Ковры", 10.0, early),
        _meas(project.id, 72, 702, "Пледы", 11.0, prev),
    ])
    await db_session.commit()

    items, total = await m.list_warehouse_measurements(
        db_session, project.id, date(2026, 7, 23), date(2026, 7, 23), None, None, None, None, 500, 0
    )
    nm_ids = {i.nm_id for i in items}
    assert 701 in nm_ids       # ранне-утренний по МСК — включён
    assert 702 not in nm_ids   # предыдущий день по МСК — исключён
    assert total == 1


# ── Штрафы за габариты в сводке (из финотчёта) ───────────────────────────────

_DIM = "Занижение фактических габаритов упаковки товара"
_DIM_STORNO = "Сторно. Занижение фактических габаритов упаковки товара"


def _fin(pid, rrd_id, nm, subj, brand, btype, pen, rr=date(2026, 7, 27)):
    return WbFinanceRow(
        project_id=pid, rrd_id=rrd_id, realizationreport_id=1,
        date_from=rr, date_to=rr, rr_dt=rr,
        nm_id=nm, subject_name=subj, brand_name=brand,
        bonus_type_name=btype, penalty=Decimal(str(pen)),
    )


@pytest.mark.asyncio
async def test_finance_penalties_digest_data(db_session, project):
    db_session.add_all([
        _fin(project.id, 1, 910389065, "Ковры", "НУ-НУ", _DIM, 6000),
        _fin(project.id, 2, 910389065, "Ковры", "НУ-НУ", _DIM, 4202),        # тот же nm → сумма
        _fin(project.id, 3, 910389065, "Ковры", "НУ-НУ", _DIM_STORNO, -202),  # сторно → нетто
        _fin(project.id, 4, 889697232, "Шторы", "Уютопия", _DIM, 1520),
        _fin(project.id, 5, 777, "Ковры", "НУ-НУ", "Логистика", 5000),        # НЕ габаритный — игнор
        _fin(project.id, 6, 888, "Ковры", "НУ-НУ", _DIM, 999, rr=date(2026, 7, 26)),  # другой день
        Nomenclature(project_id=project.id, barcode=f"bc-p-{project.id}", article_wb=910389065, volume_l=Decimal("12")),
        _meas(project.id, 91, 910389065, "Ковры", 15.0, _IN_PERIOD),
    ])
    await db_session.commit()

    data = await m.finance_penalties_digest_data(db_session, project.id, date(2026, 7, 27))
    assert data["total"] == Decimal("11520")   # 10000 (Ковры) + 1520 (Шторы)
    assert data["count"] == 2                   # 2 артикула; логистика и другой день не в счёт
    assert data["subjects"][0]["subject"] == "Ковры"
    assert data["subjects"][0]["total"] == Decimal("10000")
    kovry = data["subjects"][0]["items"][0]
    assert kovry["nm_id"] == 910389065
    assert kovry["vol"]["dev"] == pytest.approx(Decimal("25"))  # замер 15 vs карт 12 = +25%


def test_build_penalties_digest_text():
    data = {
        "total": Decimal("11520"), "count": 2,
        "subjects": [
            {"subject": "Ковры", "total": Decimal("10000"), "items": [
                {"nm_id": 910389065, "penalty": Decimal("10000"),
                 "vol": {"card": Decimal("12"), "meas": Decimal("15"), "dev": Decimal("25")}}]},
            {"subject": "Шторы", "total": Decimal("1520"), "items": [
                {"nm_id": 889697232, "penalty": Decimal("1520"), "vol": {}}]},
        ],
    }
    txt = m.build_penalties_digest_text(date(2026, 7, 27), data)
    assert "<b>ПРОВЕРЬТЕ ГАБАРИТЫ</b>" in txt
    assert "за 27.07" in txt
    assert "<b>Ковры</b>" in txt
    assert "<pre>" in txt and "910389065" in txt
    assert "(+25%)" in txt
    assert "нет замера/карточки" in txt   # Шторы без объёма


def test_build_penalties_digest_text_empty():
    assert m.build_penalties_digest_text(date(2026, 7, 27), {"subjects": []}) is None
