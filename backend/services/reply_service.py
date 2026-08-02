# ruff: noqa: RUF002 — русские комментарии и docstring
"""
Service: ИИ-автоответы на отзывы и вопросы покупателей WB.

- sync_project_questions — зеркалирование вопросов (wb_questions), паттерн
  wb_reviews_sync: пагинация + upsert по (project_id, wb_id), троттлинг 1 rps.
- CRUD ИИ-агентов автоответов (wb_reply_agents) + прогон run_reply_agent:
  отбор неотвеченных отзывов/вопросов без открытого ответа (лимит _RUN_LIMIT),
  генерация черновика СТРОГО из базы знаний товара (wb_product_kb) через LLM
  (сменный провайдер) → wb_feedback_replies, ВСЕГДА status=draft (ручное
  одобрение; agent.auto_send осознанно игнорируется). Нет фактов в КБ —
  черновик с needs_info=True без вызова LLM; без LLM-ключа — fallback
  kb_direct (точное совпадение → эталонный ответ КБ).
- База знаний товаров (wb_product_kb): CRUD + import_kb_from_answered_questions
  (архив отвеченных вопросов → эталонные пары вопрос/ответ, дедуп по md5).
- send_pending_replies — отправка approved-ответов в WB с троттлингом (1 rps):
  успех → sent + is_answered в зеркале; ошибка → error + текст.
- Ручные черновики для UI: create_draft / update_draft / approve / reject.

LLM вызывается ВНЕ открытых транзакций БД (read-транзакция закрывается до
походов в модель); частичные ошибки LLM/WB не валят прогон.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (
    Nomenclature,
    WBFeedback,
    WBFeedbackReply,
    WBProductCard,
    WBProductKB,
    WBQuestion,
    WBReplyAgent,
)
from backend.services.ai import reply_llm
from backend.utils.time import utcnow

logger = logging.getLogger("dds.reviews.replies")

_RUN_LIMIT = 25  # сколько целей агент обрабатывает LLM за один прогон
_SEND_LIMIT = 50  # сколько approved-ответов отправляем за один прогон sender'а
_THROTTLE_SEC = 1.1  # лимит WB на методы отзывов — 1 rps (3 rps → блок 60 сек)

# База знаний: сколько записей максимум подсовываем в промпт на одну цель
_KB_LIMIT = 30

# Вопросы: take ≤ 10000 за запрос, но страница 5000 как у отзывов — меньше память.
_Q_PAGE = 5000
_Q_MAX_PAGES = 20  # до 100k вопросов на прогон (защита от runaway-пагинации)
_UPSERT_BATCH = 1000


def _parse_ints(raw: str | None) -> list[int]:
    if not raw:
        return []
    out: list[int] = []
    for p in raw.replace(";", ",").split(","):
        p = p.strip()
        if p.isdigit():
            out.append(int(p))
    return out


def _parse_dt(raw: str | None) -> datetime | None:
    """WB createdDate (ISO, часто с 'Z') → naive UTC datetime."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(tz=None).replace(tzinfo=None)
    return dt


# ─── Синк вопросов (зеркало) ─────────────────────────────────────────────────


def _row_from_question(project_id: int, q: dict, now: datetime) -> dict | None:
    """WB question dict → строка wb_questions (или None, если нет id)."""
    wb_id = str(q.get("id") or "").strip()
    if not wb_id:
        return None
    pd = q.get("productDetails") or {}
    answer = q.get("answer") or {}
    answer_text = (answer.get("text") or "").strip() or None if isinstance(answer, dict) else None
    return {
        "project_id": project_id,
        "wb_id": wb_id,
        "nm_id": pd.get("nmId"),
        "text": (q.get("text") or "").strip() or None,
        "answer_text": answer_text,
        # WB отдаёт isAnswered; фолбэк — наличие answer
        "is_answered": bool(q.get("isAnswered")) or bool(answer_text),
        "created_date": _parse_dt(q.get("createdDate")),
        "user_name": (q.get("userName") or "").strip() or None,
        "subject": (pd.get("subjectName") or "").strip() or None,
        "product_name": (pd.get("productName") or "").strip() or None,
        "article": (pd.get("supplierArticle") or "").strip() or None,
        "brand": (pd.get("brandName") or "").strip() or None,
        "synced_at": now,
    }


async def _upsert_question_rows(db: AsyncSession, rows: list[dict]) -> int:
    """Upsert строк по (project_id, wb_id). Возвращает число обработанных строк."""
    if not rows:
        return 0
    for i in range(0, len(rows), _UPSERT_BATCH):
        batch = rows[i : i + _UPSERT_BATCH]
        stmt = pg_insert(WBQuestion).values(batch)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_wb_questions_project_wb_id",
            set_={
                "nm_id": stmt.excluded.nm_id,
                "text": stmt.excluded.text,
                "answer_text": stmt.excluded.answer_text,
                "is_answered": stmt.excluded.is_answered,
                "created_date": stmt.excluded.created_date,
                "user_name": stmt.excluded.user_name,
                "subject": stmt.excluded.subject,
                "product_name": stmt.excluded.product_name,
                "article": stmt.excluded.article,
                "brand": stmt.excluded.brand,
                "synced_at": stmt.excluded.synced_at,
                "updated_at": utcnow(),
            },
        )
        await db.execute(stmt)
    return len(rows)


