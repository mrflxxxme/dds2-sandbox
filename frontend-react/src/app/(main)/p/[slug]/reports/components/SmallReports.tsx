'use client';
import { useState, useMemo } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';

export function BalanceDaily() {
    const [accounts, setAccounts] = useState<any[]>([]);
    const [accIdx, setAccIdx] = useState(0);
    const [start, setStart] = useState(new Date().getFullYear() + '-01-01');
    const [end, setEnd] = useState(new Date().toISOString().slice(0, 10));
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(false);
    const [loaded, setLoaded] = useState(false);

    const loadAccounts = async () => {
        if (accounts.length > 0) return;
        try { const a = await api.getAccounts(); setAccounts(a.filter((x: any) => x.is_our_account)); } catch { }
    };
    if (accounts.length === 0) loadAccounts();

    const acc = accounts[accIdx];

    const load = async () => {
        if (!acc) return;
        setLoading(true);
        try {
            const res = await api.getBalanceDaily(acc.account, acc.currency, start, end);
            setData(res || []);
        } catch { }
        setLoading(false);
        setLoaded(true);
    };

    const columns: Column[] = useMemo(() => [
        { key: 'date', label: 'Дата', format: 'date' },
        {
            key: 'daily_net', label: 'Нетто за день', align: 'right',
            render: (v: any) => (
                <span style={{ color: (v || 0) >= 0 ? 'var(--color-success)' : 'var(--color-danger)' }}>
                    {formatNumber(v)}
                </span>
            ),
        },
        {
            key: 'balance', label: 'Баланс', align: 'right',
            render: (v: any) => <span style={{ fontWeight: 600 }}>{formatNumber(v)}</span>,
        },
    ], []);

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', gap: 12, alignItems: 'end', marginBottom: 16, flexWrap: 'wrap' }}>
                <div className="form-group" style={{ minWidth: 200 }}>
                    <label className="form-label">Счёт</label>
                    <select className="form-input" value={accIdx} onChange={e => setAccIdx(parseInt(e.target.value))}>
                        {accounts.map((a, i) => <option key={i} value={i}>{a.account_name} ({a.currency})</option>)}
                    </select>
                </div>
                <div className="form-group"><label className="form-label">С</label><input className="form-input" type="date" value={start} onChange={e => setStart(e.target.value)} /></div>
                <div className="form-group"><label className="form-label">По</label><input className="form-input" type="date" value={end} onChange={e => setEnd(e.target.value)} /></div>
                <button className="btn btn-primary btn-sm" onClick={load} disabled={loading}>{loading ? '...' : 'Показать'}</button>
            </div>

            {data.length > 0 ? (
                <TanStackDataTable
                    columns={columns}
                    data={data}
                    enableSorting
                    enablePagination
                    pageSize={50}
                    exportName="balance_daily"
                />
            ) : loaded ? (
                <div className="empty-state"><div className="empty-state-text">Нет данных за период</div></div>
            ) : (
                <div className="empty-state"><div className="empty-state-text">Выберите счёт и нажмите «Показать»</div></div>
            )}
        </div>
    );
}

export function FxControl() {
    const [start, setStart] = useState(new Date().getFullYear() + '-01-01');
    const [end, setEnd] = useState(new Date().toISOString().slice(0, 10));
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(false);

    const load = async () => {
        setLoading(true);
        try { setData(await api.getFxControl(start, end) || []); } catch { }
        setLoading(false);
    };

    const autoColumns: Column[] = useMemo(() => {
        if (data.length === 0) return [];
        return Object.keys(data[0]).map(k => ({
            key: k,
            label: k,
            render: (v: any) => typeof v === 'number' ? formatNumber(v) : v ?? '—',
        }));
    }, [data]);

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', gap: 12, alignItems: 'end', marginBottom: 16 }}>
                <div className="form-group"><label className="form-label">С</label><input className="form-input" type="date" value={start} onChange={e => setStart(e.target.value)} /></div>
                <div className="form-group"><label className="form-label">По</label><input className="form-input" type="date" value={end} onChange={e => setEnd(e.target.value)} /></div>
                <button className="btn btn-primary btn-sm" onClick={load} disabled={loading}>{loading ? '...' : 'Загрузить FX'}</button>
            </div>

            {data.length > 0 ? (
                <>
                    <div className="stat-card" style={{ marginBottom: 12 }}>
                        <div className="stat-card-label">Всего FX операций</div>
                        <div className="stat-card-value">{data.length}</div>
                    </div>
                    <TanStackDataTable
                        columns={autoColumns}
                        data={data}
                        enableSorting
                        enablePagination
                        pageSize={50}
                        exportName="fx_control"
                    />
                </>
            ) : <div className="empty-state"><div className="empty-state-text">Нажмите «Загрузить FX»</div></div>}
        </div>
    );
}

export function CustomsControl() {
    const [start, setStart] = useState(new Date().getFullYear() + '-01-01');
    const [end, setEnd] = useState(new Date().toISOString().slice(0, 10));
    const [data, setData] = useState<any[]>([]);
    const [loading, setLoading] = useState(false);

    const load = async () => {
        setLoading(true);
        try { setData(await api.getCustomsControl(start, end) || []); } catch { }
        setLoading(false);
    };

    const totalExpense = data.reduce((s, r) => s + (parseFloat(r.expense) || 0), 0);

    const autoColumns: Column[] = useMemo(() => {
        if (data.length === 0) return [];
        return Object.keys(data[0]).map(k => ({
            key: k,
            label: k,
            render: (v: any) => typeof v === 'number' ? formatNumber(v) : v ?? '—',
        }));
    }, [data]);

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', gap: 12, alignItems: 'end', marginBottom: 16 }}>
                <div className="form-group"><label className="form-label">С</label><input className="form-input" type="date" value={start} onChange={e => setStart(e.target.value)} /></div>
                <div className="form-group"><label className="form-label">По</label><input className="form-input" type="date" value={end} onChange={e => setEnd(e.target.value)} /></div>
                <button className="btn btn-primary btn-sm" onClick={load} disabled={loading}>{loading ? '...' : 'Загрузить'}</button>
            </div>

            {data.length > 0 ? (
                <>
                    <div className="stat-card" style={{ marginBottom: 12, borderLeft: '3px solid var(--color-danger)' }}>
                        <div className="stat-card-label">Итого оплачено таможне</div>
                        <div className="stat-card-value" style={{ color: 'var(--color-danger)' }}>{formatNumber(totalExpense)} ₽</div>
                    </div>
                    <TanStackDataTable
                        columns={autoColumns}
                        data={data}
                        enableSorting
                        enablePagination
                        pageSize={50}
                        exportName="customs_control"
                    />
                </>
            ) : <div className="empty-state"><div className="empty-state-text">Нажмите «Загрузить»</div></div>}
        </div>
    );
}
