'use client';
import { useMemo, useState } from 'react';
import { exportToExcel, formatNumber } from '@/lib/utils';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { Nomenclature, MissingAreaBarcode } from '@/types/api';

type MetricKey = 'area_m2' | 'weight_kg';
interface MetricRow { barcode: string; value: string }
const EMPTY_ROW = (): MetricRow => ({ barcode: '', value: '' });

interface Props {
    metricKey: MetricKey;
    /** Card title, e.g. "Площадь по баркодам (м²)". */
    title: string;
    /** Unit shown in column headers / paste hint, e.g. "м²" or "кг". */
    unit: string;
    /** Duty basis name shown in the "missing" warning, e.g. «За м²» or «От веса». */
    basisLabel: string;
    /** Subtitle under the title. */
    hint: string;
    /** Optional override for the "missing" warning heading (defaults to a duty-by-basis wording). */
    missingHeading?: string;
    /** Optional override for the "missing" warning body text. */
    missingBody?: string;
    nomenclature: Nomenclature[];
    missing: MissingAreaBarcode[];
    onBulkUpdate: (items: { barcode: string; value: number }[]) => Promise<{ updated: number; not_found: string[] }>;
    onReload: () => void;
    setMsg: (m: string) => void;
    exportName: string;
}

/** Per-barcode metric (area m² / weight kg) manager: missing-in-vehicles warning,
 *  Excel-paste grid, and an inline-editable table of existing values. Shared by the
 *  «Площадь по баркодам» and «Вес по баркодам» sections so they stay identical. */
