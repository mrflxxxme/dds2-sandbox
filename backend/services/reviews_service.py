# ruff: noqa: RUF002 — русские комментарии и docstring
"""
Service: WB customer feedbacks (отзывы) — чтение из зеркала БД (wb_feedbacks).

Список и сводная аналитика строятся из таблицы wb_feedbacks (наполняется
`wb_reviews_sync`). Категория/бренд резолвятся по nm_id: Nomenclature.subject /
Nomenclature.brand (фолбэк — brand-снапшот отзыва). Непривязанные → «Без …».

Фильтр по ЯРЛЫКУ (как в воронке): `tag` = имя ProductTag; резолвится в набор nm_id
(ProductTagMap), которым ограничивается ВСЯ сводка. tag=None → без фильтра.

has_key: у проекта есть активный WB-ключ ИЛИ уже накоплены отзывы — тогда фронт
показывает данные, иначе подсказку «настройте ключ».
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta

from sqlalchemy import String, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Subquery

from backend.cache import cached
from backend.models import Nomenclature, ProductTag, ProductTagMap, WBFeedback
from backend.schemas.reviews import (
    ReviewBreakdownResponse,
    ReviewBreakdownRow,
    ReviewItem,
    ReviewsListResponse,
)
from backend.utils.time import utcnow

logger = logging.getLogger("dds.reviews")

_LIST_MAX = 5000
_GROUP_LIMIT = 100  # топ категорий/брендов в сводке
_NO_CATEGORY = "Без категории"
_NO_BRAND = "Без бренда"
_NO_TAG = "Без ярлыка"

# Диапазоны сводки: ключ → глубина в днях (None = всё время). Дефолт — год.
_PERIOD_DAYS: dict[str, int | None] = {
    "2w": 14,
    "1m": 30,
    "3m": 90,
    "6m": 180,
    "1y": 365,
    "all": None,
}
_DEFAULT_PERIOD = "1y"
# Короткие периоды рисуем посуточно, длинные — помесячно (иначе 1–2 точки на графике).
_DAILY_PERIODS = {"2w", "1m"}


def _normalize_period(period: str | None) -> str:
    """Нормализовать ключ периода к известному (иначе — дефолт «год»)."""
    return period if period in _PERIOD_DAYS else _DEFAULT_PERIOD


def _period_start(period: str) -> datetime | None:
    """Начало окна выборки (naive UTC) для периода; None = всё время."""
    days = _PERIOD_DAYS[period]
    if days is None:
        return None
    # created_date хранится naive UTC — сравниваем с naive границей
    return utcnow().replace(tzinfo=None) - timedelta(days=days)


def _bucket_expr(granularity: str) -> ColumnElement[str]:
    """SQL-выражение бакета временного ряда (день или месяц)."""
    fmt = "YYYY-MM-DD" if granularity == "day" else "YYYY-MM"
    return func.to_char(WBFeedback.created_date, fmt).label("month")


async def resolve_wb_key(db: AsyncSession, project_id: int) -> str | None:
    """Активный WB-ключ проекта (каскад feedbacks→analytics→wb) или None."""
    from backend.services.funnel.wb_api_client import get_wb_key

    return (
        await get_wb_key(db, project_id, "wb_feedbacks")
        or await get_wb_key(db, project_id, "wb_analytics")
        or await get_wb_key(db, project_id, "wb")
    )


async def _has_wb_key(db: AsyncSession, project_id: int) -> bool:
    """Есть ли у проекта активный WB-ключ."""
    return bool(await resolve_wb_key(db, project_id))


async def has_any_feedback(db: AsyncSession, project_id: int) -> bool:
    """Есть ли у проекта хоть один отзыв в зеркале (за всё время, вне окна периода).

    Используется как для data-UI гейта сводки, так и для решения о full_backfill
    при on-demand синке (пустое зеркало → тянем и архив = полная история).
    """
    return bool(
        await db.scalar(
            select(WBFeedback.id).where(WBFeedback.project_id == project_id).limit(1)
        )
    )


def _round(v: float | None) -> float | None:
    return round(float(v), 2) if v is not None else None


def _r(n: int) -> ColumnElement[int]:
    """Кол-во отзывов с оценкой n (для распределения 1..5)."""
    return func.sum(case((WBFeedback.rating == n, 1), else_=0))


def _avg_and_count() -> tuple[ColumnElement[float], ColumnElement[int]]:
    """(avg рейтинг>0, count)."""
    return (
        func.avg(WBFeedback.rating).filter(WBFeedback.rating > 0),
        func.count(WBFeedback.id),
    )


async def _resolve_tag_nm_ids(db: AsyncSession, project_id: int, tag: str | None) -> set[int] | None:
    """Имя ярлыка → набор nm_id (активные привязки). None = фильтр не задан."""
    if not tag:
        return None
    rows = await db.execute(
        select(ProductTagMap.nm_id)
        .join(ProductTag, ProductTag.id == ProductTagMap.tag_id)
        .where(
            ProductTagMap.project_id == project_id,
            ProductTag.name == tag,
            ProductTag.is_deleted == False,  # noqa: E712
        )
    )
    return {r[0] for r in rows}


def _conds(project_id: int, nm_ids: set[int] | None, date_from: datetime | None = None) -> list:
    """Базовый фильтр отзывов проекта (+ ярлык + окно по дате, если заданы)."""
    conds: list = [WBFeedback.project_id == project_id]
    if nm_ids is not None:
        # пустой set → in_([]) → ни одной строки (у ярлыка нет товаров)
        conds.append(WBFeedback.nm_id.in_(nm_ids))
    if date_from is not None:
        # отзывы без даты выпадают из периодной выборки (их нельзя разместить во времени)
        conds.append(WBFeedback.created_date >= date_from)
    return conds


async def list_reviews(
    db: AsyncSession,
    project_id: int,
    is_answered: bool = False,
    take: int = 100,
    skip: int = 0,
    nm_id: int | None = None,
) -> ReviewsListResponse:
    """
    Список отзывов покупателей WB из зеркала БД.

    По умолчанию — фильтр по ответу продавца (`is_answered`). Если задан `nm_id` —
    ВСЕ отзывы конкретного товара (без деления по ответу), текстовые сверху —
    для чтения отзывов проблемной новинки.
    """
    take = max(1, min(take, _LIST_MAX))
    skip = max(0, skip)

    conds: list = [WBFeedback.project_id == project_id]
    if nm_id is not None:
        conds.append(WBFeedback.nm_id == nm_id)
    else:
        conds.append(WBFeedback.is_answered == is_answered)

    stmt = select(WBFeedback).where(*conds)
    if nm_id is not None:
        # сначала отзывы с текстом (их читают), затем свежие
        stmt = stmt.order_by(WBFeedback.has_text.desc(), WBFeedback.created_date.desc().nullslast())
    else:
        stmt = stmt.order_by(WBFeedback.created_date.desc().nullslast())
    stmt = stmt.limit(take).offset(skip)
    rows = (await db.execute(stmt)).scalars().all()

    agg = (
        await db.execute(
            select(
                func.count(WBFeedback.id),
                func.count(WBFeedback.id).filter(~WBFeedback.is_answered),
                func.avg(WBFeedback.rating).filter(WBFeedback.rating > 0),
            ).where(WBFeedback.project_id == project_id)
        )
    ).one()
    total_all, unanswered, avg = agg
    # total текущего среза (по фильтру) — для пагинации
    total_filtered = await db.scalar(select(func.count(WBFeedback.id)).where(*conds))

    has_key = bool(total_all) or await _has_wb_key(db, project_id)

    items = [
        ReviewItem(
            id=r.wb_id,
            text=r.text or "",
            rating=r.rating,
            created_date=r.created_date.isoformat() if r.created_date else None,
            user_name=r.user_name,
            pros=r.pros,
            cons=r.cons,
            nm_id=r.nm_id,
            product_name=r.product_name,
            article=r.article,
            brand=r.brand,
            is_answered=r.is_answered,
        )
        for r in rows
    ]

    return ReviewsListResponse(
        items=items,
        total=int(total_filtered or 0),
        count_unanswered=int(unanswered or 0),
        count_archive=0,
        average_rating=_round(avg),
        has_key=has_key,
    )


async def _summary_kpis(
    db: AsyncSession, project_id: int, nm_ids: set[int] | None, date_from: datetime | None
) -> dict:
    row = (
        await db.execute(
            select(
                func.count(WBFeedback.id),
                func.avg(WBFeedback.rating).filter(WBFeedback.rating > 0),
                func.count(WBFeedback.id).filter(~WBFeedback.has_text),
                func.count(WBFeedback.id).filter(WBFeedback.has_text),
                func.count(WBFeedback.id).filter(~WBFeedback.is_answered),
                func.count(WBFeedback.id).filter(WBFeedback.rating.in_((4, 5))),
                func.count(WBFeedback.id).filter(WBFeedback.rating.in_((1, 2))),
            ).where(*_conds(project_id, nm_ids, date_from))
        )
    ).one()
    total, avg, no_text, with_text, unanswered, positive, negative = row
    return {
        "average_rating": _round(avg),
        "total": int(total or 0),
        "count_no_text": int(no_text or 0),
        "count_with_text": int(with_text or 0),
        "count_unanswered": int(unanswered or 0),
        "count_positive": int(positive or 0),
        "count_negative": int(negative or 0),
    }


async def _monthly_rating(
    db: AsyncSession, project_id: int, nm_ids: set[int] | None, date_from: datetime | None, granularity: str
) -> list[dict]:
    month = _bucket_expr(granularity)
    avg, cnt = _avg_and_count()
    rows = (
        await db.execute(
            select(month, avg, cnt)
            .where(*_conds(project_id, nm_ids, date_from), WBFeedback.created_date.isnot(None))
            .group_by(month)
            .order_by(month)
        )
    ).all()
    return [{"month": m, "avg_rating": _round(a), "count": int(c or 0)} for m, a, c in rows]


async def _monthly_volume(
    db: AsyncSession, project_id: int, nm_ids: set[int] | None, date_from: datetime | None, granularity: str
) -> list[dict]:
    month = _bucket_expr(granularity)
    rows = (
        await db.execute(
            select(month, _r(1), _r(2), _r(3), _r(4), _r(5))
            .where(*_conds(project_id, nm_ids, date_from), WBFeedback.created_date.isnot(None))
            .group_by(month)
            .order_by(month)
        )
    ).all()
    return [
        {"month": m, "r1": int(a or 0), "r2": int(b or 0), "r3": int(c or 0), "r4": int(d or 0), "r5": int(e or 0)}
        for m, a, b, c, d, e in rows
    ]


def _nom_lookup(project_id: int) -> Subquery:
    """Подзапрос nm_id → subject/brand (distinct по nm_id: размеры дают дубли)."""
    return (
        select(
            Nomenclature.article_wb.label("nm_id"),
            func.max(Nomenclature.subject).label("subject"),
            func.max(Nomenclature.brand).label("brand"),
        )
        .where(
            Nomenclature.project_id == project_id,
            Nomenclature.article_wb.isnot(None),
        )
        .group_by(Nomenclature.article_wb)
        .subquery()
    )


def _group_row(row: Sequence) -> dict:
    """Строка группы (name, avg, count, r1..r5) → dict карточки распределения."""
    n, a, c, x1, x2, x3, x4, x5 = row
    return {
        "name": n,
        "avg_rating": _round(a),
        "count": int(c or 0),
        "r1": int(x1 or 0),
        "r2": int(x2 or 0),
        "r3": int(x3 or 0),
        "r4": int(x4 or 0),
        "r5": int(x5 or 0),
    }


async def _by_category(
    db: AsyncSession, project_id: int, nm_ids: set[int] | None, date_from: datetime | None
) -> list[dict]:
    nom = _nom_lookup(project_id)
    name = func.coalesce(nom.c.subject, _NO_CATEGORY).label("name")
    avg, cnt = _avg_and_count()
    rows = (
        await db.execute(
            select(name, avg, cnt, _r(1), _r(2), _r(3), _r(4), _r(5))
            .select_from(WBFeedback)
            .outerjoin(nom, nom.c.nm_id == WBFeedback.nm_id)
            .where(*_conds(project_id, nm_ids, date_from))
            .group_by(name)
            .order_by(cnt.desc())
            .limit(_GROUP_LIMIT)
        )
    ).all()
    return [_group_row(r) for r in rows]


async def _by_brand(
    db: AsyncSession, project_id: int, nm_ids: set[int] | None, date_from: datetime | None
) -> list[dict]:
    nom = _nom_lookup(project_id)
    # Бренд из Nomenclature по nm_id, фолбэк — снапшот отзыва, затем «Без бренда»
    name = func.coalesce(nom.c.brand, WBFeedback.brand, _NO_BRAND).label("name")
    avg, cnt = _avg_and_count()
    rows = (
        await db.execute(
            select(name, avg, cnt, _r(1), _r(2), _r(3), _r(4), _r(5))
            .select_from(WBFeedback)
            .outerjoin(nom, nom.c.nm_id == WBFeedback.nm_id)
            .where(*_conds(project_id, nm_ids, date_from))
            .group_by(name)
            .order_by(cnt.desc())
            .limit(_GROUP_LIMIT)
        )
    ).all()
    return [_group_row(r) for r in rows]


@cached(prefix="reviews:summary", ttl=300)
async def get_reviews_summary(
    db: AsyncSession, project_id: int, tag: str | None = None, period: str = _DEFAULT_PERIOD
) -> dict:
    """
    Полная сводка отзывов проекта из зеркала БД за выбранный период.

    Все запросы фильтруют project_id; ярлык (`tag`) резолвится в набор nm_id, а
    `period` — в окно по дате; вместе они ограничивают ВСЕ блоки (KPI, ряды,
    категории, бренды). Гранулярность рядов: посуточно для коротких периодов
    (2 недели / месяц), помесячно для остальных.
    """
    period = _normalize_period(period)
    date_from = _period_start(period)
    granularity = "day" if period in _DAILY_PERIODS else "month"
    nm_ids = await _resolve_tag_nm_ids(db, project_id, tag)

    kpis = await _summary_kpis(db, project_id, nm_ids, date_from)
    # has_key = «показывать data-UI»: True, если у проекта есть отзывы за ВСЁ время
    # (даже когда окно периода пустое — фронт покажет «за период пусто») или активный ключ.
    has_key = (
        bool(kpis["total"])
        or await has_any_feedback(db, project_id)
        or await _has_wb_key(db, project_id)
    )

    return {
        "summary": kpis,
        "monthly_rating": await _monthly_rating(db, project_id, nm_ids, date_from, granularity),
        "monthly_volume": await _monthly_volume(db, project_id, nm_ids, date_from, granularity),
        "by_category": await _by_category(db, project_id, nm_ids, date_from),
        "by_brand": await _by_brand(db, project_id, nm_ids, date_from),
        "granularity": granularity,
        "period": period,
        "has_key": has_key,
    }


# ─── Проблемные новинки (недавно на продаже + низкий рейтинг) ────────────────

_NEWCOMERS_LIMIT = 300
_COMPLAINT_TERMS_TOP = 25  # сколько тем жалоб отдавать
_NEG_TEXT_LIMIT = 5000  # кап на негативные тексты для частотника

# Русские стоп-слова (частые незначимые) — чтобы в темах жалоб остались смысловые слова.
_RU_STOP: frozenset[str] = frozenset(
    """
    это этот эта эти того этом этой очень просто вообще совсем именно даже более менее самый
    когда потом после перед сразу пока ещё уже всё все весь вся свои свой своя моя мой меня мне
    была было были есть быть будет чтобы если чтоб хотя однако поэтому который которая которое
    здесь там тут туда сюда затем итоге общем короче ничего никак нельзя можно надо нужно нужен
    товар товара товаре заказ заказа заказала заказал купила купил брала взяла пришло пришла пришел
    штука штук цена деньги рубль рублей магазин продавец доставка отзыв звезда балл оценка
    хорошо плохо нормально спасибо руб шт см как что так вот его ему нее них они она оно
    """.split()
)
_WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)


def _complaint_tokens(text: str) -> list[str]:
    """Смысловые слова из текста жалобы (длина ≥4, не стоп-слово)."""
    return [w for w in _WORD_RE.findall(text.lower()) if len(w) >= 4 and w not in _RU_STOP]


async def _complaint_terms(db: AsyncSession, project_id: int, nm_ids: list[int]) -> list[dict]:
    """Топ частых слов из негативных (1–2★) отзывов заданных товаров."""
    if not nm_ids:
        return []
    rows = (
        await db.execute(
            select(WBFeedback.text, WBFeedback.cons)
            .where(
                WBFeedback.project_id == project_id,
                WBFeedback.nm_id.in_(nm_ids),
                WBFeedback.rating.in_((1, 2)),
                WBFeedback.has_text,
            )
            .limit(_NEG_TEXT_LIMIT)
        )
    ).all()
    counter: Counter[str] = Counter()
    for text, cons in rows:
        counter.update(_complaint_tokens(f"{text or ''} {cons or ''}"))
    return [{"term": t, "count": c} for t, c in counter.most_common(_COMPLAINT_TERMS_TOP) if c >= 2]


def _nom_lookup_dated(project_id: int) -> Subquery:
    """nm_id → subject/brand/first_sale_date (distinct по nm_id)."""
    return (
        select(
            Nomenclature.article_wb.label("nm_id"),
            func.max(Nomenclature.subject).label("subject"),
            func.max(Nomenclature.brand).label("brand"),
            func.max(Nomenclature.first_sale_date).label("first_sale_date"),
        )
        .where(Nomenclature.project_id == project_id, Nomenclature.article_wb.isnot(None))
        .group_by(Nomenclature.article_wb)
        .subquery()
    )


async def get_new_low_rated(
    db: AsyncSession,
    project_id: int,
    days: int = 30,
    max_rating: float = 4.6,
    min_reviews: int = 1,
) -> dict:
    """
    Проблемные новинки: товары «на продаже» меньше `days` дней и со средним
    рейтингом ниже `max_rating` — ранний сигнал «новинка уже собирает плохие отзывы».

    «Дата старта» = `Nomenclature.first_sale_date`, а если её нет (не заполнена) —
    фолбэк на дату ПЕРВОГО отзыва по товару (прокси начала продаж). Всё project-scoped.
    """
    days = max(1, min(days, 365))
    nom = _nom_lookup_dated(project_id)
    avg, cnt = _avg_and_count()
    subject = func.coalesce(nom.c.subject, _NO_CATEGORY).label("subject")
    brand = func.coalesce(nom.c.brand, func.max(WBFeedback.brand), _NO_BRAND).label("brand")

    rows = (
        await db.execute(
            select(
                WBFeedback.nm_id,
                avg,
                cnt,
                func.count(WBFeedback.id).filter(~WBFeedback.is_answered),
                func.count(WBFeedback.id).filter(WBFeedback.rating.in_((1, 2)) & (~WBFeedback.is_answered)),
                func.min(WBFeedback.created_date),
                _r(1), _r(2), _r(3), _r(4), _r(5),
                subject,
                brand,
                func.max(WBFeedback.product_name),
                nom.c.first_sale_date,
            )
            .select_from(WBFeedback)
            .outerjoin(nom, nom.c.nm_id == WBFeedback.nm_id)
            .where(WBFeedback.project_id == project_id, WBFeedback.nm_id.isnot(None))
            .group_by(WBFeedback.nm_id, nom.c.subject, nom.c.brand, nom.c.first_sale_date)
        )
    ).all()

    today = utcnow().replace(tzinfo=None).date()
    items: list[dict] = []
    total_newcomers = 0  # все новинки в окне (любой рейтинг) — для доли проблемных
    for r in rows:
        nm_id, ar, c, unanswered, neg_unans, first_rev, x1, x2, x3, x4, x5, subj, brnd, pname, fsd = r
        # эффективная дата старта: реальная продажа, иначе прокси — первый отзыв
        if fsd is not None:
            eff: date | None = fsd
            date_source = "sale"
        elif first_rev is not None:
            eff = first_rev.date()
            date_source = "review"
        else:
            eff = None
        if eff is None:
            continue
        days_on_sale = (today - eff).days
        if days_on_sale < 0 or days_on_sale > days:
            continue
        if int(c or 0) < min_reviews:
            continue
        total_newcomers += 1  # новинка в окне (до фильтра по рейтингу)
        if ar is None or float(ar) >= max_rating:
            continue
        items.append({
            "nm_id": int(nm_id),
            "name": (pname or f"nmID {nm_id}"),
            "brand": brnd,
            "subject": subj,
            "first_date": eff.isoformat(),
            "date_source": date_source,
            "days_on_sale": days_on_sale,
            "avg_rating": _round(ar),
            "count": int(c or 0),
            "count_unanswered": int(unanswered or 0),
            "neg_unanswered": int(neg_unans or 0),
            "r1": int(x1 or 0), "r2": int(x2 or 0), "r3": int(x3 or 0),
            "r4": int(x4 or 0), "r5": int(x5 or 0),
        })

    # худшие первыми (рейтинг ↑), при равенстве — больше отзывов
    items.sort(key=lambda it: (it["avg_rating"] if it["avg_rating"] is not None else 5.0, -it["count"]))
    items = items[:_NEWCOMERS_LIMIT]

    nm_ids = [it["nm_id"] for it in items]
    nm_to_tags = await _newcomer_tag_map(db, project_id, nm_ids)
    for it in items:
        it["tags"] = nm_to_tags.get(it["nm_id"], [])
    has_key = bool(items) or await has_any_feedback(db, project_id) or await _has_wb_key(db, project_id)
    return {
        "items": items,
        "by_category": _group_newcomers(items, lambda it: [it["subject"]]),
        "by_brand": _group_newcomers(items, lambda it: [it["brand"]]),
        "by_tag": _group_newcomers(items, lambda it: it["tags"] or [_NO_TAG]),
        "total_newcomers": total_newcomers,
        "complaint_terms": await _complaint_terms(db, project_id, nm_ids),
        "days": days,
        "max_rating": max_rating,
        "has_key": has_key,
    }


async def get_complaint_reviews(
    db: AsyncSession,
    project_id: int,
    term: str,
    days: int = 30,
    max_rating: float = 4.6,
    take: int = 200,
) -> ReviewsListResponse:
    """
    Негативные (1–2★) отзывы проблемных новинок, в тексте/минусах которых есть `term`.

    Товары — те же, что в разделе «Проблемные новинки» (окно `days`, порог `max_rating`).
    """
    term = (term or "").strip()
    newc = await get_new_low_rated(db, project_id, days=days, max_rating=max_rating)
    nm_ids = [it["nm_id"] for it in newc["items"]]
    if not term or not nm_ids:
        return ReviewsListResponse(items=[], total=0, has_key=bool(newc["has_key"]))

    # экранируем спецсимволы ILIKE в пользовательском вводе (% и _)
    esc = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pat = f"%{esc}%"
    rows = (
        await db.execute(
            select(WBFeedback)
            .where(
                WBFeedback.project_id == project_id,
                WBFeedback.nm_id.in_(nm_ids),
                WBFeedback.rating.in_((1, 2)),
                WBFeedback.has_text,
                or_(
                    WBFeedback.text.ilike(pat, escape="\\"),
                    WBFeedback.cons.ilike(pat, escape="\\"),
                ),
            )
            .order_by(WBFeedback.created_date.desc().nullslast())
            .limit(max(1, min(take, _LIST_MAX)))
        )
    ).scalars().all()

    items = [
        ReviewItem(
            id=r.wb_id,
            text=r.text or "",
            rating=r.rating,
            created_date=r.created_date.isoformat() if r.created_date else None,
            user_name=r.user_name,
            pros=r.pros,
            cons=r.cons,
            nm_id=r.nm_id,
            product_name=r.product_name,
            article=r.article,
            brand=r.brand,
            is_answered=r.is_answered,
        )
        for r in rows
    ]
    return ReviewsListResponse(items=items, total=len(items), has_key=bool(newc["has_key"]))


async def _newcomer_tag_map(db: AsyncSession, project_id: int, nm_ids: list[int]) -> dict[int, list[str]]:
    """nm_id → список имён активных ярлыков (для разреза новинок по ярлыку)."""
    if not nm_ids:
        return {}
    rows = await db.execute(
        select(ProductTagMap.nm_id, ProductTag.name)
        .join(ProductTag, ProductTag.id == ProductTagMap.tag_id)
        .where(
            ProductTagMap.project_id == project_id,
            ProductTag.is_deleted == False,  # noqa: E712
            ProductTagMap.nm_id.in_(set(nm_ids)),
        )
    )
    out: dict[int, list[str]] = {}
    for nm_id, name in rows:
        out.setdefault(nm_id, []).append(name)
    return out


def _group_newcomers(
    items: list[dict], keys_of: Callable[[dict], list[str]]
) -> list[dict]:
    """
    Сгруппировать проблемные новинки по ключам (категория/бренд/ярлык).

    Товар с несколькими ярлыками попадает в каждый. avg считается ТОЧНО из
    суммарного распределения r1..r5 (рейтинг>0), а не усреднением округлённых.
    """
    buckets: dict[str, list[dict]] = {}
    for it in items:
        for key in keys_of(it):
            buckets.setdefault(key, []).append(it)

    groups: list[dict] = []
    for name, grp in buckets.items():
        rr = [sum(it[f"r{n}"] for it in grp) for n in range(1, 6)]
        rated = sum(rr)
        avg = sum((i + 1) * rr[i] for i in range(5)) / rated if rated else None
        groups.append({
            "name": name,
            "products": len(grp),
            "avg_rating": _round(avg),
            "count": sum(it["count"] for it in grp),
            "r1": rr[0], "r2": rr[1], "r3": rr[2], "r4": rr[3], "r5": rr[4],
        })
    # больше всего проблемных новинок — первыми
    groups.sort(key=lambda g: (-g["products"], g["avg_rating"] if g["avg_rating"] is not None else 5.0))
    return groups


# ─── Детальная таблица отзывов (Динамика) ───────────────────────────────────

_BREAKDOWN_GROUPS = {"day", "week", "month", "subject", "brand", "nm_id"}
_TIME_GROUPS = {"day", "week", "month"}
_BREAKDOWN_LIMIT = 500


def _breakdown_key(group_by: str, nom: Subquery) -> ColumnElement[str]:
    """SQL-выражение ключа группировки детальной таблицы."""
    if group_by == "day":
        return func.to_char(WBFeedback.created_date, "YYYY-MM-DD")
    if group_by == "week":
        return func.to_char(func.date_trunc("week", WBFeedback.created_date), "YYYY-MM-DD")
    if group_by == "month":
        return func.to_char(WBFeedback.created_date, "YYYY-MM")
    if group_by == "subject":
        return func.coalesce(nom.c.subject, _NO_CATEGORY)
    if group_by == "brand":
        return func.coalesce(nom.c.brand, WBFeedback.brand, _NO_BRAND)
    return cast(WBFeedback.nm_id, String)  # nm_id


def _row_dict(key: str, label: str, total: int, avg: float | None, rr: Sequence) -> dict:
    return {
        "key": key,
        "label": label,
        "total": int(total or 0),
        "avg_rating": _round(avg),
        "r1": int(rr[0] or 0), "r2": int(rr[1] or 0), "r3": int(rr[2] or 0),
        "r4": int(rr[3] or 0), "r5": int(rr[4] or 0),
    }


async def _breakdown_options(db: AsyncSession, project_id: int) -> tuple[list[str], list[str]]:
    """Опции фильтров: предметы и бренды проекта (топ по числу отзывов)."""
    nom = _nom_lookup(project_id)
    subj = func.coalesce(nom.c.subject, _NO_CATEGORY)
    brnd = func.coalesce(nom.c.brand, WBFeedback.brand, _NO_BRAND)

    async def _distinct(expr: ColumnElement[str]) -> list[str]:
        rows = (
            await db.execute(
                select(expr)
                .select_from(WBFeedback)
                .outerjoin(nom, nom.c.nm_id == WBFeedback.nm_id)
                .where(WBFeedback.project_id == project_id)
                .group_by(expr)
                .order_by(func.count(WBFeedback.id).desc())
                .limit(300)
            )
        ).all()
        return [r[0] for r in rows]

    return await _distinct(subj), await _distinct(brnd)


async def get_reviews_breakdown(
    db: AsyncSession,
    project_id: int,
    group_by: str = "month",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    subject: str | None = None,
    brand: str | None = None,
    nm_id: int | None = None,
) -> ReviewBreakdownResponse:
    """
    Детальная таблица отзывов: группировка по времени/предмету/бренду/артикулу,
    распределение оценок 1–5, средний рейтинг, строка итога. Всё project-scoped.
    """
    group_by = group_by if group_by in _BREAKDOWN_GROUPS else "month"
    nom = _nom_lookup(project_id)

    conds: list = [WBFeedback.project_id == project_id]
    if group_by in _TIME_GROUPS:
        conds.append(WBFeedback.created_date.isnot(None))
    if date_from is not None:
        conds.append(WBFeedback.created_date >= date_from)
    if date_to is not None:
        conds.append(WBFeedback.created_date < date_to + timedelta(days=1))  # включая день конца
    if subject:
        conds.append(func.coalesce(nom.c.subject, _NO_CATEGORY) == subject)
    if brand:
        conds.append(func.coalesce(nom.c.brand, WBFeedback.brand, _NO_BRAND) == brand)
    if nm_id is not None:
        conds.append(WBFeedback.nm_id == nm_id)

    key = _breakdown_key(group_by, nom)
    avg, cnt = _avg_and_count()
    sel: list = [key.label("key"), avg, cnt, _r(1), _r(2), _r(3), _r(4), _r(5)]
    if group_by == "nm_id":
        sel.append(func.max(WBFeedback.product_name))

    stmt = (
        select(*sel)
        .select_from(WBFeedback)
        .outerjoin(nom, nom.c.nm_id == WBFeedback.nm_id)
        .where(*conds)
        .group_by(key)
    )
    stmt = stmt.order_by(key.desc()) if group_by in _TIME_GROUPS else stmt.order_by(cnt.desc())
    stmt = stmt.limit(_BREAKDOWN_LIMIT + 1)
    raw = (await db.execute(stmt)).all()

    truncated = len(raw) > _BREAKDOWN_LIMIT
    raw = raw[:_BREAKDOWN_LIMIT]

    rows: list[dict] = []
    for r in raw:
        k = str(r[0])
        label = (r[8] or f"nmID {k}") if group_by == "nm_id" else k
        rows.append(_row_dict(k, label, r[2], r[1], (r[3], r[4], r[5], r[6], r[7])))

    # Итог — отдельным запросом по всем отфильтрованным строкам (не по усечённым)
    tot = (
        await db.execute(
            select(cnt, avg, _r(1), _r(2), _r(3), _r(4), _r(5))
            .select_from(WBFeedback)
            .outerjoin(nom, nom.c.nm_id == WBFeedback.nm_id)
            .where(*conds)
        )
    ).one()
    totals = _row_dict("__total__", "Итого", tot[0], tot[1], (tot[2], tot[3], tot[4], tot[5], tot[6]))

    subjects, brands = await _breakdown_options(db, project_id)
    has_key = bool(totals["total"]) or await has_any_feedback(db, project_id) or await _has_wb_key(db, project_id)

    return ReviewBreakdownResponse(
        group_by=group_by,
        rows=[ReviewBreakdownRow(**r) for r in rows],
        totals=ReviewBreakdownRow(**totals),
        subjects=subjects,
        brands=brands,
        truncated=truncated,
        has_key=has_key,
    )
