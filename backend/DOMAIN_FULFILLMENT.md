# DOMAIN_FULFILLMENT — интеграция внешних фулфилментов (skladbot.ru, wmscelicom; позже migfull)

Generic-слой зеркалирования данных фулфилмент-провайдеров на странице склада: остатки (snapshot + расхождения), заявки на сборку и приёмки (зеркало + ручная связь с нашими документами). Фаза 1 — read-only pull; push-создание заявок — фаза 2.

## Провайдеры
| | skladbot.ru | wmscelicom («Целиком») |
|---|---|---|
| База API | `api.skladbot.ru`, Bearer-токен | клиентский инстанс `{client}.wmscelicom.ru` (поле при подключении), **токен в URL-пути** `/api/{token}/…` — не логировать URL |
| Остатки | `POST /v1/products` (amount/reserve/repair/nominale) | `GET items/get/` — Count→good, CountVirtual→nominal, reserve/defect нет; `Barcodes[]` — берём первый |
| Сборка | `/v1/requests?type_id=851` | `shipmentsfbo/list/?with_packages=1&with_items=1` (отгрузки FBO) |
| Приёмки | `/v1/requests?type_id=852,2644` | `unloadingorders/list/` (items в ответе списка) |
| Деталка | живой `GET /v1/requests/show/{id}` | **из raw зеркала** — by-id эндпоинта нет; актуальность = последний синк |
| Лимиты | 60/120 rpm | 150 rpm, max 30 элементов/страница |
| Токен | RS256 JWT (exp → token_expires_at) | hex-строка, без срока в самом токене |

Внутри сервиса провайдер-специфика заканчивается на нормализаторах `_normalize_*` (fulfillment_service.py); `_apply_stocks`/`_apply_requests` работают с нормализованными dict.

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
- `kind`: skladbot — по `type_id` (851 → `assembly`, 852/2644 → `inbound`); wmscelicom — по источнику (shipmentsfbo → `assembly`, unloadingorders → `inbound`).
- Один склад — один активный провайдер (guard в `connect`); `IntegrationKey.config`: skladbot — customer_id/token_expires_at, wmscelicom — api_base_url (хост валидируется суффиксом `.wmscelicom.ru` — SSRF-guard).
- wmscelicom-статусы: отгрузка завершена при «Отгружена»/«Вручена получателю»/«Принята в СЦ…», «Аннулирована» → archived; приёмка завершена при заполненном `unloading_close_date`.
- **Авто-READY связанных сборок**: при синке и при link связанная `AssemblyRequest` переводится `IN_PROGRESS → READY` (история `changed_by=ff_sync`, `actual_ready_date`, invalidate `reports:assembly_flow`), когда стадия ФФ говорит «груз собран» (`_assembly_ready_signal`): skladbot — стадия вне сборочных (`ASSEMBLY_WIP_STAGE_CODES` = cargo_pickup, delivery_to_the_marketplace_warehouse + title-маркеры «забор груза»/«объема груза» в обеих орфографиях) или `is_completed`; wmscelicom — только `is_completed`. Прочие статусы (VEHICLE_ASSIGNED+) не трогаются, обратных переходов нет; archived/expired не триггерят. Намеренно мимо `mark_ready` (его пред-условия FBO-supply/палеты не применимы к внешнему сигналу).
- **Обогащение зеркала заявок** (`total_qty` — заявлено всего, `dest_warehouse` — «Склад МП»): skladbot — живая деталка `show` при синке (активные сборки каждый синк, завершённые — разовый бэкфилл, cap 100/синк, 429 прекращает обогащение не валя синк); wmscelicom — из raw списка (`packages.items` / `shipped_target`; пустой состав → None, не 0). UPSERT не затирает обогащение `None`'ом.
- Токен write-only: наружу только `key_preview` (***последние 4); шифрование `backend/utils/crypto.py`; `config` хранит customer_id и token_expires_at (exp из JWT, ~180 дней — следить за истечением).

## Сводная страница «Заявки ФФ»
`GET /warehouse/fulfillment/overview` (query: `kind=assembly|inbound|other`, `warehouse_id`, `only_unlinked`) — сводка по всем складам проекта с активной интеграцией: список складов с каунтами активных assembly-заявок (total / unlinked) + заявки зеркала (limit 500) с `suggestions` — топ-3 кандидата мэтчинга к нашим AssemblyRequest. Эвристика (`_load_match_suggestions`, без HTTP к провайдерам): date_score по |external_created_at − created_at| (0/1/2 дн → 70/55/40, дальше отсев) + пересечение ШК из raw (только wmscelicom: packages→items; Jaccard × 30) + бонус 10 за суммарное qty в ±10%; cap 100, порог 30. Кандидаты — статусы IN_PROGRESS/READY/VEHICLE_ASSIGNED того же склада, ещё не связанные с ФФ-заявками. Роут со статическим префиксом объявлен раньше параметризованного `/{warehouse_id}/fulfillment/*` (отдельный sub-router в `routers/fulfillment.py`).

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
- **Коды стадий skladbot ≠ названия** (тип 851, живые пробы 2026-06-11): `cargo_pickup`=«Забор груза» → `delivery_to_the_marketplace_warehouse`=«Указание обьема груза v2» (!) → «Указание виды работ логистики» (код неизвестен — живых примеров не было). Орфография провайдера гуляет («обьема»/«объема», «виды работ») — классификатор матчит и код, и title-маркеры.

## Файлы
- `backend/integrations/skladbot_client.py` — httpx-клиент (Bearer, 429→RateLimitError, circuit breaker).
- `backend/integrations/wmscelicom_client.py` — httpx-клиент wmscelicom (токен в path, normalize_base_url, circuit breaker).
- `backend/services/fulfillment_service.py` — connect/status/sync/list/link + нормализаторы провайдеров.
- `backend/routers/fulfillment.py` — `/warehouse/fulfillment/overview` + `/warehouse/{id}/fulfillment/*`.
- `backend/scheduler/jobs/fulfillment_sync.py` — периодический синк (15 мин, worker).
- `frontend-react/src/app/(main)/p/[slug]/warehouse/[id]/page.tsx` — вкладки «ФФ остатки / ФФ сборка / ФФ приёмки» + блок подключения в «Реквизитах».
