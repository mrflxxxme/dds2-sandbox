# DDS Code Review Instructions

## Калибровка severity
- **Important**: баги, проблемы безопасности, риск потери данных, сломанная бизнес-логика
- **Nit**: стиль, нейминг, мелкий рефакторинг
- **Pre-existing**: проблемы в неизменённом коде

## Лимит nits
Максимум 5 nits на ревью. Для остальных — "плюс N аналогичных".

## НЕ репортить
- То что проверяют pre-commit хуки (Ruff, Gitleaks, Bandit)
- Сгенерированные файлы, файлы миграций (если нет ошибок логики)
- Порядок импортов (Ruff)

## ВСЕГДА проверять
- Новые API роуты имеют тесты
- SQL запросы фильтруют по `project_id` и `is_deleted`
- `soft_delete()` вместо `db.delete()`
- Database запросы async (asyncpg), никогда sync
- Services не импортируют из routers (нарушение слоёв)
- Frontend API вызовы через TanStack Query хуки
- Alembic миграции имеют `downgrade()`
- Нет хардкод credentials или API keys
- Docker exec команды используют `-T` флаг
- Денежные значения: `Numeric(18,2)`, никогда Float
- `datetime` через `backend.utils.time.utcnow()`
