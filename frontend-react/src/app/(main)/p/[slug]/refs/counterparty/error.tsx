'use client';

export default function CounterpartyError({
    error,
    reset,
}: {
    error: Error & { digest?: string };
    reset: () => void;
}) {
    return (
        <div className="animate-in">
            <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>
                <div style={{ fontSize: 32, marginBottom: 16 }}>⚠️</div>
                <div style={{ color: 'var(--color-danger)', marginBottom: 16 }}>
                    {error.message || 'Ошибка загрузки контрагентов'}
                </div>
                <button className="btn btn-primary btn-sm" onClick={reset}>
                    Повторить
                </button>
            </div>
        </div>
    );
}
