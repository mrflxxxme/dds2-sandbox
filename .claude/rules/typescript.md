---
paths:
  - "frontend-react/**/*.ts"
  - "frontend-react/**/*.tsx"
---
# TypeScript / React правила DDS2

## Типы
- ВСЕ типы в `src/types/api.ts` (НИКОГДА inline interface / `any`)
- Новый endpoint → тип в `types/api.ts` + метод в `src/lib/api/` соответствующем модуле
- `import type { X } from '@/types/api'` — всегда `type` import для типов

## API клиент
- ВСЕГДА через `api.*` из `@/lib/api` (НИКОГДА raw `fetch`)
- Единственное исключение — `FormData` upload через `api.uploadFormData()`
- Модули: auth, projects, transactions, reports, refs, cost, planning, funnel, imports, telegram, warehouse, monitoring, supply-chain
- Новый домен → новый файл `src/lib/api/{domain}.ts` + экспорт в `src/lib/api.ts`

## Форматирование данных
- Числа → `formatNumber(value)` из `@/lib/utils` (НИКОГДА `toFixed()` / `toLocaleString()`)
- Даты → `formatDate(value)` или `formatDateTime(value)` (НИКОГДА ручной формат)
- Null/undefined → возвращает `'—'` (em-dash) автоматически через утилиты
- Локаль: `ru-RU` всегда

## Обязательные состояния (КАЖДАЯ страница)
```
loading → <div className="glass-card">Загрузка...</div>
error   → <div className="glass-card" style={{ color: 'var(--color-danger)' }}>{error}</div>
empty   → <div className="empty-state"><div className="empty-state-icon">📋</div>...</div>
data    → основной рендер
```

## Паттерн страницы
```typescript
'use client';
import { useEffect, useState, useCallback } from 'react';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import type { FeatureType } from '@/types/api';

export default function FeaturePage() {
    const { slug } = useParams() as { slug: string };
    const [data, setData] = useState<FeatureType[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const loadData = useCallback(async () => {
        try { setLoading(true); setData(await api.getFeature()); }
        catch (e: any) { setError(e?.message || 'Ошибка загрузки'); }
        finally { setLoading(false); }
    }, []);

    useEffect(() => { loadData(); }, [loadData]);
    // ... loading/error/empty/data states
}
```

## Таблицы
- ВСЕГДА `exportName` prop для Excel экспорта
- Формат колонок: `'number'`, `'date'`, `'money'`, `'money-color'`, `'badge'`
- `maxRows` по умолчанию 500 — для больших данных указывать явно

## Компоненты — использовать существующие
- `DataTable` / `TanStackDataTable` — таблицы с сортировкой, экспортом
- `FormModal` — модальные формы
- `PageHeader` — заголовок с действиями
- `PageGuard` — проверка прав доступа
- `TabLayout` — вкладки
- `KpiCard` — карточки метрик со sparkline

## Импорты — каноничные пути
```typescript
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';
import type { X } from '@/types/api';
import DataTable from '@/components/DataTable';
import { usePermissions } from '@/lib/hooks/usePermissions';
```

## Анти-паттерны (НЕ ДЕЛАТЬ)
- `any` как тип (кроме `catch (e: any)`)
- Прямой `fetch()` вместо `api.*`
- Inline стили вместо CSS классов из `globals.css`
- `console.log` в коммите
- Числа без `formatNumber()`, даты без `formatDate()`
- Страница без loading/error/empty states
- Таблица без Excel экспорта
