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
| `LoanPayment` | Платёж по займу (`payment_type`: DISBURSEMENT/PRINCIPAL_REPAY/INTEREST_PAY/PENALTY/COMMISSION) | UNIQUE partial `(transaction_id)` WHERE not null |
| `LoanRatePeriod` | История ставок (плавающая = ключевая ЦБ + спред), `valid_from` | INDEX `(loan_id, valid_from)` |
| `LoanScheduleEntry` | Строка планового графика платежей (аннуитет) | UNIQUE `(loan_id, seq)` |
| `LoanFee` | Разовая комиссия (`fee_kind`: ORIGINATION/LIMIT_SETUP/OTHER) с флагом амортизации | INDEX `(loan_id, charged_at)` |

`Loan.mirror_loan_id` — вторая сторона того же договора в другом проекте (займ между своими юрлицами); partial INDEX `WHERE mirror_loan_id IS NOT NULL`.

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

### Два разных вопроса из одних данных
Экраны займов отвечают на два вопроса, и их нельзя мешать:
1. **«Кому и сколько заплатить»** — по ДОГОВОРНЫМ периодам: частные займы 25→25, ВКЛ — календарный
   месяц с платежом 5-го числа следующего, аннуитет — по своему графику. Живёт на вкладке
   «Заёмщики», в карточке линии и в графике платежей.
2. **«Сколько стоят деньги»** — по КАЛЕНДАРНЫМ месяцам, начисление по дням: дашборд и
   `loan_analytics.accrual_by_month` (база для ОПиУ). Период 25.06→25.07 шестью днями лежит в июне,
   складывать периоды выплат в один P&L нельзя.

### График платежей (`services/loan_schedule.py`)
`GET/PUT /loans/{id}/schedule` — плановый график из договора (аннуитет) со сверкой факта.
- **Аннуитет не выводится формулой.** Платёж фиксирован, а деление на тело и проценты банк считает
  своим округлением, плюс даёт льготные дни: у Симпл Финанса первые 7 дней процентов нет — за них
  взята комиссия за выдачу 4,25 %. Поэтому график хранится как факт договора, а
  `loan_interest.schedule_interest_in_window` раскладывает проценты СТРОКИ по календарным месяцам
  пропорционально дням (внутри строки проценты линейны — тело и ставка постоянны).
- **Матч факта — по ближайшей плановой дате** (`MATCH_TOLERANCE_DAYS=20`), не по порядку и не по
  сумме: платят и раньше срока (24.04 при плане 27.04), и позже (26.05 при плане 25.05), суммы
  аннуитета одинаковые, а деньги ходят с РАЗНЫХ счетов одного холдинга.
- **График бывает ТОЛЬКО по телу.** У аннуитета в строках и тело, и проценты; у кредитной
  линии график — это возврат траншей (каждый на 180 дней), а проценты платят помесячно по факту
  выборки. Поэтому правило не «есть график → проценты из него», а `_schedule_has_interest`:
  если процентных строк нет, проценты по-прежнему считает движок, а из графика берётся только
  тело. Иначе линия потеряла бы 1,6 млн ₽ в месяц и в стоимости денег, и в ОПиУ. В прогнозе для
  такого графика проценты считаются на УБЫВАЮЩЕМ теле: run-rate начислял бы на транш, который
  уже вернулся.
- `is_fee` на строке — «в колонке процентов напечатана комиссия». В `total_interest` она остаётся
  (так в договоре), а в стоимость денег заходит через `LoanFee`, иначе расход задвоится.

### Займ между своими проектами (`Loan.mirror_loan_id`, `services/loan_mirror.py`)
Займ учредителя (ИП → своё ООО) — одна сделка и ДВЕ книги: у ООО долг (`INCOMING`),
у ИП актив (`OUTGOING`). Одной строкой это не описать — правило «каждый запрос фильтрует
`project_id`» иначе ломается. Поэтому записи две, а `mirror_loan_id` держит их вместе.
- `POST /loans/{id}/mirror` — завести вторую сторону в другом проекте: направление
  переворачивается, реквизиты и ставки копируются, контрагент ищется по ИНН или создаётся
  (`primary_type=AFFILIATED`).
- `POST /loans/{id}/mirror/sync` — свести движения и ставки к ОБЪЕДИНЕНИЮ. Копируем в обе
  стороны: платёж могли завести с любой книги, «источника истины» тут нет. Идемпотентно по
  ключу `(payment_type, amount, paid_at)`. **`transaction_id` НЕ копируется** — банковская
  проводка принадлежит книге своего проекта и уникальна на неё.
- `GET /loans/chains` и `GET /loans/{id}/chain` — договор целиком: обе стороны, движения,
  начисление по календарным месяцам. `in_sync=false` + `sync_note` подсвечивают расхождение.
- **UI: отдельного раздела у таких займов НЕТ.** Для проекта это обычный заимодавец — он живёт
  в Реестре и «Заёмщиках» наравне с банками, а факт второй стороны показывает блок
  `LoanMirrorBlock` на карточке самого займа (канон юзера 2026-07-27: вкладку «Между проектами»
  убрали как параллельный экран ради одной записи).
- `principal` у таких займов = 0: тело задают движения (см. `LoanBase.principal` — `ge=0`).

Боевой случай — займ ИП Вяткина в ООО «Плюс Вайб» (`scripts/seed_vyatkin_pv_loan.py`):
револьверный, до востребования, 2 % годовых до 31.10.2025 и 27 % с 01.11.2025, 118 движений
из карточки 67.03. Движок сходится с бухгалтерией: за период 04.03–30.09.2025 начисление
даёт ровно уплаченные 210 682,08 ₽, а на 30.06.2026 расхождение с 1С — 2 копейки.

### Разовые комиссии (`LoanFee`)
`GET/POST/DELETE /loans/{id}/fees`. Комиссия за выдачу (4,25 % = 1 275 000 ₽) и за установление
лимита (0,25 % = 252 500 ₽) — плата за доступ к деньгам на ВЕСЬ срок, а не расход одного дня.
- `amortize=True` (дефолт) → расход размазывается по дням окна `[amortize_from, amortize_to]`
  (пусто → срок займа). Считается через накопленную сумму на границах окна, поэтому сумма долей
  по месяцам ровно равна комиссии, без копеечного хвоста.
- `amortize=False` → падает целиком в месяц `charged_at`.
- Касса при этом не меняется: деньги живут в `LoanPayment` типа COMMISSION, `payment_id` их связывает.

### Импорт реестра займов из Excel (`services/loan_import.py`)
`POST /loans/import` — лист с колонками Дата/Тип/Сумма/Контрагент/Номер договора/Ставка/От/До/Статус
(«Приход» → `Loan`, «Возврат» → `LoanPayment` PRINCIPAL_REPAY + закрытие). Идемпотентно по
`(project_id, counterparty_id, contract_number, start_date)` — один номер договора может нести
несколько траншей. Сущность (физ/ИП) и банк — со второго листа по имени контрагента, best-effort:
слово «Контрагент» там встречается ДВАЖДЫ, и физ/ИП с банком относятся к списку, стоящему
вплотную слева от колонок «куда», а не к первому вхождению (служебный столбец графика выплат).
`_build_entity_map` берёт `max(колонки «Контрагент» левее первой «куда»)` — с `header.index()`
сущность не проставлялась ни у кого, кроме одного заёмщика (224 займа из 226 приезжали пустыми).
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
