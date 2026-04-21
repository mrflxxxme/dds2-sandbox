# Handoff: Спецификация «Контрагенты + Займы»

**Дата:** 2026-04-21
**Статус:** готово к запуску `/spec`

## Контекст

Пользователь прислал две банковские выписки:
1. `/Users/a1/Desktop/VTB_BankStatement_some_accounts_01.03.2026-20.04.2026_39321.xlsx` — ВТБ, 4 счёта (RUB + CNY)
2. `/Users/a1/Desktop/statement_21042026.xls` — WB Банк (XML Faktura.ru формат)

В ходе анализа выявлено:
- **VTB выписка:** 11 уникальных ИНН, 457 млн оборот, 98 млн платежей китайским поставщикам в CNY
- **WB Банк выписка:** 117 уникальных ИНН, 242 млн оборот, 90 ИП-перевозчиков + 7 фулфилментов
- Основные пробелы в DDS: нет справочника контрагентов, нет модели займов, нет связи Warehouse↔Counterparty, нет парсера Faktura.ru, в P&L неправильно учитываются депозиты и займы

## Задача

Реализовать большую фичу «Справочник контрагентов + Займы + Парсер Faktura.ru + Отчёт по оборотам».

## Финальный scope

### Новые модели
- **Counterparty** — справочник (project_id, inn UNIQUE, name, primary_type, secondary_types[], notes, contacts)
- **Loan** — займы (counterparty_id, direction INCOMING/OUTGOING/AFFILIATED, principal, rate, status, contract_number, dates)
- **LoanPayment** — связь Loan ↔ Transaction (type: DISBURSEMENT/PRINCIPAL_REPAY/INTEREST_PAY/PENALTY)
- **CounterpartyDocument** — документы (minio_path, doc_type: CONTRACT/CERTIFICATE/INVOICE/OTHER)

### Расширения существующих моделей
| Модель | Новые поля |
|---|---|
| `Transaction` | `counterparty_id` FK, `contract_number`, `unk_number`, `loan_id` FK, `loan_payment_type` enum |
| `Supplier` (supply_chain.py) | `inn`, `contract_number` UNIQUE, `counterparty_id` FK |
| `Warehouse` (warehouse.py) | `counterparty_id` FK |
| `CounterpartyCategory` (refs.py) | `cp_key` → `counterparty_id` FK (ПОЛНАЯ миграция) |
| `EventType2` enum | +DEPOSIT_PLACE, +DEPOSIT_RETURN, +DEPOSIT_INTEREST |

### Категории контрагентов (Counterparty.primary_type)

13 значений:
```
SUPPLIER, FULFILLMENT, CARRIER, CUSTOMS_BROKER, DESIGNER, LEGAL,
LANDLORD, IT_SERVICE, MARKETPLACE, BANK, GOVERNMENT, AFFILIATED, OTHER
```

- **Мультироль:** Counterparty имеет `primary_type` (основной) + `secondary_types[]` (дополнительные). Пример: ИП Кузнецов — primary=FULFILLMENT, secondary=[CARRIER]
- **Категория платежа** определяется по regex в `Transaction.purpose`, НЕ по типу контрагента. Примеры ключевых слов:
  - FULFILLMENT: "упаковка|маркировка|фулфилмент|ФФ-|фф "
  - CARRIER: "доставка|транспортные|перевозка|экспедиц"

### ETL изменения
1. **Новый парсер** `FAKTURA_WB_BANK` в `backend/etl/parsers/faktura.py` для формата Excel 2003 XML (Faktura.ru). Добавить в `SOURCE_PARSERS` registry.
2. **Regex в master_logic.py:**
   - `CONTRACT\s+(?:NO\.)?(\d{6,})` → `contract_number`
   - `УНК\s+([\d/]+)` → `unk_number`
   - `Размещение средств в депозит` → event_type2=DEPOSIT_PLACE, is_cashflow2=0
   - `Возврат депозита` → DEPOSIT_RETURN, is_cashflow2=0
   - `Уплата процентов по депозиту` → DEPOSIT_INTEREST, is_cashflow2=1
   - `Предоставление денежных средств по Договору.*займа` → создать LoanPayment.DISBURSEMENT
   - `Оплата по договору.*займа` → LoanPayment.PRINCIPAL_REPAY
   - `Уплата процентов по.*займ` → LoanPayment.INTEREST_PAY
3. **Автосоздание Counterparty** по ИНН при импорте (UPSERT по (project_id, inn)):
   - Если существует: обновить `name` если новое длиннее (полнее)
   - Если нет: создать с `primary_type=OTHER`, пометкой `created_by_import=true`
   - Китайские поставщики (ИНН нет) — identify по `contract_number`

