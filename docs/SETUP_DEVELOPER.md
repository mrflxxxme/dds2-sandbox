# DDS — онбординг разработчика (работа через Claude Code)

> **Как пользоваться:** открой этот проект в **Claude Code** и напиши:
> «Прочитай `docs/SETUP_DEVELOPER.md` и работай по нему. Сначала подними окружение и налей прод-снимок».
> Дальше веди разработку обычными словами — Claude пишет код по правилам проекта.

Это полный онбординг для **разработчика** (не дизайнера). Ты работаешь как полноценный член команды:
и фронт, и бэкенд, прямой push в `dev`, деплой через CI. Дизайнерский бриф — `docs/SETUP_DESIGNER.md`.

---

## 0. Что тебе выдал владелец (проверь до старта)

Без этих четырёх доступов дальше не пройти — если чего-то нет, запроси у владельца проекта:

1. **Коллаборатор в GitHub** на `github.com/vladbydaev37/dds2` (write-доступ, приглашение на почту).
2. **SSH-ключ к прод-data-серверу** (`dds-data`, `194.67.100.57`) — нужен для `make sync-prod`.
3. **Значения секретов для `.env`** — минимум `SECRET_KEY` и MINIO-креды (см. §2, шаг 3).
   Реальные ключи WB / Telegram / VTB **не нужны** локально (прод-снимок отдаёт их замаскированными).
4. Установленный **Claude Code** (уже есть, раз читаешь это).

---

## 1. Требования на машине

- **Docker Desktop** — запущен (всё крутится в контейнерах);
- **Git**;
- **GitHub CLI** `gh` — желательно (для PR/issue из терминала), не обязательно.

Node.js и Python на хост ставить **не надо** — они внутри контейнеров.

---

## 2. Первый запуск (Claude выполняет один раз)

1. **Клонировать репозиторий** (это и есть «зеркало текущего проекта» — прод собирается из `origin/main`):
   ```bash
   git clone git@github.com:vladbydaev37/dds2.git
   cd dds2
   ```
   > Работаем от ветки `dev` (интеграционная). Сразу после клона: `git checkout dev`.

2. **Проверить Docker:** `docker info`. Не запущен — попросить пользователя запустить Docker Desktop.

3. **Создать `.env`:** `cp .env.example .env`, затем задать минимум:
   - `SECRET_KEY` = случайная строка (`openssl rand -hex 32`);
   - `MINIO_ACCESS_KEY=minioadmin`
   - `MINIO_SECRET_KEY=minioadmin123`

   Ключи WB / Telegram / VTB **оставить пустыми** — их заменит замаскированный прод-снимок.
   Локальная среда **не** ходит во внешние API (WB/Telegram), даже с прод-данными.

4. **Поднять контейнеры:** `docker compose up -d --build` (первый билд ~5–10 мин, дождаться).
   Docker сам подхватит `docker-compose.override.yml` — dev-режим с hot-reload фронта на `:3000`
   и backend на `:8000`.

5. **Схема БД:** `make migrate` (применит все Alembic-миграции до head).

6. **Налить реальные данные — прод-снимок:** `make sync-prod`.
   - Тянет свежий дамп с прод-data-сервера (SSH `dds-data`) в `backups/prod/`, заливает в локальную БД
     и **маскирует чувствительные поля** (ключи API, токены) через `scripts/sql/mask-sensitive.sql`.
   - Требует настроенного `~/.ssh/config` с `Host dds-data` (см. §0.2). Если SSH недоступен —
     скрипт честно упадёт с подсказкой; тогда для старта можно временно `make seed` (демо-данные,
     логин `demo` / `demo1234`) и вернуться к `sync-prod`, когда дадут доступ.
   - Скрипт сам DROP+CREATE локальную БД, поэтому **несёт защиту**: если `DATABASE_URL` указывает
     на прод — прерывается. Никогда не гоняй его на прод-машине.

7. **Проверить:** `make status` — все сервисы `Up`/healthy. Упало → `make logs`, найти причину.

8. Готово → открыть **http://localhost:3000** (приложение) и **http://localhost:8000/docs** (Swagger API).

Правки фронта видны в браузере сразу (hot-reload). Backend перезапускается автоматически при правке `.py`.

---

## 3. Канон проекта — читать обязательно

Главный always-on файл — **`CLAUDE.md` в корне**. Claude Code видит его автоматически. Ключевое:

### Iron rules (нарушение = баг; часть проверяет hook `scripts/hooks/post_edit_check.py`)
1. Каждый DB-запрос фильтрует `project_id`.
2. Модель с `SoftDeleteMixin` → `.where(Model.is_deleted == False)`.
3. Удаление → `model.soft_delete()`, не `db.delete()`.
4. Время → `from backend.utils.time import utcnow`, не `datetime.utcnow()`.
5. Деньги → `Numeric(18, 2)`, не `Float`.
6. SQL → `:param`-binding, не f-string в `text()`.
7. После мутации → `invalidate_cache(prefix)`.
8. Бизнес-логика → `services/`; роутер — только HTTP и валидация.
9. Write-эндпоинты → `Depends(rate_limit_write)`.

### Архитектура
`routers/` (HTTP) → `services/` (логика) → `models/` (ORM). `schemas/` — Pydantic, `etl/` — парсеры,
`integrations/` — внешние API, `scheduler/jobs/` — только worker-контейнер.
**Порядок нового модуля:** Model → Migration → Schema → Service → Router → Test.
**Frontend:** `src/app/(main)/p/[slug]/` — основное, `(tma)/tma/[slug]/` — Telegram Mini App.
Клиент — `src/lib/api/`, типы — `src/types/api.ts`. Числа через `formatNumber()`.
На каждой странице обязательны состояния **loading / error / empty / data**.

