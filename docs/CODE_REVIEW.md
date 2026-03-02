# Code Review: DDS Project Audit

> **Дата:** 2026-03-02
> **Ревьюер:** Claude Opus 4.6 (AI-assisted audit)
> **Версия:** dev branch @ da6b8e2

---

## Общая оценка: 7/10

Проект сделан **грамотно для стадии MVP**. Правильный стек, чёткая архитектура, хорошая документация. Основные риски — неполная изоляция проектов, dev-режим в Docker, отсутствие тестов фронтенда.

---

## Сводная таблица

| Модуль | Оценка | Зрелость |
|--------|--------|----------|
| Backend API (FastAPI) | 8/10 | Production-ready с доработками |
| ETL Pipeline | 8/10 | Хорошая база, нужна валидация |
| Models / DB | 7/10 | Дублирование полей, неполная изоляция |
| Auth / Security | 6/10 | Работает, слабая валидация пароля |
| Frontend React | 8/10 | Отличная структура, нет тестов |
| TypeScript типизация | 9/10 | Strict mode, полное покрытие |
| UI/UX дизайн | 8/10 | Единая дизайн-система, glassmorphism |
| Тесты | 4/10 | Только бэкенд, частичное покрытие |
| CI/CD | 4/10 | Только lint + test, нет деплоя |
| Deploy | 2/10 | Ручной, не для продакшена |
| Документация | 8/10 | Выше среднего |

---

## Что сделано правильно

### Архитектура бэкенда (8/10)
- Правильное разделение: `routers/ → services/ → models/`
- Доменная организация моделей (auth, transactions, cost, planning, customs, integrations)
- `SoftDeleteMixin` и `TimestampMixin` — переиспользуемые миксины
- Два варианта project dependency: `get_project_id()` и `get_current_project()`

### Стек технологий (9/10)
- FastAPI + async SQLAlchemy 2.0 + PostgreSQL — отличный выбор для финансовой системы
- Redis для кэширования отчётов с decorator pattern `@cached`
- MinIO (S3-совместимый) для файлового хранилища
- Numeric(18,2) для финансовых полей — правильный тип

### ETL Pipeline (8/10)
- Модульные парсеры для 5+ форматов банковских выписок (VTB, WB, OZON)
- Идемпотентный импорт через детерминированный `txn_id`
- Regex-паттерны для категоризации (русский + английский)
- `make_cp_key()` для нормализации контрагентов (по ИНН или имени)

### Frontend React (8/10)
- **3 runtime-зависимости** — минимализм (Next.js 15, React 19, Recharts, xlsx)
- TypeScript strict mode, 326 строк типов API
- Переиспользуемые компоненты: DataTable, FormModal, TabLayout, PageHeader, Toast
- Tailwind CSS 4 + glassmorphism тёмная тема
- App Router с `[slug]` для мультипроектности

### Docker Compose (7/10)
- Internal network — DB/Redis/MinIO недоступны извне
- Healthchecks для всех сервисов
- Volume mapping для persistent data

### Документация (8/10)
- ARCHITECTURE.md — слои, модули, таблицы
- CONVENTIONS.md — правила разработки
- BUSINESS_RULES.md — бизнес-логика
- README.md — быстрый старт, ETL описание, модель данных

---

## Критичные проблемы (P0)

### 1. Dockerfiles в dev-режиме

**Проблема:** Оба Dockerfile запускают dev-серверы.

`Dockerfile.backend` строка 22:
```
CMD ["uvicorn", "...", "--reload"]
```
`--reload` следит за изменениями файлов — overhead в production.

`Dockerfile.react` строка 17:
```
CMD ["npm", "run", "dev"]
```
Dev-сервер Next.js не оптимизирован, нет SSG/ISR, медленный.

**Решение:**
- Backend: убрать `--reload`, добавить `--workers 2`
- Frontend: multi-stage build с `npm run build` + `npm start`
- Запускать от non-root пользователя

---

### 2. Неполная изоляция проектов

**Проблема:** 7 моделей не имеют `project_id` — данные видны всем проектам.

| Модель | Файл | Риск |
|--------|------|------|
| `CategoryRef` | `backend/models/refs.py:71` | Категории ДДС общие для всех |
| `LeadTime` | `backend/models/planning.py:35` | Lead times общие |
| `Nomenclature` | `backend/models/cost.py:17` | Номенклатура WB общая |
| `DutyRule` | `backend/models/cost.py:29` | Ставки пошлин общие |
| `CustomsTopup` | `backend/models/customs.py:15` | Таможенные авансы общие |
| `CustomsAlloc` | `backend/models/customs.py:29` | Распределение общее |
| `CustomsDT` | `backend/models/customs.py:43` | ДТ декларации общие |

**Пример уязвимости** (`routers/refs.py:153-158`):
```python
@router.get("/categories")
async def get_categories(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(CategoryRef).order_by(...))
    # Нет фильтра по project_id — возвращает ВСЕ категории
```

**Решение:**
- Добавить `project_id` + FK к каждой модели
- Обновить unique constraints (включить project_id)
- Обновить роутеры: добавить `Depends(get_current_project)` + `.where(Model.project_id == project.id)`
- Alembic миграция

---

### 3. Хардкод MinIO credentials

