# DOMAIN_COST — Себестоимость, номенклатура, пошлины

## Таблицы
| Модель | Назначение | Ключ / constraint |
|--------|------------|-------------------|
| `Nomenclature` | Справочник товаров (barcode, brand, article_wb, volume_l, `first_sale_date`) | — |
| `DutyRule` | Правила пошлин по категориям | basis: weight (€/кг) / area (€/м²) / invoice (% от инвойса) |
| `CostOrder` | Заказы с расчётом себестоимости (`actual_arrival_date` — ось хронологии для FIFO) | — |
| `CostOrderItem` | Позиции заказа (qty, price_cny, weight, volume, calculated costs) | SoftDeleteMixin — фильтровать `is_deleted == False` |
| `CostOpeningBalance` | Стартовый остаток SKU (qty + unit_cost + as_of_date) — seed для FIFO/скользящей | `uq (project_id, barcode)` |

## Бизнес-правила
- **Nomenclature** синхронизируется из WB Content API (`get_cards_list` → `parse_wb_cards_to_nomenclature`). После каждого синка `services.cost.first_sale.backfill_first_sale_dates(only_missing=True)` заполняет `first_sale_date` через `MIN(wb_funnel_daily.date) WHERE orders_count > 0` (best-effort, локальный SQL, без WB API). Frontend по этому полю рендерит бейдж «Новинка ≤40 дней / Активный >40 / Без продаж».
- **Расчёт себестоимости единицы:** `price_cny × курс` + доставка (пропорционально весу/объёму) + пошлина (по `duty_rules`: basis × rate) + утилизационный сбор (`util_collect_rub`).
- **DutyRule basis:** weight (€/кг × вес), area (€/м² × `Nomenclature.area_m2`), invoice (% от инвойса). Для basis=area нужна площадь по баркоду — её заполняют на странице настроек.
- **Площадь для пошлины «За м²»:** `get_missing_area_barcodes` (роутер `GET /cost/nomenclature/missing_area`) отдаёт баркоды, которые есть в машинах (`cost_order_items` × `cost_orders`, любой статус, не удалённые) и чья категория имеет AREA-правило, но `Nomenclature.area_m2` пуст/0 — чтобы дозаполнить площадь перед просчётом пошлины.
- **Cost parsers:** 5 форматов Excel, каждый со своей структурой колонок.
- **avg_cost (cost history):** средняя себестоимость SKU — **взвешенная по qty**: `Σ(total_rub × qty) / Σ(qty)`, не простое среднее по партиям (простое среднее искажает при партиях разного размера). Это `latest`/`avg` ВИТРИНЫ (`cost_history_service`) — для отображения.
- **Оценка COGS для прибыли (`services/cost/valuation.py`):** себестоимость, уходящая в ОПиУ/BDR/воронку/склад. Метод — per-project флаг `ProjectSetting.valuation_method` (`settings_service.get/set_valuation_method`), дефолт `lifetime_avg` (нулевой регресс):
  - `lifetime_avg` — текущее: одно `Σ(total_rub·qty)/Σqty` на всю историю, ко всем периодам (= `bdr_loaders.load_avg_costs`).
  - `fifo` — продажа гасит старейшую партию по её цене; COGS периода = цена реально проданных слоёв. **Рекомендуемый.**
  - `moving_avg` — скользящее среднее остатка, пересчёт на каждом приходе.
  - **Путь-зависимость:** FIFO/moving считаются проигрыванием ВСЕЙ истории продаж от начала (или `CostOpeningBalance`), потом срез по периоду (`compute_project_valuation` → `slice_window`). Нельзя считать «из продаж периода».
  - **Чистая последовательность:** партии — очередь по дате прихода, продажи (в порядке дат, для помесячной разбивки) подтягивают партии по мере необходимости и гасят с головы, БЕЗ гейта по дате прихода (стандартный бухгалтерский FIFO; устойчив к пустым `actual_arrival_date` и продажам раньше первой партии). Физическое списание (ledger) — всегда FIFO; метод меняет только деньги.
  - **Ключ SKU** = `lower(article_seller)` (как у `load_avg_costs`) → `lifetime_avg` бит-в-бит совпадает со старым расчётом. Источник продаж — `wb_finance_rows` нетто (Продажа−Возврат) по `sale_dt`. Нехватка партий → seed (`WbCostOverride`→avg) + флаг `is_estimated`.
  - **Авто-опенинг (`_walk_auto_opening`):** продажи сверх заведённых партий кроются синтетическим слоем `AUTO-OPENING` в голове очереди **по цене первой ПЛАТНОЙ партии** (нулевые опенинги/total_rub=0 не берутся): ранние продажи получают первый себес, поздние — свои реальные партии; при пустой очереди `eff_now.fifo` = первый себес (не средняя). Срабатывает ТОЛЬКО на исторической дыре — первая продажа раньше первой партии (прошлые закупки не завести задним числом); хвостовой дефицит (не заведена свежая закупка) остаётся на seed-пути с warning «заведите приходы». Слой виден в ledger (на фронте нередактируем), warning «Авто-опенинг: N шт…», `is_estimated` True, `oversold_units` = реальный дефицит. Касается только fifo/moving-контуров; `lifetime_avg` и `global_avg` считаются по реальным партиям.
  - **Golden-фикстура:** SKU `200х300_трава` (barcode `2044388679647`, project 4) — проверенные числа в `tests/test_cost_valuation.py`.

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
- `services/cost/valuation.py` — движок оценки COGS (FIFO / moving / lifetime), `compute_project_valuation` → `slice_window`.
- `services/cost/opening_balance.py` — CRUD стартовых остатков + обёртки аналитики (`get_sku_valuation`, `get_valuation_summary`).
- `services/cost_history_service.py` — история себестоимости (витрина avg/latest).
- `etl/cost_parsers.py` — оркестратор импорта из Excel.
- `etl/cost_parser_helpers.py` — хелперы парсинга.
- `routers/cost.py` — HTTP endpoints.
- `models/cost.py` — `Nomenclature`, `DutyRule`, `CostOrder`, `CostOrderItem`.
- `schemas/cost.py`, `tests/test_api_cost.py`.

## Кэш
CRUD/история — без кэша. Аналитика оценки — `@cached("reports:cost_valuation", 300)` (роутер `/cost/valuation/*`); префикс зарегистрирован в `invalidate_project_reports`. Инвалидация — при правке партий/дат прихода/опенинг-баланса/метода и при синке `wb_finance_rows`.
