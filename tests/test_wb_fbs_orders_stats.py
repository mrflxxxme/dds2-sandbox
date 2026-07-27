"""
Статистика заказов FBS за период: выручка, разрезы, доля в объёме воронки.

Что здесь защищается:
  • сумма считается по `sale_price` с фолбэком на `price` — та же формула, что в
    листе подбора; разойдись они, две страницы показывали бы разные деньги;
  • отменённые ВХОДЯТ в основную цифру (воронка их тоже не вычитает) и при этом
    видны отдельной парой полей;
  • сутки московские: заказ в 23:30 UTC — это уже следующий день по МСК, и без
    приведения дневная разбивка не сошлась бы с воронкой;
  • бренд берётся из номенклатуры (в задании его нет), предмет — из снимка;
  • доля FBS считается от воронки, а пустая воронка обязана давать `has_data=False`,
    иначе деление на ноль показало бы «100 % FBS» на отсутствующих данных;
  • изоляция по проекту и по контуру: задания песочницы не попадают в выручку.
"""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.cost import Nomenclature
from backend.models.integrations import WbFunnelDaily
from backend.models.refs import ProductSubcategory, ProductSubcategoryMap
from backend.models.wb_fbs import WbFbsOrder
from backend.services.wb_fbs.orders_stats import orders_stats, resolve_period

WB_WAREHOUSE_ID = 1775123


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _mk_nom(db: AsyncSession, project_id: int, *, brand: str, subject: str) -> Nomenclature:
    nom = Nomenclature(
        project_id=project_id,
        barcode=f"ST{_uid()}",
        article_seller=f"ART-{_uid()}",
        article_wb=int(uuid.uuid4().int % 9_000_000) + 1,
        brand=brand,
        subject=subject,
    )
    db.add(nom)
    await db.flush()
    return nom


async def _mk_order(
    db: AsyncSession,
    project_id: int,
    *,
    created_at: datetime,
    nom: Nomenclature | None = None,
    sale_price: Decimal | None = Decimal("100"),
    price: Decimal | None = None,
    supplier_status: str = "new",
    wb_status: str | None = None,
    subject: str | None = None,
    contour: str | None = None,
) -> WbFbsOrder:
    raw: dict = {}
    if contour:
        raw["_dds_contour"] = contour
    order = WbFbsOrder(
        project_id=project_id,
        wb_order_id=int(uuid.uuid4().int % 900_000_000) + 1,
        wb_warehouse_id=WB_WAREHOUSE_ID,
        created_at_wb=created_at,
        nomenclature_id=nom.id if nom else None,
        nm_id=nom.article_wb if nom else None,
        barcode=nom.barcode if nom else None,
        subject=subject,
        sale_price=sale_price,
        price=price,
        supplier_status=supplier_status,
        wb_status=wb_status,
        raw=raw,
    )
    db.add(order)
    await db.flush()
    return order


@pytest_asyncio.fixture
async def noon_utc() -> datetime:
    """Полдень UTC вчера — заведомо внутри периода по умолчанию и вдали от границ суток."""
    return datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=1)


class TestTotals:
    @pytest.mark.asyncio
    async def test_revenue_falls_back_to_price(self, db_session, project, noon_utc):
        """`salePrice` пуст — платим по `price`; иначе строка молча уходит в ноль."""
        nom = await _mk_nom(db_session, project.id, brand="Уютопия", subject="Ковры")
        await _mk_order(db_session, project.id, created_at=noon_utc, nom=nom, sale_price=Decimal("250"))
        await _mk_order(
            db_session, project.id, created_at=noon_utc, nom=nom,
            sale_price=None, price=Decimal("400"),
        )
        await db_session.commit()

        stats = await orders_stats(db_session, project.id)

        assert stats["orders_count"] == 2
        assert stats["orders_sum"] == Decimal("650")

    @pytest.mark.asyncio
    async def test_cancelled_counted_in_total_and_separately(self, db_session, project, noon_utc):
        """Отменённые входят в общую цифру (как в воронке) и видны отдельно."""
        nom = await _mk_nom(db_session, project.id, brand="Уютопия", subject="Ковры")
        await _mk_order(db_session, project.id, created_at=noon_utc, nom=nom, sale_price=Decimal("300"))
        await _mk_order(
            db_session, project.id, created_at=noon_utc, nom=nom,
            sale_price=Decimal("100"), supplier_status="cancel_carrier",
        )
        await db_session.commit()

        stats = await orders_stats(db_session, project.id)

        assert stats["orders_count"] == 2
        assert stats["orders_sum"] == Decimal("400")
        assert stats["cancelled_count"] == 1
        assert stats["cancelled_sum"] == Decimal("100")


