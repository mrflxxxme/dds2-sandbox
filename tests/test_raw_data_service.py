"""
Tests for raw_data_service (обзор сырых данных + принудительная дозагрузка).

Реестр источников декларативный, поэтому легко разъехаться: указать несуществующий
адаптер, забыть колонку даты в модели или запустить вторую дозагрузку поверх идущей.
"""

import pytest

from backend.services import raw_data_service as rds


def test_every_source_key_is_unique():
    keys = [s.key for s in rds.RAW_SOURCES]
    assert len(keys) == len(set(keys))


def test_refresh_names_have_adapters():
    """refresh указывает на реально существующий адаптер."""
    for s in rds.RAW_SOURCES:
        if s.refresh is not None:
            assert s.refresh in rds.REFRESH_ADAPTERS, f"{s.key}: нет адаптера {s.refresh}"


def test_date_field_exists_on_model():
    """Колонка даты есть в ORM-модели — иначе _source_stats упадёт на getattr."""
    for s in rds.RAW_SOURCES:
        assert hasattr(s.model, s.date_field), f"{s.key}: у {s.model.__name__} нет {s.date_field}"
        assert hasattr(s.model, "project_id"), f"{s.key}: у {s.model.__name__} нет project_id"


def test_labels_reference_existing_columns():
    """Метка на несуществующую колонку молча не применится — ловим на тесте."""
    for s in rds.RAW_SOURCES:
        real = {c.name for c in s.model.__table__.columns}
        unknown = set(s.labels) - real
        assert not unknown, f"{s.key}: меток нет таких колонок — {sorted(unknown)}"


def test_service_columns_hide_internal_fields():
    for s in rds.RAW_SOURCES:
        keys = {c["key"] for c in rds.source_columns(s)}
        assert "id" not in keys and "project_id" not in keys


def test_id_columns_are_not_formatted_as_numbers():
    """«37 158 056» вместо «37158056» — ID не должен получать разделители разрядов."""
    cols = {c["key"]: c["type"] for c in rds.source_columns(rds.SOURCES_BY_KEY["ad_nm"])}
    assert cols["campaign_id"] == "id"
    assert cols["nm_id"] == "id"
    assert cols["views"] == "number"


def test_column_types_are_known():
    allowed = {"id", "date", "datetime", "number", "bool", "json", "string"}
    for s in rds.RAW_SOURCES:
        for c in rds.source_columns(s):
            assert c["type"] in allowed, f"{s.key}.{c['key']}: тип {c['type']}"


async def test_unknown_source_rows_raises():
    with pytest.raises(ValueError):
        await rds.get_source_rows(None, 1, "нет-такого")


def test_refreshable_sources_have_hint():
    for s in rds.RAW_SOURCES:
        if s.refresh is not None:
            assert s.refresh_hint, f"{s.key}: кнопка без подсказки"


async def test_unknown_source_is_unsupported():
    res = await rds.start_refresh(1, "нет-такого", None, None)
    assert res["status"] == "unsupported"


async def test_refresh_is_not_started_twice(monkeypatch):
    """Пока дозагрузка идёт, повторный запуск не плодит вторую задачу."""
    rds._REFRESH.clear()
    rds._REFRESH[(1, "prices")] = {"status": "running", "started_at": "x", "finished_at": None, "error": None}
    try:
        res = await rds.start_refresh(1, "prices", None, None)
        assert res["status"] == "already_running"
    finally:
        rds._REFRESH.clear()


async def test_period_ignored_for_snapshot_sources(monkeypatch):
    """У «цен» период бессмысленен (срез «сейчас») — start_refresh должен его обнулить,
    а ranged-источнику («воронка») передать как есть."""
    import asyncio
    from datetime import date

    calls = []

    def fake_create_task(coro):
        coro.close()  # задачу не запускаем — важны только аргументы
        return None

    async def fake_run(project_id, key, date_from, date_to):
        calls.append((key, date_from, date_to))

    rds._REFRESH.clear()
    monkeypatch.setattr(asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(rds, "_run_refresh", fake_run)

    d1, d2 = date(2026, 1, 1), date(2026, 1, 31)
    # start_refresh строит корутину _run_refresh(...) — аргументы фиксируются в ней;
    # fake_create_task её закрывает, поэтому ловим аргументы через саму корутину.
    monkeypatch.setattr(rds, "_run_refresh", lambda p, k, f, t: calls.append((k, f, t)) or _noop())

    await rds.start_refresh(1, "prices", d1, d2)   # не ranged → период сбросить
    await rds.start_refresh(1, "funnel", d1, d2)   # ranged → период сохранить

    assert ("prices", None, None) in calls
    assert ("funnel", d1, d2) in calls
    rds._REFRESH.clear()


async def _noop():
    return None


async def test_progress_records_error(monkeypatch):
    async def boom(project_id, date_from, date_to):
        raise ValueError("WB недоступен")

    rds._REFRESH.clear()
    monkeypatch.setitem(rds.REFRESH_ADAPTERS, "prices", boom)
    await rds._run_refresh(7, "prices", None, None)
    p = rds.get_refresh_progress(7)["prices"]
    assert p["status"] == "error"
    assert "WB недоступен" in p["error"]
    rds._REFRESH.clear()


async def test_progress_is_scoped_by_project():
    rds._REFRESH.clear()
    rds._REFRESH[(1, "prices")] = {"status": "running"}
    rds._REFRESH[(2, "funnel")] = {"status": "ok"}
    assert set(rds.get_refresh_progress(1)) == {"prices"}
    assert set(rds.get_refresh_progress(2)) == {"funnel"}
    rds._REFRESH.clear()
