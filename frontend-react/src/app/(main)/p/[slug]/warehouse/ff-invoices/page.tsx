'use client';
/**
 * Счета ФФ — счета фулфилментов за услуги (отправки/хранение/приёмки/логистика):
 * загрузка файла с распознаванием, сверка с нашим расчётом (строки разнесения),
 * привязка оплаты (дебет выписки по ИНН юрлиц склада или заявка на оплату).
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatDate, formatNumber, pluralRu } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import Toast from '@/components/Toast';
import type { Column } from '@/components/DataTable';
import type {
    FfCandidatePayment,
    FfInvoiceDetail,
    FfInvoiceKind,
    FfInvoiceRow,
    FfInvoiceStatus,
    FfInvoiceUpdatePayload,
    Warehouse,
} from '@/types/api';

// ─── Справочники ────────────────────────────────────────────────────────────

const KIND_LABEL: Record<FfInvoiceKind, string> = {
    SHIPMENT: 'Отправки',
    STORAGE: 'Хранение',
    RECEIVING: 'Приёмки',
    LOGISTICS: 'Логистика',
    MIXED: 'Смешанный',
};

const STATUS_MAP: Record<FfInvoiceStatus, { label: string; className: string }> = {
    NEW:        { label: 'Новый',       className: 'badge-secondary' },
    RECONCILED: { label: 'Сверен',      className: 'badge-info' },
    DISPUTED:   { label: 'Расхождение', className: 'badge-danger' },
    PAID:       { label: 'Оплачен',     className: 'badge-success' },
};

const LINE_KIND_LABEL: Record<string, string> = {
    ASSEMBLY: 'Отправка',
    STORAGE: 'Хранение',
    RECEIVING: 'Приёмка',
    LOGISTICS: 'Забор',
    CUSTOM: 'Прочее',
};

function periodLabel(start: string | null, end: string | null): string {
    if (!start && !end) return '—';
    return `${start ? formatDate(start) : '…'} — ${end ? formatDate(end) : '…'}`;
}

/** Δ = счёт − наш расчёт. Подсветка: в допуске (RECONCILED) — зелёный, DISPUTED — красный. */
function diffCell(row: FfInvoiceRow): React.ReactNode {
    if (row.diff == null) return <span style={{ color: 'var(--color-text-muted)' }}>—</span>;
    const color = row.status === 'DISPUTED' ? 'var(--color-danger)'
        : row.status === 'RECONCILED' || row.status === 'PAID' ? 'var(--color-success)'
        : 'var(--color-text-muted)';
    const sign = row.diff > 0 ? '+' : '';
    return (
        <span style={{ fontWeight: 600, color }} title="Сумма счёта − наш расчёт">
            {sign}{formatNumber(row.diff)}
        </span>
    );
}

function paymentCell(row: FfInvoiceRow): React.ReactNode {
    if (row.matched_transaction_id) {
        return <span title={`Привязан дебет выписки #${row.matched_transaction_id}`}>🏦</span>;
    }
    if (row.payment_request_id) {
        return <span title={`Заявка на оплату #${row.payment_request_id}`}>📄</span>;
    }
    return <span style={{ color: 'var(--color-text-muted)' }}>—</span>;
}

// ─── Страница ───────────────────────────────────────────────────────────────