async def sync_project_questions(
    db: AsyncSession,
    project_id: int,
    api_key: str,
    full_backfill: bool = False,
) -> dict:
    """
    Синхронизировать вопросы проекта из WB в wb_questions.

    Тянет неотвеченные и отвеченные (isAnswered false+true); full_backfill —
    флаг первого прогона (для вопросов WB не имеет отдельного архива, поэтому
    влияет только на лог/отчёт). Между страницами — пауза (лимит WB 1 rps).
    Возвращает {"rows_fetched": int, "rows_upserted": int}.
    """
    from backend.integrations.wb_api import WBApiClient

    client = WBApiClient(api_key, project_id=project_id)
    now = utcnow()

    collected: dict[str, dict] = {}  # wb_id → row (dedup last-wins)

    async def _drain(is_answered: bool) -> None:
        for page in range(_Q_MAX_PAGES):
            if page or not is_answered:
                # пауза между вызовами — лимит WB 1 rps на методы отзывов/вопросов
                await asyncio.sleep(_THROTTLE_SEC)
            data = await client.get_questions(is_answered=is_answered, take=_Q_PAGE, skip=page * _Q_PAGE)
            questions = data.get("questions") or []
            for q in questions:
                if isinstance(q, dict):
                    row = _row_from_question(project_id, q, now)
                    if row:
                        collected[row["wb_id"]] = row
            if len(questions) < _Q_PAGE:
                break
        else:
            logger.warning(
                "WB questions sync: project %d — is_answered=%s hit page cap (%d×%d), выдача усечена",
                project_id, is_answered, _Q_MAX_PAGES, _Q_PAGE,
            )

    await _drain(is_answered=False)
    await _drain(is_answered=True)

    rows = list(collected.values())
    upserted = await _upsert_question_rows(db, rows)
    await db.commit()

    # Вопросы о наличии («когда появится?») → слежение за поступлением (идемпотентно)
    from backend.services import stock_watch_service

    await stock_watch_service.scan_stock_questions(db, project_id)

    logger.info(
        "WB questions sync: project %d — fetched=%d, upserted=%d (backfill=%s)",
        project_id, len(rows), upserted, full_backfill,
    )
    return {"rows_fetched": len(rows), "rows_upserted": upserted}


async def has_any_question(db: AsyncSession, project_id: int) -> bool:
    """Есть ли у проекта хоть один вопрос в зеркале (для решения о первом прогоне)."""
    return bool(
        await db.scalar(select(WBQuestion.id).where(WBQuestion.project_id == project_id).limit(1))
    )


async def list_questions(
    db: AsyncSession,
    project_id: int,
    *,
    is_answered: bool = False,
    take: int = 100,
    skip: int = 0,
) -> dict:
    """Список вопросов из зеркала + счётчики (unanswered/archive) и has_key."""
    from backend.services import reviews_service

    base = select(WBQuestion).where(WBQuestion.project_id == project_id)
    count_unanswered = await db.scalar(
        select(func.count(WBQuestion.id)).where(
            WBQuestion.project_id == project_id, WBQuestion.is_answered.is_(False)
        )
    )
    count_archive = await db.scalar(
        select(func.count(WBQuestion.id)).where(
            WBQuestion.project_id == project_id, WBQuestion.is_answered.is_(True)
        )
    )
    rows = (
        await db.execute(
            base.where(WBQuestion.is_answered.is_(is_answered))
            .order_by(WBQuestion.created_date.desc().nullslast())
            .limit(take)
            .offset(skip)
        )
    ).scalars().all()

    # бейдж «следим за наличием» для вопросов о поступлении
    from backend.services import stock_watch_service

    watched = await stock_watch_service.watched_question_ids(db, project_id)

    return {
        "items": [
            {
                "id": q.wb_id,
                "nm_id": q.nm_id,
                "text": q.text,
                "answer_text": q.answer_text,
                "is_answered": q.is_answered,
                "created_date": q.created_date.isoformat() if q.created_date else None,
                "user_name": q.user_name,
                "subject": q.subject,
                "product_name": q.product_name,
                "article": q.article,
                "brand": q.brand,
                "has_stock_watch": q.wb_id in watched,
            }
            for q in rows
        ],
        "count_unanswered": int(count_unanswered or 0),
        "count_archive": int(count_archive or 0),
        "has_key": bool(await reviews_service.resolve_wb_key(db, project_id)),
    }


# ─── База знаний товаров (wb_product_kb) ─────────────────────────────────────

# Эвристика тематики типичного вопроса: первое совпадение ключевых слов побеждает,
# порядок объявления важен (более специфичные темы — раньше).
_TOPIC_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Размер", ("размер", "маломер", "большемер", "обхват", "параметр", "рост",
                "длина", "ширина", "садится", "сидит", "глубина", "высота")),
    ("Доставка", ("доставк", "отправк", "срок", "придёт", "придет", "пвз",
                  "пункт выдачи", "почт", "курьер")),
    ("Качество", ("качеств", "брак", "дефект", "рвёт", "рвет", "порвал", "сломал",
                  "шов", "швы", "люфт", "катыш", "скрип", "трещин")),
    ("Состав", ("состав", "материал", "ткань", "хлопок", "полиэстер", "из чего",
                "плотност", "фольг", "металл", "пластик")),
    ("Цвет", ("цвет", "оттенок", "расцветк")),
    ("Комплект", ("комплект", "входит", "набор", "штук", "упаковк", "сколько в",
                  "количеств")),
    ("Гарантия", ("гаранти", "возврат", "обмен")),
)


