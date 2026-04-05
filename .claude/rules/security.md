---
paths:
  - "**/*.py"
  - "**/*.ts"
  - "**/*.tsx"
---
# Безопасность DDS2

## Обязательные проверки перед коммитом

- [ ] Нет хардкод-секретов (API keys, passwords, tokens)
- [ ] SQL — только параметризованные запросы (`:param` binding)
- [ ] Нет f-string в `text()` — SQL injection
- [ ] `ilike()` с экранированием `%` и `_`
- [ ] Все входные данные валидируются (Pydantic schemas)
- [ ] API-ключи WB шифруются через `utils/crypto.py`
- [ ] PII/секреты не попадают в логи
- [ ] CORS_ORIGINS ограничен в production
- [ ] Rate limiting на публичных endpoints

## Шифрование API-ключей
- Fernet symmetric encryption: `backend/utils/crypto.py`
- legacy_fallback — НЕ менять без data-migration
- Ключи НЕ возвращаются в API response (только маскированные)

## Multi-tenancy изоляция
- КАЖДЫЙ запрос фильтрует по `project_id`
- `get_current_project()` dependency в каждом роутере
- Нет cross-tenant data leaks
- **Дочерние сущности без project_id** (CostOrderItem и т.п.) — ВСЕГДА проверять parent.project_id перед выдачей
- `project_id` в моделях — ВСЕГДА `nullable=False` (если не глобальная справочная таблица)

## Файловый upload
- КАЖДЫЙ upload-эндпоинт ОБЯЗАН проверять `MAX_UPLOAD_SIZE_MB` ПЕРЕД обработкой
- Паттерн: `data = await file.read(); if len(data) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024: raise HTTPException(413)`

## scheduler/jobs — asyncio safety
- `except Exception` в scheduler jobs ВСЕГДА должен быть ПОСЛЕ `except asyncio.CancelledError: raise`
- Без этого graceful shutdown worker поглощает CancelledError и продолжает работу

## Финансовые вычисления
- `Decimal` для всех промежуточных расчётов (себестоимость, пошлины, налоги)
- `float()` — ТОЛЬКО на границе JSON-сериализации
- В моделях — `Numeric(18, 2)` (НИКОГДА `Numeric(14, 2)` или `Float`)

## Кэш-инвалидация при мутациях
- Изменение `tax_rate` → invalidate: `reports:wb_bdr`, `reports:opiu`, `reports:dashboard`, `funnel`
- Изменение `vat_rate` → invalidate: `cost`, `reports:wb_bdr`, `reports:opiu`, `reports:dashboard`
- **Правило:** мутации настроек проекта — через `project_settings_service`, НЕ напрямую в роутере

## Автоматические проверки безопасности
- **Bandit** — pre-commit hook, сканирует Python на injection, eval, weak crypto (конфиг: `bandit.yaml`)
- **Ruff S-rules** — flake8-bandit правила в `ruff.toml`
- **Gitleaks** — pre-commit hook, ищет секреты в коде
- **pip-audit** — CI workflow, проверяет CVE в зависимостях
- **Trivy** — CI workflow, filesystem scan (HIGH/CRITICAL)

## При обнаружении уязвимости
1. СТОП — не продолжать
2. Использовать агент `security-reviewer`
3. Исправить CRITICAL перед продолжением
4. Ротировать скомпрометированные секреты
