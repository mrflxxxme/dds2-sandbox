# ruff: noqa: RUF001, RUF002, RUF003, E712
"""
Service: fulfillment — интеграция с внешними фулфилментами (skladbot, migfull).

Слой read-only зеркала: остатки (FulfillmentStock, полная замена при синке)
и заявки (FulfillmentRequest, UPSERT с сохранением ручных связей).
Документный контур (WarehouseStock / StockMovement) НЕ трогаем.
"""

import logging
from datetime import date

import httpx
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.integrations.resilience import CircuitOpenError, RateLimitError
from backend.integrations.skladbot_client import (
    ASSEMBLY_TYPE_IDS,
    INBOUND_TYPE_IDS,
    SkladbotApiError,
    SkladbotClient,
    decode_jwt_exp,
)
from backend.models import (
    FfRequestKind,
    FulfillmentRequest,
    FulfillmentStock,
    InboundReceipt,
    IntegrationKey,
    Nomenclature,
    Warehouse,
    WarehouseStock,
)
from backend.models.assembly import AssemblyRequest
from backend.utils.crypto import decrypt as _decrypt, encrypt as _encrypt
from backend.utils.time import utcnow

logger = logging.getLogger("dds.fulfillment")

FF_SERVICES = ("skladbot", "migfull")
STOCKS_LIMIT = 5000
REQUESTS_LIMIT = 500

# Advisory lock namespace для синка (pg_advisory_xact_lock(ns, project_id)).
# Лок по project_id (не warehouse_id): uq заявок — (project_id, provider,
# external_id), и два склада одного проекта на один кабинет ФФ не должны
# вставлять один external_id параллельно.
_FF_SYNC_LOCK_NS = 0x46465359  # 'FFSY' ≈ FulFillment SYnc


# ─── Connection ──────────────────────────────────────────────────────────────


async def get_integration(db: AsyncSession, project_id: int, warehouse_id: int) -> IntegrationKey | None:
    """Active fulfillment key bound to the warehouse, or None."""
    result = await db.execute(
        select(IntegrationKey).where(
            IntegrationKey.project_id == project_id,
            IntegrationKey.service.in_(FF_SERVICES),
            IntegrationKey.warehouse_id == warehouse_id,
            IntegrationKey.is_active.is_(True),
            IntegrationKey.is_deleted == False,
        )
    )
    return result.scalars().first()


async def get_status(db: AsyncSession, project_id: int, warehouse_id: int) -> dict:
    """Connection status for the warehouse (FulfillmentStatus shape)."""
    key = await get_integration(db, project_id, warehouse_id)
    if not key:
        return {"connected": False}
    config = key.config or {}
    return {
        "connected": True,
        "provider": key.service,
        "key_preview": "***" + _decrypt(key.encrypted_key)[-4:],
        "customer_id": config.get("customer_id"),
        "customer_name": config.get("customer_name"),
        "token_expires_at": config.get("token_expires_at"),
        "last_sync_at": key.last_sync_at,
    }


