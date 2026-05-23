# DOMAIN_COST — Себестоимость, номенклатура, пошлины

## Таблицы
| Модель | Назначение | Ключ / constraint |
|--------|------------|-------------------|
| `Nomenclature` | Справочник товаров (barcode, brand, article_wb, volume_l, `first_sale_date`) | — |
| `DutyRule` | Правила пошлин по категориям | basis: weight (€/кг) / area (€/м²) / invoice (% от инвойса) |
| `CostOrder` | Заказы с расчётом себестоимости | — |
| `CostOrderItem` | Позиции заказа (qty, price_cny, weight, volume, calculated costs) | SoftDeleteMixin — фильтровать `is_deleted == False` |

## Бизнес-правила
- **Nomenclature** синхронизируется из WB Content API (`get_cards_list` → `parse_wb_cards_to_nomenclature`). После каждого синка `services.cost.first_sale.backfill_first_sale_dates(only_missing=True)` заполняет `first_sale_date` через `MIN(wb_funnel_daily.date) WHERE orders_count > 0` (best-effort, локальный SQL, без WB API). Frontend по этому полю рендерит бейдж «Новинка ≤40 дней / Активный >40 / Без продаж».
- **Расчёт себестоимости единицы:** `price_cny × курс` + доставка (пропорционально весу/объёму) + пошлина (по `duty_rules`: basis × rate) + утилизационный сбор (`util_collect_rub`).
- **DutyRule basis:** weight (€/кг × вес), area (€/м² × `Nomenclature.area_m2`), invoice (% от инвойса). Для basis=area нужна площадь по баркоду — её заполняют на странице настроек.
- **Площадь для пошлины «За м²»:** `get_missing_area_barcodes` (роутер `GET /cost/nomenclature/missing_area`) отдаёт баркоды, которые есть в машинах (`cost_order_items` × `cost_orders`, любой статус, не удалённые) и чья категория имеет AREA-правило, но `Nomenclature.area_m2` пуст/0 — чтобы дозаполнить площадь перед просчётом пошлины.
- **Cost parsers:** 5 форматов Excel, каждый со своей структурой колонок.
- **avg_cost (cost history):** средняя себестоимость SKU — **взвешенная по qty**: `Σ(total_rub × qty) / Σ(qty)`, не простое среднее по партиям (простое среднее искажает при партиях разного размера).

## Зависимости
- `planning` — `cost_orders` генерируют `planned_payments`.
- `funnel` — `nomenclature.cost_price` используется в unit-экономике.
- `wb_cost_override` — ручное переопределение себестоимости.

## Грабли
- `.all()` без LIMIT в `items.py` — загружает всю nomenclature в память.
- Float division в `funnel/sync.py` для `cost_price` — должен быть Decimal.

## Файлы
- `services/cost/orders.py` — CRUD заказов себестоимости.
- `services/cost/items.py` — позиции в заказе.
- `services/cost/nomenclature.py` — номенклатура (справочник товаров).
- `services/cost/duty.py` — правила пошлин.
- `services/cost/helpers.py` — утилиты расчёта.
- `services/cost/plan_gen.py` — генерация плановых платежей из заказа.
- `services/cost_history_service.py` — история себестоимости.
- `etl/cost_parsers.py` — оркестратор импорта из Excel.
- `etl/cost_parser_helpers.py` — хелперы парсинга.
- `routers/cost.py` — HTTP endpoints.
- `models/cost.py` — `Nomenclature`, `DutyRule`, `CostOrder`, `CostOrderItem`.
- `schemas/cost.py`, `tests/test_api_cost.py`.

## Кэш
Нет кэширования (данные редко запрашиваются массово).