### Backfill (один идемпотентный скрипт)

`scripts/backfill_counterparties.py --project-id=X`:
1. Пройти по всем Transaction → для каждого уникального ИНН создать Counterparty
2. Перепривязать `CounterpartyCategory.cp_key` → `counterparty_id` (FK)
3. Определить `primary_type` для известных ИНН из хардкод-мапы (см. анализ выписок):
   - 7704217370 → MARKETPLACE (OZON)
   - 9714053621 → MARKETPLACE (WB/РВБ)
   - 9707021650 → BANK (ВБ Финанс)
   - 7702070139 → BANK (ВТБ)
   - 7727406020 → GOVERNMENT (ФНС)
   - 7730176610 → GOVERNMENT (ФТС)
   - 7703381225 → GOVERNMENT (Росприроднадзор)
   - 7703381419 → BANK (МКК Симплфинанс)
   - 370212932304 → AFFILIATED (ИП Вяткин)
4. CustomsTopup: backfill `counterparty_id` на ФТС
5. Проставить FK `Transaction.counterparty_id` для всех записей

### UI изменения

| Страница | Действие |
|---|---|
| `frontend-react/src/app/(main)/p/[slug]/refs/counterparty/` | **Новая**: список с фильтрами (тип, период, только активные), карточка с календарём, табами "Статистика/Платежи/Документы/Займы", статистика в RUB и CNY раздельно |
| `frontend-react/src/app/(main)/p/[slug]/refs/loans/` | **Новая**: «Займы и кредиты», фильтры Получен./Выдан./Внутренние/Закрытые, график платежей |
| `frontend-react/src/app/(main)/p/[slug]/warehouse/` | +dropdown "Контрагент" (поиск по ИНН/имени) |
| `frontend-react/src/app/(main)/p/[slug]/supply-chain/` (или где Supplier) | +поля ИНН, Номер контракта |
| `frontend-react/src/app/(main)/p/[slug]/import/page.tsx` | +тип источника "WB Банк (Faktura.ru)" → FAKTURA_WB_BANK |
| `frontend-react/src/app/(main)/p/[slug]/reports/` | +отчёт "Обороты по контрагентам" (pivot месяц × контрагент, мультивалюта) |

### Sidebar (layout.tsx)
```
ФИНАНСЫ
  📥 Импорт документов
  💳 Операции
  🔴 INBOX
  📈 Отчёты
СПРАВОЧНИКИ  ← новая секция
  👥 Контрагенты  (новая)
  💸 Займы и кредиты  (новая)
  📦 Склады  (существующая)
  🏭 Поставщики  (существующая)
```

### API endpoints (новые)
```
GET    /api/v1/counterparties              список с фильтрами
GET    /api/v1/counterparties/{id}         карточка + stats за период
POST   /api/v1/counterparties              ручное создание
PATCH  /api/v1/counterparties/{id}
DELETE /api/v1/counterparties/{id}         soft delete (archive)
POST   /api/v1/counterparties/{id}/documents upload
GET    /api/v1/counterparties/{id}/documents list
DELETE /api/v1/counterparties/{id}/documents/{doc_id}

GET    /api/v1/loans
GET    /api/v1/loans/{id}
POST   /api/v1/loans
PATCH  /api/v1/loans/{id}
POST   /api/v1/loans/{id}/payments/match   manual match transaction

GET    /api/v1/reports/counterparty-turnovers?date_from=&date_to=&type=
```

### Тесты
- Unit: regex-паттерны (contract_number, УНК, депозиты, займы), категоризатор по purpose
- Integration: парсер Faktura.ru (на реальном файле), автосоздание Counterparty при импорте, backfill script
- E2E: создание займа → привязка транзакции → отображение на карточке контрагента

## Решения пользователя (финализированы)

| Вопрос | Решение |
|---|---|
| Backfill старых транзакций | Автоматически через скрипт |
| Счета контрагентов | НЕ хранить, только ИНН |
| Конфликты при повторном импорте | Тихо обновлять имя (самое длинное) |
| Госорганы | Заводить как обычных контрагентов |
| Документы | Загружаемые файлы через MinIO |
| Мелкие ИП-перевозчики | Все заводить автоматически |
| Китайские поставщики | Идентификация по contract_number |
| Обогащение из ЕГРЮЛ | НЕ делать |
| Период статистики на карточке | Календарь (произвольный период) |
| Мультивалюта | RUB и CNY раздельно (без конвертации в RUB) |
| Permissions | Пока все могут всё |
| Миграция CounterpartyCategory | Полная: cp_key → counterparty_id |
| УНК валютного контроля | Хранить как Transaction.unk_number |
| Депозиты | Включить в эту спеку (новые EventType2) |
| FactoryOrder ↔ Transaction | НЕ в этой спеке (отложено) |
| Уведомления о крупных платежах | НЕ в этой спеке |

