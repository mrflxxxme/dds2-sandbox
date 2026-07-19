# DOMAIN_WAREHOUSE — Склад, остатки, приёмка, отгрузка, перемещения, FBO

Учёт складских операций: остатки, приёмка, отгрузка (только FULFILLMENT), перемещения, корректировки. Материализованный баланс (`WarehouseStock`) + полный audit trail (`StockMovement`). Синхронизация FBO-поставок с WB Marketplace API.

## Таблицы

### models/warehouse.py
| Модель | Назначение | Ключ / constraint |
|--------|------------|-------------------|
| `Warehouse` | Склад, `warehouse_type` EXTERNAL\|FULFILLMENT, `assembly_days` | SoftDelete |
| `InboundReceipt` | Приёмка, статус DRAFT\|EXPECTED\|ACCEPTED\|CANCELLED | SoftDelete |
| `InboundReceiptItem` | Позиции приёмки (`expected_qty`, `actual_qty`) | — |
| `OutboundShipment` | Отгрузка, статус DRAFT\|SHIPPED\|DELIVERED\|CANCELLED, `wb_supply_id` | SoftDelete |
| `OutboundShipmentItem` | Позиции отгрузки | — |
| `StockTransfer` | Перемещение, статус DRAFT\|IN_TRANSIT\|COMPLETED | SoftDelete |
| `StockTransferItem` | Позиции перемещения | — |
| `StockMovement` | Append-only audit log, `movement_type`, `reference_type/id` | БЕЗ SoftDelete |
| `WarehouseStock` | Материализованный баланс (`quantity` + `in_transit` + `cost_price`) | БЕЗ SoftDelete, UNIQUE(project_id, warehouse_id, nomenclature_id) |
| `StockAdjustment` | Корректировка, `delta` (+ излишек, − недостача) | БЕЗ SoftDelete |

### models/wb_fbo.py
| `WbFboSupply` | Поставка из WB API | UNIQUE(project_id, wb_supply_id) |
| `WbFboSupplyItem` | Позиции (заказы) в поставке | — |

### models/wb_returns.py
| `WbGoodsReturn` | Возвраты на ПВЗ, линк на `InboundReceipt` | SoftDelete + Timestamp, UNIQUE(project_id, srid) |

### Box multiplicity (models/cost.py)
| `Nomenclature.box_qty_override: int\|None` | SKU-уровневый ручной дефолт кратности коробки |
| `Nomenclature.use_box_multiplicity: bool` | Per-SKU toggle: учитывать ли кратность при распределении |
| `BoxQtyPerWarehouse` | Ручная per-ФФ кратность/размер (`project_id`, `barcode`, `warehouse_id`, `box_qty`, `box_size`, `use_box_multiplicity`) | UNIQUE `uq_box_qty_pw_project_bc_wh` |
| `BoxMultiplicityChangeLog` | Журнал изменений кратности/размера (`project_id`, `barcode`, `warehouse_id` NULL=SKU-уровень, `field`, `old_value`, `new_value`, `change_source`, `created_at`) | для «Истории изменений» и отката |

### Enums
- `WarehouseType` — EXTERNAL, FULFILLMENT
- `ReceiptStatus` — DRAFT, EXPECTED, ACCEPTED, CANCELLED
- `OutboundStatus` — DRAFT, SHIPPED, DELIVERED, CANCELLED
- `TransferStatus` — DRAFT, IN_TRANSIT, COMPLETED
- `MovementType` — INBOUND, INBOUND_CANCEL, OUTBOUND, OUTBOUND_CANCEL, TRANSFER_IN, TRANSFER_OUT, INBOUND_EDIT, ADJUSTMENT, DEFECT_MARK, DEFECT_RECEIVE, DEFECT_WRITEOFF, DEFECT_RECOVER, DEFECT_TRANSFER_OUT, DEFECT_TRANSFER_IN

## Бизнес-правила

### Ядро остатков
- **`_update_stock()` в `warehouse_stock_engine.py` — ЕДИНСТВЕННАЯ точка изменения остатков.** Никогда не менять `WarehouseStock` напрямую. `delta > 0` — приход, `delta < 0` — расход; создаёт `WarehouseStock` (upsert) + `StockMovement` (audit).
- **Неотрицательность:** `stock.quantity` не может быть < 0 — enforce в `_update_stock` (raises `ValueError`).
- **`StockMovement`** — append-only, не удалять, не редактировать. `WarehouseStock` = `SUM(StockMovement.quantity)`.
- `_resolve_barcode()` в `warehouse_stock_engine.py` — резолвит баркод → `Nomenclature` (raises `ValueError` если не найден); все items резолвятся через баркод.
- `_next_number()` там же — генерирует автономер (IN-1, OUT-2, TR-3).
- `in_transit` — информационное поле, обновляется при send/complete transfer.
- `cost_price` — ручной ввод на `WarehouseStock`.