async def connect(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    provider: str,
    token: str,
) -> dict:
    """Validate the token and bind a fulfillment key to the warehouse.

    Возвращает статус (FulfillmentStatus shape). Raises ValueError при
    невалидном токене или отсутствующем складе.
    """
    result = await db.execute(
        select(Warehouse.id).where(
            Warehouse.id == warehouse_id,
            Warehouse.project_id == project_id,
            Warehouse.is_deleted == False,
        )
    )
    if result.scalar_one_or_none() is None:
        raise ValueError("Склад не найден в проекте")

    client = SkladbotClient(token, project_id=project_id)
    try:
        customer = await client.test_connection()
    except CircuitOpenError as e:
        raise ValueError(f"skladbot.ru временно недоступен, попробуйте позже ({e})") from e
    if customer is None:
        raise ValueError("Токен невалидный: skladbot.ru не вернул данные customer. Проверьте токен.")

    expires_at = decode_jwt_exp(token)
    config = {
        "customer_id": customer.get("id"),
        "customer_name": customer.get("name"),
        "token_expires_at": expires_at.isoformat() if expires_at else None,
    }
    label = f"warehouse:{warehouse_id}"

    # UniqueConstraint(project_id, service, label) НЕ учитывает is_deleted:
    # после soft_delete строка занимает уникальный слот. Ищем существующую
    # ВКЛЮЧАЯ soft-deleted → restore() + обновление полей, НЕ новый INSERT.
    result = await db.execute(
        select(IntegrationKey).where(
            IntegrationKey.project_id == project_id,
            IntegrationKey.service == provider,
            IntegrationKey.label == label,
        )
    )
    key = result.scalars().first()
    if key:
        key.restore()
        key.encrypted_key = _encrypt(token)
        key.config = config
        key.is_active = True
        key.warehouse_id = warehouse_id
    else:
        key = IntegrationKey(
            project_id=project_id,
            service=provider,
            label=label,
            encrypted_key=_encrypt(token),
            is_active=True,
            warehouse_id=warehouse_id,
            config=config,
        )
        db.add(key)
    try:
        await db.commit()
    except IntegrityError as e:
        # TOCTOU: конкурентный connect вставил ключ между select и commit
        await db.rollback()
        raise ValueError("Подключение уже выполняется параллельно — обновите страницу") from e
    await db.refresh(key)

    return {
        "connected": True,
        "provider": provider,
        "key_preview": "***" + token[-4:],
        "customer_id": config["customer_id"],
        "customer_name": config["customer_name"],
        "token_expires_at": config["token_expires_at"],
        "last_sync_at": key.last_sync_at,
    }


async def disconnect(db: AsyncSession, project_id: int, warehouse_id: int) -> bool:
    """Soft-delete the fulfillment key. Зеркальные данные ff_* НЕ трогаем."""
    key = await get_integration(db, project_id, warehouse_id)
    if not key:
        return False
    key.soft_delete()
    await db.commit()
    return True


# ─── Sync ────────────────────────────────────────────────────────────────────


async def sync_warehouse(db: AsyncSession, project_id: int, warehouse_id: int) -> dict:
    """Sync stocks + requests from the fulfillment provider (FfSyncResult shape).

    Используется и роутером (ручной синк), и scheduler-job.
    Raises ValueError если фулфилмент не подключён.

    Порядок важен: сначала читаем ключ и ЗАКРЫВАЕМ транзакцию, потом ходим
    в skladbot (retry/backoff может занять минуты — нельзя держать
    idle-in-transaction коннект PgBouncer), и только потом пишем — под
    advisory xact-lock по (NS, project_id), сериализующим конкурентные синки
    (ручной + scheduler, любые склады проекта), иначе full-replace и UPSERT
    заявок ловят IntegrityError по uq.
    """
    key = await get_integration(db, project_id, warehouse_id)
    if not key:
        raise ValueError("Фулфилмент не подключён к этому складу")

    key_id = key.id
    provider = key.service
    token = _decrypt(key.encrypted_key)
    config = key.config or {}
    customer_id = config.get("customer_id")
    if not customer_id:
        raise ValueError("В конфигурации ключа нет customer_id — переподключите фулфилмент")
    await db.commit()  # закрыть read-транзакцию до внешних HTTP-вызовов

    client = SkladbotClient(token, project_id=project_id)
    try:
        items = await client.fetch_all_products(customer_id)
        fetched_requests: list[tuple[int, list[dict]]] = []
        for type_id in sorted(ASSEMBLY_TYPE_IDS | INBOUND_TYPE_IDS):
            fetched_requests.append((type_id, await client.fetch_requests(type_id)))
    except CircuitOpenError as e:
        raise ValueError(f"skladbot.ru временно недоступен, попробуйте позже ({e})") from e
    except RateLimitError as e:
        raise ValueError("skladbot.ru ограничил частоту запросов — повторите синк через минуту") from e
    except httpx.HTTPError as e:
        raise ValueError(f"Сетевая ошибка при обращении к skladbot.ru: {e}") from e

    await db.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :project_id)"),
        {"ns": _FF_SYNC_LOCK_NS, "project_id": project_id},
    )
    stocks_synced, unmatched = await _apply_stocks(db, project_id, warehouse_id, provider, items)
    requests_synced = await _apply_requests(db, project_id, warehouse_id, provider, fetched_requests)

    synced_at = utcnow()
    key = await db.get(IntegrationKey, key_id)
    if key:
        key.last_sync_at = synced_at
    await db.commit()

    logger.info(
        "Fulfillment sync: project=%s warehouse=%s stocks=%d requests=%d unmatched=%d",
        project_id,
        warehouse_id,
        stocks_synced,
        requests_synced,
        unmatched,
    )
    return {
        "stocks_synced": stocks_synced,
        "requests_synced": requests_synced,
        "unmatched_barcodes": unmatched,
        "synced_at": synced_at,
    }


