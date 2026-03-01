---
description: Стандартный рабочий процесс при любом изменении кода в проекте DDS
---

# Рабочий процесс DDS

## Обязательные правила

### 1. Документация
- **Перед началом работы** — прочитай `docs/MODULES.md` для ориентации по модулям проекта.
- **После завершения изменений** — обнови `docs/MODULES.md` если были добавлены/изменены модули, роутеры, страницы или API эндпоинты.

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

**Изменения только backend** — hot-reload через volume mount, пересборка НЕ нужна.
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
| Страницы | `frontend-react/src/app/p/[slug]/<module>/page.tsx` |
| Layout + навигация | `frontend-react/src/app/p/[slug]/layout.tsx` |
| Стили | `frontend-react/src/app/globals.css` |
| Backend роутеры | `backend/routers/*.py` |
| Модели БД | `backend/models.py` |
| Схемы Pydantic | `backend/schemas.py` |
| Конфигурация | `backend/config.py` |
