"""
Тесты формулы FBS-остатка и трансляции остатков в WB (backend/services/wb_fbs).

Покрывают ровно те инварианты, ради которых домен и написан:
  • формула по слагаемым: резерв заявки уменьшает выдачу, брак не отдаётся,
    буфер (% + абсолют), потолок max_qty_per_sku, min_of_both при расхождении
    ledger/зеркала, фолбэк на ledger, когда зеркала нет;
  • открытые FBS-задания вычитаются (иначе продадим то, что уже продано);
  • позиции без Nomenclature.chrt_id не теряются молча (blocked_reason + счётчик);
  • позиции, ушедшие в ноль, ОБЯЗАНЫ уехать на WB;
  • верификация после PUT (WB отвечает 204 даже когда остаток не обновился)
    и обязательная ПЕРЕОТПРАВКА при расхождении;
  • трансляция идёт под распределённым локом (кнопка ‖ джоб);
  • изоляция по project_id.

Потоварная замена количества (`wb_fbs_stock_overrides`), FBO-гейт и режим
склада живут в `tests/test_wb_fbs_overrides.py` — здесь только то, что они
не должны сломать.
"""

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.cost import Nomenclature
from backend.models.fulfillment import FulfillmentRequest, FulfillmentStock
from backend.models.warehouse import Warehouse, WarehouseStock
from backend.models.wb_fbs import (
    FbsPushStatus,
    FbsStockSource,
    FbsWarehouseMode,
    WbFbsOrder,
    WbFbsStockOverride,
    WbFbsStockState,
    WbFbsWarehouse,
    WbFbsWarehouseLink,
)
from backend.services.wb_fbs import locks, stock_service

# ─── Хелперы фикстур домена ──────────────────────────────────────────────────


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _wb_id() -> int:
    """Уникальный id склада продавца (уникальность — в паре с project_id)."""
    return int(uuid.uuid4().int % 10_000_000) + 1


async def _mk_warehouse(db: AsyncSession, project_id: int) -> Warehouse:
    wh = Warehouse(project_id=project_id, name=f"FBS-{_uid()}", warehouse_type="FULFILLMENT")
    db.add(wh)
    await db.flush()
    return wh


async def _mk_nom(
    db: AsyncSession,
    project_id: int,
    *,
    chrt_id: int | None = None,
    brand: str | None = None,
    subject: str | None = None,
    nm_id: int | None = None,
) -> Nomenclature:
    nom = Nomenclature(
        project_id=project_id,
        barcode=f"FBS{_uid()}",
        chrt_id=chrt_id,
        article_seller=f"ART-{_uid()}",
        article_wb=nm_id if nm_id is not None else _wb_id(),
        brand=brand,
        subject=subject,
    )
    db.add(nom)
    await db.flush()
    return nom


async def _mk_stock(
    db: AsyncSession, project_id: int, wh: Warehouse, nom: Nomenclature, qty: int, defect: int = 0
) -> None:
    db.add(
        WarehouseStock(
            project_id=project_id,
            warehouse_id=wh.id,
            nomenclature_id=nom.id,
            barcode=nom.barcode,
            quantity=qty,
            defect_quantity=defect,
        )
    )
    await db.flush()


async def _mk_mirror(
    db: AsyncSession,
    project_id: int,
    wh: Warehouse,
    nom: Nomenclature,
    qty_good: int,
    *,
    units_per_box: int = 1,
    barcode: str | None = None,
    provider: str = "skladbot",
    qty_defect: int = 0,
    qty_reserve: int = 0,
    #: `stock_available` migfull — их собственное «свободно к отгрузке».
    #: По умолчанию = весь остаток: у провайдеров без этого поля оно не читается.
    qty_nominal: int | None = None,
    external_product_id: str | None = None,
) -> None:
    db.add(
        FulfillmentStock(
            project_id=project_id,
            warehouse_id=wh.id,
            provider=provider,
            barcode=barcode or nom.barcode,
            nomenclature_id=nom.id,
            qty_good=qty_good,
            qty_defect=qty_defect,
            qty_reserve=qty_reserve,
            qty_nominal=qty_good if qty_nominal is None else qty_nominal,
            external_product_id=external_product_id,
            units_per_box=units_per_box,
        )
    )
    await db.flush()


async def _mk_fbs_warehouse(db: AsyncSession, project_id: int, **settings) -> WbFbsWarehouse:
    params: dict = {
        "stock_source": FbsStockSource.LEDGER.value,
        "safety_stock_pct": Decimal("0"),
        "safety_stock_abs": 0,
        "max_qty_per_sku": 0,
        "is_active": True,
        "is_processing": False,
        # Дефолт МОДЕЛИ — `observe` (ничего не пишем в чужой кабинет), но эти
        # тесты проверяют саму трансляцию: без `translate` пуш был бы пропущен.
        "mode": FbsWarehouseMode.TRANSLATE.value,
    }
    params.update(settings)
    wh = WbFbsWarehouse(project_id=project_id, wb_warehouse_id=_wb_id(), name="FBS склад", **params)
    db.add(wh)
    await db.flush()
    return wh


async def _mk_link(db: AsyncSession, project_id: int, fbs_wh: WbFbsWarehouse, wh: Warehouse) -> None:
    db.add(
        WbFbsWarehouseLink(
            project_id=project_id,
            wb_warehouse_id=fbs_wh.wb_warehouse_id,
            warehouse_id=wh.id,
            is_active=True,
        )
    )
    await db.flush()


async def _mk_assembly(
    db: AsyncSession,
    project_id: int,
    wh: Warehouse,
    nom: Nomenclature,
    qty: int,
    status: str = AssemblyStatus.PENDING,
) -> None:
    req = AssemblyRequest(
        project_id=project_id,
        warehouse_id=wh.id,
        number=f"ASM-{_uid()}",
        status=status,
        pallets_count=1,
        pallet_weight_kg=Decimal("100.00"),
    )
    db.add(req)
    await db.flush()
    db.add(
        AssemblyRequestItem(
            project_id=project_id,
            assembly_request_id=req.id,
            nomenclature_id=nom.id,
            barcode=nom.barcode,
            quantity=qty,
        )
    )
    await db.flush()


async def _mk_fbs_order(
    db: AsyncSession, project_id: int, fbs_wh: WbFbsWarehouse, nom: Nomenclature, status: str = "new"
) -> None:
    db.add(
        WbFbsOrder(
            project_id=project_id,
            wb_order_id=int(uuid.uuid4().int % 10_000_000_000),
            wb_warehouse_id=fbs_wh.wb_warehouse_id,
            nomenclature_id=nom.id,
            barcode=nom.barcode,
            chrt_id=nom.chrt_id,
            supplier_status=status,
        )
    )
    await db.flush()


def _limit_row(
    chrt_id: int | None,
    qty_available: int,
    *,
    fbs_open: int = 0,
    buffer: int = 0,
    override_qty: int | None = None,
    qty_computed: int | None = None,
) -> dict:
    """Минимальная строка превью под `_apply_chrt_limits` (вычеты уровня chrtId).

    Ключи `barcode` / `nomenclature_id` / `qty_sent` нужны, чтобы ту же строку
    можно было прогнать через `_aggregate_by_chrt` — то есть проверить не превью,
    а ровно то число, которое уедет в WB. `qty_computed` участвует в потолке
    позиции: строка без ручного количества ничем не ограничена и вносит в него
    свой рассчитанный остаток.
    """
    return {
        "chrt_id": chrt_id,
        "barcode": str(chrt_id or ""),
        "nomenclature_id": None,
        "qty_available": qty_available,
        "qty_computed": qty_available if qty_computed is None else qty_computed,
        "qty_sent": None,
        "fbs_open": fbs_open,
        "buffer": buffer,
        "override_qty": override_qty,
    }


async def _row_for(db: AsyncSession, project_id: int, fbs_wh: WbFbsWarehouse, nom: Nomenclature) -> dict:
    rows = await stock_service.compute_fbs_stock(db, project_id, fbs_wh.wb_warehouse_id)
    match = [r for r in rows if r["nomenclature_id"] == nom.id]
    assert match, f"строка номенклатуры {nom.id} потеряна в расчёте"
    return match[0]


class _FakeClient:
    """Заглушка WbFbsClient: помнит PUT'ы и умеет врать в верификации."""

    def __init__(self, *, confirm: dict[int, int] | None = None, put_error: Exception | None = None) -> None:
        self.puts: list[list[tuple[int, int]]] = []
        self.gets: list[list[int]] = []
        self.stocks: dict[int, int] = {}
        self._confirm = confirm
        self._put_error = put_error

    async def put_stocks(self, wb_warehouse_id: int, items: list[tuple[int, int]]) -> None:
        if self._put_error is not None:
            raise self._put_error
        self.puts.append(list(items))
        for chrt, amount in items:
            self.stocks[chrt] = amount

    async def get_stocks(self, wb_warehouse_id: int, chrt_ids: list[int]) -> dict[int, int]:
        self.gets.append(list(chrt_ids))
        if self._confirm is not None:
            return {c: self._confirm.get(c, self.stocks.get(c, 0)) for c in chrt_ids}
        return {c: self.stocks.get(c, 0) for c in chrt_ids}

    @property
    def sent_map(self) -> dict[int, int]:
        return {chrt: amount for chunk in self.puts for chrt, amount in chunk}


