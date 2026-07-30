"""Оркестрация раздела «Биржа карточек товаров».

Проксирует витрину/корзину биржи WB (WbPortalClient.showcase_*/exc_cart_*) и
добавляет то, чего у WB нет:
  • каскад по КОРНЕВОЙ категории (справочник Дениса, `categories.py`) → subjectIDs;
  • фильтр по НАШИМ товарам в двух режимах:
      - "categories": предметы нашей номенклатуры → subjectIDs (server-side, как каскад);
      - "exact": скан витрины с матчем по нашему nmID (WB не фильтрует по nmID).
  • подсветку «наша карточка» (nmID ∈ нашей номенклатуры) на любом объявлении.

Сессия — ОТДЕЛЬНЫЙ слот `wb_exchange_session` (не сессия поставок): провизионится и
протухает независимо. Протухание → mark_wb_exchange_expired + CardExchangeError (роутер → 400).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import TypeVar

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.integrations.wb_portal_client import (
    WbPortalClient,
    WbPortalError,
    WbSessionExpired,
)
from backend.cache import get_redis
from backend.models.cost import Nomenclature
from backend.schemas.card_exchange import ShowcaseQuery
from backend.services import integrations_service
from backend.services.card_exchange import categories as cat_ref

_T = TypeVar("_T")

logger = structlog.get_logger("dds.card_exchange")

# Режим "exact" сканирует витрину постранично (WB не умеет фильтр по nmID). Потолок,
# чтобы не молотить биржу бесконечно; при упоре — scan_truncated=True (не молча, см. learnings).
_MAX_SCAN_PAGES = 300
# Детали объявления (единственный источник предмета) тянем параллельно, но щадя WB.
_DETAILS_CONCURRENCY = 8
_AD_SUBJ_PREFIX = "cex:ad_subjects:"
_AD_SUBJ_TTL = 86_400  # предметы объявления не меняются — сутки
# Значение поля сортировки последней карточки для курсора WB {lastAdID, lastValue}.
_SORT_VALUE = {
    "feedbacksCount": lambda ad: (ad.get("feedbacks") or {}).get("count", 0),
    "rating": lambda ad: (ad.get("feedbacks") or {}).get("rating", 0),
    "totalPrice": lambda ad: ad.get("totalPrice", 0),
}


class CardExchangeError(Exception):
    """Доменная ошибка раздела биржи (роутер маппит в HTTP 400)."""


# ─── справочники ──────────────────────────────────────────────────────────


def list_root_categories() -> list[dict]:
    """Корневые категории для селектора фильтра (из статического справочника)."""
    return cat_ref.list_root_categories()


# ─── наши данные из кабинета ───────────────────────────────────────────────


async def _our_nm_ids(db: AsyncSession, project_id: int) -> set[int]:
    """Множество наших артикулов WB (nmID) из номенклатуры проекта."""
    result = await db.execute(
        select(Nomenclature.article_wb)
        .where(Nomenclature.project_id == project_id, Nomenclature.article_wb.isnot(None))
        .distinct()
        .limit(200_000)
    )
    return {nm for (nm,) in result.fetchall() if nm is not None}


async def _our_subjects(db: AsyncSession, project_id: int) -> set[str]:
    """Множество предметов (Nomenclature.subject) наших товаров — для режима 'categories'."""
    result = await db.execute(
        select(Nomenclature.subject)
        .where(Nomenclature.project_id == project_id, Nomenclature.subject.isnot(None))
        .distinct()
        .limit(10_000)
    )
    return {s.strip() for (s,) in result.fetchall() if s and s.strip()}


async def _our_root_categories(db: AsyncSession, project_id: int) -> set[str]:
    """Корневые категории НАШИХ товаров (предметы номенклатуры → справочник Дениса)."""
    subjects = await _our_subjects(db, project_id)
    return set(cat_ref.root_categories_for_subjects(sorted(subjects)))


async def _ad_subjects(client: WbPortalClient, ad_id: int) -> list[str]:
    """Предметы объявления (варианты группы). Кэшируем: у объявления они не меняются."""
    key = f"{_AD_SUBJ_PREFIX}{ad_id}"
    try:
        redis = await get_redis()
        hit = await redis.get(key)
        if hit:
            return list(json.loads(hit))
    except Exception:  # noqa: BLE001 — кэш не критичен, при сбое просто идём в WB
        redis = None
    group = await client.showcase_ad_details(ad_id)
    subjects = sorted({
        str((g.get("meta") or {}).get("subjectName") or "").strip()
        for g in group
        if (g.get("meta") or {}).get("subjectName")
    })
    # Пустой результат НЕ кэшируем: WB мог не отдать детали (транзиент), и объявление
    # осталось бы без категорий на целые сутки.
    if redis is not None and subjects:
        try:
            await redis.setex(key, _AD_SUBJ_TTL, json.dumps(subjects, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass
    return subjects


async def _enrich_categories(
    client: WbPortalClient, ads: list[dict], our_categories: set[str]
) -> None:
    """Проставить объявлениям корневые категории и пересечение с нашими (мутирует ads).

    Предмет есть только в деталях объявления, поэтому дёргаем их параллельно с
    ограничением конкурентности. Сбой детали не роняет выдачу — просто без категорий.
    """
    sem = asyncio.Semaphore(_DETAILS_CONCURRENCY)

    async def one(ad: dict) -> None:
        ad_id = ad.get("ad_id")
        if not ad_id:
            return
        async with sem:
            try:
                subjects = await _ad_subjects(client, int(ad_id))
            except (WbPortalError, ValueError, TypeError):
                return
        cats = cat_ref.root_categories_for_subjects(subjects)
        ad["categories"] = cats
        ad["our_categories"] = [c for c in cats if c in our_categories]

    await asyncio.gather(*(one(a) for a in ads))


async def _subjects_name_to_id(client: WbPortalClient) -> dict[str, int]:
    """Карта имя предмета → subjectID из живого справочника биржи WB."""
    subs = await client.showcase_subjects()
    return {s["name"]: s["id"] for s in subs if s.get("name") and s.get("id") is not None}


# ─── маппинг WB → наружу ───────────────────────────────────────────────────


def _map_ad(ad: dict, our_nm: set[int]) -> dict:
    """WB-объявление (camelCase, вложенный meta/feedbacks) → плоский snake_case dict."""
    meta = ad.get("meta") or {}
    fb = ad.get("feedbacks") or {}
    nm_id = ad.get("nmID")
    return {
        "ad_id": ad.get("adID"),
        "nm_id": nm_id,
        "imt_id": ad.get("imtID"),
        "title": meta.get("title"),
        "brand": meta.get("brand"),
        "supplier_name": meta.get("supplierName"),
        "imt_count": meta.get("imtCount"),
        "stock_qty": meta.get("stockQty"),
        "photo": meta.get("photo"),
        "contact_countries": meta.get("contactCountries"),
        "is_kiz": bool(meta.get("isKiz")),
        "total_price": ad.get("totalPrice"),
        "rating": fb.get("rating", 0),
        "feedbacks_count": fb.get("count", 0),
        "has_in_cart": bool(ad.get("hasInCart")),
        "is_card_owner": bool(ad.get("isCardOwner")),
        "is_ours": nm_id in our_nm if nm_id is not None else False,
        # Заполняются в _enrich_categories (предмет доступен только в деталях объявления).
        "categories": [],
        "our_categories": [],
    }


def _next_cursor(ads: list[dict], sort_field: str) -> dict | None:
    """Курсор следующей страницы из последней карточки текущей."""
    if not ads:
        return None
    last = ads[-1]
    value_of = _SORT_VALUE.get(sort_field, _SORT_VALUE["feedbacksCount"])
    return {"last_ad_id": last.get("adID"), "last_value": float(value_of(last) or 0)}


# ─── витрина ───────────────────────────────────────────────────────────────


async def list_showcase(db: AsyncSession, project_id: int, q: ShowcaseQuery) -> dict:
    """Одна страница витрины (или полный скан в режиме 'exact')."""
    client = await integrations_service.get_wb_exchange_client(db, project_id)
    try:
        our_nm = await _our_nm_ids(db, project_id)

        subject_ids: list[int] | None = None
        unmatched: list[str] = []
        want_category_filter = bool(q.root_categories) or q.our_mode == "categories"
        if want_category_filter:
            name_to_id = await _subjects_name_to_id(client)
            names: list[str] = []
            if q.root_categories:
                for cat in q.root_categories:
                    names.extend(cat_ref.subjects_for_category(cat))
            if q.our_mode == "categories":
                names.extend(sorted(await _our_subjects(db, project_id)))
            subject_ids, unmatched = _resolve_names(names, name_to_id)
            if not subject_ids:
                # Намеренный фильтр по категориям, но ни один предмет не сматчился —
                # пустая витрина (НЕ шлём subjectIDs=[] в WB, где это может значить «без фильтра»).
                return {"ads": [], "next_cursor": None, "has_more": False, "unmatched_subjects": unmatched}

        wb_filter = {
            "subjectIDs": subject_ids,
            "brands": q.brands,
            "supplierIDs": q.supplier_ids,
            "rating": q.rating,
            "hasStocks": q.has_stocks,
        }
        sort = {"field": q.sort_field, "order": q.sort_order}

        if q.our_mode == "exact":
            scanned = await _scan_exact(client, wb_filter, sort, q.search, our_nm)
            await _enrich_categories(client, scanned["ads"], await _our_root_categories(db, project_id))
            # Диагностику справочника не теряем: exact может идти вместе с root_categories.
            return {**scanned, "unmatched_subjects": unmatched}

        cursor = None
        if q.cursor:
            cursor = {"lastAdID": q.cursor.last_ad_id, "lastValue": q.cursor.last_value}
        data = await client.showcase_ads(search=q.search, filter=wb_filter, sort=sort, cursor=cursor)
        raw = data.get("ads") or []
        ads = [_map_ad(a, our_nm) for a in raw]
        await _enrich_categories(client, ads, await _our_root_categories(db, project_id))
        return {
            "ads": ads,
            "next_cursor": _next_cursor(raw, q.sort_field),
            "has_more": len(raw) > 0,
            "unmatched_subjects": unmatched,
        }
    except WbSessionExpired as e:
        await integrations_service.mark_wb_exchange_expired(db, project_id)
        raise CardExchangeError(
            "Сессия WB для биржи истекла. Вставьте свежий доступ в разделе «Биржа карточек»."
        ) from e
    except WbPortalError as e:
        raise CardExchangeError(f"WB отклонил запрос биржи: {e}") from e
    finally:
        await client.aclose()


async def _scan_exact(
    client: WbPortalClient, wb_filter: dict, sort: dict, search: str | None, our_nm: set[int]
) -> dict:
    """Режим 'точно наши nmID': скан витрины постранично с матчем по нашему nmID.

    WB фильтра по nmID не имеет, поэтому идём курсором до конца выдачи (или до потолка
    _MAX_SCAN_PAGES). Возвращаем ВСЕ совпадения разом (без пагинации наружу).
    """
    matched: list[dict] = []
    cursor: dict | None = None
    pages = 0
    truncated = False
    while pages < _MAX_SCAN_PAGES:
        data = await client.showcase_ads(search=search, filter=wb_filter, sort=sort, cursor=cursor)
        raw = data.get("ads") or []
        if not raw:
            break
        pages += 1
        matched.extend(_map_ad(a, our_nm) for a in raw if a.get("nmID") in our_nm)
        cursor = {
            "lastAdID": raw[-1].get("adID"),
            "lastValue": float(_SORT_VALUE.get(sort["field"], _SORT_VALUE["feedbacksCount"])(raw[-1]) or 0),
        }
    else:
        truncated = True
        logger.warning("card_exchange.scan_exact truncated", pages=pages, matched=len(matched))
    return {
        "ads": matched,
        "next_cursor": None,
        "has_more": False,
        "unmatched_subjects": [],
        "scanned_pages": pages,
        "scan_truncated": truncated,
    }


def _resolve_names(names: list[str], name_to_id: dict[str, int]) -> tuple[list[int], list[str]]:
    """Имена предметов → уникальные subjectIDs + несматченные (диагностика рассинхрона)."""
    ids: set[int] = set()
    unmatched: set[str] = set()
    for name in names:
        sid = name_to_id.get(name)
        (ids.add(sid) if sid is not None else unmatched.add(name))
    return sorted(ids), sorted(unmatched)


# ─── корзина ───────────────────────────────────────────────────────────────


async def _cart_call(
    db: AsyncSession, project_id: int, coro_factory: Callable[[WbPortalClient], Awaitable[_T]]
) -> _T:
    """Обёртка вызова корзины: клиент + маппинг ошибок сессии/портала."""
    client = await integrations_service.get_wb_exchange_client(db, project_id)
    try:
        return await coro_factory(client)
    except WbSessionExpired as e:
        await integrations_service.mark_wb_exchange_expired(db, project_id)
        raise CardExchangeError(
            "Сессия WB для биржи истекла. Вставьте свежий доступ в разделе «Биржа карточек»."
        ) from e
    except WbPortalError as e:
        raise CardExchangeError(f"WB отклонил операцию с корзиной: {e}") from e
    finally:
        await client.aclose()


async def get_cart(db: AsyncSession, project_id: int) -> dict:
    return await _cart_call(db, project_id, lambda c: c.exc_cart_get())


async def cart_add(db: AsyncSession, project_id: int, ad_id: int) -> bool:
    return await _cart_call(db, project_id, lambda c: c.exc_cart_add(ad_id))


async def cart_delete(db: AsyncSession, project_id: int, ad_ids: list[int]) -> bool:
    return await _cart_call(db, project_id, lambda c: c.exc_cart_delete(ad_ids))
