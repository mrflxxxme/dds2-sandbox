---
name: new-page
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
Локальный dev идёт в Docker: `docker compose up -d` авто-подхватывает `docker-compose.override.yml` (`next dev --turbopack`, volume-mount `./frontend-react`). HMR на Docker-on-mac правки НЕ всегда ловит → после правок `frontend-react/src/*` делай `docker compose restart frontend-react`. Полная пересборка (после изменения `package.json`): `make build-frontend`. Сервис везде — `frontend-react`, не `frontend`.

Детали правил фронта — `.claude/rules/frontend.md` и `design.md`.
