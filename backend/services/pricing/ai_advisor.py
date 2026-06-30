# ruff: noqa: RUF001, RUF002, RUF003
"""AI-агент рекомендаций по ценообразованию — анализ СКЛЕЙКА + ДИНАМИКА 3 дней.

Группирует портфель по склейкам (imt_id — варианты «цвет/размер» одной карточки
WB), подмешивает динамику крайних 3 дней (заказы/реклама/ДРР/цена/сток — тренд
vs предыдущие 3 дня) и просит Claude дать РАЗВЁРНУТЫЕ, по-деньгам приоритизи-
рованные рекомендации: (1) наценка внутри склейки; (2) эффективность рекламы на
склейку (якорь/донор, охват кампаний); (3) реакция на свежий моментум.
"""

import asyncio
import logging
from collections import defaultdict
from datetime import timedelta

import anthropic
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import WbAdCampaign, WbFunnelDaily
from backend.services.ai.llm_client import get_client
from backend.services.pricing.markup import get_markup_analytics
from backend.utils.time import utcnow

logger = logging.getLogger("dds.pricing")

MODEL = "claude-opus-4-8"  # самый сильный для стратегического анализа
_MAX_SKLEJKI = 20  # сколько склеек слать (топ по выручке)
_MAX_VARIANTS = 10  # вариантов на склейку
_MAX_SINGLES = 18  # одиночных артикулов (без склейки) для проблем/данных
_DYN_DAYS = 3  # окно динамики: крайние N дней vs предыдущие N

