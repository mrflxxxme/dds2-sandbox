'use client';
import { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatDateTime, formatNumber, exportToExcel } from '@/lib/utils';

const fmt = (n: number) => n?.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) ?? '0';

export default function SettingsPage() {
    const [tab, setTab] = useState<'integrations' | 'nomenclature' | 'leadtimes' | 'duties' | 'taxrates' | 'warehouses'>('integrations');

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">⚙️ Настройки проекта</h1>
                    <p className="page-subtitle">API интеграции, номенклатура, себестоимость, lead times, пошлины</p>
                </div>
            </div>
            <div style={{ display: 'flex', gap: 4, marginBottom: 20, flexWrap: 'wrap' }}>
                {[
                    { key: 'integrations' as const, label: '🔌 API Интеграции' },
                    { key: 'nomenclature' as const, label: '📋 Номенклатура' },
                    { key: 'leadtimes' as const, label: '⏱ Lead Times' },
                    { key: 'duties' as const, label: '⚖️ Пошлины / Утиль' },
                    { key: 'taxrates' as const, label: '📋 Налоговые ставки' },
                    { key: 'warehouses' as const, label: '🏭 Склады' },
                ].map(t => (
                    <button key={t.key} className={`btn ${tab === t.key ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                        onClick={() => setTab(t.key)}>{t.label}</button>
                ))}
            </div>
            {tab === 'integrations' && <Integrations />}
            {tab === 'nomenclature' && <Nomenclature />}
            {tab === 'leadtimes' && <LeadTimes />}
            {tab === 'duties' && <DutyRules />}
            {tab === 'taxrates' && <TaxRates />}
            {tab === 'warehouses' && <WarehouseSettings />}
        </div>
    );
}

/* ─── API Integrations (original settings content) ──────────────── */

function Integrations() {
    const { slug } = useParams() as { slug: string };
    const [keys, setKeys] = useState<any[]>([]);
    const [syncLog, setSyncLog] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [showAdd, setShowAdd] = useState(false);
    const [apiKey, setApiKey] = useState('');
    const [label, setLabel] = useState('');
    const [syncing, setSyncing] = useState<number | null>(null);
    const [msg, setMsg] = useState('');

    useEffect(() => { loadData(); }, []);

    const loadData = async () => {
        try {
            const [k, s] = await Promise.all([api.getIntegrationKeys(), api.getSyncLog()]);
            setKeys(k);
            setSyncLog(s);
        } catch { }
        setLoading(false);
    };

    const addKey = async () => {
        if (!apiKey.trim()) return;
        try {
            await api.addIntegrationKey('wb', apiKey.trim(), label || undefined);
            setApiKey(''); setLabel(''); setShowAdd(false); setMsg(''); loadData();
        } catch (e: any) { setMsg(e.message); }
    };

    const deleteKey = async (id: number) => {
        if (!confirm('Удалить ключ?')) return;
        await api.deleteIntegrationKey(id);
        loadData();
    };

    const syncWb = async (keyId: number) => {
        setSyncing(keyId);
        try {
            const d = new Date(); d.setDate(d.getDate() - 7);
            const dateTo = new Date().toISOString().slice(0, 10);
            const dateFrom = d.toISOString().slice(0, 10);
            await api.syncFunnel(dateFrom, dateTo);
            setMsg('Синхронизация завершена');
            loadData();
        } catch (e: any) { setMsg(e.message); }
        setSyncing(null);
    };

    if (loading) return <div style={{ padding: 40, color: 'var(--color-text-muted)' }}>Загрузка...</div>;

    return (
        <>
            {msg && (
                <div className="auth-error" style={{ marginBottom: 16 }}>{msg}
                    <span style={{ float: 'right', cursor: 'pointer' }} onClick={() => setMsg('')}>✕</span>
                </div>
            )}

            {/* API Keys */}
            <div className="glass-card" style={{ marginBottom: 24 }}>
                <div className="table-toolbar">
                    <h3 style={{ fontSize: 16, fontWeight: 600 }}>🔌 API Интеграции</h3>
                    <button className="btn btn-primary btn-sm" onClick={() => setShowAdd(true)}>+ Добавить ключ</button>
                </div>

                {showAdd && (
                    <div style={{
                        background: 'var(--color-bg-input)', border: '1px solid var(--color-border)',
                        borderRadius: 10, padding: 16, marginBottom: 16
                    }}>
                        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                            <div className="form-group" style={{ flex: 1, minWidth: 200 }}>
                                <label className="form-label">API Ключ Wildberries</label>
                                <input className="form-input" placeholder="Вставьте API ключ" value={apiKey}
                                    onChange={e => setApiKey(e.target.value)} autoFocus />
                            </div>
                            <div className="form-group" style={{ width: 180 }}>
                                <label className="form-label">Название</label>
                                <input className="form-input" placeholder="Опционально" value={label}
                                    onChange={e => setLabel(e.target.value)} />
                            </div>
                        </div>
                        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                            <button className="btn btn-primary btn-sm" onClick={addKey}>Добавить</button>
                            <button className="btn btn-secondary btn-sm" onClick={() => setShowAdd(false)}>Отмена</button>
                        </div>
                    </div>
                )}

                {keys.length === 0 ? (
                    <div className="empty-state">
                        <div className="empty-state-icon">🔑</div>
                        <div className="empty-state-text">Нет подключенных API ключей</div>
                    </div>
                ) : (
                    keys.map(k => (
                        <div key={k.id} style={{
                            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                            padding: '12px 0', borderBottom: '1px solid var(--color-border)'
                        }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                                <div style={{
                                    width: 36, height: 36, borderRadius: 8, background: 'rgba(139, 92, 246, 0.1)',
                                    display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16
                                }}>W</div>
                                <div>
                                    <div style={{ fontWeight: 500 }}>{k.label || 'Wildberries API'}</div>
                                    <div style={{ fontSize: 12, color: 'var(--color-text-dim)', fontFamily: 'monospace' }}>{k.encrypted_key}</div>
                                </div>
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                <span className="badge badge-success">Подключено</span>
                                <button className="btn btn-success btn-sm" onClick={() => syncWb(k.id)} disabled={syncing === k.id}>
                                    {syncing === k.id ? '⏳' : '🔄'} Синхронизировать
                                </button>
                                <button className="btn btn-danger btn-sm" onClick={() => deleteKey(k.id)}>✕</button>
                            </div>
                        </div>
                    ))
                )}
            </div>

            {/* Sync Log */}
            <div className="glass-card">
                <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16 }}>📋 Логи синхронизации</h3>
                {syncLog.length === 0 ? (
                    <div className="empty-state">
                        <div className="empty-state-text">Синхронизаций пока не было</div>
                    </div>
                ) : (
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>Сервис</th><th>Тип</th><th>Статус</th><th>Начало</th>
                                <th>Строк получено</th><th>Вставлено</th><th>Ошибка</th>
                            </tr>
                        </thead>
                        <tbody>
                            {syncLog.map(s => (
                                <tr key={s.id}>
                                    <td><span className="badge badge-info">{s.service}</span></td>
                                    <td>{s.sync_type}</td>
                                    <td><span className={`badge ${s.status === 'OK' ? 'badge-success' : s.status === 'RUNNING' ? 'badge-warning' : s.status === 'ERROR' || s.status === 'TIMEOUT' ? 'badge-danger' : 'badge-secondary'}`}>{s.status}</span></td>
                                    <td style={{ fontSize: 13 }}>{formatDateTime(s.started_at)}</td>
                                    <td>{s.rows_fetched}</td>
                                    <td>{s.rows_inserted}</td>
                                    <td style={{ color: 'var(--color-danger)', fontSize: 13 }}>{s.error_msg || '—'}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>
        </>
    );
}

/* ─── Nomenclature (moved from cost/page.tsx) ──────────────────── */

function Nomenclature() {
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [syncing, setSyncing] = useState(false);
    const [syncMsg, setSyncMsg] = useState('');
    const [lastSyncAt, setLastSyncAt] = useState<string | null>(null);

    const load = async () => { try { setData(await api.getNomenclature()); } catch { } setLoading(false); };
    const loadLastSync = async () => {
        try {
            const logs = await api.getSyncLog();
            const nomLog = logs.find((l: any) => l.sync_type === 'nomenclature' && l.status === 'OK');
            if (nomLog?.finished_at) { setLastSyncAt(nomLog.finished_at); }
        } catch { }
    };
    useEffect(() => { load(); loadLastSync(); }, []);

    const handleSync = async () => {
        setSyncing(true); setSyncMsg('');
        try {
            const result = await api.syncNomenclature();
            if (result.status === 'OK') {
                setSyncMsg(`✅ Синхронизация завершена: ${result.error_msg || 'OK'}`);
                if (result.finished_at) setLastSyncAt(result.finished_at);
            } else {
                setSyncMsg(`❌ Ошибка: ${result.error_msg || 'unknown'}`);
            }
            await load();
        } catch (e: any) { setSyncMsg(`❌ ${e.message}`); }
        setSyncing(false);
    };

    const formatSyncTime = (iso: string) => {
        const d = new Date(iso);
        return d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
    };

    if (loading) return <div style={{ padding: 20, color: 'var(--color-text-muted)' }}>Загрузка...</div>;

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <div>
                    <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Номенклатура ({data.length})</h3>
                    {lastSyncAt && <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginTop: 4 }}>🕐 Последняя синхронизация: {formatSyncTime(lastSyncAt)}</div>}
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <button className="btn btn-primary btn-sm" onClick={handleSync} disabled={syncing}
                        style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        {syncing ? (
                            <><span className="spinner" style={{ width: 14, height: 14, border: '2px solid rgba(255,255,255,0.3)', borderTop: '2px solid #fff', borderRadius: '50%', animation: 'spin 0.8s linear infinite', display: 'inline-block' }} /> Синхронизация...</>
                        ) : '🔄 Синхронизация WB'}
                    </button>
                    {data.length > 0 && <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'nomenclature')}>📥 Excel</button>}
                </div>
            </div>
            {syncMsg && <div style={{ fontSize: 13, marginBottom: 12, padding: '8px 12px', borderRadius: 6, background: syncMsg.startsWith('✅') ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)', color: syncMsg.startsWith('✅') ? 'var(--color-success)' : 'var(--color-danger)' }}>{syncMsg}</div>}
            {data.length > 0 ? (
                <div style={{ overflowX: 'auto', maxHeight: 500, overflowY: 'auto' }}>
                    <table className="data-table">
                        <thead><tr>{Object.keys(data[0]).map(k => <th key={k} style={{ fontSize: 11 }}>{k}</th>)}</tr></thead>
                        <tbody>{data.map((r, i) => <tr key={i}>{Object.values(r).map((v: any, j) => <td key={j} style={{ fontSize: 12 }}>{typeof v === 'number' ? formatNumber(v) : v ?? '—'}</td>)}</tr>)}</tbody>
                    </table>
                </div>
            ) : <div className="empty-state"><div className="empty-state-text">Номенклатура пуста. Нажмите «🔄 Синхронизация WB» для загрузки из WB</div></div>}
        </div>
    );
}

/* ─── Funnel Costs (moved from funnel/page.tsx) ────────────────── */

function FunnelCosts() {
    const { slug } = useParams();
    const router = useRouter();
    const [costs, setCosts] = useState<any>({ overrides: [], missing: [] });
    const [editCost, setEditCost] = useState<{ nm_id: number; cost_price: string } | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => { loadCosts(); }, []);
    const loadCosts = async () => {
        try { setCosts(await api.getFunnelCosts()); } catch { }
        setLoading(false);
    };

    const handleSaveCost = async () => {
        if (!editCost) return;
        try {
            await api.setFunnelCost(editCost.nm_id, parseFloat(editCost.cost_price));
            setEditCost(null);
            loadCosts();
        } catch (e: any) { alert(e.message); }
    };

    if (loading) return <div style={{ padding: 20, color: 'var(--color-text-muted)' }}>Загрузка...</div>;

    return (
        <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <div>
                    <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Ручная себестоимость</h2>
                    <p style={{ fontSize: 13, color: 'var(--color-text-dim)', margin: '4px 0 0' }}>
                        Товары без себестоимости из заказов. Укажите себестоимость за штуку для расчёта прибыли.
                    </p>
                </div>
                <button className="btn btn-primary btn-sm" onClick={() => router.push(`/p/${slug}/bulk-cost`)}>
                    📋 Массово себестоимость
                </button>
            </div>

            {costs.missing?.length > 0 && (
                <div className="glass-card" style={{ marginBottom: 16 }}>
                    <h3 style={{ fontSize: 14, fontWeight: 500, marginBottom: 8, padding: '12px 16px 0' }}>
                        ⚠️ Без себестоимости ({costs.missing.length})
                    </h3>
                    <table className="data-table">
                        <thead><tr><th>nmId</th><th>Артикул</th><th>Предмет</th><th>Бренд</th><th>Себестоимость ₽</th><th></th></tr></thead>
                        <tbody>
                            {costs.missing.map((m: any) => (
                                <tr key={m.nm_id}>
                                    <td><a href={`https://www.wildberries.ru/catalog/${m.nm_id}/detail.aspx`} target="_blank" rel="noreferrer" style={{ color: 'var(--color-accent)' }}>{m.nm_id}</a></td>
                                    <td>{m.vendor_code}</td><td>{m.subject}</td><td>{m.brand}</td>
                                    <td>{editCost?.nm_id === m.nm_id ? (
                                        <input type="number" value={editCost.cost_price} autoFocus
                                            onChange={e => setEditCost({ ...editCost, cost_price: e.target.value })}
                                            onKeyDown={e => e.key === 'Enter' && handleSaveCost()}
                                            style={{ width: 100, background: 'var(--color-bg)', border: '1px solid var(--color-accent)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)' }} />
                                    ) : '—'}</td>
                                    <td>{editCost?.nm_id === m.nm_id ? (
                                        <div style={{ display: 'flex', gap: 4 }}>
                                            <button className="btn-primary" onClick={handleSaveCost} style={{ padding: '2px 8px', fontSize: 12 }}>✓</button>
                                            <button className="btn-secondary" onClick={() => setEditCost(null)} style={{ padding: '2px 8px', fontSize: 12 }}>✕</button>
                                        </div>
                                    ) : (
                                        <button className="btn-secondary" onClick={() => setEditCost({ nm_id: m.nm_id, cost_price: '' })} style={{ padding: '2px 8px', fontSize: 12 }}>✏️</button>
                                    )}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
            {costs.overrides?.length > 0 && (
                <div className="glass-card">
                    <h3 style={{ fontSize: 14, fontWeight: 500, marginBottom: 8, padding: '12px 16px 0' }}>
                        ✅ Установленные ({costs.overrides.length})
                    </h3>
                    <table className="data-table">
                        <thead><tr><th>nmId</th><th>Себестоимость ₽</th><th></th></tr></thead>
                        <tbody>
                            {costs.overrides.map((o: any) => (
                                <tr key={o.nm_id}>
                                    <td><a href={`https://www.wildberries.ru/catalog/${o.nm_id}/detail.aspx`} target="_blank" rel="noreferrer" style={{ color: 'var(--color-accent)' }}>{o.nm_id}</a></td>
                                    <td>{editCost?.nm_id === o.nm_id ? (
                                        <input type="number" value={editCost.cost_price} autoFocus
                                            onChange={e => setEditCost({ ...editCost, cost_price: e.target.value })}
                                            onKeyDown={e => e.key === 'Enter' && handleSaveCost()}
                                            style={{ width: 100, background: 'var(--color-bg)', border: '1px solid var(--color-accent)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)' }} />
                                    ) : fmt(o.cost_price)}</td>
                                    <td>{editCost?.nm_id === o.nm_id ? (
                                        <div style={{ display: 'flex', gap: 4 }}>
                                            <button className="btn-primary" onClick={handleSaveCost} style={{ padding: '2px 8px', fontSize: 12 }}>✓</button>
                                            <button className="btn-secondary" onClick={() => setEditCost(null)} style={{ padding: '2px 8px', fontSize: 12 }}>✕</button>
                                        </div>
                                    ) : (
                                        <button className="btn-secondary" onClick={() => setEditCost({ nm_id: o.nm_id, cost_price: String(o.cost_price) })} style={{ padding: '2px 8px', fontSize: 12 }}>✏️</button>
                                    )}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
            {costs.missing?.length === 0 && costs.overrides?.length === 0 && (
                <div className="glass-card" style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                    Нет данных. Дождитесь автоматической синхронизации воронки.
                </div>
            )}
        </div>
    );
}



/* ─── Lead Times (moved from planning/page.tsx) ────────────────── */

function LeadTimes() {
    const [data, setData] = useState<any[]>([]);
    const [showForm, setShowForm] = useState(false);
    const [form, setForm] = useState({ transport: 'AUTO', stage: '', days: 0 });
    const [msg, setMsg] = useState('');

    useEffect(() => { load(); }, []);
    const load = async () => { try { setData(await api.getLeadTimes()); } catch { } };
    const save = async () => {
        try { await api.upsertLeadTime({ ...form, days: parseInt(String(form.days)) }); setMsg('✅ Сохранено!'); setShowForm(false); load(); } catch (e: any) { setMsg(e.message); }
    };

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Lead Times</h3>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(data, 'lead_times')}>📥 Excel</button>
                    <button className="btn btn-primary btn-sm" onClick={() => setShowForm(!showForm)}>+ Добавить</button>
                </div>
            </div>
            {msg && <div style={{ color: 'var(--color-success)', fontSize: 13, marginBottom: 8 }}>{msg}</div>}

            {showForm && (
                <div style={{ background: 'var(--color-bg-input)', padding: 16, borderRadius: 8, marginBottom: 16, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                    <div className="form-group"><label className="form-label">Транспорт</label>
                        <select className="form-input" value={form.transport} onChange={e => setForm({ ...form, transport: e.target.value })}>
                            <option>AUTO</option><option>RAIL</option><option>AIR</option><option>SEA</option>
                        </select>
                    </div>
                    <div className="form-group"><label className="form-label">Этап</label><input className="form-input" value={form.stage} onChange={e => setForm({ ...form, stage: e.target.value })} /></div>
                    <div className="form-group"><label className="form-label">Дней</label><input className="form-input" type="number" value={form.days} onChange={e => setForm({ ...form, days: parseInt(e.target.value) || 0 })} /></div>
                    <div><button className="btn btn-primary btn-sm" onClick={save}>💾 Сохранить</button></div>
                </div>
            )}

            {data.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead><tr>{Object.keys(data[0]).map(k => <th key={k}>{k}</th>)}</tr></thead>
                        <tbody>{data.map((r, i) => <tr key={i}>{Object.values(r).map((v: any, j) => <td key={j}>{typeof v === 'number' ? formatNumber(v) : v ?? '—'}</td>)}</tr>)}</tbody>
                    </table>
                </div>
            ) : <div className="empty-state"><div className="empty-state-text">Нет данных</div></div>}
        </div>
    );
}

/* ─── Duty Rules (from cost page) ──────────────────────────────── */

function DutyRules() {
    const [rules, setRules] = useState<any[]>([]);
    const [categories, setCategories] = useState<string[]>([]);
    const [showForm, setShowForm] = useState(false);
    const [form, setForm] = useState({ subject: '', basis: 'INVOICE', rate: '0', util_collect_rub: '0', note: '' });
    const [msg, setMsg] = useState('');
    const [vatRate, setVatRate] = useState<string>('22');
    const [vatSaving, setVatSaving] = useState(false);

    useEffect(() => { loadRules(); loadCategories(); loadVatRate(); }, []);
    const loadRules = async () => { try { setRules(await api.getDutyRules()); } catch { } };
    const loadCategories = async () => {
        try {
            const nom = await api.getNomenclature();
            const subjects = [...new Set(nom.map((n: any) => n.subject).filter(Boolean))].sort() as string[];
            setCategories(subjects);
        } catch { }
    };
    const loadVatRate = async () => {
        try {
            const res = await api.getVatRate();
            setVatRate(String(res.vat_rate));
        } catch { }
    };
    const saveVatRate = async () => {
        setVatSaving(true);
        try {
            await api.setVatRate(parseFloat(vatRate) || 22);
            setMsg('✅ Ставка НДС сохранена!');
        } catch (e: any) { setMsg(`❌ ${e.message}`); }
        setVatSaving(false);
    };

    const save = async () => {
        if (!form.subject) { setMsg('❌ Выберите категорию'); return; }
        try {
            await api.addDutyRule({
                subject: form.subject,
                basis: form.basis,
                rate: parseFloat(form.rate) || 0,
                util_collect_rub: parseFloat(form.util_collect_rub) || 0,
                note: form.note || null,
            });
            setMsg('✅ Правило сохранено!');
            setShowForm(false);
            setForm({ subject: '', basis: 'INVOICE', rate: '0', util_collect_rub: '0', note: '' });
            loadRules();
        } catch (e: any) { setMsg(`❌ ${e.message}`); }
    };
    const del = async (id: number) => {
        if (!confirm('Удалить правило?')) return;
        try { await api.deleteDutyRule(id); loadRules(); } catch (e: any) { setMsg(e.message); }
    };
    const editRule = (r: any) => {
        setForm({ subject: r.subject, basis: r.basis, rate: String(r.rate), util_collect_rub: String(r.util_collect_rub), note: r.note || '' });
        setShowForm(true);
    };

    const basisLabels: Record<string, string> = {
        INVOICE: 'От инвойса (%)',
        WEIGHT_EUR_KG: 'От веса (€/кг)',
        SQUARE_METER: 'За м² (€/м²)',
    };

    const ruledSubjects = new Set(rules.map(r => r.subject));
    const unruled = categories.filter(c => !ruledSubjects.has(c));

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <div>
                    <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Пошлина / Утиль / НДС</h3>
                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginTop: 2 }}>
                        Категорий: {categories.length} | С правилами: {rules.length} | Без правил: {unruled.length}
                    </div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(rules, 'duty_rules')}>📥 Excel</button>
                    <button className="btn btn-primary btn-sm" onClick={() => setShowForm(!showForm)}>+ Добавить правило</button>
                </div>
            </div>
            {msg && <div style={{ fontSize: 13, marginBottom: 8, padding: '6px 12px', borderRadius: 6, background: msg.startsWith('✅') ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)', color: msg.startsWith('✅') ? 'var(--color-success)' : 'var(--color-danger)' }}>{msg} <span style={{ float: 'right', cursor: 'pointer' }} onClick={() => setMsg('')}>✕</span></div>}

            {/* VAT Rate */}
            <div style={{
                display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16,
                padding: '12px 16px', borderRadius: 8,
                background: 'var(--color-bg-input)', border: '1px solid var(--color-border)',
            }}>
                <span style={{ fontSize: 14, fontWeight: 600 }}>📋 НДС (для всех категорий):</span>
                <input className="form-input" type="number" step="0.01" value={vatRate}
                    onChange={e => setVatRate(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && saveVatRate()}
                    style={{ width: 80, fontSize: 14, textAlign: 'center' }} />
                <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>%</span>
                <button className="btn btn-primary btn-sm" onClick={saveVatRate} disabled={vatSaving}
                    style={{ fontSize: 12 }}>
                    {vatSaving ? '⏳' : '💾'} Сохранить
                </button>
            </div>

            {showForm && (
                <div style={{ background: 'var(--color-bg-input)', padding: 16, borderRadius: 8, marginBottom: 16 }}>
                    <h4 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Правило пошлины</h4>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12 }}>
                        <div className="form-group">
                            <label className="form-label" style={{ fontSize: 11 }}>Категория (subject)*</label>
                            <select className="form-input" value={form.subject} onChange={e => setForm({ ...form, subject: e.target.value })} style={{ fontSize: 13 }}>
                                <option value="">— Выбрать —</option>
                                {categories.map(c => <option key={c} value={c}>{c}</option>)}
                            </select>
                        </div>
                        <div className="form-group">
                            <label className="form-label" style={{ fontSize: 11 }}>Базис расчёта</label>
                            <select className="form-input" value={form.basis} onChange={e => setForm({ ...form, basis: e.target.value })} style={{ fontSize: 13 }}>
                                <option value="INVOICE">От инвойса (%)</option>
                                <option value="WEIGHT_EUR_KG">От веса (€/кг)</option>
                                <option value="SQUARE_METER">За м² (€/м²)</option>
                            </select>
                        </div>
                        <div className="form-group">
                            <label className="form-label" style={{ fontSize: 11 }}>Ставка</label>
                            <input className="form-input" type="number" step="0.01" value={form.rate}
                                onChange={e => setForm({ ...form, rate: e.target.value })} style={{ fontSize: 13 }} />
                        </div>
                        <div className="form-group">
                            <label className="form-label" style={{ fontSize: 11 }}>Утиль. сбор (₽)</label>
                            <input className="form-input" type="number" step="0.01" value={form.util_collect_rub}
                                onChange={e => setForm({ ...form, util_collect_rub: e.target.value })} style={{ fontSize: 13 }} />
                        </div>
                        <div className="form-group">
                            <label className="form-label" style={{ fontSize: 11 }}>Примечание</label>
                            <input className="form-input" value={form.note} onChange={e => setForm({ ...form, note: e.target.value })} style={{ fontSize: 13 }} />
                        </div>
                    </div>
                    <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                        <button className="btn btn-primary btn-sm" onClick={save}>💾 Сохранить</button>
                        <button className="btn btn-secondary btn-sm" onClick={() => setShowForm(false)}>Отмена</button>
                    </div>
                </div>
            )}

            {rules.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                        <thead><tr><th>ID</th><th>Категория</th><th>Базис</th><th>Ставка</th><th>Утиль ₽</th><th>Примечание</th><th></th></tr></thead>
                        <tbody>{rules.map(r => (
                            <tr key={r.id}>
                                <td style={{ fontSize: 12 }}>{r.id}</td>
                                <td style={{ fontWeight: 500 }}>{r.subject}</td>
                                <td><span className="badge badge-info" style={{ fontSize: 10 }}>{basisLabels[r.basis] || r.basis}</span></td>
                                <td style={{ fontFamily: 'monospace' }}>{r.rate}</td>
                                <td style={{ fontFamily: 'monospace' }}>{r.util_collect_rub}</td>
                                <td style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>{r.note || '—'}</td>
                                <td style={{ display: 'flex', gap: 4 }}>
                                    <button className="btn btn-secondary btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => editRule(r)}>✎</button>
                                    <button className="btn btn-danger btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => del(r.id)}>✕</button>
                                </td>
                            </tr>
                        ))}</tbody>
                    </table>
                </div>
            ) : <div className="empty-state"><div className="empty-state-text">Нет правил. Добавьте правила для категорий из Номенклатуры.</div></div>}

            {unruled.length > 0 && (
                <div style={{ marginTop: 16, padding: 12, background: 'rgba(245,158,11,0.08)', borderRadius: 8, border: '1px solid rgba(245,158,11,0.2)' }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-warning)', marginBottom: 8 }}>⚠️ Категории без правил ({unruled.length}):</div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                        {unruled.slice(0, 30).map(c => (
                            <span key={c} className="badge badge-warning" style={{ fontSize: 10, cursor: 'pointer' }}
                                onClick={() => { setForm({ ...form, subject: c }); setShowForm(true); }}>{c}</span>
                        ))}
                        {unruled.length > 30 && <span style={{ fontSize: 11, color: 'var(--color-text-dim)' }}>...и ещё {unruled.length - 30}</span>}
                    </div>
                </div>
            )}
        </div>
    );
}