### Статусные переходы
- **Приёмка:** DRAFT → EXPECTED → ACCEPTED; cancel из любого статуса → CANCELLED (cancel из ACCEPTED откатывает сток).
- **Отгрузка:** DRAFT → SHIPPED → DELIVERED; cancel из SHIPPED → CANCELLED (возвращает сток). Отгрузка **только с FULFILLMENT** складов.
- **Перемещение:** DRAFT → IN_TRANSIT → COMPLETED. На source `qty -= delta` (TRANSFER_OUT), на target `qty += delta` (TRANSFER_IN); `in_transit` на target растёт при send, падает при complete. Cancel (`DELETE /transfers/{id}`) — только из DRAFT, soft-delete; мутации статуса берут row-lock (`_get_transfer_locked`, FOR UPDATE) против гонки send/cancel. UI: создание всегда сразу делает send (черновик не остаётся); приём/отправка/удаление — вкладка «Перемещения» на странице склада; `GET /transfers?warehouse_id=` фильтрует source OR destination.

### Accept / Cancel / Update приёмки
- **Accept (DRAFT|EXPECTED → ACCEPTED):** если `actual_qty <= 0` и `expected_qty > 0` — автозаполнение `actual_qty = expected_qty`. Для каждого item с `actual_qty > 0` — `_update_stock(+actual_qty, INBOUND)`. **Если `receipt.is_defect`** (возвраты WB с ПВЗ) — сток идёт в брак: `_update_stock(delta=0, defect_delta=+actual_qty, DEFECT_RECEIVE)` (симметрия с Cancel; idempotency-guard проверяет DEFECT_RECEIVE, а не INBOUND). `status = ACCEPTED`, `actual_date = today`.
- **Cancel accepted (ACCEPTED → CANCELLED):** `_update_stock(-actual_qty, INBOUND_CANCEL)`; для `is_defect` — `defect_delta=-actual_qty`.
- **Update accepted:** для каждого item `delta = new_actual_qty - old_actual_qty`; если `delta != 0` — `_update_stock(delta, INBOUND_EDIT)`.

### Брак (Defective Goods)
- `defect_quantity` — отдельный счётчик в `WarehouseStock`, **не учитывается** в `available`; `defect_in_transit` — аналог `in_transit` для бракованных перемещений.
- `DEFECT_MARK` — годный → брак (`qty -= delta`, `defect_qty += delta`); `DEFECT_RECEIVE` — приёмка брака извне; `DEFECT_WRITEOFF` — списание; `DEFECT_RECOVER` — восстановление после ремонта (`qty += delta`, `defect_qty -= delta`). Перемещение брака — через `StockTransfer.is_defect=True`.
- Assembly validation использует `quantity` → брак автоматически исключён из отгрузок. Все операции — через `_update_stock`. Сервис: `warehouse_defect.py`.

