---
name: transfer-status-mirror-invariants
description: Переезд на статусной шкале заявки (PENDING…DELIVERED + RETURNED) — что из ревью 31.07 починено, а что живо: окно авто-отгрузки и недостижимые строки таблицы переходов
metadata:
  type: project
---

Ревью 2026-07-31, ветка `feat/transfer-edit-fflink` (worktree `dds2-wt/transfer-edit`, коммиты `6abee19e..8ca1c7a6`).

**Факт 1 — ✅ ПОЧИНЕНО (проверено 2026-08-02).** `_transfer_received_map` теперь считает НЕТТО: в фильтр добавлены `TRANSFER_OUT`/`DEFECT_TRANSFER_OUT` и `reference_type IN ('TRANSFER','TRANSFER_RETURN')` + кламп `max(0, …)`. Цикл DELIVERED→RETURNED→READY→SHIPPED→DELIVERED больше не теряет приход. Историческое описание бага ниже — на случай регрессии.
~~**Факт 1 — `_transfer_received_map` НЕ нетто, и после `return_transfer` она врёт (репро зелёный).**~~
Карта «сколько получатель уже принял» (`warehouse_outbound.py:1079`) суммирует ТОЛЬКО `TRANSFER_IN`/`DEFECT_TRANSFER_IN` c `reference_type='TRANSFER'`. `return_transfer` откатывает зачисленное движением `TRANSFER_OUT` с `reference_type='TRANSFER_RETURN'` (осознанно, строка 1719) — карта его не видит и остаётся «принято N» навсегда. Из неё считают бюджет прихода `complete_transfer` (1130) и `receive_transfer_fact` (1280), поэтому цикл DELIVERED→RETURNED→READY→SHIPPED→DELIVERED списывает со склада-забора и НЕ приходует получателю: репро (в контейнере, копия дерева в /tmp/work) даёт `src −10, dst.quantity 0, dst.in_transit 10`, статус DELIVERED. Частичный вариант (факт 4 → возврат → переотправка → факт 10) даёт `dst 6, transit 4`.
**Why:** дыра появилась вместе с двумя новыми вещами разом — порционным бюджетом в `complete_transfer` (фикс двойного зачисления) и самим `return_transfer`; по отдельности каждая корректна.
**How to apply:** при любой правке этой цепочки проверяй netto, а не «сумму приходов»: либо добавь в карту `TRANSFER_OUT`/`DEFECT_TRANSFER_OUT` с `reference_type IN ('TRANSFER','TRANSFER_RETURN')`, либо скоупь её текущим кругом (`created_at >= transfer.shipped_at`). Тот же перекос в `_attach_transfer_labels` (423) — бейдж «принято X из Y» и `received_units`. Второй возврат после переотправки пытается снять с получателя устаревшее `taken` → либо 400 «Insufficient stock», либо съедает единицы чужих документов. См. [[transfer-fact-autoreceive-invariants]].

**Факт 2 — «авто-отгрузка строго из VEHICLE_ASSIGNED» проверяется ОДИН раз и не там, где списывает.**
`_collect_transfer_ship_candidates` (`fulfillment_service.py:2905`) фильтрует статус под синк-транзакцией, а сам `send_transfer` вызывается после commit (1099) и пускает ещё и READY (карв-аут `_TRANSFER_SEND_FROM`, 869). Между сбором и отгрузкой лежит вся HTTP-работа авто-приёмок и авто-шипа сборок, так что «снял машину» в этом окне не спасает: сток спишется, а забор не родится (`_create_transfer_pickup` вернёт None — `unassign` вычистил всю логистику).
**How to apply:** авто-путям нужен явный параметр (`allowed_from`/`require_vehicle`) в `send_transfer` — зеркало `allow_gazelka_ready` у `ship_request`, а не общий фрозенсет.

**Факт 3 — таблица переходов шире сервисов, и фронт шире/уже её же.**
`TRANSFER_TRANSITIONS` разрешает SHIPPED→CANCELLED и RETURNED→CANCELLED, но `cancel_transfer` рубит всё вне `TRANSFER_PRE_SHIP_STATUSES` — записи в таблице недостижимы, и первый же новый вызывающий, доверившийся `_check_transfer_transition`, отменит переезд поверх списанного стока. Обратный перекос СНЯТ: `canMarkTransferReady` теперь включает RETURNED, кнопка «Переотправить» живая — второй круг переезда достижим с UI, и именно на нём ломается забор (см. [[transfer-pickup-money-carrier-invariants]], факт 1).