async def _apply_stocks(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    provider: str,
    items: list[dict],
) -> tuple[int, int]:
    """Aggregate fetched provider stock and fully replace the snapshot.

    Returns (rows_written, unmatched_barcodes).
    """
    # Агрегация по barcode: один barcode встречается в нескольких item'ах
    # (версии товара под WB/OZON) — количества суммируем, name/vendor_code/
    # external_product_id берём от первого. Пустой barcode пропускаем.
    aggregated: dict[str, dict] = {}
    for item in items:
        barcode = str(item.get("barcode") or "").strip()
        if not barcode:
            continue
        agg = aggregated.get(barcode)
        if agg is None:
            pdid = item.get("product_data_id")
            agg = aggregated[barcode] = {
                "name": item.get("name"),
                "vendor_code": item.get("vendor_code"),
                "external_product_id": str(pdid) if pdid is not None else None,
                "qty_good": 0,
                "qty_reserve": 0,
                "qty_defect": 0,
                "qty_nominal": 0,
            }
        agg["qty_good"] += int(item.get("amount") or 0)
        agg["qty_reserve"] += int(item.get("reserve_amount") or 0)
        agg["qty_defect"] += int(item.get("repair_amount") or 0)
        agg["qty_nominal"] += int(item.get("nominale_amount") or 0)

    # Резолв номенклатуры одним запросом по всем barcode
    nom_by_barcode: dict[str, int] = {}
    if aggregated:
        result = await db.execute(
            select(Nomenclature.id, Nomenclature.barcode).where(
                Nomenclature.project_id == project_id,
                Nomenclature.barcode.in_(list(aggregated)),
            )
        )
        nom_by_barcode = {barcode: nom_id for nom_id, barcode in result.all()}

    # Полная замена снапшота этого (project, warehouse).
    # FulfillmentStock — не SoftDelete-модель: hard delete по дизайну.
    await db.execute(
        delete(FulfillmentStock).where(
            FulfillmentStock.project_id == project_id,
            FulfillmentStock.warehouse_id == warehouse_id,
        )
    )

    synced_at = utcnow()
    rows = [
        FulfillmentStock(
            project_id=project_id,
            warehouse_id=warehouse_id,
            provider=provider,
            barcode=barcode,
            nomenclature_id=nom_by_barcode.get(barcode),
            name=agg["name"],
            vendor_code=agg["vendor_code"],
            qty_good=agg["qty_good"],
            qty_reserve=agg["qty_reserve"],
            qty_defect=agg["qty_defect"],
            qty_nominal=agg["qty_nominal"],
            external_product_id=agg["external_product_id"],
            synced_at=synced_at,
        )
        for barcode, agg in aggregated.items()
    ]
    db.add_all(rows)

    unmatched = sum(1 for barcode in aggregated if barcode not in nom_by_barcode)
    return len(rows), unmatched


