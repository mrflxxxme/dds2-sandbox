# Vehicle Qty Drift Confirm — Tasks

## Phase 1: Foundation (lead, sequential)

- [ ] **1.1** Добавить `_adjust_assigned_qty`, `FactoryQtyExceeded`, `AdjustResult` в [backend/services/supply_chain/factory_orders.py](backend/services/supply_chain/factory_orders.py)
  - Pure utility, не делает commit, не вызывает refresh_factory_order_statuses
  - При extend_plan пишет `FactoryOrderHistory event_type="qty_extended_from_vehicle"` (db.add + flush для получения id, без commit)
  - Подробности — design.md §«Единая utility _adjust_assigned_qty»
- [ ] **1.2** Расширить схемы в [backend/schemas/supply_chain.py](backend/schemas/supply_chain.py):
  - `VehicleItemUpdate.mode: Literal["strict", "extend_plan"] = "strict"`
  - `VehicleItemSchema.qty_drift: int | None = None`
  - `VehicleItemSchema.fo_qty: int | None = None`
  - `VehicleItemSchema.fo_assigned: int | None = None`
  - `VehicleItemSchema.fo_number: str | None = None` (для existing-drift поповера на фронте)
- [ ] **1.3** Smoke-import: `docker compose exec backend python -c "from backend.schemas.supply_chain import VehicleItemUpdate, VehicleItemSchema; from backend.services.supply_chain.factory_orders import _adjust_assigned_qty, FactoryQtyExceeded; print('ok')"`

**Lead делает сам, без worktree.** Это фундамент — backend/frontend агенты обоих ждут эти типы.

---

## Phase 2: Parallel (2 teammates, isolation: worktree, opus 4.7, run_in_background: true)

### Backend teammate (worktree A)

**Файлы (его зона):** `backend/services/supply_chain/vehicle_delivery.py`, `backend/routers/supply_chain.py`, `tests/test_supply_chain_vehicles.py` (или новый `tests/test_vehicle_qty_drift.py`)

- [ ] **2A.1** `update_vehicle_item` в [vehicle_delivery.py:511](backend/services/supply_chain/vehicle_delivery.py:511):
  - Добавить параметры `mode: Literal["strict", "extend_plan"] = "strict"`, `user_name: str | None = None`
  - При `qty_changed and cost_item.factory_order_item_id`: загрузить `fo_item`, посчитать `delta`, вызвать `_adjust_assigned_qty(db, fo_item, delta, mode, user_name, cost_order_no=order_no)` (импорт из factory_orders.py — внимание на циклы, делать lazy import как сейчас в проекте)
  - При `delta == 0` и нет override-флагов: ранний return `{"ok": True, "noop": True}` без commit/recalculate
  - После commit: `refresh_factory_order_statuses(db, project_id, affected_fo_ids)` если `affected_fo_ids` непустое
  - Возвращать `{"ok": True, "item_id": id, "extended_by": adjust_result.extended_by}`
- [ ] **2A.2** `add_items_to_vehicle`, `remove_item_from_vehicle`, `clear_all_vehicle_items`, `delete_vehicle` — переписать на `_adjust_assigned_qty` (см. таблицу в design.md). Для `add_items_to_vehicle` ловить `FactoryQtyExceeded` и пробрасывать как `ValueError(...)` — сохраняем 400 + текст для существующего UI добавления (структурный 422 нужен только для PATCH)
- [ ] **2A.3** `_enrich_vehicle`:
  - Расширить SELECT в `fo_batch_result` колонками `FactoryOrderItem.qty`, `FactoryOrderItem.assigned_qty`, `FactoryOrder.id` уже есть — убедиться что `FactoryOrder.order_number` уже идёт (да, идёт)
  - Добавить batch query `sum_by_foi` (см. design.md)
  - Для каждого `cost_item`: посчитать `qty_drift` по формуле `max(0, cost_item.qty - max(0, fo_qty - (sum_total - cost_item.qty)))`
  - Передать `qty_drift`, `fo_qty`, `fo_assigned`, `fo_number` в `VehicleItemSchema`
- [ ] **2A.4** Роутер [supply_chain.py:469](backend/routers/supply_chain.py:469):
  - Импорт `from backend.services.supply_chain.factory_orders import FactoryQtyExceeded`
  - В `update_vehicle_item` добавить `user: User = Depends(get_current_user)`
  - try/except: `FactoryQtyExceeded` → `HTTPException(422, detail=e.detail)` (raise from e)
  - Передать `mode=payload.mode, user_name=display_name`
- [ ] **2A.5** Тесты в `tests/` — 12 кейсов из design.md §Тесты Backend
  - Использовать существующие фикстуры `db_session`, `project`, `factory_order_with_items`, `vehicle_with_items`
  - Если фикстур нет — создать минимальные (см. `tests/test_supply_chain_*.py` для паттерна)
- [ ] **2A.6** Запустить `docker compose exec backend pytest tests/test_supply_chain_vehicles.py tests/test_vehicle_qty_drift.py -x --tb=short` (после копирования файлов)
- [ ] **2A.7** `bash scripts/check_conventions.sh`
- [ ] **2A.8** Перед завершением: `git status` + `git diff --stat`, отчёт lead'у

