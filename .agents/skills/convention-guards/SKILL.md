---
name: convention-guards
description: Guard-code helpers для предотвращения повторяющихся багов — datetime, project_id, SQL injection. Используй при КАЖДОМ изменении backend-кода.
---

# Skill: Convention Guards

> ⚠️ **Этот скилл описывает ОБЯЗАТЕЛЬНЫЕ helpers. Используй их при каждом изменении backend-кода.**

## Когда применять

- Пишешь **новый** сервис, роутер, модель
- Редактируешь **существующий** код — заменяй старые паттерны на helpers
- Фиксишь баг — проверь, не забыт ли project_id или datetime

---

## 1. Datetime — `utils/time.py`

### Проблема
- `datetime.utcnow()` — deprecated в Python 3.12+
- `datetime.now(timezone.utc)` — ломает asyncpg с `TIMESTAMP WITHOUT TIME ZONE`
- **Оба варианта напрямую ЗАПРЕЩЕНЫ**

### Решение

```python
from backend.utils.time import utcnow

# В сервисном коде:
sync_log.finished_at = utcnow()
key.last_sync_at = utcnow()

# В модели (default):
created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

# В модели (onupdate):
updated_at: Mapped[datetime] = mapped_column(
    DateTime, default=utcnow, onupdate=utcnow
)
```

### ❌ НЕЛЬЗЯ

```python
# НЕЛЬЗЯ — deprecated:
expire = datetime.utcnow() + timedelta(minutes=30)

# НЕЛЬЗЯ — ломает asyncpg:
expire = datetime.now(timezone.utc) + timedelta(minutes=30)

# ПРАВИЛЬНО:
from backend.utils.time import utcnow
expire = utcnow() + timedelta(minutes=30)
```

---

## 2. SQL-запросы — `utils/queries.py`

### Проблема
Забытый `.where(Model.project_id == project_id)` → утечка данных между проектами.

### Решение

```python
from backend.utils.queries import project_select, project_select_active

# ВМЕСТО: select(WbPayout).where(WbPayout.project_id == project_id)
# ПИШЕМ:
result = await db.execute(project_select(WbPayout, project_id))

# Для моделей с SoftDeleteMixin (авто-фильтр is_deleted=False):
result = await db.execute(project_select_active(Order, project_id))

# Добавление ORDER BY, LIMIT и т.д.:
q = project_select(WbPayout, project_id).order_by(WbPayout.id.desc()).limit(100)
result = await db.execute(q)
```

### Когда НЕ использовать

```python
# Модели БЕЗ project_id (users, fx_rates) — обычный select():
result = await db.execute(select(User).where(User.id == user_id))
```

### ❌ НЕЛЬЗЯ

```python
# НЕЛЬЗЯ — забыт project_id:
result = await db.execute(select(WbPayout))

# НЕЛЬЗЯ — забыт is_deleted:
result = await db.execute(
    select(Order).where(Order.project_id == project_id)
)
# ПРАВИЛЬНО:
result = await db.execute(project_select_active(Order, project_id))
```

---

## 3. Автоматическая проверка

Перед каждым коммитом — запусти:

```bash
bash scripts/check_conventions.sh
```

Проверяет:
1. `asyncio.get_event_loop()` (→ `get_running_loop()`)
2. `print()` в backend (→ `logger`)
3. Файлы >500 строк
4. `Float` для денег (→ `Numeric(18, 2)`)
5. f-string в `text()` (SQL injection)
6. Сервисы без `project_id`

CI запускает этот скрипт автоматически (`.github/workflows/ci.yml`).

---

## ⛔ Чеклист при изменении backend-кода

- [ ] Datetime через `from backend.utils.time import utcnow`
- [ ] SQL через `project_select()` / `project_select_active()` для моделей с `project_id`
- [ ] `Numeric(18, 2)` для денег, НЕ `Float`
- [ ] `logging.getLogger("dds.module")`, НЕ `print()`
- [ ] `asyncio.get_running_loop()`, НЕ `get_event_loop()`
- [ ] `bash scripts/check_conventions.sh` — все проверки пройдены
