# DOMAIN_SUPPLY_CHAIN — Цепочка поставок (Фабрика → Машина → Таможня → Склад)

Поток: фабричный заказ → распределение по машинам → транзит/таможня → приёмка на склад.

## Таблицы
| Модель | Назначение | Ключ / constraint |
|--------|------------|-------------------|
| `FactoryOrder` (`factory_orders`) | Заказ на фабрику, 1 заказ = 1 фабрика | `order_number` уникален в проекте; SoftDeleteMixin |
| `FactoryOrderItem` | Позиция заказа: barcode, qty, `price_cny`, `assigned_qty` | SoftDeleteMixin |
| `Supplier` | Поставщик | SoftDeleteMixin |
| `CostOrder` (`cost_orders`) = «Машина» | НЕ новая таблица — расширенный CostOrder | `order_no` UNIQUE (учитывать `is_deleted`) |
| `CostOrderItem` | Позиция машины | `factory_order_item_id` — связь с позицией заказа |

Машина (Vehicle) — это `CostOrder` с дополнительными полями: `status` (VehicleStatus), `vehicle_name` (редактируемое имя), `plate_number`, `target_warehouse_id` (FK warehouses), `inbound_receipt_id` (FK inbound_receipts, ставится при DELIVERED). `order_no` неизменяем (используется в FK и URL); при создании опционален — генерится `V-NNNN`. CostOrderItem машины имеет per-vehicle override габаритов: `box_size_override`, `pcs_per_box_override` (применяются при фактическом приходе, не меняют фабричный заказ).

**Enum `VehicleStatus`:** FORMING → IN_TRANSIT → CUSTOMS → DISPATCHED → DELIVERED.

**Enum `FactoryOrderStatus`:** FORMING (часть `assigned_qty < qty`) | DISTRIBUTED (все позиции полностью распределены) | CLOSED (terminal — все машины с позициями заказа ≥ SHIPPED).

## Бизнес-правила
- **Поток:** FactoryOrder с items → split по машинам (создаётся/находится CostOrder, CostOrderItem с `factory_order_item_id`, растёт `assigned_qty`) → машина двигает статусы → при DELIVERED + `target_warehouse_id` авто-создаётся InboundReceipt.
- **Валидация распределения:** `assigned_qty + new_qty <= qty` в `split_to_vehicles`.
- **Автопересчёт статуса заказа:** `refresh_factory_order_statuses()` — единая точка, симметрично двигает FactoryOrder.status FORMING ↔ DISTRIBUTED по фактическому `assigned_qty`. CLOSED не откатывается. Вызывается после **любой** мутации со стороны машины (add/remove items, clear, delete, split). Ручной `update_factory_order_status` отвергает legacy `READY` (422).
- **Автопересчёт себестоимости:** `recalculate_order_items()` (пошлина, НДС, логистика) вызывается автоматически при split, add_items, dispatch и ручном пересчёте. Поля машины (status, factory_order_item_id) на расчёт `services/cost/items.py` не влияют — он работает с CostOrder/CostOrderItem как раньше.
- **Редактирование qty позиции машины** (`PATCH /vehicles/{order_no}/items/{id}`): все точки изменения qty идут через `_adjust_assigned_qty()`, синхронизирующую `FactoryOrderItem.assigned_qty`. Режим `strict` (default): превышение плана → `FactoryQtyExceeded` → 422 со structured detail для UI-подтверждения. Режим `extend_plan`: расширяет `FactoryOrderItem.qty` + пишет `FactoryOrderHistory(qty_extended_from_vehicle)`. Уменьшение qty уменьшает `assigned_qty` с clamp на 0.
- **Цена — снимок:** `CostOrderItem.price_cny` копируется из `FactoryOrderItem.price_cny` при добавлении; связь односторонняя — изменение цены в заказе НЕ каскадирует в машины. Ресинк — отдельный endpoint (preview расхождений + apply с recalc и invalidate reports). При paste-добавлении с ценой ≠ плановой модалка предлагает «использовать цены заказа» или «переписать в заказе» (bulk-price).
- **Кросс-заказный paste:** при вставке баркодов поиск идёт по всем фабричным заказам проекта (FIFO — первый заказ приоритетнее), не только по выбранному.
- **Смена склада машины:** `target_warehouse_id` редактируем и после DISPATCHED, но `update_vehicle` синхронизирует связанную приёмку — приёмка `EXPECTED` «переезжает» (её `warehouse_id` обновляется), приёмка `ACCEPTED` → `ValueError` (товар уже в stock_movements).
- **Отгрузочная карта** (`/suppliers/{id}/shipment-matrix`): `shipped_qty` — qty по машинам любых статусов; `really_shipped_qty` — только по машинам ≥ SHIPPED (реально уехало с фабрики, не «разложено в корзину»).

## Зависимости
- `DOMAIN_COST` — CostOrder / CostOrderItem (расширены полями машины).
- `DOMAIN_WAREHOUSE` — InboundReceipt auto-create при DELIVERED через `warehouse_stock_engine._next_number`.
- `DOMAIN_REPORTS` — `invalidate_project_reports` при пересчётах.

## Грабли
- **Рассинхрон приёмки и склада машины** — раньше смена `target_warehouse_id` не двигала `inbound_receipts.warehouse_id`: приёмка оставалась на старом складе и не показывалась на странице нового. Recovery-скрипт: `scripts/fix_inbound_warehouse_drift.py`.
- **Тихий рассинхрон qty** — раньше PATCH позиции машины не валидировал превышение плана и не двигал `FactoryOrderItem`, штуки «терялись» для аналитики. Теперь — через `_adjust_assigned_qty` со strict-режимом.
- `CostOrder.status = NULL` для НЕ-vehicle заказов (default убран миграцией).
- Barcode может отсутствовать в Nomenclature — позиция skipped с warning в логах.
- Таможня (`customs_dt`) привязывается через `dt_number` отдельно, не через статус машины.
- Кэш `supplier_catalog` (TTL 300с, префикс `supply_chain:supplier_catalog:...`) инвалидируется по `project_id` при любой мутации ассортимента (factory_orders, vehicle_delivery, warehouse_inbound при DISPATCHED→DELIVERED).

## Файлы
- `models/supply_chain.py` — FactoryOrder, FactoryOrderItem, Supplier.
- `models/cost.py` — CostOrder / CostOrderItem (extended для машин).
- `models/enums.py` — VehicleStatus, FactoryOrderStatus.
- `schemas/supply_chain.py` — все схемы, включая SupplierCatalog*, ShipmentMatrix*.
- `services/supply_chain/factory_orders.py` — CRUD заказов, split, `_adjust_assigned_qty`, `refresh_factory_order_statuses`.
- `services/supply_chain/vehicle_delivery.py` — статусы машин, auto-receipt, ресинк цен.
- `services/supply_chain/supplier_catalog.py` — агрегация ассортимента и shipment_matrix.
- `services/supply_chain/supplier_service.py` — CRUD поставщиков.
- `services/warehouse_inbound.py` — auto-DELIVERED при accept_receipt.
- `routers/supply_chain.py` — HTTP endpoints (prefix `/api/v1/supply-chain`).
- `frontend-react/src/lib/api/supply-chain.ts` — API клиент.
- `frontend-react/src/app/(main)/p/[slug]/supply-chain/page.tsx` — UI (обзор, машины с pill-фильтрами, каталог, shipment-matrix).
