# Domain: Cost (Себестоимость, Номенклатура, Пошлины)

## Ownership
Файлы этого домена:
- `services/cost/orders.py` — CRUD заказов себестоимости
- `services/cost/items.py` — позиции в заказе
- `services/cost/nomenclature.py` — номенклатура (справочник товаров)
- `services/cost/duty.py` — правила пошлин
- `services/cost/helpers.py` — утилиты расчёта
- `services/cost/plan_gen.py` — генерация плановых платежей из заказа
- `services/cost_history_service.py` — история себестоимости
- `etl/cost_parsers.py` — импорт из Excel (76 строк, оркестратор)
- `etl/cost_parser_helpers.py` — хелперы парсинга (408 строк)
- `routers/cost.py` — HTTP endpoints
- `models/cost.py` — Nomenclature, DutyRule, CostOrder, CostOrderItem
- `schemas/cost.py`
- `tests/test_api_cost.py`

## Tables
- `nomenclature` — справочник товаров (barcode, brand, article_wb, volume_l, **first_sale_date** — дата первой продажи, заполняется из `wb_funnel_daily`)
- `duty_rules` — правила пошлин по категориям (basis: weight/volume/amount)
- `cost_orders` — заказы с расчётом себестоимости
- `cost_order_items` — позиции заказа (qty, price_cny, weight, volume, calculated costs). **SoftDeleteMixin** — фильтровать `is_deleted == False` во всех SELECT

## Business Rules
1. **Nomenclature:** синхронизируется из WB Content API (get_cards_list → parse_wb_cards_to_nomenclature). После каждого синка вызывается `services.cost.first_sale.backfill_first_sale_dates(only_missing=True)` — заполняет `first_sale_date` через `MIN(wb_funnel_daily.date) WHERE orders_count > 0` (best-effort, локальный SQL, без WB API). Frontend на основе этого поля рендерит бейдж «Новинка ≤40 дней / Активный >40 / Без продаж».
2. **Cost calculation:**
   - Цена товара (price_cny) × курс
   - + Доставка (пропорционально весу/объёму)
   - + Пошлина (по duty_rules: basis × rate)
   - + Утилизационный сбор (util_collect_rub)
   = Себестоимость единицы
3. **DutyRule basis:** weight (кг), volume (л), amount (% от стоимости)
4. **Cost parsers:** 5 форматов Excel — каждый со своей структурой колонок
5. **Cost history avg_cost:** средняя себестоимость SKU = **взвешенная по qty**: `Σ(total_rub × qty) / Σ(qty)` (см. `cost_history_service.py:116-118`). До 2026-04-17 считалась простым средним по партиям — давала искажение при партиях разного размера.

## Known Issues & Gotchas
- ~~`cost_parsers.py` — 465 строк~~ — **ИСПРАВЛЕНО** (разбит на cost_parsers.py 76 строк + cost_parser_helpers.py 408 строк)
- `.all()` без LIMIT в items.py — загружает всю nomenclature в память
- Float division в funnel/sync.py для cost_price — должен быть Decimal
- ~~N+1 запросы в get_cost_orders~~ — **ИСПРАВЛЕНО** (2026-03-16, batch-загрузка)

## Dependencies
- `nomenclature` — используется в funnel для unit-экономики (cost_price)
- `wb_cost_override` — ручное переопределение себестоимости
- `planning` — cost_orders генерируют planned_payments

## Cache Invalidation
Нет кэширования (данные редко запрашиваются массово).
