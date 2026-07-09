"""Кластеризатор поисковых кампаний WB.

Анализ поисковых кластеров (norm_query) рекламной кампании:
- статистика по кластерам + классификация (голова / эффективный хвост / мусор);
- дневное распределение бюджета (% в день);
- минус-фразы: добавить/убрать кластер в кабинете WB (get→merge→set);
- on-demand агрегация по категории (subject) через все её CPM-кампании.

WB отдаёт статистику только по нормализованным кластерам (CPM-кампании),
разбивки по дням на уровне кластера нет — дневная динамика берётся из
`WbAdCampaignDaily` (уровень кампании).
"""

import logging
import re
from datetime import date as date_type
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Nomenclature, WbAdCampaign, WbFunnelDaily
from backend.models.integrations import WbAdCampaignDaily
from backend.services.funnel.wb_advertising_api import (
    fetch_normquery_stats,
    get_normquery_bids,
    get_normquery_minus,
    set_normquery_bid,
    set_normquery_minus,
)
from backend.services.funnel.wb_api_client import get_wb_key

logger = logging.getLogger("dds.funnel")

TARGET_DRR = 10.0          # целевой ДРР, % — граница «эффективного» кластера
HEAD_SPEND_SHARE = 0.08    # доля бюджета, с которой кластер считается «головой»
WASTE_MIN_SPEND = 500.0    # ₽ — порог «ест бюджет» для 0-заказных релевантных
CATEGORY_PAIR_CAP = 200    # предел (advert_id, nm_id)-пар для on-demand категории
LOCK_MIN_VIEWS = 100       # <100 показов — WB не даёт ставку/минус (стадия сбора данных)


def _bid_map(bids: dict[tuple[int, int], dict[str, float]]) -> dict[str, float]:
    """Слить ставки по всем парам в {norm_query: max bid}."""
    out: dict[str, float] = {}
    for per_pair in bids.values():
        for q, b in per_pair.items():
            if b and b > out.get(q, 0):
                out[q] = b
    return out


def _enrich(cluster: dict[str, Any], minus_set: set[str], bid_map: dict[str, float]) -> dict[str, Any]:
    """Доклеить к кластеру: is_minused, текущую ставку и флаг locked (<100 показов)."""
    q = cluster["norm_query"]
    return {
        **cluster,
        "is_minused": q in minus_set,
        "bid": bid_map.get(q),
        "locked": cluster["views"] < LOCK_MIN_VIEWS,
    }

# порядок важен: длинные окончания раньше коротких (re-альтернатива берёт первое совпадение)
_RU_END = re.compile(r"(ами|ями|ыми|ими|ов|ей|ах|ях|ая|яя|ое|ее|ые|ие|ый|ий|ой|у|ю|а|я|ы|и|е|ь)$")
_WORD = re.compile(r"[а-яёa-z0-9]+")
_STOP = {"для", "с", "и", "на", "без", "от", "в", "по", "до", "the", "все", "что"}


def _d(s: str) -> date_type:
    """asyncpg строго типизирован: Date-колонку нельзя сравнивать со строкой."""
    return date_type.fromisoformat(s)


async def _resolve_key(db: AsyncSession, project_id: int) -> str | None:
    return (
        await get_wb_key(db, project_id, "wb_advert")
        or await get_wb_key(db, project_id, "wb_analytics")
        or await get_wb_key(db, project_id, "wb")
    )


def _stem(word: str) -> str:
    w = word.lower()
    return _RU_END.sub("", w)[:6] if len(w) > 4 else w


def _stems_of(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall((text or "").lower()) if len(w) >= 4 and w not in _STOP}


def _subject_stems(subject: str | None) -> set[str]:
    return _stems_of(subject or "")


def _build_vocab(clusters: list[dict[str, Any]], subject_stems: set[str]) -> set[str]:
    """Релевантный словарь товара = стемы названия категории + стемы всех
    кластеров, которые реально сконвертировали (orders≥1). Так «мусор» = запрос,
    не пересекающийся ни с категорией, ни с тем, что для этого товара уже покупали.
    Самокалибруется под любую категорию (палатки/ковры/диваны)."""
    vocab = set(subject_stems)
    for c in clusters:
        if c.get("orders", 0) >= 1:
            vocab |= _stems_of(c.get("norm_query", ""))
    return vocab


