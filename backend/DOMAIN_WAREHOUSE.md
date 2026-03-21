# DOMAIN_WAREHOUSE.md — Склад + FBO Поставки

## Обзор модуля
Управление складами, приёмками, отгрузками, перемещениями, остатками.
Синхронизация FBO-поставок с WB Marketplace API.

## Архитектура

```
backend/
  models/warehouse.py          — Warehouse, Inbound, Outbound, Transfer, Stock, Adjustment
  models/wb_fbo.py             — WbFboSupply, WbFboSupplyItem (из WB API)
  schemas/warehouse.py         — Pydantic схемы склада
  schemas/wb_fbo.py            — Pydantic схемы FBO
  services/warehouse_service.py — CRUD + stock engine (приём/отгрузка/перемещение)
  services/fbo_supply_service.py — FBO синхронизация + список + связь с отгрузками
  routers/warehouse.py         — 19 endpoints: склады, приёмки, отгрузки, перемещения
  routers/fbo_supplies.py      — 6 endpoints: список FBO, items, sync, link/unlink
  scheduler/jobs/fbo_supplies.py — автосинхронизация каждый час

frontend-react/src/
  app/p/[slug]/warehouse/      — страницы складов
  app/p/[slug]/warehouse/fbo-supplies/ — страница FBO поставок
  lib/api/warehouse.ts         — API методы (включая FBO)
  types/api.ts                 — TypeScript типы
```

## Ключевые модели

### Warehouse (склады)
- `EXTERNAL` — внешний (поставщик)
- `FULFILLMENT` — склад фулфилмента (наш)
- SoftDeleteMixin + TimestampMixin

### Stock Engine
- `_update_stock()` — атомарное обновление WarehouseStock + StockMovement
- Проверяет неотрицательность остатков
- 8 типов движений: INBOUND, OUTBOUND, TRANSFER_IN/OUT, ADJUSTMENT и т.д.

### FBO Supplies (WB API)
- `WbFboSupply` — поставка из WB Marketplace API
- `WbFboSupplyItem` — позиции (заказы) в поставке
- Статусы **read-only** — только через синхронизацию
- Связь: `outbound_shipment_id` ↔ `OutboundShipment`
- Автоматический DELIVERED при ACCEPTED на WB

## WB API (Marketplace)
- `GET /api/v3/supplies` — список поставок
- `GET /api/v3/supplies/{id}/orders` — позиции
- `GET /api/v3/supplies/{id}` — детали одной поставки
- Base URL: `https://marketplace-api.wildberries.ru`
- Авторизация тем же ключом (IntegrationKey service="wb")

## Статусы FBO
| WB статус | Отображение | Финальный? |
|-----------|-------------|------------|
| ACTIVE | Запланирована | Нет |
| ON_DELIVERY | В пути | Нет |
| IN_PROGRESS | Разгрузка разрешена | Нет |
| ACCEPTED | Принята | Да |
| CANCELLED | Отменена | Да |

## Связь FBO ↔ Отгрузка
- Пользователь вручную связывает через UI
- `wb_fbo_supplies.outbound_shipment_id` → FK на outbound_shipments
- `outbound_shipments.wb_supply_id` → строка с ID поставки WB
- При ACCEPTED: автоматически outbound_shipment → DELIVERED (если SHIPPED)

## Синхронизация
- **Автоматическая:** каждый 1 час (scheduler job)
- **Ручная полная:** POST /sync — все поставки за 60 дней
- **Ручная статусов:** POST /sync-statuses — только активные поставки
- Логируется в SyncLog (sync_type="fbo_supplies")

## Тесты
- `tests/test_fbo_supply_service.py` — unit + API тесты
- Helpers (parse datetime), schemas, enums, models
- List (search/filter/sort/pagination)
- Items, link/unlink
- Sync с мокированным WB API клиентом
- Router endpoints (auth, params)
