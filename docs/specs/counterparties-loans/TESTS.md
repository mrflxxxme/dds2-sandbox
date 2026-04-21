# Counterparties + Loans — Test Plan

**Spec:** [SPEC.md](./SPEC.md) · **Plan:** [PLAN.md](./PLAN.md)

## Test Strategy (pyramid)

| Слой | Доля | Инструмент | Скорость | Что тестируем |
|---|---|---|---|---|
| Unit | 70% | pytest (backend) / vitest (frontend) | ms | чистая логика: regex, парсер, сервисы (с mock DB где возможно), компоненты |
| Integration | 25% | pytest + real PG + MinIO | s | endpoint↔DB↔MinIO, ETL pipeline, backfill |
| E2E smoke | 5% | Playwright | min | критические UI flow: создание КА, привязка платежа займа, импорт Faktura |

### Coverage targets

- Backend (новый код): **≥ 80%** строк, ≥ 70% branches.
- Frontend (новый код): **≥ 70%** строк.
- Regex pipeline: **100%** (каждый паттерн + negative case).
- Codecov upload уже настроен в `test.yml` (коммит `d892f45`).

## Backend Tests

### 1. `tests/test_counterparty_service.py`

**Unit, real PG (conftest session fixture).**

| Case | Ожидание |
|---|---|
| `upsert_by_inn(new_inn)` | создаётся Counterparty с `primary_type=OTHER`, `created_by_import=True` |
| `upsert_by_inn(existing, name_longer)` | `name` обновляется, `primary_type` НЕ перезаписывается |
| `upsert_by_inn(existing, name_shorter)` | `name` НЕ меняется |
| `upsert_by_inn` с тем же ИНН в двух проектах | создаются **разные** Counterparty (multi-tenancy) |
| `upsert_by_contract(contract_20250707)` | создаётся без ИНН, китайский поставщик |
| `list(type=FULFILLMENT)` | возвращает только фулфилменты |
| `list(q="кузнец")` | поиск ILIKE работает, `%` в input escaped |
| `list(active_only=True)` | не возвращает is_deleted=True |
| `stats(id, date_from, date_to, currency=RUB)` | агрегирует только RUB транзакции |
| `stats` с периодом без транзакций | возвращает нулевые значения, не падает |
| `create(duplicate_inn)` | raises `CounterpartyConflictError` → HTTP 409 |
| `soft_delete` | row есть, `is_deleted=True`, `deleted_at` установлен |
| `get()` чужого project | raises `NotFoundError` → 404 |

### 2. `tests/test_loan_service.py`

| Case | Ожидание |
|---|---|
| `create(direction=OUTGOING, principal=1_000_000)` | Loan создан, status=ACTIVE |
| `create(principal=0)` | 422 валидация |
| `match_transaction(tx_from_other_project)` | `ProjectMismatchError` → 403 |
| `match_transaction(tx_already_matched)` | `LoanPaymentAlreadyExists` → 409 |
| `match_transaction` + tx `counterparty_id != loan.counterparty_id` | warning в лог, но создаётся (ручная привязка — не всегда совпадает КА) |
| `auto_link_from_etl(matched_loan_disbursement)` | создаёт LoanPayment, связывает tx |
| `auto_link_from_etl(no_matching_loan)` | возвращает None, tx без loan_id но с loan_payment_type |

### 3. `tests/test_master_logic_regex.py`

Unit, без DB. Каждый паттерн — таблица кейсов.

| Regex | Positive | Negative |
|---|---|---|
| `RE_CONTRACT` | `"CONTRACT NO.20250707"` → `20250707`, `"CONTRACT 20260317"` → `20260317`, `"contract no. 20250801"` → `20250801` | `"contract of sale"`, `"no contract"`, `"CONTRACT-123"` (dashed) — no match |
| `RE_UNK` | `"УНК 24093000/0423/0000/2/1"` → `24093000/0423/0000/2/1` | `"unknown"`, `"УНКа"` |
| `RE_DEPOSIT_PLACE` | `"Размещение средств в депозит 1М"` | `"Возврат депозита"` |
| `RE_DEPOSIT_RETURN` | `"Возврат депозита по договору..."` | `"Размещение"` |
| `RE_DEPOSIT_INTEREST` | `"Уплата процентов по депозиту"` | `"Проценты по займу"` |
| `RE_LOAN_DISBURSEMENT` | `"Предоставление денежных средств по Договору займа №3"` | `"Возврат займа"` |
| `RE_LOAN_REPAY` | `"Оплата по договору займа"` | `"Выдача займа"` |
| `RE_LOAN_INTEREST` | `"Уплата процентов по договору займа"` | `"Проценты по депозиту"` |
| `RE_FULFILLMENT` | `"упаковка товара"`, `"маркировка WB"`, `"ФФ-12"`, `"фф услуги"` | `"фундамент"`, `"фасовка бумаг"` |
| `RE_LOGISTICS` | `"доставка груза"`, `"транспортные услуги"`, `"экспедиция"`, `"forwarding fee"` | `"доставка пиццы в офис"` (ложное попадание — документируем) |

