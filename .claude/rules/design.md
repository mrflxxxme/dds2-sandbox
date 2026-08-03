---
paths:
  - "frontend-react/**/*.tsx"
  - "frontend-react/**/*.css"
---
# Дизайн-система DDS2

Apple-inspired glassmorphism. Все стили — в `globals.css` (основное) / `(tma)/tma/tma.css` (Telegram Mini App) — НИКОГДА inline.

## Цвета — ТОЛЬКО `var(--color-*)`
`--color-accent` (Apple Blue), `--color-success/warning/danger`, `--color-bg-card` (glass), `--color-text/muted/dim`, `--color-border`.

## Glass-карточки
Класс `glass-card`: `backdrop-filter: blur(24px)`, `border-radius: 20px`, `padding: 24px`. Тени: `--shadow-glass` / `--shadow-glass-hover`.

`backdrop-filter` — писать ТОЛЬКО стандартное свойство: `-webkit-` допишет Lightning CSS на прод-сборке. Если префикс всё же пишется руками, он обязан идти ПЕРЕД стандартным: Lightning схлопывает пару в одно объявление и оставляет последнюю форму, поэтому при обратном порядке из бандла выпадает стандартная — а `-webkit-backdrop-filter` убран из актуальных Chrome, и размытие пропадает во всём приложении. В dev проход не запускается, дефект виден только в прод-сборке. Закреплено тестом `src/__tests__/globalsBackdropFilter.test.ts`.

## Скругления: 8 (мини) / 12 (кнопки) / 14 (TMA) / 20 (карточки) / 24 (бейджи-pill)
## Отступы: сетка 4/8px (стандарт: 4, 8, 12, 16, 20, 24, 32, 48)

## Типографика (Inter)
Заголовок 28/700, метрика 32/700, тело 14-15/400, мелкий 12-13/500.

## Классы: `btn btn-primary/secondary/danger/success/sm`, `badge badge-success/warning/danger/info/secondary`

## Анимации: `animate-in` (fadeIn 0.3s), hover `translateY(-2px)`, transition `0.2-0.3s cubic-bezier`.

## TMA: `.tma-*` префикс, `14px` скругления, `--tma-*` переменные, компактные отступы.

## Нельзя: hex-цвета в компонентах, inline стили, свои тени, radius вне сетки, анимации >0.5s.