export function BarcodeMetricManager({
    metricKey, title, unit, basisLabel, hint, missingHeading, missingBody, nomenclature, missing, onBulkUpdate, onReload, setMsg, exportName,
}: Props) {
    const [rows, setRows] = useState<MetricRow[]>(() => Array.from({ length: 5 }, EMPTY_ROW));
    const [saving, setSaving] = useState(false);
    const [editingBarcode, setEditingBarcode] = useState<string | null>(null);
    const [editValue, setEditValue] = useState('');
    const [missingDraft, setMissingDraft] = useState<Record<string, string>>({});

    const val = (n: Nomenclature): number => Number(n[metricKey] ?? 0);

    const nomMap = useMemo(() => {
        const m: Record<string, Nomenclature> = {};
        for (const n of nomenclature) { if (n.barcode) m[n.barcode] = n; }
        return m;
    }, [nomenclature]);

    const itemsWithValue = useMemo(() => nomenclature.filter(n => val(n) > 0), [nomenclature]); // eslint-disable-line react-hooks/exhaustive-deps

    const updateRow = (idx: number, field: keyof MetricRow, value: string) => {
        setRows(prev => {
            const next = [...prev];
            next[idx] = { ...next[idx], [field]: value };
            if (idx === next.length - 1 && value) next.push(EMPTY_ROW());
            return next;
        });
    };

    const handlePaste = (e: React.ClipboardEvent, idx: number) => {
        const text = e.clipboardData.getData('text');
        if (text.includes('\t') || text.includes('\n')) {
            e.preventDefault();
            const lines = text.trim().split('\n').filter(Boolean);
            setRows(prev => {
                const next = [...prev];
                for (let i = 0; i < lines.length; i++) {
                    const cols = lines[i].split('\t');
                    const rowIdx = idx + i;
                    if (rowIdx >= next.length) next.push(EMPTY_ROW());
                    next[rowIdx] = {
                        barcode: (cols[0] || '').trim(),
                        value: (cols[1] || '').replace(',', '.').trim(),
                    };
                }
                if (next[next.length - 1].barcode) next.push(EMPTY_ROW());
                return next;
            });
        }
    };

    const validRows = rows.filter(r => r.barcode.trim() && r.value.trim());

    const save = async () => {
        if (!validRows.length) { setMsg('❌ Нет данных для сохранения'); return; }
        setSaving(true);
        try {
            const items = validRows.map(r => ({ barcode: r.barcode.trim(), value: parseFloat(r.value.replace(',', '.')) || 0 }));
            const res = await onBulkUpdate(items);
            const parts = [`✅ Обновлено: ${res.updated}`];
            if (res.not_found.length) parts.push(`Не найдено: ${res.not_found.join(', ')}`);
            setMsg(parts.join(' | '));
            setRows(Array.from({ length: 5 }, EMPTY_ROW));
            onReload();
        } catch (e: any) { setMsg(`❌ ${e.message}`); }
        setSaving(false);
    };

    const saveMissing = async (barcode: string) => {
        const v = parseFloat((missingDraft[barcode] ?? '').replace(',', '.'));
        if (isNaN(v) || v <= 0) { setMsg('❌ Введите корректное значение'); return; }
        try {
            await onBulkUpdate([{ barcode, value: v }]);
            setMsg('✅ Сохранено');
            setMissingDraft(prev => { const n = { ...prev }; delete n[barcode]; return n; });
            onReload();
        } catch (e: any) { setMsg(`❌ ${e.message}`); }
    };

    const loadMissingToGrid = () => {
        const next: MetricRow[] = missing.map(m => ({ barcode: m.barcode, value: '' }));
        next.push(EMPTY_ROW());
        setRows(next);
        setMsg(`✏️ Загружено в форму: ${missing.length}. Заполните значения и сохраните.`);
    };

    const loadAllToGrid = () => {
        const next: MetricRow[] = itemsWithValue.map(n => ({ barcode: n.barcode || '', value: String(val(n) || '') }));
        next.push(EMPTY_ROW());
        setRows(next);
    };

    const startEdit = (barcode: string, v: number) => { setEditingBarcode(barcode); setEditValue(String(v)); };
    const cancelEdit = () => { setEditingBarcode(null); setEditValue(''); };
    const saveEdit = async () => {
        if (!editingBarcode) return;
        const v = parseFloat(editValue.replace(',', '.'));
        if (isNaN(v) || v <= 0) { setMsg('❌ Введите корректное значение'); return; }
        try {
            await onBulkUpdate([{ barcode: editingBarcode, value: v }]);
            setMsg('✅ Обновлено');
            setEditingBarcode(null);
            onReload();
        } catch (e: any) { setMsg(`❌ ${e.message}`); }
    };
    const deleteValue = async (barcode: string) => {
        if (!confirm('Удалить значение для этого баркода?')) return;
        try {
            await onBulkUpdate([{ barcode, value: 0 }]);
            setMsg('✅ Удалено');
            onReload();
        } catch (e: any) { setMsg(`❌ ${e.message}`); }
    };

    const valueColumns: Column[] = [
        { key: 'barcode', label: 'Баркод', render: (v: any) => <span style={{ fontFamily: 'monospace', fontSize: 13 }}>{v}</span> },
        { key: 'subject', label: 'Категория', render: (v: any) => <span style={{ fontSize: 13 }}>{v || '—'}</span> },
        { key: 'article_seller', label: 'Артикул', render: (v: any) => <span style={{ fontSize: 13 }}>{v || '—'}</span> },
        {
            key: 'value', label: `Значение ${unit}`,
            render: (v: any, row: any) => editingBarcode === row.barcode ? (
                <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                    <input
                        className="form-input"
                        value={editValue}
                        onChange={e => setEditValue(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') saveEdit(); if (e.key === 'Escape') cancelEdit(); }}
                        autoFocus
                        style={{ width: 100, fontSize: 13, fontFamily: 'monospace', textAlign: 'right', padding: '4px 8px' }}
                    />
                    <button className="btn btn-success btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={saveEdit}>✓</button>
                    <button className="btn btn-secondary btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={cancelEdit}>✕</button>
                </div>
            ) : (
                <span style={{ fontFamily: 'monospace', fontWeight: 600, cursor: 'pointer' }} onClick={() => startEdit(row.barcode, v)}>{formatNumber(v)}</span>
            ),
        },
        {
            key: '_actions', label: '',
            sortable: false,
            render: (_v: any, row: any) => editingBarcode === row.barcode ? null : (
                <div style={{ display: 'flex', gap: 4 }}>
                    <button className="btn btn-secondary btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => startEdit(row.barcode, row.value)}>✎</button>
                    <button className="btn btn-danger btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => deleteValue(row.barcode)}>✕</button>
                </div>
            ),
        },
    ];

    const missingColumns: Column[] = [
        { key: 'barcode', label: 'Баркод', render: (v: any) => <span style={{ fontFamily: 'monospace', fontSize: 13 }}>{v}</span> },
        { key: 'subject', label: 'Категория', render: (v: any) => <span style={{ fontSize: 13 }}>{v || '—'}</span> },
        { key: 'article_seller', label: 'Артикул', render: (v: any) => <span style={{ fontSize: 13 }}>{v || '—'}</span> },
        { key: 'total_qty', label: 'Кол-во, шт', render: (v: any) => <span style={{ fontFamily: 'monospace' }}>{formatNumber(v)}</span> },
        {
            key: 'vehicles', label: 'Машины', sortable: false,
            render: (v: string[]) => <span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}>{(v || []).join(', ') || '—'}</span>,
        },
        {
            key: '_fill', label: `Значение ${unit}`, sortable: false,
            render: (_v: any, row: any) => (
                <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                    <input
                        className="form-input"
                        value={missingDraft[row.barcode] ?? ''}
                        onChange={e => setMissingDraft(prev => ({ ...prev, [row.barcode]: e.target.value }))}
                        onKeyDown={e => { if (e.key === 'Enter') saveMissing(row.barcode); }}
                        placeholder="0.00"
                        style={{ width: 90, fontSize: 13, fontFamily: 'monospace', textAlign: 'right', padding: '4px 8px' }}
                    />
                    <button className="btn btn-success btn-sm" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => saveMissing(row.barcode)}>✓</button>
                </div>
            ),
        },
    ];

    return (
        <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <div>
                    <h3 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>{title}</h3>
                    <div style={{ fontSize: 11, color: 'var(--color-text-dim)', marginTop: 2 }}>
                        {hint} Баркодов со значением: {itemsWithValue.length}
                    </div>
                </div>
                {itemsWithValue.length > 0 && (
                    <div style={{ display: 'flex', gap: 8 }}>
                        <button className="btn btn-secondary btn-sm" onClick={loadAllToGrid}>✎ Редактировать все</button>
                        <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(itemsWithValue.map(n => ({ barcode: n.barcode, subject: n.subject, article_seller: n.article_seller, [metricKey]: n[metricKey] })), exportName)}>📥 Excel</button>
                    </div>
                )}
            </div>

            {/* Barcodes in vehicles missing this metric */}
            {missing.length > 0 && (
                <div style={{ marginBottom: 16, padding: 12, background: 'rgba(245,158,11,0.08)', borderRadius: 8, border: '1px solid rgba(245,158,11,0.2)' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
                        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-warning)' }}>
                            ⚠️ {missingHeading ?? `В машинах без значения (пошлина «${basisLabel}»)`}: {missing.length}
                        </div>
                        <div style={{ display: 'flex', gap: 8 }}>
                            <button className="btn btn-secondary btn-sm" onClick={loadMissingToGrid}>✎ Заполнить в форме ниже</button>
                            <button className="btn btn-secondary btn-sm" onClick={() => exportToExcel(missing.map(m => ({ barcode: m.barcode, subject: m.subject, article_seller: m.article_seller, total_qty: m.total_qty, vehicles: (m.vehicles || []).join(', ') })), `${exportName}_missing`)}>📥 Excel</button>
                        </div>
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 8 }}>
                        {missingBody ?? `Эти баркоды есть в машинах, но без значения (${unit}) — пошлина «${basisLabel}» не посчитается. Заполните здесь или загрузите в форму ниже.`}
                    </div>
                    <TanStackDataTable columns={missingColumns} data={missing} emptyText="" enableSorting enablePagination={false} />
                </div>
            )}

            {/* Paste grid */}
            <div style={{ background: 'var(--color-bg-input)', padding: 16, borderRadius: 8, marginBottom: 16 }}>
                <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                    Вставьте из Excel: Баркод, Значение {unit}
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '32px 1fr 120px', gap: '4px 8px', alignItems: 'center' }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-dim)' }}>#</div>
                    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-dim)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>БАРКОД</div>
                    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-dim)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>ЗНАЧЕНИЕ {unit.toUpperCase()}</div>

                    {rows.map((row, i) => {
                        const nom = row.barcode.trim() ? nomMap[row.barcode.trim()] : null;
                        const isFound = row.barcode.trim() && nom;
                        const isNotFound = row.barcode.trim() && !nom;
                        return (
                            <div key={i} style={{ display: 'contents' }}>
                                <div style={{ fontSize: 12, color: 'var(--color-text-dim)', textAlign: 'center' }}>{i + 1}</div>
                                <div style={{ position: 'relative' }}>
                                    <input
                                        className="form-input"
                                        value={row.barcode}
                                        onChange={e => updateRow(i, 'barcode', e.target.value)}
                                        onPaste={e => handlePaste(e, i)}
                                        placeholder="Баркод"
                                        style={{
                                            fontSize: 13, fontFamily: 'monospace', padding: '6px 10px',
                                            borderColor: isFound ? 'var(--color-success)' : isNotFound ? 'var(--color-danger)' : undefined,
                                            background: isFound ? 'rgba(16,185,129,0.05)' : isNotFound ? 'rgba(239,68,68,0.05)' : undefined,
                                        }}
                                    />
                                    {isFound && nom && (
                                        <div style={{ fontSize: 10, color: 'var(--color-text-dim)', marginTop: 1 }}>
                                            {nom.subject} {nom.article_seller ? `| ${nom.article_seller}` : ''}
                                        </div>
                                    )}
                                </div>
                                <input
                                    className="form-input"
                                    value={row.value}
                                    onChange={e => updateRow(i, 'value', e.target.value)}
                                    placeholder="0.00"
                                    style={{ fontSize: 13, fontFamily: 'monospace', textAlign: 'right', padding: '6px 10px' }}
                                />
                            </div>
                        );
                    })}
                </div>
                <div style={{ display: 'flex', gap: 8, marginTop: 12, alignItems: 'center' }}>
                    <button className="btn btn-primary btn-sm" onClick={save} disabled={saving || !validRows.length}>
                        {saving ? '⏳' : '💾'} Сохранить ({validRows.length})
                    </button>
                    <button className="btn btn-secondary btn-sm" onClick={() => setRows(Array.from({ length: 5 }, EMPTY_ROW))}>Очистить</button>
                </div>
            </div>

            {/* Table of existing values */}
            {itemsWithValue.length > 0 && (
                <TanStackDataTable
                    columns={valueColumns}
                    data={itemsWithValue.map(n => ({ barcode: n.barcode, subject: n.subject, article_seller: n.article_seller, value: val(n) }))}
                    emptyText=""
                    enableSorting
                    enablePagination={false}
                />
            )}
        </div>
    );
}