def _is_relevant(norm_query: str, vocab: set[str]) -> bool:
    """Кластер относится к товару, если делит хотя бы один стем с релевантным словарём."""
    if not vocab:
        return True
    return bool(_stems_of(norm_query) & vocab)


def _classify(row: dict[str, Any], aov: float, total_spend: float, vocab: set[str]) -> dict[str, Any]:
    o = row["orders"]
    sp = row["spend"]
    cl = row["clicks"]
    drr = round(sp / (o * aov) * 100, 1) if o and aov else None
    cpo = round(sp / o) if o else None
    cr = round(o / cl * 100, 2) if cl else 0.0
    share = sp / total_spend if total_spend else 0.0
    relevant = _is_relevant(row["norm_query"], vocab)

    if o >= 1 and share >= HEAD_SPEND_SHARE:
        tier, reason = "HEAD", "голова спроса (объём бюджета)"
    elif o >= 1 and drr is not None and drr <= TARGET_DRR:
        tier, reason = "EFFICIENT", f"эффективный хвост (ДРР ≤ {TARGET_DRR:.0f}%)"
    elif o >= 1:
        tier, reason = "CONVERTS", "конвертит, но ДРР высокий"
    elif not relevant:
        tier, reason = "TRASH", "не относится к товару (нет пересечения с категорией)"
    elif sp >= WASTE_MIN_SPEND:
        tier, reason = "WASTE", "релевантно, 0 заказов, ест бюджет"
    else:
        tier, reason = "NOCONV", "релевантно, 0 заказов"

    return {**row, "cr": cr, "cpo": cpo, "drr": drr, "relevant": relevant, "tier": tier, "reason": reason}


async def _campaign_row(db: AsyncSession, project_id: int, campaign_id: int) -> WbAdCampaign | None:
    return (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id,
                WbAdCampaign.campaign_id == campaign_id,
            ).limit(1)
        )
    ).scalar_one_or_none()


async def _aov_and_subject(
    db: AsyncSession, project_id: int, nm_ids: list[int], date_from: str, date_to: str
) -> tuple[float, str | None]:
    """AOV (выручка/заказ) и доминирующий subject по nm_ids за период."""
    if not nm_ids:
        return 0.0, None
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(WbFunnelDaily.orders_sum_rub), 0),
                func.coalesce(func.sum(WbFunnelDaily.orders_count), 0),
            ).where(
                WbFunnelDaily.project_id == project_id,
                WbFunnelDaily.nm_id.in_(nm_ids),
                WbFunnelDaily.date.between(_d(date_from), _d(date_to)),
            )
        )
    ).first()
    aov = float(row[0]) / row[1] if row and row[1] else 0.0
    subj = (
        await db.execute(
            select(WbFunnelDaily.subject, func.count())
            .where(
                WbFunnelDaily.project_id == project_id,
                WbFunnelDaily.nm_id.in_(nm_ids),
                WbFunnelDaily.subject.isnot(None),
            )
            .group_by(WbFunnelDaily.subject)
            .order_by(func.count().desc())
            .limit(1)
        )
    ).first()
    return aov, (subj[0] if subj else None)


async def _daily_budget(
    db: AsyncSession, project_id: int, campaign_id: int, date_from: str, date_to: str
) -> list[dict[str, Any]]:
    """Дневная динамика кампании + доля бюджета по дням (%)."""
    rows = (
        await db.execute(
            select(
                WbAdCampaignDaily.date,
                WbAdCampaignDaily.views,
                WbAdCampaignDaily.clicks,
                WbAdCampaignDaily.spend,
            ).where(
                WbAdCampaignDaily.project_id == project_id,
                WbAdCampaignDaily.campaign_id == campaign_id,
                WbAdCampaignDaily.date.between(_d(date_from), _d(date_to)),
            ).order_by(WbAdCampaignDaily.date)
        )
    ).all()
    total = sum(float(r[3]) for r in rows) or 0.0
    return [
        {
            "date": r[0].isoformat(),
            "views": r[1],
            "clicks": r[2],
            "spend": round(float(r[3]), 2),
            "spend_pct": round(float(r[3]) / total * 100, 2) if total else 0.0,
        }
        for r in rows
    ]


