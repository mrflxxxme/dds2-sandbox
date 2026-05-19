# Frontend — ориентир

Правила фронта — в `.claude/rules/frontend.md` и `design.md` (грузятся при правке `frontend-react/`). Iron rules — в корневом `CLAUDE.md`.

## Архитектура
Next.js 15 App Router, React 19, TypeScript. Две оболочки:
- `src/app/(main)/p/[slug]/` — основное приложение (~22 страницы: dds, import, txn, inbox, reports, planning, cost, funnel, trends, refs, settings, opiu, orders, plan-fact, team, monitoring, bulk-cost, container-loader, order-geography, warehouse, supply-chain, ai-chat).
- `src/app/(tma)/tma/[slug]/` — Telegram Mini App (capital, chat, funnel, pnl, pulse, warehouse).

## API-слой
Модульный клиент: `src/lib/api/client.ts` (JWT + auto-refresh), реэкспорт через `src/lib/api.ts`. Доменные модули в `src/lib/api/`: auth, client, cost, funnel, imports, integrations, monitoring, planning, projects, refs, reports, supply-chain, telegram, transactions, warehouse, ai-chat.
Новый домен → создать `src/lib/api/{domain}.ts`, добавить в barrel `src/lib/api.ts`.

## Компоненты (`src/components/`)
DataTable / TanStackDataTable (таблицы с сортировкой и Excel-экспортом), FormModal, PageHeader, PageGuard, TabLayout, KpiCard, BoxDetailCell, Toast.

## Типы и утилиты
- Типы — `src/types/api.ts` (единый источник истины).
- `src/lib/utils.ts` — `formatNumber` (ru-RU), `formatDate`, `formatDateTime`, `exportToExcel`.
- `src/lib/hooks/usePermissions.ts` — роли и доступы; `src/lib/telegram.ts` — мост TMA.

## Тесты
- Unit: `npx vitest run` (`src/__tests__/`, ~301 тест — блокирует CI).
- TS-гейт: `tsc --noEmit` блокирует PR.
- E2E smoke: `npx playwright test tests/e2e/smoke.spec.ts` (~27 страниц, nightly, merge не блокирует).
