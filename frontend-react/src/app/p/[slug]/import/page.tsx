'use client';
import { useState, useEffect } from 'react';
import { api } from '@/lib/api';

const importTypes = [
    { id: 'VTB_RUB_MAIN', label: 'ВТБ Рубли Основной (.xlsx)', icon: '🏦', requiresAccount: true },
    { id: 'VTB_RUB_TRANSIT', label: 'ВТБ Рубли Транзит (.xlsx)', icon: '🏦', requiresAccount: true },
    { id: 'VTB_CNY', label: 'ВТБ Юани (.xlsx)', icon: '🏦', requiresAccount: true },
    { id: 'WB_MAIN', label: 'WB Выписка ООО (.xlsx)', icon: '🟣', requiresAccount: true },
    { id: 'WB_PAYOUT', label: 'WB Выписка ИП/Транзит (.xlsx)', icon: '🟣', requiresAccount: true },
    { id: 'WB_CABINET_PAYOUTS', label: 'WB Выплаты (Кабинет WB)', icon: '💰', requiresAccount: false },
];

export default function ImportPage() {
    const [file, setFile] = useState<File | null>(null);
    const [sourceType, setSourceType] = useState('VTB_RUB_MAIN');
    const [accountNo, setAccountNo] = useState('');
    const [accounts, setAccounts] = useState<any[]>([]);

    const [loading, setLoading] = useState(false);
    const [result, setResult] = useState<any>(null);
    const [error, setError] = useState('');

    useEffect(() => {
        api.getAccounts().then(setAccounts).catch(() => { });
    }, []);

    const selectedType = importTypes.find(t => t.id === sourceType);

    const handleUpload = async () => {
        if (!file) return;
        if (selectedType?.requiresAccount && !accountNo) {
            setError('Выберите счет для загрузки выписки');
            return;
        }

        setLoading(true);
        setError('');
        setResult(null);
        try {
            let res;
            if (sourceType === 'WB_CABINET_PAYOUTS') {
                res = await api.uploadWbPayouts(file);
                // fake mapping to match existing UI
                res = {
                    total: res.total_parsed,
                    rows_inserted: res.created + (res.updated || 0),
                    rows_skipped: res.skipped,
                    rows_raw: res.total_parsed
                };
            } else {
                res = await api.uploadFile(file, sourceType, accountNo);
            }
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
                    <p className="page-subtitle">Загрузка банковских выписок и данных WB</p>
                </div>
            </div>

            <div className="glass-card" style={{ marginBottom: 24 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>📥 Загрузить файл</h3>

                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
                    {importTypes.map(t => (
                        <button key={t.id}
                            className={`btn ${sourceType === t.id ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                            onClick={() => { setSourceType(t.id); setError(''); setResult(null); }}>
                            {t.icon} {t.label}
                        </button>
                    ))}
                </div>

                {selectedType?.requiresAccount && (
                    <div className="form-group" style={{ maxWidth: 400, marginBottom: 16 }}>
                        <label className="form-label">Банковский счет</label>
                        <select className="form-input" value={accountNo} onChange={e => setAccountNo(e.target.value)}>
                            <option value="">— Выберите счет —</option>
                            {accounts.map(a => (
                                <option key={a.id} value={a.account}>
                                    {a.account} ({a.bank} {a.currency}) {a.account_name ? `— ${a.account_name}` : ''}
                                </option>
                            ))}
                        </select>
                    </div>
                )}

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
                            <div className="stat-card-label">Вставлено (или обновлено)</div>
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
