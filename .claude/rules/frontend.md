---
paths:
  - "frontend-react/**"
---

# Frontend Development Rules

## Компоненты
- Функциональные компоненты с TypeScript
- Props interface над компонентом: `interface XxxProps { ... }`
- Default export для page components, named export для shared
- React 19 hooks (useActionState, useFormStatus, use)

## Данные
- ТОЛЬКО TanStack Query хуки из `src/lib/api/`
- НИКОГДА raw `fetch()` или axios (кроме FormData upload)
- Все запросы имеют loading/error/empty states
- Мутации инвалидируют связанные queries

## Стили
- Tailwind 4 utility classes
- Tremor компоненты для дашбордов и графиков
- Recharts для визуализаций
- Без inline styles, без CSS modules

## Безопасность HTML
- **`dangerouslySetInnerHTML` разрешён ТОЛЬКО с `sanitizeAIHtml()` из `@/lib/sanitize`** (DOMPurify 3.x)
- Ручные regex-allowlist тегов ЗАПРЕЩЕНЫ — не ловят `onerror=`, `javascript:`, `<svg onload>`
- При расширении разрешённых тегов/атрибутов — добавить регрессионный XSS-тест в `src/__tests__/lib/sanitize.test.ts`

## URL-билдинг в API-модулях
- Параметры в query — ВСЕГДА через `URLSearchParams`, НИКОГДА template literals (`?a=${b}`)
- Пробелы → `+`, спецсимволы → `%XX`; template-строки ломаются на `&`, `+`, `%` в значениях
- Паттерн: `const q = new URLSearchParams({...}); if (opt) q.set('opt', opt); url + '?' + q`

## Truthiness в body/API
- Для передачи возможно-пустых массивов/объектов в body: `value ?? null` (не `||`)
- `[] || null` → `null` (массив теряется); `[] ?? null` → `[]` (сохраняется)
- Актуально для `api.request` где `body: v ? JSON.stringify(v) : undefined`

## Именование файлов
- Компоненты: PascalCase.tsx
- Хуки: useXxx.ts
- API модули: xxx.ts в `lib/api/`
- Типы: xxx.ts в `types/`
