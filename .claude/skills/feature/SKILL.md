---
name: feature
description: "Сквозная разработка фичи DDS2: план (ждёт ok) → хребет → фан-аут backend‖frontend → verify-луп → review → /ship. Один вход на всю фичу."
argument-hint: "<описание фичи: что строим и зачем>"
---

# /feature — фича от плана до отправки

Единый конвейер фичи. Отличие от `/autofix`: у фичи нет жёсткого оракула «правильно/неправильно» — главный риск «сделал НЕ ТО». Поэтому **план утверждает человек** (это единственный обязательный чекпоинт), а реализация дальше идёт под гейты автономно.

**Фича:** $ARGUMENTS

## Фаза 1 — План (ГЕЙТ: ждёт твоё «ок»)
Отрабатывает как `/plan`: анализ требований (что/зачем/вне scope) → чтение `DOMAIN_*.md` затронутых доменов и кода → пошаговый план в порядке **Model → Migration → Schema → Service → Router → Test → Frontend** → риски (breaking changes, миграции на больших таблицах) → **API-контракт фиксируется здесь** (эндпоинты + схемы + коды ошибок).
Крупная фича (>5 файлов / новый домен) — сначала формальная спека (Requirements / Design / Tasks), потом план.
**Без явного «ок»/«proceed» код не пишется.** Это твоя точка «то / не то».

## Фаза 2 — Хребет (lead, последовательно)
После «ок»: кладу и **коммичу хребет** — Model → Migration → Schema. Сюда же `cache.py`, `models/`, `schemas/`, миграции — всё lead-sequential (общие файлы, фан-аут их не трогает). API-контракт заморожен → **types-first** (`src/types/api.ts` обновлён до использования).
Миграции — по `/migration` (heads → генерация → `upgrade/downgrade/upgrade` тест → docs).

## Фаза 3 — Фан-аут (параллельно, зоны конфликт-фри)
Хребет закоммичен → две lane параллельно:
- **backend**: Service → Router → Test (`Depends(rate_limit_write)` на write, `invalidate_cache` после мутации, `project_id`/`is_deleted` в запросах).
- **frontend**: `types` → `src/lib/api/` → страница с **loading / error / empty / data**, числа через `formatNumber()`.

## Фаза 4 — Verify-луп (до зелёного)
`/verify` (full): pytest → conventions → безопасность → `tsc --noEmit && npm run lint` (если фронт). Красное → правлю → снова, как self-verification луп. Затем `/review` — фан-аут профильных субагентов (вкл. `frontend-reviewer`), вердикт APPROVE/WARNING/BLOCK. BLOCK не пропускаю.

## Фаза 5 — Отправка
Документацию — в тот же коммит, что и код. Дальше **`/ship`** (verify→commit→push dev→watch CI→`/learn`). **Пуш — по твоему слову**, не автоматически.

## Стоп-условия (спросить тебя)
- План не утверждён — стою на Фазе 1.
- Нужен бизнес-выбор (какие поля user-facing, какие данные можно трогать).
- Миграция на большой таблице / breaking change контракта — подсвечиваю риск до реализации.
- UI: финально смотришь вживую сам (браузерный прогон агенту запрещён).

## Итог
```
FEATURE
  План:    утверждён ✓
  Хребет:  Model+Migration+Schema коммит <hash>
  Backend: service/router/test ✓   Frontend: page+types ✓
  Verify:  pytest ✓ | mypy ✓ | conventions ✓ | tsc ✓
  Review:  APPROVE / WARNING / BLOCK
  → /ship по твоему слову
```
