"""
Потоварная замена количества, FBO-гейт и режим склада продавца.

Три механики, заменившие прежнюю систему правил (четыре уровня с приоритетами
оказалась лишней — канон владельца 2026-07-24):

  • **Ручное количество по товару** (`wb_fbs_stock_overrides`): одно поле в
    строке вкладки «Остатки». `0` — не отдавать, `N` — потолок (итог всегда
    `min(N, расчёт)`), очистка — вернуться к расчёту. Потолок обязан
    соблюдаться и на уровне позиции WB (chrtId), у которой может быть
    несколько баркодов.
  • **FBO-гейт** (`WbFbsWarehouse.fbo_max_qty`): «отдаём в FBS только то, чего
    нет на складах WB». Остаток FBO считается БЕЗ сгоревших (🔥), исключённых и
    транзитных складов; пустое зеркало обязано давать `fbo_qty = None` и
    выключать гейт целиком — иначе порог 0 отправил бы в FBS весь каталог.
  • **Режим склада** (`observe` / `translate`): в режиме наблюдения PUT в WB
    не делается НИКОГДА, и гейт стоит в сервисе, а не в роутере — иначе
    фоновый джоб обошёл бы его.
"""

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.cost import Nomenclature
from backend.models.integrations import WbWarehouseRemains, WbWarehouseStock
from backend.models.refs import ProductSubcategory, ProductSubcategoryMap, ProjectSetting
from backend.models.warehouse import Warehouse, WarehouseStock
from backend.models.wb_fbs import (
    FbsPushStatus,
    FbsStockSource,
    FbsWarehouseMode,
    WbFbsStockOverride,
    WbFbsStockPush,
    WbFbsWarehouse,
    WbFbsWarehouseLink,
)
from backend.schemas.wb_fbs import FbsOverrideSet
from backend.services.wb_fbs import stock_service

