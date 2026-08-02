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
| `StockTransfer` | Переезд, статус PENDING\|IN_PROGRESS\|READY\|VEHICLE_ASSIGNED\|SHIPPED\|DELIVERED\|RETURNED\|CLOSED\|CANCELLED, вехи `actual_ready_date`/`shipped_at` | SoftDelete |
| `StockTransferStatusHistory` | Журнал смены статусов переезда (`old_status`/`new_status`/`changed_by`), зеркало `AssemblyStatusHistory` | append-only, CASCADE от переезда |
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
- `TransferStatus` — PENDING, IN_PROGRESS, READY, VEHICLE_ASSIGNED, SHIPPED, DELIVERED, RETURNED, CLOSED, CANCELLED (**зеркало `AssemblyStatus`**, миграция `trv04`). Таблица переходов — `TRANSFER_TRANSITIONS`, правимые статусы — `TRANSFER_EDITABLE_STATUSES`; обе в `models/warehouse.py`
- `MovementType` — INBOUND, INBOUND_CANCEL, OUTBOUND, OUTBOUND_CANCEL, TRANSFER_IN, TRANSFER_OUT, INBOUND_EDIT, ADJUSTMENT, DEFECT_MARK, DEFECT_RECEIVE, DEFECT_WRITEOFF, DEFECT_RECOVER, DEFECT_TRANSFER_OUT, DEFECT_TRANSFER_IN

## Бизнес-правила

### Ядро остатков
- **`_update_stock()` в `warehouse_stock_engine.py` — ЕДИНСТВЕННАЯ точка изменения остатков.** Никогда не менять `WarehouseStock` напрямую. `delta > 0` — приход, `delta < 0` — расход; создаёт `WarehouseStock` (upsert) + `StockMovement` (audit).
- **Неотрицательность:** `stock.quantity` не может быть < 0 — enforce в `_update_stock` (raises `ValueError`).
- **`StockMovement`** — append-only, не удалять, не редактировать. `WarehouseStock` = `SUM(StockMovement.quantity)`.
- `_resolve_barcode()` в `warehouse_stock_engine.py` — резолвит баркод → `Nomenclature` (raises `ValueError` если не найден); все items резолвятся через баркод.
- `_next_number()` там же — генерирует автономер (IN-1, OUT-2, TR-3).
- `in_transit` — НЕ только информационное: с 2026-07-28 кормит `transfer_transit` в «Единых остатках» (входит в «Итого»-капитал; SKU целиком уехавший в TR виден). Обновляется при send/complete/receive_transfer_fact.
- `cost_price` — ручной ввод на `WarehouseStock`.

