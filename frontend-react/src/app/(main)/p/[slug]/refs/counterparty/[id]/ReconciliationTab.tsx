'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate, formatDateTime, exportToExcel } from '@/lib/utils';
import type { CounterpartyReconciliation, ShippableShipmentRow, ReconciliationPaymentRow } from '@/types/api';

function isPaid(s: ShippableShipmentRow): boolean {
    return s.matched_transaction_id != null || s.request_status === 'PAID';
}

/**
 * Переход к НАШЕМУ документу забора: заявка на сборку либо перемещение.
 * У забора переезда `assembly_request_id` пуст — до появления ветки с
 * `stock_transfer_id` такая строка выглядела ссылкой, но клик молча ничего
 * не делал. Функции модульные: строка забора рисуется в двух разных
 * компонентах этого файла (таблица сверки и карточка платежа).
 */
function canOpenShipmentDoc(s: ShippableShipmentRow): boolean {
    return s.assembly_request_id != null || s.stock_transfer_id != null;
}

function shipmentDocTitle(s: ShippableShipmentRow): string {
    if (s.assembly_request_id != null) return 'Открыть заявку';
    if (s.stock_transfer_id != null) return 'Открыть перемещение';
    return 'Забор без нашего документа — открывать нечего';
}

function openShipmentDoc(
    s: ShippableShipmentRow,
    slug: string,
    router: ReturnType<typeof useRouter>,
): void {
    if (s.assembly_request_id != null) router.push(`/p/${slug}/warehouse/assembly/${s.assembly_request_id}`);
    else if (s.stock_transfer_id != null) router.push(`/p/${slug}/warehouse/transfers/${s.stock_transfer_id}`);
}

const TH: React.CSSProperties = {
    padding: '8px 8px', fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)',
    textTransform: 'uppercase', letterSpacing: '0.05em', whiteSpace: 'nowrap',
};
const TD: React.CSSProperties = { padding: '8px 8px' };

