---
description: Деплой на сервер — через CI/CD или make команды
---

# Deploy Workflow

## ⛔ НИКОГДА не деплоить через SSH напрямую

Деплой ТОЛЬКО через CI/CD pipeline или `make` команды.

## Стандартный деплой (staging)

// turbo-all

### 1. Проверить тесты
```bash
cd /Users/a1/Desktop/dds_app && docker compose exec backend pytest tests/ -x --tb=short
```

### 2. Commit и push в dev
```bash
cd /Users/a1/Desktop/dds_app && git add -A
```

```bash
cd /Users/a1/Desktop/dds_app && git commit -m "<описание изменений>"
```

```bash
cd /Users/a1/Desktop/dds_app && git push origin dev
```

> ✅ CI/CD автоматически задеплоит на staging

### 3. Проверить staging

Открыть staging и проверить:
- Health check работает
- Логин работает
- Затронутые страницы работают корректно

### 4. Деплой в production (после проверки staging)
```bash
cd /Users/a1/Desktop/dds_app && git checkout main && git merge dev && git push origin main && git checkout dev
```

> ✅ CI/CD автоматически задеплоит на production

### 5. Проверить production

Открыть production и убедиться что всё работает.

---

## Откат при проблемах

```bash
# На сервере (только в экстренных случаях!):
ssh root@130.49.150.69 "cd /opt/dds_app && git log --oneline -5"
ssh root@130.49.150.69 "cd /opt/dds_app && git checkout <commit-hash> . && docker compose up -d --build backend"
```

## Быстрые Makefile команды

| Команда | Что делает |
|---------|-----------|
| `make test` | Запустить тесты |
| `make deploy MSG="feat: ..."` | Commit + push в dev |
| `make deploy-prod` | Merge dev → main → production |
| `make logs` | Логи backend |
| `make status` | Статус контейнеров |

## Сервер
- IP: 130.49.150.69
- Путь: /opt/dds_app
- Ветка prod: main
- Ветка staging: dev
