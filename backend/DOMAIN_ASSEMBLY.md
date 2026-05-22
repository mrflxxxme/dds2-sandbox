# DOMAIN_ASSEMBLY — Заявки на сборку + Лист логиста

Промежуточный слой между складом фулфилмента и отгрузкой FBO-поставок WB.
`WbFboSupply` (1:1) → `AssemblyRequest` → при SHIPPED создаёт `OutboundShipment`.

## Таблицы
| Модель | Назначение | Ключ / constraint |
|--------|------------|-------------------|
| `AssemblyRequest` | Заявка на сборку (статус, FBO-привязка, vehicle/pallet поля) | `models/assembly.py` |
| `AssemblyRequestItem` | Позиция заявки (barcode, qty) | FK на request |
| `AssemblyStatusHistory` | История смен статуса | FK на request |
| `AssemblyDraft` | Черновик распределения N×M (`distribution` JSONB), SoftDelete | `models/assembly.py` |
| `OutboundShipment` / `OutboundShipmentItem` | Отгрузка, создаётся при SHIPPED | `models/warehouse.py` |

`PackageType` = `BOX | MONOPALLET | SUPERSAFE`. Один `AssemblyRequest` = одна транспортная единица WB = один `package_type`; тип определяется через `POST /warehouse/acceptance-check` (см. `DOMAIN_WAREHOUSE.md`).

