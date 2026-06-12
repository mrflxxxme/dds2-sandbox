# DOMAIN_FULFILLMENT — интеграция внешних фулфилментов (skladbot.ru, wmscelicom, migfull)

Generic-слой зеркалирования данных фулфилмент-провайдеров на странице склада: остатки (snapshot + расхождения), заявки на сборку и приёмки (зеркало + ручная связь с нашими документами). Фаза 1 — read-only pull; push-создание заявок — фаза 2.

## Провайдеры
| | skladbot.ru | wmscelicom («Целиком») | migfull.app («Натали») |
|---|---|---|---|
| База API | `api.skladbot.ru`, Bearer-токен | клиентский инстанс `{client}.wmscelicom.ru` (поле при подключении), **токен в URL-пути** `/api/{token}/…` — не логировать URL | `migfull.app/api/customer/{tenant_guid}` (GUID — поле при подключении, строго UUID), Bearer в заголовке; Laravel-конверт `{success,data,links,meta}` |
| Остатки | `POST /v1/products` (amount/reserve/repair/nominale) | `GET items/get/` — Count→good, CountVirtual→nominal, reserve/defect нет; `Barcodes[]` — берём первый | `GET /products` — stock_actual→good, stock_locked→reserve, stock_available→nominal; **ШК только в карточке** `GET /products/{guid}` |
| Сборка | `/v1/requests?type_id=851` | `shipmentsfbo/list/?with_packages=1&with_items=1` (отгрузки FBO) | `GET /shipments` — planned/shipped_lines приходят в списке ЦЕЛИКОМ (сверено с *_lines_count живьём) |
| Приёмки | `/v1/requests?type_id=852,2644` | `unloadingorders/list/` (items в ответе списка) | `GET /submissions` (состава в списке нет) + `GET /submissions/{guid}/lines/incoming\|received` |
| Деталка | живой `GET /v1/requests/show/{id}` | **из raw зеркала** — by-id эндпоинта нет; актуальность = последний синк | сборки — из raw зеркала; приёмки — живые lines/incoming+received |
| Лимиты | 60/120 rpm | 150 rpm, max 30 элементов/страница | per_page 1..1000; явных rpm-лимитов нет (caps наши) |
| Токен | RS256 JWT (exp → token_expires_at) | hex-строка, без срока в самом токене | hex-строка, read-only API (POST только для `…/search`) |

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
- `kind`: skladbot — по `type_id` (851 → `assembly`, 852/2644 → `inbound`); wmscelicom — по источнику (shipmentsfbo → `assembly`, unloadingorders → `inbound`); migfull — shipments → `assembly`, submissions → `inbound`.
- Один склад — один активный провайдер (guard в `connect`); `IntegrationKey.config`: skladbot — customer_id/token_expires_at, wmscelicom — api_base_url (хост валидируется суффиксом `.wmscelicom.ru` — SSRF-guard), migfull — tenant_guid (строгая UUID-валидация = guard от инъекции пути; хост фиксированный).
- wmscelicom-статусы: отгрузка завершена при «Отгружена»/«Вручена получателю»/«Принята в СЦ…», «Аннулирована» → archived; приёмка завершена при заполненном `unloading_close_date`.
- migfull-статусы (слаг → stage_code, человекочитаемый status_display → status/stage_title): отгрузки `uploaded → ready → closed` (+`canceled` → archived), приёмки `processing → send → closed` (+`canceled` → archived); `closed` → is_completed. Служебные позиции «ФФ грузовое место…» (учёт грузомест склада) фильтруются из остатков, тоталов и деталки.
- **Авто-READY связанных сборок**: при синке и при link связанная `AssemblyRequest` переводится `IN_PROGRESS → READY` (история `changed_by=ff_sync`, `actual_ready_date`, invalidate `reports:assembly_flow`), когда стадия ФФ говорит «груз собран» (`_assembly_ready_signal`): skladbot — стадия вне сборочных (`ASSEMBLY_WIP_STAGE_CODES` = cargo_pickup, delivery_to_the_marketplace_warehouse + title-маркеры «забор груза»/«объема груза» в обеих орфографиях) или `is_completed`; wmscelicom — только `is_completed`; migfull — `stage_code == "ready"` («Собран») или `is_completed`. Прочие статусы (VEHICLE_ASSIGNED+) не трогаются, обратных переходов нет; archived/expired не триггерят. Намеренно мимо `mark_ready` (его пред-условия FBO-supply/палеты не применимы к внешнему сигналу).
- **Обогащение зеркала заявок** (`total_qty` — заявлено всего, `dest_warehouse` — «Склад МП»): skladbot — живая деталка `show` при синке (активные сборки каждый синк, завершённые — разовый бэкфилл, cap 100/синк, 429 прекращает обогащение не валя синк); wmscelicom — из raw списка (`packages.items` / `shipped_target`; пустой состав → None, не 0); migfull — сборки бесплатно из списка (`planned_quantity_total` / `destination_marketplace.name`), приёмки — `lines/incoming` (активные каждый синк, закрытые — разовый бэкфилл, отменённые НЕ бэкфиллятся — вечный NULL голодал бы cap; cap 100/синк). UPSERT не затирает обогащение `None`'ом.
- **migfull guid→barcode**: в списке `/products` ШК пустые у ВСЕХ товаров — резолв детальными карточками только для незакэшированных guid с остатком, cap 300/синк (хвост дотягивается следующими синками); персистентный кэш = прошлый снапшот `fulfillment_stocks` (`external_product_id` → `barcode`). Нерезолвленные строки в снапшот не попадают (пустой barcode).
- Токен write-only: наружу только `key_preview` (***последние 4); шифрование `backend/utils/crypto.py`; `config` хранит customer_id и token_expires_at (exp из JWT, ~180 дней — следить за истечением).
- **Локальный архив** (`local_archived`/`local_archived_at`, ff03): пометка пользователя в DDS, НЕ зеркало провайдера (`archived` затирается каждым синком — local_* синк не трогает). `POST|DELETE /requests/{id}/archive`; списки/overview/suggestions/авто-READY исключают архивные; `GET /requests?show_archived=true` — вид «Архив».
- **Кандидаты связывания** (`GET /requests/{id}/link-candidates`): документы склада (assembly → AssemblyRequest кроме CANCELLED + ФБО-поставка/склад WB; inbound → InboundReceipt), не связанные с другими ФФ-заявками, со скорингом «подходит под наполнение»: Jaccard ШК × 60 + кол-во ±10% (+20) + дата 0/±1/±2 дн (+20/15/10), порог 40; состав ФФ — wmscelicom из raw, skladbot живой деталкой (недоступна → фолбэк по датам, порог 30). Питает модал «Связать» (поиск + секция «Похожие»).
- **Создание сборки из ФФ-заявки** (`POST /requests/{id}/create-assembly`): наша AssemblyRequest из состава ФФ-заявки (kind=assembly, ещё не связанной) через `create_assembly_request` (номер ASM-N, IN_PROGRESS, валидация доступного стока) + автосвязь; ШК без номенклатуры пропускаются и возвращаются в `skipped_barcodes`.

