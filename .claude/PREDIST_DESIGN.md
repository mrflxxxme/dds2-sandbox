# Фича: «Предраспределение машины в пути» — рабочая спецификация

> Дизайн проработан мультиагентным анализом + адверсариальной критикой (2026-06-30).
> Это ФИНАЛЬНЫЙ спек с учётом решений пользователя. Anchors — на live `dev`/worktree.

## Идея
Машина в пути (`CostOrder`, статус CUSTOMS/DISPATCHED) везёт товар, который ещё не на ФФ.
До прибытия — раскладываем её входящий товар по WB-складам **без приёмки** (создаём заявки
на сборку со спец-статусом), а при разгрузке машины заявки **автоматически** становятся
обычными (резерв → реальный сток). Главный принцип — **НИКАКОГО фейкового стока**: не пишем
`WarehouseStock` до физприёмки; предраспределение — это статус+флаг+ссылка на машину.

## Решения пользователя (зафиксированы)
1. **Приоритет** — MVP **вручную** (открыл машину → раздал → заявки `PRE_DISTRIBUTED`). Авто-инъекция приоритетного источника в «Заполнить черновик» — Phase 2.
2. **Машины** — только **CUSTOMS + DISPATCHED** (не SHIPPED).
3. **Недопоставка** при разгрузке — **авто-перевод** `PRE_DISTRIBUTED→IN_PROGRESS`, дефицит ловится позже на гейте отгрузки (никакого фейк-стока).
4. **Распределение** — **детерминированный движок** (кратность короба/паллеты, локализация-75%; переиспользуем `regularShipmentAlloc`/`buildDraftRows`). **ИИ-советник (Опус 4.8)** — отдельная фаза позже, НЕ для самой раскладки (хард-констрейнты целых коробов LLM делает плохо).
5. **Модалка** — два списка: **«К отправке на WB»** (станет заявками) + **«На хранение (остаётся на ФФ)»** (излишек машины сверх потребности).
6. **Потребность** — предраспр. **уменьшает** need с пометкой «в пути» (через `transit_target_map`).

## Жизненный цикл
```
CostOrder (cost_orders.status):  FORMING → SHIPPED → CUSTOMS → DISPATCHED → DELIVERED
                                                       └ можно предраспределять ┘
AssemblyRequest:  (create без приёмки) → PRE_DISTRIBUTED
                  └─ accept_receipt (разгрузка машины) ─→ IN_PROGRESS (авто, сток реальный)
                     is_pre_distribution=True остаётся (бейдж/отчёты)
                  → READY → VEHICLE_ASSIGNED → SHIPPED → DELIVERED (обычный поток)
```

## ✅ СДЕЛАНО — слой МОДЕЛИ (uncommitted `backend/models/assembly.py`, поверх fbb0d07)
- `AssemblyStatus.PRE_DISTRIBUTED` + transition `PRE_DISTRIBUTED: {IN_PROGRESS, CANCELLED}` (entry-статус).
- `AssemblyRequest.source_vehicle_id` (FK `cost_orders.id`, nullable, index `ix_assembly_requests_source_vehicle_id`) + `is_pre_distribution` (Boolean, default false). Импортнут `Boolean`.
- Колонки добавлены в dev DB ВРУЧНУЮ (ADD COLUMN IF NOT EXISTS, аддитивно). mypy чисто, ORM грузится.

## ⚠️ Миграция — ОТЛОЖЕНА на rebase
Worktree DB на `loan02_loan_entity_extend` (нет в этой ветке; head ветки = `mf01_migfull_shipment_orders`; ветка 70 behind origin/dev; миграции sequential). Формальный alembic-файл создать **при ребейзе на origin/dev** (chain от реального head после +70). Сейчас колонки в dev DB вручную (sync-prod их сотрёт — повторить ALTER при надобности):
```sql
ALTER TABLE assembly_requests ADD COLUMN IF NOT EXISTS source_vehicle_id integer;
ALTER TABLE assembly_requests ADD COLUMN IF NOT EXISTS is_pre_distribution boolean NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS ix_assembly_requests_source_vehicle_id ON assembly_requests(source_vehicle_id);
```
Миграция при ребейзе: add 2 columns + index + FK `cost_orders`. Статус = String(20) → БЕЗ pg-enum DDL.

## ✅ СДЕЛАНО — backend-СПИНА (uncommitted поверх fbb0d07, гейты зелёные 2026-06-30)
Гейты: **mypy** (services+models, 206 файлов) чисто · **pytest** 10 новых + 243 смежных зелёные.