class TestCuts:
    @pytest.mark.asyncio
    async def test_brand_comes_from_nomenclature(self, db_session, project, noon_utc):
        """Бренда в задании нет — только через номенклатуру по FK."""
        cosy = await _mk_nom(db_session, project.id, brand="Уютопия", subject="Ковры")
        nunu = await _mk_nom(db_session, project.id, brand="НУ-НУ", subject="Ковры")
        await _mk_order(db_session, project.id, created_at=noon_utc, nom=cosy, sale_price=Decimal("900"))
        await _mk_order(db_session, project.id, created_at=noon_utc, nom=nunu, sale_price=Decimal("100"))
        await db_session.commit()

        stats = await orders_stats(db_session, project.id)

        # По убыванию суммы — самый денежный бренд первым.
        assert [(r["label"], r["orders_sum"]) for r in stats["by_brand"]] == [
            ("Уютопия", Decimal("900")),
            ("НУ-НУ", Decimal("100")),
        ]

    @pytest.mark.asyncio
    async def test_order_without_nomenclature_lands_in_no_brand(self, db_session, project, noon_utc):
        """Задание без карточки не теряется: уходит в «Без бренда», а не пропадает."""
        await _mk_order(db_session, project.id, created_at=noon_utc, nom=None, sale_price=Decimal("500"))
        await db_session.commit()

        stats = await orders_stats(db_session, project.id)

        assert stats["orders_count"] == 1
        assert [r["label"] for r in stats["by_brand"]] == ["Без бренда"]

    @pytest.mark.asyncio
    async def test_subject_prefers_order_snapshot(self, db_session, project, noon_utc):
        """Предмет — снимок на момент заказа: переименование карточки не двигает прошлое."""
        nom = await _mk_nom(db_session, project.id, brand="Уютопия", subject="Новое имя")
        await _mk_order(
            db_session, project.id, created_at=noon_utc, nom=nom,
            sale_price=Decimal("100"), subject="Имя на момент заказа",
        )
        await db_session.commit()

        stats = await orders_stats(db_session, project.id)

        assert [r["label"] for r in stats["by_subject"]] == ["Имя на момент заказа"]

    @pytest.mark.asyncio
    async def test_deleted_subcategory_does_not_leak(self, db_session, project, noon_utc):
        """Удалённая под-категория выпадает в «Без под-категории», а не тянет мёртвый ярлык."""
        nom = await _mk_nom(db_session, project.id, brand="Уютопия", subject="Ковры")
        subcat = ProductSubcategory(project_id=project.id, name=f"Винтаж-{_uid()}", is_deleted=True)
        db_session.add(subcat)
        await db_session.flush()
        db_session.add(
            ProductSubcategoryMap(project_id=project.id, subcategory_id=subcat.id, nm_id=nom.article_wb)
        )
        await _mk_order(db_session, project.id, created_at=noon_utc, nom=nom, sale_price=Decimal("100"))
        await db_session.commit()

        stats = await orders_stats(db_session, project.id)

        assert [r["label"] for r in stats["by_subcategory"]] == ["Без под-категории"]

    @pytest.mark.asyncio
    async def test_status_cut_matches_cancelled_kpi(self, db_session, project, noon_utc):
        """Разрез «По статусам» считает ЭФФЕКТИВНЫЙ статус — как и шапка блока.

        Отказ покупателя до сборки оставляет `supplierStatus = new` навсегда.
        По сырому полю такое задание попадало в «Отменено, шт» вверху и
        одновременно в строку «Новое» в разрезе — две цифры об одном и том же.
        """
        nom = await _mk_nom(db_session, project.id, brand="Уютопия", subject="Ковры")
        await _mk_order(db_session, project.id, created_at=noon_utc, nom=nom, sale_price=Decimal("300"))
        await _mk_order(
            db_session, project.id, created_at=noon_utc, nom=nom,
            sale_price=Decimal("100"), wb_status="declined_by_client",
        )
        await db_session.commit()

        stats = await orders_stats(db_session, project.id)

        by_status = {r["label"]: r for r in stats["by_status"]}
        assert set(by_status) == {"new", "cancel"}
        assert by_status["cancel"]["orders_count"] == stats["cancelled_count"] == 1
        assert by_status["cancel"]["orders_sum"] == stats["cancelled_sum"] == Decimal("100")


