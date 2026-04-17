# DOMAIN_ASSEMBLY — Заявки на сборку + Лист логиста

## Назначение
Модуль управляет процессом сборки товаров на складах фулфилмента
для FBO-поставок WB. Это промежуточный слой между складом и отгрузкой.

## Связи с другими доменами

```
WbFboSupply ←(1:1)→ AssemblyRequest →(creates on SHIPPED)→ OutboundShipment
                            ↓
                     AssemblyRequestItem
                            ↓
                   _update_stock() при SHIPPED
```

## Ключевые зависимости

### Из warehouse_service.py (backend/services/warehouse_service.py):

```python
# L146 — Резолвит баркод → Nomenclature. Raises ValueError если не найден.
async def _resolve_barcode(db: AsyncSession, project_id: int, barcode: str) -> Nomenclature:

# L163 — Обновляет остатки + создаёт StockMovement (аудит).
# delta > 0 = приход, delta < 0 = расход. Raises ValueError при qty < 0.
async def _update_stock(
    db, project_id, warehouse_id, nomenclature_id, barcode,
    delta, movement_type, reference_type, reference_id=None, comment=None
) -> None:

# L122 — Генерирует автономер: ASM-1, ASM-2...
async def _next_number(db, project_id, prefix, model_class) -> str:

# L520 — Пример создания OutboundShipment (use as template).
async def create_shipment(db, project_id, warehouse_id, payload) -> OutboundShipment:

# L556 — Пример ship: проверка status, stock -= qty для каждого item.
async def ship_shipment(db, project_id, shipment_id) -> OutboundShipment:
```

### Модели:
- `OutboundShipment` → models/warehouse.py:142 (status, wb_supply_id, shipped_date)
- `OutboundShipmentItem` → models/warehouse.py:168 (shipment_id, nomenclature_id, barcode, qty)
- `WbFboSupply` → models/wb_fbo.py:40 (outbound_shipment_id, warehouse_name, wb_supply_id)
- `WbFboSupplyItem` → models/wb_fbo.py:85 (barcode, nm_id, quantity)
- `Warehouse` → models/warehouse.py:26 (warehouse_type: FULFILLMENT)
- `Nomenclature` → models/refs.py (barcode, project_id)

### Enums:
- `MovementType.OUTBOUND` — для списания stock при ship
- `OutboundStatus.SHIPPED` — статус создаваемого OutboundShipment
- `WarehouseType.FULFILLMENT` — тип склада (проверка при создании заявки)

## Статусная модель

```
PENDING → IN_PROGRESS → READY → VEHICLE_ASSIGNED → SHIPPED
              ↑           ↓           ↓ (rollback)       ↓ (rollback)
              └───────────┘         READY             READY
CANCELLED ← (любой статус)
```

Строго последовательные; пропуск запрещён. Допустимые откаты:
- `READY → IN_PROGRESS` — возврат в сборку (через `start_assembly`); сбрасывается `actual_ready_date`
- `VEHICLE_ASSIGNED → READY` — отмена назначения машины (`unassign_vehicle`)
- `SHIPPED → READY` — откат отгрузки (через cancel + повторная сборка)

## При Ship (VEHICLE_ASSIGNED → SHIPPED)

1. Валидация: warehouse_stock.quantity >= need для каждого item
2. _update_stock(delta=-qty, movement_type=OUTBOUND, reference_type="ASSEMBLY", reference_id=request.id)
3. Создать OutboundShipment(status=SHIPPED, wb_supply_id=fbo.wb_supply_id, shipped_date=today)
4. Создать OutboundShipmentItem для каждого item
5. WbFboSupply.outbound_shipment_id = shipment.id
6. assembly_request.outbound_shipment_id = shipment.id
7. assembly_request.shipped_at = utcnow()

## При Cancel SHIPPED (rollback)

1. _update_stock(delta=+qty, movement_type=OUTBOUND, reference_type="ASSEMBLY_CANCEL")
2. WbFboSupply.outbound_shipment_id = NULL
3. OutboundShipment.soft_delete()
4. assembly_request.outbound_shipment_id = NULL, shipped_at = NULL
5. Status → READY

## Аналитика логистики

Endpoint: `GET /warehouse/assembly/shipments/analytics`
- Сервис: `get_logistics_analytics(db, project_id, date_from, date_to, warehouse_ids, brands)`
- Кэш: `@cached(prefix="reports:logistics_analytics", ttl=300)`
- Возвращает: summary (total_cost, avg_cost_per_pallet, total_pallets, total_shipments), by_destination, by_route
- avg_cost = средняя стоимость за палету: `avg(pickup_cost / pallets_count)`
- Фильтры: период (shipped_at), склады, бренды
- date_to: `shipped_at < date_to + 1 day` (включает весь день)

## Редактирование полей

### До READY (PENDING, IN_PROGRESS):
- Items (позиции), FBO поставка, все scalar поля

### В любом статусе (кроме CANCELLED):
- pickup_cost, vehicle_info, vehicle_brand, driver_phone, pallets_count, pallet_weight_kg
- Inline edit на detail page через EditableInfoField

### Страница редактирования: `/assembly/[id]/edit`
- Полная форма: FBO, дата, палеты, комментарий, items
- Ctrl+V paste в таблицу items (как в приёмке)
- Кнопка обновления FBO из WB (sync + reload items)

## Файлы модуля

| Файл | Назначение |
|------|-----------|
| models/assembly.py | ORM: AssemblyRequest + Item + StatusHistory |
| schemas/assembly.py | Pydantic: CRUD + LogisticsAnalytics DTOs |
| services/assembly_service.py | Бизнес-логика + аналитика |
| routers/assembly.py | 14 HTTP endpoints (CRUD + workflow + analytics) |

## Frontend

| Файл | Назначение |
|------|-----------|
| warehouse/assembly/page.tsx | Список заявок |
| warehouse/assembly/new/page.tsx | Создание (Ctrl+V paste) |
| warehouse/assembly/[id]/page.tsx | Детали + inline edit |
| warehouse/assembly/[id]/edit/page.tsx | Форма редактирования |
| warehouse/logistics/page.tsx | Лист логиста + аналитика (KPI, график, матрица) |

## Полное ТЗ и UX

- ТЗ: `docs/tz_assembly_logistics.md` (или в artifacts)
- UX: `docs/ux_plan_assembly_logistics.md` (или в artifacts)
