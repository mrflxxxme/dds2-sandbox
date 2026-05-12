# Known Bugs & Lessons Learned

## Активные проблемы

_Нет активных. Последняя проверка: 2026-05-07._

## Исправленные

### Cold-start «фантомное распределение»: предлагает грузить шт, которых на ФФ уже нет (project default, BLACKOUT_200х260_кофейный + BZ-YY1106/30х60-30ШТ)
- **Исправлено:** 2026-05-12
- **Описание:** На вкладке «Новинки» (`/warehouse/analytics`) для SKU с `rf_qty=144` + `asm_qty=144` (все 144 уже едут в 9 разных WB-складов) UI предлагал «Распределить: 84» — будто на ФФ ещё 84 шт. Причина двойная:
  1. `compute_cold_start_table` использовал `total_qty = sku["rf_qty"]` без вычета `asm_qty` → `distribute_multi` размазывал «фантом» по bench-складам.
  2. Per-warehouse safety net `max(0, alloc - asm[wh])` промахивался из-за рассогласования имён: `assembly_requests.wb_warehouse_name_manual` хранит «Краснодар (Тихорецкая)», «Новосемейкино», «Склад Шушары», а bench-traffic из `wb_orders.warehouse_name` каноничные: «Краснодар», «Самара (Новосемейкино)», «СПБ Шушары».
  - Прецедент BZ-YY1106: rf=550, asm=230 (10 разных целевых складов), система предлагала 205 → должно быть 165.
- **Фикс:**
  - `cold_start_distribution_service.compute_cold_start_table`: `total_qty = max(0, sku["rf_qty"] - sku["asm_qty"])`.
  - `compute_distribution`: `active_asm` поднят выше расчёта `total_qty`; дефолт = `rf_qty - sum(active_asm)`.
  - `_canonicalize_asm_warehouse()` через `ACCEPTANCE_TO_STOCK_NAME` в `fetch_active_assemblies_for_sku` — per-warehouse subtraction срабатывает для всех вариантов имён.
  - UI: разбивка ФФ по складам (новые колонки Ханди/натали/wms/...), `asm_by_warehouse` в response — зелёная подстрока «🚚 N» в WB-ячейке («куда уже едет»), tooltip «Уже в сборке: N шт». Приоритет приёмки короб → моно → super (короб дешевле). Сортировка + ИТОГО.
- **Тесты:** 4 regression в `test_cold_start_distribution.py` (BLACKOUT-сценарий с 9 целевыми складами + BZ-YY1106 partial assembly).
- **Файлы:** `backend/services/cold_start_distribution_service.py`, `backend/schemas/cold_start.py`, `frontend-react/src/types/api.ts`, `frontend-react/src/app/(main)/p/[slug]/warehouse/analytics/components/WarehouseNeedView.tsx`, `tests/test_cold_start_distribution.py`.

### Vehicle target_warehouse_id ↔ inbound_receipt warehouse_id рассинхрон (project default, V-0001)
- **Исправлено:** 2026-05-07
- **Описание:** Поле `target_warehouse_id` есть в `always_editable` для CostOrder (vehicle) — можно править даже после DISPATCHED. При DISPATCHED-переходе `_create_inbound_from_vehicle` создаёт `InboundReceipt(warehouse_id=vehicle.target_warehouse_id)`. После — если юзер меняет `target_warehouse_id` на машине, привязанная приёмка остаётся на старом складе. Vehicle висит в «Ожидаемых поставках» нового склада (фильтр по `cost_orders.target_warehouse_id`), но приёмка не показывается в табе «Приёмки» (фильтр по `inbound_receipts.warehouse_id`), кнопка «Принять» недоступна.
- **Фикс:**
  - `backend/services/supply_chain/vehicle_delivery.py::update_vehicle`: при изменении `target_warehouse_id` синхронизирует `inbound_receipts.warehouse_id`. Если приёмка `ACCEPTED` — `ValueError` (товар уже в stock_movements).
  - `scripts/fix_inbound_warehouse_drift.py`: одноразовый recovery-скрипт. Безопасен — ACCEPTED не трогает.
  - Тесты: `test_change_target_warehouse_syncs_inbound_receipt`, `test_change_target_warehouse_blocked_when_receipt_accepted` в `test_vehicle_delivery.py`.

### WB stocks UPSERT падал с CardinalityViolationError (project Вяткин)
- **Исправлено:** 2026-05-04 (commit 01a9cd9)
- **Описание:** `services/warehouse_stock_service.sync_warehouse_stocks` делал bulk UPSERT в `wb_warehouse_stocks` с unique key `(project_id, nm_id, warehouse_name)`. WB API `/supplier/stocks` отдаёт строки per barcode/size — для одного (nm_id, warehouseName) приходит несколько записей. Postgres падал «ON CONFLICT DO UPDATE command cannot affect row a second time». В результате у проекта 15 (Вяткин) `wb_warehouse_stocks` оставался пустой, страницы `/warehouse/wb-stocks` и группировка по категориям в «Единых остатках» не работали.
- **Фикс:** дедуп в Python ДО executemany — агрегировать в `dict[(nm_id, wh_name)]` с суммированием `quantity/quantity_full/in_way_*` и first-non-empty для `vendor_code/subject/brand`. Снапшоты в `wb_stock_snapshots` остаются per-barcode (там PK=id).
- **Файл:** `backend/services/warehouse_stock_service.py`

