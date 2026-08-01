---
name: migfull-inbound-two-sources-invariants
description: Поставка у Натали из двух источников (InboundReceipt / StockTransfer) — что реально держит анти-дубль, где его нет, и почему отсутствие invalidate_cache здесь безвредно
metadata:
  type: project
---

Ревью 2026-07-31 (второй источник состава поставки migfull: `StockTransfer` в дополнение к `InboundReceipt`; `backend/services/migfull_portal_inbound.py`).

**Факт 1 — анти-дубль скоуплен ПО ВИДУ ДОКУМЕНТА, кросс-источникового гарда нет вообще.**
`_already_pushed` смотрит две вещи, и обе — только внутри своего вида: audit `MigfullShipmentOrder` со `status=SENT` по своей колонке FK и `FulfillmentRequest(provider=migfull, kind=inbound)` по своей колонке FK. Значит:
- id-коллизия `inbound_receipts.id` == `stock_transfers.id` безопасна (колонка выбирается по `source.kind`), но тестом это НЕ покрыто — существующий тест «независимости» вставляет audit-строку с ОБОИМИ FK = NULL и потому проходит тривиально;
- `UNCERTAIN`-исход без guid не ловится ни одной из двух веток (audit-ветка требует ровно `SENT`, FF-ветка требует guid) — единственная реальная дыра «не увидит свой дубль», унаследована от receipt-контура;
- два DDS-документа на один физический приход (переезд + вручную заведённая приёмка на склад Натали) дадут две необратимые PVB — по дизайну не проверяется.
**How to apply:** любой новый источник состава обязан приносить свою колонку FK и своё слагаемое в `_already_pushed`; проверять «а не создан ли уже документ на этот же приход» здесь нечем — это ответственность оператора.

**Факт 2 — `reports:assembly_link_anomalies` НЕ читает `kind=inbound`-заявки, поэтому отсутствие `invalidate_cache` при автосвязи безвредно.**
Все шесть блоков отчёта (`services/assembly/link_anomalies.py`) либо про `kind=assembly` (`_ff_without_assembly`, `_ff_composition_mismatch`), либо про сток/поставки. Кроме него, `FulfillmentRequest` из кэшируемых сервисов трогает только `assembly/analytics.py` — тоже сборочная сторона. `link_request` кэш гасит, а `_upsert_ff_link` — нет, и это расхождение сегодня ничего не ломает.
**Why:** соблазн записать «нарушен Iron rule 7» велик; фактического протухания нет.
**How to apply:** если появится кэшируемый отчёт по приёмкам ФФ (резерв «в приёмке», расхождение TR↔PVB) — гасить его придётся и в `_upsert_ff_link`, и в `link_request`.

**Факт 3 — автосвязь ставит `kind=inbound` + `stock_transfer_id` на переезд в ЛЮБОМ статусе, включая DRAFT.**
Ручной путь так не умеет: `_transfer_candidates` для `kind=inbound` показывает только `IN_TRANSIT`/`COMPLETED` («черновик ещё не выехал, приходовать нечего»). Опасности задвоения нет — `_collect_transfer_fact_candidates` требует `IN_TRANSIT`, а `receive_transfer_fact` идемпотентен по движениям (см. [[transfer-fact-autoreceive-invariants]]). Побочный эффект: у DRAFT-переезда состав ещё редактируется, а документ на портале уже необратим.
**How to apply:** при разборе «PVB висит на черновике TR» это не баг связи, а следствие того, что поставку у ФФ заводят заранее.

**Факт 4 — N:1 для migfull разрешён намеренно.** `link_request` явно допускает несколько ФФ-заявок на один наш документ («migfull дробит один наш документ на 2+ своих заявки»), поэтому `force_resend`, порождающий вторую `FulfillmentRequest` с тем же `stock_transfer_id`, инвариант 1:1 не нарушает. Нарушением было бы другое: `_apply_ff_source` не чистит соседний слот у НАЙДЕННОЙ строки — тогда в одной заявке окажутся два FK, а `_load_linked_doc_items` молча предпочтёт transfer приёмке (порядок веток).
