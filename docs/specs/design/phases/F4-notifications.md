---
phase: F4 · Уведомления — личные + сводка + флаг чата · status: planned
tier: 1
depends_on: [F2]
executors: [tm-backend]      # worktree, зона backend/services/, backend/scheduler/, tests/; модели НЕ трогать (dsn02 уже в Ф0)
reviewers: [code-reviewer]
donors:
  - backend/scheduler/jobs/draft_staleness_watch.py   # эталон джобы: антиспам Redis SET NX EX, send_analytics_message, CancelledError
  - backend/services/telegram_service.py              # пары toggle_supply_notify :218 / list_supply_notify_chats :234
  - backend/services/fulfillment_notify.py            # уведомления по смене статуса
  - backend/scheduler/__init__.py                     # регистрация джоб :29
prd_refs: [PRD v4 §5 (у руководителя), §9]
---
<!-- HEAD-SUMMARY: личные TG-уведомления (4 события, best-effort после commit, резолв через TelegramBotUser) — НОВАЯ механика; утренняя сводка в чаты с design_notify_enabled + «срок завтра» исполнителям; toggle/list в telegram_service. Колонка dsn02 создана в Ф0. -->

## Goal

Уведомления по PRD: личные — исполнителю/автору по событиям задачи; групповые — утренняя сводка руководителю в чаты.

## In scope

`backend/services/design/notify.py` (замена no-op заглушек Ф1) · `backend/scheduler/jobs/design_notify.py` · пары `toggle_design_notify` / `list_design_notify_chats` в `telegram_service.py` · регистрация джобы · `tests/test_design_notify.py`.

## Out of scope

Изменение моделей (dsn02 сделан в Ф0; нужна правка — эскалация) · настройка времени сводки из UI (конфиг-константа, дефолт 09:00 МСК — см. STATUS «Открытые вопросы») · inline-кнопки в сообщениях (→ потом).

## Работы

### Личные (`notify.py`, Р6)

Резолв: `assignee/author user_id → TelegramBotUser.telegram_id` (существующая модель, deep-link привязка `/start` уже работает). Доставка — `send_analytics_message(chat_id, text)` (httpx, работает вне aiogram). Вызов — из сервисных функций Ф1 сразу после `commit()`, весь блок в `try/except Exception` (сбой TG не роняет операцию — инвариант §6.8). Нет привязки — молча скип.

| Событие (хук Ф1) | Кому | Текст (HTML, `html.escape`, deep-link на задачу `/p/{slug}/design-tasks/{id}`) |
|---|---|---|
| Назначили задачу | исполнителю | `DES-N · <title> · срок <дата>` |
| Вернули на доработку | исполнителю | `DES-N вернули: <причина>` |
| Приняли работу | исполнителю | `DES-N принята` |
| Сдали версию | автору | `DES-N ждёт проверки, версия N` |

### Групповая сводка + «срок завтра» (`design_notify.py`)

Джоба по образцу `draft_staleness_watch.py`: обход проектов → `list_design_notify_chats` → HTML-сводка: в работе / на проверке / просрочено / принято вчера (+deep-link на доску) → `asyncio.gather(..., return_exceptions=True)`. «Срок завтра» — личное исполнителям задач с `due_date = tomorrow` и активным статусом. Антиспам — Redis `SET NX EX` на ключ `design_digest:{project_id}:{date}` (повторный запуск в день — no-op). Обязательно: `except asyncio.CancelledError: raise` ПЕРЕД `except Exception`. Регистрация в `scheduler/__init__.py`: `CronTrigger` на конфиг-время, `replace_existing=True`, `misfire_grace_time`.

### Toggle

`toggle_design_notify(db, binding_id, project_id, enabled)` + `list_design_notify_chats(db, project_id)` — точное зеркало пары supply (:218/:234). Управление флагом — существующей командой бота/ручкой по образцу остальных флагов (минимальный путь: расширить существующий `/notify`-механизм бота, не создавая новый роутер).

## AC

- **AC-1:** с замоканным `send_analytics_message`: назначение задачи пользователю БЕЗ привязки TG — операция успешна, отправок 0.
- **AC-2:** каждое из 4 событий с привязкой — ровно одна отправка с ожидаемым текстом (мок фиксирует аргументы).
- **AC-3:** сбой отправки (мок кидает) — операция сервиса всё равно коммитится.
- **AC-4:** повторный запуск джобы в тот же день — 0 повторных сообщений (антиспам-ключ).
- **AC-5:** в чат с `design_notify_enabled=false` сводка не идёт; с `true` — идёт.
- **AC-6:** grep-проверка: в джобе есть `except asyncio.CancelledError: raise` (конвенционный анти-паттерн).

## Exit-gate

| Критерий | Порог | Evidence |
|---|---|---|
| Тесты фазы | `pytest tests/test_design_notify.py -x` зелёный | транскрипт |
| Полный срез | `make test-fast` без регрессий | транскрипт |
| Ревью T1 | code-reviewer без BLOCK | вердикт |

## Hints

- Заглушки notify-хуков уже объявлены в Ф1 — заменить тела, НЕ сигнатуры.
- Тексты сообщений — короткие, по образцу таблицы; `_split_message` из telegram_bot для длинных сводок.
- Зона worktree: `backend/models/telegram.py` НЕ трогать — модельная правка уже в Ф0; если чего-то не хватает — эскалация.
