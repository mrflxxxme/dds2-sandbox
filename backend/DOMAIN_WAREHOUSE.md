# DOMAIN_WAREHOUSE — Склад, остатки, приёмка, отгрузка, перемещения, FBO

## Назначение
Модуль управления складскими операциями: учёт остатков, приёмка товаров,
отгрузка (только FULFILLMENT), перемещения между складами, корректировки.
Материализованный баланс (WarehouseStock) + полный audit trail (StockMovement).
Синхронизация FBO-поставок с WB Marketplace API.

## Связи с другими доменами

```
Warehouse (EXTERNAL | FULFILLMENT)
  ├─ InboundReceipt → accept → _update_stock(INBOUND)
  ├─ OutboundShipment → ship → _update_stock(OUTBOUND)    ← Assembly создаёт при SHIPPED
  ├─ StockTransfer → send/complete → _update_stock(TRANSFER_IN/OUT)
  ├─ StockAdjustment → _update_stock(ADJUSTMENT)
  │
  ├─ WarehouseStock (материализованный баланс: quantity + in_transit + cost_price)
  └─ StockMovement (append-only audit log)

WbFboSupply ←(link)→ OutboundShipment   # ручная привязка или через Assembly
WbWarehouseStock                         # отдельная система — остатки WB API (read-only)
Nomenclature ← _resolve_barcode()       # все items резолвятся через баркод
```

## Ключевые зависимости

### Ядро стока (разбит на 4 файла):

#### warehouse_stock_engine.py (323 строки) — движения и остатки:
```python
# Генерирует автономер: IN-1, OUT-2, TR-3
async def _next_number(db, project_id, prefix, model_class) -> str:

# Резолвит баркод → Nomenclature. Raises ValueError если не найден.
async def _resolve_barcode(db, project_id, barcode) -> Nomenclature:

# ЕДИНСТВЕННАЯ точка изменения остатков.
# delta > 0 = приход, delta < 0 = расход. Raises ValueError при qty < 0.
# Создаёт WarehouseStock (upsert) + StockMovement (audit).
async def _update_stock(
    db, project_id, warehouse_id, nomenclature_id, barcode,
    delta, movement_type, reference_type, reference_id=None, comment=None
) -> None:
```

#### warehouse_crud.py (241 строк) — CRUD складов
#### warehouse_inbound.py (254 строки) — приёмка (create, accept, cancel, update)
#### warehouse_outbound.py (365 строк) — отгрузка + перемещения
#### warehouse_service.py (59 строк) — re-export для обратной совместимости

### Модели (models/warehouse.py):
- `Warehouse` L26 — SoftDelete, warehouse_type: EXTERNAL|FULFILLMENT, assembly_days
- `InboundReceipt` L72 — SoftDelete, status: DRAFT|EXPECTED|ACCEPTED|CANCELLED
- `InboundReceiptItem` L104 — receipt_id, nomenclature_id, barcode, expected_qty, actual_qty
- `OutboundShipment` L142 — SoftDelete, status: DRAFT|SHIPPED|DELIVERED|CANCELLED, wb_supply_id
- `OutboundShipmentItem` L168 — shipment_id, nomenclature_id, barcode, quantity
- `StockTransfer` L192 — SoftDelete, from_warehouse_id, to_warehouse_id, status: DRAFT|IN_TRANSIT|COMPLETED
- `StockTransferItem` L218 — transfer_id, nomenclature_id, barcode, quantity
- `StockMovement` L240 — БЕЗ SoftDelete (append-only), movement_type enum, reference_type/id
- `WarehouseStock` L262 — БЕЗ SoftDelete, UNIQUE(project_id, warehouse_id, nomenclature_id)
- `StockAdjustment` L280 — БЕЗ SoftDelete, delta (+ излишек, - недостача)

### Enums:
- `WarehouseType` — EXTERNAL, FULFILLMENT
- `ReceiptStatus` — DRAFT, EXPECTED, ACCEPTED, CANCELLED
- `OutboundStatus` — DRAFT, SHIPPED, DELIVERED, CANCELLED
- `TransferStatus` — DRAFT, IN_TRANSIT, COMPLETED
- `MovementType` — INBOUND, INBOUND_CANCEL, OUTBOUND, OUTBOUND_CANCEL, TRANSFER_IN, TRANSFER_OUT, INBOUND_EDIT, ADJUSTMENT

## Статусные модели

### Приёмка (InboundReceipt)
```
DRAFT → EXPECTED → ACCEPTED
                      ↓ (cancel = rollback stock)
CANCELLED ← ACCEPTED
CANCELLED ← DRAFT
CANCELLED ← EXPECTED
```

### Отгрузка (OutboundShipment)
```
DRAFT → SHIPPED → DELIVERED
           ↓ (cancel = return stock)
       CANCELLED
```

### Перемещение (StockTransfer)
```
DRAFT → IN_TRANSIT → COMPLETED
  source: qty -= delta, TRANSFER_OUT    target: qty += delta, TRANSFER_IN
  target: in_transit += delta           target: in_transit -= delta
```

