'use client';
import { useState } from 'react';
import { api } from '@/lib/api';

const importTypes = [
    { id: 'vtb_rub', label: 'ВТБ Рубли (.xlsx)', icon: '🏦' },
    { id: 'vtb_cny', label: 'ВТБ Юани (.xlsx)', icon: '🏦' },
    { id: 'wb', label: 'Wildberries (.xlsx)', icon: '🟣' },
    { id: 'orders', label: 'Заказы (.xlsx)', icon: '📦' },
    { id: 'planned_payments', label: 'Плановые платежи (.xlsx)', icon: '📅' },
];

export default function ImportPage() {
    const [file, setFile] = useState<File | null>(null);
    const [sourceType, setSourceType] = useState('vtb_rub');
    const [loading, setLoading] = useState(false);
    const [result, setResult] = useState<any>(null);
    const [error, setError] = useState('');

    const handleUpload = async () => {
        if (!file) return;
        setLoading(true);
        setError('');
        setResult(null);
        try {
            const res = await api.uploadFile(file, sourceType);
            setResult(res);
            setFile(null);
        } catch (e: any) {
            setError(e.message);
        }
        setLoading(false);
    };

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Импорт данных</h1>
                    <p className="page-subtitle">Загрузка банковских выписок и данных</p>
                </div>
            </div>

            <div className="glass-card" style={{ marginBottom: 24 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>📥 Загрузить файл</h3>

                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
                    {importTypes.map(t => (
                        <button key={t.id}
                            className={`btn ${sourceType === t.id ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                            onClick={() => setSourceType(t.id)}>
                            {t.icon} {t.label}
                        </button>
                    ))}
                </div>

                <div style={{ display: 'flex', gap: 12, alignItems: 'end' }}>
                    <div className="form-group" style={{ flex: 1 }}>
                        <label className="form-label">Файл (.xlsx)</label>
                        <input className="form-input" type="file" accept=".xlsx,.xls,.csv"
                            onChange={e => setFile(e.target.files?.[0] || null)}
                            style={{ padding: '8px 12px' }} />
                    </div>
                    <button className="btn btn-primary" onClick={handleUpload}
                        disabled={!file || loading}>
                        {loading ? '⏳ Загрузка...' : '📤 Загрузить'}
                    </button>
                </div>
            </div>

            {error && (
                <div className="auth-error" style={{ marginBottom: 16 }}>
                    {error}
                    <span style={{ float: 'right', cursor: 'pointer' }} onClick={() => setError('')}>✕</span>
                </div>
            )}

            {result && (
                <div className="glass-card" style={{ borderLeft: '3px solid var(--color-success)' }}>
                    <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 12, color: 'var(--color-success)' }}>
                        ✅ Импорт завершён
                    </h3>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
                        <div className="stat-card">
                            <div className="stat-card-label">Обработано строк</div>
                            <div className="stat-card-value" style={{ fontSize: 22 }}>{result.rows_raw ?? result.total ?? '—'}</div>
                        </div>
                        <div className="stat-card">
                            <div className="stat-card-label">Вставлено</div>
                            <div className="stat-card-value" style={{ fontSize: 22, color: 'var(--color-success)' }}>
                                {result.rows_inserted ?? '—'}
                            </div>
                        </div>
                        <div className="stat-card">
                            <div className="stat-card-label">Пропущено</div>
                            <div className="stat-card-value" style={{ fontSize: 22, color: 'var(--color-warning)' }}>
                                {result.rows_skipped ?? '—'}
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
