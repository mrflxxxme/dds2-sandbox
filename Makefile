# DDS — Makefile
# Быстрые команды для повседневной разработки

.PHONY: dev stop logs test lint deploy seed migrate backup restore status ssl-init

# ─── Разработка ──────────────────────────────────────────────────────────────

dev: ## Запустить все сервисы
	docker compose up -d
	@echo "✅ API:      http://localhost:8000"
	@echo "✅ Frontend: http://localhost:3000"
	@echo "✅ Логи:     make logs"

stop: ## Остановить все сервисы
	docker compose down

logs: ## Логи backend (follow)
	docker compose logs -f backend --tail=50

logs-all: ## Логи всех сервисов
	docker compose logs -f --tail=30

logs-worker: ## Логи worker (scheduler)
	docker compose logs -f worker --tail=50

status: ## Статус контейнеров
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'

# ─── Тесты ───────────────────────────────────────────────────────────────────

test: ## Запустить все тесты
	docker compose exec -T backend pytest tests/ -x --tb=short

test-v: ## Тесты с подробным выводом
	docker compose exec -T backend pytest tests/ -v --tb=long

test-fast: ## Параллельные тесты (pytest-xdist)
	docker compose exec -T backend pytest tests/ -n auto --tb=short -q

test-changed: ## Только изменённые тесты (testmon)
	docker compose exec -T backend pytest --testmon --tb=short -q

test-unit: ## Только unit-тесты
	docker compose exec -T backend pytest tests/ -m unit --tb=short -q

lint: ## Линтер + convention checks
	ruff check backend/ --select E,W,F --ignore E501
	bash scripts/check_conventions.sh

# ─── База данных ─────────────────────────────────────────────────────────────

migrate: ## Применить миграции
	docker compose exec -T backend alembic upgrade head

migrate-new: ## Создать новую миграцию (usage: make migrate-new MSG="описание")
	docker compose exec -T backend alembic revision --autogenerate -m "$(MSG)"

backup: ## Ручной бэкап БД
	docker compose exec -T db-backup /scripts/backup.sh
	@echo "✅ Бэкап создан в ./backups/"

restore: ## Восстановить БД из бэкапа (usage: make restore FILE=backup.sql.gz)
	docker compose exec -T db-backup /scripts/restore.sh $(FILE)

pull-prod: ## Скачать свежий прод-снимок в backups/prod/ (без записи в БД)
	bash scripts/pull-prod-snapshot.sh

pull-prod-fresh: ## Создать НОВЫЙ дамп на проде, потом скачать
	bash scripts/pull-prod-snapshot.sh --fresh

load-prod: ## Залить прод-снимок в локальную БД + маскировка (требует confirm)
	bash scripts/load-prod-snapshot.sh

sync-prod: pull-prod load-prod ## pull-prod + load-prod одной командой

# ─── Сборка ──────────────────────────────────────────────────────────────────

build-backend: ## Пересобрать backend (после изменения requirements)
	docker compose up -d --build backend

build-frontend: ## Пересобрать frontend (после изменения package.json)
	docker compose up -d --build frontend-react

build-all: ## Пересобрать всё
	docker compose up -d --build

# ─── Деплой ──────────────────────────────────────────────────────────────────

commit: ## Git add + commit (usage: make commit MSG="feat: описание")
	git add -A
	git commit -m "$(MSG)"

push: ## Push в dev
	git push origin dev

deploy: commit push ## Commit + push в dev = автодеплой на staging
	@echo "✅ Запушено в dev → staging автодеплой"

deploy-prod: ## Merge dev → main → production
	git checkout main
	git merge dev
	git push origin main
	git checkout dev
	@echo "✅ Задеплоено в production"

# ─── SSL ─────────────────────────────────────────────────────────────────────

ssl-init: ## Первичная настройка SSL (запускать на сервере)
	bash scripts/ssl-setup.sh

ssl-renew: ## Обновить SSL сертификат
	docker compose run --rm certbot renew --quiet
	docker compose exec -T nginx nginx -s reload
	@echo "✅ Сертификат обновлён"

ssl-status: ## Проверить срок действия SSL
	docker compose run --rm certbot certificates

# ─── Утилиты ─────────────────────────────────────────────────────────────────

seed: ## Загрузить тестовые данные на staging
	docker compose exec -T backend python -m scripts.seed_demo

shell-db: ## Консоль PostgreSQL
	docker compose exec -T db psql -U dds -d dds_db

shell-redis: ## Консоль Redis
	docker compose exec -T redis redis-cli

clean: ## Удалить volumes (ОСТОРОЖНО!)
	docker compose down -v
	@echo "⚠️  Все данные удалены"

# ─── Setup ──────────────────────────────────────────────────────────────────

setup: ## Установить git hooks (pre-commit + pre-push)
	bash scripts/setup-hooks.sh

# ─── Multi-Claude (parallel worktrees) ───────────────────────────────────────

wt-start: ## Создать worktree'ы для параллельной работы (usage: make wt-start F=feature-name)
	bash scripts/worktree-start.sh "$(F)"

wt-finish: ## Слить worktree'ы обратно в dev (usage: make wt-finish F=feature-name)
	bash scripts/worktree-finish.sh "$(F)"

wt-list: ## Показать активные worktree'ы
	@git worktree list

# ─── Type sync (FastAPI → TypeScript) ────────────────────────────────────────

sync-types: ## Сгенерировать TS типы из FastAPI OpenAPI schema
	bash scripts/sync-api-types.sh

# ─── Help ────────────────────────────────────────────────────────────────────

help: ## Показать все команды
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-16s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