class TestMoscowDays:
    @pytest.mark.asyncio
    async def test_late_utc_order_belongs_to_next_msk_day(self, db_session, project):
        """23:30 UTC — это 02:30 МСК следующих суток.

        Без приведения зон дневная разбивка разъехалась бы с воронкой WB, которая
        отчитывается московскими сутками.
        """
        nom = await _mk_nom(db_session, project.id, brand="Уютопия", subject="Ковры")
        utc_late = datetime.utcnow().replace(hour=23, minute=30, second=0, microsecond=0) - timedelta(days=2)
        await _mk_order(db_session, project.id, created_at=utc_late, nom=nom, sale_price=Decimal("100"))
        await db_session.commit()

        stats = await orders_stats(db_session, project.id)

        assert len(stats["by_day"]) == 1
        assert stats["by_day"][0]["day"] == (utc_late + timedelta(hours=3)).date()


class TestFunnelShare:
    @pytest.mark.asyncio
    async def test_empty_funnel_reports_no_data(self, db_session, project, noon_utc):
        """Пустая воронка — «данных нет», а не «100 % FBS»."""
        nom = await _mk_nom(db_session, project.id, brand="Уютопия", subject="Ковры")
        await _mk_order(db_session, project.id, created_at=noon_utc, nom=nom, sale_price=Decimal("100"))
        await db_session.commit()

        stats = await orders_stats(db_session, project.id)

        assert stats["funnel"]["has_data"] is False
        assert stats["funnel"]["orders_count"] == 0

    @pytest.mark.asyncio
    async def test_funnel_totals_are_the_denominator(self, db_session, project, noon_utc):
        """Знаменатель — весь объём проекта по воронке за тот же период."""
        nom = await _mk_nom(db_session, project.id, brand="Уютопия", subject="Ковры")
        await _mk_order(db_session, project.id, created_at=noon_utc, nom=nom, sale_price=Decimal("250"))
        db_session.add(
            WbFunnelDaily(
                project_id=project.id,
                date=(noon_utc + timedelta(hours=3)).date(),
                nm_id=nom.article_wb,
                orders_count=10,
                orders_sum_rub=Decimal("1000"),
            )
        )
        await db_session.commit()

        stats = await orders_stats(db_session, project.id)

        assert stats["funnel"]["has_data"] is True
        assert stats["funnel"]["orders_count"] == 10
        assert stats["funnel"]["orders_sum"] == Decimal("1000")
        # Числитель — те же дни: 1 из 10 штук, 250 из 1000 рублей.
        assert stats["funnel"]["fbs_orders_count"] == 1
        assert stats["funnel"]["fbs_orders_sum"] == Decimal("250")

    @pytest.mark.asyncio
    async def test_share_is_computed_over_the_covered_range_only(self, db_session, project):
        """Доля считается за дни, которые воронка РЕАЛЬНО покрывает.

        Прод-ловушка: воронку наполняет свой синк, и хвост периода в ней пуст.
        Полный период FBS, делённый на неполный период воронки, давал бы кратно
        завышенную долю — на живых данных 30 дней заданий против 5 дней воронки
        показывали «2 % по деньгам» вместо честного нуля.
        """
        nom = await _mk_nom(db_session, project.id, brand="Уютопия", subject="Ковры")
        old_day = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=20)
        fresh_day = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=1)
        # Заказ в покрытых воронкой днях и заказ в непокрытом хвосте.
        await _mk_order(db_session, project.id, created_at=old_day, nom=nom, sale_price=Decimal("100"))
        await _mk_order(db_session, project.id, created_at=fresh_day, nom=nom, sale_price=Decimal("900"))
        db_session.add(
            WbFunnelDaily(
                project_id=project.id,
                date=(old_day + timedelta(hours=3)).date(),
                nm_id=nom.article_wb,
                orders_count=10,
                orders_sum_rub=Decimal("1000"),
            )
        )
        await db_session.commit()

        stats = await orders_stats(db_session, project.id)
        funnel = stats["funnel"]

        assert funnel["full_period"] is False, "воронка покрывает только один день периода"
        assert funnel["covered_from"] == funnel["covered_to"] == (old_day + timedelta(hours=3)).date()
        # KPI — за весь период (оба заказа), доля — только за покрытый день.
        assert stats["orders_count"] == 2
        assert stats["orders_sum"] == Decimal("1000")
        assert funnel["fbs_orders_count"] == 1
        assert funnel["fbs_orders_sum"] == Decimal("100")

    @pytest.mark.asyncio
    async def test_full_period_coverage_is_flagged(self, db_session, project, noon_utc):
        """Воронка покрывает весь период — оговорки в интерфейсе не нужно."""
        nom = await _mk_nom(db_session, project.id, brand="Уютопия", subject="Ковры")
        await _mk_order(db_session, project.id, created_at=noon_utc, nom=nom, sale_price=Decimal("100"))
        d_from, d_to = resolve_period()
        for d in (d_from, d_to):
            db_session.add(
                WbFunnelDaily(
                    project_id=project.id, date=d, nm_id=nom.article_wb,
                    orders_count=5, orders_sum_rub=Decimal("500"),
                )
            )
        await db_session.commit()

        stats = await orders_stats(db_session, project.id)

        assert stats["funnel"]["full_period"] is True
        assert stats["funnel"]["orders_count"] == 10


