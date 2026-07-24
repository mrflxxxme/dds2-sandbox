'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';
import CounterpartyTypeBadge, { TYPE_CONFIG } from '@/components/CounterpartyTypeBadge';
import CurrencySplitStats from '@/components/CurrencySplitStats';
import DocumentUploader from '@/components/DocumentUploader';
import TabLayout from '@/components/TabLayout';
import ReconciliationTab from './ReconciliationTab';
import type {
    CounterpartyDetail, CounterpartyDocument, DocType,
    CounterpartyUpdate, CounterpartyTransactionItem, CounterpartyType,
    AssemblyRequest, PaymentRequestRow,
} from '@/types/api';

const PAY_STATUS: Record<string, { label: string; cls: string }> = {
    DRAFT: { label: 'Черновик', cls: 'badge-secondary' },
    PENDING_REVIEW: { label: 'На проверке', cls: 'badge-warning' },
    DRAFT_CREATED: { label: 'Платёжка создана', cls: 'badge-info' },
    PAID: { label: 'Оплачено', cls: 'badge-success' },
    REJECTED: { label: 'Отклонено', cls: 'badge-danger' },
    CANCELLED: { label: 'Отменено', cls: 'badge-secondary' },
};

const TODAY = new Date().toISOString().slice(0, 10);
const HALF_YEAR_AGO = new Date(Date.now() - 180 * 24 * 3600 * 1000).toISOString().slice(0, 10);

const DOC_TYPE_LABELS: Record<DocType, string> = {
    CONTRACT: 'Договор',
    CERTIFICATE: 'Сертификат',
    INVOICE: 'Счёт',
    OTHER: 'Прочее',
};

type TabKey = 'stats' | 'docs' | 'payments' | 'recon' | 'deliveries';

const ASM_STATUS: Record<string, { label: string; cls: string }> = {
    PENDING: { label: 'В сборке', cls: 'badge-info' },
    IN_PROGRESS: { label: 'В сборке', cls: 'badge-info' },
    READY: { label: 'Готово', cls: 'badge-success' },
    VEHICLE_ASSIGNED: { label: 'Машина назначена', cls: 'badge-info' },
    SHIPPED: { label: 'Отгружена', cls: 'badge-success' },
    DELIVERED: { label: 'Принята WB', cls: 'badge-success' },
    CANCELLED: { label: 'Отменена', cls: 'badge-secondary' },
};