class _FakeRedis:
    """Минимальный Redis под лок трансляции: `SET NX EX` + `EXISTS` + `EVAL`."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def eval(self, script, numkeys, key, arg):
        if self.store.get(key) == arg:
            del self.store[key]
            return 1
        return 0


@pytest.fixture
def fake_client(monkeypatch):
    """Подменяет фабрику клиента — в тестах в WB не ходим."""
    client = _FakeClient()

    async def _factory(db, project_id):
        return client

    monkeypatch.setattr(stock_service, "_get_client", _factory)
    return client


@pytest_asyncio.fixture
async def fbs_env(db_session: AsyncSession, project):
    """Минимальный стенд: наш склад + склад продавца + привязка."""
    wh = await _mk_warehouse(db_session, project.id)
    fbs_wh = await _mk_fbs_warehouse(db_session, project.id)
    await _mk_link(db_session, project.id, fbs_wh, wh)
    await db_session.commit()
    return wh, fbs_wh


# ═════════════════════════════════════════════════════════════════════════════
# Чистые хелперы формулы
# ═════════════════════════════════════════════════════════════════════════════


class TestFormulaHelpers:
    def test_source_ledger(self):
        assert stock_service._source_qty("ledger", 10, 3) == 10

    def test_source_mirror(self):
        assert stock_service._source_qty("ff_mirror", 10, 3) == 3

    def test_source_mirror_falls_back_to_ledger(self):
        """Склад без зеркала (нет интеграции) не должен обнуляться."""
        assert stock_service._source_qty("ff_mirror", 10, None) == 10

    def test_source_min_of_both(self):
        assert stock_service._source_qty("min_of_both", 10, 3) == 3
        assert stock_service._source_qty("min_of_both", 3, 10) == 3
        assert stock_service._source_qty("min_of_both", 7, None) == 7

    def test_reserve_not_subtracted_twice_when_wms_already_picked(self):
        """Прод-кейс wms Домодедово 27.07.2026 (chashka_vasilki).

        Наш учёт 252, три заявки READY на 108, зеркало Целиком 144 = 252 − 108:
        WMS уже отобрал товар под сборку. Старая формула брала min(252, 144) = 144
        и снимала 108 ВТОРОЙ раз → 36 вместо 144, товар зря снимался с продажи.
        """
        assert stock_service._free_sides(252, 144, 108) == (144, 144)

    def test_reserve_subtracted_from_mirror_when_provider_has_not_picked(self):
        """Провайдер ещё не отобрал (зеркало == наш учёт) — резерв обязан уйти с обеих."""
        assert stock_service._free_sides(339, 339, 138) == (201, 201)

    def test_reserve_partially_taken_by_provider(self):
        """Провайдер забрал часть резерва — с зеркала снимаем только остаток."""
        # ниже наших книг на 40 из 100 резерва → с зеркала снять ещё 60
        assert stock_service._free_sides(200, 160, 100) == (100, 100)

    def test_reserve_over_stock_clamps_to_zero(self):
        """Обещали больше, чем есть — ноль, а не отрицательный остаток."""
        assert stock_service._free_sides(30, 22, 36) == (0, 0)

    def test_free_sides_without_mirror(self):
        """Склад без интеграции: зеркала нет, резерв снимается только с нашего учёта."""
        assert stock_service._free_sides(100, None, 30) == (70, None)

    def test_computed_absorbs_position_level_deductions(self):
        """«Можем отдать» обязано учитывать проданное по FBS и абсолютный буфер.

        Раньше `qty_computed` фиксировался ДО вычетов уровня позиции, и экран
        показывал «Остаток 100 · Можем отдать 100», хотя уезжало 95 — колонки
        между собой не сходились.
        """
        rows = [
            {"chrt_id": 7, "qty_available": 100, "qty_computed": 100, "fbs_open": 3,
             "buffer": 0, "override_qty": None},
        ]
        stock_service._apply_chrt_limits(rows, {7: 3}, abs_buffer=2, max_qty_per_sku=0)
        assert rows[0]["qty_computed"] == 95
        assert rows[0]["qty_available"] == 95

    def test_buffer_pct_rounds_up(self):
        # 5% от 11 = 0.55 → 1 (вверх). Абсолют сюда НЕ входит: он вычитается
        # один раз на позицию, после суммирования по складам.
        assert stock_service._buffer_pct(11, Decimal("5")) == 1

    def test_buffer_zero(self):
        assert stock_service._buffer_pct(100, Decimal("0")) == 0

    def test_aggregate_by_chrt_sums_shared_chrt(self):
        """Один chrtId у нескольких баркодов — схлопываем, иначе дубликат ключа в PUT."""
        rows = [
            {"chrt_id": 5, "barcode": "A", "nomenclature_id": 1, "qty_available": 3, "qty_sent": None},
            {"chrt_id": 5, "barcode": "B", "nomenclature_id": 2, "qty_available": 4, "qty_sent": None},
            {"chrt_id": None, "barcode": "C", "nomenclature_id": 3, "qty_available": 9, "qty_sent": None},
        ]
        agg = stock_service._aggregate_by_chrt(rows)
        assert len(agg) == 1
        assert agg[0]["chrt_id"] == 5
        assert agg[0]["amount"] == 7

    def test_aggregate_applies_sku_cap_after_sum(self):
        """Потолок — на chrtId, не на баркод: 3+4 при потолке 5 = 5, а не 7.

        Кап, применённый до схлопывания, дал бы min(3,5)+min(4,5) = 7 —
        суммарный остаток пробил бы потолок ровно в том случае, ради которого
        потолок и ставят (много баркодов на одном chrtId).
        """
        rows = [
            {"chrt_id": 5, "barcode": "A", "nomenclature_id": 1, "qty_available": 3, "qty_sent": None},
            {"chrt_id": 5, "barcode": "B", "nomenclature_id": 2, "qty_available": 4, "qty_sent": None},
        ]
        agg = stock_service._aggregate_by_chrt(rows, max_qty_per_sku=5)
        assert [i["amount"] for i in agg] == [5]

    def test_aggregate_without_cap_is_unchanged(self):
        """max_qty_per_sku = 0 — потолка нет (дефолт склада)."""
        rows = [{"chrt_id": 9, "barcode": "A", "nomenclature_id": 1, "qty_available": 40, "qty_sent": None}]
        assert stock_service._aggregate_by_chrt(rows, max_qty_per_sku=0)[0]["amount"] == 40

    def test_sku_cap_trims_group_in_preview(self):
        """Превью показывает то же, что уедет: группа chrtId режется до потолка."""
        rows = [
            _limit_row(7, 6),
            _limit_row(7, 6),
            _limit_row(8, 2),
        ]
        stock_service._apply_chrt_limits(rows, {}, abs_buffer=0, max_qty_per_sku=5)
        assert [r["qty_available"] for r in rows] == [5, 0, 2]

    def test_sku_cap_noop_without_limit(self):
        rows = [_limit_row(7, 6)]
        stock_service._apply_chrt_limits(rows, {}, abs_buffer=0, max_qty_per_sku=0)
        assert rows[0]["qty_available"] == 6

    def test_open_orders_subtracted_after_collapse(self):
        """Открытые задания — на chrtId целиком, а не построчно с клампом.

        Регресс: все 12 заданий висят на строке с нулевым остатком (резолв
        `_resolve_nomenclature` вешает их на МЕНЬШИЙ nomenclature_id). Раньше
        строка давала max(0, 0−12) = 0, спрос молча съедался клампом, и остаток
        соседнего баркода того же chrtId уезжал в WB целиком — проданное
        выставлялось к продаже второй раз.
        """
        rows = [_limit_row(7001, 0, fbs_open=12), _limit_row(7001, 40)]
        stock_service._apply_chrt_limits(rows, {7001: 12}, abs_buffer=0, max_qty_per_sku=0)
        assert sum(r["qty_available"] for r in rows) == 28

    def test_abs_buffer_is_subtracted_once_per_chrt(self):
        """Абсолютный буфер — на позицию (chrtId), а не на каждый баркод."""
        rows = [_limit_row(7001, 50), _limit_row(7001, 50)]
        stock_service._apply_chrt_limits(rows, {}, abs_buffer=10, max_qty_per_sku=0)
        assert sum(r["qty_available"] for r in rows) == 90
        assert sum(r["buffer"] for r in rows) == 10, "расшифровка «Буфер» обязана совпадать с вычетом"

    def test_chrt_limits_never_go_negative(self):
        rows = [_limit_row(7001, 3)]
        stock_service._apply_chrt_limits(rows, {7001: 10}, abs_buffer=5, max_qty_per_sku=0)
        assert rows[0]["qty_available"] == 0

    def test_no_chrt_row_keeps_per_row_deductions(self):
        """Строка без chrtId в WB не уедет, но в превью цифра обязана быть честной."""
        rows = [_limit_row(None, 30, fbs_open=4)]
        stock_service._apply_chrt_limits(rows, {}, abs_buffer=6, max_qty_per_sku=0)
        assert rows[0]["qty_available"] == 20

    def test_split_open_by_stock_is_proportional(self):
        """Обязательство склада WB разносится по пулу пропорционально остатку."""
        assert stock_service._split_open_by_stock(10, {1: 10, 2: 10}) == {1: 5, 2: 5}
        assert stock_service._split_open_by_stock(10, {1: 2, 2: 8}) == {1: 2, 2: 8}
        assert stock_service._split_open_by_stock(8, {1: 8, 2: 0}) == {1: 8, 2: 0}

    def test_split_open_by_stock_keeps_total(self):
        """Σ долей == общее обязательство (иначе гейт теряет или множит штуки)."""
        for total, weights in ((7, {1: 1, 2: 1, 3: 1}), (5, {1: 0, 2: 0}), (1, {1: 3, 2: 4})):
            assert sum(stock_service._split_open_by_stock(total, weights).values()) == total

    def test_split_open_single_warehouse_is_whole(self):
        """Одна привязка — вся цифра ей: прежнее поведение бит-в-бит."""
        assert stock_service._split_open_by_stock(9, {1: 0}) == {1: 9}

    def test_select_delta_skips_unchanged(self):
        items = [{"chrt_id": 1, "amount": 5, "prev": 5}, {"chrt_id": 2, "amount": 4, "prev": 3}]
        assert [i["chrt_id"] for i in stock_service._select_delta(items, force=False)] == [2]

    def test_select_delta_force_resends_known(self):
        items = [{"chrt_id": 1, "amount": 5, "prev": 5}]
        assert len(stock_service._select_delta(items, force=True)) == 1

    def test_select_delta_never_sends_first_zero(self):
        """Позицию, которую никогда не отправляли и которая 0, слать бессмысленно."""
        items = [{"chrt_id": 1, "amount": 0, "prev": None}]
        assert stock_service._select_delta(items, force=True) == []

    def test_select_delta_sends_zeroed_position(self):
        """А вот ушедшую в ноль — обязательно (иначе WB продолжит продавать)."""
        items = [{"chrt_id": 1, "amount": 0, "prev": 7}]
        assert len(stock_service._select_delta(items, force=False)) == 1

    def test_apply_override_without_value_is_identity(self):
        """Ручного количества нет (или поле очистили) — расчёт не трогаем."""
        assert stock_service._apply_override(17, None) == 17

    def test_apply_override_is_a_ceiling_only(self):
        """«Фиксированное, но не больше свободных остатков»: только режет."""
        assert stock_service._apply_override(40, 10) == 10
        assert stock_service._apply_override(3, 100) == 3

    def test_apply_override_zero_is_zero(self):
        assert stock_service._apply_override(99, 0) == 0

    def test_override_cap_binds_the_whole_chrt_group(self):
        """Потолок — на ПОЗИЦИЮ WB, а не на строку.

        Регресс: потолок применялся только построчно, а в WB уезжала сумма
        группы — два баркода одного chrtId под потолком 10 давали WB 20
        (N×потолок), и «не больше 10» не соблюдалось.
        """
        rows = [
            _limit_row(90100, 10, override_qty=10, qty_computed=50),
            _limit_row(90100, 10, override_qty=10, qty_computed=50),
        ]
        stock_service._apply_chrt_limits(rows, {}, abs_buffer=0, max_qty_per_sku=0)
        assert sum(r["qty_available"] for r in rows) == 20, "Σ потолков товаров = 20"
        assert stock_service._aggregate_by_chrt(rows)[0]["amount"] == 20

    def test_override_group_limit_is_the_sum_of_ceilings(self):
        """Ручное количество задано ПО ТОВАРУ → потолок позиции = сумма потолков."""
        rows = [
            _limit_row(90150, 40, override_qty=10, qty_computed=40),
            _limit_row(90150, 40, override_qty=5, qty_computed=40),
        ]
        assert stock_service._group_override_limit(rows) == 15
        stock_service._apply_chrt_limits(rows, {}, abs_buffer=0, max_qty_per_sku=0)
        assert sum(r["qty_available"] for r in rows) == 15

    def test_override_zero_on_all_rows_zeroes_the_position(self):
        """«Не отдавать» на всех строках позиции даёт WB честный ноль."""
        rows = [
            _limit_row(90300, 0, override_qty=0, qty_computed=12),
            _limit_row(90300, 0, override_qty=0, qty_computed=7),
        ]
        stock_service._apply_chrt_limits(rows, {}, abs_buffer=0, max_qty_per_sku=0)
        assert [r["qty_available"] for r in rows] == [0, 0]
        assert stock_service._aggregate_by_chrt(rows)[0]["amount"] == 0

    def test_row_without_override_keeps_its_own_stock(self):
        """Соседний баркод без ручного количества ничем не ограничен."""
        rows = [_limit_row(90200, 10, override_qty=10, qty_computed=50), _limit_row(90200, 50)]
        assert stock_service._group_override_limit(rows) == 60
        stock_service._apply_chrt_limits(rows, {}, abs_buffer=0, max_qty_per_sku=0)
        assert sum(r["qty_available"] for r in rows) == 60

    def test_group_without_overrides_has_no_limit(self):
        """Ни одного ручного количества — потолка позиции нет вовсе."""
        assert stock_service._group_override_limit([_limit_row(90600, 10), _limit_row(90600, 5)]) is None

    def test_override_does_not_touch_rows_without_chrt(self):
        """Строка без chrtId в WB не уезжает — её потолок остаётся построчным."""
        rows = [_limit_row(None, 10, override_qty=10), _limit_row(None, 10, override_qty=10)]
        stock_service._apply_chrt_limits(rows, {}, abs_buffer=0, max_qty_per_sku=0)
        assert [r["qty_available"] for r in rows] == [10, 10]


# ═════════════════════════════════════════════════════════════════════════════
# Брак: зеркало ФФ отдаётся ГОДНЫМ
# ═════════════════════════════════════════════════════════════════════════════


class TestMirrorNetOfDefect:
    """В WB уходит обещание отгрузить — значит зеркало ФФ обязано быть тем, что
    реально можно взять и отправить.

    Из него вычитается всё недоступное (собранное под чужую отгрузку, идущее
    в приёмке), КРОМЕ собранного под наши активные заявки: его убирает `reserved`,
    и вычесть здесь значило бы вычесть дважды.

    Отдельно закреплено, что недоступное ≠ брак: брак skladbot из зеркала НЕ
    вычитается (`qty_good` его уже не содержит), а колонка «Брак» показывает
    измеренную провайдером цифру (см. `test_migfull_locked_is_not_defect_in_column`).
    """

    def test_unavailable_is_subtracted_per_warehouse(self):
        mirror = {5: {1: 100, 2: 40}}
        blocked = {1: {5: 30}}
        assert stock_service._net_mirror(mirror, blocked) == {5: {1: 70, 2: 40}}

    def test_never_pushes_mirror_below_zero(self):
        """Блокировка больше остатка (дрейф синков) — ноль, а не отрицательный остаток."""
        assert stock_service._net_mirror({5: {1: 10}}, {1: {5: 25}}) == {5: {1: 0}}

    def test_empty_map_is_identity(self):
        """Склад без интеграции: вычитать нечего — зеркало не трогаем."""
        mirror = {5: {1: 100}}
        assert stock_service._net_mirror(mirror, {}) == mirror

    def test_row_defect_prefers_provider_where_measured(self):
        """Наш `defect_quantity` и брак ФФ — один и тот же товар: складывать нельзя."""
        assert stock_service._row_defect(5, {5: {1: 70}}, {1: {5: 30}}, {5: 12}) == 30

    def test_row_defect_falls_back_to_ledger_when_provider_is_silent(self):
        """Провайдер брак не считает (migfull/wmscelicom) — показываем свой."""
        assert stock_service._row_defect(5, {5: {1: 70}}, {}, {5: 12}) == 12
        assert stock_service._row_defect(5, {}, {}, {5: 12}) == 12

    @pytest.mark.asyncio
    async def test_skladbot_defect_does_not_cut_mirror(self, db_session, project, fbs_env):
        """Брак skladbot лежит ОТДЕЛЬНО от годного — из зеркала его не вычитаем.

        `amount` (→`qty_good`) и `repair_amount` (→`qty_defect`) дизъюнктны; вычет
        был двойным и резал живой остаток. Показываем брак колонкой, остаток не
        трогаем.
        """
        wh, fbs_wh = fbs_env
        fbs_wh.stock_source = FbsStockSource.FF_MIRROR.value
        nom = await _mk_nom(db_session, project.id, chrt_id=901)
        await _mk_stock(db_session, project.id, wh, nom, 0)
        await _mk_mirror(db_session, project.id, wh, nom, 100, qty_defect=30)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["qty_ff_mirror"] == 100
        assert row["qty_source"] == 100
        assert row["defect"] == 30

    @pytest.mark.asyncio
    async def test_skladbot_defect_over_good_does_not_zero_position(self, db_session, project, fbs_env):
        """Прод-кейс 160х230_вишня (склад Газпром 28.07.2026): годного 8, брака 14.

        Брак БОЛЬШЕ годного — прямое доказательство, что корзины дизъюнктны. Со
        старым вычетом зеркало схлопывалось в 0, `min_of_both` давал 0, и позиция
        уезжала к WB нулём при живых 8 у ФФ и 5 в наших книгах.
        """
        wh, fbs_wh = fbs_env
        fbs_wh.stock_source = FbsStockSource.MIN_OF_BOTH.value
        nom = await _mk_nom(db_session, project.id, chrt_id=902)
        await _mk_stock(db_session, project.id, wh, nom, 5)
        await _mk_mirror(db_session, project.id, wh, nom, 8, qty_defect=14)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["qty_ff_mirror"] == 8
        assert row["qty_source"] == 5  # min(наш учёт 5, зеркало 8)
        assert row["qty_available"] == 5
        assert row["defect"] == 14

    @pytest.mark.asyncio
    async def test_migfull_uses_provider_own_available(self, db_session, project, fbs_env):
        """migfull: берём ИХ `stock_available`, а не реконструкцию блокировки.

        `qty_good` у них — `stock_actual`, весь физический остаток; свободное они
        отдают отдельным полем. Прод-кейс 120х170_серыйоднотон 27.07.2026:
        actual 478 / locked 466 / available 12 — Натали выставила в WB 11, а наша
        реконструкция («минус собранное под наши отгрузки») оставляла 443, потому
        что нашей заявки на сборку не существовало и вычитать было нечем.

        Поля брака у migfull в API нет вовсе, поэтому в колонке «Брак» обязан
        остаться наш `defect_quantity` — единственная честная цифра брака.
        """
        wh, fbs_wh = fbs_env
        fbs_wh.stock_source = FbsStockSource.FF_MIRROR.value
        nom = await _mk_nom(db_session, project.id, chrt_id=902)
        await _mk_stock(db_session, project.id, wh, nom, 0, defect=7)
        await _mk_mirror(
            db_session, project.id, wh, nom, 478,
            provider="migfull", qty_reserve=466, qty_nominal=12,
        )
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["qty_ff_mirror"] == 12
        assert row["qty_source"] == 12
        assert row["defect"] == 7  # НЕ 466: заблокированное браком не является

    @pytest.mark.asyncio
    async def test_migfull_own_assembly_is_not_subtracted_twice(
        self, db_session, project, fbs_env,
    ):
        """Наша заявка на сборку не должна вычитаться поверх блокировки провайдера.

        Провайдер уже снял товар под неё со своего свободного остатка. Если снять
        его ещё и нашим `reserved`, позиция ложно уходит в ноль и снимается с
        продажи — это удерживает `_free_sides` (зеркало ниже наших книг ровно на
        то, что провайдер уже забрал).
        """
        wh, fbs_wh = fbs_env
        fbs_wh.stock_source = FbsStockSource.MIN_OF_BOTH.value
        nom = await _mk_nom(db_session, project.id, chrt_id=904)
        await _mk_stock(db_session, project.id, wh, nom, 100)
        # Натали залочила 40 под нашу сборку: свободно у них 60
        await _mk_mirror(
            db_session, project.id, wh, nom, 100,
            provider="migfull", qty_reserve=40, qty_nominal=60,
        )
        await _mk_assembly(db_session, project.id, wh, nom, 40, AssemblyStatus.READY)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["reserved_assembly"] == 40
        assert row["qty_source"] == 60  # НЕ 20

    @pytest.mark.asyncio
    async def test_ff_mirror_source_ignores_stale_ledger(self, db_session, project, fbs_env):
        """Источник «Система ФФ»: наш отставший учёт не должен удерживать выдачу.

        Прод-кейс wms Домодедово 27.07.2026: товар физически у ФФ (640), а в наших
        книгах по этому складу ноль — 48 позиций и 18 840 штук, которые `min_of_both`
        не отдавал вовсе. Владелец: «нужно брать за основу остатки по системе их WMS».
        """
        wh, fbs_wh = fbs_env
        fbs_wh.stock_source = FbsStockSource.FF_MIRROR.value
        nom = await _mk_nom(db_session, project.id, chrt_id=905)
        await _mk_stock(db_session, project.id, wh, nom, 0)
        await _mk_mirror(db_session, project.id, wh, nom, 640)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["qty_ledger"] == 0
        assert row["qty_source"] == 640
        assert row["qty_computed"] == 640

    @pytest.mark.asyncio
    async def test_ff_mirror_source_still_holds_back_assembly(self, db_session, project, fbs_env):
        """Но обязательства остаются: собранное под заявку не продаём и на «Системе ФФ»."""
        wh, fbs_wh = fbs_env
        fbs_wh.stock_source = FbsStockSource.FF_MIRROR.value
        nom = await _mk_nom(db_session, project.id, chrt_id=906)
        await _mk_stock(db_session, project.id, wh, nom, 100)
        await _mk_mirror(db_session, project.id, wh, nom, 100)  # провайдер ещё не отобрал
        await _mk_assembly(db_session, project.id, wh, nom, 40, AssemblyStatus.READY)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["reserved_assembly"] == 40
        assert row["qty_source"] == 60

    @pytest.mark.asyncio
    async def test_boxes_are_not_sellable_stock(self, db_session, project, fbs_env):
        """Короб — НЕ остаток: продать штуку из невскрытого короба нельзя.

        Маркетплейс покупает штуку, а провайдер отдаёт короб; чтобы остаток «встал»
        в FBS, короб надо вскрыть и принять поштучно. Пока `qty_good × units_per_box`
        падало прямо в зеркало, экран обещал вчетверо больше отгружаемого (натали
        27.07.2026: 29 776 против 7 041). Содержимое коробов уходит отдельным полем —
        это рабочий список «что вскрыть», а не доступный товар.
        """
        wh, fbs_wh = fbs_env
        fbs_wh.stock_source = FbsStockSource.FF_MIRROR.value
        nom = await _mk_nom(db_session, project.id, chrt_id=903)
        await _mk_stock(db_session, project.id, wh, nom, 0)
        db_session.add(
            FulfillmentStock(
                project_id=project.id,
                warehouse_id=wh.id,
                provider="migfull",
                barcode=f"1{nom.barcode}",
                base_barcode=nom.barcode,
                nomenclature_id=nom.id,
                qty_good=10,
                units_per_box=18,
            )
        )
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["qty_source"] == 0          # отдавать нечего
        assert row["qty_ff_boxed"] == 10 * 18  # но 180 шт можно достать
        assert row["ff_box_count"] == 10

    @pytest.mark.asyncio
    async def test_box_only_position_does_not_fall_back_to_ledger(
        self, db_session, project, fbs_env,
    ):
        """Позиция ЦЕЛИКОМ в коробах — это «россыпью ноль», а не «зеркала нет».

        `_source_qty` трактует отсутствие зеркала как «склад без интеграции» и берёт
        наш учёт. Если короб-строки просто отфильтровать, такая позиция теряет ключ
        в зеркале и уезжает в WB по нашим книгам — ровно мимо короб-правила.
        """
        wh, fbs_wh = fbs_env
        fbs_wh.stock_source = FbsStockSource.MIN_OF_BOTH.value
        nom = await _mk_nom(db_session, project.id, chrt_id=908)
        await _mk_stock(db_session, project.id, wh, nom, 500)  # наши книги полны
        db_session.add(
            FulfillmentStock(
                project_id=project.id,
                warehouse_id=wh.id,
                provider="migfull",
                barcode=f"1{nom.barcode}",
                base_barcode=nom.barcode,
                nomenclature_id=nom.id,
                qty_good=20,
                units_per_box=25,
            )
        )
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["qty_ff_mirror"] == 0    # провайдер позицию знает, россыпью — ноль
        assert row["qty_source"] == 0       # НЕ 500
        assert row["qty_ff_boxed"] == 500

    @pytest.mark.asyncio
    async def test_loose_and_boxes_live_side_by_side(self, db_session, project, fbs_env):
        """Есть и россыпь, и короба: продаём россыпь, короба показываем к вскрытию."""
        wh, fbs_wh = fbs_env
        fbs_wh.stock_source = FbsStockSource.FF_MIRROR.value
        nom = await _mk_nom(db_session, project.id, chrt_id=907)
        await _mk_stock(db_session, project.id, wh, nom, 0)
        await _mk_mirror(db_session, project.id, wh, nom, 24, provider="migfull")
        db_session.add(
            FulfillmentStock(
                project_id=project.id,
                warehouse_id=wh.id,
                provider="migfull",
                barcode=f"1{nom.barcode}",
                base_barcode=nom.barcode,
                nomenclature_id=nom.id,
                qty_good=5,
                units_per_box=20,
            )
        )
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["qty_source"] == 24
        assert row["qty_ff_boxed"] == 100
        assert row["ff_box_count"] == 5


# ═════════════════════════════════════════════════════════════════════════════
# Формула на живых данных
# ═════════════════════════════════════════════════════════════════════════════


class TestStockFormula:
    @pytest.mark.asyncio
    async def test_plain_ledger(self, db_session, project, fbs_env):
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=111)
        await _mk_stock(db_session, project.id, wh, nom, 12)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["qty_ledger"] == 12
        assert row["qty_source"] == 12
        assert row["qty_available"] == 12
        assert row["chrt_id"] == 111
        assert row["blocked_reason"] is None

    @pytest.mark.asyncio
    async def test_defect_is_not_offered(self, db_session, project, fbs_env):
        """Брак лежит в defect_quantity и в остаток WB не входит."""
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=112)
        await _mk_stock(db_session, project.id, wh, nom, 10, defect=6)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["defect"] == 6
        assert row["qty_available"] == 10

    @pytest.mark.asyncio
    async def test_assembly_reserve_reduces_output(self, db_session, project, fbs_env):
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=113)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        await _mk_assembly(db_session, project.id, wh, nom, 4)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["reserved_assembly"] == 4
        assert row["qty_available"] == 6

    @pytest.mark.asyncio
    async def test_pre_distributed_is_not_a_reserve(self, db_session, project, fbs_env):
        """PRE_DISTRIBUTED — товар машины в пути, реального стока он не держит."""
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=114)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        await _mk_assembly(db_session, project.id, wh, nom, 4, status=AssemblyStatus.PRE_DISTRIBUTED)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["reserved_assembly"] == 0
        assert row["qty_available"] == 10

    @pytest.mark.asyncio
    async def test_buffer_pct_and_abs(self, db_session, project):
        wh = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id, safety_stock_pct=Decimal("10"), safety_stock_abs=5)
        await _mk_link(db_session, project.id, fbs_wh, wh)
        nom = await _mk_nom(db_session, project.id, chrt_id=115)
        await _mk_stock(db_session, project.id, wh, nom, 100)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["buffer"] == 15
        assert row["qty_available"] == 85

    @pytest.mark.asyncio
    async def test_safety_abs_is_subtracted_once_across_warehouses(self, db_session, project):
        """Абсолютный буфер — N штук на позицию, а не N на каждую привязку.

        Два наших склада по 10 шт при буфере 5: отдать нужно 20 − 5 = 15.
        Прежний per-склад вычет давал 10 (5 съедалось дважды), и с ростом числа
        привязок «держим 5 штук про запас» превращалось в «держим 5×N».
        """
        wh1 = await _mk_warehouse(db_session, project.id)
        wh2 = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id, safety_stock_abs=5)
        await _mk_link(db_session, project.id, fbs_wh, wh1)
        await _mk_link(db_session, project.id, fbs_wh, wh2)
        nom = await _mk_nom(db_session, project.id, chrt_id=1151)
        await _mk_stock(db_session, project.id, wh1, nom, 10)
        await _mk_stock(db_session, project.id, wh2, nom, 10)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["qty_source"] == 20
        assert row["buffer"] == 5
        assert row["qty_available"] == 15

    @pytest.mark.asyncio
    async def test_safety_pct_stays_per_warehouse(self, db_session, project):
        """Процент — величина относительная: считается по каждому складу от его остатка.

        10% от 11 и от 5 = 2 + 1 = 3 (округление вверх на каждом складе),
        а не 10% от суммы 16 = 2: буфер обязан быть консервативнее.
        """
        wh1 = await _mk_warehouse(db_session, project.id)
        wh2 = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id, safety_stock_pct=Decimal("10"))
        await _mk_link(db_session, project.id, fbs_wh, wh1)
        await _mk_link(db_session, project.id, fbs_wh, wh2)
        nom = await _mk_nom(db_session, project.id, chrt_id=1152)
        await _mk_stock(db_session, project.id, wh1, nom, 11)
        await _mk_stock(db_session, project.id, wh2, nom, 5)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["buffer"] == 3
        assert row["qty_available"] == 13

    @pytest.mark.asyncio
    async def test_default_buffers_change_nothing(self, db_session, project):
        """Дефолт 0/0 на двух складах — регрессии нет, отдаём всё."""
        wh1 = await _mk_warehouse(db_session, project.id)
        wh2 = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id)
        await _mk_link(db_session, project.id, fbs_wh, wh1)
        await _mk_link(db_session, project.id, fbs_wh, wh2)
        nom = await _mk_nom(db_session, project.id, chrt_id=1153)
        await _mk_stock(db_session, project.id, wh1, nom, 7)
        await _mk_stock(db_session, project.id, wh2, nom, 3)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["buffer"] == 0
        assert row["qty_available"] == 10

    @pytest.mark.asyncio
    async def test_abs_buffer_cannot_push_available_below_zero(self, db_session, project):
        """Буфер больше остатка — отдаём 0, а не отрицательное число."""
        wh = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id, safety_stock_abs=50)
        await _mk_link(db_session, project.id, fbs_wh, wh)
        nom = await _mk_nom(db_session, project.id, chrt_id=1154)
        await _mk_stock(db_session, project.id, wh, nom, 4)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["qty_available"] == 0

    @pytest.mark.asyncio
    async def test_max_qty_per_sku_caps(self, db_session, project):
        wh = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id, max_qty_per_sku=3)
        await _mk_link(db_session, project.id, fbs_wh, wh)
        nom = await _mk_nom(db_session, project.id, chrt_id=116)
        await _mk_stock(db_session, project.id, wh, nom, 40)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["qty_available"] == 3

    @pytest.mark.asyncio
    async def test_max_qty_per_sku_caps_shared_chrt_after_aggregation(self, db_session, project, fake_client):
        """Два баркода на одном chrtId: потолок держит СУММУ, а не каждую строку.

        Раньше кап применялся per-номенклатура, до схлопывания по chrtId, и в WB
        уезжало 2× потолка — то есть ограничение «максимум N штук на SKU» просто
        не работало на самом частом случае (размерный ряд с общим chrtId).
        """
        wh = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id, max_qty_per_sku=5)
        await _mk_link(db_session, project.id, fbs_wh, wh)
        nom_a = await _mk_nom(db_session, project.id, chrt_id=1160)
        nom_b = await _mk_nom(db_session, project.id, chrt_id=1160)
        await _mk_stock(db_session, project.id, wh, nom_a, 40)
        await _mk_stock(db_session, project.id, wh, nom_b, 40)
        await db_session.commit()

        rows = await stock_service.compute_fbs_stock(db_session, project.id, fbs_wh.wb_warehouse_id)
        shared = [r for r in rows if r["chrt_id"] == 1160]
        assert len(shared) == 2, "строки не должны схлопываться в превью"
        assert sum(r["qty_available"] for r in shared) == 5

        with patch("backend.cache.get_redis", AsyncMock(return_value=None)):
            await stock_service.push_stocks(db_session, project.id, wb_warehouse_ids=[fbs_wh.wb_warehouse_id])
        assert fake_client.sent_map == {1160: 5}

    @pytest.mark.asyncio
    async def test_min_of_both_takes_lower(self, db_session, project):
        """Расхождение ledger/зеркала при min_of_both решается в пользу меньшего."""
        wh = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id, stock_source=FbsStockSource.MIN_OF_BOTH.value)
        await _mk_link(db_session, project.id, fbs_wh, wh)
        nom = await _mk_nom(db_session, project.id, chrt_id=117)
        await _mk_stock(db_session, project.id, wh, nom, 20)
        await _mk_mirror(db_session, project.id, wh, nom, 7)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["qty_ledger"] == 20
        assert row["qty_ff_mirror"] == 7
        assert row["qty_available"] == 7

    @pytest.mark.asyncio
    async def test_min_of_both_without_mirror_uses_ledger(self, db_session, project):
        """Склад без зеркала (нет интеграции ФФ) не должен обнуляться."""
        wh = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id, stock_source=FbsStockSource.MIN_OF_BOTH.value)
        await _mk_link(db_session, project.id, fbs_wh, wh)
        nom = await _mk_nom(db_session, project.id, chrt_id=118)
        await _mk_stock(db_session, project.id, wh, nom, 9)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["qty_ff_mirror"] is None
        assert row["qty_available"] == 9

    @pytest.mark.asyncio
    async def test_mirror_box_rows_converted_to_units(self, db_session, project):
        """Короб ФФ приводится к россыпи: qty_good × units_per_box."""
        wh = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id, stock_source=FbsStockSource.FF_MIRROR.value)
        await _mk_link(db_session, project.id, fbs_wh, wh)
        nom = await _mk_nom(db_session, project.id, chrt_id=119)
        await _mk_stock(db_session, project.id, wh, nom, 0)
        await _mk_mirror(db_session, project.id, wh, nom, 3, units_per_box=12, barcode=f"BOX{_uid()}")
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["qty_ff_mirror"] == 36
        assert row["qty_available"] == 36

    @pytest.mark.asyncio
    async def test_open_fbs_orders_subtracted(self, db_session, project, fbs_env):
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=120)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        await _mk_fbs_order(db_session, project.id, fbs_wh, nom, status="new")
        await _mk_fbs_order(db_session, project.id, fbs_wh, nom, status="confirm")
        await _mk_fbs_order(db_session, project.id, fbs_wh, nom, status="cancel")
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["fbs_open"] == 2  # cancel не держит остаток
        assert row["qty_available"] == 8

    @pytest.mark.asyncio
    async def test_two_linked_warehouses_sum_up(self, db_session, project):
        wh1 = await _mk_warehouse(db_session, project.id)
        wh2 = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id)
        await _mk_link(db_session, project.id, fbs_wh, wh1)
        await _mk_link(db_session, project.id, fbs_wh, wh2)
        nom = await _mk_nom(db_session, project.id, chrt_id=121)
        await _mk_stock(db_session, project.id, wh1, nom, 4)
        await _mk_stock(db_session, project.id, wh2, nom, 6)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["qty_available"] == 10

    @pytest.mark.asyncio
    async def test_no_chrt_position_is_not_lost(self, db_session, project, fbs_env):
        """Без chrt_id транслировать нечем — но и молчать нельзя."""
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=None)
        await _mk_stock(db_session, project.id, wh, nom, 15)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["blocked_reason"] == "no_chrt"
        assert row["qty_available"] == 15

        preview = await stock_service.preview_stock(db_session, project.id, fbs_wh.wb_warehouse_id)
        assert preview["rows_no_chrt"] >= 1
        # Позиция физически не уедет — расшифровка обязана назвать её причиной,
        # иначе 15 штук просто исчезают между «Доступно» склада и «к передаче».
        assert preview["breakdown"]["cut_no_chrt"] >= 15

    @pytest.mark.asyncio
    async def test_breakdown_balances_and_names_the_cuts(self, db_session, project):
        """Расшифровка дельты сходится до штуки и называет виновника поимённо.

        Карточка склада показывает «Доступно» ТОЛЬКО по нашему учёту, а FBS
        пропускает его ещё через зеркало ФФ, буфер и ручное количество. Две
        цифры об одном товаре читаются как ошибка расчёта, пока разложения нет.
        """
        # Свой стенд: дефолт фикстуры — источник «наш учёт», а зеркало срезает
        # остаток только при «Минимуме из двух».
        wh = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(
            db_session, project.id, stock_source=FbsStockSource.MIN_OF_BOTH.value
        )
        await _mk_link(db_session, project.id, fbs_wh, wh)
        # Зеркало провайдера видит меньше нашего учёта → «Минимум из двух» срежет 40.
        mirrored = await _mk_nom(db_session, project.id, chrt_id=7810)
        await _mk_stock(db_session, project.id, wh, mirrored, 100)
        await _mk_mirror(db_session, project.id, wh, mirrored, 60)
        # Ручное «Кол-во» — потолок 30 при свободных 50.
        capped = await _mk_nom(db_session, project.id, chrt_id=7811)
        await _mk_stock(db_session, project.id, wh, capped, 50)
        db_session.add(
            WbFbsStockOverride(
                project_id=project.id,
                wb_warehouse_id=fbs_wh.wb_warehouse_id,
                nomenclature_id=capped.id,
                qty=30,
            )
        )
        await db_session.commit()

        preview = await stock_service.preview_stock(db_session, project.id, fbs_wh.wb_warehouse_id)
        b = preview["breakdown"]

        assert b["cut_by_mirror"] == 40
        assert b["cut_by_override"] == 20
        # Цепочка обязана сходиться: иначе расшифровка врёт заметнее цифры,
        # которую объясняет.
        cuts = sum(
            b[k] for k in
            ("cut_by_mirror", "cut_by_buffer", "cut_no_chrt", "cut_by_override", "cut_other")
        )
        assert b["ledger_free"] - cuts == preview["total_units"]

    @pytest.mark.asyncio
    async def test_preview_attaches_live_wb_stock(self, db_session, project, fbs_env, monkeypatch):
        """Колонка «В WB» приезжает вместе с превью — автоматически, без кнопки."""
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=7710)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        await db_session.commit()

        asked: list[tuple] = []

        class _Client:
            async def get_stocks(self, wb_warehouse_id, chrt_ids):
                asked.append((wb_warehouse_id, sorted(chrt_ids)))
                return {7710: 42}

        async def _fake(db, project_id):
            return _Client()

        monkeypatch.setattr(stock_service, "_get_client", _fake)

        preview = await stock_service.preview_stock(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert preview["wb_stock_known"] is True
        assert asked == [(fbs_wh.wb_warehouse_id, [7710])]  # спрашиваем только позиции с chrt_id
        row = next(r for r in preview["rows"] if r.get("chrt_id") == 7710)
        assert row["qty_wb"] == 42

    @pytest.mark.asyncio
    async def test_preview_survives_wb_failure(self, db_session, project, fbs_env, monkeypatch):
        """Падение WB не роняет экран: `qty_wb` = None, флаг снят — рисуем прочерк, не ноль."""
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=7711)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        await db_session.commit()

        class _Client:
            async def get_stocks(self, wb_warehouse_id, chrt_ids):
                raise RuntimeError("429 too many requests")

        async def _fake(db, project_id):
            return _Client()

        monkeypatch.setattr(stock_service, "_get_client", _fake)

        preview = await stock_service.preview_stock(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert preview["wb_stock_known"] is False
        assert preview["total_rows"] >= 1
        assert all(r.get("qty_wb") is None for r in preview["rows"])


    @pytest.mark.asyncio
    async def test_matrix_columns_are_linked_fbs_warehouses(
        self, db_session, project, fbs_env, monkeypatch
    ):
        """Колонки матрицы — склады ПРОДАВЦА WB со связкой, а не наши внутренние.

        Первая версия экрана строилась по нашим складам и отвечала не на тот
        вопрос: пользователю нужно видеть, что стоит на «Белой Даче» и сколько
        туда можно довезти, а не сумму по нашему складу.
        """
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=8801)
        await _mk_stock(db_session, project.id, wh, nom, 7)
        await db_session.commit()

        class _Client:
            async def get_stocks(self, wb_warehouse_id, chrt_ids):
                return {8801: 3}

        async def _fake(db, project_id):
            return _Client()

        monkeypatch.setattr(stock_service, "_get_client", _fake)

        matrix = await stock_service.stock_matrix(db_session, project.id)

        ids = [w["wb_warehouse_id"] for w in matrix["warehouses"]]
        assert ids == [fbs_wh.wb_warehouse_id]
        assert matrix["wb_stock_known"] is True

        row = next(r for r in matrix["rows"] if r["nomenclature_id"] == nom.id)
        cell = row["cells"][str(fbs_wh.wb_warehouse_id)]
        assert cell["wb"] == 3  # стоит в кабинете
        assert cell["can"] == 7  # можем поставить с привязанного склада
        # Маржа без выручки не определена — None, а не ноль.
        assert row["margin_pct"] is None or isinstance(row["margin_pct"], float)

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project, fbs_env):
        """Остаток чужого проекта на «том же» складе продавца не подмешивается."""
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=122)
        await _mk_stock(db_session, project.id, wh, nom, 5)

        alien_wh = await _mk_warehouse(db_session, other_project.id)
        alien_fbs = WbFbsWarehouse(
            project_id=other_project.id,
            wb_warehouse_id=fbs_wh.wb_warehouse_id,  # тот же id склада продавца
            stock_source=FbsStockSource.LEDGER.value,
            is_active=True,
        )
        db_session.add(alien_fbs)
        await db_session.flush()
        await _mk_link(db_session, other_project.id, alien_fbs, alien_wh)
        alien_nom = await _mk_nom(db_session, other_project.id, chrt_id=123)
        await _mk_stock(db_session, other_project.id, alien_wh, alien_nom, 999)
        await db_session.commit()

        rows = await stock_service.compute_fbs_stock(db_session, project.id, fbs_wh.wb_warehouse_id)
        assert {r["nomenclature_id"] for r in rows} == {nom.id}

    @pytest.mark.asyncio
    async def test_unlinked_fbs_warehouse_returns_nothing(self, db_session, project):
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id)
        await db_session.commit()
        assert await stock_service.compute_fbs_stock(db_session, project.id, fbs_wh.wb_warehouse_id) == []

    @pytest.mark.asyncio
    async def test_unknown_warehouse_raises(self, db_session, project):
        with pytest.raises(ValueError, match="не найден"):
            await stock_service.compute_fbs_stock(db_session, project.id, 987654321)


# ═════════════════════════════════════════════════════════════════════════════
# get_open_fbs_qty — обратный гейт для сборки
# ═════════════════════════════════════════════════════════════════════════════


class TestOpenFbsQty:
    @pytest.mark.asyncio
    async def test_empty_without_fbs(self, db_session, project):
        wh = await _mk_warehouse(db_session, project.id)
        await db_session.commit()
        assert await stock_service.get_open_fbs_qty(db_session, project.id, [wh.id]) == {}
        assert await stock_service.get_open_fbs_qty(db_session, project.id, []) == {}

    @pytest.mark.asyncio
    async def test_counts_open_orders(self, db_session, project, fbs_env):
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=130)
        await _mk_fbs_order(db_session, project.id, fbs_wh, nom)
        await _mk_fbs_order(db_session, project.id, fbs_wh, nom, status="complete")
        await db_session.commit()

        assert await stock_service.get_open_fbs_qty(db_session, project.id, [wh.id]) == {nom.id: 1}

    @pytest.mark.asyncio
    async def test_no_double_count_for_two_linked_warehouses(self, db_session, project):
        """Один склад WB ← два наших склада: задание не должно посчитаться дважды."""
        wh1 = await _mk_warehouse(db_session, project.id)
        wh2 = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id)
        await _mk_link(db_session, project.id, fbs_wh, wh1)
        await _mk_link(db_session, project.id, fbs_wh, wh2)
        nom = await _mk_nom(db_session, project.id, chrt_id=131)
        await _mk_fbs_order(db_session, project.id, fbs_wh, nom)
        await db_session.commit()

        assert await stock_service.get_open_fbs_qty(db_session, project.id, [wh1.id, wh2.id]) == {nom.id: 1}


# ═════════════════════════════════════════════════════════════════════════════
# push_stocks — дельта, верификация, обнуление
# ═════════════════════════════════════════════════════════════════════════════


async def _state_for(db: AsyncSession, project_id: int, chrt_id: int) -> WbFbsStockState | None:
    result = await db.execute(
        select(WbFbsStockState).where(
            WbFbsStockState.project_id == project_id,
            WbFbsStockState.chrt_id == chrt_id,
        )
    )
    return result.scalar_one_or_none()


class TestPushStocks:
    @pytest.mark.asyncio
    async def test_push_sends_and_verifies(self, db_session, project, fbs_env, fake_client):
        wh, _fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=201)
        await _mk_stock(db_session, project.id, wh, nom, 8)
        await db_session.commit()

        push_ids = await stock_service.push_stocks(db_session, project.id, trigger="manual")
        assert len(push_ids) == 1
        assert fake_client.sent_map == {201: 8}
        assert fake_client.gets == [[201]]  # верификация обязательна после PUT

        pushes = await stock_service.list_pushes(db_session, project.id)
        assert pushes[0]["status"] == FbsPushStatus.OK.value
        assert pushes[0]["rows_sent"] == 1
        assert pushes[0]["rows_mismatch"] == 0

        state = await _state_for(db_session, project.id, 201)
        assert state is not None
        assert (state.qty_sent, state.qty_confirmed) == (8, 8)
        assert state.verified_at is not None

    @pytest.mark.asyncio
    async def test_push_records_mismatch_on_204_lie(self, db_session, project, fbs_env, monkeypatch):
        """WB отвечает 204 на PUT, даже когда остаток не обновился — ловим верификацией."""
        wh, _fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=202)
        await _mk_stock(db_session, project.id, wh, nom, 5)
        await db_session.commit()

        client = _FakeClient(confirm={202: 0})

        async def _factory(db, project_id):
            return client

        monkeypatch.setattr(stock_service, "_get_client", _factory)
        await stock_service.push_stocks(db_session, project.id)

        pushes = await stock_service.list_pushes(db_session, project.id)
        assert pushes[0]["status"] == FbsPushStatus.PARTIAL.value
        assert pushes[0]["rows_mismatch"] == 1
        state = await _state_for(db_session, project.id, 202)
        # Базовая точка дельты — ПОДТВЕРЖДЁННОЕ значение, а не отправленное:
        # иначе следующий прогон решит «не изменилось» (см. тест ниже).
        assert (state.qty_sent, state.qty_confirmed) == (0, 0)
        assert state.last_error and "0" in state.last_error

    @pytest.mark.asyncio
    async def test_mismatch_is_retried_on_next_push(self, db_session, project, fbs_env, monkeypatch):
        """После «204-lie» позиция обязана уехать снова — сама, без ручного force.

        Иначе у WB навсегда висит фантомный остаток, а мы каждые 3 минуты
        рапортуем «изменений нет».
        """
        wh, _fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=208)
        await _mk_stock(db_session, project.id, wh, nom, 5)
        await db_session.commit()

        client = _FakeClient(confirm={208: 0})  # WB подтвердил 0 вместо 5

        async def _factory(db, project_id):
            return client

        monkeypatch.setattr(stock_service, "_get_client", _factory)
        await stock_service.push_stocks(db_session, project.id)
        assert len(client.puts) == 1

        await stock_service.push_stocks(db_session, project.id)
        assert client.puts[-1] == [(208, 5)]  # переотправили без force
        assert len(client.puts) == 2

    @pytest.mark.asyncio
    async def test_busy_lock_blocks_concurrent_push(self, db_session, project, fbs_env, fake_client):
        """Кнопка ‖ джоб: пока лок проекта занят, второй прогон не идёт в WB.

        Лок берёт САМ push_stocks — обе точки входа (api-контейнер и worker)
        зовут именно его, и лок только у одной из них не исключал бы ничего.
        """
        wh, _fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=209)
        await _mk_stock(db_session, project.id, wh, nom, 7)
        await db_session.commit()

        fake_redis = _FakeRedis()
        with patch("backend.cache.get_redis", AsyncMock(return_value=fake_redis)):
            # Лок держит «соседний» прогон (джоб из worker).
            assert await locks.acquire_lock(locks.PUSH_LOCK_NAME, project.id) is not None
            assert await stock_service.push_stocks(db_session, project.id) == []

        assert fake_client.puts == []
        assert await stock_service.list_pushes(db_session, project.id) == []

    @pytest.mark.asyncio
    async def test_push_delta_skips_unchanged(self, db_session, project, fbs_env, fake_client):
        wh, _fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=203)
        await _mk_stock(db_session, project.id, wh, nom, 4)
        await db_session.commit()

        await stock_service.push_stocks(db_session, project.id)
        assert len(fake_client.puts) == 1

        await stock_service.push_stocks(db_session, project.id)
        assert len(fake_client.puts) == 1  # второй прогон ничего не шлёт

        await stock_service.push_stocks(db_session, project.id, force=True)
        assert len(fake_client.puts) == 2  # force пересылает всё

    @pytest.mark.asyncio
    async def test_zeroed_position_is_pushed(self, db_session, project, fbs_env, fake_client):
        """Остаток кончился → на WB обязан уехать 0, иначе он продолжит продавать."""
        wh, _fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=204)
        await _mk_stock(db_session, project.id, wh, nom, 6)
        await db_session.commit()

        await stock_service.push_stocks(db_session, project.id)
        assert fake_client.sent_map[204] == 6

        stock = await db_session.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == project.id,
                WarehouseStock.nomenclature_id == nom.id,
            )
        )
        row = stock.scalar_one()
        row.quantity = 0
        await db_session.commit()

        await stock_service.push_stocks(db_session, project.id)
        assert fake_client.puts[-1] == [(204, 0)]
        state = await _state_for(db_session, project.id, 204)
        assert state.qty_sent == 0

    @pytest.mark.asyncio
    async def test_orphan_state_is_zeroed(self, db_session, project, fbs_env, fake_client):
        """Позиция исчезла из остатков совсем — состояние прошлого пуша всё равно обнуляем."""
        _wh, fbs_wh = fbs_env
        db_session.add(
            WbFbsStockState(
                project_id=project.id,
                wb_warehouse_id=fbs_wh.wb_warehouse_id,
                chrt_id=205,
                barcode="GONE",
                qty_sent=11,
            )
        )
        await db_session.commit()

        await stock_service.push_stocks(db_session, project.id)
        assert fake_client.sent_map == {205: 0}

    @pytest.mark.asyncio
    async def test_no_chrt_positions_counted_in_push(self, db_session, project, fbs_env, fake_client):
        wh, _fbs_wh = fbs_env
        good = await _mk_nom(db_session, project.id, chrt_id=206)
        blind = await _mk_nom(db_session, project.id, chrt_id=None)
        await _mk_stock(db_session, project.id, wh, good, 3)
        await _mk_stock(db_session, project.id, wh, blind, 9)
        await db_session.commit()

        await stock_service.push_stocks(db_session, project.id)
        pushes = await stock_service.list_pushes(db_session, project.id)
        assert pushes[0]["rows_no_chrt"] == 1
        assert fake_client.sent_map == {206: 3}

    @pytest.mark.asyncio
    async def test_processing_warehouse_is_skipped(self, db_session, project, fake_client):
        """isProcessing → WB не принимает остатки; авто-прогон молча пропускает склад."""
        wh = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id, is_processing=True)
        await _mk_link(db_session, project.id, fbs_wh, wh)
        nom = await _mk_nom(db_session, project.id, chrt_id=207)
        await _mk_stock(db_session, project.id, wh, nom, 4)
        await db_session.commit()

        assert await stock_service.push_stocks(db_session, project.id) == []
        assert fake_client.puts == []

        ids = await stock_service.push_stocks(
            db_session, project.id, wb_warehouse_ids=[fbs_wh.wb_warehouse_id], trigger="manual"
        )
        assert len(ids) == 1
        pushes = await stock_service.list_pushes(db_session, project.id)
        assert pushes[0]["status"] == FbsPushStatus.ERROR.value
        assert "isProcessing" in (pushes[0]["error_msg"] or "")

    @pytest.mark.asyncio
    async def test_inactive_warehouse_not_pushed(self, db_session, project, fake_client):
        """Автопрогон выключенный склад молча пропускает, ЯВНЫЙ запрос — с журналом.

        Отсечение по `is_active` жило прямо в выборке целей, поэтому явный запрос
        давал пустой `targets` и ранний `return []` ДО создания журнала: ручка
        отвечала успехом, тост обещал «трансляция запущена», а следов прогона
        не появлялось вовсе — пользователь считал, что остатки уехали.
        """
        wh = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id, is_active=False)
        await _mk_link(db_session, project.id, fbs_wh, wh)
        await db_session.commit()

        assert await stock_service.push_stocks(db_session, project.id) == []
        assert fake_client.puts == []
        assert await stock_service.list_pushes(db_session, project.id) == []

        ids = await stock_service.push_stocks(
            db_session, project.id, wb_warehouse_ids=[fbs_wh.wb_warehouse_id], trigger="manual"
        )
        assert len(ids) == 1
        pushes = await stock_service.list_pushes(db_session, project.id)
        assert pushes[0]["status"] == FbsPushStatus.ERROR.value
        assert "выключена" in (pushes[0]["error_msg"] or "")
        assert fake_client.puts == []

    @pytest.mark.asyncio
    async def test_put_failure_finalizes_push_as_error(self, db_session, project, fbs_env, monkeypatch):
        """Падение WB не должно оставлять журнал висеть в RUNNING."""
        wh, _fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=208)
        await _mk_stock(db_session, project.id, wh, nom, 2)
        await db_session.commit()

        client = _FakeClient(put_error=RuntimeError("WB 409 StoreIsProcessing"))

        async def _factory(db, project_id):
            return client

        monkeypatch.setattr(stock_service, "_get_client", _factory)
        await stock_service.push_stocks(db_session, project.id)

        pushes = await stock_service.list_pushes(db_session, project.id)
        assert pushes[0]["status"] == FbsPushStatus.ERROR.value
        assert "StoreIsProcessing" in (pushes[0]["error_msg"] or "")
        assert await _state_for(db_session, project.id, 208) is None

    @pytest.mark.asyncio
    async def test_missing_client_finalizes_push_as_error(self, db_session, project, fbs_env, monkeypatch):
        wh, _fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=209)
        await _mk_stock(db_session, project.id, wh, nom, 2)
        await db_session.commit()

        async def _factory(db, project_id):
            raise RuntimeError("FbsNotConfigured")

        monkeypatch.setattr(stock_service, "_get_client", _factory)
        ids = await stock_service.push_stocks(db_session, project.id)

        assert len(ids) == 1
        pushes = await stock_service.list_pushes(db_session, project.id)
        assert pushes[0]["status"] == FbsPushStatus.ERROR.value
        assert "FbsNotConfigured" in (pushes[0]["error_msg"] or "")

    @pytest.mark.asyncio
    async def test_reserve_reaches_wb(self, db_session, project, fbs_env, fake_client):
        """Сквозной инвариант: заявка на сборку уменьшает то, что уезжает в WB."""
        wh, _fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=210)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        await _mk_assembly(db_session, project.id, wh, nom, 7, status=AssemblyStatus.READY)
        await db_session.commit()

        await stock_service.push_stocks(db_session, project.id)
        assert fake_client.sent_map == {210: 3}


# ═════════════════════════════════════════════════════════════════════════════
# Позиция WB = chrtId: два баркода, открытые задания, буфер, пул привязок
# ═════════════════════════════════════════════════════════════════════════════


class TestSharedChrtOnLiveData:
    @pytest.mark.asyncio
    async def test_open_orders_of_shared_chrt_do_not_resell(self, db_session, project, fbs_env, fake_client):
        """Два баркода на одном chrtId: проданное вычитается из СУММЫ позиции.

        Задания резолвятся на МЕНЬШИЙ nomenclature_id (`_resolve_nomenclature`
        сортирует по id), и раньше строка старого баркода с нулевым остатком
        гасила спрос клампом — в WB уезжал полный остаток нового баркода, то есть
        уже проданные единицы выставлялись к продаже второй раз.
        """
        wh, fbs_wh = fbs_env
        old = await _mk_nom(db_session, project.id, chrt_id=7001)
        new = await _mk_nom(db_session, project.id, chrt_id=7001)
        await _mk_stock(db_session, project.id, wh, old, 0)
        await _mk_stock(db_session, project.id, wh, new, 40)
        for _ in range(12):
            await _mk_fbs_order(db_session, project.id, fbs_wh, old)
        await db_session.commit()

        await stock_service.push_stocks(db_session, project.id)
        assert fake_client.sent_map == {7001: 28}, "40 + 0 − 12 = 28, а не 40"

    @pytest.mark.asyncio
    async def test_open_orders_without_stock_row_are_still_subtracted(self, db_session, project, fbs_env, fake_client):
        """У номенклатуры с заданиями нет строки WarehouseStock — вычет обязан остаться."""
        wh, fbs_wh = fbs_env
        ghost = await _mk_nom(db_session, project.id, chrt_id=7002)
        stocked = await _mk_nom(db_session, project.id, chrt_id=7002)
        await _mk_stock(db_session, project.id, wh, stocked, 30)
        for _ in range(5):
            await _mk_fbs_order(db_session, project.id, fbs_wh, ghost)
        await db_session.commit()

        await stock_service.push_stocks(db_session, project.id)
        assert fake_client.sent_map == {7002: 25}

    @pytest.mark.asyncio
    async def test_safety_abs_is_held_once_per_chrt(self, db_session, project, fake_client):
        """abs = 10 при двух баркодах одного chrtId придерживает 10 штук, а не 20."""
        wh = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id, safety_stock_abs=10)
        await _mk_link(db_session, project.id, fbs_wh, wh)
        first = await _mk_nom(db_session, project.id, chrt_id=7003)
        second = await _mk_nom(db_session, project.id, chrt_id=7003)
        await _mk_stock(db_session, project.id, wh, first, 50)
        await _mk_stock(db_session, project.id, wh, second, 50)
        await db_session.commit()

        rows = await stock_service.compute_fbs_stock(db_session, project.id, fbs_wh.wb_warehouse_id)
        group = [r for r in rows if r["chrt_id"] == 7003]
        assert sum(r["qty_available"] for r in group) == 90
        # Расшифровка обязана совпадать с фактически удержанным.
        assert sum(r["buffer"] for r in group) == 10

        await stock_service.push_stocks(db_session, project.id)
        assert fake_client.sent_map == {7003: 90}


class TestOpenFbsSplitAcrossPool:
    @pytest.mark.asyncio
    async def test_open_orders_split_between_linked_warehouses(self, db_session, project):
        """Один склад WB ← два наших: блокируется fbs_open ОДИН раз, а не N раз.

        Прежний возврат полной цифры на каждый склад давал available 0 и там,
        и там — из пула в 20 шт было заблокировано 20 при обязательстве в 10.
        """
        wh1 = await _mk_warehouse(db_session, project.id)
        wh2 = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id)
        await _mk_link(db_session, project.id, fbs_wh, wh1)
        await _mk_link(db_session, project.id, fbs_wh, wh2)
        nom = await _mk_nom(db_session, project.id, chrt_id=7010)
        await _mk_stock(db_session, project.id, wh1, nom, 10)
        await _mk_stock(db_session, project.id, wh2, nom, 10)
        for _ in range(10):
            await _mk_fbs_order(db_session, project.id, fbs_wh, nom)
        await db_session.commit()

        per_wh1 = await stock_service.get_open_fbs_qty(db_session, project.id, [wh1.id])
        per_wh2 = await stock_service.get_open_fbs_qty(db_session, project.id, [wh2.id])
        assert per_wh1 == {nom.id: 5}
        assert per_wh2 == {nom.id: 5}
        # Пул целиком держит ровно обязательство — прямой и обратный гейт сходятся.
        both = await stock_service.get_open_fbs_qty(db_session, project.id, [wh1.id, wh2.id])
        assert both == {nom.id: 10}

    @pytest.mark.asyncio
    async def test_split_follows_stock(self, db_session, project):
        """Разнесение пропорционально остатку: пустой склад чужое не держит."""
        empty = await _mk_warehouse(db_session, project.id)
        full = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id)
        await _mk_link(db_session, project.id, fbs_wh, empty)
        await _mk_link(db_session, project.id, fbs_wh, full)
        nom = await _mk_nom(db_session, project.id, chrt_id=7011)
        await _mk_stock(db_session, project.id, empty, nom, 0)
        await _mk_stock(db_session, project.id, full, nom, 8)
        for _ in range(4):
            await _mk_fbs_order(db_session, project.id, fbs_wh, nom)
        await db_session.commit()

        assert await stock_service.get_open_fbs_qty(db_session, project.id, [empty.id]) == {}
        assert await stock_service.get_open_fbs_qty(db_session, project.id, [full.id]) == {nom.id: 4}


class TestLedgerRowCap:
    @pytest.mark.asyncio
    async def test_partial_ledger_refuses_to_push(self, db_session, project, fbs_env, fake_client, monkeypatch):
        """Неполная выборка ledger'а = отказ, а не отправка обрезанного хвоста нулями."""
        wh, fbs_wh = fbs_env
        for chrt in (7020, 7021, 7022):
            nom = await _mk_nom(db_session, project.id, chrt_id=chrt)
            await _mk_stock(db_session, project.id, wh, nom, 5)
        await db_session.commit()

        monkeypatch.setattr(stock_service, "_MAX_LEDGER_ROWS", 2)
        with pytest.raises(stock_service.FbsStockDataTooLarge):
            await stock_service.compute_fbs_stock(db_session, project.id, fbs_wh.wb_warehouse_id)

        push_ids = await stock_service.push_stocks(db_session, project.id, trigger="manual")
        assert len(push_ids) == 1
        pushes = await stock_service.list_pushes(db_session, project.id)
        assert pushes[0]["status"] == FbsPushStatus.ERROR.value
        assert "потолок выборки" in (pushes[0]["error_msg"] or "")
        assert fake_client.puts == [], "по неполным данным в WB не ходим"

    @pytest.mark.asyncio
    async def test_ledger_exactly_at_cap_is_complete(self, db_session, project, fbs_env, monkeypatch):
        """Ровно потолок — данные полные, отказывать не за что."""
        wh, fbs_wh = fbs_env
        for chrt in (7030, 7031):
            nom = await _mk_nom(db_session, project.id, chrt_id=chrt)
            await _mk_stock(db_session, project.id, wh, nom, 3)
        await db_session.commit()

        monkeypatch.setattr(stock_service, "_MAX_LEDGER_ROWS", 2)
        rows = await stock_service.compute_fbs_stock(db_session, project.id, fbs_wh.wb_warehouse_id)
        assert {r["chrt_id"] for r in rows} == {7030, 7031}