def _totals(clusters: list[dict[str, Any]]) -> dict[str, Any]:
    v = sum(c["views"] for c in clusters)
    cl = sum(c["clicks"] for c in clusters)
    o = sum(c["orders"] for c in clusters)
    sp = round(sum(c["spend"] for c in clusters), 2)
    return {
        "clusters": len(clusters),
        "views": v,
        "clicks": cl,
        "orders": o,
        "spend": sp,
        "ctr": round(cl / v * 100, 2) if v else 0.0,
        "cpo": round(sp / o) if o else None,
    }


async def get_campaign_clusters(
    db: AsyncSession, project_id: int, campaign_id: int, date_from: str, date_to: str
) -> dict:
    """Кластеры одной кампании: статистика + классификация + минус-флаги + дневной бюджет."""
    camp = await _campaign_row(db, project_id, campaign_id)
    if camp is None:
        return {"error": "campaign_not_found"}
    nm_ids = [int(n) for n in (camp.nm_ids or [])]
    key = await _resolve_key(db, project_id)
    if not key:
        return {"error": "no_api_key"}

    pairs = [(campaign_id, nm) for nm in nm_ids]
    stats = await fetch_normquery_stats(key, pairs, date_from, date_to)
    minus = await get_normquery_minus(key, pairs)
    bids = await get_normquery_bids(key, pairs)
    minus_set = {q for lst in minus.values() for q in lst}
    bid_map = _bid_map(bids)

    # слить кластеры по всем nm кампании в один список
    merged: dict[str, dict[str, Any]] = {}
    for lst in stats.values():
        for c in lst:
            q = c["norm_query"]
            m = merged.get(q)
            if m is None:
                merged[q] = dict(c)
            else:
                for f in ("views", "clicks", "spend", "orders", "atbs", "shks"):
                    m[f] = m.get(f, 0) + c.get(f, 0)
    clusters_raw = list(merged.values())

    aov, subject = await _aov_and_subject(db, project_id, nm_ids, date_from, date_to)
    vocab = _build_vocab(clusters_raw, _subject_stems(subject))
    total_spend = sum(c["spend"] for c in clusters_raw) or 0.0
    clusters = [
        _enrich(_classify(c, aov, total_spend, vocab), minus_set, bid_map)
        for c in clusters_raw
    ]
    clusters.sort(key=lambda x: -x["spend"])

    return {
        "campaign_id": campaign_id,
        "name": camp.name,
        "campaign_type": camp.campaign_type,
        "nm_ids": nm_ids,
        "subject": subject,
        "window": {"from": date_from, "to": date_to},
        "aov": round(aov, 2),
        "target_drr": TARGET_DRR,
        "totals": _totals(clusters_raw),
        "daily": await _daily_budget(db, project_id, campaign_id, date_from, date_to),
        "clusters": clusters,
    }


async def toggle_cluster_minus(
    db: AsyncSession,
    project_id: int,
    campaign_id: int,
    nm_id: int,
    norm_query: str,
    action: str,
) -> dict:
    """Добавить/убрать кластер из минус-фраз кампании в кабинете WB (replace-семантика)."""
    camp = await _campaign_row(db, project_id, campaign_id)
    if camp is None:
        return {"ok": False, "error": "campaign_not_found"}
    nm_ids = [int(n) for n in (camp.nm_ids or [])]
    if nm_id not in nm_ids:
        return {"ok": False, "error": "nm_not_in_campaign"}
    if action not in ("add", "remove"):
        return {"ok": False, "error": "bad_action"}
    key = await _resolve_key(db, project_id)
    if not key:
        return {"ok": False, "error": "no_api_key"}

    # read-before-write: НЕЛЬЗЯ писать при непрочитанном текущем списке — set-minus заменяет
    # весь список, транзиентный сбой чтения затёр бы живые минус-фразы продавца.
    minus_map = await get_normquery_minus(key, [(campaign_id, nm_id)])
    if (campaign_id, nm_id) not in minus_map:
        return {"ok": False, "error": "Не удалось прочитать текущие минус-фразы, попробуйте позже"}
    current = minus_map[(campaign_id, nm_id)]

    if action == "add":
        new_list = current if norm_query in current else [*current, norm_query]
    else:
        new_list = [q for q in current if q != norm_query]

    if new_list == current:  # уже в нужном состоянии — не дёргаем WB лишний раз
        return {"ok": True, "action": action, "norm_query": norm_query, "minus": current, "error": None}

    ok, err = await set_normquery_minus(key, campaign_id, nm_id, new_list)
    logger.info(
        f"cluster minus {action}: project={project_id} camp={campaign_id} nm={nm_id} "
        f"query={norm_query!r} ok={ok} n={len(new_list)}"
    )
    return {
        "ok": ok,
        "action": action,
        "norm_query": norm_query,
        "minus": new_list if ok else None,
        "error": err,
    }


