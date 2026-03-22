---
description: "Ревью кода DDS2 — безопасность, качество, конвенции. Проверяет uncommitted changes."
---

# Code Review — DDS2

Комплексное ревью безопасности и качества незакоммиченных изменений.

## Инструкции

1. Получить изменённые файлы: `git diff --name-only HEAD`

2. Для каждого файла проверить:

**Железные правила DDS2 (БЛОКИРУЮЩИЕ):**
- project_id в каждом запросе к БД
- is_deleted фильтр для SoftDeleteMixin моделей
- soft_delete() вместо db.delete()
- utcnow() вместо datetime.utcnow()
- Numeric(18,2) для денег, не Float
- Параметризованный SQL, не f-string в text()
- invalidate_cache() после мутаций
- Бизнес-логика в services/, не routers/

**Безопасность (CRITICAL):**
- Хардкод-секреты (API keys, passwords, tokens)
- SQL injection
- XSS уязвимости
- Отсутствие валидации ввода
- ilike() без экранирования % и _

**Качество (HIGH):**
- Функции > 50 строк
- Файлы > 400 строк
- Вложенность > 4 уровней
- Пустые except/catch блоки
- .scalars().all() без .limit()

3. Также запустить: `bash scripts/check_conventions.sh`

4. Сгенерировать отчёт с severity и предложенными фиксами.

5. Блокировать коммит если найдены CRITICAL проблемы.
