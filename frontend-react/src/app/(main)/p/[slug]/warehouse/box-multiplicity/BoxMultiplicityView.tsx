'use client';
import { useEffect, useState, useCallback, useMemo } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate, formatDateTime } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type {
    BoxMultiplicityRow,
    BoxMultiplicityBulkItem,
    BoxMultiplicityBatchSummary,
    BoxMultiplicityChangeRow,
    BoxMultiplicityPerWarehouseRow,
    BoxMultiplicitySourceRow,
} from '@/types/api';
import { parseBoxMultiplicityPaste } from '@/lib/utils/boxMultiplicityPaste';
import { parseBoxSize, effectiveBoxesPerPallet, palletsForBoxes, maxPalletHeightCm, canonBoxSize } from '@/lib/utils/boxPallet';
import {
    type StockFilter,
    skuHasBoxQty,
    skuCanPalletize,
    skuHasPartialBoxQty,
    skuHasMixedBoxQty,
    passesRowFilter,
} from '@/lib/utils/boxMultiplicityFilter';

const SOURCE_TYPE_META: Record<string, { label: string; icon: string }> = {
    vehicle: { label: 'Машина', icon: '🚚' },
    factory: { label: 'Заказ', icon: '🏭' },
};

// Бейдж источника кратности для ячейки ФФ-склада.
function SourceBadge({ wh }: { wh: BoxMultiplicityPerWarehouseRow }) {
    if (wh.source === 'machine') {
        const date = wh.machine_received_at ? ` · ${formatDate(wh.machine_received_at)}` : '';
        const label = wh.machine_order_no ? `машина ${wh.machine_order_no}${date}` : 'машина';
        return (
            <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
                <span className="badge badge-info" style={{ fontSize: 10 }} title="Кратность из принятой машины — заблокирована">
                    🔒 {label}
                </span>
                {wh.machine_variants > 0 && (
                    <span
                        className="badge badge-warning"
                        style={{ fontSize: 10 }}
                        title={`Ещё ${wh.machine_variants} принятых машин на этом ФФ с другой кратностью — показана последняя`}
                    >
                        +{wh.machine_variants}
                    </span>
                )}
            </span>
        );
    }
    if (wh.source === 'manual') {
        return (
            <span className="badge badge-success" style={{ fontSize: 10 }} title="Кратность задана вручную для этого ФФ">
                вручную
            </span>
        );
    }
    if (wh.source === 'default') {
        return (
            <span className="badge badge-secondary" style={{ fontSize: 10 }} title="Кратность взята из ручного дефолта SKU">
                дефолт
            </span>
        );
    }
    return (
        <span className="badge badge-secondary" style={{ fontSize: 10, opacity: 0.6 }} title="Кратность не определена">
            —
        </span>
    );
}