_SYSTEM = """Ты — старший аналитик юнит-экономики маркетплейса Wildberries.
Твоя задача — дать владельцу ГЛУБОКИЕ, развёрнутые, приоритизированные ПО ДЕНЬГАМ
рекомендации. Не отделывайся общими словами — копай в цифры каждой склейки.

КОНТЕКСТ ДАННЫХ:
- Склейка = карточка WB с вариантами (цвета/размеры, у каждого свой nm_id/«арт»).
  Варианты ДЕЛЯТ трафик и позицию одной карточки → цена и реклама одного влияют
  на всю склейку.
- У каждого варианта есть «динамика_3д»: крайние 3 дня vs предыдущие 3 дня
  (тренд_заказов%, ДРР_3д vs ДРР_пред3д, реклама_3д, тренд_цены%, дней_до_0,
  CR_3д). ЭТО ГЛАВНЫЙ СИГНАЛ — он показывает, что происходит ПРЯМО СЕЙЧАС, а не
  в среднем за период. Всегда сверяйся с динамикой, прежде чем советовать.

КАК ЧИТАТЬ ДИНАМИКУ (примеры выводов):
- заказы падают + реклама растёт + клики растут, но CR падает → реклама перестала
  конвертить (выжигает бюджет): не доливать, а резать ставки/проверять цену и
  карточку. Часто причина — цена выросла или ушла скидка.
- заказы падают сразу после роста цены (тренд_цены%>0) → спрос отреагировал,
  откатить цену частично.
- заказы растут при стабильной рекламе → реклама эффективна, можно масштабировать
  бюджет / поднять цену на неэластичных.
- дней_до_0 маленькое при хорошей марже → НЕ снижать цену (дефицит), пополнять;
  большое при слабых продажах → распродавать на ВБ.

ДВА РАЗБОРА (делай оба, по каждой значимой склейке):
A) НАЦЕНКА ВНУТРИ СКЛЕЙКИ. Сравнивай варианты между собой: где наценка/цена
   завышена и душит конверсию, где есть запас поднять. Решай ПО ВАРИАНТАМ.
B) РЕКЛАМА НА СКЛЕЙКУ. ДРР_склейки (Σреклама/Σвыручка) — честная эффективность,
   а НЕ ДРР отдельного варианта. Роли: «якорь» (несёт рекламу), «донор» (продаёт
   за счёт чужого трафика почти без своей рекламы — его «хороший» ДРР иллюзорен).
   Думай: рекламу льют в тот ли вариант? Размазать на все конвертящие или
   сконцентрировать? Кто продаёт, но «охвачено_кампаниями» его не включает —
   кандидат подключить.

ФОРМАТ КАЖДОЙ РЕКОМЕНДАЦИИ (обязательно все 5 шагов, с цифрами):
1. Состояние: ключевые числа (цена/себест/наценка/ДРР/остаток).
2. Динамика: что изменилось за 3 дня (с числами тренда).
3. Диагноз: ПОЧЕМУ так (причинно, опираясь на 1-2).
4. Действие: конкретно и с числовым таргетом (напр. «снизить цену 14736→~13500»,
   «срезать дневной бюджет рекламы вдвое», «подключить вариант X в кампанию»).
5. Эффект и контроль: ожидаемый ₽-эффект + что мониторить и через сколько дней.

Жёсткие правила (нарушишь — совет вредный):
- ПРОДАЖИ ТОЛЬКО НА WILDBERRIES. Канал один, вывозить некуда. Ликвидация =
  ТОЛЬКО распродажа на ВБ (скидка/акция/продвижение/карточка). НИКОГДА не
  предлагай вернуть поставщику/вывезти/продать на другой площадке.
- «новинка»=true → мало продаж из-за РАСКАЧКИ, не неликвид. Не распродавать;
  стратегия запуска: трафик, контент, конкурентная цена, отзывы.
- Очень высокий ДРР + низкая конверсия = реклама НЕ КОНВЕРТИТ → проблема в цене
  или карточке, а не «просто режь рекламу». Дай 2-3 рычага.
- «Залежавшийся остаток»/неликвид → распродать на ВБ скидкой, не поднимать цену.
- «Цена завышена / нет скидки» → выставить скидку, это не «кривая себестоимость».
- Остаток = ВБ + наш склад + сборка + в пути; капитал/дни — по всем локациям.
- Используй ТОЛЬКО присланные числа, ничего не выдумывай. Суммы — в рублях.

ФОРМАТ ОТВЕТА — простой HTML (без markdown, без <html>/<body>): заголовки <b>,
абзацы <p>, списки <ul><li>, вложенные списки допустимы. Структура:
1) <b>Картина портфеля</b> — 3-4 предложения: общее состояние + ГЛАВНЫЙ тренд
   последних 3 дней (что ускоряется/тормозит).
2) <b>Срочное (моментум 3 дней)</b> — что горит ИМЕННО сейчас по динамике
   (резкие падения заказов, всплески ДРР, дефицит): 3-5 пунктов по 5 шагов.
3) <b>Наценка по склейкам</b> — 4-6 пунктов: склейка → вариант → цена, 5 шагов.
4) <b>Реклама по склейкам</b> — 4-6 пунктов: переставить/размазать/добавить/
   урезать, по склейкам/вариантам, 5 шагов.
5) <b>Быстрые победы</b> — 3-5 коротких конкретных пунктов с ₽.
Будь конкретным и подробным, давай числовые таргеты. Пиши по-русски."""


def _compact_variant(r: dict) -> dict:
    return {
        "арт": r.get("vendor_code"),
        "размер": r.get("size") or None,
        "цена": r.get("current_price"),
        "цена_с_СПП": r.get("buyer_price"),
        "СПП%": r.get("spp_rate"),
        "себест": r.get("cost_price"),
        "коэф_наценки": r.get("markup_coef"),
        "наценка%": r.get("markup_pct"),
        "мин_цена": r.get("breakeven_price"),
        "мин_цена_с_рекламой": r.get("breakeven_with_adv"),
        "ДРР%": r.get("drr"),
        "CTR%": r.get("ctr"),
        "CPC": r.get("cpc"),
        "конверсия%": r.get("cr"),
        "доля_выручки_склейки%": r.get("rev_share_pct"),
        "доля_рекламы_склейки%": r.get("adv_share_pct"),
        "роль": r.get("sklejka_role") or None,
        "реклама₽": r.get("adv_sum"),
        "выручка₽": r.get("revenue"),
        "прибыль₽": r.get("profit"),
        "всего_остаток": r.get("total_stock"),
        "новинка": r.get("is_new"),
        "аномалия": r.get("anomaly"),
    }