Детальные правила подгружаются по путям автоматически при правке кода:
`.claude/rules/{backend,frontend,design,migrations}.md`. Накопленные грабли — `.claude/rules/learnings.md`.

---

## 4. Как здесь настроен Claude Code

Всё это уже в репозитории (`.claude/`) и приезжает вместе с клоном — ничего доустанавливать не нужно.

- **Skills** (`/<имя>` или по контексту) — типовые сценарии: `/feature` (сквозная фича),
  `/plan`, `/new-endpoint`, `/new-page`, `/migration`, `/bug`, `/autofix`, `/build-fix`,
  `/verify`, `/review`, `/ship`, `/hotfix`, `/rollback`, `/learn`.
- **Субагенты** (`.claude/agents/`) — профильные ревьюеры на Opus (`code-reviewer`,
  `security-reviewer`, `database-reviewer`, `performance-optimizer`, `api-designer`,
  `frontend-reviewer`) и исполнители (`test-runner`, `debugger`, `docs-syncer`,
  `refactorer`, `prod-diagnost`).
- **Хуки** (`scripts/hooks/`) — авто-проверки: `post_edit_check.py` энфорсит iron rules 1–6,
  `prompt_quality_check.py` подсвечивает расплывчатые ТЗ, `pre-push` гоняет тесты бэкенда.
- **Правила по путям** — `.claude/rules/*.md` подгружаются, когда трогаешь профильные файлы.

### Роутинг задач (из `CLAUDE.md`)
| Запрос | Действие |
|---|---|
| новый endpoint / страница / миграция | `/new-endpoint` · `/new-page` · `/migration` |
| фича от плана до отправки | `/feature` |
| только спланировать | `/plan` |
| прод упал / откат | `/hotfix` · `/rollback` |
| pytest/сборка падают | `/build-fix` |
| баг с тестируемым критерием | `/autofix` |
| перед коммитом / отправка / после | `/verify` · `/ship` · `/learn` |
| баг, вопрос, мелкая правка | без skill — сразу |

**После кода — `/review`** (фан-аут профильных субагентов по diff, вердикт APPROVE/WARNING/BLOCK),
обычно шагом `/verify`.

---

## 5. Рабочий цикл (daily flow)

Разработка — через **TDD**: сначала падающий тест, потом минимальная реализация.

```bash
git checkout dev && git pull origin dev     # всегда от свежего dev
git checkout -b feat/<короткое-описание>    # feat/ fix/ infra/ refactor/ test/
# ... код + тесты (Claude пишет по правилам) ...
```

**Перед коммитом — прогнать гейты (`/verify` делает это за тебя):**
```bash
make test-fast                 # backend pytest (-n 2, не менять на auto)
make lint                      # ruff + проверка конвенций
make typecheck                 # mypy (services + models, как в CI)
cd frontend-react && npx vitest run   # frontend тесты
```
> Backend-тестам может понадобиться `REGISTER_ENABLED=true` в окружении контейнера.
> Фронт-гейты гоняй через контейнер, а не хостовый npm (на хосте Node нет).

**Отправка (прямой push — деплой через CI):**
```bash
git add -A && git commit -m "feat: описание"   # русский текст ok
git push origin feat/<...>                      # затем PR в dev, или push прямо в dev
```
Поток деплоя: **`dev` → CI зелёный → PR/merge в `main` → авто-деплой** (`cd-production.yml`).
Деплой **только через CI**, вручную по SSH на прод не деплоим.

### Миграции
Alembic — sequential, цепляй новую за **голову `origin/dev`** (не за локальный хвост).
Перед коммитом: `alembic upgrade head && downgrade -1 && upgrade head`.
Одновременно две ветки миграции не плодить (иначе две головы → merge-миграция).

### Документация
Обновление docs (`backend/DOMAIN_*.md`, `MAP.md`) — **в тот же коммит**, что и код (`docs-syncer` помогает).

---

## 6. Границы и безопасность

- **Секреты не коммитить.** `.env` в `.gitignore`. Прод-ключи локально не нужны — снимок их маскирует.
- **`sync-prod` — только локально.** Скрипт защищён от запуска на прод-URL, но перепроверяй `DATABASE_URL`.
- **Прод трогаем только через CI.** SSH к прод-серверам — для `sync-prod`, диагностики (`prod-diagnost`),
  `/hotfix` и `/rollback`, не для ручного деплоя.
- **fail2ban на прод-серверах:** не долбить SSH подряд (забанит на минуты) — пауза, потом 1 коннект.
- **Не пушить в `main` напрямую** — только через `dev` → CI.

---

## 7. Шпаргалка

| Действие | Команда |
|---|---|
| Поднять всё | `make dev` (или `docker compose up -d`) |
| Остановить | `make stop` |
| Логи backend | `make logs` |
| Статус сервисов | `make status` |
| Применить миграции | `make migrate` |
| Новая миграция | `make migrate-new MSG="описание"` |
| Налить прод-данные | `make sync-prod` |
| Демо-данные (без прода) | `make seed` |
| Backend тесты | `make test-fast` |
| Линт + конвенции | `make lint` |
| Типы (mypy) | `make typecheck` |
| Консоль PostgreSQL | `make shell-db` |
| Пересобрать после `git pull` | `docker compose up -d --build` |
| Все команды | `make help` |

**Если сломалось** — показать Claude вывод `make logs` или текст ошибки. Частое:
пустой/неполный `.env` (§2 шаг 3), занятый порт 3000/8000/5432, забыли `make migrate`,
SSH `dds-data` недоступен для `sync-prod` (§0.2).
