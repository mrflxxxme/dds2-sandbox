# Domain: Reports (ДДС, БДР, ОПИУ, Dashboard)

## Ownership
Файлы этого домена:
- `services/reports/balance.py` — баланс по счетам
- `services/reports/dds.py` — ДДС-месяц и PnL
- `services/reports/dashboard.py` — KPI дашборда
- `services/reports/queries.py` — общие SQL-запросы
- `services/wb_bdr_service.py` — WB БДР (бюджет доходов и расходов)
- `services/opiu_service.py` — ОПИУ (отчёт о прибылях и убытках)
- `services/bdr_enrichment.py` — обогащение БДР данными
- `services/bdr_loaders.py` — загрузчики данных для БДР
- `services/fx_service.py` — курсы валют
- `services/tax_service.py` — налоговые ставки
- `routers/reports.py` — HTTP endpoints отчётов
- `routers/reports_wb.py` — WB-специфичные отчёты
- `schemas/reports.py`
- `tests/test_api_reports.py`, `tests/test_wb_bdr_service.py`, `tests/test_opiu_service.py`, `tests/test_fx_service.py`

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

### FX
- Курсы извлекаются из конвертационных транзакций ВТБ (backfill)
- PnL использует AVG rate за год (ИЗВЕСТНЫЙ БАГ — нужен daily rate)

## Dependencies
- `transactions` — все отчёты строятся на транзакциях
- `wb_finance_rows` — данные из WB Finance API для БДР/ОПИУ
- `wb_funnel_daily` — данные воронки для unit-экономики

## Known Issues & Gotchas
- `get_dds_pnl()` — 170 строк, сложная функция, трудно тестировать
- FX conversion использует среднюю ставку за год вместо daily rate
- Кэш TTL=300s (5 мин) — может показывать устаревшие данные
- wb_bdr_service.py (420 строк) и opiu_service.py (402 строки) — на грани лимита 400 строк

## Cache Keys
```
reports:balance:project_id={pid}:as_of={date}
reports:dds_month:project_id={pid}:year={y}:month={m}:currency={c}
reports:dashboard:project_id={pid}:date_from={d1}:date_to={d2}
reports:opiu:project_id={pid}:...
reports:wb_bdr:project_id={pid}:...
```