async def _apply_requests(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    provider: str,
    fetched_by_type: list[tuple[int, list[dict]]],
) -> int:
    """UPSERT the request mirror from prefetched provider data.

    Существующие строки: обновляем status/stage/number/type_name/даты/флаги/
    synced_at/raw; ручные связи (assembly_request_id/inbound_receipt_id) НЕ трогаем.
    """
    fetched: list[tuple[int, dict]] = []
    for type_id, rows in fetched_by_type:
        fetched.extend((type_id, row) for row in rows)

    # Дедуп по external_id в рамках батча (защита от пересечения выборок)
    by_external: dict[str, tuple[int, dict]] = {}
    for type_id, row in fetched:
        external_id = str(row.get("id"))
        if external_id not in by_external:
            by_external[external_id] = (type_id, row)

    if not by_external:
        return 0

    result = await db.execute(
        select(FulfillmentRequest).where(
            FulfillmentRequest.project_id == project_id,
            FulfillmentRequest.provider == provider,
            FulfillmentRequest.external_id.in_(list(by_external)),
        )
    )
    existing = {r.external_id: r for r in result.scalars().all()}

    synced_at = utcnow()
    for external_id, (type_id, row) in by_external.items():
        req = existing.get(external_id)
        if req:
            req.number = row.get("delivery_number") or req.number
            req.type_name = row.get("type") or req.type_name
            req.status = row.get("status")
            req.stage_code = row.get("stage_code")
            req.stage_title = row.get("stage_title")
            req.archived = bool(row.get("archived"))
            req.expired = bool(row.get("expired"))
            req.is_completed = bool(row.get("is_completed"))
            req.external_created_at = _parse_date(row.get("created_at")) or req.external_created_at
            req.synced_at = synced_at
            req.raw = row
        else:
            kind = FfRequestKind.ASSEMBLY.value if type_id in ASSEMBLY_TYPE_IDS else FfRequestKind.INBOUND.value
            db.add(
                FulfillmentRequest(
                    project_id=project_id,
                    warehouse_id=warehouse_id,
                    provider=provider,
                    external_id=external_id,
                    number=row.get("delivery_number"),
                    kind=kind,
                    type_id=type_id,
                    type_name=row.get("type"),
                    status=row.get("status"),
                    stage_code=row.get("stage_code"),
                    stage_title=row.get("stage_title"),
                    is_completed=bool(row.get("is_completed")),
                    archived=bool(row.get("archived")),
                    expired=bool(row.get("expired")),
                    external_created_at=_parse_date(row.get("created_at")),
                    raw=row,
                    synced_at=synced_at,
                )
            )
    return len(by_external)


