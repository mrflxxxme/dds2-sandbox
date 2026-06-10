---
description: "Откат прод-деплоя DDS2 на предыдущую версию без нового коммита фичи."
---

# /rollback — откат прод-деплоя

Когда последний деплой сломал прод и нет времени на forward fix. НЕ применять, если прод был сломан ДО деплоя (тогда `/hotfix`).

## 1. Что откатывать
```bash
gh run list --workflow=cd-production.yml --limit=3 --json databaseId,headSha,conclusion,createdAt
git log --oneline main -5
```

## 2. Git revert (основной путь)
```bash
git checkout main && git pull origin main
git revert <broken-sha> --no-edit
git push origin main      # cd-production.yml задеплоит автоматически
gh run watch
```
Чище форс-пуша — история сохраняется, CI прогоняется.

## 3. Docker image rollback (если revert недоступен)
На сервере: `docker images | grep dds-backend` → найти предыдущий тег → прописать в `docker-compose.app.yml` → `docker compose up -d backend`.

## 4. Если в деплое была миграция
Откат кода НЕ откатывает схему БД. Миграция обратимая → `ssh dds-app "cd /opt/dds_app && docker compose exec backend alembic downgrade -1"`. Необратимая (`DROP COLUMN` с данными) — схему откатывать нельзя, нужен forward fix.

## 5. Верификация и postmortem
`curl -sf https://app.vyatkin-wb.ru/health`; запись в `memory/project_known_bugs.md`.

## Запрещено
`git push --force` / `git reset --hard` на `main` без согласия пользователя; `alembic downgrade` без проверки обратимости; правки кода по SSH (только через CI/CD).
