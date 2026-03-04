'use client';
import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatDateTime, formatNumber, exportToExcel } from '@/lib/utils';

const fmt = (n: number) => n?.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) ?? '0';

export default function SettingsPage() {
    const [tab, setTab] = useState<'integrations' | 'nomenclature' | 'costs' | 'leadtimes' | 'duties'>('integrations');

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
                    { key: 'costs' as const, label: '💰 Себестоимость' },
                    { key: 'leadtimes' as const, label: '⏱ Lead Times' },
                    { key: 'duties' as const, label: '⚖️ Пошлины / Утиль' },
                ].map(t => (
                    <button key={t.key} className={`btn ${tab === t.key ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                        onClick={() => setTab(t.key)}>{t.label}</button>
                ))}
            </div>
            {tab === 'integrations' && <Integrations />}
            {tab === 'nomenclature' && <Nomenclature />}
            {tab === 'costs' && <FunnelCosts />}
            {tab === 'leadtimes' && <LeadTimes />}
            {tab === 'duties' && <DutyRules />}
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
            await api.syncWb(keyId, d.toISOString().slice(0, 10));
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
                                    <td><span className={`badge ${s.status === 'success' ? 'badge-success' : 'badge-danger'}`}>{s.status}</span></td>
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
            <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 12 }}>Ручная себестоимость</h2>
            <p style={{ fontSize: 13, color: 'var(--color-text-dim)', marginBottom: 16 }}>
                Товары без себестоимости из заказов. Укажите себестоимость за штуку для расчёта прибыли.
            </p>
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

    useEffect(() => { loadRules(); loadCategories(); }, []);
    const loadRules = async () => { try { setRules(await api.getDutyRules()); } catch { } };
    const loadCategories = async () => {
        try {
            const nom = await api.getNomenclature();
            const subjects = [...new Set(nom.map((n: any) => n.subject).filter(Boolean))].sort() as string[];
            setCategories(subjects);
        } catch { }
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
                    <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Пошлина / Утиль — по категориям</h3>
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
