"""Управление рекламой — список кампаний и «нехватка бюджета».

Read-only поверх существующих таблиц: wb_ad_campaigns (кампании+бюджеты,
часовой синк), wb_ad_campaign_daily (расход по дням), wb_ad_campaign_events
(история изменений бюджета). Час «бюджет кончился» восстанавливается из
событий budget_change → 0 с точностью до интервала синка (~1 час).
"""

import json
import logging
import asyncio
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytz
from sqlalchemy import Date, cast, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbAdCampaign, WbAdCampaignEvent, WbFunnelDaily
from backend.models.integrations import WbAdCampaignDaily, WbAdNmDaily, WbAdCampaignSnapshot
from backend.services.funnel.bdr_rates import BdrRatesLookup
from backend.utils.time import msk_now, msk_today, utcnow

logger = logging.getLogger("dds.funnel")

MSK = pytz.timezone("Europe/Moscow")

CAMPAIGN_STATUS_LABELS = {4: "Готова", 7: "Завершена", 9: "Активна", 11: "Пауза", -1: "Удаляется"}

# WB не принимает пополнение рекламной кампании меньше этой суммы.
MIN_TOPUP_RUB = 1000.0

# Окно (дней МСК) — глубина поиска полных дней для оценки «реальных возможностей».
BUDGET_GAP_WINDOW_DAYS = 30
# Потенциал полного дня = медиана по стольким ПОСЛЕДНИМ дням-остановкам (текущий режим,
# а не устаревшее среднее). Фолбэк на полные дни, если кампания не упирается в бюджет.
BUDGET_GAP_POTENTIAL_DAYS = 7
# «Хронический» риск: кампания регулярно упирается в бюджет до конца дня. Показываем её
# в «нехватке» ещё утром, до фактической остановки (прогноз по паттерну прошлых дней).
BUDGET_GAP_CHRONIC_MIN_DAYS = 3    # минимум дней-остановок в окне, чтобы счесть паттерном
BUDGET_GAP_CHRONIC_MIN_RATE = 0.5  # доля дней-остановок среди дней с расходом


# ─── Список кампаний ─────────────────────────────────────────────────────────


