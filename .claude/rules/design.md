---
paths:
  - "frontend-react/**/*.tsx"
  - "frontend-react/**/*.css"
---
# Дизайн-система DDS2

Apple-inspired glassmorphism. Все стили — в `globals.css` (основное) / `(tma)/tma/tma.css` (Telegram Mini App) — НИКОГДА inline.

## Цвета — ТОЛЬКО `var(--color-*)`
Акцент: `--color-accent` (Apple Blue), `--color-accent-hover`, `--color-accent-bg` (акцентная подложка: активный пункт, выбранный чип). Статусы: `--color-success/warning/danger`.

Текст: `--color-text` (основной), `--color-text-muted` (приглушённый), `--color-text-dim` (совсем блёклый). Границы: `--color-border`, `--color-border-inner`, `--color-border-focus`.

Полотно поверхностей: `--color-bg` (#f5f5f7, НЕпрозрачный — sticky-колонки, тултипы, панели), `--color-bg-card` (glass, полупрозрачный), `--color-bg-input` (поля ввода), `--color-bg-sidebar`, `--color-bg-hover` (наведение). Промежуточных `--color-bg-secondary/tertiary/elevated/surface` НЕ существует.

⚠️ Имена пишутся ЦЕЛИКОМ, сокращений нет: `--color-text-muted` / `--color-text-dim`, а не `--color-muted` / `--color-dim`. Один и тот же класс дефекта ловили дважды — 50 вхождений в модуле дизайна карточек и ещё 174 в 36 файлах остального фронта.

**Сверяйся с `@theme` в `globals.css` — переменной вне этого списка НЕТ.** `var(--несуществующая)` без fallback делает всё объявление невалидным: фон молча остаётся прозрачным, а `color`/`border-color` откатываются к `currentColor` (тёмный текст). Ошибка тихая — ни сборка, ни tsc, ни браузерная консоль её не ловят.

**Добавляешь токен в `@theme` — сошлись на него `var(--x)` внутри самого `globals.css`.** Tailwind v4 эмитит в `:root` только «использованные» переменные, а inline-стиль в `.tsx` за использование не считается: иначе токен не доедет до браузера и сломается ровно так же тихо.

Сток замороженной колонки (`position: sticky`) и тултипа — всегда НЕпрозрачный фон, иначе сквозь него видно уезжающий контент. Полупрозрачный тинт делать через `color-mix(in srgb, var(--color-accent) 6%, var(--color-bg))`, не через `rgba()`.

## Glass-карточки
Класс `glass-card`: `backdrop-filter: blur(24px)`, `border-radius: 20px`, `padding: 24px`. Тени: `--shadow-glass` / `--shadow-glass-hover`.

`backdrop-filter` **в CSS-файлах** — писать ТОЛЬКО стандартное свойство: `-webkit-` допишет Lightning CSS на прод-сборке. Если префикс всё же пишется руками, он обязан идти ПЕРЕД стандартным: Lightning схлопывает пару в одно объявление и оставляет последнюю форму, поэтому при обратном порядке из бандла выпадает стандартная — а `-webkit-backdrop-filter` убран из актуальных Chrome, и размытие пропадает во всём приложении. В dev проход не запускается, дефект виден только в прод-сборке. Закреплено тестом `src/__tests__/globalsBackdropFilter.test.ts`.

**Мимо Lightning — обе формы обязательны, руками.** Правило выше действует только для CSS-файлов. Инлайновый `style={{...}}` и сырой JSX-тег `<style>` с CSS-строкой до Lightning не доходят, префикс им никто не допишет: React ставит каждый ключ отдельно и `-webkit-`-алиас для camelCase не эмитит. Поэтому там пишем пару — `WebkitBackdropFilter` рядом с `backdropFilter` (порядок неважен, это разные ключи CSSOM), а внутри JSX-строки CSS — `-webkit-backdrop-filter` ПЕРЕД стандартным, чтобы правило пережило возможный переезд в `globals.css`. Иначе на Safari < 18 размытие не появится, а гашение (`'none'`) не снимет blur, унаследованный от класса. Не «упрощать», убирая `-webkit-` форму: тест выше её не увидит — он читает только `globals.css`.

## Скругления: 8 (мини) / 12 (кнопки) / 14 (TMA) / 20 (карточки) / 24 (бейджи-pill)
## Отступы: сетка 4/8px (стандарт: 4, 8, 12, 16, 20, 24, 32, 48)

## Типографика (Inter)
Заголовок 28/700, метрика 32/700, тело 14-15/400, мелкий 12-13/500.

## Классы: `btn btn-primary/secondary/danger/success/sm`, `badge badge-success/warning/danger/info/secondary`

## Анимации: `animate-in` (fadeIn 0.3s), hover `translateY(-2px)`, transition `0.2-0.3s cubic-bezier`.

## TMA: `.tma-*` префикс, `14px` скругления, `--tma-*` переменные, компактные отступы.

## Нельзя: hex-цвета в компонентах, inline стили, свои тени, radius вне сетки, анимации >0.5s.