export default function FfInvoicesPage() {
    const params = useParams();
    const slug = params.slug as string;

    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [items, setItems] = useState<FfInvoiceRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [toast, setToast] = useState('');

    // Фильтры
    const [whFilter, setWhFilter] = useState<number | ''>('');
    const [statusFilter, setStatusFilter] = useState<FfInvoiceStatus | ''>('');
    const [kindFilter, setKindFilter] = useState<FfInvoiceKind | ''>('');

    // Загрузка файла счёта
    const fileInputRef = useRef<HTMLInputElement>(null);
    const [uploading, setUploading] = useState(false);

    // Форма счёта: подтверждение после upload / ручное создание / правка
    const [editor, setEditor] = useState<{
        mode: 'confirm' | 'edit' | 'create';
        invoice: FfInvoiceDetail | null;
        parsedWarehouseId: number | null;
        warnings: string[];
    } | null>(null);

    // Деталка
    const [detailId, setDetailId] = useState<number | null>(null);

    const ffWarehouses = useMemo(
        () => warehouses.filter(w => w.warehouse_type === 'FULFILLMENT'),
        [warehouses],
    );

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const res = await api.listFfInvoices({
                warehouse_id: whFilter === '' ? undefined : whFilter,
                status: statusFilter || undefined,
                kind: kindFilter || undefined,
            });
            if (signal?.aborted) return;
            setItems(res.items);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [whFilter, statusFilter, kindFilter]);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load]);

    useEffect(() => {
        let cancelled = false;
        api.getWarehouses()
            .then(whs => { if (!cancelled) setWarehouses(whs); })
            .catch(() => { /* фильтр по складу останется пустым */ });
        return () => { cancelled = true; };
    }, []);

    const handleFileChosen = async (file: File) => {
        setUploading(true);
        setError('');
        try {
            const res = await api.uploadFfInvoice(file);
            setEditor({
                mode: 'confirm',
                invoice: res.invoice,
                parsedWarehouseId: res.parsed_warehouse_id,
                warnings: res.warnings ?? [],
            });
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки файла');
        } finally {
            setUploading(false);
            if (fileInputRef.current) fileInputRef.current.value = '';
        }
    };

    const cols: Column[] = [
        { key: 'number', label: '№', render: (v: string | null) => v || '—' },
        { key: 'invoice_date', label: 'Дата', render: (v: string | null) => formatDate(v) },
        { key: 'warehouse_name', label: 'Склад', render: (v: string | null, row: FfInvoiceRow) => v || (row.warehouse_id == null ? <span className="badge badge-warning" style={{ fontSize: 11 }}>не определён</span> : `#${row.warehouse_id}`) },
        {
            key: 'period_start', label: 'Период', sortable: false,
            render: (_: unknown, row: FfInvoiceRow) => <span style={{ whiteSpace: 'nowrap', fontSize: 13 }}>{periodLabel(row.period_start, row.period_end)}</span>,
        },
        { key: 'kind', label: 'Тип', render: (v: FfInvoiceKind) => KIND_LABEL[v] ?? v },
        { key: 'amount', label: 'Сумма, ₽', align: 'right', render: (v: number) => <strong>{formatNumber(v)}</strong> },
        { key: 'our_amount', label: 'Наш расчёт, ₽', align: 'right', render: (v: number | null) => v != null ? formatNumber(v) : '—' },
        { key: 'diff', label: 'Δ', align: 'right', render: (_: unknown, row: FfInvoiceRow) => diffCell(row) },
        {
            key: 'status', label: 'Статус',
            render: (v: FfInvoiceStatus) => <span className={`badge ${STATUS_MAP[v]?.className ?? ''}`}>{STATUS_MAP[v]?.label ?? v}</span>,
        },
        { key: 'payment', label: 'Оплата', align: 'center', sortable: false, render: (_: unknown, row: FfInvoiceRow) => paymentCell(row) },
    ];

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Счета ФФ</h1>
                    <p className="page-subtitle">Счета фулфилментов: сверка с нашим расчётом и привязка оплат</p>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <input
                        ref={fileInputRef}
                        type="file"
                        accept=".pdf,.png,.jpg,.jpeg,.xls,.xlsx,.doc,.docx"
                        style={{ display: 'none' }}
                        onChange={e => { const f = e.target.files?.[0]; if (f) handleFileChosen(f); }}
                    />
                    <button className="btn btn-primary" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
                        {uploading ? 'Распознавание...' : '⬆ Загрузить счёт'}
                    </button>
                    <button
                        className="btn btn-secondary"
                        onClick={() => setEditor({ mode: 'create', invoice: null, parsedWarehouseId: null, warnings: [] })}
                    >
                        + Вручную
                    </button>
                </div>
            </div>

            {/* Фильтры */}
            <div className="glass-card" style={{ padding: 16, marginBottom: 16, display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                <div>
                    <label className="form-label" style={{ fontSize: 12 }}>Склад</label>
                    <select className="form-input" value={whFilter} onChange={e => setWhFilter(e.target.value ? Number(e.target.value) : '')} style={{ width: 220 }}>
                        <option value="">Все склады</option>
                        {ffWarehouses.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
                    </select>
                </div>
                <div>
                    <label className="form-label" style={{ fontSize: 12 }}>Статус</label>
                    <select className="form-input" value={statusFilter} onChange={e => setStatusFilter(e.target.value as FfInvoiceStatus | '')} style={{ width: 180 }}>
                        <option value="">Все статусы</option>
                        {(Object.keys(STATUS_MAP) as FfInvoiceStatus[]).map(s => <option key={s} value={s}>{STATUS_MAP[s].label}</option>)}
                    </select>
                </div>
                <div>
                    <label className="form-label" style={{ fontSize: 12 }}>Тип</label>
                    <select className="form-input" value={kindFilter} onChange={e => setKindFilter(e.target.value as FfInvoiceKind | '')} style={{ width: 180 }}>
                        <option value="">Все типы</option>
                        {(Object.keys(KIND_LABEL) as FfInvoiceKind[]).map(k => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
                    </select>
                </div>
            </div>

            {loading ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
            ) : error ? (
                <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>{error}</div>
            ) : items.length === 0 ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    🧾 Счетов пока нет — загрузите первый счёт от фулфилмента.
                </div>
            ) : (
                <TanStackDataTable
                    columns={cols}
                    data={items}
                    exportName="Счета_ФФ"
                    enableSorting
                    enablePagination={items.length > 50}
                    onRowClick={(row: FfInvoiceRow) => setDetailId(row.id)}
                />
            )}

            {editor && (
                <InvoiceFormModal
                    mode={editor.mode}
                    invoice={editor.invoice}
                    parsedWarehouseId={editor.parsedWarehouseId}
                    warnings={editor.warnings}
                    warehouses={ffWarehouses}
                    onClose={() => setEditor(null)}
                    onSaved={() => { setEditor(null); load(); setToast('Счёт сохранён'); }}
                    onDraftDiscarded={() => { setEditor(null); load(); }}
                />
            )}

            {detailId != null && (
                <InvoiceDetailModal
                    invoiceId={detailId}
                    slug={slug}
                    onClose={() => setDetailId(null)}
                    onChanged={() => load()}
                    onEdit={(inv) => { setDetailId(null); setEditor({ mode: 'edit', invoice: inv, parsedWarehouseId: null, warnings: [] }); }}
                    onToast={setToast}
                />
            )}

            {toast && <Toast message={toast} onClose={() => setToast('')} />}
        </div>
    );
}

// ─── Форма счёта (подтверждение после upload / правка / ручное создание) ───

function InvoiceFormModal({
    mode, invoice, parsedWarehouseId, warnings, warehouses, onClose, onSaved, onDraftDiscarded,
}: {
    mode: 'confirm' | 'edit' | 'create';
    invoice: FfInvoiceDetail | null;
    parsedWarehouseId: number | null;
    warnings: string[];
    warehouses: Warehouse[];
    onClose: () => void;
    onSaved: () => void;
    onDraftDiscarded: () => void;
}) {
    const [warehouseId, setWarehouseId] = useState<number | ''>(
        parsedWarehouseId ?? invoice?.warehouse_id ?? '',
    );
    const [number, setNumber] = useState(invoice?.number ?? '');
    const [invoiceDate, setInvoiceDate] = useState(invoice?.invoice_date ?? '');
    const [periodStart, setPeriodStart] = useState(invoice?.period_start ?? '');
    const [periodEnd, setPeriodEnd] = useState(invoice?.period_end ?? '');
    const [kind, setKind] = useState<FfInvoiceKind>(invoice?.kind ?? 'MIXED');
    const [amount, setAmount] = useState(invoice?.amount != null ? String(invoice.amount) : '');
    const [comment, setComment] = useState(invoice?.comment ?? '');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');

    const title = mode === 'confirm' ? 'Подтвердите распознанный счёт'
        : mode === 'edit' ? `Счёт ${invoice?.number || `#${invoice?.id}`}`
        : 'Новый счёт ФФ';

    const handleSave = async () => {
        const amt = Number(amount.replace(',', '.'));
        if (!amount.trim() || !Number.isFinite(amt) || amt <= 0) {
            setError('Сумма счёта должна быть больше 0');
            return;
        }
        setSaving(true);
        setError('');
        const body: FfInvoiceUpdatePayload = {
            warehouse_id: warehouseId === '' ? null : warehouseId,
            number: number.trim() || null,
            invoice_date: invoiceDate || null,
            period_start: periodStart || null,
            period_end: periodEnd || null,
            kind,
            amount: amt,
            comment: comment.trim() || null,
        };
        try {
            if (mode === 'create') {
                await api.createFfInvoice({ ...body, kind, amount: amt });
            } else {
                await api.updateFfInvoice(invoice!.id, body);
            }
            onSaved();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка сохранения');
        } finally {
            setSaving(false);
        }
    };

    // Отмена подтверждения: черновик уже создан upload'ом — не оставляем мусор.
    const handleCancel = async () => {
        if (mode === 'confirm' && invoice) {
            if (!confirm('Отменить загрузку? Распознанный черновик счёта будет удалён.')) return;
            try { await api.deleteFfInvoice(invoice.id); } catch { /* черновик останется в списке NEW */ }
            onDraftDiscarded();
            return;
        }
        onClose();
    };

    return (
        <div className="modal-overlay" onClick={handleCancel}>
            <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 520 }}>
                <h2 className="modal-title">{title}</h2>

                {mode === 'confirm' && invoice?.original_filename && (
                    <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                        Файл: {invoice.original_filename}
                    </p>
                )}

                {warnings.length > 0 && (
                    <div style={{ padding: '10px 12px', marginBottom: 12, background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.25)', borderRadius: 8, fontSize: 13 }}>
                        {warnings.map((w, i) => (
                            <div key={i} style={{ color: 'var(--color-warning)' }}>⚠️ {w}</div>
                        ))}
                    </div>
                )}

                <div className="form-group">
                    <label className="form-label">Склад (фулфилмент) {warehouseId === '' && <span style={{ color: 'var(--color-warning)' }}>— не определён по ИНН, выберите вручную</span>}</label>
                    <select className="form-input" value={warehouseId} onChange={e => setWarehouseId(e.target.value ? Number(e.target.value) : '')}>
                        <option value="">— Не выбран —</option>
                        {warehouses.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
                    </select>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                    <div className="form-group">
                        <label className="form-label">№ счёта</label>
                        <input className="form-input" value={number} onChange={e => setNumber(e.target.value)} placeholder="СЧ-123" />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Дата счёта</label>
                        <input className="form-input" type="date" value={invoiceDate} onChange={e => setInvoiceDate(e.target.value)} />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Период с</label>
                        <input className="form-input" type="date" value={periodStart} onChange={e => setPeriodStart(e.target.value)} />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Период по</label>
                        <input className="form-input" type="date" value={periodEnd} onChange={e => setPeriodEnd(e.target.value)} />
                    </div>
                    <div className="form-group">
                        <label className="form-label">Тип</label>
                        <select className="form-input" value={kind} onChange={e => setKind(e.target.value as FfInvoiceKind)}>
                            {(Object.keys(KIND_LABEL) as FfInvoiceKind[]).map(k => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
                        </select>
                    </div>
                    <div className="form-group">
                        <label className="form-label">Сумма, ₽ *</label>
                        <input className="form-input" type="number" min={0} step="0.01" value={amount} onChange={e => setAmount(e.target.value)} />
                    </div>
                </div>

                <div className="form-group">
                    <label className="form-label">Комментарий</label>
                    <input className="form-input" value={comment} onChange={e => setComment(e.target.value)} />
                </div>

                {error && <div style={{ marginBottom: 12, fontSize: 13, color: 'var(--color-danger)' }}>{error}</div>}

                <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                    <button className="btn btn-secondary" onClick={handleCancel} disabled={saving}>
                        {mode === 'confirm' ? 'Отменить загрузку' : 'Отмена'}
                    </button>
                    <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
                        {saving ? 'Сохранение...' : mode === 'confirm' ? 'Подтвердить' : 'Сохранить'}
                    </button>
                </div>
            </div>
        </div>
    );
}

// ─── Деталка счёта ──────────────────────────────────────────────────────────

function InvoiceDetailModal({
    invoiceId, slug, onClose, onChanged, onEdit, onToast,
}: {
    invoiceId: number;
    slug: string;
    onClose: () => void;
    onChanged: () => void;
    onEdit: (inv: FfInvoiceDetail) => void;
    onToast: (msg: string) => void;
}) {
    const [detail, setDetail] = useState<FfInvoiceDetail | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState<string | null>(null); // 'reconcile' | 'payreq' | 'unlink' | 'delete' | 'download'
    const [showLinkModal, setShowLinkModal] = useState(false);

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError('');
        try {
            const d = await api.getFfInvoice(invoiceId);
            if (signal?.aborted) return;
            setDetail(d);
        } catch (e: unknown) {
            if (signal?.aborted) return;
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [invoiceId]);

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load]);

    const run = async (key: string, fn: () => Promise<void>) => {
        setBusy(key);
        setError('');
        try {
            await fn();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка');
        } finally {
            setBusy(null);
        }
    };

    const handleReconcile = () => run('reconcile', async () => {
        const d = await api.reconcileFfInvoice(invoiceId);
        setDetail(d);
        onChanged();
        onToast(d.status === 'DISPUTED' ? 'Сверено: расхождение вне допуска' : 'Сверено');
    });

    const handleCreatePaymentRequest = () => run('payreq', async () => {
        const res = await api.createFfInvoicePaymentRequest(invoiceId);
        await load();
        onChanged();
        onToast(`Создана заявка на оплату #${res.payment_request_id}`);
    });

    const handleUnlink = () => run('unlink', async () => {
        if (!confirm('Отвязать платёж от счёта?')) return;
        await api.unlinkFfInvoicePayments({ invoice_ids: [invoiceId] });
        await load();
        onChanged();
        onToast('Платёж отвязан');
    });

    const handleDelete = () => run('delete', async () => {
        if (!confirm('Удалить счёт?')) return;
        await api.deleteFfInvoice(invoiceId);
        onChanged();
        onClose();
    });

    const handleDownload = () => run('download', async () => {
        const blob = await api.downloadFfInvoiceDocument(invoiceId);
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = detail?.original_filename || `ff-invoice-${invoiceId}`;
        link.click();
        URL.revokeObjectURL(url);
    });

    const lineCols: Column[] = [
        { key: 'kind', label: 'Тип', render: (v: string) => LINE_KIND_LABEL[v] ?? v },
        {
            key: 'ref_number', label: 'Объект', sortable: false,
            render: (v: string | null, row) => {
                const line = row as FfInvoiceDetail['lines'][number];
                if (line.kind === 'STORAGE') return <span style={{ fontSize: 13 }}>{periodLabel(line.date_from, line.date_to)}</span>;
                return v || line.label || '—';
            },
        },
        { key: 'qty_boxes', label: 'Короба', align: 'right', render: (v: number | null) => v != null ? formatNumber(v, 0) : '—' },
        { key: 'qty_pallets', label: 'Паллеты', align: 'right', render: (v: number | null) => v != null ? formatNumber(v, 0) : '—' },
        { key: 'our_cost', label: 'Наш расчёт, ₽', align: 'right', render: (v: number | null) => v != null ? formatNumber(v) : '—' },
        { key: 'allocated_amount', label: 'Доля счёта, ₽', align: 'right', render: (v: number | null) => v != null ? formatNumber(v) : '—' },
    ];

    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 860, width: '95%', maxHeight: '90vh', overflowY: 'auto' }}>
                {loading ? (
                    <div style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
                ) : !detail ? (
                    <div style={{ padding: 32, color: 'var(--color-danger)' }}>{error || 'Счёт не найден'}</div>
                ) : (
                    <>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
                            <h2 className="modal-title" style={{ margin: 0 }}>
                                Счёт {detail.number || `#${detail.id}`}
                            </h2>
                            <span className={`badge ${STATUS_MAP[detail.status]?.className ?? ''}`}>
                                {STATUS_MAP[detail.status]?.label ?? detail.status}
                            </span>
                            <span className="badge badge-secondary">{KIND_LABEL[detail.kind] ?? detail.kind}</span>
                        </div>

                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 12, marginBottom: 16, fontSize: 14 }}>
                            <div>
                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>Склад</div>
                                <div style={{ fontWeight: 500 }}>{detail.warehouse_name || '—'}</div>
                            </div>
                            <div>
                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>Контрагент</div>
                                <div style={{ fontWeight: 500 }}>{detail.counterparty_name || '—'}{detail.payee_inn ? ` · ${detail.payee_inn}` : ''}</div>
                            </div>
                            <div>
                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>Дата / период</div>
                                <div style={{ fontWeight: 500 }}>{formatDate(detail.invoice_date)} · {periodLabel(detail.period_start, detail.period_end)}</div>
                            </div>
                            <div>
                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>Сумма счёта</div>
                                <div style={{ fontWeight: 600 }}>{formatNumber(detail.amount)} ₽</div>
                            </div>
                            <div>
                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>Наш расчёт</div>
                                <div style={{ fontWeight: 600 }}>{detail.our_amount != null ? formatNumber(detail.our_amount) + ' ₽' : '—'}</div>
                            </div>
                            <div>
                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>Δ (счёт − расчёт)</div>
                                <div>{diffCell(detail)}</div>
                            </div>
                            {(detail.matched_transaction_id || detail.payment_request_id) && (
                                <div>
                                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>Оплата</div>
                                    <div style={{ fontWeight: 500 }}>
                                        {detail.matched_transaction_id ? (
                                            `🏦 Дебет выписки #${detail.matched_transaction_id}`
                                        ) : (
                                            <Link href={`/p/${slug}/payments`} style={{ color: 'var(--color-accent)' }}>
                                                📄 Заявка #{detail.payment_request_id} →
                                            </Link>
                                        )}
                                        {detail.matched_at ? ` · ${formatDate(detail.matched_at)}` : ''}
                                    </div>
                                </div>
                            )}
                            {detail.comment && (
                                <div style={{ gridColumn: '1 / -1' }}>
                                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>Комментарий</div>
                                    <div>{detail.comment}</div>
                                </div>
                            )}
                        </div>

                        {/* Строки разнесения */}
                        {detail.lines.length === 0 ? (
                            <div style={{ padding: 16, marginBottom: 16, textAlign: 'center', color: 'var(--color-text-muted)', border: '1px dashed var(--color-border)', borderRadius: 12 }}>
                                Строк разнесения нет — нажмите «Сверить», чтобы собрать расчёт по заявкам/дням хранения.
                            </div>
                        ) : (
                            <div style={{ marginBottom: 16 }}>
                                <TanStackDataTable
                                    columns={lineCols}
                                    data={detail.lines}
                                    title="Разнесение"
                                    exportName={`Счёт_ФФ_${detail.number || detail.id}`}
                                    enableSorting={false}
                                    enablePagination={false}
                                    emptyText="Нет строк"
                                />
                            </div>
                        )}

                        {error && <div style={{ marginBottom: 12, fontSize: 13, color: 'var(--color-danger)' }}>{error}</div>}

                        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                            <button className="btn btn-danger btn-sm" onClick={handleDelete} disabled={busy != null} style={{ marginRight: 'auto' }}>
                                Удалить
                            </button>
                            {detail.has_document && (
                                <button className="btn btn-secondary btn-sm" onClick={handleDownload} disabled={busy != null}>
                                    {busy === 'download' ? 'Скачивание...' : '📎 Файл'}
                                </button>
                            )}
                            <button className="btn btn-secondary btn-sm" onClick={() => onEdit(detail)} disabled={busy != null}>
                                Редактировать
                            </button>
                            <button className="btn btn-primary btn-sm" onClick={handleReconcile} disabled={busy != null}>
                                {busy === 'reconcile' ? 'Сверка...' : 'Сверить'}
                            </button>
                            {!detail.payment_request_id && !detail.matched_transaction_id && (
                                <button className="btn btn-secondary btn-sm" onClick={handleCreatePaymentRequest} disabled={busy != null}>
                                    {busy === 'payreq' ? 'Создание...' : 'Создать заявку на оплату'}
                                </button>
                            )}
                            {!detail.matched_transaction_id ? (
                                <button className="btn btn-secondary btn-sm" onClick={() => setShowLinkModal(true)} disabled={busy != null}>
                                    Привязать платёж
                                </button>
                            ) : (
                                <button className="btn btn-secondary btn-sm" onClick={handleUnlink} disabled={busy != null}>
                                    {busy === 'unlink' ? '...' : 'Отвязать платёж'}
                                </button>
                            )}
                        </div>

                        {showLinkModal && (
                            <LinkPaymentModal
                                invoice={detail}
                                onClose={() => setShowLinkModal(false)}
                                onLinked={() => {
                                    setShowLinkModal(false);
                                    load();
                                    onChanged();
                                    onToast('Платёж привязан');
                                }}
                            />
                        )}
                    </>
                )}
            </div>
        </div>
    );
}

