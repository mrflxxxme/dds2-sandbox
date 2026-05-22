# Known Bugs & Lessons

Активные баги и постмортемы крупных инцидентов. Полная история фиксов — в `git log`; распространённые грабли-паттерны — в `.claude/rules/learnings.md`.

## Активные проблемы
Открытые edge-cases переработки распределения «Потребность → сборка» (box-кратность, новинки). Не блокеры, но с риском данных.

- 🔴 **SUPERSAFE теряется между вкладками distribute.** Вкладка «Короб» = `package_type !== 'MONOPALLET'` (включает SUPERSAFE), но коммит шлёт `package_type=BOX`; бэкенд `_is_selected` (`SUPERSAFE != BOX`) исключает эти строки — они молча остаются в черновике. Фронт: `assembly/distribute/page.tsx`.
- 🔴 **Автосейв на distribute может вернуть закоммиченные строки** после частичного коммита → риск повторной отгрузки. Сверять автосейв с актуальным состоянием draft после commit.
- 🟠 **Приёмка 405 SKU → timeout/500.** `get_acceptance_options` (`integrations/wb_api.py`, TIMEOUT=30, chunk 5000) на ~405 баркодов таймаутит 3× → 500 → фронт «Failed to fetch». Транзиентно (утром те же 405 проходили). Фикс: чанковать по ≤150 (WB-лимит 6 req/min позволяет).
- 🟡 **Перф editMode на больших списках.** В режиме «Редактировать» рендерятся инпуты всех ячеек всех строк — на «Все (420)» лагает. Воркэраунд: фильтровать список. Кандидат на виртуализацию.

## Исправленные (последние)
Свежие крупные постмортемы; старое — в git-истории.

### Box-shortfall: хвост < короба молча выпадал из сборки (2026-05-21)
Строгая box-кратность отгружала только целые короба, а хвост < короба оседал на ФФ. На стороне ИСТОЧНИКА (`WarehouseNeedView.tsx`, boxMode-ветка сбора) SKU без единого целого короба с одного ФФ (напр. ромбсерый: 3 шт, K>3) `continue`-ил молча — не попадая даже в `skippedNoQty`. По решению пользователя правило изменено на **«любой хвост — в заявку»**: `distributeByBoxMultiple(..., looseTail=true)` (Pass 3) и источник boxMode (Pass 2) дослают остаток россыпью (полные короба сохраняются как форма). **Уточнение:** россыпь — ТОЛЬКО для обычных SKU; **новинки cold-start возвращены к строгой кратности** (`looseTail=false`, источник Pass 2 под `if(!newcomer)`) — хвост остаётся на ФФ. Файлы: `frontend-react/src/lib/utils/boxDistribution.ts`, `WarehouseNeedView.tsx`. ⚠️ WB может заворачивать некратные короба на приёмке — при жалобах вернуть строгий режим и обычным SKU.

> Отдельно (НЕ этим фиксом): у новинок без явного box_qty K берётся из машины (`FactoryOrderItem.pcs_per_box`, fallback в `box_multiplicity_service`). В draft 702 новинки сохранились ПОЛНОСТЬЮ сырыми (105/84/47/27) — `resolveSkuLevelPpb` вернул null при создании, хотя бейдж distribute показывает «кратно 10». Если повторится на свежем черновике — чинить резолв machine-K для новинок в `newcomerBoxedAlloc`.

### Warehouse Need — точность расчёта потребности (2026-05-12)
`compute_stock_need` не учитывал lead time доставки FF→WB и district-pool для WB-stock, leftover распределялся greedy. Фикс: `effective_days = supply_days + lead_time`, Hamilton-method для остатка, pooling излишков по округу. Файлы: `warehouse_need_service.py`, `routers/reports_stock.py`.

### Cold-start «фантомное распределение» (2026-05-12)
UI предлагал грузить SKU, которых на ФФ уже нет — не вычитался `asm_qty`, рассинхрон имён складов (acceptance vs orders). Фикс: `total_qty = max(0, rf_qty - asm_qty)`, канонизация имён через `ACCEPTANCE_TO_STOCK_NAME`. Файл: `cold_start_distribution_service.py`.

### Vehicle target_warehouse ↔ inbound_receipt рассинхрон (2026-05-07)
Смена `target_warehouse_id` на машине после DISPATCHED не синхронизировала `inbound_receipts.warehouse_id` — приёмка терялась. Фикс: синхронизация в `update_vehicle` (ValueError, если приёмка уже ACCEPTED). Файл: `supply_chain/vehicle_delivery.py`.
