---
description: "TDD workflow для DDS2. Тесты ПЕРВЫМИ, потом минимальная реализация. RED → GREEN → REFACTOR."
---

# TDD — DDS2

Enforce test-driven development для проекта DDS2.

## Цикл

```
RED:      Написать падающий тест
GREEN:    Минимальный код чтобы прошёл
REFACTOR: Улучшить, тесты зелёные
```

## Шаги

1. **Определить интерфейс** — что принимает, что возвращает
2. **Написать тест ПЕРВЫМ** — он ДОЛЖЕН упасть
3. **Запустить** — `docker compose exec backend pytest tests/test_X.py -x`
4. **Убедиться что ПАДАЕТ** по правильной причине
5. **Написать минимальную реализацию**
6. **Запустить** — убедиться что ПРОХОДИТ
7. **Рефакторинг** — тесты остаются зелёными
8. **Финальная проверка** — все тесты + конвенции

## Обязательные тест-кейсы DDS2

- **Multi-tenancy** — данные изолированы по project_id
- **Soft delete** — удалённые записи не в выборках
- **Деньги** — Decimal точность до копеек
- **Пустой проект** — нулевые отчёты
- **Дубли импорта** — идемпотентность
- **WB API ошибки** — graceful degradation

## Запуск тестов

```bash
# Один тест
docker compose exec backend pytest tests/test_X.py -x --tb=short

# Все тесты
docker compose exec backend pytest tests/ -x

# С coverage
docker compose exec backend pytest tests/ --cov=backend --cov-report=term-missing
```

**ОБЯЗАТЕЛЬНО**: Тесты пишутся ДО реализации. Никогда не пропускай RED фазу.