async def list_categories(db: AsyncSession, project_id: int) -> list[dict[str, Any]]:
    """Категории (subject) с рекламируемыми товарами — для селектора on-demand агрегации."""
    rows = (
        await db.execute(
            select(WbFunnelDaily.subject, func.count(func.distinct(WbFunnelDaily.nm_id)))
            .where(
                WbFunnelDaily.project_id == project_id,
                WbFunnelDaily.subject.isnot(None),
                WbFunnelDaily.adv_sum > 0,
            )
            .group_by(WbFunnelDaily.subject)
            .order_by(func.count(func.distinct(WbFunnelDaily.nm_id)).desc())
            .limit(100)
        )
    ).all()
    return [{"subject": r[0], "nm_count": r[1]} for r in rows]


async def get_category_clusters(
    db: AsyncSession, project_id: int, subject: str, date_from: str, date_to: str
) -> dict:
    """On-demand агрегация кластеров по всем CPM-поиск-кампаниям одной категории."""
    key = await _resolve_key(db, project_id)
    if not key:
        return {"error": "no_api_key"}

    nm_rows = (
        await db.execute(
            select(func.distinct(WbFunnelDaily.nm_id)).where(
                WbFunnelDaily.project_id == project_id,
                WbFunnelDaily.subject == subject,
            ).limit(10000)
        )
    ).all()
    subj_nms = {int(r[0]) for r in nm_rows}
    if not subj_nms:
        return {"error": "no_products_in_subject", "subject": subject}

    camps = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id,
                WbAdCampaign.campaign_type == "cpm",
            ).limit(5000)
        )
    ).scalars().all()

    # ранжируем кампании по расходу за период — при усечении сохраняем крупнейшие
    camp_ids = [c.campaign_id for c in camps]
    spend_map: dict[int, float] = {}
    if camp_ids:
        srows = (
            await db.execute(
                select(WbAdCampaignDaily.campaign_id, func.sum(WbAdCampaignDaily.spend))
                .where(
                    WbAdCampaignDaily.project_id == project_id,
                    WbAdCampaignDaily.campaign_id.in_(camp_ids),
                    WbAdCampaignDaily.date.between(_d(date_from), _d(date_to)),
                )
                .group_by(WbAdCampaignDaily.campaign_id)
            )
        ).all()
        spend_map = {r[0]: float(r[1] or 0) for r in srows}

    pairs: list[tuple[int, int]] = [
        (c.campaign_id, int(nm))
        for c in sorted(camps, key=lambda x: -spend_map.get(x.campaign_id, 0.0))
        for nm in (c.nm_ids or [])
        if int(nm) in subj_nms
    ]
    total_pairs = len(pairs)
    truncated = total_pairs > CATEGORY_PAIR_CAP
    if truncated:
        logger.warning(f"category '{subject}': {total_pairs} pairs > cap {CATEGORY_PAIR_CAP}, keeping top-spend")
        pairs = pairs[:CATEGORY_PAIR_CAP]
    if not pairs:
        return {"error": "no_campaigns_for_subject", "subject": subject}

    stats = await fetch_normquery_stats(key, pairs, date_from, date_to)
    aov, _ = await _aov_and_subject(db, project_id, list(subj_nms), date_from, date_to)

    # агрегируем кластеры по всей категории
    agg: dict[str, dict[str, Any]] = {}
    per_product: dict[int, dict[str, Any]] = {}
    for (adv_id, nm), lst in stats.items():
        pp = per_product.setdefault(nm, {"nm_id": nm, "views": 0, "clicks": 0, "orders": 0, "spend": 0.0})
        for c in lst:
            q = c["norm_query"]
            a = agg.setdefault(q, {"norm_query": q, "views": 0, "clicks": 0, "orders": 0, "spend": 0.0, "atbs": 0, "shks": 0})
            for f in ("views", "clicks", "orders", "spend", "atbs", "shks"):
                a[f] += c.get(f, 0)
            for f in ("views", "clicks", "orders", "spend"):
                pp[f] += c.get(f, 0)

    for a in agg.values():
        v, cl, sp = a["views"], a["clicks"], a["spend"]
        a["ctr"] = round(cl / v * 100, 2) if v else 0.0
        a["cpc"] = round(sp / cl, 2) if cl else 0.0
        a["cpm"] = round(sp / v * 1000, 2) if v else 0.0
        a["avg_pos"] = 0.0
    clusters_raw = list(agg.values())
    vocab = _build_vocab(clusters_raw, _subject_stems(subject))
    total_spend = sum(c["spend"] for c in clusters_raw) or 0.0
    clusters = [_classify(c, aov, total_spend, vocab) for c in clusters_raw]
    clusters.sort(key=lambda x: -x["spend"])

    # артикул/бренд/склейка (imt) — батч-запросы для фильтров списка товаров
    meta_map: dict[int, tuple[str | None, str | None]] = {}
    imt_map: dict[int, int | None] = {}
    if per_product:
        nm_keys = list(per_product.keys())
        mrows = (
            await db.execute(
                select(
                    WbFunnelDaily.nm_id,
                    func.max(WbFunnelDaily.vendor_code),
                    func.max(WbFunnelDaily.brand),
                )
                .where(
                    WbFunnelDaily.project_id == project_id,
                    WbFunnelDaily.nm_id.in_(nm_keys),
                )
                .group_by(WbFunnelDaily.nm_id)
            )
        ).all()
        meta_map = {int(r[0]): (r[1], r[2]) for r in mrows}
        irows = (
            await db.execute(
                select(Nomenclature.article_wb, Nomenclature.imt_id).where(
                    Nomenclature.project_id == project_id,
                    Nomenclature.article_wb.in_(nm_keys),
                )
            )
        ).all()
        imt_map = {int(r[0]): r[1] for r in irows if r[0] is not None}

    products = []
    for pp in per_product.values():
        o, sp, cl = pp["orders"], pp["spend"], pp["clicks"]
        vendor, brand = meta_map.get(pp["nm_id"], (None, None))
        pp["vendor_code"] = vendor
        pp["brand"] = brand
        pp["imt_id"] = imt_map.get(pp["nm_id"])
        pp["drr"] = round(sp / (o * aov) * 100, 1) if o and aov else None
        pp["cpo"] = round(sp / o) if o else None
        pp["cr"] = round(o / cl * 100, 2) if cl else 0.0
        pp["spend"] = round(sp, 2)
        products.append(pp)
    products.sort(key=lambda x: -x["orders"])

    return {
        "subject": subject,
        "window": {"from": date_from, "to": date_to},
        "aov": round(aov, 2),
        "target_drr": TARGET_DRR,
        "campaigns_count": len({p[0] for p in pairs}),
        "products_count": len(per_product),
        "pairs": len(pairs),
        "truncated": truncated,
        "totals": _totals(clusters_raw),
        "clusters": clusters,
        "products": products,
    }


