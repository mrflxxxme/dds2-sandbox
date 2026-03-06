---
name: new-page
description: Шаблон для создания новой страницы в Next.js frontend (page.tsx, API методы, sidebar, стили)
---

# Skill: Новая страница Frontend

> ⚠️ **Прочитай `AGENTS.md` — секции ЗАПРЕЩЕНО и ОБЯЗАТЕЛЬНО — перед началом.**

## Шаги

### 1. Определи что нужно
- Название страницы и URL slug
- Какие данные отображаются?
- Какие API эндпоинты нужны? (должны уже существовать в backend)
- Есть ли табы / формы / таблицы?

### 2. TypeScript типы (frontend-react/src/types/api.ts)
Добавь интерфейсы в **типы** (НЕ inline):
```typescript
export interface Feature {
  id: number;
  name: string;
  amount: number;
  created_at: string;
}

export interface FeatureCreate {
  name: string;
  amount: number;
}
```

### 3. API методы (frontend-react/src/lib/api.ts)
Добавь методы в класс `ApiClient`:
```typescript
// === Feature ===
async getFeatures(limit = 100, offset = 0): Promise<Feature[]> {
  return this.request<Feature[]>('GET', `/feature/?limit=${limit}&offset=${offset}`);
}

async createFeature(data: FeatureCreate): Promise<Feature> {
  return this.request<Feature>('POST', '/feature/', data);
}

async deleteFeature(id: number): Promise<void> {
  return this.request('DELETE', `/feature/${id}`);
}
```

### 4. Страница (frontend-react/src/app/p/[slug]/feature/page.tsx)
```tsx
'use client';

import { useState, useEffect, useCallback } from 'react';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import type { Feature } from '@/types/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';

export default function FeaturePage() {
  const params = useParams();
  const slug = params.slug as string;
  const [data, setData] = useState<Feature[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // ✅ useCallback для стабильной ссылки
  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      const result = await api.getFeatures();
      setData(result);
    } catch (err: any) {
      setError(err.message || 'Ошибка загрузки');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  // ✅ Loading state
  if (loading) return <div className="page-loading">Загрузка...</div>;
  // ✅ Error state
  if (error) return <div className="error-message">{error}</div>;

  return (
    <div className="page-container">
      <div className="page-header">
        <h1>Feature Name</h1>
        <div className="header-actions">
          {/* ✅ Excel export для каждой таблицы */}
          <button className="btn-secondary" onClick={() => exportToExcel(data, 'features')}>
            📥 Excel
          </button>
          <button className="btn-primary" onClick={() => {/* TODO */}}>
            + Добавить
          </button>
        </div>
      </div>

      {/* ✅ Empty state */}
      {data.length === 0 ? (
        <div className="glass-card" style={{ textAlign: 'center', padding: '2rem' }}>
          Нет данных
        </div>
      ) : (
        <div className="glass-card">
          <table className="data-table">
            <thead>
              <tr>
                <th>Название</th>
                <th>Сумма</th>
                <th>Дата</th>
              </tr>
            </thead>
            <tbody>
              {data.map(item => (
                <tr key={item.id}>
                  <td>{item.name}</td>
                  {/* ✅ formatNumber для чисел */}
                  <td>{formatNumber(item.amount)}</td>
                  {/* ✅ formatDate для дат */}
                  <td>{formatDate(item.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

### 5. Sidebar (frontend-react/src/app/p/[slug]/layout.tsx)
Добавь пункт в навигацию:
```tsx
{ href: `/p/${slug}/feature`, icon: '📦', label: 'Feature Name' },
```

### 6. Стили
Используй существующие CSS классы из `globals.css`:
- `glass-card` — карточка с glassmorphism
- `data-table` — таблица данных
- `btn-primary`, `btn-secondary` — кнопки
- `badge-*` — бейджи статусов
- `page-container`, `page-header` — layout
- `page-loading`, `error-message` — состояния

⛔ **НЕ используй inline стили** — только CSS классы.

## ⛔ Чеклист (обязательный)

- [ ] Типы в `types/api.ts` — НЕ inline
- [ ] API методы в `lib/api.ts` класс `ApiClient`
- [ ] Страница в `p/[slug]/feature/page.tsx`
- [ ] **`'use client'`** directive
- [ ] **Loading state** — индикатор загрузки
- [ ] **Error state** — сообщение об ошибке
- [ ] **Empty state** — пустое состояние
- [ ] **`useCallback`** для функций загрузки данных
- [ ] **`formatNumber()`** для всех чисел
- [ ] **`formatDate()`** для всех дат
- [ ] **«📥 Excel»** кнопка для таблиц
- [ ] **CSS классы** из `globals.css` — не inline стили
- [ ] Пункт в sidebar `layout.tsx`
- [ ] Обновлён `AGENTS.md` + `docs/MODULES.md`
- [ ] Коммит через `/dev`
