"""
Service: VTB «Интеграционный Банк-Клиент» — авто-синк выписки (read-only).

Зеркало ``faktura_service``: тянет выписку ВТБ по ИБК (SOAP H2H) и кладёт операции в
ТОТ ЖЕ ETL-конвейер, что и ручной импорт (``etl.service.persist_df``) — категоризация,
дедуп по ``txn_id`` и ``is_internal`` («перемещение между счетами») работают одинаково.

Креды в ``IntegrationKey(service="vtb_ibk")``: ``encrypted_key`` = PIN ключа (или ""),
``config = {custid, endpoint, cert_thumbprint, cert_serial, kbopid, accounts:[{account,bic,currency}]}``.

ВТБ авторизует mutual-TLS клиентским сертификатом — отдельного login-вызова нет.
Подпись/транспорт идут через КриптоПро (``csptest``/``cprocsp-curl``, subprocess) →
весь fetch блокирующий, гоним в thread-executor (как sync-ORM в faktura).

⚠ Парсер ответа выписки — SEAM (``vtb_ibk_client.parse_statement_to_norm``):
достраивается по живому ответу тест-стенда / companion-спеке. До этого синк проходит
предзаказ→поллинг→получение, но кладёт 0 строк. См. [[vtb-ibk-integration]].
"""

import asyncio
import logging
import time
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.integrations import vtb_ibk_client as vc
from backend.models import IntegrationKey, SyncLog
from backend.utils.time import utcnow

logger = logging.getLogger("dds.vtb")

SERVICE = "vtb_ibk"
DEFAULT_LOOKBACK_DAYS = 14
_POLL_ATTEMPTS = 30
_POLL_SLEEP_SEC = 2.0
# Cooperative deadline for the blocking account×day walk. The executor thread is NOT
# cancellable, so the job's wait_for(900) cannot stop it — this budget (< 900, with margin
# for one in-flight ~90s HTTP call) lets the loop exit itself and persist partial data.
_SYNC_BUDGET_SEC = 780


async def _get_vtb_key(db: AsyncSession, project_id: int) -> IntegrationKey:
    """Активный ключ ИБК проекта. Raises ValueError, если нет."""
    result = await db.execute(
        select(IntegrationKey).where(
            IntegrationKey.project_id == project_id,
            IntegrationKey.service == SERVICE,
            IntegrationKey.is_active.is_(True),
            IntegrationKey.is_deleted.is_(False),
        )
    )
    key = result.scalar_one_or_none()
    if not key:
        raise ValueError("VTB ИБК ключ не найден. Добавьте через scripts/setup_vtb_account.py")
    return key


def _fetch_day_rows(
    *, endpoint: str, custid: str, thumb: str, uid: str, kbopid: str,
    account: str, bic: str, day: date,
) -> list[dict]:
    """Один (счёт, день): подпись → предзаказ → поллинг статуса → получение выписки."""
    ddmmyyyy = day.strftime("%d.%m.%Y")
    iso = day.strftime("%Y-%m-%d")
    doc_number = day.strftime("%y%j")  # YYjjj; точная схема нумерации — на тест-стенде

    sign_obj = vc.build_statement_sign_object(
        doc_date=ddmmyyyy, doc_number=doc_number, custid=custid, account=account,
        date_from=ddmmyyyy, date_to=ddmmyyyy, kbopid=kbopid,
    )
    sign_b64 = vc.sign_detached(sign_obj, thumb)
    preorder = vc.build_statement_preorder(
        custid=custid, doc_date=ddmmyyyy, doc_number=doc_number, account=account,
        date_from=ddmmyyyy, date_to=ddmmyyyy, kbopid=kbopid, sign_b64=sign_b64, uid=uid,
    )
    record_id = vc.parse_record_id(vc.post(url=endpoint, body=preorder, thumbprint=thumb))
    if not record_id:
        logger.warning("VTB: предзаказ без RecordID (acc=%s day=%s)", account, iso)
        return []

    status = None
    for _ in range(_POLL_ATTEMPTS):
        st = vc.parse_doc_status(
            vc.post(url=endpoint, body=vc.build_get_doc_status(custid=custid, record_id=record_id), thumbprint=thumb)
        )
        if st == vc.STATUS_READY:
            status = st
            break
        if st == vc.STATUS_REQ_ERROR:
            logger.warning("VTB: ошибка реквизитов предзаказа (acc=%s day=%s)", account, iso)
            return []
        time.sleep(_POLL_SLEEP_SEC)
    if status != vc.STATUS_READY:
        logger.warning("VTB: предзаказ не готов за %d попыток (acc=%s day=%s)", _POLL_ATTEMPTS, account, iso)
        return []

    stmt_resp = vc.post(
        url=endpoint, body=vc.build_get_statement(custid=custid, account=account, bic=bic, statement_date=iso), thumbprint=thumb
    )
    rows, _skipped = vc.parse_statement_to_norm(vc.extract_statement_payload(stmt_resp) or "")
    return rows