async def _campaigns_for_nm(db: AsyncSession, project_id: int, nm_id: int) -> list[WbAdCampaign]:
    """CPM-кампании проекта, содержащие данный nm_id (обычно одна)."""
    camps = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id,
                WbAdCampaign.campaign_type == "cpm",
            ).limit(5000)
        )
    ).scalars().all()
    return [c for c in camps if nm_id in [int(x) for x in (c.nm_ids or [])]]


async def _daily_budget_multi(
    db: AsyncSession, project_id: int, campaign_ids: list[int], date_from: str, date_to: str
) -> list[dict[str, Any]]:
    """Суммарная дневная динамика по нескольким кампаниям + доля бюджета по дням (%)."""
    if not campaign_ids:
        return []
    rows = (
        await db.execute(
            select(
                WbAdCampaignDaily.date,
                func.sum(WbAdCampaignDaily.views),
                func.sum(WbAdCampaignDaily.clicks),
                func.sum(WbAdCampaignDaily.spend),
            ).where(
                WbAdCampaignDaily.project_id == project_id,
                WbAdCampaignDaily.campaign_id.in_(campaign_ids),
                WbAdCampaignDaily.date.between(_d(date_from), _d(date_to)),
            ).group_by(WbAdCampaignDaily.date).order_by(WbAdCampaignDaily.date)
        )
    ).all()
    total = sum(float(r[3] or 0) for r in rows) or 0.0
    return [
        {
            "date": r[0].isoformat(),
            "views": int(r[1] or 0),
            "clicks": int(r[2] or 0),
            "spend": round(float(r[3] or 0), 2),
            "spend_pct": round(float(r[3] or 0) / total * 100, 2) if total else 0.0,
        }
        for r in rows
    ]


