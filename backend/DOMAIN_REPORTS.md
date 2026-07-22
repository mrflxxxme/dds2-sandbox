# DOMAIN_REPORTS — ДДС, БДР, ОПИУ, Dashboard, Cost-DNA

Финансовые и складские отчёты. Все строятся поверх `transactions`,
`wb_finance_rows`, `wb_funnel_daily` (read-only). На запись — только `fx_rates`
и `tax_rates`. Кэш отчётов TTL=300s.

## Таблицы
| Таблица | Роль |
|---------|------|
| `transactions` | источник данных всех отчётов (чтение) |
| `opening_balances` | начальные остатки счетов |
| `wb_finance_rows` | данные WB Finance API для БДР/ОПИУ/Cost-DNA |
| `wb_funnel_daily` | данные воронки для unit-экономики |
| `fx_rates` | курсы валют (запись через backfill/manual) |
| `tax_rates` | помесячные налоговые ставки проекта |

## Бизнес-правила

### ДДС (Cash Flow)
- Баланс = `opening_balance + SUM(net) WHERE is_cashflow2=1`.
- Группировка по `cat_lvl1_2` / `cat_lvl2_2`. Фильтр по `currency`; CNY конвертируется в RUB.

### БДР (WB)
- `deduction` содержит рекламу, кредиты, отзывы, прочее.
- **КРИТИЧНО:** `ad_deduction` — отдельная статья, НЕ включать в `to_pay`.
- **КРИТИЧНО:** `loan_deduction` — финансовая операция, НЕ включать в операционную прибыль.
- Только `other_deduction` → операционные расходы.
- При добавлении нового типа удержаний — обновить ОБА файла: `wb_bdr_service.py` И `opiu_service.py`.
- **Налог мультимесячного диапазона** — `load_tax_settings_by_month` (ставки на каждый месяц). Одинаковые настройки → одноставочный расчёт как раньше; смешанные → «Итого» помесячно (`apply_tax_by_month`, точно), строки — по взвешенной по доходам blended-ставке (`blended_tax_info`, приближение). В ОПиУ месячные колонки всегда считаются ставками своего месяца.
- **Себестоимость при движковых методах (fifo/moving_avg):** ноль движка (партии не сматчились по ключу / нетто-ноль окна) НЕ списывается как 0 — фолбэк `cost_with_fallback` на среднюю закупочную → override (в BDR и ОПиУ). Иначе прибыль тихо завышается (прод-кейс elka 2026-07-21).

### ОПИУ (P&L)
- Выручка = `to_pay` (от WB) + продажи из `transactions`.
- Себестоимость = `cost_price * qty`. Налог = % от выручки (по умолчанию 6%, настраивается).

### Cost-DNA (декомпозиция выручки по категориям)
`GET /reports/cost_dna` (`routers/reports_wb.py`) — per-subject разбор каждого рубля выручки: себестоимость (factory/duty/delivery/VAT) + комиссии WB + налоги + маржа.
- **Период:** rolling (`period_days=30|60` от вчера) либо custom `date_from`/`date_to` (оба обязательны, max 365 дней — для сравнения с БДР).
- **Выручка / WB-fees:** `wb_finance_rows GROUP BY subject_name` (пустой subject игнорируется).
- **Ad spend:** `wb_funnel_daily.adv_sum GROUP BY subject`.
- **Cost aggregation:** per-article weighted cost из `cost_order_items` (weight = purchase qty) × per-article net sale qty из `wb_finance_rows` за период, потом сумма по subject. Subject-уровневый вес завышал бы себестоимость при несовпадении закупочного и продажного микса.
- **SA join:** `LOWER(article_seller)` ↔ `LOWER(sa_name)` — CSV-импорты хранят SA в UPPERCASE, WB API в lowercase; без LOWER mixed-case каталог даёт `cost_total=0`.
- **Commission + Tax:** совпадают с правилами `opiu_service.py` — меняешь там, меняй здесь синхронно.

### FX
- Курсы извлекаются из конвертационных транзакций ВТБ (backfill).