## Бизнес-правила
- **Статусная модель:** `IN_PROGRESS → READY → VEHICLE_ASSIGNED → SHIPPED`. Строго последовательная, пропуск запрещён. Допустимые откаты: `READY → IN_PROGRESS` (`start_assembly`, сбрасывает `actual_ready_date`), `VEHICLE_ASSIGNED → READY` (`unassign_vehicle`), `SHIPPED → READY` (cancel). `CANCELLED` — из любого статуса.
- **PENDING — legacy:** остаётся в enum для совместимости со `status_history`, но активных заявок в нём быть не должно. Новые заявки создаются сразу в `IN_PROGRESS`. `start_assembly` идемпотентен (повторный клик в IN_PROGRESS = no-op).
- **Валидация остатков** при create/update (PENDING/IN_PROGRESS) и `start_assembly`: `available = warehouse_stock.quantity − reserved_by_other_active_requests`, где reserved — сумма позиций по этому товару в других активных заявках (PENDING/IN_PROGRESS/READY/VEHICLE_ASSIGNED). Нехватка → `ValueError` с детализацией по баркодам. См. `_validate_available_for_assembly()` в `services/assembly/crud.py`.
- **При Ship (VEHICLE_ASSIGNED → SHIPPED):** валидация stock ≥ need; списание stock (`OUTBOUND`, `reference_type="ASSEMBLY"`); создаётся `OutboundShipment(SHIPPED)` + items; проставляются взаимные `outbound_shipment_id` на request и `WbFboSupply`; `shipped_at = utcnow()`.
- **При Cancel SHIPPED:** возврат stock (`reference_type="ASSEMBLY_CANCEL"`); `WbFboSupply.outbound_shipment_id = NULL`; `OutboundShipment.soft_delete()`; статус → READY.
- **Редактирование:** items и FBO-поставка — только до READY. Поля логистики (`pickup_cost`, `vehicle_*`, `driver_phone`, `pallets_count`, `pallet_weight_kg`) — в любом статусе кроме CANCELLED (inline edit).
- **AssemblyDraft (N×M):** черновик с экрана «Потребность по складам». Матрица `ФФ-источники × WB-целевые`, юзер балансирует Σ src ↔ Σ tgt по каждой строке. `commit_draft`: per-row валидация `Σ src == Σ tgt > 0` (иначе 400); pro-rata распределение в пары (largest-remainder для целочисленности); создаёт N `AssemblyRequest(IN_PROGRESS)` — по одной на уникальную пару `(source_ff, target_wb, package_type, is_newcomer)` с qty>0. Atomic: исключение → rollback всех созданных, draft остаётся; успех → soft-delete draft.
- **Объединение черновиков (`merge_drafts`, `POST /merge`):** когда несколько черновиков отправляют товар по одному маршруту `(ff → wb)`, они создадут раздельные отправки на ФФ. `merge_drafts` сливает список черновиков в один. Survivor = черновик с наибольшим числом строк (tie-break: наименьший id); остальные soft-delete. Строки объединяются по ключу `(nm_id, package_type)` — src/tgt суммируются поэлементно. `cold_start_shares` сбрасывается (юзер запускает авто-баланс заново). Блокировка: если у любого черновика есть `handed_units` — 400 «сначала верните в черновик». После merge → редирект на `distribute/preview?draft=<id>&pkg=BOX&type=all`.
- **Партиальный commit_draft по двум независимым осям:** `package_type` (BOX/MONOPALLET — короб/моно) и `newcomer_filter` (`newcomer`/`regular`/`all` — новинки/обычные). **Срез упаковки = вкладка UI, а не точный тип:** `package_type=BOX` («Короб») коммитит ВСЁ, кроме MONOPALLET — т.е. BOX И SUPERSAFE (каждая заявка сохраняет свой реальный тип); `MONOPALLET` — только моно (`_pkg_selected`). Иначе SUPERSAFE-строки молча оставались бы в черновике. Всё не выбранное остаётся в `rows` черновика для следующих сборок; если осталось что-то ИЛИ есть `handed_units` — draft не удаляется. Newcomer-множество (`fetch_newcomer_nm_ids`) считается по ВСЕМ строкам ДО фильтрации — оно нужно и для фильтра по новизне, и для группировки. Новинки создают отдельные `AssemblyRequest` с префиксом в `comment` (`NEWCOMER_COMMENT_PREFIX`).
- **Жизненный цикл заявки-юнита** (`distribute` → «передан на ФФ» → «в сборке»). «Заявка-юнит» = ключ `(source_ff × target_wb × package_type × is_newcomer)`. Состояния: **auto** (живёт в `rows`) → **draft** (ручная правка, `set_unit_items`, status=`draft`, ещё в `handed_units`) → **handed** (`hand_off_unit`, status=`handed`, заморожен) → **committed** (`AssemblyRequest`). Эндпоинты `units/*` (`assembly_draft_service`):
  - `hand_off_unit` — «передать на ФФ»: `_carve_unit_from_rows` вырезает поток ff→wb из `rows` в замороженный `HandedUnit` (status=`handed`). Снимок не трогается правками распределения (его уже нет в `rows`).
  - `revert_unit` — «вернуть в черновик»: вливает позиции `handed`-юнита обратно в `rows`.
  - `commit_unit` — «в сборку»: создаёт один `AssemblyRequest` из снимка (требует status=`handed`, иначе 400 «сначала передайте на ФФ»), удаляет юнит; пустой draft → soft-delete.
  - `set_unit_items` — редактор наполнения: заменяет позиции (фиксирует auto-юнит как `draft`); на `handed` — 400 «правка запрещена». Проверку свободного остатка ФФ делает фронт.
  - `delete_unit` — удалить юнит целиком (товар остаётся на ФФ); на `handed` — 400 «сначала верните в черновик».
  - `move_unit` — «сменить склад WB» (UI: кнопка на `distribute/request`): перенести юнит на другой WB-целевой склад черновика. Двигается ТОЛЬКО поток этого ФФ (`ff→wb`), баланс матрицы сохраняется. Источник — авто (вырезаем из rows) или ручной черновик; на складе-получателе сливается с существующим ручным черновиком по баркоду, иначе поток возвращается в rows как `ff→new_wb`. `handed` переносить нельзя (400); перенос на тот же склад — 400.
- **Связь заявка ↔ черновик (`source_draft_id`):** `commit_unit` и `commit_draft` проставляют `AssemblyRequest.source_draft_id = draft.id`. Страница склада-источника (`distribute/ff`) показывает блок «История — в сборке» — заявки этого черновика (`GET /warehouse/assembly?draft_id=&warehouse_id=`). NULL для заявок из других путей (FBO/ручные). Заявки, созданные до миграции `asm_source_draft_id`, в инлайне не появятся (только в полном списке `/warehouse/assembly`).
- **Предпросмотр созданных (`get_created_groups`, `GET /warehouse/assembly/created-groups`):** после полного коммита черновик soft-удаляется, но созданные заявки группируются по `source_draft_id` в read-only «Созданные партии» на странице `warehouse/assembly`. Только **IN_PROGRESS**-заявки (ещё в сборке, не ушли в работу/отгрузку), кап 50 групп, свежие сверху. Имя черновика резолвится даже после его soft-delete. Это якорь для будущего мёрджа созданных заявок (заявка↔заявка / черновик→заявка по дублирующимся маршрутам).