**Полный coverage:** 100% regex branch.

### 4. `tests/test_faktura_parser.py`

Fixture: `tests/fixtures/faktura_wb_bank_sample.xml` — 10 анонимизированных строк, репрезентативные:
- Обычный перевод (ИП)
- Налоговый платёж (ФНС)
- Депозит (размещение)
- Займ выдача
- Возврат НДС
- Комиссия банка (без ИНН)

| Case | Ожидание |
|---|---|
| `parse(valid_file_bytes)` | возвращает 10 `ParsedTransaction` |
| Все 10 ИНН извлечены | match ожидаемому списку |
| Суммы (Дт/Кт) | знак правильный, Numeric(18,2) не Float |
| Даты | парсятся в `date` (не `datetime`) |
| Purpose | полностью извлечён включая переносы строк |
| `parse(empty_xml)` | возвращает `[]` |
| `parse(malformed_xml)` | raises `FakturaParseError` |
| `parse(wrong_namespace)` | raises `FakturaParseError("unsupported format")` |
| `parse(not_xml)` | raises `FakturaParseError` |

### 5. `tests/test_counterparty_turnovers_report.py`

Seed: 5 контрагентов, 60 транзакций за 3 месяца (RUB+CNY).

| Case | Ожидание |
|---|---|
| `generate(period=3mo)` | 5 rows × 3 months, totals корректны |
| `generate(type=FULFILLMENT)` | только фулфилменты |
| `generate(currency=CNY)` | только CNY транзакции |
| 2-й запрос в рамках 300s | возвращается из cache (быстрее на порядок) |
| После `create(new_tx)` | cache invalidated, следующий запрос свежий |
| `min_turnover=100_000` | фильтрация rows с оборотом ниже порога |
| Пустой период | rows=[], period корректен |

### 6. `tests/test_backfill_script.py`

Seed: 3 Counterparty (1 уже существует с правильным type, 2 новых), 50 транзакций, 10 CounterpartyCategory.

| Case | Ожидание |
|---|---|
| 1-й run | created=2, updated=0 (existing не дублирован), tx_linked=50 |
| 2-й run без изменений | created=0, updated=0, tx_linked=0 (идемпотентен) |
| ФНС/ФТС в seed | получают `primary_type=GOVERNMENT` из хардкод-мапы |
| CustomsTopup row | `counterparty_id` = ID ФТС Counterparty |
| CounterpartyCategory.cp_key без match | warning в лог, `counterparty_id=NULL` |
| `--dry-run` | no writes (assert row counts не изменились) |
| Run для 10К транзакций | <60s (benchmark) |

### 7. Integration: `tests/test_counterparties_api.py`

Real FastAPI TestClient + PG + MinIO.

| Case | Status | Assertion |
|---|---|---|
| POST new counterparty | 201 | id в ответе, rate_limit_write применён |
| POST dup INN same project | 409 | `{code: "inn_conflict"}` |
| POST same INN different project | 201 | multi-tenancy |
| POST invalid type | 422 | field validation |
| GET list с фильтром type | 200 | только FULFILLMENT |
| GET list с q="Кузнец" | 200 | ILIKE работает |
| GET list с q="%Кузнец" (injection попытка) | 200 | `%` escaped, не падает |
| GET list чужого project_id | 200 | пустой список (middleware filter) |
| GET detail с period | 200 | stats_rub, stats_cny корректны |
| PATCH name | 200 | изменён |
| DELETE | 204 | `is_deleted=True`, GET → 404 |
| POST/GET/DELETE documents | 201/200/204 | MinIO object создан/удалён |
| Upload .exe file | 415 | `mime_not_allowed` |
| Upload 25MB file | 413 | `file_too_large` |

### 8. Integration: `tests/test_loans_api.py`

Аналогично counterparties + `POST /loans/{id}/payments/match` кейсы.

### 9. Integration: `tests/test_faktura_import_e2e.py`

| Case | Ожидание |
|---|---|
| POST `/import/statement` FAKTURA_WB_BANK + real file | 201, imported >0, counterparties_created >0 |
| 2-й POST того же файла | imported_count=0, skipped_dedup=N (по txn_id) |
| ИНН впервые встречается | Counterparty upsert'нут с `created_by_import=true` |
| Транзакция с RE_DEPOSIT_PLACE | `event_type2=DEPOSIT_PLACE`, `is_cashflow2=false` |
| Транзакция с contract_number | `transaction.contract_number` заполнен |

## Frontend Tests

### 10. `frontend-react/tests/counterparty.test.tsx`

Vitest + React Testing Library.