### WB FBO поставки
- Статусы (read-only из WB): ACTIVE (Запланирована), ON_DELIVERY (Отгрузка разрешена), IN_PROGRESS (Идёт приёмка), ACCEPTED (Принята, финальный), CANCELLED (Отклонена, финальный).
- `statusID` из `/api/v1/supplies` и `statusId` кабинета (`supplyDetails`) — **одна и та же шкала**: `1` черновик (без `supplyID`, отсеивается в sync) · `2` Запланирована · `3` Отгрузка разрешена · `4` Идёт приёмка · `5` Принята (в т.ч. частично) · `6` Отклонена. Маппинг — `FBW_STATUS_MAP` в `services/fbo_supply/mappers.py`. До 2026-07-10 шкала была сдвинута на единицу (2 → «В пути», 4 → ACCEPTED), из-за чего запланированная поставка показывалась «В пути», а авто-приёмка срабатывала ещё на разгрузке.
- Связь FBO ↔ Отгрузка — вручную через UI (link/unlink): `wb_fbo_supplies.outbound_shipment_id` → FK, `outbound_shipments.wb_supply_id` → строка с ID поставки WB.
- При WB-статусе ACCEPTED: `outbound_shipment` → DELIVERED и `assembly_request` → DELIVERED (если были SHIPPED).
- **Авто-SHIP «забыли отгрузить» (`_collect_assembly_ship_on_wb_accepted` + `_ship_assemblies_best_effort` в `fbo_supply/sync.py`):** WB принял поставку (ACCEPTED), а связанная `AssemblyRequest` ещё `VEHICLE_ASSIGNED` (машина назначена, но не отгружена) → авто-`ship_request` (списывает сток, создаёт `OutboundShipment`, статус → SHIPPED). Второй сигнал авто-отгрузки в дополнение к FF-авто-шипу (ФФ закрыл заявку, `is_completed` — `fulfillment_service._collect_assembly_ship_candidates`): вместе закрывают правило «машина назначена И (ФФ закрыл ИЛИ WB принял) → отгружаем». Гейт строго `VEHICLE_ASSIGNED` (READY без машины — рано; SHIPPED+ — уже отгружена → auto-deliver). Работает и в full-sync (по уже-ACCEPTED поставкам — догоняет залежавшиеся), и в status-sync (по свежему переходу в ACCEPTED), и для складов без интеграции ФФ. Кандидаты собираются ПОД синк-транзакцией, сам `ship_request` — ПОСЛЕ commit, своей сессией; дефицит стока / гонка с FF-шипом синк НЕ валит (best-effort, лог `fbo_sync.auto_ship_skipped`). Счётчик — `result["assemblies_shipped"]`.
- Синхронизация: авто каждый час (scheduler job); ручная полная `POST /sync` (все поставки + async enrichment); ручная статусов `POST /sync-statuses`. Логируется в `SyncLog` (`sync_type="fbo_supplies"`).
- **Зеркало состава (`WbFboSupplyItem`):** единственный писатель — `_upsert_supply_items_fbw` (goods-API). `enrich_fbo_supplies` перетягивает goods для статусов из `_GOODS_REFRESH_STATUSES` = {ACCEPTED, ACTIVE, ON_DELIVERY, IN_PROGRESS} (раньше — только ACCEPTED): состав непринятой поставки в кабинете WB ещё меняется (добавили SKU/штуки), а `detail` отдаёт лишь агрегаты → без goods зеркало item-ов застревало и расходилось с WB/ФФ. `total_qty`/`accepted_qty` пересчитываются из суммы goods. Cooldown 24h (`ACTIVE_REENRICH_COOLDOWN_HOURS`/`ACCEPTED_REENRICH_COOLDOWN_HOURS`) бережёт rate-limit (6 req/min); `synced_at` ставится только при успешном goods-вызове (429 → ретрай на след. прогоне). Принудительно из UI — `GET /fbo-supplies/{id}/items?refresh=true` (`get_fbo_supply_items(force_refresh=True)`, любой статус).

### WB Acceptance Check (проверка приёмки складами)
Для каждого SKU + его планируемого распределения по WB-складам дёргает `POST https://supplies-api.wildberries.ru/api/v1/acceptance/options` и возвращает per-(barcode, warehouse) флаги `can_box / can_monopallet / can_supersafe`. Затем выбирает один `package_type` на SKU (BOX предпочтительнее MONOPALLET, далее SUPERSAFE) и **перераспределяет** qty с закрытых для этого типа складов на ближайший открытый в том же федеральном округе (через `warehouse_to_district`).
- **Маппинг WB `boxTypeID`:** `5` = МОНО, `6` = КОРОБ, `2` = иной / «Суперсейф».
- **Тип упаковки выбирается per-SKU, не per-warehouse:** если часть складов берёт только моно — для всего SKU выбирается MONOPALLET + warning «часть складов недоступна» (моно-аккейпт обычно шире).
- WB не возвращает `error` для нового баркода / без карточки → SKU помечается warning'ом «WB не вернул данные», в матрице остаётся как есть.
- **Excluded-фильтр получателей:** исключённые юзером склады (`ProjectSetting "excluded_warehouses"`, «Настройки складов») режут ПОЛУЧАТЕЛЕЙ redistribute — во все пулы (`open_set`/ФО/центр/global в `redistribute_blocked_qty` и `_split_distribution_by_package_type`) они не попадают, матч имён в каноне `_normalize_acceptance_wh`. Склад из distribution юзера не вычищается (план юзера), а если после вычета получателей нет — qty остаётся/дропается (`to=None`), но НЕ едет на исключённый склад.
- Кэш: `wb:acceptance:bc:{project_id}:{barcode}` — **пер-баркод**, 10 мин (WB rate-limit 6 req/min): живой вызов только по недостающим баркодам, количества/состав батча на ключ не влияют (distribution пересчитывается на каждый запрос; не вернул WB → негативный кэш `{}`; коэффициенты НЕ загрузились (429/сбой → флаги без `*_meta`) → снимок кэшируется коротко, 60с — 10-минутная выпечка «can_box, дней 0» демотировала черновики в предбронь); `wb:acceptance_warehouses:{project_id}` — 1 час. Endpoint сидит на **отдельном** rate-limit бакете `acceptance_check` (60/мин), не на общем `write` — фоновые проверки страницы распределения не выедают лимит автосейва черновиков; `force=true` дополнительно гейтится суб-бакетом `acceptance_check_force` (6/мин = квота WB). Капы схемы: `items ≤ 1000`, `barcode ≤ 64` (barcode попадает в Redis-ключ; 1 запрос = ceil(N/150) живых POST к WB).
- При создании сборки `package_type` пишется в `AssemblyRequest.package_type` — одна заявка = одна транспортная единица.

