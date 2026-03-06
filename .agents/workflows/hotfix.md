---
description: Быстрый фикс бага — диагностика, исправление, проверка, коммит
---

# Workflow: Hotfix

// turbo-all

## 1. Диагностика
Проверь логи backend:
```bash
cd /Users/a1/Desktop/dds_app && docker compose logs backend --tail=50
```

Проверь логи frontend:
```bash
cd /Users/a1/Desktop/dds_app && docker compose logs frontend-react --tail=50
```

Проверь состояние контейнеров:
```bash
cd /Users/a1/Desktop/dds_app && docker compose ps
```

## 2. Исправление

⛔ **При фиксе — НЕ вноси новые нарушения архитектуры:**
- Бизнес-логика → в `services/`, НЕ в роутер
- `datetime.now(timezone.utc)`, НЕ `datetime.utcnow`
- `Mapped[]` + `mapped_column()`, НЕ `Column()`
- `Numeric(18,2)` для денег, НЕ `Float`
- SQL: `:param` binding, НЕ f-string
- `project_id` фильтрация в каждом запросе

- Backend: hot-reload автоматический (uvicorn --reload)
- Frontend: hot-reload автоматический (Next.js)
- Пересборка нужна ТОЛЬКО при изменении package.json / requirements.txt / Dockerfile

## 3. Проверка после фикса

Запусти тесты:
```bash
cd /Users/a1/Desktop/dds_app && docker compose exec backend pytest tests/ -x --tb=short
```

> ⛔ **Если тесты упали — исправь ДО коммита!**

Проверь логи:
```bash
cd /Users/a1/Desktop/dds_app && docker compose logs backend --tail=10
```

```bash
cd /Users/a1/Desktop/dds_app && docker compose logs frontend-react --tail=10
```

> Если фикс затрагивает данные, проверь: нужна ли `invalidate_cache()` для связанных кэшированных ключей?

## 4. Коммит
```bash
cd /Users/a1/Desktop/dds_app && git add -A
```

```bash
cd /Users/a1/Desktop/dds_app && git commit -m "fix: описание бага"
```

```bash
cd /Users/a1/Desktop/dds_app && git push origin dev
```
