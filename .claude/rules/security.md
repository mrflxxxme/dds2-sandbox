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

## При обнаружении уязвимости
1. СТОП — не продолжать
2. Использовать агент `security-reviewer`
3. Исправить CRITICAL перед продолжением
4. Ротировать скомпрометированные секреты
