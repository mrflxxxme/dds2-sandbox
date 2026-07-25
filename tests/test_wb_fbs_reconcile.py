"""
Тесты первичной сверки склада продавца WB («захват»).

Зачем эта сверка вообще существует: пуш остатков ДЕЛЬТОВЫЙ. Позиция, которую
наш расчёт считает в ноль и которую мы ни разу не отправляли, не уезжает вовсе
(`_select_delta`), а `_orphan_state_rows` ходит только по тому, что МЫ уже
слали. Значит остаток, проставленный в кабинете руками ДО подключения, не виден
ни одному пути — он так и продаётся, хотя на привязанном складе товара нет
(прод-кейс: «Фрунзе Мигфул», ковёр 2043788808268 — 125 шт в кабинете).

Покрываем ровно те инварианты, ради которых сверка и написана:
  • WB > 0 при нашем нуле → `unmanaged` с человекочитаемой причиной;
  • обе стороны > 0 и цифры разные → `mismatch` (это чинит сам пуш);
  • сошлось или в WB ноль → в отчёт не тащим;
  • запрос в WB чанкуется по 1000 chrtId;
  • apply ПЕРЕСЧИТЫВАЕТ сверку и обнуляет ТОЛЬКО `unmanaged` — даже если
    клиент явно прислал chrtId управляемой позиции (иначе через ручку можно
    занулить что угодно);
  • apply пишет `qty_sent = 0` в состояние (иначе следующая дельта снова
    не увидит позицию) и оставляет след в журнале прогонов;
  • изоляция по project_id; пустая номенклатура не роняет и в WB не ходит.
"""

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.cost import Nomenclature
from backend.models.warehouse import Warehouse, WarehouseStock
from backend.models.wb_fbs import (
    FbsPushStatus,
    FbsStockSource,
    FbsWarehouseMode,
    WbFbsStockOverride,
    WbFbsStockState,
    WbFbsWarehouse,
    WbFbsWarehouseLink,
)
from backend.services.wb_fbs import locks, stock_service

# ─── Хелперы фикстур ─────────────────────────────────────────────────────────


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


async def _mk_nom(db: AsyncSession, project_id: int, *, chrt_id: int | None = None) -> Nomenclature:
    nom = Nomenclature(
        project_id=project_id,
        barcode=f"FBS{_uid()}",
        chrt_id=chrt_id,
        article_seller=f"ART-{_uid()}",
        article_wb=_wb_id(),
    )
    db.add(nom)
    await db.flush()
    return nom


