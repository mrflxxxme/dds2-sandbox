'use client';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { formatDate, formatDateTime, formatNumber, exportToExcel } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import CreatePaymentRequestModal from '../warehouse/logistics/components/CreatePaymentRequestModal';
import PaymentCategoriesModal from './PaymentCategoriesModal';
import { usePermissions } from '@/lib/hooks/usePermissions';
import type {
    PaymentRequestRow,
    PaymentRequestDetail,
    PaymentRequestStatus,
    PaymentRequestCategory,
    PaymentRequestDocument,
    PaymentRequestDocType,
    PaymentRequestEvent,
    PaymentCategory,
    CreateDraftsResponse,
} from '@/types/api';

// ─── Status map ────────────────────────────────────────────────────────────────

export const PAYMENT_STATUS_MAP: Record<PaymentRequestStatus, { label: string; className: string }> = {
    DRAFT:          { label: 'Черновик',           className: 'badge-secondary' },
    PENDING_REVIEW: { label: 'На проверке',        className: 'badge-warning' },
    APPROVED:       { label: 'Согласовано',        className: 'badge-info' },
    DRAFT_CREATED:  { label: 'Платёжка создана',   className: 'badge-info' },
    PAID:           { label: 'Оплачено',           className: 'badge-success' },
    REJECTED:       { label: 'Отклонено',          className: 'badge-danger' },
    CANCELLED:      { label: 'Отменено',           className: 'badge-secondary' },
};

// Фолбэк-лейблы 7 системных кодов — на случай, если справочник не загрузился.
// Актуальные лейблы (вкл. кастомные категории) тянутся с API в компоненте.
const CATEGORY_LABEL: Record<string, string> = {
    LOGISTICS: 'Логистика',
    PHOTO_CONTENT: 'Фотоконтент',
    CUSTOMS: 'Таможенное оформление',
    FULFILLMENT: 'Фулфилмент',
    DESIGN: 'Дизайн',
    HOUSEHOLD: 'Хозрасходы',
    OTHER: 'Другое',
};

const STATUS_TABS: { key: PaymentRequestStatus | 'all'; label: string }[] = [
    { key: 'all',            label: 'Все' },
    { key: 'PENDING_REVIEW', label: 'На проверке' },
    { key: 'APPROVED',       label: 'Согласовано' },
    { key: 'DRAFT_CREATED',  label: 'Платёжка создана' },
    { key: 'PAID',           label: 'Оплачено' },
    { key: 'DRAFT',          label: 'Черновик' },
    { key: 'REJECTED',       label: 'Отклонено' },
    { key: 'CANCELLED',      label: 'Отменено' },
];

const DOC_TYPE_LABEL: Record<string, string> = {
    INVOICE: 'Счёт',
    ACT: 'Акт',
};

interface Props {
    /** When embedded as a tab, skip the standalone page header. */
    embedded?: boolean;
}

// ─── Component ─────────────────────────────────────────────────────────────────

