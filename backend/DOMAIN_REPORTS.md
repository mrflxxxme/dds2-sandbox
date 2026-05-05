# Domain: Reports (ДДС, БДР, ОПИУ, Dashboard)

## Ownership
Файлы этого домена:
- `services/reports/balance.py` — баланс по счетам
- `services/reports/dds.py` — ДДС-месяц и PnL
- `services/reports/dashboard.py` — KPI дашборда
- `services/reports/queries.py` — общие SQL-запросы
- `services/wb_bdr_service.py` — WB БДР (бюджет доходов и расходов)
- `services/opiu_service.py` — ОПИУ (отчёт о прибылях и убытках)
- `services/cost_dna_service.py` — Cost-DNA (декомпозиция выручки по категориям)
- `services/cost_dna_helpers.py` — SQL builders и per-subject агрегации для Cost-DNA
- `services/bdr_enrichment.py` — обогащение БДР данными
- `services/bdr_loaders.py` — загрузчики данных для БДР
- `services/fx_service.py` — курсы валют
- `services/tax_service.py` — налоговые ставки
- `services/stock_forecast_service.py` — прогноз запасов
- `services/warehouse_stock_service.py` — остатки на складах WB
- `services/order_geography_service.py` — география заказов WB
- `services/warehouse_geo.py` — координаты складов
- `services/warehouse_geo_data.py` — данные координат складов
- `services/stock_analytics_service.py` — ре-экспорт stock_forecast + warehouse
- `routers/reports.py` — HTTP endpoints отчётов
- `routers/reports_wb.py` — WB-специфичные отчёты
- `routers/reports_stock.py` — складские отчёты и прогноз
- `schemas/reports.py`
- `tests/test_api_reports.py`, `tests/test_wb_bdr_service.py`, `tests/test_opiu_service.py`, `tests/test_fx_service.py`
- `tests/test_stock_analytics_service.py`, `tests/test_warehouse_stocks.py`, `tests/test_warehouse_geo.py`, `tests/test_order_geography.py`
- `tests/test_cost_dna_helpers.py`, `tests/test_reports_cost_dna.py` — Cost-DNA unit + API
- `tests/test_warehouse_unified_stock_bdr_parity.py` — гарантирует что Unified Stock и БДР не расходятся по реализации

## Tables (read-only, кроме fx_rates и tax_rates)
- `transactions` — источник данных (ЧТЕНИЕ)
- `opening_balances` — начальные остатки
- `fx_rates` — курсы валют (ЗАПИСЬ через backfill/manual)
- `tax_rates` — налоговые ставки

## Business Rules

### ДДС (Cash Flow)
- Баланс = opening_balance + SUM(net) WHERE is_cashflow2=1
- Группировка по cat_lvl1_2 / cat_lvl2_2
- Валюта: фильтр по currency, CNY конвертируется в RUB

### БДР (WB)
- `deduction` содержит: рекламу, кредиты, отзывы, прочее
- **КРИТИЧНО:** `ad_deduction` — ОТДЕЛЬНАЯ статья (НЕ включать в to_pay)
- **КРИТИЧНО:** `loan_deduction` — финансовая операция (НЕ включать в операционную прибыль)
- Только `other_deduction` → операционные расходы
- При добавлении нового типа удержаний → обновить ОБОИХ: wb_bdr_service.py И opiu_service.py

### ОПИУ (P&L)
- Выручка = to_pay (от WB) + продажи из transactions
- Себестоимость = cost_price * qty
- Налог = % от выручки (6% по умолчанию, настраивается)

### Cost-DNA (декомпозиция выручки по категориям)
- **Endpoint:** `GET /reports/cost_dna` (`routers/reports_wb.py`) — возвращает per-subject разбор каждого рубля выручки: себестоимость (factory/duty/delivery/VAT) + комиссии WB (commission/logistics/storage/adv/other) + налоги + маржа.
- **Период:** либо rolling (`period_days=30|60` от вчерашнего дня), либо custom `date_from`/`date_to` (оба обязательны, max 365 дней) — custom range нужен для прямого сравнения с БДР.
- **Выручка / WB-fees:** `wb_finance_rows GROUP BY subject_name` (пустой subject игнорируется — ≈408k строк).
- **Ad spend:** `wb_funnel_daily.adv_sum GROUP BY subject` — тот же паттерн что в БДР/ОПИУ.
- **Cost aggregation (фикс 2026-04-14):** per-article weighted cost из `cost_order_items` (weight = purchase qty) умножается на per-article net sale qty из `wb_finance_rows` за период, потом суммируется по subject. Старый алгоритм (weight = purchase qty на subject-уровне) завышал себестоимость, если закупочный микс не совпадал с продажным.
- **SA join (фикс 2026-04-14):** `LOWER(article_seller)` в `cost_order_items` ↔ `LOWER(sa_name)` в `wb_finance_rows` — CSV-импорты часто хранят SA в UPPERCASE, WB API — в lowercase. Без LOWER проекты с mixed-case каталогом видели cost_total=0.
- **Commission + Tax:** совпадают с правилами OPIU (`opiu_service.py`) — меняешь там → меняй здесь синхронно.
- **Cache key (фикс 2026-04-15):** `snapshot_date`, `date_from`, `date_to` ОБЯЗАНЫ быть в kwargs `get_cost_dna(...)` — иначе `@cached` не включит их в ключ и rolling snapshot будет тихо дрейфовать через полночь. Router (`reports_wb.py`) пиннит `snapshot_date=utcnow().date()` в момент запроса.

