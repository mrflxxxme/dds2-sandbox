---
name: smoke
description: Быстрая проверка (30 сек) — импорты, тесты, конвенции. Между шагами вместо /verify.
---

Выполни быструю проверку что ничего не сломано:

1. **Импорты backend** — проверь что все модели импортируются:
   ```
   docker compose exec backend python -c "from backend.models import *; print('Models OK')"
   ```

2. **Быстрые тесты** — запусти с timeout:
   ```
   docker compose exec backend pytest tests/ -x --timeout=60 -q 2>&1 | tail -20
   ```

3. **Конвенции**:
   ```
   bash scripts/check_conventions.sh 2>&1 | tail -10
   ```

Формат ответа:
```
SMOKE TEST:
  Models:      OK / FAIL (описание)
  Tests:       OK / FAIL (X passed, Y failed)
  Conventions: OK / FAIL (описание)
  Result:      PASS ✅ / FAIL ❌
```

Если FAIL — покажи конкретную ошибку и предложи фикс.
НЕ запускай frontend build — это для /verify.
