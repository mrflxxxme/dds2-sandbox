# ruff: noqa: RUF001, RUF002, RUF003
"""
Service: штраф WB за невыполненный заказ FBS — ОЦЕНКА по правилам + ФАКТ из финотчёта.

Зачем два числа рядом. Оценка доступна сразу (задание отменилось — считаем), но
она приблизительная. Факт точен до копейки, но приходит с лагом: штраф садится в
`reportDetailByPeriod` примерно на пятый день после даты заказа, поэтому по
свежим отменам факта ещё нет, а по позавчерашним — уже есть. Показывать только
оценку — врать в цифре, только факт — показывать ноль там, где деньги уже
потеряны.

ПРАВИЛА WB (справка «Невыполненный заказ (отмена продавцом)»):
  1. Ставка штрафа зависит от рейтинга доставки продавца:
     • рейтинг < 95 %  — двойная комиссия, но не больше 50 % цены; ≤ 10 000 ₽;
     • 95 – 97 %       — двойная комиссия, но не больше 50 % цены; ≤ 3 000 ₽;
     • ≥ 97 %          — ОДИНАРНАЯ комиссия товара;                ≤ 3 000 ₽.
  2. Порог по обороту продавца («Ответственность продавца ограничена») —
     ФОРМУЛЫ в правилах нет, применить нечем → шаг пропущен, оценка сверху.
  3. Коэффициент срока отмены: ≤ 18 ч — 0,8; 18–72 ч — 1,0; > 72 ч — 1,4;
     авто-отмена — 1,6. 🔴 НЕ ПРИМЕНЯЕТСЯ: момента отмены у нас нет (см. ниже).
  Пол: 10 ₽ (отменил продавец) / 100 ₽ (отменилось автоматически).
  Потолок: 10 000 ₽ за единицу товара.

ПОЧЕМУ ОЦЕНКА — ВЕРХНЯЯ ГРАНИЦА, а не точное число:
  • WB считает от РОЗНИЧНОЙ цены (со скидкой продавца), а Marketplace API отдаёт
    в задании `price` ДО скидки — `salePrice` заполнен у меньшинства заданий.
    Сверка с фактом на проде: задание 2 993 ₽ → штраф 1 100 ₽ (36,7 % цены
    задания, но ровно 50 % розничной ≈ 2 200 ₽); 3 600 ₽ → 880 ₽ (24,4 %).
    То есть «50 % от цены задания» завышает результат в 1,3–2 раза.
  • Момент отмены неизвестен: WB его не отдаёт, а наш журнал переходов
    (`wb_fbs_order_events`) по отменённым заданиям пуст — синк видит их уже
    отменёнными. `updated_at` строки равен `synced_at`, то есть моменту
    прохода синка, а не отмены. Поэтому коэффициент срока = 1,0 (нейтральный
    разряд 18–72 ч), и авто-отмены (коэффициент 1,6) оценка занижает.
  • Рейтинг доставки нигде не хранится (WB не отдаёт его в API) — тир зашит
    константой `DELIVERY_RATING_TIER`.

ОТМЕНА ПОКУПАТЕЛЯ штрафуется только «из-за задержки продавца» (50 % розничной
цены не переданного в срок товара). Отличить такую отмену от обычного отказа
покупателя нечем: `wbStatus` у них общий. Проверка по факту — за всю историю
проекта в финотчёте НЕТ НИ ОДНОЙ строки такого штрафа (единственные типы —
«отмена продавцом» и «отправка товара отличного от заявленного»), поэтому по
корзине клиентских отмен считаем только потерянную выручку, а штраф не
выдумываем. Появится строка в отчётах — вылезет в `penalty_fact`.
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.wb_fbs import WbFbsOrder
from backend.models.wb_finance import WbFinanceRow
from backend.services.tariff_service import get_tariff_map

logger = logging.getLogger(__name__)

#: Тип удержания в `reportDetailByPeriod`, которым WB проводит этот штраф.
#: Матчим по вхождению, а не по равенству: формулировку WB меняет, корень —
#: «Невыполненный заказ (отмена продавцом)». Второй тип того же семейства,
#: «(отправка товара отличного от заявленного)», к отменам НЕ относится и в
#: сумму не идёт.
PENALTY_BONUS_TYPE_LIKE = "%Невыполненный заказ (отмена продавцом)%"

#: Тир рейтинга доставки кабинета. WB рейтинг в API не отдаёт — смотреть в
#: кабинете (Аналитика → Рейтинг продавца) и править здесь. На текущих ценах
#: отмен (1,5–3 тыс ₽) потолок тира не срабатывает вовсе: связывает только
#: ставку (двойная комиссия vs одинарная при рейтинге ≥ 97 %).
DELIVERY_RATING_TIER = "lt95"

#: Потолок суммы штрафа по тиру рейтинга.
_TIER_CAP: dict[str, Decimal] = {
    "lt95": Decimal("10000"),
    "95_97": Decimal("3000"),
    "gte97": Decimal("3000"),
}

#: Доля цены, выше которой штраф не поднимается (шаг 1 правил, «не больше 50 %»).
_RATE_CEILING = Decimal("0.5")

#: Жёсткий потолок за единицу товара (для автомобилей 50 000 ₽ — не наш случай).
_MAX_PER_UNIT = Decimal("10000")

#: Пол штрафа, когда заказ отменил продавец. Пол авто-отмены (100 ₽) не
#: применяем: отличить авто-отмену от нашей нечем (см. докстринг модуля).
_MIN_SELLER_CANCEL = Decimal("10")

#: Потолок строк, по которым считается оценка. Корзина отмен за разумный период
#: — сотни заданий; год истории мог бы вытянуть десятки тысяч в память.
#: Усечение НЕ молчаливое: `estimate_truncated` уезжает во фронт.
_ESTIMATE_ROW_LIMIT = 5000


def estimate_penalty(price: Decimal | None, commission_pct: float | Decimal | None) -> Decimal:
    """Штраф за ОДНО невыполненное задание по правилам WB (оценка сверху).

    `commission_pct` — ставка комиссии категории в ПРОЦЕНТАХ (как в `wb_tariffs`),
    `None` = ставки нет. Без ставки штраф не посчитать: возвращаем 0 и считаем
    такое задание отдельно (`no_commission_count`), иначе дефолтная ставка
    молча подмешала бы выдуманные рубли в итог.
    """
    if price is None or price <= 0 or commission_pct is None:
        return Decimal(0)

    rate = Decimal(str(commission_pct)) / Decimal(100)
    if rate <= 0:
        return Decimal(0)

    # Шаг 1: ставка штрафа. Двойная комиссия с потолком 50 % цены — кроме
    # рейтинга ≥ 97 %, где штраф равен ОДИНАРНОЙ комиссии товара.
    if DELIVERY_RATING_TIER == "gte97":
        penalty_rate = rate
    else:
        penalty_rate = min(rate * 2, _RATE_CEILING)

    # Шаг 2 (порог по обороту) пропущен — формулы в правилах нет.
    amount = price * penalty_rate

    # Потолки: тир рейтинга и жёсткий лимит за единицу товара.
    amount = min(amount, _TIER_CAP[DELIVERY_RATING_TIER], _MAX_PER_UNIT)

    # Шаг 3 (коэффициент срока) пропущен — момента отмены нет.
    return max(amount, _MIN_SELLER_CANCEL).quantize(Decimal("0.01"))


#: Дубль `orders_service.RUB_CURRENCY_CODE`: импортировать оттуда нельзя —
#: orders_service сам импортирует этот модуль (цикл).
RUB_CURRENCY_CODE = "643"


def _price_expr() -> Any:
    """Цена задания В РУБЛЯХ — зеркало `orders_service.revenue_rub_expr()`.

    WB торгует и в СНГ: `price`/`salePrice` приходят в валюте ПРОДАЖИ (тенге,
    сумы, белорусские рубли — заказ 5384434223 лежал как «60.10» BYN и
    показывался «60 ₽»). Рубль или пустой код → прежний канон
    `coalesce(sale_price, price)`; иная валюта → ТОЛЬКО `converted_price`
    (пересчёт WB); без него строка даёт NULL/0 — занизить лучше, чем сложить
    тенге с рублями.
    """
    is_rub = or_(
        WbFbsOrder.currency_code.is_(None),
        WbFbsOrder.currency_code == RUB_CURRENCY_CODE,
    )
    return case(
        (is_rub, func.coalesce(WbFbsOrder.sale_price, WbFbsOrder.price)),
        else_=WbFbsOrder.converted_price,
    )


def order_price(order: WbFbsOrder) -> Decimal | None:
    """Python-двойник `_price_expr()` — держать рядом, чтобы не разъехались.

    Именно `is None`, а не `or`: цена 0 (нулевой заказ) — валидное значение, и
    `sale_price or price` подменил бы её ценой до скидки.
    """
    if order.currency_code in (None, RUB_CURRENCY_CODE):
        return order.sale_price if order.sale_price is not None else order.price
    return order.converted_price


async def build_cancel_stats(
    db: AsyncSession,
    project_id: int,
    *,
    conditions: list[Any],
    is_seller_bucket: bool,
    dt_from: date | None,
    dt_to: date | None,
    warehouse_scoped: bool,
) -> dict:
    """Сводка корзины отмен: выручка по ВСЕЙ выборке + штраф (оценка и факт).

    `conditions` — ровно те же предикаты, по которым отбирается список, иначе
    сводка считала бы не то, что видно под ней. Выручка агрегируется в БД (не
    по странице!), оценка штрафа — построчно в Python: формула ветвится
    потолками, и в SQL это `CASE`-простыня, которую нечем покрыть тестом.

    `warehouse_scoped` — включён ли фильтр склада WB. У строк финотчёта склада
    НЕТ, поэтому при фильтре по складу факт не сопоставим с выборкой и не
    показывается (лучше пусто, чем сумма по всем складам под заголовком одного).
    """
    # Выручка и число заданий — одним агрегатом по всей выборке фильтра.
    totals = (
        await db.execute(
            select(func.coalesce(func.sum(_price_expr()), 0), func.count()).where(*conditions)
        )
    ).one()
    revenue = Decimal(str(totals[0] or 0))
    orders = int(totals[1] or 0)

    stats: dict[str, Any] = {
        "revenue": revenue,
        "orders": orders,
        "penalty_est": Decimal(0),
        "penalty_est_count": 0,
        "no_commission_count": 0,
        "estimate_truncated": False,
        "penalty_fact": Decimal(0),
        "penalty_fact_count": 0,
        "fact_covered_to": None,
        "fact_scoped_out": False,
    }

    # Клиентские отмены штрафом не облагаются (см. докстринг модуля): выручка
    # посчитана, дальше идти незачем.
    if not is_seller_bucket:
        return stats

    rows = (
        await db.execute(
            select(_price_expr(), WbFbsOrder.subject).where(*conditions).limit(_ESTIMATE_ROW_LIMIT)
        )
    ).all()
    stats["estimate_truncated"] = len(rows) >= _ESTIMATE_ROW_LIMIT

    tariff_map = await get_tariff_map(db, project_id)
    penalty_est = Decimal(0)
    est_count = 0
    no_commission = 0
    for price, subject in rows:
        commission = tariff_map.get(subject) if subject else None
        if commission is None:
            no_commission += 1
            continue
        amount = estimate_penalty(price, commission)
        if amount > 0:
            penalty_est += amount
            est_count += 1
    stats["penalty_est"] = penalty_est
    stats["penalty_est_count"] = est_count
    stats["no_commission_count"] = no_commission

    # Факт: строки удержаний финотчёта за то же окно ДАТ ЗАКАЗА (`order_dt`
    # финотчёта == `created_at_wb` задания), плюс граница, до которой отчёт
    # вообще доехал — она объясняет нули по свежим отменам.
    covered_to = (
        await db.execute(
            select(func.max(WbFinanceRow.date_to)).where(WbFinanceRow.project_id == project_id)
        )
    ).scalar()
    stats["fact_covered_to"] = covered_to

    if warehouse_scoped:
        stats["fact_scoped_out"] = True
        return stats

    fact_conditions = [
        WbFinanceRow.project_id == project_id,
        WbFinanceRow.bonus_type_name.ilike(PENALTY_BONUS_TYPE_LIKE),
    ]
    if dt_from is not None:
        fact_conditions.append(WbFinanceRow.order_dt >= dt_from)
    if dt_to is not None:
        fact_conditions.append(WbFinanceRow.order_dt <= dt_to)
    fact = (
        await db.execute(
            select(func.coalesce(func.sum(WbFinanceRow.penalty), 0), func.count()).where(
                *fact_conditions
            )
        )
    ).one()
    stats["penalty_fact"] = Decimal(str(fact[0] or 0))
    stats["penalty_fact_count"] = int(fact[1] or 0)
    return stats