def _compact_single(r: dict) -> dict:
    return {
        "арт": r.get("vendor_code"),
        "кат": r.get("category"),
        "цена": r.get("current_price"),
        "цена_с_СПП": r.get("buyer_price"),
        "себест": r.get("cost_price"),
        "наценка%": r.get("markup_pct"),
        "мин_цена_с_рекламой": r.get("breakeven_with_adv"),
        "ДРР%": r.get("drr"),
        "конверсия%": r.get("cr"),
        "выручка₽": r.get("revenue"),
        "прибыль₽": r.get("profit"),
        "всего_остаток": r.get("total_stock"),
        "заморожено₽": r.get("stock_value_cost"),
        "новинка": r.get("is_new"),
        "аномалия": r.get("anomaly"),
    }


async def _load_nm_to_campaigns(db: AsyncSession, project_id: int) -> dict[int, set[int]]:
    """nm_id → множество id АКТИВНЫХ кампаний (status=9), таргетящих этот товар."""
    rows = (
        await db.execute(
            select(WbAdCampaign.campaign_id, WbAdCampaign.nm_ids)
            .where(WbAdCampaign.project_id == project_id, WbAdCampaign.status == 9)
            .limit(5000)
        )
    ).all()
    nm_to_camp: dict[int, set[int]] = defaultdict(set)
    for r in rows:
        for nm in r.nm_ids or []:
            try:
                nm_to_camp[int(nm)].add(r.campaign_id)
            except (TypeError, ValueError):
                continue
    return nm_to_camp


def _pct_change(new: float, old: float) -> int | None:
    if old and old > 0:
        return round((new - old) / old * 100)
    return None


async def _load_recent_dynamics(
    db: AsyncSession, project_id: int, days: int = _DYN_DAYS
) -> tuple[dict[int, dict], dict | None]:
    """Динамика крайних N дней vs предыдущих N (по последней дате воронки).

    Возвращает (nm_id → метрики тренда, окно). Якорится на MAX(date) воронки —
    данные WB отстают на 1-2 дня, поэтому «сегодня» брать нельзя.
    """
    max_d = (
        await db.execute(select(func.max(WbFunnelDaily.date)).where(WbFunnelDaily.project_id == project_id))
    ).scalar()
    if not max_d:
        return {}, None
    recent_from = max_d - timedelta(days=days - 1)
    prior_from = max_d - timedelta(days=2 * days - 1)
    rows = (
        await db.execute(
            select(
                WbFunnelDaily.nm_id,
                WbFunnelDaily.date,
                WbFunnelDaily.orders_count,
                WbFunnelDaily.orders_sum_rub,
                WbFunnelDaily.adv_sum,
                WbFunnelDaily.adv_clicks,
                WbFunnelDaily.stocks_wb,
                WbFunnelDaily.avg_price,
            )
            .where(
                WbFunnelDaily.project_id == project_id,
                WbFunnelDaily.date >= prior_from,
                WbFunnelDaily.date <= max_d,
            )
            .limit(500000)
        )
    ).all()

    acc: dict[int, dict] = {}
    for r in rows:
        a = acc.setdefault(
            r.nm_id,
            {"r_ord": 0, "r_os": 0.0, "r_adv": 0.0, "r_clk": 0,
             "p_ord": 0, "p_os": 0.0, "p_adv": 0.0,
             "last_stock": 0, "last_date": None, "price_sum": 0.0, "price_n": 0},
        )
        if r.date >= recent_from:
            a["r_ord"] += r.orders_count or 0
            a["r_os"] += float(r.orders_sum_rub or 0)
            a["r_adv"] += float(r.adv_sum or 0)
            a["r_clk"] += r.adv_clicks or 0
            if r.avg_price:
                a["price_sum"] += float(r.avg_price)
                a["price_n"] += 1
        else:
            a["p_ord"] += r.orders_count or 0
            a["p_os"] += float(r.orders_sum_rub or 0)
            a["p_adv"] += float(r.adv_sum or 0)
        if a["last_date"] is None or r.date >= a["last_date"]:
            a["last_date"] = r.date
            a["last_stock"] = r.stocks_wb or 0

    out: dict[int, dict] = {}
    for nm, a in acc.items():
        days_to_0 = round(a["last_stock"] / (a["r_ord"] / days), 1) if a["r_ord"] > 0 else None
        out[nm] = {
            "заказы_3д": a["r_ord"],
            "заказы_пред3д": a["p_ord"],
            "тренд_заказов%": _pct_change(a["r_ord"], a["p_ord"]),
            "ДРР_3д%": round(a["r_adv"] / a["r_os"] * 100, 1) if a["r_os"] > 0 else None,
            "ДРР_пред3д%": round(a["p_adv"] / a["p_os"] * 100, 1) if a["p_os"] > 0 else None,
            "реклама_3д₽": round(a["r_adv"]),
            "цена_3д": round(a["price_sum"] / a["price_n"]) if a["price_n"] else None,
            "ост_ВБ": a["last_stock"],
            "дней_до_0": days_to_0,
            "CR_3д%": round(a["r_ord"] / a["r_clk"] * 100, 1) if a["r_clk"] > 0 else None,
        }
    window = {
        "свежие_3д": f"{recent_from.isoformat()}..{max_d.isoformat()}",
        "пред_3д": f"{prior_from.isoformat()}..{(recent_from - timedelta(days=1)).isoformat()}",
    }
    return out, window


