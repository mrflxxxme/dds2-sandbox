---
description: "Создать новый API endpoint в DDS2: schema → service → router → test, с проверкой конвенций и обновлением docs."
---

# /new-endpoint — Новый API endpoint DDS2

## Параметры (спрашивай у пользователя если не дано)
1. **Имя ресурса** (например, `assembly_request`)
2. **Домен** (warehouse / wb / cost / planning / assembly / supply_chain / reports / refs)
3. **Операция** (list / get / create / update / delete / custom)
4. **HTTP метод** (GET / POST / PUT / DELETE)
5. **Путь** (например, `/projects/{project_id}/assembly/{id}`)

## Чеклист (выполнять в порядке)

### 1. Schema (Pydantic)
- Файл: `backend/schemas/{resource}.py` (создать или дополнить)
- Если новый файл — взять template `.claude/templates/new_schema.py.tmpl`
- Классы: `XCreate`, `XUpdate`, `XOut` (минимум что нужно)
- Все поля с типами; `Decimal` для денег

### 2. Service (бизнес-логика)
- Файл: `backend/services/{domain}/{resource}_service.py` (или `backend/services/{resource}_service.py`)
- Если новый файл — взять template `.claude/templates/new_service.py.tmpl`
- ОБЯЗАТЕЛЬНО:
  - `project_id` фильтр в каждом query
  - `is_deleted == False` для SoftDeleteMixin моделей
  - `soft_delete()` НЕ `db.delete()`
  - `from backend.utils.time import utcnow`
  - Кэш: `@cached(prefix="...", ttl=300)` на read, `invalidate_cache(prefix)` на write
  - При новом prefix кэша → добавить в `invalidate_project_reports()` в `backend/cache.py`

### 3. Router (HTTP only)
- Файл: `backend/routers/{domain_or_resource}.py` (дополнить существующий или создать)
- Если новый файл — template `.claude/templates/new_router.py.tmpl`
- ОБЯЗАТЕЛЬНО:
  - `Depends(get_current_project)` — auth + project_id
  - Write endpoints: `Depends(rate_limit_write)` из `backend/utils/rate_limit.py`
  - Бизнес-логика — НЕ здесь, только вызов сервиса
  - Upload endpoints: проверка `MAX_UPLOAD_SIZE_MB` ПЕРЕД обработкой
  - Регистрация роутера в `backend/main.py` если файл новый

### 4. Test (TDD — пиши ПЕРВЫМ если новая логика)
- Файл: `tests/test_{resource}.py` (или `tests/test_{domain}.py`)
- Template: `.claude/templates/new_test.py.tmpl`
- Минимум:
  - Happy path
  - Edge case (null/пустой/максимум)
  - Multi-tenancy (другой project_id не видит)
  - Soft-delete (удалённые не в выборке)
  - Если деньги — Decimal precision

### 5. Frontend (если нужен UI)
- Тип в `frontend-react/src/types/api.ts`
- Метод в `frontend-react/src/lib/api/{domain}.ts`
- НЕ inline fetch (только api.ts)

### 6. Документация
- Запусти логику `/docs` ИЛИ обнови вручную:
  - `backend/DOMAIN_{DOMAIN}.md` — описание endpoint и поведения
  - `backend/MAP.md` — если новый сервис/роутер
  - Если новая модель → `models/__init__.py` + SOFT_MODELS в `scripts/check_conventions.sh`

### 7. Verify
- `bash scripts/check_conventions.sh` — passed
- `make test-changed` — passed
- `bash scripts/check_docs.sh` — passed

## Антипаттерны (НЕ ДЕЛАТЬ)
- Запрос без `project_id` → дыра multi-tenancy
- f-string в SQL → injection
- Float для денег
- `.scalars().all()` без `.limit()`
- Бизнес-логика в роутере
- Прямой `db.delete()` для SoftDeleteMixin
- Новый кэш-prefix без записи в `invalidate_project_reports()`

## Отчёт пользователю
```
| Файл | Создан/Обновлён |
|------|-----------------|
| schemas/X.py | created (Create/Update/Out) |
| services/X_service.py | created (5 функций) |
| routers/X.py | updated (3 endpoint) |
| tests/test_X.py | created (8 кейсов) |
| DOMAIN_X.md | updated |
```
