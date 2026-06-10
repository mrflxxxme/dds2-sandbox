---
description: "Экстренный фикс прода DDS2: диагностика инцидента → минимальный фикс → деплой через CI."
---

# /hotfix — экстренный фикс прода

Только для прод-инцидентов (прод лёг, 5xx, потеря данных, безопасность, деньги). Обычные баги — чини в `dev`.

## 1. Диагностика
```bash
ssh dds-app "cd /opt/dds_app && docker compose logs backend --tail=200 | grep -E 'ERROR|CRITICAL|Traceback'"
curl -sf https://app.vyatkin-wb.ru/health
gh run list --workflow=cd-production.yml --limit=3
git log --oneline -10 main
```

## 2. Решение
**A. Rollback** — если прод сломал последний деплой → `/rollback`.
**B. Forward fix** — если баг давний или был деплой с миграцией:
1. `git checkout -b hotfix/<issue>` от `main` (не от `dev`).
2. Минимальный diff — только строки, вызывающие ошибку.
3. Тест, воспроизводящий баг (если успеваешь).
4. `git commit -m "fix(prod): ..."` → push → PR `hotfix → main` (минуя `dev`), label `hotfix`.
5. После green CI → squash merge → `cd-production.yml` деплоит автоматически.

## 3. Верификация
```bash
gh run watch
curl -sf https://app.vyatkin-wb.ru/health
ssh dds-app "cd /opt/dds_app && docker compose logs backend --tail=100 | grep -c ERROR"
```

## 4. Postmortem
Запись в `memory/project_known_bugs.md`: что сломалось, root cause, коммит фикса, как не повторить. После merge в `main` — cherry-pick в `dev`.

## Запрещено в hotfix
Рефакторинг, новые фичи, переименования, обновление зависимостей, изменение миграций (миграция — это полноценный релиз, не hotfix).
