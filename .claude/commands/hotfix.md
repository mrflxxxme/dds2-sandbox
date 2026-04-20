---
description: "Emergency hotfix DDS2: диагностика прод-инцидента → минимальный фикс → быстрый деплой через CI."
---

# /hotfix — Экстренный фикс продакшна

## Когда использовать
- Прод лежит / ошибка у пользователей
- Критичный баг (потеря данных, безопасность, деньги)
- 500 errors в мониторинге
- Sentry/Grafana алерт

**НЕ использовать** для обычных багов — для них `/plan` или просто исправить в `dev`.

## Процесс (строго по шагам)

### Шаг 1: Диагностика (5 мин)
Параллельно запустить:
```bash
# Логи бэкенда на проде
ssh dds-app "cd /opt/dds && docker compose logs backend --tail=200 | grep -E 'ERROR|CRITICAL|Traceback'"

# Health-check
curl -sf https://app.vyatkin-wb.ru/api/health

# Последние деплои
gh run list --workflow=cd-production.yml --limit=3

# DB коннект (на data-сервере)
ssh dds-data "docker exec dds-postgres pg_isready"

# Недавние коммиты
git log --oneline -10 main
```

### Шаг 2: Решение (выбрать одно)

**A. Rollback** — если последний деплой сломал прод:
```bash
# Откатить на предыдущий образ
ssh dds-app "cd /opt/dds && docker compose pull backend:previous && docker compose up -d backend"
# Или через git revert
git checkout main && git revert <hash> --no-edit && git push origin main
```

**B. Forward fix** — если баг давний или rollback невозможен (миграция):
1. Ветка: `git checkout -b hotfix/<issue>` от `main`
2. Минимальный diff: только строки вызывающие ошибку
3. Тест который воспроизводит (если успеваешь) — `tests/test_hotfix_<issue>.py`
4. `git commit -m "fix(prod): краткое описание"`
5. `git push origin hotfix/<issue>` → PR `hotfix → main` (НЕ в dev)
6. Label `hotfix` → в `claude-review.yml` триггерит opus-4-7 review
7. После green CI → squash merge → `cd-production.yml` автоматически

### Шаг 3: Верификация (5 мин)
```bash
# Дождаться деплоя
gh run watch

# Проверить health
curl -sf https://app.vyatkin-wb.ru/api/health

# Проверить что ошибка пропала
ssh dds-app "cd /opt/dds && docker compose logs backend --tail=100 | grep -c ERROR"
```

### Шаг 4: Postmortem (сразу, пока свежее)
Запиши в `memory/project_known_bugs.md` секция «Исправленные»:
- Что сломалось
- Когда обнаружили (timestamp)
- Коммит фикса
- Root cause
- Что сделать чтобы не повторилось (новый check в `scripts/check_conventions.sh`?)

Если инцидент крупный — создай `docs/incidents/YYYY-MM-DD-<name>.md`.

## Что запрещено в hotfix
- Рефакторинг
- Новые фичи
- Переименования
- Обновление зависимостей
- Изменения миграций (если нужна миграция — это НЕ hotfix, это полноценный release)

## Merge правила
- Ветка: `hotfix/<slug>` от `main` (НЕ от `dev`)
- PR в `main` напрямую (минуя `dev`)
- После merge в main → cherry-pick в `dev`:
  ```bash
  git checkout dev && git cherry-pick <hotfix-hash> && git push origin dev
  ```

## Связанные
- `/rollback` — откатить последний деплой без нового коммита
- `/status` — быстрая диагностика состояния прода
