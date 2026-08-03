---
name: fbs-stage-analytics-invariants
description: Аналитика этапов FBS (stage_analytics + order_history) — что закрыто ревью 30.07 и какая НОВАЯ связка сломалась после появления истории кабинета: t_closed перестал означать «зеркало поставки есть»
metadata:
  type: project
---

История двух ревью домена: 2026-07-30 (до истории кабинета) и 2026-07-31 (коммиты `dc7d9b83..20394c33`, догон истории из ЛК + вкладки «Этапы»/«География»).

**ЗАКРЫТО во второй итерации** (не переоткрывать): дыра `postponed_delivery` в зеркале очереди — теперь `sorted_not_ready = FBS_WB_SORTED_STATUSES минус ready_for_pickup`, инвариант `sorted ∪ ready == sorted_condition()` держится по КОНСТАНТЕ; `resolve_period` импортируется из `orders_stats`, а не копируется; этапа на `written_off_at` больше нет (в коде стоит развёрнутый комментарий-почему).

**1. 🔴 НОВОЕ: `t_closed` больше НЕ означает «строка поставки в зеркале есть».**
`t_closed = coalesce(history.h_assembled, case(with_supply, supply.closed_at))`. А `_left_us_expr` (граница очереди `to_ship` / `in_transit`) построен на посылке «поставки нет ⇒ t_closed IS NULL»: `t_scan IS NOT NULL OR (t_closed IS NULL AND t_writeoff IS NOT NULL)`. Как только догон истории привезёт заданию веху «Продавец собрал заказ», `t_closed` заполнится ДАЖЕ без зеркала поставки — и те 326 прод-заданий, ради которых вторая ветка написана, вернутся в «Собрано — ждёт отгрузки», тогда как вкладка «Заказы» (`transit_anchor` = coalesce(scan_dt, closed_at, **written_off_at**)) продолжит считать их зависшими в пути.
**Why:** веха истории и веха зеркала легли в ОДНУ колонку CTE, а читатель колонки различал их по NULL.
**How to apply:** тест `test_queue_without_supply_mirror_counts_as_left_us` данных истории НЕ создаёт — он зелёный и после поломки. Любую правку `_left_us_expr` / `_milestones` проверять кейсом «нет supply-строки + есть `WbFbsOrderHistory('Продавец собрал заказ')` + `written_off_at`». Разбиение `to_ship ∪ in_transit` при этом не рвётся — расходится только раскладка внутри, поэтому инвариант-тест тоже молчит.

**2. Период в `geo_analytics._directions` действует НЕ на всю строку.** `orders`/`median_days`/`avg_hops`/`sla_*` — по путям, завершившимся в периоде; `matured`/`refused` — по всей истории проекта (`created_at_wb < now - 14д`, без границ периода). Смена периода 30д→7д долю отказов не двигает вовсе. `_nodes` (узлы маршрута) точно так же игнорирует фильтр склада — единственный блок вкладки, который на него не реагирует. Тесты обоих случаев не ловят: `test_refusals_counted_only_on_matured_cohort` берёт период в целый год, `test_warehouse_filter_narrows_everything` проверяет только `directions` и `matrix`.

**3. `written_off_at` — по-прежнему артефакт бэкфилла** (кластер 390 строк в одном часе, медиана 42.3 ч / max 2136 ч). Из этапов он убран, но живёт третьим фолбэком `transit_anchor_expr` и второй веткой `_left_us_expr` — см. п.1. Связано с [[fbs-writeoff-transit-invariants]].

**4. Догон истории идёт БЕЗ Redis-лока**, хотя канон домена — `services/wb_fbs/locks.py` (так живут push и writeoff). Джоб (15 мин) и ручка `POST /orders/history/sync` берут одну и ту же очередь `history_synced_at IS NULL` и при наложении делают двойную работу и удваивают темп против лимита 150/мин; лечение выбрано другое — retry на 429. Ср. п.4 в [[fbs-assembly-mirror-invariants]] («идемпотентность создания есть, идемпотентности прогона нет»).
