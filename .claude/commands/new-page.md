---
description: "Создать новую страницу в Next.js фронтенде DDS2: types → api → page с loading/error/empty states."
---

# /new-page — Новая страница DDS2 frontend

## Параметры (спрашивай у пользователя)
1. **Имя страницы** (например, `assembly`)
2. **Раздел** (main / tma)
3. **Endpoint API** (какой бэкенд endpoint использовать)
4. **Тип страницы** (list+CRUD / dashboard / report / form)

## Процесс

### 1. Скопируй ближайшую похожую страницу
- Main: `frontend-react/src/app/(main)/p/[slug]/{похожая}/page.tsx`
- TMA: `frontend-react/src/app/(tma)/tma/[slug]/{похожая}/page.tsx`
- НЕ создавай с нуля — копируй существующий паттерн

### 2. Типы → `types/api.ts`
- Файл: `frontend-react/src/types/api.ts`
- НЕ inline / any
- Если backend изменился — обнови существующие типы тоже

### 3. API метод → `lib/api/{domain}.ts`
- Файл: `frontend-react/src/lib/api/{domain}.ts` (или новый)
- НЕ прямой fetch (только метод в api.ts)
- Если новый домен → создай `{domain}.ts` + экспорт в `frontend-react/src/lib/api/index.ts`
- FormData upload — единственное исключение (можно прямой fetch)

### 4. Page component
- Файл: `frontend-react/src/app/(main)/p/[slug]/{name}/page.tsx`
- ОБЯЗАТЕЛЬНО:
  - **Loading state** — спиннер или skeleton
  - **Error state** — `<Toast>` или inline error
  - **Empty state** — пустая таблица с CTA
  - **PageGuard** — если нужны права
  - **PageHeader** — заголовок + хлебные крошки
- Числа: `formatNumber()` из `src/lib/utils.ts`
- Даты: `formatDate()` из `src/lib/utils.ts`
- Таблицы: `<TanStackDataTable>` или `<DataTable>` + кнопка Excel export (`exportToExcel`)
- Формы: `<FormModal>` для модалок

### 5. Регистрация (если нужна навигация)
- Меню sidebar — обычно автоматически по файловой структуре, но проверь `src/components/Sidebar.tsx` если нет
- Иконка Lucide

### 6. Frontend rebuild (если в production режиме)
- ВАЖНО: production использует standalone build, HMR нет (см. `feedback_frontend_rebuild.md`)
- Локально dev: `npm run dev` — HMR работает
- В docker: `docker compose build frontend && docker compose up -d frontend`

### 7. Тест
```bash
cd frontend-react && npx vitest run                            # unit
cd frontend-react && npx playwright test tests/e2e/smoke.spec.ts  # smoke (27 страниц)
```

### 8. Документация
- `CLAUDE.md` — если страница в основном списке (22 страниц), обнови count + список
- Если новая фича/раздел — `backend/DOMAIN_FRONTEND.md` (если есть)
- `bash scripts/check_docs.sh` — passed

## Антипаттерны
- inline `any` или inline тип вместо types/api.ts
- Прямой fetch вместо api.ts метода
- Голое число без `formatNumber()` (пользователь увидит `1234567.89` вместо `1 234 567,89`)
- Нет loading state → пользователь видит пустоту
- Нет error state → silent fail
- Таблица без Excel export

## Отчёт
```
| Файл | Создан/Обновлён |
|------|-----------------|
| types/api.ts | added IAssembly type |
| lib/api/assembly.ts | created (list, get, create, update) |
| app/(main)/p/[slug]/assembly/page.tsx | created |
| CLAUDE.md | updated (22 → 23 страниц) |
```
