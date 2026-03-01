'use client';

interface PageHeaderProps {
    title: string;
    subtitle?: string;
    icon?: string;
    actions?: React.ReactNode;
}

/**
 * PageHeader — consistent page header with title, subtitle, and action buttons.
 *
 * Usage:
 * ```tsx
 * <PageHeader
 *   title="Операции"
 *   subtitle="Все движения денежных средств"
 *   actions={<button className="btn btn-primary btn-sm">+ Добавить</button>}
 * />
 * ```
 */
export default function PageHeader({ title, subtitle, icon, actions }: PageHeaderProps) {
    return (
        <div className="page-header">
            <div>
                <h1 className="page-title">{icon && `${icon} `}{title}</h1>
                {subtitle && <p className="page-subtitle">{subtitle}</p>}
            </div>
            {actions && <div style={{ display: 'flex', gap: 8 }}>{actions}</div>}
        </div>
    );
}
