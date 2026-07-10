# DOMAIN: Заявки на оплату (payment-request)

Оплата перевозчику по отгрузке. Логист по ОТПРАВЛЕННОЙ отгрузке формирует заявку
на оплату, прикладывает счёт+акт, передаёт в оплату; оператор создаёт реальный
черновик платёжки в банке (Faktura write); матчер выписки авто-ставит «Оплачено».

## Модель данных
- **`PaymentRequest`** (`models/payment_request.py`) — заявка. SoftDelete+Timestamp,
  `project_id`-scoped. Снимок реквизитов получателя (`payee_*`) замораживается при
  создании (переживает правки контрагента). `amount Numeric(18,2)` CK>0.
  Линки-провенанс: `outbound_shipment_id` (первый/основной забор — legacy-указатель,
  оставлен для матчера/совместимости), `counterparty_id` (перевозчик), `assembly_request_id`.
  `matched_transaction_id` (FK `transactions.id`) — банковский дебет, сматченный матчером.
  `bank_guid` (idempotency для save_payment), `bank_doc_id` (id черновика в банке).
- **`PaymentRequestShipment`** (`models/payment_request.py`) — child M:N **заявка↔забор**
  (одна оплата за N заборов одного перевозчика: один счёт на несколько отгрузок).
  `project_id`, `payment_request_id` (CASCADE), `outbound_shipment_id` (CASCADE),
  `uq(payment_request_id, outbound_shipment_id)`. Relationship `PaymentRequest.shipments`.
  Миграция `pay06` (таблица+индексы+backfill: каждая активная заявка с `outbound_shipment_id`
  → строка связи). Источник истины «какие заборы в заявке» = эта таблица; `outbound_shipment_id`
  на заявке = первый из них. `list_shippable` помечает ВСЕ заборы мульти-заявки занятыми (join по child).
- **`PaymentRequestDocument`** — счёт/акт в MinIO (`doc_type` INVOICE|ACT), зеркало
  `CounterpartyDocument`. Ключ `payment_requests/{id}/docs/{ts}_{file}`.
- **`PaymentRequestEvent`** — аудит-лог переходов (вкл. авто-матч `system:faktura_match`).
- **`Counterparty`** +4 колонки: `bank_account`, `bik`, `bank_name`, `corr_account`
  (банковские реквизиты перевозчика для авто-заполнения).
- **`PaymentShipmentArchive`** — маркер «забор убран из рабочего списка логиста»
  (наличие строки = в архиве; разархивация = удаление строки, это plain-таблица без SoftDelete).
- **`OutboundShipment`** +2 колонки `matched_transaction_id` (FK `transactions.id`) + `matched_at` —
  связка забора с дебетом выписки БЕЗ заявки: авто (`sync_shipment_payments`, 1:1) ИЛИ ручная
  (привязка **N заборов → 1 оплата** для агрегированных платежей «по счетам №…»). `matched_transaction_id`
  НЕ-уникален (миграция `pay04` снимает 1:1-индекс); защита авто-матча — consumed-set, не БД.
- **`PaymentTxnArchive`** — маркер «исходящий платёж перевозчику разобран» (сверка оплат,
  plain-таблица без SoftDelete, как `PaymentShipmentArchive`).

Партиал-уникальные индексы (все `WHERE is_deleted=false`): `(project_id, number)`,
`bank_guid`, `PaymentRequest.matched_transaction_id` (одна транзакция → максимум одна заявка).
Партиал-уник `(project_id, transaction_id)` на `payment_txn_archive`. `OutboundShipment.matched_transaction_id`
БЕЗ уникальности (N заборов могут делить одну агрегированную оплату).

## Стейт-машина (`services/payment_request_status.py`)
```
[создание] ──▶ PENDING_REVIEW ──create-draft──▶ DRAFT_CREATED ──выписка──▶ PAID
                  ├─REJECTED/CANCELLED            ├─REJECTED/CANCELLED
```
- **PENDING_REVIEW** («На проверке») — стартовый статус (заявка создаётся СРАЗУ здесь, без
  «Черновика»). Редактируема (реквизиты/документы) до создания платёжки в банке.
