# DOMAIN_WB — WB Integration (API, Funnel, Finance, Sync)

Интеграция с Wildberries: HTTP-клиенты, resilience, синхронизация воронки/рекламы/финансов, управление API-ключами.

## Таблицы
| Модель | Назначение | Примечание |
|--------|------------|------------|
| `IntegrationKey` (`integration_keys`) | Зашифрованные API-ключи | service: wb/ozon; типы: analytics/adv/content |
| `SyncLog` (`sync_log`) | Лог синхронизаций | status: RUNNING/OK/ERROR/STALE/TIMEOUT |
| `WbFunnelDaily` (`wb_funnel_daily`) | Ежедневная воронка по nmID | |
| `WbCostOverride` (`wb_cost_override`) | Ручные себестоимости | |
| `WbWarehouseStock` (`wb_warehouse_stocks`) | Остатки по складам WB | |
| `WbFinanceRow` (`wb_finance_rows`) | Кэш финансового отчёта WB | |
| `WbFinanceSyncLog` (`wb_finance_sync_log`) | Лог синхронизации финансов | |
| `WbOrderCancelDaily` (`wb_order_cancel_daily`) | Ежедневная статистика отмен | |
| `WbTariff` (`wb_tariffs`) | Коэффициенты WB | SoftDeleteMixin |

## Бизнес-правила

### API-ключи
- Шифрование AES-256 Fernet (`utils/crypto.py`). LEGACY fallback для старых ключей — **не удалять** (требует data-migration).
- Типы ключей: analytics (воронка), adv (реклама), content (карточки).

### Resilience
- `429` → `RateLimitError`, respect `Retry-After`. **НЕ** считается failure для Circuit Breaker.
- Circuit Breaker — **per-project** (`CircuitBreakerRegistry`, `_wb_circuits`), только для 500–504: 5 failures → 120s cooldown.
- Retry: max 3 attempts, exponential backoff (2s, 4s, 8s).

### Sync
- Scheduler работает **только** в worker-контейнере (`DDS_ROLE=worker`).
- `sync_log` — **всегда** обновлять в `finally`, никогда не оставлять `RUNNING`.
- На старте worker — stale cleanup: `RUNNING` > 10 мин → `STALE`. При SIGKILL (exit 137) запись остаётся `RUNNING` и чинится этим cleanup.
- Partial data: при ошибке mid-sync сохранять уже загруженные дни.
- Все sync jobs **обязаны** ловить `asyncio.CancelledError` (не наследуется от `Exception`) — иначе `error_msg=null` в `sync_log`.
- Monitoring: статус scheduler определяется по `sync_log` (не in-memory) — `/monitoring/overview` работает в api-контейнере.

### Расписание WB Finance (MSK, tue–sun)
WB публикует финотчёт за прошлый день к 03–05 MSK. Прогоны: `05:00` (ранний, успевает к утреннему дайджесту), `08:00` (страховка от поздней публикации), `14:00` (добор), `catchup` на старте worker. `misfire_grace_time=3600` — задача не теряется при рестарте worker в пределах часа.

### Worker lifecycle
- `stop_grace_period: 60s` + `stop_scheduler(wait=True)` — APScheduler ждёт завершения текущих задач при деплое.
- Healthcheck проверяет scheduler (не только HTTP) — docker рестартит зависший scheduler.

### Воронка и метрики
- Воронка: transitions → add_to_cart → orders_count → orders_sum → buyout_count.
- Реклама: ad_spend, ad_views, ad_clicks → CTR, CPC, CPM.
- Unit-экономика: `revenue − cost − ads − tax = profit` per unit.

### Локализация (ИЛ + ИРП) — с 23.03.2026
- Источник: WB Analytics v3 `sales-funnel/products` → `localizationPercent`, `timeToReady`.
- Хранение: `wb_funnel_daily.localization_percent` + `time_to_ready_minutes`.
- Детали расчёта — `DOMAIN_LOCALIZATION`.

### Review-deduction enrichment (списания за отзыв)
WB возвращает строки удержаний за отзывы с пустыми товарными полями (`nm_id=0`, `sa_name=''`); идентификатор товара зашит только в тексте `bonus_type_name` («Списание за отзыв XXX: акция №N, товар N»).
- `wb_finance_helpers.parse_review_target` — регэксп извлекает nm_id из текста.
- `wb_finance_sync._upsert_batch` подтягивает `(brand, subject, sa_name)` через `_load_nm_meta` (DISTINCT ON по свежей продажной строке `wb_finance_rows` того же товара) — без зависимости от `nomenclature` (он у многих проектов пуст).
- Результат: BDR/OPIU/Cost-DNA разносят «Прочие удержания» по бренду/категории/артикулу; сумма не меняется, появляется только разрез.
- Бэкфил истории: `python -m scripts.backfill_review_deductions`.
- При новом типе удержаний с nm_id в тексте — расширить `_REVIEW_TARGET_RE` или добавить аналогичный helper.

### Cache invalidation
После WB sync инвалидировать **точечно**: `reports:opiu`, `reports:wb_bdr`, `reports:dashboard`. Никогда не сбрасывать все ключи разом — worker starvation.

## Зависимости
- `DOMAIN_TRANSACTIONS` — WB-выплаты матчатся с транзакциями.
- `DOMAIN_REPORTS` — БДР/ОПИУ строятся на `wb_finance_rows`.
- `DOMAIN_COST` — `nomenclature` для себестоимости в воронке.
- `DOMAIN_LOCALIZATION` — расчёт ИЛ/ИРП на полях `wb_funnel_daily`.

## Грабли
- **Дублирование retry-логики** — `wb_funnel_api`, `wb_advertising_api`, `wb_supplier_api` повторяют одинаковую retry-логику; следует использовать `resilience.retry_with_backoff`.
- **TOCTOU в scheduler locks** — `_backfill_locks`: проверка `locked()` + `acquire` не атомарны.
- **`wb_finance_sync` partial commit** — при падении mid-page часть данных уже закоммичена.
- **Float в `cost_price`** — `funnel/sync.py` использует float division вместо `Decimal`.

## Файлы
- `integrations/wb_api.py` — WB Statistics/Content API клиент.
- `integrations/resilience.py` — `CircuitBreakerRegistry` (per-project) + `retry_with_backoff`.
- `services/funnel/` — обёртки WB API, оркестратор синхронизации, анализ, backfill, capital, аномалии, рекламные кампании.
- `services/wb_finance_sync.py` (+ `wb_finance_helpers.py`) — синхронизация WB Finance Report.
- `services/wb_cancel_sync.py` — синхронизация статистики отмен.
- `services/integrations_service.py` — управление API-ключами.
- `services/tariff_service.py` — управление тарифами WB.
- `services/warehouse_stock_service.py`, `services/stock_forecast_service.py` — остатки и прогноз.
- `scheduler/jobs/` — фоновая синхронизация (`funnel.py`, `wb_finance.py`, `wb_stocks.py`).
- `routers/integrations.py`, `routers/funnel.py`, `routers/reports_stock.py` — HTTP endpoints.
- `models/integrations.py`, `models/wb_finance.py`, `models/wb_order_cancel.py`, `models/wb_tariff.py` — ORM.
- `utils/crypto.py` — шифрование API-ключей.