/* ─── Tax Rates Settings ───────────────────────────────────────── */

const MONTH_NAMES = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
const QUARTER_MONTHS = [[0,1,2],[3,4,5],[6,7,8],[9,10,11]];
const REGIME_LABELS: Record<string,string> = {
    'usn_income': 'УСН «Доходы»',
    'usn_income_expense_vat': 'УСН «Доходы – Расходы» с фикс. НДС',
};

interface MonthRate {
    month: number;
    usn_rate: number;
    nds_rate: number;
    cost_as_expense: boolean;
}

function TaxRates() {
    const currentYear = new Date().getFullYear();
    const [year, setYear] = useState(currentYear);
    const [taxRegime, setTaxRegime] = useState('usn_income');
    const [months, setMonths] = useState<MonthRate[]>(
        Array.from({ length: 12 }, (_, i) => ({ month: i + 1, usn_rate: 0, nds_rate: 0, cost_as_expense: false }))
    );
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [msg, setMsg] = useState('');
    const [editRegime, setEditRegime] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const data = await api.getTaxRates(year);
            setTaxRegime(data.tax_regime || 'usn_income');
            setMonths(data.months || Array.from({ length: 12 }, (_, i) => ({ month: i + 1, usn_rate: 0, nds_rate: 0, cost_as_expense: false })));
        } catch { }
        setLoading(false);
    }, [year]);

    useEffect(() => { load(); }, [load]);

    const getMonth = (idx: number): MonthRate => {
        return months.find(m => m.month === idx + 1) || { month: idx + 1, usn_rate: 0, nds_rate: 0, cost_as_expense: false };
    };

    const updateRate = (monthIdx: number, field: 'usn_rate' | 'nds_rate', value: number) => {
        setMonths(prev => prev.map(m =>
            m.month === monthIdx + 1 ? { ...m, [field]: value } : { ...m }
        ));
    };

    const updateQuarterRate = (qIdx: number, field: 'usn_rate' | 'nds_rate', value: number) => {
        QUARTER_MONTHS[qIdx].forEach(mi => updateRate(mi, field, value));
    };

    const updateCostAsExpense = (quarterIdx: number, value: boolean) => {
        setMonths(prev => prev.map(m => {
            const mi = m.month - 1;
            if (QUARTER_MONTHS[quarterIdx].includes(mi)) {
                return { ...m, cost_as_expense: value };
            }
            return { ...m };
        }));
    };

    const changeRegime = (regime: string) => {
        setTaxRegime(regime);
        setEditRegime(false);
    };

    const quarterAvg = (qIdx: number, field: 'usn_rate' | 'nds_rate'): number => {
        const vals = QUARTER_MONTHS[qIdx].map(mi => getMonth(mi)[field]);
        const sum = vals.reduce((a, b) => a + b, 0);
        return +(sum / 3).toFixed(2);
    };

    const save = async () => {
        setSaving(true);
        try {
            await api.saveTaxRates({ year, tax_regime: taxRegime, months });
            setMsg('✅ Налоговые ставки сохранены!');
        } catch (e: any) {
            setMsg(`❌ ${e.message}`);
        }
        setSaving(false);
    };

    if (loading) return <div style={{ padding: 40, color: 'var(--color-text-muted)' }}>Загрузка...</div>;

    const inputStyle = {
        width: 65, fontSize: 13, textAlign: 'center' as const,
        background: 'var(--color-bg)', border: '1px solid var(--color-border)',
        borderRadius: 6, padding: '4px 6px', color: 'var(--color-text)',
    };

    return (
        <div>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <h3 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>Налоговые ставки</h3>
                    <select className="form-input" value={year} onChange={e => setYear(+e.target.value)}
                        style={{ width: 90, fontSize: 14 }}>
                        {[currentYear - 1, currentYear, currentYear + 1].map(y =>
                            <option key={y} value={y}>{y}</option>
                        )}
                    </select>
                </div>
                <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>
                    {saving ? '⏳ Сохранение...' : '💾 Сохранить'}
                </button>
            </div>

            {msg && (
                <div style={{
                    fontSize: 13, marginBottom: 12, padding: '8px 12px', borderRadius: 6,
                    background: msg.startsWith('✅') ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)',
                    color: msg.startsWith('✅') ? 'var(--color-success)' : 'var(--color-danger)',
                }}>
                    {msg}
                    <span style={{ float: 'right', cursor: 'pointer' }} onClick={() => setMsg('')}>✕</span>
                </div>
            )}

            {/* Single project-level card */}
            <div className="glass-card" style={{ padding: 20 }}>
                {/* Regime header */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
                    <div style={{
                        width: 36, height: 36, borderRadius: 8,
                        background: 'linear-gradient(135deg, rgba(139,92,246,0.2), rgba(59,130,246,0.2))',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        fontSize: 16,
                    }}>📋</div>
                    <div>
                        <span style={{ fontWeight: 700, fontSize: 15 }}>
                            {REGIME_LABELS[taxRegime] || taxRegime}
                        </span>
                        {editRegime ? (
                            <select className="form-input" value={taxRegime}
                                onChange={e => changeRegime(e.target.value)}
                                style={{ marginLeft: 12, fontSize: 12, width: 'auto' }} autoFocus>
                                {Object.entries(REGIME_LABELS).map(([k, v]) =>
                                    <option key={k} value={k}>{v}</option>
                                )}
                            </select>
                        ) : (
                            <span style={{ marginLeft: 12, fontSize: 12, color: 'var(--color-accent)', cursor: 'pointer' }}
                                onClick={() => setEditRegime(true)}>Изменить</span>
                        )}
                    </div>
                </div>

                {/* Quarters grid */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
                    {QUARTER_MONTHS.map((qMonths, qIdx) => (
                        <div key={qIdx} style={{
                            border: '1px solid var(--color-border)', borderRadius: 8, padding: 12,
                            background: 'var(--color-bg-input)',
                        }}>
                            {/* Quarter header */}
                            <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr 1fr', gap: 6, alignItems: 'center', marginBottom: 10 }}>
                                <span style={{ fontWeight: 600, fontSize: 13 }}>{qIdx + 1} квартал</span>
                                <div style={{ textAlign: 'center' }}>
                                    <div style={{ fontSize: 10, color: 'var(--color-text-dim)', marginBottom: 2 }}>УСН, %</div>
                                    <input type="number" step="0.01" style={inputStyle}
                                        value={quarterAvg(qIdx, 'usn_rate') || ''}
                                        onChange={e => updateQuarterRate(qIdx, 'usn_rate', +e.target.value || 0)} />
                                </div>
                                <div style={{ textAlign: 'center' }}>
                                    <div style={{ fontSize: 10, color: 'var(--color-text-dim)', marginBottom: 2 }}>НДС, %</div>
                                    <input type="number" step="0.01" style={inputStyle}
                                        value={quarterAvg(qIdx, 'nds_rate') || ''}
                                        onChange={e => updateQuarterRate(qIdx, 'nds_rate', +e.target.value || 0)} />
                                </div>
                            </div>

                            {/* Months */}
                            {qMonths.map(mi => {
                                const mr = getMonth(mi);
                                return (
                                    <div key={mi} style={{
                                        display: 'grid', gridTemplateColumns: 'auto 1fr 1fr', gap: 6,
                                        alignItems: 'center', padding: '4px 0',
                                        borderTop: '1px solid var(--color-border)',
                                    }}>
                                        <span style={{ fontSize: 12, color: 'var(--color-text-dim)', minWidth: 70 }}>
                                            {MONTH_NAMES[mi]}
                                        </span>
                                        <input type="number" step="0.01" style={inputStyle}
                                            value={mr.usn_rate || ''}
                                            onChange={e => updateRate(mi, 'usn_rate', +e.target.value || 0)} />
                                        <input type="number" step="0.01" style={inputStyle}
                                            value={mr.nds_rate || ''}
                                            onChange={e => updateRate(mi, 'nds_rate', +e.target.value || 0)} />
                                    </div>
                                );
                            })}

                            {/* Cost as expense checkbox — shown for both regimes */}
                            <label style={{
                                display: 'flex', alignItems: 'center', gap: 6, marginTop: 8,
                                fontSize: 11, color: 'var(--color-text-dim)', cursor: 'pointer',
                            }}>
                                <input type="checkbox"
                                    checked={getMonth(qMonths[0]).cost_as_expense}
                                    onChange={e => updateCostAsExpense(qIdx, e.target.checked)} />
                                Учитывать себестоимость товара как официальный расход
                            </label>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}


// ─── Warehouse Exclusion Settings ────────────────────────────────────────────

function WarehouseSettings() {
    const { slug } = useParams();
    const [warehouses, setWarehouses] = useState<Array<{ name: string; lat: number; lng: number }>>([]);
    const [excluded, setExcluded] = useState<string[]>([]);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [msg, setMsg] = useState('');
    const [hasChanges, setHasChanges] = useState(false);

    useEffect(() => {
        (async () => {
            try {
                const [wh, ex] = await Promise.all([
                    api.getWarehouses(),
                    api.getExcludedWarehouses(),
                ]);
                setWarehouses(wh);
                setExcluded(ex);
            } catch {
                setMsg('Ошибка загрузки складов');
            } finally {
                setLoading(false);
            }
        })();
    }, [slug]);

    const toggle = (name: string) => {
        setExcluded(prev => {
            const next = prev.includes(name) ? prev.filter(n => n !== name) : [...prev, name];
            return next;
        });
        setHasChanges(true);
    };

    const save = async () => {
        setSaving(true);
        setMsg('');
        try {
            await api.setExcludedWarehouses(excluded);
            setMsg('✅ Сохранено');
            setHasChanges(false);
        } catch {
            setMsg('❌ Ошибка сохранения');
        } finally {
            setSaving(false);
        }
    };

    if (loading) return <div className="card" style={{ padding: 20 }}>Загрузка складов...</div>;

    const active = warehouses.filter(w => !excluded.includes(w.name));
    const excludedList = warehouses.filter(w => excluded.includes(w.name));

    return (
        <div>
            <div className="card" style={{ padding: 20, marginBottom: 16 }}>
                <h3 style={{ margin: '0 0 8px' }}>🏭 Исключение складов</h3>
                <p style={{ color: 'var(--text-secondary)', margin: '0 0 16px', fontSize: 14 }}>
                    Исключённые склады не участвуют в расчёте потребностей. Заказы из их регионов перераспределяются на ближайшие оставшиеся склады.
                </p>

                <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center' }}>
                    <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                        Активных: <strong>{active.length}</strong> / {warehouses.length}
                    </span>
                    {excluded.length > 0 && (
                        <span style={{ fontSize: 13, color: 'var(--error)', fontWeight: 500 }}>
                            ⛔ Исключено: {excluded.length}
                        </span>
                    )}
                </div>

                <div style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
                    gap: 8,
                }}>
                    {warehouses.map(w => {
                        const isExcluded = excluded.includes(w.name);
                        return (
                            <label
                                key={w.name}
                                style={{
                                    display: 'flex', alignItems: 'center', gap: 8,
                                    padding: '8px 12px',
                                    borderRadius: 8,
                                    border: `1px solid ${isExcluded ? 'var(--error)' : 'var(--border)'}`,
                                    background: isExcluded ? 'rgba(239,68,68,0.08)' : 'var(--surface)',
                                    cursor: 'pointer',
                                    transition: 'all 0.15s',
                                    opacity: isExcluded ? 0.7 : 1,
                                }}
                            >
                                <input
                                    type="checkbox"
                                    checked={!isExcluded}
                                    onChange={() => toggle(w.name)}
                                    style={{ accentColor: 'var(--primary)' }}
                                />
                                <span style={{
                                    fontSize: 14,
                                    textDecoration: isExcluded ? 'line-through' : 'none',
                                    color: isExcluded ? 'var(--error)' : 'var(--text-primary)',
                                }}>
                                    {w.name}
                                </span>
                            </label>
                        );
                    })}
                </div>
            </div>

            <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                <button
                    className="btn btn-primary"
                    onClick={save}
                    disabled={saving || !hasChanges}
                >
                    {saving ? 'Сохранение...' : '💾 Сохранить'}
                </button>
                {msg && <span style={{ fontSize: 14 }}>{msg}</span>}
            </div>

            {excludedList.length > 0 && (
                <div className="card" style={{ padding: 16, marginTop: 16 }}>
                    <p style={{ fontSize: 13, color: 'var(--text-secondary)', margin: 0 }}>
                        ⚠️ Исключены: <strong>{excludedList.map(w => w.name).join(', ')}</strong>.
                        Заказы из этих регионов будут распределены на ближайшие активные склады.
                    </p>
                </div>
            )}
        </div>
    );
}

