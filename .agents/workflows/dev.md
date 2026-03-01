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
cd /Users/a1/Desktop/dds_app && git push origin main
```

### 3. Сборка и проверка
// turbo
```bash
cd /Users/a1/Desktop/dds_app && docker compose up -d --build frontend-react
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
