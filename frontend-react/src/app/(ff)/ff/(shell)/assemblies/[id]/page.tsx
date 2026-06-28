'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';
import {
    ffGetAssembly,
    ffUpdateAssemblyItems,
    ffUpdateAssemblyPallets,
    ffArchiveAssembly,
    ffStartAssembly,
    ffReadyAssembly,
    ffShipAssembly,
    FfApiError,
} from '@/lib/api/ff';
import type {
    FfAssemblyDetail,
    FfAssemblyItem,
    FfAssemblyItemInput,
    FfAssemblyStatus,
} from '@/types/ff';
import { ProjectBadge, AssemblyStatusBadge, StuckBadge, assemblyStuckDays } from '../../../../_components/badges';
import { useTableSort, SortableTh, type SortValue } from '../../../../_components/table';

// Pre-ship statuses where pallets count / weight may still be corrected.
const PALLET_EDITABLE_STATUSES: FfAssemblyStatus[] = ['PENDING', 'IN_PROGRESS', 'READY', 'VEHICLE_ASSIGNED'];

// Status labels mirror AssemblyStatusBadge — for the timeline.
const STATUS_LABELS: Record<FfAssemblyStatus, string> = {
    PENDING: 'Ожидает сборку',
    IN_PROGRESS: 'В сборке',
    READY: 'Готово',
    VEHICLE_ASSIGNED: 'Машина назначена',
    SHIPPED: 'Отгружена',
    DELIVERED: 'Принята WB',
    RETURNED: 'Возврат на склад',
    CLOSED: 'Закрыта',
    CANCELLED: 'Отменена',
};

// Editable draft row for «Состав».
interface DraftItem {
    barcode: string;
    product_name: string | null;
    quantity: string;
}

function errMsg(err: unknown, fallback: string): string {
    if (err instanceof FfApiError) return err.message;
    if (err instanceof Error) return err.message;
    return fallback;
}

