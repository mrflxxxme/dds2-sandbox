---
paths:
  - "**/*.py"
  - "tests/**"
---
# Тестирование DDS2

## Framework: pytest (asyncio_mode=auto)

## TDD — обязательный workflow
1. Написать тест ПЕРВЫМ (RED)
2. Запустить — убедиться что ПАДАЕТ
3. Написать минимальную реализацию (GREEN)
4. Запустить — убедиться что ПРОХОДИТ
5. Рефакторинг (IMPROVE)

## Обязательные тест-кейсы

### Для каждого сервиса
- Happy path
- Edge cases (null, пустые, граничные значения)
- Error paths (ошибки БД, внешних API)
- Multi-tenancy: изоляция по project_id
- Soft delete: удалённые записи не в выборках

### Для финансовых расчётов
- Decimal точность (Numeric(18,2))
- Нулевые суммы
- Большие суммы (99999999999999.99)
- Мультивалюта (RUB, CNY, USD)

### Для ETL/импорта
- Идемпотентность (повторный импорт)
- Дедупликация по txn_id
- Невалидный формат файла
- Пустой файл

### Для WB интеграции
- Rate limiting (429)
- Server errors (500-504)
- Timeout
- Partial data
- Circuit breaker

## Запуск
```bash
make test            # все тесты
make test-fast       # параллельные (pytest-xdist, ~3-4x быстрее)
make test-changed    # только изменённые (pytest-testmon)
make test-unit       # только unit (@pytest.mark.unit)
```

Прямой запуск (без make):
```bash
docker compose exec backend pytest tests/ -x --tb=short
docker compose exec backend pytest tests/test_X.py -x  # Один файл
```

## Перед коммитом
```bash
make test            # или make test-fast
make lint            # ruff + check_conventions.sh
```
Bandit (security) и Gitleaks (секреты) запускаются автоматически через pre-commit hooks.
