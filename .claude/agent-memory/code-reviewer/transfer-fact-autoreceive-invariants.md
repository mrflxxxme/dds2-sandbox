---
name: transfer-fact-autoreceive-invariants
description: Авто-приём перемещения по факту ФФ-приёмки (receive_transfer_fact + хук синка) — что реально идемпотентно, а что держится только на маркере; и почему transfer_transit не задваивает капитал
metadata:
  type: project
---

Разбор ревью 2026-07-28 (фича «авто-приём TR по факту приёмки migfull», кейс PVB-0000121 ← TR-21).

**Факт 1 — идемпотентность держится на ДВУХ разных механизмах, и только первый настоящий.**
`receive_transfer_fact` выводит «уже принято» из движений (`TRANSFER_IN`+`DEFECT_TRANSFER_IN`, `reference_type='TRANSFER'`, `reference_id=transfer.id`, склад = `to_warehouse_id`) → повтор с тем же фактом даёт добор 0. Это доказуемо: `defect_delta` NOT NULL server_default 0 (миграция `wh02_add_defect_stock`), поэтому `sum(quantity + defect_delta)` строк не теряет; `in_transit`/`defect_in_transit` в `WarehouseStock` пишут ТОЛЬКО `send_transfer` / `complete_transfer` / `receive_transfer_fact` — других писателей нет.
Маркер `fulfillment_requests.transfer_fact_applied_at` (миграция `ff11`) — НЕ защита от задвоения, а защита от бесконечного перечитывания факта. Его провал безопасен по деньгам, но при полном покрытии повтор упирается в `status != IN_TRANSIT` → ValueError → маркер не встаёт → кандидат вечный.
**Why:** прод-инцидент TR-21 (9 234 шт невидимы 12 дней) заставил делать порционный приём; вопрос «а не задвоит ли» решается движениями, не маркером.
**How to apply:** при правке этой цепочки проверяй сначала контур движений (он несёт корректность), маркер — только про стоимость HTTP и шум в логах. Кандидатов надо фильтровать по статусу TR, иначе любой ручной `complete_transfer` делает заявку вечным кандидатом.

**Факт 2 — `transfer_transit` в `get_unified_stock_summary` не задваивает капитал.**
`send_transfer` уже списал `quantity` на источнике и кредитовал `in_transit` на назначении, ФФ-зеркало (`FulfillmentStock`) в эту сводку не входит вовсе → `total_own + transfer_transit` инвариантен по плану перемещения. Задваивание возможно только если появится второй писатель `in_transit`.
**How to apply:** при добавлении новой колонки в «Сводные остатки» помни про позиционные таблицы фронта — `summaryRow` у `TanStackDataTable` и `renderGroupedTable` (шапка + строка «Итого» + строка группы + строка ребёнка) собраны руками из `<td>`, колонка в `cols` без парных ячеек ломает выравнивание итогов.

**Факт 3 — паритет с ручным путём неполный.** `complete_transfer` наследует кратность коробов (`box_multiplicity_service.inherit_on_transfer`, жалоба TR-20 Брянцево→Домодедово) и инвалидирует кэш; авто-путь не делает ни того, ни другого. См. [[warehouse-need-invariants]] — `reports:warehouse_need` кэшируется 300 с и читает `WarehouseStock`.