### Stock Forecast (аналитика остатков)
`stock_forecast_service.get_stock_analytics` (router `reports_stock.py /stock_analytics`).
- **Режимы (`mode`):** `wb` (только полезный остаток WB); `wb_rf` (+ свободный РФ как scheduled delivery); `wb_rf_transit` (+ on-assembly + in-transit, полный pipeline); `wb_assembly_transit` (WB + on-assembly + in-transit, без свободного РФ).
- **День 0 матрицы — всегда только полезный остаток WB:** `quantity + in_way_from_client + in_way_to_client × (1 − buyout%)`. РФ / on-assembly / in-transit в день 0 не плюсуются.
- **Свободный РФ → WB:** `free_rf = stocks_rf − in_assembly_total` приходит синтетической поставкой через `total_days = assembly + avg(delivery) + wb_acceptance`. Несколько складов → арифметическое среднее `total_days`. Fallback при пустой `warehouse_delivery_times` — `forecast_rf_default_days` (default 8, настройка `GET/PUT /api/v1/refs/forecast-rf-default-days`, диапазон 0..365).
- **On-assembly:** каждая `AssemblyRequestItem` (PENDING/IN_PROGRESS/READY/VEHICLE_ASSIGNED) — отдельная поставка. `ready = COALESCE(actual_ready_date, estimated_ready_date, created_at + 3 дн)`, `eta = ready + post_assembly_days[warehouse_id]` (`post_assembly_days = avg(delivery) + wb_acceptance`). Просроченные ETA попадают в день 0.
- **In-transit (SHIPPED):** прибытие = `delivery_date` (дата сдачи из листа логиста).
- **Дедупликация:** `free_rf` исключает on-assembly qty (иначе двойной счёт); SHIPPED-заявки физически не на РФ-складе и `stocks_rf` их не содержит.
- **`days_left` / traffic-light** считаются по `total_stock = WB + stocks_rf + in_transit` (физический остаток, без расписаний) — дневная матрица может опустеть раньше `days_left`, если РФ далеко.

### Cold-start (распределение SKU-новинок)
SKU-новинка имеет ФФ-остаток, но нет статистики продаж — обычная локализация не работает. Cold-start распределяет ФФ-остаток по WB-складам пропорционально долям ФО проекта. Подробный алгоритм и связь с распределением сборки — в `DOMAIN_WAREHOUSE.md` (`cold_start_distribution_service.py`).
- Endpoints: `POST /api/v1/reports/distribute_cold_start` (per-SKU), `GET /api/v1/reports/cold_start_table` (сегмент целиком, SKU-новинки с `rf_qty > 0`).

## Зависимости
- `transactions`, `wb_finance_rows`, `wb_funnel_daily` — источники данных всех отчётов.
- `DOMAIN_ASSEMBLY` — on-assembly заявки в Stock Forecast.
- `DOMAIN_WAREHOUSE` — cold-start алгоритм, остатки WB.

## Грабли
- **Cost-DNA cache key:** `snapshot_date`, `date_from`, `date_to` обязаны быть в kwargs `get_cost_dna(...)` — иначе `@cached` не включит их в ключ и rolling snapshot тихо дрейфует через полночь. Router пиннит `snapshot_date=utcnow().date()` в момент запроса.
- FX-конверсия в PnL использует среднюю годовую ставку вместо daily rate (известный баг).
- Кэш TTL=300s — может показывать устаревшие данные.

## Файлы
- `services/reports/` — `dds.py` (ДДС + PnL), `balance.py`, `dashboard.py`, `queries.py`.
- `services/wb_bdr_service.py` (+ `bdr_loaders.py`, `bdr_enrichment.py`) — WB БДР.
- `services/opiu_service.py` — ОПИУ.
- `services/cost_dna_service.py` (+ `cost_dna_helpers.py`) — Cost-DNA.
- `services/fx_service.py`, `services/tax_service.py` — курсы валют, налоговые ставки.
- `services/stock_forecast_service.py`, `services/warehouse_stock_service.py` — прогноз и остатки WB.
- `services/order_geography_service.py`, `services/warehouse_geo*.py` — география заказов, координаты складов.
- `services/cold_start_distribution_service.py` — cold-start распределение.
- `routers/reports.py`, `routers/reports_wb.py`, `routers/reports_stock.py` — HTTP endpoints.
- `schemas/reports.py`, `schemas/cold_start.py` — Pydantic.

### Cache keys
```
reports:balance:project_id={pid}:as_of={date}
reports:dds_month:project_id={pid}:year={y}:month={m}:currency={c}
reports:dashboard:project_id={pid}:date_from={d1}:date_to={d2}
reports:opiu / reports:wb_bdr / reports:cost_dna  (см. примечание про cache key)
```
