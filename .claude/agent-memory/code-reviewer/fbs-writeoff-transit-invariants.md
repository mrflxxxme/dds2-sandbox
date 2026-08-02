---
name: fbs-writeoff-transit-invariants
description: FBS «надёжность списания» — три неочевидных ловушки: blacklist wbStatus в in_delivery (ready_for_pickup), двойное назначение written_off_at (бэкфилл), lifetime-нетто ff_fbs с глухим капом
metadata:
  type: project
---

Ревью фичи «надёжность списания FBS» (`d185947a`/`c61496e3`/`b4a92d0d`, ветка `feat/main-tree-port`, 30.07.2026). Три ловушки, которые не видны при чтении диффа в изоляции.

**1. `in_delivery_condition()` — blacklist, а не whitelist по `wbStatus`.**
`complete` минус `{sold, defect}` (доставлено) минус `{sorted}` минус отмены. Всё ОСТАЛЬНОЕ читается как «ещё едет к СЦ». Но WB отдаёт и пост-сортировочные `ready_for_pickup` («прибыло в ПВЗ») и `postponed_delivery` — они есть в `WB_STATUS_LABEL` фронта, но не в `FBS_WB_SORTED_STATUSES`/`FBS_WB_DELIVERED_STATUSES` бэка. Заказ, доехавший до ПВЗ, ВЫХОДИТ из «Отсортировано» и ВОЗВРАЩАЕТСЯ в «Ещё в доставке» — движение назад по воронке.
**Why:** на этом же условии построен новый алярм `in_delivery_stuck` («передано ≥2 дн, СЦ не принял»): всё, что лежит в ПВЗ и ждёт покупателя, попадает в ⚠-счётчик.
**How to apply:** любую новую фазу/алярм поверх `wbStatus` строить БЕЛЫМ списком до-сортировочных статусов (`waiting`, `sent_to_carrier`, `accepted_by_carrier`, NULL), а не «всё, кроме известного».

**2. `WbFbsOrder.written_off_at` — поле с ДВУМЯ смыслами.**
Обычно это факт списания из ledger'а (≈ момент передачи поставки), но `backfill_orders_history` ставит `written_off_at = sync_ts` (время прогона бэкфилла!) историческим заданиям БЕЗ движений — защита от повторного вычета старых продаж (`/api/v3/orders` не отдаёт `supplierStatus`, поэтому история ложится как `new`).
**Why:** новый якорь транзита `COALESCE(supply.scan_dt, supply.closed_at, order.written_off_at)` на фолбэке берёт синтетическую дату; если бэкфилленное задание получит `supplier_status=complete` при пустом `wb_status` и без строки поставки в зеркале — оно станет «зависшим» с возрастом «сколько дней назад прогнали бэкфилл».
**How to apply:** не использовать `written_off_at` как временную метку события без оговорки про бэкфилл; предпочитать `WbFbsSupply.scan_dt/closed_at`.

**3. `ff_fbs` — lifetime-нетто против снимка зеркала, кап `min()` глушит расхождение молча.**
`_fbs_shipped_multi` (`fulfillment_service`) суммирует `-SUM(quantity)` по `reference_type='FBS_ORDER'` ЗА ВСЮ ИСТОРИЮ склада и вычитает из `ff_good` снимка провайдера. Корректно только пока НИ ОДИН провайдер никогда не снимал остаток под FBS (сверка 29.07.2026). Первая же инвентаризация/пересчёт у ФФ делает вычет двойным, а `min(нетто, ff_good)` превращает перебор в тихий ноль → расхождение переворачивается в ложное «у нас больше». Клип нигде не логируется и не отдаётся полем; промах по ключу (barcode движения = WB sku, ключ ячейки = `base_barcode|barcode` зеркала) тоже молча `continue`.
**How to apply:** при правках `ff_fbs` первым делом добавлять наблюдаемость клипа/промаха; помнить, что суточный снимок расхождения (`stock_mismatch_history.snapshot_project`) колонки `ff_fbs` НЕ хранит — ряд «Динамика расхождения» ступенькой ломается в день деплоя.

Связано: [[warehouse-need-invariants]], [[transfer-fact-autoreceive-invariants]], [[sku-key-whitespace-classes]].