export default function CounterpartyDetailPage() {
    const params = useParams();
    const slug = params.slug as string;
    const id = Number(params.id);
    const router = useRouter();

    const [detail, setDetail] = useState<CounterpartyDetail | null>(null);
    const [catEditing, setCatEditing] = useState(false);
    const [catL1, setCatL1] = useState('');
    const [catL2, setCatL2] = useState('');
    const [catSaving, setCatSaving] = useState(false);
    const [catMsg, setCatMsg] = useState('');
    const [documents, setDocuments] = useState<CounterpartyDocument[]>([]);
    const [transactions, setTransactions] = useState<CounterpartyTransactionItem[]>([]);
    const [txTotal, setTxTotal] = useState(0);
    const [deliveries, setDeliveries] = useState<AssemblyRequest[]>([]);
    const [deliveriesLoading, setDeliveriesLoading] = useState(false);
    const [payments, setPayments] = useState<PaymentRequestRow[]>([]);
    const [paymentsLoading, setPaymentsLoading] = useState(false);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [activeKey, setActiveKey] = useState<TabKey>('stats');

    // Carrier role shows the «Доставки» tab even with zero linked requests.
    // We still fetch deliveries for every counterparty so the tab also appears
    // when requests are linked but the category was never switched to CARRIER —
    // assign_vehicle only bumps OTHER → CARRIER, never an already-typed CP.
    const isCarrier = useMemo(
        () => detail?.primary_type === 'CARRIER' || (detail?.secondary_types ?? []).includes('CARRIER'),
        [detail],
    );

    // Date range for stats
    const [dateFrom, setDateFrom] = useState(HALF_YEAR_AGO);
    const [dateTo, setDateTo] = useState(TODAY);

    // Edit
    const [editing, setEditing] = useState(false);
    const [editForm, setEditForm] = useState<CounterpartyUpdate>({});
    const [saving, setSaving] = useState(false);

    const loadDetail = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [d, docs, txs] = await Promise.all([
                api.getCounterparty(id, { date_from: dateFrom, date_to: dateTo }),
                api.listCounterpartyDocuments(id),
                api.getCounterpartyTransactions(id, { date_from: dateFrom, date_to: dateTo, limit: 500 }),
            ]);
            setDetail(d);
            setDocuments(docs);
            setTransactions(txs.items);
            setTxTotal(txs.total);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, [id, dateFrom, dateTo]);

    useEffect(() => { loadDetail(); }, [loadDetail]);

    const loadDeliveries = useCallback(async () => {
        setDeliveriesLoading(true);
        try {
            const resp = await api.getAssemblyRequests({ counterparty_id: id, limit: 500 });
            setDeliveries(resp.items);
        } catch {
            setDeliveries([]);
        } finally {
            setDeliveriesLoading(false);
        }
    }, [id]);

    useEffect(() => { if (detail) loadDeliveries(); }, [detail, loadDeliveries]);

    const loadPayments = useCallback(async () => {
        setPaymentsLoading(true);
        try {
            const resp = await api.listPaymentRequests({ counterparty_id: id, limit: 500 });
            setPayments(resp.items);
        } catch {
            setPayments([]);
        } finally {
            setPaymentsLoading(false);
        }
    }, [id]);

    useEffect(() => { if (detail) loadPayments(); }, [detail, loadPayments]);

    const handleDelete = async () => {
        if (!confirm(`Удалить контрагента "${detail?.name}"?`)) return;
        try {
            await api.deleteCounterparty(id);
            router.push(`/p/${slug}/refs/counterparty`);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка удаления');
        }
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            await api.updateCounterparty(id, editForm);
            setEditing(false);
            await loadDetail();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка сохранения');
        } finally {
            setSaving(false);
        }
    };

    const handleSaveCategory = async () => {
        setCatSaving(true); setCatMsg('');
        try {
            const res = await api.setCounterpartyCategory(id, catL1.trim() || null, catL2.trim() || null);
            setCatEditing(false);
            setCatMsg(res.cat_lvl1
                ? `✓ Категория применена к ${res.applied} операциям`
                : `Категория снята (${res.applied} операций)`);
            await loadDetail();
        } catch (e: unknown) {
            setCatMsg(e instanceof Error ? e.message : 'Ошибка');
        } finally {
            setCatSaving(false);
        }
    };

    const handleDocUpload = async (file: File, docType: DocType) => {
        await api.uploadCounterpartyDocument(id, file, docType);
        const docs = await api.listCounterpartyDocuments(id);
        setDocuments(docs);
    };

    const handleDocDelete = async (docId: number) => {
        if (!confirm('Удалить документ?')) return;
        await api.deleteCounterpartyDocument(id, docId);
        setDocuments(docs => docs.filter(d => d.id !== docId));
    };

    const handleDocDownload = async (doc: CounterpartyDocument) => {
        try {
            const blob = await api.downloadCounterpartyDocument(id, doc.id);
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = doc.original_filename || 'document';
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка скачивания');
        }
    };

    if (loading && !detail) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
            </div>
        );
    }

    if (error && !detail) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span>{error}</span>
                    <button className="btn btn-secondary btn-sm" onClick={loadDetail}>Повторить</button>
                </div>
            </div>
        );
    }

    if (!detail) return null;

    const tabs: { key: TabKey; label: string; icon: string }[] = [
        { key: 'stats', label: 'Статистика', icon: '📊' },
        { key: 'docs', label: `Документы (${detail.docs_count})`, icon: '📎' },
        { key: 'payments', label: `Оплаты (${payments.length})`, icon: '💳' },
        ...((isCarrier || deliveries.length > 0)
            ? [
                { key: 'recon' as TabKey, label: 'Сверка оплат', icon: '🔗' },
                { key: 'deliveries' as TabKey, label: `Доставки (${deliveries.length})`, icon: '🚚' },
            ]
            : []),
    ];

    return (
        <div className="animate-in">
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24 }}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => router.push(`/p/${slug}/refs/counterparty`)}
                    >
                        ← Назад
                    </button>
                    <div>
                        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                            <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>{detail.name}</h1>
                            <CounterpartyTypeBadge type={detail.primary_type} />
                        </div>
                        {detail.inn && (
                            <div style={{ fontSize: 13, color: 'var(--color-text-dim)', fontFamily: 'monospace', marginTop: 4 }}>
                                ИНН: {detail.inn} {detail.kpp ? `/ КПП: ${detail.kpp}` : ''}
                            </div>
                        )}
                        {(detail.bank_account || detail.bik) && (
                            <div style={{ fontSize: 12, color: 'var(--color-text-dim)', fontFamily: 'monospace', marginTop: 2 }}>
                                {detail.bank_account ? `р/с ${detail.bank_account}` : ''}
                                {detail.bik ? `   БИК ${detail.bik}` : ''}
                                {detail.bank_name ? `  ·  ${detail.bank_name}` : ''}
                            </div>
                        )}
                    </div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => {
                            setEditForm({
                                name: detail.name,
                                primary_type: detail.primary_type,
                                inn: detail.inn,
                                kpp: detail.kpp,
                                contract_number: detail.contract_number,
                                notes: detail.notes,
                                bank_account: detail.bank_account,
                                bik: detail.bik,
                                bank_name: detail.bank_name,
                                corr_account: detail.corr_account,
                            });
                            setEditing(true);
                        }}
                    >
                        Изменить
                    </button>
                    <button className="btn btn-danger btn-sm" onClick={handleDelete}>Удалить</button>
                </div>
            </div>

            {error && (
                <div style={{ color: 'var(--color-danger)', marginBottom: 16, fontSize: 13 }}>{error}</div>
            )}

            {/* Edit form */}
            {editing && (
                <div className="glass-card" style={{ marginBottom: 16 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
                        <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Редактировать контрагента</h3>
                        <button className="btn btn-secondary btn-sm" onClick={() => setEditing(false)}>✕</button>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                        <div className="form-group" style={{ gridColumn: '1/-1' }}>
                            <label className="form-label">Название *</label>
                            <input className="form-input" value={editForm.name ?? ''} onChange={e => setEditForm(f => ({ ...f, name: e.target.value }))} />
                        </div>
                        <div className="form-group" style={{ gridColumn: '1/-1' }}>
                            <label className="form-label">Категория *</label>
                            <select
                                className="form-input"
                                value={editForm.primary_type ?? 'OTHER'}
                                onChange={e => setEditForm(f => ({ ...f, primary_type: e.target.value as CounterpartyType }))}
                            >
                                {(Object.entries(TYPE_CONFIG) as [CounterpartyType, { label: string; icon: string }][]).map(([key, cfg]) => (
                                    <option key={key} value={key}>{cfg.icon} {cfg.label}</option>
                                ))}
                            </select>
                        </div>
                        <div className="form-group">
                            <label className="form-label">ИНН</label>
                            <input className="form-input" value={editForm.inn ?? ''} onChange={e => setEditForm(f => ({ ...f, inn: e.target.value || null }))} />
                        </div>
                        <div className="form-group">
                            <label className="form-label">КПП</label>
                            <input className="form-input" value={editForm.kpp ?? ''} onChange={e => setEditForm(f => ({ ...f, kpp: e.target.value || null }))} />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Контракт</label>
                            <input className="form-input" value={editForm.contract_number ?? ''} onChange={e => setEditForm(f => ({ ...f, contract_number: e.target.value || null }))} />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Расчётный счёт</label>
                            <input className="form-input" value={editForm.bank_account ?? ''} onChange={e => setEditForm(f => ({ ...f, bank_account: e.target.value || null }))} placeholder="20 цифр" maxLength={20} />
                        </div>
                        <div className="form-group">
                            <label className="form-label">БИК</label>
                            <input className="form-input" value={editForm.bik ?? ''} onChange={e => setEditForm(f => ({ ...f, bik: e.target.value || null }))} placeholder="9 цифр" maxLength={9} />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Банк</label>
                            <input className="form-input" value={editForm.bank_name ?? ''} onChange={e => setEditForm(f => ({ ...f, bank_name: e.target.value || null }))} placeholder="Сбербанк / ВТБ..." />
                        </div>
                        <div className="form-group">
                            <label className="form-label">Корр. счёт</label>
                            <input className="form-input" value={editForm.corr_account ?? ''} onChange={e => setEditForm(f => ({ ...f, corr_account: e.target.value || null }))} placeholder="20 цифр" maxLength={20} />
                        </div>
                        <div className="form-group" style={{ gridColumn: '1/-1' }}>
                            <label className="form-label">Примечания</label>
                            <input className="form-input" value={editForm.notes ?? ''} onChange={e => setEditForm(f => ({ ...f, notes: e.target.value || null }))} />
                        </div>
                    </div>
                    <div style={{ marginTop: 16, display: 'flex', gap: 8 }}>
                        <button className="btn btn-primary btn-sm" onClick={handleSave} disabled={saving}>
                            {saving ? 'Сохранение...' : 'Сохранить'}
                        </button>
                        <button className="btn btn-secondary btn-sm" onClick={() => setEditing(false)}>Отмена</button>
                    </div>
                </div>
            )}

            {/* Категория расхода (level-2) — управляется здесь, применяется ко всем операциям */}
            <div className="glass-card" style={{ marginBottom: 16, padding: '14px 16px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 13, color: 'var(--color-text-muted)', fontWeight: 600 }}>💸 Категория расхода:</span>
                    {!catEditing ? (
                        <>
                            {detail.cat_lvl1
                                ? <span className="badge badge-warning">{detail.cat_lvl1}{detail.cat_lvl2 ? ` · ${detail.cat_lvl2}` : ''}</span>
                                : <span style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>не задана</span>}
                            <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }}
                                onClick={() => { setCatL1(detail.cat_lvl1 ?? ''); setCatL2(detail.cat_lvl2 ?? ''); setCatEditing(true); setCatMsg(''); }}>
                                {detail.cat_lvl1 ? 'Изменить' : 'Задать'}
                            </button>
                        </>
                    ) : (
                        <>
                            <input className="form-input" style={{ width: 220 }} placeholder="Категория (напр. Логистика)" value={catL1} onChange={e => setCatL1(e.target.value)} />
                            <input className="form-input" style={{ width: 180 }} placeholder="Под-категория (опц.)" value={catL2} onChange={e => setCatL2(e.target.value)} />
                            <button className="btn btn-primary btn-sm" onClick={handleSaveCategory} disabled={catSaving}>
                                {catSaving ? '…' : 'Применить ко всем операциям'}
                            </button>
                            <button className="btn btn-secondary btn-sm" onClick={() => setCatEditing(false)}>Отмена</button>
                        </>
                    )}
                </div>
                {catMsg && <div style={{ fontSize: 12, color: 'var(--color-success)', marginTop: 8 }}>{catMsg}</div>}
                <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginTop: 6 }}>
                    Применяется ко ВСЕМ операциям этого контрагента и к будущим импортам. Тип (бейдж выше) — крупная группа, категория — детальнее.
                </div>
            </div>

            {/* Date range picker for stats */}
            <div className="glass-card" style={{ marginBottom: 16, padding: '12px 16px' }}>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                    <span style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>Период статистики:</span>
                    <input type="date" className="form-input" style={{ width: 160 }} value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
                    <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                    <input type="date" className="form-input" style={{ width: 160 }} value={dateTo} onChange={e => setDateTo(e.target.value)} />
                </div>
            </div>

            {/* Stats */}
            <div style={{ marginBottom: 20 }}>
                <CurrencySplitStats rub={detail.stats_rub} cny={detail.stats_cny} loading={loading} />
            </div>

            {/* Linked entities */}
            {(detail.linked_warehouses.length > 0 || detail.linked_suppliers.length > 0) && (
                <div style={{ display: 'flex', gap: 16, marginBottom: 20 }}>
                    {detail.linked_warehouses.length > 0 && (
                        <div className="glass-card" style={{ flex: 1 }}>
                            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>🏢 Привязанные склады</div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                {detail.linked_warehouses.map(w => (
                                    <button
                                        key={w.id}
                                        type="button"
                                        onClick={() => router.push(`/p/${slug}/warehouse/${w.id}`)}
                                        style={{
                                            background: 'none', border: 'none', padding: '4px 0',
                                            fontSize: 13, color: 'var(--color-accent)', cursor: 'pointer',
                                            textAlign: 'left',
                                        }}
                                    >
                                        {w.name} →
                                    </button>
                                ))}
                            </div>
                        </div>
                    )}
                    {detail.linked_suppliers.length > 0 && (
                        <div className="glass-card" style={{ flex: 1 }}>
                            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>🏭 Связанные поставщики</div>
                            {detail.linked_suppliers.map(s => (
                                <div key={s.id} style={{ fontSize: 13, padding: '4px 0' }}>{s.name}</div>
                            ))}
                        </div>
                    )}
                </div>
            )}

            {/* Notes */}
            {detail.notes && (
                <div className="glass-card" style={{ marginBottom: 20, padding: '12px 16px' }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-text-muted)', marginBottom: 4 }}>Примечания</div>
                    <div style={{ fontSize: 14 }}>{detail.notes}</div>
                </div>
            )}

            {/* Tabs */}
            <div>
                {/* Tab navigation */}
                <div style={{ display: 'flex', gap: 4, marginBottom: 16, flexWrap: 'wrap' }}>
                    {tabs.map((t) => (
                        <button
                            key={t.key}
                            className={`btn ${activeKey === t.key ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                            onClick={() => setActiveKey(t.key)}
                        >
                            {t.icon} {t.label}
                        </button>
                    ))}
                </div>

                {/* Статистика */}
                {activeKey === 'stats' && (
                    <div>
                        {transactions.length === 0 ? (
                            <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                                Транзакций за выбранный период не найдено
                            </div>
                        ) : (
                            <div className="glass-card">
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                                    <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>
                                        Транзакции ({formatNumber(txTotal, 0)}
                                        {txTotal > transactions.length ? `, показано ${formatNumber(transactions.length, 0)}` : ''})
                                    </h3>
                                    <button
                                        className="btn btn-secondary btn-sm"
                                        onClick={() => exportToExcel(
                                            transactions.map(t => ({
                                                Дата: formatDate(t.date),
                                                Счёт: t.account,
                                                Валюта: t.currency,
                                                Приход: Number(t.income),
                                                Расход: Number(t.expense),
                                                Назначение: t.purpose ?? '',
                                                Тип: t.event_type2 ?? '',
                                                'Займ-тип': t.loan_payment_type ?? '',
                                                Контракт: t.contract_number ?? '',
                                            })),
                                            'counterparty_transactions',
                                        )}
                                    >
                                        Excel
                                    </button>
                                </div>
                                <div style={{ overflowX: 'auto' }}>
                                    <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                                        <thead>
                                            <tr style={{ borderBottom: '1px solid var(--color-border)', textAlign: 'left' }}>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Дата</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Счёт</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', textAlign: 'right' }}>Приход</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', textAlign: 'right' }}>Расход</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Вал.</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Назначение</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {transactions.map(t => (
                                                <tr key={t.id} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                                    <td style={{ padding: '8px 8px', whiteSpace: 'nowrap' }}>{formatDate(t.date)}</td>
                                                    <td style={{ padding: '8px 8px', fontFamily: 'monospace', fontSize: 12, color: 'var(--color-text-muted)' }}>
                                                        {t.account.length > 10 ? `…${t.account.slice(-10)}` : t.account}
                                                    </td>
                                                    <td style={{ padding: '8px 8px', textAlign: 'right', color: Number(t.income) > 0 ? 'var(--color-success)' : undefined, fontVariantNumeric: 'tabular-nums' }}>
                                                        {Number(t.income) > 0 ? formatNumber(t.income) : ''}
                                                    </td>
                                                    <td style={{ padding: '8px 8px', textAlign: 'right', color: Number(t.expense) > 0 ? 'var(--color-danger)' : undefined, fontVariantNumeric: 'tabular-nums' }}>
                                                        {Number(t.expense) > 0 ? formatNumber(t.expense) : ''}
                                                    </td>
                                                    <td style={{ padding: '8px 8px', fontSize: 12 }}>{t.currency}</td>
                                                    <td style={{ padding: '8px 8px', fontSize: 12, maxWidth: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={t.purpose ?? ''}>
                                                        {t.purpose ?? '—'}
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        )}
                    </div>
                )}

                {/* Документы */}
                {activeKey === 'docs' && (
                    <div>
                        <div className="glass-card" style={{ marginBottom: 16 }}>
                            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>Загрузить документ</div>
                            <DocumentUploader onUpload={handleDocUpload} />
                        </div>

                        {documents.length === 0 ? (
                            <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                                Документов пока нет
                            </div>
                        ) : (
                            <div className="glass-card">
                                <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>
                                    Документы ({documents.length})
                                </div>
                                {documents.map(doc => (
                                    <div
                                        key={doc.id}
                                        style={{
                                            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                                            padding: '10px 0', borderBottom: '1px solid var(--color-border)',
                                        }}
                                    >
                                        <div style={{ flex: 1, minWidth: 0 }}>
                                            <div style={{ fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                {doc.original_filename || 'Без имени'}
                                            </div>
                                            <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginTop: 2 }}>
                                                {DOC_TYPE_LABELS[doc.doc_type]} · {doc.file_size ? `${(doc.file_size / 1024).toFixed(1)} KB` : ''} · {formatDate(doc.uploaded_at)}
                                            </div>
                                        </div>
                                        <div style={{ display: 'flex', gap: 8 }}>
                                            <button
                                                type="button"
                                                className="btn btn-secondary btn-sm"
                                                style={{ padding: '2px 8px', fontSize: 11 }}
                                                onClick={() => handleDocDownload(doc)}
                                            >
                                                Скачать
                                            </button>
                                            <button
                                                className="btn btn-danger btn-sm"
                                                style={{ padding: '2px 8px', fontSize: 11 }}
                                                onClick={() => handleDocDelete(doc.id)}
                                            >
                                                ✕
                                            </button>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                )}

                {/* Оплаты — заявки на оплату по этому контрагенту (связанные логистом) */}
                {activeKey === 'payments' && (
                    <div>
                        {paymentsLoading ? (
                            <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка...</div>
                        ) : payments.length === 0 ? (
                            <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                                Заявок на оплату по этому контрагенту нет.
                                <div style={{ fontSize: 12, marginTop: 6 }}>Создаются логистом из листа логиста по заборам.</div>
                            </div>
                        ) : (
                            <div className="glass-card">
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                                    <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Заявки на оплату ({payments.length})</h3>
                                    <button
                                        className="btn btn-secondary btn-sm"
                                        onClick={() => exportToExcel(
                                            payments.map(p => ({
                                                '№': p.number,
                                                Статус: PAY_STATUS[p.status]?.label ?? p.status,
                                                Сумма: Number(p.amount),
                                                'Дата забора': p.pickup_date ? formatDate(p.pickup_date) : '',
                                                Документов: p.doc_count,
                                                Создана: formatDate(p.created_at),
                                            })),
                                            'counterparty_payments',
                                        )}
                                    >
                                        Excel
                                    </button>
                                </div>
                                <div style={{ overflowX: 'auto' }}>
                                    <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                                        <thead>
                                            <tr style={{ borderBottom: '1px solid var(--color-border)', textAlign: 'left' }}>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase' }}>Статус</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase' }}>№</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', textAlign: 'right' }}>Сумма</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase' }}>Дата забора</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', textAlign: 'right' }}>Доки</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase' }}>Создана</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {payments.map(p => {
                                                const st = PAY_STATUS[p.status] ?? { label: p.status, cls: 'badge-secondary' };
                                                return (
                                                    <tr key={p.id} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                                        <td style={{ padding: '8px 8px' }}><span className={`badge ${st.cls}`}>{st.label}</span></td>
                                                        <td style={{ padding: '8px 8px', fontWeight: 500 }}>{p.number}</td>
                                                        <td style={{ padding: '8px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{formatNumber(Number(p.amount), 2)} ₽</td>
                                                        <td style={{ padding: '8px 8px', whiteSpace: 'nowrap' }}>{p.pickup_date ? formatDate(p.pickup_date) : '—'}</td>
                                                        <td style={{ padding: '8px 8px', textAlign: 'right' }}>{p.doc_count}</td>
                                                        <td style={{ padding: '8px 8px', whiteSpace: 'nowrap' }}>{p.created_at ? formatDate(p.created_at) : '—'}</td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        )}
                    </div>
                )}

                {/* Сверка оплат — заборы (оплачено/не оплачено) + платежи без забора */}
                {activeKey === 'recon' && (
                    <ReconciliationTab counterpartyId={id} />
                )}

                {/* Доставки — заявки, где этот контрагент является перевозчиком */}
                {activeKey === 'deliveries' && (
                    <div>
                        {deliveriesLoading ? (
                            <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                                Загрузка...
                            </div>
                        ) : deliveries.length === 0 ? (
                            <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                                Доставок по этому перевозчику нет
                            </div>
                        ) : (
                            <div className="glass-card">
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                                    <h3 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>
                                        Заявки на доставку ({deliveries.length})
                                    </h3>
                                    <button
                                        className="btn btn-secondary btn-sm"
                                        onClick={() => exportToExcel(
                                            deliveries.map(d => ({
                                                '№': d.number,
                                                Статус: ASM_STATUS[d.status]?.label ?? d.status,
                                                Забор: d.warehouse_name ?? '',
                                                'Сдача WB': d.effective_wb_warehouse ?? '',
                                                'Дата забора': d.pickup_date ? formatDate(d.pickup_date) : '',
                                                'Дата сдачи': d.delivery_date ? formatDate(d.delivery_date) : '',
                                                Палет: d.pallets_count,
                                                'Вес, кг': Number(d.total_weight_kg ?? 0),
                                                'Стоимость, ₽': Number(d.pickup_cost ?? 0),
                                            })),
                                            'carrier_deliveries',
                                        )}
                                    >
                                        Excel
                                    </button>
                                </div>
                                <div style={{ overflowX: 'auto' }}>
                                    <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                                        <thead>
                                            <tr style={{ borderBottom: '1px solid var(--color-border)', textAlign: 'left' }}>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>№</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Статус</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Забор</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Сдача WB</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Дата забора</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Дата сдачи</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', textAlign: 'right' }}>Палет</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', textAlign: 'right' }}>Вес</th>
                                                <th style={{ padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', textAlign: 'right' }}>Стоимость</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {deliveries.map(d => {
                                                const st = ASM_STATUS[d.status] ?? { label: d.status, cls: 'badge-secondary' };
                                                return (
                                                    <tr
                                                        key={d.id}
                                                        style={{ borderBottom: '1px solid var(--color-border)', cursor: 'pointer' }}
                                                        onClick={() => router.push(`/p/${slug}/warehouse/assembly/${d.id}`)}
                                                    >
                                                        <td style={{ padding: '8px 8px', fontWeight: 500 }}>{d.number}</td>
                                                        <td style={{ padding: '8px 8px' }}><span className={`badge ${st.cls}`}>{st.label}</span></td>
                                                        <td style={{ padding: '8px 8px', color: 'var(--color-text-muted)' }}>{d.warehouse_name ?? '—'}</td>
                                                        <td style={{ padding: '8px 8px' }}>{d.effective_wb_warehouse ?? '—'}</td>
                                                        <td style={{ padding: '8px 8px', whiteSpace: 'nowrap' }}>{d.pickup_date ? formatDate(d.pickup_date) : '—'}</td>
                                                        <td style={{ padding: '8px 8px', whiteSpace: 'nowrap' }}>{d.delivery_date ? formatDate(d.delivery_date) : '—'}</td>
                                                        <td style={{ padding: '8px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{d.pallets_count}</td>
                                                        <td style={{ padding: '8px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{d.total_weight_kg ? `${formatNumber(d.total_weight_kg, 1)} кг` : '—'}</td>
                                                        <td style={{ padding: '8px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: Number(d.pickup_cost) > 0 ? 'var(--color-danger)' : undefined }}>{d.pickup_cost ? `${formatNumber(d.pickup_cost)} ₽` : '—'}</td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        )}
                    </div>
                )}

            </div>
        </div>
    );
}