// ─── Привязка платежа: дебет выписки → N счетов ─────────────────────────────

function LinkPaymentModal({
    invoice, onClose, onLinked,
}: {
    invoice: FfInvoiceDetail;
    onClose: () => void;
    onLinked: () => void;
}) {
    const [candidates, setCandidates] = useState<FfCandidatePayment[]>([]);
    const [linkable, setLinkable] = useState<FfInvoiceRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [selectedTx, setSelectedTx] = useState<FfCandidatePayment | null>(null);
    const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set([invoice.id]));
    const [linking, setLinking] = useState(false);

    useEffect(() => {
        const controller = new AbortController();
        (async () => {
            setLoading(true);
            setError('');
            try {
                const [cands, list] = await Promise.all([
                    api.listFfCandidatePayments(invoice.id),
                    api.listFfInvoices({ warehouse_id: invoice.warehouse_id ?? undefined }),
                ]);
                if (controller.signal.aborted) return;
                setCandidates(cands);
                setLinkable(list.items);
            } catch (e: unknown) {
                if (controller.signal.aborted) return;
                setError(e instanceof Error ? e.message : 'Ошибка загрузки кандидатов');
            } finally {
                if (!controller.signal.aborted) setLoading(false);
            }
        })();
        return () => controller.abort();
    }, [invoice.id, invoice.warehouse_id]);

    // Счета, доступные для включения в этот платёж: без привязки к другой выписке
    // (уже привязанные к ВЫБРАННОМУ дебету — включаются: платёж закрывает их вместе).
    const selectableInvoices = useMemo(() => {
        const txId = selectedTx?.transaction_id;
        return linkable.filter(inv =>
            inv.matched_transaction_id == null || (txId != null && inv.matched_transaction_id === txId),
        );
    }, [linkable, selectedTx]);

    const sumSelected = useMemo(() => {
        let sum = 0;
        for (const inv of selectableInvoices) {
            if (selectedIds.has(inv.id)) sum += inv.amount;
        }
        // Текущий счёт может не попасть в отфильтрованный список (фильтр склада NULL и т.п.)
        if (selectedIds.has(invoice.id) && !selectableInvoices.some(i => i.id === invoice.id)) {
            sum += invoice.amount;
        }
        return sum;
    }, [selectableInvoices, selectedIds, invoice]);

    const diff = selectedTx ? sumSelected - selectedTx.amount : null;
    const canLink = selectedTx != null && selectedIds.size > 0 && diff != null && Math.abs(diff) <= 0.01;

    const handleSelectTx = (tx: FfCandidatePayment) => {
        setSelectedTx(tx);
        // Преселект: текущий счёт + счета, уже привязанные к этому дебету (Σ должна биться целиком).
        setSelectedIds(new Set([invoice.id, ...tx.already_linked_invoice_ids]));
    };

    const toggleInvoice = (id: number) => {
        setSelectedIds(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    };

    const handleLink = async () => {
        if (!selectedTx || !canLink) return;
        setLinking(true);
        setError('');
        try {
            await api.linkFfInvoicePayments({
                transaction_id: selectedTx.transaction_id,
                invoice_ids: [...selectedIds],
            });
            onLinked();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка привязки');
        } finally {
            setLinking(false);
        }
    };

    return (
        <div className="modal-overlay" onClick={onClose} style={{ zIndex: 60 }}>
            <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 720, width: '95%', maxHeight: '85vh', overflowY: 'auto' }}>
                <h2 className="modal-title">Привязать платёж</h2>
                <p style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 16 }}>
                    Дебеты выписки по ИНН юрлиц склада. Один платёж может закрывать несколько счетов —
                    сумма выбранных счетов должна совпасть с суммой дебета.
                </p>

                {loading ? (
                    <div style={{ padding: 24, textAlign: 'center' }}>Загрузка...</div>
                ) : candidates.length === 0 ? (
                    <div style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                        Подходящих дебетов в выписке не найдено.
                        {error && <div style={{ color: 'var(--color-danger)', marginTop: 8 }}>{error}</div>}
                    </div>
                ) : (
                    <>
                        {/* Шаг 1: дебет */}
                        <div style={{ marginBottom: 16 }}>
                            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>1. Выберите дебет</div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 220, overflowY: 'auto' }}>
                                {candidates.map(tx => (
                                    <label
                                        key={tx.transaction_id}
                                        style={{
                                            display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px',
                                            border: `1px solid ${selectedTx?.transaction_id === tx.transaction_id ? 'var(--color-accent)' : 'var(--color-border)'}`,
                                            borderRadius: 8, cursor: 'pointer', fontSize: 13,
                                        }}
                                    >
                                        <input
                                            type="radio"
                                            name="ff-candidate-tx"
                                            checked={selectedTx?.transaction_id === tx.transaction_id}
                                            onChange={() => handleSelectTx(tx)}
                                        />
                                        <span style={{ whiteSpace: 'nowrap' }}>{formatDate(tx.date)}</span>
                                        <strong style={{ whiteSpace: 'nowrap' }}>{formatNumber(tx.amount)} ₽</strong>
                                        <span style={{ color: 'var(--color-text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                            {tx.counterparty || ''}{tx.purpose ? ` — ${tx.purpose}` : ''}
                                        </span>
                                        {tx.already_linked_invoice_ids.length > 0 && (
                                            <span className="badge badge-info" style={{ fontSize: 11, marginLeft: 'auto' }}>
                                                уже {formatNumber(tx.already_linked_invoice_ids.length, 0)} {pluralRu(tx.already_linked_invoice_ids.length, ['счёт', 'счёта', 'счетов'])}
                                            </span>
                                        )}
                                    </label>
                                ))}
                            </div>
                        </div>

                        {/* Шаг 2: счета */}
                        {selectedTx && (
                            <div style={{ marginBottom: 16 }}>
                                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>2. Счета, которые закрывает платёж</div>
                                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 200, overflowY: 'auto' }}>
                                    {selectableInvoices.map(inv => (
                                        <label
                                            key={inv.id}
                                            style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 12px', border: '1px solid var(--color-border)', borderRadius: 8, cursor: 'pointer', fontSize: 13 }}
                                        >
                                            <input
                                                type="checkbox"
                                                checked={selectedIds.has(inv.id)}
                                                onChange={() => toggleInvoice(inv.id)}
                                            />
                                            <span>{inv.number || `#${inv.id}`}</span>
                                            <span style={{ color: 'var(--color-text-muted)' }}>{periodLabel(inv.period_start, inv.period_end)}</span>
                                            <strong style={{ marginLeft: 'auto', whiteSpace: 'nowrap' }}>{formatNumber(inv.amount)} ₽</strong>
                                            {inv.id === invoice.id && <span className="badge badge-secondary" style={{ fontSize: 10 }}>текущий</span>}
                                        </label>
                                    ))}
                                </div>

                                {/* Контроль суммы */}
                                <div style={{ marginTop: 10, fontSize: 13, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                                    <span>Выбрано: <strong>{formatNumber(sumSelected)} ₽</strong></span>
                                    <span>Дебет: <strong>{formatNumber(selectedTx.amount)} ₽</strong></span>
                                    {canLink ? (
                                        <span style={{ color: 'var(--color-success)', fontWeight: 600 }}>✓ суммы совпадают</span>
                                    ) : (
                                        <span style={{ color: 'var(--color-warning)' }}>
                                            {diff != null && diff < 0
                                                ? `не хватает ${formatNumber(Math.abs(diff))} ₽ — добавьте счета`
                                                : `перебор ${formatNumber(diff ?? 0)} ₽ — снимите лишние счета`}
                                        </span>
                                    )}
                                </div>
                            </div>
                        )}

                        {error && <div style={{ marginBottom: 12, fontSize: 13, color: 'var(--color-danger)' }}>{error}</div>}

                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                            <button className="btn btn-secondary" onClick={onClose} disabled={linking}>Отмена</button>
                            <button
                                className="btn btn-primary"
                                onClick={handleLink}
                                disabled={!canLink || linking}
                                title={canLink ? undefined : 'Сумма выбранных счетов должна совпасть с суммой дебета (допуск 1 коп.)'}
                            >
                                {linking ? 'Привязка...' : 'Привязать'}
                            </button>
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}
