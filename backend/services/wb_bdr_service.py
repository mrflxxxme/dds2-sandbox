"""
Service: wb_bdr — WB P&L report from locally cached finance data.

Reads from wb_finance_rows (pre-synced), enriches with:
- Advertising data from WbFunnelDaily
- Cost prices from cost_order_items (average)
- Tax calculation from project tax_rates settings

Verified formulas match TrueStats.

Architecture:
- Loaders (DB queries) → bdr_loaders.py
- Enrichment (tax, ABC) → bdr_enrichment.py
- SQL queries & metrics computation → wb_bdr_helpers.py
- Orchestration → this file
"""

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached
from backend.services.bdr_enrichment import (
    apply_tax,
    apply_tax_article,
    apply_tax_by_month,
    blended_tax_info,
    compute_abc,
    enrich_article,
    finalize_net_payout,
    tax_settings_uniform,
)
from backend.services.bdr_loaders import (
    load_ads,
    load_ads_monthly,
    load_avg_costs,
    load_cancel_stats,
    load_cost_overrides,
    load_orders_stocks,
    load_tax_settings_by_month,
)
from backend.services.cost.valuation import (
    DEFAULT_METHOD,
    compute_project_valuation,
    slice_window,
)
from backend.services.settings_service import get_valuation_method
from backend.services.wb_bdr_helpers import (
    build_bdr_aggregate_sql,
    build_bdr_monthly_totals_sql,
    build_brands_sql_bdr,
    build_group_nm_ids_sql,
    build_group_sa_names_sql,
    build_total_count_sql,
    compute_metrics_from_sql,
    cost_with_fallback,
    empty_metrics,
    serialize,
)

logger = logging.getLogger("dds.wb_bdr")

D = Decimal
ZERO = D("0")

# Keep old private names as aliases for backwards compatibility
_build_bdr_aggregate_sql = build_bdr_aggregate_sql
_build_brands_sql_bdr = build_brands_sql_bdr
_build_total_count_sql = build_total_count_sql
_empty_metrics = empty_metrics
_compute_metrics_from_sql = compute_metrics_from_sql
_serialize = serialize


