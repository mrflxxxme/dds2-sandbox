# DOMAIN_COUNTERPARTY — Контрагенты, Займы, Обороты

Справочник контрагентов, займы и обороты по контрагентам, связанные с
банковскими транзакциями. Заменяет старый `CounterpartyCategory.cp_key`
полноценной сущностью с типом, мультиролью, документами и статистикой
(мультивалюта RUB/CNY раздельно).

## Таблицы
| Модель | Назначение | Ключ / constraint |
|--------|------------|-------------------|
| `Counterparty` | Контрагент (`primary_type`, `secondary_types[]`, `inn`, `contract_number`), SoftDelete | UNIQUE `(project_id, inn)` и `(project_id, contract_number)` — partial, WHERE not null + not deleted |
| `CounterpartyDocument` | Документ в MinIO (`doc_type`: CONTRACT/CERTIFICATE/INVOICE/OTHER), SoftDelete | FK на counterparty |
| `Loan` | Займ (`direction`: INCOMING/OUTGOING/AFFILIATED; `principal`, `currency`, `rate`, `status`), SoftDelete | FK на counterparty |
| `LoanPayment` | Платёж по займу (`payment_type`: DISBURSEMENT/PRINCIPAL_REPAY/INTEREST_PAY/PENALTY) | UNIQUE partial `(transaction_id)` WHERE not null |

`primary_type` — 13 значений: `SUPPLIER, FULFILLMENT, CARRIER, CUSTOMS_BROKER, DESIGNER, LEGAL, LANDLORD, IT_SERVICE, MARKETPLACE, BANK, GOVERNMENT, AFFILIATED, OTHER`. `secondary_types` — дополнительные роли (пример: ИП Кузнецов primary=FULFILLMENT, secondary=[CARRIER]).

`transactions` несёт FK: `counterparty_id`, `loan_id`, `loan_payment_id`, `loan_payment_type`, плюс `contract_number`, `unk_number`.

## Бизнес-правила

### Контрагенты
- **Upsert by INN:** при импорте всегда upsert по `(project_id, inn)` — имя обновляется, если новое длиннее.
- **Китайские поставщики без ИНН:** upsert по `(project_id, contract_number)`, не по INN.
- Автосоздание при импорте — `primary_type=OTHER`, `created_by_import=True`. `primary_type` НЕ перезаписывается, если был выставлен вручную (`created_by_import=False`).
- Custom exceptions: `CounterpartyConflictError` (дубль INN/contract), `CounterpartyNotFoundError`, `ProjectMismatchError`.

### Займы (loan)
- Мультивалюта: `currency` на займе и на каждом `LoanPayment` раздельно; проценты — поле `rate`.
- **Автопривязка платежей при ETL:** `LoanService.auto_link_from_etl()` ищет ACTIVE `Loan` с тем же `contract_number` в проекте, создаёт `LoanPayment` и проставляет `Transaction.loan_id` / `loan_payment_id`. Если займ не найден — `loan_payment_type` всё равно проставляется (видимость в UI).
- Custom exceptions: `LoanNotFoundError`, `LoanPaymentAlreadyExistsError` (транзакция уже связана).

### Импорт реестра займов из Excel (`services/loan_import.py`)
`POST /loans/import` — лист с колонками Дата/Тип/Сумма/Контрагент/Номер договора/Ставка/От/До/Статус
(«Приход» → `Loan`, «Возврат» → `LoanPayment` PRINCIPAL_REPAY + закрытие). Идемпотентно по
`(project_id, counterparty_id, contract_number, start_date)` — один номер договора может нести
несколько траншей. Сущность (физ/ИП) и банк — со второго листа по имени контрагента, best-effort.
Две неочевидные семантики (обе стоили боевых искажений, не откатывать без теста):
- **Колонка «Статус» — НЕ статус займа.** В реестре это формула
  `=IF(AND(TODAY()>=От; TODAY()<=До); «Активен»; «Не активен»)`, то есть «срок идёт». Займ создаётся
  `ACTIVE`, закрыть его может ТОЛЬКО строка «Возврат». Иначе просроченный невозвращённый займ
  приезжает `CLOSED` и выпадает из остатка долга (на боевом реестре пряталось 6 займов на 10.5 млн ₽).
