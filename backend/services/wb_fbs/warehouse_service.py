# ruff: noqa: RUF002, RUF003
"""
Склады продавца WB (FBS) и их привязка к нашим складам.

Домен: справочник `wb_fbs_warehouses` — зеркало `GET /api/v3/warehouses`
кабинета WB плюс НАШИ настройки трансляции остатков (тумблер, источник,
буфер, потолок). Привязки `wb_fbs_warehouse_links` отвечают на вопрос
«из каких наших складов кормится этот склад продавца».

Инварианты:
  • Один наш склад — максимум ОДИН активный склад продавца WB. Иначе один и
    тот же физический остаток уехал бы в два кабинета = двойная продажа.
    В БД это partial unique index `uq_wb_fbs_link_warehouse_active`; здесь
    проверяем заранее, чтобы отдать внятную ошибку, а не IntegrityError 500.
  • Обратное направление разрешено: один склад WB ← N наших складов
    (остатки суммируются, см. stock_service).
  • Синк НЕ трогает наши настройки — обновляет только поля со стороны WB.
  • Режим склада (`mode`) по умолчанию `observe`: подключение к складу, где
    остатки ведут руками, не должно ничего перезаписать. Запись в кабинет
    включается явно (`translate`), гейт стоит в `stock_service.push_stocks`.
  • В WB не ходим с открытой транзакцией: `db.commit()` до HTTP
    (иначе pgbouncer-коннект висит `idle in transaction`).
"""

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached, invalidate_cache
from backend.models.fulfillment import FulfillmentStock
from backend.models.integrations import IntegrationKey
from backend.models.warehouse import Warehouse, WarehouseStock
from backend.models.wb_fbs import FbsStockSource, FbsWarehouseMode, WbFbsWarehouse, WbFbsWarehouseLink
from backend.schemas.wb_fbs import FbsWarehouseSettingsUpdate
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)


class FbsMirrorAboveLedger(Exception):
    """Гейт настроек: «translate + ff_mirror», а зеркало ФФ выше нашего учёта.

    Такая пара транслировала бы в WB остаток, которого в нашем учёте нет, —
    WB продаст, а списывать нечего. Конфликт СОСТОЯНИЯ, не ошибка ввода:
    роутер переводит в HTTP 409; `force=true` в payload'е обходит гейт
    (решение человека). `min_of_both` не гейтится — минимум не обещает
    больше учёта по построению.
    """

#: Префиксы кэша домена (зарегистрированы в backend/cache.py).
#: ВНИМАНИЕ: в самих декораторах `@cached(prefix=...)` префикс обязан стоять
#: СТРОКОВЫМ ЛИТЕРАЛОМ — гейт `tests/test_conventions_sync.py` ищет их регуляркой
#: `@cached\(\s*prefix\s*=\s*"…"` и константу не видит. Константы ниже — для
#: `invalidate_cache()`; при правке значения синхронизировать с литералами.
CACHE_WAREHOUSES = "fbs:warehouses"
CACHE_STOCK_PREVIEW = "fbs:stock_preview"


# ─── Клиент ──────────────────────────────────────────────────────────────────


async def _get_client(db: AsyncSession, project_id: int) -> Any:
    """Клиент Marketplace API проекта.

    Импорт локальный: `client_factory` — модуль соседней полосы, и держать его
    на верхнем уровне значит ронять импорт всего домена, если у клиента поменяется
    расположение. Плюс так тесты формулы не тянут за собой интеграционный слой.
    """
    from backend.services.wb_fbs.client_factory import get_fbs_client

    return await get_fbs_client(db, project_id)


# ─── Мапперы ответов WB ──────────────────────────────────────────────────────


def _map_office(raw: dict) -> dict[str, Any]:
    """`GET /api/v3/offices` → FbsOfficeOut."""
    return {
        "id": int(raw.get("id") or 0),
        "name": raw.get("name"),
        "address": raw.get("address"),
        "city": raw.get("city"),
        "cargo_type": raw.get("cargoType"),
        "delivery_type": raw.get("deliveryType"),
        "federal_district": raw.get("federalDistrict"),
        "selected": bool(raw.get("selected")),
    }


