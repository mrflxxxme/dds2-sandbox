---
name: new-endpoint
description: "Новый API endpoint DDS2: schema → service → router → test, с проверкой конвенций и docs."
---

# /new-endpoint — новый API endpoint

## Параметры (спросить, если не дано)
Имя ресурса, домен, операция (list / get / create / update / delete), HTTP-метод, путь.

## Порядок
1. **Schema** — `backend/schemas/{resource}.py`. Классы `XCreate` / `XUpdate` / `XOut`. Шаблон: `.claude/templates/new_schema.py.tmpl`.
2. **Service** — `backend/services/{domain}/{resource}_service.py`. Вся бизнес-логика здесь. Шаблон: `new_service.py.tmpl`. Соблюдай iron rules из `CLAUDE.md` (project_id, is_deleted, soft_delete, кэш). Новый кэш-prefix → добавить в `invalidate_project_reports()` в `backend/cache.py`.
3. **Router** — `backend/routers/{domain}.py`. Только HTTP — вызов сервиса, без логики. Шаблон: `new_router.py.tmpl`. `Depends(get_current_project)` + на write-методах `Depends(rate_limit_write)`. Upload — проверка `MAX_UPLOAD_SIZE_MB` до обработки. Новый файл-роутер → зарегистрировать в `backend/main.py`.
4. **Test** — `tests/test_{resource}.py`. Шаблон: `new_test.py.tmpl`. Покрыть: happy path, edge case, multi-tenancy (чужой `project_id` не видит), soft-delete, Decimal для денег.
5. **Frontend** (если нужен UI) — тип в `src/types/api.ts`, метод в `src/lib/api/{domain}.ts`.
6. **Docs** — обновить `backend/DOMAIN_{DOMAIN}.md`; новая модель → `models/__init__.py` (SoftDelete-модели энфорсеры находят сами).

## Verify
`bash scripts/check_conventions.sh` · `make test-changed` · `bash scripts/check_docs.sh`.
