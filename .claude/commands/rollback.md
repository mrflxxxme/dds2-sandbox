---
description: "Rollback последнего деплоя DDS2 на предыдущую версию (без нового коммита)."
---

# /rollback — Откат прод-деплоя

## Когда использовать
- Последний деплой сломал прод (5xx, зависания, регрессии)
- Не успеваешь писать forward fix
- Нужно выиграть время на диагностику

**НЕ использовать**:
- Если прод уже был сломан ДО деплоя (нужен `/hotfix`)
- Если новый деплой содержал Alembic миграцию (миграции не откатываются тривиально — см. Шаг 3b)

## Процесс

### Шаг 1: Определить что откатывать
```bash
# Последние 3 деплоя
gh run list --workflow=cd-production.yml --limit=3 --json databaseId,headSha,conclusion,createdAt

# Последние коммиты в main
git log --oneline main -5
```

Нужен SHA предыдущего успешного деплоя.

### Шаг 2: Git revert (безопасный путь)
```bash
# Откатить коммит на main (создаёт новый revert-коммит)
git checkout main
git pull origin main
git revert <broken-sha> --no-edit
git push origin main

# cd-production.yml задеплоит автоматически
gh run watch
```

Это чище чем форс-пуш — история сохраняется, CI прогонится, деплой автоматический.

### Шаг 3a: Docker image rollback (если git revert недоступен)
Если на сервере сохранён предыдущий образ:
```bash
ssh dds-app
cd /opt/dds
docker images | grep dds-backend   # найти previous tag
docker compose stop backend
# Отредактировать docker-compose.app.yml:
#   image: ghcr.io/.../dds-backend:<PREVIOUS_SHA>
docker compose up -d backend
docker compose logs backend --tail=50
```

### Шаг 3b: С миграцией (осторожно!)
Если сломанный деплой содержал Alembic миграцию — rollback кода **не откатывает** схему БД. Варианты:
1. Код старый + схема новая → возможны ошибки типа «column not found»
2. Сделать `alembic downgrade -1` на проде:
   ```bash
   ssh dds-app "cd /opt/dds && docker compose exec backend alembic downgrade -1"
   ```
   ⚠️ ТОЛЬКО если миграция обратимая (не `DROP COLUMN` с данными)

### Шаг 4: Верификация
```bash
curl -sf https://app.vyatkin-wb.ru/api/health
ssh dds-app "cd /opt/dds && docker compose logs backend --tail=100 | grep -c ERROR"
```

### Шаг 5: Postmortem
После стабилизации прода:
1. Создай issue: `gh issue create --title "Rollback YYYY-MM-DD: <summary>" --label incident`
2. Запиши в `memory/project_known_bugs.md`: что сломалось, как откатили, кто виноват (bug / race / bad test)
3. Реши: forward fix через `/hotfix` или доработка в `dev` → обычный релиз

## Запрещено
- `git push --force` на main без согласия пользователя
- `git reset --hard` на main
- `alembic downgrade` без проверки что миграция обратимая
- SSH-правки кода на сервере (только через CI/CD — см. memory/feedback_workflow)
