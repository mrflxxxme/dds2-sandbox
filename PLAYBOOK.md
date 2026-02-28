# DDS Playbook — Как вносить изменения

> Этот документ описывает процесс разработки.
> Следуй этим шагам при любом изменении: фича, багфикс, рефакторинг.

---

## Процесс: Контракт → Код → Тесты → Review

### 1. Контракт (Schema First)

**Прежде чем писать код**, определи API-контракт:

```python
# backend/schemas.py
class MyFeatureRequest(BaseModel):
    """Что мы принимаем."""
    field: str

class MyFeatureResponse(BaseModel):
    """Что мы возвращаем."""
    id: int
    field: str
```

- Если меняешь существующий эндпоинт — проверь, что не ломаешь фронтенд
- Новые поля — `Optional` с дефолтами
- Удаление полей — НЕЛЬЗЯ без версионирования API

### 2. Код

Порядок написания:
1. **Model** (models.py) — если нужна новая таблица/поле
2. **Migration** — `alembic revision --autogenerate -m "описание"`
3. **Schema** (schemas.py) — request/response Pydantic-модели
4. **Service** (если сложная логика) — бизнес-логика
5. **Router** — эндпоинт, связывает schema + service

### 3. Тесты

- Unit-тесты для бизнес-логики → `tests/test_*.py`
- API-тесты для эндпоинтов → `tests/test_api_*.py`
- Запуск: `pytest tests/ -v`

### 4. Review

- Коммит с описательным сообщением (см. CONVENTIONS.md)
- `git push` → проверить на GitHub

---

## Чеклист для нового модуля

```
[ ] Описать контракт в schemas.py
[ ] Создать/обновить модель в models.py
[ ] Создать Alembic миграцию
[ ] Написать роутер в routers/
[ ] Написать тесты в tests/
[ ] Обновить ARCHITECTURE.md (границы модуля)
[ ] Обновить api_client (frontend) или TypeScript типы (React)
[ ] Коммит + push
```

---

## Чеклист для новой интеграции (WB, банки)

```
[ ] Создать файл в integrations/ (например wb_api.py)
[ ] Модель для хранения API-ключей (integration_keys)
[ ] Модель для лога синхронизации (sync_log)
[ ] Роутер в routers/integrations.py
[ ] Тест с mock HTTP-ответами
[ ] Документировать в ARCHITECTURE.md
```

---

## Чеклист для багфикса

```
[ ] Воспроизвести баг (описать шаги)
[ ] Написать falling test
[ ] Исправить код
[ ] Тест проходит
[ ] Коммит: fix: описание
```

---

## Как запустить проект

```bash
# Первый запуск
cp .env.example .env
docker compose up --build -d

# Проверка
curl http://localhost:8000/health
open http://localhost:8501

# Тесты
pytest tests/ -v

# Миграция
alembic revision --autogenerate -m "описание"
alembic upgrade head

# Логи
docker compose logs -f backend
```