- **DRAFT_CREATED** («Платёжка создана») — после реальной записи в банк; ждёт подписи.
- **PAID** («Оплачено») — матчер выписки нашёл исходящий дебет (терминальный).
- `REJECTED` / `CANCELLED` — терминальные.
- **DRAFT** — устаревший статус: новые заявки его не получают; миграция `pay05` перевела
  старые DRAFT→PENDING_REVIEW. В enum/истории остаётся для аудита.

`TRANSITIONS` декларативны (`PAYMENT_REQUEST_TRANSITIONS`), guard `check_transition`.

## Сервисы
- `payment_request_service.py` — list/get/list_shippable/create/update/submit. **create** валидирует
  реквизиты (ИНН/счёт/БИК/наименование/amount) и ставит сразу PENDING_REVIEW; документы опциональны
  (грузятся после). **Мульти-забор:** `outbound_shipment_ids` (или одиночный `outbound_shipment_id`)
  → грузит заборы, проверяет ОДНОГО перевозчика (`len({counterparty_id})>1`→ошибка) и что ни один
  забор не занят активной заявкой (`_assert_shipments_free`); `amount = Σ pickup_cost`;
  `outbound_shipment_id` = первый; создаёт строки `PaymentRequestShipment`. `_build_purpose` строит
  назначение «Транспортные услуги, N палет, забор DD.MM.YYYY[–DD.MM.YYYY], сдача <склады>» (≤210 симв),
  если пользователь не задал своё. `covered_shipment_numbers` → номера заборов заявки (для детали).
  **submit** идемпотентен (legacy DRAFT→PENDING_REVIEW). **update** — реквизиты (вкл. **amount** и
  **purpose**, allowlist `_EDITABLE_FIELDS`) до DRAFT_CREATED. **cancel** (`cancel_request`):
  PENDING_REVIEW/DRAFT → CANCELLED (НЕ из DRAFT_CREATED — там платёжка уже в банке); отменённая
  заявка освобождает свои заборы (CANCELLED исключён из `_assert_shipments_free`/`already_requested`).
- `payment_request_documents.py` — upload/download(стрим через бэк, project-scoped)/delete (MinIO).
  Счёт/акт: **PDF, Word, Excel или фото** (MIME- **или** ext-allowlist + magic-bytes; исполняемые
  отсекаются). Ext-фолбэк обязателен: браузер шлёт `.heic`/`.xls` с пустым/`octet-stream` типом.
- `invoice_parser.py` — `POST /parse-invoice`: файл счёта → реквизиты получателя (ПОДСКАЗКА для
  формы, в БД не пишется). Текст: PDF → pdfplumber, `.docx` → stdlib zipfile, `.xls` (BIFF, печатная
  форма 1С) → xlrd, `.xlsx` → openpyxl, HTML-таблица под именем `.xls` → снятие тегов; скан-PDF и
  фото → vision-Claude. Текст разбирает Haiku (понимает «Продавец vs Покупатель») с regex-fallback.
  Гейты доверия: р/с surface'ится только пройдя контроль-ключ ЦБ по БИК; счёт, совпавший с НАШИМ
  (`own_accounts`), чистится как блок плательщика. Банк по БИК — `enrich_bank_from_db` (полный
  справочник `cbr_bic`, фолбэк на 9 зашитых): без неё счета ИП из региональных филиалов
  («Тульское отделение N8604 ПАО Сбербанк») оставались без банка и к/с.
- `faktura_payment.py` — `create_payment_draft`: authenticate→get_accounts→build body→
  validate_payment→persist `bank_guid` ДО save→save_payment→DRAFT_CREATED. Идемпотентно по guid.
- `etl/sync_payment_requests.py` — **матчер заявок**: хук в `persist_df` (sync). На каждый синк
  выписки ищет дебет (expense>0) с `inn==payee_inn` и `|expense−amount|≤0.01` в дата-окне
  → DRAFT_CREATED→PAID. Consumed-once, Decimal-сравнение, пустой ИНН пропускается.
