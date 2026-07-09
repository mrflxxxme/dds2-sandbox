"""
Service: integrations — business logic for integration keys and WB sync.
Extracted from routers/integrations.py to keep router as thin HTTP layer.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import IntegrationKey, Nomenclature, SyncLog, WbPayout
from backend.utils.crypto import decrypt as _decrypt, encrypt as _encrypt
from backend.utils.time import utcnow

if TYPE_CHECKING:
    from backend.integrations.wb_portal_client import WbPortalClient

logger = logging.getLogger("dds.integrations")


# ─── Integration Keys ────────────────────────────────────────────────────────


async def list_keys(db: AsyncSession, project_id: int) -> list[dict]:
    """List all integration keys for a project (with masked previews)."""
    result = await db.execute(
        select(IntegrationKey)
        .where(IntegrationKey.project_id == project_id, IntegrationKey.is_deleted.is_(False))
        .order_by(IntegrationKey.created_at.desc())
    )
    keys = result.scalars().all()
    output = []
    for k in keys:
        # Один нерасшифровываемый ключ (напр. замаскированная заглушка после sync-prod)
        # не должен ронять весь список — показываем плейсхолдер вместо 500.
        try:
            preview = "***" + _decrypt(k.encrypted_key)[-4:]
        except Exception:
            preview = "•••• (не расшифровать)"
        output.append(
            {
                "id": k.id,
                "service": k.service,
                "label": k.label,
                "is_active": k.is_active,
                "created_at": k.created_at,
                "last_sync_at": k.last_sync_at,
                "key_preview": preview,
            }
        )
    return output


async def add_key(
    db: AsyncSession,
    project_id: int,
    service: str,
    api_key: str,
    label: str | None = None,
) -> dict:
    """
    Add a new integration API key (encrypted).
    Validates the key with the external service before saving.
    Returns dict with key info.
    Raises ValueError on validation failure.
    """
    if service not in ("wb", "wb_advert", "ozon"):
        raise ValueError(f"Unsupported service: {service}. Use 'wb', 'wb_advert' or 'ozon'.")

    # Test connection before saving — different scope, different endpoint.
    if service == "wb":
        from backend.integrations.wb_api import WBApiClient

        client = WBApiClient(api_key, project_id=None)
        valid = await client.test_connection()
        if not valid:
            raise ValueError("WB API ключ невалидный. Проверьте ключ.")
    elif service == "wb_advert":
        from backend.services.funnel.wb_advertising_api import check_advert_scope

        # "ok" → verified; "no_scope" (401/403) → reject; "unknown" (429/5xx/network)
        # → could not verify due to rate-limit, save anyway (a bad token will surface
        # as an error on the next ads sync, not block a legit key on a transient 429).
        scope = await check_advert_scope(api_key)
        if scope == "no_scope":
            raise ValueError(
                "Рекламный ключ без доступа «Продвижение». "
                "Проверьте, что токен выпущен с категорией «Продвижение»."
            )

    encrypted = _encrypt(api_key)
    key = IntegrationKey(
        project_id=project_id,
        service=service,
        label=label or service.upper(),
        encrypted_key=encrypted,
        is_active=True,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)

    # Auto-restart scheduler jobs + trigger first sync
    if service in ("wb", "wb_advert"):
        try:
            from backend.scheduler import restart_backfill_jobs

            restart_backfill_jobs()
            logger.info("Scheduler jobs restarted for project %s after WB key added", project_id)
        except Exception as e:
            logger.warning("Could not restart scheduler jobs: %s", e)

        # Launch first sync pipeline in background
        try:
            import asyncio

            from backend.services.funnel.unified_sync import run_first_sync_bg

            asyncio.create_task(run_first_sync_bg(project_id))  # noqa: RUF006
            logger.info("First sync pipeline launched for project %s", project_id)
        except Exception as e:
            logger.warning("Could not launch first sync: %s", e)

    return {
        "id": key.id,
        "service": key.service,
        "label": key.label,
        "is_active": key.is_active,
        "created_at": key.created_at,
        "last_sync_at": key.last_sync_at,
        "key_preview": "***" + api_key[-4:],
    }


async def delete_key(db: AsyncSession, project_id: int, key_id: int) -> bool:
    """Delete an integration key. Returns True if deleted, False if not found."""
    result = await db.execute(
        select(IntegrationKey).where(
            IntegrationKey.id == key_id,
            IntegrationKey.project_id == project_id,
            IntegrationKey.is_deleted == False,
        )
    )
    key = result.scalar_one_or_none()
    if not key:
        return False
    key.soft_delete()
    await db.commit()
    return True


# ─── WB Sync ─────────────────────────────────────────────────────────────────


async def _get_wb_key(db: AsyncSession, project_id: int) -> tuple:
    """Get active WB key and decrypted API key. Raises ValueError if not found."""
    result = await db.execute(
        select(IntegrationKey).where(
            IntegrationKey.project_id == project_id,
            IntegrationKey.service == "wb",
            IntegrationKey.is_active.is_(True),
            IntegrationKey.is_deleted.is_(False),
        )
    )
    key = result.scalar_one_or_none()
    if not key:
        raise ValueError("WB API ключ не найден. Добавьте ключ через /integrations/keys")
    return key, _decrypt(key.encrypted_key)


# ─── WB Portal Session (реплей кабинета для FBW-поставок) ─────────────────────

WB_PORTAL_SERVICE = "wb_portal_session"
WB_PORTAL_LABEL = "WB Portal Session"


async def set_wb_portal_session(db: AsyncSession, project_id: int, authorizev3: str) -> dict:
    """
    Завести/обновить session-токен кабинета WB (authorizev3) для реплея портала.
    Валидирует токен обновлением wb-seller-lk перед сохранением.
    Upsert по (project_id, service, label) с восстановлением soft-deleted строки.
    """
    from backend.integrations.wb_portal_client import WbPortalClient, WbPortalError

    authorizev3 = authorizev3.strip()
    if not authorizev3:
        raise ValueError("Пустой токен")

    client = WbPortalClient(authorizev3)
    try:
        await client.check_session()
    except WbPortalError as e:
        # WbSessionExpired (протух токен) И сетевые ошибки WB → 400 с понятным текстом.
        raise ValueError(f"Токен недействителен или WB недоступен: {e}") from e

    # Ищем ВКЛЮЧАЯ soft-deleted (unique-слот занят даже у удалённой строки).
    result = await db.execute(
        select(IntegrationKey).where(
            IntegrationKey.project_id == project_id,
            IntegrationKey.service == WB_PORTAL_SERVICE,
            IntegrationKey.label == WB_PORTAL_LABEL,
        )
    )
    key = result.scalar_one_or_none()
    encrypted = _encrypt(authorizev3)
    if key:
        key.restore() if key.is_deleted else None
        key.encrypted_key = encrypted
        key.is_active = True
        key.config = {"status": "ACTIVE", "updated_at": utcnow().isoformat()}
    else:
        key = IntegrationKey(
            project_id=project_id,
            service=WB_PORTAL_SERVICE,
            label=WB_PORTAL_LABEL,
            encrypted_key=encrypted,
            is_active=True,
            config={"status": "ACTIVE", "updated_at": utcnow().isoformat()},
        )
        db.add(key)
    await db.commit()
    await db.refresh(key)
    return {"status": "ACTIVE", "updated_at": (key.config or {}).get("updated_at")}


async def get_wb_portal_client(db: AsyncSession, project_id: int) -> "WbPortalClient":
    """Собрать WbPortalClient из активной сессии. Raises ValueError если нет."""
    from backend.integrations.wb_portal_client import WbPortalClient

    result = await db.execute(
        select(IntegrationKey).where(
            IntegrationKey.project_id == project_id,
            IntegrationKey.service == WB_PORTAL_SERVICE,
            IntegrationKey.is_active.is_(True),
            IntegrationKey.is_deleted.is_(False),
        )
    )
    key = result.scalar_one_or_none()
    if not key:
        raise ValueError("Сессия WB-кабинета не задана. Обновите доступ WB в настройках.")
    return WbPortalClient(_decrypt(key.encrypted_key))


async def mark_wb_portal_expired(db: AsyncSession, project_id: int) -> None:
    """Пометить сессию EXPIRED (после 401) — UI попросит вставить свежий токен."""
    result = await db.execute(
        select(IntegrationKey).where(
            IntegrationKey.project_id == project_id,
            IntegrationKey.service == WB_PORTAL_SERVICE,
            IntegrationKey.is_deleted.is_(False),
        )
    )
    key = result.scalar_one_or_none()
    if key:
        key.is_active = False
        key.config = {**(key.config or {}), "status": "EXPIRED", "expired_at": utcnow().isoformat()}
        await db.commit()


async def get_wb_portal_status(db: AsyncSession, project_id: int) -> dict:
    """Статус сессии кабинета для UI: ACTIVE / EXPIRED / NONE."""
    result = await db.execute(
        select(IntegrationKey).where(
            IntegrationKey.project_id == project_id,
            IntegrationKey.service == WB_PORTAL_SERVICE,
            IntegrationKey.is_deleted.is_(False),
        )
    )
    key = result.scalar_one_or_none()
    if not key:
        return {"status": "NONE"}
    status = (key.config or {}).get("status", "ACTIVE" if key.is_active else "EXPIRED")
    return {"status": status, "updated_at": (key.config or {}).get("updated_at")}


async def sync_wb_sales(
    db: AsyncSession,
    project_id: int,
    sync_type: str = "sales",
    date_from: date | None = None,
) -> SyncLog:
    """
    Sync data from Wildberries API.
    - sales: fetch sales → wb_payouts
    - orders: fetch orders (future)
    - finance: fetch finance report (future)
    """
    key, api_key = await _get_wb_key(db, project_id)

    if date_from is None:
        date_from = date.today() - timedelta(days=7)

    # Captured up front so the error path can log with a fresh row after rollback
    # expunges sync_log (see except below).
    key_id = key.id
    started_at = utcnow()
    sync_log = SyncLog(
        integration_id=key_id,
        service="wb",
        sync_type=sync_type,
        started_at=started_at,
        status="RUNNING",
    )
    db.add(sync_log)
    await db.flush()

    try:
        from backend.integrations.wb_api import WBApiClient, parse_wb_sales_to_payouts

        client = WBApiClient(api_key, project_id=project_id)

        if sync_type == "sales":
            raw_data = await client.get_sales(date_from)
            payouts = parse_wb_sales_to_payouts(raw_data)

            existing_ids: set = set()
            if payouts:
                req_ids = [p["request_id"] for p in payouts]
                result = await db.execute(
                    select(WbPayout.request_id).where(
                        WbPayout.project_id == project_id,
                        WbPayout.is_deleted == False,
                        WbPayout.request_id.in_(req_ids),
                    )
                )
                existing_ids = {r[0] for r in result}

            inserted = 0
            for p in payouts:
                if p["request_id"] in existing_ids:
                    continue
                payout = WbPayout(
                    project_id=project_id,
                    request_id=p["request_id"],
                    amount_rub=Decimal(str(p["amount_rub"])),
                    currency=p["currency"],
                    created_at=p["created_at"],
                    wb_status_raw=p.get("wb_status_raw"),
                    status=p.get("status", "TRANSIT"),
                    imported_at=utcnow(),
                )
                db.add(payout)
                inserted += 1

            sync_log.rows_fetched = len(raw_data)
            sync_log.rows_inserted = inserted

        elif sync_type == "orders":
            raw_data = await client.get_orders(date_from)
            sync_log.rows_fetched = len(raw_data)
            sync_log.rows_inserted = 0

        elif sync_type == "finance":
            date_to = date.today()
            raw_data = await client.get_finance_report(date_from, date_to)
            sync_log.rows_fetched = len(raw_data)
            sync_log.rows_inserted = 0

        sync_log.status = "OK"
        sync_log.finished_at = utcnow()
        key.last_sync_at = utcnow()

        await db.commit()
        await db.refresh(sync_log)

    except Exception as e:
        # The session may be in a failed state (a bad row rejected on commit); a bare
        # commit here would raise PendingRollbackError and mask the real error. Roll
        # back, then record the failure with a FRESH row — the flushed sync_log is
        # expunged by rollback (mirrors sync_wb_nomenclature).
        await db.rollback()
        err_log = SyncLog(
            integration_id=key_id,
            service="wb",
            sync_type=sync_type,
            started_at=started_at,
            finished_at=utcnow(),
            status="ERROR",
            error_msg=str(e)[:1000],
        )
        db.add(err_log)
        await db.commit()
        logger.error("WB sync error: %s", e)
        return err_log

    return sync_log


async def sync_wb_nomenclature(db: AsyncSession, project_id: int) -> SyncLog:
    """
    Sync nomenclature from Wildberries Content API (cards/list).
    Fetches all product cards and upserts into nomenclature table.
    """
    key, api_key = await _get_wb_key(db, project_id)
    key_id = key.id  # capture before any rollback expires the ORM object
    started_at = utcnow()

    sync_log = SyncLog(
        integration_id=key_id,
        service="wb",
        sync_type="nomenclature",
        started_at=started_at,
        status="RUNNING",
    )
    db.add(sync_log)
    await db.flush()

    try:
        from backend.integrations.wb_api import WBApiClient, parse_wb_cards_to_nomenclature

        client = WBApiClient(api_key, project_id=project_id)
        raw_cards = await client.get_cards_list(limit=100)
        nom_items = parse_wb_cards_to_nomenclature(raw_cards)

        inserted = 0
        updated = 0

        for item in nom_items:
            bc = item["barcode"]
            result = await db.execute(
                select(Nomenclature).where(
                    Nomenclature.project_id == project_id,
                    Nomenclature.barcode == bc,
                )
            )
            nom = result.scalar_one_or_none()
            if nom:
                nom.brand = item.get("brand") or nom.brand
                nom.subject = item.get("subject") or nom.subject
                nom.article_seller = item.get("article_seller") or nom.article_seller
                nom.article_wb = item.get("article_wb") or nom.article_wb
                nom.imt_id = item.get("imt_id") or nom.imt_id
                if item.get("volume_l"):
                    nom.volume_l = Decimal(str(item["volume_l"]))
                nom.updated_at = utcnow()
                updated += 1
            else:
                nom = Nomenclature(
                    project_id=project_id,
                    barcode=bc,
                    brand=item.get("brand"),
                    subject=item.get("subject"),
                    article_seller=item.get("article_seller"),
                    article_wb=item.get("article_wb"),
                    imt_id=item.get("imt_id"),
                    volume_l=Decimal(str(item.get("volume_l", 0))),
                )
                db.add(nom)
                inserted += 1

        sync_log.rows_fetched = len(raw_cards)
        sync_log.rows_inserted = inserted
        sync_log.status = "OK"
        sync_log.finished_at = utcnow()
        sync_log.error_msg = (
            f"cards={len(raw_cards)}, barcodes={len(nom_items)}, inserted={inserted}, updated={updated}"
        )

        key.last_sync_at = utcnow()

        # Best-effort: backfill first_sale_date for nm_ids that don't have it yet.
        # Failure here MUST NOT break nomenclature sync (WB /sales is rate-limited
        # ~1 req/min — 429 is normal). We log and continue.
        try:
            from backend.services.cost.first_sale import backfill_first_sale_dates

            fs_result = await backfill_first_sale_dates(db, project_id, only_missing=True)
            sync_log.error_msg = (sync_log.error_msg or "") + f", first_sale_backfill={fs_result}"
        except Exception as fs_err:  # — best-effort
            logger.warning("first_sale_date backfill skipped: %s", fs_err)

        await db.commit()
        await db.refresh(sync_log)

    except Exception as e:
        # A failed flush (e.g. a card value out of column range) poisons the
        # session — `sync_log` and any partial inserts are unusable until we
        # roll back. Without this rollback the ERROR-write below would raise
        # PendingRollbackError and the endpoint would return 500 instead of a
        # proper ERROR SyncLog (the "Внутренняя ошибка сервера" symptom).
        await db.rollback()
        logger.error("WB nomenclature sync error: %s", e)

        err_log = SyncLog(
            integration_id=key_id,
            service="wb",
            sync_type="nomenclature",
            started_at=started_at,
            finished_at=utcnow(),
            status="ERROR",
            error_msg=str(e)[:1000],
        )
        db.add(err_log)
        await db.commit()
        await db.refresh(err_log)
        return err_log

    return sync_log


# ─── Sync Log ─────────────────────────────────────────────────────────────────


async def get_sync_log(
    db: AsyncSession,
    project_id: int,
    service: str | None = None,
    limit: int = 20,
) -> list:
    """Get sync history for a project."""
    key_ids_q = select(IntegrationKey.id).where(
        IntegrationKey.project_id == project_id,
        IntegrationKey.is_deleted.is_(False),
    )
    q = select(SyncLog).where(SyncLog.integration_id.in_(key_ids_q)).order_by(SyncLog.started_at.desc()).limit(limit)
    if service:
        q = q.where(SyncLog.service == service)
    result = await db.execute(q)
    return list(result.scalars().all())