### Schema (`backend/schemas/assembly.py`) ✅
- `PreDistRow` {barcode, wb_warehouse_name, qty, package_type} · `PreDistributionCreate` {vehicle_id, wb_fbo_supply_id?, rows[]} · `PreDistVehicle` (агрегаты+can_distribute/block_reason) · `PreDistPoolRow` (gross/distributed/available) · `PreDistVehiclePool` · `PreDistributionCreateResult`.
- `AssemblyRequestResponse` расширен: `source_vehicle_id`, `is_pre_distribution`, `source_vehicle_order_no` (для бейджа; enrich в create-result, в списке/деталке пока None — добить в Router-фазе).

### Service `backend/services/assembly/pre_distribution.py` (новый) ✅
- `get_vehicle_pre_dist_pool` / `get_pre_distribution_vehicles` — net-математика (gross Σ`cost_order_items.qty` по order_no − уже разнесённое не-CANCELLED с `source_vehicle_id`). M3 хард-блок: target_warehouse_id NULL/не-ФФ → ValueError (+ block_reason в списке).
- `create_pre_distribution` — группировка строк по (WB-склад, упаковка)→1 заявка/группа; fail-fast валидации (ШК в номенклатуре, over-commit guard H3, FBO только при одном WB-складе); зовёт **параметризованный** `create_assembly_request`. Перечитывает результат через `get_assembly_request` (selectinload) перед `_build_response` — иначе MissingGreenlet на свежем объекте.
- Параметризация `create_assembly_request` (`crud.py:687`, НЕ форк): `skip_stock_validation` (пропуск `_validate_available_for_assembly` @`:792`), `status_override` (→PRE_DISTRIBUTED), `source_vehicle_id`, `is_pre_distribution`. FF-type check (@`:699`) и `_next_number` (@`:735`) сохранены; pallets/weight=0 (C2). В create НЕТ notify-хука → ФФ не уведомляется (как и хотели).
- `_advance_pre_distribution_assemblies` (НЕ коммитит, идемпотентно) + `advance_pre_distribution_manual` (H2, коммитит+инвалидирует).
- **Хук разгрузки** `accept_receipt` (`warehouse_inbound.py:343-350`): `_advance_...` по `receipt.cost_order_id` в той же транзакции ПЕРЕД commit, инвалидация после (H1 — не по DISPATCHED→DELIVERED). Экспорт в `__init__.py` + `assembly_service.py`.

### Потребность fold-in (`warehouse_need_service.py`) — критика C1 ✅
- PRE_DISTRIBUTED добавлен в `target_alloc_result` (статус-фильтр @`:633`) и роутится в `transit_target_map` (@`:644`, `is_transit`). НЕ в `active_statuses`/`in_assembly_per_wh` → нет фейк-резерва ФФ-стока. Локализация (шаг 4.5 @`:704,713-716`) уже фолдит transit симметрично с assembly — корректно в обоих режимах.
- `_get_reserved_map` + `_batch` (`warehouse_stock_engine.py`) — добавлен guard-коммент (PRE_DISTRIBUTED намеренно исключён). Тест `test_predist_does_not_reserve_ff_stock`.

### Tests `tests/test_pre_distribution.py` (10) ✅
pool net-math · группировка+skip-stock+C2 · C1 no-fake-reserve (+ резерв после advance) · over-commit H3 · FBO-single-WB · M3 block · H1 unload-advance (accept_receipt+идемпотентность) · H2 manual · список-агрегаты · project-изоляция.

## ✅ ROUTER + FRONTEND ГОТОВЫ (UNCOMMITTED, гейты зелёные)
### Router (`backend/routers/assembly.py`) ✅
4 ручки `/pre-distribution` (vehicles · vehicles/{id}/pool · POST create · vehicles/{id}/advance), оба write — `rate_limit_write`; объявлены ДО `/{request_id}`. List-эндпоинт обогащает `source_vehicle_order_no` (батч по source_vehicle_id) для бейджа. mypy 0, 5 API-тестов (`tests/test_api_pre_distribution.py`).

