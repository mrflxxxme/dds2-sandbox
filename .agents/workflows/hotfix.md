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
- Найди и исправь баг
- Backend: hot-reload автоматический (uvicorn --reload)
- Frontend: hot-reload автоматический (Next.js)
- Пересборка нужна ТОЛЬКО при изменении package.json / requirements.txt / Dockerfile

## 3. Проверка после фикса
```bash
cd /Users/a1/Desktop/dds_app && docker compose logs backend --tail=10
```

```bash
cd /Users/a1/Desktop/dds_app && docker compose logs frontend-react --tail=10
```

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
