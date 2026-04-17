# Frontend Context

## Before ANY frontend change
Read `DOMAIN_FRONTEND.md` in this directory.

## Architecture
Next.js 15 App Router, React 19, TypeScript. Two app shells:
- `src/app/(main)/p/[slug]/` — 22 pages (dds, import, txn, inbox, reports, planning,
  cost, funnel, trends, refs, settings, opiu, orders, plan-fact, team, monitoring,
  bulk-cost, container-loader, order-geography, warehouse, supply-chain, ai-chat)
- `src/app/(tma)/tma/[slug]/` — Telegram Mini App (capital, chat, funnel, pnl, pulse, warehouse)

## API layer
Modular client: `src/lib/api/client.ts` (JWT auth + auto-refresh) re-exported
through `src/lib/api.ts`. Domain modules (16 files):
auth, client, cost, funnel, imports, integrations, monitoring,
planning, projects, refs, reports, supply-chain, telegram,
transactions, warehouse, ai-chat

New domain: create `src/lib/api/{domain}.ts`, add to `src/lib/api.ts` barrel.

## Components (`src/components/`)
DataTable, TanStackDataTable — tables with sort + Excel export
FormModal — modal CRUD forms
PageHeader — page title with action buttons
PageGuard — role-based access check
TabLayout — tab navigation
KpiCard — metric cards with sparkline
BoxDetailCell, Toast

## Types
Single source of truth: `src/types/api.ts` (~2000 lines).
NEVER use inline interfaces or `any`.

## Utilities (`src/lib/`)
- `utils.ts` — formatNumber (ru-RU), formatDate, formatDateTime, exportToExcel
- `hooks/usePermissions.ts` — role/permission hook
- `telegram.ts` — TMA bridge utilities

## Conventions
- Types in `types/api.ts`, API calls via `api.*` (NEVER raw fetch)
- Numbers: `formatNumber()`, Dates: `formatDate()` (null returns em-dash)
- Tables: ALWAYS add Excel export button
- Every page MUST have loading, error, empty states
- Styles: CSS classes from `globals.css` (NEVER inline styles)
- Data loading functions wrapped in `useCallback`
- Imports: `@/lib/api`, `@/types/api`, `@/components/*`, `@/lib/utils`

## Testing
- Unit: `npx vitest run` (files in `src/__tests__/`, 42 tests)
- E2E smoke (CI nightly): `npx playwright test tests/e2e/smoke.spec.ts` — 27 страниц не крашатся, ~2 мин
- E2E full (только локально / debug): `npx playwright test` — 73 теста в 11 spec-файлах
- Config: `vitest.config.ts`, `playwright.config.ts`
- E2E **НЕ блокирует** PR/merge/deploy — только nightly-сигнал. Блокирующие: pytest (1368) + vitest (42)

## New endpoint checklist
1. Add TypeScript interface in `src/types/api.ts`
2. Add API method in `src/lib/api/{domain}.ts`
3. Use in component with loading/error/empty states