## При Accept Receipt (DRAFT|EXPECTED → ACCEPTED)

1. **Автозаполнение:** если `actual_qty <= 0` и `expected_qty > 0`, ставит `actual_qty = expected_qty`
2. Для каждого item с `actual_qty > 0`: `_update_stock(delta=+actual_qty, movement_type=INBOUND, reference_type=RECEIPT)`
3. `receipt.status = ACCEPTED`, `receipt.actual_date = date.today()`

## При Cancel Accepted Receipt (ACCEPTED → CANCELLED)

1. Для каждого item: `_update_stock(delta=-actual_qty, movement_type=INBOUND_CANCEL)`
2. `receipt.status = CANCELLED`

## При Ship Shipment (DRAFT → SHIPPED)

1. Только `warehouse.warehouse_type == FULFILLMENT`
2. Для каждого item: `_update_stock(delta=-quantity, movement_type=OUTBOUND)`
3. Raises ValueError если `qty < 0` (недостаточно остатков)
4. `shipment.status = SHIPPED`, `shipment.shipped_date = utcnow()`

## При Cancel Shipment (SHIPPED → CANCELLED)

1. Для каждого item: `_update_stock(delta=+quantity, movement_type=OUTBOUND_CANCEL)`
2. `shipment.status = CANCELLED`

## При Update Accepted Receipt (пересчёт actual_qty)

1. Рассчитать `delta = new_actual_qty - old_actual_qty` для каждого item
2. Если `delta != 0`: `_update_stock(delta=delta, movement_type=INBOUND_EDIT)`

## Важные бизнес-правила

- **_update_stock** — ЕДИНСТВЕННАЯ точка изменения остатков. НИКОГДА не менять WarehouseStock напрямую
- **Неотрицательность**: stock.quantity не может быть < 0 (enforce в _update_stock)
- **Отгрузка** только с FULFILLMENT складов
- **StockMovement** — append-only, НЕ удалять, НЕ редактировать
- **WarehouseStock** — materialized view, всегда = SUM(StockMovement.quantity)
- **in_transit** — информационное поле, обновляется при send/complete transfer
- **cost_price** — ручной ввод на WarehouseStock

## WB FBO Поставки

### Модели (models/wb_fbo.py):
- `WbFboSupply` — поставка из WB API, UNIQUE(project_id, wb_supply_id)
- `WbFboSupplyItem` — позиции (заказы) в поставке

### Статусы FBO (read-only из WB)
| WB статус | Отображение | Финальный? |
|-----------|-------------|------------|
| ACTIVE | Запланирована | Нет |
| ON_DELIVERY | В пути | Нет |
| IN_PROGRESS | Разгрузка разрешена | Нет |
| ACCEPTED | Принята | Да |
| CANCELLED | Отменена | Да |

### Связь FBO ↔ Отгрузка
- Пользователь вручную связывает через UI (link/unlink)
- `wb_fbo_supplies.outbound_shipment_id` → FK на outbound_shipments
- `outbound_shipments.wb_supply_id` → строка с ID поставки WB
- При ACCEPTED: автоматически outbound_shipment → DELIVERED (если SHIPPED)
- При ACCEPTED: автоматически assembly_request → DELIVERED (если SHIPPED)

### WB API (Marketplace)
- `GET /api/v3/supplies` — список поставок
- `GET /api/v3/supplies/{id}/orders` — позиции
- `GET /api/v3/supplies/{id}` — детали одной поставки
- Base URL: `https://marketplace-api.wildberries.ru`
- Авторизация: IntegrationKey service="wb"

### Синхронизация
- **Автоматическая:** каждый 1 час (scheduler job)
- **Ручная полная:** POST /sync — все поставки + async enrichment
- **Ручная статусов:** POST /sync-statuses — только статусы активных
- Логируется в SyncLog (sync_type="fbo_supplies")

## Единые остатки (Unified Stock)

Объединённый вид: свои склады + WB + в пути (SHIPPED сборки).

### Endpoint: `GET /warehouse/stock/unified?group_by={mode}&brand={name}`

**Функция:** `warehouse_stock_engine.get_unified_stock_summary(db, project_id, group_by, brand=None)`

**Параметры:**
- `group_by`: sku (default) | brand | subject | imt | tag | abc
- `brand`: опциональный фильтр по бренду (max 200 символов) — применяется после агрегации

**Данные из:**
1. `WarehouseStock` — свои склады (quantity)
2. `WbWarehouseStock` — WB склады (quantity_full, включая к/от клиента)
3. `AssemblyRequest` items status=SHIPPED — в пути к WB
4. `AssemblyRequest` items PENDING→VEHICLE_ASSIGNED — зарезервировано (на нашем складе)
5. `WbFinanceRow` — реализация, профит, sale_qty за period (rolling trend window)
6. `CostOrderItem` → `load_avg_costs()` — средняя себестоимость
7. `wb_funnel_daily.adv_sum` — рекламные расходы за тот же период

