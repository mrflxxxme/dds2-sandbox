---
description: Стандартный рабочий процесс при любом изменении кода в проекте DDS
---

# Workflow: Работа с кодом DDS

Этот workflow ОБЯЗАТЕЛЕН при любом изменении кода в проекте.

## Перед началом работы

1. Прочитай `AGENTS.md` в корне проекта — полная документация по структуре, conventions, моделям.
// turbo
2. Прочитай `ARCHITECTURE.md` — высокоуровневая архитектура и слои.
// turbo
3. Определи, какие модули затрагивает задача (backend/frontend/infra).
4. Проверь секцию "Известные проблемы (TODO)" в `AGENTS.md` — убедись что не дублируешь работу.

## Во время работы

5. Следуй Conventions из `AGENTS.md`:
   - Backend: async SQLAlchemy, parametrized SQL, HTTPException errors
   - Frontend: `'use client'`, API через `api.ts`, `exportToExcel()` для таблиц
   - Стили: CSS variables, glass-card, data-table, btn-*, badge-*

6. Для каждой новой страницы/роутера:
   - Backend: router → main.py registration → schema → model (если нужна)
   - Frontend: page.tsx → api.ts methods → layout.tsx sidebar

## После завершения работы

// turbo
7. Обнови `AGENTS.md`:
   - Структуру файлов (если добавил/удалил файлы)
   - Таблицу моделей (если добавил/изменил таблицы)
   - Секцию "Как добавить" (если изменились conventions)
   - Секцию "Известные проблемы" (если решил или нашёл новые)
   - Дату "Последнее обновление"

// turbo
8. Обнови `ARCHITECTURE.md` если изменилась архитектура (новые модули, таблицы, интеграции).

9. Запусти `docker compose build --no-cache` для затронутых сервисов и проверь работоспособность.