@cached(prefix="reports:wb_bdr", ttl=3600)
async def get_wb_bdr(
    db: AsyncSession,
    project_id: int,
    date_from: date,
    date_to: date,
    brand: str | None = None,
    article: str | None = None,
    group_by: str = "article",
    period_mode: str = "sale",
    include_cost_tax: bool = True,
) -> dict:
    """
    Build BDR from locally cached WB finance data.
    Enriches with ads, cost, tax.
    Returns { summary, articles, brands, period, total_rows, sync_status, tax_info }

    period_mode: "sale" (default) attributes rows by sale/accrual date (accrual
    view); "report" attributes by WB report period so "Итого к оплате" reconciles
    with the WB cabinet. Only the wb_finance SQL date predicate changes — ads /
    cost / cancel enrichment keep their own date windows.

    include_cost_tax: False пропускает себестоимость (load_avg_costs /
    compute_project_valuation — при fifo это сотни тысяч строк в Python) и
    налоговый блок. net_payout/to_pay от них НЕ зависят (net_payout = to_pay −
    adv_sum + finalize_net_payout; реклама load_ads остаётся) — режим для
    потребителей, которым нужна только «Чистая выплата» (payroll-ведомость:
    ~8 вызовов на месяц). cost_price/cost_total/tax_*/profit при False = 0/нет.
    Дефолт True — поведение существующих вызовов не меняется ни на бит.

    Uses SQL-level aggregation for performance (handles 1M+ rows).
    """
    # ── 0. ABC / tag / imt mode: fetch per-article data, then re-aggregate ──
    original_group_by = group_by
    if group_by in ("abc", "tag", "imt"):
        group_by = "article"

    # ── 1. Get sync status ──
    from backend.services.wb_finance_sync import get_sync_status

    sync_status = await get_sync_status(db, project_id)

    # ── 2. SQL aggregation — returns ~N articles instead of 634K rows ──
    params: dict = {
        "project_id": project_id,
        "date_from": date_from,
        "date_to": date_to,
    }
    if brand:
        params["brand"] = brand
    if article:
        safe_article = article.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params["article_like"] = f"%{safe_article}%"

    result = await db.execute(
        text(build_bdr_aggregate_sql(brand, article, group_by=group_by, period_mode=period_mode)), params
    )
    agg_rows = result.mappings().all()

    if not agg_rows:
        return {
            "summary": serialize(_empty_metrics()),
            "articles": [],
            "brands": [],
            "period": {"date_from": str(date_from), "date_to": str(date_to)},
            "total_rows": 0,
            "sync_status": sync_status,
            "tax_info": {},
            "period_mode": period_mode,
        }

    # ── 3. Total row count (for response metadata) ──
    count_result = await db.execute(text(build_total_count_sql(brand, article, period_mode=period_mode)), params)
    total_rows = count_result.scalar() or 0

    # ── 4. Load brands for filter dropdown ──
    brands_result = await db.execute(
        text(build_brands_sql_bdr(period_mode=period_mode)),
        {"project_id": project_id, "date_from": date_from, "date_to": date_to},
    )
    all_brands = sorted(r[0] for r in brands_result)

    # ── 5. Load enrichment data (small queries) ──
    ads_map = await load_ads(db, project_id, date_from, date_to)
    orders_stocks_map = await load_orders_stocks(db, project_id, date_from, date_to)
    cost_overrides = await load_cost_overrides(db, project_id) if include_cost_tax else {}
    # Налог: настройки на каждый месяц диапазона. Одинаковые во всех месяцах
    # (обычный случай) → прежний одноставочный путь бит-в-бит; смешанные →
    # «Итого» считается помесячно, строки — по взвешенной эффективной ставке.
    # include_cost_tax=False → налог не считаем вовсе (tax_info={} в ответе).
    if include_cost_tax:
        tax_by_month = await load_tax_settings_by_month(db, project_id, date_from, date_to)
        tax_uniform = tax_settings_uniform(tax_by_month)
        tax_info = next(iter(tax_by_month.values()))
    else:
        tax_by_month = {}
        tax_uniform = True
        tax_info = {}
    cancel_stats = await load_cancel_stats(db, project_id, date_from, date_to)

    # COGS source depends on the project's valuation method. lifetime_avg keeps the
    # legacy global-average path bit-for-bit (zero regression): cost_map holds the
    # per-unit weighted average, cost_total = cost_price × sale_qty. fifo/moving_avg
    # source per-period COGS from the path-dependent valuation engine (win[sku]["cogs"]),
    # with eff_cost shown as the per-unit price; override fallback covers SKUs the
    # engine doesn't know.
    # include_cost_tax=False → себестоимость не грузим (главная экономия: у fifo
    # compute_project_valuation переваривает сотни тысяч строк в Python).
    method = await get_valuation_method(db, project_id) if include_cost_tax else DEFAULT_METHOD
    win: dict[str, dict] = {}
    # cost_map грузится при ЛЮБОМ методе: для движковых методов это страховка —
    # SKU, по которому движок дал 0 (партии не сматчились по ключу, нетто-ноль
    # окна), не должен уходить в P&L с нулевой себестоимостью, когда средняя
    # закупочная известна (прод-кейс elka_240х220: 123 шт/нед по 0 ₽ при
    # известных 1 396 ₽ — прибыль завышалась на ~172 тыс. за неделю).
    cost_map = await load_avg_costs(db, project_id) if include_cost_tax else {}
    if method != DEFAULT_METHOD:
        valuation = await compute_project_valuation(db, project_id)
        win = slice_window(valuation, date_from, date_to, method)

    period_days = max((date_to - date_from).days + 1, 1)

    # ── 5b. For brand/subject grouping, build nm_id→group and sa_name→group maps ──
    nm_to_group: dict[int, str] = {}
    sa_to_group: dict[str, str] = {}
    if group_by in ("brand", "subject"):
        nm_sql = build_group_nm_ids_sql(group_by, brand, article, period_mode=period_mode)
        if nm_sql:
            nm_result = await db.execute(text(nm_sql), params)
            for r in nm_result.mappings():
                nm_id_val = r["nm_id"]
                if nm_id_val:
                    nm_to_group[int(nm_id_val)] = r["group_key"] or ""

        sa_sql = build_group_sa_names_sql(group_by, brand, article, period_mode=period_mode)
        if sa_sql:
            sa_result = await db.execute(text(sa_sql), params)
            for r in sa_result.mappings():
                sa_to_group[(r["sa_name"] or "").strip().lower()] = r["group_key"] or ""

    # Pre-aggregate enrichment data by group (brand/subject)
    if group_by in ("brand", "subject"):
        # Ads: aggregate by group
        grouped_ads: dict[str, float] = {}
        for nm_id_key, adv_val in ads_map.items():
            gk = nm_to_group.get(nm_id_key, "")
            if gk:
                grouped_ads[gk] = grouped_ads.get(gk, 0) + adv_val

        # Orders/stocks: aggregate by group
        grouped_orders: dict[str, dict] = {}
        for nm_id_key, os_val in orders_stocks_map.items():
            gk = nm_to_group.get(nm_id_key, "")
            if gk:
                if gk not in grouped_orders:
                    grouped_orders[gk] = {"orders_count": 0, "orders_sum": 0, "stocks_wb": 0}
                grouped_orders[gk]["orders_count"] += os_val.get("orders_count", 0)
                grouped_orders[gk]["orders_sum"] += os_val.get("orders_sum", 0)
                grouped_orders[gk]["stocks_wb"] += os_val.get("stocks_wb", 0)

        # Cancel stats: aggregate by group
        grouped_cancel: dict[str, dict] = {}
        for nm_id_key, cs_val in cancel_stats.items():
            gk = nm_to_group.get(nm_id_key, "")
            if gk:
                if gk not in grouped_cancel:
                    grouped_cancel[gk] = {"total": 0, "cancelled": 0}
                grouped_cancel[gk]["total"] += cs_val.get("total", 0)
                grouped_cancel[gk]["cancelled"] += cs_val.get("cancelled", 0)

        # Cost: aggregate avg_cost per article by group (sum costs for cost_total later)
        # grouped_cost_sum: group_key → sum of avg_cost_per_article across all articles in group
        # grouped_cost_count: group_key → number of articles with known cost in group
        # We compute group cost_total = avg(cost_prices) * sale_qty from SQL row
        grouped_cost_sum: dict[str, float] = {}
        grouped_cost_count: dict[str, int] = {}
        for sa_name_key, cost_val in cost_map.items():
            gk = sa_to_group.get(sa_name_key, "")
            if gk and cost_val > 0:
                grouped_cost_sum[gk] = grouped_cost_sum.get(gk, 0.0) + cost_val
                grouped_cost_count[gk] = grouped_cost_count.get(gk, 0) + 1
        # Also fold in cost_overrides (by nm_id → group) for articles missing cost_map entry
        for nm_id_key, override_val in cost_overrides.items():
            gk = nm_to_group.get(nm_id_key, "")
            if gk and override_val > 0:
                # Only add if this nm_id's sa_name is NOT already in cost_map for this group
                grouped_cost_sum[gk] = grouped_cost_sum.get(gk, 0.0) + override_val
                grouped_cost_count[gk] = grouped_cost_count.get(gk, 0) + 1
        # Build grouped_avg_cost: group_key → weighted-mean cost per unit
        grouped_avg_cost: dict[str, float] = {
            gk: grouped_cost_sum[gk] / grouped_cost_count[gk]
            for gk in grouped_cost_sum
            if grouped_cost_count.get(gk, 0) > 0
        }

        # Engine path (fifo/moving_avg): per-group COGS = Σ per-SKU window COGS for
        # the SKUs that map into the group. eff per-unit = group COGS / group net qty.
        grouped_cogs: dict[str, float] = {}
        grouped_cogs_qty: dict[str, int] = {}
        if method != DEFAULT_METHOD:
            for sku, wv in win.items():
                gk = sa_to_group.get(sku, "")
                if not gk:
                    continue
                grouped_cogs[gk] = grouped_cogs.get(gk, 0.0) + float(wv["cogs"])
                grouped_cogs_qty[gk] = grouped_cogs_qty.get(gk, 0) + int(wv["qty"])

    # ── 6. Compute metrics per article from SQL rows ──
    total_metrics = _empty_metrics()
    total_adv = ZERO
    total_cost = ZERO

    result_articles = []
    for row in agg_rows:
        metrics = compute_metrics_from_sql(row)
        sa_name = row["sa_name"] or ""
        nm_id = int(row["nm_id"] or 0)

        metrics["sa_name"] = sa_name
        metrics["brand"] = row["brand_name"] or ""
        metrics["subject"] = row["subject_name"] or ""
        metrics["nm_id"] = nm_id

        # Accumulate totals (skip percentages/averages — they are recalculated)
        _skip_sum = {"buyout_pct", "avg_sale_price", "avg_retail_price", "avg_logistics"}
        for key in total_metrics:
            if key not in _skip_sum:
                total_metrics[key] += D(str(metrics.get(key, 0)))

        if group_by in ("brand", "subject"):
            # Grouped enrichment: use pre-aggregated maps
            adv_sum = float(grouped_ads.get(sa_name, 0))
            metrics["adv_sum"] = adv_sum
            total_adv += D(str(adv_sum))

            # Cost: aggregate avg cost across articles in this group
            sale_qty = metrics["sale_qty"]
            if method != DEFAULT_METHOD and sa_name in grouped_cogs:
                # Engine per-unit cost for the group (Σ window-month COGS / Σ net
                # qty); apply it to the window's actual net units — NOT the engine's
                # whole-month COGS total — so COGS spans the SAME days as revenue.
                # The engine buckets COGS by calendar month, so the raw group total
                # would charge a full month against a sub-month window.
                g_qty = grouped_cogs_qty.get(sa_name, 0)
                cost_price = (grouped_cogs[sa_name] / g_qty) if g_qty > 0 else 0.0
                if cost_price <= 0:
                    # Движок дал группе 0 (партии не сматчились / нетто-ноль окна) —
                    # не списываем ноль при известной средней закупочной.
                    cost_price = grouped_avg_cost.get(sa_name, 0)
                cost_total = cost_price * sale_qty if sale_qty > 0 else 0
            else:
                cost_price = grouped_avg_cost.get(sa_name, 0)
                cost_total = cost_price * sale_qty if sale_qty > 0 else 0
            metrics["cost_price"] = round(cost_price, 2)
            metrics["cost_total"] = round(cost_total, 2)
            total_cost += D(str(cost_total))

            os_data = grouped_orders.get(sa_name, {})
            metrics["orders_count"] = os_data.get("orders_count", 0)
            metrics["orders_sum"] = os_data.get("orders_sum", 0)
            metrics["stocks_wb"] = os_data.get("stocks_wb", 0)

            cs = grouped_cancel.get(sa_name)
            if cs and cs["total"] > 0:
                delivered = cs["total"] - cs["cancelled"]
                metrics["buyout_pct"] = round(float(delivered / cs["total"] * 100), 2)

            metrics["nm_id"] = 0
        else:
            # Per-article enrichment (original logic)
            adv_sum = float(ads_map.get(nm_id, 0))
            metrics["adv_sum"] = adv_sum
            total_adv += D(str(adv_sum))

            # Cost: engine per-period COGS when method != lifetime_avg, else the
            # legacy weighted-average path. Override by nm_id is the fallback for
            # SKUs the engine doesn't cover. cost_map keyed by lowercased
            # article_seller; sa_name from WB may differ in case.
            sku = sa_name.strip().lower() if sa_name else ""
            sale_qty = metrics["sale_qty"]
            if method != DEFAULT_METHOD and sku in win:
                # eff_cost is the engine's per-unit COGS for the window's month(s).
                # Apply it to the window's actual net units (sale_qty) so COGS spans
                # the SAME days as revenue. Using win[sku]["cogs"] directly charges
                # the WHOLE calendar month's COGS against a sub-month window (the
                # engine buckets COGS by month) → COGS far above revenue / phantom loss.
                cost_price = cost_with_fallback(
                    float(win[sku]["eff_cost"]), sku, nm_id, cost_map, cost_overrides
                )
                cost_total = cost_price * sale_qty if sale_qty > 0 else 0
            else:
                cost_price = cost_map.get(sku, 0) if sku else 0
                if cost_price == 0 and nm_id in cost_overrides:
                    cost_price = cost_overrides[nm_id]
                cost_total = cost_price * sale_qty if sale_qty > 0 else 0
            metrics["cost_price"] = round(cost_price, 2)
            metrics["cost_total"] = round(cost_total, 2)
            total_cost += D(str(cost_total))

            # Orders & stocks from funnel
            os_data = orders_stocks_map.get(nm_id, {})
            metrics["orders_count"] = os_data.get("orders_count", 0)
            metrics["orders_sum"] = os_data.get("orders_sum", 0)
            metrics["stocks_wb"] = os_data.get("stocks_wb", 0)

            # Per-article buyout from order cancel data (more accurate than finance report)
            cs = cancel_stats.get(nm_id)
            if cs and cs["total"] > 0:
                delivered = cs["total"] - cs["cancelled"]
                metrics["buyout_pct"] = round(float(delivered / cs["total"] * 100), 2)

        metrics["_period_days"] = period_days

        result_articles.append(metrics)

    # Sort by to_pay descending
    result_articles.sort(key=lambda x: x["to_pay"], reverse=True)

    # ── 7. Summary ──
    summary_result = {k: float(v) for k, v in total_metrics.items()}

    # Recalculate averages/percentages from totals (cannot sum them across articles)
    total_sale_qty = summary_result.get("sale_qty_gross", 0) or summary_result.get("sale_qty", 0)
    total_ret_qty = summary_result.get("ret_qty", 0)
    net_sale_qty = summary_result.get("sale_qty", 0)

    # Buyout % from order cancel data (accurate), fallback to finance report formula
    total_orders_all = sum(cs["total"] for cs in cancel_stats.values())
    total_cancelled_all = sum(cs["cancelled"] for cs in cancel_stats.values())
    if total_orders_all > 0:
        delivered = total_orders_all - total_cancelled_all
        summary_result["buyout_pct"] = round(delivered / total_orders_all * 100, 2)
    else:
        total_qty = total_sale_qty + total_ret_qty
        summary_result["buyout_pct"] = round(total_sale_qty / total_qty * 100, 2) if total_qty > 0 else 0
    summary_result["avg_sale_price"] = (
        round(summary_result.get("sales_amount", 0) / net_sale_qty, 2) if net_sale_qty > 0 else 0
    )
    summary_result["avg_retail_price"] = (
        round(summary_result.get("realization", 0) / net_sale_qty, 2) if net_sale_qty > 0 else 0
    )
    summary_result["avg_logistics"] = (
        round(summary_result.get("logistics", 0) / net_sale_qty, 2) if net_sale_qty > 0 else 0
    )

    # Ads + cost totals
    summary_result["adv_sum"] = float(total_adv)
    summary_result["cost_total"] = float(total_cost)

    # ── 8. Tax calculation ──
    # include_cost_tax=False → пропускаем целиком: net_payout/to_pay от налога
    # не зависят, а blended-путь делает дополнительные SQL-проходы.
    if not include_cost_tax:
        pass
    elif tax_uniform:
        apply_tax(summary_result, tax_info, D(str(summary_result["adv_sum"])), total_cost)
    else:
        # Смена ставок внутри диапазона: «Итого»-налог помесячно (точно), строки —
        # по blended-ставке. Себестоимость раскладывается по месяцам долей дохода
        # (нужна только режиму Д−Р с cost_as_expense; помесячного среза COGS у
        # сводки нет — допущение задокументировано в blended_tax_info).
        monthly_rows = (
            await db.execute(text(build_bdr_monthly_totals_sql(brand, article, period_mode=period_mode)), params)
        ).mappings().all()
        if group_by in ("brand", "subject"):
            included_nms = {nm for nm in nm_to_group if nm}
        else:
            included_nms = {a["nm_id"] for a in result_articles if a.get("nm_id")}
        ads_by_month = await load_ads_monthly(db, project_id, date_from, date_to)
        total_income = sum(float(compute_metrics_from_sql(r)["sales_amount"]) for r in monthly_rows) or 1.0
        monthly_tax: dict[str, dict] = {}
        for mrow in monthly_rows:
            mm = compute_metrics_from_sql(mrow)
            income_m = float(mm["sales_amount"])
            monthly_tax[mrow["month_key"]] = {
                "income": income_m,
                "logistics": float(mm["logistics"]),
                "storage": float(mm["storage"]),
                "commission": float(mm["commission"]),
                "penalties": float(mm["penalties"]),
                "deductions": float(mm["deductions"]),
                "adv": sum(
                    a for nm, a in ads_by_month.get(mrow["month_key"], {}).items() if nm in included_nms
                ),
                "cost": float(total_cost) * income_m / total_income,
            }
        apply_tax_by_month(
            summary_result, monthly_tax, tax_by_month, D(str(summary_result["adv_sum"])), total_cost
        )
        tax_info = blended_tax_info(
            {mk: m["income"] for mk, m in monthly_tax.items()}, tax_by_month
        )
    if include_cost_tax:
        for art_row in result_articles:
            apply_tax_article(art_row, tax_info)

    # ── 9. Computed enrichment fields ──
    total_real = float(summary_result.get("realization", 0)) or 1
    total_sales = float(summary_result.get("sales_amount", 0)) or 1

    sum_orders_count = 0
    sum_orders_sum = 0.0
    sum_stocks_wb = 0
    for art_row in result_articles:
        enrich_article(art_row, total_real, total_sales, period_days)
        sum_orders_count += art_row.get("orders_count", 0)
        sum_orders_sum += art_row.get("orders_sum", 0)
        sum_stocks_wb += art_row.get("stocks_wb", 0)

    summary_result["orders_count"] = sum_orders_count
    summary_result["orders_sum"] = round(sum_orders_sum, 2)
    summary_result["stocks_wb"] = sum_stocks_wb
    enrich_article(summary_result, total_real, total_sales, period_days)

    # Чистая выплата: разносим некатегорийные удержания (кредиты + зазор рекламы)
    # по категориям, чтобы Σ = фактической выплате ВБ. Подробности — в хелпере.
    finalize_net_payout(result_articles, summary_result)

    # ── 10. ABC analysis ──
    compute_abc(result_articles)

    # ── 11. Re-aggregate by tag / imt if needed ──
    if original_group_by in ("tag", "imt"):
        result_articles = await _reaggregate_by_classification(db, project_id, result_articles, original_group_by)

    return {
        "summary": serialize(summary_result),
        "articles": [serialize(a) for a in result_articles],
        "brands": all_brands,
        "period": {"date_from": str(date_from), "date_to": str(date_to)},
        "total_rows": total_rows,
        "sync_status": sync_status,
        "tax_info": tax_info,
        "group_by": original_group_by,
        "period_mode": period_mode,
    }