`distribution` JSONB: `source_warehouse_ids[]`, `target_warehouse_names[]`, `rows[]` (`nm_id`, `barcode`, `vendor_code`, `src{warehouse_id:qty}`, `tgt{wb_name:qty}`, `package_type`), `cold_start_shares{wb_name:доля}|null` (cold-start авто-баланс), `handed_units[]` (замороженные снимки: `source_ff_id`, `target_wb_name`, `package_type`, `is_newcomer`, `status`, `items[]`), `pallets_count`, `pallet_weight_kg`, `estimated_ready_date`.

## Зависимости
- `DOMAIN_WAREHOUSE` — `acceptance-check` определяет `package_type`; `_update_stock` / `_resolve_barcode` для движения остатков.
- `DOMAIN_REPORTS` — on-assembly заявки участвуют в Stock Forecast.
- WB API — данные FBO-поставок (`WbFboSupply`).

## Грабли
- При `commit_draft` строки группируются по `(source_ff, target_wb, package_type, is_newcomer)` — если для одного склада нужны и короб, и моно (или новинка + обычный), это разные заявки.
- **Carve сохраняет баланс строки:** `_carve_unit_from_rows` вычитает выделенный qty из `src` И из `tgt` на одну величину → остаток строки остаётся сбалансированным (`Σsrc == Σtgt`). При правке снимка не пересчитывать через `src/tgt` — `HandedUnit.items` уже плоский список qty.
- **SUPERSAFE и срез «Короб»:** `commit_draft?package_type=BOX` трактует «Короб» как не-MONOPALLET (`_pkg_selected`), поэтому SUPERSAFE-строки коммитятся вместе с BOX (исправлено — раньше они молча оставались в черновике). Каждая заявка сохраняет реальный `package_type`. ⚠️ Если WB заворачивает SUPERSAFE на приёмке иначе — это уже про acceptance, не про отбор.
- **Cross-type drag (distribute, фронт):** перетаскивание WB-колонки между вкладками Моно/Короб меняет `package_type` перенесённых строк — с подтверждением. Срабатывает ТОЛЬКО если цель однотипна (`onlyMono`/`onlyBox`); смешанная цель → обычный merge без смены типа. Колонки с нулём в текущем типе показывают вторичный счётчик (📦/📐) — подсказка куда тащить. Типы определяются по `allRows` (все строки, не только видимые во вкладке).
- Аналитика логистики (`GET /warehouse/assembly/shipments/analytics`) кэшируется 300s; `date_to` фильтрует как `shipped_at < date_to + 1 day` (включает весь день).

## Файлы
- `models/assembly.py` — ORM (Request, Item, StatusHistory, Draft).
- `schemas/assembly.py`, `schemas/assembly_draft.py` — Pydantic DTO.
- `services/assembly_service.py` — бизнес-логика + аналитика логистики + `get_created_groups` (предпросмотр созданных по `source_draft_id`).
- `services/assembly_draft_service.py` — CRUD черновика + `commit_draft` (партиальный по package_type/newcomer) + `merge_drafts` + жизненный цикл юнита (`hand_off_unit` / `revert_unit` / `commit_unit` / `set_unit_items` / `move_unit` / `delete_unit`, helpers `_carve_unit_from_rows` / `_create_one_request`).
- `services/assembly/crud.py` — `_validate_available_for_assembly`.
- `routers/assembly.py` — CRUD + workflow + analytics + `GET /warehouse/assembly/created-groups`.
- `routers/assembly_drafts.py` — `/api/v1/assembly/drafts` (list/get/create/update/delete; `POST /merge`; `POST /{id}/commit?package_type=&newcomer_filter=`; `POST /{id}/units/{hand-off,revert,commit,items,move,delete}`).
- Frontend: `warehouse/assembly/` (page / new / [id] / [id]/edit / merge / distribute / distribute/ff / distribute/preview / distribute/request) + `warehouse/logistics/page.tsx`. Хелпер предпросмотра/коробок — `src/lib/utils/assemblyPreview.ts` (коробки = `ceil(шт/K)`, разбивка «N кор + M шт»).
- ТЗ/UX: `docs/tz_assembly_logistics.md`, `docs/ux_plan_assembly_logistics.md`.