### Статусные переходы
- **Приёмка:** DRAFT → EXPECTED → ACCEPTED; cancel из любого статуса → CANCELLED (cancel из ACCEPTED откатывает сток).
- **Отгрузка:** DRAFT → SHIPPED → DELIVERED; cancel из SHIPPED → CANCELLED (возвращает сток). Отгрузка **только с FULFILLMENT** складов.
- **Перемещение / Переезд (статусная модель заявки, канон юзера 31.07.2026):** `PENDING → IN_PROGRESS → READY → VEHICLE_ASSIGNED → SHIPPED → DELIVERED → CLOSED`, плюс `RETURNED` и `CANCELLED`. Валидация — ЕДИНОЙ точкой `_check_transfer_transition` по `TRANSFER_TRANSITIONS` (россыпь `if status != ...` вычищена). Два осознанных отличия от `ASSEMBLY_TRANSITIONS`: `PENDING → READY` напрямую разрешён (у переезда без ФФ фазы «провайдер собирает» нет), `SHIPPED → READY` запрещён (сток списан, откат только через `RETURNED`, который его возвращает).
  - **Сток движется РОВНО дважды:** `→ SHIPPED` списывает с источника (`TRANSFER_OUT`) и вешает транзит на получателя; `→ DELIVERED` приходует получателю (`TRANSFER_IN`) и снимает транзит. Прочие ступени сток НЕ трогают. `complete_transfer` книжит ОСТАТОК плана за вычетом уже принятого по журналу движений — иначе ручное «Принять» поверх порционного авто-приёма ФФ зачислило бы единицы дважды (транзит ушёл бы в ноль по `max(0, …)`, и расхождение не всплыло бы нигде).
  - **`/send` работает из READY И из VEHICLE_ASSIGNED** — осознанный карв-аут сверх таблицы (зеркало `allow_gazelka_ready` у заявки): переезд между нашими складами возят и без оформления машины, забор тогда просто не создаётся. АВТО-отгрузка по сигналу ФФ карв-аутом НЕ пользуется — она строго из VEHICLE_ASSIGNED.
  - **Ручные ступени** (переезд БЕЗ связки с ФФ): `POST /transfers/{id}/ready` (PENDING|IN_PROGRESS → READY, требует непустой состав), `POST /transfers/{id}/return` (SHIPPED|DELIVERED → RETURNED), `POST /transfers/{id}/close` (RETURNED|DELIVERED → CLOSED). У `/return` и `/close` опциональное тело `{"comment": …}` — комментарий уходит в историю.
  - **Возврат (`return_transfer`)** — зеркало `assembly.status.return_to_warehouse` с поправкой на то, что получатель ТОЖЕ наш склад: на источник товар возвращается приёмкой `InboundReceipt(ACCEPTED)` на ВЕСЬ план, у получателя снимается транзит И списывается уже зачисленное (`reference_type='TRANSFER_RETURN'` — чтобы производная «сколько принято» осталась честной). Забор переезда НЕ удаляется: перевозка состоялась и оплачена. `shipped_at` обнуляется — следующая попытка проставит свой.
  - **Отмена** (`DELETE /transfers/{id}`) ставит `CANCELLED` + soft-delete, и только ДО отгрузки: из SHIPPED/DELIVERED (которые таблица формально допускает) сервис отказывает — сток уже уехал, обратный ход только `/return`. Статус проставляется ЯВНО, чтобы «отменён» был отличим от «удалён по ошибке» в отчётах, читающих статус.
  - Мутации статуса берут row-lock (`_get_transfer_locked`, FOR UPDATE) против гонки send/cancel. Каждый переход пишется в `stock_transfer_status_history` (`changed_by`: `user` | `ff_sync` | `system`). `GET /transfers?warehouse_id=` фильтрует source OR destination; `?in_transit=true` = SHIPPED; срез Листа логиста — `?status=READY&has_vehicle=false`.
- **Переезд с машиной (2026-07):** полноценный переезд между складами с назначением машины, логистикой и оплатой, по аналогии с заявкой на сборку. Машина назначается из READY и двигает статус в VEHICLE_ASSIGNED (`assign_vehicle_transfer`, `assign_vehicle_transfer_bulk`); ПЕРЕназначение внутри VEHICLE_ASSIGNED разрешено (в отличие от заявки: у переезда нет ни пропуска, ни брони на маркетплейсе — поменять госномер за день до забора это норма), статус при этом не меняется и строка истории не пишется. `unassign_vehicle_transfer` возвращает в READY; после отгрузки снять нельзя — гарантирует сама таблица переходов. Транспортная единица: паллеты или короба (`shipped_as_boxes`), количество (`pallets_count`), вес единицы (`pallet_weight_kg`). При отправке (`send_transfer`) создаётся забор (`OutboundShipment` с непустым `stock_transfer_id`) — НЕ отгрузка, а **носитель логистики и денег** (связка с заявкой на оплату, выпиской).
  - **🔴 Инвариант 1: Забор переезда не пишет движений стока.** Списание — исключительно на самом перемещении (`TRANSFER_OUT/TRANSFER_IN` на source/target). Поэтому `ship_shipment`, `deliver_shipment`, `cancel_shipment` запрещены на заборах переездов (гард в каждой функции): забор идёт в SHIPPED-DELIVERED когда перемещение завершено, другие операции отгрузки были бы фантомными движениями.
  - **🔴 Инвариант 2: `OutboundShipment.assembly_request_id = NULL` у забора переезда всегда.** Иначе переезд протечёт во все отчёты логистики заявок (они фильтруют через INNER JOIN по `assembly_request_id`), расходуя их бюджеты и медианы ₽/паллета — переезд это другой рынок, его цены несопоставимы с отгрузками на WB. Без `assembly_request_id` забор видим только через связь `stock_transfer_id`.
  - **Конвертация заявки в переезд** (`convert_assembly_to_transfer`): разрешена из ЛЮБОГО статуса заявки (включая терминальные CLOSED/RETURNED/CANCELLED), но только при нулевом нетто-эффекте заявки на сток (см. ниже). Новый переезд по составу заявки, и **готовность НАСЛЕДУЕТСЯ**: если заявка была в READY/VEHICLE_ASSIGNED/SHIPPED/DELIVERED/RETURNED/CLOSED (ФФ её уже собрал), переезд рождается сразу в READY с `actual_ready_date` заявки — гонять ступени заново бессмысленно (кейс ASM-726/727); иначе PENDING. Сама заявка статус НЕ меняет, остаётся со своей историей и своими попытками отгрузки. Транспортная единица наследуется от заявки (паллеты/короба и вес), иначе забор уедет без основания для расчёта стоимости и ₽/паллета переезда не посчитается. Зеркала ФФ по умолчанию остаются на заявке (`move_ff_links=False`); при `move_ff_links=True` перемещаются на новый переезд.
  - **Гард двойного списания при конвертации:** нетто-эффект заявки на сток определяется как `Σ(ASSEMBLY + ASSEMBLY_CANCEL + RECEIPT)` — движения по самой заявке и возвратам (приёмкам) на её базе. Если нетто < 0 (что-то списано, не вернулось), конвертация отказана: отправка переезда спишет те же единицы второй раз → фантомный дефицит. Исключение: если возврат оформлен на ДРУГОЙ склад (не склад-источник заявки), сообщение подскажет откуда брать переезд.