async def list_ad_campaigns(
    db: AsyncSession,
    project_id: int,
    tax_info: dict | None = None,
    bdr_rates_map: BdrRatesLookup | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Все кампании проекта + расход/клики/CTR, ДРР и маржа за выбранный период.

    Период по умолчанию — последние 7 дней (МСК). «Расход сегодня» — всегда
    за сегодня, независимо от периода. ДРР/маржа считаются по товарам кампании
    (nm_ids) из воронки (get_funnel_by_sku, та же формула, что на странице
    воронки). tax_info=None — без ДРР/маржи.
    """
    campaigns = (
        (await db.execute(select(WbAdCampaign).where(WbAdCampaign.project_id == project_id).limit(2000)))
        .scalars()
        .all()
    )
    if not campaigns:
        return []

    today_msk = msk_today()  # НЕ utcnow().date(): в 00:00–02:59 МСК это вчера
    period_to = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else today_msk
    period_from = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else period_to - timedelta(days=6)
    period_days = (period_to - period_from).days + 1  # дней в периоде — для «затраты в час»

    CD = WbAdCampaignDaily
    spend_rows = (
        await db.execute(
            select(
                CD.campaign_id,
                func.coalesce(func.sum(CD.spend), Decimal("0")).label("spend"),
                func.coalesce(func.sum(CD.views), 0).label("views"),
                func.coalesce(func.sum(CD.clicks), 0).label("clicks"),
            )
            .where(CD.project_id == project_id, CD.date >= period_from, CD.date <= period_to)
            .group_by(CD.campaign_id)
        )
    ).all()
    spend_map = {r.campaign_id: r for r in spend_rows}

    today_rows = (
        await db.execute(
            select(CD.campaign_id, func.coalesce(func.sum(CD.spend), Decimal("0")).label("today"))
            .where(CD.project_id == project_id, CD.date == today_msk)
            .group_by(CD.campaign_id)
        )
    ).all()
    today_map = {r.campaign_id: float(r.today) for r in today_rows}

    # Выручка ВЧЕРА по товарам (для «ДРР план»): сумма заказов из воронки (all-traffic),
    # тот же источник, что у ДРР списка. Фронт умножит её на целевой ДРР % из шапки.
    yesterday = today_msk - timedelta(days=1)
    yest_rows = (
        await db.execute(
            select(
                WbFunnelDaily.nm_id,
                func.coalesce(func.sum(WbFunnelDaily.orders_sum_rub), Decimal("0")).label("rev"),
            )
            .where(WbFunnelDaily.project_id == project_id, WbFunnelDaily.date == yesterday)
            .group_by(WbFunnelDaily.nm_id)
        )
    ).all()
    yest_rev_map = {r.nm_id: float(r.rev) for r in yest_rows}

    # Расход кампаний ВЧЕРА — для ДРР за вчерашний день (расход вчера / заказы вчера)
    yest_spend_rows = (
        await db.execute(
            select(CD.campaign_id, func.coalesce(func.sum(CD.spend), Decimal("0")).label("spend"))
            .where(CD.project_id == project_id, CD.date == yesterday)
            .group_by(CD.campaign_id)
        )
    ).all()
    yest_spend_map = {r.campaign_id: float(r.spend) for r in yest_spend_rows}

    # Недобор бюджета за сегодня (до полуночи) по кампаниям с исчерпанным бюджетом
    gap_map = await _budget_gap_today_map(db, project_id, list(campaigns), today_map)

    # Карта nm_id → бренд/категория (для фильтров на фронте)
    nm_meta_rows = (
        await db.execute(
            select(WbFunnelDaily.nm_id, func.max(WbFunnelDaily.brand).label("brand"), func.max(WbFunnelDaily.subject).label("subject"))
            .where(WbFunnelDaily.project_id == project_id)
            .group_by(WbFunnelDaily.nm_id)
        )
    ).all()
    nm_meta = {r.nm_id: (r.brand, r.subject) for r in nm_meta_rows}

    # Финансовые метрики товаров за период — из воронки (revenue/profit/orders_sum)
    sku_map: dict[int, dict] = {}
    if tax_info is not None:
        from backend.services.funnel.queries import get_funnel_by_sku

        sku_rows = await get_funnel_by_sku(
            db, project_id, tax_info, period_from.isoformat(), period_to.isoformat(),
            None, None, bdr_rates_map=bdr_rates_map, limit=5000,
        )
        sku_map = {r["nm_id"]: r for r in sku_rows}

    result: list[dict[str, Any]] = []
    for c in campaigns:
        s = spend_map.get(c.campaign_id)
        nm_ids = c.nm_ids or []
        spend_period = float(s.spend) if s else 0.0
        views_period = int(s.views) if s else 0
        clicks_period = int(s.clicks) if s else 0
        # Агрегат по товарам кампании (все источники трафика)
        orders_p = revenue_p = profit_p = 0.0
        opens_p = carts_p = order_cnt_p = 0
        for nm in nm_ids:
            row = sku_map.get(nm)
            if row:
                orders_p += float(row.get("orders_sum_rub") or 0)
                revenue_p += float(row.get("revenue") or 0)
                profit_p += float(row.get("profit") or 0)
                opens_p += int(row.get("open_card") or 0)
                carts_p += int(row.get("add_to_cart") or 0)
                order_cnt_p += int(row.get("orders_count") or 0)
        brands = sorted({nm_meta[nm][0] for nm in nm_ids if nm in nm_meta and nm_meta[nm][0]})
        subjects = sorted({nm_meta[nm][1] for nm in nm_ids if nm in nm_meta and nm_meta[nm][1]})
        rev_yesterday = sum(yest_rev_map.get(nm, 0.0) for nm in nm_ids)  # сумма заказов товаров вчера
        spend_yesterday = yest_spend_map.get(c.campaign_id, 0.0)  # расход кампании вчера
        result.append(
            {
                "campaign_id": c.campaign_id,
                "name": c.name,
                "campaign_type": c.campaign_type,
                "advert_type": c.advert_type,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "status": c.status,
                "status_label": CAMPAIGN_STATUS_LABELS.get(c.status, str(c.status)),
                "budget": float(c.budget or 0),
                "nm_ids": nm_ids,
                "nm_count": len(nm_ids),
                "brands": brands,
                "subjects": subjects,
                "spend_today": today_map.get(c.campaign_id, 0.0),
                "spend_period": spend_period,
                "views_period": views_period,
                "clicks_period": clicks_period,
                "ctr": round(clicks_period / views_period * 100, 2) if views_period else 0,
                "cpc": round(spend_period / clicks_period, 2) if clicks_period else 0,
                # Стоимость корзины/заказа за период: расход кампании / корзины (заказы) её товаров
                "cpl": round(spend_period / carts_p, 2) if carts_p else 0,
                "cpo": round(spend_period / order_cnt_p, 2) if order_cnt_p else 0,
                # ДРР за вчера: расход кампании вчера / сумма заказов её товаров вчера
                "drr": round(spend_yesterday / rev_yesterday * 100, 2) if rev_yesterday else 0,
                "margin": round(profit_p / revenue_p * 100, 2) if revenue_p else 0,
                # Средний расход в час за день = расход за период / (дней × 24 ч)
                "spend_per_hour": round(spend_period / (period_days * 24), 2) if period_days > 0 else 0,
                # Режим ставки CPM (единая/ручная) — из WB bid_type, заполняется синком
                "bid_mode": c.bid_mode,
                # Ставка кампании ₽ по активной зоне — для инлайн-правки в списке (из синка)
                "default_bid": float(c.default_bid) if c.default_bid is not None else None,
                # Доля рекл. кликов = клики кампании / все переходы её товаров
                "ad_click_share": round(clicks_period / opens_p * 100, 2) if opens_p else 0,
                "cr_cart": round(carts_p / opens_p * 100, 2) if opens_p else 0,  # конверсия в корзину
                "cr_order": round(order_cnt_p / carts_p * 100, 2) if carts_p else 0,  # конверсия в заказ
                "rev_yesterday": round(rev_yesterday, 2),  # сумма заказов товаров вчера (для «ДРР план»)
                "budget_gap": gap_map.get(c.campaign_id, 0.0),  # недобор бюджета до конца дня, ₽
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
        )
    # Активные с расходом — первыми, затем по расходу за период
    result.sort(key=lambda r: (r["status"] != 9, -r["spend_period"]))
    return result


# ─── История кампании (для графика в UI) ────────────────────────────────────


async def get_campaign_history(
    db: AsyncSession,
    project_id: int,
    campaign_id: int,
    date_from: str | None = None,
    date_to: str | None = None,
    days: int = 30,
) -> list[dict]:
    """История кампании по дням за выбранный период: расход кампании + метрики товаров.

    Диапазон = [date_from, date_to] (если date_from не задан — последние `days`
    дней до date_to). Формат совпадает с точками графика артикула: date,
    price_spp (средняя цена товаров), open_card, adv_sum (расход ИМЕННО этой
    кампании), drr (расход / заказы её товаров), orders_sum_rub.
    """
    camp = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id,
            )
        )
    ).scalar_one_or_none()
    if camp is None:
        return []
    nm_ids = camp.nm_ids or []

    today_msk = msk_today()  # НЕ utcnow().date(): в 00:00–02:59 МСК это вчера
    period_to = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else today_msk
    period_from = (
        datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else period_to - timedelta(days=days - 1)
    )

    CD = WbAdCampaignDaily
    spend_rows = (
        await db.execute(
            select(CD.date, func.coalesce(func.sum(CD.spend), Decimal("0")).label("spend"))
            .where(
                CD.project_id == project_id, CD.campaign_id == campaign_id,
                CD.date >= period_from, CD.date <= period_to,
            )
            .group_by(CD.date)
        )
    ).all()
    spend_by_date = {r.date: float(r.spend) for r in spend_rows}

    funnel_by_date: dict = {}
    if nm_ids:
        F = WbFunnelDaily
        f_rows = (
            await db.execute(
                select(
                    F.date,
                    func.coalesce(func.sum(F.open_card), 0).label("open_card"),
                    func.coalesce(func.sum(F.orders_sum_rub), Decimal("0")).label("orders_sum"),
                    func.avg(func.nullif(F.avg_price, 0)).label("avg_price"),
                )
                .where(F.project_id == project_id, F.nm_id.in_(nm_ids), F.date >= period_from, F.date <= period_to)
                .group_by(F.date)
            )
        ).all()
        funnel_by_date = {r.date: r for r in f_rows}

    result = []
    d = period_from
    while d <= period_to:
        f = funnel_by_date.get(d)
        spend = spend_by_date.get(d, 0.0)
        orders_sum = float(f.orders_sum) if f else 0.0
        if f or spend > 0:
            result.append(
                {
                    "date": d.isoformat(),
                    "price_spp": round(float(f.avg_price), 2) if f and f.avg_price else 0,
                    "open_card": int(f.open_card) if f else 0,
                    "adv_sum": round(spend, 2),
                    "drr": round(spend / orders_sum * 100, 2) if orders_sum else 0,
                    "orders_sum_rub": round(orders_sum, 2),
                }
            )
        d += timedelta(days=1)
    return result


# Порог смены НАШЕЙ цены, доля. Ниже — шум: у кампании на несколько товаров avg_price
# это средняя по заказам дня, и она гуляет от того, каких цветов/размеров взяли больше.
PRICE_EVENT_MIN_DELTA = 0.03
# Порог смены СПП, п.п. ВБ шевелит скидку на 0.5–1.5 п.п. почти каждый день.
SPP_EVENT_MIN_DELTA_PP = 3.0
# Сколько дней усредняем с каждой стороны, чтобы отличить СДВИГ УРОВНЯ от однодневного
# выброса. Без этого «33% → 30%» и назавтра «30% → 33%» давали два события подряд —
# ВБ ничего не решал, просто день выбился. Медиана (а не среднее) не тянется за выбросом.
LEVEL_WINDOW = 3


def _level_shifts(
    pts: Sequence[tuple[date, float]], min_delta: float, relative: bool,
) -> list[tuple[date, float, float]]:
    """Сдвиги уровня в ряду (день, значение) → [(день, уровень «до», уровень «после»)].

    Сравниваем медиану LEVEL_WINDOW дней ДО дня с медианой LEVEL_WINDOW дней НАЧИНАЯ с
    него. Событие — только если уровень действительно переехал и остался там; разовый
    выброс медиану не сдвигает. После срабатывания пропускаем окно вперёд, иначе один
    переезд дал бы событие на каждом дне окна.

    relative=True — порог в долях (наша цена), иначе в абсолютных единицах (СПП, п.п.).
    """
    out: list[tuple[date, float, float]] = []
    vals = [v for _, v in pts]
    skip_until = -1
    last_shift = -1  # индекс предыдущего зафиксированного сдвига — левая граница поиска
    for k in range(1, len(pts)):
        if k <= skip_until:
            continue
        back = _median(vals[max(0, k - LEVEL_WINDOW):k])
        fwd = _median(vals[k:k + LEVEL_WINDOW])
        if back is None or fwd is None or back <= 0:
            continue
        delta = abs(fwd - back) / back if relative else abs(fwd - back)
        if delta >= min_delta:
            # Медиана «вперёд» включает сам день k, поэтому срабатывание может опередить
            # реальную смену на день-два (два новых значения из трёх уже перетянули
            # медиану). Уточняем: событие принадлежит ПЕРВОМУ дню окна, который сам ближе
            # к новому уровню, чем к старому, — иначе разделитель встанет не на ту границу.
            # Ищем и НАЗАД в пределах окна: при цепочке смен подряд (цену снижали неделю
            # шагами) окно пропуска после прошлого события съедает настоящий день перехода,
            # и разделитель уезжает на сутки вперёд. Дальше прошлого сдвига не заходим.
            lo = max(last_shift + 1, k - LEVEL_WINDOW + 1, 0)
            shift = next(
                (j for j in range(lo, min(k + LEVEL_WINDOW, len(pts)))
                 if abs(vals[j] - fwd) < abs(vals[j] - back)),
                k,
            )
            out.append((pts[shift][0], back, fwd))
            last_shift = shift
            skip_until = k + LEVEL_WINDOW - 1
    return out


def _price_change_events(
    rows_asc: Sequence[tuple[date, float | None, float | None]],
) -> list[dict]:
    """События смены цены из ряда (день, наша цена, СПП %), отсортированного по возрастанию.

    РАЗДЕЛЯЕМ ДВА ИСТОЧНИКА СКИДКИ — это разные управленческие ситуации:
      • `price` — цену поменяли МЫ (уступили маржу, решение продавца);
      • `spp`   — цену клиенту поменял ВБ своей скидкой (мы на неё не влияем, и завтра
        ВБ может её убрать; строить на ней выводы о «работающей цене» опасно).
    Оба ведут к одному — изменению цены клиенту, но реагировать на них надо по-разному,
    поэтому в тексте события всегда есть и цена клиенту, и её источник.

    Событие вешается на день, С КОТОРОГО держится новый уровень: цифры этого дня — уже
    про новую цену. Дни без данных ряд не разрывают.
    """
    out: list[dict] = []

    price_pts = [(d, p) for d, p, _ in rows_asc if p is not None and p > 0]
    for d, was, now in _level_shifts(price_pts, PRICE_EVENT_MIN_DELTA, relative=True):
        pct = (now - was) / was * 100
        out.append({
            "date": d.isoformat(), "kind": "price",
            # short/value — подпись метки в ячейке даты (число красится по знаку отдельно),
            # text — полная формулировка в подсказке
            "short": "цена", "value": f"{pct:+.0f}%", "dir": 1 if pct > 0 else -1,
            "text": f"наша цена {_rub(was)} → {_rub(now)} ({pct:+.0f}%)",
        })

    spp_pts = [(d, sp) for d, _, sp in rows_asc if sp is not None]
    price_by_day = {d: p for d, p, _ in rows_asc if p is not None and p > 0}
    for d, was, now in _level_shifts(spp_pts, SPP_EVENT_MIN_DELTA_PP, relative=False):
        # Цену клиенту считаем от нашей цены ЭТОГО дня по обоим уровням СПП: показываем
        # вклад именно скидки ВБ, не смешивая его с нашей правкой цены.
        base = price_by_day.get(d) or next((p for dd, p in sorted(price_by_day.items()) if dd >= d), None)
        tail = ""
        if base:
            tail = f" · клиенту {_rub(base * (1 - was / 100))} → {_rub(base * (1 - now / 100))}"
        out.append({
            "date": d.isoformat(), "kind": "spp",
            "short": "СПП", "value": f"{now - was:+.0f} п.п.", "dir": 1 if now > was else -1,
            "text": f"скидка ВБ (СПП) {was:.0f}% → {now:.0f}%{tail}",
        })
    return out


def _rub(v: float) -> str:
    """1234.5 → «1 235 ₽» (неразрывный пробел как разделитель разрядов)."""
    return f"{v:,.0f}".replace(",", " ") + " ₽"


def _msk_day_bounds(d: date) -> tuple[datetime, datetime]:
    """Границы МСК-суток в naive UTC — в таком виде хранится created_at событий."""
    start = MSK.localize(datetime.combine(d, datetime.min.time())).astimezone(pytz.UTC).replace(tzinfo=None)
    end = MSK.localize(datetime.combine(d + timedelta(days=1), datetime.min.time())).astimezone(pytz.UTC).replace(tzinfo=None)
    return start, end


def _fmt_span(minutes: float) -> str:
    """Длительность простоя в компактный вид: «40 мин», «2 ч», «3 ч 20 мин»."""
    m = int(round(minutes))
    if m < 60:
        return f"{m} мин"
    h, rest = divmod(m, 60)
    return f"{h} ч" if rest < 10 else f"{h} ч {rest} мин"


def _intraday_budget_gaps(
    ev_rows: Sequence[WbAdCampaignEvent], skip_days: set[date],
) -> dict[date, tuple[datetime, datetime]]:
    """Дни, когда бюджет кончался, но его ДОЛИЛИ и кампания добрала день.

    `_runout_by_day` такие дни намеренно не считает остановленными — день закончился с
    бюджетом. Но кампания реально стояла от обнуления до долива, и в цифрах дня это
    провал без объяснения. Возвращаем {день: (момент нуля, момент долива)}.

    skip_days — дни, что и так закончились на нуле: там уже стоит метка «стоп», второй
    значок про тот же бюджет только зашумит строку.
    """
    out: dict[date, tuple[datetime, datetime]] = {}
    zero_at: dict[date, datetime] = {}
    for ev in ev_rows:
        v = _parse_num(ev.new_value)
        if v is None:
            continue
        d = pytz.UTC.localize(ev.created_at).astimezone(MSK).date()
        if d in skip_days:
            continue
        if v <= 0:
            zero_at[d] = ev.created_at        # последнее обнуление дня
            out.pop(d, None)                  # долив был раньше — он уже не в счёт
        elif d in zero_at and d not in out:
            out[d] = (zero_at[d], ev.created_at)
    return out


# День считается простоем, если показов практически нет: доля от медианы окна ниже этой.
# Не ноль — WB досчитывает единичные показы даже у остановленной кампании.
IDLE_VIEWS_SHARE = 0.05


def _idle_days(views_by_day: Sequence[tuple[date, int]], skip: set[date]) -> set[date]:
    """Дни, когда кампания практически не крутилась → метка «простой весь день».

    Считаем по ФАКТУ показов, а НЕ по журналу статусов. Журнал ненадёжен: у живой
    кампании divan_lightgrey он показывал «пауза» с 27.06 по 08.07, тогда как в эти дни
    она откручивала 12–22 тыс. показов и тратила по 6–11 тыс. ₽ в день. Показы — то, что
    видно в самой таблице, и они не расходятся с остальными её цифрами.

    Порог — доля от МЕДИАНЫ окна: у кампаний разного масштаба «почти ноль» разный, а
    медиана не тянется за выбросами вроде дня с тройным бюджетом.

    skip — дни, у которых уже есть метка про бюджет: там причина названа точнее.
    """
    live = [v for _, v in views_by_day if v > 0]
    if len(live) < 3:
        return set()
    med = _median([float(v) for v in live])
    if not med:
        return set()
    limit = med * IDLE_VIEWS_SHARE
    # Ровный ноль не метим: там вся строка нулевая, метка ничего не добавляет. Ценность —
    # в днях, где показы ЕСТЬ, но их в разы меньше обычного: глазом такую строку не отличить
    # от рабочей, а сравнивать её с остальными нельзя.
    return {d for d, v in views_by_day if 0 < v <= limit and d not in skip}


async def _campaign_day_events(
    db: AsyncSession, project_id: int, campaign_id: int,
    period_from: date, period_to: date,
    price_by_date: Sequence[tuple[date, float | None, float | None]],
    views_by_date: Sequence[tuple[date, int]],
) -> list[dict]:
    """События, объясняющие изломы в посуточных метриках кампании.

    Каждое событие привязано к МСК-дню и рисуется меткой в ячейке даты:
      • `price` / `spp` — цену подвинули мы или ВБ своей скидкой;
      • `budget` — деньги кончились, и день ТАК И закончился на нуле;
      • `gap`    — деньги кончились, но их долили: кампания стояла кусок дня;
      • `idle`   — показов практически нет: кампания простояла день целиком.
    Смена цены берётся из той же средней цены, что уже в строках (отдельного источника
    нет); остальное — из журнала событий кампании.
    """
    events = _price_change_events(price_by_date)

    # Окно событий берём с запасом в сутки по краям: created_at — UTC, а дни — МСК
    win_from = datetime.combine(period_from, datetime.min.time()) - timedelta(days=1)
    win_to = datetime.combine(period_to, datetime.min.time()) + timedelta(days=2)
    ev_rows = (
        await db.execute(
            select(WbAdCampaignEvent)
            .where(
                WbAdCampaignEvent.project_id == project_id,
                WbAdCampaignEvent.campaign_id == campaign_id,
                WbAdCampaignEvent.event_type == "budget_change",
                WbAdCampaignEvent.created_at >= win_from,
                WbAdCampaignEvent.created_at <= win_to,
            )
            .order_by(WbAdCampaignEvent.created_at)
            .limit(20000)
        )
    ).scalars().all()
    budget_ev = list(ev_rows)

    runout = {
        d: t for d, t in _runout_by_day(budget_ev).get(campaign_id, {}).items()
        if period_from <= d <= period_to
    }
    for d, stop_utc in runout.items():
        msk = pytz.UTC.localize(stop_utc).astimezone(MSK)
        events.append({
            "date": d.isoformat(), "kind": "budget",
            # Час идёт прямо в метку дня, а не в подсказку — так колонку дат можно
            # просматривать сверху вниз и видеть, во сколько кампания вставала.
            # dir=0 — у часа остановки нет направления, красить нечего
            "short": "стоп", "value": f"{msk:%H:%M}", "dir": 0,
            "text": f"бюджет кончился в {msk:%H:%M} — день неполный",
        })

    for d, (zero_utc, back_utc) in _intraday_budget_gaps(budget_ev, set(runout)).items():
        if not (period_from <= d <= period_to):
            continue
        z = pytz.UTC.localize(zero_utc).astimezone(MSK)
        b = pytz.UTC.localize(back_utc).astimezone(MSK)
        span = _fmt_span((back_utc - zero_utc).total_seconds() / 60)
        events.append({
            "date": d.isoformat(), "kind": "gap",
            "short": "простой", "value": span, "dir": 0,
            "text": f"бюджет кончился в {z:%H:%M}, долили в {b:%H:%M} — кампания стояла {span}",
        })

    marked = set(runout) | {date.fromisoformat(e["date"]) for e in events if e["kind"] == "gap"}
    for d in sorted(_idle_days(views_by_date, marked), reverse=True):
        events.append({
            "date": d.isoformat(), "kind": "idle",
            "short": "простой", "value": "весь день", "dir": 0,
            "text": "показов почти нет — кампания в этот день не крутилась",
        })

    # Порядок меток в строке: наше решение → решение ВБ → следствия
    order = {"price": 0, "spp": 1, "budget": 2, "gap": 3, "idle": 4}
    events.sort(key=lambda e: (e["date"], -order.get(e["kind"], 9)), reverse=True)
    return events


def _metric_row(
    label: str, views: int, clicks: int, spend: float,
    opens: int, carts: int, orders: int, orders_sum: float, price: float | None,
    customer_price: float | None = None, spp: float | None = None, is_partial: bool = False,
) -> dict:
    """Сводит сырые агрегаты дня/периода в строку метрик (РК + воронка).

    customer_price — «Цена Клиенту»: цена товара с учётом СПП (avg_price × (1 − spp_rate)),
    т.е. сколько реально платил покупатель в тот день. Нужна, чтобы видеть, как цена влияла
    на рекламу. None → 0 (нет данных по СПП за день).
    """
    return {
        "date": label,
        # Статистика по РК
        "views": int(views),
        "clicks": int(clicks),
        "ctr": round(clicks / views * 100, 2) if views else 0.0,
        "cpc": round(spend / clicks, 2) if clicks else 0.0,
        "spend": round(spend, 2),
        # Воронка продаж
        "open_card": int(opens),
        "add_to_cart": int(carts),
        "cr1": round(carts / opens * 100, 2) if opens else 0.0,
        "orders": int(orders),
        "cr2": round(orders / carts * 100, 2) if carts else 0.0,
        "orders_sum": round(orders_sum, 2),
        "cpl": round(spend / carts, 2) if carts else None,  # стоимость 1 корзины
        "cpo": round(spend / orders, 2) if orders else None,  # стоимость 1 заказа
        "avg_price": round(price, 2) if price else 0.0,
        # None (а не 0) — «данных ещё нет»: отчёт «Заказы» приходит с лагом
        "customer_price": round(customer_price, 2) if customer_price else None,
        "spp": None if spp is None else round(spp, 1),  # средний СПП за день, %
        "drr": round(spend / orders_sum * 100, 2) if orders_sum else 0.0,
        # День ещё идёт (сегодня по МСК): цифры неполные, сравнивать с прошлым нельзя
        "is_partial": is_partial,
    }


def _ad_metric_row(label: str, views: int, clicks: int, spend: float, atbs: int, orders: int) -> dict:
    """Строка чисто РК-метрик зоны показов (без воронки продаж).

    atbs/orders — корзины/заказы, АТРИБУТИРОВАННЫЕ рекламе (из WbAdNmDaily/WbAdSearchDaily),
    а не все продажи товара (те — в воронке по кампании). Числа не взаимозаменяемы.
    """
    return {
        "date": label,
        "views": int(views),
        "clicks": int(clicks),
        "ctr": round(clicks / views * 100, 2) if views else 0.0,
        "cpc": round(spend / clicks, 2) if clicks else 0.0,
        "cpm": round(spend / views * 1000, 2) if views else 0.0,  # цена 1000 показов зоны
        "spend": round(spend, 2),
        "atbs": int(atbs),  # корзины (реклама)
        "orders": int(orders),  # заказы (реклама)
        "cpo": round(spend / orders, 2) if orders else None,  # стоимость 1 заказа
    }


async def list_ad_article_catalog(db: AsyncSession, project_id: int) -> list[dict]:
    """Полный каталог артикулов проекта для каскадных фильтров рекламы:
    nm_id → артикул/предмет/бренд. Без фильтра активности и без топ-лимита —
    иначе в фильтре «Артикул» выпадают товары без свежих продаж/показов.
    """
    rows = (
        await db.execute(
            select(
                WbFunnelDaily.nm_id,
                func.max(WbFunnelDaily.vendor_code),
                func.max(WbFunnelDaily.subject),
                func.max(WbFunnelDaily.brand),
            )
            .where(WbFunnelDaily.project_id == project_id)
            .group_by(WbFunnelDaily.nm_id)
            .limit(50000)
        )
    ).all()
    return [
        {"nm_id": int(r[0]), "vendor_code": r[1] or str(r[0]), "subject": r[2] or "", "brand": r[3] or ""}
        for r in rows
        if r[0] is not None
    ]


async def _campaign_min_bids(db: AsyncSession, project_id: int, campaign_id: int) -> dict:
    """Минимальные (аукционный «пол») ставки ПО КАЖДОЙ ЗОНЕ — ЖИВАЯ проверка при заходе в РК.

    У WB пол разный на кампанию (категорию) И на зону, поэтому пробим каждую зону отдельно.
    НЕ кэшируем: аукционный пол динамический (меняется в течение дня) — запомненное значение
    протухнет, а к нему клампятся и рекомендации, и ставка зоны. Read-only пробник WB (ставку
    НЕ меняет) → {"search": ₽|None, "recommendations": ₽|None}. None у зоны — WB не отдал
    (зона выключена/не настроена/read-only токен). CPM-единая — пробим placement 'combined'
    и кладём результат в 'search' (единый бид рисуется одной карточкой «Поиск»).
    """
    from backend.services.funnel.wb_advertising_api import fetch_campaign_min_bid

    camp = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    out: dict = {"search": None, "recommendations": None}
    if camp is None:
        return out
    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return out
    nm_ids = [int(n) for n in (camp.nm_ids or [])]
    payment = (camp.campaign_type or "").lower()
    if payment == "cpm" and (camp.bid_mode or "") == "unified":
        out["search"] = await fetch_campaign_min_bid(api_key, campaign_id, nm_ids, "combined")
        return out
    out["search"] = await fetch_campaign_min_bid(api_key, campaign_id, nm_ids, "search")
    if payment != "cpc":  # у CPC зоны «Рекомендации» нет
        out["recommendations"] = await fetch_campaign_min_bid(api_key, campaign_id, nm_ids, "recommendations")
    return out


async def get_campaign_zones(
    db: AsyncSession, project_id: int, campaign_id: int,
    date_from: str | None = None, date_to: str | None = None, nm_id: int | None = None,
) -> dict:
    """Зоны показов кампании: что включено, по какой ставке и как ставка устроена.

    Правила WB (подтверждены на живых кампаниях):
    - `payment_type=cpm` + `bid_type=unified` — обе зоны всегда включены и крутятся
      одновременно, ставка одна на все зоны (отключить зону нельзя);
    - `payment_type=cpc` — только «Поиск»; ставка — цена за клик, единая для всех фраз
      (в bids_kopecks.recommendations приходит 0).

    Дневного лимита в API WB нет — в кабинете он есть, наружу не отдаётся.
    """
    from backend.services.funnel.wb_advertising_api import fetch_campaign_placements

    camp = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if camp is None:
        return {"error": "campaign_not_found"}

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"error": "no_api_key"}

    info = await fetch_campaign_placements(api_key, campaign_id)
    payment_type = (camp.campaign_type or "").lower()

    # У CPC зона одна («Поиск»), поэтому её статистика = вся РК-статистика кампании.
    # Берём её из wb_ad_nm_daily: там рекламные заказы, а не заказы воронки со всех источников.
    zone_stats = None
    if payment_type == "cpc" and date_from and date_to:
        from backend.services.funnel.cluster_analysis_service import zone_metrics

        conds = [
            WbAdNmDaily.project_id == project_id, WbAdNmDaily.campaign_id == campaign_id,
            WbAdNmDaily.date >= datetime.strptime(date_from, "%Y-%m-%d").date(),
            WbAdNmDaily.date <= datetime.strptime(date_to, "%Y-%m-%d").date(),
        ]
        if nm_id is not None:
            conds.append(WbAdNmDaily.nm_id == nm_id)
        row = (
            await db.execute(
                select(
                    func.coalesce(func.sum(WbAdNmDaily.views), 0),
                    func.coalesce(func.sum(WbAdNmDaily.clicks), 0),
                    func.coalesce(func.sum(WbAdNmDaily.spend), 0),
                    func.coalesce(func.sum(WbAdNmDaily.orders), 0),
                ).where(*conds)
            )
        ).one()
        zone_stats = {
            "search": zone_metrics(int(row[0]), int(row[1]), float(row[2]), int(row[3])),
            "recommendations": None,
            "derived": False,  # у CPC зона одна — это прямые данные, не вычитание
        }

    locked, lock_reason = _zones_lock(payment_type, camp.bid_mode, camp.status)
    min_bids = await _campaign_min_bids(db, project_id, campaign_id)  # пол по каждой зоне (справочно)
    return {
        "zone_stats": zone_stats,
        "campaign_id": campaign_id,
        "payment_type": payment_type,          # cpm | cpc
        "bid_mode": camp.bid_mode,             # unified | manual
        "placements": info["placements"],      # {"search": bool, "recommendations": bool}
        "bids": info["bids"],                  # ₽ по зонам
        "min_bids": min_bids,                  # аукционный «пол» ПО ЗОНАМ, ₽ (справочно, пробник WB)
        # Переключать зоны WB даёт только у CPM с ручной ставкой (статусы 4/9/11)
        "zones_locked": locked,
        "lock_reason": lock_reason,
        # Ставка задаётся сразу на все зоны (CPM-единая) или на все фразы (CPC)
        "single_bid": payment_type == "cpc" or (camp.bid_mode or "") == "unified",
    }


# Статусы, в которых WB разрешает менять зоны: 4 — готова, 9 — активна, 11 — пауза
ZONE_EDIT_STATUSES = (4, 9, 11)


def _zones_lock(payment_type: str, bid_mode: str | None, status: int | None) -> tuple[bool, str | None]:
    """Можно ли переключать зоны показов, и если нет — почему.

    WB (PUT /adv/v0/auction/placements) принимает только CPM с ручной ставкой
    в статусах 4/9/11. Возвращает (locked, lock_reason | None).
    """
    if payment_type == "cpc":
        return True, "CPC · показы только в поиске, зону выключить нельзя"
    if (bid_mode or "") != "manual":
        return True, "CPM · единая ставка: зоны работают одновременно, WB не даёт выключить"
    if status not in ZONE_EDIT_STATUSES:
        return True, "Зоны можно менять только у готовой, активной или приостановленной кампании"
    return False, None


async def set_campaign_zones(
    db: AsyncSession, project_id: int, campaign_id: int, placements: dict[str, bool],
) -> dict:
    """Включить/выключить зоны показов кампании (только CPM с ручной ставкой).

    `placements` — итоговое состояние обеих зон, напр. {"search": True, "recommendations": False}.
    WB заменяет набор целиком, поэтому передаём обе зоны, а не дельту.
    Возвращает {"ok": bool, "error": str | None, "placements": {...} | None}.
    """
    from backend.services.funnel.wb_advertising_api import set_campaign_placements

    camp = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if camp is None:
        return {"ok": False, "error": "Кампания не найдена", "placements": None}

    locked, reason = _zones_lock((camp.campaign_type or "").lower(), camp.bid_mode, camp.status)
    if locked:
        return {"ok": False, "error": reason, "placements": None}

    # Кампания без единой зоны показов нигде не крутится — WB такое молча принимает,
    # реклама встаёт. Требуем хотя бы одну включённую.
    if not any(placements.values()):
        return {"ok": False, "error": "Нельзя выключить обе зоны: реклама перестанет показываться", "placements": None}

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"ok": False, "error": "Не настроен API-ключ «Продвижение»", "placements": None}

    res = await set_campaign_placements(api_key, campaign_id, placements)
    if not res["ok"]:
        return {"ok": False, "error": res["error"], "placements": None}
    # Зоны у нас не хранятся — их читает fetch_campaign_placements напрямую из WB.
    return {"ok": True, "error": None, "placements": placements}


async def set_campaign_zone_bid(
    db: AsyncSession, project_id: int, campaign_id: int, zone: str, bid: float,
) -> dict:
    """Сменить ставку кампании для зоны (Поиск/Рекомендации) по всем её товарам.

    Единая ставка → placement 'combined' (одна на обе зоны), ручная → placement=зона,
    CPC → 'search'. WB (PATCH /api/advert/v1/bids) принимает статусы 4/9/11. Реальные деньги.
    Возвращает {"ok", "error", "bid"} (bid — применённая ставка ₽).
    """
    from backend.services.funnel.wb_advertising_api import set_campaign_bid

    if bid is None or bid <= 0:
        return {"ok": False, "error": "Ставка должна быть больше 0", "bid": None}

    camp = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if camp is None:
        return {"ok": False, "error": "Кампания не найдена", "bid": None}
    if camp.status not in ZONE_EDIT_STATUSES:
        return {"ok": False, "error": "Ставку можно менять только у готовой, активной или приостановленной кампании", "bid": None}
    nm_ids = [int(n) for n in (camp.nm_ids or [])]
    if not nm_ids:
        return {"ok": False, "error": "У кампании нет товаров", "bid": None}

    ptype = (camp.campaign_type or "").lower()
    if ptype == "cpc":
        placement = "search"  # CPC крутится только в поиске
    elif (camp.bid_mode or "unified") == "manual":
        placement = zone if zone in ("search", "recommendations") else "search"
    else:
        placement = "combined"  # единая ставка — одна на обе зоны

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"ok": False, "error": "Не настроен API-ключ «Продвижение»", "bid": None}

    return await set_campaign_bid(api_key, campaign_id, nm_ids, bid, placement)


async def set_campaign_default_bid(
    db: AsyncSession, project_id: int, campaign_id: int, bid: float,
) -> dict:
    """Сменить ставку кампании из СПИСКА (инлайн-правка) — по активной зоне.

    Ставит ставку на зону «Поиск» (для manual она приоритетная — как и в
    default_bid), 'combined' для единой, 'search' для CPC — ту же, что показана
    в колонке «Ставка». При успехе обновляет зеркало (wb_ad_campaigns.default_bid),
    чтобы список показывал новое значение и без ре-синка. Реальные деньги.
    Возвращает {"ok", "error", "bid", "min_bid"?} (min_bid — если ставка ниже
    аукционного минимума WB).
    """
    res = await set_campaign_zone_bid(db, project_id, campaign_id, "search", bid)
    if res.get("ok"):
        await db.execute(
            update(WbAdCampaign)
            .where(WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id)
            .values(default_bid=res["bid"])
        )
        await db.commit()
    return res


async def get_campaign_metrics(
    db: AsyncSession,
    project_id: int,
    campaign_id: int,
    date_from: str | None = None,
    date_to: str | None = None,
    days: int = 30,
    nm_id: int | None = None,
    zone: str | None = None,
    bdr_rates_map: BdrRatesLookup | None = None,
) -> dict:
    """Посуточные метрики кампании: статистика РК (показы/клики/CTR/CPC/затраты)
    + воронка её товаров (переходы/корзины/CR1/заказы/CR2/сумма/CPO/цена/ДРР).

    nm_id — если задан, И воронка, И РК-статистика считаются ТОЛЬКО по этому товару:
    разбивку РК по товарам даёт WB (/adv/v3/fullstats → nms), мы храним её в
    WbAdNmDaily. Флаг ответа `ad_by_nm` говорит, отфильтрована ли РК по товару —
    за даты до появления таблицы разбивки нет, и там РК-цифры будут нулевыми.

    Строки отсортированы по убыванию даты; сверху — строка-итог «За всё время».
    """
    camp = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id,
            )
        )
    ).scalar_one_or_none()
    if camp is None:
        return {"error": "campaign_not_found"}
    nm_ids = camp.nm_ids or []
    # Фильтр по одному товару. Не сверяем с camp.nm_ids: там текущий состав кампании,
    # а в статистике бывают товары, выведенные из неё позже (история остаётся за ними).
    if nm_id is not None:
        nm_ids = [nm_id]

    today_msk = msk_today()  # НЕ utcnow().date(): в 00:00–02:59 МСК это вчера
    period_to = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else today_msk
    period_from = (
        datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else period_to - timedelta(days=days - 1)
    )

    if nm_id is not None:
        # РК по одному товару — из разбивки кампания × товар (источник: WB fullstats → nms)
        ND = WbAdNmDaily
        ad_rows = (
            await db.execute(
                select(
                    ND.date,
                    func.coalesce(func.sum(ND.views), 0).label("views"),
                    func.coalesce(func.sum(ND.clicks), 0).label("clicks"),
                    func.coalesce(func.sum(ND.spend), Decimal("0")).label("spend"),
                )
                .where(
                    ND.project_id == project_id, ND.campaign_id == campaign_id, ND.nm_id == nm_id,
                    ND.date >= period_from, ND.date <= period_to,
                )
                .group_by(ND.date)
            )
        ).all()
    else:
        CD = WbAdCampaignDaily
        ad_rows = (
            await db.execute(
                select(
                    CD.date,
                    func.coalesce(func.sum(CD.views), 0).label("views"),
                    func.coalesce(func.sum(CD.clicks), 0).label("clicks"),
                    func.coalesce(func.sum(CD.spend), Decimal("0")).label("spend"),
                )
                .where(
                    CD.project_id == project_id, CD.campaign_id == campaign_id,
                    CD.date >= period_from, CD.date <= period_to,
                )
                .group_by(CD.date)
            )
        ).all()
    ad_by_date: dict[Any, Any] = {r.date: r for r in ad_rows}

    funnel_by_date: dict = {}
    if nm_ids:
        F = WbFunnelDaily
        f_rows = (
            await db.execute(
                select(
                    F.date,
                    func.coalesce(func.sum(F.open_card), 0).label("opens"),
                    func.coalesce(func.sum(F.add_to_cart), 0).label("carts"),
                    func.coalesce(func.sum(F.orders_count), 0).label("orders"),
                    func.coalesce(func.sum(F.orders_sum_rub), Decimal("0")).label("orders_sum"),
                    func.avg(func.nullif(F.avg_price, 0)).label("avg_price"),
                )
                .where(F.project_id == project_id, F.nm_id.in_(nm_ids), F.date >= period_from, F.date <= period_to)
                .group_by(F.date)
            )
        ).all()
        funnel_by_date = {r.date: r for r in f_rows}

    # Разбивка РК-части по зоне показов (только CPM). WB не даёт зоны по дням готовыми:
    # «Поиск» — из wb_ad_search_daily (сумма кластеров), «Рекомендации» — итог минус поиск.
    # Воронка по зонам НЕ разбивается — остаётся по кампании/товару (флаг zone_note).
    zone_applied = None
    if zone in ("search", "recommendations") and (camp.campaign_type or "").lower() == "cpm":
        from types import SimpleNamespace

        from backend.services.funnel.ad_search_stats import get_search_daily

        search_by_date = await get_search_daily(db, project_id, campaign_id, period_from, period_to, nm_id)
        if zone == "search":
            ad_by_date = {
                d: SimpleNamespace(views=v["views"], clicks=v["clicks"], spend=v["spend"])
                for d, v in search_by_date.items()
            }
        else:  # recommendations = итог кампании по дням минус поиск (зажим нулём)
            rec: dict = {}
            for d, a in ad_by_date.items():
                s = search_by_date.get(d, {"views": 0, "clicks": 0, "spend": 0.0})
                rec[d] = SimpleNamespace(
                    views=max(0, int(a.views) - s["views"]),
                    clicks=max(0, int(a.clicks) - s["clicks"]),
                    spend=max(0.0, float(a.spend) - s["spend"]),
                )
            ad_by_date = rec
        zone_applied = zone

    # Средний СПП кампании за день = среднее spp_rate по её товарам (из финотчёта, per (nm,дата)).
    # «Цена Клиенту» = цена дня × (1 − СПП). Нет карты СПП → 0 (customer_price = сама цена).
    def _spp_for_day(d: date) -> float | None:
        """Средний СПП кампании за день (доля) или None, если за день его ЕЩЁ НЕТ.

        None ≠ 0%: отчёт «Заказы» приходит с лагом, и у сегодняшнего дня СПП обычно
        неизвестен. Ноль здесь означал бы «ВБ не дал скидку» и подставлял в «Цену
        Клиенту» полную цену — то есть врал бы ровно в ту сторону, ради которой
        колонку и смотрят.
        """
        if not bdr_rates_map or not nm_ids:
            return None
        rates = [
            br.spp_rate for nm in nm_ids
            if (br := bdr_rates_map.get(int(nm), d)) is not None
        ]
        return sum(rates) / len(rates) if rates else None

    rows: list[dict] = []
    tot = {"views": 0, "clicks": 0, "spend": 0.0, "opens": 0, "carts": 0, "orders": 0,
           "orders_sum": 0.0, "price_sum": 0.0, "price_n": 0, "cust_sum": 0.0, "cust_n": 0,
           "spp_sum": 0.0, "spp_n": 0}
    all_dates = sorted(set(ad_by_date) | set(funnel_by_date), reverse=True)
    for d in all_dates:
        a = ad_by_date.get(d)
        f = funnel_by_date.get(d)
        views = int(a.views) if a else 0
        clicks = int(a.clicks) if a else 0
        spend = float(a.spend) if a else 0.0
        opens = int(f.opens) if f else 0
        carts = int(f.carts) if f else 0
        orders = int(f.orders) if f else 0
        orders_sum = float(f.orders_sum) if f else 0.0
        price = float(f.avg_price) if f and f.avg_price else None
        spp_frac = _spp_for_day(d)  # средний СПП кампании за день (доля) | None — нет данных
        customer_price = round(price * (1 - spp_frac), 2) if price and spp_frac is not None else None
        rows.append(_metric_row(d.isoformat(), views, clicks, spend, opens, carts, orders, orders_sum, price, customer_price, spp=None if spp_frac is None else spp_frac * 100, is_partial=d == today_msk))
        tot["views"] += views; tot["clicks"] += clicks; tot["spend"] += spend
        tot["opens"] += opens; tot["carts"] += carts; tot["orders"] += orders; tot["orders_sum"] += orders_sum
        if price:
            tot["price_sum"] += price; tot["price_n"] += 1
        if customer_price:
            tot["cust_sum"] += customer_price; tot["cust_n"] += 1
        if spp_frac is not None:
            tot["spp_sum"] += spp_frac * 100; tot["spp_n"] += 1

    totals = _metric_row(
        "За всё время", int(tot["views"]), int(tot["clicks"]), tot["spend"],
        int(tot["opens"]), int(tot["carts"]), int(tot["orders"]), tot["orders_sum"],
        tot["price_sum"] / tot["price_n"] if tot["price_n"] else None,
        tot["cust_sum"] / tot["cust_n"] if tot["cust_n"] else None,
        spp=tot["spp_sum"] / tot["spp_n"] if tot["spp_n"] else None,
    )

    from backend.services.funnel.cluster_analysis_service import TARGET_DRR  # локально: цикл импорта

    # Цена берётся из воронки товара и не зависит от зоны показов — событие о её смене
    # одинаково верно для «Всего»/«Поиск»/«Рекомендации».
    by_day = sorted(((date.fromisoformat(r["date"]), r) for r in rows), key=lambda p: p[0])
    events = await _campaign_day_events(
        db, project_id, campaign_id, period_from, period_to,
        [(d, r["avg_price"] or None, r["spp"]) for d, r in by_day],
        # Незакрытый день выкидываем: у него показов заведомо меньше, и он ложно попал бы в простой
        [(d, int(r["views"])) for d, r in by_day if not r["is_partial"]],
    )

    return {
        "campaign_id": campaign_id,
        "name": camp.name,
        "window": {"from": period_from.isoformat(), "to": period_to.isoformat()},
        "nm_id": nm_id,
        # РК-метрики отфильтрованы по товару (а не по всей кампании)
        "ad_by_nm": nm_id is not None,
        # Зона показов, по которой отфильтрована РК-часть (воронка остаётся по кампании)
        "zone": zone_applied,
        # Целевой ДРР — порог светофора в колонке ДРР (менеджер переопределяет на фронте)
        "target_drr": TARGET_DRR,
        "totals": totals,
        "rows": rows,
        # Изломы, объясняющие цифры: смена цены / остановка по бюджету / пауза
        "events": events,
    }


async def get_campaign_zone_metrics(
    db: AsyncSession,
    project_id: int,
    campaign_id: int,
    date_from: str | None = None,
    date_to: str | None = None,
    days: int = 30,
    zone: str = "total",
) -> dict:
    """Посуточные РК-метрики кампании в разрезе зоны показов: Всего / Поиск / Рекомендации.

    Только рекламная статистика (показы/клики/CTR/CPC/затраты + рекламные корзины/заказы/CPO):
    воронку продаж по зонам делить нельзя — WB не отдаёт её в разбивке по зонам.

    Источники: «Всего» — WbAdNmDaily (итог кампании по дням, из fullstats, там же рекламные
    корзины/заказы); «Поиск» — WbAdSearchDaily (сумма поисковых кластеров, /adv/v1/normquery/stats);
    «Рекомендации» = Всего − Поиск (по каждой метрике, зажим нулём; считается на чтении).
    Разбивка по зонам — только для CPM (у CPC поиск = вся кампания).

    Строки по убыванию даты; сверху — итог «За всё время».
    """
    camp = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id,
            )
        )
    ).scalar_one_or_none()
    if camp is None:
        return {"error": "campaign_not_found"}

    today_msk = msk_today()  # НЕ utcnow().date(): в 00:00–02:59 МСК это вчера
    period_to = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else today_msk
    period_from = (
        datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else period_to - timedelta(days=days - 1)
    )

    # «Всего» по кампании и дням — из разбивки по товарам (в ней есть рекламные корзины/заказы)
    ND = WbAdNmDaily
    tot_rows = (
        await db.execute(
            select(
                ND.date,
                func.coalesce(func.sum(ND.views), 0).label("views"),
                func.coalesce(func.sum(ND.clicks), 0).label("clicks"),
                func.coalesce(func.sum(ND.spend), Decimal("0")).label("spend"),
                func.coalesce(func.sum(ND.atbs), 0).label("atbs"),
                func.coalesce(func.sum(ND.orders), 0).label("orders"),
            )
            .where(
                ND.project_id == project_id, ND.campaign_id == campaign_id,
                ND.date >= period_from, ND.date <= period_to,
            )
            .group_by(ND.date)
        )
    ).all()
    total_by_date = {
        r.date: {
            "views": int(r.views), "clicks": int(r.clicks), "spend": float(r.spend),
            "atbs": int(r.atbs), "orders": int(r.orders),
        }
        for r in tot_rows
    }

    is_cpm = (camp.campaign_type or "").lower() == "cpm"
    zone_applied = "total"
    if zone in ("search", "recommendations") and is_cpm:
        from backend.services.funnel.ad_search_stats import get_search_daily

        search_by_date = await get_search_daily(db, project_id, campaign_id, period_from, period_to)
        if zone == "search":
            data_by_date = search_by_date  # {date: {views, clicks, spend, atbs, orders}}
        else:  # recommendations = Всего − Поиск (по каждой метрике, зажим нулём)
            data_by_date = {}
            for d, t in total_by_date.items():
                s = search_by_date.get(d, {"views": 0, "clicks": 0, "spend": 0.0, "atbs": 0, "orders": 0})
                data_by_date[d] = {
                    "views": max(0, t["views"] - s["views"]),
                    "clicks": max(0, t["clicks"] - s["clicks"]),
                    "spend": max(0.0, t["spend"] - s["spend"]),
                    "atbs": max(0, t["atbs"] - s["atbs"]),
                    "orders": max(0, t["orders"] - s["orders"]),
                }
        zone_applied = zone
    else:
        # Не CPM или зона не запрошена — отдаём «Всего» (зоны неприменимы)
        data_by_date = total_by_date

    tot = {"views": 0, "clicks": 0, "spend": 0.0, "atbs": 0, "orders": 0}
    rows: list[dict] = []
    for d in sorted(data_by_date, reverse=True):
        m = data_by_date[d]
        rows.append(_ad_metric_row(d.isoformat(), m["views"], m["clicks"], m["spend"], m["atbs"], m["orders"]))
        for k in tot:
            tot[k] += m[k]

    totals = _ad_metric_row("За всё время", int(tot["views"]), int(tot["clicks"]), tot["spend"], int(tot["atbs"]), int(tot["orders"]))

    return {
        "campaign_id": campaign_id,
        "name": camp.name,
        "zone": zone_applied,
        "window": {"from": period_from.isoformat(), "to": period_to.isoformat()},
        "totals": totals,
        "rows": rows,
    }


# ─── Пауза по расписанию (project_settings, без миграций) ───────────────────
#
# Деньгами рулит родное автопополнение ВБ (доливает в 00:00 МСК). ДДС управляет
# только показами: ставит кампанию на паузу в окне «плохих» часов (по умолчанию
# 00:00–09:00 МСК) и запускает обратно по его окончании — дневной бюджет не
# сгорает ночью на низкой конверсии. Возобновляем СТРОГО то, что глушили сами.

SCHEDULE_SETTINGS_KEY = "ads_schedule"
# Попыток pause/start на одно окно: ретраим ошибки WB следующими тиками, но не
# долбим API часами при постоянном отказе (например, read-only токен).
SCHEDULE_MAX_ATTEMPTS = 5


def _sanitize_schedule(entry: dict) -> dict:
    """Нормализация настройки расписания одной кампании: часы МСК в 0–23."""
    return {
        "enabled": bool(entry.get("enabled", False)),
        "pause_hour": min(23, max(0, int(entry.get("pause_hour", 0) or 0))),
        "resume_hour": min(23, max(0, int(entry.get("resume_hour", 9) or 0))),
    }


def _schedule_active(s: dict) -> bool:
    """Настройка реально работает: включена и окно ненулевое."""
    return bool(s.get("enabled")) and s.get("pause_hour") != s.get("resume_hour")


async def get_schedule_settings(db: AsyncSession, project_id: int) -> dict:
    """{campaign_id(str): {enabled, pause_hour, resume_hour}} из project_settings."""
    from backend.services.settings_service import get_setting

    raw = await get_setting(db, project_id, SCHEDULE_SETTINGS_KEY)
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        data = {}
    return {str(k): _sanitize_schedule(v) for k, v in data.items() if isinstance(v, dict)}


async def set_schedule_setting(db: AsyncSession, project_id: int, campaign_id: int, entry: dict) -> dict:
    """Обновить настройку одной кампании (merge в JSON-настройке проекта).

    Кампанию НЕ трогаем при сохранении: паузу/запуск делает только тик по
    расписанию (урок автопея — сейв не должен менять статус кампании).
    """
    from backend.services.settings_service import set_setting

    settings = await get_schedule_settings(db, project_id)
    settings[str(campaign_id)] = _sanitize_schedule(entry)
    # Выключенные не храним — не раздуваем JSON (окно часов дефолтное восстановимо)
    settings = {k: v for k, v in settings.items() if v["enabled"]}
    await set_setting(db, project_id, SCHEDULE_SETTINGS_KEY, json.dumps(settings, ensure_ascii=False))
    return settings


def _in_pause_window(hour: int, pause_hour: int, resume_hour: int) -> bool:
    """Час МСК внутри окна паузы; окно может переходить через полночь (22→08)."""
    if pause_hour < resume_hour:
        return pause_hour <= hour < resume_hour
    return hour >= pause_hour or hour < resume_hour


def _window_id(now_msk: datetime, pause_hour: int, resume_hour: int) -> str:
    """Идентификатор текущего окна = МСК-дата его старта (для идемпотентности журнала)."""
    if pause_hour < resume_hour or now_msk.hour >= pause_hour:
        return now_msk.date().isoformat()
    # окно через полночь, мы в его утренней части — стартовало вчера
    return (now_msk.date() - timedelta(days=1)).isoformat()


def compute_schedule_action(setting: dict, status: int | None, now_msk: datetime, journal: list[dict]) -> dict:
    """Чистое решение «что сделать с кампанией сейчас»: pause / start / skip.

    setting — {enabled, pause_hour, resume_hour}; status — наш WbAdCampaign.status;
    journal — записи журнала ЭТОЙ кампании, новые первыми.

    Внутри окна: пауза, если кампания активна и в это окно мы её ещё не глушили
    (успешная пауза в журнале). Ручной запуск ночью уважаем — повторно не глушим.
    Вне окна: запуск, если кампания на паузе и ПОСЛЕДНЯЯ успешная операция
    журнала — наша пауза (чужие/ручные паузы не трогаем). Пропущенное окно
    самолечится: незапущенная кампания поднимется первым же тиком вне окна.

    Возвращает {"action": "pause"|"start"|"skip", "window_id": str, "reason": str}.
    """
    if not _schedule_active(setting):
        return {"action": "skip", "window_id": "", "reason": "disabled"}
    pause_hour, resume_hour = int(setting["pause_hour"]), int(setting["resume_hour"])

    if _in_pause_window(now_msk.hour, pause_hour, resume_hour):
        wid = _window_id(now_msk, pause_hour, resume_hour)
        attempts = [e for e in journal if e.get("kind") == "pause" and e.get("window_id") == wid]
        if any(e.get("status") == "ok" for e in attempts):
            return {"action": "skip", "window_id": wid, "reason": "already_paused"}
        if len(attempts) >= SCHEDULE_MAX_ATTEMPTS:
            return {"action": "skip", "window_id": wid, "reason": "attempts_exhausted"}
        if status != CAMPAIGN_STATUS_ACTIVE:
            return {"action": "skip", "window_id": wid, "reason": "not_active"}
        return {"action": "pause", "window_id": wid, "reason": "ok"}

    if status != CAMPAIGN_STATUS_PAUSED:
        return {"action": "skip", "window_id": "", "reason": "not_paused"}
    last_ok = next((e for e in journal if e.get("status") == "ok" and e.get("kind") in ("pause", "start")), None)
    if not last_ok or last_ok.get("kind") != "pause":
        return {"action": "skip", "window_id": "", "reason": "not_ours"}
    wid = str(last_ok.get("window_id") or "")
    starts = [e for e in journal if e.get("kind") == "start" and e.get("window_id") == wid]
    if len(starts) >= SCHEDULE_MAX_ATTEMPTS:
        return {"action": "skip", "window_id": wid, "reason": "attempts_exhausted"}
    return {"action": "start", "window_id": wid, "reason": "ok"}


# ─── Автопополнение ВБ: детект по фактическим доливам ────────────────────────
# API ВБ статус своего автопополнения не отдаёт. Определяем по истории бюджета:
# рост значения в budget_change-событиях за окно = долив со стороны ВБ
# (автопей кабинета или ручной долив там же). Наши ручные пополнения через ДДС
# исключаются по журналу (совпадение кампании и времени).

WB_TOPUP_LOOKBACK_DAYS = 7
WB_TOPUP_EVENTS_CAP = 50000  # событий роста за окно на проект (страховка от раздувания)
WB_TOPUP_MANUAL_MATCH_SEC = 900  # долив в ±15 мин от нашего ручного депозита = это он и есть


def _wb_side_topups(events: Sequence[Any], manual_marks: list[tuple[int, datetime]]) -> dict[int, dict]:
    """{campaign_id: {last, last_amount, count}} — доливы бюджета со стороны ВБ.

    events — budget_change-события (created_at — naive UTC); долив = рост значения
    на ≥ BUDGET_TOPUP_MIN_DELTA (меньше — дрожание между синками, как в ledger).
    manual_marks — (campaign_id, ts UTC) успешных ручных пополнений через ДДС.
    """
    out: dict[int, dict] = {}
    for ev in events:
        old_v, new_v = _parse_num(ev.old_value), _parse_num(ev.new_value)
        if old_v is None or new_v is None:
            continue
        delta = new_v - old_v
        if delta < BUDGET_TOPUP_MIN_DELTA:
            continue
        if any(
            cid == ev.campaign_id and abs((ev.created_at - ts).total_seconds()) <= WB_TOPUP_MANUAL_MATCH_SEC
            for cid, ts in manual_marks
        ):
            continue
        rec = out.setdefault(ev.campaign_id, {"last": None, "last_amount": 0.0, "count": 0})
        rec["count"] += 1
        if rec["last"] is None or ev.created_at > rec["last"]:
            rec["last"] = ev.created_at
            rec["last_amount"] = round(delta, 2)
    return out


async def get_wb_autopay_status(db: AsyncSession, project_id: int, campaign_id: int | None = None) -> dict:
    """{campaign_id(str): {last_ts, last_amount, count}} — где ВБ доливал бюджет за окно.

    Кампании без записи = доливов со стороны ВБ не замечено (автопей ВБ выключен,
    либо кампания не тратила и долив не требовался, либо истории ещё нет).
    """
    since = utcnow() - timedelta(days=WB_TOPUP_LOOKBACK_DAYS)
    q = (
        select(WbAdCampaignEvent)
        .where(
            WbAdCampaignEvent.project_id == project_id,
            WbAdCampaignEvent.event_type == "budget_change",
            WbAdCampaignEvent.created_at >= since,
        )
    )
    if campaign_id is not None:
        q = q.where(WbAdCampaignEvent.campaign_id == campaign_id)
    events = (await db.execute(q.order_by(WbAdCampaignEvent.created_at).limit(WB_TOPUP_EVENTS_CAP))).scalars().all()

    marks: list[tuple[int, datetime]] = []
    for e in await get_autopay_log(db, project_id):
        if e.get("status") != "ok" or not e.get("campaign_id"):
            continue
        try:
            ts = datetime.fromisoformat(str(e.get("ts", "")))
        except (TypeError, ValueError):
            continue
        marks.append((int(e["campaign_id"]), ts.replace(tzinfo=None)))

    return {
        str(cid): {"last_ts": _iso_utc(rec["last"]), "last_amount": rec["last_amount"], "count": rec["count"]}
        for cid, rec in _wb_side_topups(events, marks).items()
    }


# ─── Пауза / запуск кампании (реальный вызов WB) ─────────────────────────────

CAMPAIGN_STATUS_READY = 4       # готова к запуску (создана, не запускалась)
CAMPAIGN_STATUS_COMPLETED = 7   # завершена (необратимо)
CAMPAIGN_STATUS_ACTIVE = 9
CAMPAIGN_STATUS_PAUSED = 11

# В каких статусах WB разрешает операцию (см. openapi promotion)
MANAGE_ALLOWED_STATUSES = {
    "stop": (4, 9, 11),
    "delete": (4,),
    "bids": (4, 9, 11),
    "nms": (4, 9, 11),
    "rename": (4, 7, 9, 11, -1),  # можно в любой момент
}


def _manage_guard(action: str, status: int | None) -> str | None:
    """Причина, по которой операцией нельзя, либо None если можно.

    Отбиваем у себя ДО похода в WB: у завершённой/в-процессе-удаления кампании
    большинство операций WB вернёт 400, а удаление доступно только «готовой».
    """
    allowed = MANAGE_ALLOWED_STATUSES.get(action)
    if allowed is None:
        return f"Неизвестная операция: {action}"
    if status in allowed:
        return None
    if action == "delete":
        return "Удалить можно только кампанию в статусе «Готова» (не запускавшуюся)"
    if action == "stop":
        return "Завершить можно только готовую, активную или приостановленную кампанию"
    return "Операция недоступна в текущем статусе кампании"


async def _get_advert_api_key(db: AsyncSession, project_id: int) -> str | None:
    """Ключ для рекламных вызовов — каскад wb_advert → wb_analytics → wb."""
    from backend.services.funnel.wb_api_client import get_wb_key

    return (
        await get_wb_key(db, project_id, "wb_advert")
        or await get_wb_key(db, project_id, "wb_analytics")
        or await get_wb_key(db, project_id, "wb")
    )


async def set_campaign_active(db: AsyncSession, project_id: int, campaign_id: int, active: bool) -> dict:
    """Запустить (active=True) или поставить на паузу (False) кампанию в WB.

    Дёргает WB start/pause; при успехе синхронно обновляет наш WbAdCampaign.status
    (чтобы UI не ждал часового синка). Возвращает {"ok", "status", "error"}.
    """
    from backend.services.funnel.wb_advertising_api import set_campaign_state

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"ok": False, "status": None, "error": "Нет WB-ключа с доступом к рекламе."}

    result = await set_campaign_state(api_key, campaign_id, "start" if active else "pause")
    if not result["ok"]:
        return {"ok": False, "status": None, "error": result.get("error")}

    new_status = CAMPAIGN_STATUS_ACTIVE if active else CAMPAIGN_STATUS_PAUSED
    row = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id,
                WbAdCampaign.campaign_id == campaign_id,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        row.status = new_status
        await db.commit()
    return {"ok": True, "status": new_status, "error": None}


async def _load_campaign(db: AsyncSession, project_id: int, campaign_id: int) -> WbAdCampaign | None:
    return (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id,
            ).limit(1)
        )
    ).scalar_one_or_none()


async def stop_campaign(db: AsyncSession, project_id: int, campaign_id: int) -> dict:
    """Завершить кампанию в WB (НЕОБРАТИМО: статус → 7). Только из 4/9/11."""
    from backend.services.funnel.wb_advertising_api import set_campaign_state

    camp = await _load_campaign(db, project_id, campaign_id)
    if camp is None:
        return {"ok": False, "error": "Кампания не найдена"}
    reason = _manage_guard("stop", camp.status)
    if reason:
        return {"ok": False, "error": reason}

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"ok": False, "error": "Нет WB-ключа с доступом к рекламе."}
    res = await set_campaign_state(api_key, campaign_id, "stop")
    if not res["ok"]:
        return {"ok": False, "error": res.get("error")}
    camp.status = CAMPAIGN_STATUS_COMPLETED
    await db.commit()
    return {"ok": True, "status": CAMPAIGN_STATUS_COMPLETED, "error": None}


async def rename_campaign(db: AsyncSession, project_id: int, campaign_id: int, name: str) -> dict:
    """Переименовать кампанию в WB (в любом статусе). При успехе обновляем наше имя."""
    from backend.services.funnel.wb_advertising_api import rename_campaign as wb_rename

    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "Название не может быть пустым"}
    if len(name) > 50:
        return {"ok": False, "error": "Название длиннее 50 символов"}
    camp = await _load_campaign(db, project_id, campaign_id)
    if camp is None:
        return {"ok": False, "error": "Кампания не найдена"}

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"ok": False, "error": "Нет WB-ключа с доступом к рекламе."}
    res = await wb_rename(api_key, campaign_id, name)
    if not res["ok"]:
        return {"ok": False, "error": res.get("error")}
    camp.name = name
    await db.commit()
    return {"ok": True, "name": name, "error": None}


async def delete_campaign(db: AsyncSession, project_id: int, campaign_id: int) -> dict:
    """Удалить кампанию в WB (только статус 4). После удаления WB держит её в -1."""
    from backend.services.funnel.wb_advertising_api import delete_campaign as wb_delete

    camp = await _load_campaign(db, project_id, campaign_id)
    if camp is None:
        return {"ok": False, "error": "Кампания не найдена"}
    reason = _manage_guard("delete", camp.status)
    if reason:
        return {"ok": False, "error": reason}

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"ok": False, "error": "Нет WB-ключа с доступом к рекламе."}
    res = await wb_delete(api_key, campaign_id)
    if not res["ok"]:
        return {"ok": False, "error": res.get("error")}
    # WB переводит в -1 (в процессе удаления); полное удаление — позже. Пометим у себя.
    camp.status = -1
    await db.commit()
    return {"ok": True, "error": None}


async def change_campaign_nms(
    db: AsyncSession, project_id: int, campaign_id: int, add: list[int], delete: list[int]
) -> dict:
    """Добавить/убрать товары кампании в WB (статусы 4/9/11)."""
    from backend.services.funnel.wb_advertising_api import change_campaign_nms as wb_change

    add = [int(x) for x in (add or [])]
    delete = [int(x) for x in (delete or [])]
    if not add and not delete:
        return {"ok": False, "error": "Не указаны товары для добавления или удаления"}
    camp = await _load_campaign(db, project_id, campaign_id)
    if camp is None:
        return {"ok": False, "error": "Кампания не найдена"}
    reason = _manage_guard("nms", camp.status)
    if reason:
        return {"ok": False, "error": reason}

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"ok": False, "error": "Нет WB-ключа с доступом к рекламе."}
    res = await wb_change(api_key, campaign_id, add, delete)
    if not res["ok"]:
        return {"ok": False, "error": res.get("error")}
    return {"ok": True, "error": None}


async def set_card_bids(
    db: AsyncSession, project_id: int, campaign_id: int, bids: list[dict]
) -> dict:
    """Ставки карточек товаров (статусы 4/9/11).

    bids — [{"nm_id": int, "bid_rub": float, "placement": "search"|"recommendations"}].
    Ставку в рублях переводим в копейки для WB.
    """
    from backend.services.funnel.wb_advertising_api import set_card_bids as wb_set_bids

    camp = await _load_campaign(db, project_id, campaign_id)
    if camp is None:
        return {"ok": False, "error": "Кампания не найдена"}
    reason = _manage_guard("bids", camp.status)
    if reason:
        return {"ok": False, "error": reason}

    wb_bids = []
    for b in bids or []:
        placement = b.get("placement", "search")
        if placement not in ("search", "recommendations"):
            return {"ok": False, "error": f"Недопустимая зона: {placement}"}
        bid_rub = float(b.get("bid_rub") or 0)
        if bid_rub <= 0:
            return {"ok": False, "error": "Ставка должна быть больше нуля"}
        wb_bids.append({
            "nm_id": int(b["nm_id"]),
            "bid_kopecks": round(bid_rub * 100),
            "placement": placement,
        })
    if not wb_bids:
        return {"ok": False, "error": "Не переданы ставки"}

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"ok": False, "error": "Нет WB-ключа с доступом к рекламе."}
    res = await wb_set_bids(api_key, campaign_id, wb_bids)
    if not res["ok"]:
        return {"ok": False, "error": res.get("error")}
    return {"ok": True, "error": None}


async def get_ad_subjects(db: AsyncSession, project_id: int) -> dict:
    """Предметы, по которым можно создать рекламную кампанию."""
    from backend.services.funnel.wb_advertising_api import fetch_ad_subjects

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"error": "no_api_key", "subjects": []}
    subjects = await fetch_ad_subjects(api_key)
    return {"subjects": subjects}


async def get_ad_nms(db: AsyncSession, project_id: int, subject_ids: list[int]) -> dict:
    """Карточки товаров для кампании по выбранным предметам."""
    from backend.services.funnel.wb_advertising_api import fetch_ad_nms

    if not subject_ids:
        return {"nms": []}
    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"error": "no_api_key", "nms": []}
    nms = await fetch_ad_nms(api_key, subject_ids)
    return {"nms": nms}


# Ограничения WB на создание кампании
CREATE_MAX_NMS = 50
CREATE_BID_TYPES = ("manual", "unified")
CREATE_PAYMENT_TYPES = ("cpm", "cpc")
CREATE_PLACEMENTS = ("search", "recommendations")


def validate_create_campaign(
    name: str, nms: list[int], bid_type: str, payment_type: str, placement_types: list[str] | None,
) -> str | None:
    """Проверка параметров создания ДО похода в WB. None — ок, иначе текст ошибки.

    Правила WB: ≤50 товаров; placement_types только для ручной ставки; CPC — показы
    только в поиске (зона одна). Единая ставка — зоны не выбираются, крутятся обе.
    """
    name = (name or "").strip()
    if not name:
        return "Укажите название кампании"
    if len(name) > 50:
        return "Название длиннее 50 символов"
    if not nms:
        return "Выберите хотя бы один товар"
    if len(nms) > CREATE_MAX_NMS:
        return f"Не больше {CREATE_MAX_NMS} товаров в кампании"
    if bid_type not in CREATE_BID_TYPES:
        return "Недопустимый тип ставки"
    if payment_type not in CREATE_PAYMENT_TYPES:
        return "Недопустимый тип оплаты"
    if placement_types:
        bad = [p for p in placement_types if p not in CREATE_PLACEMENTS]
        if bad:
            return f"Недопустимая зона показа: {', '.join(bad)}"
    # Ручной CPM крутится по выбранным зонам — без единой зоны кампания нигде не покажется
    if bid_type == "manual" and payment_type == "cpm" and not placement_types:
        return "Для ручной ставки выберите хотя бы одну зону показа"
    return None


async def create_campaign(
    db: AsyncSession, project_id: int, name: str, nms: list[int],
    bid_type: str = "manual", payment_type: str = "cpm", placement_types: list[str] | None = None,
) -> dict:
    """Создать рекламную кампанию в WB. Возвращает {"ok", "campaign_id", "error"}.

    После создания синкаем список кампаний, чтобы новая сразу появилась у нас.
    """
    from backend.services.funnel.wb_advertising_api import create_campaign as wb_create

    name = (name or "").strip()
    nms = [int(x) for x in (nms or [])]
    # Для единой ставки и CPC зоны не задаются вручную (WB решает сам)
    if bid_type != "manual":
        placement_types = None
    elif payment_type == "cpc":
        placement_types = ["search"]

    reason = validate_create_campaign(name, nms, bid_type, payment_type, placement_types)
    if reason:
        return {"ok": False, "campaign_id": None, "error": reason}

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"ok": False, "campaign_id": None, "error": "Нет WB-ключа с доступом к рекламе."}

    res = await wb_create(api_key, name, nms, bid_type, payment_type, placement_types)
    if not res["ok"]:
        return res
    # Подтянем ТОЛЬКО новую кампанию (3 узких запроса WB, ~1–2с). Полный
    # sync_ad_campaigns здесь отвечал минутами (1300+ budget-запросов с
    # 429-паузами) → таймаут прокси и дубли при повторном сабмите.
    try:
        from backend.services.funnel.ad_campaigns_service import refresh_one_campaign

        refreshed = await refresh_one_campaign(db, project_id, int(res["campaign_id"]))
        if not refreshed.get("ok"):
            # WB может отдать деталь новой кампании не мгновенно — одна повторная попытка
            await asyncio.sleep(2)
            await refresh_one_campaign(db, project_id, int(res["campaign_id"]))
    except Exception as e:
        logger.warning("create_campaign: точечный догруз после создания не удался: %s", e)
    return res


async def deposit_campaign_budget_manual(
    db: AsyncSession, project_id: int, campaign_id: int, amount: int, source: int
) -> dict:
    """Ручное пополнение бюджета кампании (реальные деньги). Инициируется пользователем.

    Пишет запись в тот же журнал, что и автопополнение; при успехе синхронно
    обновляет наш WbAdCampaign.budget новым значением из WB.
    """
    from backend.services.funnel.wb_advertising_api import deposit_campaign_budget

    if amount < 1000:
        return {"ok": False, "error": "Минимальная сумма пополнения — 1000 ₽ (ограничение WB)."}
    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"ok": False, "error": "Нет WB-ключа с доступом к рекламе."}

    row = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id,
            )
        )
    ).scalar_one_or_none()
    budget_before = float(row.budget) if row and row.budget is not None else None

    res = await deposit_campaign_budget(api_key, campaign_id, amount, source)
    budget_after = res.get("total")
    if res.get("ok") and row is not None and budget_after is not None:
        row.budget = Decimal(str(budget_after))
        await db.commit()

    await append_autopay_log(db, project_id, {
        "campaign_id": campaign_id,
        "ts": utcnow().isoformat(),
        "amount": amount if res.get("ok") else 0,
        "requested": amount,
        "source": "вручную",
        "status": res.get("status", "unknown"),
        "budget_before": budget_before,
        "budget_after": budget_after,
        "reason": res.get("error"),
    })
    return {"ok": bool(res.get("ok")), "status": res.get("status"), "budget_after": budget_after, "error": res.get("error")}


# ─── Журнал пополнений (ручные, реальные деньги) ─────────────────────────────

AUTOPAY_LOG_KEY = "ads_autopay_log"
AUTOPAY_LOG_CAP = 500  # храним последние N записей, чтобы не раздувать JSON


async def get_autopay_log(db: AsyncSession, project_id: int) -> list[dict]:
    """Журнал пополнений проекта (новые записи первыми)."""
    from backend.services.settings_service import get_setting

    raw = await get_setting(db, project_id, AUTOPAY_LOG_KEY)
    try:
        data = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        data = []
    return [e for e in data if isinstance(e, dict)]


async def append_autopay_log(db: AsyncSession, project_id: int, entry: dict) -> None:
    """Дописать запись в журнал (в начало) с обрезкой до AUTOPAY_LOG_CAP."""
    from backend.services.settings_service import set_setting

    log = await get_autopay_log(db, project_id)
    log.insert(0, entry)
    await set_setting(db, project_id, AUTOPAY_LOG_KEY, json.dumps(log[:AUTOPAY_LOG_CAP], ensure_ascii=False))


# ─── Пауза по расписанию: журнал и исполнение ────────────────────────────────

SCHEDULE_LOG_KEY = "ads_schedule_log"
SCHEDULE_LOG_CAP = 500  # храним последние N записей, чтобы не раздувать JSON


async def get_schedule_log(db: AsyncSession, project_id: int) -> list[dict]:
    """Журнал пауз/запусков по расписанию (новые записи первыми)."""
    from backend.services.settings_service import get_setting

    raw = await get_setting(db, project_id, SCHEDULE_LOG_KEY)
    try:
        data = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        data = []
    return [e for e in data if isinstance(e, dict)]


async def append_schedule_log(db: AsyncSession, project_id: int, entry: dict) -> None:
    """Дописать запись в журнал (в начало) с обрезкой до SCHEDULE_LOG_CAP."""
    from backend.services.settings_service import set_setting

    log = await get_schedule_log(db, project_id)
    log.insert(0, entry)
    await set_setting(db, project_id, SCHEDULE_LOG_KEY, json.dumps(log[:SCHEDULE_LOG_CAP], ensure_ascii=False))


async def get_schedule_log_for_ui(db: AsyncSession, project_id: int, campaign_id: int | None = None) -> list[dict]:
    """Журнал для фронта: фильтр по кампании + ts с UTC-смещением.

    В журнале ts — naive UTC (`utcnow().isoformat()`). Строку без зоны браузер
    трактует как ЛОКАЛЬНОЕ время и не конвертирует — история показывала UTC
    вместо МСК (пауза «22:16» при реальной 01:16 МСК). Отдаём ts как aware-ISO,
    формат дат на фронте сам переведёт в зону пользователя.
    """
    log = await get_schedule_log(db, project_id)
    if campaign_id is not None:
        log = [e for e in log if int(e.get("campaign_id") or 0) == campaign_id]
    out = []
    for e in log:
        try:
            dt = datetime.fromisoformat(str(e.get("ts", "")))
        except (TypeError, ValueError):
            out.append(e)
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        out.append({**e, "ts": dt.isoformat()})
    return out


async def run_ads_schedule_tick(db: AsyncSession, project_id: int, api_key: str) -> dict:
    """Один проход паузы по расписанию: решения + WB start/pause + журнал.

    Вызывается scheduler'ом каждые ~15 минут. Первый тик после полуночи (00:01)
    глушит кампании ПОСЛЕ штатного долива ВБ в 00:00 — долитый бюджет доживает
    до утреннего запуска. Статус берём из нашей таблицы: pause/start сами же
    синхронно её обновляют, а фоновый синк докатывает внешние изменения.
    """
    from backend.services.funnel.wb_advertising_api import set_campaign_state

    settings = await get_schedule_settings(db, project_id)
    enabled = {int(cid): s for cid, s in settings.items() if _schedule_active(s)}
    if not enabled:
        return {"paused": 0, "started": 0, "checked": 0}

    now_msk = msk_now()
    camps = {
        c.campaign_id: c
        for c in (
            await db.execute(
                select(WbAdCampaign).where(
                    WbAdCampaign.project_id == project_id,
                    WbAdCampaign.campaign_id.in_(list(enabled)),
                ).limit(len(enabled))
            )
        ).scalars().all()
    }
    log = await get_schedule_log(db, project_id)

    paused = started = 0
    for cid, setting in enabled.items():
        camp = camps.get(cid)
        journal = [e for e in log if int(e.get("campaign_id") or 0) == cid]
        decision = compute_schedule_action(setting, camp.status if camp else None, now_msk, journal)
        action = decision["action"]
        # camp is None → status None → decision всегда skip; проверка для mypy (union-attr ниже)
        if action == "skip" or camp is None:
            continue

        res = await set_campaign_state(api_key, cid, action)
        if res["ok"]:
            camp.status = CAMPAIGN_STATUS_PAUSED if action == "pause" else CAMPAIGN_STATUS_ACTIVE
            await db.commit()
            if action == "pause":
                paused += 1
            else:
                started += 1
        await append_schedule_log(db, project_id, {
            "campaign_id": cid,
            "ts": utcnow().isoformat(),
            "kind": action,
            "status": "ok" if res["ok"] else "error",
            "window_id": decision["window_id"],
            "reason": res.get("error"),
        })
    return {"paused": paused, "started": started, "checked": len(enabled)}


# ─── История бюджета: единая лента движения денег из данных, парсимых из WB ───

# ₽: рост бюджета меньше порога — это дрожание значения между синками, а не пополнение
BUDGET_TOPUP_MIN_DELTA = 50.0
LEDGER_CAP = 300  # сколько записей ленты отдаём (новые первыми)


def _iso_utc(dt: datetime | None) -> str | None:
    """datetime (naive = UTC по конвенции проекта) → ISO-строка с UTC-смещением для фронта."""
    from datetime import timezone

    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


async def get_budget_ledger(
    db: AsyncSession, project_id: int, campaign_id: int | None = None, kind: str | None = None
) -> list[dict]:
    """Лента движения бюджета из данных WB (новые первыми, обрезано до LEDGER_CAP):

    - `campaign_topup` — пополнение кампании: рост бюджета (`wb_ad_campaign_events`, delta ≥ порога),
      ловит любой источник (кабинет WB / авто / наш апп), т.к. WB видит итоговый бюджет;
    - `account_topup` — пополнение счёта кабинета (`wb_ad_payments`), уровень аккаунта — только в
      режиме «все кампании» (campaign_id=None), у пополнений счёта нет привязки к кампании;
    - `charge` — списание/расход (`wb_ad_upd`), сумма со знаком минус.

    campaign_id=None — по всему проекту, иначе по конкретной кампании.
    kind: None — всё; "topup" — только пополнения (+); "charge" — только списания (−).
    Каждый вид тянется своим лимитом LEDGER_CAP (вкладки не делят один лимит на двоих).
    """
    from sqlalchemy import Float, cast, desc, select

    from backend.models.integrations import WbAdCampaignEvent, WbAdPayment, WbAdUpd

    want_topup = kind in (None, "topup")
    want_charge = kind in (None, "charge")
    entries: list[dict] = []

    if want_topup:
        # Пополнения кампании — рост бюджета выше порога
        new_f = cast(WbAdCampaignEvent.new_value, Float)
        old_f = cast(WbAdCampaignEvent.old_value, Float)
        ev_q = select(WbAdCampaignEvent).where(
            WbAdCampaignEvent.project_id == project_id,
            WbAdCampaignEvent.event_type == "budget_change",
            new_f > old_f + BUDGET_TOPUP_MIN_DELTA,
        )
        if campaign_id is not None:
            ev_q = ev_q.where(WbAdCampaignEvent.campaign_id == campaign_id)
        ev_q = ev_q.order_by(desc(WbAdCampaignEvent.created_at)).limit(LEDGER_CAP)
        for e in (await db.execute(ev_q)).scalars().all():
            old_v, new_v = float(e.old_value or 0), float(e.new_value or 0)
            entries.append({
                "ts": _iso_utc(e.created_at), "kind": "campaign_topup",
                "amount": round(new_v - old_v, 2), "campaign_id": e.campaign_id,
                "source": "бюджет кампании", "note": f"{old_v:.0f} → {new_v:.0f} ₽",
            })

        # Пополнения счёта кабинета — только в режиме «все кампании» (нет привязки к кампании)
        if campaign_id is None:
            pay_q = (
                select(WbAdPayment).where(WbAdPayment.project_id == project_id)
                .order_by(desc(WbAdPayment.paid_at)).limit(LEDGER_CAP)
            )
            for p in (await db.execute(pay_q)).scalars().all():
                note = f"статус {p.status_id}" if p.status_id is not None else None
                if p.card_status:
                    note = f"{note} · {p.card_status}" if note else p.card_status
                entries.append({
                    "ts": _iso_utc(p.paid_at), "kind": "account_topup",
                    "amount": round(float(p.amount or 0), 2), "campaign_id": None,
                    "source": "счёт кабинета", "note": note,
                })

    if want_charge:
        # Списания / расход
        upd_q = select(WbAdUpd).where(WbAdUpd.project_id == project_id)
        if campaign_id is not None:
            upd_q = upd_q.where(WbAdUpd.advert_id == campaign_id)
        upd_q = upd_q.order_by(desc(WbAdUpd.upd_time)).limit(LEDGER_CAP)
        for u in (await db.execute(upd_q)).scalars().all():
            entries.append({
                "ts": _iso_utc(u.upd_time), "kind": "charge",
                "amount": -round(float(u.upd_sum or 0), 2), "campaign_id": u.advert_id,
                "source": u.payment_type or "—", "note": (f"док. {u.upd_num}" if u.upd_num else None),
            })

    entries.sort(key=lambda x: x["ts"] or "", reverse=True)
    return entries[:LEDGER_CAP]


# ─── Нехватка бюджета ────────────────────────────────────────────────────────


def compute_budget_gap(spend_today: float, ran_out_hour: float | None, now_hour: float) -> dict:
    """Чистый расчёт для строки «нехватка бюджета».

    spend_today — потрачено сегодня ₽; ran_out_hour — час МСК (дробный), когда
    бюджет кончился (None = неизвестно, кончился до первого синка);
    now_hour — текущий час МСК (для скорости, если ран-аут неизвестен).

    burn_rate: ₽/час за время работы (с 00:00 до остановки);
    needed_till_midnight: сколько долить, чтобы при той же скорости хватило
    до 00:00. WB не принимает пополнение меньше MIN_TOPUP_RUB (1000 ₽): если
    расчётная нужда >0, но меньше минимума — показываем минимум (иначе долить
    физически нельзя). raw_needed — расчётная нужда без учёта минимума.
    """
    effective_stop = ran_out_hour if ran_out_hour is not None else min(now_hour, 24.0)
    hours_active = max(effective_stop, 0.25)  # защита от деления на ноль
    burn_rate = spend_today / hours_active if spend_today > 0 else 0.0
    remaining_hours = max(24.0 - effective_stop, 0.0)
    raw_needed = burn_rate * remaining_hours
    needed = max(MIN_TOPUP_RUB, round(raw_needed)) if raw_needed > 0 else 0.0
    return {
        "burn_rate": round(burn_rate, 2),
        "needed_till_midnight": needed,
        "raw_needed": round(raw_needed, 2),
        "min_topup": MIN_TOPUP_RUB,
        "hours_active": round(hours_active, 2),
        "remaining_hours": round(remaining_hours, 2),
    }


def _parse_num(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _runout_by_day(ev_rows: Sequence[WbAdCampaignEvent]) -> dict[int, dict[date, datetime]]:
    """{campaign_id: {МСК-день: момент последнего budget_change→0}} за окно событий.

    Зеркало today-логики get_budget_gaps, но по каждому дню отдельно: в пределах дня
    последний переход бюджета в 0 выигрывает, а пополнение (>0) после него сбрасывает
    день из «остановленных» (день закончился с бюджетом). Ожидает ev_rows,
    отсортированные по created_at ASC; created_at — naive UTC.
    """
    out: dict[int, dict[date, datetime]] = {}
    for ev in ev_rows:
        new_v = _parse_num(ev.new_value)
        if new_v is None:
            continue
        d = pytz.UTC.localize(ev.created_at).astimezone(MSK).date()
        camp = out.setdefault(ev.campaign_id, {})
        if new_v <= 0:
            camp[d] = ev.created_at  # последний →0 за день выигрывает
        else:
            camp.pop(d, None)  # пополнение после → день не считается остановленным
    return out


def _stop_hour_msk(stop_utc: datetime | None) -> float | None:
    """Момент остановки (naive UTC) → дробный час МСК (0..24), None если None."""
    if stop_utc is None:
        return None
    m = pytz.UTC.localize(stop_utc).astimezone(MSK)
    return m.hour + m.minute / 60


def _extrapolate_full_day(spend: float, stop_hour: float | None) -> tuple[float, float]:
    """(потенциал полного дня, недобор) для одного дня — линейная экстраполяция до 24:00.

    Бюджет кончился в stop_hour (МСК, дробный час) → скорость расхода = spend/stop_hour,
    потенциал = скорость × 24 ч, недобор = потенциал − факт (сколько не хватило, чтобы
    крутить до полуночи). Если день не кончался (stop_hour None) — потенциал = факт,
    недобора нет. hours_active защищён снизу от сверх-ранних остановок / деления на ноль.
    """
    if spend <= 0 or stop_hour is None:
        return spend, 0.0
    hours_active = max(stop_hour, 0.25)
    potential = spend / hours_active * 24.0
    return potential, max(0.0, potential - spend)


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def _campaign_potential(
    day_spend: dict[date, float], day_stop_hour: dict[date, float], recent_days: int,
) -> tuple[float | None, int]:
    """Потенциал полного дня кампании (₽) = МЕДИАНА экстраполированных полных дней по
    последним `recent_days` дням, когда бюджет КОНЧИЛСЯ (есть момент остановки) — так
    оцениваем реальный спрос у кампании, что упирается в бюджет. Медиана устойчива к
    сверх-ранним остановкам (выбросам вверх).

    Фолбэк: если дней-остановок нет (кампания не упирается в бюджет) — средний расход по
    последним полным дням. Возвращает (потенциал | None, число усреднённых дней).
    """
    runout_dates = sorted((d for d in day_spend if d in day_stop_hour and day_spend[d] > 0), reverse=True)[:recent_days]
    if runout_dates:
        pots = [_extrapolate_full_day(day_spend[d], day_stop_hour[d])[0] for d in runout_dates]
        return _median(pots), len(pots)
    full_dates = sorted((d for d, s in day_spend.items() if s > 0 and d not in day_stop_hour), reverse=True)[:recent_days]
    if not full_dates:
        return None, 0
    vals = [day_spend[d] for d in full_dates]
    return sum(vals) / len(vals), len(vals)


def _chronic_stats(day_spend: dict[date, float], stop_hours: dict[date, float]) -> tuple[int, int, float | None]:
    """(дни-остановки, дни-с-расходом, типичный час остановки МСК) по окну (без сегодня).

    Дни-с-расходом — сколько дней кампания вообще крутилась; дни-остановки — из них
    те, где бюджет кончился до конца дня; типичный час — медиана часов остановки
    (устойчива к разбросу), None если остановок не было.
    """
    active_days = sum(1 for s in day_spend.values() if s > 0)
    runout_hours = [stop_hours[d] for d in stop_hours if day_spend.get(d, 0.0) > 0]
    return len(runout_hours), active_days, _median(runout_hours)


async def _budget_window_history(
    db: AsyncSession, project_id: int, ids: list[int], today_msk: date,
) -> dict[int, tuple[dict[date, float], dict[date, float]]]:
    """{campaign_id: (расход по дням ₽, час остановки по дням МСК)} за окно, БЕЗ сегодня.

    Один батч дневного расхода + один событий budget_change по всем `ids`. День считается
    «остановкой», если ПОСЛЕДНЕЕ событие бюджета за МСК-день ≤ 0 (пополнение после сбрасывает
    день — та же логика, что в _runout_by_day). Собираем «последнее событие дня» прямо в SQL
    (DISTINCT ON campaign+день, ORDER BY … created_at DESC) — иначе на большом аккаунте тянули
    бы сотни тысяч событий и упирались в лимит. База и для потенциала, и для «хроников».
    """
    if not ids:
        return {}
    win_start = today_msk - timedelta(days=BUDGET_GAP_WINDOW_DAYS)
    win_start_utc = MSK.localize(datetime.combine(win_start, datetime.min.time())).astimezone(pytz.UTC).replace(tzinfo=None)
    day_start_utc = MSK.localize(datetime.combine(today_msk, datetime.min.time())).astimezone(pytz.UTC).replace(tzinfo=None)

    CD = WbAdCampaignDaily
    win_rows = (
        await db.execute(
            select(CD.campaign_id, CD.date, CD.spend)
            .where(CD.project_id == project_id, CD.campaign_id.in_(ids), CD.date >= win_start, CD.date < today_msk)
            .limit(200000)
        )
    ).all()
    daily_by_c: dict[int, dict[date, float]] = {}
    for r in win_rows:
        daily_by_c.setdefault(r.campaign_id, {})[r.date] = float(r.spend or 0)

    # created_at — naive UTC; переводим в МСК-дату средствами БД для DISTINCT ON по дню
    E = WbAdCampaignEvent
    msk_day = cast(func.timezone("Europe/Moscow", func.timezone("UTC", E.created_at)), Date)
    last_ev = (
        await db.execute(
            select(E.campaign_id, msk_day.label("msk_day"), E.new_value, E.created_at)
            .where(
                E.project_id == project_id,
                E.campaign_id.in_(ids),
                E.event_type == "budget_change",
                E.created_at >= win_start_utc,
                E.created_at < day_start_utc,
            )
            .distinct(E.campaign_id, msk_day)
            .order_by(E.campaign_id, msk_day, E.created_at.desc())
            .limit(200000)
        )
    ).all()
    stop_by_c: dict[int, dict[date, float]] = {}
    for er in last_ev:
        v = _parse_num(er.new_value)
        if v is None or v > 0:
            continue  # день закончился с бюджетом → не остановка
        m = pytz.UTC.localize(er.created_at).astimezone(MSK)
        stop_by_c.setdefault(er.campaign_id, {})[er.msk_day] = m.hour + m.minute / 60

    return {cid: (daily_by_c.get(cid, {}), stop_by_c.get(cid, {})) for cid in ids}


async def get_budget_gaps(db: AsyncSession, project_id: int) -> list[dict]:
    """Кампании с нехваткой бюджета: фактически кончившиеся сегодня + «хронические».

    Факт (predicted=False): активная кампания (status=9), текущий бюджет 0, сегодня
    был расход. Час остановки — последнее событие budget_change → 0 за сегодня; если
    события нет (кончился между синками/до первого) — None.

    Прогноз (predicted=True): активная кампания с бюджетом >0, которая за окно упиралась
    в бюджет ≥ BUDGET_GAP_CHRONIC_MIN_DAYS дней И на ≥ BUDGET_GAP_CHRONIC_MIN_RATE дней
    с расходом. typical_stop_hour — медиана часа остановки (МСК). Так утром, ещё до
    фактической остановки, виден риск: «этот артикул обычно кончается ~15:00».
    """
    now_utc = utcnow()
    now_msk = now_utc.astimezone(MSK) if now_utc.tzinfo else MSK.localize(now_utc.replace(tzinfo=None), is_dst=None).astimezone(MSK)
    today_msk = now_msk.date()
    # Начало суток МСК в naive-UTC (события хранятся в UTC)
    day_start_utc = MSK.localize(datetime.combine(today_msk, datetime.min.time())).astimezone(pytz.UTC).replace(tzinfo=None)

    campaigns = (
        (
            await db.execute(
                select(WbAdCampaign).where(
                    WbAdCampaign.project_id == project_id,
                    WbAdCampaign.status == 9,
                ).limit(5000)
            )
        )
        .scalars()
        .all()
    )
    if not campaigns:
        return []
    # Кончившиеся (бюджет ≤ 0) — кандидаты в «факт»; живые (>0) — кандидаты в «прогноз».
    ran_campaigns = [c for c in campaigns if float(c.budget or 0) <= 0]
    alive_campaigns = [c for c in campaigns if float(c.budget or 0) > 0]
    ran_ids = [c.campaign_id for c in ran_campaigns]
    all_ids = [c.campaign_id for c in campaigns]

    CD = WbAdCampaignDaily
    spend_rows = (
        await db.execute(
            select(CD.campaign_id, func.coalesce(func.sum(CD.spend), Decimal("0")).label("spend"))
            .where(CD.project_id == project_id, CD.campaign_id.in_(all_ids), CD.date == today_msk)
            .group_by(CD.campaign_id)
        )
    ).all()
    spend_map = {r.campaign_id: float(r.spend) for r in spend_rows}

    # Последнее событие «бюджет → 0» за сегодня по кончившимся кампаниям
    ran_out_at: dict[int, datetime] = {}
    if ran_ids:
        ev_rows = (
            (
                await db.execute(
                    select(WbAdCampaignEvent)
                    .where(
                        WbAdCampaignEvent.project_id == project_id,
                        WbAdCampaignEvent.campaign_id.in_(ran_ids),
                        WbAdCampaignEvent.event_type == "budget_change",
                        WbAdCampaignEvent.created_at >= day_start_utc,
                    )
                    .order_by(WbAdCampaignEvent.created_at)
                    .limit(5000)
                )
            )
            .scalars()
            .all()
        )
        for ev in ev_rows:
            new_v = _parse_num(ev.new_value)
            if new_v is not None and new_v <= 0:
                ran_out_at[ev.campaign_id] = ev.created_at  # последний по времени выигрывает
            elif new_v is not None and new_v > 0:
                ran_out_at.pop(ev.campaign_id, None)  # после пополнения не считается остановленной

    # Факт-строки: бюджет кончился И сегодня был расход. Прогноз-кандидаты: живые кампании.
    actual_campaigns = [c for c in ran_campaigns if spend_map.get(c.campaign_id, 0.0) > 0]
    eligible_ids = [c.campaign_id for c in actual_campaigns]
    alive_ids = [c.campaign_id for c in alive_campaigns]

    # История окна (без сегодня): база для потенциала полного дня и «хронического» паттерна.
    # «Реальные возможности» = медиана экстраполированных дней-остановок (скорость × 24 ч).
    hist = await _budget_window_history(db, project_id, list(set(eligible_ids) | set(alive_ids)), today_msk)
    stats_map = {cid: _chronic_stats(ds, sh) for cid, (ds, sh) in hist.items()}
    potential_map: dict[int, float] = {}
    full_days_map: dict[int, int] = {}

    def _remember_potential(cid: int) -> None:
        ds, sh = hist.get(cid, ({}, {}))
        pot, fdays = _campaign_potential(ds, sh, BUDGET_GAP_POTENTIAL_DAYS)
        if pot is not None:
            potential_map[cid] = pot
            full_days_map[cid] = fdays

    for cid in eligible_ids:
        _remember_potential(cid)

    # Хронические (прогноз): живые кампании, что за окно регулярно упирались в бюджет
    chronic_ids: set[int] = set()
    for c in alive_campaigns:
        runout_days, active_days, _typical = stats_map.get(c.campaign_id, (0, 0, None))
        if runout_days >= BUDGET_GAP_CHRONIC_MIN_DAYS and active_days > 0 and runout_days / active_days >= BUDGET_GAP_CHRONIC_MIN_RATE:
            chronic_ids.add(c.campaign_id)
            _remember_potential(c.campaign_id)

    result_campaigns = actual_campaigns + [c for c in alive_campaigns if c.campaign_id in chronic_ids]
    if not result_campaigns:
        return []

    # Карта nm_id → бренд/категория (для фильтров бренд/категория на фронте) — только выдача
    all_nm_ids = {nm for c in result_campaigns for nm in (c.nm_ids or [])}
    nm_meta: dict[int, tuple] = {}
    if all_nm_ids:
        meta_rows = (
            await db.execute(
                select(
                    WbFunnelDaily.nm_id,
                    func.max(WbFunnelDaily.brand).label("brand"),
                    func.max(WbFunnelDaily.subject).label("subject"),
                )
                .where(WbFunnelDaily.project_id == project_id, WbFunnelDaily.nm_id.in_(all_nm_ids))
                .group_by(WbFunnelDaily.nm_id)
            )
        ).all()
        nm_meta = {r.nm_id: (r.brand, r.subject) for r in meta_rows}

    now_hour = now_msk.hour + now_msk.minute / 60
    result = []
    for c in result_campaigns:
        cid = c.campaign_id
        predicted = cid in chronic_ids
        spend_today = spend_map.get(cid, 0.0)
        # Прогноз ещё не остановился сегодня → часа остановки нет; у факта — из события
        stopped_utc = None if predicted else ran_out_at.get(cid)
        if stopped_utc is not None:
            stopped_msk = pytz.UTC.localize(stopped_utc).astimezone(MSK)
            ran_out_hour: float | None = stopped_msk.hour + stopped_msk.minute / 60
            ran_out_iso = stopped_msk.isoformat()
        else:
            ran_out_hour = None
            ran_out_iso = None
        gap = compute_budget_gap(spend_today, ran_out_hour, now_hour)
        # Недобор по «реальным возможностям»: потенциал полного дня − потрачено сегодня
        pot = potential_map.get(cid)
        raw_pot_needed = max(0.0, pot - spend_today) if pot is not None else 0.0
        needed_potential = max(MIN_TOPUP_RUB, round(raw_pot_needed)) if raw_pot_needed > 0 else 0.0
        runout_days, active_days, typical = stats_map.get(cid, (0, 0, None))
        result.append(
            {
                "campaign_id": cid,
                "name": c.name,
                "campaign_type": c.campaign_type,
                "nm_ids": c.nm_ids or [],
                "nm_count": len(c.nm_ids or []),
                "brands": sorted({nm_meta[nm][0] for nm in (c.nm_ids or []) if nm in nm_meta and nm_meta[nm][0]}),
                "subjects": sorted({nm_meta[nm][1] for nm in (c.nm_ids or []) if nm in nm_meta and nm_meta[nm][1]}),
                "spend_today": round(spend_today, 2),
                "ran_out_at": ran_out_iso,  # None = кончился до первого синка / прогноз (ещё не кончился)
                "predicted": predicted,  # True — риск по истории, кампания сегодня ещё крутится
                "typical_stop_hour": round(typical, 2) if typical is not None else None,  # медиана часа остановки, МСК
                "runout_days": runout_days,  # дней-остановок за окно
                "active_days": active_days,  # дней с расходом за окно
                "potential_daily": round(pot, 2) if pot is not None else None,  # ₽/день по полным дням (None — нет данных)
                "full_days": full_days_map.get(cid, 0),
                "window_days": BUDGET_GAP_WINDOW_DAYS,
                "needed_potential": needed_potential,  # долить до потенциала (с минимумом WB)
                "raw_needed_potential": round(raw_pot_needed, 2),
                **gap,
            }
        )
    # Факт-строки первыми (predicted=False < True), внутри — по расходу сегодня ↓
    result.sort(key=lambda r: (r["predicted"], -r["spend_today"]))
    return result


async def get_budget_gap_history(
    db: AsyncSession, project_id: int, campaign_id: int, days: int = BUDGET_GAP_WINDOW_DAYS,
) -> dict:
    """История «недобора бюджета» по дням (МСК) для одной кампании за `days` дней.

    По каждому дню: расход/показы/клики, кончился ли бюджет и во сколько (МСК),
    недобор ЗА ЭТОТ ДЕНЬ — линейная экстраполяция скорости расхода до 24:00
    (сколько не хватило, чтобы крутить до полуночи). Дни без остановки — недобора нет.
    potential_daily (заголовок) = медиана экстраполированных дней-остановок за окно,
    сегодня в базу не входит (неполный день). Дни отдаются от свежих к старым.
    """
    days = max(1, min(days, 180))
    today_msk = msk_now().date()
    win_start = today_msk - timedelta(days=days)
    win_start_utc = MSK.localize(datetime.combine(win_start, datetime.min.time())).astimezone(pytz.UTC).replace(tzinfo=None)

    CD = WbAdCampaignDaily
    rows = (
        await db.execute(
            select(CD.date, CD.spend, CD.views, CD.clicks)
            .where(CD.project_id == project_id, CD.campaign_id == campaign_id, CD.date >= win_start, CD.date <= today_msk)
            .order_by(CD.date)
            .limit(400)
        )
    ).all()
    daily = {
        r.date: {"spend": float(r.spend or 0), "views": int(r.views or 0), "clicks": int(r.clicks or 0)}
        for r in rows
    }

    ev = (
        (
            await db.execute(
                select(WbAdCampaignEvent)
                .where(
                    WbAdCampaignEvent.project_id == project_id,
                    WbAdCampaignEvent.campaign_id == campaign_id,
                    WbAdCampaignEvent.event_type == "budget_change",
                    WbAdCampaignEvent.created_at >= win_start_utc,
                )
                .order_by(WbAdCampaignEvent.created_at)
                .limit(20000)
            )
        )
        .scalars()
        .all()
    )
    runout = _runout_by_day(ev).get(campaign_id, {})  # МСК-день → момент остановки (naive UTC)
    stop_hours_raw = {d: _stop_hour_msk(dt) for d, dt in runout.items()}
    stop_hours: dict[date, float] = {d: h for d, h in stop_hours_raw.items() if h is not None}

    # Потенциал (заголовок) — по дням-остановкам за окно, БЕЗ сегодня (неполный день)
    base_spend = {d: v["spend"] for d, v in daily.items() if d != today_msk}
    base_stops = {d: h for d, h in stop_hours.items() if d != today_msk}
    potential, full_days = _campaign_potential(base_spend, base_stops, BUDGET_GAP_POTENTIAL_DAYS)

    out_days: list[dict] = []
    total_shortfall = 0.0
    days_ran_out = 0
    for d in sorted(daily.keys(), reverse=True):
        v = daily[d]
        stopped = runout.get(d)
        ran_out = stopped is not None and v["spend"] > 0
        if ran_out:
            days_ran_out += 1
        ran_out_iso = pytz.UTC.localize(stopped).astimezone(MSK).isoformat() if stopped is not None else None
        # Недобор дня = экстраполяция скорости расхода до 24:00 (для дней-остановок)
        _, shortfall = _extrapolate_full_day(v["spend"], stop_hours.get(d) if ran_out else None)
        total_shortfall += shortfall
        out_days.append(
            {
                "date": d.isoformat(),
                "spend": round(v["spend"], 2),
                "views": v["views"],
                "clicks": v["clicks"],
                "ran_out": ran_out,
                "ran_out_at": ran_out_iso,
                "shortfall": round(shortfall, 2),
            }
        )

    return {
        "campaign_id": campaign_id,
        "window_days": days,
        "potential_daily": round(potential, 2) if potential is not None else None,
        "full_days": full_days,
        "days_ran_out": days_ran_out,
        "total_shortfall": round(total_shortfall, 2),
        "days": out_days,
    }


async def _budget_gap_today_map(
    db: AsyncSession, project_id: int, campaigns: list, today_map: dict[int, float],
) -> dict[int, float]:
    """{campaign_id: ₽ недобора до полуночи} для кампаний, у которых сегодня (МСК)
    исчерпан бюджет (status=9, budget<=0, был расход). Момент остановки — последнее
    событие budget_change→0 за сегодня (как в get_budget_gaps); если события нет —
    считаем от текущего часа. Остальным кампаниям недобора нет (в карте отсутствуют).
    """
    ids = [
        c.campaign_id for c in campaigns
        if c.status == 9 and float(c.budget or 0) <= 0 and today_map.get(c.campaign_id, 0.0) > 0
    ]
    if not ids:
        return {}
    now_utc = utcnow()
    now_msk = now_utc.astimezone(MSK) if now_utc.tzinfo else MSK.localize(now_utc.replace(tzinfo=None), is_dst=None).astimezone(MSK)
    today_msk = now_msk.date()
    day_start_utc = MSK.localize(datetime.combine(today_msk, datetime.min.time())).astimezone(pytz.UTC).replace(tzinfo=None)
    ev_rows = (
        (
            await db.execute(
                select(WbAdCampaignEvent)
                .where(
                    WbAdCampaignEvent.project_id == project_id,
                    WbAdCampaignEvent.campaign_id.in_(ids),
                    WbAdCampaignEvent.event_type == "budget_change",
                    WbAdCampaignEvent.created_at >= day_start_utc,
                )
                .order_by(WbAdCampaignEvent.created_at)
                .limit(5000)
            )
        )
        .scalars()
        .all()
    )
    ran_out_at: dict[int, datetime] = {}
    for ev in ev_rows:
        new_v = _parse_num(ev.new_value)
        if new_v is not None and new_v <= 0:
            ran_out_at[ev.campaign_id] = ev.created_at  # последний по времени выигрывает
        elif new_v is not None and new_v > 0:
            ran_out_at.pop(ev.campaign_id, None)  # после пополнения не считается остановленной
    now_hour = now_msk.hour + now_msk.minute / 60
    out: dict[int, float] = {}
    for cid in ids:
        stopped = ran_out_at.get(cid)
        if stopped is not None:
            st = pytz.UTC.localize(stopped).astimezone(MSK)
            ran_out_hour: float | None = st.hour + st.minute / 60
        else:
            ran_out_hour = None
        out[cid] = compute_budget_gap(today_map[cid], ran_out_hour, now_hour)["needed_till_midnight"]
    return out


async def get_hourly_spend(
    db: AsyncSession, project_id: int, campaign_id: int, date: str | None = None,
) -> dict:
    """Почасовой расход кампании за день, восстановленный из снимков остатка бюджета.

    WB НЕ отдаёт показы/клики/заказы по часам — только суточно. Но остаток бюджета
    синкается каждые ~10 мин (WbAdCampaignEvent budget_change), и убывание остатка между
    снимками = потраченные деньги. Суммируем убывания по часу МСК (рост = пополнение —
    пропускаем). Точность — до интервала синка (~10 мин); события есть только пока кампания
    активна (status=9). Возвращает 24 часа (0..23) с расходом ₽ + итог.
    """
    camp = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id,
            )
        )
    ).scalar_one_or_none()
    if camp is None:
        return {"error": "campaign_not_found"}

    now_utc = utcnow()
    now_msk = now_utc.astimezone(MSK) if now_utc.tzinfo else MSK.localize(now_utc.replace(tzinfo=None), is_dst=None).astimezone(MSK)
    day = datetime.strptime(date, "%Y-%m-%d").date() if date else now_msk.date()
    day_start_utc = MSK.localize(datetime.combine(day, datetime.min.time())).astimezone(pytz.UTC).replace(tzinfo=None)
    day_end_utc = MSK.localize(datetime.combine(day, datetime.min.time()) + timedelta(days=1)).astimezone(pytz.UTC).replace(tzinfo=None)

    events = (
        (
            await db.execute(
                select(WbAdCampaignEvent)
                .where(
                    WbAdCampaignEvent.project_id == project_id,
                    WbAdCampaignEvent.campaign_id == campaign_id,
                    WbAdCampaignEvent.event_type == "budget_change",
                    WbAdCampaignEvent.created_at >= day_start_utc,
                    WbAdCampaignEvent.created_at < day_end_utc,
                )
                .order_by(WbAdCampaignEvent.created_at)
                .limit(5000)
            )
        )
        .scalars()
        .all()
    )
    hourly = [0.0] * 24
    for ev in events:
        old_v = _parse_num(ev.old_value)
        new_v = _parse_num(ev.new_value)
        if old_v is None or new_v is None:
            continue
        delta = old_v - new_v  # >0 = потрачено; <0 = пополнение (пропускаем)
        if delta > 0:
            hour = pytz.UTC.localize(ev.created_at).astimezone(MSK).hour
            hourly[hour] += delta

    rows = [{"hour": h, "spend": round(hourly[h], 2)} for h in range(24)]
    return {
        "campaign_id": campaign_id,
        "name": camp.name,
        "date": day.isoformat(),
        "total": round(sum(hourly), 2),
        "hours": rows,
    }


async def get_intraday_metrics(
    db: AsyncSession, project_id: int, campaign_id: int, date: str | None = None,
) -> dict:
    """Внутридневные показы/клики/расход по кампании из снимков накопительного счётчика.

    WB нативно почасовку показов/кликов НЕ отдаёт (мин. ось — сутки). Мы копим снимки
    кабинетного campaigns-stats каждые ~30 мин (WbAdCampaignSnapshot / snapshot_ad_intraday).
    Дельта между соседними снимками одного дня = метрики за интервал (стиль mkeeper,
    «место принятия решения»). Первый снимок дня = накопление от полуночи МСК.

    CTR и порог «мин показов» считает ФРОНТ (интерактивный контрол), поэтому отдаём сырые
    дельты. points[] упорядочены; time = ЧЧ:ММ МСК момента снимка (конец интервала).
    totals = последний накопительный счётчик (авторитетное число WB за день).
    """
    camp = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id,
            )
        )
    ).scalar_one_or_none()
    if camp is None:
        return {"error": "campaign_not_found"}

    now_msk = pytz.UTC.localize(utcnow()).astimezone(MSK)
    day = datetime.strptime(date, "%Y-%m-%d").date() if date else now_msk.date()

    snaps = (
        (
            await db.execute(
                select(WbAdCampaignSnapshot)
                .where(
                    WbAdCampaignSnapshot.project_id == project_id,
                    WbAdCampaignSnapshot.campaign_id == campaign_id,
                    WbAdCampaignSnapshot.stat_date == day,
                )
                .order_by(WbAdCampaignSnapshot.captured_at)
                .limit(500)
            )
        )
        .scalars()
        .all()
    )

    points = []
    prev_v = prev_c = 0
    prev_s = 0.0
    for snap in snaps:
        cum_v, cum_c = snap.views_cum or 0, snap.clicks_cum or 0
        cum_s = float(snap.spend_cum or 0)
        points.append(
            {
                "time": pytz.UTC.localize(snap.captured_at).astimezone(MSK).strftime("%H:%M"),
                "views": max(0, cum_v - prev_v),  # clamp: WB может скорректировать счётчик вниз
                "clicks": max(0, cum_c - prev_c),
                "spend": round(max(0.0, cum_s - prev_s), 2),
            }
        )
        prev_v, prev_c, prev_s = cum_v, cum_c, cum_s

    return {
        "campaign_id": campaign_id,
        "name": camp.name,
        "date": day.isoformat(),
        "points": points,
        "totals": {"views": prev_v, "clicks": prev_c, "spend": round(prev_s, 2)},
        "snapshots": len(snaps),
    }