## НЕ делаем в этой спеке (отложено)

- ❌ FactoryOrder ↔ Transaction (связь оплат с заказами) — отдельная спека
- ❌ Уведомления / алерты — нужна инфра notifications
- ❌ Обогащение из ЕГРЮЛ / ФНС API
- ❌ Несколько счетов у одного контрагента
- ❌ Прогноз регулярных платежей (аренда, подписки)
- ❌ Геолокация логистов
- ❌ Fine-grained permissions для справочников

## Оценка срока

7-10 рабочих дней. Можно параллелить backend + frontend через TeamCreate.

## Команда для старта в новой сессии

```
@.claude/handoff/spec-counterparties-loans.md
Запусти /spec на основе этого документа. Параллельные explorations не нужны — всё решено.
```

После готовности SPEC.md / PLAN.md / TESTS.md — ревью и TeamCreate для параллельной реализации.

## Критичные пути файлов (для быстрой ориентации)

### Backend модели (куда добавлять поля)
- `backend/models/transactions.py` — Transaction, EventType2 enum
- `backend/models/refs.py` — CounterpartyCategory, Account (is_our_account уже есть)
- `backend/models/supply_chain.py` — Supplier, FactoryOrder
- `backend/models/warehouse.py` — Warehouse
- `backend/models/customs.py` — CustomsTopup (для ФТС backfill)

### Backend ETL
- `backend/etl/master_logic.py` — где regex и логика обогащения
- `backend/etl/service.py` — оркестратор import_statement
- `backend/etl/parsers/__init__.py` — SOURCE_PARSERS registry
- `backend/etl/parsers/` — новый файл `faktura.py` для XML Spreadsheet 2003

### Backend API/сервисы
- `backend/routers/` — новые файлы counterparty.py, loans.py
- `backend/services/` — counterparty_service.py, loan_service.py
- `backend/schemas/` — counterparty.py, loan.py (Pydantic)
- `backend/services/reports/` — counterparty_turnovers.py

### Frontend
- `frontend-react/src/app/(main)/p/[slug]/refs/counterparty/page.tsx` — новая
- `frontend-react/src/app/(main)/p/[slug]/refs/loans/page.tsx` — новая
- `frontend-react/src/app/(main)/p/[slug]/layout.tsx` — sidebar (добавить секцию СПРАВОЧНИКИ)
- `frontend-react/src/app/(main)/p/[slug]/import/page.tsx` — +источник FAKTURA_WB_BANK
- `frontend-react/src/app/(main)/p/[slug]/warehouse/` — dropdown контрагента
- `frontend-react/src/lib/api/counterparty.ts` — новый (модули API клиента)
- `frontend-react/src/lib/api/loans.ts` — новый
- `frontend-react/src/types/api.ts` — интерфейсы Counterparty, Loan, и т.д.

### Миграции
- `migrations/versions/` — новая миграция для всех новых таблиц и полей
- `scripts/backfill_counterparties.py` — новый скрипт

## Справочные данные из анализа выписок

### Контрагенты по категориям (топ, для backfill и валидации)

**SUPPLIER** (китайские поставщики, по contract_number):
- 20250707 — текстиль (12 платежей, 5.66M CNY)
- 20260317 — тенты/мебель (7 платежей, 1.11M CNY)
- 20250801 — PVC (4 платежа, 1.07M CNY)
- 20260331 — бескаркасные диваны
- 20260319 — пароочистители

**FULFILLMENT** (7 шт):
- ООО ТРИКОТАЖ НАТАЛИ (3702703225)
- ИП Перфильев А.В. (550508916039)
- ИП Кожонов А.Н. (370226099442)
- ИП Кузнецов А.К. (772975711121) — мультироль с CARRIER
- ИП Крикова М.С. (503818940420)
- ИП Владимирова Я.Е. (370254749999)
- ООО ЭФЭМДЖИ ШИППИНГ (7839353264) — мультироль

**CARRIER** (90+ ИП, топ):
- ИП Бабанова Н.С. (371301792940)
- ИП Карапетян С.Г. (930802589360)
- ООО ЭФЭМДЖИ ШИППИНГ (7839353264)
- ИП Кузнецов А.К. (мультироль)
- ООО АР-ТРАНС (3906154245)

