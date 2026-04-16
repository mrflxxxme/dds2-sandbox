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

## Именование файлов
- Компоненты: PascalCase.tsx
- Хуки: useXxx.ts
- API модули: xxx.ts в `lib/api/`
- Типы: xxx.ts в `types/`