| Case | Ожидание |
|---|---|
| Список рендерится с данными | 5 карточек, badge правильного типа |
| Skeleton при loading | `data-testid="skeleton"` видим |
| ErrorBoundary при API fail | retry-кнопка |
| EmptyState при пустом списке | CTA «Добавить» видна |
| Фильтр type | после change query в URLSearchParams |
| Поиск debounced (300ms) | один request, не 5 |
| Excel export | скачивается файл (jsdom mock) |
| Карточка: DateRangePicker change | re-fetch stats |
| Карточка: переключение Tab | корректный контент |
| Модалка создания: invalid INN | показывает inline error |
| Мутация → invalidate React Query cache | список рефетчится |

### 11. `frontend-react/tests/loans.test.tsx`

Аналогично + LoanScheduleChart рендерится с моковыми данными.

### 12. `frontend-react/tests/components/*`

| Компонент | Тест |
|---|---|
| `CounterpartyTypeBadge` | 13 типов → 13 различных классов/цветов |
| `LoanScheduleChart` | рендерит recharts ResponsiveContainer + правильные axis labels |
| `DocumentUploader` | drag-drop file, progress bar, ошибка при >20MB |
| `CurrencySplitStats` | 2 колонки, formatNumber применён, 0/negative не сломаны |

## E2E (Playwright, nightly)

### 13. `frontend-react/tests/e2e/counterparty.spec.ts`

```
test('smoke: counterparty list loads', async ({page}) => {
  await page.goto('/p/plus-vibe/refs/counterparty');
  await expect(page.locator('[data-testid="counterparty-list"]')).toBeVisible();
  await expect(page.locator('[data-testid="counterparty-row"]')).toHaveCount({greaterThan: 0});
});

test('happy: create counterparty', async ({page}) => {
  await page.goto('/p/plus-vibe/refs/counterparty');
  await page.click('text=Добавить');
  await page.fill('[name=inn]', '7704217370');
  await page.fill('[name=name]', 'Тест КА');
  await page.selectOption('[name=primary_type]', 'MARKETPLACE');
  await page.click('text=Сохранить');
  await expect(page.locator('text=Тест КА')).toBeVisible();
});
```

Запуск только в `e2e-nightly.yml`, не блокирует deploy.

### 14. `frontend-react/tests/e2e/loan.spec.ts`

`create loan → attach transaction → see in schedule` — happy path.

### 15. Update `frontend-react/tests/e2e/smoke.spec.ts`

+2 строки в массив smoke-страниц:
```
'/p/plus-vibe/refs/counterparty',
'/p/plus-vibe/refs/loans',
```

## Performance benchmarks (в тестах + pytest-benchmark)

| Operation | p95 target |
|---|---|
| `GET /counterparties` (500 rows) | <200ms |
| `GET /counterparties/{id}` + stats 3mo | <300ms |
| `GET /reports/counterparty-turnovers` (200 КА × 12 мес) с cache | <1s (first), <50ms (cached) |
| `backfill` 10K tx | <60s |
| `FakturaParser.parse` 1000 rows | <500ms |

```python
# tests/test_perf_counterparty_turnovers.py
@pytest.mark.benchmark
def test_turnovers_under_1s(benchmark, seed_large_project):
    result = benchmark(turnovers_report.generate, project_id=1, date_from=..., date_to=...)
    assert benchmark.stats['mean'] < 1.0
```

## Regression guard (перед коммитом Phase 2 → Phase 3)

Все должны pass:

```bash
# Backend
docker compose exec backend pytest tests/ -x               # 1184+ existing + ~40 new
docker compose exec backend pytest tests/ --cov=backend --cov-fail-under=80
bash scripts/check_conventions.sh

# Frontend
cd frontend-react
npx vitest run --coverage
npx playwright test tests/e2e/smoke.spec.ts

# Integration
docker compose exec backend alembic downgrade -1
docker compose exec backend alembic upgrade head
docker compose exec backend python -m scripts.backfill_counterparties --project-id 1 --dry-run
```

## Известные негативные кейсы (акцент для security-reviewer)

- ILIKE injection через `%`/`_` в `q` параметре.
- File upload с MIME spoofing (`.pdf` с `.exe` содержимым) → magic-byte check.
- Загрузка файла в counterparty чужого проекта — 403.
- SQL inject через `contract_number` regex capture → параметризованный bind.
- Enumeration attack через `GET /counterparties/{id}` (чужой id) — должен быть 404 не 403 (не утекать существование).
- Rate limit bypass на upload через множество параллельных запросов (rate_limit_write должен держать).

## Test data fixtures

Новые `conftest.py` fixtures:
- `sample_counterparties` — 5 КА разных типов
- `sample_loans` — 3 займа (INCOMING, OUTGOING, AFFILIATED)
- `sample_transactions_with_counterparty` — 50 tx со всеми связями
- `faktura_sample_xml_bytes` — инлайн XML для парсера
- `mock_minio_client` — для document tests без real MinIO (используется только в unit; integration = real)
