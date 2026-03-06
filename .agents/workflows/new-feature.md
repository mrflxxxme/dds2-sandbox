---
description: Полный цикл создания новой фичи (backend + frontend + тест + деплой)
---

# Workflow: Новая фича

// turbo-all

## 1. Планирование
- Определи что нужно: API эндпоинты, таблицы БД, страницы UI
- Прочитай `AGENTS.md` (секции ⛔ ЗАПРЕЩЕНО и ✅ ОБЯЗАТЕЛЬНО) и `docs/MODULES.md` для контекста

## 2. Schema-First
**ДО написания кода** определи Pydantic request/response модели:
- Schema в `schemas/feature.py` + re-export в `schemas/__init__.py`
- TypeScript типы в `types/api.ts`

## 3. Backend (если нужен новый API)
- Используй skill `new-api-endpoint` — Router → Service → Model
- **Вся бизнес-логика в `services/`**, НЕ в роутере
- Если нужна новая таблица — используй skill `db-migration`

## 4. Frontend (если нужна новая страница)
- Используй skill `new-page`
- Обязательно: loading/error/empty states, `formatNumber`, `formatDate`, Excel export

## 5. Тесты
- Создай `tests/test_api_feature.py` для нового модуля
- Минимум: test_create, test_list, test_delete

## 6. Проверка

⛔ **Проверь архитектурные правила из `/dev` workflow ПЕРЕД коммитом!**

Проверь что Docker контейнеры работают:
```bash
cd /Users/a1/Desktop/dds_app && docker compose ps
```

Проверь логи backend на ошибки:
```bash
cd /Users/a1/Desktop/dds_app && docker compose logs backend --tail=30
```

Проверь логи frontend на ошибки:
```bash
cd /Users/a1/Desktop/dds_app && docker compose logs frontend-react --tail=30
```

## 7. Документация
- Обнови `AGENTS.md` — структура, таблицы моделей
- Обнови `docs/MODULES.md` — новые модули/эндпоинты

## 8. Коммит
Используй workflow `/dev` для коммита и пуша.