# ─── Хелперы стенда ──────────────────────────────────────────────────────────


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _wb_id() -> int:
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
) -> Nomenclature:
    nom = Nomenclature(
        project_id=project_id,
        barcode=f"OVR{_uid()}",
        chrt_id=chrt_id,
        article_seller=f"ART-{_uid()}",
        article_wb=_wb_id(),
        brand=brand,
        subject=subject,
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


async def _mk_remains(
    db: AsyncSession, project_id: int, nom: Nomenclature, qty: int, *, warehouse_name: str = "Коледино"
) -> None:
    """Строка зеркала отчёта «Остатки на складах» (гранулярность — баркод)."""
    db.add(
        WbWarehouseRemains(
            project_id=project_id,
            nm_id=nom.article_wb,
            barcode=nom.barcode,
            warehouse_name=warehouse_name,
            quantity=qty,
        )
    )
    await db.flush()


async def _mk_wb_stock(
    db: AsyncSession, project_id: int, nom: Nomenclature, qty: int, *, warehouse_name: str = "Коледино"
) -> None:
    """Строка суточного `wb_warehouse_stocks` (фолбэк, гранулярность — nm_id)."""
    db.add(
        WbWarehouseStock(
            project_id=project_id,
            nm_id=nom.article_wb,
            warehouse_name=warehouse_name,
            quantity=qty,
        )
    )
    await db.flush()


async def _row_for(db: AsyncSession, project_id: int, fbs_wh: WbFbsWarehouse, nom: Nomenclature) -> dict:
    rows = await stock_service.compute_fbs_stock(db, project_id, fbs_wh.wb_warehouse_id)
    match = [r for r in rows if r["nomenclature_id"] == nom.id]
    assert match, f"строка номенклатуры {nom.id} потеряна в расчёте"
    return match[0]


class _FakeClient:
    """Заглушка WbFbsClient: помнит PUT'ы (в WB в тестах не ходим)."""

    def __init__(self) -> None:
        self.puts: list[list[tuple[int, int]]] = []
        self.stocks: dict[int, int] = {}

    async def put_stocks(self, wb_warehouse_id: int, items: list[tuple[int, int]]) -> None:
        self.puts.append(list(items))
        for chrt, amount in items:
            self.stocks[chrt] = amount

    async def get_stocks(self, wb_warehouse_id: int, chrt_ids: list[int]) -> dict[int, int]:
        return {c: self.stocks.get(c, 0) for c in chrt_ids}

    @property
    def sent_map(self) -> dict[int, int]:
        return {chrt: amount for chunk in self.puts for chrt, amount in chunk}


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient()

    async def _factory(db, project_id):
        return client

    monkeypatch.setattr(stock_service, "_get_client", _factory)
    return client


@pytest_asyncio.fixture
async def fbs_env(db_session: AsyncSession, project):
    """Минимальный стенд: наш склад + склад продавца (translate) + привязка."""
    wh = await _mk_warehouse(db_session, project.id)
    fbs_wh = await _mk_fbs_warehouse(db_session, project.id)
    await _mk_link(db_session, project.id, fbs_wh, wh)
    await db_session.commit()
    return wh, fbs_wh


# ═════════════════════════════════════════════════════════════════════════════
# Ручное количество: 0 / потолок / снятие
# ═════════════════════════════════════════════════════════════════════════════


class TestOverrideFormula:
    """Канон владельца: «фиксированное, но не больше чем свободных остатков от ФФ»."""

    @pytest.mark.asyncio
    async def test_without_override_formula_is_bit_for_bit(self, db_session, project, fbs_env):
        """Ручного количества нет → `qty_available` равен расчёту, поле пустое."""
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=8100)
        await _mk_stock(db_session, project.id, wh, nom, 25)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["qty_computed"] == 25
        assert row["qty_available"] == 25
        assert row["override_qty"] is None
        assert row["blocked_reason"] is None

    @pytest.mark.asyncio
    async def test_zero_means_do_not_offer(self, db_session, project, fbs_env):
        """`qty = 0` — не отдавать: физика в превью видна, отдаём ноль."""
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=8101)
        await _mk_stock(db_session, project.id, wh, nom, 12)
        await db_session.commit()

        await stock_service.set_overrides(db_session, project.id, fbs_wh.wb_warehouse_id, [nom.id], 0)

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["qty_computed"] == 12, "физика остаётся видимой"
        assert row["qty_available"] == 0
        assert row["override_qty"] == 0
        assert row["blocked_reason"] == "override_zero"

    @pytest.mark.asyncio
    async def test_positive_qty_is_a_ceiling(self, db_session, project, fbs_env):
        """Число ниже расчёта режет расчёт."""
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=8102)
        await _mk_stock(db_session, project.id, wh, nom, 40)
        await db_session.commit()

        await stock_service.set_overrides(db_session, project.id, fbs_wh.wb_warehouse_id, [nom.id], 10)

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["qty_computed"] == 40
        assert row["qty_available"] == 10
        assert row["override_qty"] == 10

    @pytest.mark.asyncio
    async def test_qty_above_computed_does_not_raise_output(self, db_session, project, fbs_env):
        """Число выше свободного остатка НЕ поднимает выдачу.

        Иначе WB продолжит продавать то, чего на складе физически нет — ровно
        та беда, ради которой ручная цифра сделана потолком, а не назначением.
        """
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=8103)
        await _mk_stock(db_session, project.id, wh, nom, 3)
        await db_session.commit()

        await stock_service.set_overrides(db_session, project.id, fbs_wh.wb_warehouse_id, [nom.id], 100)

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["qty_available"] == 3

    @pytest.mark.asyncio
    async def test_clearing_override_restores_calculation(self, db_session, project, fbs_env):
        """`qty = None` снимает ограничение — позиция возвращается к расчёту."""
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=8104)
        await _mk_stock(db_session, project.id, wh, nom, 30)
        await db_session.commit()

        await stock_service.set_overrides(db_session, project.id, fbs_wh.wb_warehouse_id, [nom.id], 4)
        assert (await _row_for(db_session, project.id, fbs_wh, nom))["qty_available"] == 4

        await stock_service.set_overrides(db_session, project.id, fbs_wh.wb_warehouse_id, [nom.id], None)
        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["qty_available"] == 30
        assert row["override_qty"] is None
        assert row["blocked_reason"] is None

    @pytest.mark.asyncio
    async def test_applies_before_chrt_level_deductions(self, db_session, project, fbs_env, fake_client):
        """Порядок: физика → ручное количество → вычеты уровня chrtId.

        min(100, 10) − 3 открытых задания − 2 буфера = 5. Если бы потолок
        накладывался последним, в WB уехало бы 10 — на 5 штук больше, чем
        физически свободно под обязательствами.

        `qty_computed` («Можем отдать») те же вычеты позиции тоже получает —
        100 − 3 − 2 = 95, — но БЕЗ ручного количества: колонка обязана показывать
        потолок до вмешательства человека, и при этом сходиться с тем, что реально
        уедет. Пока она стояла «до вычетов», экран показывал 100 против 5.
        """
        wh = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id, safety_stock_abs=2)
        await _mk_link(db_session, project.id, fbs_wh, wh)
        nom = await _mk_nom(db_session, project.id, chrt_id=8105)
        await _mk_stock(db_session, project.id, wh, nom, 100)
        from backend.models.wb_fbs import WbFbsOrder

        for _ in range(3):
            db_session.add(
                WbFbsOrder(
                    project_id=project.id,
                    wb_order_id=int(uuid.uuid4().int % 10_000_000_000),
                    wb_warehouse_id=fbs_wh.wb_warehouse_id,
                    nomenclature_id=nom.id,
                    barcode=nom.barcode,
                    chrt_id=nom.chrt_id,
                    supplier_status="new",
                )
            )
        await db_session.commit()
        await stock_service.set_overrides(db_session, project.id, fbs_wh.wb_warehouse_id, [nom.id], 10)

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["qty_computed"] == 95  # 100 − 3 задания − 2 буфера, без ручного потолка
        assert row["qty_available"] == 5

        await stock_service.push_stocks(db_session, project.id)
        assert fake_client.sent_map == {8105: 5}

    @pytest.mark.asyncio
    async def test_zero_pushes_zero_instead_of_dropping_row(self, db_session, project, fbs_env, fake_client):
        """«Не отдавать» отправляет ИМЕННО ноль.

        Если бы строка просто выпадала из пуша, у WB остался бы последний
        известный остаток и он продолжил бы продавать запрещённый товар.
        """
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=8106)
        await _mk_stock(db_session, project.id, wh, nom, 12)
        await db_session.commit()

        await stock_service.push_stocks(db_session, project.id)
        assert fake_client.sent_map == {8106: 12}

        await stock_service.set_overrides(db_session, project.id, fbs_wh.wb_warehouse_id, [nom.id], 0)
        await stock_service.push_stocks(db_session, project.id)
        assert fake_client.puts[-1] == [(8106, 0)]

    @pytest.mark.asyncio
    async def test_ceiling_holds_on_shared_chrt_group(self, db_session, project, fbs_env, fake_client):
        """Один chrtId на двух баркодах: в WB уезжает не больше суммы потолков.

        Регресс, который чинили в системе правил и который не должен вернуться:
        потолок применялся только построчно, а в WB уходила сумма группы.
        """
        wh, fbs_wh = fbs_env
        nom_a = await _mk_nom(db_session, project.id, chrt_id=8107)
        nom_b = await _mk_nom(db_session, project.id, chrt_id=8107)
        await _mk_stock(db_session, project.id, wh, nom_a, 40)
        await _mk_stock(db_session, project.id, wh, nom_b, 40)
        await db_session.commit()

        await stock_service.set_overrides(db_session, project.id, fbs_wh.wb_warehouse_id, [nom_a.id], 10)
        await stock_service.set_overrides(db_session, project.id, fbs_wh.wb_warehouse_id, [nom_b.id], 5)

        rows = await stock_service.compute_fbs_stock(db_session, project.id, fbs_wh.wb_warehouse_id)
        shared = [r for r in rows if r["chrt_id"] == 8107]
        assert len(shared) == 2, "строки не схлопываются в превью — пользователь правит их по отдельности"
        assert sum(r["qty_available"] for r in shared) == 15

        await stock_service.push_stocks(db_session, project.id)
        assert fake_client.sent_map == {8107: 15}

    @pytest.mark.asyncio
    async def test_zero_on_whole_group_zeroes_the_position(self, db_session, project, fbs_env, fake_client):
        """Ноль на всех баркодах позиции → WB получает ноль, а не остаток соседа."""
        wh, fbs_wh = fbs_env
        nom_a = await _mk_nom(db_session, project.id, chrt_id=8108)
        nom_b = await _mk_nom(db_session, project.id, chrt_id=8108)
        await _mk_stock(db_session, project.id, wh, nom_a, 9)
        await _mk_stock(db_session, project.id, wh, nom_b, 11)
        await db_session.commit()

        await stock_service.push_stocks(db_session, project.id)
        assert fake_client.sent_map == {8108: 20}

        await stock_service.set_overrides(
            db_session, project.id, fbs_wh.wb_warehouse_id, [nom_a.id, nom_b.id], 0
        )
        await stock_service.push_stocks(db_session, project.id)
        assert fake_client.puts[-1] == [(8108, 0)]