export default function PaymentsPanel({ embedded = false }: Props) {
    // Реальная запись платёжки в банк — только owner/admin (бэкенд тоже гейтит require_project_admin).
    const { canManage } = usePermissions();
    const canBankWrite = canManage();

    const [items, setItems] = useState<PaymentRequestRow[]>([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Справочник «Назначение оплаты» — динамические лейблы для таблицы/деталки + управление.
    const [categories, setCategories] = useState<PaymentCategory[]>([]);
    const [showCategories, setShowCategories] = useState(false);
    const loadCategories = useCallback(async () => {
        try { setCategories(await api.listPaymentCategories()); } catch { /* останутся фолбэк-лейблы */ }
    }, []);

    // Filters
    const [statusTab, setStatusTab] = useState<PaymentRequestStatus | 'all'>('all');
    const [search, setSearch] = useState('');
    const [dateFrom, setDateFrom] = useState('');
    const [dateTo, setDateTo] = useState('');

    // Detail drawer
    const [selectedId, setSelectedId] = useState<number | null>(null);
    const [detail, setDetail] = useState<PaymentRequestDetail | null>(null);
    const [detailLoading, setDetailLoading] = useState(false);
    const [detailError, setDetailError] = useState('');

    // Create draft action
    const [creatingDraft, setCreatingDraft] = useState(false);
    const [draftErrors, setDraftErrors] = useState<string[]>([]);

    // Create-request modal
    const [showCreate, setShowCreate] = useState(false);
    // Edit existing request (PENDING_REVIEW/DRAFT) — правка суммы/назначения/реквизитов
    const [editId, setEditId] = useState<number | null>(null);
    // Cancel request
    const [cancelling, setCancelling] = useState(false);
    // Согласование / отклонение / отметка «оплачено» (ручной флоу)
    const [acting, setActing] = useState(false);
    // Догрузка документов (счёт/акт) к существующей заявке прямо из деталки.
    const [docUploading, setDocUploading] = useState<PaymentRequestDocType | null>(null);
    const invoiceInputRef = useRef<HTMLInputElement>(null);
    const actInputRef = useRef<HTMLInputElement>(null);

    // Bulk «создать оплаты в банке» по нескольким PENDING_REVIEW заявкам (необратимо)
    const [selected, setSelected] = useState<Set<number>>(new Set());
    const [bulkRunning, setBulkRunning] = useState(false);
    const [bulkResult, setBulkResult] = useState<CreateDraftsResponse | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const resp = await api.listPaymentRequests({
                status: statusTab === 'all' ? undefined : statusTab,
                search: search || undefined,
                date_from: dateFrom || undefined,
                date_to: dateTo || undefined,
            });
            setItems(resp.items);
            setTotal(resp.total);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setLoading(false);
    }, [statusTab, search, dateFrom, dateTo]);

    useEffect(() => { load(); }, [load]);
    useEffect(() => { loadCategories(); }, [loadCategories]);

    // code → отображаемый лейбл: справочник → фолбэк системных → сам код.
    const labelOf = useMemo(() => {
        const m: Record<string, string> = {};
        for (const c of categories) m[c.code] = c.label;
        return (code: PaymentRequestCategory | null | undefined): string =>
            code ? (m[code] ?? CATEGORY_LABEL[code] ?? code) : '—';
    }, [categories]);

    // Смена вкладки статуса сбрасывает выбор и баннер — чтобы галочки/счётчик «N»
    // и результат всегда соответствовали видимой выборке (не тащить стейл-selection).
    useEffect(() => { setSelected(new Set()); setBulkResult(null); }, [statusTab]);

    // Load detail when row clicked
    const loadDetail = useCallback(async (id: number) => {
        setSelectedId(id);
        setDetailLoading(true);
        setDetailError('');
        setDraftErrors([]);
        try {
            const d = await api.getPaymentRequest(id);
            setDetail(d);
        } catch (e: unknown) {
            setDetailError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
        setDetailLoading(false);
    }, []);

    // Debounced search
    useEffect(() => {
        const t = setTimeout(() => load(), 300);
        return () => clearTimeout(t);
    }, [search]); // eslint-disable-line react-hooks/exhaustive-deps

    const handleCreateDraft = async () => {
        if (!detail) return;
        const payeeLabel = detail.payee_name || detail.payee_inn || 'получателю';
        const amountLabel = `${formatNumber(Number(detail.amount), 2)} ₽`;
        if (!confirm(`Создать платёжку в банке: ${amountLabel} → ${payeeLabel}?\n\nЭто действие НЕОБРАТИМО (черновик попадёт в банк).`)) return;
        setCreatingDraft(true);
        setDraftErrors([]);
        setDetailError('');
        try {
            const updated = await api.createPaymentDraft(detail.id, true);
            setDetail(updated);
            await load();
        } catch (e: unknown) {
            const msg = e instanceof Error ? e.message : '';
            try {
                const parsed = JSON.parse(msg);
                if (parsed?.bank_errors && Array.isArray(parsed.bank_errors)) {
                    setDraftErrors(parsed.bank_errors);
                    setCreatingDraft(false);
                    return;
                }
                if (Array.isArray(parsed)) { setDraftErrors(parsed); setCreatingDraft(false); return; }
            } catch { /* not JSON */ }
            setDetailError(msg || 'Ошибка создания платёжки');
        }
        setCreatingDraft(false);
    };

    const handleCancel = async () => {
        if (!detail) return;
        if (!confirm(
            `Отменить заявку ${detail.number}?\n\n` +
            `Статус станет «Отменено», её заборы снова станут свободными для новой заявки.`,
        )) return;
        setCancelling(true);
        setDetailError('');
        try {
            const updated = await api.cancelPaymentRequest(detail.id);
            setDetail(updated);
            await load();
        } catch (e: unknown) {
            setDetailError(e instanceof Error ? e.message : 'Ошибка отмены');
        }
        setCancelling(false);
    };

    // ─── Ручное согласование (без банка): Согласовать / Отклонить / Оплачено ──────
    const runAction = async (fn: () => Promise<PaymentRequestDetail>) => {
        setActing(true);
        setDetailError('');
        try {
            const updated = await fn();
            setDetail(updated);
            await load();
        } catch (e: unknown) {
            setDetailError(e instanceof Error ? e.message : 'Ошибка действия');
        }
        setActing(false);
    };

    const handleApprove = () => {
        if (!detail) return;
        runAction(() => api.approvePaymentRequest(detail.id));
    };

    const handleReject = () => {
        if (!detail) return;
        const reason = prompt('Причина отклонения (необязательно):');
        if (reason === null) return;  // отмена диалога
        runAction(() => api.rejectPaymentRequest(detail.id, reason || undefined));
    };

    const handleMarkPaid = () => {
        if (!detail) return;
        if (!confirm(`Отметить заявку ${detail.number} оплаченной?`)) return;
        runAction(() => api.markPaymentRequestPaid(detail.id));
    };

    // ─── Догрузка/удаление документов (счёт/акт) к существующей заявке ────────────
    // Акт обычно приходит ПОЗЖE счёта/оплаты — поэтому доступно на любой активной заявке.
    const handleAttachDoc = async (file: File, docType: PaymentRequestDocType) => {
        if (!detail) return;
        setDocUploading(docType);
        setDetailError('');
        try {
            await api.uploadPaymentRequestDocument(detail.id, file, docType);
            setDetail(await api.getPaymentRequest(detail.id));
            await load();
        } catch (e: unknown) {
            setDetailError(e instanceof Error ? e.message : 'Ошибка загрузки документа');
        }
        setDocUploading(null);
    };

    const handleDeleteDoc = async (docId: number) => {
        if (!detail) return;
        if (!confirm('Удалить документ?')) return;
        setDetailError('');
        try {
            await api.deletePaymentRequestDocument(detail.id, docId);
            setDetail(await api.getPaymentRequest(detail.id));
            await load();
        } catch (e: unknown) {
            setDetailError(e instanceof Error ? e.message : 'Ошибка удаления документа');
        }
    };

    // Export
    const handleExport = () => {
        const rows = items.map(r => ({
            '№': r.number,
            'Статус': PAYMENT_STATUS_MAP[r.status]?.label ?? r.status,
            'Назначение': r.category ? labelOf(r.category) : '',
            'Проект': r.project_name ?? '',
            'Получатель': r.payee_name ?? '',
            'ИНН': r.payee_inn ?? '',
            'Сумма': Number(r.amount),
            'Валюта': r.currency,
            'Дата забора': r.pickup_date ?? '',
            'Документов': r.doc_count,
            'Создана': r.created_at,
        }));
        exportToExcel(rows, 'payment_requests');
    };

    const pendingCount = useMemo(() => items.filter(r => r.status === 'PENDING_REVIEW').length, [items]);
    // «Дата забора» — логистическая колонка. Показываем, только если хоть у одной видимой
    // строки есть забор — на чисто фото/дизайн/таможня выборке колонка-«—» исчезает.
    const hasPickup = useMemo(() => items.some(r => r.pickup_date), [items]);

    // ─── Bulk selection (только PENDING_REVIEW можно передать в банк) ─────────────
    const pendingIds = useMemo(
        () => items.filter(r => r.status === 'PENDING_REVIEW').map(r => r.id),
        [items],
    );
    const selectedPendingIds = useMemo(
        () => pendingIds.filter(id => selected.has(id)),
        [pendingIds, selected],
    );
    const allPendingSelected = pendingIds.length > 0 && pendingIds.every(id => selected.has(id));
    const numberById = useMemo(() => new Map(items.map(r => [r.id, r.number])), [items]);

    const toggleRow = (id: number) => setSelected(prev => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
    });
    const toggleAllPending = () => setSelected(prev => {
        const next = new Set(prev);
        if (allPendingSelected) pendingIds.forEach(id => next.delete(id));
        else pendingIds.forEach(id => next.add(id));
        return next;
    });

    const handleBulkDrafts = async () => {
        const ids = selectedPendingIds;
        if (ids.length === 0) return;
        if (!confirm(
            `Создать платёжки в банке — выбрано заявок: ${ids.length}.\n\n` +
            `Это действие НЕОБРАТИМО: по каждой заявке формируется черновик платёжного поручения в банке.`,
        )) return;
        setBulkRunning(true);
        setBulkResult(null);
        setError('');
        try {
            const res = await api.createPaymentDrafts(ids, true);
            setBulkResult(res);
            setSelected(new Set());
            await load();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка массового создания платёжек');
        }
        setBulkRunning(false);
    };

    const actions = (
        <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(true)}>
                + Заявка на оплату
            </button>
            <button className="btn btn-secondary btn-sm" onClick={() => setShowCategories(true)} title="Управление категориями «Назначение»">
                ⚙ Категории
            </button>
            <button className="btn btn-secondary btn-sm" onClick={handleExport} disabled={items.length === 0}>
                📥 Excel
            </button>
        </div>
    );

    const createModal = showCreate && (
        <CreatePaymentRequestModal
            onClose={() => { setShowCreate(false); load(); }}
            onSuccess={() => { load(); }}
        />
    );

    const editModal = editId != null && (
        <CreatePaymentRequestModal
            key={`edit-${editId}`}
            editRequestId={editId}
            onClose={() => {
                const id = editId;
                setEditId(null);
                load();
                if (id != null && selectedId === id) loadDetail(id);  // освежить деталку открытой заявки
            }}
            onSuccess={() => { load(); if (selectedId != null) loadDetail(selectedId); }}
        />
    );

    if (loading) {
        return (
            <>
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка...</div>
                {createModal}
            </>
        );
    }
    if (error) {
        return (
            <>
                <div className="glass-card" style={{ padding: 32 }}>
                    <p style={{ color: 'var(--color-danger)', marginBottom: 16 }}>{error}</p>
                    <button className="btn btn-secondary" onClick={load}>Повторить</button>
                </div>
                {createModal}
            </>
        );
    }

    // Догрузка/удаление документов доступна, пока заявка активна (не отклонена/отменена).
    const canAttachDocs = detail != null && detail.status !== 'CANCELLED' && detail.status !== 'REJECTED';
    const DOC_ACCEPT = '.pdf,.doc,.docx,.jpg,.jpeg,.png,.webp,.heic';

    return (
        <div className={embedded ? undefined : 'animate-in'}>
            {embedded ? (
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)' }}>
                        Заявки на оплату · всего {total}
                        {pendingCount > 0 && statusTab === 'all' && (
                            <span style={{ color: 'var(--color-warning)', marginLeft: 8 }}>· {pendingCount} на проверке</span>
                        )}
                    </div>
                    {actions}
                </div>
            ) : (
                <div className="page-header">
                    <div>
                        <h1 className="page-title">Оплаты</h1>
                        <p className="page-subtitle">
                            Заявки на оплату — логистика, фотоконтент, таможня и др.
                            {pendingCount > 0 && statusTab === 'all' && (
                                <span style={{ color: 'var(--color-warning)', marginLeft: 8 }}>· {pendingCount} на проверке</span>
                            )}
                        </p>
                    </div>
                    {actions}
                </div>
            )}

            {/* Filters */}
            <div className="glass-card" style={{ padding: 16, marginBottom: 16 }}>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
                    {STATUS_TABS.map(t => (
                        <button
                            key={t.key}
                            className={`btn btn-sm ${statusTab === t.key ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setStatusTab(t.key)}
                        >
                            {t.label}
                        </button>
                    ))}
                </div>
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label" style={{ fontSize: 12 }}>Поиск</label>
                        <input
                            className="form-input"
                            placeholder="Номер, получатель, ИНН..."
                            value={search}
                            onChange={e => setSearch(e.target.value)}
                            style={{ minWidth: 220 }}
                        />
                    </div>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label" style={{ fontSize: 12 }}>Дата от</label>
                        <input className="form-input" type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
                    </div>
                    <div className="form-group" style={{ margin: 0 }}>
                        <label className="form-label" style={{ fontSize: 12 }}>до</label>
                        <input className="form-input" type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} />
                    </div>
                    <button className="btn btn-secondary btn-sm" onClick={load}>Обновить</button>
                </div>
            </div>

            {/* Layout: table + drawer */}
            <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                    {/* Bulk «создать оплаты в банке» — только admin/owner */}
                    {canBankWrite && items.length > 0 && pendingIds.length > 0 && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
                            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer', color: 'var(--color-text-muted)' }}>
                                <input type="checkbox" checked={allPendingSelected} onChange={toggleAllPending} style={{ cursor: 'pointer' }} />
                                Выбрать все «на проверке» ({pendingIds.length})
                            </label>
                            {selectedPendingIds.length > 0 && (
                                <>
                                    <button className="btn btn-primary btn-sm" onClick={handleBulkDrafts} disabled={bulkRunning}>
                                        {bulkRunning ? 'Создание платёжек...' : `💳 Создать оплаты в банке (${selectedPendingIds.length})`}
                                    </button>
                                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                        Необратимо — черновики уйдут в банк
                                    </span>
                                </>
                            )}
                        </div>
                    )}
                    {bulkResult && (
                        <div className="glass-card" style={{ padding: '12px 16px', marginBottom: 12 }}>
                            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: bulkResult.failed > 0 ? 8 : 0 }}>
                                <span style={{ color: 'var(--color-success)' }}>Создано платёжек: {bulkResult.created}</span>
                                {bulkResult.failed > 0 && (
                                    <span style={{ color: 'var(--color-danger)', marginLeft: 12 }}>Ошибок: {bulkResult.failed}</span>
                                )}
                            </div>
                            {bulkResult.failed > 0 && (
                                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: 'var(--color-danger)' }}>
                                    {bulkResult.results.filter(r => !r.ok).map(r => (
                                        <li key={r.id}>{numberById.get(r.id) ?? `#${r.id}`}: {r.error ?? 'ошибка'}</li>
                                    ))}
                                </ul>
                            )}
                            <button className="btn btn-secondary btn-sm" style={{ marginTop: 8 }} onClick={() => setBulkResult(null)}>Скрыть</button>
                        </div>
                    )}
                    {items.length === 0 ? (
                        <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                            <div style={{ fontSize: 40, marginBottom: 12 }}>💳</div>
                            <p style={{ marginBottom: 4 }}>Нет заявок на оплату.</p>
                            <p style={{ fontSize: 13 }}>Создайте заявку кнопкой «+ Заявка на оплату» выше или из листа логиста на отгруженных строках.</p>
                        </div>
                    ) : (
                        <TanStackDataTable
                            columns={[
                                {
                                    key: '_sel', label: '', sortable: false, align: 'center' as const, width: '40px',
                                    exportValue: () => '',
                                    render: (_: unknown, row: PaymentRequestRow) => (
                                        canBankWrite && row.status === 'PENDING_REVIEW' ? (
                                            <input
                                                type="checkbox"
                                                checked={selected.has(row.id)}
                                                onClick={e => e.stopPropagation()}
                                                onChange={() => toggleRow(row.id)}
                                                style={{ cursor: 'pointer' }}
                                                aria-label={`Выбрать заявку ${row.number}`}
                                            />
                                        ) : null
                                    ),
                                },
                                {
                                    key: 'status', label: 'Статус', sortable: true,
                                    render: (v: PaymentRequestStatus) => {
                                        const cfg = PAYMENT_STATUS_MAP[v] ?? { label: v, className: '' };
                                        return <span className={`badge ${cfg.className}`}>{cfg.label}</span>;
                                    }
                                },
                                { key: 'number', label: '№', sortable: true, render: (v: string) => <span style={{ fontWeight: 600 }}>{v}</span> },
                                {
                                    key: 'category', label: 'Назначение', sortable: true,
                                    render: (v: PaymentRequestCategory | null) => labelOf(v),
                                },
                                {
                                    key: 'project_name', label: 'Проект', sortable: true,
                                    render: (v: string | null) => v ?? <span style={{ color: 'var(--color-text-muted)' }}>— общая</span>,
                                },
                                { key: 'payee_name', label: 'Получатель', sortable: true, render: (v: string | null) => v ?? '—' },
                                { key: 'payee_inn', label: 'ИНН', sortable: false, render: (v: string | null) => <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{v ?? '—'}</span> },
                                {
                                    key: 'amount', label: 'Сумма', sortable: true, align: 'right' as const,
                                    render: (v: string) => <span style={{ fontWeight: 600 }}>{formatNumber(Number(v), 2)} ₽</span>
                                },
                                ...(hasPickup ? [{ key: 'pickup_date', label: 'Дата забора', sortable: true, render: (v: string | null) => v ? formatDate(v) : '—' }] : []),
                                { key: 'doc_count', label: 'Доки', sortable: true, align: 'right' as const, render: (v: number) => v > 0 ? <span style={{ color: 'var(--color-success)' }}>{v}</span> : <span style={{ color: 'var(--color-text-muted)' }}>0</span> },
                                { key: 'created_at', label: 'Создана', sortable: true, render: (v: string) => formatDate(v) },
                            ]}
                            data={items}
                            title={`Всего: ${total}`}
                            exportName="payment_requests"
                            enableSorting
                            enablePagination
                            pageSize={50}
                            onRowClick={(row: PaymentRequestRow) => loadDetail(row.id)}
                        />
                    )}
                </div>

                {/* ─── Detail drawer ─── */}
                {selectedId != null && (
                    <div className="glass-card" style={{ width: 420, flexShrink: 0, padding: 20, position: 'sticky', top: 16 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                            <div style={{ fontWeight: 600, fontSize: 15 }}>Детали</div>
                            <button
                                onClick={() => { setSelectedId(null); setDetail(null); }}
                                style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 18, color: 'var(--color-text-muted)' }}
                            >
                                ✕
                            </button>
                        </div>

                        {detailLoading ? (
                            <div style={{ textAlign: 'center', padding: 32, color: 'var(--color-text-muted)' }}>Загрузка...</div>
                        ) : detailError ? (
                            <div style={{ color: 'var(--color-danger)', fontSize: 13 }}>{detailError}</div>
                        ) : detail ? (
                            <>
                                {/* Status + number */}
                                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 16 }}>
                                    <span className={`badge ${PAYMENT_STATUS_MAP[detail.status]?.className ?? ''}`}>
                                        {PAYMENT_STATUS_MAP[detail.status]?.label ?? detail.status}
                                    </span>
                                    <span style={{ fontWeight: 600 }}>{detail.number}</span>
                                </div>

                                {/* Requisites */}
                                <div style={{ fontSize: 13, marginBottom: 16 }}>
                                    <div style={{ fontWeight: 600, marginBottom: 8, color: 'var(--color-text-muted)', textTransform: 'uppercase', fontSize: 11, letterSpacing: '0.5px' }}>Реквизиты</div>
                                    {detail.category && <DetailRow label="Категория" value={labelOf(detail.category)} />}
                                    <DetailRow label="Проект" value={detail.project_name ?? '— общая (без проекта)'} />
                                    {detail.payee_name && <DetailRow label="Получатель" value={detail.payee_name} />}
                                    {detail.payee_inn && <DetailRow label="ИНН" value={detail.payee_inn} mono />}
                                    {detail.payee_kpp && <DetailRow label="КПП" value={detail.payee_kpp} mono />}
                                    {detail.payee_account && <DetailRow label="Счёт" value={detail.payee_account} mono />}
                                    {detail.payee_bik && <DetailRow label="БИК" value={detail.payee_bik} mono />}
                                    {detail.payee_bank_name && <DetailRow label="Банк" value={detail.payee_bank_name} />}
                                    {detail.payee_corr_account && <DetailRow label="Корр. счёт" value={detail.payee_corr_account} mono />}
                                    {detail.amount && <DetailRow label="Сумма" value={`${formatNumber(Number(detail.amount), 2)} ${detail.currency}`} />}
                                    {detail.pickup_date && <DetailRow label="Дата забора" value={formatDate(detail.pickup_date)} />}
                                    {detail.purpose && <DetailRow label="Назначение" value={detail.purpose} />}
                                    {detail.bank_doc_id && <DetailRow label="ID платёжки" value={detail.bank_doc_id} mono />}
                                    {detail.matched_at && <DetailRow label="Дата совпадения" value={formatDateTime(detail.matched_at)} />}
                                </div>

                                {/* Documents — счёт/акт; акт можно догрузить позже (приходит после оплаты) */}
                                {(detail.documents.length > 0 || canAttachDocs) && (
                                    <div style={{ marginBottom: 16 }}>
                                        <div style={{ fontWeight: 600, marginBottom: 8, color: 'var(--color-text-muted)', textTransform: 'uppercase', fontSize: 11, letterSpacing: '0.5px' }}>Документы</div>
                                        {detail.documents.length > 0 ? (
                                            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                                                {detail.documents.map((doc: PaymentRequestDocument) => (
                                                    <div
                                                        key={doc.id}
                                                        style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 10px', background: 'var(--color-bg)', borderRadius: 8 }}
                                                    >
                                                        <div style={{ minWidth: 0 }}>
                                                            <span className="badge badge-secondary" style={{ fontSize: 11, marginRight: 6 }}>{DOC_TYPE_LABEL[doc.doc_type] ?? doc.doc_type}</span>
                                                            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{doc.original_filename ?? 'файл'}</span>
                                                        </div>
                                                        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexShrink: 0 }}>
                                                            <a
                                                                href={api.paymentRequestDocumentDownloadUrl(detail.id, doc.id)}
                                                                target="_blank"
                                                                rel="noopener noreferrer"
                                                                style={{ fontSize: 12, color: 'var(--color-accent)', textDecoration: 'none' }}
                                                            >
                                                                Скачать
                                                            </a>
                                                            {canAttachDocs && (
                                                                <button
                                                                    title="Удалить документ"
                                                                    onClick={() => handleDeleteDoc(doc.id)}
                                                                    disabled={docUploading !== null}
                                                                    style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--color-danger)', fontSize: 13 }}
                                                                >
                                                                    ✕
                                                                </button>
                                                            )}
                                                        </div>
                                                    </div>
                                                ))}
                                            </div>
                                        ) : (
                                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>Документы не приложены</div>
                                        )}
                                        {canAttachDocs && (
                                            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                                                <button className="btn btn-secondary btn-sm" style={{ flex: 1 }} onClick={() => invoiceInputRef.current?.click()} disabled={docUploading !== null}>
                                                    {docUploading === 'INVOICE' ? 'Загрузка...' : '📄 + Счёт'}
                                                </button>
                                                <button className="btn btn-secondary btn-sm" style={{ flex: 1 }} onClick={() => actInputRef.current?.click()} disabled={docUploading !== null}>
                                                    {docUploading === 'ACT' ? 'Загрузка...' : '📋 + Акт'}
                                                </button>
                                                <input
                                                    ref={invoiceInputRef} type="file" accept={DOC_ACCEPT} style={{ display: 'none' }}
                                                    onChange={e => { const f = e.target.files?.[0]; e.target.value = ''; if (f) handleAttachDoc(f, 'INVOICE'); }}
                                                />
                                                <input
                                                    ref={actInputRef} type="file" accept={DOC_ACCEPT} style={{ display: 'none' }}
                                                    onChange={e => { const f = e.target.files?.[0]; e.target.value = ''; if (f) handleAttachDoc(f, 'ACT'); }}
                                                />
                                            </div>
                                        )}
                                    </div>
                                )}

                                {/* Ручное согласование (без банка) — PENDING_REVIEW, админ */}
                                {canBankWrite && detail.status === 'PENDING_REVIEW' && (
                                    <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
                                        <button className="btn btn-success" style={{ flex: 1 }} onClick={handleApprove} disabled={acting}>
                                            {acting ? '...' : '✓ Согласовать'}
                                        </button>
                                        <button className="btn btn-danger" style={{ flex: 1 }} onClick={handleReject} disabled={acting}>
                                            ✗ Отклонить
                                        </button>
                                    </div>
                                )}

                                {/* Отметить оплаченным / отклонить — APPROVED, админ */}
                                {canBankWrite && detail.status === 'APPROVED' && (
                                    <div style={{ marginBottom: 16 }}>
                                        <div style={{ display: 'flex', gap: 8 }}>
                                            <button className="btn btn-success" style={{ flex: 1 }} onClick={handleMarkPaid} disabled={acting}>
                                                {acting ? '...' : '₽ Отметить оплаченным'}
                                            </button>
                                            <button className="btn btn-danger" style={{ flex: 1 }} onClick={handleReject} disabled={acting}>
                                                ✗ Отклонить
                                            </button>
                                        </div>
                                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 4, textAlign: 'center' }}>
                                            Заявка согласована — оплату проводите вне системы
                                        </div>
                                    </div>
                                )}

                                {/* Создать платёжку в банке — только логистика (Faktura), PENDING_REVIEW, админ */}
                                {canBankWrite && detail.status === 'PENDING_REVIEW' && (detail.category === 'LOGISTICS' || detail.category == null) && (
                                    <div style={{ marginBottom: 16 }}>
                                        {draftErrors.length > 0 && (
                                            <div style={{ padding: '8px 12px', marginBottom: 8, borderRadius: 8, background: 'rgba(239,68,68,0.08)', fontSize: 12, color: 'var(--color-danger)' }}>
                                                <div style={{ fontWeight: 600, marginBottom: 4 }}>Ошибки банка:</div>
                                                <ul style={{ margin: 0, paddingLeft: 16 }}>
                                                    {draftErrors.map((e, i) => <li key={i}>{e}</li>)}
                                                </ul>
                                            </div>
                                        )}
                                        <button
                                            className="btn btn-primary"
                                            style={{ width: '100%' }}
                                            onClick={handleCreateDraft}
                                            disabled={creatingDraft}
                                        >
                                            {creatingDraft ? 'Создание платёжки...' : 'Создать оплату (банк)'}
                                        </button>
                                        <div style={{ fontSize: 11, color: 'var(--color-text-muted)', marginTop: 4, textAlign: 'center' }}>
                                            Необратимое действие — формирует платёжное поручение в банке
                                        </div>
                                    </div>
                                )}

                                {/* Правка/отмена — пока платёжка не создана в банке */}
                                {(detail.status === 'PENDING_REVIEW' || detail.status === 'DRAFT') && (
                                    <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
                                        <button
                                            className="btn btn-secondary"
                                            style={{ flex: 1 }}
                                            onClick={() => setEditId(detail.id)}
                                            title="Изменить сумму, назначение и реквизиты"
                                        >
                                            ✏️ Редактировать
                                        </button>
                                        <button
                                            className="btn btn-danger"
                                            style={{ flex: 1 }}
                                            onClick={handleCancel}
                                            disabled={cancelling}
                                        >
                                            {cancelling ? 'Отмена...' : '✖ Отменить оплату'}
                                        </button>
                                    </div>
                                )}

                                {/* Status history */}
                                {detail.events.length > 0 && (
                                    <div>
                                        <div style={{ fontWeight: 600, marginBottom: 8, color: 'var(--color-text-muted)', textTransform: 'uppercase', fontSize: 11, letterSpacing: '0.5px' }}>История статусов</div>
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                            {detail.events.map((ev: PaymentRequestEvent) => (
                                                <div key={ev.id} style={{ display: 'flex', gap: 8, fontSize: 12, alignItems: 'flex-start' }}>
                                                    <span style={{ color: 'var(--color-text-muted)', whiteSpace: 'nowrap', flexShrink: 0 }}>
                                                        {formatDateTime(ev.changed_at)}
                                                    </span>
                                                    <div style={{ minWidth: 0 }}>
                                                        <span className={`badge ${PAYMENT_STATUS_MAP[ev.new_status]?.className ?? ''}`} style={{ fontSize: 10 }}>
                                                            {PAYMENT_STATUS_MAP[ev.new_status]?.label ?? ev.new_status}
                                                        </span>
                                                        {ev.changed_by && (
                                                            <span style={{ color: 'var(--color-text-muted)', marginLeft: 6 }}>{ev.changed_by}</span>
                                                        )}
                                                        {ev.comment && (
                                                            <div style={{ color: 'var(--color-text-muted)', marginTop: 2 }}>{ev.comment}</div>
                                                        )}
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                )}
                            </>
                        ) : null}
                    </div>
                )}
            </div>

            {createModal}
            {editModal}
            {showCategories && (
                <PaymentCategoriesModal
                    canManage={canBankWrite}
                    onClose={() => setShowCategories(false)}
                    onChanged={() => { loadCategories(); load(); }}
                />
            )}
        </div>
    );
}

// ─── Helper component ──────────────────────────────────────────────────────────

function DetailRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
    return (
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, padding: '4px 0', borderBottom: '1px solid var(--color-border)' }}>
            <span style={{ color: 'var(--color-text-muted)', flexShrink: 0 }}>{label}</span>
            <span style={{ fontFamily: mono ? 'monospace' : undefined, textAlign: 'right', wordBreak: 'break-all' }}>{value}</span>
        </div>
    );
}