**Проблема** (`backend/config.py:25-26`):
```python
MINIO_ACCESS_KEY: str = "minioadmin"
MINIO_SECRET_KEY: str = "minioadmin"
```
Дефолтные admin-пароли. Если `.env` не настроен — MinIO открыт.

**Решение:**
- Пустые дефолты + field_validator с предупреждением
- Документация в `.env.example`

---

### 4. Файловые аплоады — только проверка расширения

**Проблема** (`routers/import_txn.py:47-48`):
```python
file_ext = os.path.splitext(file.filename or "")[1].lower()
if file_ext not in allowed_exts:
```
Проверяется только расширение файла, не содержимое. Можно загрузить исполняемый файл с расширением `.xlsx`.

5 эндпоинтов с аплоадами:
- `POST /import/upload`
- `POST /cost/nomenclature/upload`
- `POST /cost/orders/{order_no}/upload`
- `POST /planning/customs_dt/upload_fts`
- `POST /planning/wb_payouts/upload`

**Решение:**
- Валидация magic bytes (file signatures): `.xlsx` = `PK\x03\x04`, `.xls` = `\xD0\xCF\x11\xE0`, `.pdf` = `%PDF`
- Утилита `validate_file_content()` — zero-dependency подход

---

## Важные проблемы (P1)

### 5. Нет тестов фронтенда
- 0 тестов React/TypeScript
- Бэкенд покрыт частично: 784 строки тестов (test_master_logic, test_parsers, test_api_auth, test_api_isolation)
- Нет jest/vitest конфигурации

### 6. Нет валидации форм на клиенте
- FormModal принимает пустые/невалидные значения
- Нет библиотеки валидации (Zod, react-hook-form)
- Ручная конвертация типов: `parseFloat(form.value) || 0`

### 7. Service layer не завершён
- Reports и Refs содержат бизнес-логику в роутерах
- Нет `services/reports_service.py`, `services/refs_service.py`
- Cost и Planning — services уже выделены (хороший паттерн)

### 8. Дублирование полей в Transaction
```
cat_lvl1 / cat_lvl1_2    — два набора категорий
is_cashflow / is_cashflow2 — два флага кэшфлоу
event_type / event_type2   — два типа событий
```
Неясно какие поля актуальны. Код использует `_2` версии, старые не удалены.

### 9. Нет пагинации на LIST-эндпоинтах
- Некоторые GET-эндпоинты возвращают все записи без limit/offset
- Риск OOM при больших объёмах данных

### 10. Deploy не для production
- `deploy.sh` — ручное копирование файлов (25+ хардкод-маппингов)
- Нет CI/CD push, нет rollback, нет version tagging
- GitHub Actions: только lint + test, нет деплоя

---

## Желательные улучшения (P2)

| # | Проблема | Решение |
|---|----------|---------|
| 11 | Нет refresh token (JWT 8h, потом перелогин) | Implement refresh token flow |
| 12 | Нет React Query/SWR (refetch при каждом переходе) | Добавить SWR или TanStack Query |
| 13 | Нет pre-commit hooks (Ruff только в CI) | pre-commit config (ruff, black, mypy) |
| 14 | 3 файла requirements (дублирование) | Один requirements-base + requirements-dev |
| 15 | Нет error boundaries в React | React Error Boundary для graceful fallback |
| 16 | Слабая валидация пароля (6 символов) | 12+ символов, uppercase, digit |
| 17 | Нет token revocation (JWT blacklist) | Redis-based blacklist |
| 18 | Silent catch в React (пустые catch блоки) | Логирование ошибок (Sentry) |
| 19 | Нет ARIA-атрибутов | Accessibility improvements |
| 20 | `order_no` как FK в CostOrderItem (natural key) | Рефактор: использовать surrogate key (id) |

---

## Рекомендации по порядку исправления

### P0 — До деплоя в production (~3-4 дня)
1. Dockerfiles → production mode
2. Project isolation (7 моделей + миграция + роутеры)
3. MinIO credentials — убрать хардкод
4. File upload validation — magic bytes

### P1 — В следующем спринте (~5-7 дней)
5. Тесты фронтенда (jest + testing-library)
6. Валидация форм (Zod + react-hook-form)
7. Извлечение services из routers
8. Очистка дублей в Transaction модели
9. Пагинация на LIST-эндпоинтах
10. CI/CD деплой pipeline

### P2 — Итеративно
11-20. По приоритету бизнес-задач

---

## Архитектурная диаграмма (текущее состояние)

```
Frontend (Next.js 15 + React 19)
├── App Router /p/[slug]/ — мультипроект
├── Components: DataTable, FormModal, TabLayout
├── API Client: singleton, JWT auth, X-Project-Id
└── Tailwind CSS 4 + glassmorphism

Backend (FastAPI + async)
├── Routers (7): auth, import, txn, cost, planning, reports, funnel
├── Services (3): cost_service, funnel_service, planning_service
├── ETL: parsers (VTB/WB/OZON) → master_logic → service
├── Models (30+): organized by domain
├── Schemas: Pydantic v2 per domain
├── Cache: Redis @cached decorator
├── Storage: MinIO S3-compatible
└── Auth: JWT + bcrypt

Infrastructure
├── PostgreSQL 15 (async + sync engines)
├── Redis 7 (cache, rate limiting)
├── MinIO (file storage)
├── Docker Compose (internal network)
└── GitHub Actions (lint + test)
```