async def _mk_stock(db: AsyncSession, project_id: int, wh: Warehouse, nom: Nomenclature, qty: int) -> None:
    db.add(
        WarehouseStock(
            project_id=project_id,
            warehouse_id=wh.id,
            nomenclature_id=nom.id,
            barcode=nom.barcode,
            quantity=qty,
            defect_quantity=0,
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
        # Дефолт МОДЕЛИ — `observe` (в чужой кабинет ничего не пишем), но сверка
        # с обнулением — это запись: без `translate` гейт режима сработал бы
        # раньше проверяемого условия и тест проверял бы не то.
        "mode": FbsWarehouseMode.TRANSLATE.value,
    }
    params.update(settings)
    wh = WbFbsWarehouse(project_id=project_id, wb_warehouse_id=_wb_id(), name="Фрунзе Мигфул", **params)
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


async def _mk_exclude_override(
    db: AsyncSession, project_id: int, fbs_wh: WbFbsWarehouse, nom: Nomenclature
) -> WbFbsStockOverride:
    """Ручное количество 0 = «не отдавать» этот товар на этом складе продавца."""
    override = WbFbsStockOverride(
        project_id=project_id,
        wb_warehouse_id=fbs_wh.wb_warehouse_id,
        nomenclature_id=nom.id,
        qty=0,
    )
    db.add(override)
    await db.flush()
    return override


async def _mk_state(
    db: AsyncSession,
    project_id: int,
    fbs_wh: WbFbsWarehouse,
    nom: Nomenclature,
    *,
    qty_sent: int,
) -> None:
    """«Мы это уже транслировали» — база дельты для позиции."""
    db.add(
        WbFbsStockState(
            project_id=project_id,
            wb_warehouse_id=fbs_wh.wb_warehouse_id,
            chrt_id=nom.chrt_id,
            barcode=nom.barcode,
            nomenclature_id=nom.id,
            qty_sent=qty_sent,
            qty_confirmed=qty_sent,
        )
    )
    await db.flush()


async def _state_for(db: AsyncSession, project_id: int, chrt_id: int) -> WbFbsStockState | None:
    result = await db.execute(
        select(WbFbsStockState).where(
            WbFbsStockState.project_id == project_id,
            WbFbsStockState.chrt_id == chrt_id,
        )
    )
    return result.scalar_one_or_none()


class _FakeClient:
    """Заглушка WbFbsClient: помнит запросы и держит «остатки кабинета»."""

    def __init__(self, wb_stocks: dict[int, int] | None = None) -> None:
        self.stocks: dict[int, int] = {int(k): int(v) for k, v in (wb_stocks or {}).items()}
        self.gets: list[list[int]] = []
        self.puts: list[list[tuple[int, int]]] = []

    async def get_stocks(self, wb_warehouse_id: int, chrt_ids: list[int]) -> dict[int, int]:
        self.gets.append([int(c) for c in chrt_ids])
        return {int(c): int(self.stocks.get(int(c), 0)) for c in chrt_ids}

    async def put_stocks(self, wb_warehouse_id: int, items: list[tuple[int, int]]) -> None:
        self.puts.append([(int(c), int(a)) for c, a in items])
        for chrt, amount in items:
            self.stocks[int(chrt)] = int(amount)

    @property
    def asked(self) -> list[int]:
        return [chrt for chunk in self.gets for chrt in chunk]

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


@pytest_asyncio.fixture
async def fbs_env(db_session: AsyncSession, project):
    """Минимальный стенд: наш склад + склад продавца В РЕЖИМЕ ТРАНСЛЯЦИИ + привязка.

    `mode` задаётся ЯВНО: дефолт модели — `observe`, в котором в кабинет не пишем
    вовсе, и молчаливое наследование дефолта делало зелёными тесты обнуления,
    которые на самом деле проверяли запрещённый сценарий.
    """
    wh = await _mk_warehouse(db_session, project.id)
    fbs_wh = await _mk_fbs_warehouse(db_session, project.id, mode=FbsWarehouseMode.TRANSLATE.value)
    await _mk_link(db_session, project.id, fbs_wh, wh)
    await db_session.commit()
    return wh, fbs_wh


def _use_client(monkeypatch, client: _FakeClient) -> _FakeClient:
    """Подменяет фабрику клиента — в тестах в WB не ходим."""

    async def _factory(db, project_id):
        return client

    monkeypatch.setattr(stock_service, "_get_client", _factory)
    return client


# ═════════════════════════════════════════════════════════════════════════════
# Классификация строк сверки
# ═════════════════════════════════════════════════════════════════════════════


class TestReconcileClassification:
    @pytest.mark.asyncio
    async def test_wb_stock_without_ours_is_unmanaged(self, db_session, project, fbs_env, monkeypatch):
        """Главный кейс: в кабинете 125 шт, у нас позиции нет — это продаётся мимо нас."""
        _wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=5001)
        await db_session.commit()
        client = _use_client(monkeypatch, _FakeClient({5001: 125}))

        out = await stock_service.reconcile_warehouse(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert client.asked == [5001]
        assert out["checked"] == 1
        assert out["unmanaged_positions"] == 1
        assert out["unmanaged_units"] == 125
        assert out["mismatch_positions"] == 0
        row = out["rows"][0]
        assert (row["chrt_id"], row["wb_amount"], row["our_amount"]) == (5001, 125, 0)
        assert row["kind"] == stock_service.RECONCILE_UNMANAGED
        assert row["reason"] == stock_service.REASON_NO_STOCK
        assert row["barcode"] == nom.barcode
        assert row["article_seller"] == nom.article_seller

    @pytest.mark.asyncio
    async def test_both_positive_different_is_mismatch(self, db_session, project, fbs_env, monkeypatch):
        """Обе стороны > 0, цифры разошлись — это штатная работа дельта-пуша."""
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=5002)
        await _mk_stock(db_session, project.id, wh, nom, 8)
        await db_session.commit()
        _use_client(monkeypatch, _FakeClient({5002: 5}))

        out = await stock_service.reconcile_warehouse(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert out["mismatch_positions"] == 1
        assert out["unmanaged_positions"] == 0
        assert out["unmanaged_units"] == 0
        row = out["rows"][0]
        assert row["kind"] == stock_service.RECONCILE_MISMATCH
        assert (row["wb_amount"], row["our_amount"]) == (5, 8)
        assert row["reason"] is None

    @pytest.mark.asyncio
    async def test_equal_amounts_not_reported(self, db_session, project, fbs_env, monkeypatch):
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=5003)
        await _mk_stock(db_session, project.id, wh, nom, 8)
        await db_session.commit()
        _use_client(monkeypatch, _FakeClient({5003: 8}))

        out = await stock_service.reconcile_warehouse(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert out["rows"] == []
        assert out["checked"] == 1

    @pytest.mark.asyncio
    async def test_zero_in_wb_not_reported(self, db_session, project, fbs_env, monkeypatch):
        """В кабинете ноль — продавать нечего, показывать нечего."""
        _wh, fbs_wh = fbs_env
        await _mk_nom(db_session, project.id, chrt_id=5004)
        await db_session.commit()
        _use_client(monkeypatch, _FakeClient({5004: 0}))

        out = await stock_service.reconcile_warehouse(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert out["rows"] == []
        assert out["unmanaged_positions"] == 0

    @pytest.mark.asyncio
    async def test_reason_zero_stock(self, db_session, project, fbs_env, monkeypatch):
        """Позиция в расчёте есть, но остаток кончился — причина именно «нулевой остаток»."""
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=5005)
        await _mk_stock(db_session, project.id, wh, nom, 0)
        await db_session.commit()
        _use_client(monkeypatch, _FakeClient({5005: 12}))

        out = await stock_service.reconcile_warehouse(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert out["rows"][0]["reason"] == stock_service.REASON_ZERO

    @pytest.mark.asyncio
    async def test_reason_override_zero(self, db_session, project, fbs_env, monkeypatch):
        """Товар физически есть, но вручную выставлен 0 — причина обязана это назвать."""
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=5006)
        await _mk_stock(db_session, project.id, wh, nom, 30)
        await _mk_exclude_override(db_session, project.id, fbs_wh, nom)
        await db_session.commit()
        _use_client(monkeypatch, _FakeClient({5006: 7}))

        out = await stock_service.reconcile_warehouse(db_session, project.id, fbs_wh.wb_warehouse_id)

        row = out["rows"][0]
        assert row["kind"] == stock_service.RECONCILE_UNMANAGED
        assert row["reason"] == stock_service.REASON_OVERRIDE_ZERO

    @pytest.mark.asyncio
    async def test_reason_no_links(self, db_session, project, monkeypatch):
        """Склад продавца без привязки: мы не управляем НИЧЕМ, что в нём лежит."""
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id)
        await _mk_nom(db_session, project.id, chrt_id=5007)
        await db_session.commit()
        _use_client(monkeypatch, _FakeClient({5007: 4}))

        out = await stock_service.reconcile_warehouse(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert out["rows"][0]["reason"] == stock_service.REASON_NO_LINKS

    @pytest.mark.asyncio
    async def test_rows_sorted_by_wb_amount_desc(self, db_session, project, fbs_env, monkeypatch):
        """Сначала самое опасное: чем больше висит в кабинете, тем дороже ошибка."""
        _wh, fbs_wh = fbs_env
        for chrt in (5011, 5012, 5013):
            await _mk_nom(db_session, project.id, chrt_id=chrt)
        await db_session.commit()
        _use_client(monkeypatch, _FakeClient({5011: 3, 5012: 125, 5013: 40}))

        out = await stock_service.reconcile_warehouse(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert [r["chrt_id"] for r in out["rows"]] == [5012, 5013, 5011]

    @pytest.mark.asyncio
    async def test_rows_no_chrt_counted(self, db_session, project, fbs_env, monkeypatch):
        """Позиции без chrtId проверить нечем — но и терять их молча нельзя."""
        _wh, fbs_wh = fbs_env
        await _mk_nom(db_session, project.id, chrt_id=5014)
        await _mk_nom(db_session, project.id, chrt_id=None)
        await _mk_nom(db_session, project.id, chrt_id=None)
        await db_session.commit()
        _use_client(monkeypatch, _FakeClient({5014: 1}))

        out = await stock_service.reconcile_warehouse(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert out["checked"] == 1
        assert out["rows_no_chrt"] == 2

    @pytest.mark.asyncio
    async def test_empty_nomenclature_does_not_call_wb(self, db_session, project, fbs_env, monkeypatch):
        """Спрашивать нечего — в WB не идём и не падаем."""
        _wh, fbs_wh = fbs_env
        client = _use_client(monkeypatch, _FakeClient())

        out = await stock_service.reconcile_warehouse(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert out["checked"] == 0
        assert out["rows"] == []
        assert client.gets == []

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project, fbs_env, monkeypatch):
        """Номенклатура соседнего проекта не проверяется и в WB не спрашивается."""
        _wh, fbs_wh = fbs_env
        await _mk_nom(db_session, project.id, chrt_id=5021)
        await _mk_nom(db_session, other_project.id, chrt_id=5022)
        await db_session.commit()
        client = _use_client(monkeypatch, _FakeClient({5021: 2, 5022: 999}))

        out = await stock_service.reconcile_warehouse(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert client.asked == [5021]
        assert out["checked"] == 1
        assert [r["chrt_id"] for r in out["rows"]] == [5021]

    @pytest.mark.asyncio
    async def test_unknown_warehouse_rejected(self, db_session, project, monkeypatch):
        """Чужой / несуществующий склад продавца — доменная ошибка, а не пустой отчёт."""
        _use_client(monkeypatch, _FakeClient())
        with pytest.raises(ValueError):
            await stock_service.reconcile_warehouse(db_session, project.id, 424242)


# ═════════════════════════════════════════════════════════════════════════════
# Чанкование запроса в WB
# ═════════════════════════════════════════════════════════════════════════════


class TestReconcileChunking:
    @pytest.mark.asyncio
    async def test_chunks_by_1000(self):
        """WB принимает ≤1000 chrtId за запрос — 2500 обязаны уехать тремя чанками."""
        chrt_ids = list(range(9_000_000, 9_002_500))
        client = _FakeClient(dict.fromkeys(chrt_ids, 1))

        got = await stock_service._fetch_wb_stocks(client, 1, chrt_ids)

        assert [len(chunk) for chunk in client.gets] == [1000, 1000, 500]
        assert client.asked == chrt_ids  # ни один ключ не потерян
        assert len(got) == 2500


# ═════════════════════════════════════════════════════════════════════════════
# Применение сверки: обнуление неуправляемых позиций
# ═════════════════════════════════════════════════════════════════════════════


class TestApplyReconcile:
    @pytest.mark.asyncio
    async def test_zeroes_unmanaged_and_verifies(self, db_session, project, fbs_env, monkeypatch):
        _wh, fbs_wh = fbs_env
        await _mk_nom(db_session, project.id, chrt_id=6001)
        await db_session.commit()
        client = _use_client(monkeypatch, _FakeClient({6001: 125}))

        out = await stock_service.apply_reconcile(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert out["affected"] == 1
        assert out["ok"] is True
        assert client.sent_map == {6001: 0}
        # Верификация обязательна: WB отвечает 204 на PUT, даже если не обновил.
        assert client.gets[-1] == [6001]

    @pytest.mark.asyncio
    async def test_ignores_managed_even_if_requested(self, db_session, project, fbs_env, monkeypatch):
        """Клиентский список — ФИЛЬТР по свежей сверке, а не команда на отправку.

        Иначе через ручку можно занулить любую позицию склада, включая ту,
        которой мы управляем и которая прямо сейчас продаётся.
        """
        wh, fbs_wh = fbs_env
        unmanaged = await _mk_nom(db_session, project.id, chrt_id=6002)
        managed = await _mk_nom(db_session, project.id, chrt_id=6003)
        equal = await _mk_nom(db_session, project.id, chrt_id=6004)
        await _mk_stock(db_session, project.id, wh, managed, 8)
        await _mk_stock(db_session, project.id, wh, equal, 9)
        await db_session.commit()
        client = _use_client(monkeypatch, _FakeClient({6002: 125, 6003: 5, 6004: 9}))

        out = await stock_service.apply_reconcile(
            db_session,
            project.id,
            fbs_wh.wb_warehouse_id,
            [unmanaged.chrt_id, managed.chrt_id, equal.chrt_id],
        )

        assert out["affected"] == 1
        assert client.sent_map == {6002: 0}
        assert client.stocks[6003] == 5  # управляемая позиция не тронута
        assert client.stocks[6004] == 9

    @pytest.mark.asyncio
    async def test_filter_narrows_targets(self, db_session, project, fbs_env, monkeypatch):
        """Переданный список сужает обнуление до выбранных строк."""
        _wh, fbs_wh = fbs_env
        first = await _mk_nom(db_session, project.id, chrt_id=6005)
        await _mk_nom(db_session, project.id, chrt_id=6006)
        await db_session.commit()
        client = _use_client(monkeypatch, _FakeClient({6005: 10, 6006: 20}))

        out = await stock_service.apply_reconcile(
            db_session, project.id, fbs_wh.wb_warehouse_id, [first.chrt_id]
        )

        assert out["affected"] == 1
        assert client.sent_map == {6005: 0}
        assert client.stocks[6006] == 20

    @pytest.mark.asyncio
    async def test_writes_zero_state(self, db_session, project, fbs_env, monkeypatch):
        """Без записи состояния следующий дельта-пуш снова не увидел бы позицию."""
        _wh, fbs_wh = fbs_env
        await _mk_nom(db_session, project.id, chrt_id=6007)
        await db_session.commit()
        _use_client(monkeypatch, _FakeClient({6007: 30}))

        await stock_service.apply_reconcile(db_session, project.id, fbs_wh.wb_warehouse_id)

        state = await _state_for(db_session, project.id, 6007)
        assert state is not None
        assert (state.qty_sent, state.qty_confirmed) == (0, 0)
        assert state.verified_at is not None
        assert state.wb_warehouse_id == fbs_wh.wb_warehouse_id

    @pytest.mark.asyncio
    async def test_writes_push_journal(self, db_session, project, fbs_env, monkeypatch):
        """«Захват» склада обязан остаться в журнале: кто и когда обнулил."""
        _wh, fbs_wh = fbs_env
        await _mk_nom(db_session, project.id, chrt_id=6008)
        await db_session.commit()
        _use_client(monkeypatch, _FakeClient({6008: 11}))

        await stock_service.apply_reconcile(db_session, project.id, fbs_wh.wb_warehouse_id, user_id=None)

        pushes = await stock_service.list_pushes(db_session, project.id)
        assert len(pushes) == 1
        assert pushes[0]["trigger"] == "manual"
        assert pushes[0]["status"] == FbsPushStatus.OK.value
        assert pushes[0]["rows_total"] == 1
        assert pushes[0]["rows_sent"] == 1
        assert pushes[0]["finished_at"] is not None

    @pytest.mark.asyncio
    async def test_nothing_to_zero(self, db_session, project, fbs_env, monkeypatch):
        """Нечего обнулять — ни PUT, ни строки в журнале."""
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=6009)
        await _mk_stock(db_session, project.id, wh, nom, 4)
        await db_session.commit()
        client = _use_client(monkeypatch, _FakeClient({6009: 4}))

        out = await stock_service.apply_reconcile(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert out["affected"] == 0
        assert client.puts == []
        assert await stock_service.list_pushes(db_session, project.id) == []

    @pytest.mark.asyncio
    async def test_mismatch_alone_is_not_zeroed(self, db_session, project, fbs_env, monkeypatch):
        """`mismatch` чинит обычный пуш — обнулять живую позицию нельзя."""
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=6010)
        await _mk_stock(db_session, project.id, wh, nom, 8)
        await db_session.commit()
        client = _use_client(monkeypatch, _FakeClient({6010: 3}))

        out = await stock_service.apply_reconcile(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert out["affected"] == 0
        assert client.puts == []

    @pytest.mark.asyncio
    async def test_busy_lock_blocks_apply(self, db_session, project, fbs_env, monkeypatch):
        """Пока идёт трансляция, обнулять нельзя: два PUT по складу = гонка «кто последний»."""
        _wh, fbs_wh = fbs_env
        await _mk_nom(db_session, project.id, chrt_id=6011)
        await db_session.commit()
        client = _use_client(monkeypatch, _FakeClient({6011: 50}))

        fake_redis = _FakeRedis()
        with patch("backend.cache.get_redis", AsyncMock(return_value=fake_redis)):
            assert await locks.acquire_lock(locks.PUSH_LOCK_NAME, project.id) is not None
            with pytest.raises(stock_service.FbsReconcileBusy):
                await stock_service.apply_reconcile(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert client.puts == []
        assert await stock_service.list_pushes(db_session, project.id) == []

    @pytest.mark.asyncio
    async def test_project_isolation_on_apply(self, db_session, project, other_project, fbs_env, monkeypatch):
        """chrtId соседнего проекта не обнуляется, даже если его явно прислали."""
        _wh, fbs_wh = fbs_env
        mine = await _mk_nom(db_session, project.id, chrt_id=6012)
        alien = await _mk_nom(db_session, other_project.id, chrt_id=6013)
        await db_session.commit()
        client = _use_client(monkeypatch, _FakeClient({6012: 6, 6013: 777}))

        out = await stock_service.apply_reconcile(
            db_session, project.id, fbs_wh.wb_warehouse_id, [mine.chrt_id, alien.chrt_id]
        )

        assert out["affected"] == 1
        assert client.sent_map == {6012: 0}
        assert client.stocks[6013] == 777


# ═════════════════════════════════════════════════════════════════════════════
# Гарды обнуления: без них кнопка сносит каталог или жжёт бакет впустую
# ═════════════════════════════════════════════════════════════════════════════


class TestApplyGuards:
    @pytest.mark.asyncio
    async def test_no_links_refuses_zeroing(self, db_session, project, monkeypatch):
        """Склад без привязок: наш расчёт пуст → «неуправляем» ВЕСЬ каталог кабинета.

        Регресс, ради которого гард и написан: `_build_rows(..., [])` даёт пустой
        расчёт, сверка честно помечает каждую позицию `unmanaged`, и кнопка
        обнуляла бы весь FBS-ассортимент (продажи встают до ручной привязки
        и пуша с force). Дельта-пуш в этой же ситуации безопасен — он трогает
        только то, что мы сами когда-то отправляли.
        """
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id)
        for chrt in (6101, 6102, 6103):
            await _mk_nom(db_session, project.id, chrt_id=chrt)
        await db_session.commit()
        client = _use_client(monkeypatch, _FakeClient({6101: 10, 6102: 20, 6103: 30}))

        with pytest.raises(stock_service.FbsReconcileNoLinks):
            await stock_service.apply_reconcile(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert client.puts == []
        assert client.stocks == {6101: 10, 6102: 20, 6103: 30}
        assert await stock_service.list_pushes(db_session, project.id) == []

    @pytest.mark.asyncio
    async def test_no_links_report_still_readable(self, db_session, project, monkeypatch):
        """Чтение сверки на непривязанном складе полезно и не блокируется."""
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id)
        await _mk_nom(db_session, project.id, chrt_id=6104)
        await db_session.commit()
        _use_client(monkeypatch, _FakeClient({6104: 9}))

        out = await stock_service.reconcile_warehouse(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert out["unmanaged_positions"] == 1
        assert out["rows"][0]["reason"] == stock_service.REASON_NO_LINKS

    @pytest.mark.asyncio
    async def test_processing_warehouse_refuses_zeroing(self, db_session, project, monkeypatch):
        """Склад в обработке у WB: PUT вернул бы 409, а каждый 4xx стоит 10 запросов бакета.

        Пуш такие склады пропускает (`_push_stocks_locked`), кнопка «Передать
        остатки» во фронте погашена — путь обнуления обязан вести себя так же.
        """
        wh = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id, is_processing=True)
        await _mk_link(db_session, project.id, fbs_wh, wh)
        await _mk_nom(db_session, project.id, chrt_id=6105)
        await db_session.commit()
        client = _use_client(monkeypatch, _FakeClient({6105: 15}))

        with pytest.raises(stock_service.FbsWarehouseProcessing):
            await stock_service.apply_reconcile(db_session, project.id, fbs_wh.wb_warehouse_id)

        # Отказ до похода в WB: ни PUT, ни даже чтения остатков.
        assert client.puts == []
        assert client.gets == []
        assert await stock_service.list_pushes(db_session, project.id) == []

    @pytest.mark.asyncio
    async def test_observe_warehouse_refuses_zeroing(self, db_session, project, monkeypatch):
        """Склад в режиме наблюдения не получает PUT НИКОГДА — обнуление тоже запись.

        `observe` — дефолт модели и обещание фичи: подключение склада, остатки
        которого продавец ведёт руками, не должно ничего перезаписать в кабинете.
        Гейт стоял только в пуше, и кнопка «Обнулить их в WB» снимала товар
        с продажи в обход него.
        """
        wh = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id, mode=FbsWarehouseMode.OBSERVE.value)
        await _mk_link(db_session, project.id, fbs_wh, wh)
        await _mk_nom(db_session, project.id, chrt_id=6106)
        await db_session.commit()
        client = _use_client(monkeypatch, _FakeClient({6106: 125}))

        with pytest.raises(stock_service.FbsWarehouseObserveMode):
            await stock_service.apply_reconcile(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert client.puts == []
        assert client.gets == []
        assert await stock_service.list_pushes(db_session, project.id) == []

    @pytest.mark.asyncio
    async def test_observe_warehouse_report_still_readable(self, db_session, project, monkeypatch):
        """GET-сверка в наблюдении работает: она только читает кабинет."""
        wh = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id, mode=FbsWarehouseMode.OBSERVE.value)
        await _mk_link(db_session, project.id, fbs_wh, wh)
        await _mk_nom(db_session, project.id, chrt_id=6107)
        await db_session.commit()
        client = _use_client(monkeypatch, _FakeClient({6107: 12}))

        out = await stock_service.reconcile_warehouse(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert out["unmanaged_positions"] == 1
        assert client.puts == []


# ═════════════════════════════════════════════════════════════════════════════
# Расхождение цифр: сам пуш его НЕ чинит, пока база дельты равна расчёту
# ═════════════════════════════════════════════════════════════════════════════


class TestMismatchRealign:
    @pytest.mark.asyncio
    async def test_mismatch_realigns_delta_base(self, db_session, project, fbs_env, monkeypatch):
        """Отправили 5, продавец руками поставил 100 — дельта видит `amount == prev` и молчит.

        Единственный выход — записать в базу дельты то, что реально стоит у WB:
        тогда ближайший обычный пуш увидит разницу и отправит верное число.
        """
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=6201)
        await _mk_stock(db_session, project.id, wh, nom, 5)
        await _mk_state(db_session, project.id, fbs_wh, nom, qty_sent=5)
        await db_session.commit()
        client = _use_client(monkeypatch, _FakeClient({6201: 100}))

        out = await stock_service.apply_reconcile(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert out["affected"] == 0
        assert client.puts == []  # живой товар обнулять нельзя
        state = await _state_for(db_session, project.id, 6201)
        assert state is not None
        assert state.qty_sent == 100  # база дельты = факт кабинета
        assert "выровнена" in (out["message"] or "").lower()

    @pytest.mark.asyncio
    async def test_wb_zero_with_confirmed_state_is_reported(self, db_session, project, fbs_env, monkeypatch):
        """Обратное расхождение: мы «транслируем» 50, а в кабинете 0 — тупик дельты.

        `_select_delta` даёт `amount == prev` → позиция не уедет никогда, WB её
        не продаёт. Сверка — единственный механизм, способный это поймать,
        и объявлять такое состояние чистым нельзя.
        """
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=6202)
        await _mk_stock(db_session, project.id, wh, nom, 50)
        await _mk_state(db_session, project.id, fbs_wh, nom, qty_sent=50)
        await db_session.commit()
        _use_client(monkeypatch, _FakeClient({6202: 0}))

        out = await stock_service.reconcile_warehouse(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert out["mismatch_positions"] == 1
        assert out["unmanaged_positions"] == 0
        row = out["rows"][0]
        assert (row["wb_amount"], row["our_amount"]) == (0, 50)
        assert row["kind"] == stock_service.RECONCILE_MISMATCH
        assert row["reason"] == stock_service.REASON_WB_ZERO

    @pytest.mark.asyncio
    async def test_wb_zero_without_state_not_reported(self, db_session, project, fbs_env, monkeypatch):
        """Ноль в кабинете по позиции, которую мы ещё не слали, — штатно: пуш её отправит."""
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=6203)
        await _mk_stock(db_session, project.id, wh, nom, 50)
        await db_session.commit()
        _use_client(monkeypatch, _FakeClient({6203: 0}))

        out = await stock_service.reconcile_warehouse(db_session, project.id, fbs_wh.wb_warehouse_id)

        assert out["rows"] == []
        assert out["mismatch_positions"] == 0


# ═════════════════════════════════════════════════════════════════════════════
# Контракт причин: коды, а не фразы (зеркало RECONCILE_REASON_LABEL во фронте)
# ═════════════════════════════════════════════════════════════════════════════


class TestReasonContract:
    def test_reason_codes_are_stable(self):
        """Причины — машинные коды: человеческий текст живёт во фронтовом словаре.

        Список зафиксирован здесь и в `src/__tests__/lib/fbsReconcile.test.ts`:
        новый код обязан появиться в обоих местах, иначе в колонке «Причина»
        (и в Excel-выгрузке) вылезет сырой код.
        """
        assert stock_service.RECONCILE_REASONS == ("not_linked", "override_zero", "zero", "no_stock", "wb_zero")


# ═════════════════════════════════════════════════════════════════════════════
# Частичная отправка: обнуление необратимо, журнал обязан говорить правду
# ═════════════════════════════════════════════════════════════════════════════


class TestPartialPut:
    @pytest.mark.asyncio
    async def test_put_chunks_keeps_sent_on_fatal(self):
        """Фатальная ошибка не должна уносить с собой уже отправленные чанки."""
        from backend.integrations.wb_fbs_api import WbFbsRateLimited

        class _RateLimitedOnSecond:
            def __init__(self) -> None:
                self.calls = 0

            async def put_stocks(self, wb_warehouse_id, items):
                self.calls += 1
                if self.calls > 1:
                    raise WbFbsRateLimited("бакет исчерпан", retry_after=60)

        client = _RateLimitedOnSecond()
        items = [{"chrt_id": i, "amount": 0} for i in range(1, 4)]

        with patch.object(stock_service, "_WB_CHUNK", 1):
            sent, errors, fatal_exc = await stock_service._put_chunks(
                client, 1, items, fatal=(WbFbsRateLimited,)
            )

        assert [i["chrt_id"] for i in sent] == [1]  # первый чанк реально уехал
        assert errors == []
        assert isinstance(fatal_exc, WbFbsRateLimited)
        assert client.calls == 2  # после фатальной ошибки в WB больше не ходим

    @pytest.mark.asyncio
    async def test_apply_journals_what_really_went(self, db_session, project, fbs_env, monkeypatch):
        """429 на втором чанке: 1 позиция реально обнулена — журнал и состояние обязаны это знать.

        Прежнее поведение (`raise` прямо из `_put_chunks`) писало `rows_sent=0`
        и не сохраняло состояния: единственный аудит-след необратимой операции
        врал, а оператор жал кнопку повторно.
        """
        from backend.integrations.wb_fbs_api import WbFbsRateLimited

        _wh, fbs_wh = fbs_env
        await _mk_nom(db_session, project.id, chrt_id=6301)
        await _mk_nom(db_session, project.id, chrt_id=6302)
        await db_session.commit()

        class _FailSecondPut(_FakeClient):
            def __init__(self, stocks):
                super().__init__(stocks)
                self.put_calls = 0

            async def put_stocks(self, wb_warehouse_id, items):
                self.put_calls += 1
                if self.put_calls > 1:
                    raise WbFbsRateLimited("бакет исчерпан", retry_after=60)
                await super().put_stocks(wb_warehouse_id, items)

        client = _use_client(monkeypatch, _FailSecondPut({6301: 10, 6302: 20}))

        with patch.object(stock_service, "_WB_CHUNK", 1), pytest.raises(WbFbsRateLimited):
            await stock_service.apply_reconcile(db_session, project.id, fbs_wh.wb_warehouse_id)

        pushes = await stock_service.list_pushes(db_session, project.id)
        assert len(pushes) == 1
        assert pushes[0]["rows_sent"] == 1  # НЕ 0: одна позиция снята с продажи
        assert pushes[0]["status"] == FbsPushStatus.PARTIAL.value
        assert "RateLimited" in (pushes[0]["error_msg"] or "")
        # Состояние по реально ушедшей позиции записано — иначе дельта врёт.
        zeroed = 6301 if client.sent_map.get(6301) == 0 else 6302
        state = await _state_for(db_session, project.id, zeroed)
        assert state is not None
        assert state.qty_sent == 0
