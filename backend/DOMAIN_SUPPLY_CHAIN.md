# DOMAIN: Supply Chain (Поставки)

## Описание
Модуль управления цепочкой поставок: Фабричный заказ → Машина → Таможня → Склад.

## Ключевые сущности

### FactoryOrder (factory_orders)
Заказ на фабрику. 1 заказ = 1 фабрика.
- `order_number` — уникальный номер в рамках проекта
- `factory_name` — название фабрики
- Items: позиции с barcode, qty, price_cny
- `assigned_qty` на item — сколько уже распределено по машинам

### Vehicle = CostOrder (cost_orders) — расширен
Машина (транспортное средство). НЕ новая таблица — расширены cost_orders:
- `status` — VehicleStatus: FORMING → IN_TRANSIT → CUSTOMS → DELIVERED
- `target_warehouse_id` — FK на warehouses, куда едет машина
- `inbound_receipt_id` — FK на inbound_receipts, создаётся при DELIVERED
- `factory_order_item_id` на CostOrderItem — связь с позицией фабричного заказа

### Enum VehicleStatus
FORMING | IN_TRANSIT | CUSTOMS | DISPATCHED | DELIVERED

## Поток данных
1. Создаётся FactoryOrder с items (qty, barcode, price_cny)
2. Items распределяются по машинам (split_to_vehicles):
   - Создаётся/находится CostOrder по order_no
   - Создаётся CostOrderItem с factory_order_item_id
   - Обновляется assigned_qty на FactoryOrderItem
3. Машина меняет статусы: FORMING → IN_TRANSIT → CUSTOMS → DELIVERED
4. При DELIVERED + target_warehouse_id → auto-create InboundReceipt

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
| POST | /factory-orders/{id}/split-to-vehicles | Разбить на машины |
| GET | /vehicles | Список машин с items и агрегацией |
| GET | /vehicles/{order_no} | Детали машины |
| POST | /vehicles | Создать машину |
| PUT | /vehicles/{order_no}/status | Изменить статус машины |
| POST | /vehicles/{order_no}/items | Добавить товар в машину |
| DELETE | /vehicles/{order_no} | Удалить машину (только FORMING) |
| DELETE | /vehicles/{order_no}/items/{id} | Удалить товар из машины |
| GET | /vehicles/available-items | Доступные позиции для добавления |
| POST | /vehicles/recalc-all | Пересчёт себестоимости ВСЕХ машин |
| POST | /vehicles/{order_no}/recalc | Пересчёт себестоимости одной машины |
| GET | /overview | Сводка по supply chain |

## Файлы
- `models/supply_chain.py` — FactoryOrder, FactoryOrderItem
- `models/cost.py` — CostOrder (extended), CostOrderItem (extended)
- `models/enums.py` — VehicleStatus
- `schemas/supply_chain.py` — все схемы
- `services/supply_chain/factory_orders.py` — CRUD + split
- `services/supply_chain/vehicle_delivery.py` — статусы + auto-receipt
- `routers/supply_chain.py` — API endpoints
- `migrations/versions/sc01_add_supply_chain.py` — основная миграция
- `migrations/versions/sc05_fix_missing_columns.py` — changed_by/changed_at в vehicle_status_history
- `migrations/versions/sc06_add_dispatched_status.py` — DISPATCHED в enum VehicleStatus
- `migrations/versions/sc07_cleanup_vehicle_status.py` — обнуление status у старых (не-vehicle) cost_orders

## Frontend
- `types/api.ts` — FactoryOrder*, VehicleStatus, SupplyChainOverview, SplitItem
- `lib/api/supply-chain.ts` — API клиент
- `app/(main)/p/[slug]/supply-chain/page.tsx` — страница с 3 табами
