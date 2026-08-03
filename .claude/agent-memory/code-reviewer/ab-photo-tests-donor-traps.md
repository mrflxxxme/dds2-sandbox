---
name: ab-photo-tests-donor-traps
description: Ловушки донора services/funnel/ab_photo_tests.py для любого моста поверх него (таксономия ошибок, delete_variant не чистит контроль, txn через WB-HTTP, домен /ab-tests без require_role)
metadata:
  type: project
---

Донор `backend/services/funnel/ab_photo_tests.py` (использован АБ-мостом дизайна, Ф6) несёт четыре неочевидные ловушки — каждая стоит цикла ревью, если её не знать заранее.

1. **Таксономия ошибок донора НЕ одна.** `create_test`/`add_variant` кидают `ValueError` (гварды, PIL, MinIO-недоступность), но `fetch_card` внутри `create_test` кидает `WbContentError` на ЛЮБОЙ не-200 (401/429/5xx) и на сетевую ошибку. Донорский роутер `routers/ab_tests.py` мапит его в 502; чужой роутер-обёртка, который ловит только `ValueError`/`PermissionError`, отдаёт безликий 500 (`exceptions.py` generic-handler даже не логирует). Любой мост обязан ловить `WbContentError` явно.
2. **`delete_variant` не годится для отката полусозданного теста**: он отказывается удалять `is_control` и требует статус draft. Полная зачистка = жёсткий `db.delete` вариантов (модель без SoftDeleteMixin) + `test.soft_delete()`; soft-deleted тест снимает блок «по артикулу уже есть незавершённый тест» (`create_test` фильтрует `is_deleted == False`). Отдельной публичной функции `discard_draft_test` в доноре НЕТ — мосты лезут в модели чужого домена напрямую.
3. **`create_test` сам держит транзакцию через внешний HTTP**: SELECT existing + `_get_keys` открывают txn, дальше `fetch_card` (timeout 60 с) + CDN-скачивание (30 с). `await db.commit()` ПЕРЕД вызовом донора этого не лечит — риск `idle in transaction` живёт внутри донора (learnings, клин пула 2026-07-16).
4. **Домен `/ab-tests` не гейтится ролью**: `main.py` подключает роутер только с `Depends(get_current_user)`, внутри — `get_current_project` (членство). Ни `require_role`, ни page-гейта на бэке нет — страничный гейт только фронтовый (`PageGuard page="ab-tests"`). Следствия: (а) участник с ролью viewer может создать/запустить тест, меняющий живое главное фото карточки WB; (б) мост из другого модуля не даёт эскалации прав (создать тест и так может любой член проекта), но может привести пользователя без страницы `ab-tests` в PageGuard-тупик. Та же напряжённость, что в [[design-allprojects-rbac-tension]].

**Why:** ловушки 1–3 всплыли на T3-ревью Ф6 «Дизайн карточек» (мост `services/design/ab_bridge.py`); 4 — найдена рядом, вне диффа, не эскалирована.

**How to apply:** ревьюя любой новый код поверх `ab_photo_tests` — сразу проверять маппинг `WbContentError`, полноту отката (контроль + soft_delete теста) и не верить комментарию «коммитим перед внешним HTTP». Родственная запись про донорские копипасты — [[sweep-job-donor-trap]].