# ═════════════════════════════════════════════════════════════════════════════
# set_overrides: хранение, массовое проставление, изоляция
# ═════════════════════════════════════════════════════════════════════════════


async def _overrides(db: AsyncSession, project_id: int, wb_warehouse_id: int) -> dict[int, int]:
    result = await db.execute(
        select(WbFbsStockOverride.nomenclature_id, WbFbsStockOverride.qty).where(
            WbFbsStockOverride.project_id == project_id,
            WbFbsStockOverride.wb_warehouse_id == wb_warehouse_id,
        )
    )
    return {int(n): int(q) for n, q in result.all()}


class TestSetOverrides:
    @pytest.mark.asyncio
    async def test_bulk_set_and_update(self, db_session, project, fbs_env):
        """Массовое проставление по выделенным строкам + повтор перезаписывает."""
        _wh, fbs_wh = fbs_env
        noms = [await _mk_nom(db_session, project.id, chrt_id=8200 + i) for i in range(3)]
        await db_session.commit()

        affected = await stock_service.set_overrides(
            db_session, project.id, fbs_wh.wb_warehouse_id, [n.id for n in noms], 7
        )
        assert affected == 3
        assert set((await _overrides(db_session, project.id, fbs_wh.wb_warehouse_id)).values()) == {7}

        await stock_service.set_overrides(db_session, project.id, fbs_wh.wb_warehouse_id, [noms[0].id], 2)
        stored = await _overrides(db_session, project.id, fbs_wh.wb_warehouse_id)
        assert stored[noms[0].id] == 2
        assert stored[noms[1].id] == 7

    @pytest.mark.asyncio
    async def test_duplicate_ids_do_not_break_upsert(self, db_session, project, fbs_env):
        """Дубли в списке дедуплицируются ДО executemany (CardinalityViolation)."""
        _wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=8210)
        await db_session.commit()

        assert await stock_service.set_overrides(
            db_session, project.id, fbs_wh.wb_warehouse_id, [nom.id, nom.id, nom.id], 3
        ) == 1
        assert await _overrides(db_session, project.id, fbs_wh.wb_warehouse_id) == {nom.id: 3}

    @pytest.mark.asyncio
    async def test_per_item_sets_own_qty_to_each(self, db_session, project, fbs_env):
        """Форма `items`: каждому товару своё число — как «проставить из зеркала ФФ».

        Одним `qty` это не выразить, а слать по запросу на позицию нельзя:
        `rate_limit_write` режет такие серии.
        """
        _wh, fbs_wh = fbs_env
        noms = [await _mk_nom(db_session, project.id, chrt_id=8220 + i) for i in range(3)]
        await db_session.commit()

        payload = FbsOverrideSet(
            wb_warehouse_id=fbs_wh.wb_warehouse_id,
            items=[
                {"nomenclature_id": noms[0].id, "qty": 5},
                {"nomenclature_id": noms[1].id, "qty": 0},
                {"nomenclature_id": noms[2].id, "qty": 120},
            ],
        )
        assert await stock_service.set_overrides(db_session, project.id, payload) == 3

        stored = await _overrides(db_session, project.id, fbs_wh.wb_warehouse_id)
        assert stored == {noms[0].id: 5, noms[1].id: 0, noms[2].id: 120}

    @pytest.mark.asyncio
    async def test_per_item_rejects_foreign_nomenclature(self, db_session, project, other_project, fbs_env):
        """Чужая номенклатура не проставляется (multi-tenancy на входе мутации)."""
        _wh, fbs_wh = fbs_env
        mine = await _mk_nom(db_session, project.id, chrt_id=8230)
        alien = await _mk_nom(db_session, other_project.id, chrt_id=8231)
        await db_session.commit()

        payload = FbsOverrideSet(
            wb_warehouse_id=fbs_wh.wb_warehouse_id,
            items=[
                {"nomenclature_id": mine.id, "qty": 4},
                {"nomenclature_id": alien.id, "qty": 99},
            ],
        )
        assert await stock_service.set_overrides(db_session, project.id, payload) == 1
        assert await _overrides(db_session, project.id, fbs_wh.wb_warehouse_id) == {mine.id: 4}

    def test_two_body_forms_are_mutually_exclusive(self):
        """Схема не даёт прислать обе формы разом — иначе неясно, что применять."""
        with pytest.raises(ValidationError):
            FbsOverrideSet(wb_warehouse_id=1, nomenclature_ids=[1], items=[{"nomenclature_id": 1, "qty": 2}])
        with pytest.raises(ValidationError):
            FbsOverrideSet(wb_warehouse_id=1)

    @pytest.mark.asyncio
    async def test_clearing_deletes_rows(self, db_session, project, fbs_env):
        _wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=8211)
        await db_session.commit()

        await stock_service.set_overrides(db_session, project.id, fbs_wh.wb_warehouse_id, [nom.id], 5)
        await stock_service.set_overrides(db_session, project.id, fbs_wh.wb_warehouse_id, [nom.id], None)
        assert await _overrides(db_session, project.id, fbs_wh.wb_warehouse_id) == {}

    @pytest.mark.asyncio
    async def test_foreign_nomenclature_is_ignored(self, db_session, project, other_project, fbs_env):
        """Чужая номенклатура не попадает в настройку — multi-tenancy на входе."""
        _wh, fbs_wh = fbs_env
        mine = await _mk_nom(db_session, project.id, chrt_id=8212)
        alien = await _mk_nom(db_session, other_project.id, chrt_id=8213)
        await db_session.commit()

        affected = await stock_service.set_overrides(
            db_session, project.id, fbs_wh.wb_warehouse_id, [mine.id, alien.id], 6
        )
        assert affected == 1
        assert await _overrides(db_session, project.id, fbs_wh.wb_warehouse_id) == {mine.id: 6}

    @pytest.mark.asyncio
    async def test_unknown_wb_warehouse_raises(self, db_session, project):
        """Ручное количество на чужой склад продавца — строка-призрак, отказ."""
        nom = await _mk_nom(db_session, project.id, chrt_id=8214)
        await db_session.commit()
        with pytest.raises(ValueError, match="не найден"):
            await stock_service.set_overrides(db_session, project.id, 987654321, [nom.id], 1)

    @pytest.mark.asyncio
    async def test_overrides_are_per_wb_warehouse(self, db_session, project):
        """Настройка живёт на паре (склад продавца, товар) — соседний склад не задевает."""
        wh = await _mk_warehouse(db_session, project.id)
        wh2 = await _mk_warehouse(db_session, project.id)
        fbs_a = await _mk_fbs_warehouse(db_session, project.id)
        fbs_b = await _mk_fbs_warehouse(db_session, project.id)
        await _mk_link(db_session, project.id, fbs_a, wh)
        await _mk_link(db_session, project.id, fbs_b, wh2)
        nom = await _mk_nom(db_session, project.id, chrt_id=8215)
        await _mk_stock(db_session, project.id, wh, nom, 20)
        await _mk_stock(db_session, project.id, wh2, nom, 20)
        await db_session.commit()

        await stock_service.set_overrides(db_session, project.id, fbs_a.wb_warehouse_id, [nom.id], 1)

        assert (await _row_for(db_session, project.id, fbs_a, nom))["qty_available"] == 1
        assert (await _row_for(db_session, project.id, fbs_b, nom))["qty_available"] == 20


