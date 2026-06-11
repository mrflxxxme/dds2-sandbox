# DOMAIN_FULFILLMENT — интеграция внешних фулфилментов (skladbot.ru, позже migfull)

Generic-слой зеркалирования данных фулфилмент-провайдеров на странице склада: остатки (snapshot + расхождения), заявки на сборку и приёмки (зеркало + ручная связь с нашими документами). Фаза 1 — read-only pull; push-создание заявок — фаза 2.

## Таблицы
| Модель | Назначение | Ключ / constraint |
|--------|------------|-------------------|
| `FulfillmentStock` | снапшот остатков ФФ (qty_good/reserve/defect/nominal) | `(project_id, warehouse_id, barcode)` |
| `FulfillmentRequest` | зеркало заявки ФФ + связь с нашими документами | `(project_id, provider, external_id)` |
| `IntegrationKey` (+`warehouse_id`, `config`) | токен провайдера, привязанный к складу | `(project_id, service, label)`, label=`warehouse:{id}` |

## Бизнес-правила
- **Зеркало никогда не пишет в `WarehouseStock` / `StockMovement`** — наши остатки производны от документов; данные ФФ живут отдельно, вкладка показывает расхождения (diff = ff_good − our_quantity).
- Снапшот остатков перезаписывается целиком при каждом синке (delete + insert в одной транзакции); нулевые остатки тоже хранятся — нужны для диффа.
- Дубликаты barcode на стороне ФФ (версии товара под WB/OZON) агрегируются суммированием до записи.
- Заявки ФФ — UPSERT по `external_id`; связи `assembly_request_id`/`inbound_receipt_id` при обновлении НЕ перетираются.
- Один наш документ ↔ максимум одна заявка ФФ (проверка при link).
- `kind` определяется по `type_id` провайдера: skladbot — 851 → `assembly`, 852/2644 → `inbound`.
- Статусы ФФ (stage_code/is_completed) — информативные; наши статусы документов автоматически НЕ меняются.
- Токен write-only: наружу только `key_preview` (***последние 4); шифрование `backend/utils/crypto.py`; `config` хранит customer_id и token_expires_at (exp из JWT, ~180 дней — следить за истечением).

## Зависимости
- `DOMAIN_WAREHOUSE` — Warehouse, WarehouseStock (для диффа), InboundReceipt (связь приёмок).
- `DOMAIN_ASSEMBLY` — AssemblyRequest (связь заявок на сборку).
- `DOMAIN_COST` — Nomenclature: маппинг ФФ-товаров по `(project_id, barcode)`.
- skladbot.ru API — детали в memory `project-skladbot-api` (лимиты 60/120 rpm, формы ответов, типы заявок).

## Грабли
- **Деталка заявки skladbot — только недокументированный `GET /v1/requests/show/{id}`** (документированного `GET /v1/requests/{id}` нет — 404). Состав (`products[]`), динамические поля и история стадий доступны; роут не из официальных доков — при внезапных 404 проверить, не выпилил ли его провайдер. Деталка тянется живьём при открытии (не кэшируется): принятые количества меняются на стороне ФФ.
- **`/v1/products` отдаёт сырой Laravel-paginator**: `data` — dict с ключом system_product_id и списком barcode-уровневых item'ов внутри; `limit` в body работает (1000), пагинация `?page=N`.
- **`IntegrationKey` = SoftDeleteMixin + UniqueConstraint без is_deleted** — повторный connect обязан искать строку включая soft-deleted и делать `.restore()`, иначе IntegrityError.
- **Создание заявки у ФФ = реальный заказ** — POST /v1/requests только после явного подтверждения пользователя; локалка и тесты не ходят в живой API (в тестах клиент мокается).

## Файлы
- `backend/integrations/skladbot_client.py` — httpx-клиент (Bearer, 429→RateLimitError, circuit breaker).
- `backend/services/fulfillment_service.py` — connect/status/sync/list/link.
- `backend/routers/fulfillment.py` — `/warehouse/{id}/fulfillment/*`.
- `backend/scheduler/jobs/fulfillment_sync.py` — периодический синк (15 мин, worker).
- `frontend-react/src/app/(main)/p/[slug]/warehouse/[id]/page.tsx` — вкладки «ФФ остатки / ФФ сборка / ФФ приёмки» + блок подключения в «Реквизитах».