## Сводная страница «Заявки ФФ»
`GET /warehouse/fulfillment/overview` (query: `kind=assembly|inbound|other`, `warehouse_id`, `only_unlinked`) — сводка по всем складам проекта с активной интеграцией: список складов с каунтами активных assembly-заявок (total / unlinked) + заявки зеркала (limit 500) с `suggestions` — топ-3 кандидата мэтчинга к нашим AssemblyRequest. Эвристика (`_load_match_suggestions`, без HTTP к провайдерам): date_score по |external_created_at − created_at| (0/1/2 дн → 70/55/40, дальше отсев) + пересечение ШК из raw (только wmscelicom: packages→items; Jaccard × 30) + бонус 10 за суммарное qty в ±10%; cap 100, порог 30. Кандидаты — статусы IN_PROGRESS/READY/VEHICLE_ASSIGNED того же склада, ещё не связанные с ФФ-заявками. Роут со статическим префиксом объявлен раньше параметризованного `/{warehouse_id}/fulfillment/*` (отдельный sub-router в `routers/fulfillment.py`).

## Зависимости
- `DOMAIN_WAREHOUSE` — Warehouse, WarehouseStock (для диффа), InboundReceipt (связь приёмок).
- `DOMAIN_ASSEMBLY` — AssemblyRequest (связь заявок на сборку).
- `DOMAIN_COST` — Nomenclature: маппинг ФФ-товаров по `(project_id, barcode)`.
- skladbot.ru API — детали в memory `project-skladbot-api` (лимиты 60/120 rpm, формы ответов, типы заявок).
- migfull.app API — детали в memory `project-natali-fulfilment-api` и `~/Desktop/Натали_фулфилмент_API_документация.md`.