async def _reaggregate_by_classification(
    db: AsyncSession,
    project_id: int,
    articles: list[dict],
    mode: str,
) -> list[dict]:
    """Re-aggregate per-article BDR rows into tag or imt groups."""
    from collections import defaultdict

    from backend.models.cost import Nomenclature
    from backend.models.refs import ImtAlias, ProductTag, ProductTagMap

    # Build nm_id → group_keys mapping
    nm_to_groups: dict[int, list[str]] = defaultdict(list)

    if mode == "tag":
        tag_result = await db.execute(
            select(ProductTagMap.nm_id, ProductTag.name)
            .join(ProductTag, ProductTag.id == ProductTagMap.tag_id)
            .where(ProductTagMap.project_id == project_id, ProductTag.is_deleted.is_(False))
        )
        for nm_id, tag_name in tag_result:
            nm_to_groups[nm_id].append(tag_name)
    elif mode == "imt":
        nom_result = await db.execute(
            select(Nomenclature.article_wb, Nomenclature.imt_id).where(
                Nomenclature.project_id == project_id, Nomenclature.imt_id.isnot(None)
            )
        )
        nm_imt: dict[int, int] = {}
        for article_wb, imt_id in nom_result:
            if article_wb and imt_id:
                nm_imt[article_wb] = imt_id

        alias_result = await db.execute(select(ImtAlias.imt_id, ImtAlias.name).where(ImtAlias.project_id == project_id))
        imt_aliases = {r.imt_id: r.name for r in alias_result}

        for nm_id, imt_id in nm_imt.items():
            label = imt_aliases.get(imt_id, f"#{imt_id}")
            nm_to_groups[nm_id].append(label)

    # Summable fields for aggregation
    _SUM_KEYS = [
        "realization",
        "sales_amount",
        "ret_amount",
        "to_pay",
        "net_payout",
        "logistics",
        "storage_fee",
        "acceptance",
        "penalty",
        "additional_payment",
        "sale_qty",
        "sale_qty_gross",
        "ret_qty",
        "deduction",
        "ad_deduction",
        "loan_deduction",
        "other_deduction",
        "adv_sum",
        "cost_total",
        "ppvz_reward",
        "ppvz_sales_commission",
        "ppvz_for_pay",
        "ppvz_vw",
        "ppvz_vw_nds",
        "delivery_rub",
        "rebill_logistic_cost",
        "tax_usn",
        "tax_nds",
        "tax_total",
        "profit",
        "orders_count",
        "orders_sum",
        "stocks_wb",
    ]

    groups: dict[str, dict] = {}
    for art in articles:
        nm_id = art.get("nm_id", 0)
        group_keys = nm_to_groups.get(nm_id)
        if not group_keys:
            group_keys = ["Без ярлыка" if mode == "tag" else "Без склейки"]

        for gk in group_keys:
            if gk not in groups:
                groups[gk] = {k: 0 for k in _SUM_KEYS}
                groups[gk]["sa_name"] = gk
                groups[gk]["brand"] = ""
                groups[gk]["subject"] = ""
                groups[gk]["nm_id"] = 0
                groups[gk]["_period_days"] = art.get("_period_days", 1)
            grp = groups[gk]
            for k in _SUM_KEYS:
                grp[k] = grp.get(k, 0) + (art.get(k, 0) or 0)

    # Recalculate derived fields
    result = list(groups.values())
    for grp in result:
        sale_qty = grp.get("sale_qty", 0) or 0
        ret_qty = grp.get("ret_qty", 0) or 0
        total_qty = sale_qty + ret_qty
        grp["buyout_pct"] = round(sale_qty / total_qty * 100, 2) if total_qty > 0 else 0
        grp["avg_sale_price"] = round(grp.get("sales_amount", 0) / sale_qty, 2) if sale_qty > 0 else 0
        grp["avg_retail_price"] = round(grp.get("realization", 0) / sale_qty, 2) if sale_qty > 0 else 0
        grp["avg_logistics"] = round(grp.get("logistics", 0) / sale_qty, 2) if sale_qty > 0 else 0
        grp["cost_price"] = round(grp["cost_total"] / sale_qty, 2) if sale_qty > 0 else 0

    result.sort(key=lambda x: x.get("to_pay", 0), reverse=True)
    return result
