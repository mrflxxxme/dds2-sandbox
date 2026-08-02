# ruff: noqa: RUF002, RUF003 — русские комментарии и docstring
"""
Service: слежение за поступлением товара (wb_stock_watches).

Сценарий: покупатель спрашивает «когда появится в наличии?» — база знаний это
не покрывает (ответ зависит от будущих остатков). Сервис:
1. Классификатор is_stock_question — русские паттерны вопросов о наличии.
2. scan_stock_questions — backfill: неотвеченные вопросы о наличии → watch
   (идемпотентно, uniq (project_id, question_wb_id)); watch по уже отвеченному
   вопросу → dismissed. Вызывается из sync_project_questions и on-demand.
3. stock_watch_tick — для всех watching: totalQuantity публичной карточки WB
   (card.wb.ru/cards/v4/detail, батчи по 50 nm через «;»). Остатки > 0 →
   черновик ответа (reply_llm с КБ товара + контекст «появился в наличии»;
   без LLM — шаблон) со status='draft' — отправка ТОЛЬКО вручную после
   одобрения (принципиальная политика проекта). Watch → drafted + reply_id.
   Ошибки сети не валят тик — watch остаётся watching.

Сеть: транспорт переиспользуется из wb_cards_service (_http_get_json): прямое
TLS, либо SOCKS5 через env WB_CARDS_SOCKS_PROXY="host:port" (dev с DPI-фильтром).
⚠️ dev-грабля: WAF card.wb.ru режет TLS-фingerprint python-клиента (403) при
доступе НЕ через прокси; basket-хосты (card.json) при этом доступны напрямую.
В проде прямой доступ работает.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import WBFeedbackReply, WBQuestion, WBStockWatch
from backend.services import reply_service
from backend.services.ai import reply_llm
from backend.services.wb_cards_service import _http_get_json, _proxy_from_env
from backend.utils.time import utcnow

logger = logging.getLogger("dds.reviews.stock_watch")

_BATCH_SIZE = 50  # nm_id в одном запросе cards/v4/detail (через «;»)
_THROTTLE_SEC = 0.5  # вежливый троттлинг публичного API между батчами

# Шаблон ответа без LLM
RESTOCK_TEMPLATE = "Здравствуйте! Товар снова в наличии — успейте заказать."

# Правила для LLM-черновика о поступлении
_RESTOCK_RULES = (
    "Покупатель спрашивал, когда товар появится в наличии. Товар СНОВА В НАЛИЧИИ: "
    "сообщи, что можно оформить заказ, поблагодари за ожидание. Не называй точных "
    "сроков и остатков, если их нет в базе знаний."
)

# ─── Классификатор вопросов о наличии ────────────────────────────────────────

# Сильные паттерны: подстрока в нормализованном тексте (lower, схлопнутые пробелы)
_STRONG_PATTERNS: tuple[str, ...] = (
    "появ",        # появится / появятся / появление
    "наличи",      # в наличии / наличия / нет в наличии
    "поступлен",   # поступление / когда поступление
    "поступит",
    "завез",       # завезёте / завезут / завезете
    "привез",      # привезут / привезёте
    "ожидается",   # когда ожидается
    "restock",
    "скоро будет",
)
# «Когда будет …» — только если это НЕ про доставку (доставка ≠ наличие)
_WHEN_BE = "когда будет"
_DELIVERY_WORDS: tuple[str, ...] = ("доставк", "отправк", "придёт", "придет", "пвз", "почт", "курьер")


def is_stock_question(text: str | None) -> bool:
    """True — вопрос покупателя о наличии/поступлении товара (русские паттерны)."""
    t = reply_service._norm_question(text or "")
    if not t:
        return False
    if any(p in t for p in _STRONG_PATTERNS):
        return True
    if _WHEN_BE in t and not any(w in t for w in _DELIVERY_WORDS):
        return True
    return False


# ─── Остатки из публичной карточки WB ────────────────────────────────────────


def fetch_total_quantities(
    nm_ids: list[int], proxy: tuple[str, int] | None = None
) -> dict[int, int]:
    """
    totalQuantity по списку nm_id (card.wb.ru/cards/v4/detail, батчи по _BATCH_SIZE).

    Синхронная (raw-сокет); из async-кода вызывать через asyncio.to_thread.
    nm_id, отсутствующий в выдаче WB, получает 0. HTTP ≠ 200 → RuntimeError
    (тик ловит: сбой сети не должен валить job и трогать watches).
    """
    if proxy is None:
        proxy = _proxy_from_env()
    out: dict[int, int] = {}
    for i in range(0, len(nm_ids), _BATCH_SIZE):
        batch = nm_ids[i : i + _BATCH_SIZE]
        if i:
            import time

            time.sleep(_THROTTLE_SEC)
        nm_param = "%3B".join(str(nm) for nm in batch)
        status, data = _http_get_json(
            "card.wb.ru",
            f"/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=0&nm={nm_param}",
            proxy,
        )
        if status != 200:
            raise RuntimeError(f"WB cards/v4/detail HTTP {status}")
        found = {
            int(p["id"]): int(p.get("totalQuantity") or 0)
            for p in data.get("products") or []
            if isinstance(p, dict) and p.get("id")
        }
        for nm in batch:
            out[int(nm)] = found.get(int(nm), 0)
    return out


# ─── Скан/backfill: вопросы о наличии → watches ──────────────────────────────


async def scan_stock_questions(db: AsyncSession, project_id: int) -> dict:
    """
    Найти неотвеченные вопросы о наличии без watch → создать watching.

    Идемпотентно: существующие watches (по question_wb_id) не дублируются.
    Watch по вопросу, который уже отвечен (is_answered), переводится в dismissed.
    Возвращает {"scanned", "created", "dismissed"}.
    """
    questions = (
        await db.execute(
            select(WBQuestion).where(
                WBQuestion.project_id == project_id,
                WBQuestion.text.isnot(None),
                WBQuestion.nm_id.isnot(None),
            )
        )
    ).scalars().all()
    existing: dict[str, WBStockWatch] = {
        w.question_wb_id: w
        for w in (
            await db.execute(
                select(WBStockWatch).where(WBStockWatch.project_id == project_id)
            )
        ).scalars().all()
    }

    scanned = created = dismissed = 0
    for q in questions:
        watch = existing.get(q.wb_id)
        if watch is not None:
            # вопрос отвечен (вручную/ранее) — слежение больше не нужно
            if q.is_answered and watch.status == "watching":
                watch.status = "dismissed"
                watch.resolved_at = utcnow()
                dismissed += 1
            continue
        if q.is_answered or not is_stock_question(q.text):
            continue
        scanned += 1
        db.add(WBStockWatch(
            project_id=project_id,
            nm_id=q.nm_id,
            question_wb_id=q.wb_id,
            status="watching",
        ))
        created += 1
    await db.commit()

    if created or dismissed:
        logger.info(
            "stock watch scan: project %d — created=%d, dismissed=%d (candidates=%d)",
            project_id, created, dismissed, scanned,
        )
    return {"scanned": scanned, "created": created, "dismissed": dismissed}


# ─── Тик: проверка остатков → черновики ──────────────────────────────────────


async def _draft_restock_reply(
    db: AsyncSession, project_id: int, watch: WBStockWatch, question: WBQuestion | None
) -> tuple[str, str]:
    """
    Текст черновика «товар появился» → (text, generation).

    LLM (если настроен ключ): КБ товара + контекст поступления; сбой/нет ключа
    — шаблон RESTOCK_TEMPLATE. generation: 'llm' | 'template'.
    """
    if (settings.COMPLAINT_LLM_API_KEY or "").strip():
        try:
            kb_map = await reply_service.load_kb_map(db, project_id, [int(watch.nm_id)])
            kb_entries = reply_service.rank_kb_entries(
                kb_map.get(int(watch.nm_id)) or [], (question.text if question else "") or ""
            )
            parsed = await reply_llm.draft_reply(
                "openai_compatible", "deepseek-chat", None,
                _RESTOCK_RULES, None,
                {
                    "name": (question.product_name if question else "") or f"nmID {watch.nm_id}",
                    "subject": (question.subject if question else "") or "",
                    "brand": (question.brand if question else "") or "",
                },
                {"text": (question.text if question else "") or "Когда появится в наличии?"},
                "question",
                kb_entries=kb_entries,
            )
            if parsed["reply_text"]:
                return parsed["reply_text"], "llm"
        except Exception as e:  # noqa: BLE001 — недоступность LLM не валит тик: шаблон
            logger.warning("stock watch: LLM restock draft failed (nm %d): %s", watch.nm_id, e)
    return RESTOCK_TEMPLATE, "template"


async def stock_watch_tick(
    db: AsyncSession,
    project_id: int,
    *,
    fetcher: Callable[..., Any] | None = None,
) -> dict:
    """
    Проверить остатки по watching-watches проекта; появившиеся → черновики draft.

    fetcher — async nm_ids → {nm: qty} (тесты/dev); по умолчанию
    fetch_total_quantities в потоке. Сетевая ошибка fetcher'а → тик завершается
    с errors=1, watches не трогаем. Возвращает {checked, drafted, waiting, errors}.
    """
    watches = (
        await db.execute(
            select(WBStockWatch).where(
                WBStockWatch.project_id == project_id,
                WBStockWatch.status == "watching",
            )
        )
    ).scalars().all()
    if not watches:
        return {"checked": 0, "drafted": 0, "waiting": 0, "errors": 0}

    nm_ids = sorted({int(w.nm_id) for w in watches})
    if fetcher is None:

        async def fetcher(ids: list[int]) -> dict[int, int]:  # type: ignore[no-redef]
            return await asyncio.to_thread(fetch_total_quantities, ids)

    try:
        quantities = await fetcher(nm_ids)
    except Exception as e:  # noqa: BLE001 — сбой сети/WAF: ждём следующий тик
        logger.warning("stock watch tick: project %d — stock fetch failed: %s", project_id, e)
        await db.rollback()
        return {"checked": 0, "drafted": 0, "waiting": len(watches), "errors": 1}

    checked = drafted = waiting = errors = 0
    for watch in watches:
        checked += 1
        qty = int(quantities.get(int(watch.nm_id)) or 0)
        watch.last_qty = qty  # фиксируем остаток при каждой проверке (и waiting, и drafted)
        if qty <= 0:
            waiting += 1
            continue
        try:
            # На вопрос уже есть открытый ответ — дубль не нужен, снимаем слежение
            busy = await db.scalar(
                select(WBFeedbackReply.id).where(
                    WBFeedbackReply.project_id == project_id,
                    WBFeedbackReply.target_type == "question",
                    WBFeedbackReply.target_wb_id == watch.question_wb_id,
                    WBFeedbackReply.status.in_(reply_service._BUSY_STATUSES),
                ).limit(1)
            )
            if busy is not None:
                watch.status = "dismissed"
                watch.resolved_at = utcnow()
                await db.commit()
                continue

            question = await db.scalar(
                select(WBQuestion).where(
                    WBQuestion.project_id == project_id,
                    WBQuestion.wb_id == watch.question_wb_id,
                )
            )
            text, generation = await _draft_restock_reply(db, project_id, watch, question)
            reply = WBFeedbackReply(
                project_id=project_id,
                target_type="question",
                target_wb_id=watch.question_wb_id,
                draft_text=text,
                status="draft",  # только ручное одобрение — политика проекта
                source="agent",
                is_stock_reply=True,
                generation=generation,
            )
            db.add(reply)
            await db.flush()
            watch.status = "drafted"
            watch.reply_id = reply.id
            watch.resolved_at = utcnow()
            await db.commit()
            drafted += 1
        except Exception as e:  # noqa: BLE001 — одна цель не валит тик
            await db.rollback()
            errors += 1
            logger.warning("stock watch tick: watch %d failed: %s", watch.id, e)

    # last_qty по waiting-часам (drafted/dismissed коммитятся в своих ветках выше)
    await db.commit()

    logger.info(
        "stock watch tick: project %d — checked=%d, drafted=%d, waiting=%d, errors=%d",
        project_id, checked, drafted, waiting, errors,
    )
    return {"checked": checked, "drafted": drafted, "waiting": waiting, "errors": errors}


# ─── Список для UI ────────────────────────────────────────────────────────────


def _watch_to_dict(w: WBStockWatch, question: WBQuestion | None = None) -> dict:
    return {
        "id": w.id,
        "nm_id": w.nm_id,
        "question_wb_id": w.question_wb_id,
        "status": w.status,
        "reply_id": w.reply_id,
        "last_qty": w.last_qty,
        "created_at": w.created_at.isoformat() if w.created_at else None,
        "resolved_at": w.resolved_at.isoformat() if w.resolved_at else None,
        "question_text": question.text if question else None,
        "product_name": question.product_name if question else None,
    }


async def dismiss_stock_watch(db: AsyncSession, project_id: int, watch_id: int) -> dict | None:
    """
    Снять слежение вручную: watching → dismissed.

    None — watch не найден в проекте (404); ValueError — статус не watching (409).
    """
    watch = await db.scalar(
        select(WBStockWatch).where(
            WBStockWatch.id == watch_id,
            WBStockWatch.project_id == project_id,
        )
    )
    if watch is None:
        return None
    if watch.status != "watching":
        raise ValueError(f"Снять можно только активное слежение (текущий статус: {watch.status})")
    watch.status = "dismissed"
    watch.resolved_at = utcnow()
    await db.commit()
    question = await db.scalar(
        select(WBQuestion).where(
            WBQuestion.project_id == project_id,
            WBQuestion.wb_id == watch.question_wb_id,
        )
    )
    return _watch_to_dict(watch, question)


async def list_stock_watches(
    db: AsyncSession,
    project_id: int,
    *,
    status: str | None = None,
    take: int = 100,
    skip: int = 0,
) -> dict:
    """Список watches проекта (+ текст вопроса/товар из зеркала) и счётчики статусов."""
    stmt = select(WBStockWatch).where(WBStockWatch.project_id == project_id)
    if status:
        stmt = stmt.where(WBStockWatch.status == status)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (
        await db.execute(
            stmt.order_by(WBStockWatch.created_at.desc()).limit(take).offset(skip)
        )
    ).scalars().all()

    items = []
    for w in rows:
        q = await db.scalar(
            select(WBQuestion).where(
                WBQuestion.project_id == project_id,
                WBQuestion.wb_id == w.question_wb_id,
            )
        )
        items.append(_watch_to_dict(w, q))

    counts = {
        s: int(await db.scalar(
            select(func.count(WBStockWatch.id)).where(
                WBStockWatch.project_id == project_id, WBStockWatch.status == s
            )
        ) or 0)
        for s in ("watching", "drafted", "dismissed")
    }
    return {"items": items, "total": int(total or 0), "counts": counts}


async def watched_question_ids(db: AsyncSession, project_id: int) -> set[str]:
    """wb_id вопросов с активным слежением (watching) — для бейджа в списке вопросов."""
    rows = (
        await db.execute(
            select(WBStockWatch.question_wb_id).where(
                WBStockWatch.project_id == project_id,
                WBStockWatch.status == "watching",
            )
        )
    ).all()
    return {r[0] for r in rows}
