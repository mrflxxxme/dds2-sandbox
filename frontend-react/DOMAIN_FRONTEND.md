# Domain: Frontend (Next.js 15 + React 19)

## Ownership
- `src/app/p/[slug]/` — все страницы (dashboard, import, txn, inbox, reports, dds, planning, orders, cost, funnel, trends, refs, settings, team)
- `src/lib/api.ts` — API клиент (80+ методов, JWT auth + refresh)
- `src/lib/utils.ts` — formatNumber, formatDate, exportToExcel
- `src/components/` — DataTable, FormModal, PageHeader, TabLayout, Toast
- `src/types/api.ts` — TypeScript интерфейсы

## Rules
1. **Типы:** ВСЕГДА в `types/api.ts` (НИКОГДА inline)
2. **API вызовы:** ВСЕГДА через методы `api.ts` (НИКОГДА прямой fetch, кроме FormData upload)
3. **Форматирование:** formatNumber() для чисел, formatDate() для дат (НИКОГДА сырые значения)
4. **Таблицы:** ВСЕГДА кнопка Excel export (exportToExcel)
5. **Состояния:** ОБЯЗАТЕЛЬНО loading, error, empty states
6. **Стили:** CSS классы из globals.css (НИКОГДА inline styles)
7. **Callbacks:** useCallback для стабильных ссылок
8. **Новый endpoint:** добавить метод в api.ts + тип в types/api.ts

## Page Structure
```tsx
'use client'
import { useState, useEffect, useCallback } from 'react'
import { useProject } from '@/lib/project-context'
import { api } from '@/lib/api'

export default function FeaturePage() {
  const { project } = useProject()
  const [data, setData] = useState<FeatureType[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadData = useCallback(async () => {
    try {
      setLoading(true)
      const result = await api.getFeature(project.slug)
      setData(result)
    } catch (e) {
      setError('Ошибка загрузки')
    } finally {
      setLoading(false)
    }
  }, [project.slug])

  useEffect(() => { loadData() }, [loadData])

  if (loading) return <Loading />
  if (error) return <Error message={error} />
  if (!data.length) return <Empty />

  return <DataTable data={data} />
}
```

## Upload Pattern (FormData)
```tsx
const formData = new FormData()
formData.append('file', file)
formData.append('source_type', sourceType)

const response = await fetch(`${API_URL}/api/v1/import/upload`, {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${token}`,
    'X-Project-Id': String(project.id),
  },
  body: formData,
})
```

## Dependencies
- Backend API: все данные приходят через REST API
- JWT: access token (30 min) + refresh token (30 days) — автоматический refresh в api.ts
