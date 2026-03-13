"""Order geography service — aggregates WB orders by delivery city/region."""
import logging
from collections import Counter, defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached

logger = logging.getLogger("dds.order_geography")


@cached(prefix="reports:order_geography", ttl=300)
async def get_order_geography(
    db: AsyncSession,
    project_id: int,
    date_from: str,
    date_to: str,
    brand: str | None = None,
    category: str | None = None,
    article: str | None = None,
) -> dict:
    """Aggregate WB orders by delivery city.

    Returns cities with order counts, daily chart data, available dates,
    and filter options (brands, categories, articles).
    """
    from backend.services.funnel.wb_api_client import get_wb_key, fetch_supplier_orders
    from backend.models.order_city import OrderCityMap

    api_key = await get_wb_key(db, project_id, "wb")
    if not api_key:
        return _empty_response()

    # Load city map from uploaded Excel
    city_rows = await db.execute(
        select(OrderCityMap.srid, OrderCityMap.city, OrderCityMap.okrug).where(
            OrderCityMap.project_id == project_id
        )
    )
    city_map: dict[str, str] = {}
    okrug_map: dict[str, str] = {}
    for row in city_rows.fetchall():
        city_map[row.srid] = row.city
        if row.okrug:
            okrug_map[row.srid] = row.okrug

    # Fetch orders from WB API
    orders = await fetch_supplier_orders(api_key, date_from)
    if not orders:
        return _empty_response()

    # Parse date range
    dt_from = date.fromisoformat(date_from)
    dt_to = date.fromisoformat(date_to)

    # Aggregate
    city_counter: Counter[str] = Counter()
    daily_counter: Counter[str] = Counter()
    dates_set: set[str] = set()
    brands_set: set[str] = set()
    categories_set: set[str] = set()
    articles_set: set[str] = set()
    city_region: dict[str, str] = {}    # city -> regionName
    city_okrug: dict[str, str] = {}     # city -> okrug

    for order in orders:
        order_date_str = order.get("date", "")[:10]
        if not order_date_str:
            continue

        # Date filter
        try:
            order_date = date.fromisoformat(order_date_str)
        except ValueError:
            continue
        if order_date < dt_from or order_date > dt_to:
            continue

        # Skip cancelled
        if order.get("isCancel"):
            continue

        order_brand = order.get("brand", "") or ""
        order_category = order.get("subject", "") or ""
        order_article = order.get("supplierArticle", "") or ""

        # Collect filter options (before filtering)
        if order_brand:
            brands_set.add(order_brand)
        if order_category:
            categories_set.add(order_category)
        if order_article:
            articles_set.add(order_article)

        # Apply filters
        if brand and order_brand != brand:
            continue
        if category and order_category != category:
            continue
        if article and order_article != article:
            continue

        # Determine city
        srid = str(order.get("srid", ""))
        city = city_map.get(srid)
        if not city:
            city = order.get("regionName", "Неизвестно")

        region = order.get("regionName", "")
        okrug = okrug_map.get(srid, order.get("oblastOkrugName", ""))

        city_counter[city] += 1
        daily_counter[order_date_str] += 1
        dates_set.add(order_date_str)
        if city not in city_region and region:
            city_region[city] = region
        if city not in city_okrug and okrug:
            city_okrug[city] = okrug

    # Build cities list sorted by count desc
    cities = []
    for city_name, count in city_counter.most_common():
        cities.append({
            "city": city_name,
            "region": city_region.get(city_name, ""),
            "okrug": city_okrug.get(city_name, ""),
            "order_count": count,
        })

    # Build daily chart data sorted by date
    daily = [{"date": d, "count": daily_counter[d]} for d in sorted(daily_counter)]

    # Available dates sorted
    dates = sorted(dates_set)

    return {
        "cities": cities,
        "daily": daily,
        "dates": dates,
        "totals": {
            "total_orders": sum(city_counter.values()),
            "unique_cities": len(city_counter),
        },
        "filters": {
            "brands": sorted(brands_set),
            "categories": sorted(categories_set),
            "articles": sorted(articles_set),
        },
    }


def _empty_response() -> dict:
    """Return empty response structure."""
    return {
        "cities": [],
        "daily": [],
        "dates": [],
        "totals": {"total_orders": 0, "unique_cities": 0},
        "filters": {"brands": [], "categories": [], "articles": []},
    }