### Frontend ✅ — АВТО-движок раскладки (как «Потребность по складам», источник=пул машины)
Юзер уточнил вживую: раскладывать **остатки именно этой машины** как need-view (потребность WB · приёмка · кратность короба/паллеты), а не вручную. Реализовано:
- **Новый pure-helper `lib/assembly/preDistribution.ts`** (9 vitest зелёных): `buildPoolSkus` (пул barcode→nm_id, потребность из `getStockNeed.warehouses[].articles[nm].need`, **ffStock={target_warehouse_id: pool.available}** — источник=машина), `applyAcceptanceSplits` (closed→open + тип упаковки), `finalizePoolRows` (`buildDraftRows`→`roundDraftRowsToWholeBoxes`→`normalizeDraft` целые паллеты), `rowsToPreDistRows` (свёртка в PreDistRow[]).
- **`PreDistributionView.tsx` переписан**: грузит `getBoxMultiplicity`(nmPpb/nmBoxSize)+`getPalletBoxesBySize`+`getStockNeed`; модалка авто-считает раскладку (приёмка через `checkWbAcceptance`) → матрица SKU×WB-склад (целые коробы/паллеты) + сводка «к отправке N шт / M заявок» + «на хранение» + нота приёмки; submit → `createPreDistribution`. types/api.ts + lib/api/warehouse.ts (4 метода) + бейдж + supply-chain кнопка — ранее.
- **Σtgt ≤ pool** гарантирован движком (ffStock=pool cap) + бэк over-commit guard. `wb_warehouse_name` = канон-имя из getStockNeed → need fold-in (C1) матчит корректно.
- Гейты: **tsc 0**, **9 vitest**. ⚠Гонять одноразовым контейнером (tsc/vitest OOM-роняют next dev в общем контейнере).
- ⚠Поведение: <1 целой паллеты → остаётся на ФФ (строгие паллеты, как в need-view). Возможный тюнинг: тоггл «целые коробы без паллет» если юзер захочет больше отгружать. Мульти-баркод-на-nm (размеры) — need делится по nm_id, не по barcode (известный edge, товар не теряется, кап пулом держит).

### Tests — ДОБИТЬ (спина уже покрыта `tests/test_pre_distribution.py`, 10 шт)
- ✅ done: pool net-math, create без стока, C1 no-fake-reserve, over-commit H3, M3, H1 unload-advance+идемпотентность, H2, список, изоляция.
- ⬜ остаётся: **полный need-engine тест** (PRE_DISTRIBUTED уменьшает raw_need целевого WB-склада РОВНО раз — через `get_warehouse_need`, не только reserved-map); WbFboSupply attach happy-path (один WB-склад) + сценарий недопоставки (advance при actual<expected — accept_receipt всё равно переводит); API/router тесты.

## Критика — обязательные фиксы (учтены выше)
- **C1** двойной счёт: два агрегата в потребности (`assembly_target_map` по WB vs `in_assembly_per_wh` по ФФ) — PRE_DISTRIBUTED только в transit, не в общий active_statuses.
- **C2** pallets_count/pallet_weight_kg NOT NULL → дефолт 0 при создании.
- **C3** anchors: create = `crud.py:854`; gate = `_validate_available_for_assembly` @`:962`; FF-type check @`:866` СОХРАНИТЬ. Параметризовать, не форкать.
- **H1** хук advance по `receipt.cost_order_id`, не по DISPATCHED→DELIVERED.
- **H2** добавить ручную кнопку «перевести в сборку» (manual PRE_DISTRIBUTED→IN_PROGRESS) на случай если авто-хук не сработал.
- **H3** над-коммит WB-склада: валидировать Σзаявок(WB,nm) ≤ raw_need.
- **M3** `target_warehouse_id` nullable → хард-блок если NULL/не-FULFILLMENT.

## Anchors (быстрый справочник)
| Что | Файл:строка |
|---|---|
| Статус+переход | backend/models/assembly.py ✅ |
| Машина (CostOrder) | backend/models/cost.py:196-227; items join cost_order_items.order_no (cost.py:200,234); target_warehouse_id nullable :223; status enum enums.py:49 (CUSTOMS/DISPATCHED/DELIVERED) |
| create (параметризован ✅) | backend/services/assembly/crud.py:687; gate `_validate_available_for_assembly` :792; FF-check :699; `_next_number` :735; status=effective_status |
| Хук разгрузки ✅ | backend/services/warehouse_inbound.py:343-350 (advance по cost_order_id, vehicle DELIVERED :318); инвалидация :360-365 |
| Потребность WB-target ✅ | backend/services/warehouse_need_service.py:620-647 (фильтр :633; transit-роут :644; subtract :673; active_statuses :551; localization 4.5 :704,713) |
| reserved map ✅ | backend/services/warehouse_stock_engine.py:164-188 (guard-коммент) |
| Спина-сервис ✅ | backend/services/assembly/pre_distribution.py |
| UI таб | …/warehouse/assembly/distribute/page.tsx:30-38 |
| UI supply-chain | …/supply-chain/page.tsx:2161-2178 |