- **Правка перемещения (`update_transfer`, `PUT /transfers/{id}`, 2026-07-31): в `TRANSFER_EDITABLE_STATUSES` — PENDING / IN_PROGRESS / READY.** VEHICLE_ASSIGNED сюда НЕ входит осознанно: машина посчитана под конкретный объём и маршрут, правка состава разошлась бы с тем, за что логист договорился платить (снять машину — `unassign_vehicle_transfer`, вернёт в READY). Меняются маршрут (`from_warehouse_id`/`to_warehouse_id` — сток ещё не двигался), комментарий, брак, транспортная единица и состав. `items` — ПОЛНАЯ ЗАМЕНА (резолв по баркодам, как в `create_transfer`); не передан — состав не трогаем, передан пустым — 400 (переезд без позиций всё равно не отправить, а молча стереть состав хуже отказа). Применяются только ЯВНО переданные поля (`model_dump(exclude_unset=True)` в роутере): частичное тело не обнуляет остальное дефолтами схемы, `shipped_as_boxes` здесь обычный bool (трёхзначный — только в `TransferAssignVehicle`). Вся валидация идёт ДО первой мутации: отказ на резолве ШК не должен оставить наполовину применённую правку. В VEHICLE_ASSIGNED и после отгрузки — 400: сток уже списан и снят снимком в забор, правка состава разъехалась бы с движениями. 🔴 **Гард смены маршрута:** если у переезда есть связки ФФ, чей склад после правки перестаёт быть концом маршрута — отказ с номерами заявок. `link_request` такую пару создать бы не дал, а `_collect_transfer_fact_candidates` отбирает приёмки ПО СКЛАДУ ЗАЯВКИ и джойном конец маршрута не сверяет — факт чужого склада поехал бы на новый `to_warehouse_id`.
- **Связки ФФ (`ff_links`) — И в карточке, И в списке:** `get_transfer` (и ответ `PUT`) отдаёт привязанные заявки обеих сторон по `stock_transfer_id` (`fulfillment_service.list_transfer_ff_links`), список — тем же набором полей, но БАТЧЕВОЙ выборкой на все строки сразу (`list_transfer_ff_links_batch`): строка переезда стоит в одном рабочем списке с заявками на сборку и обязана нести бейдж «ФФ: PVB-…» без догрузки, а поштучный вызов дал бы N+1. Раньше карточка ради 0-2 связок тянула ДВА полных списка заявок ФФ по обоим складам (~300 КБ); на складе «Натали» уже 432 заявки при лимите 500 — связка вот-вот перестала бы находиться. Это про корректность, а не про скорость.
- **Порционный приём по факту (`receive_transfer_fact`, 2026-07-28):** авто-приём по завершённой связанной ФФ-приёмке (см. DOMAIN_FULFILLMENT). TRANSFER_IN на факт (годное+брак), дубли строк плана схлопываются по номенклатуре, сверх плана не приходуем; «уже принято» выводится из движений (идемпотентность), transfer → DELIVERED только при полном покрытии (переход пишется в историю с `changed_by='ff_sync'`); работает ТОЛЬКО в SHIPPED; при завершении наследует кратность коробов и сбрасывает `reports:balance/assembly_link_anomalies/warehouse_need`; опциональный `mark_ff_request_applied` ставит маркер заявки атомарно с движениями.

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

