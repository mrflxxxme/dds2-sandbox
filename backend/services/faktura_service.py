"""
Service: Faktura.ru (ВБ Банк) statement auto-sync.

Pulls the bank statement for every account via the Faktura JSON API and feeds the
operations into the SAME ETL pipeline as the manual ``/p/<slug>/import`` upload
(``etl.service.persist_df``) — so categorization, dedup by ``txn_id`` and the
inter-account «перемещение» handling (``is_internal``) all behave identically.

Credentials live in ``IntegrationKey`` (service="faktura"): ``encrypted_key`` =
Fernet-encrypted password, ``config={"login": ..., "host": ...}``.

Async/sync bridge: the API calls are async (httpx); the ETL persist is sync ORM,
run in a thread executor (same pattern as ``routers/import_txn.py``).
"""

import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.integrations.faktura_api import DEFAULT_HOST, FakturaApiClient
from backend.models import IntegrationKey, SyncLog
from backend.utils.crypto import decrypt as _decrypt
from backend.utils.time import utcnow

logger = logging.getLogger("dds.faktura")

DEFAULT_LOOKBACK_DAYS = 14  # rolling window pulled each run (dedup makes overlap safe)


async def _get_faktura_key(db: AsyncSession, project_id: int) -> tuple[IntegrationKey, str, str]:
    """Get the active Faktura key + decrypted (login, password). Raises ValueError if absent."""
    result = await db.execute(
        select(IntegrationKey).where(
            IntegrationKey.project_id == project_id,
            IntegrationKey.service == "faktura",
            IntegrationKey.is_active.is_(True),
            IntegrationKey.is_deleted.is_(False),
        )
    )
    key = result.scalar_one_or_none()
    if not key:
        raise ValueError("Faktura.ru ключ не найден. Добавьте через scripts/setup_faktura_account.py")
    login = (key.config or {}).get("login") or (key.label or "")
    if not login:
        raise ValueError("Faktura.ru: в ключе не задан login (config.login)")
    return key, login, _decrypt(key.encrypted_key)


def _persist_faktura_sync(project_id: int, account_ops: dict[str, list[dict]]) -> tuple[int, int, int]:
    """Sync-side: ensure accounts, convert ops → df, persist via shared ETL core.

    Runs inside a thread executor (sync ORM session). Returns
    ``(inserted, skipped, fetched)``.
    """
    from backend.database import SyncSessionLocal
    from backend.etl.parsers.faktura_api import faktura_ops_to_df, faktura_ops_to_requisites
    from backend.etl.service import _ensure_account, _invalidate_reports_sync, persist_df
    from backend.services.counterparty_enrich import enrich_counterparty_requisites

    fetched = sum(len(v or []) for v in account_ops.values())

    with SyncSessionLocal() as db:
        # Both settlement accounts must exist as our_account → drives is_internal
        # for «перемещение между счетами» (existing rows are left untouched).
        for acc_no in account_ops:
            _ensure_account(db, acc_no, "FAKTURA_WB_BANK", project_id)
        db.flush()

        df, parse_skipped = faktura_ops_to_df(account_ops)
        if df.empty:
            db.commit()
            return 0, parse_skipped, fetched

        primary_acc = next(iter(account_ops), "")
        inserted, skipped = persist_df(db, df, project_id, primary_acc)
        # БИК/КПП/банк контрагентов — только из сырых ops Faktura (в transactions их нет).
        enrich_counterparty_requisites(db, project_id, ops_requisites=faktura_ops_to_requisites(account_ops))
        db.commit()

    _invalidate_reports_sync(project_id)
    return inserted, skipped + parse_skipped, fetched


async def sync_faktura_statement(
    db: AsyncSession,
    project_id: int,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> SyncLog:
    """Pull the statement for all accounts (rolling window) → transactions. Returns the SyncLog."""
    key, login, password = await _get_faktura_key(db, project_id)
    key_id = key.id  # capture before any rollback expires the ORM object
    host = (key.config or {}).get("host") or DEFAULT_HOST
    started_at = utcnow()

    sync_log = SyncLog(
        integration_id=key_id,
        service="faktura",
        sync_type="statement",
        started_at=started_at,
        status="RUNNING",
    )
    db.add(sync_log)
    await db.flush()

    try:
        date_to = date.today()
        date_from = date_to - timedelta(days=lookback_days)

        account_ops: dict[str, list[dict]] = {}
        async with FakturaApiClient(login, password, host) as client:
            await client.authenticate()
            accounts = await client.get_accounts()
            for acc in accounts:
                acc_id = acc.get("id")
                acc_no = acc.get("number")
                if acc_id is None or not acc_no:
                    continue
                account_ops[str(acc_no)] = await client.get_motions(acc_id, date_from, date_to)

        loop = asyncio.get_running_loop()
        inserted, skipped, fetched = await loop.run_in_executor(
            None, _persist_faktura_sync, project_id, account_ops
        )

        sync_log.status = "OK"
        sync_log.rows_fetched = fetched
        sync_log.rows_inserted = inserted
        sync_log.finished_at = utcnow()
        key.last_sync_at = utcnow()
        await db.commit()
        await db.refresh(sync_log)

        logger.info(
            "Faktura statement sync: project %d — accounts=%d, fetched=%d, inserted=%d, skipped=%d",
            project_id,
            len(account_ops),
            fetched,
            inserted,
            skipped,
        )

    except Exception as e:
        # Rollback may expire sync_log → write a fresh ERROR record on a clean session.
        await db.rollback()
        logger.error("Faktura statement sync error (project %d): %s", project_id, e)
        err_log = SyncLog(
            integration_id=key_id,
            service="faktura",
            sync_type="statement",
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
