'use client';

interface ModalShellProps {
    title: string;
    onClose: () => void;
    children: React.ReactNode;
    width?: number;
}

/** Простая модальная оболочка (glass-card поверх затемнения). */
export default function ModalShell({ title, onClose, children, width = 480 }: ModalShellProps) {
    return (
        <div
            style={{
                position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0, 0, 0, 0.5)',
                display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
            }}
            onClick={onClose}
        >
            <div
                className="glass-card animate-in"
                style={{ width, maxWidth: '95vw', maxHeight: '90vh', overflowY: 'auto' }}
                onClick={(e) => e.stopPropagation()}
            >
                <div style={{ display: 'flex', alignItems: 'center', marginBottom: 16 }}>
                    <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>{title}</h3>
                    <button
                        className="btn btn-sm btn-secondary"
                        style={{ marginLeft: 'auto' }}
                        onClick={onClose}
                        aria-label="Закрыть"
                    >
                        ✕
                    </button>
                </div>
                {children}
            </div>
        </div>
    );
}