# ═════════════════════════════════════════════════════════════════════════════
# Фильтр по категории: разрезы строки
# ═════════════════════════════════════════════════════════════════════════════


class TestRowFacets:
    @pytest.mark.asyncio
    async def test_row_carries_brand_subject_and_subcategory(self, db_session, project, fbs_env):
        """Бренд / предмет / товарная под-категория — по ним фильтруют и выделяют.

        Товарная категория — это `product_subcategories` + связка по `nm_id`,
        а НЕ финансовая `category_ref` (категории ДДС к товарам отношения не имеют).
        """
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=8300, brand="Газик", subject="Диваны")
        await _mk_stock(db_session, project.id, wh, nom, 5)
        sub = ProductSubcategory(project_id=project.id, name=f"Кат-{_uid()}")
        db_session.add(sub)
        await db_session.flush()
        db_session.add(
            ProductSubcategoryMap(project_id=project.id, subcategory_id=sub.id, nm_id=nom.article_wb)
        )
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert (row["brand"], row["subject"]) == ("Газик", "Диваны")
        assert row["subcategory_id"] == sub.id
        assert row["subcategory_name"] == sub.name


# ═════════════════════════════════════════════════════════════════════════════
# FBO-гейт: отдаём только то, чего нет на складах WB
# ═════════════════════════════════════════════════════════════════════════════