def _warehouse_to_dict(wh: WbFbsWarehouse, links: list[dict] | None = None) -> dict[str, Any]:
    """WbFbsWarehouse → FbsWarehouseOut (плоский dict, как ждёт роутер)."""
    return {
        "id": wh.id,
        "wb_warehouse_id": wh.wb_warehouse_id,
        "name": wh.name,
        "office_id": wh.office_id,
        "office_name": wh.office_name,
        "cargo_type": wh.cargo_type,
        "delivery_type": wh.delivery_type,
        "is_processing": wh.is_processing,
        "is_deleting": wh.is_deleting,
        "is_active": wh.is_active,
        "stock_source": wh.stock_source,
        "safety_stock_pct": wh.safety_stock_pct,
        "safety_stock_abs": wh.safety_stock_abs,
        "max_qty_per_sku": wh.max_qty_per_sku,
        # Режим и FBO-гейт обязаны ехать наружу: без них форма настроек
        # показывала бы дефолт схемы («observe» / «гейта нет») вместо
        # сохранённого значения и молча предлагала бы перезаписать его.
        "mode": wh.mode,
        "fbo_max_qty": wh.fbo_max_qty,
        "synced_at": wh.synced_at,
        "links": links or [],
    }


# ─── Чтение ──────────────────────────────────────────────────────────────────


@cached(prefix="fbs:warehouses", ttl=600)  # литерал: см. комментарий у CACHE_WAREHOUSES
async def _offices_cached(db: AsyncSession, project_id: int, kind: str = "offices") -> list[dict[str, Any]]:
    """Кэш-обёртка справочника офисов WB.

    `kind` существует только ради ключа кэша: `@cached` строит ключ из ИМЁН
    аргументов, а не из имени функции, поэтому без дискриминатора офисы и склады
    (обе — `(db, project_id)` под префиксом `fbs:warehouses`) затирали бы друг друга.
    """
    client = await _get_client(db, project_id)
    # Транзакцию, открытую чтением ключа интеграции, закрываем ДО HTTP.
    await db.commit()
    offices = await client.list_offices()
    return [_map_office(o) for o in offices if o.get("id")]


async def list_offices(db: AsyncSession, project_id: int) -> list[dict[str, Any]]:
    """Склады WB (пункты приёма) — справочник кабинета, своей таблицы нет.

    Меняется редко, поэтому кэшируем на 10 минут: список нужен только форме
    «создать склад продавца», а каждый вызов — это поход в WB с его лимитами.
    """
    offices: list[dict[str, Any]] = await _offices_cached(db, project_id)
    return offices


@cached(prefix="fbs:warehouses", ttl=300)  # литерал: см. комментарий у CACHE_WAREHOUSES
async def list_warehouses(db: AsyncSession, project_id: int) -> list[dict[str, Any]]:
    """Склады продавца проекта + вложенные привязки с именами наших складов.

    Два запроса, без N+1: справочник и все привязки проекта одним JOIN'ом
    к `warehouses` (мягко удалённые склады не показываем).
    """
    links_by_wb = await _links_by_wb_warehouse(db, project_id)
    result = await db.execute(
        select(WbFbsWarehouse)
        .where(WbFbsWarehouse.project_id == project_id)
        .order_by(WbFbsWarehouse.name, WbFbsWarehouse.wb_warehouse_id)
        .limit(500)
    )
    warehouses = list(result.scalars().all())
    return [_warehouse_to_dict(wh, links_by_wb.get(wh.wb_warehouse_id, [])) for wh in warehouses]


async def _links_by_wb_warehouse(db: AsyncSession, project_id: int) -> dict[int, list[dict[str, Any]]]:
    """Все привязки проекта одним JOIN'ом: {wb_warehouse_id: [FbsWarehouseLinkOut]}."""
    links_result = await db.execute(
        select(
            WbFbsWarehouseLink.id,
            WbFbsWarehouseLink.wb_warehouse_id,
            WbFbsWarehouseLink.warehouse_id,
            WbFbsWarehouseLink.is_active,
            Warehouse.name,
        )
        .join(Warehouse, Warehouse.id == WbFbsWarehouseLink.warehouse_id)
        .where(
            WbFbsWarehouseLink.project_id == project_id,
            Warehouse.is_deleted == False,  # noqa: E712 — SQLAlchemy expression
        )
        .order_by(Warehouse.name)
        .limit(2000)
    )
    rows = links_result.all()
    source_info = await _source_info(db, project_id, [r.warehouse_id for r in rows])
    links_by_wb: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        info = source_info.get(row.warehouse_id, {})
        links_by_wb.setdefault(row.wb_warehouse_id, []).append(
            {
                "id": row.id,
                "warehouse_id": row.warehouse_id,
                "warehouse_name": row.name,
                "is_active": row.is_active,
                **info,
            }
        )
    return links_by_wb