### Box Multiplicity (сборка по кратности коробок)
WB не принимает коробку, где кол-во штук одного SKU не кратно `pcs_per_box`. Кратность резолвится **пер пару (товар barcode, ФФ-склад)** — разные машины-поставки приняты на разные ФФ, поэтому per-ФФ кратность выходит сама собой. Распределение в `WarehouseNeedView` округляет до кратного, переливая остаток на соседа по федеральному округу.
- **Priority chain (резолв пары `(barcode, warehouse_id)`, первое сработавшее побеждает):**
  1. **machine** — последняя `ACCEPTED`-приёмка этого товара на этот ФФ (`inbound_receipts.status=ACCEPTED`, `cost_order_id` NOT NULL, item с `actual_qty>0`) → её машина (`cost_order`) → наполняемость строк `cost_order_items` с этим barcode. Несколько строк с разной кратностью → qty-weighted mode. Ячейка заблокирована (`editable=false`, `source="machine"`).
  2. **manual** — `BoxQtyPerWarehouse.box_qty` (ручная per-ФФ кратность), если не NULL.
  3. **default** — `Nomenclature.box_qty_override` (SKU-уровневый ручной дефолт).
  4. **none** — ничего не задано (`box_qty=null`).
- Машины **не** принятые (приёмка не `ACCEPTED` либо её нет) кратность не дают. Заказы фабрики **не** являются источником кратности — `factory`-строки остаются только в read-only drill-down.
- **Редактируемость** — на уровне ячейки ФФ: `editable=false` только при `source="machine"`. SKU-уровневое `box_qty_override` редактируется всегда.
- **Флаг `use_box_multiplicity` per-ФФ:** из строки `BoxQtyPerWarehouse` если она есть, иначе SKU-уровневый `Nomenclature.use_box_multiplicity`. Выключен → распределение для пары без округления.
- **Размер коробки `box_size` per-ФФ:** при `source="machine"` берётся из машины; иначе — из `BoxQtyPerWarehouse.box_size` (ручной per-ФФ размер), если строка есть; иначе `null`. На машинном ФФ изменение `box_size` блокируется (`409`), как и `box_qty`.
- `resolve_effective_ppb_for_assembly(db, project_id, barcode, warehouse_id)` → `(box_qty, use_flag)` по той же цепочке (переиспользует table-builder).
- **Bulk paste:** пользователь копирует прямоугольный диапазон из Excel → frontend парсит (`boxMultiplicityPaste.ts`), auto-detect колонок → батч `POST /bulk`; `bulk_update_by_barcode` upsert-ит по `(project_id, barcode)` либо `(project_id, barcode, warehouse_id)`. Машинно-заблокированные для `box_qty`/`box_size` пары попадают в ответ `locked` и не апдейтятся (только их `use_box_multiplicity` всё ещё применяется).
- **Журнал изменений + откат:** каждое реальное изменение поля (`box_qty_override` / `box_qty` / `box_size` / `use_box_multiplicity`) в `update_box_multiplicity`, `update_per_warehouse`, `bulk_update_by_barcode` пишет строку `BoxMultiplicityChangeLog` (`change_source`: `manual` / `bulk`). No-op (значение не поменялось) не логируется. `get_change_log(db, project_id, barcode)` → история свежие сверху (limit 200, JOIN `Warehouse` для `warehouse_name`). `revert_change(db, project_id, change_id)` применяет `old_value` обратно (`Nomenclature` для SKU-уровня, `BoxQtyPerWarehouse` для ФФ) и пишет новую запись `change_source="revert"`; откат `box_qty`/`box_size` для ставшего машинно-заблокированным ФФ не применяется (`409`).
- Acceptance redistribute учитывает эффективный `pcs_per_box` для каждого SKU+ФФ — после перелива qty остаётся кратным коробке.

