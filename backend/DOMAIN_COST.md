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
- `etl/cost_parsers.py` — импорт из Excel (465 строк — ПРЕВЫШАЕТ лимит 400!)
- `routers/cost.py` — HTTP endpoints
- `models/cost.py` — Nomenclature, DutyRule, CostOrder, CostOrderItem
- `schemas/cost.py`
- `tests/test_api_cost.py`

## Tables
- `nomenclature` — справочник товаров (barcode, brand, article_wb, volume_l)
- `duty_rules` — правила пошлин по категориям (basis: weight/volume/amount)
- `cost_orders` — заказы с расчётом себестоимости
- `cost_order_items` — позиции заказа (qty, price_cny, weight, volume, calculated costs)

## Business Rules
1. **Nomenclature:** синхронизируется из WB Content API (get_cards_list → parse_wb_cards_to_nomenclature)
2. **Cost calculation:**
   - Цена товара (price_cny) × курс
   - + Доставка (пропорционально весу/объёму)
   - + Пошлина (по duty_rules: basis × rate)
   - + Утилизационный сбор (util_collect_rub)
   = Себестоимость единицы
3. **DutyRule basis:** weight (кг), volume (л), amount (% от стоимости)
4. **Cost parsers:** 5 форматов Excel — каждый со своей структурой колонок

## Known Issues & Gotchas
- `cost_parsers.py` — 465 строк (ПРЕВЫШАЕТ лимит 400, нужен рефакторинг)
- `.all()` без LIMIT в items.py — загружает всю nomenclature в память
- Float division в funnel/sync.py для cost_price — должен быть Decimal

## Dependencies
- `nomenclature` — используется в funnel для unit-экономики (cost_price)
- `wb_cost_override` — ручное переопределение себестоимости
- `planning` — cost_orders генерируют planned_payments

## Cache Invalidation
Нет кэширования (данные редко запрашиваются массово).