- `etl/sync_shipment_payments.py` — **матчер заборов** (хук в `persist_df` ПОСЛЕ матчера заявок,
  чтобы явная заявка забрала транзакцию первой): отгруженный/сданный забор без активной заявки
  → ищет дебет с `inn==Counterparty.inn перевозчика` и `|expense−pickup_cost|≤0.01` (окно
  `shipped_date−7д … сегодня+1`) → проставляет `OutboundShipment.matched_transaction_id`. Тот же
  advisory-lock-namespace и union-consumed-set (заявки ∪ заборы), что не даёт занять транзакцию дважды.
  Бейдж «✅ Оплачено (авто)» в листе логиста для заборов БЕЗ заявки.
- `payment_request_service.get_counterparty_reconciliation` — **сверка** на карточке перевозчика:
  его заборы (оплачено через заявку/авто-/ручную связку / не оплачено) + **ВСЕ платежи из выписки**
  на ИНН перевозчика (кроме занятых формальной заявкой), каждый с привязанными заборами
  (`linked_count`/`linked_sum`/`diff`). `link_shipments_to_payment` (N заборов → 1 оплата, ставит
  `matched_transaction_id`) / `unlink_shipments` (очистка). `archive_txns`/`unarchive_txns`
  (`PaymentTxnArchive`) — пометить платёж разобранным.

## API (`/api/v1/payment-requests`)
`GET /` · `GET /{id}` (деталь доливает `shipment_numbers`/`shipment_count`) · `GET /shippable`
(+`counterparty_id` фильтр) · `POST /` (тело принимает `outbound_shipment_id` ИЛИ
`outbound_shipment_ids[]` — одна оплата за N заборов) · `PATCH /{id}` ·
`POST /{id}/documents` (multipart PDF) · `GET /{id}/documents/{doc}/download` ·
`DELETE /{id}/documents/{doc}` · `POST /{id}/submit` · `GET /{id}/status` (поллинг) ·
`POST /{id}/create-draft` (`confirm:true` обязателен — реальная запись в банк) ·
`POST /{id}/cancel` (`{comment?}` → CANCELLED, только из PENDING_REVIEW/DRAFT; освобождает заборы) ·
`POST /create-drafts` (**массовая** запись в банк по `{ids[], confirm:true, payer_account_id?}`:
дедуп ids, идёт по заявкам последовательно, ошибка одной НЕ останавливает остальные →
`{results:[{id,ok,bank_doc_id?,error?}], created, failed}`) ·
`POST /shippable/archive`+`/unarchive` (заборы) · `GET /reconciliation?counterparty_id=` (сверка) ·
`POST /orphan-payments/archive`+`/unarchive` (платежи) · `POST /payments/link`+`/payments/unlink`
(привязка N заборов к оплате).
Структурные ошибки → `error.payload` (`{missing:[...]}` / `{bank_errors:[...]}`).

## Frontend
Две роли:
- **Лист логиста → вкладка «Оплаты»** (`ShipmentPaymentsTab.tsx`): рабочий список ЗАБОРОВ
  (SHIPPED+DELIVERED отгрузки), поиск, фильтры (Все/Без заявки/Заявка есть/Архив), **сортировка по
  любому столбцу** (клик по заголовку, ▲/▼), массовый выбор + в архив. По забору без заявки —
  «Создать оплату» (модалка); по забору с заявкой — кликабельный бейдж статуса → открыть/редактировать
  (черновик editable, прочие read-only). **Bulk «💳 Создать оплату (N)»**: выбрать 2+ неоплаченных
  забора ОДНОГО перевозчика (`canBulkPay` = один non-null `counterparty_id`, ни один не already_requested/
  matched) → модалка в мульти-режиме (один счёт за все).