### FX
- Курсы извлекаются из конвертационных транзакций ВТБ (backfill)
- PnL использует AVG rate за год (ИЗВЕСТНЫЙ БАГ — нужен daily rate)

### Stock Forecast (Аналитика остатков)
- **Источник:** `stock_forecast_service.get_stock_analytics` (router `reports_stock.py /stock_analytics`).
- **Стартовая точка матрицы прогноза:** ВСЕГДА только полезный остаток WB (`quantity + in_way_from_client + in_way_to_client × (1 − buyout%)`). РФ / on-assembly / in-transit НЕ плюсуются в день 0.
- **Свободный РФ → WB:** `free_rf = stocks_rf − in_assembly_total` приходит синтетической поставкой через `total_days = assembly + avg(delivery) + wb_acceptance` ([WarehouseDeliveryTime](models/warehouse.py)). Если qty на нескольких складах — арифметическое среднее `total_days` (вариант B). Поле API: `articles[].rf_avg_days`. Fallback при пустой `warehouse_delivery_times` — `forecast_rf_default_days` (default 8).
- **On-assembly (PENDING/IN_PROGRESS/READY/VEHICLE_ASSIGNED):** каждая `AssemblyRequestItem` идёт отдельной поставкой с собственной ETA. `ready = COALESCE(actual_ready_date, estimated_ready_date, created_at + 3 дн)`, далее `eta = ready + post_assembly_days[warehouse_id]`, где `post_assembly_days = avg(delivery) + wb_acceptance` (без assembly — оно уже в `ready_date`). Fallback: `max(0, forecast_rf_default_days − 3)`. Просроченные ETA (`eta < today`) попадают в день 0.
- **In-transit (SHIPPED):** прибытие = `delivery_date` (она же «дата сдачи» из листа логиста). Без acceptance — `delivery_date` уже фиксирует сдачу.
- **Дедупликация:** `free_rf` исключает on-assembly qty (физически на РФ, но уйдёт через свою ETA), иначе двойной счёт. SHIPPED заявки физически уже **не** на РФ-складе и `stocks_rf` их не содержит.
- **`days_left` / traffic-light** — по `total_stock = WB + stocks_rf + in_transit` (физический остаток, без учёта расписаний). Дневная матрица может опустошиться раньше `days_left`, если РФ далеко.
- **Настройка fallback-дней:** `GET/PUT /api/v1/refs/forecast-rf-default-days` (`settings_service.get/set_forecast_rf_default_days`, ключ `forecast_rf_default_days`, диапазон 0..365). UI: страница `warehouse/analytics` → вкладка «⚙️ Настройки» → карточка «📦 Время РФ → WB по умолчанию».

## Dependencies
- `transactions` — все отчёты строятся на транзакциях
- `wb_finance_rows` — данные из WB Finance API для БДР/ОПИУ
- `wb_funnel_daily` — данные воронки для unit-экономики

## Known Issues & Gotchas
- `get_dds_pnl()` — 170 строк, сложная функция, трудно тестировать
- FX conversion использует среднюю ставку за год вместо daily rate
- Кэш TTL=300s (5 мин) — может показывать устаревшие данные
- wb_bdr_service.py (360 строк) и opiu_service.py (365 строк) — ниже лимита 400 строк

## Cache Keys
```
reports:balance:project_id={pid}:as_of={date}
reports:dds_month:project_id={pid}:year={y}:month={m}:currency={c}
reports:dashboard:project_id={pid}:date_from={d1}:date_to={d2}
reports:opiu:project_id={pid}:...
reports:wb_bdr:project_id={pid}:...
reports:cost_dna:project_id={pid}:period_days={n}:date_from={d1}:date_to={d2}:snapshot_date={s}
```