async def get_product_clusters(
    db: AsyncSession, project_id: int, nm_id: int, date_from: str, date_to: str
) -> dict:
    """Кластеры одного артикула (nm_id) по всем его CPM-кампаниям: статистика +
    классификация + минус-флаги + дневной бюджет. Для страницы drill-down из категории."""
    key = await _resolve_key(db, project_id)
    if not key:
        return {"error": "no_api_key"}
    camps = await _campaigns_for_nm(db, project_id, nm_id)
    if not camps:
        return {"error": "no_campaign_for_nm", "nm_id": nm_id}

    pairs = [(c.campaign_id, nm_id) for c in camps]
    stats = await fetch_normquery_stats(key, pairs, date_from, date_to)
    minus = await get_normquery_minus(key, pairs)
    bids = await get_normquery_bids(key, pairs)
    minus_set = {q for lst in minus.values() for q in lst}
    bid_map = _bid_map(bids)

    merged: dict[str, dict[str, Any]] = {}
    for lst in stats.values():
        for c in lst:
            q = c["norm_query"]
            m = merged.get(q)
            if m is None:
                merged[q] = dict(c)
            else:
                for f in ("views", "clicks", "spend", "orders", "atbs", "shks"):
                    m[f] = m.get(f, 0) + c.get(f, 0)
    clusters_raw = list(merged.values())

    aov, subject = await _aov_and_subject(db, project_id, [nm_id], date_from, date_to)
    vendor = (
        await db.execute(
            select(WbFunnelDaily.vendor_code).where(
                WbFunnelDaily.project_id == project_id,
                WbFunnelDaily.nm_id == nm_id,
                WbFunnelDaily.vendor_code.isnot(None),
            ).limit(1)
        )
    ).scalar_one_or_none()

    vocab = _build_vocab(clusters_raw, _subject_stems(subject))
    total_spend = sum(c["spend"] for c in clusters_raw) or 0.0
    clusters = [
        _enrich(_classify(c, aov, total_spend, vocab), minus_set, bid_map)
        for c in clusters_raw
    ]
    clusters.sort(key=lambda x: -x["spend"])

    return {
        "nm_id": nm_id,
        "vendor_code": vendor,
        "subject": subject,
        "campaigns": [{"campaign_id": c.campaign_id, "name": c.name} for c in camps],
        "window": {"from": date_from, "to": date_to},
        "aov": round(aov, 2),
        "target_drr": TARGET_DRR,
        "totals": _totals(clusters_raw),
        "daily": await _daily_budget_multi(db, project_id, [c.campaign_id for c in camps], date_from, date_to),
        "clusters": clusters,
    }


async def toggle_product_cluster_minus(
    db: AsyncSession, project_id: int, nm_id: int, norm_query: str, action: str
) -> dict:
    """Добавить/убрать кластер в минус-фразы по ВСЕМ CPM-кампаниям артикула."""
    camps = await _campaigns_for_nm(db, project_id, nm_id)
    if not camps:
        return {"ok": False, "error": "no_campaign_for_nm"}
    results = [
        await toggle_cluster_minus(db, project_id, c.campaign_id, nm_id, norm_query, action)
        for c in camps
    ]
    ok = all(r.get("ok") for r in results)
    err = next((r.get("error") for r in results if not r.get("ok")), None)
    return {
        "ok": ok,
        "action": action,
        "norm_query": norm_query,
        "campaigns": len(camps),
        "error": err,
    }


async def set_cluster_bid(
    db: AsyncSession, project_id: int, campaign_id: int, nm_id: int, norm_query: str, bid_rub: float
) -> dict:
    """Ставит ставку одному кластеру кампании (живое действие WB)."""
    camp = await _campaign_row(db, project_id, campaign_id)
    if camp is None:
        return {"ok": False, "error": "campaign_not_found"}
    if nm_id not in [int(n) for n in (camp.nm_ids or [])]:
        return {"ok": False, "error": "nm_not_in_campaign"}
    if bid_rub is None or bid_rub <= 0:
        return {"ok": False, "error": "bad_bid"}
    key = await _resolve_key(db, project_id)
    if not key:
        return {"ok": False, "error": "no_api_key"}
    ok, err = await set_normquery_bid(key, campaign_id, nm_id, norm_query, bid_rub)
    logger.info(f"cluster bid: project={project_id} camp={campaign_id} nm={nm_id} q={norm_query!r} bid={bid_rub} ok={ok}")
    return {"ok": ok, "norm_query": norm_query, "bid": bid_rub if ok else None, "error": err}


