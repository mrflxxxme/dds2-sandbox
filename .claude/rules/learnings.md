---
paths:
  - "backend/**/*.py"
  - "frontend-react/**/*.{ts,tsx}"
  - "migrations/versions/*.py"
  - "tests/**/*.py"
---

# DDS Learnings (живые паттерны)

> Только то, что реально предотвращает баги. CI/hooks/worktree quirks — уже в коде.

## JS/TS ловушки
- `formatNumber()` по умолчанию 2 знака — для счётчиков/штук всегда `formatNumber(x, 0)`, иначе UI показывает «3,00 черновика» (поймано сразу на двух новых страницах)
- `Numeric`/`Decimal`-поля бэка сериализуются в JSON СТРОКОЙ, не number (несмотря на тип `number` в `types/api.ts`). `formatNumber(s)` зовёт `String.prototype.toLocaleString` — опции игнорируются, строка отдаётся as-is («1666.67» вместо «1 667»). Арифметика (`s * n`) коэрсит в number и маскирует баг, а прямой показ — нет. Перед `formatNumber` на сыром Decimal-поле — `Number(x)` (поймано на прогнозе логиста; те же «1771008.00» в KPI History)
- `\b` regex НЕ матчит кириллицу — использовать `includes()` для русских маркеров
- React StrictMode (dev) монтирует `useEffect` дважды: catch/finally первого (abort-нутого) запроса перезаписывает загруженные данные ложной ошибкой → `AbortController` + `if (controller.signal.aborted) return` в then/catch/finally
- `useSearchParams` пуст на первом рендере до гидратации — не редиректь, пока сырая строка пуста (`const raw = sp.get('x') ?? ''; if (raw && cond) router.replace(...)`)
- Convert по множеству: проверяй строгую однотипность цели (`onlyBox`/`onlyMono`), не отрицание присутствия (`!hasBox`) — иначе СМЕШАННАЯ цель ложно триггерит конвертацию
- Stale-страница (bfcache / browser-back / вторая вкладка) держит React-state; debounce-автосейв может PUT'нуть устаревшее поверх серверного состояния, изменившегося под ним (напр. после commit в другом месте) → перед записью сверять с сервером и выкидывать исчезнувшие сущности (паттерн `dropCommittedRows`)