- **Модалка** (`CreatePaymentRequestModal.tsx`): создание (пикер/ручной/**мульти** через
  `initialShipmentIds`) ИЛИ просмотр/правка существующей заявки (`editRequestId`: DRAFT — editable +
  правка документов, иначе read-only). **Мульти-режим:** сводка-блок (список заборов + Итого Σ),
  реквизиты префиллятся с первого (общего) перевозчика, `amount = Σ pickup_cost` (`.toFixed(2)`),
  одиночный пикер и mode-toggle скрыты; назначение строит бэкенд.
- **Отдельная страница «Оплаты»** (`payments/PaymentsPanel.tsx`, сайдбар): операторский экран
  согласования — табы статусов, деталка, документы, «Создать оплату» (банк, confirm), история.
  **Bulk:** чекбоксы на строках PENDING_REVIEW + «Выбрать все на проверке» → «💳 Создать оплаты в
  банке (N)» (`createPaymentDrafts`, confirm-гейт «НЕОБРАТИМО», `bulkRunning`-дизейбл) → баннер
  результата (created/failed + ошибки по номерам). Уходит строго `selected ∩ видимые PENDING_REVIEW`.
  В деталке заявки PENDING_REVIEW/DRAFT — «✏️ Редактировать» (открывает модалку в режиме
  `editRequestId`: правка суммы/назначения/реквизитов) и «✖ Отменить оплату» (`cancelPaymentRequest`,
  confirm). Назначение для новых заявок строит бэкенд (`_build_purpose`: палеты+дата+сдача).
- **Карточка контрагента → вкладка «🔗 Сверка оплат»** (`refs/counterparty/[id]/ReconciliationTab.tsx`,
  для перевозчиков): секция «Платежи из выписки» (карточки с привязанными заборами, бейдж сходимости
  суммы, кнопка «Связать заборы» → инлайн-чек-лист неоплаченных заборов с бегущей суммой vs платёж) +
  секция «Заборы» (бейдж оплачено/не оплачено, «Отвязать»). Архив платежа/забора + Активные/Архив;
  сводка (сопоставлено/не оплачено/платежей/без забора).

## Запись в банк (Faktura write)
Платёжка строится по **проверенному** контракту `POST /payments/validate`
(`scripts/faktura_validate_probe.py` вернул `errors:[]` на проде): поля
`payeeAccountNumber/payeeBankBic/payeeBankAccount`, `payerAccountId` = ВНУТРЕННИЙ id
счёта из `/accounts` (не номер), `payerName/payerKpp`, `queue/uip/urgent/payoutsCode/
incomeTypeCode`. Тест `tests/test_faktura_payment.py` фиксирует контракт.
- Счёт-плательщик: `payer_account_id` в запросе → `config.payer_account_id` ключа Faktura
  → авто только если RUB-счёт ОДИН (при нескольких — ошибка, не угадываем).
- Идемпотентность: `bank_guid` **коммитится ДО** `/payments/save` (краш после save → ретрай тем же guid).

### ⚠ Pre-launch гейты
`/payments/save` — НЕОБРАТИМАЯ реальная запись, **ни разу не гонялась живьём** (проверен
только `/validate`). До включения для операторов: (1) залить креды Faktura на прод +
`payer_account_id` в config ключа; (2) **тест на 1₽** через validate→save (выяснить, требует
ли `/save` непустой `fingerprint`, который `/validate` пропускает); (3) согласовать
robotic-write с банком (161-ФЗ/ДБО). Эндпоинт за `confirm:true` + аудит; рекомендуется
отдельная финанс-роль (логисту запись в банк не давать).

## Ограничения v1
Авто-матч 1:1 по ИНН+сумма НЕ ловит объединённые/частичные/округлённые платежи и
строки выписки без ИНН — для **заборов** это закрывает ручная привязка N→1 на сверке
(агрегированный платёж «по счетам №…» → несколько заборов, со сверкой суммы); для
**заявок** на оплату остаётся ручной разбор (DRAFT_CREATED). Один забор привязан максимум
к одному платежу (частичная оплата одного забора несколькими платежами не поддержана).
Один р/с на контрагента (колонки, не child-таблица). «Live-флип» статуса = поллинг/
рефетч (матчер в worker-процессе, WS-broadcast in-memory не доходит).