def _run_vtb_blocking(
    project_id: int, config: dict, date_from: date, date_to: date, deadline: float | None = None
) -> tuple[int, int, int, bool]:
    """Sync-side (thread-executor): обход счёт×день через КриптоПро → persist_df."""
    import pandas as pd

    from backend.database import SyncSessionLocal
    from backend.etl.parsers.helpers import NORM_COLS
    from backend.etl.service import _ensure_account, _invalidate_reports_sync, persist_df

    custid = str(config["custid"])
    endpoint = config.get("endpoint") or vc.ENDPOINT_PROD
    thumb = str(config["cert_thumbprint"])
    uid = str(config.get("cert_serial") or "")
    kbopid = str(config.get("kbopid") or "0")
    accounts = config.get("accounts") or []  # [{account, bic, currency}]

    days = [date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)]
    norm_rows: list[dict] = []
    partial = False
    for acc in accounts:
        for day in days:
            if deadline is not None and time.monotonic() > deadline:
                logger.warning(
                    "VTB: sync budget exhausted — persisting partial statement (project %d)", project_id
                )
                partial = True
                break
            norm_rows.extend(
                _fetch_day_rows(
                    endpoint=endpoint, custid=custid, thumb=thumb, uid=uid, kbopid=kbopid,
                    account=str(acc["account"]), bic=str(acc.get("bic") or ""), day=day,
                )
            )
        if partial:
            break

    fetched = len(norm_rows)
    if not norm_rows:
        return 0, 0, fetched, partial

    with SyncSessionLocal() as db:
        for acc in accounts:
            src = "VTB_CNY" if str(acc.get("currency") or "").upper() == "CNY" else "VTB_RUB_MAIN"
            _ensure_account(db, str(acc["account"]), src, project_id)
        db.flush()
        df = pd.DataFrame(norm_rows, columns=NORM_COLS)
        primary = str(accounts[0]["account"]) if accounts else ""
        inserted, skipped = persist_df(db, df, project_id, primary)
        db.commit()
    _invalidate_reports_sync(project_id)
    return inserted, skipped, fetched, partial


async def sync_vtb_statement(
    db: AsyncSession, project_id: int, lookback_days: int = DEFAULT_LOOKBACK_DAYS
) -> SyncLog:
    """Тянет выписку ВТБ по всем счетам (скользящее окно) → транзакции. Возвращает SyncLog."""
    key = await _get_vtb_key(db, project_id)
    key_id = key.id
    config = dict(key.config or {})
    started_at = utcnow()

    sync_log = SyncLog(
        integration_id=key_id, service=SERVICE, sync_type="statement",
        started_at=started_at, status="RUNNING",
    )
    db.add(sync_log)
    await db.flush()

    try:
        date_to = date.today()
        date_from = date_to - timedelta(days=lookback_days)
        loop = asyncio.get_running_loop()
        deadline = time.monotonic() + _SYNC_BUDGET_SEC
        inserted, skipped, fetched, partial = await loop.run_in_executor(
            None, _run_vtb_blocking, project_id, config, date_from, date_to, deadline
        )
        sync_log.status = "OK"
        sync_log.rows_fetched = fetched
        sync_log.rows_inserted = inserted
        if partial:
            sync_log.error_msg = "deadline: partial statement (narrow window or raise budget)"
        sync_log.finished_at = utcnow()
        key.last_sync_at = utcnow()
        await db.commit()
        await db.refresh(sync_log)
        logger.info(
            "VTB statement sync: project %d — fetched=%d, inserted=%d, skipped=%d",
            project_id, fetched, inserted, skipped,
        )
    except Exception as e:
        await db.rollback()
        logger.error("VTB statement sync error (project %d): %s", project_id, e)
        err_log = SyncLog(
            integration_id=key_id, service=SERVICE, sync_type="statement",
            started_at=started_at, finished_at=utcnow(), status="ERROR",
            error_msg=str(e)[:1000],
        )
        db.add(err_log)
        await db.commit()
        await db.refresh(err_log)
        return err_log

    return sync_log
