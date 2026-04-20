'use client';
import { useEffect, useState, useMemo } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';

export function PlanPayments() {
    const [data, setData] = useState<any[]>([]);
    const [msg, setMsg] = useState('');
    const [showAddForm, setShowAddForm] = useState(false);
    const [showLinkForm, setShowLinkForm] = useState(false);
    const [payForm, setPayForm] = useState({ pay_date: '', order_no: '', direction: 'ЗАКАЗ', amount: '', currency: 'RUB', fx_rate: '', amount_rub: '' });
    const [candidates, setCandidates] = useState<any[]>([]);
    const [accounts, setAccounts] = useState<any[]>([]);
    const [selPayId, setSelPayId] = useState<number | null>(null);
    const [selTxnId, setSelTxnId] = useState<string>('');
    const [linkAmount, setLinkAmount] = useState('');
    const [selAccount, setSelAccount] = useState<string>('');
    const [filterDir, setFilterDir] = useState<string>('');
    const [filterStatus, setFilterStatus] = useState<string>('');
    const [orderMeta, setOrderMeta] = useState<Record<string, { invoice_no?: string; dt_number?: string }>>({});
    const [txnSearch, setTxnSearch] = useState('');

    useEffect(() => { load(); loadOrderMeta(); }, []);
    const load = async () => { try { setData(await api.getPlanningPayments()); } catch { } };
    const loadOrderMeta = async () => {
        try {
            const orders = await api.getCostOrders();
            const map: Record<string, { invoice_no?: string; dt_number?: string }> = {};
            for (const o of orders) map[String(o.order_no)] = { invoice_no: o.invoice_no, dt_number: o.dt_number };
            setOrderMeta(map);
        } catch { }
    };
    const del = async (id: number) => { if (!confirm('Удалить?')) return; try { await api.deletePlanningPayment(id); load(); } catch (e: any) { setMsg(e.message); } };
    const markPaid = async (id: number) => { try { await api.markPaymentPaid(id); setMsg('✅ Оплачено!'); load(); } catch (e: any) { setMsg(e.message); } };
    const sync = async () => { try { await api.syncPlanPayments(); setMsg('✅ Факт синхронизирован!'); load(); } catch (e: any) { setMsg(e.message); } };
    const addPayment = async () => {
        try {
            await api.upsertPlanningPayment({
                pay_date: payForm.pay_date || undefined, order_no: payForm.order_no ? parseInt(payForm.order_no) : undefined,
                direction: payForm.direction, amount: payForm.amount ? parseFloat(payForm.amount) : undefined,
                currency: payForm.currency, fx_rate: payForm.fx_rate ? parseFloat(payForm.fx_rate) : undefined,
                amount_rub: payForm.amount_rub ? parseFloat(payForm.amount_rub) : undefined,
            });
            setMsg('✅ Платёж добавлен!'); setShowAddForm(false); load();
        } catch (e: any) { setMsg(`❌ ${e.message}`); }
    };
    const loadCandidates = async (account?: string) => { try { setCandidates(await api.getCandidateTransactions(account || undefined)); } catch { setCandidates([]); } };
    const openLinkForm = async () => {
        setShowLinkForm(!showLinkForm);
        if (!showLinkForm) { await loadCandidates(selAccount); try { setAccounts(await api.getAccountsList()); } catch { setAccounts([]); } }
    };
    const handleAccountChange = async (acc: string) => { setSelAccount(acc); await loadCandidates(acc); };
    const linkTransaction = async () => {
        if (!selPayId || !selTxnId) return;
        try { await api.createFactLink({ payment_id: selPayId, txn_id: selTxnId, amount_rub: linkAmount ? parseFloat(linkAmount) : 0 }); setMsg('✅ Привязано!'); await sync(); setShowLinkForm(false); } catch (e: any) { setMsg(`❌ ${e.message}`); }
    };

    const _status = (r: any) => {
        const amt = parseFloat(r.amount_rub || 0); const paid = parseFloat(r.paid_rub || 0);
        if (r.is_paid || (amt > 0 && paid >= amt)) return { label: '✅ Оплачено', cls: 'success' };
        if (paid > 0 && amt > 0) return { label: `🟠 ${(paid / amt * 100).toFixed(0)}%`, cls: 'warning' };
        const d = r.pay_date;
        if (d) { try { const dt = new Date(d); const today = new Date(); today.setHours(0,0,0,0); if (dt < today) return { label: '🔴 Просрочено', cls: 'danger' }; if (dt.toDateString() === today.toDateString()) return { label: '🟡 Сегодня', cls: 'warning' }; return { label: '🔵 Будущее', cls: 'info' }; } catch { } }
        return { label: '—', cls: 'secondary' };
    };

    let filtered = filterDir ? data.filter(r => (r.direction || '').toUpperCase() === filterDir.toUpperCase()) : data;
    if (filterStatus) {
        const today = new Date(); today.setHours(0,0,0,0);
        filtered = filtered.filter(r => {
            if (filterStatus === 'PAID') return r.is_paid;
            if (filterStatus === 'OVERDUE') { if (r.is_paid) return false; if (!r.pay_date) return false; return new Date(r.pay_date) < today; }
            if (filterStatus === 'FUTURE') { if (r.is_paid) return false; if (!r.pay_date) return true; return new Date(r.pay_date) >= today; }
            return true;
        });
    }

    const totalPlan = filtered.reduce((s, r) => s + parseFloat(r.amount || 0), 0);
    const totalPaidAmt = filtered.reduce((s, r) => { if (r.is_paid) return s + parseFloat(r.amount || 0); return s + parseFloat(r.paid_amount || 0); }, 0);
    const totalPlanRub = filtered.reduce((s, r) => s + parseFloat(r.amount_rub || 0), 0);
    const totalPaidRub = filtered.reduce((s, r) => {
        const paidAmt = r.is_paid ? parseFloat(r.amount || 0) : parseFloat(r.paid_amount || 0);
        const rate = parseFloat(r.fx_rate || 0); const paidRub = parseFloat(r.paid_rub || 0);
        const ccy = (r.currency || 'RUB').toUpperCase();
        if (ccy !== 'RUB' && rate > 0) return s + paidAmt * rate + paidRub;
        return s + paidRub;
    }, 0);
    const totalRemain = totalPlan - totalPaidAmt;
    const progress = totalPlan > 0 ? (totalPaidAmt / totalPlan * 100).toFixed(0) + '%' : '—';
    const dominantCcy = filtered.length > 0 ? (filtered[0].currency || 'CNY') : 'CNY';
    const unpaidPayments = filtered.filter(p => !p.is_paid);
    const filteredCandidates = candidates.filter((c: any) => {
        if (!txnSearch) return true; const s = txnSearch.toLowerCase();
        return (c.counterparty || '').toLowerCase().includes(s) || String(c.expense || '').includes(s) || (c.date || '').includes(s) || (c.purpose || '').toLowerCase().includes(s);
    });
    const dirFilters = [{ key: '', label: 'Все' }, { key: 'ЗАКАЗ', label: '📦 Заказ' }, { key: 'ТАМОЖНЯ', label: '🛃 Таможня' }, { key: 'ДОСТАВКА', label: '🚛 Доставка' }];

    const columns: Column[] = useMemo(() => {
        const baseCols: Column[] = [
            { key: 'id', label: 'ID' },
            { key: 'pay_date', label: 'Дата', render: (v: any) => <span style={{ fontSize: 12 }}>{v ? formatDate(v) : '—'}</span> },
            { key: 'order_no', label: 'Заказ', render: (v: any) => v || '—' },
            { key: 'direction', label: 'Направление', render: (v: any) => <span className="badge badge-info">{v || '—'}</span> },
        ];
        if (filterDir === 'ДОСТАВКА') {
            baseCols.push({ key: '_invoice', label: 'Инвойс', render: (_v: any, row: any) => <span style={{ fontSize: 11, fontFamily: 'monospace' }}>{orderMeta[String(row.order_no)]?.invoice_no || '—'}</span> });
        }
        if (filterDir === 'ТАМОЖНЯ') {
            baseCols.push({ key: '_dt', label: 'ДТ', render: (_v: any, row: any) => <span style={{ fontSize: 11, fontFamily: 'monospace' }}>{orderMeta[String(row.order_no)]?.dt_number || '—'}</span> });
        }
        baseCols.push(
            { key: 'amount', label: 'План вал.', render: (v: any) => <span style={{ fontWeight: 500 }}>{formatNumber(parseFloat(v || 0))}</span> },
            { key: 'currency', label: 'Вал.', render: (v: any) => <span className={`badge badge-${v === 'RUB' ? 'success' : 'warning'}`}>{v}</span> },
            { key: 'paid_amount', label: 'Факт вал.', render: (v: any, row: any) => { const paidAmt = parseFloat(row.paid_amount || 0); return <span style={{ color: paidAmt > 0 ? 'var(--color-success)' : undefined }}>{paidAmt > 0 ? formatNumber(paidAmt) : '—'}</span>; } },
            { key: '_remain', label: 'Остаток вал.', render: (_v: any, row: any) => { const amt = parseFloat(row.amount || 0); const paidAmt = parseFloat(row.paid_amount || 0); const remain = amt - paidAmt; return <span style={{ color: remain > 0 ? 'var(--color-danger)' : 'var(--color-success)' }}>{remain > 0 ? formatNumber(remain) : '✓'}</span>; } },
            { key: 'amount_rub', label: 'План ₽', render: (v: any) => <span style={{ fontSize: 12, opacity: 0.7 }}>{formatNumber(parseFloat(v || 0))} ₽</span> },
            { key: 'paid_rub', label: 'Факт ₽', render: (v: any) => { const paidRub = parseFloat(v || 0); return <span style={{ fontSize: 12, opacity: 0.7 }}>{paidRub > 0 ? `${formatNumber(paidRub)} ₽` : '—'}</span>; } },
            { key: '_status', label: 'Статус', render: (_v: any, row: any) => { const st = _status(row); return <span className={`badge badge-${st.cls}`}>{st.label}</span>; } },
            {
                key: '_actions', label: '', sortable: false,
                render: (_v: any, row: any) => (
                    <div style={{ display: 'flex', gap: 4 }}>
                        {!row.is_paid && <button className="btn btn-primary btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => markPaid(row.id)}>✓</button>}
                        <button className="btn btn-danger btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => del(row.id)}>✕</button>
                    </div>
                ),
            },
        );
        return baseCols;
    }, [filterDir, orderMeta]);

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>Плановые платежи</h3>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={sync}>🔄 Синхронизировать факт с выписками</button>
                    <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(filtered, 'plan_payments')}>📥 Excel</button>
                </div>
            </div>

            <div style={{ display: 'flex', gap: 6, marginBottom: 12, flexWrap: 'wrap' }}>
                {dirFilters.map(f => (<button key={f.key} className={`btn btn-sm ${filterDir === f.key ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setFilterDir(f.key)} style={{ fontSize: 12, padding: '4px 12px' }}>{f.label}</button>))}
                {(filterDir || filterStatus) && <span style={{ fontSize: 12, color: 'var(--color-text-muted)', alignSelf: 'center' }}>({filtered.length} из {data.length})</span>}
            </div>
            <div style={{ display: 'flex', gap: 6, marginBottom: 12, flexWrap: 'wrap' }}>
                {[{ key: '', label: 'Все статусы' }, { key: 'PAID', label: '✅ Оплачено' }, { key: 'OVERDUE', label: '🔴 Просрочено' }, { key: 'FUTURE', label: '🔵 Будущее' }].map(f => (
                    <button key={f.key} className={`btn btn-sm ${filterStatus === f.key ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setFilterStatus(f.key)} style={{ fontSize: 12, padding: '4px 12px' }}>{f.label}</button>
                ))}
            </div>

            {msg && <div style={{ fontSize: 13, marginBottom: 8, padding: '6px 12px', borderRadius: 6, background: msg.startsWith('✅') ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)', color: msg.startsWith('✅') ? 'var(--color-success)' : 'var(--color-danger)' }}>{msg} <span style={{ float: 'right', cursor: 'pointer' }} onClick={() => setMsg('')}>✕</span></div>}

            {/* Summary row */}
            {filtered.length > 0 && (() => {
                const today = new Date(); today.setHours(0,0,0,0);
                const overdue: Record<string, number> = {}; let overdueRub = 0;
                filtered.forEach(r => { if (r.is_paid || !r.pay_date) return; if (new Date(r.pay_date) >= today) return; const ccy = (r.currency || 'RUB').toUpperCase(); const remain = parseFloat(r.amount || 0) - parseFloat(r.paid_amount || 0); if (remain > 0) { if (ccy.includes('/')) { overdue[ccy.split('/')[0]] = (overdue[ccy.split('/')[0]] || 0) + remain; } else { overdue[ccy] = (overdue[ccy] || 0) + remain; } } });
                filtered.forEach(r => { if (r.is_paid || !r.pay_date) return; if (new Date(r.pay_date) >= today) return; const ccy = (r.currency || 'RUB').toUpperCase(); if (ccy !== 'RUB') return; const remainRub = parseFloat(r.amount || 0) - parseFloat(r.paid_amount || 0); if (remainRub > 0) overdueRub += remainRub; });
                return (
                    <div style={{ display: 'flex', gap: 24, marginBottom: 12, padding: '8px 12px', background: 'var(--color-bg-input)', borderRadius: 8, fontSize: 13, alignItems: 'center', flexWrap: 'wrap' }}>
                        <span><span style={{ color: 'var(--color-text-muted)' }}>План {dominantCcy}:</span> <b>{formatNumber(totalPlan)}</b></span>
                        <span><span style={{ color: 'var(--color-text-muted)' }}>Факт {dominantCcy}:</span> <b style={{ color: 'var(--color-success)' }}>{formatNumber(totalPaidAmt)}</b></span>
                        <span><span style={{ color: 'var(--color-text-muted)' }}>Остаток {dominantCcy}:</span> <b style={{ color: totalRemain > 0 ? 'var(--color-danger)' : 'var(--color-success)' }}>{formatNumber(totalRemain)}</b></span>
                        <span><span style={{ color: 'var(--color-text-muted)' }}>Прогресс:</span> <b>{progress}</b></span>
                        {(overdue['CNY'] || 0) > 0 && <span style={{ color: 'var(--color-danger)' }}>🔴 Просрочено CNY: <b>{formatNumber(overdue['CNY'])}</b></span>}
                        {(overdue['USD'] || 0) > 0 && <span style={{ color: 'var(--color-danger)' }}>🔴 Просрочено USD: <b>{formatNumber(overdue['USD'])}</b></span>}
                        {overdueRub > 0 && <span style={{ color: 'var(--color-danger)' }}>🔴 Просрочено ₽: <b>{formatNumber(overdueRub)}</b></span>}
                    </div>
                );
            })()}

            <TanStackDataTable
                columns={columns}
                data={filtered}
                emptyText="Нет платежей"
                enableSorting
                enablePagination
                pageSize={50}
            />

            <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button className="btn btn-secondary btn-sm" onClick={() => setShowAddForm(!showAddForm)}>➕ Добавить платёж</button>
                <button className="btn btn-secondary btn-sm" onClick={openLinkForm}>🔗 Привязать транзакцию</button>
            </div>

            {showAddForm && (
                <div style={{ background: 'var(--color-bg-input)', padding: 16, borderRadius: 8, marginTop: 8 }}>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
                        <div className="form-group"><label className="form-label" style={{ fontSize: 11 }}>Дата оплаты</label><input className="form-input" type="date" value={payForm.pay_date} onChange={e => setPayForm({...payForm, pay_date: e.target.value})} style={{ fontSize: 12 }} /></div>
                        <div className="form-group"><label className="form-label" style={{ fontSize: 11 }}>Номер заказа</label><input className="form-input" type="number" value={payForm.order_no} onChange={e => setPayForm({...payForm, order_no: e.target.value})} style={{ fontSize: 12 }} /></div>
                        <div className="form-group"><label className="form-label" style={{ fontSize: 11 }}>Направление</label><select className="form-input" value={payForm.direction} onChange={e => setPayForm({...payForm, direction: e.target.value})} style={{ fontSize: 12 }}><option>ЗАКАЗ</option><option>ДОСТАВКА</option><option>ТАМОЖНЯ</option><option>ДЕПОЗИТ</option><option>ДРУГОЕ</option></select></div>
                        <div className="form-group"><label className="form-label" style={{ fontSize: 11 }}>Валюта</label><select className="form-input" value={payForm.currency} onChange={e => setPayForm({...payForm, currency: e.target.value})} style={{ fontSize: 12 }}><option>RUB</option><option>CNY</option><option>USD</option></select></div>
                        <div className="form-group"><label className="form-label" style={{ fontSize: 11 }}>Сумма</label><input className="form-input" type="number" value={payForm.amount} onChange={e => setPayForm({...payForm, amount: e.target.value})} style={{ fontSize: 12 }} /></div>
                        <div className="form-group"><label className="form-label" style={{ fontSize: 11 }}>Курс</label><input className="form-input" type="number" step="0.01" value={payForm.fx_rate} onChange={e => setPayForm({...payForm, fx_rate: e.target.value})} style={{ fontSize: 12 }} /></div>
                        <div className="form-group"><label className="form-label" style={{ fontSize: 11 }}>Сумма ₽</label><input className="form-input" type="number" value={payForm.amount_rub} onChange={e => setPayForm({...payForm, amount_rub: e.target.value})} style={{ fontSize: 12 }} /></div>
                        <div style={{ display: 'flex', alignItems: 'flex-end' }}><button className="btn btn-primary btn-sm" onClick={addPayment}>💾 Добавить</button></div>
                    </div>
                </div>
            )}

            {showLinkForm && (
                <div style={{ background: 'var(--color-bg-input)', padding: 20, borderRadius: 8, marginTop: 8 }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
                        <div className="form-group" style={{ margin: 0 }}>
                            <label className="form-label" style={{ fontSize: 12, fontWeight: 600 }}>Платёж</label>
                            <select className="form-input" value={selPayId ?? ''} onChange={e => setSelPayId(e.target.value ? parseInt(e.target.value) : null)} style={{ fontSize: 12 }}>
                                <option value="">— Выберите платёж —</option>
                                {unpaidPayments.map((p: any) => <option key={p.id} value={p.id}>ID:{p.id} | №{p.order_no || '—'} | {p.direction} | {formatNumber(p.amount || 0)} {p.currency} ({formatNumber(p.amount_rub || 0)}₽)</option>)}
                            </select>
                        </div>
                        <div className="form-group" style={{ margin: 0 }}>
                            <label className="form-label" style={{ fontSize: 12, fontWeight: 600 }}>Счёт</label>
                            <select className="form-input" value={selAccount} onChange={e => handleAccountChange(e.target.value)} style={{ fontSize: 12 }}>
                                <option value="">Все счета</option>
                                {accounts.map((a: any) => <option key={a.id} value={a.account}>{a.bank} ({a.currency})</option>)}
                            </select>
                        </div>
                    </div>
                    <div className="form-group" style={{ margin: '0 0 12px 0' }}>
                        <label className="form-label" style={{ fontSize: 12, fontWeight: 600 }}>Транзакция из выписки</label>
                        <select className="form-input" value={selTxnId} onChange={e => setSelTxnId(e.target.value)} style={{ fontSize: 12 }}>
                            <option value="">— Выберите транзакцию —</option>
                            {filteredCandidates.map((c: any) => <option key={c.txn_id} value={c.txn_id}>{c.date?.slice(0,10)} | {c.currency} {formatNumber(c.expense)} | {c.counterparty || '—'}</option>)}
                        </select>
                    </div>
                    {selTxnId && (() => { const sel = candidates.find((c: any) => c.txn_id === selTxnId); return sel?.purpose ? <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginBottom: 12, padding: '8px 12px', background: 'var(--color-bg)', borderRadius: 6, border: '1px solid var(--color-border)' }}><span style={{ opacity: 0.6 }}>📋 Назначение:</span> {sel.purpose}</div> : null; })()}
                    <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end' }}>
                        <div className="form-group" style={{ margin: 0 }}>
                            <label className="form-label" style={{ fontSize: 12, fontWeight: 600 }}>Сумма привязки{selPayId ? ` (${unpaidPayments.find((p: any) => p.id === selPayId)?.currency || 'RUB'})` : ''}</label>
                            <input className="form-input" type="number" value={linkAmount} onChange={e => setLinkAmount(e.target.value)} style={{ fontSize: 12, width: 200 }} />
                        </div>
                        <button className="btn btn-primary btn-sm" onClick={linkTransaction} disabled={!selPayId || !selTxnId}>🔗 Привязать</button>
                    </div>
                </div>
            )}
        </div>
    );
}