async def _set_project_setting(db: AsyncSession, project_id: int, key: str, value: str) -> None:
    db.add(ProjectSetting(project_id=project_id, key=key, value=value))
    await db.flush()


class TestFboGate:
    @pytest.mark.asyncio
    async def test_no_mirror_means_none_not_zero(self, db_session, project, fbs_env):
        """Пустое зеркало FBO → `fbo_qty = None` и гейт НЕ применяется.

        Ключевой инвариант: с нулём вместо None порог `fbo_max_qty = 0`
        пропустил бы в FBS весь каталог, включая товар, которого на WB полно.
        """
        wh, fbs_wh = fbs_env
        fbs_wh.fbo_max_qty = 0
        nom = await _mk_nom(db_session, project.id, chrt_id=8400)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["fbo_qty"] is None
        assert row["qty_available"] == 10
        assert row["blocked_reason"] is None

    @pytest.mark.asyncio
    async def test_gate_blocks_position_above_threshold(self, db_session, project, fbs_env):
        """На FBO ещё лежит товар — в FBS его не отдаём."""
        wh, fbs_wh = fbs_env
        fbs_wh.fbo_max_qty = 0
        nom = await _mk_nom(db_session, project.id, chrt_id=8401)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        await _mk_remains(db_session, project.id, nom, 4)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["fbo_qty"] == 4
        assert row["qty_available"] == 0
        assert row["blocked_reason"] == "fbo_in_stock"

    @pytest.mark.asyncio
    async def test_gate_passes_position_at_threshold(self, db_session, project, fbs_env):
        """FBO кончился (или в пределах порога) — отдаём в FBS."""
        wh, fbs_wh = fbs_env
        fbs_wh.fbo_max_qty = 5
        nom = await _mk_nom(db_session, project.id, chrt_id=8402)
        other = await _mk_nom(db_session, project.id, chrt_id=8403)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        await _mk_stock(db_session, project.id, wh, other, 10)
        await _mk_remains(db_session, project.id, nom, 5)  # ровно порог — проходит
        await _mk_remains(db_session, project.id, other, 6)  # выше порога — блок
        await db_session.commit()

        assert (await _row_for(db_session, project.id, fbs_wh, nom))["qty_available"] == 10
        assert (await _row_for(db_session, project.id, fbs_wh, other))["qty_available"] == 0

    @pytest.mark.asyncio
    async def test_gate_off_when_threshold_is_null(self, db_session, project, fbs_env):
        """`fbo_max_qty = NULL` — на FBO не смотрим вовсе, но цифру показываем."""
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=8404)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        await _mk_remains(db_session, project.id, nom, 999)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["fbo_qty"] == 999
        assert row["qty_available"] == 10

    @pytest.mark.asyncio
    async def test_burnt_warehouse_is_not_counted(self, db_session, project, fbs_env):
        """🔥 «Остатки не учитывать»: WB отдаёт сгоревший склад как живой.

        Верить ему нельзя — иначе гейт решил бы «на FBO ещё полно» и товар
        навсегда остался бы вне FBS.
        """
        wh, fbs_wh = fbs_env
        fbs_wh.fbo_max_qty = 0
        nom = await _mk_nom(db_session, project.id, chrt_id=8405)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        await _mk_remains(db_session, project.id, nom, 50, warehouse_name="Шушары")
        await _set_project_setting(db_session, project.id, "stock_ignored_warehouses", '["Шушары"]')
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["fbo_qty"] == 0
        assert row["qty_available"] == 10

    @pytest.mark.asyncio
    async def test_excluded_warehouse_is_not_counted(self, db_session, project, fbs_env):
        """Исключённые склады в FBO-остаток не входят (настройка хранится без «(…)»)."""
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=8406)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        await _mk_remains(db_session, project.id, nom, 30, warehouse_name="Рязань (Тюшевское)")
        await _set_project_setting(db_session, project.id, "excluded_warehouses", '["Рязань"]')
        await db_session.commit()

        assert (await _row_for(db_session, project.id, fbs_wh, nom))["fbo_qty"] == 0

    @pytest.mark.asyncio
    async def test_transit_pseudo_warehouses_are_not_counted(self, db_session, project, fbs_env):
        """«В пути» и итоговая строка — не доступный остаток, а транзит и дубль суммы."""
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=8407)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        await _mk_remains(db_session, project.id, nom, 3, warehouse_name="Коледино")
        await _mk_remains(db_session, project.id, nom, 40, warehouse_name="В пути до получателей")
        await _mk_remains(db_session, project.id, nom, 7, warehouse_name="в пути возвраты на склад WB")
        await _mk_remains(db_session, project.id, nom, 43, warehouse_name="Всего находится на складах")
        await db_session.commit()

        assert (await _row_for(db_session, project.id, fbs_wh, nom))["fbo_qty"] == 3

    @pytest.mark.asyncio
    async def test_falls_back_to_wb_warehouse_stocks(self, db_session, project, fbs_env):
        """Нет ни одной строки remains → берём суточное `wb_warehouse_stocks` по nm_id.

        Без фолбэка гейт молча считал бы, что на WB пусто, и отдал бы в FBS всё
        (на локальной базе remains пуст, а stocks — десятки тысяч строк).
        """
        wh, fbs_wh = fbs_env
        fbs_wh.fbo_max_qty = 0
        nom = await _mk_nom(db_session, project.id, chrt_id=8408)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        await _mk_wb_stock(db_session, project.id, nom, 8)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["fbo_qty"] == 8
        assert row["qty_available"] == 0

    @pytest.mark.asyncio
    async def test_remains_wins_over_stocks(self, db_session, project, fbs_env):
        """Есть remains — суточный `wb_warehouse_stocks` не подмешивается."""
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=8409)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        await _mk_remains(db_session, project.id, nom, 2)
        await _mk_wb_stock(db_session, project.id, nom, 100)
        await db_session.commit()

        assert (await _row_for(db_session, project.id, fbs_wh, nom))["fbo_qty"] == 2

    @pytest.mark.asyncio
    async def test_gate_and_override_coexist(self, db_session, project, fbs_env):
        """Явное «не отдавать» называется первым: в UI показывается одна причина."""
        wh, fbs_wh = fbs_env
        fbs_wh.fbo_max_qty = 0
        nom = await _mk_nom(db_session, project.id, chrt_id=8410)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        await _mk_remains(db_session, project.id, nom, 5)
        await db_session.commit()

        await stock_service.set_overrides(db_session, project.id, fbs_wh.wb_warehouse_id, [nom.id], 0)
        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["qty_available"] == 0
        assert row["blocked_reason"] == "override_zero"

    @pytest.mark.asyncio
    async def test_fbo_is_project_scoped(self, db_session, project, other_project, fbs_env):
        """Остаток FBO чужого проекта в гейт не подмешивается."""
        wh, fbs_wh = fbs_env
        fbs_wh.fbo_max_qty = 0
        nom = await _mk_nom(db_session, project.id, chrt_id=8411)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        alien = await _mk_nom(db_session, other_project.id, chrt_id=8412)
        await _mk_remains(db_session, other_project.id, alien, 90)
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["fbo_qty"] is None, "в НАШЕМ проекте зеркала нет — гейт выключен"
        assert row["qty_available"] == 10

    @pytest.mark.asyncio
    async def test_remains_row_with_foreign_barcode_counted_by_nm(self, db_session, project, fbs_env):
        """Баркод строки remains не заведён в номенклатуре → добираем фолбэком по nm_id.

        Канон `warehouse_stock_engine` делает ДВА джойна: по баркоду плюс по
        `nm_id` для строк, чей баркод в номенклатуре отсутствует. Без второго
        джойна остаток такой строки терялся целиком, и позиция с живым товаром
        на FBO уезжала в FBS — ровно тот отказ, от которого гейт защищает.
        Расхождение ШК у нас реальность, а не гипотеза.
        """
        wh, fbs_wh = fbs_env
        fbs_wh.fbo_max_qty = 0
        nom = await _mk_nom(db_session, project.id, chrt_id=8413)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        db_session.add(
            WbWarehouseRemains(
                project_id=project.id,
                nm_id=nom.article_wb,
                barcode="ЧУЖОЙ-ШК",  # в Nomenclature такого баркода нет
                warehouse_name="Коледино",
                quantity=100,
            )
        )
        await db_session.commit()

        row = await _row_for(db_session, project.id, fbs_wh, nom)
        assert row["fbo_qty"] == 100
        assert row["qty_available"] == 0
        assert row["blocked_reason"] == stock_service.BLOCKED_FBO_IN_STOCK

    @pytest.mark.asyncio
    async def test_remains_barcode_and_nm_fallback_do_not_double_count(self, db_session, project, fbs_env):
        """Ветки джойна непересекающиеся: строку, взятую по баркоду, фолбэк не повторяет."""
        wh, fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=8414)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        await _mk_remains(db_session, project.id, nom, 7)
        await db_session.commit()

        assert (await _row_for(db_session, project.id, fbs_wh, nom))["fbo_qty"] == 7

    @pytest.mark.asyncio
    async def test_stocks_fallback_skips_multi_barcode_card(self, db_session, project, fbs_env):
        """Фолбэк по nm_id не судит о размерах: у карточки с N баркодами гейт выключается.

        `wb_warehouse_stocks` уникальна по (project, nm_id, warehouse_name) —
        размерности в ней нет. Раздав остаток КАЖДОМУ размеру, мы блокировали бы
        размер, которого на FBO уже нет: он навсегда остался бы вне FBS, хотя
        ровно ради него фичу и делали.
        """
        wh, fbs_wh = fbs_env
        fbs_wh.fbo_max_qty = 0
        size_s = await _mk_nom(db_session, project.id, chrt_id=8415)
        size_m = await _mk_nom(db_session, project.id, chrt_id=8416)
        size_m.article_wb = size_s.article_wb  # один nm_id, два баркода-размера
        await _mk_stock(db_session, project.id, wh, size_s, 10)
        await _mk_stock(db_session, project.id, wh, size_m, 10)
        await _mk_wb_stock(db_session, project.id, size_s, 40)
        await db_session.commit()

        for nom in (size_s, size_m):
            row = await _row_for(db_session, project.id, fbs_wh, nom)
            assert row["fbo_qty"] is None, "судить по nm-остатку о конкретном размере нечем"
            assert row["qty_available"] == 10
            assert row["blocked_reason"] is None


