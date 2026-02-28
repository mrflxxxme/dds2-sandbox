# DDS Conventions — Соглашения по коду

---

## Именование

| Что | Стиль | Пример |
|-----|-------|--------|
| Python файлы | snake_case | `import_txn.py` |
| Python функции | snake_case | `get_balance()` |
| Python классы | PascalCase | `PlannedPayment` |
| TypeScript файлы | kebab-case | `use-balance.ts` |
| TypeScript компоненты | PascalCase | `BalanceCard.tsx` |
| API endpoints | kebab-case | `/api/reports/balance-daily` |
| DB таблицы | snake_case | `planned_payments` |
| DB колонки | snake_case | `cat_lvl1_2` |
| Env переменные | UPPER_SNAKE | `DATABASE_URL` |

---

## Git Commits

Формат: `тип: описание на русском или английском`

| Тип | Когда |
|-----|-------|
| `feat:` | Новая функциональность |
| `fix:` | Исправление бага |
| `infra:` | Инфраструктура (Docker, CI, конфиг) |
| `refactor:` | Рефакторинг без изменения поведения |
| `test:` | Добавление/изменение тестов |
| `docs:` | Документация |

Примеры:
```
feat: WB API интеграция — загрузка выплат
fix: парсер VTB CNY не находит колонку дебет
infra: добавлен Redis для кэширования отчётов
```

---

## API Design

### Response format (success)
```json
{"data": [...], "meta": {"total": 100}}
```
или простой массив/объект для простых эндпоинтов.

### Response format (error)
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Поле amount обязательно",
    "details": [{"field": "amount", "issue": "required"}]
  }
}
```

### Pagination
```
GET /api/transactions/search?limit=50&offset=0
```

### Filtering
```
GET /api/transactions/search?currency=RUB&status=OK&date_from=2025-01-01
```

---

## Модели (SQLAlchemy)

- Каждая модель наследует `Base` из `database.py`
- PK всегда `id: Mapped[int]` autoincrement
- Даты: `DateTime` для timestamp, `Date` для дат без времени
- Деньги: `Numeric(18, 2)` — никогда не Float
- Строки: всегда указываем `String(N)` с длиной
- FK: `ForeignKey("table.column")` с явным указанием
- Индексы: через `__table_args__`

---

## Schemas (Pydantic)

- Request-модель: `*Request` или `*Create` / `*Update`
- Response-модель: `*Response` или `*Schema`
- Все поля с типами, Optional где нужно
- `model_config = ConfigDict(from_attributes=True)` для ORM

---

## Файловая структура нового модуля

```
backend/
  models.py          ← добавить модель
  schemas.py         ← добавить request/response
  routers/
    my_module.py     ← роутер
  services/          ← если сложная логика
    my_module.py
  integrations/      ← если внешний API
    my_api.py
tests/
  test_my_module.py  ← тесты
```

---

## Безопасность

- API-ключи внешних сервисов (WB) — шифровать перед сохранением (Fernet)
- JWT secret — через `.env`, никогда не в коде
- SQL — только через ORM или `text()` с параметрами, НЕ f-strings
- CORS — только разрешённые origins