**Constraints для teammate (обязательно в промпте):**
1. Все пути относительные, `./` или `<worktree>/...`. НИКАКИХ `/Users/a1/Desktop/dds_app/...` — иначе add/add конфликт при merge (см. learnings.md, прецедент agent-a9ac9883)
2. Не трогать frontend файлы — это зона B
3. Не трогать `factory_orders.py` (туда уже всё положил lead в Phase 1) — только imports
4. Не трогать `schemas/supply_chain.py` — то же
5. Pre-commit падает 2 раза подряд на одном файле → fail-fast, отчёт, НЕ крутить цикл
6. Task budget 80k токенов (тестов много); при превышении без результата → fail-fast
7. `recalculate_order_items`, `_invalidate_supplier_catalog`, `refresh_factory_order_statuses` — оставить вызовы как сейчас, не оптимизировать порядок
8. `# noqa: E712` на `is_deleted == False` — если ruff падает, добавить рядом

### Frontend teammate (worktree B)

**Файлы (его зона):** `frontend-react/src/types/api.ts`, `frontend-react/src/lib/api/supply-chain.ts`, `frontend-react/src/lib/api/supply-chain.test.ts`, `frontend-react/src/app/(main)/p/[slug]/supply-chain/vehicles/[order_no]/page.tsx`, `frontend-react/src/app/(main)/p/[slug]/supply-chain/i18n.tsx`

- [ ] **2B.1** Типы в [types/api.ts](frontend-react/src/types/api.ts):
  - `VehicleItemUpdate.mode?: "strict" | "extend_plan"`
  - `VehicleItem.qty_drift?: number | null`, `fo_qty?: number | null`, `fo_assigned?: number | null`, `fo_number?: string | null`
  - `FactoryQtyExceededDetail` interface (8 полей, см. design.md)
  - `FactoryQtyExceededError` class extends Error
- [ ] **2B.2** [lib/api/supply-chain.ts](frontend-react/src/lib/api/supply-chain.ts) `updateVehicleItem`:
  - Принимает `payload: VehicleItemUpdate`
  - Ловит 422 с `detail.error === "exceeds_factory_qty"` → throw `FactoryQtyExceededError(detail)`
  - Иначе пробрасывает дальше
  - **Важно:** проверить как `client.ts` пробрасывает 422 (response.data vs error.detail) — адаптировать
- [ ] **2B.3** Vitest [lib/api/supply-chain.test.ts](frontend-react/src/lib/api/supply-chain.test.ts):
  - 3 кейса (parses_422, passes_through_500, success_typed) — см. design.md
  - Mock fetch / client с jest.fn / vi.fn соответствующим паттерну в проекте
- [ ] **2B.4** Страница машины [vehicles/[order_no]/page.tsx](frontend-react/src/app/\(main\)/p/[slug]/supply-chain/vehicles/[order_no]/page.tsx):
  - State `driftStateById: Record<number, FactoryQtyExceededDetail & {oldQty, newQty}>`
  - В `commitQty` (или эквиваленте onBlur input qty) — try/catch FactoryQtyExceededError, ставить в driftStateById
  - Render `<DriftConfirmRow>` под строкой когда `driftStateById[item.id]` непустой
  - Постоянная оранжевая точка в ячейке qty когда `item.qty_drift > 0` (даже без pending), click открывает поповер с теми же данными из `item.fo_qty/fo_assigned/fo_number`
  - В шапке — `<DriftBadge count={Object.keys(driftStateById).length} />` если >0
  - Кнопка «Изменить статус» disabled если `pendingDriftCount > 0` + tooltip
  - `onExtend`: PATCH mode=extend_plan → toast → удалить из driftStateById → refetch
  - `onRevert`: localQty = oldQty, удалить из driftStateById, без сети
- [ ] **2B.5** [i18n.tsx](frontend-react/src/app/\(main\)/p/[slug]/supply-chain/i18n.tsx) — RU/ZH:
  - `drift_warning_title`, `drift_extend_button`, `drift_revert_button`, `drift_mix_group_note`, `drift_large_delta_warning`, `drift_pending_badge`, `drift_status_blocked_tooltip`, `drift_existing_dot_tooltip`, `drift_extend_toast`
- [ ] **2B.6** Запустить `cd frontend-react && npx vitest run src/lib/api/supply-chain.test.ts`
- [ ] **2B.7** Smoke страницы: `cd frontend-react && npm run build` — проверить что нет TS errors
- [ ] **2B.8** Перед завершением: `git status` + `git diff --stat`, отчёт

**Constraints для teammate:**
1. Все пути относительные
2. Не трогать backend
3. Types-first: сначала types/api.ts → потом api клиент → потом страница (иначе TS красным)
4. Не трогать `client.ts` (общий) — если 422 неправильно пробрасывается, договориться с lead и ждать
5. Toast — использовать существующий `import { toast } from '@/components/Toast'` (или путь из проекта)
6. Pre-commit падает 2 раза → fail-fast
7. Task budget 60k токенов
8. **НЕ запускать playwright** — не входит в этот PR
9. Если на странице сейчас inline-edit qty не реализован inline (а через FormModal) — адаптировать решение под существующий UX, обсудить с lead перед глубокой переработкой