// История снабжения SKU (read-only) — вложенная секция внутри sub-row.
function SkuSourcesSection({ barcode }: { barcode: string }) {
    const [sources, setSources] = useState<BoxMultiplicitySourceRow[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loaded, setLoaded] = useState(false);

    // Ленивая загрузка — только когда секция раскрыта пользователем.
    const loadSources = useCallback(async () => {
        if (loaded) return;
        try {
            setError(null);
            const resp = await api.getBoxMultiplicitySources(barcode);
            setSources(resp.items);
            setLoaded(true);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
    }, [barcode, loaded]);

    return (
        <details
            style={{ marginTop: 10 }}
            onToggle={e => { if ((e.currentTarget as HTMLDetailsElement).open) loadSources(); }}
        >
            <summary style={{
                cursor: 'pointer', fontSize: 11, fontWeight: 600,
                color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: 0.05,
            }}>
                История снабжения
            </summary>
            <div style={{ marginTop: 8 }}>
                {error && (
                    <div style={{ color: 'var(--color-danger)', fontSize: 13 }}>{error}</div>
                )}
                {!error && sources === null && (
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>
                        Загрузка истории снабжения…
                    </div>
                )}
                {!error && sources !== null && sources.length === 0 && (
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>
                        Нет истории снабжения — ни машин, ни заказов на фабрику для этого SKU.
                    </div>
                )}
                {!error && sources !== null && sources.length > 0 && (
                    <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                        <thead>
                            <tr style={{
                                color: 'var(--color-text-muted)', fontSize: 10,
                                textTransform: 'uppercase', letterSpacing: 0.05,
                            }}>
                                <th style={{ textAlign: 'left', padding: '4px 8px' }}>Тип</th>
                                <th style={{ textAlign: 'left', padding: '4px 8px' }}>№ заказа</th>
                                <th style={{ textAlign: 'left', padding: '4px 8px' }}>ФФ-склад</th>
                                <th style={{ textAlign: 'right', padding: '4px 8px', width: 80 }}>Кол-во</th>
                                <th style={{ textAlign: 'right', padding: '4px 8px', width: 110 }}>Кратность</th>
                                <th style={{ textAlign: 'left', padding: '4px 8px', width: 120 }}>Размер</th>
                                <th style={{ textAlign: 'left', padding: '4px 8px', width: 110 }}>Дата</th>
                                <th style={{ textAlign: 'center', padding: '4px 8px', width: 90 }}>Принято</th>
                            </tr>
                        </thead>
                        <tbody>
                            {sources.map((row, i) => {
                                const meta = SOURCE_TYPE_META[row.source_type] ?? { label: row.source_type, icon: '' };
                                return (
                                    <tr
                                        key={`${row.source_type}-${row.order_no}-${i}`}
                                        style={{ borderTop: '1px solid var(--color-border)' }}
                                    >
                                        <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}>
                                            {meta.icon} {meta.label}
                                        </td>
                                        <td style={{ padding: '6px 8px' }}>
                                            <span style={{ fontFamily: 'monospace' }}>{row.order_no}</span>
                                            {row.status && (
                                                <span className="badge badge-secondary" style={{ marginLeft: 6, fontSize: 10 }}>
                                                    {row.status}
                                                </span>
                                            )}
                                        </td>
                                        <td style={{
                                            padding: '6px 8px',
                                            color: row.warehouse_name ? undefined : 'var(--color-text-dim)',
                                        }}>
                                            {row.warehouse_name || '—'}
                                        </td>
                                        <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(row.qty, 0)}</td>
                                        <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                                            <span style={{
                                                color: row.box_qty !== null ? undefined : 'var(--color-text-dim)',
                                                fontWeight: row.box_qty !== null ? 500 : 400,
                                            }}>
                                                {row.box_qty !== null ? `${formatNumber(row.box_qty, 0)} шт` : 'не задана'}
                                            </span>
                                        </td>
                                        <td style={{
                                            padding: '6px 8px',
                                            color: row.box_size ? undefined : 'var(--color-text-dim)',
                                        }}>
                                            {row.box_size || '—'}
                                        </td>
                                        <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}>{formatDate(row.date)}</td>
                                        <td style={{ padding: '6px 8px', textAlign: 'center' }}>
                                            {row.accepted ? (
                                                <span className="badge badge-success" style={{ fontSize: 10 }}>принято</span>
                                            ) : (
                                                <span className="badge badge-secondary" style={{ fontSize: 10 }}>в пути</span>
                                            )}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                )}
            </div>
        </details>
    );
}

// Человекочитаемые лейблы полей/источников истории изменений.
const CHANGE_FIELD_LABELS: Record<string, string> = {
    box_qty_override: 'Ручной дефолт',
    box_qty: 'Кратность ФФ',
    box_size: 'Размер ФФ',
    use_box_multiplicity: 'Учитывать',
};
const CHANGE_SOURCE_LABELS: Record<string, string> = {
    manual: 'вручную',
    bulk: 'массово',
    revert: 'откат',
};

// Отображение значения изменения: null → «не задано».
function changeValueText(field: string, value: string | null): string {
    if (value === null) return 'не задано';
    if (field === 'use_box_multiplicity') {
        return value === 'true' ? 'да' : value === 'false' ? 'нет' : value;
    }
    return value;
}

// История изменений кратности/размера SKU (SKU- и per-ФФ-уровень) — collapsible.
function SkuChangesSection({
    barcode,
    onRowUpdated,
}: {
    barcode: string;
    onRowUpdated: (row: BoxMultiplicityRow) => void;
}) {
    const [changes, setChanges] = useState<BoxMultiplicityChangeRow[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loaded, setLoaded] = useState(false);
    const [revertingId, setRevertingId] = useState<number | null>(null);
    const [revertError, setRevertError] = useState<string | null>(null);

    // Ленивая загрузка — только когда секция раскрыта пользователем.
    const loadChanges = useCallback(async () => {
        if (loaded) return;
        try {
            setError(null);
            const resp = await api.getBoxMultiplicityChanges(barcode);
            setChanges(resp.items);
            setLoaded(true);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }
    }, [barcode, loaded]);

    const revert = useCallback(async (changeId: number) => {
        setRevertingId(changeId);
        setRevertError(null);
        try {
            const updated = await api.revertBoxMultiplicityChange(changeId);
            onRowUpdated(updated);
            // Перечитываем историю — откат добавляет новую запись.
            const resp = await api.getBoxMultiplicityChanges(barcode);
            setChanges(resp.items);
        } catch (e: unknown) {
            // 409 — откат заблокирован машиной.
            setRevertError(e instanceof Error ? e.message : 'Не удалось откатить изменение');
        } finally {
            setRevertingId(null);
        }
    }, [barcode, onRowUpdated]);

    return (
        <details
            style={{ marginTop: 10 }}
            onToggle={e => { if ((e.currentTarget as HTMLDetailsElement).open) loadChanges(); }}
        >
            <summary style={{
                cursor: 'pointer', fontSize: 11, fontWeight: 600,
                color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: 0.05,
            }}>
                История изменений
            </summary>
            <div style={{ marginTop: 8 }}>
                {error && (
                    <div style={{ color: 'var(--color-danger)', fontSize: 13 }}>{error}</div>
                )}
                {revertError && (
                    <div style={{
                        marginBottom: 8, padding: '8px 12px', borderRadius: 8,
                        background: 'rgba(255, 59, 48, 0.08)',
                        borderLeft: '3px solid var(--color-danger)', fontSize: 12,
                        color: 'var(--color-danger)',
                    }}>
                        ⚠️ {revertError}
                    </div>
                )}
                {!error && changes === null && (
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>
                        Загрузка истории изменений…
                    </div>
                )}
                {!error && changes !== null && changes.length === 0 && (
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>
                        Нет истории изменений кратности или размера для этого SKU.
                    </div>
                )}
                {!error && changes !== null && changes.length > 0 && (
                    <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                        <thead>
                            <tr style={{
                                color: 'var(--color-text-muted)', fontSize: 10,
                                textTransform: 'uppercase', letterSpacing: 0.05,
                            }}>
                                <th style={{ textAlign: 'left', padding: '4px 8px' }}>Поле</th>
                                <th style={{ textAlign: 'left', padding: '4px 8px' }}>Изменение</th>
                                <th style={{ textAlign: 'left', padding: '4px 8px', width: 90 }}>Источник</th>
                                <th style={{ textAlign: 'left', padding: '4px 8px', width: 150 }}>Дата</th>
                                <th style={{ textAlign: 'center', padding: '4px 8px', width: 100 }}>Откат</th>
                            </tr>
                        </thead>
                        <tbody>
                            {changes.map(ch => {
                                const fieldLabel = CHANGE_FIELD_LABELS[ch.field] ?? ch.field;
                                const sourceLabel = CHANGE_SOURCE_LABELS[ch.change_source] ?? ch.change_source;
                                return (
                                    <tr key={ch.id} style={{ borderTop: '1px solid var(--color-border)' }}>
                                        <td style={{ padding: '6px 8px' }}>
                                            <span style={{ fontWeight: 500 }}>{fieldLabel}</span>
                                            {ch.warehouse_id !== null && (
                                                <span style={{ color: 'var(--color-text-muted)', display: 'block', fontSize: 11 }}>
                                                    {ch.warehouse_name || `ФФ #${ch.warehouse_id}`}
                                                </span>
                                            )}
                                        </td>
                                        <td style={{ padding: '6px 8px' }}>
                                            <span style={{
                                                color: ch.old_value === null ? 'var(--color-text-dim)' : undefined,
                                            }}>
                                                {changeValueText(ch.field, ch.old_value)}
                                            </span>
                                            <span style={{ color: 'var(--color-text-muted)' }}> → </span>
                                            <span style={{
                                                fontWeight: 500,
                                                color: ch.new_value === null ? 'var(--color-text-dim)' : 'var(--color-accent)',
                                            }}>
                                                {changeValueText(ch.field, ch.new_value)}
                                            </span>
                                        </td>
                                        <td style={{ padding: '6px 8px' }}>
                                            <span className="badge badge-secondary" style={{ fontSize: 10 }}>
                                                {sourceLabel}
                                            </span>
                                        </td>
                                        <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}>
                                            {formatDateTime(ch.created_at)}
                                        </td>
                                        <td style={{ padding: '6px 8px', textAlign: 'center' }}>
                                            <button
                                                className="btn btn-secondary btn-sm"
                                                onClick={() => revert(ch.id)}
                                                disabled={revertingId !== null}
                                                style={{ padding: '2px 8px', fontSize: 11 }}
                                                title="Откатить это изменение"
                                            >
                                                {revertingId === ch.id ? 'Откат…' : '↩ Откатить'}
                                            </button>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                )}
            </div>
        </details>
    );
}

// Второй уровень под строкой артикула — инлайн-аккордеон.
// Основное содержимое: кратность по каждому ФФ-складу. История снабжения —
// вложенная collapsible-секция внизу.
function SkuExpandPanel({
    sku,
    perRfEdit,
    setPerRfEdit,
    perRfSizeEdit,
    setPerRfSizeEdit,
    onSavePerRfPpb,
    onSavePerRfSize,
    onTogglePerRfUse,
    onRowUpdated,
    palletOverrides,
}: {
    sku: BoxMultiplicityRow;
    perRfEdit: Record<string, string>;
    setPerRfEdit: React.Dispatch<React.SetStateAction<Record<string, string>>>;
    perRfSizeEdit: Record<string, string>;
    setPerRfSizeEdit: React.Dispatch<React.SetStateAction<Record<string, string>>>;
    onSavePerRfPpb: (row: BoxMultiplicityRow, wh: BoxMultiplicityPerWarehouseRow) => void;
    onSavePerRfSize: (row: BoxMultiplicityRow, wh: BoxMultiplicityPerWarehouseRow) => void;
    onTogglePerRfUse: (row: BoxMultiplicityRow, wh: BoxMultiplicityPerWarehouseRow, next: boolean) => void;
    onRowUpdated: (row: BoxMultiplicityRow) => void;
    palletOverrides: Record<string, number>;
}) {
    const panelStyle: React.CSSProperties = {
        background: 'var(--color-bg)',
        padding: '14px 16px 16px 48px',
        borderTop: '1px solid var(--color-border)',
    };

    // Видны ФФ с остатком ИЛИ с заданной кратностью (машинной/ручной) — чтобы
    // машинная кратность на опустевшем складе оставалась видимой и доступной автозаполнению.
    const warehouses = sku.per_warehouse.filter(wh => wh.rf_stock > 0 || wh.box_qty !== null);

    // ─── Автозаполнение кратности других ФФ по данным машины ──────────────
    // Источник — машинные кратности (source==='machine'); цели — пустые
    // редактируемые ФФ (box_qty===null, editable). Считаем только по ФФ с
    // остатком — как и видимый список.
    const machineWarehouses = warehouses.filter(wh => wh.source === 'machine' && wh.box_qty !== null);
    const emptyTargets = warehouses.filter(wh => wh.editable && wh.box_qty === null);
    // Уникальные машинные наполнения (кратность + размер + пример № машины).
    // Ключ — qty+size: одна кратность с разными коробками = разные варианты.
    const machineOptions = useMemo(() => {
        const map = new Map<string, { qty: number; size: string | null; orderNo: string | null }>();
        for (const wh of machineWarehouses) {
            const qty = wh.box_qty as number;
            const key = `${qty}|${wh.box_size ?? ''}`;
            if (!map.has(key)) map.set(key, { qty, size: wh.box_size, orderNo: wh.machine_order_no });
        }
        return Array.from(map.values()).sort((a, b) => a.qty - b.qty);
    }, [machineWarehouses]);
    const canAutofill = machineOptions.length > 0 && emptyTargets.length > 0;

    // Ключ варианта наполнения для radio-выбора: qty + size.
    const optionKey = (opt: { qty: number; size: string | null }) => `${opt.qty}|${opt.size ?? ''}`;

    const [autofillOpen, setAutofillOpen] = useState(false);
    const [autofillKey, setAutofillKey] = useState<string | null>(null);
    const [autofillTargets, setAutofillTargets] = useState<Set<number>>(new Set());
    const [autofillApplying, setAutofillApplying] = useState(false);
    const [autofillResult, setAutofillResult] = useState<string | null>(null);

    const openAutofill = () => {
        // Дефолты: единственный вариант зафиксирован, все цели отмечены.
        setAutofillKey(machineOptions.length === 1 ? optionKey(machineOptions[0]) : null);
        setAutofillTargets(new Set(emptyTargets.map(wh => wh.warehouse_id)));
        setAutofillResult(null);
        setAutofillOpen(true);
    };

    const closeAutofill = () => {
        setAutofillOpen(false);
        setAutofillResult(null);
    };

    const toggleAutofillTarget = (warehouseId: number) => {
        setAutofillTargets(prev => {
            const next = new Set(prev);
            if (next.has(warehouseId)) next.delete(warehouseId);
            else next.add(warehouseId);
            return next;
        });
    };

    const applyAutofill = async () => {
        const selected = machineOptions.find(opt => optionKey(opt) === autofillKey);
        if (!selected || autofillTargets.size === 0) return;
        setAutofillApplying(true);
        try {
            // Копируем и кратность, и размер выбранного машинного наполнения.
            const items: BoxMultiplicityBulkItem[] = Array.from(autofillTargets).map(warehouseId => ({
                barcode: sku.barcode,
                warehouse_id: warehouseId,
                box_qty_override: selected.qty,
                box_size: selected.size,
            }));
            const resp = await api.bulkBoxMultiplicity(items);
            const updated = resp.updated.find(r => r.nm_id === sku.nm_id);
            if (updated) onRowUpdated(updated);
            const sizePart = selected.size ? ` · ${selected.size}` : '';
            setAutofillResult(
                `Заполнено ${autofillTargets.size} ФФ — ${selected.qty} шт${sizePart}`,
            );
            // Свернуть форму, оставив краткий фидбек.
            setAutofillOpen(false);
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка автозаполнения');
        } finally {
            setAutofillApplying(false);
        }
    };

    return (
        <div style={panelStyle}>
            <div style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                gap: 12, marginBottom: 8, flexWrap: 'wrap',
            }}>
                <div style={{
                    fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)',
                    textTransform: 'uppercase', letterSpacing: 0.05,
                }}>
                    Кратность по ФФ-складам · {warehouses.length}
                </div>
                {canAutofill && !autofillOpen && (
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={openAutofill}
                        style={{ fontSize: 11 }}
                        title="Проставить кратность машины на пустые редактируемые ФФ-склады"
                    >
                        ✨ Заполнить по машине ({emptyTargets.length})
                    </button>
                )}
            </div>

            {autofillResult && (
                <div style={{
                    marginBottom: 8, padding: '8px 12px', borderRadius: 8,
                    background: 'rgba(52, 199, 89, 0.08)',
                    borderLeft: '3px solid var(--color-success)', fontSize: 12,
                }}>
                    ✅ {autofillResult}
                </div>
            )}

            {autofillOpen && (
                <div style={{
                    marginBottom: 10, padding: 12, borderRadius: 8,
                    background: 'var(--color-bg-card)', border: '1px solid var(--color-border)',
                }}>
                    <div style={{
                        fontSize: 11, fontWeight: 600, color: 'var(--color-text-muted)',
                        textTransform: 'uppercase', letterSpacing: 0.05, marginBottom: 8,
                    }}>
                        ✨ Заполнить кратность по машине
                    </div>

                    {/* Шаг 1: выбор машинного наполнения (кратность + размер) */}
                    <div style={{ marginBottom: 10 }}>
                        <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 4 }}>Наполнение машины</div>
                        {machineOptions.length === 1 ? (
                            <div style={{ fontSize: 13 }}>
                                <strong style={{ color: 'var(--color-accent)' }}>
                                    {formatNumber(machineOptions[0].qty, 0)} шт
                                </strong>
                                {machineOptions[0].size && (
                                    <span style={{ color: 'var(--color-text)', marginLeft: 6 }}>
                                        · {machineOptions[0].size}
                                    </span>
                                )}
                                {machineOptions[0].orderNo && (
                                    <span style={{ color: 'var(--color-text-muted)', marginLeft: 6 }}>
                                        — машина {machineOptions[0].orderNo}
                                    </span>
                                )}
                            </div>
                        ) : (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                {machineOptions.map(opt => {
                                    const key = optionKey(opt);
                                    return (
                                        <label
                                            key={key}
                                            style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}
                                        >
                                            <input
                                                type="radio"
                                                name={`autofill-qty-${sku.nm_id}`}
                                                checked={autofillKey === key}
                                                onChange={() => setAutofillKey(key)}
                                                style={{ cursor: 'pointer' }}
                                            />
                                            <strong style={{ color: 'var(--color-accent)' }}>{formatNumber(opt.qty, 0)} шт</strong>
                                            {opt.size && (
                                                <span style={{ color: 'var(--color-text)' }}>· {opt.size}</span>
                                            )}
                                            {opt.orderNo && (
                                                <span style={{ color: 'var(--color-text-muted)' }}>— машина {opt.orderNo}</span>
                                            )}
                                        </label>
                                    );
                                })}
                            </div>
                        )}
                    </div>

                    {/* Шаг 2: выбор целевых ФФ */}
                    <div style={{ marginBottom: 10 }}>
                        <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 4 }}>
                            Целевые ФФ-склады (пустые) · {autofillTargets.size} из {emptyTargets.length}
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                            {emptyTargets.map(wh => (
                                <label
                                    key={wh.warehouse_id}
                                    style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}
                                >
                                    <input
                                        type="checkbox"
                                        checked={autofillTargets.has(wh.warehouse_id)}
                                        onChange={() => toggleAutofillTarget(wh.warehouse_id)}
                                        style={{ cursor: 'pointer' }}
                                    />
                                    <span style={{ fontWeight: 500 }}>{wh.warehouse_name}</span>
                                    <span style={{ color: 'var(--color-text-muted)', fontSize: 12 }}>
                                        · сток {formatNumber(wh.rf_stock, 0)}
                                    </span>
                                </label>
                            ))}
                        </div>
                    </div>

                    <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', alignItems: 'center' }}>
                        <span style={{ marginRight: 'auto', fontSize: 11, color: 'var(--color-text-muted)' }}>
                            Заполнятся только пустые ФФ — машинные и ручные не затрагиваются.
                        </span>
                        <button
                            className="btn btn-secondary btn-sm"
                            onClick={closeAutofill}
                            disabled={autofillApplying}
                        >Отмена</button>
                        <button
                            className="btn btn-primary btn-sm"
                            onClick={applyAutofill}
                            disabled={autofillApplying || autofillKey === null || autofillTargets.size === 0}
                        >
                            {autofillApplying ? 'Применяю…' : `Применить (${autofillTargets.size})`}
                        </button>
                    </div>
                </div>
            )}

            {warehouses.length === 0 ? (
                <div style={{ color: 'var(--color-text-muted)', fontSize: 13 }}>
                    Нет ФФ-складов с остатком или кратностью.
                </div>
            ) : (
                <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                    <thead>
                        <tr style={{ color: 'var(--color-text-muted)', fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.05 }}>
                            <th style={{ textAlign: 'left', padding: '4px 8px' }}>ФФ-склад</th>
                            <th style={{ textAlign: 'right', padding: '4px 8px', width: 80 }}>Сток</th>
                            <th style={{ textAlign: 'left', padding: '4px 8px' }}>Кратность</th>
                            <th style={{ textAlign: 'left', padding: '4px 8px', width: 110 }}>Размер</th>
                            <th style={{ textAlign: 'right', padding: '4px 8px', width: 110 }}>Коробов</th>
                            <th style={{ textAlign: 'right', padding: '4px 8px', width: 130 }}>Паллета</th>
                            <th style={{ textAlign: 'center', padding: '4px 8px', width: 90 }}>Учитывать</th>
                        </tr>
                    </thead>
                    <tbody>
                        {warehouses.map(wh => {
                            const editKey = `${sku.nm_id}:${wh.warehouse_id}`;
                            const editVal = perRfEdit[editKey];
                            const showEdit = editVal !== undefined;
                            const sizeEditVal = perRfSizeEdit[editKey];
                            const showSizeEdit = sizeEditVal !== undefined;
                            // Коробов на этом ФФ = сток / кратность (целых + остаток).
                            const ppb = wh.box_qty;
                            const fullBoxes = ppb && ppb > 0 ? Math.floor(wh.rf_stock / ppb) : null;
                            const remPcs = ppb && ppb > 0 ? wh.rf_stock % ppb : 0;
                            // Паллета из габаритов коробки (евро 120×80 + лимит высоты склада).
                            // Вес НЕ учитываем (по требованию) — только геометрия.
                            const palDims = parseBoxSize(wh.box_size);
                            const whMaxHcm = maxPalletHeightCm(wh.warehouse_name);
                            // override по размеру (если задан на странице pallet-sizes) перебивает геометрию.
                            const boxesPerPal = effectiveBoxesPerPallet(wh.box_size, whMaxHcm, palletOverrides);
                            const palletsNow = boxesPerPal && fullBoxes ? palletsForBoxes(fullBoxes, boxesPerPal) : 0;
                            return (
                                <tr key={wh.warehouse_id} style={{ borderTop: '1px solid var(--color-border)' }}>
                                    <td style={{ padding: '6px 8px', fontWeight: 500 }}>{wh.warehouse_name}</td>
                                    <td style={{
                                        padding: '6px 8px', textAlign: 'right',
                                        color: wh.rf_stock > 0 ? 'var(--color-success)' : 'var(--color-text-dim)',
                                    }}>
                                        {wh.rf_stock > 0 ? formatNumber(wh.rf_stock, 0) : '—'}
                                    </td>
                                    <td style={{ padding: '6px 8px' }}>
                                        {showEdit && wh.editable ? (
                                            <div style={{ display: 'flex', gap: 3, alignItems: 'center' }}>
                                                <input
                                                    type="text"
                                                    inputMode="numeric"
                                                    value={editVal}
                                                    onChange={e => setPerRfEdit(p => ({ ...p, [editKey]: e.target.value }))}
                                                    onKeyDown={e => {
                                                        if (e.key === 'Enter') onSavePerRfPpb(sku, wh);
                                                        else if (e.key === 'Escape') {
                                                            setPerRfEdit(p => {
                                                                const next = { ...p };
                                                                delete next[editKey];
                                                                return next;
                                                            });
                                                        }
                                                    }}
                                                    placeholder="—"
                                                    autoFocus
                                                    style={{
                                                        width: 100, padding: '3px 6px', fontSize: 12,
                                                        border: '1px solid var(--color-border)', borderRadius: 4,
                                                    }}
                                                />
                                                <button
                                                    className="btn btn-success btn-sm"
                                                    onClick={() => onSavePerRfPpb(sku, wh)}
                                                    style={{ padding: '2px 6px', fontSize: 11 }}
                                                    title="Сохранить (Enter)"
                                                >✓</button>
                                                <button
                                                    className="btn btn-secondary btn-sm"
                                                    onClick={() => setPerRfEdit(p => {
                                                        const next = { ...p };
                                                        delete next[editKey];
                                                        return next;
                                                    })}
                                                    style={{ padding: '2px 6px', fontSize: 11 }}
                                                    title="Отмена (Esc)"
                                                >✕</button>
                                            </div>
                                        ) : (
                                            <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                                                <span style={{
                                                    color: wh.box_qty !== null ? 'var(--color-accent)' : 'var(--color-text-dim)',
                                                    fontWeight: wh.box_qty !== null ? 600 : 400,
                                                }}>
                                                    {wh.box_qty !== null ? `${formatNumber(wh.box_qty, 0)} шт` : 'не задана'}
                                                </span>
                                                <SourceBadge wh={wh} />
                                                {wh.editable ? (
                                                    <button
                                                        className="btn btn-secondary btn-sm"
                                                        onClick={() => setPerRfEdit(p => ({
                                                            ...p, [editKey]: wh.box_qty !== null ? String(wh.box_qty) : '',
                                                        }))}
                                                        style={{ padding: '2px 6px', fontSize: 11 }}
                                                        title={wh.box_qty !== null ? 'Изменить кратность ФФ' : 'Задать кратность ФФ'}
                                                    >
                                                        {wh.box_qty !== null ? '✏️' : '➕'}
                                                    </button>
                                                ) : (
                                                    <span
                                                        style={{ fontSize: 14 }}
                                                        title="Заблокировано — кратность из принятой машины"
                                                    >🔒</span>
                                                )}
                                            </div>
                                        )}
                                    </td>
                                    <td style={{ padding: '6px 8px' }}>
                                        {showSizeEdit && wh.editable ? (
                                            <div style={{ display: 'flex', gap: 3, alignItems: 'center' }}>
                                                <input
                                                    type="text"
                                                    value={sizeEditVal}
                                                    onChange={e => setPerRfSizeEdit(p => ({ ...p, [editKey]: e.target.value }))}
                                                    onKeyDown={e => {
                                                        if (e.key === 'Enter') onSavePerRfSize(sku, wh);
                                                        else if (e.key === 'Escape') {
                                                            setPerRfSizeEdit(p => {
                                                                const next = { ...p };
                                                                delete next[editKey];
                                                                return next;
                                                            });
                                                        }
                                                    }}
                                                    placeholder="напр. 60×40×50"
                                                    autoFocus
                                                    style={{
                                                        width: 110, padding: '3px 6px', fontSize: 12,
                                                        border: '1px solid var(--color-border)', borderRadius: 4,
                                                    }}
                                                />
                                                <button
                                                    className="btn btn-success btn-sm"
                                                    onClick={() => onSavePerRfSize(sku, wh)}
                                                    style={{ padding: '2px 6px', fontSize: 11 }}
                                                    title="Сохранить (Enter)"
                                                >✓</button>
                                                <button
                                                    className="btn btn-secondary btn-sm"
                                                    onClick={() => setPerRfSizeEdit(p => {
                                                        const next = { ...p };
                                                        delete next[editKey];
                                                        return next;
                                                    })}
                                                    style={{ padding: '2px 6px', fontSize: 11 }}
                                                    title="Отмена (Esc)"
                                                >✕</button>
                                            </div>
                                        ) : (
                                            <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                                                <span style={{ color: wh.box_size ? undefined : 'var(--color-text-dim)' }}>
                                                    {wh.box_size || '—'}
                                                </span>
                                                {wh.editable ? (
                                                    <button
                                                        className="btn btn-secondary btn-sm"
                                                        onClick={() => setPerRfSizeEdit(p => ({
                                                            ...p, [editKey]: wh.box_size ?? '',
                                                        }))}
                                                        style={{ padding: '2px 6px', fontSize: 11 }}
                                                        title={wh.box_size ? 'Изменить размер коробки ФФ' : 'Задать размер коробки ФФ'}
                                                    >
                                                        {wh.box_size ? '✏️' : '➕'}
                                                    </button>
                                                ) : (
                                                    <span
                                                        style={{ fontSize: 14 }}
                                                        title="Заблокировано — размер из принятой машины"
                                                    >🔒</span>
                                                )}
                                            </div>
                                        )}
                                    </td>
                                    <td style={{ padding: '6px 8px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                                        {fullBoxes !== null ? (
                                            <>
                                                <strong style={{ color: fullBoxes === 0 ? 'var(--color-warning)' : undefined }}>
                                                    {formatNumber(fullBoxes, 0)}
                                                </strong>
                                                <span style={{ color: 'var(--color-text-muted)' }}> кор</span>
                                                {remPcs > 0 && (
                                                    <span
                                                        style={{ color: 'var(--color-warning)' }}
                                                        title="Неполная коробка — остаток не кратен"
                                                    > +{formatNumber(remPcs, 0)} шт</span>
                                                )}
                                            </>
                                        ) : (
                                            <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                                        )}
                                    </td>
                                    <td style={{ padding: '6px 8px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                                        {boxesPerPal !== null ? (
                                            <span title={`${boxesPerPal} коробок на евро-паллету (120×80, высота склада ${whMaxHcm / 100} м)${ppb ? `; ${formatNumber(boxesPerPal * ppb, 0)} шт/паллету` : ''}`}>
                                                <strong>{formatNumber(boxesPerPal, 0)}</strong>
                                                <span style={{ color: 'var(--color-text-muted)' }}> кор/пал</span>
                                                {palletsNow > 0 && (
                                                    <span style={{ color: 'var(--color-accent)' }}> · ≈{formatNumber(palletsNow, 0)} пал</span>
                                                )}
                                            </span>
                                        ) : palDims ? (
                                            <span
                                                style={{ color: 'var(--color-warning)' }}
                                                title="Коробка крупногабаритная или выше лимита высоты склада — на паллету коробами не уложить (монопаллета)"
                                            >⚠️ крупногабарит</span>
                                        ) : ppb && ppb > 0 ? (
                                            <span
                                                style={{ color: 'var(--color-warning)' }}
                                                title="Нет габаритов коробки — паллету не сформировать. Задайте размер в колонке «Размер»."
                                            >⚠️ нет габаритов</span>
                                        ) : (
                                            <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                                        )}
                                    </td>
                                    <td style={{ padding: '6px 8px', textAlign: 'center' }}>
                                        {/* Per-ФФ тоггл разрешён даже на машинных ФФ */}
                                        <input
                                            type="checkbox"
                                            checked={wh.use_box_multiplicity}
                                            onChange={e => onTogglePerRfUse(sku, wh, e.target.checked)}
                                            title={wh.use_box_multiplicity
                                                ? 'Кратность учитывается при сборке с этого ФФ'
                                                : 'Кратность игнорируется — без округления'}
                                            style={{ width: 14, height: 14, cursor: 'pointer' }}
                                        />
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            )}

            <SkuSourcesSection barcode={sku.barcode} />
            <SkuChangesSection barcode={sku.barcode} onRowUpdated={onRowUpdated} />
        </div>
    );
}

// Размеры коробки SKU по ФФ-складам. У одного товара на разных ФФ коробки могут
// отличаться → собираем все уникальные размеры + раскладку «склад: размер».
function skuBoxSizeInfo(row: BoxMultiplicityRow): { sizes: string[]; perFf: Array<{ wh: string; size: string }> } {
    const perFf = row.per_warehouse
        .filter(p => p.box_size && p.box_size.trim())
        .map(p => ({ wh: p.warehouse_name, size: (p.box_size as string).trim() }));
    // Уникальные по КАНОНУ: разные написания одного размера («60x40xc40» и
    // «60*40*40») = один размер, не «разные». Показываем по представителю на канон.
    const byCanon = new Map<string, string>();
    for (const p of perFf) {
        const c = canonBoxSize(p.size);
        if (!byCanon.has(c)) byCanon.set(c, p.size);
    }
    return { sizes: Array.from(byCanon.values()), perFf };
}

export function BoxMultiplicityView({ onSaved }: { onSaved?: () => void } = {}) {
    const { slug } = useParams() as { slug: string };
    const [rows, setRows] = useState<BoxMultiplicityRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    // Ручной override «коробок на паллету» по размеру (страница pallet-sizes) —
    // перебивает геометрию в колонке «Паллета».
    const [palletOverrides, setPalletOverrides] = useState<Record<string, number>>({});

    // Per-ФФ inline edit кратности: "{nm_id}:{warehouse_id}" → string value
    const [perRfEdit, setPerRfEdit] = useState<Record<string, string>>({});
    // Per-ФФ inline edit размера: "{nm_id}:{warehouse_id}" → string value
    const [perRfSizeEdit, setPerRfSizeEdit] = useState<Record<string, string>>({});

    // Filters
    const [brandFilter, setBrandFilter] = useState('');
    const [subjectFilter, setSubjectFilter] = useState('');
    const [stockFilter, setStockFilter] = useState<StockFilter>('all');
    const [search, setSearch] = useState('');

    // «Липкие» строки: только что отредактированные не выпадают из активного фильтра сразу
    // после сохранения (иначе, задав кратность в «Нет кратности», строка исчезает и нельзя
    // дозадать размер коробки). Сбрасывается при смене chip-фильтра.
    const [stickyNmIds, setStickyNmIds] = useState<ReadonlySet<number>>(new Set());
    useEffect(() => { setStickyNmIds(new Set()); }, [stockFilter]);
    const markSticky = useCallback((nmId: number) => {
        setStickyNmIds(prev => new Set(prev).add(nmId));
    }, []);

    // Bulk-paste inline editor — 5+ редактируемых строк. Колонки:
    //   barcode  — ключ (обязателен)
    //   warehouse — имя ФФ (опционально, пусто = SKU-уровень)
    //   ppb      — кратность (число, «-»/«0» = очистка, пусто = не трогать)
    //   box_size — размер коробки (только при заданном warehouse)
    // Флаг «учитывать» при apply всегда выставляется в true (см. applyPaste).
    type PasteRow = { barcode: string; warehouse: string; ppb: string; box_size: string };
    const emptyPasteRow = (): PasteRow => ({ barcode: '', warehouse: '', ppb: '', box_size: '' });
    const isPasteRowFilled = (r: PasteRow): boolean =>
        !!(r.barcode.trim() || r.warehouse.trim() || r.ppb.trim() || r.box_size.trim());
    const [pasteOpen, setPasteOpen] = useState(false);
    const [pasteRows, setPasteRows] = useState<PasteRow[]>(() => Array.from({ length: 5 }, emptyPasteRow));
    // Raw-режим — для расширенного шаблона (header + per-ФФ + box_size), который
    // нельзя уместить в 3-колоночный inline-редактор.
    const [pasteRawText, setPasteRawText] = useState<string>('');
    const [pasteApplying, setPasteApplying] = useState(false);
    const [pasteResult, setPasteResult] = useState<{
        matched: number; updated: number; notFound: string[]; locked: string[];
        batchId: string | null;          // null = ничего не изменилось, кнопка отката недоступна
        reverted?: { count: number; locked: string[] }; // заполняется после успешного отката
    } | null>(null);
    const [batchReverting, setBatchReverting] = useState(false);

    // Глобальный журнал bulk-вставок проекта (toggle-панель).
    const [historyOpen, setHistoryOpen] = useState(false);
    const [batches, setBatches] = useState<BoxMultiplicityBatchSummary[] | null>(null);
    const [batchesLoading, setBatchesLoading] = useState(false);
    const [revertingBatchId, setRevertingBatchId] = useState<string | null>(null);

    const load = useCallback(async () => {
        try {
            setLoading(true);
            setError(null);
            const resp = await api.getBoxMultiplicity();
            setRows(resp.items);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    // Ручной override «коробок на паллету» по размеру коробки (best-effort).
    useEffect(() => {
        let cancelled = false;
        api.getPalletBoxesBySize()
            .then(ov => { if (!cancelled) setPalletOverrides(ov || {}); })
            .catch(() => { /* нет override → геометрия */ });
        return () => { cancelled = true; };
    }, []);

    // ─── Карта имени ФФ → ID (для резолва per-ФФ paste) ────────────────────
    const warehouseMap = useMemo(() => {
        const map = new Map<string, number>();
        for (const r of rows) {
            for (const p of r.per_warehouse) {
                if (!map.has(p.warehouse_name)) map.set(p.warehouse_name, p.warehouse_id);
                const lower = p.warehouse_name.toLowerCase();
                if (!map.has(lower)) map.set(lower, p.warehouse_id);
            }
        }
        return map;
    }, [rows]);

    // ─── Bulk paste (inline editor, не модалка) ───────────────────────────
    // Источник TSV: либо raw-буфер (расширенный header-формат), либо inline-строки.
    // Если хоть одна строка использует warehouse/box_size — шлём header-режим
    // (парсер тогда распознает per-ФФ + box_size); иначе legacy 3-колоночный.
    const pasteText = useMemo(() => {
        if (pasteRawText.trim()) return pasteRawText;
        const filled = pasteRows.filter(isPasteRowFilled);
        if (filled.length === 0) return '';
        const usesExtended = filled.some(r => r.warehouse.trim() || r.box_size.trim());
        if (usesExtended) {
            const header = 'Barcode\tСклад\tКратность по ФФ\tРазмер коробки';
            const body = filled.map(r => `${r.barcode}\t${r.warehouse}\t${r.ppb}\t${r.box_size}`);
            return [header, ...body].join('\n');
        }
        return filled.map(r => `${r.barcode}\t${r.ppb}`).join('\n');
    }, [pasteRows, pasteRawText]);
    const parsedPaste = useMemo(() => parseBoxMultiplicityPaste(pasteText, warehouseMap), [pasteText, warehouseMap]);

    const openPaste = () => {
        setPasteOpen(true);
        setPasteRows(Array.from({ length: 5 }, emptyPasteRow));
        setPasteRawText('');
        setPasteResult(null);
    };

    const closePaste = () => {
        setPasteOpen(false);
        setPasteRows(Array.from({ length: 5 }, emptyPasteRow));
        setPasteRawText('');
        setPasteResult(null);
    };

    const updatePasteRow = (idx: number, field: keyof PasteRow, value: string) => {
        setPasteRows(prev => {
            const next = [...prev];
            next[idx] = { ...next[idx], [field]: value };
            // Авто-добавляем буферные строки если последняя строка тронута.
            if (isPasteRowFilled(next[next.length - 1])) {
                next.push(emptyPasteRow());
            }
            return next;
        });
    };

    // Excel-paste: TSV в первую пустую строку → распарсить и заполнить начиная отсюда.
    // Если в первой строке детектится header расширенного шаблона — кидаем в raw-буфер
    // (3-колоночный inline-редактор не умеет per-ФФ + box_size).
    const handleRowsPaste = (e: React.ClipboardEvent, startIdx: number) => {
        const text = e.clipboardData.getData('text/plain');
        if (!text.includes('\t') && !text.includes('\n')) return;
        e.preventDefault();
        const lines = text.replace(/\r\n/g, '\n').split('\n').filter(l => l.length > 0);
        if (lines.length === 0) return;

        // Header-детект — только когда первая строка содержит ключевые слова.
        // Если детект сработал → raw-режим, парсер сам распознает по header.
        const firstCols = lines[0].split('\t').map(c => c.trim().toLowerCase());
        const looksLikeHeader = firstCols.some(c =>
            c.includes('склад') || c.includes('размер') || c === 'nm_id' || c.includes('артикул'),
        );
        if (looksLikeHeader) {
            setPasteRawText(text);
            return;
        }

        // Детектим формат по содержимому первой строки. Возможные раскладки
        // (после удаления колонки «Учитывать» из UI):
        //   • Long template (6 кол.): Артикул, Barcode, nm_id, Склад, Кратность, Размер
        //   • Short (≥4 кол.):        Barcode, Склад, Кратность, Размер
        //   • Legacy (≤3 кол.):       Barcode, Кратность
        // Эвристика: если col[1] — длинная цифровая строка (12+), значит первая
        // колонка артикул, второй — barcode (long template). Иначе короткий формат.
        const isLongTemplate =
            firstCols.length >= 5 && /^\d{8,}$/.test((firstCols[1] || '').trim());
        const isShortPerFf = !isLongTemplate && firstCols.length >= 4;
        const parsed: PasteRow[] = lines.map(l => {
            const cols = l.split('\t');
            if (isLongTemplate) {
                // Артикул(0), Barcode(1), nm_id(2), Склад(3), Кратность(4), Размер(5)
                return {
                    barcode: (cols[1] ?? '').trim(),
                    warehouse: (cols[3] ?? '').trim(),
                    ppb: (cols[4] ?? '').trim(),
                    box_size: (cols[5] ?? '').trim(),
                };
            }
            if (isShortPerFf) {
                // Barcode(0), Склад(1), Кратность(2), Размер(3)
                return {
                    barcode: (cols[0] ?? '').trim(),
                    warehouse: (cols[1] ?? '').trim(),
                    ppb: (cols[2] ?? '').trim(),
                    box_size: (cols[3] ?? '').trim(),
                };
            }
            // Legacy ≤3 колонок: Barcode, Кратность
            return {
                barcode: (cols[0] ?? '').trim(),
                warehouse: '',
                ppb: (cols[1] ?? '').trim(),
                box_size: '',
            };
        });
        setPasteRows(prev => {
            const next = [...prev];
            // Заменяем строки начиная с startIdx
            for (let i = 0; i < parsed.length; i++) {
                next[startIdx + i] = parsed[i];
            }
            // Гарантируем 2 буферные пустые строки в конце
            while (next.length < startIdx + parsed.length + 2 ||
                   isPasteRowFilled(next[next.length - 1])) {
                next.push(emptyPasteRow());
                if (next.length > startIdx + parsed.length + 5) break;  // safety
            }
            return next;
        });
    };

    // ─── Глобальный журнал bulk-вставок ─────────────────────────────────────
    const loadBatches = useCallback(async () => {
        setBatchesLoading(true);
        try {
            const resp = await api.listBoxMultiplicityBatches(50);
            setBatches(resp.items);
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка загрузки истории');
        } finally {
            setBatchesLoading(false);
        }
    }, []);

    const toggleHistory = useCallback(() => {
        setHistoryOpen(prev => {
            const next = !prev;
            if (next && batches === null) loadBatches();
            return next;
        });
    }, [batches, loadBatches]);

    const revertBatchFromHistory = useCallback(async (batchId: string, changesCount: number) => {
        if (!confirm(`Откатить вставку (${changesCount} изменений)?`)) return;
        setRevertingBatchId(batchId);
        try {
            const resp = await api.revertBoxMultiplicityBatch(batchId);
            await load();
            await loadBatches();
            const lockedNote = resp.locked_barcodes.length > 0
                ? `\n🔒 Заблокировано машиной: ${resp.locked_barcodes.length}`
                : '';
            alert(`↩ Откачено ${resp.reverted} записей${lockedNote}`);
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка отката');
        } finally {
            setRevertingBatchId(null);
        }
    }, [load, loadBatches]);

    // Откат всей последней bulk-операции по batch_id, полученному от backend.
    const revertLastBatch = useCallback(async () => {
        if (!pasteResult?.batchId) return;
        if (!confirm(`Откатить всю последнюю вставку (${pasteResult.updated} SKU)?`)) return;
        setBatchReverting(true);
        try {
            const resp = await api.revertBoxMultiplicityBatch(pasteResult.batchId);
            // Перезагружаем таблицу — после отката изменилось много строк.
            await load();
            setPasteResult(prev => prev ? {
                ...prev,
                reverted: { count: resp.reverted, locked: resp.locked_barcodes },
            } : prev);
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка отката');
        } finally {
            setBatchReverting(false);
        }
    }, [pasteResult, load]);

    const applyPaste = async () => {
        if (parsedPaste.rows.length === 0) return;
        setPasteApplying(true);
        try {
            const items: BoxMultiplicityBulkItem[] = parsedPaste.rows.map(r => {
                // При массовой вставке всегда включаем флаг «учитывать» —
                // отдельной колонкой UI больше не управляется.
                const item: BoxMultiplicityBulkItem = { barcode: r.barcode, use_box_multiplicity: true };
                if (r.warehouse_id !== undefined) item.warehouse_id = r.warehouse_id;
                if (r.box_qty_override !== undefined) item.box_qty_override = r.box_qty_override ?? null;
                if (r.box_size !== undefined) item.box_size = r.box_size ?? null;
                return item;
            });
            const resp = await api.bulkBoxMultiplicity(items);
            if (resp.updated.length > 0) {
                const updMap = new Map(resp.updated.map(r => [r.nm_id, r]));
                setRows(prev => prev.map(r => updMap.get(r.nm_id) || r));
                for (const u of resp.updated) markSticky(u.nm_id);
                onSaved?.();
            }
            setPasteResult({
                matched: resp.matched_count,
                updated: resp.updated.length,
                notFound: resp.not_found,
                locked: resp.locked,
                batchId: resp.batch_id,
            });
            setPasteRows(Array.from({ length: 5 }, emptyPasteRow));
            setPasteRawText('');
            // Если панель истории открыта — обновим список (свежий batch появится сверху).
            if (historyOpen) loadBatches(); else setBatches(null);
        } catch (e: unknown) {
            alert(e instanceof Error ? e.message : 'Ошибка применения');
        } finally {
            setPasteApplying(false);
        }
    };

    // ─── Per-ФФ handlers ───────────────────────────────────────────────────
    const savePerRfPpb = async (row: BoxMultiplicityRow, wh: BoxMultiplicityPerWarehouseRow) => {
        const key = `${row.nm_id}:${wh.warehouse_id}`;
        const trimmed = (perRfEdit[key] ?? '').trim();
        const value = trimmed === '' ? null : parseInt(trimmed, 10);
        if (value !== null && (Number.isNaN(value) || value < 1)) {
            alert('Кратность должна быть положительным числом или пустой');
            return;
        }
        try {
            const updated = await api.patchPerWarehouseBoxMultiplicity(
                row.barcode, wh.warehouse_id, { box_qty: value },
            );
            setRows(prev => prev.map(r => r.nm_id === row.nm_id ? updated : r));
            markSticky(row.nm_id);
            onSaved?.();
            setPerRfEdit(prev => {
                const next = { ...prev };
                delete next[key];
                return next;
            });
        } catch (e: unknown) {
            // 409 — ФФ машинно-заблокирован
            alert(e instanceof Error ? e.message : 'Ошибка сохранения');
        }
    };

    const savePerRfSize = async (row: BoxMultiplicityRow, wh: BoxMultiplicityPerWarehouseRow) => {
        const key = `${row.nm_id}:${wh.warehouse_id}`;
        const trimmed = (perRfSizeEdit[key] ?? '').trim();
        const value = trimmed === '' ? null : trimmed;
        try {
            const updated = await api.patchPerWarehouseBoxMultiplicity(
                row.barcode, wh.warehouse_id, { box_size: value },
            );
            setRows(prev => prev.map(r => r.nm_id === row.nm_id ? updated : r));
            markSticky(row.nm_id);
            onSaved?.();
            setPerRfSizeEdit(prev => {
                const next = { ...prev };
                delete next[key];
                return next;
            });
        } catch (e: unknown) {
            // 409 — ФФ машинно-заблокирован
            alert(e instanceof Error ? e.message : 'Ошибка сохранения');
        }
    };

    const togglePerRfUse = async (row: BoxMultiplicityRow, wh: BoxMultiplicityPerWarehouseRow, next: boolean) => {
        // Optimistic
        setRows(prev => prev.map(r => {
            if (r.nm_id !== row.nm_id) return r;
            return {
                ...r,
                per_warehouse: r.per_warehouse.map(p =>
                    p.warehouse_id === wh.warehouse_id ? { ...p, use_box_multiplicity: next } : p,
                ),
            };
        }));
        markSticky(row.nm_id);
        try {
            const updated = await api.patchPerWarehouseBoxMultiplicity(
                row.barcode, wh.warehouse_id, { use_box_multiplicity: next },
            );
            setRows(prev => prev.map(r => r.nm_id === row.nm_id ? updated : r));
            onSaved?.();
        } catch (e: unknown) {
            // rollback
            setRows(prev => prev.map(r => {
                if (r.nm_id !== row.nm_id) return r;
                return {
                    ...r,
                    per_warehouse: r.per_warehouse.map(p =>
                        p.warehouse_id === wh.warehouse_id ? { ...p, use_box_multiplicity: !next } : p,
                    ),
                };
            }));
            alert(e instanceof Error ? e.message : 'Ошибка сохранения');
        }
    };

    const toggleUse = async (nmId: number, next: boolean) => {
        // Optimistic flip — откат при ошибке
        setRows(prev => prev.map(r => r.nm_id === nmId ? { ...r, use_box_multiplicity: next } : r));
        markSticky(nmId);
        try {
            const updated = await api.patchBoxMultiplicity(nmId, { use_box_multiplicity: next });
            setRows(prev => prev.map(r => r.nm_id === nmId ? updated : r));
            onSaved?.();
        } catch (e: unknown) {
            setRows(prev => prev.map(r => r.nm_id === nmId ? { ...r, use_box_multiplicity: !next } : r));
            alert(e instanceof Error ? e.message : 'Ошибка сохранения');
        }
    };

    // ─── Filter options (учитывают друг друга — если выбран предмет, бренды
    // сужаются до тех что встречаются в этом предмете и наоборот) ──────────
    const brandOptions = useMemo(() => {
        const set = new Set<string>();
        for (const r of rows) {
            if (subjectFilter && r.subject !== subjectFilter) continue;
            if (r.brand) set.add(r.brand);
        }
        return Array.from(set).sort();
    }, [rows, subjectFilter]);

    const subjectOptions = useMemo(() => {
        const set = new Set<string>();
        for (const r of rows) {
            if (brandFilter && r.brand !== brandFilter) continue;
            if (r.subject) set.add(r.subject);
        }
        return Array.from(set).sort();
    }, [rows, brandFilter]);

    // ─── Filtered rows ─────────────────────────────────────────────────────
    const filteredRows = useMemo(() => {
        const q = search.trim().toLowerCase();
        return rows.filter(r => {
            // Бренд/предмет/поиск — жёсткие гейты (липкость их не обходит).
            if (brandFilter && r.brand !== brandFilter) return false;
            if (subjectFilter && r.subject !== subjectFilter) return false;
            if (q) {
                const haystack = `${r.vendor_code || ''} ${r.barcode} ${r.nm_id}`.toLowerCase();
                if (!haystack.includes(q)) return false;
            }
            // Chip-фильтр остатков ЛИБО «липкая» строка (только что отредактирована).
            return passesRowFilter(r, stockFilter, stickyNmIds);
        });
    }, [rows, brandFilter, subjectFilter, stockFilter, search, stickyNmIds]);

    // KPI и chip-каунты — по scope бренд+предмет (без чипов остатков и без search),
    // чтобы при выборе предмета шапка показывала числа в рамках этого предмета.
    const scopeRows = useMemo(() => rows.filter(r => {
        if (brandFilter && r.brand !== brandFilter) return false;
        if (subjectFilter && r.subject !== subjectFilter) return false;
        return true;
    }), [rows, brandFilter, subjectFilter]);

    const stats = useMemo(() => {
        const total = scopeRows.length;
        const filterCounts = {
            // «Есть на ФФ» — SKU с остатком на ФФ-складе.
            rf: scopeRows.filter(r => r.rf_stock > 0).length,
            // «Частичная кратность» — у части ФФ с остатком кратность есть, у части — нет.
            partial: scopeRows.filter(skuHasPartialBoxQty).length,
            // «Разные кратности» — у одного товара возможно несколько разных кратностей (между ФФ или внутри одного ФФ).
            mixed: scopeRows.filter(skuHasMixedBoxQty).length,
            // «Нет кратности» — есть остаток на ФФ И кратность не задана.
            no_box_qty: scopeRows.filter(r => r.rf_stock > 0 && !skuHasBoxQty(r)).length,
            // «Нет габаритов» — кратность есть, но размер коробки не задан → паллету не сформировать.
            no_pallet: scopeRows.filter(r => r.rf_stock > 0 && skuHasBoxQty(r) && !skuCanPalletize(r)).length,
        };
        return { total, filterCounts };
    }, [scopeRows]);

    // ─── Per-ФФ строки для второго листа Excel (шаблон для вставки) ────────
    // Только редактируемые (не machine-locked) ФФ с остатком или заданной кратностью.
    const perWarehouseExportRows = useMemo(() => {
        const out: Array<{ article: string; barcode: string; nm_id: number; warehouse: string; box_qty: number | ''; box_size: string }> = [];
        for (const r of filteredRows) {
            for (const p of r.per_warehouse) {
                if (!p.editable) continue;                       // machine-locked — пропускаем
                if (p.rf_stock <= 0 && p.box_qty === null) continue;
                out.push({
                    article: r.vendor_code || '',
                    barcode: r.barcode,
                    nm_id: r.nm_id,
                    warehouse: p.warehouse_name,
                    box_qty: p.box_qty ?? '',
                    box_size: p.box_size ?? '',
                });
            }
        }
        return out;
    }, [filteredRows]);

    const columns: Column[] = [
        {
            key: 'vendor_code',
            label: 'Артикул',
            width: '240px',
            render: (_v: unknown, r: BoxMultiplicityRow) => (
                <span style={{ wordBreak: 'break-word', whiteSpace: 'normal', display: 'inline-block', maxWidth: 230 }}>
                    {r.vendor_code || <span style={{ color: 'var(--color-text-dim)' }}>—</span>}
                </span>
            ),
        },
        {
            key: 'barcode',
            label: 'Barcode',
            width: '140px',
            render: (_v: unknown, r: BoxMultiplicityRow) => (
                <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{r.barcode}</span>
            ),
        },
        {
            key: 'nm_id',
            label: 'nm_id',
            align: 'right',
            width: '110px',
            // НЕ format: 'number' — иначе ru-RU локаль рендерит ID с пробелами/запятой
            render: (_v: unknown, r: BoxMultiplicityRow) => (
                <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{r.nm_id}</span>
            ),
            exportValue: (r: BoxMultiplicityRow) => r.nm_id,
        },
        { key: 'brand', label: 'Бренд', width: '120px' },
        { key: 'subject', label: 'Предмет', width: '140px' },
        {
            key: 'rf_stock',
            label: 'Остатки',
            align: 'right',
            width: '180px',
            getValue: (r: BoxMultiplicityRow) => r.rf_stock + r.wb_stock + r.in_assembly + r.in_transit,
            render: (_v: unknown, r: BoxMultiplicityRow) => {
                const parts: Array<{ label: string; value: number; color: string }> = [];
                if (r.rf_stock > 0) parts.push({ label: 'ФФ', value: r.rf_stock, color: 'var(--color-success)' });
                if (r.in_assembly > 0) parts.push({ label: 'сборка', value: r.in_assembly, color: 'var(--color-warning)' });
                if (r.in_transit > 0) parts.push({ label: 'путь', value: r.in_transit, color: 'var(--color-accent)' });
                if (r.wb_stock > 0) parts.push({ label: 'WB', value: r.wb_stock, color: 'var(--color-text)' });
                if (parts.length === 0) {
                    return <span style={{ color: 'var(--color-text-dim)', fontSize: 12 }}>—</span>;
                }
                return (
                    <div style={{ fontSize: 12, lineHeight: 1.4 }}>
                        {parts.map(p => (
                            <div key={p.label} style={{ color: p.color }}>
                                {p.label}: <strong>{formatNumber(p.value, 0)}</strong>
                            </div>
                        ))}
                    </div>
                );
            },
            exportValue: (r: BoxMultiplicityRow) =>
                `ФФ:${r.rf_stock} | сборка:${r.in_assembly} | путь:${r.in_transit} | WB:${r.wb_stock}`,
        },
        {
            key: 'per_warehouse',
            label: 'Кратность по ФФ',
            width: '240px',
            // Сводка — раскрытие per-ФФ панели делает встроенный expander таблицы (стрелка слева).
            getValue: (r: BoxMultiplicityRow) =>
                r.per_warehouse.filter(p => p.rf_stock > 0 || p.box_qty !== null).length,
            render: (_v: unknown, r: BoxMultiplicityRow) => {
                // Сводка — только по ФФ с остатком (как и развёрнутый список).
                const whs = r.per_warehouse.filter(p => p.rf_stock > 0 || p.box_qty !== null);
                const withQty = whs.filter(p => p.box_qty !== null).length;
                const machineCount = whs.filter(p => p.source === 'machine').length;
                const manualCount = whs.filter(p => p.source === 'manual').length;
                return (
                    <div
                        style={{ fontSize: 12, color: 'var(--color-text-muted)' }}
                        title="Разверните строку (стрелка слева) — настройка кратности по каждому ФФ-складу"
                    >
                        🛠 {whs.length} ФФ
                        {withQty > 0 && <span style={{ marginLeft: 6 }}>· задано {withQty}</span>}
                        {machineCount > 0 && (
                            <span style={{ marginLeft: 6, color: 'var(--color-accent)' }}>· 🔒 {machineCount}</span>
                        )}
                        {manualCount > 0 && (
                            <span style={{ marginLeft: 6, color: 'var(--color-success)' }}>· ✏️ {manualCount}</span>
                        )}
                    </div>
                );
            },
            exportValue: (r: BoxMultiplicityRow) => r.per_warehouse
                .filter(p => p.rf_stock > 0 || p.box_qty !== null)
                .map(p => `${p.warehouse_name}:${p.box_qty ?? '—'}[${p.source}]${p.use_box_multiplicity ? '' : '*'}`)
                .join(' | '),
        },
        {
            key: 'use_box_multiplicity',
            label: 'Учитывать',
            align: 'center',
            width: '110px',
            getValue: (r: BoxMultiplicityRow) => (r.use_box_multiplicity ? 1 : 0),
            render: (_v: unknown, r: BoxMultiplicityRow) => (
                <label
                    title={r.use_box_multiplicity
                        ? 'Кратность учитывается при создании сборки (дефолт для ФФ без своего флага)'
                        : 'Кратность игнорируется — раздаём без округления'}
                    style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}
                >
                    <input
                        type="checkbox"
                        checked={r.use_box_multiplicity}
                        onChange={e => toggleUse(r.nm_id, e.target.checked)}
                        style={{ width: 16, height: 16, cursor: 'pointer' }}
                    />
                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                        {r.use_box_multiplicity ? 'да' : 'нет'}
                    </span>
                </label>
            ),
        },
        {
            key: 'has_machine_data',
            label: 'Кратность ФФ',
            align: 'center',
            width: '140px',
            // Сортировка: 2 = есть на всех, 1 = частично, 0 = нет.
            getValue: (r: BoxMultiplicityRow) => {
                const stocked = r.per_warehouse.filter(p => p.rf_stock > 0);
                if (stocked.length === 0) return 0;
                const withQty = stocked.filter(p => p.box_qty !== null).length;
                if (withQty === 0) return 0;
                return withQty === stocked.length ? 2 : 1;
            },
            render: (_v: unknown, r: BoxMultiplicityRow) => {
                const stocked = r.per_warehouse.filter(p => p.rf_stock > 0);
                const withQty = stocked.filter(p => p.box_qty !== null).length;
                const lock = r.has_machine_data ? '🔒 ' : '';
                if (stocked.length === 0 || withQty === 0) {
                    return <span style={{ color: 'var(--color-text-dim)', fontSize: 12 }}>—</span>;
                }
                if (withQty === stocked.length) {
                    return (
                        <span
                            className="badge badge-success"
                            style={{ fontSize: 11 }}
                            title={`Кратность задана на всех ФФ с остатком (${withQty} из ${stocked.length})`}
                        >
                            {lock}есть
                        </span>
                    );
                }
                return (
                    <span
                        className="badge badge-warning"
                        style={{ fontSize: 11 }}
                        title={`Кратность задана на ${withQty} из ${stocked.length} ФФ с остатком — на остальных нет`}
                    >
                        {lock}есть частично
                    </span>
                );
            },
            exportValue: (r: BoxMultiplicityRow) => {
                const stocked = r.per_warehouse.filter(p => p.rf_stock > 0);
                const withQty = stocked.filter(p => p.box_qty !== null).length;
                if (stocked.length === 0 || withQty === 0) return 'нет';
                return withQty === stocked.length ? 'есть' : 'есть частично';
            },
        },
        {
            key: 'box_sizes',
            label: 'Размер коробки',
            align: 'left',
            width: '170px',
            // Сорт: 0 = нет данных, 1 = один размер, 2 = разные по складам.
            getValue: (r: BoxMultiplicityRow) => {
                const n = skuBoxSizeInfo(r).sizes.length;
                return n === 0 ? 0 : n === 1 ? 1 : 2;
            },
            render: (_v: unknown, r: BoxMultiplicityRow) => {
                const { sizes, perFf } = skuBoxSizeInfo(r);
                if (sizes.length === 0) {
                    return r.rf_stock > 0 ? (
                        <span
                            className="badge badge-warning"
                            style={{ fontSize: 11 }}
                            title="Нет габаритов коробки ни на одном ФФ — паллету не сформировать. Задайте размер в раскрытии строки или вставкой из буфера."
                        >
                            нет данных
                        </span>
                    ) : (
                        <span style={{ color: 'var(--color-text-dim)', fontSize: 12 }}>—</span>
                    );
                }
                const tip = perFf.map(p => `${p.wh}: ${p.size}`).join('\n');
                if (sizes.length === 1) {
                    return (
                        <span style={{ fontSize: 12, fontFamily: 'monospace' }} title={tip}>
                            {sizes[0]}
                        </span>
                    );
                }
                return (
                    <span
                        className="badge badge-info"
                        style={{ fontSize: 11 }}
                        title={`Разные коробки по складам:\n${tip}`}
                    >
                        разные ({sizes.length})
                    </span>
                );
            },
            exportValue: (r: BoxMultiplicityRow) => {
                const { sizes } = skuBoxSizeInfo(r);
                return sizes.length === 0 ? 'нет данных' : sizes.join(' / ');
            },
        },
    ];

    if (loading) {
        return <div className="glass-card" style={{ padding: 24 }}>Загрузка...</div>;
    }
    if (error) {
        return (
            <div className="glass-card" style={{ padding: 24, color: 'var(--color-danger)' }}>
                {error}
            </div>
        );
    }

    return (
        <div className="animate-in">
            <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16 }}>
                <div>
                    <Link
                        href={`/p/${slug}/warehouse/analytics`}
                        className="btn btn-secondary btn-sm"
                        style={{ marginBottom: 8, display: 'inline-flex', alignItems: 'center', gap: 6 }}
                    >
                        ← Аналитика остатков
                    </Link>
                    <h1 className="page-title">📦 Кратность коробок</h1>
                    <p className="page-subtitle">
                        Кратность резолвится по каждому ФФ-складу: машина → вручную → дефолт.
                        Разверните строку для настройки по ФФ. Используется в распределении при создании сборки.
                    </p>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <Link
                        href={`/p/${slug}/warehouse/pallet-sizes`}
                        className="btn btn-secondary btn-sm"
                        title="Все размеры коробок из системы + коробок на паллету (с ручным переопределением)"
                    >
                        📐 Паллеты по размерам
                    </Link>
                    <button
                        className={`btn btn-sm ${historyOpen ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={toggleHistory}
                        title="История массовых вставок проекта с откатом"
                    >
                        📜 История вставок
                    </button>
                    <button className="btn btn-primary btn-sm" onClick={openPaste}>
                        📋 Вставить из буфера
                    </button>
                </div>
            </div>

            {/* ─── История массовых вставок (toggle-панель) ─────────────────── */}
            {historyOpen && (
                <div className="glass-card" style={{ padding: 20, marginTop: 16 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
                        <div>
                            <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>📜 История массовых вставок</h3>
                            <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--color-text-muted)' }}>
                                Последние 50 операций. Откат вернёт значения до вставки —
                                машинно-заблокированные ФФ пропустятся.
                            </p>
                        </div>
                        <button className="btn btn-secondary btn-sm" onClick={() => setHistoryOpen(false)}>✕ Свернуть</button>
                    </div>

                    {batchesLoading && (
                        <div style={{ padding: 16, color: 'var(--color-text-muted)', fontSize: 13 }}>Загрузка…</div>
                    )}
                    {!batchesLoading && batches !== null && batches.length === 0 && (
                        <div style={{ padding: 16, color: 'var(--color-text-muted)', fontSize: 13 }}>
                            Нет массовых вставок в проекте.
                        </div>
                    )}
                    {!batchesLoading && batches && batches.length > 0 && (
                        <div style={{ overflowX: 'auto' }}>
                            <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                                <thead>
                                    <tr style={{ color: 'var(--color-text-muted)', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.05 }}>
                                        <th style={{ textAlign: 'left', padding: '8px 8px' }}>Когда</th>
                                        <th style={{ textAlign: 'right', padding: '8px 8px', width: 110 }}>Изменений</th>
                                        <th style={{ textAlign: 'right', padding: '8px 8px', width: 110 }}>SKU</th>
                                        <th style={{ textAlign: 'left', padding: '8px 8px', width: 180 }}>Batch ID</th>
                                        <th style={{ textAlign: 'right', padding: '8px 8px', width: 200 }}>—</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {batches.map(b => (
                                        <tr key={b.batch_id} style={{ borderTop: '1px solid var(--color-border)' }}>
                                            <td style={{ padding: '8px 8px' }}>
                                                {formatDateTime(b.created_at)}
                                            </td>
                                            <td style={{ padding: '8px 8px', textAlign: 'right' }}>
                                                {formatNumber(b.changes_count, 0)}
                                            </td>
                                            <td style={{ padding: '8px 8px', textAlign: 'right' }}>
                                                {formatNumber(b.affected_barcodes, 0)}
                                            </td>
                                            <td style={{ padding: '8px 8px', fontFamily: 'monospace', fontSize: 11, color: 'var(--color-text-muted)' }}>
                                                {b.batch_id.slice(0, 8)}…
                                            </td>
                                            <td style={{ padding: '8px 8px', textAlign: 'right' }}>
                                                <button
                                                    className="btn btn-secondary btn-sm"
                                                    onClick={() => revertBatchFromHistory(b.batch_id, b.changes_count)}
                                                    disabled={revertingBatchId !== null}
                                                >
                                                    {revertingBatchId === b.batch_id ? 'Откат…' : '↩ Откатить'}
                                                </button>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            )}

            {/* ─── Bulk paste inline editor ───────────────────────────────── */}
            {pasteOpen && (
                <div className="glass-card" style={{ padding: 20, marginTop: 16 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
                        <div>
                            <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>📋 Массовое редактирование</h3>
                            <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--color-text-muted)' }}>
                                <strong>Шаблон выгрузки</strong> (Артикул, Barcode, nm_id, Склад, Кратность, Размер коробки) — per-ФФ.
                                <br />
                                <strong>Короткий формат</strong> (Barcode, Склад, Кратность, Размер) — без Артикула и nm_id.
                                <br />
                                <strong>SKU-дефолт</strong>: Barcode, Кратность (2 колонки).
                                <br />
                                кратность: число или <code>-</code>/<code>0</code> = очистить, пусто = не менять
                                {' · '}флаг «учитывать» при вставке всегда выставляется в <strong>да</strong>
                                {' · '}машинно-заблокированные ФФ пропускаются
                            </p>
                        </div>
                        <button className="btn btn-secondary btn-sm" onClick={closePaste} disabled={pasteApplying}>✕ Свернуть</button>
                    </div>

                    {pasteResult && (
                        <div style={{
                            padding: '10px 14px', marginBottom: 12,
                            borderRadius: 8, background: 'rgba(52, 199, 89, 0.08)',
                            borderLeft: '3px solid var(--color-success)', fontSize: 13,
                        }}>
                            ✅ Обновлено <strong>{pasteResult.updated}</strong> SKU
                            {' · '}найдено по barcode <strong>{pasteResult.matched}</strong>
                            {pasteResult.updated === 0 && pasteResult.matched > 0 && (
                                <div style={{ marginTop: 6, fontSize: 12, color: 'var(--color-text-muted)' }}>
                                    Значения уже совпадают с сохранёнными — ничего не изменилось, журнал не пополнен.
                                </div>
                            )}
                            {pasteResult.notFound.length > 0 && (
                                <details style={{ marginTop: 6, fontSize: 12 }}>
                                    <summary style={{ cursor: 'pointer', color: 'var(--color-warning)' }}>
                                        Не найдено barcode ({pasteResult.notFound.length})
                                    </summary>
                                    <div style={{ fontFamily: 'monospace', marginTop: 4, maxHeight: 80, overflowY: 'auto' }}>
                                        {pasteResult.notFound.join(', ')}
                                    </div>
                                </details>
                            )}
                            {pasteResult.locked.length > 0 && (
                                <details style={{ marginTop: 6, fontSize: 12 }}>
                                    <summary style={{ cursor: 'pointer', color: 'var(--color-accent)' }}>
                                        🔒 Заблокировано машиной — пропущено ({pasteResult.locked.length})
                                    </summary>
                                    <div style={{ fontFamily: 'monospace', marginTop: 4, maxHeight: 80, overflowY: 'auto' }}>
                                        {pasteResult.locked.join(', ')}
                                    </div>
                                </details>
                            )}
                            {pasteResult.batchId && !pasteResult.reverted && (
                                <div style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                                    <button
                                        className="btn btn-secondary btn-sm"
                                        onClick={revertLastBatch}
                                        disabled={batchReverting}
                                        title={`Откатит все ${pasteResult.updated} изменённых строк к значениям до вставки`}
                                    >
                                        {batchReverting ? 'Откат…' : '↩ Откатить эту вставку'}
                                    </button>
                                    <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                                        batch <code>{pasteResult.batchId.slice(0, 8)}…</code>
                                    </span>
                                </div>
                            )}
                            {pasteResult.reverted && (
                                <div style={{
                                    marginTop: 8, padding: '8px 12px', borderRadius: 8,
                                    background: 'rgba(0, 122, 255, 0.08)',
                                    borderLeft: '3px solid var(--color-accent)', fontSize: 12,
                                }}>
                                    ↩ Отменено <strong>{pasteResult.reverted.count}</strong> записей
                                    {pasteResult.reverted.locked.length > 0 && (
                                        <> · 🔒 заблокировано машиной: <strong>{pasteResult.reverted.locked.length}</strong></>
                                    )}
                                </div>
                            )}
                            <button
                                className="btn btn-secondary btn-sm"
                                style={{ marginTop: 8 }}
                                onClick={() => setPasteResult(null)}
                            >Закрыть</button>
                        </div>
                    )}

                    {pasteRawText ? (
                        <div style={{
                            padding: 12, marginBottom: 8,
                            borderRadius: 8, background: 'rgba(0, 122, 255, 0.06)',
                            borderLeft: '3px solid var(--color-accent)', fontSize: 13,
                        }}>
                            🧾 Распознан расширенный шаблон с per-ФФ ({pasteRawText.split('\n').filter(l => l.trim()).length} строк).
                            <button
                                className="btn btn-secondary btn-sm"
                                style={{ marginLeft: 12 }}
                                onClick={() => setPasteRawText('')}
                            >Очистить</button>
                            <details style={{ marginTop: 8 }}>
                                <summary style={{ cursor: 'pointer', fontSize: 12, color: 'var(--color-text-muted)' }}>
                                    Показать содержимое
                                </summary>
                                <pre style={{
                                    fontSize: 11, fontFamily: 'monospace', marginTop: 6,
                                    maxHeight: 240, overflow: 'auto', padding: 8,
                                    background: 'var(--color-bg-card)', borderRadius: 6,
                                }}>{pasteRawText}</pre>
                            </details>
                        </div>
                    ) : (
                    <div style={{ overflowX: 'auto' }}>
                        <table style={{ width: '100%', fontSize: 13 }}>
                            <thead>
                                <tr style={{ color: 'var(--color-text-muted)', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.05 }}>
                                    <th style={{ textAlign: 'left', padding: '6px 8px', width: 40 }}>#</th>
                                    <th style={{ textAlign: 'left', padding: '6px 8px' }}>Баркод</th>
                                    <th style={{ textAlign: 'left', padding: '6px 8px', width: 180 }}>Склад (пусто = SKU)</th>
                                    <th style={{ textAlign: 'left', padding: '6px 8px', width: 110 }}>Кратность</th>
                                    <th style={{ textAlign: 'left', padding: '6px 8px', width: 140 }}>Размер коробки</th>
                                    <th style={{ textAlign: 'center', padding: '6px 8px', width: 50 }}>—</th>
                                </tr>
                            </thead>
                            <tbody>
                                {pasteRows.map((row, i) => {
                                    const filled = isPasteRowFilled(row);
                                    const whInvalid = row.warehouse.trim() !== '' && !warehouseMap.has(row.warehouse.trim()) && !warehouseMap.has(row.warehouse.trim().toLowerCase());
                                    const inputStyle = (extra: React.CSSProperties = {}): React.CSSProperties => ({
                                        width: '100%', padding: '6px 10px', fontSize: 13,
                                        border: '1px solid var(--color-border)', borderRadius: 8,
                                        ...extra,
                                    });
                                    return (
                                        <tr key={i} style={{ borderTop: '1px solid var(--color-border)' }}>
                                            <td style={{ padding: '6px 8px', color: 'var(--color-text-muted)' }}>{i + 1}</td>
                                            <td style={{ padding: '6px 8px' }}>
                                                <input
                                                    type="text"
                                                    value={row.barcode}
                                                    onChange={e => updatePasteRow(i, 'barcode', e.target.value)}
                                                    onPaste={e => handleRowsPaste(e, i)}
                                                    placeholder="Баркод"
                                                    style={inputStyle({ fontFamily: 'monospace' })}
                                                />
                                            </td>
                                            <td style={{ padding: '6px 8px' }}>
                                                <input
                                                    type="text"
                                                    value={row.warehouse}
                                                    onChange={e => updatePasteRow(i, 'warehouse', e.target.value)}
                                                    onPaste={e => handleRowsPaste(e, i)}
                                                    placeholder="ФФ (опц.)"
                                                    style={inputStyle({
                                                        borderColor: whInvalid ? 'var(--color-danger)' : undefined,
                                                    })}
                                                    title={whInvalid ? 'Склад не найден' : 'Имя ФФ; пусто — обновится SKU-дефолт'}
                                                />
                                            </td>
                                            <td style={{ padding: '6px 8px' }}>
                                                <input
                                                    type="text"
                                                    inputMode="numeric"
                                                    value={row.ppb}
                                                    onChange={e => updatePasteRow(i, 'ppb', e.target.value)}
                                                    onPaste={e => handleRowsPaste(e, i)}
                                                    placeholder="0"
                                                    style={inputStyle({ textAlign: 'right' })}
                                                />
                                            </td>
                                            <td style={{ padding: '6px 8px' }}>
                                                <input
                                                    type="text"
                                                    value={row.box_size}
                                                    onChange={e => updatePasteRow(i, 'box_size', e.target.value)}
                                                    onPaste={e => handleRowsPaste(e, i)}
                                                    placeholder="напр. 30×40×20"
                                                    style={inputStyle()}
                                                    title="Только при заданном складе"
                                                />
                                            </td>
                                            <td style={{ padding: '6px 8px', textAlign: 'center' }}>
                                                {filled && pasteRows.length > 1 && (
                                                    <button
                                                        className="btn btn-secondary btn-sm"
                                                        onClick={() => setPasteRows(prev => prev.filter((_, j) => j !== i))}
                                                        title="Удалить строку"
                                                        style={{ padding: '2px 8px' }}
                                                    >✕</button>
                                                )}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                    )}

                    {/* Errors summary */}
                    {parsedPaste.errors.length > 0 && (
                        <div style={{
                            marginTop: 8, padding: '8px 12px', borderRadius: 8,
                            background: 'rgba(255, 59, 48, 0.06)', borderLeft: '3px solid var(--color-danger)',
                            fontSize: 12, color: 'var(--color-danger)',
                        }}>
                            <strong>Ошибки в строках:</strong>
                            <ul style={{ margin: '4px 0 0 18px', padding: 0 }}>
                                {parsedPaste.errors.slice(0, 5).map((err, i) => (
                                    <li key={i}>строка {err.line}: {err.reason}</li>
                                ))}
                                {parsedPaste.errors.length > 5 && <li>…и ещё {parsedPaste.errors.length - 5}</li>}
                            </ul>
                        </div>
                    )}

                    <div style={{ display: 'flex', gap: 8, marginTop: 16, justifyContent: 'flex-end', alignItems: 'center' }}>
                        <span style={{ marginRight: 'auto', fontSize: 12, color: 'var(--color-text-muted)' }}>
                            Распознано: <strong style={{ color: 'var(--color-success)' }}>{parsedPaste.rows.length}</strong>
                            {parsedPaste.errors.length > 0 && (
                                <> · ошибок: <strong style={{ color: 'var(--color-danger)' }}>{parsedPaste.errors.length}</strong></>
                            )}
                        </span>
                        <button className="btn btn-secondary btn-sm" onClick={closePaste} disabled={pasteApplying}>
                            Отмена
                        </button>
                        <button
                            className="btn btn-primary btn-sm"
                            onClick={applyPaste}
                            disabled={pasteApplying || parsedPaste.rows.length === 0}
                        >
                            {pasteApplying ? 'Применяю…' : `Применить (${parsedPaste.rows.length})`}
                        </button>
                    </div>
                </div>
            )}

            {/* KPI-карты совпадают с чипами фильтра остатков — клик активирует фильтр. */}
            <div className="stats-grid" style={{ marginBottom: 16 }}>
                {([
                    { key: 'all', label: 'Все', count: stats.total, color: 'var(--color-text)' },
                    { key: 'rf', label: 'Есть на ФФ', count: stats.filterCounts.rf, color: 'var(--color-success)' },
                    { key: 'partial', label: 'Частичная кратность', count: stats.filterCounts.partial, color: 'var(--color-warning)' },
                    { key: 'mixed', label: 'Разные кратности', count: stats.filterCounts.mixed, color: 'var(--color-accent)' },
                    { key: 'no_box_qty', label: 'Нет кратности', count: stats.filterCounts.no_box_qty, color: 'var(--color-danger)' },
                    { key: 'no_pallet', label: 'Нет габаритов', count: stats.filterCounts.no_pallet, color: 'var(--color-warning)' },
                ] as Array<{ key: StockFilter; label: string; count: number; color: string }>).map(c => {
                    const active = stockFilter === c.key;
                    return (
                        <div
                            key={c.key}
                            role="button"
                            tabIndex={0}
                            className="glass-card"
                            onClick={() => setStockFilter(c.key)}
                            onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') setStockFilter(c.key); }}
                            style={{
                                padding: '14px 18px',
                                textAlign: 'center',
                                cursor: 'pointer',
                                border: active ? '2px solid var(--color-accent)' : undefined,
                                transition: 'all 0.2s',
                            }}
                            title={active ? 'Фильтр активен' : `Включить фильтр «${c.label}»`}
                        >
                            <div style={{ fontSize: 26, fontWeight: 700, color: c.color }}>
                                {formatNumber(c.count, 0)}
                            </div>
                            <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{c.label}</div>
                        </div>
                    );
                })}
            </div>

            {rows.length === 0 ? (
                <div className="empty-state">
                    <div className="empty-state-icon">📦</div>
                    <div>Нет SKU с привязкой к WB</div>
                </div>
            ) : (
                <>
                    {/* Filter row 1: brand + subject + search */}
                    <div className="glass-card" style={{ padding: 12, marginBottom: 12, display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                        <select
                            value={brandFilter}
                            onChange={e => setBrandFilter(e.target.value)}
                            style={{ padding: '6px 10px', fontSize: 13, border: '1px solid var(--color-border)', borderRadius: 8, minWidth: 160 }}
                        >
                            <option value="">Все бренды ({brandOptions.length})</option>
                            {brandOptions.map(b => <option key={b} value={b}>{b}</option>)}
                        </select>
                        <select
                            value={subjectFilter}
                            onChange={e => setSubjectFilter(e.target.value)}
                            style={{ padding: '6px 10px', fontSize: 13, border: '1px solid var(--color-border)', borderRadius: 8, minWidth: 160 }}
                        >
                            <option value="">Все предметы ({subjectOptions.length})</option>
                            {subjectOptions.map(s => <option key={s} value={s}>{s}</option>)}
                        </select>
                        <input
                            type="text"
                            placeholder="Поиск по артикулу / barcode / nm_id"
                            value={search}
                            onChange={e => setSearch(e.target.value)}
                            style={{ padding: '6px 10px', fontSize: 13, border: '1px solid var(--color-border)', borderRadius: 8, flex: 1, minWidth: 200 }}
                        />
                        {(brandFilter || subjectFilter || search || stockFilter !== 'all') && (
                            <button
                                className="btn btn-secondary btn-sm"
                                onClick={() => { setBrandFilter(''); setSubjectFilter(''); setSearch(''); setStockFilter('all'); }}
                            >✕ Сброс</button>
                        )}
                    </div>

                    {/* Filter row 2: stock chips */}
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
                        {([
                            { key: 'all', label: 'Все', count: rows.length },
                            { key: 'rf', label: 'Есть на ФФ', count: stats.filterCounts.rf },
                            { key: 'partial', label: 'Частичная кратность', count: stats.filterCounts.partial },
                            { key: 'mixed', label: 'Разные кратности', count: stats.filterCounts.mixed },
                            { key: 'no_box_qty', label: 'Нет кратности', count: stats.filterCounts.no_box_qty },
                            { key: 'no_pallet', label: 'Нет габаритов', count: stats.filterCounts.no_pallet },
                        ] as Array<{ key: StockFilter; label: string; count: number }>).map(c => (
                            <button
                                key={c.key}
                                className={`btn btn-sm ${stockFilter === c.key ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setStockFilter(c.key)}
                            >
                                {c.label} ({formatNumber(c.count, 0)})
                            </button>
                        ))}
                    </div>

                    {filteredRows.length === 0 ? (
                        <div className="empty-state">
                            <div className="empty-state-icon">🔍</div>
                            <div>Под текущие фильтры ничего не подходит</div>
                        </div>
                    ) : (
                        <TanStackDataTable
                            columns={columns}
                            data={filteredRows}
                            exportName="box_multiplicity"
                            exportAdditionalSheets={[{
                                sheetName: 'Шаблон для вставки',
                                data: perWarehouseExportRows,
                                columns: [
                                    { key: 'article', label: 'Артикул' },
                                    { key: 'barcode', label: 'Barcode' },
                                    { key: 'nm_id', label: 'nm_id' },
                                    { key: 'warehouse', label: 'Склад' },
                                    { key: 'box_qty', label: 'Кратность по ФФ' },
                                    { key: 'box_size', label: 'Размер коробки' },
                                ],
                            }]}
                            emptyText="Нет данных"
                            pageSize={100}
                            getRowId={(r: BoxMultiplicityRow) => r.barcode}
                            renderSubRow={(r: BoxMultiplicityRow) => (
                                <SkuExpandPanel
                                    sku={r}
                                    perRfEdit={perRfEdit}
                                    setPerRfEdit={setPerRfEdit}
                                    perRfSizeEdit={perRfSizeEdit}
                                    setPerRfSizeEdit={setPerRfSizeEdit}
                                    onSavePerRfPpb={savePerRfPpb}
                                    onSavePerRfSize={savePerRfSize}
                                    onTogglePerRfUse={togglePerRfUse}
                                    onRowUpdated={updated => {
                                        setRows(prev => prev.map(x => x.nm_id === updated.nm_id ? updated : x));
                                        markSticky(updated.nm_id);
                                        onSaved?.();
                                    }}
                                    palletOverrides={palletOverrides}
                                />
                            )}
                        />
                    )}
                </>
            )}

        </div>
    );
}
