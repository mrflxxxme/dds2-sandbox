'use client';
import { useState, useEffect, useCallback } from 'react';
import { api } from '@/lib/api';
import { usePermissions } from '@/lib/hooks/usePermissions';
import { formatNumber, formatDateTime } from '@/lib/utils';
import type { FakturaStatus, SyncLog } from '@/types/api';

const importTypes = [
    { id: 'VTB_RUB_MAIN', label: 'ВТБ Рубли Основной (.xlsx)', icon: '🏦', requiresAccount: true },
    { id: 'VTB_RUB_TRANSIT', label: 'ВТБ Рубли Транзит (.xlsx)', icon: '🏦', requiresAccount: true },
    { id: 'VTB_CNY', label: 'ВТБ Юани (.xlsx)', icon: '🏦', requiresAccount: true },
    { id: 'VTB_MULTI', label: 'ВТБ Все счета (.xlsx)', icon: '🏦', requiresAccount: false },
    { id: 'WB_MAIN', label: 'WB Выписка ООО (.xlsx)', icon: '🟣', requiresAccount: true },
    { id: 'WB_PAYOUT', label: 'WB Выписка ИП/Транзит (.xlsx)', icon: '🟣', requiresAccount: true },
    { id: 'WB_MULTI', label: 'WB Все счета (.xls)', icon: '🟣', requiresAccount: false },
    { id: 'WB_CABINET_PAYOUTS', label: 'WB Выплаты (Кабинет WB)', icon: '💰', requiresAccount: false },
    { id: 'FAKTURA_WB_BANK', label: 'WB Банк (Faktura.ru .xls)', icon: '📄', requiresAccount: false },
    { id: 'FTS_CUSTOMS', label: 'Таможня ФТС (.pdf)', icon: '🛃', requiresAccount: false },
];

export default function ImportPage() {
    const [file, setFile] = useState<File | null>(null);
    const [sourceType, setSourceType] = useState('VTB_RUB_MAIN');
    const [accountNo, setAccountNo] = useState('');
    const [accounts, setAccounts] = useState<any[]>([]);

    const [loading, setLoading] = useState(false);
    const [result, setResult] = useState<any>(null);
    const [error, setError] = useState('');
    const { canEdit } = usePermissions();

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
            if (sourceType === 'FTS_CUSTOMS') {
                const ftsRes = await api.uploadFtsPdf(file);
                res = {
                    total: ftsRes.created + ftsRes.skipped,
                    rows_inserted: ftsRes.created,
                    rows_skipped: ftsRes.skipped,
                    rows_raw: ftsRes.created + ftsRes.skipped
                };
            } else if (sourceType === 'WB_CABINET_PAYOUTS') {
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

            <FakturaSyncCard canEdit={canEdit()} />

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
                        <label className="form-label">{sourceType === 'FTS_CUSTOMS' ? 'Файл (.pdf)' : 'Файл (.xlsx)'}</label>
                        <input className="form-input" type="file" accept={sourceType === 'FTS_CUSTOMS' ? '.pdf' : '.xlsx,.xls,.csv'}
                            onChange={e => setFile(e.target.files?.[0] || null)}
                            style={{ padding: '8px 12px' }} />
                    </div>
                    {canEdit() && (
                        <button className="btn btn-primary" onClick={handleUpload}
                            disabled={!file || loading}>
                            {loading ? '⏳ Загрузка...' : '📤 Загрузить'}
                        </button>
                    )}
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

function FakturaSyncCard({ canEdit }: { canEdit: boolean }) {
    const [status, setStatus] = useState<FakturaStatus | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [syncing, setSyncing] = useState(false);
    const [result, setResult] = useState<SyncLog | null>(null);

    const loadStatus = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            setStatus(await api.getFakturaStatus());
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки статуса');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { loadStatus(); }, [loadStatus]);

    const handleSync = async () => {
        setSyncing(true);
        setError('');
        setResult(null);
        try {
            const log = await api.syncFaktura();
            setResult(log);
            await loadStatus();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка синхронизации');
        } finally {
            setSyncing(false);
        }
    };

    const lastRun = result ?? status?.last_run ?? null;

    return (
        <div className="glass-card" style={{ marginBottom: 24 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
                <div>
                    <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 4 }}>🏦 Faktura — выписка ВБ Банка</h3>
                    <p style={{ fontSize: 13, color: 'var(--color-text-muted)', margin: 0 }}>
                        Прямая выгрузка по API банка (помимо файла .xls). Синхронизируется автоматически 4×/день.
                    </p>
                </div>
                {canEdit && status?.configured && (
                    <button className="btn btn-primary" onClick={handleSync} disabled={syncing}>
                        {syncing ? '⏳ Обновление...' : '🔄 Обновить выписку'}
                    </button>
                )}
            </div>

            {loading ? (
                <div style={{ fontSize: 13, color: 'var(--color-text-dim)', marginTop: 12 }}>Загрузка статуса...</div>
            ) : error ? (
                <div className="auth-error" style={{ marginTop: 12 }}>
                    {error}
                    <span style={{ float: 'right', cursor: 'pointer' }} onClick={() => setError('')}>✕</span>
                </div>
            ) : !status?.configured ? (
                <div style={{ fontSize: 13, color: 'var(--color-text-dim)', marginTop: 12 }}>
                    Faktura не настроена. Добавьте ключ через <code>scripts/setup_faktura_account.py</code>.
                </div>
            ) : (
                <div style={{ marginTop: 12, display: 'flex', gap: 24, flexWrap: 'wrap', fontSize: 13 }}>
                    <div>
                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Логин</div>
                        <div style={{ fontFamily: 'monospace' }}>{status.login ?? '—'}</div>
                    </div>
                    <div>
                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Последняя синхронизация</div>
                        <div>{status.last_sync_at ? formatDateTime(status.last_sync_at) : '— ещё не было'}</div>
                    </div>
                    {lastRun && (
                        <div>
                            <div style={{ fontSize: 11, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Последний запуск</div>
                            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                                <span className={`badge ${lastRun.status === 'OK' ? 'badge-success' : lastRun.status === 'ERROR' ? 'badge-danger' : 'badge-secondary'}`}>
                                    {lastRun.status === 'OK' ? '✅ OK' : lastRun.status === 'ERROR' ? '✕ Ошибка' : lastRun.status}
                                </span>
                                {lastRun.status === 'OK' && (
                                    <span style={{ color: 'var(--color-text-muted)' }}>
                                        получено {formatNumber(lastRun.rows_fetched, 0)}, добавлено {formatNumber(lastRun.rows_inserted, 0)}
                                    </span>
                                )}
                                {lastRun.status === 'ERROR' && lastRun.error_msg && (
                                    <span style={{ color: 'var(--color-danger)' }} title={lastRun.error_msg}>
                                        {lastRun.error_msg.length > 60 ? `${lastRun.error_msg.slice(0, 60)}…` : lastRun.error_msg}
                                    </span>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
