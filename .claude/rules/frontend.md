---
paths:
  - "frontend-react/**"
---

# Frontend — детали реализации

Высокоуровневые правила — в корневом `CLAUDE.md`. Навигация по фронту — в `frontend-react/CLAUDE.md`. Дизайн-система — в `.claude/rules/design.md`.

## Типы и API
- Все типы — в `src/types/api.ts`. Никаких inline-интерфейсов и `any`.
- Запросы — через `api.*` из `@/lib/api`. Raw `fetch` нельзя (исключение — FormData-аплоады).
- Новый endpoint → тип в `types/api.ts` + метод в `lib/api/{domain}.ts`.
- Числа — `formatNumber()`, даты — `formatDate()`. Не `toFixed` / `toLocaleString`.

## URL и тело запроса
- Query-параметры — только через `URLSearchParams`, не шаблонные строки (`H&M` ломает `&`).
- Пустые массивы/объекты в body — `value ?? null`, не `|| null` (`||` теряет `[]` / `0` / `''`).

## Страница
`'use client'` → `useState` → `useCallback` → `useEffect`. Обязательны состояния loading / error / empty / data. Функции загрузки данных — в `useCallback`.

## Компоненты
`TanStackDataTable` (сортировка + экспорт), `FormModal`, `PageHeader`, `PageGuard`, `KpiCard`, `TabLayout`. Таблицы — всегда с `exportName` для выгрузки в Excel.

## Безопасность
`dangerouslySetInnerHTML` — только с `sanitizeAIHtml()` из `@/lib/sanitize` (DOMPurify). Regex-allowlist запрещён.

## Именование
Компоненты — `PascalCase.tsx`, хуки — `useXxx.ts`, API-модули — `xxx.ts` в `lib/api/`.

## Анти-паттерны
`any`, raw `fetch()`, inline-стили, `console.log` в коммите, страница без loading/error/empty, таблица без Excel-экспорта.
