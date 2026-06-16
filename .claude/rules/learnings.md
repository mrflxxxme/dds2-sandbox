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
- `\b` regex НЕ матчит кириллицу — использовать `includes()` для русских маркеров
- React StrictMode (dev) монтирует `useEffect` дважды: catch/finally первого (abort-нутого) запроса перезаписывает загруженные данные ложной ошибкой → `AbortController` + `if (controller.signal.aborted) return` в then/catch/finally
- `useSearchParams` пуст на первом рендере до гидратации — не редиректь, пока сырая строка пуста (`const raw = sp.get('x') ?? ''; if (raw && cond) router.replace(...)`)
- Convert по множеству: проверяй строгую однотипность цели (`onlyBox`/`onlyMono`), не отрицание присутствия (`!hasBox`) — иначе СМЕШАННАЯ цель ложно триггерит конвертацию
- Stale-страница (bfcache / browser-back / вторая вкладка) держит React-state; debounce-автосейв может PUT'нуть устаревшее поверх серверного состояния, изменившегося под ним (напр. после commit в другом месте) → перед записью сверять с сервером и выкидывать исчезнувшие сущности (паттерн `dropCommittedRows`)

## Python/DB паттерны
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
