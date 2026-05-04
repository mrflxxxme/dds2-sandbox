# DOMAIN: Supply Chain (Поставки)

## Описание
Модуль управления цепочкой поставок: Фабричный заказ → Машина → Таможня → Склад.

## Ключевые сущности

### FactoryOrder (factory_orders)
Заказ на фабрику. 1 заказ = 1 фабрика.
- `order_number` — уникальный номер в рамках проекта
- `factory_name` — название фабрики
- Items: позиции с barcode, qty, price_cny. **SoftDeleteMixin** — фильтровать `is_deleted == False`
- `assigned_qty` на item — сколько уже распределено по машинам
- При удалении item → `item.soft_delete()` (не `db.delete()`)

### Vehicle = CostOrder (cost_orders) — расширен
Поля машины (sc15): `vehicle_name` (человекочитаемое имя, редактируется), `plate_number` (гос. номер), `order_no` — неизменяемый ID (используется в FK `cost_order_items.order_no` и в URL).

Поля override на CostOrderItem (sc16): `box_size_override`, `pcs_per_box_override` — per-vehicle переопределение габаритов коробки и шт/кор. Применяется при фактическом приходе (упаковка отличается от плана фабричного заказа). Не меняет фабричный заказ.

Машина (транспортное средство). НЕ новая таблица — расширены cost_orders:
- `status` — VehicleStatus: FORMING → IN_TRANSIT → CUSTOMS → DELIVERED
- `target_warehouse_id` — FK на warehouses, куда едет машина
- `inbound_receipt_id` — FK на inbound_receipts, создаётся при DELIVERED
- `factory_order_item_id` на CostOrderItem — связь с позицией фабричного заказа

### Enum VehicleStatus
FORMING | IN_TRANSIT | CUSTOMS | DISPATCHED | DELIVERED

### Enum FactoryOrderStatus
FORMING | DISTRIBUTED | CLOSED
- `FORMING` — не всё распределено по машинам (часть `assigned_qty < qty`)
- `DISTRIBUTED` — все позиции полностью распределены (`assigned_qty == qty` для всех items)
- `CLOSED` — все машины с позициями этого заказа имеют статус ≥ SHIPPED
- Legacy `READY` удалён (sc14) — сбивал операторов («Готов» при 1% распределении)

## Поток данных
1. Создаётся FactoryOrder с items (qty, barcode, price_cny)
2. Items распределяются по машинам (split_to_vehicles):
   - Создаётся/находится CostOrder по order_no
   - Создаётся CostOrderItem с factory_order_item_id
   - Обновляется assigned_qty на FactoryOrderItem
3. Машина меняет статусы: FORMING → IN_TRANSIT → CUSTOMS → DELIVERED
4. При DELIVERED + target_warehouse_id → auto-create InboundReceipt

## Автопересчёт статусов FactoryOrder (фикс 2026-04-15)
`factory_orders.refresh_factory_order_statuses(db, project_id, factory_order_ids)` — единая точка, которая симметрично двигает FactoryOrder.status между FORMING ↔ DISTRIBUTED на основе фактического `assigned_qty`. **CLOSED не откатывается** (terminal state).

Вызывается **после** каждой мутации со стороны машины — не только `split_to_vehicles`:
- `vehicle_delivery.add_items_to_vehicle` — добавили в машину → возможно DISTRIBUTED
- `vehicle_delivery.remove_item_from_vehicle` — вернули qty заказу → возможно откат в FORMING
- `vehicle_delivery.clear_vehicle_items` / `delete_vehicle` — массовый возврат
- `factory_orders.split_to_vehicles` — первичное распределение

Ручной `update_factory_order_status` **отвергает READY** (422) — миграция sc14 удалила значение из enum.

## Расчёт себестоимости и автопересчёт
**НЕ ТРОГАТЬ** — `services/cost/items.py` работает с CostOrder/CostOrderItem как раньше.
Новые поля (status, factory_order_item_id) не влияют на расчёт.

### Автопересчёт при добавлении позиций
При split_to_vehicles и add_items_to_vehicle автоматически вызывается `recalculate_order_items(db, project_id, order_no)` для каждой затронутой машины. Это пересчитывает пошлину, НДС и логистику на позициях.