class TestIsolation:
    @pytest.mark.asyncio
    async def test_other_project_orders_are_invisible(self, db_session, project, other_project, noon_utc):
        nom = await _mk_nom(db_session, project.id, brand="Уютопия", subject="Ковры")
        alien = await _mk_nom(db_session, other_project.id, brand="Чужой", subject="Ковры")
        await _mk_order(db_session, project.id, created_at=noon_utc, nom=nom, sale_price=Decimal("100"))
        await _mk_order(db_session, other_project.id, created_at=noon_utc, nom=alien, sale_price=Decimal("999"))
        await db_session.commit()

        stats = await orders_stats(db_session, project.id)

        assert stats["orders_count"] == 1
        assert stats["orders_sum"] == Decimal("100")

    @pytest.mark.asyncio
    async def test_sandbox_orders_excluded_from_prod_revenue(self, db_session, project, noon_utc):
        """Задания песочницы не должны попадать в боевую выручку."""
        nom = await _mk_nom(db_session, project.id, brand="Уютопия", subject="Ковры")
        await _mk_order(db_session, project.id, created_at=noon_utc, nom=nom, sale_price=Decimal("100"))
        await _mk_order(
            db_session, project.id, created_at=noon_utc, nom=nom,
            sale_price=Decimal("777"), contour="sandbox",
        )
        await db_session.commit()

        stats = await orders_stats(db_session, project.id)

        assert stats["orders_count"] == 1
        assert stats["orders_sum"] == Decimal("100")

    @pytest.mark.asyncio
    async def test_warehouse_filter_narrows_orders(self, db_session, project, noon_utc):
        """Фильтр по складу продавца режет заказы, но не знаменатель воронки."""
        nom = await _mk_nom(db_session, project.id, brand="Уютопия", subject="Ковры")
        ours = await _mk_order(db_session, project.id, created_at=noon_utc, nom=nom, sale_price=Decimal("100"))
        other = await _mk_order(db_session, project.id, created_at=noon_utc, nom=nom, sale_price=Decimal("900"))
        other.wb_warehouse_id = WB_WAREHOUSE_ID + 1
        await db_session.commit()

        stats = await orders_stats(db_session, project.id, wb_warehouse_id=ours.wb_warehouse_id)

        assert stats["orders_count"] == 1
        assert stats["orders_sum"] == Decimal("100")


class TestPeriod:
    def test_default_is_last_30_days(self):
        d_from, d_to = resolve_period()
        assert (d_to - d_from).days == 29

    def test_swapped_dates_are_reordered(self):
        d_from, d_to = resolve_period(date(2026, 7, 20), date(2026, 7, 1))
        assert d_from == date(2026, 7, 1)
        assert d_to == date(2026, 7, 20)

    def test_absurd_range_is_capped(self):
        """Запрос «с 1970 года» не должен класть базу."""
        d_from, d_to = resolve_period(date(1970, 1, 1), date(2026, 7, 20))
        assert (d_to - d_from).days == 400