### Unified Stock (единые остатки)
Объединённый вид: свои склады + WB + в пути. `get_unified_stock_summary(db, project_id, group_by, brand=None, include_forecast=False)`.
- **Источники:** `WarehouseStock` (свои склады), `WbWarehouseStock` (WB склады), `AssemblyRequest` items SHIPPED (в пути к WB) и PENDING→VEHICLE_ASSIGNED (зарезервировано), `WbFinanceRow` (реализация/профит/sale_qty), `CostOrderItem`→`load_avg_costs()` (средняя себестоимость), `wb_funnel_daily.adv_sum` (реклама).
- **Группировки (`group_by`):** sku (default), brand (двухуровневая: бренд → категории → артикулы), subject/imt/tag (одноуровневая), abc (per-SKU с A/B/C бейджем по `avg_daily_revenue`).
- **`include_forecast`:** `False` (default) = Факт, сходится с БДР копейка-в-копейку. `True` = «С прогнозом»: для товаров в пути (factory/vehicle), но ещё не на полке, оценивает выручку по qty-weighted средней категории. Узкий fallback — только incoming без стока (залежавшийся сток не раздувает итоги).
- **БДР parity:** `_build_finance_query` зеркалирует фильтры `wb_bdr_helpers.build_bdr_aggregate_sql`, иначе Unified Stock и БДР показывают разную реализацию: окно `COALESCE(sale_dt, rr_dt) BETWEEN cutoff AND today`; исключает `LOWER(sa_name) = 'неопознанный товар'`; `sale_qty`/`ret_qty` фильтруются по `supplier_oper_name IN ('Продажа','Возврат')`; `wb_funnel_daily` ограничен сверху `today`.
- **Tax:** `_compute_tax_and_profit` зеркалирует `bdr_enrichment.apply_tax_article`; принимает `tax_info` от `load_tax_settings`, поддерживает регим `usn_income_expense_vat` (опция `cost_as_expense`), иначе USN на net income (income − НДС). Регим берётся из `project_settings`, не захардкожен.
- **`load_tax_settings(cutoff, anchor)` — первый параметр это начало окна, НЕ anchor.** Передача anchor вместо cutoff при плейсхолдерных рейтах текущего месяца приводит к расчёту маржи без налога.
- **Trend window:** `_compute_trend_cutoffs(today)` якорит rolling-окна 7/14/30 дней к **вчерашнему** дню — чтобы неполный текущий день не размывал тренды.
- `get_unified_stock_summary()` принимает `today` и прокидывает во все под-запросы — одна «сегодняшняя» точка.
- При изменении tax regime/rate в `project_settings` — инвалидировать кэш отчётов (`tax_service` → `invalidate_project_reports`).
- **Карточка «Новинки»:** товары без единой продажи за 60 дней (`no_sale_window`); считается в `get_unified_stock_summary`. Category averages считаются one-shot и переиспользуются novelty KPI и forecast fallback — расхождение между вкладками = 0.
- Фронт: 4 режима отображения (шт / себестоимость / реализация / прибыль) + фильтры по бренду, `stock_days` и Факт/Прогноз тоггл.

### WB остатки (отдельная система)
`WbWarehouseStock` — read-only данные из WB API. Не интегрирован с локальным `WarehouseStock`. Синхронизация: `warehouse_stock_service.sync_warehouse_stocks()`. Используется для аналитики (`compute_need`) и единых остатков.