Места вызова:
- `factory_orders.split_to_vehicles` — после commit, для каждой vehicles_used
- `vehicle_delivery.add_items_to_vehicle` — после commit, для конкретной машины
- `vehicle_delivery.dispatch_vehicle` — при DISPATCHED (отгрузке)
- `vehicle_delivery.recalculate_vehicle_costs` — ручной пересчёт одной машины
- `vehicle_delivery.recalculate_all_vehicles` — массовый пересчёт всех машин проекта

## Gotchas
- CostOrder.order_no UNIQUE — при split_to_vehicles проверяем is_deleted тоже
- assigned_qty + new_qty <= qty — валидация в split_to_vehicles
- CostOrder.status=NULL для НЕ-vehicle заказов (sc07 почистила, default убран)
- InboundReceipt создаётся через warehouse_stock_engine._next_number
- Barcode может не быть в Nomenclature — skipped с warning в логах
- Таможня (customs_dt) привязывается через dt_number отдельно (не через статус)

## Формирование машины (со стороны машины)
1. Создать машину (POST /vehicles) с номером, типом транспорта, курсами
2. Добавить товар из заказов (POST /vehicles/{no}/items) — пикер доступных позиций
3. Удалить позицию (DELETE /vehicles/{no}/items/{id}) — восстанавливает assigned_qty
4. Удалить машину (DELETE /vehicles/{no}) — только FORMING, soft-delete + restore assigned_qty
5. Доступные позиции (GET /vehicles/available-items) — FactoryOrderItems с remaining_qty > 0
6. Расчёт загрузки — box_size из FactoryOrderItem + CONTAINERS из container-loader

### Синхронизация цен (фикс 2026-04-17)
Цена в `CostOrderItem.price_cny` — снимок на момент добавления в машину (копируется из `FactoryOrderItem.price_cny`). Связь односторонняя: обновление цены в заказе после добавления **не каскадирует** в машины.

Два инструмента:
1. **Ресинк из заказа в машину** — кнопка «🔄 Пересинхр. цены» на странице машины. Preview показывает расхождения (qty × old→new, Δ сумма), apply обновляет `CostOrderItem.price_cny` + `recalculate_order_items` + `invalidate_project_reports`. Работает для любого статуса (для не-FORMING — предупреждение о пересчёте БДР).
2. **Переписать в заказе из paste** — при добавлении товара через paste обязательна колонка Цена ¥. Если введённая цена ≠ `FactoryOrderItem.price_cny`, модалка предлагает: «использовать цены заказа» (paste игнорируется) ИЛИ «переписать в заказе и добавить» (bulk-price endpoint → FOI обновляется → history `price_updated` → новые CostOrderItem создаются с уже новой ценой). Не трогает `CostOrderItem` других машин.

### Редактирование позиций машины (sc17, фикс 2026-05-04)
До sc17 PATCH `/vehicles/{order_no}/items/{id}` тихо рассинхронизировал `CostOrderItem.qty` с `FactoryOrderItem.assigned_qty` — превышение плана не валидировалось, фабричный заказ оставался в прежнем `qty`. Прецедент: машина 16.04, барод 2049448537820, qty 24→32, фабричный заказ #16 не вырос (8 шт «потеряны» для аналитики).

**Решение:**
- Единая utility `_adjust_assigned_qty(db, fo_item, delta, mode, user_name, *, cost_order_no)` в `services/supply_chain/factory_orders.py` — все точки изменения qty (`update_vehicle_item`, `add_items_to_vehicle`, `remove_item_from_vehicle`, `clear_all_vehicle_items`, `delete_vehicle`) идут через неё.
- `mode="strict"` (default для PATCH): превышение `available = fo.qty - fo.assigned_qty` → `FactoryQtyExceeded` → роутер маппит в 422 со structured detail (`fo_id, fo_number, foi_id, barcode, subject, fo_qty, fo_assigned, available, attempted_delta, in_mix_group, mix_group_id`). Frontend парсит → `DriftConfirmRow` под строкой qty с двумя кнопками («Расширить план» / «Откатить ввод»).
- `mode="extend_plan"`: расширяет `FactoryOrderItem.qty` на `(delta - available)`, пишет `FactoryOrderHistory(event_type="qty_extended_from_vehicle", changed_by=user_name)`. После commit вызывает `refresh_factory_order_statuses` (статус заказа может перейти `PARTIAL → DISTRIBUTED` если plan догнал assigned).
- `delta == 0` без override-флагов → ранний return `{noop: true}` без commit/recalc.
- Уменьшение qty (`delta < 0`) уменьшает `assigned_qty` с clamp на 0 (раньше тоже было багом — не уменьшалось).

