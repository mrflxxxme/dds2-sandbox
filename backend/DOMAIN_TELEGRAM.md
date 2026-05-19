# DOMAIN_TELEGRAM — Telegram Bot & TMA

Telegram-бот для AI-аналитики + Telegram Mini App (TMA).
Бот: пользователи задают вопросы в Telegram, AI отвечает (см. `DOMAIN_AI.md`).
TMA: мобильный дашборд внутри Telegram.

## Таблицы
| Модель | Назначение | Ключ |
|--------|------------|------|
| `TelegramBotUser` | Связь Telegram account ↔ DDS user (через deep link) | `telegram_id` |
| `TelegramChatBinding` | Привязка chat ↔ project + brand + `notify_enabled` | `chat_id` |
| `BrandNote` | Заметки по брендам для AI-контекста (Obsidian-style память) | `(project_id, brand)` |

## Бизнес-правила
- **Авторизация:** deep link → `/start {token}` → линк `telegram_id` ↔ `user_id`.
- **Binding:** один chat = один project + опционально brand.
- **AI Rate Limit:** 20 req/hour на чат, 100 req/day на проект (Redis).
- **Webhook secret:** заголовок `X-Telegram-Bot-Api-Secret-Token` обязателен в production; webhook endpoint НЕ использует JWT — валидация только через secret token (`TELEGRAM_WEBHOOK_SECRET`, непустой в prod).
- **Background processing:** webhook возвращает 200 сразу, AI обрабатывает в `asyncio.create_task`.
- **Polling vs Webhook:** `TELEGRAM_USE_POLLING=true` + `DDS_ENV=development` → polling (local dev); production → webhook. Polling удаляет webhook — никогда не включать для prod-токена.
- **TMA auth:** `POST /tma/auth` проверяет HMAC-подпись `initData` Telegram → JWT tokens. Fallback: по `telegram_id`, затем по `@username` (auto-link).
- **Команды бота:** `/start <token>`, `/setup`, `/brand`, `/notify on|off`, `/note <text>`, `/notes`, `/delnote N`.

TMA-страницы (frontend `src/app/(tma)/tma/[slug]/`): Dashboard (health check, аномалии, план-факт), Capital, Funnel, PnL (БДР+ОПИУ), Pulse, Warehouse, Chat.

## Зависимости
- `DOMAIN_AI` (`services/ai/`) — AI-агенты для обработки вопросов.
- `services/health_check_service.py` — данные для TMA dashboard.
- `funnel/`, `reports/` — данные воронки и отчётов для TMA.
- Anthropic API — `ANTHROPIC_API_KEY`.

## Грабли
- Polling и webhook взаимоисключающи: запуск polling сбрасывает зарегистрированный webhook — фатально для prod-токена.
- Webhook endpoint не защищён JWT — единственная защита это secret token header, он обязан быть непустым в production.

## Файлы
- `models/telegram.py` — `TelegramBotUser`, `TelegramChatBinding`, `BrandNote`.
- `schemas/telegram.py` — Pydantic.
- `services/telegram_service.py` — CRUD bindings/notes, TMA auth.
- `integrations/telegram_bot.py` — aiogram 3 (dp, handlers, deep link auth, polling/webhook).
- `routers/telegram.py` — HTTP API (JWT) для bindings/notes.
- `routers/telegram_webhook.py` — webhook endpoint (без JWT, secret token).
- `routers/telegram_miniapp.py` — TMA API (auth, chat, projects).
- `scheduler/jobs/ai_digest.py` — утренний дайджест (7:00 MSK).