---

## Phase 3: Verify (lead, sequential, без агентов)

- [ ] **3.1** Merge worktrees: backend → main, frontend → main (sequential, разные файлы — без конфликтов ожидаемо)
- [ ] **3.2** `docker compose exec backend pytest tests/ -x --tb=short` — все 1184+ тестов
- [ ] **3.3** `cd frontend-react && npx vitest run` — все frontend тесты
- [ ] **3.4** `bash scripts/check_conventions.sh`
- [ ] **3.5** Smoke вручную через preview_start → открыть машину 16.04 → попробовать ввести qty с превышением → убедиться что DriftConfirmRow появляется → клик «Расширить» → проверить что план фабричного заказа вырос (через `GET /factory-orders/{id}` или UI)
- [ ] **3.6** Обновить [backend/DOMAIN_SUPPLY_CHAIN.md](backend/DOMAIN_SUPPLY_CHAIN.md) — секция «Редактирование позиций машины»: добавить про `mode=strict|extend_plan`, drift confirm, qty_drift в response

---

## Phase 4: Review (3 параллельных subagents in the same turn, opus 4.7)

- [ ] **4.1** `code-reviewer` — диф ≥3 файлов backend + ≥3 файлов frontend, проверить iron rules
- [ ] **4.2** `api-designer` — изменён router (новый mode параметр + 422 structured detail) — OpenAPI consistency, breaking compat
- [ ] **4.3** `database-reviewer` — нет миграций, но есть batch query в `_enrich_vehicle` — проверить что не N+1 и не >100 параметров в IN

> **Не запускать** `security-reviewer` — нет изменений в auth/SQL/crypto/user-input. Перед запуском любого блокирующего фикса от security-reviewer обязательно читать `memory/feedback_register_enabled_prod.md` (см. learnings.md прецедент 2026-04-21).
>
> **Не запускать** `performance-optimizer` — добавляется один batch query `sum_by_foi` (один SELECT с GROUP BY), фронт без новых fetch'ей в hot path.

---

## Phase 5: Commit & Deploy

- [ ] **5.1** Запросить approval юзера на commit
- [ ] **5.2** Один коммит: `feat(supply-chain): drift-confirm для qty в позициях машины (sc17)`
- [ ] **5.3** Push в `dev` → auto-PR → claude-review → green CI → auto-merge → cd-production → healthcheck
- [ ] **5.4** Smoke на проде: открыть `https://app.vyatkin-wb.ru/p/default/supply-chain/vehicles/16.04` → убедиться что на позиции `2049448537820` (барод с реальным drift +8) видна оранжевая точка → клик → DriftConfirmRow → «Расширить» → проверить что фабричный план вырос
- [ ] **5.5** `/learn` (через post-commit hook автоматически) — фиксация `learnings.md`:
  - Паттерн «mode=strict|extend_plan для silent-sync операций»
  - Паттерн «единая `_adjust_*` utility для одинаковой логики в N точках сервиса»
  - Урок про sc15-инвариант (intentional silent → drift) — добавить в KNOWN_PITFALLS если уместно

---

## Риски и mitigations

| Риск | Mitigation |
|---|---|
| `client.ts` не пробрасывает structured detail из 422 | Phase 2B.2 — frontend агент проверяет первым делом, если не пробрасывает — лидер в Phase 1.5 правит client.ts |
| Strict-режим default ломает пайплайн где-то в проде где было «тихое превышение» | Это исправление бага, см. backward-compat в design.md. Возможные жертвы — только `update_vehicle_item`, в роутере 422 ловится grateful'но. Старые скрипты не используют этот endpoint программно. |
| `_enrich_vehicle` стал тяжелее (sum_by_foi query) | Один SELECT GROUP BY на batch foi_ids — O(n) по permanent items в машине. На 20-50 позициях машины — пренебрежимо. |
| Mix-group: extend_plan на одной FOI оставит группу несогласованной (mix_box_size остаётся прежний, но qty одного выросло) | Out-of-scope явно (см. design.md). Пользователь увидит в UI плашку «Только этот баркод» — осознанное действие. |
| Frontend inline-edit qty может быть реализован через FormModal (не inline) | Constraint 9 для teammate B — адаптировать под существующий UX, не делать большой UX рефакторинг |

---

## Definition of Done

1. ✅ Все 12 backend тестов зелёные
2. ✅ Все 3 frontend vitest тесты зелёные
3. ✅ `bash scripts/check_conventions.sh` зелёный
4. ✅ `docker compose exec backend pytest tests/ -x` 0 regression
5. ✅ Smoke на проде: оранжевая точка на drift-позициях, click → поповер, «Расширить» → план растёт, history запись появилась
6. ✅ DOMAIN_SUPPLY_CHAIN.md обновлён
7. ✅ 1 коммит в `dev`, auto-flow до production
8. ✅ `/learn` зафиксировал паттерны
