# DOMAIN_ASSEMBLY — Заявки на сборку + Лист логиста

Промежуточный слой между складом фулфилмента и отгрузкой FBO-поставок WB.
`WbFboSupply` (1:1) → `AssemblyRequest` → при SHIPPED создаёт `OutboundShipment`.

## Таблицы
| Модель | Назначение | Ключ / constraint |
|--------|------------|-------------------|
| `AssemblyRequest` | Заявка на сборку (статус, FBO-привязка, vehicle/pallet поля) | `models/assembly.py` |
| `AssemblyRequestItem` | Позиция заявки (barcode, qty) | FK на request |
| `AssemblyStatusHistory` | История смен статуса | FK на request |
| `AssemblyDraft` | Черновик распределения N×M (`distribution` JSONB), SoftDelete | `models/assembly.py` |
| `OutboundShipment` / `OutboundShipmentItem` | Отгрузка, создаётся при SHIPPED | `models/warehouse.py` |

`PackageType` = `BOX | MONOPALLET | SUPERSAFE`. Один `AssemblyRequest` = одна транспортная единица WB = один `package_type`; тип определяется через `POST /warehouse/acceptance-check` (см. `DOMAIN_WAREHOUSE.md`).

## Бизнес-правила
- **Статусная модель:** `IN_PROGRESS → READY → VEHICLE_ASSIGNED → SHIPPED`. Строго последовательная, пропуск запрещён. Допустимые откаты: `READY → IN_PROGRESS` (`start_assembly`, сбрасывает `actual_ready_date`), `VEHICLE_ASSIGNED → READY` (`unassign_vehicle`), `SHIPPED → READY` (cancel). `CANCELLED` — из любого статуса.
- **PENDING — legacy:** остаётся в enum для совместимости со `status_history`, но активных заявок в нём быть не должно. Новые заявки создаются сразу в `IN_PROGRESS`. `start_assembly` идемпотентен (повторный клик в IN_PROGRESS = no-op).
- **Валидация остатков** при create/update (PENDING/IN_PROGRESS) и `start_assembly`: `available = warehouse_stock.quantity − reserved_by_other_active_requests`, где reserved — сумма позиций по этому товару в других активных заявках (PENDING/IN_PROGRESS/READY/VEHICLE_ASSIGNED). Нехватка → `ValueError` с детализацией по баркодам. См. `_validate_available_for_assembly()` в `services/assembly/crud.py`.
- **При Ship (VEHICLE_ASSIGNED → SHIPPED):** валидация stock ≥ need; списание stock (`OUTBOUND`, `reference_type="ASSEMBLY"`); создаётся `OutboundShipment(SHIPPED)` + items; проставляются взаимные `outbound_shipment_id` на request и `WbFboSupply`; `shipped_at = utcnow()`.
- **При Cancel SHIPPED:** возврат stock (`reference_type="ASSEMBLY_CANCEL"`); `WbFboSupply.outbound_shipment_id = NULL`; `OutboundShipment.soft_delete()`; статус → READY.
- **Редактирование:** items и FBO-поставка — только до READY. Поля логистики (`pickup_cost`, `vehicle_*`, `driver_phone`, `pallets_count`, `pallet_weight_kg`) — в любом статусе кроме CANCELLED (inline edit).
- **AssemblyDraft (N×M):** черновик с экрана «Потребность по складам». Матрица `ФФ-источники × WB-целевые`, юзер балансирует Σ src ↔ Σ tgt по каждой строке. `commit_draft`: per-row валидация `Σ src == Σ tgt > 0` (иначе 400); pro-rata распределение в пары (largest-remainder для целочисленности); создаёт N `AssemblyRequest(IN_PROGRESS)` — по одной на уникальную пару `(source_ff, target_wb)` с qty>0. Atomic: исключение → rollback всех созданных, draft остаётся; успех → soft-delete draft.

`distribution` JSONB: `source_warehouse_ids[]`, `target_warehouse_names[]`, `rows[]` (`nm_id`, `barcode`, `vendor_code`, `src{warehouse_id:qty}`, `tgt{wb_name:qty}`), `pallets_count`, `pallet_weight_kg`, `estimated_ready_date`.

## Зависимости
- `DOMAIN_WAREHOUSE` — `acceptance-check` определяет `package_type`; `_update_stock` / `_resolve_barcode` для движения остатков.
- `DOMAIN_REPORTS` — on-assembly заявки участвуют в Stock Forecast.
- WB API — данные FBO-поставок (`WbFboSupply`).

## Грабли
- При `commit_draft` строки группируются по `(source_ff, target_wb, package_type)` — если для одного склада нужны и короб, и моно, это две отдельные заявки.
- Аналитика логистики (`GET /warehouse/assembly/shipments/analytics`) кэшируется 300s; `date_to` фильтрует как `shipped_at < date_to + 1 day` (включает весь день).

## Файлы
- `models/assembly.py` — ORM (Request, Item, StatusHistory, Draft).
- `schemas/assembly.py`, `schemas/assembly_draft.py` — Pydantic DTO.
- `services/assembly_service.py` — бизнес-логика + аналитика логистики.
- `services/assembly_draft_service.py` — CRUD черновика + `commit_draft`.
- `services/assembly/crud.py` — `_validate_available_for_assembly`.
- `routers/assembly.py` — CRUD + workflow + analytics.
- `routers/assembly_drafts.py` — `/api/v1/assembly/drafts` (list/get/create/update/delete/commit).
- Frontend: `warehouse/assembly/` (page / new / [id] / [id]/edit / distribute) + `warehouse/logistics/page.tsx`.
- ТЗ/UX: `docs/tz_assembly_logistics.md`, `docs/ux_plan_assembly_logistics.md`.
