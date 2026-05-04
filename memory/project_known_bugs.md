# Known Bugs & Lessons Learned

## Активные проблемы

### fbo_supply_service.py — 856 строк
- **Описание:** Превышает лимит 500 строк более чем вдвое
- **Риск:** Сложность поддержки, merge conflicts
- **План:** Разбить по аналогии с warehouse_service.py (crud, sync, enrich)

## Исправленные

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