def _parse_date(value) -> date | None:
    """'2026-06-10' → date; мусор/None → None."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# ─── Stocks view ─────────────────────────────────────────────────────────────


async def list_stocks(db: AsyncSession, project_id: int, warehouse_id: int) -> dict:
    """UNION остатков ФФ и нашего склада по barcode (FfStocksResponse shape).

    diff = ff_good - our_quantity; сортировка diff desc, затем barcode.
    """
    result = await db.execute(
        select(FulfillmentStock)
        .where(
            FulfillmentStock.project_id == project_id,
            FulfillmentStock.warehouse_id == warehouse_id,
        )
        .limit(STOCKS_LIMIT)
    )
    ff_rows = list(result.scalars().all())

    result = await db.execute(
        select(WarehouseStock)
        .where(
            WarehouseStock.project_id == project_id,
            WarehouseStock.warehouse_id == warehouse_id,
            (WarehouseStock.quantity > 0) | (WarehouseStock.defect_quantity > 0),
        )
        .limit(STOCKS_LIMIT)
    )
    our_rows = list(result.scalars().all())

    # article_seller одним запросом для всех номенклатур (без N+1)
    nom_ids = {r.nomenclature_id for r in ff_rows if r.nomenclature_id}
    nom_ids |= {r.nomenclature_id for r in our_rows}
    article_by_nom: dict[int, str | None] = {}
    if nom_ids:
        result = await db.execute(
            select(Nomenclature.id, Nomenclature.article_seller).where(
                Nomenclature.project_id == project_id,
                Nomenclature.id.in_(nom_ids),
            )
        )
        article_by_nom = dict(result.all())

    rows: dict[str, dict] = {}
    for r in ff_rows:
        rows[r.barcode] = {
            "barcode": r.barcode,
            "name": r.name,
            "vendor_code": r.vendor_code,
            "nomenclature_id": r.nomenclature_id,
            "article_seller": article_by_nom.get(r.nomenclature_id),
            "ff_good": r.qty_good,
            "ff_reserve": r.qty_reserve,
            "ff_defect": r.qty_defect,
            "ff_nominal": r.qty_nominal,
            "our_quantity": 0,
            "our_defect": 0,
            "diff": r.qty_good,
        }
    for r in our_rows:
        row = rows.get(r.barcode)
        if row is None:
            row = rows[r.barcode] = {
                "barcode": r.barcode,
                "name": None,
                "vendor_code": None,
                "nomenclature_id": r.nomenclature_id,
                "article_seller": article_by_nom.get(r.nomenclature_id),
                "ff_good": 0,
                "ff_reserve": 0,
                "ff_defect": 0,
                "ff_nominal": 0,
                "our_quantity": 0,
                "our_defect": 0,
                "diff": 0,
            }
        elif row["nomenclature_id"] is None:
            row["nomenclature_id"] = r.nomenclature_id
            row["article_seller"] = article_by_nom.get(r.nomenclature_id)
        row["our_quantity"] = r.quantity
        row["our_defect"] = r.defect_quantity
        row["diff"] = row["ff_good"] - r.quantity

    out_rows = sorted(rows.values(), key=lambda x: (-x["diff"], x["barcode"]))
    totals = {
        "ff_good": sum(r["ff_good"] for r in out_rows),
        "ff_reserve": sum(r["ff_reserve"] for r in out_rows),
        "ff_defect": sum(r["ff_defect"] for r in out_rows),
        "our_quantity": sum(r["our_quantity"] for r in out_rows),
        "diff": sum(r["diff"] for r in out_rows),
        "unmatched": sum(1 for r in ff_rows if r.nomenclature_id is None),
    }
    synced_at = max((r.synced_at for r in ff_rows), default=None)
    return {"rows": out_rows, "totals": totals, "synced_at": synced_at}


# ─── Requests view + linking ─────────────────────────────────────────────────


def _request_to_dict(
    req: FulfillmentRequest,
    assembly_map: dict | None = None,
    inbound_map: dict | None = None,
) -> dict:
    """FulfillmentRequest → FfRequestRow-shaped dict с обогащением связи."""
    linked_number = linked_status = None
    if req.assembly_request_id and assembly_map and req.assembly_request_id in assembly_map:
        linked_number, linked_status = assembly_map[req.assembly_request_id]
    elif req.inbound_receipt_id and inbound_map and req.inbound_receipt_id in inbound_map:
        linked_number, linked_status = inbound_map[req.inbound_receipt_id]
    return {
        "id": req.id,
        "external_id": req.external_id,
        "number": req.number,
        "kind": req.kind,
        "type_name": req.type_name,
        "status": req.status,
        "stage_code": req.stage_code,
        "stage_title": req.stage_title,
        "is_completed": req.is_completed,
        "archived": req.archived,
        "expired": req.expired,
        "external_created_at": req.external_created_at,
        "synced_at": req.synced_at,
        "assembly_request_id": req.assembly_request_id,
        "inbound_receipt_id": req.inbound_receipt_id,
        "linked_number": linked_number,
        "linked_status": linked_status,
    }


async def list_requests(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    kind: str | None = None,
) -> list[dict]:
    """Зеркало заявок ФФ с обогащением linked_number/linked_status (без N+1)."""
    q = select(FulfillmentRequest).where(
        FulfillmentRequest.project_id == project_id,
        FulfillmentRequest.warehouse_id == warehouse_id,
    )
    if kind:
        q = q.where(FulfillmentRequest.kind == kind)
    q = q.order_by(
        FulfillmentRequest.external_created_at.desc().nullslast(),
        FulfillmentRequest.id.desc(),
    ).limit(REQUESTS_LIMIT)
    result = await db.execute(q)
    requests = list(result.scalars().all())

    assembly_ids = {r.assembly_request_id for r in requests if r.assembly_request_id}
    inbound_ids = {r.inbound_receipt_id for r in requests if r.inbound_receipt_id}

    assembly_map: dict[int, tuple] = {}
    if assembly_ids:
        result = await db.execute(
            select(AssemblyRequest.id, AssemblyRequest.number, AssemblyRequest.status).where(
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.id.in_(assembly_ids),
                AssemblyRequest.is_deleted == False,
            )
        )
        assembly_map = {row[0]: (row[1], row[2]) for row in result.all()}

    inbound_map: dict[int, tuple] = {}
    if inbound_ids:
        result = await db.execute(
            select(InboundReceipt.id, InboundReceipt.number, InboundReceipt.status).where(
                InboundReceipt.project_id == project_id,
                InboundReceipt.id.in_(inbound_ids),
                InboundReceipt.is_deleted == False,
            )
        )
        inbound_map = {row[0]: (row[1], row[2]) for row in result.all()}

    return [_request_to_dict(r, assembly_map, inbound_map) for r in requests]


def _coerce_name(value) -> str | None:
    """Провайдер отдаёт исполнителя/создателя то строкой, то объектом {name}."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get("name")
    return str(value)