**`qty_drift` в GET `/vehicles/{order_no}`:**
- Per-row `qty_drift = max(0, cost_item.qty - max(0, fo_qty - other_vehicles_qty))` — показывает по этой позиции «лишних» штук поверх плана.
- `sum_by_foi` — единый batch query (один SELECT GROUP BY на машину, не N+1).
- Frontend: оранжевая точка в ячейке qty при `qty_drift > 0` (existing рассинхрон). Click → поповер с `fo_number, fo_qty, fo_assigned`.
- Кнопка «Изменить статус» disabled при `pendingDriftCount > 0` + tooltip.

### Кросс-заказный поиск (paste mode)
При вставке баркодов из буфера — поиск идёт по ВСЕМ фабричным заказам (не только выбранному).
- `allItemMap` строится из всех AvailableItemGroups (FIFO: первый заказ приоритетнее)
- Колонка "Заказ" показывает источник каждой позиции
- Кнопка "Добавить найденные (N)" активна даже если часть баркодов не найдена
- После добавления ненайденные строки остаются в таблице

## API Endpoints
Prefix: `/api/v1/supply-chain`

| Method | Path | Описание |
|--------|------|----------|
| GET | /factory-orders | Список фабричных заказов |
| GET | /factory-orders/{id} | Детали заказа |
| POST | /factory-orders | Создать заказ |
| PUT | /factory-orders/{id} | Обновить заказ |
| DELETE | /factory-orders/{id} | Мягкое удаление |
| POST | /factory-orders/{id}/items | Добавить позиции |
| PUT | /factory-orders/items/bulk-price | Массовое обновление `FactoryOrderItem.price_cny` + history `price_updated` |
| POST | /factory-orders/{id}/split-to-vehicles | Разбить на машины |
| GET | /vehicles | Список машин с items и агрегацией |
| GET | /vehicles/{order_no} | Детали машины |
| POST | /vehicles | Создать машину. `order_no` опционален — если не задан, генерируется `V-NNNN` (sc15/sc16) |
| PUT | /vehicles/{order_no}/status | Изменить статус машины |
| POST | /vehicles/{order_no}/items | Добавить товар в машину. Принимает `box_size_override`, `pcs_per_box_override` — per-vehicle override, не меняет план фабричного заказа (sc16) |
| PATCH | /vehicles/{order_no}/items/{id} | Обновить qty позиции. Синхронизирует `FactoryOrderItem.assigned_qty` с `CostOrderItem.qty` (sc17). Принимает `mode: "strict"\|"extend_plan"` (default `strict`) — strict при превышении плана возвращает 422 structured, extend_plan расширяет `FactoryOrderItem.qty` + history `qty_extended_from_vehicle` |
| GET | /vehicles/{order_no}/price-resync/preview | Превью расхождений цен `CostOrderItem.price_cny` vs `FactoryOrderItem.price_cny` |
| POST | /vehicles/{order_no}/price-resync | Синк цен в CostOrderItem из связанного FactoryOrderItem + recalc + invalidate reports |
| DELETE | /vehicles/{order_no} | Удалить машину (только FORMING) |
| DELETE | /vehicles/{order_no}/items/{id} | Удалить товар из машины |
| GET | /vehicles/available-items | Доступные позиции для добавления |
| POST | /vehicles/recalc-all | Пересчёт себестоимости ВСЕХ машин |
| POST | /vehicles/{order_no}/recalc | Пересчёт себестоимости одной машины |
| GET | /overview | Сводка по supply chain |
| GET | /suppliers | Список поставщиков |
| POST | /suppliers | Создать поставщика |
| PUT | /suppliers/{id} | Обновить поставщика |
| DELETE | /suppliers/{id} | Мягкое удаление |
| GET | /suppliers/{id}/catalog | Ассортимент поставщика (группировка по subject, агрегация по barcode, кэш 300с) |
| GET | /suppliers/{id}/shipment-matrix | Отгрузочная карта: плоская таблица позиций × машин, с `really_shipped_qty` и `latest_order_date` |

