'use client';
import { useEffect, useState, useMemo } from 'react';
import { api } from '@/lib/api';
import { formatDateTime } from '@/lib/utils';
import { usePermissions } from '@/lib/hooks/usePermissions';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';

export function Integrations() {
    const { canEdit } = usePermissions();
    const [keys, setKeys] = useState<any[]>([]);
    const [syncLog, setSyncLog] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [showAdd, setShowAdd] = useState(false);
    const [service, setService] = useState<'wb' | 'wb_advert' | 'wb_content' | 'wb_feedbacks'>('wb');
    const [apiKey, setApiKey] = useState('');
    const [label, setLabel] = useState('');
    const [syncing, setSyncing] = useState<number | null>(null);
    const [msg, setMsg] = useState('');

    useEffect(() => { loadData(); }, []);
    const loadData = async () => {
        try { const [k, s] = await Promise.all([api.getIntegrationKeys(), api.getSyncLog()]); setKeys(k); setSyncLog(s); } catch { }
        setLoading(false);
    };
    const addKey = async () => {
        if (!apiKey.trim()) return;
        try { await api.addIntegrationKey(service, apiKey.trim(), label || undefined); setApiKey(''); setLabel(''); setService('wb'); setShowAdd(false); setMsg(''); loadData(); } catch (e: any) { setMsg(e.message); }
    };
    const deleteKey = async (id: number) => { if (!confirm('Удалить ключ?')) return; await api.deleteIntegrationKey(id); loadData(); };
    const syncWb = async (keyId: number) => {
        setSyncing(keyId);
        try { const d = new Date(); d.setDate(d.getDate() - 7); await api.syncFunnel(d.toISOString().slice(0, 10), new Date().toISOString().slice(0, 10)); setMsg('Синхронизация завершена'); loadData(); } catch (e: any) { setMsg(e.message); }
        setSyncing(null);
    };

    const syncLogColumns: Column[] = useMemo(() => [
        {
            key: 'service', label: 'Сервис',
            render: (v: any) => <span className="badge badge-info">{v}</span>,
        },
        { key: 'sync_type', label: 'Тип' },
        {
            key: 'status', label: 'Статус',
            render: (v: any) => (
                <span className={`badge ${v === 'OK' ? 'badge-success' : v === 'RUNNING' ? 'badge-warning' : v === 'ERROR' || v === 'TIMEOUT' ? 'badge-danger' : 'badge-secondary'}`}>{v}</span>
            ),
        },
        {
            key: 'started_at', label: 'Начало',
            render: (v: any) => <span style={{ fontSize: 13 }}>{formatDateTime(v)}</span>,
        },
        { key: 'rows_fetched', label: 'Строк получено', format: 'number' as const },
        { key: 'rows_inserted', label: 'Вставлено', format: 'number' as const },
        {
            key: 'error_msg', label: 'Ошибка',
            render: (v: any) => <span style={{ color: 'var(--color-danger)', fontSize: 13 }}>{v || '—'}</span>,
        },
    ], []);

    if (loading) return <div style={{ padding: 40, color: 'var(--color-text-muted)' }}>Загрузка...</div>;

    return (
        <>
            {msg && (<div className="auth-error" style={{ marginBottom: 16 }}>{msg}<span style={{ float: 'right', cursor: 'pointer' }} onClick={() => setMsg('')}>✕</span></div>)}
            <div className="glass-card" style={{ marginBottom: 24 }}>
                <div className="table-toolbar">
                    <h3 style={{ fontSize: 16, fontWeight: 600 }}>🔌 API Интеграции</h3>
                    {canEdit() && <button className="btn btn-primary btn-sm" onClick={() => setShowAdd(true)}>+ Добавить ключ</button>}
                </div>
                {showAdd && (
                    <div style={{ background: 'var(--color-bg-input)', border: '1px solid var(--color-border)', borderRadius: 10, padding: 16, marginBottom: 16 }}>
                        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                            <div className="form-group" style={{ width: 200 }}><label className="form-label">Тип ключа</label><select className="form-input" value={service} onChange={e => setService(e.target.value as 'wb' | 'wb_advert' | 'wb_content' | 'wb_feedbacks')}><option value="wb">Основной (статистика, финансы)</option><option value="wb_advert">Реклама (Продвижение)</option><option value="wb_content">Контент (карточки, АБ-тесты фото)</option><option value="wb_feedbacks">Отзывы (Вопросы и отзывы)</option></select></div>
                            <div className="form-group" style={{ flex: 1, minWidth: 200 }}><label className="form-label">API Ключ Wildberries</label><input className="form-input" placeholder="Вставьте API ключ" value={apiKey} onChange={e => setApiKey(e.target.value)} autoFocus /></div>
                            <div className="form-group" style={{ width: 180 }}><label className="form-label">Название</label><input className="form-input" placeholder="Опционально" value={label} onChange={e => setLabel(e.target.value)} /></div>
                        </div>
                        {service === 'wb_advert' && (<div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 8 }}>Токен должен быть выпущен в кабинете WB с категорией «Продвижение». Используется для авто-пополнения бюджета кампаний.</div>)}
                        {service === 'wb_content' && (<div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 8 }}>Токен с категорией «Контент» и правом записи. Используется АБ-тестами: сервис будет менять главное фото карточки во время теста.</div>)}
                        {service === 'wb_feedbacks' && (<div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 8 }}>Токен с категорией «Вопросы и отзывы». Используется разделом «Отзывы»: зеркалит отзывы покупателей в БД для сводной аналитики.</div>)}
                        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                            <button className="btn btn-primary btn-sm" onClick={addKey}>Добавить</button>
                            <button className="btn btn-secondary btn-sm" onClick={() => setShowAdd(false)}>Отмена</button>
                        </div>
                    </div>
                )}
                {keys.length === 0 ? (
                    <div className="empty-state"><div className="empty-state-icon">🔑</div><div className="empty-state-text">Нет подключенных API ключей</div></div>
                ) : (
                    keys.map(k => (
                        <div key={k.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 0', borderBottom: '1px solid var(--color-border)' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                                <div style={{ width: 36, height: 36, borderRadius: 8, background: 'rgba(139, 92, 246, 0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16 }}>W</div>
                                <div><div style={{ fontWeight: 500, display: 'flex', alignItems: 'center', gap: 8 }}>{k.label || 'Wildberries API'}<span className={`badge ${k.service === 'wb_advert' ? 'badge-warning' : k.service === 'wb_content' ? 'badge-success' : k.service === 'wb_feedbacks' ? 'badge-info' : 'badge-info'}`}>{k.service === 'wb_advert' ? 'Реклама' : k.service === 'wb_content' ? 'Контент' : k.service === 'wb_feedbacks' ? 'Отзывы' : k.service === 'wb_analytics' ? 'Аналитика' : 'Основной'}</span></div><div style={{ fontSize: 12, color: 'var(--color-text-dim)', fontFamily: 'monospace' }}>{k.key_preview || '••••••••'}</div></div>
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                <span className="badge badge-success">Подключено</span>
                                <button className="btn btn-success btn-sm" onClick={() => syncWb(k.id)} disabled={syncing === k.id}>{syncing === k.id ? '⏳' : '🔄'} Синхронизировать</button>
                                <button className="btn btn-danger btn-sm" onClick={() => deleteKey(k.id)}>✕</button>
                            </div>
                        </div>
                    ))
                )}
            </div>
            <div className="glass-card">
                <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>📋 Логи синхронизации</h3>
                <TanStackDataTable
                    columns={syncLogColumns}
                    data={syncLog}
                    emptyText="Синхронизаций пока не было"
                    enableSorting
                    enablePagination={false}
                />
            </div>
        </>
    );
}
