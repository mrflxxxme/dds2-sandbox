---
description: "Ревью кода DDS2 — безопасность, качество, конвенции. Проверяет uncommitted changes."
---

# Code Review — DDS2

Комплексное ревью незакоммиченных изменений. Запускает 3 проверки ПАРАЛЛЕЛЬНО.

## Инструкции

1. Получить изменённые файлы: `git diff --name-only HEAD`
   Если файлов нет — сообщить и остановиться.

2. Запустить **параллельно** 3 субагента (Agent tool, `run_in_background: true`):

   **Субагент 1 — code-reviewer:**
   Передать список изменённых файлов. Проверить железные правила DDS2:
   - project_id в каждом запросе к БД
   - is_deleted фильтр для SoftDeleteMixin моделей
   - soft_delete() вместо db.delete()
   - utcnow() вместо datetime.utcnow()
   - Numeric(18,2) для денег, не Float
   - Параметризованный SQL, не f-string в text()
   - invalidate_cache() после мутаций
   - Бизнес-логика в services/, не routers/
   - Функции > 50 строк, файлы > 400 строк, вложенность > 4

   **Субагент 2 — security-reviewer:**
   Передать список изменённых файлов. Проверить:
   - Хардкод-секреты (API keys, passwords, tokens)
   - SQL injection (f-string в text())
   - XSS уязвимости
   - Отсутствие валидации ввода
   - ilike() без экранирования % и _
   - Multi-tenancy: project_id изоляция

   **Субагент 3 — conventions check:**
   Запустить `bash scripts/check_conventions.sh` и вернуть результат.

3. Собрать результаты всех 3 субагентов.

4. Сгенерировать единый отчёт:

```
РЕВЬЮ DDS2
==========

Качество:     [OK/X проблем]
Безопасность: [OK/X проблем]
Конвенции:    [OK/FAIL]

CRITICAL:
- ...

HIGH:
- ...

MEDIUM:
- ...

Готов к коммиту: [ДА/НЕТ]
```

5. Блокировать коммит если найдены CRITICAL проблемы.
