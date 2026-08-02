---
name: design-token-emission-invariant
description: Новый токен в @theme (globals.css) не доедет до браузера, если на него нет ссылки из самого CSS — Tailwind v4 отдаёт только «использованные» переменные
metadata:
  type: project
---

Новая переменная в `@theme` блоке `frontend-react/src/app/globals.css` обязана быть хотя бы один раз использована через `var(--x)` **внутри самого globals.css**. Ссылок из inline-стилей `.tsx` НЕДОСТАТОЧНО.

**Why:** Tailwind v4 (в проекте 4.3.3) не эмитит все theme-переменные в `:root` — у него есть `ThemeOptions.USED` / `ThemeOptions.STATIC` (см. `node_modules/tailwindcss/dist/lib.d.mts`), и по умолчанию в вывод попадают только «использованные». Usage он считает по кандидатам-утилитам из скана исходников и по `var()`-ссылкам в пользовательском CSS; `style={{ background: 'var(--color-x)' }}` в `.tsx` кандидатом не является. Итог — переменной нет в `:root`, `var()` без fallback делает объявление невалидным, свойство тихо не применяется (фон прозрачный, `color`/`border-color` → `currentColor`). Ни сборка, ни `tsc` этого не ловят.

Поэтому при вводе `--color-accent-bg` (2026-08-03) заодно заменили хардкод `rgba(0,113,227,.1)` в `.sidebar-link.active` на `var(--color-accent-bg)` — это не косметика, а гарантия эмиссии токена. Все остальные токены палитры (`--color-bg`, `--color-accent`, `--color-bg-input`, `--color-bg-hover`, `--color-bg-card`, `--color-text`, `--color-border`, `--color-danger`, `--color-success`) тоже имеют `var()`-ссылки в globals.css — потому и работают.

**How to apply:** в ревью диффа, который добавляет токен в `@theme`, — искать в globals.css хотя бы одно `var(--новый-токен)`. Если его нет, а используется токен только из `.tsx`, это блокирующая находка: фича «не применится» ровно тем же тихим способом, который чинили. Альтернатива для будущего — перевести блок на `@theme static`, тогда эмитятся все переменные. Связано с `.claude/rules/design.md` (раздел «Цвета — ТОЛЬКО var(--color-*)»).
