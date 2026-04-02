---
paths:
  - "frontend-react/**/*.tsx"
  - "frontend-react/**/*.css"
---
# Дизайн-система DDS2

## Философия
Apple-inspired glassmorphism. Мягкие тени, скруглённые углы, frost-эффект.
Все стили в `globals.css` — НИКОГДА inline styles (кроме динамических значений).

## Цвета — ТОЛЬКО CSS-переменные
```
--color-bg: #f5f5f7              фон страницы
--color-bg-card: rgba(255,255,255,0.65)  фон карточек (glass)
--color-text: #1d1d1f            основной текст
--color-text-muted: #86868b      вторичный текст
--color-text-dim: #a1a1aa        третичный текст
--color-accent: #0071e3          основное действие (Apple Blue)
--color-success: #34c759         успех / положительные значения
--color-warning: #ff9f0a         предупреждения
--color-danger: #ff3b30          ошибки / деструктивные действия
--color-border: rgba(0,0,0,0.08) границы
```
НИКОГДА hex-цвета в компонентах — только `var(--color-*)`.

## Скругления
- Карточки: `border-radius: 20px`
- Кнопки, инпуты: `border-radius: 12px`
- Маленькие кнопки: `border-radius: 8px`
- Бейджи: `border-radius: 24px` (pill)
- TMA карточки: `border-radius: 14px`

## Отступы — сетка 4/8px
Стандартные значения: `4, 8, 12, 16, 20, 24, 32, 40, 48, 64`
- Внутри карточки: `padding: 24px`
- Между карточками: `gap: 16px`
- Заголовок → контент: `margin-bottom: 32px`
- Label → input: `gap: 8px`

## Тени
```
--shadow-glass: 0 4px 24px rgba(0,0,0,0.04)         обычное состояние
--shadow-glass-hover: 0 8px 32px rgba(0,0,0,0.08)    hover
```
НИКОГДА тяжёлых теней (> 0.12 opacity).

## Glass-эффект (карточки)
```css
background: var(--color-bg-card);
backdrop-filter: blur(24px) saturate(180%);
-webkit-backdrop-filter: blur(24px) saturate(180%);
border: 1px solid var(--color-border);
box-shadow: inset 0 1px 0 0 var(--color-border-inner), var(--shadow-glass);
```
Класс: `glass-card`. Для новых карточек — наследовать этот паттерн.

## Типографика
- Шрифт: Inter (fallback: -apple-system)
- Заголовок страницы: `28px / 700 / -0.03em`
- Подзаголовок: `15px / 400 / --color-text-muted`
- Метрика (большое число): `32px / 700 / -0.03em`
- Тело текста: `14-15px / 400`
- Мелкий текст/label: `12-13px / 500-600 / --color-text-muted`
- Заголовок таблицы: `12px / 600 / uppercase / 0.05em letter-spacing`

## Кнопки — существующие классы
| Класс | Когда |
|-------|-------|
| `btn btn-primary` | Основное действие (сохранить, создать) |
| `btn btn-secondary` | Вторичное действие (отмена, фильтр) |
| `btn btn-danger` | Деструктивное (удалить) |
| `btn btn-success` | Положительное (подтвердить) |
| `btn btn-sm` | Маленький размер (в таблицах, тулбарах) |

## Бейджи
| Класс | Когда |
|-------|-------|
| `badge badge-success` | Зелёный: оплачен, активен, в наличии |
| `badge badge-warning` | Жёлтый: в процессе, ожидает |
| `badge badge-danger` | Красный: ошибка, отменён, просрочен |
| `badge badge-info` | Синий: новый, информация |
| `badge badge-secondary` | Серый: архив, черновик |

## Анимации
- Появление страницы: `className="animate-in"` (fadeIn 0.3s)
- Модалки: `springIn 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275)`
- Hover карточек: `translateY(-2px)` + усиление тени
- Transition: `all 0.2-0.3s cubic-bezier(0.25, 1, 0.5, 1)`

## Сетки
- KPI карточки: `grid-template-columns: repeat(auto-fit, minmax(200px, 1fr))`
- Класс: `stats-grid` (`gap: 16px`)
- Формы: 2 колонки по умолчанию (`FormModal columns={2}`)

## Паттерн нового компонента
1. Использовать CSS-переменные, НЕ хардкодить цвета
2. Обернуть в `glass-card` или использовать glass-эффект
3. Скругления из стандартной сетки (8/12/14/20/24)
4. Отступы из сетки 4/8px
5. Состояния: default → hover → active → disabled → focus
6. Анимации: плавные, с `cubic-bezier`, не резкие
7. Добавить CSS-класс в `globals.css`, НЕ inline стили

## TMA (Telegram Mini App) — отдельные правила
- Префикс: `.tma-*` для изоляции
- Скругления: `14px` (не 20px)
- Цвета: через `--tma-*` переменные (наследуют тему Telegram)
- Отступы: компактнее (12-16px вместо 24px)
- Навигация: `tma-bottom-nav` с safe-area-inset

## Анти-паттерны (НЕ ДЕЛАТЬ)
- Hex-цвета в компонентах (`#0071e3` → `var(--color-accent)`)
- Inline styles для оформления (только для динамических значений: width, height)
- Свои тени вместо `--shadow-glass` / `--shadow-glass-hover`
- `border-radius` не из стандартной сетки
- Тяжёлые тени (opacity > 0.12)
- Новый шрифт / размер вне системы типографики
- Анимации > 0.5s или без easing
