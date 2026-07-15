"""Синк рекламных финансов: история затрат (upd) и пополнений (payments).

Оба — «сырые данные» рекламного кабинета WB, копим у себя для раздела «Сырые данные»
и последующей аналитики. Идемпотентно: повторная заливка периода не плодит дублей.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.integrations import WbAdPayment, WbAdUpd

logger = logging.getLogger(__name__)


# asyncpg не принимает >32767 параметров на statement; у wb_ad_upd 10 колонок →
# потолок ~3276 строк. 2000 — с запасом и для payments (8 колонок).
_INSERT_CHUNK = 2000


def _insert_chunks(rows: list[dict], size: int = _INSERT_CHUNK):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def _parse_dt(value: str | None) -> datetime | None:
    """ISO-строка WB (с tz или без, 'Z' или '+03:00', пробел вместо 'T') → naive UTC."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _upd_rows(project_id: int, items: list[dict]) -> list[dict]:
    """Строки WB /adv/v1/upd → строки таблицы. Без upd_time пропускаем (нельзя атрибутировать)."""
    rows: dict[tuple, dict] = {}
    for it in items:
        t = _parse_dt(it.get("updTime"))
        if t is None:
            continue
        advert_id = it.get("advertId")
        upd_num = it.get("updNum") or 0
        upd_sum = it.get("updSum") or 0
        # Дедуп в Python до вставки: WB может вернуть полные дубли (CardinalityViolation иначе)
        key = (project_id, advert_id, t, upd_num, float(upd_sum))
        rows[key] = {
            "project_id": project_id, "advert_id": advert_id, "upd_time": t,
            "upd_sum": upd_sum, "upd_num": upd_num, "camp_name": it.get("campName"),
            "advert_type": it.get("advertType"), "payment_type": it.get("paymentType"),
            "advert_status": it.get("advertStatus"), "currency": it.get("currency"),
        }
    return list(rows.values())


def _payment_rows(project_id: int, items: list[dict]) -> list[dict]:
    rows: dict[tuple, dict] = {}
    for it in items:
        wb_id = it.get("id")
        paid_at = _parse_dt(it.get("date"))
        if wb_id is None or paid_at is None:
            continue
        rows[(project_id, wb_id)] = {
            "project_id": project_id, "wb_id": wb_id, "paid_at": paid_at,
            "amount": it.get("sum") or 0, "payment_type": it.get("type"),
            "status_id": it.get("statusId"), "currency": it.get("currency"),
            "card_status": (it.get("cardStatus") or "").strip() or None,
        }
    return list(rows.values())


async def sync_ad_upd(db: AsyncSession, project_id: int, date_from: str, date_to: str) -> dict:
    """История затрат за период → wb_ad_upd. Списание неизменяемо → ON CONFLICT DO NOTHING."""
    from backend.services.funnel.wb_advertising_api import fetch_adv_upd
    from backend.services.funnel.ads_manager import _get_advert_api_key

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"status": "error", "error": "Не настроен API-ключ «Продвижение»", "rows": 0}

    items = await fetch_adv_upd(api_key, date_from, date_to)
    rows = _upd_rows(project_id, items)
    if rows:
        for chunk in _insert_chunks(rows):
            stmt = pg_insert(WbAdUpd).values(chunk)
            await db.execute(stmt.on_conflict_do_nothing(constraint="uq_ad_upd"))
        await db.commit()
    logger.info("Ad upd sync: project %s, %s..%s — %s строк (из %s от WB)", project_id, date_from, date_to, len(rows), len(items))
    return {"status": "ok", "rows": len(rows), "fetched": len(items)}


async def sync_ad_payments(db: AsyncSession, project_id: int, date_from: str, date_to: str) -> dict:
    """История пополнений за период → wb_ad_payments. Статус может меняться → DO UPDATE."""
    from backend.services.funnel.wb_advertising_api import fetch_adv_payments
    from backend.services.funnel.ads_manager import _get_advert_api_key

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"status": "error", "error": "Не настроен API-ключ «Продвижение»", "rows": 0}

    items = await fetch_adv_payments(api_key, date_from, date_to)
    rows = _payment_rows(project_id, items)
    if rows:
        for chunk in _insert_chunks(rows):
            stmt = pg_insert(WbAdPayment).values(chunk)
            await db.execute(
                stmt.on_conflict_do_update(
                    constraint="uq_ad_payment",
                    set_={
                        "amount": stmt.excluded.amount, "status_id": stmt.excluded.status_id,
                        "card_status": stmt.excluded.card_status, "paid_at": stmt.excluded.paid_at,
                    },
                )
            )
        await db.commit()
    logger.info("Ad payments sync: project %s, %s..%s — %s строк", project_id, date_from, date_to, len(rows))
    return {"status": "ok", "rows": len(rows), "fetched": len(items)}