class TestFboNameFilter:
    """Чистый фильтр имён складов — без БД."""

    def test_drops_total_transit_ignored_and_excluded(self):
        names = [
            "Коледино",
            "Всего находится на складах",
            "В пути до получателей",
            "в пути возвраты на склад WB",
            "Шушары",
            "Рязань (Тюшевское)",
        ]
        kept = stock_service._fbo_allowed_names(names, ignored={"Шушары"}, excluded={"Рязань"})
        assert kept == ["Коледино"]

    def test_keeps_everything_by_default(self):
        assert stock_service._fbo_allowed_names(["Коледино", "Казань"], set(), set()) == [
            "Коледино",
            "Казань",
        ]

    def test_drops_sorting_centres(self):
        """СЦ — перевалка: товар там расписан по заказам и к продаже не доступен.

        Матчим ПРЕФИКС со следующим пробелом: без него под правило попали бы
        обычные склады на те же буквы («Сарапул», «Самара»), и живой остаток
        FBO молча пропал бы из расчёта.
        """
        names = [
            "СЦ Ижевск", "СЦ Чита 2", "SC Tbilisi",
            "Сарапул", "Самара (Новосемейкино)", "Коледино",
        ]
        assert stock_service._fbo_allowed_names(names, set(), set()) == [
            "Сарапул",
            "Самара (Новосемейкино)",
            "Коледино",
        ]

    def test_sorting_centre_check_is_case_insensitive(self):
        assert stock_service._is_sorting_centre("сц Липецк") is True
        assert stock_service._is_sorting_centre("СЦ Брянск 2") is True
        assert stock_service._is_sorting_centre("Сарапул") is False
        assert stock_service._is_sorting_centre("Сочи") is False


