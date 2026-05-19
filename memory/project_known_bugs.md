# Known Bugs & Lessons

Активные баги и постмортемы крупных инцидентов. Полная история фиксов — в `git log`; распространённые грабли-паттерны — в `.claude/rules/learnings.md`.

## Активные проблемы
_Нет активных._

## Исправленные (последние)
Свежие крупные постмортемы; старое — в git-истории.

### Warehouse Need — точность расчёта потребности (2026-05-12)
`compute_stock_need` не учитывал lead time доставки FF→WB и district-pool для WB-stock, leftover распределялся greedy. Фикс: `effective_days = supply_days + lead_time`, Hamilton-method для остатка, pooling излишков по округу. Файлы: `warehouse_need_service.py`, `routers/reports_stock.py`.

### Cold-start «фантомное распределение» (2026-05-12)
UI предлагал грузить SKU, которых на ФФ уже нет — не вычитался `asm_qty`, рассинхрон имён складов (acceptance vs orders). Фикс: `total_qty = max(0, rf_qty - asm_qty)`, канонизация имён через `ACCEPTANCE_TO_STOCK_NAME`. Файл: `cold_start_distribution_service.py`.

### Vehicle target_warehouse ↔ inbound_receipt рассинхрон (2026-05-07)
Смена `target_warehouse_id` на машине после DISPATCHED не синхронизировала `inbound_receipts.warehouse_id` — приёмка терялась. Фикс: синхронизация в `update_vehicle` (ValueError, если приёмка уже ACCEPTED). Файл: `supply_chain/vehicle_delivery.py`.