### 🔥 Остатки не учитывать (`stock_ignored_warehouses`)
Сгоревшие склады WB (Краснодар/Невинномысск/Электросталь, 2026-07): WB продолжает отдавать их остатки как живые → фантомные штуки в расчётах. `ProjectSetting "stock_ignored_warehouses"` (`settings_service.get/set_stock_ignored_warehouses`, GET/PUT `/refs/stock-ignored-warehouses`) — список складов, стоку которых расчёты НЕ верят. **Независим от `excluded_warehouses`** (excluded = «не цель отгрузки», ignored = «сток фантомный, спрос/цель — живые»).
- **Хелпер:** `settings_service.get_stock_ignored_set(db, pid)` — канон (`_normalize_wb_warehouse`) + все сырые написания из `ACCEPTANCE_TO_STOCK_NAME` (для SQL-фильтров `warehouse_name NOT IN (...)` по сырому имени). KV-чтение дешёвое, отдельного кэша нет.
- **Фильтруют сток:** `warehouse_need_service` (только `stock_lookup`/`wb_stock_total`; спрос по заказам сохранён), `assembly_load_forecast_service` (только `stock`, не incoming), `stock_forecast_service._load_wb_breakdown` («На WB»/days_left; fallback на `WbFunnelDaily` не фильтруется — нет разбивки по складам), `cold_start_distribution_service` (`fetch_sku`/`fetch_cold_start_segment`, raw SQL + expanding `:ignored`), `box_multiplicity_service`, `funnel/stock_costs.py`, `pricing/markup.py`, `funnel/ad_campaigns_service.py`.
- **СОЗНАТЕЛЬНО не трогаются:** страницы факта (Остатки по складам WB, Сводные остатки), история/снапшоты, БДР/Отчёты и спрос матрицы потребности.
- **Ловушка in_way-носителя:** мост `_bridge_rows_from_remains` вешает общекарточные `in_way_*` на строку-носителя (max qty). Носитель теперь предпочитает склад НЕ из ignored-сета (иначе `notin_`-читатели выкинули бы и в-пути); все склады ignored → как раньше (max qty). Настройка читается один раз на прогон синка.
- `set_*` инвалидирует project-scoped: `reports:warehouse_need|stock_warehouses|stock_warehouses_articles|stock_history`.