async def get_request_detail(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    ff_request_id: int,
) -> dict | None:
    """Деталка ФФ-заявки: зеркальная шапка + ЖИВОЙ состав от провайдера.

    Тянет GET /v1/requests/show/{external_id} при каждом открытии (не кэшируем:
    состав и принятые количества меняются на стороне ФФ). None — заявка не
    найдена; ValueError — не подключено / провайдер недоступен.
    """
    result = await db.execute(
        select(FulfillmentRequest).where(
            FulfillmentRequest.id == ff_request_id,
            FulfillmentRequest.project_id == project_id,
            FulfillmentRequest.warehouse_id == warehouse_id,
        )
    )
    req = result.scalar_one_or_none()
    if not req:
        return None

    key = await get_integration(db, project_id, warehouse_id)
    if not key:
        raise ValueError("Фулфилмент не подключён к этому складу")
    token = _decrypt(key.encrypted_key)

    # Связанный документ — до закрытия транзакции
    assembly_map: dict[int, tuple] = {}
    inbound_map: dict[int, tuple] = {}
    if req.assembly_request_id:
        result = await db.execute(
            select(AssemblyRequest.id, AssemblyRequest.number, AssemblyRequest.status).where(
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.id == req.assembly_request_id,
                AssemblyRequest.is_deleted == False,
            )
        )
        assembly_map = {row[0]: (row[1], row[2]) for row in result.all()}
    elif req.inbound_receipt_id:
        result = await db.execute(
            select(InboundReceipt.id, InboundReceipt.number, InboundReceipt.status).where(
                InboundReceipt.project_id == project_id,
                InboundReceipt.id == req.inbound_receipt_id,
                InboundReceipt.is_deleted == False,
            )
        )
        inbound_map = {row[0]: (row[1], row[2]) for row in result.all()}
    external_id = req.external_id
    await db.commit()  # закрыть read-транзакцию до внешнего HTTP-вызова

    client = SkladbotClient(token, project_id=project_id)
    try:
        detail = await client.fetch_request_detail(external_id)
    except CircuitOpenError as e:
        raise ValueError(f"skladbot.ru временно недоступен, попробуйте позже ({e})") from e
    except RateLimitError as e:
        raise ValueError("skladbot.ru ограничил частоту запросов — откройте деталку через минуту") from e
    except SkladbotApiError as e:
        # 404 особенно вероятен: роут /v1/requests/show недокументированный
        raise ValueError(f"skladbot.ru не отдал деталку заявки (HTTP {e.status_code})") from e
    except httpx.HTTPError as e:
        raise ValueError(f"Сетевая ошибка при обращении к skladbot.ru: {e}") from e
    except ValueError as e:
        raise ValueError(f"skladbot.ru вернул ошибку сервера, попробуйте позже ({str(e)[:100]})") from e

    raw_products = detail.get("products") or []
    barcodes = {str(p.get("barcode") or "").strip() for p in raw_products}
    barcodes.discard("")
    nom_by_barcode: dict[str, tuple[int, str | None]] = {}
    if barcodes:
        result = await db.execute(
            select(Nomenclature.id, Nomenclature.barcode, Nomenclature.article_seller).where(
                Nomenclature.project_id == project_id,
                Nomenclature.barcode.in_(list(barcodes)),
            )
        )
        nom_by_barcode = {barcode: (nom_id, article) for nom_id, barcode, article in result.all()}

    products = []
    for p in raw_products:
        barcode = str(p.get("barcode") or "").strip() or None
        nom_id, article = nom_by_barcode.get(barcode, (None, None)) if barcode else (None, None)
        products.append(
            {
                "barcode": barcode,
                "vendor_code": p.get("vendorCode"),
                "name": p.get("name"),
                "nomenclature_id": nom_id,
                "article_seller": article,
                "qty": int(p.get("amount") or 0),
                "accepted_qty": int(p.get("acceptedAmount") or 0),
                "delivery_qty": int(p.get("delivery_amount") or 0),
                "defect_qty": int(p.get("repairAmount") or 0),
                "color": p.get("color"),
                "size": p.get("size"),
                "comment": p.get("comment") or None,
                "image": p.get("image"),
            }
        )

    stage = detail.get("stage") or {}
    customer = detail.get("customer") or {}
    fields = [
        {
            "name": f.get("name"),
            "field": f.get("field"),
            "value": str(f["value"]) if f.get("value") is not None else None,
        }
        for f in (detail.get("fields") or [])
    ]
    stage_logs = [
        {
            "stage": log.get("stage"),
            "executor": _coerce_name(log.get("executor")),
            "created_at": log.get("created_at"),
            "spent_time": log.get("spent_time") or None,
        }
        for log in (detail.get("stageLogs") or [])
    ]

    row = _request_to_dict(req, assembly_map, inbound_map)
    row.update(
        {
            "comment": detail.get("comment"),
            "customer_name": customer.get("name"),
            "executor": _coerce_name(detail.get("executor")),
            "creator": _coerce_name(detail.get("creator")),
            "stage_description": stage.get("description"),
            "total_qty": sum(p["qty"] for p in products),
            "total_accepted": sum(p["accepted_qty"] for p in products),
            "products": products,
            "stage_logs": stage_logs,
            "fields": fields,
        }
    )
    return row