def _build_sklejka_payload(
    groups: list[dict], nm_to_camp: dict[int, set[int]], dyn: dict[int, dict] | None = None
) -> list[dict]:
    """Топ многовариантных склеек по выручке + охват кампаниями + динамика 3д."""
    real = [g for g in groups if g.get("imt_id") is not None and g.get("articles", 0) >= 2]
    real.sort(key=lambda g: g.get("revenue") or 0, reverse=True)
    out: list[dict] = []
    for g in real[:_MAX_SKLEJKI]:
        children = g.get("children") or []
        camp_ids: set[int] = set()
        covered = 0
        variants = []
        for r in children[:_MAX_VARIANTS]:
            cs = nm_to_camp.get(r.get("nm_id"))
            if cs:
                covered += 1
                camp_ids |= cs
            v = _compact_variant(r)
            nm = r.get("nm_id")
            if dyn and nm is not None:
                v["динамика_3д"] = dyn.get(nm)
            variants.append(v)
        # охват считаем по всем вариантам склейки, не только показанным
        for r in children:
            cs = nm_to_camp.get(r.get("nm_id"))
            if cs:
                camp_ids |= cs
        covered_all = sum(1 for r in children if nm_to_camp.get(r.get("nm_id")))
        out.append(
            {
                "склейка": g.get("category"),
                "imt_id": g.get("imt_id"),
                "вариантов": g.get("articles"),
                "в_рекламе": g.get("advertised_variants"),
                "продаёт": g.get("converting_variants"),
                "охвачено_активными_кампаниями": covered_all,
                "активных_кампаний": len(camp_ids),
                "выручка₽": g.get("revenue"),
                "реклама₽": g.get("adv_sum"),
                "ДРР_склейки%": g.get("drr"),
                "прибыль₽": g.get("profit"),
                "наценка_портфель%": g.get("markup_pct"),
                "заморожено₽": g.get("stock_value_cost"),
                "варианты": variants,
            }
        )
    return out