async def set_product_cluster_bid(
    db: AsyncSession, project_id: int, nm_id: int, norm_query: str, bid_rub: float
) -> dict:
    """Ставит ставку кластеру по всем CPM-кампаниям артикула."""
    camps = await _campaigns_for_nm(db, project_id, nm_id)
    if not camps:
        return {"ok": False, "error": "no_campaign_for_nm"}
    if bid_rub is None or bid_rub <= 0:
        return {"ok": False, "error": "bad_bid"}
    results = [
        await set_cluster_bid(db, project_id, c.campaign_id, nm_id, norm_query, bid_rub)
        for c in camps
    ]
    ok = all(r.get("ok") for r in results)
    err = next((r.get("error") for r in results if not r.get("ok")), None)
    return {"ok": ok, "norm_query": norm_query, "bid": bid_rub if ok else None, "campaigns": len(camps), "error": err}


def _daily_row(r: Any) -> dict[str, Any]:
    """Собрать дневную строку карточки артикула (реклама + воронка) из wb_funnel_daily."""
    views = int(r.adv_views or 0)
    clicks = int(r.adv_clicks or 0)
    spend = float(r.adv_sum or 0)
    opens = int(r.open_card or 0)
    carts = int(r.add_to_cart or 0)
    orders = int(r.orders_count or 0)
    revenue = float(r.orders_sum_rub or 0)
    return {
        "date": r.date.isoformat(),
        # реклама
        "views": views,
        "clicks": clicks,
        "ctr": round(clicks / views * 100, 2) if views else 0.0,
        "cpc": round(spend / clicks, 2) if clicks else 0.0,
        "spend": round(spend, 2),
        # воронка
        "opens": opens,
        "carts": carts,
        "cr1": round(carts / opens * 100, 2) if opens else 0.0,
        "orders": orders,
        "cr2": round(orders / carts * 100, 2) if carts else 0.0,
        "revenue": round(revenue, 2),
        "price": round(float(r.avg_price), 2) if r.avg_price is not None else None,
        # связка
        "cpo": round(spend / orders) if orders else None,
        "drr": round(spend / revenue * 100, 2) if revenue else None,
    }


def _daily_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    views = sum(r["views"] for r in rows)
    clicks = sum(r["clicks"] for r in rows)
    spend = sum(r["spend"] for r in rows)
    opens = sum(r["opens"] for r in rows)
    carts = sum(r["carts"] for r in rows)
    orders = sum(r["orders"] for r in rows)
    revenue = sum(r["revenue"] for r in rows)
    return {
        "date": "За всё время",
        "views": views,
        "clicks": clicks,
        "ctr": round(clicks / views * 100, 2) if views else 0.0,
        "cpc": round(spend / clicks, 2) if clicks else 0.0,
        "spend": round(spend, 2),
        "opens": opens,
        "carts": carts,
        "cr1": round(carts / opens * 100, 2) if opens else 0.0,
        "orders": orders,
        "cr2": round(orders / carts * 100, 2) if carts else 0.0,
        "revenue": round(revenue, 2),
        "price": round(revenue / orders, 2) if orders else None,
        "cpo": round(spend / orders) if orders else None,
        "drr": round(spend / revenue * 100, 2) if revenue else None,
    }


async def get_product_daily(
    db: AsyncSession, project_id: int, nm_id: int, date_from: str, date_to: str
) -> dict:
    """Дневная статистика артикула (реклама + воронка продаж) — вкладка «По дням»."""
    rows = (
        await db.execute(
            select(WbFunnelDaily).where(
                WbFunnelDaily.project_id == project_id,
                WbFunnelDaily.nm_id == nm_id,
                WbFunnelDaily.date.between(_d(date_from), _d(date_to)),
            ).order_by(WbFunnelDaily.date.desc()).limit(400)
        )
    ).scalars().all()
    daily = [_daily_row(r) for r in rows]
    vendor = rows[0].vendor_code if rows else None
    return {
        "nm_id": nm_id,
        "vendor_code": vendor,
        "window": {"from": date_from, "to": date_to},
        "totals": _daily_totals(daily),
        "rows": daily,
    }
