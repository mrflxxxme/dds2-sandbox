---
name: transfer-pickup-money-carrier-invariants
description: Забор переезда (OutboundShipment.stock_transfer_id) как единственный носитель денег — где upsert теряет оплаченный круг, где гейт «Отправить» обходится и где переезд под Газелькой встаёт намертво
metadata:
  type: project
---

Ревью 2026-08-02, ветка `feat/main-tree-port`, диапазон `b6625f37..a4c4ca8b` (гейт отправки, ретро-логистика, Газелька возит переезды).

**Факт 1 — «один переезд = один забор» держится партиальным уникальным, а переотправка теперь СУЩЕСТВУЕТ.**
Комментарий у `uq_outbound_shipments_stock_transfer` (`models/warehouse.py:369`) обосновывает уникальность фразой «переотправки у переезда нет» — это уже неправда: `TRANSFER_TRANSITIONS[RETURNED] = {READY, CLOSED}`, `mark_transfer_ready` её пускает, фронт даёт кнопку «Переотправить» (`canMarkTransferReady` включает RETURNED). Поэтому `_create_transfer_pickup` из INSERT стал upsert'ом и на втором круге ПЕРЕЗАПИСЫВАЕТ снимок первого: `pickup_cost`, `shipped_date`, позиции. Первый круг мог быть уже оплачен (`PaymentRequestShipment` → `list_shippable` метит забор «занят»), поэтому второй круг ни оплатить, ни увидеть в сверке нельзя.
**Why:** upsert вводили ради идемпотентности ретро-оформления (`set_transfer_logistics` зовут повторно осознанно), про второй круг забыли.
**How to apply:** если ветку когда-нибудь чинят — либо `attempt_no` у забора (как у заявки) и уникальность по паре, либо софт-делит старого забора при переотправке. Просто «не перезаписывать» нельзя: ретро-оформление обязано обновлять снимок.

**Факт 2 — гейт «Отправить без логистики» действует ТОЛЬКО на ручной путь.**
`send_transfer` отбивает голый READY, если `allowed_from is None and not allow_no_logistics` (`warehouse_outbound.py:1032`). Значит любой авто-путь, передающий `allowed_from`, гейт не видит: ФФ-авто-шип безопасен (сужен до VEHICLE_ASSIGNED, где логистика есть по определению), а вот Газелька зовёт `send_transfer(allowed_from={READY, VEHICLE_ASSIGNED})` (`gazelka_service.py:1535`) — и когда `_transfer_vehicle_payload` вернул None (у портала в маршруте только марка ТС или только ФИО водителя: `has_vehicle = car_number or car_model or first or last`, а payload требует `car_number or carrier`), сток списывается, а забора нет. Это ровно TR-32, только через агрегатор; `_apply_transfer_cost` такой переезд не чинит (он требует существующий забор).
**How to apply:** любому новому авто-вызывающему `send_transfer` проверяй, есть ли на документе логистика, ИЛИ передавай `allow_no_logistics` осознанно. Гейт — не защита кода, а защита ручной кнопки.

**Факт 3 — гард Газельки на переезде жёстче, чем у заявки, и создаёт тупик READY.**
`_deny_if_gazelka_leads` (по заказам SENT/MATCHED) рубит `assign_vehicle_transfer` и `unassign_vehicle_transfer`; `unmatch_order` удаляет ТОЛЬКО MATCHED-строки, наш собственный SENT остаётся навсегда. Плюс новый гейт закрыл ручную отправку голого READY, а карточка гасит инлайн-правку логистики при `via_gazelka`. Итог: если портал не довёл заказ до кода 31 (или не отдал реквизиты), переезд не отправить ничем — у заявки в этом месте есть второй выход (авто-шип по приёмке WB с `allow_gazelka_ready`), у переезда ФФ-авто-шип требует VEHICLE_ASSIGNED. Единственный выход — `cancel_transfer` и завести переезд заново.

**Факт 3.5 — что именно ломается в ОТЧЁТАХ на втором круге (детализация Факта 1).**
`transfer_logistics.get_transfer_logistics_report` берёт период по `OutboundShipment.shipped_date`, а upsert пересчитывает его как `shipped_at.date()` нового круга (`return_transfer` обнуляет `shipped_at`, поэтому у RETURNED фолбэк — `pickup_date`, иначе `date.today()`). Итог перезаписи: расход первого круга ИСЧЕЗАЕТ из своего месяца и появляется в месяце второго, `transfers_count` считает одну перевозку вместо двух, а `_paid_condition` (EXISTS заявки на оплату в APPROVED/DRAFT_CREATED/PAID) объявляет второй круг оплаченным — он же не попадает в рабочий список `list_shippable` (`already_requested=True`). Кэша у отчёта нет, так что расхождение видно сразу и навсегда.

**Факт 4 — `_write_transfer_logistics` присваивает БЕЗУСЛОВНО, кроме трёх полей.**
`vehicle_info/brand/driver_phone/pickup_date/pickup_time_slot/pickup_cost/delivery_date/logistics_by_warehouse` перетираются значением payload всегда; трёхзначны только `pallets_count`/`pallet_weight_kg`/`shipped_as_boxes` (None = «не трогать») и `counterparty_id` (меняется, только если `_resolve_carrier` вернул id, а он требует ИНН). Поэтому любой клиент обязан слать ПОЛНЫЙ снимок — и обязан помнить, что `date | None` не принимает `''` (пустая строка = 422, проверено в контейнере), а `pickup_cost ?? 0` записывает «везли бесплатно» вместо «поле пустое» (и блокирует газельный бэкфилл тарифа: `_apply_transfer_cost` пишет только в `pickup_cost IS NULL`).
Самый опасный такой клиент — не человек, а СИНК: `_transfer_vehicle_payload` собирает `TransferAssignVehicle` из портала и кладёт `pickup_cost=None`, когда тариф ещё не проставлен (`_parse_rate` не распарсил `rate`), а `pickup_time_slot`/`logistics_by_warehouse` не кладёт вовсе. Каждый прогон синка поэтому стирает уже известную стоимость и даты переезда — ассемблейный близнец `assembly.status.apply_gazelka_logistics` от этого защищён построчными `if value is not None`, переездный путь — нет.
См. [[transfer-status-mirror-invariants]], [[transfer-vehicle-conversion-invariants]].
