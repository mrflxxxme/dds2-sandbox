---
name: new-page
description: Шаблон для создания новой страницы в Next.js frontend (page.tsx, API методы, sidebar, стили)
---

# Skill: Новая страница Frontend

Используй этот skill когда нужно создать новую страницу в frontend-react.

## Шаги

### 1. Определи что нужно
- Название страницы и URL slug
- Какие данные отображаются?
- Какие API эндпоинты нужны? (должны уже существовать в backend)
- Есть ли табы / формы / таблицы?

### 2. API методы (frontend-react/src/lib/api.ts)
Добавь методы в класс `ApiClient`:
```typescript
// === Feature Name ===
async getFeatures(projectId: number): Promise<Feature[]> {
  return this.request<Feature[]>(`/api/v1/feature/?project_id=${projectId}`);
}

async createFeature(data: FeatureCreate): Promise<Feature> {
  return this.request<Feature>('/api/v1/feature/', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}
```

Добавь TypeScript интерфейсы в начало файла или рядом с методами:
```typescript
export interface Feature {
  id: number;
  name: string;
  created_at: string;
}

export interface FeatureCreate {
  name: string;
  project_id: number;
}
```

### 3. Страница (frontend-react/src/app/p/[slug]/feature/page.tsx)
```tsx
'use client';

import { useState, useEffect, useCallback } from 'react';
import { useParams } from 'next/navigation';
import { api, Feature } from '@/lib/api';

export default function FeaturePage() {
  const params = useParams();
  const slug = params.slug as string;
  const [data, setData] = useState<Feature[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      const result = await api.getFeatures(/* projectId */);
      setData(result);
    } catch (err: any) {
      setError(err.message || 'Ошибка загрузки');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  if (loading) return <div className="page-loading">Загрузка...</div>;
  if (error) return <div className="error-message">{error}</div>;

  return (
    <div className="page-container">
      <div className="page-header">
        <h1>Feature Name</h1>
        <div className="header-actions">
          <button className="btn-primary" onClick={() => {/* TODO */}}>
            + Добавить
          </button>
        </div>
      </div>

      <div className="glass-card">
        <table className="data-table">
          <thead>
            <tr>
              <th>Название</th>
              <th>Дата</th>
            </tr>
          </thead>
          <tbody>
            {data.map(item => (
              <tr key={item.id}>
                <td>{item.name}</td>
                <td>{new Date(item.created_at).toLocaleDateString('ru-RU')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

### 4. Sidebar (frontend-react/src/app/p/[slug]/layout.tsx)
Добавь пункт в навигацию:
```tsx
{ href: `/p/${slug}/feature`, icon: '📦', label: 'Feature Name' },
```

### 5. Стили
Используй существующие CSS классы из `globals.css`:
- `glass-card` — карточка с glassmorphism
- `data-table` — таблица данных
- `btn-primary`, `btn-secondary` — кнопки
- `badge-*` — бейджи статусов
- `page-container`, `page-header` — layout

### 6. Чеклист перед завершением
- [ ] API методы добавлены в `api.ts`
- [ ] TypeScript интерфейсы определены
- [ ] Страница создана в `p/[slug]/feature/page.tsx`
- [ ] Пункт добавлен в sidebar `layout.tsx`
- [ ] Используются стандартные CSS классы
- [ ] `formatNumber()` / `formatDate()` для форматирования
- [ ] Кнопка "📥 Excel" для таблиц (`exportToExcel()`)
- [ ] Обработка loading / error состояний
- [ ] Обновлён `AGENTS.md`
- [ ] Обновлён `docs/MODULES.md`
- [ ] Сделан коммит через `/dev`