class TestBlockedReasonContract:
    def test_blocked_reason_codes_are_stable(self):
        """`blocked_reason` — машинные коды: человеческий текст живёт во фронте.

        Список зафиксирован здесь и в `src/__tests__/lib/fbsStockOverride.test.ts`
        (`BACKEND_BLOCKED_REASONS`): новый код обязан появиться в обоих местах,
        иначе словарь `BLOCKED_REASON_LABEL` промахнётся, и в колонке «Причина»
        — а заодно в Excel-выгрузке — вылезет сырой код. Ровно так разъехались
        `fbo_in_stock` (бэкенд) и `fbo_present` (фронт).
        """
        assert stock_service.BLOCKED_REASONS == ("no_chrt", "override_zero", "fbo_in_stock")


# ═════════════════════════════════════════════════════════════════════════════
# Режим склада: observe не пишет в WB никогда
# ═════════════════════════════════════════════════════════════════════════════


class TestWarehouseMode:
    @pytest.mark.asyncio
    async def test_observe_never_puts(self, db_session, project, fake_client):
        """Режим наблюдения: считаем и показываем, но в кабинет не пишем.

        Гейт стоит в СЕРВИСЕ, а не в роутере: джоб зовёт `push_stocks` напрямую
        и роутерную проверку обошёл бы — подключение к складу с ручными
        остатками не должно перезаписать их без явного включения трансляции.
        """
        wh = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id, mode=FbsWarehouseMode.OBSERVE.value)
        await _mk_link(db_session, project.id, fbs_wh, wh)
        nom = await _mk_nom(db_session, project.id, chrt_id=8500)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        await db_session.commit()

        assert await stock_service.push_stocks(db_session, project.id) == []
        assert fake_client.puts == []

    @pytest.mark.asyncio
    async def test_observe_still_computes_preview(self, db_session, project, fake_client):
        """Расчёт в observe работает — иначе не увидеть расхождение «в WB / у нас»."""
        wh = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id, mode=FbsWarehouseMode.OBSERVE.value)
        await _mk_link(db_session, project.id, fbs_wh, wh)
        nom = await _mk_nom(db_session, project.id, chrt_id=8501)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        await db_session.commit()

        assert (await _row_for(db_session, project.id, fbs_wh, nom))["qty_available"] == 10

    @pytest.mark.asyncio
    async def test_explicit_push_leaves_a_journal_reason(self, db_session, project, fake_client):
        """Явный запрос на observe-склад обязан оставить след, почему пусто."""
        wh = await _mk_warehouse(db_session, project.id)
        fbs_wh = await _mk_fbs_warehouse(db_session, project.id, mode=FbsWarehouseMode.OBSERVE.value)
        await _mk_link(db_session, project.id, fbs_wh, wh)
        nom = await _mk_nom(db_session, project.id, chrt_id=8502)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        await db_session.commit()

        push_ids = await stock_service.push_stocks(
            db_session, project.id, wb_warehouse_ids=[fbs_wh.wb_warehouse_id], trigger="manual"
        )
        assert len(push_ids) == 1
        push = (
            await db_session.execute(select(WbFbsStockPush).where(WbFbsStockPush.id == push_ids[0]))
        ).scalar_one()
        assert push.status == FbsPushStatus.ERROR.value
        assert "наблюдение" in (push.error_msg or "")
        assert fake_client.puts == []

    @pytest.mark.asyncio
    async def test_translate_mode_pushes(self, db_session, project, fbs_env, fake_client):
        """Контроль: с `translate` тот же стенд отправляет остаток."""
        wh, _fbs_wh = fbs_env
        nom = await _mk_nom(db_session, project.id, chrt_id=8503)
        await _mk_stock(db_session, project.id, wh, nom, 10)
        await db_session.commit()

        with patch("backend.cache.get_redis", AsyncMock(return_value=None)):
            await stock_service.push_stocks(db_session, project.id)
        assert fake_client.sent_map == {8503: 10}
