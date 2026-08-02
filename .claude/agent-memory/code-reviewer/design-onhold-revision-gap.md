---
name: design-onhold-revision-gap
description: Модуль «Дизайн карточек» — DESIGN_TASK_TRANSITIONS позволяет REVISION→ON_HOLD, но не ON_HOLD→REVISION; held_from_status для REVISION нерабочий (найдено на ревью Ф0)
metadata:
  type: project
---

В `DESIGN_TASK_TRANSITIONS` (`backend/models/design.py`) `ON_HOLD` возвращает только в `{NEW, ASSIGNED, IN_PROGRESS, CANCELLED}`, при этом вход `REVISION→ON_HOLD` разрешён. Значит задача, отложенная из «Правок», прямого возврата в `REVISION` не имеет — `held_from_status='REVISION'` не примется в `change_status` (Ф1), возврат только окольным IN_PROGRESS→REVIEW→REVISION.

Так написано и в самом спеке `docs/specs/design/phases/F0-spine.md:61`, т.е. код спеке соответствует — противоречие внутри спеки (Р1 в CHARTER обещает «возврат из ON_HOLD через held_from_status»). Докстринг `backend/models/design.py` при этом рисует `ON_HOLD ⇄ …/REVISION`, чего в словаре нет.

**Why:** поймано на code-ревью Ф0 (2026-08-02). После мержа Ф0 схема и словарь заморожены (CHARTER §7: изменение словаря переходов = эскалация к архитектору), поэтому чинить дешевле всего до мержа.

**How to apply:** при ревью Ф1 (`change_status`, AC-6) проверять, обрабатывается ли `held_from_status == 'REVISION'` — иначе там 400/тупик. Если словарь так и остался прежним, требовать явного гварда либо запрета `REVISION→ON_HOLD`. Связано с [[warehouse-need-invariants]] по типу «инвариант, который тесты формально проходят, но семантика ломается».