### Assembly «Без склада» — race с FBO enrich-job 3h
- **Исправлено:** 2026-05-04 (commit 6927649)
- **Описание:** Если ASM создавалась сразу после list-sync FBO supply (до enrich-job), `supply.warehouse_name=NULL` → `wb_warehouse_name_manual` копировался NULL и оставался пустым до следующего enrich-цикла, в листе логиста заявка висела «Без склада».
- **Фикс:** `_try_force_enrich_supply` (best-effort, try/except) в `assembly/crud.py` create/update и `assembly/analytics.refresh_from_fbo` (`force=True` ловит смену склада в кабинете WB). Inline-editor «Сдача WB» откатили — склад только pull from WB API.
- **Файл:** `backend/services/assembly/crud.py`, `backend/services/assembly/analytics.py`, frontend logistics page

### Cost lookup чувствителен к регистру (PALATKA_зеленая vs palatka_зеленая)
- **Исправлено:** 2026-05-04 (commit b2f6b4a)
- **Описание:** Артикулы в `avg_costs` и в источнике сравнения шли в разных регистрах → тихий KeyError, нулевая себестоимость в отчётах.
- **Фикс:** case-insensitive lookup (нормализация ключа перед match). Дополняет существующее правило P26 в learnings.md о case-sensitive JOINs.

### Supply-chain: soft-deleted FOI протекали в get_available_*
- **Исправлено:** 2026-05-04 (commits b36b7bc, 8f97da7)
- **Описание:** В `selectinload(FactoryOrder.items)` и `get_available_*` запросах не было фильтра `is_deleted == False` → удалённые items участвовали в расчётах доступного к привязке остатка.
- **Фикс:** добавили фильтр soft-delete в `selectinload` через `lambda` и в base-query сервиса. Iron rule №2 — каждый запрос к SoftDelete-модели должен фильтровать `is_deleted`.

### Локальная БД: 74k тестовых проектов, worker startup >5 мин
- **Исправлено:** 2026-05-04
- **Описание:** Тестовые фикстуры conftest.py (project, other_project) и 20+
  файлов делали POST /api/v1/projects + commit без teardown. За 2 месяца
  накопилось 74596 проектов, 524 MB category_ref, 2.2M INSERT в lifespan
  seed_default_categories через PgBouncer → worker startup 5+ мин →
  healthcheck failed → autoheal restart loop.
- **Фикс:**
  - `tests/conftest.py`: project/other_project теперь yield + teardown
    (DELETE через session_replication_role=replica)
  - `tests/conftest.py`: pytest_sessionfinish safety-net hook удаляет
    остальной мусор после сессии (whitelist id=4 Default, id=15 вяткин)
  - `backend/seeds/default_categories.py`: skip уже засиженных проектов
    (один SELECT GROUP BY вместо N×30 INSERT)
  - `Dockerfile.backend`: mkdir + chown /data/ai_memory
  - `docker-compose.yml`: named volume ai_memory:
  - `scripts/cleanup_test_projects.sh`: reusable идемпотентный скрипт
- **Результат:** DB 1325 MB → 630 MB, worker startup >5 мин → ~3 сек

### WB ad upsert затирал реальные значения нулями (project Вяткин)
- **Исправлено:** 2026-05-04
- **Описание:** В `services/funnel/sync.py` ON CONFLICT UPDATE присваивал
  `update_fields["adv_*"] = stmt.excluded.adv_*` напрямую. При partial fetch_ad_stats
  (часть chunks 429-throttled или time-budget exceeded) для дня day_ads был непустой
  (has_ad_data=True), но для многих nm_ids ad={} → excluded.adv_sum=0. UPSERT
  переписывал ранее загруженные реальные значения нулями. Симптом: `wb_funnel_daily.adv_sum=0`
  за 30.04, 01.05, 02.05 у Вяткина при наличии данных в `wb_ad_campaign_daily`.
- **Фикс:** `update_fields["adv_*"] = sa_func.greatest(stmt.excluded.adv_*, WbFunnelDaily.adv_*)`
  — реальное (>0) значение никогда не перезаписывается нулём из частичного синка.
- **Файл:** `backend/services/funnel/sync.py`, regression-guard `tests/test_funnel_adv_upsert.py`
- **Trade-off:** если кампания легитимно ушла в 0, оставим завышенное прежнее значение
  (приемлемо для дашборда расходов на рекламу — лучше stale-overstate, чем тихий зануляющий
  upsert). При необходимости — `batch_resync_ads` принудительно перезапишет через прямой UPDATE.

### is_paid не сбрасывался при удалении fact_link
- **Исправлено:** 2026-03-16
- **Описание:** При soft-delete PaymentFactLink поле is_paid на PlannedPayment не пересчитывалось
- **Файл:** services/planning/fact_links.py

### Soft delete fact_links — paid_amount не фильтровал is_deleted
- **Исправлено:** 2026-03-16
- **Описание:** Запрос суммы paid_amount не исключал удалённые fact_links
- **Файл:** services/planning/fact_links.py

### sync_log оставался в RUNNING при ошибке (wb_finance, fbo_supplies)
- **Исправлено:** 2026-03-23
- **Описание:** wb_finance.py и fbo_supplies.py не имели finally блока для обновления sync_log
- **Файл:** scheduler/jobs/wb_finance.py, scheduler/jobs/fbo_supplies.py

### ilike() без escape parameter
- **Исправлено:** 2026-03-23
- **Описание:** funnel/product_trends.py и funnel/queries.py экранировали символы, но не передавали escape="\\" в ilike()
- **Файл:** services/funnel/product_trends.py, services/funnel/queries.py

### TELEGRAM_WEBHOOK_SECRET пустая строка bypass
- **Исправлено:** 2026-03-23
- **Описание:** При пустом TELEGRAM_WEBHOOK_SECRET любой запрос с пустым заголовком проходил валидацию
- **Файл:** routers/telegram_webhook.py