async def _source_info(
    db: AsyncSession, project_id: int, warehouse_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """Откуда берётся остаток по каждому нашему складу — определяется автоматически.

    Отдельной настройки «источник» нет и не должно быть: достаточно выбрать наш
    склад. Если у него есть зеркало WMS-провайдера — считаем консервативно,
    `min(наш учёт, зеркало)`; если зеркала нет (склад без интеграции, напр. «апл»,
    «хамза») — берём наш складской учёт.

    Признак «зеркальный склад» — наличие строк `FulfillmentStock`; это канон
    проекта (`ff_billing/storage.py`, `stock_distribution`), а не активность
    ключа: у склада может быть живое зеркало при выключенном ключе (и наоборот),
    поэтому активность отдаём отдельным флагом — чтобы UI мог предупредить
    о протухших данных.
    """
    if not warehouse_ids:
        return {}
    unique_ids = list(set(warehouse_ids))

    mirror_rows = (
        await db.execute(
            select(
                FulfillmentStock.warehouse_id,
                func.count(FulfillmentStock.id).label("rows"),
                func.min(FulfillmentStock.provider).label("provider"),
                func.max(FulfillmentStock.synced_at).label("synced_at"),
            )
            .where(
                FulfillmentStock.project_id == project_id,
                FulfillmentStock.warehouse_id.in_(unique_ids),
            )
            .group_by(FulfillmentStock.warehouse_id)
        )
    ).all()
    mirror_by_wh = {r.warehouse_id: r for r in mirror_rows}

    key_rows = (
        await db.execute(
            select(IntegrationKey.warehouse_id, func.min(IntegrationKey.service).label("service"))
            .where(
                IntegrationKey.warehouse_id.in_(unique_ids),
                IntegrationKey.is_active == True,  # noqa: E712 — SQLAlchemy expression
                IntegrationKey.is_deleted == False,  # noqa: E712 — SQLAlchemy expression
            )
            .group_by(IntegrationKey.warehouse_id)
        )
    ).all()
    key_by_wh = {r.warehouse_id: r.service for r in key_rows}

    out: dict[int, dict[str, Any]] = {}
    for wh_id in unique_ids:
        mirror = mirror_by_wh.get(wh_id)
        provider = (mirror.provider if mirror else None) or key_by_wh.get(wh_id)
        out[wh_id] = {
            "has_mirror": mirror is not None,
            "mirror_rows": int(mirror.rows) if mirror else 0,
            "mirror_synced_at": mirror.synced_at if mirror else None,
            "mirror_provider": provider,
            "integration_active": wh_id in key_by_wh,
        }
    return out


async def get_warehouse(db: AsyncSession, project_id: int, wb_warehouse_id: int) -> WbFbsWarehouse:
    """Склад продавца проекта или доменная ошибка (роутер отдаст 400/404)."""
    result = await db.execute(
        select(WbFbsWarehouse).where(
            WbFbsWarehouse.project_id == project_id,
            WbFbsWarehouse.wb_warehouse_id == wb_warehouse_id,
        )
    )
    wh = result.scalar_one_or_none()
    if wh is None:
        raise ValueError(f"Склад продавца WB {wb_warehouse_id} не найден в проекте — сначала синхронизируйте склады")
    return wh


async def get_linked_warehouse_ids(db: AsyncSession, project_id: int, wb_warehouse_id: int) -> list[int]:
    """id наших складов, кормящих этот склад продавца (только активные привязки).

    Мягко удалённый склад из выдачи ИСКЛЮЧАЕТСЯ. Иначе он продолжал кормить
    витрину WB своим остатком, при этом исчезнув из интерфейса: привязку не
    видно, снять её нечем, а товар продолжает продаваться с несуществующего
    склада — и списание при продаже уходит в тот же мёртвый остаток.
    """
    result = await db.execute(
        select(WbFbsWarehouseLink.warehouse_id)
        .join(Warehouse, Warehouse.id == WbFbsWarehouseLink.warehouse_id)
        .where(
            WbFbsWarehouseLink.project_id == project_id,
            WbFbsWarehouseLink.wb_warehouse_id == wb_warehouse_id,
            WbFbsWarehouseLink.is_active == True,  # noqa: E712 — SQLAlchemy expression
            Warehouse.is_deleted == False,  # noqa: E712 — SQLAlchemy expression
        )
        .order_by(WbFbsWarehouseLink.warehouse_id)
        .limit(100)
    )
    return [int(r) for r in result.scalars().all()]


# ─── Синк справочника ────────────────────────────────────────────────────────


async def sync_warehouses(db: AsyncSession, project_id: int) -> int:
    """Обновить справочник складов продавца из кабинета WB.

    Наши настройки трансляции (is_active/stock_source/буфер/потолок) НЕ трогаем —
    синк владеет только полями со стороны WB. Возвращает число складов в ответе WB.
    """
    client = await _get_client(db, project_id)
    await db.commit()  # не держим транзакцию через внешний HTTP

    offices = await client.list_offices()
    office_names = {int(o["id"]): o.get("name") for o in offices if o.get("id")}
    remote = await client.list_warehouses()

    existing_result = await db.execute(
        select(WbFbsWarehouse).where(WbFbsWarehouse.project_id == project_id).limit(500)
    )
    existing = {w.wb_warehouse_id: w for w in existing_result.scalars().all()}

    now = utcnow()
    for raw in remote:
        wb_id = raw.get("id")
        if not wb_id:
            continue
        wb_id = int(wb_id)
        office_id = raw.get("officeId")
        wh = existing.get(wb_id)
        if wh is None:
            wh = WbFbsWarehouse(project_id=project_id, wb_warehouse_id=wb_id)
            db.add(wh)
            existing[wb_id] = wh
        wh.name = raw.get("name")
        wh.office_id = int(office_id) if office_id else None
        wh.office_name = office_names.get(int(office_id)) if office_id else None
        wh.cargo_type = raw.get("cargoType")
        wh.delivery_type = raw.get("deliveryType")
        wh.is_processing = bool(raw.get("isProcessing"))
        wh.is_deleting = bool(raw.get("isDeleting"))
        wh.synced_at = now

    await db.commit()
    await invalidate_cache(CACHE_WAREHOUSES)
    await invalidate_cache(CACHE_STOCK_PREVIEW)
    logger.info("FBS warehouses synced: project=%s count=%s", project_id, len(remote))
    return len(remote)


# ─── Мутации на стороне WB ───────────────────────────────────────────────────


async def create_wb_warehouse(db: AsyncSession, project_id: int, name: str, office_id: int) -> int:
    """Создать склад продавца в кабинете WB и завести его локальную строку."""
    client = await _get_client(db, project_id)
    await db.commit()

    wb_id = int(await client.create_warehouse(name, office_id))

    wh = WbFbsWarehouse(
        project_id=project_id,
        wb_warehouse_id=wb_id,
        name=name,
        office_id=office_id,
        synced_at=utcnow(),
    )
    db.add(wh)
    await db.commit()
    await invalidate_cache(CACHE_WAREHOUSES)
    return wb_id


async def rename_wb_warehouse(
    db: AsyncSession,
    project_id: int,
    wb_warehouse_id: int,
    name: str,
    office_id: int | None = None,
) -> None:
    """Переименовать склад продавца (и, опционально, сменить офис).

    WB разрешает менять офис не чаще раза в сутки — ошибку от WB пробрасываем
    как есть, локальную строку правим только после успешного ответа.
    """
    wh = await get_warehouse(db, project_id, wb_warehouse_id)
    target_office = office_id if office_id is not None else wh.office_id
    client = await _get_client(db, project_id)
    await db.commit()

    await client.update_warehouse(wb_warehouse_id, name, target_office)

    wh = await get_warehouse(db, project_id, wb_warehouse_id)
    wh.name = name
    if office_id is not None:
        wh.office_id = office_id
        wh.office_name = None  # имя офиса подтянет ближайший синк
    await db.commit()
    await invalidate_cache(CACHE_WAREHOUSES)


# ─── Наши настройки трансляции ───────────────────────────────────────────────


async def _mirror_over_ledger_totals(
    db: AsyncSession, project_id: int, wb_warehouse_id: int
) -> tuple[int, int, int]:
    """(mirror_over_ledger, ledger_total, mirror_total) по активным привязкам склада.

    mirror_over_ledger = Σ per-SKU GREATEST(россыпь зеркала − учёт, 0) — сколько
    штук источник `ff_mirror` пообещал бы WB СВЕРХ нашего учёта. Россыпь зеркала:
    строки `fulfillment_stocks` без `base_barcode` (короба не считаем дважды) с
    привязанной номенклатурой, `qty_good × units_per_box`; учёт —
    `warehouse_stock.quantity`; джойн по (warehouse_id, nomenclature_id).
    SKU только в учёте (зеркала нет) даёт 0 — занижать зеркало безопасно.
    """
    warehouse_ids = await get_linked_warehouse_ids(db, project_id, wb_warehouse_id)
    if not warehouse_ids:
        return 0, 0, 0

    mirror_rows = (
        await db.execute(
            select(
                FulfillmentStock.warehouse_id,
                FulfillmentStock.nomenclature_id,
                func.sum(FulfillmentStock.qty_good * FulfillmentStock.units_per_box).label("pieces"),
            )
            .where(
                FulfillmentStock.project_id == project_id,
                FulfillmentStock.warehouse_id.in_(warehouse_ids),
                FulfillmentStock.base_barcode.is_(None),
                FulfillmentStock.nomenclature_id.is_not(None),
            )
            .group_by(FulfillmentStock.warehouse_id, FulfillmentStock.nomenclature_id)
        )
    ).all()
    mirror_by_key = {(int(r.warehouse_id), int(r.nomenclature_id)): int(r.pieces or 0) for r in mirror_rows}

    ledger_rows = (
        await db.execute(
            select(
                WarehouseStock.warehouse_id,
                WarehouseStock.nomenclature_id,
                WarehouseStock.quantity,
            ).where(
                WarehouseStock.project_id == project_id,
                WarehouseStock.warehouse_id.in_(warehouse_ids),
            )
        )
    ).all()
    ledger_by_key = {(int(r.warehouse_id), int(r.nomenclature_id)): int(r.quantity or 0) for r in ledger_rows}

    mirror_total = sum(mirror_by_key.values())
    ledger_total = sum(ledger_by_key.values())
    over = sum(
        max(pieces - ledger_by_key.get(key, 0), 0) for key, pieces in mirror_by_key.items()
    )
    return over, ledger_total, mirror_total


async def update_settings(
    db: AsyncSession,
    project_id: int,
    wb_warehouse_id: int,
    payload: FbsWarehouseSettingsUpdate,
) -> dict[str, Any]:
    """Частичное обновление настроек трансляции (WB не трогаем).

    `exclude_unset` различает «поле не передано» и «передано значение» —
    иначе PATCH одним полем сбрасывал бы остальные в дефолты.

    `fbo_max_qty = -1` — это «снять гейт» (в базе NULL). Отдельный сентинел
    нужен потому, что `None` в JSON неотличим от «поле не передано»: без него
    снять гейт было бы нечем, а `0` означает совсем другое — «отдаём в FBS
    только то, чего на складах WB не осталось вовсе».

    Гейт рискованной пары «translate + ff_mirror»: если ЭФФЕКТИВНАЯ пара после
    применения изменений даёт трансляцию из зеркала ФФ, а зеркало по привязанным
    складам обещает больше нашего учёта, — без `force` поднимаем
    `FbsMirrorAboveLedger` (роутер → 409) ДО записи изменений: половина настроек
    сохраниться не должна. `min_of_both` не гейтится.
    """
    wh = await get_warehouse(db, project_id, wb_warehouse_id)
    changes = payload.model_dump(exclude_unset=True)
    # `force` — подтверждение гейта, а не настройка склада: в setattr не идёт.
    changes.pop("force", None)

    # Эффективная пара ПОСЛЕ применения изменений (None = «не менять»).
    effective_mode = changes.get("mode") if changes.get("mode") is not None else wh.mode
    effective_source = (
        changes.get("stock_source") if changes.get("stock_source") is not None else wh.stock_source
    )
    if (
        effective_mode == FbsWarehouseMode.TRANSLATE.value
        and effective_source == FbsStockSource.FF_MIRROR.value
        and not payload.force
    ):
        over, ledger_total, mirror_total = await _mirror_over_ledger_totals(
            db, project_id, wb_warehouse_id
        )
        if over > 0:
            raise FbsMirrorAboveLedger(
                "Зеркало ФФ выше нашего учёта: источник «зеркало ФФ» отдал бы WB "
                f"на {over} шт больше, чем есть в учёте "
                f"(mirror_over_ledger={over}, ledger_total={ledger_total}, "
                f"mirror_total={mirror_total}). Разберитесь с расхождением или "
                "повторите запрос с force=true, чтобы применить как есть."
            )

    for field, value in changes.items():
        if value is None:
            continue
        if field == "fbo_max_qty" and int(value) < 0:
            wh.fbo_max_qty = None
            continue
        setattr(wh, field, value)
    await db.commit()
    await invalidate_cache(CACHE_WAREHOUSES)
    await invalidate_cache(CACHE_STOCK_PREVIEW)

    links_by_wb = await _links_by_wb_warehouse(db, project_id)
    logger.info(
        "FBS warehouse settings updated: project=%s wb_wh=%s fields=%s",
        project_id,
        wb_warehouse_id,
        sorted(changes),
    )
    return _warehouse_to_dict(wh, links_by_wb.get(wb_warehouse_id, []))


# ─── Привязки ────────────────────────────────────────────────────────────────


async def link_warehouse(db: AsyncSession, project_id: int, wb_warehouse_id: int, warehouse_id: int) -> int:
    """Привязать наш склад к складу продавца WB.

    Проверяем инвариант «наш склад кормит максимум один склад WB» ДО вставки:
    partial unique index иначе отдал бы IntegrityError → 500 без объяснения.
    Повторная привязка той же пары реактивирует существующую строку.
    """
    await get_warehouse(db, project_id, wb_warehouse_id)

    wh_result = await db.execute(
        select(Warehouse).where(
            Warehouse.id == warehouse_id,
            Warehouse.project_id == project_id,
            Warehouse.is_deleted == False,  # noqa: E712 — SQLAlchemy expression
        )
    )
    warehouse = wh_result.scalar_one_or_none()
    if warehouse is None:
        raise ValueError(f"Склад {warehouse_id} не найден в проекте")

    existing_result = await db.execute(
        select(WbFbsWarehouseLink).where(
            WbFbsWarehouseLink.project_id == project_id,
            WbFbsWarehouseLink.warehouse_id == warehouse_id,
        )
    )
    same_pair: WbFbsWarehouseLink | None = None
    for link in existing_result.scalars().all():
        if link.wb_warehouse_id == wb_warehouse_id:
            same_pair = link
        elif link.is_active:
            raise ValueError(
                f"Склад «{warehouse.name}» уже привязан к складу продавца WB {link.wb_warehouse_id} — "
                "один наш склад может кормить только один склад WB (иначе двойная продажа)"
            )

    if same_pair is not None:
        same_pair.is_active = True
        link_id = same_pair.id
    else:
        new_link = WbFbsWarehouseLink(
            project_id=project_id,
            wb_warehouse_id=wb_warehouse_id,
            warehouse_id=warehouse_id,
            is_active=True,
        )
        db.add(new_link)
        await db.flush()
        link_id = new_link.id

    await db.commit()
    await invalidate_cache(CACHE_WAREHOUSES)
    await invalidate_cache(CACHE_STOCK_PREVIEW)
    return link_id


async def unlink_warehouse(db: AsyncSession, project_id: int, link_id: int) -> None:
    """Отвязать наш склад (hard delete — link-таблица без SoftDeleteMixin).

    После отвязки остаток этого склада перестаёт кормить WB, но сам по себе
    ничего на WB не обнуляет — обнуление приедет ближайшим пушем остатков.
    """
    result = await db.execute(
        select(WbFbsWarehouseLink).where(
            WbFbsWarehouseLink.id == link_id,
            WbFbsWarehouseLink.project_id == project_id,
        )
    )
    link = result.scalar_one_or_none()
    if link is None:
        raise ValueError(f"Привязка {link_id} не найдена в проекте")
    await db.delete(link)  # no-soft-delete-check: WbFbsWarehouseLink — link-таблица без SoftDeleteMixin
    await db.commit()
    await invalidate_cache(CACHE_WAREHOUSES)
    await invalidate_cache(CACHE_STOCK_PREVIEW)