## Грабли
- **Деталка заявки skladbot — только недокументированный `GET /v1/requests/show/{id}`** (документированного `GET /v1/requests/{id}` нет — 404). Состав (`products[]`), динамические поля и история стадий доступны; роут не из официальных доков — при внезапных 404 проверить, не выпилил ли его провайдер. Деталка тянется живьём при открытии (не кэшируется): принятые количества меняются на стороне ФФ.
- **`/v1/products` отдаёт сырой Laravel-paginator**: `data` — dict с ключом system_product_id и списком barcode-уровневых item'ов внутри; `limit` в body работает (1000), пагинация `?page=N`.
- **`IntegrationKey` = SoftDeleteMixin + UniqueConstraint без is_deleted** — повторный connect обязан искать строку включая soft-deleted и делать `.restore()`, иначе IntegrityError.
- **Создание заявки у ФФ = реальный заказ** — POST /v1/requests только после явного подтверждения пользователя; локалка и тесты не ходят в живой API (в тестах клиент мокается).
- **Коды стадий skladbot ≠ названия** (тип 851, живые пробы 2026-06-11): `cargo_pickup`=«Забор груза» → `delivery_to_the_marketplace_warehouse`=«Указание обьема груза v2» (!) → «Указание виды работ логистики» (код неизвестен — живых примеров не было). Орфография провайдера гуляет («обьема»/«объема», «виды работ») — классификатор матчит и код, и title-маркеры.
- **migfull: строки приёмок содержат служебные грузоместа** («ФФ грузовое место — короб…», в т.ч. с пометкой «выявленный брак») вперемешку с товарами — фильтровать по name-маркеру при подсчётах. В строках заявок (`lines`) у `product` нет ШК — только guid; резолв через зеркало остатков.

## Файлы
- `backend/integrations/skladbot_client.py` — httpx-клиент (Bearer, 429→RateLimitError, circuit breaker).
- `backend/integrations/wmscelicom_client.py` — httpx-клиент wmscelicom (токен в path, normalize_base_url, circuit breaker).
- `backend/integrations/migfull_client.py` — httpx-клиент migfull (Bearer в заголовке, Laravel-пагинация, UUID-валидация guid, circuit breaker).
- `backend/services/fulfillment_service.py` — connect/status/sync/list/link + нормализаторы провайдеров.
- `backend/routers/fulfillment.py` — `/warehouse/fulfillment/overview` + `/warehouse/{id}/fulfillment/*`.
- `backend/scheduler/jobs/fulfillment_sync.py` — периодический синк (каждый час, worker).
- `frontend-react/src/app/(main)/p/[slug]/warehouse/[id]/page.tsx` — вкладки «ФФ остатки / ФФ сборка / ФФ приёмки» + блок подключения в «Реквизитах».
