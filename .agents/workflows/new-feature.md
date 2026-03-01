---
description: Полный цикл создания новой фичи (backend + frontend + тест + деплой)
---

# Workflow: Новая фича

// turbo-all

## 1. Планирование
- Определи что нужно: API эндпоинты, таблицы БД, страницы UI
- Прочитай `AGENTS.md` и `docs/MODULES.md` для контекста

## 2. Backend (если нужен новый API)
- Используй skill `new-api-endpoint`
- Если нужна новая таблица — используй skill `db-migration`

## 3. Frontend (если нужна новая страница)
- Используй skill `new-page`

## 4. Проверка

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

## 5. Документация
- Обнови `AGENTS.md` — структура, таблицы моделей
- Обнови `docs/MODULES.md` — новые модули/эндпоинты

## 6. Коммит
Используй workflow `/dev` для коммита и пуша.