**Группировки:**
- `brand` — двухуровневая: бренд → категории → артикулы (children)
- `subject/imt/tag` — одноуровневая: группа → артикулы (children)
- `abc` — per-SKU с A/B/C бейджем по avg_daily_revenue

**Фронт:** 4 режима отображения — шт / себестоимость / реализация / прибыль. Плюс фильтры по бренду и `stock_days` (max дней остатков до включения позиции).

### БДР parity (фикс 2026-04-15)
`_build_finance_query` зеркалирует фильтры `services/wb_bdr_helpers.build_bdr_aggregate_sql` — иначе Unified Stock и БДР показывают разную реализацию для одного периода:
- `COALESCE(sale_dt, rr_dt) BETWEEN cutoff AND today` ИЛИ `sale_dt/rr_dt IS NULL AND date_from >= cutoff AND date_to <= today`
- Исключает `LOWER(sa_name) = 'неопознанный товар'` — как БДР
- `sale_qty`/`ret_qty` фильтруются по `supplier_oper_name IN ('Продажа','Возврат')` — компенсации/ре-начисления не считаются продажами
- `wb_funnel_daily` ограничен сверху `today` (раньше был только нижний предел cutoff → «убегал» вперёд)

При любых изменениях `_build_finance_query`/`_compute_period_metrics` в `warehouse_stock_engine.py` — прогнать `tests/test_warehouse_unified_stock_bdr_parity.py`.

### Tax formula (фикс 2026-04-15)
`_compute_tax_and_profit` — pure-function расчёта налога и прибыли, зеркалирующая `bdr_enrichment.apply_tax_article`. Принимает `tax_info` dict от `load_tax_settings` и поддерживает регим `usn_income_expense_vat` (с опцией `cost_as_expense`), иначе USN считается на net income (income − НДС). Регим берётся из `project_settings` — не захардкожен.

### Trend window (фикс 2026-04-15)
`_compute_trend_cutoffs(today)` якорит rolling окна 7/14/30 дней к **вчерашнему** дню, а не сегодняшнему — чтобы неполный текущий день не размывал тренды. На фронте период показан в UI (раньше пользователь видел цифру без указания от какого дня).

### Общие правила
- `get_unified_stock_summary()` принимает `today` и прокидывает его во все под-запросы → один запрос, одна «сегодняшняя» точка.
- Tax regime / rate меняется в `project_settings` → инвалидировать кэш отчётов (tax_service → `invalidate_project_reports`).
- Helpers покрыты `tests/test_warehouse_stock_engine_helpers.py` (pure-function) — сохраняй их без DB чтобы не замедлять suite.

## WB остатки (отдельная система)

`WbWarehouseStock` — read-only данные из WB API.
Не интегрирован с локальным WarehouseStock. Синхронизация: `warehouse_stock_service.sync_warehouse_stocks()`.
Используется для аналитики (compute_need) и единых остатков.

## Файлы модуля

| Файл | Назначение |
|------|-----------|
| models/warehouse.py | ORM: 10 моделей (Warehouse, Receipt, Shipment, Transfer, Stock, Movement, Adjustment) |
| models/wb_fbo.py | ORM: WbFboSupply + Item |
| schemas/warehouse.py | Pydantic: Create/Update/Schema для складских сущностей |
| schemas/wb_fbo.py | Pydantic: FBO supply schemas |
| schemas/assembly.py | Pydantic: Assembly schemas |
| services/warehouse_service.py | Re-export (59 строк) |
| services/warehouse_crud.py | CRUD складов (241 строка) |
| services/warehouse_inbound.py | Приёмка (254 строки) |
| services/warehouse_outbound.py | Отгрузка + перемещения (365 строк) |
| services/warehouse_stock_engine.py | Движения + остатки + единые остатки (~760 строк) |
| services/fbo_supply_service.py | FBO синхронизация + авто-доставка (882 строки — нужен рефакторинг) |
| services/warehouse_stock_service.py | WB остатки sync + compute_need (852 строки — нужен рефакторинг) |
| services/warehouse_need_service.py | Расчёт потребности в товарах (get_warehouse_need, compute_need) |
| routers/warehouse.py | 21 endpoint: склады, stock, receipt, shipment, transfer, FBO |
| routers/assembly.py | 14 endpoints: заявки на сборку (см. DOMAIN_ASSEMBLY.md) |

## Связанные домены

- **Assembly** → `DOMAIN_ASSEMBLY.md` — заявки на сборку, создают OutboundShipment при SHIPPED
- **Cost** → `DOMAIN_COST.md` — cost_order_id в InboundReceipt (связь с заказом себестоимости)
- **WB** → `DOMAIN_WB.md` — FBO sync, WB warehouse stock

## Тесты
- `tests/test_fbo_supply_service.py` — unit + API тесты FBO
- Helpers (parse datetime), schemas, enums, models
- List (search/filter/sort/pagination)
- Items, link/unlink
- Sync с мокированным WB API клиентом
