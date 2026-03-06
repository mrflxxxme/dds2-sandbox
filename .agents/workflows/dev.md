---
description: Стандартный рабочий процесс при любом изменении кода в проекте DDS
---

// turbo-all

# Рабочий процесс DDS

## ⛔ Архитектурные правила (ОБЯЗАТЕЛЬНЫЕ)

> **Проверяй ЭТИ правила при КАЖДОМ изменении кода. Нарушил — исправь ДО коммита.**

### Backend

| Правило | Что делать |
|---------|-----------|
| **Бизнес-логика в services/** | Роутер тонкий — вызывает service-функцию, НЕ содержит логику |
| **`datetime.now(timezone.utc)`** | Не `datetime.utcnow` — deprecated с Python 3.12 |
| **`Mapped[]` + `mapped_column()`** | Не `Column()` — новый SQLAlchemy стиль |
| **`Numeric(18,2)` для денег** | Не `Float` — точные вычисления |
| **SQL: `:param` binding** | Не f-string — безопасность |
| **`project_id`** | Каждый запрос фильтрует по project_id |
| **`is_deleted == False`** | Фильтр для SoftDeleteMixin моделей |
| **Пагинация (`limit/offset`)** | Для всех list-эндпоинтов |
| **Модели в `models/domain.py`** | Не в монолитном `models.py` |
| **Схемы в `schemas/domain.py`** | Не в монолитном `schemas.py` |
| **Logging** | `logger = logging.getLogger("dds.module")` |
| **Кэш инвалидация** | `invalidate_cache()` после мутации |

### Frontend

| Правило | Что делать |
|---------|-----------|
| **Loading / Error / Empty states** | Обязательны в каждом компоненте |
| **`formatNumber()` / `formatDate()`** | Для всех чисел и дат |
| **«📥 Excel»** | Кнопка для каждой таблицы |
| **`useCallback`** | Для функций загрузки данных |
| **Типы в `types/api.ts`** | Не inline |
| **CSS классы из `globals.css`** | Не inline стили |

---

## Обязательные правила процесса

### 1. Документация
- **Перед началом работы** — прочитай `AGENTS.md` (особенно секции ЗАПРЕЩЕНО и ОБЯЗАТЕЛЬНО) и `docs/MODULES.md`.
- **После завершения изменений** — обнови `AGENTS.md` и `docs/MODULES.md` если были добавлены/изменены модули, роутеры, страницы или API эндпоинты.

### 2. Git — коммит и пуш
- **После каждого завершённого блока работы** — сделай коммит и пуш на GitHub.
- Используй понятные коммит-сообщения на русском или английском.

**Всегда работаем в ветке `dev`!** Ветка `main` — стабильная продакшн версия.

```bash
# turbo
cd /Users/a1/Desktop/dds_app && git checkout dev
```

```bash
# turbo
cd /Users/a1/Desktop/dds_app && git add -A
```

```bash
# turbo
cd /Users/a1/Desktop/dds_app && git commit -m "описание изменений"
```

```bash
# turbo
cd /Users/a1/Desktop/dds_app && git push origin dev
```

#### Мердж в main (только после тестирования!)
```bash
cd /Users/a1/Desktop/dds_app && git checkout main && git merge dev && git push origin main && git checkout dev
```

### 3. Сборка и проверка

**Изменения кода** (src/, public/) — hot-reload автоматический, пересборка НЕ нужна.
Контейнеры используют volume mount, Next.js подхватывает изменения мгновенно.

**Изменения зависимостей** (package.json, Dockerfile, next.config) — нужна пересборка:
// turbo
```bash
cd /Users/a1/Desktop/dds_app && docker compose up -d --build frontend-react
```

**Изменения только backend** — hot-reload через volume mount + uvicorn `--reload`, пересборка НЕ нужна.
При изменении requirements-backend.txt:
// turbo
```bash
cd /Users/a1/Desktop/dds_app && docker compose up -d --build backend
```

- После сборки проверь что страница загружается корректно.

### 4. Структура проекта
- **Frontend**: `frontend-react/` (Next.js + TypeScript)
- **Backend**: `backend/` (FastAPI + Python)
- **БД**: PostgreSQL (в Docker)
- **Кэш**: Redis (в Docker)
- **Docker**: `docker-compose.yml`

### 5. Ключевые файлы
| Область | Путь |
|---------|------|
| API клиент | `frontend-react/src/lib/api.ts` |
| TypeScript типы | `frontend-react/src/types/api.ts` |
| Страницы | `frontend-react/src/app/p/[slug]/<module>/page.tsx` |
| Layout + навигация | `frontend-react/src/app/p/[slug]/layout.tsx` |
| Стили | `frontend-react/src/app/globals.css` |
| Backend роутеры | `backend/routers/*.py` |
| Backend сервисы | `backend/services/*.py` |
| Модели БД | `backend/models/*.py` |
| Схемы Pydantic | `backend/schemas/*.py` |
| Конфигурация | `backend/config.py` |
