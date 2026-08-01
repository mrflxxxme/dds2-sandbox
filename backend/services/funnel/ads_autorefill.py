"""Родное автопополнение бюджета ВБ — чтение и запись настройки кабинета.

Своей логики доливов у нас НЕТ: правило хранится и исполняется на стороне ВБ,
мы лишь показываем и меняем его. Публичный `advert-api` эту настройку не отдаёт,
поэтому ходим кабинетной сессией (`wb_portal_session`, заголовок authorizev3) —
см. `WbPortalClient.fetch_autorefill` / `save_autorefill`.

Ответ наружу всегда несёт `session` (ACTIVE / EXPIRED / NONE): по протухшей сессии
UI обязан показать «доступ к кабинету потерян», а не пустую настройку — иначе
менеджер решит, что автопополнение выключено, и останется без бюджета.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("dds.ads_autorefill")

#: Минимальная сумма долива у ВБ (ограничение кабинета, оно же в модалке).
MIN_TOPUP_RUB = 1000
#: Сколько записей истории отдаём в UI (кабинет присылает всю).
HISTORY_LIMIT = 30


def _num(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def to_ui(settings: dict) -> dict:
    """Ответ кабинета → плоский вид для фронта (рубли, без *_cents)."""
    source = settings.get("source") if isinstance(settings.get("source"), dict) else {}
    history = []
    for e in (settings.get("history") or [])[:HISTORY_LIMIT]:
        if not isinstance(e, dict):
            continue
        history.append({
            "id": str(e.get("id") or ""),
            "date": e.get("date"),
            "source": e.get("source"),  # net — баланс взаиморасчётов, account — счёт
            "sum": _num(e.get("sum")),
        })
    return {
        "enabled": bool(settings.get("is_enable")),
        "threshold": _num(settings.get("bet_min")),
        "amount": _num(settings.get("bet_sum")),
        "daily_limit": bool(settings.get("is_daily_limit")),
        "limit": int(_num(settings.get("limit"), 1)),
        "unified_account": bool(source.get("unified_account", True)),
        "status": settings.get("status"),  # working — правило живо на стороне ВБ
        "history": history,
    }


def to_wb(payload: dict) -> dict:
    """Форма фронта → тело кабинета. Шлём ровно те поля, что шлёт сам кабинет."""
    return {
        "bet_min": int(_num(payload.get("threshold"))),
        "bet_sum": int(_num(payload.get("amount"))),
        "is_daily_limit": bool(payload.get("daily_limit")),
        "limit": max(1, int(_num(payload.get("limit"), 1))),
        "is_enable": bool(payload.get("enabled")),
        "source": {"unified_account": bool(payload.get("unified_account", True))},
    }


def validate(payload: dict) -> str | None:
    """Причина отказа или None. Бережём от заведомо отбиваемых кабинетом правил."""
    if not payload.get("enabled"):
        return None  # выключение не требует корректных сумм
    if _num(payload.get("amount")) < MIN_TOPUP_RUB:
        return f"Минимальная сумма пополнения у ВБ — {MIN_TOPUP_RUB} ₽."
    if _num(payload.get("threshold")) < 0:
        return "Порог остатка не может быть отрицательным."
    if payload.get("daily_limit") and int(_num(payload.get("limit"), 1)) < 1:
        return "Количество пополнений в день — не меньше одного."
    return None


async def _with_client(db: AsyncSession, project_id: int, call):
    """Общая обвязка: собрать клиент кабинета, выполнить call, разобрать ошибки.

    Возвращает (данные | None, session-статус). Протухшую сессию помечаем EXPIRED,
    чтобы страница интеграций сразу просила свежий доступ.
    """
    from backend.integrations.wb_portal_client import WbPortalError, WbSessionExpired
    from backend.services.integrations_service import get_wb_portal_client, mark_wb_portal_expired

    try:
        client = await get_wb_portal_client(db, project_id)
    except ValueError:
        return None, "NONE"

    try:
        return await call(client), "ACTIVE"
    except WbSessionExpired:
        await mark_wb_portal_expired(db, project_id)
        return None, "EXPIRED"
    except WbPortalError as e:
        logger.warning("Автопополнение ВБ: проект %s — %s", project_id, e)
        raise
    finally:
        await client.aclose()


async def get_autorefill(db: AsyncSession, project_id: int, campaign_id: int) -> dict:
    """Настройка автопополнения кампании из кабинета ВБ + история доливов."""
    data, session = await _with_client(
        db, project_id, lambda c: c.fetch_autorefill(campaign_id)
    )
    return {"session": session, "settings": to_ui(data) if data is not None else None}


async def save_autorefill(db: AsyncSession, project_id: int, campaign_id: int, payload: dict) -> dict:
    """Записать настройку в кабинет ВБ. Возвращает то, что кабинет подтвердил."""
    reason = validate(payload)
    if reason:
        return {"ok": False, "session": "ACTIVE", "error": reason, "settings": None}

    body = to_wb(payload)
    saved, session = await _with_client(
        db, project_id, lambda c: c.save_autorefill(campaign_id, body)
    )
    if session != "ACTIVE":
        return {"ok": False, "session": session, "error": None, "settings": None}

    # Кабинет может ответить пустым телом — тогда показываем то, что отправили.
    settings = to_ui(saved) if saved else to_ui({**body, "is_enable": body["is_enable"]})
    return {"ok": True, "session": session, "error": None, "settings": settings}
