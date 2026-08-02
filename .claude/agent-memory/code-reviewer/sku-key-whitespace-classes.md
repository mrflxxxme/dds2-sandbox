---
name: sku-key-whitespace-classes
description: Артикулы-ключи (article_seller / sa_name / vendor_code) содержат ДВА разных класса «мусорного» пробела — btrim и python .strip() их режут по-разному; полный список потребителей карт себестоимости
metadata:
  type: project
---

В данных проекта 4 (снимок прод-копии 2026-07-28) артикулы-ключи «грязные» ДВУМЯ разными способами,
и это ломает любую попытку нормализовать ключ только с одной стороны:

- **Класс A — обычный пробел U+0020.** `nomenclature.article_seller` 38 шт (15 из них с себесом
  и WB-стоком), `cost_order_items` 38, `wb_finance_rows.sa_name` 27 артикулов / 94 034 строки /
  ≈82.8M ₽ выручки, `wb_funnel_daily.vendor_code` 28. Режется И `btrim`, И `.strip()`.
- **Класс B — `\r\n` в хвосте** (артикулы `STYL_*`, заливка из Excel): `nomenclature` 15,
  `cost_order_items` 45, `wb_finance_rows` 15 артикулов / 434 строки / ≈330k ₽. Все 15 СЕГОДНЯ
  матчатся с закупками «как есть» (и напрямую по ключу, и по баркоду).
  **`btrim(x)` по умолчанию режет ТОЛЬКО пробелы — `\r\n` остаётся; python `x.strip()` режет всё
  (вкл. `\t\n\r\xa0`).** Поэтому «btrim в SQL + .strip() в python» = новый промах на классе B.

Проверочный запрос (различает классы):
`btrim(col) <> btrim(col, ' '||chr(9)||chr(10)||chr(13)||chr(160))` → класс B;
`col <> btrim(col)` → класс A.

**Полный список потребителей карт себестоимости, которые лукапят по строковому артикулу** (при любой
правке ключа карты-производителя надо править ВСЕ, иначе для «грязных» артикулов станет хуже, чем было):
`warehouse_stock_engine.get_unified_stock_summary` (cost_map), `wb_bdr_service` (per-article sku
И **`sa_to_group`** — про него забывают), `opiu_service`, `funnel/stock_costs.get_stock_cost_map`,
`funnel/cost_overrides` (ТРИ места: get_missing_costs ×2 + get_cost_overrides),
`funnel/sync` (`vendor_code` → пишет `wb_funnel_daily.cost_price`), `pricing/markup._resolve_cost`,
`stock_forecast_service` (margin_pct), `cost/opening_balance._resolve_sku_for_barcode`.
Производители: `bdr_loaders.load_avg_costs` (2 ключа), `cost/valuation._load_batches/_load_sales/_load_meta`
+ skus-фильтр `compute_project_valuation`.

**Why:** денежный контур — ключ-промах = потерянный COGS = завышенная прибыль в БДР/ОПиУ/остатках.
Ревью 2026-07-28 поймало трим только у производителей: 6 потребителей остались нетримленными,
а `stock_costs.py` проверял `.strip()`-ключом и доставал не-`.strip()` → KeyError/500.
**How to apply:** при любом ревью «нормализации артикула» — грепнуть весь список выше и убедиться,
что обе стороны режут ОДИН И ТОТ ЖЕ набор символов (SQL: `btrim(col, ' '||chr(9)||chr(10)||chr(13)||chr(160))`).
Связано с [[cost-valuation-auto-opening-invariants]].
