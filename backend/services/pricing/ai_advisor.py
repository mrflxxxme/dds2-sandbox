# ruff: noqa: RUF001, RUF002, RUF003
"""AI-агент рекомендаций по ценообразованию.

Собирает компактную высокосигнальную сводку метрик (агрегаты + топ-артикулы по
денежному эффекту) и просит Claude дать приоритизированные рекомендации по
цене/рекламе/остаткам. Переиспользует общий LLM-клиент (services/ai/llm_client).
"""

import asyncio
import logging
from collections import Counter

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.services.ai.llm_client import get_client
from backend.services.pricing.markup import get_markup_analytics
from backend.utils.time import utcnow

logger = logging.getLogger("dds.pricing")

MODEL = "claude-opus-4-8"  # самый сильный для стратегического анализа
_MAX_ITEMS = 50

_SYSTEM = """Ты — старший аналитик юнит-экономики маркетплейса Wildberries.
По данным портфеля артикулов дай владельцу КОНКРЕТНЫЕ, приоритизированные ПО ДЕНЬГАМ
рекомендации: где поднять/снизить цену, где резать рекламу, что распродать/вывести.

Жёсткие правила (не нарушай — иначе совет вредный):
- ПРОДАЖИ ТОЛЬКО НА WILDBERRIES. У продавца НЕТ другого канала и некуда вывозить товар.
  Ликвидация неликвида = ТОЛЬКО распродажа на ВБ (скидка/акция/участие в акциях ВБ,
  продвижение, улучшение карточки). НИКОГДА не предлагай вернуть поставщику, вывезти со
  склада, продать на другой площадке/офлайн — этого варианта не существует.
- «Новинка» (поле новинка=true) → мало продаж из-за РАСКАЧКИ, а НЕ неликвид. Не предлагай
  распродажу/ликвидацию новинок. Для них — стратегия запуска: реклама на трафик, хорошие
  фото/контент карточки, конкурентная стартовая цена, сбор отзывов, мониторинг 2-4 недели.
- «Реклама в минус» (ДРР высокий, без рекламы был бы плюс) → РЕЗАТЬ/оптимизировать рекламу, НЕ поднимать цену.
- «Залежавшийся остаток» / неликвид (НЕ новинка, мало продаж, большой остаток) → распродать
  НА ВБ скидкой/акцией, НЕ поднимать цену.
- «Низкая наценка» или неэластичный спрос (эластичность ближе к 0) → можно поднять цену.
- «Подозрительная/нет себестоимости» → сперва проверить данные, без выводов по цене.
- Остаток = ВБ + наш склад + в сборке + в пути на ВБ (всего_остаток). Заморожённый капитал и
  дни до распродажи считаются по ВСЕМ локациям. Учитывай это: если на ВБ мало, но много в
  пути/сборке — пополнение НЕ нужно; если всего_остаток огромный при низких продажах — это
  затоваривание (распродавать на ВБ), даже если на самом ВБ остаток скромный.
- Приоритет — по денежному эффекту: замороженный капитал (по всем локациям), теряемая прибыль, потенциал роста.
- Используй ТОЛЬКО числа из присланных данных, ничего не выдумывай. Суммы — в рублях.

Формат ответа — простой HTML (без markdown, без <html>/<body>): заголовки через <b>,
абзацы <p>, списки <ul><li>. Структура:
1) <b>Картина портфеля</b> — 2-3 предложения диагностики.
2) <b>Приоритетные действия</b> — 3-6 пунктов, каждый: что сделать, по каким артикулам/категориям, ожидаемый ₽-эффект.
3) <b>Новинки</b> — если в данных есть новинки: как их раскачать/распродать на ВБ (2-4 пункта).
4) <b>Быстрые победы</b> — 2-4 коротких пункта.
Пиши кратко и по-деловому, по-русски."""