def classify_kb_topic(text: str | None) -> str:
    """Тема вопроса эвристикой по ключевым словам (Размер/Доставка/…/Прочее)."""
    t = (text or "").lower()
    for topic, keywords in _TOPIC_KEYWORDS:
        if any(kw in t for kw in keywords):
            return topic
    return "Прочее"


def _norm_question(text: str) -> str:
    """Нормализация текста вопроса для дедуп-хэша: lower + схлопнуть пробелы."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _question_hash(text: str) -> str:
    """md5 нормализованного вопроса — ключ дедупликации импорта КБ."""
    return hashlib.md5(_norm_question(text).encode("utf-8")).hexdigest()  # noqa: S324 — не секрет, а дедуп-ключ


async def import_kb_from_answered_questions(db: AsyncSession, project_id: int) -> dict:
    """
    Импорт базы знаний из архива отвеченных вопросов (wb_questions).

    Каждая пара «вопрос покупателя → ответ продавца» становится записью
    wb_product_kb (topic — эвристика, source='import'). Дедуп по
    (project_id, nm_id, md5(вопрос)) — повторный импорт не плодит дубли.
    """
    rows = (
        await db.execute(
            select(WBQuestion).where(
                WBQuestion.project_id == project_id,
                WBQuestion.is_answered.is_(True),
                WBQuestion.text.isnot(None),
                WBQuestion.answer_text.isnot(None),
                WBQuestion.nm_id.isnot(None),
            )
        )
    ).scalars().all()

    existing: set[tuple[int, str]] = {
        (int(nm), h)
        for nm, h in (
            await db.execute(
                select(WBProductKB.nm_id, WBProductKB.question_hash).where(
                    WBProductKB.project_id == project_id,
                    WBProductKB.question_hash.isnot(None),
                )
            )
        ).all()
    }

    created = skipped_dupe = skipped_empty = 0
    nm_set: set[int] = set()
    for q in rows:
        q_text = (q.text or "").strip()
        a_text = (q.answer_text or "").strip()
        if not q_text or not a_text:
            skipped_empty += 1
            continue
        # Мусорные ответы WB («Вопрос отклонён» и т.п.) в базу знаний не берём —
        # это не факты о товаре, а служебные статусы модерации
        if _is_junk_answer(a_text):
            skipped_empty += 1
            continue
        h = _question_hash(q_text)
        key = (int(q.nm_id), h)
        if key in existing:
            skipped_dupe += 1
            continue
        db.add(
            WBProductKB(
                project_id=project_id,
                nm_id=q.nm_id,
                topic=classify_kb_topic(q_text),
                question_example=q_text,
                answer=a_text,
                source="import",
                question_hash=h,
            )
        )
        existing.add(key)
        nm_set.add(int(q.nm_id))
        created += 1
        if created % 500 == 0:
            await db.flush()
    await db.commit()

    logger.info(
        "KB import: project %d — questions=%d, created=%d, dupes=%d, empty=%d, nm_ids=%d",
        project_id, len(rows), created, skipped_dupe, skipped_empty, len(nm_set),
    )
    return {
        "source_questions": len(rows),
        "created": created,
        "skipped_dupe": skipped_dupe,
        "skipped_empty": skipped_empty,
        "nm_count": len(nm_set),
    }


_WORD_RE = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)

# Служебные ответы WB, не несущие фактов о товаре (модерация вопроса и т.п.)
_JUNK_ANSWER_RE = re.compile(r"^\s*вопрос\s+отклон[её]н", re.IGNORECASE)


def _is_junk_answer(answer: str) -> bool:
    """True, если ответ — служебный статус WB, а не факт для базы знаний."""
    return bool(_JUNK_ANSWER_RE.search(answer or ""))


def _tokens(text: str | None) -> set[str]:
    """Слова текста (≥3 символов) для тематического скоринга."""
    return {w.lower() for w in _WORD_RE.findall(text or "") if len(w) >= 3}


def _kb_score(entry: dict, q_tokens: set[str]) -> int:
    """Скор записи КБ: совпадения слов вопроса в теме (×2) и примере вопроса (×1)."""
    if not q_tokens:
        return 0
    topic_tokens = _tokens(entry.get("topic"))
    example_tokens = _tokens(entry.get("question_example"))
    return 2 * len(q_tokens & topic_tokens) + len(q_tokens & example_tokens)


def rank_kb_entries(entries: list[dict], question_text: str, limit: int = _KB_LIMIT) -> list[dict]:
    """Записи КБ по убыванию тематической близости к вопросу (без векторов)."""
    q_tokens = _tokens(question_text)
    scored = [(_kb_score(e, q_tokens), -i, e) for i, e in enumerate(entries)]
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [e for score, _, e in scored if score > 0][:limit] or entries[:limit]


def find_direct_kb_match(entries: list[dict], question_text: str) -> dict | None:
    """
    Ровно подходящая запись КБ для fallback без LLM-ключа.

    Точное совпадение нормализованного текста вопроса с примером — или совпадение
    темы + высокое пересечение слов (Jaccard ≥ 0.6). Иначе None (→ needs_info).
    """
    norm_q = _norm_question(question_text)
    q_tokens = _tokens(question_text)
    best: tuple[float, dict] | None = None
    for e in entries:
        example = e.get("question_example") or ""
        if not example:
            continue
        if _norm_question(example) == norm_q and norm_q:
            return e  # точное совпадение — сразу победа
        e_tokens = _tokens(example)
        if not q_tokens or not e_tokens:
            continue
        jaccard = len(q_tokens & e_tokens) / len(q_tokens | e_tokens)
        if jaccard >= 0.6 and classify_kb_topic(question_text) == e.get("topic"):
            if best is None or jaccard > best[0]:
                best = (jaccard, e)
    return best[1] if best else None


def _kb_to_dict(k: WBProductKB) -> dict:
    return {
        "id": k.id,
        "nm_id": k.nm_id,
        "topic": k.topic,
        "question_example": k.question_example,
        "answer": k.answer,
        "source": k.source,
        "enabled": k.enabled,
        "created_at": k.created_at.isoformat() if k.created_at else None,
        "updated_at": k.updated_at.isoformat() if k.updated_at else None,
    }


async def load_kb_map(
    db: AsyncSession, project_id: int, nm_ids: list[int]
) -> dict[int, list[dict]]:
    """Enabled-записи КБ проекта, сгруппированные по nm_id (для прогона агента)."""
    if not nm_ids:
        return {}
    rows = (
        await db.execute(
            select(WBProductKB).where(
                WBProductKB.project_id == project_id,
                WBProductKB.nm_id.in_(nm_ids),
                WBProductKB.enabled.is_(True),
            )
        )
    ).scalars().all()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(int(r.nm_id), []).append(_kb_to_dict(r))
    return out


async def list_kb_products(db: AsyncSession, project_id: int) -> dict:
    """Список nm_id проекта с числом записей КБ + имя/артикул из зеркала вопросов."""
    counts = (
        await db.execute(
            select(WBProductKB.nm_id, func.count(WBProductKB.id))
            .where(WBProductKB.project_id == project_id)
            .group_by(WBProductKB.nm_id)
            .order_by(func.count(WBProductKB.id).desc())
            .limit(500)
        )
    ).all()
    nm_ids = [int(nm) for nm, _ in counts]

    # Последний снапшот имени товара из зеркала вопросов (DISTINCT ON по nm_id)
    names: dict[int, tuple[str | None, str | None, str | None]] = {}
    if nm_ids:
        snap = (
            select(
                WBQuestion.nm_id,
                WBQuestion.product_name,
                WBQuestion.article,
                WBQuestion.brand,
            )
            .distinct(WBQuestion.nm_id)
            .where(
                WBQuestion.project_id == project_id,
                WBQuestion.nm_id.in_(nm_ids),
            )
            .order_by(WBQuestion.nm_id, WBQuestion.synced_at.desc())
            .subquery()
        )
        for nm, pname, article, brand in (await db.execute(select(snap.c))).all():
            names[int(nm)] = (pname, article, brand)
        # Фолбэк для товаров без вопросов в зеркале — зеркало отзывов
        missing = [nm for nm in nm_ids if nm not in names or not names[nm][0]]
        if missing:
            snap_fb = (
                select(WBFeedback.nm_id, WBFeedback.product_name, WBFeedback.brand)
                .distinct(WBFeedback.nm_id)
                .where(
                    WBFeedback.project_id == project_id,
                    WBFeedback.nm_id.in_(missing),
                )
                .order_by(WBFeedback.nm_id, WBFeedback.synced_at.desc())
                .subquery()
            )
            for nm, pname, brand in (await db.execute(select(snap_fb.c))).all():
                cur = names.get(int(nm), (None, None, None))
                names[int(nm)] = (cur[0] or pname, cur[1], cur[2] or brand)

    # Дата синка зеркала карточки (wb_product_cards) по каждому nm_id
    cards: dict[int, datetime] = {}
    if nm_ids:
        for nm, synced in (
            await db.execute(
                select(WBProductCard.nm_id, WBProductCard.synced_at).where(
                    WBProductCard.project_id == project_id,
                    WBProductCard.nm_id.in_(nm_ids),
                )
            )
        ).all():
            cards[int(nm)] = synced

    items = [
        {
            "nm_id": int(nm),
            "kb_count": int(cnt),
            "product_name": (names.get(int(nm)) or (None, None, None))[0],
            "article": (names.get(int(nm)) or (None, None, None))[1],
            "brand": (names.get(int(nm)) or (None, None, None))[2],
            "card_synced_at": (
                cards[int(nm)].isoformat() if cards.get(int(nm)) else None
            ),
        }
        for nm, cnt in counts
    ]
    return {"items": items, "total": len(items)}


async def list_kb(
    db: AsyncSession,
    project_id: int,
    *,
    nm_id: int | None = None,
    enabled: bool | None = None,
    take: int = 200,
    skip: int = 0,
) -> dict:
    """Записи базы знаний проекта (фильтры: товар, enabled)."""
    stmt = select(WBProductKB).where(WBProductKB.project_id == project_id)
    if nm_id is not None:
        stmt = stmt.where(WBProductKB.nm_id == nm_id)
    if enabled is not None:
        stmt = stmt.where(WBProductKB.enabled.is_(enabled))
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (
        await db.execute(
            stmt.order_by(WBProductKB.nm_id, WBProductKB.topic, WBProductKB.id)
            .limit(take)
            .offset(skip)
        )
    ).scalars().all()
    return {"items": [_kb_to_dict(k) for k in rows], "total": int(total or 0)}


async def create_kb(db: AsyncSession, project_id: int, data: dict) -> dict:
    """Ручная запись базы знаний (source='manual')."""
    nm_id = data.get("nm_id")
    topic = (data.get("topic") or "").strip()
    answer = (data.get("answer") or "").strip()
    if not nm_id or int(nm_id) <= 0:
        raise ValueError("Не задан nm_id товара")
    if not topic:
        raise ValueError("Не задана тема записи")
    if not answer:
        raise ValueError("Пустой эталонный ответ")
    k = WBProductKB(
        project_id=project_id,
        nm_id=int(nm_id),
        topic=topic,
        question_example=(data.get("question_example") or "").strip() or None,
        answer=answer,
        source="manual",
        enabled=bool(data.get("enabled", True)),
    )
    db.add(k)
    await db.commit()
    await db.refresh(k)
    return _kb_to_dict(k)


async def _get_kb(db: AsyncSession, project_id: int, kb_id: int) -> WBProductKB | None:
    return (
        await db.execute(
            select(WBProductKB).where(
                WBProductKB.project_id == project_id, WBProductKB.id == kb_id
            )
        )
    ).scalar_one_or_none()


async def update_kb(db: AsyncSession, project_id: int, kb_id: int, data: dict) -> dict | None:
    """Изменить запись КБ (topic/question_example/answer/enabled; enabled=false — мягкое отключение)."""
    k = await _get_kb(db, project_id, kb_id)
    if k is None:
        return None
    if "nm_id" in data and data["nm_id"]:
        k.nm_id = int(data["nm_id"])
    if "topic" in data and (data["topic"] or "").strip():
        k.topic = data["topic"].strip()
    if "question_example" in data:
        k.question_example = (data["question_example"] or "").strip() or None
    if "answer" in data:
        answer = (data["answer"] or "").strip()
        if not answer:
            raise ValueError("Пустой эталонный ответ")
        k.answer = answer
    if "enabled" in data:
        k.enabled = bool(data["enabled"])
    await db.commit()
    await db.refresh(k)
    return _kb_to_dict(k)


async def delete_kb(db: AsyncSession, project_id: int, kb_id: int) -> bool:
    """Удалить запись КБ (реальный delete, как у агентов; мягкое отключение — PATCH enabled=false)."""
    k = await _get_kb(db, project_id, kb_id)
    if k is None:
        return False
    await db.delete(k)
    await db.commit()
    return True


# ─── CRUD агентов автоответов ────────────────────────────────────────────────


def _agent_to_dict(a: WBReplyAgent) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "enabled": a.enabled,
        "target": a.target,
        "star_levels": a.star_levels,
        "nm_ids": a.nm_ids,
        "auto_send": a.auto_send,
        "rules": a.rules,
        "examples": a.examples,
        "llm_provider": a.llm_provider,
        "llm_model": a.llm_model,
        "llm_base_url": a.llm_base_url,
        "last_run_at": a.last_run_at.isoformat() if a.last_run_at else None,
    }


async def list_agents(db: AsyncSession, project_id: int) -> list[dict]:
    rows = (
        await db.execute(
            select(WBReplyAgent)
            .where(WBReplyAgent.project_id == project_id)
            .order_by(WBReplyAgent.created_at.desc())
            .limit(200)
        )
    ).scalars().all()
    return [_agent_to_dict(a) for a in rows]


async def _get_agent(db: AsyncSession, project_id: int, agent_id: int) -> WBReplyAgent | None:
    return (
        await db.execute(
            select(WBReplyAgent).where(
                WBReplyAgent.project_id == project_id, WBReplyAgent.id == agent_id
            )
        )
    ).scalar_one_or_none()


async def create_agent(db: AsyncSession, project_id: int, data: dict) -> dict:
    target = data.get("target") or "both"
    if target not in ("feedback", "question", "both"):
        raise ValueError("target должен быть feedback|question|both")
    a = WBReplyAgent(
        project_id=project_id,
        name=(data.get("name") or "").strip() or "Агент автоответов",
        enabled=bool(data.get("enabled", True)),
        target=target,
        star_levels=data.get("star_levels") or "1,2,3,4,5",
        nm_ids=data.get("nm_ids") or None,
        auto_send=bool(data.get("auto_send", False)),
        rules=(data.get("rules") or "").strip(),
        examples=data.get("examples") or None,
        llm_provider=data.get("llm_provider") or "openai_compatible",
        llm_model=data.get("llm_model") or "deepseek-chat",
        llm_base_url=data.get("llm_base_url") or None,
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return _agent_to_dict(a)


async def update_agent(db: AsyncSession, project_id: int, agent_id: int, data: dict) -> dict | None:
    a = await _get_agent(db, project_id, agent_id)
    if a is None:
        return None
    if "target" in data and data["target"] not in (None, "feedback", "question", "both"):
        raise ValueError("target должен быть feedback|question|both")
    for field in ("name", "target", "star_levels", "nm_ids", "rules", "examples",
                  "llm_provider", "llm_model", "llm_base_url"):
        if field in data:
            setattr(a, field, data[field] or (None if field not in ("rules", "target", "star_levels") else getattr(a, field)))
    for flag in ("enabled", "auto_send"):
        if flag in data:
            setattr(a, flag, bool(data[flag]))
    await db.commit()
    await db.refresh(a)
    return _agent_to_dict(a)


async def delete_agent(db: AsyncSession, project_id: int, agent_id: int) -> bool:
    a = await _get_agent(db, project_id, agent_id)
    if a is None:
        return False
    await db.delete(a)
    await db.commit()
    return True


# ─── Прогон агента (генерация черновиков) ────────────────────────────────────

# Статусы, при которых цель уже «занята» ответом и агент её пропускает
_BUSY_STATUSES = ("draft", "approved", "sent")


def _busy_targets_subquery(project_id: int, target_type: str):
    return select(WBFeedbackReply.target_wb_id).where(
        WBFeedbackReply.project_id == project_id,
        WBFeedbackReply.target_type == target_type,
        WBFeedbackReply.status.in_(_BUSY_STATUSES),
    )


async def run_reply_agent(db: AsyncSession, project_id: int, agent_id: int) -> dict:
    """Прогнать агента: отобрать неотвеченные отзывы/вопросы, сгенерировать черновики."""
    a = await _get_agent(db, project_id, agent_id)
    if a is None:
        raise ValueError("Агент не найден")
    if not (a.rules or "").strip():
        raise ValueError("У агента не заданы «Правила для ответа»")

    stars = _parse_ints(a.star_levels) or [1, 2, 3, 4, 5]
    nm_filter = _parse_ints(a.nm_ids)
    # (target_type, wb_id, текст/рейтинг, product-context) — собранные кандидаты
    candidates: list[dict] = []

    if a.target in ("feedback", "both"):
        nom = (
            select(
                Nomenclature.article_wb.label("nm_id"),
                Nomenclature.subject.label("subject"),
                Nomenclature.brand.label("brand"),
            )
            .where(Nomenclature.project_id == project_id, Nomenclature.article_wb.isnot(None))
            .subquery()
        )
        stmt = (
            select(WBFeedback, nom.c.subject, nom.c.brand)
            .outerjoin(nom, nom.c.nm_id == WBFeedback.nm_id)
            .where(
                WBFeedback.project_id == project_id,
                WBFeedback.is_answered.is_(False),
                WBFeedback.has_text,  # на пустой отзыв (только оценка) отвечать нечего
                WBFeedback.rating.in_(stars),
                WBFeedback.wb_id.notin_(_busy_targets_subquery(project_id, "feedback")),
            )
            .order_by(WBFeedback.rating.asc(), WBFeedback.created_date.desc().nullslast())
            .limit(_RUN_LIMIT)
        )
        if nm_filter:
            stmt = stmt.where(WBFeedback.nm_id.in_(nm_filter))
        for fb, nom_subject, nom_brand in (await db.execute(stmt)).all():
            candidates.append({
                "target_type": "feedback",
                "wb_id": fb.wb_id,
                "nm_id": int(fb.nm_id) if fb.nm_id else None,
                "item": {"rating": fb.rating, "text": fb.text, "pros": fb.pros, "cons": fb.cons},
                "product": {
                    "name": fb.product_name or (f"nmID {fb.nm_id}" if fb.nm_id else ""),
                    "subject": nom_subject or "",
                    "brand": nom_brand or fb.brand or "",
                },
            })

    if a.target in ("question", "both"):
        stmt = (
            select(WBQuestion)
            .where(
                WBQuestion.project_id == project_id,
                WBQuestion.is_answered.is_(False),
                WBQuestion.text.isnot(None),
                WBQuestion.wb_id.notin_(_busy_targets_subquery(project_id, "question")),
            )
            .order_by(WBQuestion.created_date.desc().nullslast())
            .limit(_RUN_LIMIT)
        )
        if nm_filter:
            stmt = stmt.where(WBQuestion.nm_id.in_(nm_filter))
        for q in (await db.execute(stmt)).scalars().all():
            candidates.append({
                "target_type": "question",
                "wb_id": q.wb_id,
                "nm_id": int(q.nm_id) if q.nm_id else None,
                "item": {"text": q.text},
                "product": {
                    "name": q.product_name or (f"nmID {q.nm_id}" if q.nm_id else ""),
                    "subject": q.subject or "",
                    "brand": q.brand or "",
                },
            })

    candidates = candidates[:_RUN_LIMIT]
    # База знаний для всех nm_id кандидатов — одним запросом, пока открыта read-транзакция
    kb_map = await load_kb_map(
        db, project_id, sorted({c["nm_id"] for c in candidates if c.get("nm_id")})
    )
    # Закрываем read-транзакцию ДО походов в LLM (не держать БД через внешний HTTP)
    await db.commit()

    # АВТООТПРАВКА ОТКЛЮЧЕНА осознанно: ответы строятся из базы знаний и ВСЕГДА
    # требуют ручного одобрения продавца — agent.auto_send игнорируется, каждый
    # черновик создаётся со статусом draft (риск выдуманного/непроверенного ответа
    # в WB без глаз человека недопустим). Поле auto_send в модели оставлено для
    # обратной совместимости API/фронта.
    llm_ready = bool((settings.COMPLAINT_LLM_API_KEY or "").strip())

    checked = drafted = needs_info_count = errors = 0
    for cand in candidates:
        checked += 1
        q_text = cand["item"].get("text") or ""
        kb_entries = rank_kb_entries(kb_map.get(cand.get("nm_id")) or [], q_text)

        if not kb_entries:
            # Нет записей КБ по товару — LLM НЕ вызываем (нечего подставить в
            # промпт кроме выдумки): черновик-заглушка на ручную доработку.
            text, needs_info, generation = "", True, None
        elif not llm_ready:
            # Без LLM-ключа: fallback kb_direct — точное совпадение вопроса с
            # записью КБ → берём эталонный ответ как есть; иначе needs_info.
            direct = find_direct_kb_match(kb_entries, q_text)
            if direct is not None:
                text, needs_info, generation = direct["answer"], False, "kb_direct"
            else:
                text, needs_info, generation = "", True, None
        else:
            try:
                parsed = await reply_llm.draft_reply(
                    a.llm_provider, a.llm_model, a.llm_base_url,
                    a.rules, a.examples, cand["product"], cand["item"], cand["target_type"],
                    kb_entries=kb_entries,
                )
                text = parsed["reply_text"]
                needs_info = bool(parsed["needs_info"])
                generation = "llm"
            except Exception as e:  # noqa: BLE001 — сбой LLM на одной цели не валит прогон
                errors += 1
                logger.warning(
                    "reply agent %d: LLM error on %s %s: %s",
                    agent_id, cand["target_type"], cand["wb_id"], e,
                )
                continue

        try:
            db.add(WBFeedbackReply(
                project_id=project_id,
                target_type=cand["target_type"],
                target_wb_id=cand["wb_id"],
                draft_text=text,
                status="draft",  # всегда draft — только ручное одобрение (см. выше)
                source="agent",
                agent_id=a.id,
                needs_info=needs_info,
                generation=generation,
            ))
            await db.commit()
            drafted += 1
            needs_info_count += int(needs_info)
        except Exception as e:  # noqa: BLE001
            await db.rollback()
            errors += 1
            logger.warning("reply agent %d: save draft failed for %s: %s", agent_id, cand["wb_id"], e)

    a2 = await _get_agent(db, project_id, agent_id)
    if a2 is not None:
        a2.last_run_at = utcnow()
        await db.commit()

    return {
        "checked": checked,
        "drafted": drafted,
        "needs_info": needs_info_count,
        "errors": errors,
        "limit": _RUN_LIMIT,
        "auto_send": False,  # автоотправка отключена — всегда ручное одобрение
    }


# ─── Отправка approved-ответов в WB ──────────────────────────────────────────


async def send_pending_replies(db: AsyncSession, project_id: int) -> dict:
    """
    Отправить approved-ответы проекта в WB (троттлинг 1 rps, кап _SEND_LIMIT).

    Успех → status=sent, sent_at, is_answered=True в зеркале (wb_feedbacks/wb_questions).
    Ошибка WB → status=error + текст ошибки (не валит остальную очередь).
    """
    from backend.integrations.resilience import RateLimitError
    from backend.integrations.wb_api import WBApiClient
    from backend.services import reviews_service

    api_key = await reviews_service.resolve_wb_key(db, project_id)
    if not api_key:
        raise ValueError("У проекта нет WB-ключа (scope «Вопросы и отзывы»)")

    rows = (
        await db.execute(
            select(WBFeedbackReply)
            .where(
                WBFeedbackReply.project_id == project_id,
                WBFeedbackReply.status == "approved",
            )
            .order_by(WBFeedbackReply.created_at.asc())
            .limit(_SEND_LIMIT)
        )
    ).scalars().all()
    # Закрываем read-транзакцию до внешних вызовов
    await db.commit()

    if not rows:
        return {"sent": 0, "errors": 0, "pending": 0}

    client = WBApiClient(api_key, project_id=project_id)
    sent = errors = 0
    for i, reply in enumerate(rows):
        if i:
            await asyncio.sleep(_THROTTLE_SEC)
        text = (reply.final_text or reply.draft_text or "").strip()
        try:
            if not text:
                raise ValueError("Пустой текст ответа")
            if reply.target_type == "question":
                await client.answer_question(reply.target_wb_id, text)
            else:
                await client.answer_feedback(reply.target_wb_id, text)
        except RateLimitError as e:
            # 429 от WB — дальше гнать бессмысленно: помечаем текущий и выходим
            reply.status = "error"
            reply.error = f"WB rate limit (429), retry_after={e.retry_after}"
            await db.commit()
            errors += 1
            logger.warning("replies sender: project %d — WB 429, остановка прогона", project_id)
            break
        except Exception as e:  # noqa: BLE001 — одна ошибка не валит очередь
            reply.status = "error"
            reply.error = str(e)[:1000]
            await db.commit()
            errors += 1
            continue

        reply.status = "sent"
        reply.error = None
        reply.sent_at = utcnow()
        # Обновляем зеркало: цель отвечена
        if reply.target_type == "question":
            q = await db.scalar(
                select(WBQuestion).where(
                    WBQuestion.project_id == project_id, WBQuestion.wb_id == reply.target_wb_id
                )
            )
            if q is not None:
                q.is_answered = True
                q.answer_text = text
        else:
            fb = await db.scalar(
                select(WBFeedback).where(
                    WBFeedback.project_id == project_id, WBFeedback.wb_id == reply.target_wb_id
                )
            )
            if fb is not None:
                fb.is_answered = True
        await db.commit()
        sent += 1

    pending = await db.scalar(
        select(func.count(WBFeedbackReply.id)).where(
            WBFeedbackReply.project_id == project_id, WBFeedbackReply.status == "approved"
        )
    )
    logger.info("replies sender: project %d — sent=%d, errors=%d, pending=%d", project_id, sent, errors, pending)
    return {"sent": sent, "errors": errors, "pending": int(pending or 0)}


# ─── Ручные черновики и модерация (для UI) ───────────────────────────────────


def _reply_to_dict(r: WBFeedbackReply, target: dict | None = None) -> dict:
    return {
        "id": r.id,
        "target_type": r.target_type,
        "target_wb_id": r.target_wb_id,
        "draft_text": r.draft_text,
        "final_text": r.final_text,
        "text": r.final_text or r.draft_text,
        "status": r.status,
        "source": r.source,
        "agent_id": r.agent_id,
        "needs_info": r.needs_info,
        "generation": r.generation,
        "is_stock_reply": r.is_stock_reply,
        "error": r.error,
        "sent_at": r.sent_at.isoformat() if r.sent_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        "target": target,
    }


async def _target_snapshot(db: AsyncSession, project_id: int, target_type: str, wb_id: str) -> dict | None:
    """Данные цели из зеркала для UI (текст отзыва/вопроса, рейтинг, товар)."""
    if target_type == "question":
        q = await db.scalar(
            select(WBQuestion).where(WBQuestion.project_id == project_id, WBQuestion.wb_id == wb_id)
        )
        if q is None:
            return None
        return {
            "text": q.text, "rating": None, "nm_id": q.nm_id,
            "product_name": q.product_name, "brand": q.brand, "subject": q.subject,
            "user_name": q.user_name,
            "created_date": q.created_date.isoformat() if q.created_date else None,
        }
    fb = await db.scalar(
        select(WBFeedback).where(WBFeedback.project_id == project_id, WBFeedback.wb_id == wb_id)
    )
    if fb is None:
        return None
    return {
        "text": fb.text or fb.pros or fb.cons, "rating": fb.rating, "nm_id": fb.nm_id,
        "product_name": fb.product_name, "brand": fb.brand, "subject": None,
        "user_name": fb.user_name,
        "created_date": fb.created_date.isoformat() if fb.created_date else None,
    }


async def list_replies(
    db: AsyncSession,
    project_id: int,
    *,
    status: str | None = None,
    target_type: str | None = None,
    take: int = 100,
    skip: int = 0,
) -> dict:
    """
    Список ответов/черновиков проекта с данными цели (join к зеркалу).

    target_type ('feedback'|'question') — фильтр по типу цели; counts по
    статусам возвращаются с учётом этого фильтра (для раздельных очередей UI).
    """
    stmt = select(WBFeedbackReply).where(WBFeedbackReply.project_id == project_id)
    if status:
        stmt = stmt.where(WBFeedbackReply.status == status)
    if target_type:
        stmt = stmt.where(WBFeedbackReply.target_type == target_type)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (
        await db.execute(
            stmt.order_by(WBFeedbackReply.created_at.desc()).limit(take).offset(skip)
        )
    ).scalars().all()

    items = []
    for r in rows:
        target = await _target_snapshot(db, project_id, r.target_type, r.target_wb_id)
        items.append(_reply_to_dict(r, target))

    counts_stmt = select(WBFeedbackReply.status, func.count(WBFeedbackReply.id)).where(
        WBFeedbackReply.project_id == project_id
    )
    if target_type:
        counts_stmt = counts_stmt.where(WBFeedbackReply.target_type == target_type)
    counts_rows = (await db.execute(counts_stmt.group_by(WBFeedbackReply.status))).all()
    counts_raw = {s: int(c) for s, c in counts_rows}
    counts = {s: counts_raw.get(s, 0) for s in ("draft", "approved", "sent", "error", "rejected")}
    return {"items": items, "total": int(total or 0), "counts": counts}


async def create_draft(db: AsyncSession, project_id: int, data: dict) -> dict:
    """Ручной черновик ответа на отзыв/вопрос (source=manual)."""
    target_type = data.get("target_type") or ""
    target_wb_id = (data.get("target_wb_id") or "").strip()
    text = (data.get("text") or "").strip()
    if target_type not in ("feedback", "question"):
        raise ValueError("target_type должен быть feedback|question")
    if not target_wb_id:
        raise ValueError("Не задан target_wb_id")
    if not text:
        raise ValueError("Пустой текст ответа")
    # Цель должна существовать в зеркале — иначе отправка в WB бессмысленна
    if await _target_snapshot(db, project_id, target_type, target_wb_id) is None:
        raise ValueError("Отзыв/вопрос не найден в зеркале — выполните синк")
    r = WBFeedbackReply(
        project_id=project_id,
        target_type=target_type,
        target_wb_id=target_wb_id,
        draft_text=text,
        status="draft",
        source="manual",
    )
    db.add(r)
    await db.commit()
    await db.refresh(r)
    return _reply_to_dict(r, await _target_snapshot(db, project_id, target_type, target_wb_id))


async def _get_reply(db: AsyncSession, project_id: int, reply_id: int) -> WBFeedbackReply | None:
    return (
        await db.execute(
            select(WBFeedbackReply).where(
                WBFeedbackReply.project_id == project_id, WBFeedbackReply.id == reply_id
            )
        )
    ).scalar_one_or_none()


async def update_draft(db: AsyncSession, project_id: int, reply_id: int, data: dict) -> dict | None:
    """Редактирование/модерация: text → final_text; action approve|reject; сброс error→draft."""
    r = await _get_reply(db, project_id, reply_id)
    if r is None:
        return None
    if r.status == "sent":
        raise ValueError("Ответ уже отправлен — редактирование недоступно")

    action = data.get("action")
    if "text" in data:
        text = (data.get("text") or "").strip()
        if not text:
            raise ValueError("Пустой текст ответа")
        r.final_text = text
    if action == "approve":
        if not (r.final_text or r.draft_text or "").strip():
            raise ValueError("Нечего отправлять: пустой текст")
        r.status = "approved"
        r.error = None
    elif action == "reject":
        r.status = "rejected"
    elif action == "reopen":
        r.status = "draft"
        r.error = None
    elif action is not None:
        raise ValueError("action должен быть approve|reject|reopen")

    await db.commit()
    await db.refresh(r)
    return _reply_to_dict(r, await _target_snapshot(db, project_id, r.target_type, r.target_wb_id))
