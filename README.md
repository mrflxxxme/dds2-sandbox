# ДДС — Система управленческого учёта

Веб-приложение для замены хрупкой логики Google Sheets.  
Архитектура повторяет вашу систему: **DDS_01 → DDS_02 → DDS_03**.

---

## Архитектура

```
┌────────────────────────────────────────────────────────┐
│  NEXT.JS FRONTEND  (порт 3000)                         │
│  ├── 🏠 Дашборд        ← остатки, ДДС, кэшфлоу       │
│  ├── 📥 Импорт выписок ← загрузка .xlsx банков        │
│  ├── 📋 Операции       ← поиск/фильтрация             │
│  ├── 🔴 INBOX          ← неразнесённые → разноска     │
│  ├── 📊 Отчёты         ← ДДС, баланс, FX, таможня    │
│  ├── 📦 Планирование   ← заказы, платежи, кэшфлоу    │
│  ├── 💰 Себестоимость  ← заказы, номенклатура, пошлины │
│  ├── 📈 Воронка продаж ← WB аналитика + реклама       │
│  └── ⚙️ Справочники    ← счета, категории, overrides  │
└───────────────────┬────────────────────────────────────┘
                    │ REST API (JSON)
┌───────────────────▼────────────────────────────────────┐
│  FASTAPI BACKEND  (порт 8000)                          │
│  ├── /api/import/     ← ETL pipeline                  │
│  ├── /api/transactions/ ← поиск, категоризация        │
│  ├── /api/refs/       ← справочники                   │
│  ├── /api/reports/    ← агрегаты через SQL            │
│  └── /api/planning/   ← заказы, платежи, кэшфлоу     │
└───────────────────┬────────────────────────────────────┘
                    │ SQLAlchemy + asyncpg
┌───────────────────▼────────────────────────────────────┐
│  POSTGRESQL  (порт 5432)                               │
│  ├── accounts          (REF_ACCOUNTS)                  │
│  ├── counterparty_categories (REF_CP_CATEGORY)        │
│  ├── overrides         (REF_OVERRIDE)                  │
│  ├── transactions      (MASTER_LOGIC)                  │
│  ├── customs_topup / customs_alloc                     │
│  ├── orders / lead_time / planned_payments             │
│  ├── planned_incomes / opening_balances                │
│  └── import_log / category_change_log                  │
└────────────────────────────────────────────────────────┘
```

---

## Быстрый старт

### 1. Запуск через Docker Compose

```bash
cd dds_app
docker-compose up --build
```

Подождите ~30 секунд, затем откройте:
- **Приложение:** http://localhost:3000
- **API документация:** http://localhost:8000/docs

### 2. Инициализация данных

В боковом меню нажмите **"🔧 Инициализация данных"** — загрузит дефолтные счета и lead times.

### 3. Загрузка данных из Excel

Скопируйте ваши файлы в папку `data/`:
```
data/
  DDS_01_PIPELINE.xlsx
  задолжность.xlsx
```

Затем запустите seed-скрипт:
```bash
docker-compose exec backend python /app/scripts/seed_from_excel.py
```

Или вручную через UI:
1. Перейдите в **⚙️ Справочники** → добавьте счета и категории контрагентов
2. Перейдите в **📥 Импорт выписок** → загрузите файлы выписок
3. Перейдите в **🔴 INBOX** → разнесите неразнесённые операции

---

## ETL Pipeline: Бизнес-логика

### Поддерживаемые форматы выписок

| Источник | Формат |
|----------|--------|
| `VTB_RUB_MAIN` | VTB RUB выписка |
| `VTB_RUB_TRANSIT` | VTB RUB транзитный |
| `VTB_CNY` | VTB CNY выписка |
| `WB_MAIN` | WB основной счёт |
| `WB_PAYOUT` | WB Payout |

### txn_id (стабильный идентификатор)
```
{date}|{account}|{currency}|{cp_account}|{income}|{expense}|{purpose[:80]}
```
- Импорт идемпотентный: повторная загрузка файла не создаёт дублей

### Приоритет event_type2
1. `INTERNAL_TRANSFER` — если счёт получателя = наш счёт
2. `FX_BUY` — если назначение содержит: конверсия, курс, CNY/RUB
3. `CUSTOMS_PAYMENT` — если счёт получателя = таможенный
4. `OPER` — всё остальное

### Приоритет категоризации
1. `overrides` по txn_id (индивидуальное переопределение)
2. `counterparty_categories` по cp_key (ИНН или нормализованное имя)
3. → `UNASSIGNED` (попадает в INBOX)

### SRC_IMP: Теги назначения
| Тег | Regex |
|-----|-------|
| Комиссия | КОМИССИ, COMMISSION, SWIFT, ТАРИФ |
| Логистика | INVOICE, FORWARDING, FREIGHT, TRANSPORT |
| Заказ | ANNEX, APPENDIX, ПРИЛОЖЕН, ACCORDING TO |
| Другое | всё остальное |

---

## Модель данных

### Ключевые таблицы

**transactions** — единая лента операций (MASTER_LOGIC)
- `txn_id` — стабильный уникальный ID
- `event_type2` — INTERNAL_TRANSFER / FX_BUY / CUSTOMS_PAYMENT / OPER
- `is_cashflow2` — 0 для внутренних и FX, 1 для операционных
- `cat_lvl1_2 / cat_lvl2_2` — финальные категории
- `purpose_tag` — Комиссия / Логистика / Заказ / Другое
- `invoice_id / annex_id` — извлечённые из назначения

**customs_topup** — авансы на таможню (из CUSTOMS_PAYMENT)  
**customs_alloc** — распределение авансов по заказам  
**orders** — заказы (отправки)  
**planned_payments** — плановые платежи  
**wb_funnel_daily** — воронка продаж WB (ежедневные данные)  
**wb_api_keys** — зашифрованные API-ключи WB  
**planned_incomes** — плановые поступления WB  

---

## Критерии проверки корректности

1. ✅ После повторного импорта количество транзакций не увеличивается
2. ✅ FX и INTERNAL не попадают в ДДС (is_cashflow2=0), но влияют на баланс
3. ✅ UNASSIGNED показывает только is_cashflow2=1 и пустые категории
4. ✅ CUSTOMS_TOPUP автоматически заполняется из CUSTOMS_PAYMENT транзакций
5. ✅ CashflowDaily учитывает просрочку (платежи с pay_date < today и is_paid=False)
6. ✅ Связка факт↔план по annex_id/invoice_id

---

## Добавление нового банка

1. Создайте парсер в `backend/etl/parsers.py`:
```python
def parse_my_bank(data: bytes, account_no: str) -> pd.DataFrame:
    # ... parse raw Excel → normalized DataFrame
    return df  # с колонками NORM_COLS
```

2. Добавьте в `SOURCE_PARSERS`:
```python
SOURCE_PARSERS["MY_BANK_RUB"] = parse_my_bank
```

3. Добавьте в список `SOURCE_TYPES` во frontend `pages/import_page.py`

---

## API документация

Доступна по адресу: http://localhost:8000/docs (Swagger UI)

Ключевые эндпоинты:
- `POST /api/import/upload` — загрузка выписки
- `POST /api/transactions/search` — поиск операций
- `GET /api/transactions/unassigned` — INBOX
- `POST /api/transactions/assign_category` — разноска
- `GET /api/reports/balance` — остатки
- `GET /api/reports/dds_month` — ДДС за месяц
- `GET /api/planning/cashflow_daily` — прогноз кэшфлоу
