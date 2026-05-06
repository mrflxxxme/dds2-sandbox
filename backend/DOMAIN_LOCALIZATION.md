# DOMAIN_LOCALIZATION — Индекс Локализации (ИЛ + ИРП)

> С 23.03.2026 WB ввёл новые правила тарификации логистики, привязанные к
> доле локально-доставленных заказов (`localizationPercent`). Этот домен
> закрывает: сводный отчёт ИЛ/ИРП, региональную разбивку по федеральным
> округам и manual-refresh.

## Источники данных

| Что | Endpoint WB | Куда складываем |
|-----|-------------|-----------------|
| Агрегат на nm_id за день | `seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products` | `wb_funnel_daily.localization_percent`, `time_to_ready_minutes` |
| Лента заказов с geo+warehouse | `statistics-api.wildberries.ru/api/v1/supplier/orders` | `wb_orders` (1 ряд = 1 srid) |

`wb_orders` синхронится 3×/день (cron 03:30 / 09:30 / 15:30 MSK), `days_back=30`,
UPSERT по `(project_id, srid)`. Manual refresh: `POST /api/v1/localization/sync`.

## Расчёты (`backend/services/localization_index_service.py`)

### ИЛ — Индекс локализации
Средневзвешенный КТР по всем заказам периода:
```
ИЛ = Σ(orders × ktr) / Σ orders
```
КТР берётся per-SKU из `loc_pct → KTR_TABLE` ([localization_tariff.py](services/localization_tariff.py)).

### ИРП — Индекс распределения продаж
Средневзвешенный КРП (% удержания к цене):
```
ИРП = Σ(orders × krp) / Σ orders
```

### Per-SKU loc_pct
Средневзвешенно по `orders_count` за период (из `wb_funnel_daily`):
```
loc_pct(nm) = Σ(orders × localizationPercent) / Σ orders
```

### Per-SKU × округ доставки (из wb_orders)
```
local(nm, district)     = orders где
    is_cancel = false
  AND warehouse_type = 'Склад WB'
  AND country_name = 'Россия'
  AND oblast_okrug_name → district
  AND warehouse_to_district(warehouse_name) == district

non_local(nm, district) = orders где условия выше выполнены
                          AND warehouse_to_district(warehouse_name) != district
```

Округа: `central`, `northwest`, `south_caucasus`, `volga`, `ural`,
`far_east_siberia`, `abroad` (заграница), `unknown` (не в справочнике).

«Северо-Кавказский ФО» объединён с «Южный ФО», «Сибирский ФО» с
«Дальневосточный ФО» — как в эталонном калькуляторе WB.

## Маппинг складов
[`warehouse_district.py`](services/warehouse_district.py) — справочник
89 складов из WB-кабинета («Логистика → Склады → Коэффициенты по коробам»,
2026-04). Включает виртуальные склады (`Виртуальный Москва Радумля` и т.д.) и
зарубежные (Армения, Беларусь, Грузия, Казахстан, Узбекистан) → `abroad`.

`WAREHOUSE_LOGISTICS_COEF` хранит коэффициенты для будущих рублёвых
расчётов логистики (пока используется только для документации).

## API endpoints

| Метод | Path | Возвращает |
|-------|------|------------|
| GET | `/localization/summary?date_from&date_to` | `LocalizationSummary` (ИЛ, ИРП, district_totals) |
| GET | `/localization/skus?date_from&date_to` | `list[LocalizationSkuRow]` (per nm + districts) |
| GET | `/localization?date_from&date_to` | оба сразу |
| POST | `/localization/sync` | trigger `sync_wb_orders(days_back=30)` |

Все фильтруют по `project_id` через `Depends(get_current_project)`.
`/sync` под `Depends(rate_limit_write)`.

## Кэш

| Префикс | TTL | Когда инвалидируется |
|---------|-----|----------------------|
| `reports:localization` | 300s | sync wb_funnel_daily, sync wb_orders, mutate funnel |
| `reports:localization_skus` | 300s | то же |
| `reports:localization_districts` | 300s | sync wb_orders |

`sync_wb_orders` сам делает invalidate всех трёх префиксов после UPSERT.

## Scheduler

[`scheduler/jobs/wb_orders_sync.py`](scheduler/jobs/wb_orders_sync.py) —
`sync_all_projects_wb_orders()`:
- Cron `0 30 3,9,15 * * MSK` (3 раза в день, в часы наименьшей нагрузки на WB).
- WB Statistics API rate-limit ~1 req/min на ключ → проекты обходятся
  последовательно.
- `try/except asyncio.CancelledError: raise` ПЕРЕД `except Exception`
  (graceful shutdown).
- `cutoff_dt = вчера 23:59 MSK` — сегодняшние заказы пропускаются
  (счётчик `skipped_future`).

## Frontend

`/p/[slug]/localization` ([page.tsx](../frontend-react/src/app/(main)/p/[slug]/localization/page.tsx)):
- Top-блок: ИЛ, ИРП, заказы, Легенда КТР, КРП-боксы.
- Таблица per-SKU + sticky ИТОГО + group-headers по 6 ФО × 4 sub-cols.
- Conditional row coloring по КРП.
- Кнопка «Обновить данные» → `POST /localization/sync`.

API client: [`lib/api/localization.ts`](../frontend-react/src/lib/api/localization.ts).
Constants: [`lib/constants/localization.ts`](../frontend-react/src/lib/constants/localization.ts)
(DISTRICT_ORDER, DISTRICT_LABELS, DISTRICT_COLORS).

## Важные нюансы

- **Виртуальные склады**: WB иногда отгружает заказ через «Виртуальный Краснодар»
  и т.п. — это FBS-сеть в нужном городе. Маппятся на свой ФО.
- **Склад продавца** (`warehouse_type='Склад продавца'`) фильтруется из
  расчёта — это не WB-склад, а маркетплейс/FBS со склада продавца.
- **0001-01-01 как sentinel** для `cancelDate` — `_parse_dt` возвращает None
  на годах < 1900, иначе pytz переполняется.
- **OnConflictDoUpdate** при UPSERT обновляет только мутируемые поля
  (`is_cancel`, `cancel_date`, `last_change_date`, `warehouse_*`,
  `oblast_okrug_name`, `region_name`, `finished_price`, `price_with_disc`,
  `synced_at`). Иммутабельные (`nm_id`, `srid`, `order_date`,
  `total_price`, `discount_percent`) НЕ обновляются.
- **Дедуп до executemany**: WB иногда отдаёт дубликаты `srid` в одном
  ответе → `dict[srid] -> row` ДО UPSERT-batch (защита от
  CardinalityViolation).

## Тесты

| Файл | Что покрыто |
|------|-------------|
| [test_warehouse_district.py](../tests/test_warehouse_district.py) | Маппинг warehouse → ФО, okrug → district, structure (35) |
| [test_wb_orders_sync.py](../tests/test_wb_orders_sync.py) | UPSERT, повторный sync, cancel, cutoff, multi-tenancy, dedup (7) |
| [test_localization_districts.py](../tests/test_localization_districts.py) | _load_district_breakdown, exclusions, summary/skus integration (12) |
| [test_localization_index_service.py](../tests/test_localization_index_service.py) | ИЛ/ИРП расчёт, эталон 37%/62% → ИРП 1.05% (12) |
| [test_localization_tariff.py](../tests/test_localization_tariff.py) | КТР/КРП таблицы (25) |
