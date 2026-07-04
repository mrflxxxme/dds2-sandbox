'use client';
/**
 * Вкладка «Раскладка по паллетам» деталки сборки.
 *
 * Два вида: Карточки (паллеты-колонки, DnD групп + сплит-панель «N из M») и
 * Таблица (inline-редактирование коробов/россыпи + подытоги). Единое состояние
 * `pallets`. «Авто» = пересобрать из кратности. Строгий инвариант (Σ==quantity)
 * гейтит «Сохранить». Экспорт internal|wb — на бэкенде.
 *
 * Ограничение: геометрический лимит высоты паллеты не проверяется — габариты
 * короба на деталке недоступны (есть только кратность ppb). Валидность держит
 * инвариант; высоту оператор контролирует вручную.
 */
import { useCallback, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import type { AssemblyRequest, PalletBox } from '@/types/api';
import {
    addPallet,
    autoManifest,
    BUFFER_PALLET_NO,
    checkInvariant,
    moveBoxes,
    moveLoose,
    normalizeForSave,
    placedUnits,
    removePallet,
    type PpbOf,
} from '@/lib/assembly/palletLayout';

interface Props {
    assembly: AssemblyRequest;
    ppbByBarcode: Map<string, number | null>;
    slug: string;
    onSaved: () => void | Promise<void>;
}

interface PendingMove {
    barcode: string;
    fromNo: number;
    toNo: number;
    maxBoxes: number;
    maxLoose: number;
}

function palletUnits(p: PalletBox, ppbOf: PpbOf): number {
    return p.boxes.reduce((s, b) => {
        const ppb = ppbByBarcodePpb(ppbOf, b.barcode);
        return s + b.box_count * ppb + b.loose_units;
    }, 0);
}

function ppbByBarcodePpb(ppbOf: PpbOf, bc: string): number {
    const p = ppbOf(bc);
    return p && p > 0 ? Math.floor(p) : 0;
}

/** Stable, distinct color per SKU (computed hue — not a hardcoded palette), for at-a-glance grouping. */
function colorFor(bc: string): string {
    let h = 0;
    for (let i = 0; i < bc.length; i++) h = (h * 31 + bc.charCodeAt(i)) % 360;
    return `hsl(${h}, 55%, 55%)`;
}

export default function PalletLayoutTab({ assembly, ppbByBarcode, slug, onSaved }: Props) {
    const items = useMemo(() => assembly.items ?? [], [assembly.items]);
    const ppbOf: PpbOf = useCallback((bc) => ppbByBarcode.get(bc) ?? null, [ppbByBarcode]);
    const nameOf = useMemo(() => {
        const m = new Map<string, string>();
        for (const it of items) m.set(it.barcode, it.article || it.product_name || it.barcode);
        return m;
    }, [items]);

    const buildAuto = useCallback(
        () => autoManifest(items, ppbOf, assembly.pallets_count),
        [items, ppbOf, assembly.pallets_count],
    );
    const initial = useMemo<PalletBox[]>(() => {
        if (assembly.pallet_manifest && assembly.pallet_manifest.length > 0) {
            return assembly.pallet_manifest.map(p => ({
                pallet_no: p.pallet_no,
                boxes: p.boxes.map(b => ({ ...b })),
            }));
        }
        return buildAuto();
    }, [assembly.pallet_manifest, buildAuto]);

    const [pallets, setPallets] = useState<PalletBox[]>(initial);
    const [view, setView] = useState<'cards' | 'table'>('cards');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState('');
    const [savedNote, setSavedNote] = useState('');
    const [pending, setPending] = useState<PendingMove | null>(null);
    const [moveBoxesN, setMoveBoxesN] = useState(0);
    const [moveLooseN, setMoveLooseN] = useState(0);
    const dragRef = useRef<{ barcode: string; fromNo: number } | null>(null);

    const inv = useMemo(() => checkInvariant(pallets, items, ppbOf), [pallets, items, ppbOf]);
    const placed = useMemo(() => placedUnits(pallets, ppbOf), [pallets, ppbOf]);
    const totalUnits = useMemo(() => [...placed.values()].reduce((s, v) => s + v, 0), [placed]);

    const sortedPallets = useMemo(
        () => [...pallets].sort((a, b) => a.pallet_no - b.pallet_no),
        [pallets],
    );

    const mutate = (next: PalletBox[]) => {
        setPallets(next);
        setSavedNote('');
    };

    const save = async () => {
        setSaving(true);
        setError('');
        setSavedNote('');
        try {
            await api.updatePalletManifest(assembly.id, normalizeForSave(pallets));
            setSavedNote('Раскладка сохранена');
            await onSaved();
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Ошибка сохранения');
        } finally {
            setSaving(false);
        }
    };

    const resetToAuto = async () => {
        // «Авто» = сброс серверного манифеста к геометрии + локальный пересбор.
        setError('');
        setSavedNote('');
        mutate(buildAuto());
        try {
            await api.updatePalletManifest(assembly.id, null);
            await onSaved();
            setSavedNote('Сброшено к «авто»');
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Ошибка сброса');
        }
    };

    const exportXlsx = async (format: 'internal' | 'wb') => {
        setError('');
        try {
            await api.downloadPalletLayout(
                assembly.id,
                format,
                `${assembly.number}_${format === 'wb' ? 'wb' : 'pallets'}.xlsx`,
            );
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Ошибка выгрузки');
        }
    };

    const onDropPallet = (toNo: number) => {
        const d = dragRef.current;
        dragRef.current = null;
        if (!d || d.fromNo === toNo) return;
        const from = pallets.find(p => p.pallet_no === d.fromNo);
        const box = from?.boxes.find(b => b.barcode === d.barcode);
        if (!box) return;
        if (box.box_count <= 1 && box.loose_units === 0) {
            mutate(moveBoxes(pallets, d.fromNo, toNo, d.barcode, box.box_count || 1));
            return;
        }
        setMoveBoxesN(box.box_count);
        setMoveLooseN(box.loose_units);
        setPending({ barcode: d.barcode, fromNo: d.fromNo, toNo, maxBoxes: box.box_count, maxLoose: box.loose_units });
    };

    const confirmSplit = () => {
        if (!pending) return;
        let next = moveBoxes(pallets, pending.fromNo, pending.toNo, pending.barcode, moveBoxesN);
        next = moveLoose(next, pending.fromNo, pending.toNo, pending.barcode, moveLooseN);
        mutate(next);
        setPending(null);
    };

    const setBoxField = (palletNo: number, barcode: string, field: 'box_count' | 'loose_units', value: number) => {
        mutate(
            pallets.map(p =>
                p.pallet_no !== palletNo
                    ? p
                    : { ...p, boxes: p.boxes.map(b => (b.barcode !== barcode ? b : { ...b, [field]: Math.max(0, value) })) },
            ),
        );
    };

    if (items.length === 0) {
        return (
            <div className="glass-card" style={{ padding: 24, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                В заявке нет позиций — раскладывать нечего.
            </div>
        );
    }

    return (
        <div className="glass-card" style={{ padding: 24 }}>
            {/* Панель управления */}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 16 }}>
                <div style={{ display: 'flex', gap: 4 }}>
                    <button className={`btn btn-sm ${view === 'cards' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setView('cards')}>Карточки</button>
                    <button className={`btn btn-sm ${view === 'table' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setView('table')}>Таблица</button>
                </div>
                <div style={{ flex: 1 }} />
                <button className="btn btn-secondary btn-sm" onClick={() => mutate(addPallet(pallets))}>+ Паллета</button>
                <button className="btn btn-secondary btn-sm" onClick={resetToAuto}>Авто</button>
                <button className="btn btn-secondary btn-sm" onClick={() => exportXlsx('internal')}>Экспорт (кладовщик)</button>
                <button className="btn btn-secondary btn-sm" onClick={() => exportXlsx('wb')}>Экспорт в WB</button>
                <button className="btn btn-primary btn-sm" onClick={save} disabled={saving || !inv.ok}>
                    {saving ? 'Сохранение...' : 'Сохранить'}
                </button>
            </div>

            {/* Инвариант / статусы */}
            {!inv.ok && (
                <div className="glass-card" style={{ padding: 12, marginBottom: 12, fontSize: 13, color: 'var(--color-warning)' }}>
                    Раскладка не сходится с составом (сохранить нельзя):{' '}
                    {inv.mismatches.slice(0, 6).map(m => `${nameOf.get(m.barcode) || m.barcode}: ${formatNumber(m.placed, 0)}/${formatNumber(m.need, 0)}`).join('; ')}
                    {inv.mismatches.length > 6 ? ` … и ещё ${inv.mismatches.length - 6}` : ''}
                </div>
            )}
            {error && (
                <div className="glass-card" style={{ padding: 12, marginBottom: 12, fontSize: 13, color: 'var(--color-danger)' }}>{error}</div>
            )}
            {savedNote && (
                <div className="glass-card" style={{ padding: 12, marginBottom: 12, fontSize: 13, color: 'var(--color-success)' }}>{savedNote}</div>
            )}

            {/* Сводка */}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
                {[
                    { label: 'Паллет', value: sortedPallets.filter(p => p.pallet_no !== BUFFER_PALLET_NO).length },
                    { label: 'Коробов', value: sortedPallets.reduce((s, p) => p.pallet_no === BUFFER_PALLET_NO ? s : s + p.boxes.reduce((t, b) => t + b.box_count, 0), 0) },
                    { label: 'Позиций', value: items.length },
                    { label: 'Штук', value: totalUnits },
                ].map(m => (
                    <div key={m.label} style={{ background: 'var(--color-bg-card)', borderRadius: 12, padding: '8px 14px', minWidth: 84 }}>
                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>{m.label}</div>
                        <div style={{ fontSize: 20, fontWeight: 600 }}>{formatNumber(m.value, 0)}</div>
                    </div>
                ))}
            </div>

            {view === 'cards' ? (
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start' }}>
                    {sortedPallets.map(p => {
                        const isBuffer = p.pallet_no === BUFFER_PALLET_NO;
                        const boxCount = p.boxes.reduce((s, b) => s + b.box_count, 0);
                        return (
                            <div
                                key={p.pallet_no}
                                onDragOver={e => e.preventDefault()}
                                onDrop={() => onDropPallet(p.pallet_no)}
                                style={{
                                    width: 210,
                                    border: isBuffer ? '1px dashed var(--color-border)' : '1px solid var(--color-border)',
                                    borderRadius: 12,
                                    padding: 12,
                                    background: 'var(--color-bg-card)',
                                    display: 'flex',
                                    flexDirection: 'column',
                                    gap: 8,
                                }}
                            >
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 6 }}>
                                    <strong style={{ fontSize: 13 }}>
                                        {isBuffer ? 'Не распределено' : `Паллета ${p.pallet_no}`}
                                    </strong>
                                    <span className="badge badge-secondary" style={{ fontSize: 11, whiteSpace: 'nowrap' }}>
                                        {formatNumber(boxCount, 0)} кор · {formatNumber(palletUnits(p, ppbOf), 0)} шт
                                    </span>
                                </div>
                                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minHeight: 44 }}>
                                    {p.boxes.length === 0 && (
                                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)', textAlign: 'center', padding: 10, border: '1px dashed var(--color-border)', borderRadius: 8 }}>
                                            перетащите сюда
                                        </div>
                                    )}
                                    {p.boxes.map(b => (
                                        <div
                                            key={b.barcode}
                                            draggable
                                            onDragStart={() => { dragRef.current = { barcode: b.barcode, fromNo: p.pallet_no }; }}
                                            style={{
                                                display: 'flex',
                                                alignItems: 'center',
                                                gap: 8,
                                                border: '1px solid var(--color-border)',
                                                borderLeft: `3px solid ${colorFor(b.barcode)}`,
                                                borderRadius: 8,
                                                padding: '6px 8px',
                                                cursor: 'grab',
                                                background: 'var(--color-bg-input)',
                                            }}
                                            title={b.barcode}
                                        >
                                            <span style={{ width: 8, height: 8, borderRadius: '50%', background: colorFor(b.barcode), flex: 'none' }} />
                                            <div style={{ minWidth: 0, flex: 1 }}>
                                                <div style={{ fontWeight: 500, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                                    {nameOf.get(b.barcode) || b.barcode}
                                                </div>
                                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                                    {formatNumber(b.box_count, 0)} кор.
                                                    {b.loose_units > 0 ? ` + ${formatNumber(b.loose_units, 0)} шт` : ''}
                                                </div>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                                {!isBuffer && (
                                    <button
                                        className="btn btn-secondary btn-sm"
                                        style={{ width: '100%' }}
                                        onClick={() => mutate(removePallet(pallets, p.pallet_no))}
                                    >
                                        Убрать паллету
                                    </button>
                                )}
                            </div>
                        );
                    })}
                </div>
            ) : (
                <TableView
                    pallets={sortedPallets}
                    ppbOf={ppbOf}
                    nameOf={nameOf}
                    totalUnits={totalUnits}
                    onEdit={setBoxField}
                />
            )}

            {/* Сплит-панель «N из M» */}
            {pending && (
                <div className="modal-overlay" onClick={() => setPending(null)}>
                    <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 380 }}>
                        <h2 className="modal-title">Перенести на паллету {pending.toNo || 'буфер'}</h2>
                        <div style={{ fontSize: 13, marginBottom: 12 }}>{nameOf.get(pending.barcode) || pending.barcode}</div>
                        {pending.maxBoxes > 0 && (
                            <SplitRow
                                label="Коробов"
                                value={moveBoxesN}
                                max={pending.maxBoxes}
                                onChange={setMoveBoxesN}
                            />
                        )}
                        {pending.maxLoose > 0 && (
                            <SplitRow
                                label="Россыпь, шт"
                                value={moveLooseN}
                                max={pending.maxLoose}
                                onChange={setMoveLooseN}
                            />
                        )}
                        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 16 }}>
                            <button className="btn btn-secondary" onClick={() => setPending(null)}>Отмена</button>
                            <button className="btn btn-primary" onClick={confirmSplit}>Перенести</button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

function SplitRow({ label, value, max, onChange }: { label: string; value: number; max: number; onChange: (v: number) => void }) {
    return (
        <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 4 }}>{label} (из {formatNumber(max, 0)})</div>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <button className="btn btn-secondary btn-sm" onClick={() => onChange(Math.max(0, value - 1))}>−</button>
                <input
                    type="number"
                    value={value}
                    min={0}
                    max={max}
                    onChange={e => onChange(Math.min(max, Math.max(0, Number(e.target.value) || 0)))}
                    style={{ width: 64, textAlign: 'center' }}
                />
                <button className="btn btn-secondary btn-sm" onClick={() => onChange(Math.min(max, value + 1))}>+</button>
                <button className="btn btn-secondary btn-sm" onClick={() => onChange(Math.floor(max / 2))}>Половина</button>
                <button className="btn btn-secondary btn-sm" onClick={() => onChange(max)}>Все</button>
            </div>
        </div>
    );
}

