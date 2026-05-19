# DOMAIN_LOCALIZATION — Индекс Локализации (ИЛ + ИРП)

С 23.03.2026 WB тарифицирует логистику по доле локально-доставленных заказов (`localizationPercent`). Домен закрывает сводный отчёт ИЛ/ИРП, региональную разбивку по федеральным округам и manual-refresh.

## Таблицы
Своих таблиц нет — используются:
| Таблица | Что хранит | Источник WB |
|---------|------------|-------------|
| `wb_funnel_daily` | `localization_percent` (Numeric(5,2)), `time_to_ready_minutes` | Analytics v3 `sales-funnel/products` (агрегат на nm_id за день) |
| `wb_orders` | 1 ряд = 1 srid, geo + warehouse | Statistics API `/supplier/orders` |

`wb_orders` синхронится 3×/день, `days_back=30`, UPSERT по `(project_id, srid)`. Manual refresh — `POST /localization/sync`.

## Бизнес-правила
- **ИЛ (Индекс локализации)** — средневзвешенный КТР по заказам периода: `ИЛ = Σ(orders × ktr) / Σ orders`. КТР берётся per-SKU из `loc_pct → KTR_TABLE`.
- **ИРП (Индекс распределения продаж)** — средневзвешенный КРП (% удержания к цене): `ИРП = Σ(orders × krp) / Σ orders`. КРП = 0 при `loc ≥ 60%`.
- **Per-SKU `loc_pct`** — средневзвешенно по `orders_count` за период из `wb_funnel_daily`.
- **Per-SKU × округ доставки** (из `wb_orders`): заказ считается `local`, если `warehouse_to_district(warehouse_name)` совпадает с округом доставки (`oblast_okrug_name`); иначе `non_local`. Учитываются только `is_cancel=false`, `warehouse_type='Склад WB'`, `country_name='Россия'`.
- **Округа:** `central`, `northwest`, `south_caucasus`, `volga`, `ural`, `far_east_siberia`, `abroad`, `unknown`. «Северо-Кавказский ФО» объединён с «Южный», «Сибирский» с «Дальневосточный» — как в эталонном калькуляторе WB.
- **Маппинг складов** — справочник `warehouse_district.py` (89 складов из WB-кабинета). Виртуальные склады («Виртуальный Краснодар» и т.п.) — это FBS-сеть в нужном городе, маппятся на свой ФО. Зарубежные → `abroad`.
- **Эталон** (WB-инструкция): 2 SKU по 100 заказов с `loc=37%` и `62%` → ИРП = 1.05%.
- Таблицы тарифов: КТР — 18 диапазонов 0..100%, КРП — 13 диапазонов (`localization_tariff.py`).

## Priority-weighted распределение потребности
Распределение потребности между WB-складами (фича `warehouse_need_service`) учитывает «скорость доставки» из speed-карты POSTAVLENO (`backend/data/wb_warehouse_speed.json`, 97 городов с priority-chains).

- **Фильтр открытых складов:** исключаются склады из настроек проекта и склады с WB acceptance closure (`free_days_14=0 AND paid_days_14=0` во всех package_types — из Redis snapshot, fail-open если кэша нет).
- **Маршрутизация заказа** в открытый склад: priority-chain города из speed-карты (если top-1 закрыт — priority-2 того же города) → okrug-fallback по агрегатному priority_score ФО → haversine по city/region.
- **Priority-weighted доли** (`only_available=True`): доля склада ∝ `need × (1 + priority_score) × penalty`, где `penalty=0.6` для склада-«похитителя» (crawl-stealer — присутствует в priority-chains чужих ФО), `1.0` для чистого якоря своего ФО. `priority_score ∈ [0..1]` — усреднённый ранг склада по городам ФО. Leftover от округления раздаётся по Hamilton (largest-fractional-remainders), не больше `need[wh]`.
- **Min-stock bump** для дальних регионов: `main_wh` для underserved ФО выбирается по `(anchor_rank ASC, share_pct DESC)` — якорь speed-карты побеждает исторического лидера при равенстве.
- **Cold-start новинок:** `pick_*` функции сортируют по `(traffic DESC, priority_score DESC, name ASC)` — traffic из истории проекта остаётся primary, priority-rank speed-карты — tiebreak.

## Зависимости
- `DOMAIN_WB` — WB Statistics API, агрегаты `wb_funnel_daily`.
- `services/warehouse_district.py` — справочник 89 WB складов → ФО.
- `services/localization_tariff.py` — KTR_TABLE, KRP-боксы.

## Грабли
- **Виртуальные склады** — WB иногда отгружает через «Виртуальный <город>» (FBS-сеть), маппить на ФО города.
- **«Склад продавца»** (`warehouse_type='Склад продавца'`) фильтруется из расчёта — это не WB-склад.
- **`0001-01-01` как sentinel** для `cancelDate` — `_parse_dt` возвращает None на годах < 1900, иначе pytz переполняется.
- **UPSERT обновляет только мутируемые поля** (`is_cancel`, `cancel_date`, `warehouse_*`, `oblast_okrug_name`, `finished_price` и т.п.); иммутабельные (`nm_id`, `srid`, `order_date`, `total_price`) — нет.
- **Дедуп до executemany** — WB иногда отдаёт дубли `srid` в одном ответе → `dict[srid] → row` перед UPSERT-batch (защита от CardinalityViolation).
- **`srid` normalize** — `/supplier/orders` отдаёт srid с префиксом `eXX.r/i`, `order_city_map.srid` — без; нормализуем при чтении для матчинга city-key.
- **Scheduler** (`wb_orders_sync.py`, cron 03:30/09:30/15:30 MSK) — WB Statistics API rate-limit ~1 req/min на ключ, проекты обходятся последовательно; `cutoff_dt = вчера 23:59 MSK` (сегодняшние заказы пропускаются); ловить `asyncio.CancelledError` перед `except Exception`.
- **Кэш:** префиксы `reports:localization`, `reports:localization_skus`, `reports:localization_districts` (TTL 300с) — `sync_wb_orders` сам инвалидирует все три после UPSERT.

## Файлы
- `services/localization_index_service.py` — расчёт ИЛ/ИРП, per-SKU loc_pct, district breakdown.
- `services/warehouse_district.py` — справочник 89 WB складов → ФО.
- `services/warehouse_speed.py` — speed-карта POSTAVLENO (priority_score, find_priority_warehouse, anchors/stealers).
- `services/warehouse_need_service.py` — priority-weighted распределение + priority-chain fallback.
- `services/warehouse_acceptance_service.py` — `get_acceptance_closed_warehouses` для фильтра открытых складов.
- `services/cold_start_distribution_service.py` — distribute_multi + priority-rank tiebreak.
- `services/localization_tariff.py` — KTR_TABLE, KRP-боксы.
- `scheduler/jobs/wb_orders_sync.py` — sync `wb_orders` 3×/день.
- `routers/localization.py` — endpoints (`/localization`, `/summary`, `/skus`, `/sync`).
- `frontend-react/src/app/(main)/p/[slug]/localization/page.tsx` — UI.
- `frontend-react/src/lib/api/localization.ts` — API client.
- `frontend-react/src/lib/constants/localization.ts` — DISTRICT_ORDER/LABELS/COLORS.
