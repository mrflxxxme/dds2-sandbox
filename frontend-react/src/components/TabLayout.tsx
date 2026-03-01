'use client';

interface Tab {
    key: string;
    label: string;
}

interface TabLayoutProps {
    tabs: Tab[];
    active: string;
    onChange: (key: string) => void;
}

/**
 * TabLayout — horizontal tab switcher.
 *
 * Usage:
 * ```tsx
 * <TabLayout
 *   tabs={[
 *     { key: 'orders', label: '📦 Заказы' },
 *     { key: 'nomenclature', label: '📋 Номенклатура' },
 *   ]}
 *   active={tab}
 *   onChange={setTab}
 * />
 * ```
 */
export default function TabLayout({ tabs, active, onChange }: TabLayoutProps) {
    return (
        <div style={{ display: 'flex', gap: 4, marginBottom: 20 }}>
            {tabs.map(t => (
                <button
                    key={t.key}
                    className={`btn ${active === t.key ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                    onClick={() => onChange(t.key)}
                >
                    {t.label}
                </button>
            ))}
        </div>
    );
}