### WB Goods Returns (возвраты на ПВЗ)
`WbGoodsReturn` — отчёт «Возвраты и перемещение товаров» из WB Seller Analytics API (`GET /api/v1/analytics/goods-return`). Хранится зеркально + линк на `InboundReceipt` при оформлении приёмки на физ.склад.
- **Flow:** sync (раз в 30 мин, rate limit 1/min, max окно 31 день) → `WbGoodsReturn.upsert(srid)` → пользователь выбирает srids + warehouse → `POST /wb-returns/create-receipt` → `create_receipt_from_returns` создаёт `InboundReceipt(EXPECTED, is_defect=true)` → склад подтверждает через стандартный Accept flow → `WarehouseStock.defect_quantity` пополняется.
- `create_receipt_from_returns`: валидирует warehouse (inactive → 400); `pg_advisory_xact_lock(ns, project_id)` сериализует выдачу номеров; `SELECT ... FOR UPDATE` на `WbGoodsReturn` блокирует параллельный POST с пересекающимися srid. Номер `ВЗ-yymmdd-N` — **дата MSK** (не UTC контейнера), `N = MAX(suffix)+1`. Если nomenclature по barcode нет — создаёт stub; если barcode пуст — item пропускается, srid попадает в `skipped_srids` (UI warning).
- `classify_ui_state(ret, receipt_status)` — derived UI-state: `expired`, `received`, `picked_up_pending_receipt`, `pickup_planned`, `picked_without_receipt`, `ready_for_pickup`, `in_transit_to_pvz`.
- Scheduler job: `scheduler/jobs/wb_goods_returns_sync.py::sync_all_projects_wb_returns` — каждые 30 мин.

## Эндпоинты
- Router: `routers/warehouse.py` — склады, stock, receipt, shipment, transfer, FBO, acceptance-check, box-multiplicity.
- Router: `routers/wb_returns.py` — возвраты на ПВЗ (все через `Depends(get_current_project)`, мутации с `rate_limit_write`). `GET /` с фильтром `tab=pvz|in_transit|history|all`; `GET /summary` — KPI по ui_state; `POST /sync` — ручной запуск (date_from/date_to или дефолт 7 дней).
- Router: `routers/assembly.py` — заявки на сборку (см. `DOMAIN_ASSEMBLY.md`).
- `GET /warehouse/stock/unified?group_by={mode}&brand={name}&include_forecast={bool}` — единые остатки.
- `GET /warehouse/box-multiplicity` — таблица: SKU + per-ФФ резолв кратности. `PATCH .../{nm_id}` — SKU-уровень (`box_qty_override` null = сбросить, и/или `use_box_multiplicity`), разрешён всегда. `PATCH .../per-warehouse/{barcode}/{warehouse_id}` — ручная per-ФФ кратность/размер (`box_qty`, `box_size`, `use_box_multiplicity`); на машинном ФФ изменение `box_qty`/`box_size` → `409`, изменение только `use_box_multiplicity` разрешено. `POST .../bulk` — массовый paste из Excel (ответ содержит `locked`). `GET .../sources/{barcode}` — read-only история снабжения SKU. `GET .../changes/{barcode}` — журнал изменений кратности/размера (свежие сверху). `POST .../changes/{change_id}/revert` — откат изменения (`404` если запись не найдена, `409` если откат заблокирован машиной).
- WB Marketplace API: `GET /api/v3/supplies`, `/api/v3/supplies/{id}/orders`, `/api/v3/supplies/{id}`. Base `https://marketplace-api.wildberries.ru`, авторизация `IntegrationKey service="wb"`.

## Зависимости
- `DOMAIN_ASSEMBLY` — заявки на сборку, создают `OutboundShipment` при SHIPPED.
- `DOMAIN_COST` — `cost_order_id` в `InboundReceipt` (связь с заказом себестоимости).
- `DOMAIN_WB` — FBO sync, WB warehouse stock.