### Отгрузочная карта (shipment_matrix, фикс 2026-04-15)
`supplier_catalog.build_shipment_matrix(...)` возвращает `ShipmentMatrixSummary` + flat items (без группировки по subject — фронт фильтрует сам).

Ключевые поля `ShipmentMatrixItem`:
- `shipped_qty` — qty разложенное по машинам любых статусов (включая FORMING)
- `really_shipped_qty` — qty **только** по машинам со статусом ≥ SHIPPED (константа `_REALLY_SHIPPED_STATUSES`). Решает путаницу «разложено в корзинку ≠ реально уехало с фабрики»
- `latest_order_date` — max `fo.order_date` по всем FactoryOrder-ам этого barcode; фронт использует для color priority bar (0% и >30 дней = красный, 0% но свежий = серый)
- `vehicle_allocations: dict[order_no, qty]` — колонки таблицы

`ShipmentMatrixSummary` дублирует `shipped_qty` и `really_shipped_qty` на уровне totals — для KPI-карточек.

## Файлы
- `models/supply_chain.py` — FactoryOrder, FactoryOrderItem, Supplier
- `models/cost.py` — CostOrder (extended), CostOrderItem (extended)
- `models/enums.py` — VehicleStatus
- `schemas/supply_chain.py` — все схемы, включая SupplierCatalog*
- `services/supply_chain/factory_orders.py` — CRUD + split
- `services/supply_chain/vehicle_delivery.py` — статусы + auto-receipt
- `services/supply_chain/supplier_catalog.py` — агрегация ассортимента поставщика (группировка по subject, агрегация по barcode)
- `services/supply_chain/supplier_service.py` — CRUD поставщиков
- `services/warehouse_inbound.py` — auto-DELIVERED при accept_receipt (инвалидирует кэш supplier_catalog)
- `routers/supply_chain.py` — API endpoints
- `migrations/versions/sc01_add_supply_chain.py` — основная миграция
- `migrations/versions/sc05_fix_missing_columns.py` — changed_by/changed_at в vehicle_status_history
- `migrations/versions/sc06_add_dispatched_status.py` — DISPATCHED в enum VehicleStatus
- `migrations/versions/sc07_cleanup_vehicle_status.py` — обнуление status у старых (не-vehicle) cost_orders
- `migrations/versions/sc13_fix_factory_order_statuses.py` — пересчёт FactoryOrder.status по фактическому assigned_qty (с записью в factory_order_history)
- `migrations/versions/sc14_drop_factory_order_ready.py` — удаление READY из enum FactoryOrderStatus (данные → FORMING/DISTRIBUTED)

### Кэш supplier_catalog
- Префикс: `supply_chain:supplier_catalog:project_id={pid}:supplier_id={sid}`
- TTL: 300 сек
- Инвалидация (по `project_id`) при любой мутации, влияющей на ассортимент:
  - `factory_orders.py`: create/update/delete, add_items, split_to_vehicles, update_item, delete_item
  - `vehicle_delivery.py`: add_items_to_vehicle, remove_item_from_vehicle, delete_vehicle, update_vehicle_status
  - `warehouse_inbound.py`: accept_receipt (если DISPATCHED → DELIVERED)

## Frontend
- `types/api.ts` — FactoryOrder*, VehicleStatus, SupplyChainOverview, SplitItem, SupplierCatalog*, ShipmentMatrix*, SkuOrderHistoryEntry
- `lib/api/supply-chain.ts` — API клиент (включая `getSupplierCatalog`, `getShipmentMatrix`)
- `app/(main)/p/[slug]/supply-chain/page.tsx` — страница: «📊 Обзор» (merged с Поставщиками: KPI + машины по статусам + карточки поставщиков), VehiclesTab (multi-select pill-фильтры по статусам со счётчиками), SupplierCatalogView, ShipmentMatrixView (плоская таблица с фильтром/сортировкой/sticky). Legacy `?tab=overview` → redirect на `suppliers`
- `app/(main)/p/[slug]/supply-chain/i18n.tsx` — переводы RU/ZH (catalog_*, shipment_*)