- **Возврат закрывает погашаемый транш, а не преемника** (`_pick_repay_target`). При продлении без
  смены номера договора старый транш гасится ровно в день старта нового, и возврат датирован этим
  днём → приоритет матча `maturity_date == дата возврата`, прежняя эвристика «последний старт ≤
  возврата» — фолбэк. Возвраты обрабатываются хронологически, `repaid_ids` не даёт двум возвратам
  сесть на один транш. Без этого новый займ уезжал в `CLOSED`, а погашенный висел активным с чужой
  ставкой и сроком (11 таких пар на боевом реестре).

Цепочки продлений («06» → «06-01» → «06-02») линкуются в `parent_loan_id` best-effort
(`_link_extension_chains`). Лимит строк — 100k (защита от zip-бомбы).

### Обороты контрагентов
- Pivot-отчёт `GET /reports/counterparty-turnovers` (`services/reports/counterparty_turnovers.py::get_counterparty_turnovers`) — агрегация платежей по контрагентам, мультивалюта (RUB/CNY раздельными колонками).

### ETL enrichment (master_logic.py)
`enrich_purpose(purpose)` применяет regex к `Transaction.purpose`:
- `RE_CONTRACT` → `contract_number` (китайские поставщики, min 6 цифр); `RE_UNK` → `unk_number` (валютный контроль); `RE_MT103` → `mt103_ref`.
- `RE_DEPOSIT_*` → `event_type2` = DEPOSIT_PLACE / DEPOSIT_RETURN / DEPOSIT_INTEREST (`is_cashflow2` 0/0/1).
- `RE_LOAN_*` → `loan_payment_type` = DISBURSEMENT / PRINCIPAL_REPAY / INTEREST_PAY.

### Faktura.ru парсер
`FAKTURA_WB_BANK` — XML Spreadsheet 2003 парсер банковских выписок WB Банка (`etl/parsers/faktura.py`, `FakturaParser` / `parse_faktura_wb_bank`). Namespace `urn:schemas-microsoft-com:office:spreadsheet`; зарегистрирован в `SOURCE_PARSERS`.

### Backfill
`scripts/backfill_counterparties.py --project-id=X [--dry-run]` — находит уникальные ИНН во всех `Transaction`, создаёт недостающие `Counterparty` (тип из `KNOWN_INN_TYPES` если известен), линкует `Transaction.counterparty_id` где NULL. Идемпотентен — повторный запуск создаёт 0 записей.

### Cache
Все мутации (create/update/delete/match) вызывают `invalidate_project_reports(project_id)`. Затрагиваемые prefixes: `counterparty_list`, `counterparty_detail`, `reports:counterparty_turnovers`, `loan_list`.

## Зависимости
- `DOMAIN_TRANSACTIONS` — банковские транзакции с FK `counterparty_id` / `loan_id` / `loan_payment_id`.
- ETL parsers (`etl/parsers/faktura.py`) — Faktura WB Bank XML.
- MinIO — хранилище документов контрагентов.

## Грабли
- Partial indexes созданы через `CREATE INDEX CONCURRENTLY` — миграция должна использовать raw SQL / `op.execute` с `AUTOCOMMIT`.
- Не перезаписывать `primary_type` при импорте, если контрагент заведён вручную.
- Backfill идемпотентен — безопасно гнать повторно.

## Файлы
- `models/counterparty.py` — `Counterparty`, `CounterpartyDocument`.
- `models/loan.py` — `Loan`, `LoanPayment`.
- `schemas/counterparty.py`, `schemas/loan.py` — Pydantic.
- `services/counterparty_service.py` — `CounterpartyService` (`upsert_by_inn`, `upsert_by_contract`, CRUD, `stats`, документы).
- `services/loan_service.py` — `LoanService` (CRUD, `match_transaction`, `auto_link_from_etl`).
- `services/reports/counterparty_turnovers.py` — pivot-отчёт оборотов.
- `routers/counterparty.py`, `routers/loans.py` — HTTP endpoints (обороты — extension в `routers/reports.py`).
- `etl/parsers/faktura.py` — Faktura WB Bank парсер.
- `etl/master_logic.py` — regex + `enrich_purpose`.
- `scripts/backfill_counterparties.py` — backfill.