def _compact(r: dict) -> dict:
    return {
        "арт": r.get("vendor_code"),
        "кат": r.get("category"),
        "цена": r.get("current_price"),
        "себест": r.get("cost_price"),
        "наценка%": r.get("markup_pct"),
        "маржа%": r.get("margin_pct"),
        "ДРР%": r.get("drr"),
        "дней_до_исчерп": r.get("days_left"),
        "sell_through%": r.get("sell_through_pct"),
        "GMROI": r.get("gmroi"),
        "запас_прочн%": r.get("safety_margin_pct"),
        "эластичность": r.get("elasticity"),
        "остаток_ВБ": r.get("wb_stock"),
        "наш_склад": r.get("own_stock"),
        "в_сборке": r.get("assembly_stock"),
        "в_пути_ВБ": r.get("transit_stock"),
        "всего_остаток": r.get("total_stock"),
        "новинка": r.get("is_new"),
        "заморожено₽": r.get("stock_value_cost"),
        "выручка₽": r.get("revenue"),
        "прибыль₽": r.get("profit"),
        "реком_цена": r.get("optimal_price"),
        "аномалия": r.get("anomaly"),
    }


def _select_items(rows: list[dict]) -> list[dict]:
    """Отобрать топ-артикулы по денежному эффекту для каждого типа решения."""
    sel: dict[int, dict] = {}

    def top(key, n, filt=None):
        pool = [r for r in rows if (filt(r) if filt else True)]
        pool.sort(key=lambda r: (key(r) or 0), reverse=True)
        for r in pool[:n]:
            sel[r["nm_id"]] = r

    top(lambda r: r["adv_sum"], 12, lambda r: r["anomaly"] == "Реклама в минус")  # рекламные дыры
    top(lambda r: r["stock_value_cost"] or 0, 12, lambda r: r["anomaly"] == "Залежавшийся остаток")  # мёртвый капитал
    top(lambda r: r["stock_value_cost"] or 0, 10, lambda r: r.get("is_new") and r["wb_stock"] > 0)  # новинки в раскачке
    top(lambda r: -(r["profit"] or 0), 8, lambda r: r["anomaly"] == "Убыток после расходов ВБ")  # худшие убытки
    top(lambda r: r["revenue"], 10)  # топ выручки
    top(lambda r: r["profit"], 8)  # топ прибыли
    top(  # потенциал поднять цену
        lambda r: r["revenue"],
        10,
        lambda r: r["optimal_price"] and r["current_price"] and r["optimal_price"] > r["current_price"] * 1.1,
    )
    for r in rows:  # проблемы с данными
        if r["anomaly"] in ("Цена ниже себестоимости", "Подозрительная себестоимость", "Нет себестоимости"):
            sel.setdefault(r["nm_id"], r)

    return [_compact(r) for r in list(sel.values())[:_MAX_ITEMS]]


async def get_ai_recommendations(
    db: AsyncSession,
    project_id: int,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    only_in_stock: bool = True,
) -> dict:
    """Сформировать AI-рекомендации по ценообразованию для проекта."""
    if not settings.ANTHROPIC_API_KEY or settings.ANTHROPIC_API_KEY.startswith("#"):
        return {"html": "<p>AI недоступен: не задан ANTHROPIC_API_KEY.</p>", "model": MODEL, "articles_analyzed": 0}

    data = await get_markup_analytics(
        db, project_id, date_from=date_from, date_to=date_to, only_in_stock=only_in_stock, group_by="sku"
    )
    rows = data["data_rows"]
    summary = data["summary"]
    anomaly_counts = dict(Counter(r["anomaly"] for r in rows if r["anomaly"]))
    new_count = sum(1 for r in rows if r.get("is_new"))
    items = _select_items(rows)

    import json

    payload = {
        "период": {"с": date_from, "по": date_to, "только_с_остатком_ВБ": only_in_stock},
        "сводка": summary,
        "аномалии_по_типам": anomaly_counts,
        "новинок_в_портфеле": new_count,
        "ключевые_артикулы": items,
    }
    user = (
        "Данные портфеля (агрегаты + ключевые артикулы по денежному эффекту):\n\n"
        + json.dumps(payload, ensure_ascii=False, default=str)
        + "\n\nДай рекомендации строго по правилам из системного промпта."
    )

    # Прямой вызов клиента: Opus 4.8 не принимает temperature (deprecated),
    # а общий chat() его всегда шлёт. Лёгкий ретрай на транзиентных ошибках.
    client = get_client()
    msg = None
    for attempt in range(3):
        try:
            msg = await client.messages.create(
                model=MODEL,
                max_tokens=3000,
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
        "articles_analyzed": len(rows),
        "items_sent": len(items),
        "generated_at": utcnow().isoformat(),
    }