## Грабли
- **`_update_stock` — единственная точка изменения остатков** — никогда не менять `WarehouseStock` напрямую.
- **Неотрицательность `quantity`** — enforce в `_update_stock`, raises `ValueError` при qty < 0.
- **`StockMovement` append-only** — не удалять, не редактировать.
- **Отгрузка только с FULFILLMENT складов** — проверка `warehouse.warehouse_type` в `ship_shipment`.
- **Нормализация имён складов:** WB отдаёт имена с суффиксами («: Питание», «СГТ», скобками) и варианты «Шушары», «Самара» — нормализовать через `ACCEPTANCE_TO_STOCK_NAME` (`warehouse_geo_data.py`) в **canonical**-форму, не «stripped», иначе один склад разваливается на две колонки в Unified Stock после paren-strip.
- **Cold-start replace:** `WarehouseNeed` snapshot полностью замещается, не мерджится — иначе при отключённой галке «учитывать спец-склад» старая qty оседает тенью в матрице.
- **Tax cutoff parity:** `load_tax_settings(cutoff, anchor)` — первый параметр это начало окна, не anchor; ошибка приводит к расчёту маржи без налога.
- **БДР parity:** при любом изменении `_build_finance_query` / `_compute_period_metrics` — прогнать `tests/test_warehouse_unified_stock_bdr_parity.py` и `test_warehouse_tax_cutoff_parity.py`.
- **WB Returns номера `ВЗ-yymmdd-N`:** дата MSK (не UTC контейнера), `pg_advisory_xact_lock` сериализует выдачу.

## Файлы
- `models/warehouse.py` — 9 ORM-моделей склада.
- `models/wb_fbo.py` — `WbFboSupply` + Item.
- `models/wb_returns.py` — `WbGoodsReturn`.
- `schemas/warehouse.py`, `schemas/wb_fbo.py`, `schemas/wb_returns.py`, `schemas/assembly.py`, `schemas/box_multiplicity.py`.
- `services/warehouse_crud.py` — CRUD складов.
- `services/warehouse_inbound.py` — приёмка.
- `services/warehouse_outbound.py` — отгрузка + перемещения.
- `services/warehouse_defect.py` — управление браком (mark, receive, writeoff, recover).
- `services/warehouse_stock_engine.py` — движения + остатки + единые остатки (ядро).
- `services/warehouse_service.py` — re-export для обратной совместимости.
- `services/fbo_supply_service.py` — FBO синхронизация + авто-доставка.
- `services/warehouse_stock_service.py` — WB остатки sync + `compute_need`.
- `services/warehouse_need_service.py` — расчёт потребности в товарах. Скорость заказов growth-aware: `max(среднее за analysis_days, за 7 и 3 полных дня)` per SKU (`GROWTH_RECENT_WINDOWS`; растущий SKU не занижается плоским средним). Глобальный горизонт `total_need`/`can_send` и база покрытия greedy 4.6 = `supply_days + спрос-взвешенное плечо` (веса — сырой `warehouseName` заказа, mode-инвариантно); клетки всегда считали supply+lead. Отдаёт per-SKU `eff_avg_daily`/`growth_ratio`/`lead_days`/`wb_days_left(_inbound)` — колонка «Хватит, дн» в матрице черновика. Ответ `GET /reports/stock_need` несёт `summary.wb_stocks_updated_at` (ISO | null — max `updated_at` остатков WB, гард свежести для фронта); поле инжектится на уровне РОУТЕРА (`reports_stock.py`), ПОСЛЕ закэшированного сервиса (`@cached ttl=300`) — timestamp, запечённый в кэш, протухал бы.
- `services/warehouse_acceptance_service.py` — WB Acceptance Check + redistribute закрытых складов.
- `services/box_multiplicity_service.py` — box multiplicity (machine-resolved per-ФФ кратность/размер + ручные per-ФФ/SKU дефолты, bulk paste, журнал изменений + откат `get_change_log`/`revert_change`, `resolve_effective_ppb_for_assembly`, `has_machine_box_qty`).
- `services/warehouse_geo_data.py` — WB warehouse maps: координаты, federal districts, `ACCEPTANCE_TO_STOCK_NAME`, `WB_API_ID_TO_STOCK_NAME`.
- `services/wb_returns_service.py` — sync + list + summary + `classify_ui_state` + `create_receipt_from_returns`.
- `routers/warehouse.py`, `routers/wb_returns.py`, `routers/assembly.py`.
- `scheduler/jobs/wb_goods_returns_sync.py` — периодический sync возвратов.
- `integrations/wb_api.py` — `WBApiClient.get_goods_returns(date_from, date_to)`.

## Тесты
- `tests/test_fbo_supply_service.py` — unit + API тесты FBO.
- `tests/test_wb_returns.py` — sync upsert, tab filters, summary, `classify_ui_state`, create_receipt.
- `tests/test_warehouse_stock_engine_helpers.py` — pure-function helpers (без DB — не замедлять suite).
- `tests/test_warehouse_unified_stock_bdr_parity.py` — БДР parity guard.
- `tests/test_warehouse_tax_cutoff_parity.py` — tax cutoff parity regression guard.
