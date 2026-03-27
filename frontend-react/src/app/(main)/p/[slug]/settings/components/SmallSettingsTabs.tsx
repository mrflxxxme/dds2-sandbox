'use client';
import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, exportToExcel } from '@/lib/utils';

const fmt = (n: number) => n?.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) ?? '0';

/* ─── Nomenclature ─────────────────────────────────────────────── */

export function Nomenclature() {
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [syncing, setSyncing] = useState(false);
    const [syncMsg, setSyncMsg] = useState('');
    const [lastSyncAt, setLastSyncAt] = useState<string | null>(null);

    const load = async () => { try { setData(await api.getNomenclature()); } catch { } setLoading(false); };
    const loadLastSync = async () => {
        try { const logs = await api.getSyncLog(); const nomLog = logs.find((l: any) => l.sync_type === 'nomenclature' && l.status === 'OK'); if (nomLog?.finished_at) setLastSyncAt(nomLog.finished_at); } catch { }
    };
    useEffect(() => { load(); loadLastSync(); }, []);

    const handleSync = async () => {
        setSyncing(true); setSyncMsg('');
        try {
            const result = await api.syncNomenclature();
            if (result.status === 'OK') { setSyncMsg(`✅ Синхронизация завершена: ${result.error_msg || 'OK'}`); if (result.finished_at) setLastSyncAt(result.finished_at); }
            else { setSyncMsg(`❌ Ошибка: ${result.error_msg || 'unknown'}`); }
            await load();
        } catch (e: any) { setSyncMsg(`❌ ${e.message}`); }
        setSyncing(false);
    };

    const formatSyncTime = (iso: string) => new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });

    if (loading) return <div style={{ padding: 20, color: 'var(--color-text-muted)' }}>Загрузка...</div>;

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <div>
                    <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Номенклатура ({data.length})</h3>
                    {lastSyncAt && <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginTop: 4 }}>🕐 Последняя синхронизация: {formatSyncTime(lastSyncAt)}</div>}
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <button className="btn btn-primary btn-sm" onClick={handleSync} disabled={syncing} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        {syncing ? (<><span className="spinner" style={{ width: 14, height: 14, border: '2px solid rgba(255,255,255,0.3)', borderTop: '2px solid #fff', borderRadius: '50%', animation: 'spin 0.8s linear infinite', display: 'inline-block' }} /> Синхронизация...</>) : '🔄 Синхронизация WB'}
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

/* ─── LeadTimes ────────────────────────────────────────────────── */

export function LeadTimes() {
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

/* ─── FunnelCosts (unused but preserved) ───────────────────────── */

export function FunnelCosts() {
    const { slug } = useParams();
    const router = useRouter();
    const [costs, setCosts] = useState<any>({ overrides: [], missing: [] });
    const [editCost, setEditCost] = useState<{ nm_id: number; cost_price: string } | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => { loadCosts(); }, []);
    const loadCosts = async () => { try { setCosts(await api.getFunnelCosts()); } catch { } setLoading(false); };
    const handleSaveCost = async () => {
        if (!editCost) return;
        try { await api.setFunnelCost(editCost.nm_id, parseFloat(editCost.cost_price)); setEditCost(null); loadCosts(); } catch (e: any) { alert(e.message); }
    };

    if (loading) return <div style={{ padding: 20, color: 'var(--color-text-muted)' }}>Загрузка...</div>;

    const renderRow = (item: any, isOverride: boolean) => (
        <tr key={item.nm_id}>
            <td><a href={`https://www.wildberries.ru/catalog/${item.nm_id}/detail.aspx`} target="_blank" rel="noreferrer" style={{ color: 'var(--color-accent)' }}>{item.nm_id}</a></td>
            {!isOverride && <><td>{item.vendor_code}</td><td>{item.subject}</td><td>{item.brand}</td></>}
            <td>{editCost?.nm_id === item.nm_id ? (
                <input type="number" value={editCost.cost_price} autoFocus onChange={e => setEditCost({ ...editCost, cost_price: e.target.value })} onKeyDown={e => e.key === 'Enter' && handleSaveCost()}
                    style={{ width: 100, background: 'var(--color-bg)', border: '1px solid var(--color-accent)', borderRadius: 6, padding: '4px 8px', color: 'var(--color-text)' }} />
            ) : isOverride ? fmt(item.cost_price) : '—'}</td>
            <td>{editCost?.nm_id === item.nm_id ? (
                <div style={{ display: 'flex', gap: 4 }}>
                    <button className="btn-primary" onClick={handleSaveCost} style={{ padding: '2px 8px', fontSize: 12 }}>✓</button>
                    <button className="btn-secondary" onClick={() => setEditCost(null)} style={{ padding: '2px 8px', fontSize: 12 }}>✕</button>
                </div>
            ) : (
                <button className="btn-secondary" onClick={() => setEditCost({ nm_id: item.nm_id, cost_price: isOverride ? String(item.cost_price) : '' })} style={{ padding: '2px 8px', fontSize: 12 }}>✏️</button>
            )}</td>
        </tr>
    );

    return (
        <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <div>
                    <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Ручная себестоимость</h2>
                    <p style={{ fontSize: 13, color: 'var(--color-text-dim)', margin: '4px 0 0' }}>Товары без себестоимости из заказов. Укажите себестоимость за штуку для расчёта прибыли.</p>
                </div>
                <button className="btn btn-primary btn-sm" onClick={() => router.push(`/p/${slug}/bulk-cost`)}>📋 Массово себестоимость</button>
            </div>
            {costs.missing?.length > 0 && (
                <div className="glass-card" style={{ marginBottom: 16 }}>
                    <h3 style={{ fontSize: 14, fontWeight: 500, marginBottom: 8, padding: '12px 16px 0' }}>⚠️ Без себестоимости ({costs.missing.length})</h3>
                    <table className="data-table">
                        <thead><tr><th>nmId</th><th>Артикул</th><th>Предмет</th><th>Бренд</th><th>Себестоимость ₽</th><th></th></tr></thead>
                        <tbody>{costs.missing.map((m: any) => renderRow(m, false))}</tbody>
                    </table>
                </div>
            )}
            {costs.overrides?.length > 0 && (
                <div className="glass-card">
                    <h3 style={{ fontSize: 14, fontWeight: 500, marginBottom: 8, padding: '12px 16px 0' }}>✅ Установленные ({costs.overrides.length})</h3>
                    <table className="data-table">
                        <thead><tr><th>nmId</th><th>Себестоимость ₽</th><th></th></tr></thead>
                        <tbody>{costs.overrides.map((o: any) => renderRow(o, true))}</tbody>
                    </table>
                </div>
            )}
            {costs.missing?.length === 0 && costs.overrides?.length === 0 && (
                <div className="glass-card" style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>Нет данных. Дождитесь автоматической синхронизации воронки.</div>
            )}
        </div>
    );
}
