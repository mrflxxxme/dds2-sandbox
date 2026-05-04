# Opus 4.7 — migration notes для DDS2

> Канон lead-agent: [.claude/rules/lead_agent_v2.md](../.claude/rules/lead_agent_v2.md).
> Это шпаргалка по изменениям поведения Opus 4.7 vs 4.6 и что из-за этого меняется в проекте.

## Что изменилось в модели

| Область | 4.6 | 4.7 |
|---|---|---|
| Literal понимание инструкций | Достраивает по смыслу | **Буквально**. Неполный запрос → буквальный результат |
| Verbosity | Постоянные «сейчас я сделаю X, потом Y» | Калибруется под задачу. Мета-объяснения убрать |
| Subagent delegation | Агрессивная по умолчанию | **Консервативная**. Fan-out указывать явно |
| Thinking traces | Видны частично | По умолчанию пустые; `display: "summarized"` для хуков |
| Инициатива «на всякий случай» | Часто сама | Редко. Просят — делает |

## Что из-за этого меняется в проекте

### 1. Приём задачи (§1 canon)
Задача должна содержать **intent + constraints + acceptance + files**. Если <2 из 4 — один уточняющий вопрос, не дроби на turn-ы. Исключения: `/hotfix`, `/smoke`, `/status`, `/verify` — без уточнений.

### 2. Параллелизм — **явно** (§4 canon)
4.6 спавнил сам по эвристике. 4.7 — не спавнит, пока не попросят прямо. Рабочие формулы:
- «Spawn subagents **in the same turn** to investigate A, B, C»
- «Fan out to read files X, Y, Z **in parallel**»
- «Launch backend and frontend teammates **concurrently** in worktrees»
- «Run code-reviewer, security-reviewer, api-designer **in parallel** over this diff»

Без таких формулировок — lead сделает сам, sequential.

### 3. Task budget на teammate (advisory)
- Средний worktree-teammate: **40k токенов** на задачу. Превышение без результата → fail-fast.
- Long-running (миграция + backfill + тесты): **80k токенов**.
- Не жёсткий API-лимит, а индикатор: teammate просит больше — он зациклился, вернуть отчёт, не продолжать.

### 4. Fail-fast на pre-commit loop
Pre-commit падает 2 раза подряд на одном файле → teammate обязан остановиться, вернуть отчёт. Прецедент 2026-04-21 (`agent-a9ac9883`, 25 мин вместо 15).

### 5. Verbosity в ответах lead'а
- Не писать «сейчас я сделаю X, потом Y, потом Z» перед каждой командой
- `TodoWrite` заменяет forced status updates
- End-of-turn summary: 1-2 предложения максимум
- Код без объяснений, если не ревью/план

### 6. Thinking traces
Хуки в `scripts/hooks/` **не парсят** thinking traces (проверено grep 2026-04-22). Но если будете добавлять — использовать `display: "summarized"` или парсить по `[thinking_summary]` блокам.

## Cloud-offload (§5 canon)

Две команды работают в облаке и **освобождают локальный терминал**:

### `/ultraplan`
**Когда:** ошибка в плане дороже ошибки в реализации.
- Cross-domain рефакторы (3+ DOMAIN)
- Миграция auth / permission model
- Новый домен с нуля
- Большая интеграция (новый маркетплейс, банк, FFM)

**Как работать:** запустил → пока чертится (~1 мин), локально делаешь другое (багфикс, рутинный endpoint) → план готов → review в браузере → одобряешь → `/teleport` в терминал или исполнение в облаке.

**Не использовать** `/spec` для таких задач — single-agent планирование anchor-biased.

### `/ultrareview`
**Когда:** single-pass review оставляет false positives, которые дороже ревью.
- PR > 500 LOC
- Миграции БД (невозможно откатить после merge в main)
- Security-sensitive: auth, JWT, rate-limit, crypto
- Рефакторы money-handling (Numeric, округления)

**Лимит:** до 5 мая 2026 — 3 бесплатных запуска на аккаунт. После — $5–$20 за ревью, биллится как extra usage.

**План для DDS2:** потратить 3 бесплатных на реальные крупные PR, оценить найденные issues vs `/review`, решить по встраиванию в pre-push.

### Правило дублирования
Если `/ultraplan` или `/ultrareview` запущены — **не дублировать** их работу локально. Lead продолжает другую задачу, не переписывает план с нуля.

## Env vars обязательно (§6 canon)

В `.claude/settings.json` → `env`:
```json
{
  "ENABLE_PROMPT_CACHING_1H": "1",
  "CLAUDE_CODE_SUBAGENT_MODEL": "claude-opus-4-7"
}
```

- **`ENABLE_PROMPT_CACHING_1H=1`** — 1-часовой TTL на стабильный контекст (CLAUDE.md, MAP.md, DOMAIN_*.md, rules/). Экономит до 90% токенов на длинной сессии. Без него cache TTL = 5 мин, на прерывистых сессиях не окупается.
- **`CLAUDE_CODE_SUBAGENT_MODEL=claude-opus-4-7`** — явная фиксация модели. Без неё subagents могут подхватить более старый opus, если в frontmatter агента написано generic `opus`. Все 8 subagents DDS2 уже на `model: opus` — эта env фиксирует точную версию.

## Anti-patterns 4.7 (§10 canon)

- Не писать мета-объяснения и forced status updates (`TodoWrite` вместо этого)
- Не парсить thinking traces (пустые)
- Не ослаблять параллелизм «на всякий случай» — 4.7 консервативен, fan-out указывать прямо
- Не использовать `/spec` там, где есть `/ultraplan`
- Не применять блокирующий security-фикс без чтения `memory/feedback_*.md` (прецедент REGISTER_ENABLED)
- Не использовать абсолютные пути в worktree teammate
- Не дублировать работу облачного `/ultraplan` или `/ultrareview` локально
- Не тратить лимит teammate (40k/80k) на цикл без результата — fail-fast

## Acceptance для внедрения v2

Смотри «Acceptance criteria» в [.claude/rules/lead_agent_v2.md](../.claude/rules/lead_agent_v2.md).
