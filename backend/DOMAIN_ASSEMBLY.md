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
                                                       ↓ (rollback)
CANCELLED ← (любой статус)                         READY
```

Строго последовательные. Пропуск запрещён.

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

## Файлы модуля

| Файл | Назначение |
|------|-----------|
| models/assembly.py | ORM: AssemblyRequest + Item |
| schemas/assembly.py | Pydantic Request/Response |
| services/assembly_service.py | Бизнес-логика |
| routers/assembly.py | 12 HTTP endpoints |

## Полное ТЗ и UX

- ТЗ: `docs/tz_assembly_logistics.md` (или в artifacts)
- UX: `docs/ux_plan_assembly_logistics.md` (или в artifacts)