// Box render for a «Состав» row: «{full} кор» + «+ {rem} шт» (warning) when the
// quantity doesn't divide evenly by the box size; «—» when no box size is known.
// Mirrors the main-app «Позиции» Коробок column.
function BoxCell({ quantity, boxQty }: { quantity: number; boxQty: number | null }) {
    const k = boxQty ?? 0;
    if (k <= 0) return <span style={{ color: 'var(--color-text-muted)' }}>—</span>;
    const full = Math.floor(quantity / k);
    const rem = quantity % k;
    return (
        <span
            style={{ whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}
            title={`${full} полн. коробов по ${k} шт${rem > 0 ? ` + ${rem} шт россыпью (неполный короб)` : ''}`}
        >
            <span style={{ fontWeight: 500 }}>{formatNumber(full, 0)} кор</span>
            {rem > 0 && (
                <span style={{ color: 'var(--color-warning)', fontWeight: 600 }}>{` + ${formatNumber(rem, 0)} шт`}</span>
            )}
        </span>
    );
}

// Comparable value per sort key for the «Состав» read-only table.
function itemSortValue(it: FfAssemblyItem, key: string): SortValue {
    switch (key) {
        case 'barcode':
            return it.barcode;
        case 'product':
            return it.product_name ?? it.article ?? '';
        case 'boxes':
            // Sort by full-box count; barcodes without box size sort last (null).
            return it.box_qty && it.box_qty > 0 ? Math.floor(it.quantity / it.box_qty) : null;
        case 'quantity':
            return it.quantity;
        case 'stock':
            return it.stock;
        default:
            return null;
    }
}

/**
 * Read-only «Состав» table styled after the main-app «Позиции»:
 * ШК · Товар · Коробок · В поставке · На складе (stock colored red on deficit,
 * green otherwise). Sortable via the FF portal's own `useTableSort`/`SortableTh`.
 * Used for both the applied composition and the pending proposal.
 */
function CompositionTable({ items }: { items: FfAssemblyItem[] }) {
    const { sorted, sortKey, sortDir, toggleSort } = useTableSort(items, itemSortValue, {
        key: 'product',
        dir: 'asc',
    });
    return (
        <div style={{ overflowX: 'auto' }}>
            <table className="data-table" style={{ fontSize: 13 }}>
                <thead>
                    <tr>
                        <SortableTh label="ШК" sortKey="barcode" activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
                        <SortableTh label="Товар" sortKey="product" activeKey={sortKey} dir={sortDir} onSort={toggleSort} />
                        <SortableTh label="Коробок" sortKey="boxes" activeKey={sortKey} dir={sortDir} onSort={toggleSort} align="right" />
                        <SortableTh label="В поставке" sortKey="quantity" activeKey={sortKey} dir={sortDir} onSort={toggleSort} align="right" />
                        <SortableTh label="На складе" sortKey="stock" activeKey={sortKey} dir={sortDir} onSort={toggleSort} align="right" />
                    </tr>
                </thead>
                <tbody>
                    {sorted.map((it, idx) => (
                        <tr key={`${it.barcode}-${idx}`}>
                            <td>
                                <span style={{ fontFamily: 'monospace', fontSize: 13, color: 'var(--color-text-muted)', fontVariantNumeric: 'tabular-nums' }}>
                                    {it.barcode}
                                </span>
                            </td>
                            <td>
                                <div>{it.product_name || '—'}</div>
                                {it.article && (
                                    <div style={{ color: 'var(--color-text-muted)', fontSize: 13, fontVariantNumeric: 'tabular-nums' }}>
                                        {it.article}
                                    </div>
                                )}
                            </td>
                            <td className="ff-num">
                                <BoxCell quantity={it.quantity} boxQty={it.box_qty} />
                            </td>
                            <td className="ff-num" style={{ fontWeight: 500, fontVariantNumeric: 'tabular-nums' }}>
                                {formatNumber(it.quantity, 0)}
                            </td>
                            <td
                                className="ff-num"
                                style={{
                                    fontWeight: 500,
                                    fontVariantNumeric: 'tabular-nums',
                                    color: it.stock < it.quantity ? 'var(--color-danger)' : 'var(--color-success)',
                                }}
                            >
                                {formatNumber(it.stock, 0)}
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

export default function FfAssemblyDetailPage() {
    const params = useParams();
    const router = useRouter();
    const id = Number(params.id);

    const [data, setData] = useState<FfAssemblyDetail | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Action bar state.
    const [busy, setBusy] = useState(false);
    const [actionError, setActionError] = useState('');
    const [showReadyForm, setShowReadyForm] = useState(false);
    const [pallets, setPallets] = useState('');
    const [weight, setWeight] = useState('');

    // Inline pallets editor in the «Параметры» card (pre-ship correction).
    const [editPallets, setEditPallets] = useState(false);
    const [palletsEdit, setPalletsEdit] = useState('');
    const [weightEdit, setWeightEdit] = useState('');
    const [readyDateEdit, setReadyDateEdit] = useState('');
    const [palletsError, setPalletsError] = useState('');
    const [savingPallets, setSavingPallets] = useState(false);

    // Editable «Состав» draft. Edit-first: read-only by default; «Редактировать»
    // flips `editing` on (seeding the draft from the current source).
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState<DraftItem[]>([]);
    const [newBarcode, setNewBarcode] = useState('');
    const [newQty, setNewQty] = useState('');
    const [composeError, setComposeError] = useState('');
    const [savingItems, setSavingItems] = useState(false);
    const [pasteResult, setPasteResult] = useState<string | null>(null);

    // After submitting a proposal we show a one-shot confirmation note.
    const [submittedNote, setSubmittedNote] = useState(false);

    // Seed the editable draft from the PENDING proposal when one is awaiting DDS
    // approval (so re-editing continues from it); otherwise from current items.
    const syncDraft = useCallback((d: FfAssemblyDetail) => {
        const source = d.ff_review_pending ? d.proposed_items : d.items;
        setDraft(
            source.map((it) => ({
                barcode: it.barcode,
                product_name: it.product_name,
                quantity: String(it.quantity),
            })),
        );
    }, []);

    const load = useCallback(
        (signal?: AbortSignal) => {
            if (!Number.isFinite(id)) {
                setError('Некорректный идентификатор заявки');
                setLoading(false);
                return;
            }
            setLoading(true);
            setError('');
            setSubmittedNote(false);
            setEditing(false);
            ffGetAssembly(id)
                .then((res) => {
                    if (signal?.aborted) return;
                    setData(res);
                    syncDraft(res);
                })
                .catch((err: unknown) => {
                    if (signal?.aborted) return;
                    setError(errMsg(err, 'Не удалось загрузить заявку'));
                })
                .finally(() => {
                    if (signal?.aborted) return;
                    setLoading(false);
                });
        },
        [id, syncDraft],
    );

    useEffect(() => {
        const controller = new AbortController();
        load(controller.signal);
        return () => controller.abort();
    }, [load]);

    // Refresh the detail after a mutating action; keep error surfaced on failure.
    const applyUpdated = useCallback(
        (res: FfAssemblyDetail) => {
            setData(res);
            syncDraft(res);
        },
        [syncDraft],
    );

    const reload = useCallback(async () => {
        const res = await ffGetAssembly(id);
        applyUpdated(res);
    }, [id, applyUpdated]);

    const draftTotal = useMemo(
        () => draft.reduce((sum, d) => sum + (Number(d.quantity) || 0), 0),
        [draft],
    );

    const proposedTotal = useMemo(
        () => (data?.proposed_items ?? []).reduce((sum, it) => sum + it.quantity, 0),
        [data],
    );

    // Applied-composition total (read-only «Итого»). The editable footer uses
    // `draftTotal`; the read-only display shows `data.items`, so its total must
    // come from there too (the draft may be seeded from proposed_items when pending).
    const itemsTotal = useMemo(
        () => (data?.items ?? []).reduce((sum, it) => sum + it.quantity, 0),
        [data],
    );

    // Units-per-box keyed by barcode, taken from the loaded detail response.
    // Newly-pasted barcodes absent here have no box_qty until saved & reloaded.
    const boxQtyByBarcode = useMemo(() => {
        const m = new Map<string, number>();
        for (const it of data?.items ?? []) {
            if (it.box_qty != null) m.set(it.barcode, it.box_qty);
        }
        return m;
    }, [data]);

    // Excel export of the applied «Состав» (mirrors the main-app «Позиции» export).
    const exportComposition = useCallback(() => {
        if (!data) return;
        exportToExcel(
            data.items,
            `Сборка_${data.number}`,
            [
                { key: 'barcode', label: 'ШК' },
                { key: 'product_name', label: 'Товар', exportValue: (r) => r.product_name ?? '' },
                { key: 'article', label: 'Артикул', exportValue: (r) => r.article ?? '' },
                { key: 'quantity', label: 'В поставке' },
                { key: 'stock', label: 'На складе' },
            ],
        );
    }, [data]);

    // Index-based so pasted duplicate/empty-barcode rows stay individually addressable.
    const updateDraftQty = (index: number, quantity: string) => {
        setDraft((prev) => prev.map((d, i) => (i === index ? { ...d, quantity } : d)));
    };

    const removeDraftRow = (index: number) => {
        setDraft((prev) => prev.filter((_, i) => i !== index));
    };

    const addDraftRow = () => {
        setComposeError('');
        const bc = newBarcode.trim();
        const qty = Number(newQty);
        if (!bc) {
            setComposeError('Укажите баркод');
            return;
        }
        if (!(qty > 0)) {
            setComposeError('Количество должно быть больше нуля');
            return;
        }
        if (draft.some((d) => d.barcode === bc)) {
            setComposeError('Такой баркод уже есть в составе');
            return;
        }
        setDraft((prev) => [...prev, { barcode: bc, product_name: null, quantity: String(qty) }]);
        setNewBarcode('');
        setNewQty('');
    };

    // Mass-add from an Excel paste: «штрихкод ⇥ кол-во» per line. No dedup/merge
    // (same as the main-app editor); appends parsed rows + one trailing empty row.
    const handleBulkPaste = (text: string) => {
        const lines = text.split('\n').map((l) => l.trim()).filter(Boolean);
        const parsed: DraftItem[] = [];
        for (const line of lines) {
            const parts = line.split(/\t+|\s{2,}/);
            const barcode = parts[0]?.trim() ?? '';
            const qty = parts.length >= 2 ? parseInt(parts[1].trim(), 10) : 1;
            if (barcode && qty > 0) {
                parsed.push({ barcode, product_name: null, quantity: String(qty) });
            }
        }
        if (parsed.length > 0) {
            setDraft((prev) => [...prev, ...parsed, { barcode: '', product_name: null, quantity: '' }]);
            setPasteResult(`Добавлено ${parsed.length} позиций`);
            setTimeout(() => setPasteResult(null), 3000);
        }
    };

    // Enter edit mode: seed the draft from the current source (proposed_items when a
    // proposal is pending, else the applied items) so editing starts from what's shown.
    const startEditing = () => {
        if (!data) return;
        setComposeError('');
        setSubmittedNote(false);
        syncDraft(data);
        setEditing(true);
    };

    // Leave edit mode without submitting: drop draft edits back to the current source.
    const cancelEditing = () => {
        if (data) syncDraft(data);
        setComposeError('');
        setEditing(false);
    };

    const saveItems = async () => {
        setComposeError('');
        const items: FfAssemblyItemInput[] = draft
            .map((d) => ({ barcode: d.barcode, quantity: Number(d.quantity) || 0 }))
            .filter((it) => it.quantity > 0);
        if (items.length === 0) {
            setComposeError('Добавьте хотя бы одну позицию с количеством');
            return;
        }
        setSavingItems(true);
        try {
            const res = await ffUpdateAssemblyItems(id, items);
            applyUpdated(res);
            setSubmittedNote(true);
            // Back to read-only, which now shows the pending proposal via proposed_items.
            setEditing(false);
        } catch (err: unknown) {
            setComposeError(errMsg(err, 'Не удалось отправить на согласование'));
        } finally {
            setSavingItems(false);
        }
    };

    // ── Action-bar handlers ────────────────────────────────────────────────────
    const runAction = async (fn: () => Promise<unknown>, fallback: string) => {
        setBusy(true);
        setActionError('');
        try {
            await fn();
            await reload();
        } catch (err: unknown) {
            setActionError(errMsg(err, fallback));
        } finally {
            setBusy(false);
        }
    };

    const handleStart = () => runAction(() => ffStartAssembly(id), 'Не удалось взять в работу');

    const openReadyForm = () => {
        setActionError('');
        setPallets(data?.pallets_count != null ? String(data.pallets_count) : '');
        setWeight(data?.pallet_weight_kg != null ? String(Number(data.pallet_weight_kg)) : '');
        setShowReadyForm(true);
    };

    const submitReady = async () => {
        const pc = Number(pallets);
        const pw = Number(weight);
        if (!(pc > 0) || !(pw > 0)) {
            setActionError('Укажите число паллет и вес паллеты больше нуля');
            return;
        }
        setBusy(true);
        setActionError('');
        try {
            await ffReadyAssembly(id, { pallets_count: pc, pallet_weight_kg: pw });
            await reload();
            setShowReadyForm(false);
        } catch (err: unknown) {
            setActionError(errMsg(err, 'Не удалось отметить готовность'));
        } finally {
            setBusy(false);
        }
    };

    // ── Inline pallets editor (Параметры card) ─────────────────────────────────
    const openPalletsEditor = () => {
        setPalletsError('');
        setPalletsEdit(data?.pallets_count != null ? String(data.pallets_count) : '');
        setWeightEdit(data?.pallet_weight_kg != null ? String(Number(data.pallet_weight_kg)) : '');
        setReadyDateEdit(data?.estimated_ready_date ?? '');
        setEditPallets(true);
    };

    const closePalletsEditor = () => {
        setEditPallets(false);
        setPalletsError('');
    };

    const savePallets = async () => {
        const pc = Number(palletsEdit);
        const pw = Number(weightEdit);
        if (!(pc > 0) || !(pw > 0)) {
            setPalletsError('Укажите число паллет и вес паллеты больше нуля');
            return;
        }
        setSavingPallets(true);
        setPalletsError('');
        try {
            const res = await ffUpdateAssemblyPallets(id, {
                pallets_count: pc,
                pallet_weight_kg: pw,
                estimated_ready_date: readyDateEdit || null,
            });
            applyUpdated(res);
            setEditPallets(false);
        } catch (err: unknown) {
            setPalletsError(errMsg(err, 'Не удалось сохранить параметры паллет'));
        } finally {
            setSavingPallets(false);
        }
    };

    const handleShip = () => runAction(() => ffShipAssembly(id), 'Не удалось отгрузить');

    const handleArchive = () =>
        runAction(
            () => ffArchiveAssembly(id, !(data?.is_archived ?? false)),
            'Не удалось изменить архив',
        );

    // ── States: loading / error / empty / data ──────────────────────────────────
    if (loading) {
        return (
            <div className="animate-in">
                <div className="ff-feed">
                    <div className="ff-skeleton" style={{ height: 48 }} />
                    <div className="ff-skeleton" />
                    <div className="ff-skeleton" />
                </div>
            </div>
        );
    }

    if (error) {
        return (
            <div className="animate-in">
                <button className="ff-back" onClick={() => router.push('/ff/assemblies')}>
                    ← К заявкам
                </button>
                <div
                    className="glass-card"
                    style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, color: 'var(--color-danger)' }}
                >
                    <span>{error}</span>
                    <button className="btn btn-secondary btn-sm" onClick={() => load()}>
                        Повторить
                    </button>
                </div>
            </div>
        );
    }

    if (!data) {
        return (
            <div className="animate-in">
                <button className="ff-back" onClick={() => router.push('/ff/assemblies')}>
                    ← К заявкам
                </button>
                <div className="glass-card">
                    <div className="empty-state">
                        <div className="empty-state-text">Заявка не найдена</div>
                    </div>
                </div>
            </div>
        );
    }

    const editable = data.editable;
    // Edit-first: editable assemblies still default to the read-only view; the draft
    // editor (and «Отправить на согласование») render only after «Редактировать».
    const showEditor = editable && editing;
    const palletsEditable = PALLET_EDITABLE_STATUSES.includes(data.status);
    const hasDelivery =
        data.vehicle_assigned ||
        !!data.driver_phone ||
        !!data.vehicle_info ||
        !!data.vehicle_brand ||
        !!data.carrier_name ||
        !!data.pickup_date ||
        !!data.delivery_date;

    const lastHistoryIdx = data.history.length - 1;

    return (
        <div className="animate-in">
            <button className="ff-back" onClick={() => router.push('/ff/assemblies')}>
                ← К заявкам
            </button>

            <div className="page-header">
                <div>
                    <h1 className="page-title">Заявка № {data.number}</h1>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                        <AssemblyStatusBadge status={data.status} />
                        <StuckBadge days={assemblyStuckDays(data.status, data.created_at, data.actual_ready_date)} />
                        <ProjectBadge name={data.project_name} />
                        {data.ff_review_pending && (
                            <span className="badge badge-warning">
                                ⏳ Отправлено на согласование
                                {data.ff_proposed_at ? ` · ${formatDate(data.ff_proposed_at)}` : ''}
                            </span>
                        )}
                        {data.is_archived && <span className="badge badge-secondary">В архиве</span>}
                        <span className="page-subtitle" style={{ margin: 0 }}>
                            {data.warehouse_name}
                            {data.wb_warehouse_name ? ` → ${data.wb_warehouse_name}` : ''}
                        </span>
                    </div>
                </div>
            </div>

            {/* ─── Info cards: small metadata, tidy grid above the состав table ─── */}
            <div className="ff-detail-grid">
                {/* ─── Доставка ─── */}
                {hasDelivery && (
                    <div className="glass-card ff-delivery">
                        <h2 className="page-subtitle" style={{ margin: '0 0 12px', fontWeight: 600, color: 'var(--color-text)' }}>
                            Доставка
                        </h2>
                        <div className="ff-kv">
                            <span className="ff-kv-key">Водитель</span>
                            <span className="ff-kv-val">{data.driver_phone || '—'}</span>
                        </div>
                        <div className="ff-kv">
                            <span className="ff-kv-key">Машина</span>
                            <span className="ff-kv-val">{data.vehicle_info || data.vehicle_brand || '—'}</span>
                        </div>
                        <div className="ff-kv">
                            <span className="ff-kv-key">Перевозчик</span>
                            <span className="ff-kv-val">{data.carrier_name || '—'}</span>
                        </div>
                        <div className="ff-kv">
                            <span className="ff-kv-key">Дата вывоза</span>
                            <span className="ff-kv-val">
                                {data.pickup_date ? formatDate(data.pickup_date) : '—'}
                                {data.pickup_time_slot ? `, ${data.pickup_time_slot}` : ''}
                            </span>
                        </div>
                        <div className="ff-kv">
                            <span className="ff-kv-key">Дата сдачи</span>
                            <span className="ff-kv-val">{data.delivery_date ? formatDate(data.delivery_date) : '—'}</span>
                        </div>
                        {data.via_gazelka && (
                            <div style={{ marginTop: 12 }}>
                                <span className="badge badge-info">🚚 через Газельку</span>
                            </div>
                        )}
                    </div>
                )}

                {/* ─── Параметры ─── */}
                <div className="glass-card">
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 12 }}>
                        <h2 className="page-subtitle" style={{ margin: 0, fontWeight: 600, color: 'var(--color-text)' }}>
                            Параметры
                        </h2>
                        {palletsEditable && !editPallets && (
                            <button
                                className="btn btn-secondary btn-sm"
                                onClick={openPalletsEditor}
                                title="Изменить число паллет и вес"
                            >
                                ✎ Изменить
                            </button>
                        )}
                    </div>
                    <div className="ff-kv">
                        <span className="ff-kv-key">Тип упаковки</span>
                        <span className="ff-kv-val">{data.package_type || '—'}</span>
                    </div>
                    {editPallets ? (
                        <>
                            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end', margin: '4px 0' }}>
                                <div className="form-group" style={{ margin: 0 }}>
                                    <label className="form-label">Паллет, шт</label>
                                    <input
                                        className="form-input"
                                        type="number"
                                        min={1}
                                        inputMode="numeric"
                                        value={palletsEdit}
                                        onChange={(e) => setPalletsEdit(e.target.value)}
                                        style={{ width: 120 }}
                                        aria-label="Число паллет"
                                    />
                                </div>
                                <div className="form-group" style={{ margin: 0 }}>
                                    <label className="form-label">Вес паллеты, кг</label>
                                    <input
                                        className="form-input"
                                        type="number"
                                        min={0}
                                        step="0.1"
                                        inputMode="decimal"
                                        value={weightEdit}
                                        onChange={(e) => setWeightEdit(e.target.value)}
                                        style={{ width: 120 }}
                                        aria-label="Вес паллеты"
                                    />
                                </div>
                                <div className="form-group" style={{ margin: 0 }}>
                                    <label className="form-label">Плановая готовность</label>
                                    <input
                                        className="form-input"
                                        type="date"
                                        value={readyDateEdit}
                                        onChange={(e) => setReadyDateEdit(e.target.value)}
                                        style={{ width: 160 }}
                                        aria-label="Плановая готовность"
                                    />
                                </div>
                            </div>
                            {palletsError && (
                                <div className="glass-card" style={{ margin: '8px 0', color: 'var(--color-danger)', fontSize: 13 }}>
                                    {palletsError}
                                </div>
                            )}
                            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, margin: '8px 0 4px' }}>
                                <button className="btn btn-secondary btn-sm" disabled={savingPallets} onClick={closePalletsEditor}>
                                    Отмена
                                </button>
                                <button className="btn btn-primary btn-sm" disabled={savingPallets} onClick={savePallets}>
                                    {savingPallets ? 'Сохранение…' : 'Сохранить'}
                                </button>
                            </div>
                        </>
                    ) : (
                        <>
                            <div className="ff-kv">
                                <span className="ff-kv-key">Паллет</span>
                                <span className="ff-kv-val">
                                    {data.pallets_count != null ? formatNumber(data.pallets_count, 0) : '—'}
                                </span>
                            </div>
                            <div className="ff-kv">
                                <span className="ff-kv-key">Вес паллеты</span>
                                <span className="ff-kv-val">
                                    {data.pallet_weight_kg != null ? `${formatNumber(Number(data.pallet_weight_kg), 0)} кг` : '—'}
                                </span>
                            </div>
                        </>
                    )}
                    <div className="ff-kv">
                        <span className="ff-kv-key">Создана</span>
                        <span className="ff-kv-val">{formatDate(data.created_at)}</span>
                    </div>
                    <div className="ff-kv">
                        <span className="ff-kv-key">Плановая готовность</span>
                        <span className="ff-kv-val">{data.estimated_ready_date ? formatDate(data.estimated_ready_date) : '—'}</span>
                    </div>
                    <div className="ff-kv">
                        <span className="ff-kv-key">Факт. готовность</span>
                        <span className="ff-kv-val">{data.actual_ready_date ? formatDate(data.actual_ready_date) : '—'}</span>
                    </div>
                </div>

                {/* ─── Поставка WB (если есть в связке) ─── */}
                {(data.fbo_supply_name || data.fbo_supply_wb_id) && (
                    <div className="glass-card">
                        <h2 className="page-subtitle" style={{ margin: '0 0 12px', fontWeight: 600, color: 'var(--color-text)' }}>
                            Поставка WB
                        </h2>
                        {data.fbo_supply_name && (
                            <div className="ff-kv">
                                <span className="ff-kv-key">Поставка</span>
                                <span className="ff-kv-val">{data.fbo_supply_name}</span>
                            </div>
                        )}
                        {data.fbo_supply_wb_id && (
                            <div className="ff-kv">
                                <span className="ff-kv-key">№ WB</span>
                                <span className="ff-kv-val" style={{ fontVariantNumeric: 'tabular-nums' }}>{data.fbo_supply_wb_id}</span>
                            </div>
                        )}
                        {data.fbo_supply_status && (
                            <div className="ff-kv">
                                <span className="ff-kv-key">Статус</span>
                                <span className="ff-kv-val">{data.fbo_supply_status}</span>
                            </div>
                        )}
                        {data.fbo_supply_planned_date && (
                            <div className="ff-kv">
                                <span className="ff-kv-key">Плановая дата</span>
                                <span className="ff-kv-val">{formatDate(data.fbo_supply_planned_date)}</span>
                            </div>
                        )}
                    </div>
                )}

                {/* ─── Где сейчас (timeline) ─── */}
                <div className="glass-card">
                    <h2 className="page-subtitle" style={{ margin: '0 0 16px', fontWeight: 600, color: 'var(--color-text)' }}>
                        Где сейчас
                    </h2>
                    {data.history.length === 0 ? (
                        <div className="empty-state">
                            <div className="empty-state-text">История пуста</div>
                        </div>
                    ) : (
                        <div className="ff-timeline">
                            {data.history.map((ev, idx) => (
                                <div
                                    key={`${ev.status}-${ev.changed_at}-${idx}`}
                                    className={`ff-timeline-item${idx === lastHistoryIdx ? ' ff-timeline-now' : ''}`}
                                >
                                    <div className="ff-timeline-dot" />
                                    <div className="ff-timeline-body">
                                        <div style={{ fontWeight: idx === lastHistoryIdx ? 600 : 500 }}>
                                            {STATUS_LABELS[ev.status] ?? ev.status}
                                        </div>
                                        <div className="ff-timeline-when">{formatDate(ev.changed_at)}</div>
                                        {ev.comment && (
                                            <div style={{ color: 'var(--color-text-muted)', fontSize: 13, marginTop: 2 }}>{ev.comment}</div>
                                        )}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>

            {/* ─── Состав (full-width, below the info-cards grid) ─── */}
            <div
                className="glass-card"
                style={{ marginBottom: 16 }}
                onPaste={
                    showEditor
                        ? (e) => {
                              const text = e.clipboardData.getData('text/plain');
                              if (!text.includes('\t') && !text.includes('\n')) return;
                              e.preventDefault();
                              handleBulkPaste(text);
                          }
                        : undefined
                }
            >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, margin: '0 0 12px' }}>
                    <h2 className="page-subtitle" style={{ margin: 0, fontWeight: 600, color: 'var(--color-text)' }}>
                        Состав
                    </h2>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        {data.items.length > 0 && (
                            <button className="btn btn-secondary btn-sm" onClick={exportComposition} title="Выгрузить состав в Excel">
                                Excel
                            </button>
                        )}
                        {editable && !editing && (
                            <button
                                className="btn btn-primary btn-sm"
                                onClick={startEditing}
                                title={data.ff_review_pending ? 'Изменить предложенный состав' : 'Изменить состав'}
                            >
                                ✎ Редактировать
                            </button>
                        )}
                        {showEditor && (
                            <button className="btn btn-secondary btn-sm" disabled={savingItems} onClick={cancelEditing}>
                                Отмена
                            </button>
                        )}
                    </div>
                </div>

                {/* Pending proposal awaiting DDS approval — shown above the current
                    (still-applied) composition so it's clearly not yet in effect. */}
                {data.ff_review_pending && (
                    <div
                        className="glass-card"
                        style={{ marginBottom: 16, border: '1px solid var(--color-warning)', padding: 16 }}
                    >
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
                            <span className="badge badge-warning">⏳ Предложено (ожидает согласования)</span>
                            {data.ff_proposed_at && (
                                <span className="ff-kv-key" style={{ fontSize: 12 }}>{formatDate(data.ff_proposed_at)}</span>
                            )}
                        </div>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 8 }}>
                            Эти изменения отправлены в DDS и пока не применены к текущему составу.
                        </div>
                        {data.proposed_items.length === 0 ? (
                            <div className="empty-state">
                                <div className="empty-state-text">Предложен пустой состав</div>
                            </div>
                        ) : (
                            <CompositionTable items={data.proposed_items} />
                        )}
                        <div
                            style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--color-border)' }}
                        >
                            <span className="ff-kv-key">Итого предложено</span>
                            <span style={{ fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{formatNumber(proposedTotal, 0)} шт</span>
                        </div>
                    </div>
                )}

                {data.ff_review_pending && (
                    <div className="page-subtitle" style={{ margin: '0 0 8px', fontWeight: 600, color: 'var(--color-text)' }}>
                        {showEditor ? 'Редактирование предложения' : 'Текущий состав (применён)'}
                    </div>
                )}

                {!showEditor ? (
                    data.items.length === 0 ? (
                        <div className="empty-state">
                            <div className="empty-state-text">Нет позиций</div>
                        </div>
                    ) : (
                        <CompositionTable items={data.items} />
                    )
                ) : (
                    <div style={{ overflowX: 'auto' }}>
                        <table className="data-table" style={{ fontSize: 13 }}>
                            <thead>
                                <tr>
                                    <th>Товар</th>
                                    <th style={{ textAlign: 'right' }}>Коробок</th>
                                    <th style={{ textAlign: 'right' }}>Кол-во</th>
                                    {editable && <th style={{ width: 40 }} aria-label="Действия" />}
                                </tr>
                            </thead>
                            <tbody>
                                {draft.map((d, idx) => {
                                    const article = data.items.find((it) => it.barcode === d.barcode)?.article ?? null;
                                    const k = boxQtyByBarcode.get(d.barcode) ?? 0;
                                    const qty = Number(d.quantity) || 0;
                                    const full = k > 0 ? Math.floor(qty / k) : 0;
                                    const rem = k > 0 ? qty % k : 0;
                                    return (
                                    <tr key={idx}>
                                        <td>
                                            <div>{d.product_name || '—'}</div>
                                            <div style={{ color: 'var(--color-text-muted)', fontSize: 13, fontVariantNumeric: 'tabular-nums' }}>
                                                {d.barcode}{article ? ` · ${article}` : ''}
                                            </div>
                                        </td>
                                        <td style={{ textAlign: 'right' }}>
                                            {k > 0 ? (
                                                <span
                                                    style={{ whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}
                                                    title={`${full} полн. коробов по ${k} шт${rem > 0 ? ` + ${rem} шт россыпью (неполный короб)` : ''}`}
                                                >
                                                    <span style={{ fontWeight: 500 }}>{formatNumber(full, 0)} кор</span>
                                                    {rem > 0 && (
                                                        <span style={{ color: 'var(--color-warning)', fontWeight: 600 }}>{` + ${formatNumber(rem, 0)} шт`}</span>
                                                    )}
                                                </span>
                                            ) : (
                                                <span style={{ color: 'var(--color-text-muted)' }}>—</span>
                                            )}
                                        </td>
                                        <td style={{ textAlign: 'right' }}>
                                            {editable ? (
                                                <input
                                                    className="form-input"
                                                    type="number"
                                                    min={0}
                                                    inputMode="numeric"
                                                    value={d.quantity}
                                                    onChange={(e) => updateDraftQty(idx, e.target.value)}
                                                    style={{ width: 90, textAlign: 'right', padding: '4px 8px' }}
                                                    aria-label={`Кол-во ${d.product_name || d.barcode}`}
                                                />
                                            ) : (
                                                <span style={{ fontVariantNumeric: 'tabular-nums' }}>
                                                    {formatNumber(Number(d.quantity), 0)}
                                                </span>
                                            )}
                                        </td>
                                        {editable && (
                                            <td style={{ textAlign: 'right' }}>
                                                <button
                                                    className="btn btn-danger btn-sm"
                                                    onClick={() => removeDraftRow(idx)}
                                                    title="Удалить позицию"
                                                    aria-label={`Удалить ${d.product_name || d.barcode}`}
                                                >
                                                    ✕
                                                </button>
                                            </td>
                                        )}
                                    </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )}

                <div
                    style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--color-border)' }}
                >
                    <span className="ff-kv-key">Итого</span>
                    <span style={{ fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
                        {formatNumber(showEditor ? draftTotal : itemsTotal, 0)} шт
                    </span>
                </div>

                {showEditor && (
                    <>
                        <div style={{ display: 'flex', gap: 8, marginTop: 16, flexWrap: 'wrap' }}>
                            <input
                                className="form-input"
                                type="text"
                                inputMode="numeric"
                                placeholder="Баркод"
                                value={newBarcode}
                                onChange={(e) => setNewBarcode(e.target.value)}
                                style={{ flex: '1 1 140px', padding: '6px 10px' }}
                                aria-label="Новый баркод"
                            />
                            <input
                                className="form-input"
                                type="number"
                                min={1}
                                inputMode="numeric"
                                placeholder="Кол-во"
                                value={newQty}
                                onChange={(e) => setNewQty(e.target.value)}
                                style={{ width: 100, padding: '6px 10px' }}
                                aria-label="Количество новой позиции"
                            />
                            <button className="btn btn-secondary btn-sm" onClick={addDraftRow}>
                                + Добавить
                            </button>
                        </div>

                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 8 }}>
                            {pasteResult ? (
                                <span style={{ color: 'var(--color-success)' }}>{pasteResult}</span>
                            ) : (
                                'Ctrl+V — вставить из Excel (штрихкод ⇥ кол-во)'
                            )}
                        </div>

                        {composeError && (
                            <div className="glass-card" style={{ marginTop: 12, color: 'var(--color-danger)', fontSize: 13 }}>
                                {composeError}
                            </div>
                        )}

                        {submittedNote && !composeError && (
                            <div
                                className="glass-card"
                                style={{ marginTop: 12, color: 'var(--color-success)', fontSize: 13 }}
                            >
                                Изменения отправлены в DDS на согласование
                            </div>
                        )}

                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 12 }}>
                            {data.ff_review_pending
                                ? 'Повторная отправка заменит предыдущее предложение, ожидающее согласования.'
                                : 'Состав не применится сразу — он уйдёт в DDS на согласование.'}
                        </div>

                        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16 }}>
                            <button className="btn btn-primary btn-sm" disabled={savingItems} onClick={saveItems}>
                                {savingItems ? 'Отправка…' : 'Отправить на согласование'}
                            </button>
                        </div>
                    </>
                )}
            </div>

            {/* ─── Inline готовность form ─── */}
            {showReadyForm && (
                <div className="glass-card" style={{ marginBottom: 16 }}>
                    <h2 className="page-subtitle" style={{ margin: '0 0 12px', fontWeight: 600, color: 'var(--color-text)' }}>
                        Готовность № {data.number}
                    </h2>
                    <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                        <div className="form-group" style={{ margin: 0 }}>
                            <label className="form-label">Паллет, шт</label>
                            <input
                                className="form-input"
                                type="number"
                                min={1}
                                inputMode="numeric"
                                value={pallets}
                                onChange={(e) => setPallets(e.target.value)}
                                style={{ width: 140 }}
                            />
                        </div>
                        <div className="form-group" style={{ margin: 0 }}>
                            <label className="form-label">Вес паллеты, кг</label>
                            <input
                                className="form-input"
                                type="number"
                                min={0}
                                step="0.1"
                                inputMode="decimal"
                                value={weight}
                                onChange={(e) => setWeight(e.target.value)}
                                style={{ width: 140 }}
                            />
                        </div>
                    </div>
                </div>
            )}

            {/* ─── Action bar ─── */}
            <div className="ff-actionbar">
                {actionError && (
                    <span style={{ color: 'var(--color-danger)', fontSize: 13, marginRight: 'auto', alignSelf: 'center' }}>
                        {actionError}
                    </span>
                )}

                {data.status !== 'DELIVERED' && data.status !== 'CANCELLED' && (
                    <button className="btn btn-secondary" disabled={busy} onClick={handleArchive}>
                        {data.is_archived ? 'Вернуть из архива' : 'В архив'}
                    </button>
                )}

                {data.status === 'PENDING' && (
                    <button className="btn btn-primary" disabled={busy} onClick={handleStart}>
                        {busy ? '…' : 'Взять в работу'}
                    </button>
                )}

                {data.status === 'IN_PROGRESS' && !showReadyForm && (
                    <button className="btn btn-primary" disabled={busy} onClick={openReadyForm}>
                        Готово
                    </button>
                )}

                {data.status === 'IN_PROGRESS' && showReadyForm && (
                    <>
                        <button className="btn btn-secondary" disabled={busy} onClick={() => setShowReadyForm(false)}>
                            Отмена
                        </button>
                        <button className="btn btn-primary" disabled={busy} onClick={submitReady}>
                            {busy ? 'Сохранение…' : 'Подтвердить готовность'}
                        </button>
                    </>
                )}

                {data.status === 'READY' && (
                    <button className="btn btn-secondary" disabled title="Ждём назначения машины логистикой">
                        Ждём машину
                    </button>
                )}

                {data.status === 'VEHICLE_ASSIGNED' && (
                    <button
                        className="btn btn-success"
                        disabled={busy || !data.vehicle_assigned}
                        title={data.vehicle_assigned ? undefined : 'Ждём назначения машины логистикой'}
                        onClick={handleShip}
                    >
                        {busy ? '…' : 'Отгрузил'}
                    </button>
                )}
            </div>
        </div>
    );
}
