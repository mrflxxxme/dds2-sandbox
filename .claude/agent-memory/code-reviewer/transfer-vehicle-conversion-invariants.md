---
name: transfer-vehicle-conversion-invariants
description: Переезд с машиной и оплатой (StockTransfer + забор OutboundShipment) — где реально держится «не списать дважды», и три слепые зоны гарда конвертации заявка→переезд
metadata:
  type: project
---

Ревью 2026-07-31, ветка `feat/transfer-vehicle-ff` (worktree `dds2-wt/transfer-vehicle`).

**Факт 1 — «переезд не течёт в чужие отчёты» держится на ОДНОМ поле.**
`_create_transfer_pickup` создаёт `OutboundShipment` без движений и с `assembly_request_id IS NULL`; вся логистическая аналитика сборок отсекает переезды именно предикатом `assembly_request_id IS NOT NULL` + INNER JOIN по `AssemblyRequest` (`services/assembly/analytics.py::_logistics_base_filters`). Денежные читатели (`payment_request_service.list_shippable`, `etl/sync_shipment_payments`, `ff_billing/invoices` LOGISTICS-строки) переезд ВИДЯТ намеренно — они фильтруют по перевозчику/статусу, а не по заявке.
**How to apply:** любой новый читатель `outbound_shipments`, считающий отгрузку/выручку/списание, обязан добавить `stock_transfer_id IS NULL`; любой денежный — наоборот, не должен требовать заявку.

**Факт 2 — гард `_assembly_net_stock_effect` слеп к трём вещам.**
Он суммирует движения `ASSEMBLY` + `ASSEMBLY_CANCEL` + `RECEIPT` привязанных приёмок (`quantity + defect_delta`, приёмки БЕЗ `is_deleted` — компенсация живёт движением `INBOUND_CANCEL` с тем же `reference_id`, проверено). Слепые зоны:
1. **FBS-зеркала** (`kind=fbs`): списание FBS пишет `reference_type='FBS_ORDER'` (`wb_fbs/orders_service._WRITEOFF_REF_TYPE`), а не `ASSEMBLY` → нетто выходит 0 и конвертация разрешается на уже списанном товаре. Все прочие мутации заявки закрыты `_deny_fbs_manual`, конвертация — нет (спасает только ранний `if (isFbs) return buttons` на фронте).
2. **Активная заявка** (PENDING/IN_PROGRESS/READY/VEHICLE_ASSIGNED): нетто 0 честно, но заявка после конвертации остаётся живой — держит резерв (`warehouse_stock_engine._get_reserved_map` освобождает его ТОЛЬКО сменой статуса) и её всё ещё можно отгрузить `ship_request` → второе списание тех же единиц. Обратный гард (заявка → переезд) есть, прямой (переезд → заявка) отсутствует.
3. **Гонка**: проверка `existing` по `converted_from_assembly_id` — check-then-insert без row-lock на заявке и без частичного unique-индекса.

**Факт 3 — kind-асимметрия «несвязанных» в зеркале ФФ.**
После того как переезд разрешили вязать С ОБЕИХ СТОРОН (`kind=assembly` у склада-источника), `fulfillment_service.get_overview` остался несимметричным: для `inbound` «без нашего документа» = оба слота пусты, для `assembly` — только `assembly_request_id IS NULL`. Счётчик `requests_unlinked` (строка 4388) и фильтр (4433) поэтому вечно показывают привязанное к переезду отгрузочное зеркало как несвязанное. `_collect_transfer_fact_candidates` при этом безопасен: он жёстко фильтрует `kind=inbound`, так что сторона источника авто-приём не запускает.
**How to apply:** при добавлении нового слота связи на `FulfillmentRequest` править ВСЕ три места разом — `get_overview` (фильтр + счётчик), `_transfer_candidates.linked_subq`, `link_request`. См. [[transfer-fact-autoreceive-invariants]].
