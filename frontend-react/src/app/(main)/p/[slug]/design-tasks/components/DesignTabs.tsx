'use client';

import { useRouter } from 'next/navigation';

export type DesignTabKey = 'board' | 'list' | 'calendar' | 'workload' | 'all';

const TABS: { key: DesignTabKey; label: string; path: (slug: string) => string }[] = [
    { key: 'board', label: 'Доска', path: (s) => `/p/${s}/design-tasks` },
    { key: 'list', label: 'Список', path: (s) => `/p/${s}/design-tasks?view=list` },
    { key: 'calendar', label: 'Календарь', path: (s) => `/p/${s}/design-tasks/calendar` },
    { key: 'workload', label: 'Загрузка', path: (s) => `/p/${s}/design-tasks/workload` },
    { key: 'all', label: 'Все бренды', path: (s) => `/p/${s}/design-tasks/all` },
];

/** Табы разделов модуля «Дизайн карточек» — в шапке каждой страницы (спек F5). */
export default function DesignTabs({ slug, active }: { slug: string; active: DesignTabKey }) {
    const router = useRouter();
    return (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
            {TABS.map(({ key, label, path }) => (
                <button
                    key={key}
                    className={`btn btn-sm ${active === key ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => {
                        if (key === active) return;
                        // Доска⇄Список — одна страница, разный query: replace, чтобы не плодить историю.
                        if ((key === 'board' || key === 'list') && (active === 'board' || active === 'list')) {
                            router.replace(path(slug));
                        } else {
                            router.push(path(slug));
                        }
                    }}
                >
                    {label}
                </button>
            ))}
        </div>
    );
}