function TableView({
    pallets,
    ppbOf,
    nameOf,
    totalUnits,
    onEdit,
}: {
    pallets: PalletBox[];
    ppbOf: PpbOf;
    nameOf: Map<string, string>;
    totalUnits: number;
    onEdit: (palletNo: number, barcode: string, field: 'box_count' | 'loose_units', value: number) => void;
}) {
    return (
        <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                    <tr style={{ textAlign: 'left', color: 'var(--color-text-muted)' }}>
                        <th style={{ padding: 8 }}>Паллета</th>
                        <th style={{ padding: 8 }}>Артикул</th>
                        <th style={{ padding: 8 }}>Коробов</th>
                        <th style={{ padding: 8 }}>Россыпь, шт</th>
                        <th style={{ padding: 8 }}>Всего, шт</th>
                    </tr>
                </thead>
                {pallets.map(p => {
                    const label = p.pallet_no === BUFFER_PALLET_NO ? '—' : String(p.pallet_no);
                    const sub = palletUnits(p, ppbOf);
                    return (
                        <tbody key={p.pallet_no}>
                            {p.boxes.map(b => {
                                    const ppb = ppbByBarcodePpb(ppbOf, b.barcode);
                                    return (
                                        <tr key={p.pallet_no + '-' + b.barcode} style={{ borderTop: '1px solid var(--color-border)' }}>
                                            <td style={{ padding: 8 }}>{label}</td>
                                            <td style={{ padding: 8 }} title={b.barcode}>{nameOf.get(b.barcode) || b.barcode}</td>
                                            <td style={{ padding: 8 }}>
                                                <input
                                                    type="number"
                                                    min={0}
                                                    value={b.box_count}
                                                    onChange={e => onEdit(p.pallet_no, b.barcode, 'box_count', Number(e.target.value) || 0)}
                                                    style={{ width: 64 }}
                                                />
                                            </td>
                                            <td style={{ padding: 8 }}>
                                                <input
                                                    type="number"
                                                    min={0}
                                                    value={b.loose_units}
                                                    onChange={e => onEdit(p.pallet_no, b.barcode, 'loose_units', Number(e.target.value) || 0)}
                                                    style={{ width: 64 }}
                                                />
                                            </td>
                                            <td style={{ padding: 8 }}>{formatNumber(b.box_count * ppb + b.loose_units, 0)}</td>
                                        </tr>
                                    );
                                })}
                                <tr style={{ color: 'var(--color-text-muted)' }}>
                                    <td style={{ padding: 8 }} colSpan={4}>Паллета {label} — итого</td>
                                    <td style={{ padding: 8 }}>{formatNumber(sub, 0)}</td>
                                </tr>
                        </tbody>
                    );
                })}
                <tbody>
                    <tr style={{ borderTop: '2px solid var(--color-border)', fontWeight: 600 }}>
                        <td style={{ padding: 8 }} colSpan={4}>ИТОГО</td>
                        <td style={{ padding: 8 }}>{formatNumber(totalUnits, 0)}</td>
                    </tr>
                </tbody>
            </table>
        </div>
    );
}
