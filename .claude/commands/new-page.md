---
description: "Новая страница Next.js DDS2: types → api → page с loading/error/empty states."
---

# /new-page — новая страница frontend

## Параметры (спросить, если не дано)
Имя страницы, раздел (main / tma), backend-endpoint, тип (list+CRUD / dashboard / report / form).

## Порядок
1. **Скопируй похожую страницу** — не пиши с нуля. Main: `src/app/(main)/p/[slug]/{похожая}/page.tsx`; TMA: `src/app/(tma)/tma/[slug]/{похожая}/`.
2. **Типы** — `src/types/api.ts`. Без inline / `any`.
3. **API-метод** — `src/lib/api/{domain}.ts`, без raw `fetch` (исключение — FormData-аплоад). Новый домен → создать модуль + экспорт в `src/lib/api.ts`.
4. **Page** — `src/app/(main)/p/[slug]/{name}/page.tsx`. Обязательны loading / error / empty / data состояния. `PageGuard` для прав, `PageHeader` для заголовка. Числа — `formatNumber()`, даты — `formatDate()`. Таблицы — `TanStackDataTable` + Excel-экспорт. Формы — `FormModal`.
5. **Тесты** — `cd frontend-react && npx vitest run`; smoke `npx playwright test tests/e2e/smoke.spec.ts`.

## Frontend в Docker
Production-сборка standalone, HMR нет. Локальный dev: `npm run dev`. В docker: `docker compose build frontend && docker compose up -d frontend`.

Детали правил фронта — `.claude/rules/frontend.md` и `design.md`.
