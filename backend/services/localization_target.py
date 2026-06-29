"""Распределение товара «до целевой локализации» (по умолчанию 75%).

Единый АВТОРИТЕТ логики «до 75% / не перетаривать» для обоих путей распределения:
обычные SKU (``warehouse_need_service`` шаг 4.6) и новинки
(``cold_start_distribution_service``). Чистые функции без БД — на вход подаётся уже
посчитанные спрос/покрытие по округам и веса складов; вызывающий код строит их из
своих источников (заказы, WB-сток, активные сборки, транзит, speed-карта воришек).

Принцип (решение пользователя): «до 75% идти» и «не перетаривать» — ОДИН механизм.
1. Склады заполняются в порядке убывания priority_weight (anchor'ы вперёд, склады-
   воришки с penalty 0.6 — в хвост → отсекаются первыми).
2. Округ не затаривается сверх своего спроса (учитывая уже имеющееся покрытие =
   WB-сток + в-сборке + в-пути) — это и есть «не перетаривать».
3. Налив прекращается по достижении ``target_pct`` локализации (доля спроса,
   покрытая локально). Остаток ``cap`` НЕ размазывается на дальние склады — он
   остаётся на ФФ.

Локализация = Σ_округ min(спрос_округа, покрытие_округа) / общий_спрос. Общий спрос
ВКЛЮЧАЕТ незалокализуемый (зарубеж/СНГ) спрос в знаменателе — поэтому 75% означает
«75% всех заказов доставлено локально», а зарубежные заказы естественно ограничивают
достижимый максимум (их нельзя покрыть локально).

Whole-box/whole-pallet НЕ здесь: helper отдаёт сырые штуки, округление до целых
коробов/паллет делает фронтовый ``normalizeDraft`` (единый авторитет упаковки).
"""
from __future__ import annotations

import math
from collections.abc import Mapping

# Целевая локализация по умолчанию (%). 75% — порог КТР «excellent» (≤0.90);
# выше — затоварка дальних складов с убывающей отдачей. Решение пользователя.
DEFAULT_LOCALIZATION_TARGET_PCT = 75.0


def localization_pct(
    demand_by_okrug: Mapping[str, float],
    covered_by_okrug: Mapping[str, float],
    total_demand: float,
) -> float:
    """Доля локализованного спроса в процентах [0..100].

    Σ_округ min(спрос_округа, покрытие_округа) / общий_спрос * 100. Покрытие сверх
    спроса округа не засчитывается (min). Деление на ноль безопасно → 0.0.
    """
    if total_demand <= 0:
        return 0.0
    local = sum(
        min(dem, covered_by_okrug.get(okrug, 0.0))
        for okrug, dem in demand_by_okrug.items()
    )
    return local / total_demand * 100.0


def greedy_allocate_to_target(
    cap_total: int,
    wh_demand: Mapping[str, int],
    wh_weight: Mapping[str, float],
    wh_okrug: Mapping[str, str],
    demand_by_okrug: Mapping[str, float],
    existing_by_okrug: Mapping[str, float],
    total_demand: float,
    target_pct: float = DEFAULT_LOCALIZATION_TARGET_PCT,
) -> dict[str, int]:
    """Жадно распределить ``cap_total`` штук по складам до ``target_pct`` локализации.

    Args:
      cap_total: сколько штук доступно к отгрузке (= can_send = min(rf_avail, спрос)).
      wh_demand: остаточная потребность по складу (штук, ≥0).
      wh_weight: priority_weight склада (выше = заполняется раньше; anchor↑, воришка↓).
      wh_okrug: склад → канон-ключ округа (ФО).
      demand_by_okrug: суммарный локализуемый спрос по округу (основа покрытия).
      existing_by_okrug: уже покрыто по округу (WB-сток + в-сборке + в-пути).
      total_demand: ВЕСЬ спрос (вкл. зарубеж) — знаменатель локализации.
      target_pct: целевая локализация, дефолт 75%.

    Returns:
      {склад → выделенные штуки}. Инварианты: Σ ≤ cap_total; alloc[wh] ≤ wh_demand[wh];
      покрытие округа не растёт выше спроса округа (не перетаривать). Если existing уже
      даёт ≥ target — возвращает нули (ничего не отгружаем).
    """
    allocated: dict[str, int] = {wh: 0 for wh in wh_demand}
    if cap_total <= 0 or total_demand <= 0:
        return allocated

    covered: dict[str, float] = dict(existing_by_okrug)
    target_local = target_pct / 100.0 * total_demand
    remaining = cap_total

    def current_local() -> float:
        return sum(
            min(dem, covered.get(okrug, 0.0))
            for okrug, dem in demand_by_okrug.items()
        )

    # anchor'ы (высокий вес) — первыми; воришки (penalty) — в хвост; tie-break по имени.
    order = sorted(wh_demand, key=lambda w: (-wh_weight.get(w, 1.0), w))

    for wh in order:
        if remaining <= 0:
            break
        cur = current_local()
        if cur >= target_local:
            break
        okrug = wh_okrug.get(wh, "unknown")
        # Запас локализации в этом округе: сколько ещё спроса НЕ покрыто. Если округ
        # уже насыщен (покрытие ≥ спрос) — пропускаем (не перетаривать).
        headroom = demand_by_okrug.get(okrug, 0.0) - covered.get(okrug, 0.0)
        if headroom <= 0:
            continue
        need_to_target = target_local - cur
        take = int(min(
            wh_demand[wh],          # не больше потребности склада
            remaining,              # не больше доступного cap
            math.floor(headroom),   # не перетарить округ сверх спроса
            math.ceil(need_to_target),  # не перелить сильно за цель
        ))
        if take <= 0:
            continue
        allocated[wh] += take
        covered[okrug] = covered.get(okrug, 0.0) + take
        remaining -= take

    return allocated
