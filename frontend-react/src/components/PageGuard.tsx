'use client';
import { usePermissions } from '@/lib/hooks/usePermissions';

interface PageGuardProps {
    page: string;
    children: React.ReactNode;
}

export default function PageGuard({ page, children }: PageGuardProps) {
    const { canAccess, loading } = usePermissions();

    if (loading) {
        return <div style={{ padding: 40, color: 'var(--color-text-muted)' }}>Загрузка...</div>;
    }

    if (!canAccess(page)) {
        return (
            <div className="glass-card" style={{ padding: 40, textAlign: 'center' }}>
                <div style={{ fontSize: 48, marginBottom: 16 }}>🔒</div>
                <h2 style={{ fontSize: 18, fontWeight: 600, marginBottom: 8, color: 'var(--color-text)' }}>
                    Нет доступа к этому разделу
                </h2>
                <p style={{ fontSize: 14, color: 'var(--color-text-muted)' }}>
                    Обратитесь к администратору проекта для получения доступа.
                </p>
            </div>
        );
    }

    return <>{children}</>;
}
