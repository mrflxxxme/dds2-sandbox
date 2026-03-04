---
description: Деплой на сервер — коммит, пуш, pull на сервере, пересборка
---

# Deploy to Server (130.49.150.69)

Workflow: локальная разработка → тест → деплой на сервер.

## Предусловия
- Локально всё работает (`docker compose ps` — все healthy)
- Изменения протестированы на `http://localhost:3000`

## Шаги

### 1. Коммит и пуш в GitHub
// turbo
```bash
cd /Users/a1/Desktop/dds_app
git add -A
git status
```

```bash
git commit -m "<описание изменений>"
```

// turbo
```bash
git push origin main
```

### 2. SSH на сервер и pull
```bash
ssh root@130.49.150.69
```
> На сервере:
```bash
cd /root/dds_app    # или где расположен проект
git pull origin main
```

### 3. Пересобрать и перезапустить контейнеры на сервере
> На сервере:
```bash
docker compose up -d --build backend frontend-react
```

### 4. Проверить что всё поднялось
> На сервере:
```bash
docker compose ps
docker compose logs backend --tail=20 --no-log-prefix | grep -v health
curl -s http://localhost:8000/health
```

### 5. Проверить в браузере
Открыть http://130.49.150.69/p/default/funnel и убедиться что данные появились.

---

## Откат при проблемах
> На сервере:
```bash
git log --oneline -5           # найти предыдущий коммит
git checkout <commit-hash> .   # вернуть файлы
docker compose up -d --build backend frontend-react
```