export default function ReconciliationTab({ counterpartyId }: { counterpartyId: number }) {
    const router = useRouter();
    const params = useParams();
    const slug = params.slug as string;
    const [data, setData] = useState<CounterpartyReconciliation | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [archived, setArchived] = useState(false);
    const [busy, setBusy] = useState(false);
    // Привязка: какой платёж раскрыт + выбранные заборы.
    const [linkTxn, setLinkTxn] = useState<number | null>(null);
    const [selected, setSelected] = useState<Set<number>>(new Set());

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            setData(await api.getCounterpartyReconciliation(counterpartyId, archived));
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки сверки');
        } finally {
            setLoading(false);
        }
    }, [counterpartyId, archived]);

    useEffect(() => { load(); setLinkTxn(null); setSelected(new Set()); }, [load]);

    const unpaidShipments = useMemo(
        () => (data?.shipments ?? []).filter(s => !isPaid(s)),
        [data],
    );

    const reload = async () => { await load(); setLinkTxn(null); setSelected(new Set()); };

    const wrap = async (fn: () => Promise<unknown>) => {
        setBusy(true);
        try { await fn(); await reload(); }
        catch (e: unknown) { setError(e instanceof Error ? e.message : 'Ошибка операции'); }
        finally { setBusy(false); }
    };

    const openLink = (txnId: number) => {
        setLinkTxn(prev => (prev === txnId ? null : txnId));
        setSelected(new Set());
    };

    const toggleSel = (id: number) => setSelected(prev => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
    });

    const selectedSum = useMemo(
        () => unpaidShipments.filter(s => selected.has(s.outbound_shipment_id))
            .reduce((acc, s) => acc + Number(s.pickup_cost ?? 0), 0),
        [unpaidShipments, selected],
    );

    const handleExport = () => {
        if (!data) return;
        exportToExcel([
            ...data.shipments.map((s) => ({
                Тип: 'Забор', '№': s.number,
                Статус: isPaid(s) ? 'Оплачено' : 'Не оплачено',
                Сумма: s.pickup_cost != null ? Number(s.pickup_cost) : '',
                Дата: s.shipped_date ? formatDate(s.shipped_date) : '',
            })),
            ...data.payments.map((p) => ({
                Тип: 'Платёж', '№': `txn-${p.transaction_id}`,
                Статус: p.linked_count > 0 ? `привязано ${p.linked_count}` : 'без забора',
                Сумма: Number(p.amount),
                Дата: formatDate(p.date),
                Привязано: Number(p.linked_sum),
                Разница: Number(p.diff),
                Назначение: p.purpose ?? '',
            })),
        ], 'counterparty_reconciliation');
    };

    if (loading && !data) {
        return <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-dim)' }}>Загрузка...</div>;
    }
    if (error && !data) {
        return (
            <div className="glass-card" style={{ padding: 24, color: 'var(--color-danger)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span>{error}</span>
                <button className="btn btn-secondary btn-sm" onClick={load}>Повторить</button>
            </div>
        );
    }
    if (!data) return null;

    const noData = data.shipments.length === 0 && data.payments.length === 0;

    return (
        <div>
            {/* Summary + controls */}
            <div className="glass-card" style={{ padding: 16, marginBottom: 16, display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 13 }}>
                    <span><span className="badge badge-success">Сопоставлено</span> {formatNumber(data.matched_count, 0)}</span>
                    <span><span className="badge badge-warning">Не оплачено</span> {formatNumber(data.unpaid_count, 0)}</span>
                    <span><span className="badge badge-info">Платежей</span> {formatNumber(data.payment_count, 0)}</span>
                    <span><span className="badge badge-danger">Без забора</span> {formatNumber(data.unlinked_payment_count, 0)}</span>
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <button className={`btn btn-sm ${archived ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setArchived((a) => !a)}>
                        {archived ? '← К активным' : '🗄 Архив'}
                    </button>
                    <button className="btn btn-sm btn-secondary" onClick={handleExport} disabled={noData}>📥 Excel</button>
                </div>
            </div>

            {error && (
                <div className="glass-card" style={{ padding: 12, marginBottom: 16, color: 'var(--color-danger)' }}>
                    {error}
                    <span style={{ float: 'right', cursor: 'pointer' }} onClick={() => setError('')}>✕</span>
                </div>
            )}

            {noData ? (
                <div className="glass-card" style={{ padding: 40, textAlign: 'center', color: 'var(--color-text-dim)' }}>
                    {archived ? 'Архив пуст.' : 'Заборов и платежей по этому перевозчику нет.'}
                </div>
            ) : (
                <>
                    {/* ─── Платежи из выписки (с привязкой N заборов) ─── */}
                    <div className="glass-card" style={{ marginBottom: 16 }}>
                        <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 4px' }}>Платежи из выписки ({data.payments.length})</h3>
                        <p style={{ fontSize: 12, color: 'var(--color-text-dim)', margin: '0 0 12px' }}>
                            Исходящие платежи этому перевозчику. Одну оплату можно связать с несколькими заборами — суммы сверяются.
                        </p>
                        {data.payments_truncated && (
                            <div style={{ fontSize: 12, color: 'var(--color-warning)', marginBottom: 12 }}>
                                ⚠️ Показаны только последние платежи (список усечён) — уточните поиск по периоду.
                            </div>
                        )}
                        {data.payments.length === 0 ? (
                            <div style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>Платежей нет.</div>
                        ) : (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                                {data.payments.map((p) => (
                                    <PaymentCard
                                        key={p.transaction_id}
                                        p={p}
                                        slug={slug}
                                        router={router}
                                        archived={archived}
                                        busy={busy}
                                        expanded={linkTxn === p.transaction_id}
                                        onToggleLink={() => openLink(p.transaction_id)}
                                        unpaidShipments={unpaidShipments}
                                        selected={selected}
                                        selectedSum={selectedSum}
                                        onToggleSel={toggleSel}
                                        onLink={() => wrap(() => api.linkShipmentsToPayment(p.transaction_id, [...selected]))}
                                        onArchive={() => wrap(() => archived ? api.unarchiveOrphanPayments([p.transaction_id]) : api.archiveOrphanPayments([p.transaction_id]))}
                                        linkedShipments={(data.shipments).filter(s => s.matched_transaction_id === p.transaction_id)}
                                        onUnlink={(sid) => wrap(() => api.unlinkShipments([sid]))}
                                    />
                                ))}
                            </div>
                        )}
                    </div>

                    {/* ─── Заборы ─── */}
                    <div className="glass-card">
                        <h3 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 12px' }}>Заборы ({data.shipments.length})</h3>
                        {data.shipments.length === 0 ? (
                            <div style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>Нет заборов.</div>
                        ) : (
                            <div style={{ overflowX: 'auto' }}>
                                <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                                    <thead>
                                        <tr style={{ borderBottom: '1px solid var(--color-border)', textAlign: 'left' }}>
                                            <th style={TH}>№ отгрузки</th>
                                            <th style={TH}>Дата</th>
                                            <th style={TH}>Направление</th>
                                            <th style={{ ...TH, textAlign: 'right' }}>Сумма</th>
                                            <th style={TH}>Оплата</th>
                                            <th style={{ ...TH, textAlign: 'right' }}></th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {data.shipments.map((s) => (
                                            <tr key={s.outbound_shipment_id} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                                <td style={TD}>
                                                    <button
                                                        onClick={() => openShipmentDoc(s, slug, router)}
                                                        disabled={!canOpenShipmentDoc(s)}
                                                        title={shipmentDocTitle(s)}
                                                        style={{
                                                            background: 'none', border: 'none', padding: 0, fontWeight: 600, fontSize: 13,
                                                            cursor: canOpenShipmentDoc(s) ? 'pointer' : 'default',
                                                            color: canOpenShipmentDoc(s) ? 'var(--color-accent)' : 'var(--color-text-muted)',
                                                        }}
                                                    >
                                                        {s.number}{canOpenShipmentDoc(s) ? ' ↗' : ''}
                                                    </button>
                                                </td>
                                                <td style={{ ...TD, whiteSpace: 'nowrap' }}>{s.shipped_date ? formatDate(s.shipped_date) : '—'}</td>
                                                <td style={{ ...TD, color: 'var(--color-text-muted)' }}>{s.destination ?? '—'}</td>
                                                <td style={{ ...TD, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                                                    {s.pickup_cost != null ? `${formatNumber(Number(s.pickup_cost), 2)} ₽` : '—'}
                                                </td>
                                                <td style={TD}>
                                                    {s.request_status === 'PAID' ? (
                                                        <span className="badge badge-success">Оплачено (заявка)</span>
                                                    ) : s.matched_transaction_id ? (
                                                        <span className="badge badge-success" title={s.matched_txn_date ? `выписка от ${formatDate(s.matched_txn_date)}` : 'привязано к платежу'}>✅ Оплачено</span>
                                                    ) : s.request_status ? (
                                                        <span className="badge badge-info">Заявка: {s.request_status}</span>
                                                    ) : (
                                                        <span className="badge badge-warning">Не оплачено</span>
                                                    )}
                                                </td>
                                                <td style={{ ...TD, textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                    {s.matched_transaction_id && s.request_status !== 'PAID' && (
                                                        <button className="btn btn-secondary btn-sm" style={{ padding: '2px 8px', fontSize: 11, marginRight: 6 }} disabled={busy} onClick={() => wrap(() => api.unlinkShipments([s.outbound_shipment_id]))}>
                                                            Отвязать
                                                        </button>
                                                    )}
                                                    <button className="btn btn-secondary btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} disabled={busy} onClick={() => wrap(() => archived ? api.unarchiveShipments([s.outbound_shipment_id]) : api.archiveShipments([s.outbound_shipment_id]))}>
                                                        {archived ? 'Вернуть' : '🗄'}
                                                    </button>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>
                </>
            )}
        </div>
    );
}

function diffBadge(p: ReconciliationPaymentRow) {
    if (p.linked_count === 0) return <span className="badge badge-secondary">нет привязки</span>;
    if (Number(p.diff) === 0) return <span className="badge badge-success">✓ сошлось</span>;
    const d = Number(p.diff);
    return <span className="badge badge-warning">разница {formatNumber(d, 2)} ₽</span>;
}

function PaymentCard({
    p, slug, router, archived, busy, expanded, onToggleLink, unpaidShipments, selected, selectedSum,
    onToggleSel, onLink, onArchive, linkedShipments, onUnlink,
}: {
    p: ReconciliationPaymentRow;
    slug: string;
    router: ReturnType<typeof useRouter>;
    archived: boolean;
    busy: boolean;
    expanded: boolean;
    onToggleLink: () => void;
    unpaidShipments: ShippableShipmentRow[];
    selected: Set<number>;
    selectedSum: number;
    onToggleSel: (id: number) => void;
    onLink: () => void;
    onArchive: () => void;
    linkedShipments: ShippableShipmentRow[];
    onUnlink: (sid: number) => void;
}) {
    const amount = Number(p.amount);
    const remaining = amount - Number(p.linked_sum) - selectedSum;
    return (
        <div style={{ border: '1px solid var(--color-border)', borderRadius: 12, padding: '10px 14px' }}>
            <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'space-between' }}>
                <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
                        <span style={{ fontWeight: 700, fontSize: 15, color: 'var(--color-danger)', fontVariantNumeric: 'tabular-nums' }}>
                            {formatNumber(amount, 2)} ₽
                        </span>
                        <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{formatDateTime(p.date)}</span>
                        {diffBadge(p)}
                        {p.linked_count > 0 && (
                            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                привязано {p.linked_count} на {formatNumber(Number(p.linked_sum), 2)} ₽
                            </span>
                        )}
                    </div>
                    {p.purpose && (
                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)', marginTop: 4, maxWidth: 720, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={p.purpose}>
                            {p.purpose}
                        </div>
                    )}
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    {!archived && (
                        <button className={`btn btn-sm ${expanded ? 'btn-primary' : 'btn-secondary'}`} disabled={busy} onClick={onToggleLink}>
                            🔗 Связать заборы
                        </button>
                    )}
                    <button className="btn btn-secondary btn-sm" style={{ fontSize: 11 }} disabled={busy} onClick={onArchive}>
                        {archived ? 'Вернуть' : '🗄 Разобрано'}
                    </button>
                </div>
            </div>

            {/* Уже привязанные заборы */}
            {linkedShipments.length > 0 && (
                <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {linkedShipments.map(s => (
                        <span key={s.outbound_shipment_id} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, background: 'rgba(52,199,89,0.12)', padding: '2px 8px', borderRadius: 8 }}>
                            <button
                                onClick={() => openShipmentDoc(s, slug, router)}
                                disabled={!canOpenShipmentDoc(s)}
                                title={shipmentDocTitle(s)}
                                style={{
                                    background: 'none', border: 'none', padding: 0, fontWeight: 600,
                                    cursor: canOpenShipmentDoc(s) ? 'pointer' : 'default',
                                    color: canOpenShipmentDoc(s) ? 'var(--color-accent)' : 'var(--color-text-muted)',
                                }}
                            >
                                {s.number}
                            </button>
                            <span style={{ color: 'var(--color-text-muted)' }}>{formatNumber(Number(s.pickup_cost ?? 0), 2)} ₽</span>
                            {!archived && s.request_status !== 'PAID' && (
                                <span style={{ cursor: 'pointer', color: 'var(--color-danger)' }} title="Отвязать" onClick={() => onUnlink(s.outbound_shipment_id)}>✕</span>
                            )}
                        </span>
                    ))}
                </div>
            )}

            {/* Панель привязки */}
            {expanded && (
                <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px dashed var(--color-border)' }}>
                    {unpaidShipments.length === 0 ? (
                        <div style={{ fontSize: 13, color: 'var(--color-text-dim)' }}>Нет неоплаченных заборов для привязки.</div>
                    ) : (
                        <>
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 8 }}>Выберите заборы для привязки к этой оплате:</div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 240, overflowY: 'auto' }}>
                                {unpaidShipments.map(s => (
                                    <label key={s.outbound_shipment_id} style={{ display: 'flex', gap: 10, alignItems: 'center', fontSize: 13, padding: '4px 6px', borderRadius: 8, cursor: 'pointer', background: selected.has(s.outbound_shipment_id) ? 'rgba(0,122,255,0.06)' : undefined }}>
                                        <input type="checkbox" checked={selected.has(s.outbound_shipment_id)} onChange={() => onToggleSel(s.outbound_shipment_id)} style={{ cursor: 'pointer' }} />
                                        <span style={{ fontWeight: 600, minWidth: 80 }}>{s.number}</span>
                                        <span style={{ color: 'var(--color-text-muted)', minWidth: 90 }}>{s.shipped_date ? formatDate(s.shipped_date) : '—'}</span>
                                        <span style={{ color: 'var(--color-text-muted)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.destination ?? ''}</span>
                                        <span style={{ fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{formatNumber(Number(s.pickup_cost ?? 0), 2)} ₽</span>
                                    </label>
                                ))}
                            </div>
                            <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap', marginTop: 10 }}>
                                <span style={{ fontSize: 13 }}>
                                    Выбрано: <b style={{ fontVariantNumeric: 'tabular-nums' }}>{formatNumber(selectedSum, 2)} ₽</b>
                                    {' '}+ привязано {formatNumber(Number(p.linked_sum), 2)} ₽
                                    {' '}из {formatNumber(amount, 2)} ₽
                                </span>
                                {Math.abs(remaining) < 0.005 ? (
                                    <span className="badge badge-success">✓ сумма сходится</span>
                                ) : remaining > 0 ? (
                                    <span className="badge badge-warning">останется {formatNumber(remaining, 2)} ₽</span>
                                ) : (
                                    <span className="badge badge-danger">превышение на {formatNumber(-remaining, 2)} ₽</span>
                                )}
                                <button className="btn btn-primary btn-sm" disabled={busy || selected.size === 0} onClick={onLink}>
                                    Связать выбранные ({selected.size})
                                </button>
                            </div>
                        </>
                    )}
                </div>
            )}
        </div>
    );
}
