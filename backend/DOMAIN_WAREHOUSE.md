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
- `MovementType` — INBOUND, INBOUND_CANCEL, OUTBOUND, OUTBOUND_CANCEL, TRANSFER_IN, TRANSFER_OUT, INBOUND_EDIT, ADJUSTMENT, DEFECT_MARK, DEFECT_RECEIVE, DEFECT_WRITEOFF, DEFECT_RECOVER, DEFECT_TRANSFER_OUT, DEFECT_TRANSFER_IN

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

### Брак (Defective Goods)
```
DEFECT_MARK:      qty -= delta, defect_qty += delta  (пометить годный → брак)
DEFECT_RECEIVE:   defect_qty += delta                 (приёмка брака извне)
DEFECT_WRITEOFF:  defect_qty -= delta                 (списание)
DEFECT_RECOVER:   qty += delta, defect_qty -= delta   (восстановление после ремонта)
DEFECT_TRANSFER:  через StockTransfer.is_defect=True, DRAFT → IN_TRANSIT → COMPLETED
                  source: defect_qty -= delta, DEFECT_TRANSFER_OUT
                  target: defect_qty += delta, DEFECT_TRANSFER_IN
                  target: defect_in_transit += delta (send) / -= delta (complete)
```

- `defect_quantity` — отдельный счётчик в WarehouseStock, НЕ учитывается в `available`
- `defect_in_transit` — аналог `in_transit` для бракованных перемещений
- Assembly validation использует `quantity` → брак автоматически исключён из отгрузок
- Все операции проходят через `_update_stock(defect_delta=...)` — один аудит-лог
- Сервис: `warehouse_defect.py` (mark, receive, writeoff, recover, get_defect_stock, get_defect_summary)

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

### Endpoint: `GET /warehouse/stock/unified?group_by={mode}&brand={name}&include_forecast={bool}`

**Функция:** `warehouse_stock_engine.get_unified_stock_summary(db, project_id, group_by, brand=None, include_forecast=False)`

**Параметры:**
- `group_by`: sku (default) | brand | subject | imt | tag | abc
- `brand`: опциональный фильтр по бренду (max 200 символов) — применяется после агрегации
- `include_forecast` (фикс 2026-04-15): `False` (default) = Факт, сходится с БДР копейка-в-копейку. `True` = «С прогнозом»: для товаров в пути (factory/vehicle) но ещё не на полке оценивает выручку по qty-weighted средней категории. **Узкий fallback** — только incoming без стока (залежавшийся сток больше не раздувает итоги)

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

**Фронт:** 4 режима отображения — шт / себестоимость / реализация / прибыль. Плюс фильтры по бренду, `stock_days` (max дней остатков до включения позиции) и Факт/Прогноз тоггл.

### Карточка «📦 Новинки» (фикс 2026-04-15)
Показывает товары без единой продажи за 60 дней (no_sale_window). Вычисляется в `get_unified_stock_summary` рядом с основной выборкой.

- **SKU count** — сколько уникальных позиций
- **Qty** — суммарный остаток в штуках
- **Frozen cost** — замороженная себестоимость (`sum(qty * avg_cost)`)
- **Potential revenue/profit** — qty-weighted per-unit из средних по категории (тот же `_load_category_averages` что и forecast)
- **Раскрытие** — По категории / По бренду → артикулы с баркодами

Category averages считаются one-shot в `get_unified_stock_summary` и переиспользуются: novelty KPI и forecast fallback идут от одних и тех же чисел — расхождение между вкладками гарантированно = 0.

### БДР parity (фикс 2026-04-15)
`_build_finance_query` зеркалирует фильтры `services/wb_bdr_helpers.build_bdr_aggregate_sql` — иначе Unified Stock и БДР показывают разную реализацию для одного периода:
- `COALESCE(sale_dt, rr_dt) BETWEEN cutoff AND today` ИЛИ `sale_dt/rr_dt IS NULL AND date_from >= cutoff AND date_to <= today`
- Исключает `LOWER(sa_name) = 'неопознанный товар'` — как БДР
- `sale_qty`/`ret_qty` фильтруются по `supplier_oper_name IN ('Продажа','Возврат')` — компенсации/ре-начисления не считаются продажами
- `wb_funnel_daily` ограничен сверху `today` (раньше был только нижний предел cutoff → «убегал» вперёд)

При любых изменениях `_build_finance_query`/`_compute_period_metrics` в `warehouse_stock_engine.py` — прогнать `tests/test_warehouse_unified_stock_bdr_parity.py`.

### Tax formula (фикс 2026-04-15)
`_compute_tax_and_profit` — pure-function расчёта налога и прибыли, зеркалирующая `bdr_enrichment.apply_tax_article`. Принимает `tax_info` dict от `load_tax_settings` и поддерживает регим `usn_income_expense_vat` (с опцией `cost_as_expense`), иначе USN считается на net income (income − НДС). Регим берётся из `project_settings` — не захардкожен.