### Логистика переездов (Отчёт)
`services/transfer_logistics.py` — аналитика стоимости перевозки между СВОИМИ складами. Источник — заборы переездов (`OutboundShipment` с непустым `stock_transfer_id`, создаются при `send_transfer`), сджойненные с самим `StockTransfer`. Живёт ОТДЕЛЬНО от логистической аналитики заявок (`services/assembly/analytics.py`) намеренно: та фильтрует через INNER JOIN по `assembly_request_id`, а маршрут «наш склад → наш склад» несопоставим с маршрутами на маркетплейс и испортил бы её медианы и прогнозную модель ₽/паллета. Отчёт `GET /warehouse/transfers/logistics-report` отдаёт:
- **Сводку (`TransferLogisticsSummary`):** `total_pallets` (количество паллет по паллетным переездам), `total_cost` (сумма ₽), `cost_per_pallet` (₽/паллета только для переездов с `shipped_as_boxes=False`), `total_boxes` (отдельный счётчик для коробочных переездов `shipped_as_boxes=True` — не смешиваются с паллетами), `transfers_count`, `total_units`, `cost_per_unit`, а также `paid_cost` / `unpaid_cost`.
- **Разрезы:** по маршрутам (`from_warehouse_name` → `to_warehouse_name`), по перевозчикам, по периодам (день / неделя / месяц — бакет считает общий `_period_bucket` из `assembly/analytics.py`, свой не заводим).
- **Построчный список (`TransferLogisticsRow`):** перемещение, даты, машина, перевозчик, стоимость, штук/SKU, единица груза + признак оплаты: `payment_request_number` (номер заявки на оплату, если есть) и `is_paid` = забор сматчен с транзакцией выписки (`matched_transaction_id`) ИЛИ покрыт проведённой заявкой.
- **Фильтры:** период (`date_from`, `date_to` по `shipped_date` забора), склад-источник/назначение, перевозчик (по `counterparty_id`).

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
- `GET /warehouse/transfers` — список переездов (`?status=` — whitelist из девяти статусов новой шкалы, `?in_transit=true` = SHIPPED, `?has_vehicle=`, `?warehouse_id=`, `?converted_from_assembly_id=`); `GET /warehouse/transfers/{id}` — карточка (с `items`); `POST /warehouse/transfers` — создание (рождается в PENDING); `PUT /warehouse/transfers/{id}` — правка (PENDING/IN_PROGRESS/READY, см. ниже); `POST /warehouse/transfers/{id}/ready` — «собран» (PENDING|IN_PROGRESS→READY); `POST /warehouse/transfers/{id}/send` — отправка (READY|VEHICLE_ASSIGNED→SHIPPED, создаёт забор, списывает сток); `POST /warehouse/transfers/{id}/complete` — приём получателем (SHIPPED→DELIVERED, приходует остаток плана); `POST /warehouse/transfers/{id}/return` — возврат на источник (SHIPPED|DELIVERED→RETURNED); `POST /warehouse/transfers/{id}/close` — закрытие (RETURNED|DELIVERED→CLOSED); `DELETE /warehouse/transfers/{id}` — отмена (CANCELLED + soft-delete, только до отгрузки). **`ff_links` и `received_units` отдаются И в списке, И в карточке** — строка переезда живёт в одном рабочем списке с заявками на сборку: бейдж «ФФ: PVB-…» и «принято X из Y» (X = `received_units` по журналу движений, Y = `units_total`) рисуются без догрузок, одной батч-выборкой на весь список.
- `GET /warehouse/transfers/{id}/ff-candidates?side=source|dest` — заявки ФФ, которые можно привязать к переезду (связка **от карточки переезда**, обратное направление к `link-candidates` заявки ФФ). `side=source` → `kind=assembly` на складе забора, `side=dest` → `kind=inbound` на складе получателя; только заявки без нашего документа и не `local_archived`, свежие сверху, лимит 300. Сама привязка/отвязка — существующими ручками ФФ (`POST|DELETE /warehouse/{warehouse_id}/fulfillment/requests/{ff_id}/link` с `stock_transfer_id`), `warehouse_id` берётся из строки кандидата.
- `POST /warehouse/transfers/{id}/assign-vehicle` — назначить машину (READY → VEHICLE_ASSIGNED; переназначение внутри VEHICLE_ASSIGNED разрешено. Логистика: машина, водитель, перевозчик, даты, стоимость, единица груза). `POST /warehouse/transfers/assign-vehicle-bulk` — одна машина на N переездов, атомарно. `POST /warehouse/transfers/{id}/unassign-vehicle` — снять машину (VEHICLE_ASSIGNED → READY, чистит логистику, не трогает единицу груза).
- `GET /warehouse/assembly/{id}/ff-candidates` — заявки ФФ, которые можно привязать к СБОРКЕ (то же направление «от нашей карточки», что у переезда выше). Сторона у сборки одна → всегда `kind=assembly` на её складе, поля `side` в строке нет; те же предикаты «свободна» (общий `_free_ff_requests`), скоринга нет. Работает и для учётных зеркал FBS (`kind=fbs`) — см. `DOMAIN_ASSEMBLY.md`. Привязка/отвязка — теми же ручками ФФ с `assembly_request_id`.
- `POST /warehouse/assembly/{id}/to-transfer` — конвертация заявки на сборку в переезд (новый документ по составу заявки, разрешено при нулевом нетто-стоке заявки).
- `GET /warehouse/transfers/logistics-report?date_from=&date_to=&from_warehouse_id=&to_warehouse_id=&counterparty_id=` — отчёт логистики переездов (сводка, разрезы по маршрутам/перевозчикам/периодам, построчный список; ₽/паллета только для паллетных).
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
- `services/warehouse_outbound.py` — отгрузка + перемещения + переезды (машина, логистика, конвертация заявки).
- `services/transfer_logistics.py` — отчёт логистики переездов (маршруты, перевозчики, ₽/паллета паллетных, отдельный счётчик коробочных).
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
