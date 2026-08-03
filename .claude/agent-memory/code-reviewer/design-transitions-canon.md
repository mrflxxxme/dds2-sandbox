---
name: design-transitions-canon
description: «Дизайн карточек» — словарь переходов заморожен и продублирован golden-snapshot'ом; дыра ON_HOLD→REVISION (ревью Ф0) закрыта на Ф1
metadata:
  type: project
---

Словарь `DESIGN_TASK_TRANSITIONS` (`backend/models/design.py`) продублирован **дословно** golden-snapshot'ом `tests/test_design_models.py::test_transitions_dict_snapshot` и матрицей «кто двигает» в `docs/specs/design/phases/F1-service-core.md`. Любая правка ребра = три синхронных изменения + эскалация архитектору (CHARTER §7).

**Закрыто:** дыра Ф0 «REVISION→ON_HOLD есть, обратно нет» устранена — ребро `ON_HOLD→REVISION` добавлено в словарь, снапшот и таблицу спека; сервис (`state.apply_transition_locked`) чистит `held_from_status` на любом выходе из ON_HOLD. Проверено на ревью Ф1 (2026-08-02), больше не поднимать.

**Why:** после мержа Ф0 схема и словарь заморожены — расхождение кода, теста и спека стоило бы дороже всего именно здесь.

**How to apply:** при ревью любой фазы модуля сверять ребро сразу в трёх местах; расхождение спек↔код по матрице прав — так же (единственный энфорсер матрицы — `state.can_user_transition`, тестов на неё две штуки: `test_design_permissions.py` и `test_design_state.py`).