**MARKETPLACE:**
- 9714053621 — РВБ ООО (Wildberries)
- 7704217370 — ООО "Интернет Решения" (OZON)

**GOVERNMENT:**
- 7727406020 — ФНС (Казначейство)
- 7730176610 — ФТС (Таможня)
- 7703381225 — Росприроднадзор
- 7730176088 — Росимущество (Роспатент)
- 3731001044 — ОСФР

**BANK:**
- 7702070139 — Банк ВТБ
- 9707021650 — МКК ВБ Финанс (Wildberries)
- 7703381419 — МКК Симплфинанс

**AFFILIATED:**
- 370212932304 — ИП Вяткин (собственник)
- 1800027275 — ООО Плюс Вайб (сам пользователь, отфильтровать из counterparty)

**LANDLORD:**
- 370503709798 — ИП Гаврош (63К/мес)
- 371100232105 — ИП Курылева (63К/мес)

**DESIGNER / Фрилансеры:**
- 643921964912 — Щеткина А.М. (15 операций графдизайна)
- 450163723834 — Горбачева К.К.

**CUSTOMS_BROKER:**
- 7731197997 — ООО КВ ТЕРМИНАЛ
- 370229295741 — Грекова А.М. (физлицо)

**LEGAL / Патенты:**
- 165719153207 — ИП Измайлов (патенты)
- 4345497285 — ООО Патентное Бюро Железно

**IT_SERVICE:**
- 7816397410 — ООО АТИ.СУ
- 182709205140 — ИП Глухова (БульДок)
- 9729366786 — ООО ЭД СЕТЬ (АСВД ЦИТТУ)

### Структура выписки WB Банка (Faktura.ru)

XML Spreadsheet 2003 (Excel 2003 XML):
```xml
<?xml version="1.0" encoding="utf-8"?>
<?mso-application progid="Excel.Sheet"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet">
  <Worksheet ss:Name="Выписки">
    <Table>
      <Row>
        <Cell><Data ss:Type="String">Плат.пор. № XXX от ДД.ММ.ГГГГ</Data></Cell>
        <Cell><Data>ДД.ММ.ГГГГ</Data></Cell>  <!-- дата -->
        <Cell><Data>Корреспондент</Data></Cell>
        <Cell><Data>ИНН</Data></Cell>
        <Cell><Data>КПП</Data></Cell>
        <Cell><Data>Счёт</Data></Cell>
        <Cell><Data>БИК</Data></Cell>
        <Cell><Data>Вх.остаток</Data></Cell>
        <Cell><Data>Дт (расход)</Data></Cell>
        <Cell><Data>Кт (приход)</Data></Cell>
        <Cell><Data>Назначение</Data></Cell>
      </Row>
    </Table>
  </Worksheet>
</Workbook>
```

Парсер: ElementTree или lxml. Namespace `urn:schemas-microsoft-com:office:spreadsheet`.

### Ключевые regex для master_logic

```python
# Депозиты
RE_DEPOSIT_PLACE = re.compile(r'Размещение средств в депозит', re.I)
RE_DEPOSIT_RETURN = re.compile(r'Возврат депозита', re.I)
RE_DEPOSIT_INTEREST = re.compile(r'Уплата процентов по депозиту', re.I)

# Китайские поставщики / международка
RE_CONTRACT = re.compile(r'CONTRACT\s+(?:NO\.)?(\d{6,})', re.I)
RE_UNK = re.compile(r'УНК\s+([\d/]+)', re.I)
RE_MT103 = re.compile(r'МТ103\s*реф\.([A-Z0-9]+)', re.I)
RE_ANNEX = re.compile(r'(?:ANNEX|APPENDIX|ПРИЛОЖЕН\w*)\s*[№#]?\s*(\d+(?:/\d+)?)', re.I)

# Займы
RE_LOAN_DISBURSEMENT = re.compile(r'(?:Предоставление|Выдача|Перевод).*договор.*займ', re.I)
RE_LOAN_REPAY = re.compile(r'(?:Оплата|Возврат).*договор.*займ', re.I)
RE_LOAN_INTEREST = re.compile(r'Уплата процентов.*займ', re.I)
RE_LOAN_CONTRACT_ID = re.compile(r'(?:Договор\w*|договор\w*)\s*[№#]?\s*([A-Z0-9/\-]+)', re.I)

# Категоризация по purpose
RE_FULFILLMENT = re.compile(r'упаковк|маркировк|фулфилмент|ФФ-\d|фф\s', re.I)
RE_LOGISTICS = re.compile(r'доставк|транспортн|перевоз|экспедиц|форвардин|forwarding|freight', re.I)
```
