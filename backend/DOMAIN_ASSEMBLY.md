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
- `PackageType` — `BOX | MONOPALLET | SUPERSAFE` (см. `models/assembly.py`). Один
  AssemblyRequest = одна транспортная единица WB = один `package_type`. Тип
  определяется через `POST /warehouse/acceptance-check` (см. `DOMAIN_WAREHOUSE.md`).
  При `commit_draft` строки группируются по `(source_ff, target_wb, package_type)` —
  если для одного склада нужны и короб, и моно — это две заявки.

## Статусная модель

```
IN_PROGRESS → READY → VEHICLE_ASSIGNED → SHIPPED
     ↑          ↓          ↓ (rollback)       ↓ (rollback)
     └──────────┘        READY             READY
CANCELLED ← (любой статус)
```

Строго последовательные; пропуск запрещён. Допустимые откаты:
- `READY → IN_PROGRESS` — возврат в сборку (через `start_assembly`); сбрасывается `actual_ready_date`
- `VEHICLE_ASSIGNED → READY` — отмена назначения машины (`unassign_vehicle`)
- `SHIPPED → READY` — откат отгрузки (через cancel + повторная сборка)

**PENDING (legacy):** старый промежуточный статус «Ожидает сборки» убран из жизненного цикла
2026-04-29 (миграция `as02_pending_to_in_progress`). Новые заявки создаются сразу в `IN_PROGRESS`.
В коде PENDING остаётся в enum для совместимости со `status_history`, но активных заявок в этом
статусе быть не должно. `start_assembly` идемпотентен (повторный клик «Начать сборку» в IN_PROGRESS = no-op).

## Валидация остатков при создании / изменении

При `create_assembly_request`, `update_assembly_request` (для PENDING/IN_PROGRESS) и `start_assembly`
(legacy PENDING) проверяется **доступный** остаток:

```
available = warehouse_stock.quantity − reserved_by_other_active_requests
```

`reserved_by_other_active_requests` — сумма позиций по этому товару в активных заявках
(`PENDING/IN_PROGRESS/READY/VEHICLE_ASSIGNED`), исключая текущую заявку. Если заявка просит больше
доступного — `ValueError("Недостаточно доступных остатков...")` с детализацией по баркодам:
need / available / stock / reserved.

Реализация: `_validate_available_for_assembly()` в `backend/services/assembly/crud.py`.

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
| models/assembly.py | ORM: AssemblyRequest + Item + StatusHistory + AssemblyDraft |
| schemas/assembly.py | Pydantic: CRUD + LogisticsAnalytics DTOs |
| schemas/assembly_draft.py | Pydantic для AssemblyDraft (Distribution / Row / Create / Update / Read / CommitResponse) |
| services/assembly_service.py | Бизнес-логика + аналитика |
| services/assembly_draft_service.py | CRUD черновика + commit_draft (валидация, pro-rata, NxAssemblyRequest, atomic rollback) |
| routers/assembly.py | 14 HTTP endpoints (CRUD + workflow + analytics) |
| routers/assembly_drafts.py | 6 endpoints под /api/v1/assembly/drafts (list/get/create/update/delete/commit) |

## AssemblyDraft (распределение N×M)

Черновик распределения с экрана «Потребность по складам» → «Создать сборку». Юзер выбирает чекбоксами артикулы, попадает на матрицу `ФФ-источники × WB-целевые`, редактирует кол-ва, балансирует Σ src ↔ Σ tgt per row, потом «Создать сборки» создаёт N `AssemblyRequest` (по одной на каждую уникальную пару `(source_ff, target_wb)` с qty>0).

`distribution` JSONB:
```jsonc
{
  "source_warehouse_ids": [int, ...],
  "target_warehouse_names": ["Электросталь", ...],
  "rows": [
    { "nm_id": int, "barcode": str, "vendor_code": str,
      "src": {"<warehouse_id>": qty, ...},
      "tgt": {"<wb_warehouse_name>": qty, ...} }
  ],
  "pallets_count": int,
  "pallet_weight_kg": float,
  "estimated_ready_date": "YYYY-MM-DD" | null
}
```

`commit_draft`:
- per-row валидация `Σ src == Σ tgt > 0`, иначе 400
- pro-rata распределение в пары (largest-remainder для целочисленности)
- создаёт `AssemblyRequest(status=IN_PROGRESS, wb_warehouse_name_manual=<target>, wb_fbo_supply_id=None)` + items
- atomic: исключение → rollback всех созданных, draft остаётся
- успех → soft-delete draft

## Frontend

| Файл | Назначение |
|------|-----------|
| warehouse/assembly/page.tsx | Список заявок + блок «Незавершённые черновики» + подсветка `?just_created=ids` 3 сек |
| warehouse/assembly/new/page.tsx | Создание одной сборки вручную (Ctrl+V paste) |
| warehouse/assembly/[id]/page.tsx | Детали + inline edit |
| warehouse/assembly/[id]/edit/page.tsx | Форма редактирования |
| warehouse/assembly/distribute/page.tsx | Двух-сторонняя матрица ФФ × WB; live-валидация балансов; «↺ Авто-баланс» (greedy src + pro-rata tgt); autosave 5s; commit → редирект на сборку или список с `just_created` |
| warehouse/logistics/page.tsx | Лист логиста + аналитика (KPI, график, матрица) |

## Полное ТЗ и UX

- ТЗ: `docs/tz_assembly_logistics.md` (или в artifacts)
- UX: `docs/ux_plan_assembly_logistics.md` (или в artifacts)