### Tax cutoff parity (фикс 2026-04-15, вторая итерация)
`load_tax_settings(db, project_id, cutoff_X, anchor)` — **первый параметр (cutoff) это начало окна**, не anchor. Было `load_tax_settings(anchor, anchor)` → при плейсхолдерных рейтах текущего месяца Unified Stock считал маржу **без налога**: 35.94% vs БДР 26.69%. Стало `load_tax_settings(cutoff_X, anchor)` → 26.78% vs 26.69%, дельта 0.09 п.п. (остаточная разница — SKU распроданные в 0 без рефила, их нет в остатках by design).

Regression guard: `tests/test_warehouse_tax_cutoff_parity.py`. При любом изменении cutoff/anchor логики в `_compute_period_metrics` — прогнать тест. См. `KNOWN_PITFALLS.md P28`.

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

## WB Goods Returns (возвраты на ПВЗ)

`WbGoodsReturn` — отчёт «Возвраты и перемещение товаров» из WB Seller Analytics API
(`GET /api/v1/analytics/goods-return`). Хранится зеркально + линк на `InboundReceipt`
когда пользователь оформляет приёмку на физ.склад.

### Flow
```
WB API (раз в 30 мин, rate limit 1/min, max 31 день окно)
  → WbGoodsReturn.upsert(srid)                                     # sync_project_returns
  → user видит «Готовые к выдаче» на /p/<slug>/wb-returns
  → selects srids + warehouse + POST /wb-returns/create-receipt
  → create_receipt_from_returns:
       • валидирует warehouse (project_id, is_deleted, is_active — inactive → 400)
       • pg_advisory_xact_lock(ns, project_id) — сериализует выдачу номеров ВЗ-yymmdd-N
       • SELECT ... FOR UPDATE на WbGoodsReturn (project, srid, inbound_receipt_id IS NULL) —
         блокирует параллельный POST с пересекающимися srid
       • создаёт InboundReceipt(EXPECTED, is_defect=true, defect_reason="Возврат WB с ПВЗ"),
         номер ВЗ-yymmdd-N где дата — MSK (не UTC контейнера), N = MAX(suffix)+1
       • на каждую единицу — InboundReceiptItem(expected_qty=1, actual_qty=0)
         (если nomenclature по barcode нет — создаёт stub; если barcode пуст — пропускает item,
          линкует возврат, srid попадает в `skipped_srids` в ответе → UI warning)
       • выставляет WbGoodsReturn.inbound_receipt_id = receipt.id
  → склад подтверждает через стандартный Accept Receipt flow (warehouse_inbound.accept_receipt)
       → status → ACCEPTED → WarehouseStock.defect_quantity пополняется
```

### UI state (derived)
`classify_ui_state(ret, receipt_status)` — одна из:
- `expired` — inactive + expired_dt в прошлом + completed_dt = NULL
- `received` — InboundReceipt.status == ACCEPTED
- `picked_up_pending_receipt` — linked EXPECTED + completed_dt != NULL (забрали, ждём приёмку)
- `pickup_planned` — linked EXPECTED + completed_dt == NULL
- `picked_without_receipt` — completed_dt != NULL + нет линка (retro-приёмка нужна)
- `ready_for_pickup` — active + ready_to_return_dt != NULL + not completed
- `in_transit_to_pvz` — fallback для активных без ready_to_return_dt

### Endpoints (`/api/v1/wb-returns/*`)
| Метод | Путь | Назначение |
|-------|------|-----------|
| GET | `/` | список с фильтром `tab=pvz\|in_transit\|history\|all` + search + pvz_id + даты |
| GET | `/summary` | KPI-счётчики по 8 ui_state |
| GET | `/pvz-groups` | группировка «Tab 1» по `dst_office_id` |
| POST | `/create-receipt` | создать приёмку из набора srid |
| POST | `/sync` | ручной запуск sync (use date_from/date_to или дефолт 7 дней) |

### Scheduler job
`backend/scheduler/jobs/wb_goods_returns_sync.py::sync_all_projects_wb_returns`
— каждые 30 мин, интервал-trigger, SyncLog(service="wb", sync_type="goods_returns").

### Файлы
| Файл | Назначение |
|------|-----------|
| models/wb_returns.py | `WbGoodsReturn` (SoftDelete + TimestampMixin, UniqueConstraint project_id+srid) |
| schemas/wb_returns.py | Pydantic Out/Group/Summary/CreateIn/CreateOut/SyncIn/SyncOut/ListResponse |
| services/wb_returns_service.py | sync + list + summary + classify_ui_state + create_receipt_from_returns |
| routers/wb_returns.py | 5 endpoints, все через `Depends(get_current_project)` + `rate_limit_write` на мутациях |
| scheduler/jobs/wb_goods_returns_sync.py | периодический sync для всех project_id с WB ключом |
| integrations/wb_api.py | `WBApiClient.get_goods_returns(date_from, date_to)` |
| tests/test_wb_returns.py | unit тесты: sync upsert, tab filters, summary, classify_ui_state, create_receipt |

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
| services/warehouse_defect.py | Управление браком (mark, receive, writeoff, recover) |
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
