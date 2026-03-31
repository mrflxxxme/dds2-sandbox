# Domain: WB Integration (API, Funnel, Finance, Sync)

## Ownership
Файлы этого домена:
- `integrations/wb_api.py` — WB Statistics/Content API клиент
- `integrations/resilience.py` — CircuitBreaker + retry_with_backoff
- `services/funnel/wb_api_client.py` — WB API обёртка для funnel
- `services/funnel/wb_funnel_api.py` — Funnel Statistics API
- `services/funnel/wb_advertising_api.py` — Advertising API
- `services/funnel/wb_supplier_api.py` — Supplier API (coefficients, stocks)
- `services/funnel/sync.py` — оркестратор синхронизации
- `services/funnel/analysis.py` — расчёт метрик воронки
- `services/funnel/backfill.py` — загрузка исторических данных
- `services/funnel/cost_overrides.py` — ручные себестоимости
- `services/funnel/queries.py` — SQL-запросы для воронки
- `services/wb_finance_sync.py` — синхронизация WB Finance Report
- `services/integrations_service.py` — управление API-ключами
- `services/warehouse_stock_service.py` — остатки на складах WB
- `services/stock_forecast_service.py` — прогноз запасов
- `scheduler/jobs/funnel.py` — фоновая синхронизация воронки
- `scheduler/jobs/wb_finance.py` — фоновая синхронизация финансов
- `routers/integrations.py` — HTTP endpoints интеграций
- `routers/funnel.py` — HTTP endpoints воронки
- `routers/reports_stock.py` — складские отчёты
- `models/integrations.py` — IntegrationKey, SyncLog, WbFunnelDaily, WbCostOverride, WbWarehouseStock
- `models/wb_finance.py` — WbFinanceRow, WbFinanceSyncLog
- `models/wb_order_cancel.py` — WbOrderCancelDaily (статистика отмен)
- `models/wb_tariff.py` — WbTariff (коэффициенты WB)
- `services/tariff_service.py` — управление тарифами WB
- `services/wb_cancel_sync.py` — синхронизация статистики отмен
- `services/funnel/anomalies.py` — детекция аномалий в рекламе
- `services/funnel/ad_campaigns_service.py` — управление рекламными кампаниями
- `services/funnel/unified_sync.py` — унифицированная синхронизация рекламы
- `services/funnel/product_trends.py` — тренды по товарам
- `services/funnel/capital.py` — расчёт оборотного капитала
- `services/funnel/bdr_rates.py` — ставки для БДР
- `services/funnel/stock_helpers.py` — хелперы остатков
- `scheduler/jobs/wb_stocks.py` — синхронизация остатков WB
- `utils/crypto.py` — шифрование API-ключей

## Tables
- `integration_keys` — зашифрованные API-ключи (service: wb/ozon)
- `sync_log` — лог синхронизаций (status: RUNNING/OK/ERROR/STALE/TIMEOUT)
- `wb_funnel_daily` — ежедневные данные воронки по nmID
- `wb_cost_override` — ручные себестоимости
- `wb_warehouse_stocks` — остатки по складам
- `wb_finance_rows` — кэш финансового отчёта WB
- `wb_finance_sync_log` — лог синхронизации финансов
- `wb_order_cancel_daily` — ежедневная статистика отмен
- `wb_tariffs` — коэффициенты WB (SoftDeleteMixin)

## Business Rules

### API Keys
- Шифрование: AES-256 Fernet (backend/utils/crypto.py)
- LEGACY fallback для старых ключей — НЕ УДАЛЯТЬ
- Типы ключей: analytics (воронка), adv (реклама), content (карточки)

### Rate Limiting
- 429 → RateLimitError (respect Retry-After), НЕ считается Circuit Breaker failure
- Circuit Breaker → ТОЛЬКО для 500-504 (5 failures → 120s cooldown)
- Retry: max 3 attempts, exponential backoff (2s, 4s, 8s)

### Sync
- Scheduler: ТОЛЬКО в worker container (DDS_ROLE=worker)
- sync_log: ВСЕГДА обновлять в finally (НИКОГДА не оставлять RUNNING)
- При старте: stale cleanup — RUNNING > 10 min → STALE
- Partial data: при ошибке сохранять уже загруженные дни
- **CancelledError:** все sync jobs MUST ловить `asyncio.CancelledError` (не наследуется от Exception) — иначе error_msg = null в sync_log
- **Monitoring:** endpoint `/monitoring/overview` работает в api-контейнере, scheduler статус определяется по sync_log (не in-memory)

### Worker Lifecycle
- `stop_grace_period: 60s` — worker получает 60 секунд на graceful shutdown при деплое
- `stop_scheduler(wait=True)` — APScheduler ждёт завершения текущих задач
- Healthcheck проверяет scheduler (не только HTTP) — docker рестартит если scheduler завис
- При SIGKILL (exit 137) sync_log остаётся RUNNING → stale cleanup при следующем старте

### Funnel Metrics
- Воронка: transitions → add_to_cart → orders_count → orders_sum → buyout_count
- Реклама: ad_spend, ad_views, ad_clicks → CTR, CPC, CPM
- Unit-экономика: revenue - cost - ads - tax = profit per unit

## Known Issues & Gotchas
- **Глобальный CircuitBreaker:** один `_wb_circuit` на весь процесс — проблемы одного клиента блокируют всех
- **Дублирование кода:** wb_funnel_api, wb_advertising_api, wb_supplier_api — одинаковая retry-логика (нужно использовать resilience.retry_with_backoff)
- **TOCTOU в scheduler locks:** _backfill_locks проверка locked() + acquire не атомарны
- **wb_finance_sync:** partial commit — если sync fails mid-page, часть данных committed
- **Float в cost_price:** funnel/sync.py использует float division вместо Decimal
- ~~**CancelledError не ловился:**~~ исправлено — все sync jobs теперь ловят asyncio.CancelledError (df39300)
- ~~**Scheduler healthcheck:**~~ исправлено — worker healthcheck проверяет scheduler, не только HTTP (df39300)

## Dependencies
- `nomenclature` — для себестоимости в воронке
- `transactions` — WB-выплаты матчатся с транзакциями
- `reports (wb_bdr, opiu)` — строятся на wb_finance_rows

## Cache Invalidation
После WB sync: `await invalidate_cache("reports:opiu")`, `await invalidate_cache("reports:wb_bdr")`, `await invalidate_cache("reports:dashboard")`
НИКОГДА не сбрасывать все ключи разом — worker starvation!
