---
name: fbs-assembly-mirror-invariants
description: Учётные заявки kind=fbs — где kind-фильтр реально обязателен (два in_transit-сайта + TG-алярм), почему seller-имя в wb_warehouse_name_manual плодит ложные срабатывания, и что contour_condition ≠ «только боевой контур»
metadata:
  type: project
---

Ревью фичи «учётное зеркало сборки FBS» (`8bec1bcd..4f248c41`, ветка `feat/main-tree-port`, 30.07.2026). Джоб-агент расставил `AssemblyRequest.kind != 'fbs'` в ~15 читателей; неочевидно вот что.

**1. Полнота kind-фильтров решается по СТАТУСУ SHIPPED, а не по «резерву».**
Зеркало доходит до SHIPPED (поставка передана) и висит там неделями (до сортировки СЦ), при том что units УЖЕ списаны `writeoff_completed_orders` по `complete`. Поэтому опасны не только читатели резерва, а любой `status == SHIPPED`. Два таких сайта — сиамские близнецы отфильтрованных соседей и легко теряются:
`warehouse_need_service` (in_transit_map рядом с отфильтрованным target_alloc) и `warehouse_stock_engine.get_unified_stock_summary` (in_transit → `entry["total"]` = «Итого КАПИТАЛ»).
Третий — `link_anomalies._supply_discrepancies` (`_SUPPLY_SCOPE_STATUSES = VEHICLE_ASSIGNED|SHIPPED`): у зеркала нет `AssemblyWbSupply` → `pass_missing=True` → строка в аномалиях И рассылка `scheduler/jobs/supply_discrepancy.py` в TG.
**Why:** «резерв» — не единственный канал; сток-обязательством зеркало становится через транзит.
**How to apply:** грепать `AssemblyRequest.status` целиком и делить читателей на (а) gated на `wb_fbo_supply_id` / FF-link / `source_vehicle_id` / `OutboundShipment` — безопасны by construction; (б) все остальные. Категория (б) и есть чеклист.

**2. `wb_warehouse_name_manual` у зеркала = имя склада ПРОДАВЦА, а поле семантически «склад назначения WB».**
Отсюда ложные срабатывания там, где kind-фильтра нет: «Слоты сдачи поставок» (`eff = warehouse_name or wb_warehouse_name_manual` → непустой → строка + лишний фетч календаря WB), дропдаун `crud.list_wb_warehouses`, и — дороже всего — `_dir_key` в `merge_assembly_requests`: имена FBS-складов продавца совпадают с именами FBO-складов WB («Пушкино», «Коледино»), так что merge зеркала с FBO-заявкой проходит все проверки → items уезжают в реальную сборку и списываются ВТОРОЙ раз.
**Why:** поле переиспользовано как «подпись направления», а читают его как «куда сдаём на WB».
**How to apply:** при любой новой ветке kind=fbs проверять всех читателей `wb_warehouse_name_manual` (их ~20), а не только читателей `status`.
**Апдейт 02.08.2026 (коммит 714d5e0e):** `update_assembly_request` разрешил ПЕРВУЮ привязку FBO-поставки на закрытом статусе ради этих самых зеркал (они приезжают сразу в DELIVERED). Побочка: тот же блок безусловно делает `req.wb_warehouse_name_manual = fbo_supply.warehouse_name` (`assembly/crud.py:1711`) — имя склада ПРОДАВЦА затирается именем склада WB НЕОБРАТИМО: `_deny_fbs_manual` в crud нет, перепривязка теперь запрещена, а `assembly_mirror` пишет seller-имя только при СОЗДАНИИ зеркала.



**3. `contour_condition()` — «текущий контур», а НЕ «боевой».**
В режиме sandbox он выбирает ИМЕННО sandbox-строки. `assembly_requests` метки контура не имеет, поэтому зеркало в песочнице пишет РЕАЛЬНЫЕ заявки (жжёт ASM-номера, лечится только руками). Канон домена — ранний выход `if is_sandbox_contour(): return 0` (так делает `writeoff_completed_orders`); DOMAIN_WB_FBS при этом уже утверждает «только боевой контур» — расхождение docs↔code.
**How to apply:** в любом новом сервисе домена FBS, который пишет в ОБЩИЕ таблицы (не `wb_fbs_*`), нужен `is_sandbox_contour()`-гейт, а `contour_condition` — только для чтения зеркала.

**4. Идемпотентность создания есть, идемпотентности статусов нет.**
`sync_fbs_assembly_mirror` зовётся из ДВУХ расписаний (статусы 5 мин ‖ поставки 15 мин, один event-loop) без распределённого лока. Partial unique + `begin_nested` закрывают только СОЗДАНИЕ. Догон статуса идёт без row-lock и без `WHERE status = old` → дубли `AssemblyStatusHistory`, а пересборка состава (`mirror.items = [...]`, cascade delete-orphan) у проигравшего даёт `StaleDataError`, который best-effort обёртка глотает вместе с прогрессом тика.
**How to apply:** мерка «идемпотентно» для джобов домена — Redis-лок `services/wb_fbs/locks.py` (как у push/writeoff), а не только unique-индекс.

Связано: [[fbs-writeoff-transit-invariants]], [[warehouse-need-invariants]].