def _build_singles_payload(groups: list[dict], dyn: dict[int, dict] | None = None) -> list[dict]:
    """Одиночные артикулы (без склейки / 1 вариант) — топ по проблемам/капиталу."""
    singles: list[dict] = []
    for g in groups:
        if g.get("imt_id") is None or g.get("articles", 0) < 2:
            singles.extend(g.get("children") or [])
    singles.sort(
        key=lambda r: ((r.get("stock_value_cost") or 0) + abs(min(r.get("profit") or 0, 0))),
        reverse=True,
    )
    picked = [r for r in singles if r.get("anomaly")][:_MAX_SINGLES]
    if len(picked) < _MAX_SINGLES:
        seen = {r.get("nm_id") for r in picked}
        for r in singles:
            if r.get("nm_id") in seen:
                continue
            picked.append(r)
            if len(picked) >= _MAX_SINGLES:
                break
    result = []
    for r in picked:
        c = _compact_single(r)
        nm = r.get("nm_id")
        if dyn and nm is not None:
            c["динамика_3д"] = dyn.get(nm)
        result.append(c)
    return result


async def get_ai_recommendations(
    db: AsyncSession,
    project_id: int,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    only_in_stock: bool = True,
) -> dict:
    """Сформировать AI-рекомендации по ценообразованию (склейки + динамика 3д)."""
    if not settings.ANTHROPIC_API_KEY or settings.ANTHROPIC_API_KEY.startswith("#"):
        return {"html": "<p>AI недоступен: не задан ANTHROPIC_API_KEY.</p>", "model": MODEL, "articles_analyzed": 0}

    data = await get_markup_analytics(
        db, project_id, date_from=date_from, date_to=date_to, only_in_stock=only_in_stock, group_by="imt"
    )
    groups = data["data_groups"]
    summary = data["summary"]
    nm_to_camp = await _load_nm_to_campaigns(db, project_id)
    dyn, dyn_window = await _load_recent_dynamics(db, project_id)

    sklejki = _build_sklejka_payload(groups, nm_to_camp, dyn)
    singles = _build_singles_payload(groups, dyn)
    n_real_sklejki = sum(1 for g in groups if g.get("imt_id") is not None and g.get("articles", 0) >= 2)

    import json

    payload = {
        "период": {"с": date_from, "по": date_to, "только_с_остатком_ВБ": only_in_stock},
        "окно_динамики": dyn_window,
        "сводка": summary,
        "всего_склеек_многовариантных": n_real_sklejki,
        "склейки": sklejki,
        "одиночные_артикулы": singles,
    }
    user = (
        "Данные портфеля по склейкам с динамикой крайних 3 дней + одиночные артикулы.\n"
        "Изучи ДЕТАЛЬНО, особенно «динамика_3д» каждого варианта (моментум сейчас), "
        "и дай развёрнутые рекомендации строго по структуре и 5-шаговому формату из "
        "системного промпта:\n\n"
        + json.dumps(payload, ensure_ascii=False, default=str)
    )

    # Прямой вызов клиента: Opus 4.8 не принимает temperature (deprecated),
    # а общий chat() его всегда шлёт. Лёгкий ретрай на транзиентных ошибках.
    client = get_client()
    msg = None
    for attempt in range(3):
        try:
            msg = await client.messages.create(
                model=MODEL,
                max_tokens=8000,
                system=_SYSTEM,
                messages=[{"role": "user", "content": user}],
            )
            break
        except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError) as exc:
            if attempt == 2:
                raise
            logger.warning("AI advisor retry %d: %s", attempt + 1, exc)
            await asyncio.sleep(2.0 * (2**attempt))
    assert msg is not None
    parts: list[str] = []
    for block in msg.content:
        if block.type == "text":
            parts.append(block.text)
    html = "".join(parts).strip()
    # снять возможные ```html-ограждения
    if html.startswith("```"):
        html = html.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    return {
        "html": html,
        "model": MODEL,
        "articles_analyzed": summary.get("total_articles", 0),
        "sklejki_sent": len(sklejki),
        "singles_sent": len(singles),
        "dynamics_window": dyn_window,
        "generated_at": utcnow().isoformat(),
    }
