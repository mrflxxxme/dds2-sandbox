# DDS Code Review Instructions

## Два уровня ревью

| Уровень | Когда | Инструмент | Стоимость |
|---|---|---|---|
| **Default** | любой PR | `claude-review.yml` (opus-4-7, single-agent, 20-25 turns) — авто | 0 (Max subscription) |
| **Deep** ☁️ | label `deep-review` | `/ultrareview` (multi-agent verification в облаке) — вручную через CLI | $15-$25 per review |

## Когда запускать `/ultrareview` (multi-agent, платно)

Auto-label `deep-review` ставится workflow'ом [auto-label-deep-review.yml](.github/workflows/auto-label-deep-review.yml) при:
- **Миграции** — любой файл в `migrations/versions/` (невозможно откатить после merge в main)
- **Money-handling** — новые/изменённые `Numeric(18,` в diff (риск money rounding / precision bugs)
- **Auth/crypto** — файлы с `auth`, `crypto`, `jwt`, `password`, `security`, `rate_limit` в имени
- **Huge PR** — > 1000 LOC изменений

**Ручной триггер:** поставить label `deep-review` вручную на любой PR, если чувствуешь риск. Снять label если ручного ревью достаточно — `/ultrareview` не запустится.

### Как запустить `/ultrareview`
1. Убедись что PR auto-labelled `deep-review` (или поставил вручную)
2. В Claude Code CLI: `/ultrareview` (я вызову) — multi-agent анализ на Anthropic infrastructure
3. Результат: inline-комменты в PR + severity ranking
4. Merge только после `/ultrareview` green (или после явного «не применимо — ложно-положительные»)

### Бюджет
Стартуем на сценарии **D (label only)**: ~2-3 ревью/месяц, ~$30-75/мес. Первые 3 бесплатных запуска тратим на реально опасные PR для оценки value vs `claude-review.yml` single-agent.

---

## Default review (claude-review.yml)

Срабатывает автоматически на каждый PR.

### Калибровка severity
- **Important**: баги, проблемы безопасности, риск потери данных, сломанная бизнес-логика
- **Nit**: стиль, нейминг, мелкий рефакторинг
- **Pre-existing**: проблемы в неизменённом коде

### Лимит nits
Максимум 5 nits на ревью. Для остальных — "плюс N аналогичных".

### НЕ репортить
- То что проверяют pre-commit хуки (Ruff, Gitleaks, Bandit)
- Сгенерированные файлы, файлы миграций (если нет ошибок логики)
- Порядок импортов (Ruff)

### ВСЕГДА проверять
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
