"""
Обратный гейт FBS → сборка.

Товар, проданный со склада продавца WB (`WbFbsOrder.supplier_status` в
`new`/`confirm`), физически ещё лежит на нашем складе и НЕ списан из ledger'а
(списание делает `writeoff_completed_orders` только по `complete`). Значит без
явного вычета его можно второй раз набрать в сборку и увезти на FBO — а потом
не собрать FBS-задание.

Покрываем:
  1. ИНВАРИАНТ: FBS-заказов нет → поведение бит-в-бит прежнее (вычет = 0).
  2. Открытые задания режут `available` в `_validate_available_for_assembly`
     и в парном `get_warehouse_stock`.
  3. Терминальные статусы (`complete`/`cancel`) резерв не держат.
  4. Изоляция по проекту и по складу.
  5. `get_open_fbs_reserved` деградирует в no-op, когда домен FBS не установлен.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.services.assembly.crud import (
    _format_deficit_error,
    _validate_available_for_assembly,
)
from backend.services.warehouse_crud import create_warehouse
from backend.services.warehouse_inbound import accept_receipt, create_receipt
from backend.services.warehouse_stock_engine import (
    get_open_fbs_reserved,
    get_warehouse_stock,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _require_fbs_stock_service():
    """Полоса 2 кладёт `get_open_fbs_qty` — до этого интеграционные тесты пропускаем.

    Гейт при отсутствии модуля обязан деградировать в no-op, что проверяет
    отдельный тест ниже.
    """
    return pytest.importorskip(
        "backend.services.wb_fbs.stock_service",
        reason="backend/services/wb_fbs/stock_service.py ещё не создан",
    )


# ─── Фикстуры ────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def wh(db_session: AsyncSession, project):
    return await create_warehouse(
        db_session,
        project.id,
        {"name": f"FBSGATE-{_uid()}", "warehouse_type": "FULFILLMENT"},
    )


@pytest_asyncio.fixture
async def wh_other(db_session: AsyncSession, project):
    """Второй наш склад — для проверки, что вычет не течёт между складами."""
    return await create_warehouse(
        db_session,
        project.id,
        {"name": f"FBSGATE2-{_uid()}", "warehouse_type": "FULFILLMENT"},
    )


@pytest_asyncio.fixture
async def nom(db_session: AsyncSession, project):
    """Номенклатура с chrt_id (без него позиция физически не транслируется в WB)."""
    barcode = f"FBSG-{_uid()}"
    chrt_id = int(uuid.uuid4().int % 1_000_000_000)
    row = await db_session.execute(
        text(
            "INSERT INTO nomenclature (project_id, barcode, subject, chrt_id, updated_at) "
            "VALUES (:pid, :bc, :subj, :chrt, NOW()) RETURNING id"
        ),
        {"pid": project.id, "bc": barcode, "subj": "Ковёр тестовый", "chrt": chrt_id},
    )
    nom_id = row.scalar_one()
    await db_session.commit()
    from types import SimpleNamespace

    return SimpleNamespace(id=nom_id, barcode=barcode, chrt_id=chrt_id)


async def _receive(db_session, project_id: int, warehouse_id: int, barcode: str, qty: int) -> None:
    """Оприходовать qty на склад через приёмку (реальный путь ledger'а)."""
    receipt = await create_receipt(
        db_session,
        project_id,
        warehouse_id,
        {"items": [{"barcode": barcode, "expected_qty": qty, "actual_qty": qty}]},
    )
    await accept_receipt(db_session, project_id, receipt.id)
    await db_session.commit()


def _items(nom, qty: int) -> list[AssemblyRequestItem]:
    """Транзиентные позиции для прямого вызова валидатора (в сессию не кладём)."""
    return [AssemblyRequestItem(nomenclature_id=nom.id, barcode=nom.barcode, quantity=qty)]


async def _add_assembly_reserve(db_session, project_id: int, warehouse_id: int, nom, qty: int) -> AssemblyRequest:
    """Активная заявка, удерживающая резерв (статус READY)."""
    req = AssemblyRequest(
        project_id=project_id,
        warehouse_id=warehouse_id,
        number=f"ASM-FBSG-{_uid()}",
        status=AssemblyStatus.READY,
        pallets_count=1,
        pallet_weight_kg=100,
    )
    req.items = [
        AssemblyRequestItem(project_id=project_id, nomenclature_id=nom.id, barcode=nom.barcode, quantity=qty)
    ]
    db_session.add(req)
    await db_session.commit()
    return req


async def _link_fbs_warehouse(db_session, project_id: int, warehouse_id: int) -> int:
    """Склад продавца WB + активная привязка к нашему складу. Возвращает wb_warehouse_id."""
    from backend.models.wb_fbs import WbFbsWarehouse, WbFbsWarehouseLink

    wb_warehouse_id = int(uuid.uuid4().int % 900_000) + 100_000
    db_session.add(
        WbFbsWarehouse(
            project_id=project_id,
            wb_warehouse_id=wb_warehouse_id,
            name=f"WB-FBS-{_uid()}",
            is_active=True,
        )
    )
    db_session.add(
        WbFbsWarehouseLink(
            project_id=project_id,
            wb_warehouse_id=wb_warehouse_id,
            warehouse_id=warehouse_id,
            is_active=True,
        )
    )
    await db_session.commit()
    return wb_warehouse_id


async def _add_fbs_orders(db_session, project_id: int, wb_warehouse_id: int, nom, qty: int, status: str) -> None:
    """qty сборочных заданий: одно задание WB = одна единица товара."""
    from backend.models.wb_fbs import WbFbsOrder

    for _ in range(qty):
        db_session.add(
            WbFbsOrder(
                project_id=project_id,
                wb_order_id=int(uuid.uuid4().int % 9_000_000_000_000) + 1,
                wb_warehouse_id=wb_warehouse_id,
                nomenclature_id=nom.id,
                chrt_id=nom.chrt_id,
                barcode=nom.barcode,
                supplier_status=status,
            )
        )
    await db_session.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Инвариант обратной совместимости: без FBS-заказов ничего не меняется
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestNoFbsOrdersBehaviourUnchanged:
    async def test_validator_without_fbs_orders(self, db_session, project, wh, nom):
        """stock=100, резерв заявок=30 → доступно 70: ровно как до гейта."""
        await _receive(db_session, project.id, wh.id, nom.barcode, 100)
        await _add_assembly_reserve(db_session, project.id, wh.id, nom, 30)

        assert await _validate_available_for_assembly(db_session, project.id, wh.id, _items(nom, 70)) == []

        deficits = await _validate_available_for_assembly(db_session, project.id, wh.id, _items(nom, 71))
        assert len(deficits) == 1
        assert deficits[0]["stock"] == 100
        assert deficits[0]["reserved"] == 30
        assert deficits[0]["have"] == 70
        assert deficits[0]["fbs_open"] == 0

    async def test_error_text_unchanged_without_fbs(self, db_session, project, wh, nom):
        """В сообщении о дефиците не должно появляться упоминание FBS."""
        await _receive(db_session, project.id, wh.id, nom.barcode, 10)
        deficits = await _validate_available_for_assembly(db_session, project.id, wh.id, _items(nom, 50))
        msg = _format_deficit_error(deficits)
        assert "Недостаточно доступных остатков" in msg
        assert "продано по FBS" not in msg

    async def test_warehouse_stock_without_fbs_orders(self, db_session, project, wh, nom):
        """Парный расчёт: available = quantity − reserved, как раньше."""
        await _receive(db_session, project.id, wh.id, nom.barcode, 100)
        await _add_assembly_reserve(db_session, project.id, wh.id, nom, 40)

        row = next(r for r in await get_warehouse_stock(db_session, project.id, wh.id) if r["barcode"] == nom.barcode)
        assert row["quantity"] == 100
        assert row["reserved"] == 40
        assert row["available"] == 60

    async def test_get_open_fbs_reserved_empty_input(self, db_session, project):
        """Пустой список складов — без похода в БД, пустой словарь."""
        assert await get_open_fbs_reserved(db_session, project.id, []) == {}

    async def test_missing_fbs_module_is_noop(self, db_session, project, wh, nom, monkeypatch):
        """Домен FBS не установлен → вычет 0, валидатор ведёт себя как раньше.

        Симулируем отсутствие модуля через `sys.modules[name] = None` —
        в Python 3 это ровно ImportError на импорте. Гейт обязан молча
        деградировать, а не валить создание сборки.
        """
        import sys

        monkeypatch.setitem(sys.modules, "backend.services.wb_fbs.stock_service", None)

        assert await get_open_fbs_reserved(db_session, project.id, [wh.id]) == {}

        await _receive(db_session, project.id, wh.id, nom.barcode, 50)
        assert await _validate_available_for_assembly(db_session, project.id, wh.id, _items(nom, 50)) == []


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Арифметика гейта — на стабе `get_open_fbs_reserved`
#
# Проверяет ровно добавленное слагаемое, не завися от готовности полосы 2:
# и валидатор, и парный расчёт зовут функцию по имени модуля, так что подмена
# атрибута ловит обоих потребителей.
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestGateArithmeticWithStub:
    @staticmethod
    def _stub(monkeypatch, mapping: dict[int, int], seen: list | None = None) -> None:
        import backend.services.warehouse_stock_engine as engine

        async def _fake(db, project_id, warehouse_ids):
            if seen is not None:
                seen.append((project_id, list(warehouse_ids)))
            return dict(mapping)

        monkeypatch.setattr(engine, "get_open_fbs_reserved", _fake)

    async def test_validator_subtracts_fbs(self, db_session, project, wh, nom, monkeypatch):
        """stock=100, FBS держит 25 → 75 проходит, 80 нет."""
        await _receive(db_session, project.id, wh.id, nom.barcode, 100)
        self._stub(monkeypatch, {nom.id: 25})

        assert await _validate_available_for_assembly(db_session, project.id, wh.id, _items(nom, 75)) == []

        deficits = await _validate_available_for_assembly(db_session, project.id, wh.id, _items(nom, 80))
        assert len(deficits) == 1
        assert deficits[0]["stock"] == 100
        assert deficits[0]["reserved"] == 0
        assert deficits[0]["fbs_open"] == 25
        assert deficits[0]["have"] == 75
        assert "продано по FBS 25" in _format_deficit_error(deficits)

    async def test_validator_sums_fbs_and_assembly_reserve(self, db_session, project, wh, nom, monkeypatch):
        """Резерв заявок и FBS складываются, а не заменяют друг друга."""
        await _receive(db_session, project.id, wh.id, nom.barcode, 100)
        await _add_assembly_reserve(db_session, project.id, wh.id, nom, 30)
        self._stub(monkeypatch, {nom.id: 20})

        assert await _validate_available_for_assembly(db_session, project.id, wh.id, _items(nom, 50)) == []
        deficits = await _validate_available_for_assembly(db_session, project.id, wh.id, _items(nom, 51))
        assert deficits[0]["reserved"] == 30
        assert deficits[0]["fbs_open"] == 20
        assert deficits[0]["have"] == 50

    async def test_available_never_negative(self, db_session, project, wh, nom, monkeypatch):
        """FBS-заданий больше остатка (рассинхрон) → available = 0, не минус."""
        await _receive(db_session, project.id, wh.id, nom.barcode, 5)
        self._stub(monkeypatch, {nom.id: 9})

        deficits = await _validate_available_for_assembly(db_session, project.id, wh.id, _items(nom, 1))
        assert deficits[0]["have"] == 0

    async def test_validator_asks_only_target_warehouse(self, db_session, project, wh, nom, monkeypatch):
        """Спрашиваем FBS ровно по складу сборки и её проекту (iron rule)."""
        await _receive(db_session, project.id, wh.id, nom.barcode, 10)
        seen: list = []
        self._stub(monkeypatch, {}, seen)

        await _validate_available_for_assembly(db_session, project.id, wh.id, _items(nom, 5))
        assert seen == [(project.id, [wh.id])]

    async def test_warehouse_stock_subtracts_fbs(self, db_session, project, wh, nom, monkeypatch):
        """Парный расчёт available на экране склада вычитает то же самое."""
        await _receive(db_session, project.id, wh.id, nom.barcode, 100)
        await _add_assembly_reserve(db_session, project.id, wh.id, nom, 10)
        self._stub(monkeypatch, {nom.id: 15})

        row = next(r for r in await get_warehouse_stock(db_session, project.id, wh.id) if r["barcode"] == nom.barcode)
        assert row["quantity"] == 100
        assert row["reserved"] == 10
        assert row["available"] == 75

    async def test_other_nomenclature_untouched(self, db_session, project, wh, nom, monkeypatch):
        """Вычет адресный: чужой nomenclature_id в карте не влияет."""
        await _receive(db_session, project.id, wh.id, nom.barcode, 20)
        self._stub(monkeypatch, {nom.id + 10_000_000: 999})

        assert await _validate_available_for_assembly(db_session, project.id, wh.id, _items(nom, 20)) == []


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Гейт на реальных данных FBS (нужен `stock_service.get_open_fbs_qty`)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestFbsGateBlocksAssembly:
    async def test_open_orders_reduce_available(self, db_session, project, wh, nom):
        """stock=100, продано по FBS 25 → под сборку доступно 75, 80 не пройдёт."""
        _require_fbs_stock_service()
        await _receive(db_session, project.id, wh.id, nom.barcode, 100)
        wb_wh = await _link_fbs_warehouse(db_session, project.id, wh.id)
        await _add_fbs_orders(db_session, project.id, wb_wh, nom, 25, "new")

        assert await _validate_available_for_assembly(db_session, project.id, wh.id, _items(nom, 75)) == []

        deficits = await _validate_available_for_assembly(db_session, project.id, wh.id, _items(nom, 80))
        assert len(deficits) == 1
        assert deficits[0]["stock"] == 100
        assert deficits[0]["reserved"] == 0
        assert deficits[0]["fbs_open"] == 25
        assert deficits[0]["have"] == 75
        assert "продано по FBS 25" in _format_deficit_error(deficits)

    async def test_confirm_status_also_holds(self, db_session, project, wh, nom):
        """`confirm` (задание уже в поставке) держит остаток наравне с `new`."""
        _require_fbs_stock_service()
        await _receive(db_session, project.id, wh.id, nom.barcode, 40)
        wb_wh = await _link_fbs_warehouse(db_session, project.id, wh.id)
        await _add_fbs_orders(db_session, project.id, wb_wh, nom, 5, "new")
        await _add_fbs_orders(db_session, project.id, wb_wh, nom, 7, "confirm")

        deficits = await _validate_available_for_assembly(db_session, project.id, wh.id, _items(nom, 40))
        assert deficits[0]["fbs_open"] == 12
        assert deficits[0]["have"] == 28

    async def test_terminal_statuses_do_not_hold(self, db_session, project, wh, nom):
        """`complete` уже списан из ledger'а, `cancel` не продан — вычитать нельзя."""
        _require_fbs_stock_service()
        await _receive(db_session, project.id, wh.id, nom.barcode, 30)
        wb_wh = await _link_fbs_warehouse(db_session, project.id, wh.id)
        await _add_fbs_orders(db_session, project.id, wb_wh, nom, 4, "complete")
        await _add_fbs_orders(db_session, project.id, wb_wh, nom, 3, "cancel")
        await _add_fbs_orders(db_session, project.id, wb_wh, nom, 2, "cancel_carrier")

        assert await _validate_available_for_assembly(db_session, project.id, wh.id, _items(nom, 30)) == []

    async def test_inactive_link_does_not_hold(self, db_session, project, wh, nom):
        """Отвязанный склад (`is_active=false`) больше не режет доступное."""
        _require_fbs_stock_service()
        from sqlalchemy import update

        from backend.models.wb_fbs import WbFbsWarehouseLink

        await _receive(db_session, project.id, wh.id, nom.barcode, 50)
        wb_wh = await _link_fbs_warehouse(db_session, project.id, wh.id)
        await _add_fbs_orders(db_session, project.id, wb_wh, nom, 20, "new")
        await db_session.execute(
            update(WbFbsWarehouseLink)
            .where(
                WbFbsWarehouseLink.project_id == project.id,
                WbFbsWarehouseLink.warehouse_id == wh.id,
            )
            .values(is_active=False)
        )
        await db_session.commit()

        assert await _validate_available_for_assembly(db_session, project.id, wh.id, _items(nom, 50)) == []

    async def test_warehouse_stock_subtracts_fbs(self, db_session, project, wh, nom):
        """Парный расчёт available на экране склада вычитает то же самое."""
        _require_fbs_stock_service()
        await _receive(db_session, project.id, wh.id, nom.barcode, 100)
        wb_wh = await _link_fbs_warehouse(db_session, project.id, wh.id)
        await _add_fbs_orders(db_session, project.id, wb_wh, nom, 15, "new")

        row = next(r for r in await get_warehouse_stock(db_session, project.id, wh.id) if r["barcode"] == nom.barcode)
        assert row["quantity"] == 100
        assert row["reserved"] == 0
        assert row["available"] == 85


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Изоляция по складу и по проекту (на реальных данных FBS)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestFbsGateIsolation:
    async def test_other_warehouse_not_affected(self, db_session, project, wh, wh_other, nom):
        """FBS-задания склада A не режут доступное на складе B."""
        _require_fbs_stock_service()
        await _receive(db_session, project.id, wh.id, nom.barcode, 50)
        await _receive(db_session, project.id, wh_other.id, nom.barcode, 50)
        wb_wh = await _link_fbs_warehouse(db_session, project.id, wh.id)
        await _add_fbs_orders(db_session, project.id, wb_wh, nom, 20, "new")

        # На складе A вычет есть
        deficits = await _validate_available_for_assembly(db_session, project.id, wh.id, _items(nom, 50))
        assert deficits[0]["fbs_open"] == 20
        # На складе B — нет
        assert await _validate_available_for_assembly(db_session, project.id, wh_other.id, _items(nom, 50)) == []

    async def test_other_project_not_affected(self, db_session, project, other_project, wh, nom):
        """Задания чужого проекта не видны (iron rule: фильтр по project_id)."""
        _require_fbs_stock_service()
        await _receive(db_session, project.id, wh.id, nom.barcode, 50)
        wb_wh = await _link_fbs_warehouse(db_session, other_project.id, wh.id)
        await _add_fbs_orders(db_session, other_project.id, wb_wh, nom, 20, "new")

        assert await _validate_available_for_assembly(db_session, project.id, wh.id, _items(nom, 50)) == []


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Пул привязок: один склад WB ← N наших складов
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestFbsGatePoolOfWarehouses:
    """Домен разрешает N:1 («остатки суммируются»), и обязательство одно на пул.

    Прежний гейт возвращал ПОЛНУЮ цифру склада продавца на каждый наш склад:
    два склада по 8 шт при 8 открытых заданиях давали available 0 и там, и там —
    заявку нельзя было создать ни с одного, хотя физически свободно 8.
    """

    async def test_open_orders_are_split_between_linked_warehouses(
        self, db_session, project, wh, wh_other, nom
    ):
        _require_fbs_stock_service()
        from backend.models.wb_fbs import WbFbsWarehouseLink

        await _receive(db_session, project.id, wh.id, nom.barcode, 8)
        await _receive(db_session, project.id, wh_other.id, nom.barcode, 8)
        wb_wh = await _link_fbs_warehouse(db_session, project.id, wh.id)
        db_session.add(
            WbFbsWarehouseLink(
                project_id=project.id,
                wb_warehouse_id=wb_wh,
                warehouse_id=wh_other.id,
                is_active=True,
            )
        )
        await db_session.commit()
        await _add_fbs_orders(db_session, project.id, wb_wh, nom, 8, "new")

        reserved_first = await get_open_fbs_reserved(db_session, project.id, [wh.id])
        reserved_second = await get_open_fbs_reserved(db_session, project.id, [wh_other.id])
        assert reserved_first[nom.id] == 4
        assert reserved_second[nom.id] == 4

        # Заявка на свободную половину проходит с ЛЮБОГО склада пула.
        assert await _validate_available_for_assembly(db_session, project.id, wh.id, _items(nom, 4)) == []
        assert await _validate_available_for_assembly(db_session, project.id, wh_other.id, _items(nom, 4)) == []
        # А сверх обязательства — нет.
        assert await _validate_available_for_assembly(db_session, project.id, wh.id, _items(nom, 5)) != []

    async def test_single_link_still_holds_everything(self, db_session, project, wh, nom):
        """Одна привязка — вся цифра ей: поведение прежнее бит-в-бит."""
        _require_fbs_stock_service()
        await _receive(db_session, project.id, wh.id, nom.barcode, 20)
        wb_wh = await _link_fbs_warehouse(db_session, project.id, wh.id)
        await _add_fbs_orders(db_session, project.id, wb_wh, nom, 6, "new")

        assert (await get_open_fbs_reserved(db_session, project.id, [wh.id]))[nom.id] == 6
