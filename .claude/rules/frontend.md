---
paths:
  - "frontend-react/**"
---

# Frontend правила DDS2 (TypeScript + React + Next.js)

## Типы и API
- ВСЕ типы в `src/types/api.ts` (НИКОГДА inline / `any`)
- API через `api.*` из `@/lib/api` (НИКОГДА raw `fetch`, кроме FormData)
- Новый endpoint → тип в `types/api.ts` + метод в `lib/api/{domain}.ts`
- Числа → `formatNumber()`, даты → `formatDate()` (НИКОГДА `toFixed`/`toLocaleString`)

## URL и body
- Query параметры → ВСЕГДА `URLSearchParams` (НИКОГДА template literals)
- Пустые массивы/объекты в body → `value ?? null` (не `|| null`)

## Страница — обязательная структура
```typescript
'use client';
// useState → useCallback → useEffect → loading/error/empty/data states
```
Обязательно: loading, error, empty, data состояния на КАЖДОЙ странице.

## Компоненты
- `TanStackDataTable` (сортировка + экспорт), `FormModal`, `PageHeader`, `PageGuard`, `KpiCard`
- Таблицы ВСЕГДА с `exportName` для Excel
- Стили: Tailwind 4 + CSS-переменные из `globals.css` (НИКОГДА inline)

## Безопасность
- `dangerouslySetInnerHTML` ТОЛЬКО с `sanitizeAIHtml()` из `@/lib/sanitize`

## Именование
- Компоненты: PascalCase.tsx, хуки: useXxx.ts, API: xxx.ts в `lib/api/`

## Анти-паттерны
- `any`, raw `fetch()`, inline стили, `console.log` в коммите
- Страница без loading/error/empty, таблица без Excel экспорта
