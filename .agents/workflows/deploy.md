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
git push origin dev
```

### 2. Pull на сервере и пересборка
// turbo
```bash
ssh root@130.49.150.69 "cd /opt/dds_app && git pull origin dev"
```

```bash
ssh root@130.49.150.69 "cd /opt/dds_app && docker compose up -d --build backend"
```

Если нужно пересобрать и frontend:
```bash
ssh root@130.49.150.69 "cd /opt/dds_app && docker compose up -d --build backend frontend-react"
```

### 3. Проверить что всё поднялось
// turbo
```bash
ssh root@130.49.150.69 "cd /opt/dds_app && docker compose ps --format 'table {{.Name}}\t{{.Status}}'"
```

// turbo
```bash
ssh root@130.49.150.69 "cd /opt/dds_app && docker compose logs backend --tail=10 --no-log-prefix 2>&1 | grep -v health"
```

### 4. Проверить в браузере
Открыть http://130.49.150.69 и убедиться что всё работает.

---

## Откат при проблемах
```bash
ssh root@130.49.150.69 "cd /opt/dds_app && git log --oneline -5"
ssh root@130.49.150.69 "cd /opt/dds_app && git checkout <commit-hash> . && docker compose up -d --build backend"
```

## Сервер
- IP: 130.49.150.69
- Путь к проекту: /opt/dds_app
- Ветка: dev
- SSH: root (ключ настроен ~/.ssh/id_ed25519)
