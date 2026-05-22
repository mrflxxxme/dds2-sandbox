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
- `?? null` вместо `|| null` для API body — `||` теряет `[]`/`0`/`''` (falsy)
- `URLSearchParams` для query, НИКОГДА template literals — `H&M` ломает `&`
- `\b` regex НЕ матчит кириллицу — использовать `includes()` для русских маркеров
- DOMPurify (`sanitizeAIHtml()`) для HTML из AI, не regex-allowlist

## Python/DB паттерны
- `_UNSET = object()` для partial PATCH — различает «не передано» vs «null=clear»
- UPSERT batch: дедуп ключей в Python ДО executemany (CardinalityViolation)
- `func.greatest(excluded.x, Model.x)` при partial-sync UPSERT — не затирать нулями
- `CREATE INDEX CONCURRENTLY` + `AUTOCOMMIT` для partial index в Alembic
- Denormalized FK на child → синхронизировать при update parent.fk (state-guard)
- Override поле → пересчитать ВСЕ derived (`effective = override ?? source`)

## Архитектура
- Cumulative consumption: `consumed: dict[id, qty]` как mutable state при sequential distribute
- Composite priority chain: per-scope → SKU-level → derived-from-fact → derived-from-plan
- Box-multiple распределение: кратность задаёт ФОРМУ (целые короба по потребности). Обычные SKU дослают хвост < короба россыпью (`distributeByBoxMultiple(..., looseTail=true)` + источник boxMode Pass 2) — иначе фрагментированный SKU молча выпадает. Новинки cold-start — СТРОГО (`looseTail=false`): хвост остаётся на ФФ (по требованию пользователя)
- Carve потока из сбалансированной матрицы: вычитать qty из `src` И `tgt` поровну → строка остаётся `Σsrc==Σtgt`
- Force-pull mutable external fields: `_try_force_enrich_*` с try/except, не кэшировать
- Idempotent seed: `SELECT ... GROUP BY project_id` → INSERT только для новых
- TanStack sort + pagination: ≤5k строк → без пагинации; >5k → server-side sort

## Антипаттерны
- `SELECT *` — всегда указывай колонки
- `.scalars().all()` без `.limit()` на больших таблицах
- `except Exception` без `asyncio.CancelledError` в scheduler jobs
- ilike без экранирования `%`/`_` в пользовательском вводе