## Python/DB паттерны
- COGS из движка оценки (`services/cost/valuation.py`) бакетится по КАЛЕНДАРНОМУ МЕСЯЦУ (`_mk` → `YYYY-MM`); `slice_window` фильтрует только по границам месяца. Для под-месячного окна отчёта (напр. 15–20 июня) `win[sku]["cogs"]` / `grouped_cogs` = себестоимость ЗА ВЕСЬ МЕСЯЦ, а выручка/штуки в BDR/ОПиУ берутся точным day-range SQL → себестоимость 135 % выручки, фантомный убыток (прод 2026-06: БДР FIFO показал −16 М ₽ вместо +7 %). Лечение: НЕ брать `cogs`-тотал движка напрямую, а применять его per-unit (`eff_cost` = cogs/net_qty) к фактическим net-штукам окна (`cost_total = eff_cost × sale_qty`) — так себестоимость покрывает ТЕ ЖЕ дни, что и выручка, и полный месяц совпадает бит-в-бит (qty окна == qty месяца). Касается только период-P&L (BDR `wb_bdr_service`, ОПиУ `opiu_service`); сток-консьюмеры (воронка/склад) берут `eff_now[method]` — точечную цену остатка, окно их не трогает
- `FulfillmentStock` ≠ весь остаток FULFILLMENT-складов: часть складов (напр. «апл», «хамза») — тип FULFILLMENT, но БЕЗ API-интеграции (нет ключа провайдера, 0 строк зеркала). Их остаток ведётся в `WarehouseStock` (производный от документов). Любой расчёт «остаток на ФФ-складе» только из `FulfillmentStock` тихо теряет товар таких складов → заниженные тоталы. Поймано на «Распределении остатков» (`stock_distribution.py`): склад апл показывал 1 182 шт (только пайплайн) вместо ~12 k — лечится фолбэком на `WarehouseStock.quantity` для складов без зеркала (склад «ФФ-зеркальный» = есть хотя бы одна строка `FulfillmentStock`, в т.ч. нулевая). Для «расхождения остатков» (`link_anomalies._stock_mismatch`), наоборот, берём ТОЛЬКО интегрированные склады (ФФ-зеркало vs наш склад — смысл диффа). Для **migfull** diff = `ff_good − (наш годный + наш брак)`, т.к. его `stock_actual`/ff_good ВКЛЮЧАЕТ брак (отдельного поля брака в API нет) — иначе расхождение раздувается ровно на наш брак; прочие провайдеры — `ff_good − наш годный` (та же логика, что в `fulfillment_service.list_stocks`)
- Кросс-складская агрегация остатков (`warehouse_id.in_(many)`) с одним общим `.limit(STOCKS_LIMIT)` без `ORDER BY` = недетерминированное усечение: уцелевшая сторона (ff_good ИЛИ our_quantity) даёт ЛОЖНОЕ одностороннее расхождение. `list_stocks` лимитирует ПОФАЙЛОВО (один склад); агрегаты по многим складам — либо без лимита (полная сводка, как `stock_distribution`), либо лимит на склад
- `selectinload(Parent.children)` НЕ фильтрует `is_deleted` — релейшен грузит и мягко-удалённые строки. Любая денежная агрегация по нему обязана идти по заранее отфильтрованному списку (`active = [c for c in parent.children if not c.is_deleted]`), иначе сводка тихо раздувается остатками soft-deleted. Поймано на сводке себестоимости машины (`_enrich_vehicle`): «Товар/Пошлина/НДС/Итого» считались по всему `vehicle.items`, а «Стоимость ¥»/кол-во/вес — по `active_items` → 12.9M ₽ вместо ~4.6M из-за 98 мёртвых строк (остатки многих перезаливок Excel; recalc их не трогает — лечится только фильтром при суммировании)
- `_UNSET = object()` для partial PATCH — различает «не передано» vs «null=clear»
- «Дольше N дней» через `timedelta.days > N` опаздывает почти на сутки (int-усечение: 3.9 суток → days=3) — сравнивать `total_seconds()/86400 > N`
- `Date`-колонка (без времени) через `datetime.combine(d, time.min)` = полночь UTC → длительности завышаются до суток; если точный момент есть в истории/audit-таблице — брать его `changed_at`, Date только fallback
- Секрет в URL-пути (wmscelicom-стиль `/api/{token}/…`): `str()` httpx-ошибки содержит полный URL → токен утекает в UI/логи. Все тексты ошибок клиента прогонять через `_redact()` (token→`***`), логировать только path-суффикс ПОСЛЕ токена
- PHP-API внешних провайдеров: вместо null приходят `false`/`""`/`"0000-00-00 00:00:00"`, пустой `data`-dict сериализуется как `[]`, в массивах бывают null-элементы (`Barcodes:[null]`) — коэрсить `or None`, `next((x for x in arr if x), default)`, парсер контейнера принимает и dict, и list (подтверждено живым wmscelicom)
- User-supplied base_url для server-side запросов = SSRF-вектор: allowlist-суффикс хоста + только https + запрет порта/пути/userinfo (`normalize_base_url` в wmscelicom_client); httpx по умолчанию НЕ следует редиректам — не включать follow_redirects у таких клиентов
- UPSERT batch: дедуп ключей в Python ДО executemany (CardinalityViolation)
- `func.greatest(excluded.x, Model.x)` при partial-sync UPSERT — не затирать нулями
- `CREATE INDEX CONCURRENTLY` + `AUTOCOMMIT` для partial index в Alembic
- Denormalized FK на child → синхронизировать при update parent.fk (state-guard)
- Override поле → пересчитать ВСЕ derived (`effective = override ?? source`)
- Explicit `null` в JSONB-поле ≠ отсутствующий ключ: Pydantic `default_factory` И `dict.get(k, default)` дают дефолт ТОЛЬКО при отсутствии ключа, не при null. Nullable JSONB-list → `@field_validator(mode="before")` коерсит null→[]; сырой dict → `.get(k) or []`, не `.get(k, [])`
- `SoftDeleteMixin` + `UniqueConstraint` без `is_deleted` = мина: после `soft_delete()` строка остаётся и занимает уникальный слот → повторный INSERT того же бизнес-ключа падает `IntegrityError`/500. Re-create путь обязан искать ВКЛЮЧАЯ soft-deleted → `.restore()` + обновить поля, НЕ новый INSERT (см. `accept_invite` в `routers/projects.py`). Альтернатива на уровне схемы — partial unique index `WHERE is_deleted = false` (миграция). Все lookup'ы по такой модели фильтруют `is_deleted == False`, иначе мутации/проверки бьют по «мёртвой» строке. Сигнал несоблюдения: `mixins.restore()` почти нигде не зовётся

## Архитектура
- Cumulative consumption: `consumed: dict[id, qty]` как mutable state при sequential distribute
- Composite priority chain: per-scope → SKU-level → derived-from-fact → derived-from-plan
- Box-multiple распределение: кратность задаёт ФОРМУ (целые короба по потребности). Обычные SKU дослают хвост < короба россыпью (`distributeByBoxMultiple(..., looseTail=true)` + источник boxMode Pass 2) — иначе фрагментированный SKU молча выпадает. Новинки cold-start — СТРОГО (`looseTail=false`): хвост остаётся на ФФ (по требованию пользователя)
- Carve потока из сбалансированной матрицы: вычитать qty из `src` И `tgt` поровну → строка остаётся `Σsrc==Σtgt`
- Force-pull mutable external fields: `_try_force_enrich_*` с try/except, не кэшировать
- Idempotent seed: `SELECT ... GROUP BY project_id` → INSERT только для новых
- TanStack sort + pagination: ≤5k строк → без пагинации; >5k → server-side sort

<!-- Антипаттерны (SELECT *, .scalars().all() без .limit(), except Exception без CancelledError, ilike-экранирование) — в CLAUDE.md и backend.md, не дублируем здесь. -->