async def link_request(
    db: AsyncSession,
    project_id: int,
    ff_request_id: int,
    assembly_request_id: int | None = None,
    inbound_receipt_id: int | None = None,
    warehouse_id: int | None = None,
) -> dict | None:
    """Привязать ФФ-заявку к нашему документу (ровно один из двух id).

    Returns None если ФФ-заявка не найдена; ValueError при нарушении правил
    (оба/ни одного id, чужой kind, чужой склад, несуществующий документ,
    двойная привязка). warehouse_id (из path) дополнительно скоупит ФФ-заявку.
    """
    if (assembly_request_id is None) == (inbound_receipt_id is None):
        raise ValueError("Укажите ровно один из assembly_request_id / inbound_receipt_id")

    q = select(FulfillmentRequest).where(
        FulfillmentRequest.id == ff_request_id,
        FulfillmentRequest.project_id == project_id,
    )
    if warehouse_id is not None:
        q = q.where(FulfillmentRequest.warehouse_id == warehouse_id)
    result = await db.execute(q)
    req = result.scalar_one_or_none()
    if not req:
        return None

    assembly_map: dict[int, tuple] = {}
    inbound_map: dict[int, tuple] = {}

    if assembly_request_id is not None:
        if req.kind != FfRequestKind.ASSEMBLY.value:
            raise ValueError("assembly_request_id можно привязать только к ФФ-заявке типа assembly")
        result = await db.execute(
            select(AssemblyRequest).where(
                AssemblyRequest.id == assembly_request_id,
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.is_deleted == False,
            )
        )
        doc = result.scalar_one_or_none()
        if not doc:
            raise ValueError("Заявка на сборку не найдена в проекте")
        if doc.warehouse_id != req.warehouse_id:
            raise ValueError("Заявка на сборку принадлежит другому складу")
        conflict = await db.execute(
            select(FulfillmentRequest.id)
            .where(
                FulfillmentRequest.project_id == project_id,
                FulfillmentRequest.assembly_request_id == assembly_request_id,
                FulfillmentRequest.id != ff_request_id,
            )
            .limit(1)
        )
        if conflict.scalar_one_or_none() is not None:
            raise ValueError("Заявка на сборку уже связана с другой ФФ-заявкой")
        req.assembly_request_id = assembly_request_id
        assembly_map = {doc.id: (doc.number, doc.status)}
    else:
        if req.kind != FfRequestKind.INBOUND.value:
            raise ValueError("inbound_receipt_id можно привязать только к ФФ-заявке типа inbound")
        result = await db.execute(
            select(InboundReceipt).where(
                InboundReceipt.id == inbound_receipt_id,
                InboundReceipt.project_id == project_id,
                InboundReceipt.is_deleted == False,
            )
        )
        doc = result.scalar_one_or_none()
        if not doc:
            raise ValueError("Приёмка не найдена в проекте")
        if doc.warehouse_id != req.warehouse_id:
            raise ValueError("Приёмка принадлежит другому складу")
        conflict = await db.execute(
            select(FulfillmentRequest.id)
            .where(
                FulfillmentRequest.project_id == project_id,
                FulfillmentRequest.inbound_receipt_id == inbound_receipt_id,
                FulfillmentRequest.id != ff_request_id,
            )
            .limit(1)
        )
        if conflict.scalar_one_or_none() is not None:
            raise ValueError("Приёмка уже связана с другой ФФ-заявкой")
        req.inbound_receipt_id = inbound_receipt_id
        inbound_map = {doc.id: (doc.number, doc.status)}

    await db.commit()
    return _request_to_dict(req, assembly_map, inbound_map)


async def unlink_request(
    db: AsyncSession,
    project_id: int,
    ff_request_id: int,
    warehouse_id: int | None = None,
) -> dict | None:
    """Снять обе связи с ФФ-заявки. None если не найдена."""
    q = select(FulfillmentRequest).where(
        FulfillmentRequest.id == ff_request_id,
        FulfillmentRequest.project_id == project_id,
    )
    if warehouse_id is not None:
        q = q.where(FulfillmentRequest.warehouse_id == warehouse_id)
    result = await db.execute(q)
    req = result.scalar_one_or_none()
    if not req:
        return None
    req.assembly_request_id = None
    req.inbound_receipt_id = None
    await db.commit()
    return _request_to_dict(req)
