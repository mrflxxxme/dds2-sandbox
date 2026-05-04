# Vehicle Qty Drift Confirm — Requirements

## Контекст
Сейчас `PATCH /api/v1/supply-chain/vehicles/{order_no}/items/{id}` ([backend/services/supply_chain/vehicle_delivery.py:511](backend/services/supply_chain/vehicle_delivery.py:511)) меняет `CostOrderItem.qty` без двух вещей:
1. Не валидирует превышение `FactoryOrderItem.qty - FactoryOrderItem.assigned_qty`
2. Не обновляет `FactoryOrderItem.assigned_qty`

Намеренный комментарий в коде («Intentionally does NOT adjust FactoryOrderItem fields — these are facts about this vehicle only») задумывался для упаковочной разницы ±1-2 шт, но не предусматривает upper bound.

Прецедент: машина `16.04`, барод `2049448537820`, qty в машине поднят `24 → 32`. Фабричный заказ #16 не вырос, `assigned_qty` остался прежним. 8 шт «потеряны» для аналитики shipment_matrix, `refresh_factory_order_statuses` не вызвался.

## User stories

**US-1.** Как оператор поставок, я хочу при увеличении qty в позиции машины сверх фабричного плана получать **inline-подтверждение в той же ячейке** (не модалку), чтобы не терять контекст таблицы из 20+ позиций.

**US-2.** Как оператор, я хочу видеть выбор: «расширить план фабрики» или «откатить ввод» — чтобы случайная опечатка (320 вместо 32) не раздула план.

**US-3.** Как оператор, я хочу видеть **постоянную метку (оранжевая точка)** на ячейках, где уже сейчас в БД есть рассинхрон — чтобы вычистить старые «потерянные» штуки (как 8 в машине 16.04) одним кликом без отдельной reconcile-страницы.

**US-4.** Как оператор, я хочу, чтобы при наличии нерешённого расхождения (pending drift) кнопка «Изменить статус машины» была **заблокирована** + красный badge в шапке — чтобы машина не уехала с расхождением план/факт.

**US-5.** Как руководитель, я хочу, чтобы каждое расширение плана попадало в `FactoryOrderHistory` с автором и причиной — чтобы понимать почему план фабрики вырос (опечатка vs реальный факт).

**US-6.** Как оператор, при **уменьшении** qty (32 → 20) я хочу прямой PATCH без диалога с автоматическим уменьшением `assigned_qty` — расхождение в эту сторону не требует подтверждения.

## Success criteria (измеримые)

- [ ] PATCH с превышением плана и `mode=strict` (default) возвращает HTTP **422** с полным `detail` payload (см. design.md API contract)
- [ ] PATCH с `mode=extend_plan` поднимает `FactoryOrderItem.qty` на `delta - available`, синхронизирует `assigned_qty`, пишет `FactoryOrderHistory` запись `event_type=qty_extended_from_vehicle`
- [ ] PATCH с уменьшением qty синхронно уменьшает `FactoryOrderItem.assigned_qty` (никаких диалогов)
- [ ] PATCH с `delta == 0` — no-op (без `recalculate_order_items`, без `_invalidate_supplier_catalog`)
- [ ] `_enrich_vehicle` возвращает `qty_drift: int | None` в каждом `VehicleItemSchema` для visual cue существующих расхождений
- [ ] Единая функция `_adjust_assigned_qty` в `factory_orders.py` используется в 4 точках: `add_items_to_vehicle`, `update_vehicle_item`, `remove_item_from_vehicle`, `split_to_vehicles`
- [ ] Frontend: при HTTP 422 `exceeds_factory_qty` под строкой раскрывается DriftConfirmRow с кнопками «Расширить» / «Откатить»
- [ ] Frontend: ячейки с `qty_drift > 0` показывают оранжевую точку, click → тот же DriftConfirmRow
- [ ] Frontend: pending drift в любой строке → disable «Изменить статус» + красный badge «Несохранённые расхождения: N»
- [ ] Backend pytest: ≥9 кейсов покрывающих все ветки `_adjust_assigned_qty` (strict pass, strict fail, extend_plan with available=0, extend_plan with partial available, decrement, no-op, mix-group, idempotency, history записывается)
- [ ] Frontend vitest: парсинг 422 detail в `updateVehicleItem`, корректное возвращение типизированной ошибки `FactoryQtyExceededError`
- [ ] 0 regression в текущих 1184 backend + 200+ frontend тестах
- [ ] `bash scripts/check_conventions.sh` проходит

## Out of scope (не делать в этой итерации)

- Reconcile UI на странице **фабричного заказа** (offline batch reconciliation расхождений за пределами текущей машины)
- Разделение полей `planned_qty` / `actual_qty` (вариант E из обсуждения)
- Отдельная сущность `FactoryOrderAdjustment` (вариант F)
- Permission/role для `extend_plan` (любой пользователь, который умеет PATCH машины)
- Notification в Telegram о расширении плана
- Расширение mix-group целиком при превышении (только текущий FOI; см. design.md §Mix-group)
- E2E playwright (можно добавить отдельным PR если нужно)

## Constraints

**Iron rules DDS2** ([CLAUDE.md](CLAUDE.md#железные-правила)):
- Multi-tenancy: каждый запрос фильтрует `project_id` + `is_deleted == False`
- `soft_delete()` вместо `db.delete()` для `SoftDeleteMixin` моделей
- `from backend.utils.time import utcnow` (не `datetime.utcnow()`)
- Параметризованный `:param` SQL
- `invalidate_cache(prefix)` после мутаций
- Бизнес-логика в `services/`, роутер только HTTP
- Write endpoints под `Depends(rate_limit_write)`

**Backward compatibility:**
- PATCH без поля `mode` = `mode=strict` (default)
- Старые клиенты получат 422 при превышении вместо тихого рассинхрона — **это исправление бага, не breaking change**
- Поведение при `delta <= available` и `delta < 0` остаётся как сейчас + добавляется sync `assigned_qty`

**Domain constraints:**
- Mix-group: расширять только текущий FOI, не группу. Frontend показывает плашку «Позиция в mix-группе. Расширение коснётся только этого баркода»
- Большая дельта `>1000`: красный warning «Большая дельта: +X. Проверьте ввод» (sanity check, не блокирует)
- Pending drift не блокирует другие изменения в машине, только переход статуса

**Технические:**
- Не трогать `services/cost/items.py recalculate_order_items` — он остаётся as-is
- `_adjust_assigned_qty` — pure utility, не выполняет commit (вызывающий контролирует транзакцию)
- `qty_drift` считается лениво в `_enrich_vehicle`, не хранится в БД

## Связь с предыдущими фиксами (контекст)

- **sc15 (vehicle_name/plate_number rename)**: `update_vehicle_item` намеренно не трогает FOI — этот контракт мы аккуратно расширяем (явный `mode=strict` для validation, но default-поведение для `delta <= available` всё ещё синхронизирует assigned_qty, что **меняет** sc15-инвариант — см. design.md §Backward compat impact)
- **2026-04-15 фикс `refresh_factory_order_statuses`**: единая точка обновления статуса заказа — мы её уже зовём из `update_vehicle_item` после изменения `assigned_qty`
- **price-resync (2026-04-17)**: уже работает в любом статусе с предупреждением — наш drift-confirm параллельный механизм для qty
