'use client';

import { useState, useEffect, useMemo } from 'react';
import { api } from '@/lib/api';
import type { FunnelProduct, ProductSubcategory, DetectedSize } from '@/types/api';
import { formatNumber } from '@/lib/utils';

type Axis = 'size' | 'category' | 'subcat';
interface Row { barcode: string; value: string }
const EMPTY = (): Row => ({ barcode: '', value: '' });

const AXES: { key: Axis; label: string; col: string }[] = [
    { key: 'size', label: 'Размер', col: 'РАЗМЕР' },
    { key: 'category', label: 'Категория', col: 'КАТЕГОРИЯ' },
    { key: 'subcat', label: 'Под-категория', col: 'ПОД-КАТЕГОРИЯ' },
];

interface Props {
    products: FunnelProduct[];
    sizes: DetectedSize[];
    categories: string[];
    subcategories: ProductSubcategory[];
    onReload: () => void;
    setMsg: (m: string) => void;
}

/**
 * Массовая привязка размера / категории / под-категории по баркодам (вставка из Excel),
 * по образцу «Площадь/Вес по баркодам» в Пошлинах. Баркод → nm_id (barcode-map),
 * значение применяется через существующие override-сеттеры. «Значение для всех пустых»
 * закрывает частый кейс «один размер на пачку баркодов».
 */
export function BarcodeBulkAssign({ products, sizes, categories, subcategories, onReload, setMsg }: Props) {
    const [axis, setAxis] = useState<Axis>('size');
    const [rows, setRows] = useState<Row[]>(() => Array.from({ length: 6 }, EMPTY));
    const [defaultValue, setDefaultValue] = useState('');
    const [barcodeMap, setBarcodeMap] = useState<Record<string, number>>({});
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [result, setResult] = useState<{ updated: number; notFound: string[]; unknown: string[] } | null>(null);

    useEffect(() => {
        let alive = true;
        api.getBarcodeMap()
            .then(m => { if (alive) { setBarcodeMap(m); setLoading(false); } })
            .catch(() => { if (alive) { setMsg('Не удалось загрузить баркоды'); setLoading(false); } });
        return () => { alive = false; };
    }, [setMsg]);

    const productByNm = useMemo(() => {
        const m = new Map<number, FunnelProduct>();
        products.forEach(p => m.set(p.nm_id, p));
        return m;
    }, [products]);

    const subcatByName = useMemo(() => {
        const m = new Map<string, number>();
        subcategories.forEach(s => m.set(s.name.trim().toLowerCase(), s.id));
        return m;
    }, [subcategories]);

    const suggestions = useMemo(() => {
        if (axis === 'size') return sizes.map(s => s.display_name);
        if (axis === 'category') return categories;
        return subcategories.map(s => s.name);
    }, [axis, sizes, categories, subcategories]);

    const effVal = (r: Row) => r.value.trim() || defaultValue.trim();
    const validRows = rows.filter(r => r.barcode.trim() && effVal(r));

    const updateRow = (idx: number, field: keyof Row, value: string) => {
        setRows(prev => {
            const next = [...prev];
            next[idx] = { ...next[idx], [field]: value };
            if (idx === next.length - 1 && value) next.push(EMPTY());
            return next;
        });
    };

    const handlePaste = (e: React.ClipboardEvent, idx: number) => {
        const text = e.clipboardData.getData('text');
        if (!text.includes('\t') && !text.includes('\n')) return;
        e.preventDefault();
        const lines = text.trim().split('\n').filter(Boolean);
        setRows(prev => {
            const next = [...prev];
            for (let i = 0; i < lines.length; i++) {
                const cols = lines[i].split('\t');
                const rowIdx = idx + i;
                while (rowIdx >= next.length) next.push(EMPTY());
                next[rowIdx] = { barcode: (cols[0] || '').trim(), value: (cols[1] || '').trim() };
            }
            if (next[next.length - 1].barcode) next.push(EMPTY());
            return next;
        });
    };

    const save = async () => {
        if (!validRows.length) { setMsg('Нет данных для привязки'); return; }
        setSaving(true);
        setResult(null);
        try {
            const byValue = new Map<string, number[]>();
            const notFound: string[] = [];
            for (const r of validRows) {
                const bc = r.barcode.trim();
                const nm = barcodeMap[bc];
                if (nm == null) { notFound.push(bc); continue; }
                const v = effVal(r);
                if (!byValue.has(v)) byValue.set(v, []);
                byValue.get(v)!.push(nm);
            }
            const unknown: string[] = [];
            let updated = 0;
            for (const [value, nmIds] of byValue) {
                const uniq = [...new Set(nmIds)];
                if (axis === 'size') {
                    await api.bulkSetSizeOverride(uniq, value);
                    updated += uniq.length;
                } else if (axis === 'category') {
                    await api.bulkSetCategoryOverride(uniq, value);
                    updated += uniq.length;
                } else {
                    const id = subcatByName.get(value.trim().toLowerCase());
                    if (id == null) { unknown.push(value); continue; }
                    await api.bulkSetSubcategory(uniq, id);
                    updated += uniq.length;
                }
            }
            setResult({ updated, notFound, unknown });
            if (updated > 0) {
                onReload();
                // грид НЕ затираем, если остались ненайденные/неизвестные — чтобы доисправить вручную
                if (notFound.length === 0 && unknown.length === 0) { setRows(Array.from({ length: 6 }, EMPTY)); setDefaultValue(''); }
            }
        } catch (e: unknown) {
            setMsg(e instanceof Error ? e.message : 'Ошибка');
        }
        setSaving(false);
    };

    const axisCol = AXES.find(a => a.key === axis)!.col;
    const inputBase: React.CSSProperties = { fontSize: 13, fontFamily: 'monospace', padding: '6px 10px' };

    return (
        <div>
            <datalist id="bba-suggestions">
                {suggestions.map(s => <option key={s} value={s} />)}
            </datalist>

            {/* Axis selector */}
            <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
                {AXES.map(a => (
                    <button key={a.key} type="button" className={`sc-status-pill ${axis === a.key ? 'sc-status-pill-active' : ''}`}
                        onClick={() => { setAxis(a.key); setResult(null); }}>
                        {a.label}
                    </button>
                ))}
            </div>

            {loading ? (
                <div style={{ padding: 20, textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 13 }}>Загрузка баркодов…</div>
            ) : (
                <>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginBottom: 10 }}>
                        Вставьте из Excel: <b>Баркод</b>, <b>{AXES.find(a => a.key === axis)!.label}</b>. Колонку значения можно оставить пустой и задать одно значение для всех ниже.
                        {axis === 'subcat' && <span style={{ color: 'var(--color-text-dim)' }}> Под-категория должна совпадать по названию с существующей.</span>}
                    </div>

                    {/* Default value for empty rows */}
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12 }}>
                        <label className="form-label" style={{ fontSize: 12, margin: 0, whiteSpace: 'nowrap' }}>Значение для всех пустых:</label>
                        <input className="form-input" list="bba-suggestions" value={defaultValue} onChange={e => setDefaultValue(e.target.value)}
                            placeholder={axis === 'size' ? 'напр. 200x300' : axis === 'category' ? 'напр. Ковры' : 'напр. винтаж'}
                            style={{ width: 220, fontSize: 13 }} />
                    </div>

                    {/* Paste grid */}
                    <div style={{ background: 'var(--color-bg-input)', padding: 14, borderRadius: 10, marginBottom: 12 }}>
                        <div style={{ display: 'grid', gridTemplateColumns: '28px 1fr 200px', gap: '4px 8px', alignItems: 'start' }}>
                            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-dim)' }}>#</div>
                            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-dim)', letterSpacing: '0.05em' }}>БАРКОД</div>
                            <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-text-dim)', letterSpacing: '0.05em' }}>{axisCol}</div>

                            {rows.map((row, i) => {
                                const bc = row.barcode.trim();
                                const nm = bc ? barcodeMap[bc] : undefined;
                                const isFound = !!bc && nm != null;
                                const isNotFound = !!bc && nm == null;
                                const prod = nm != null ? productByNm.get(nm) : undefined;
                                return (
                                    <div key={i} style={{ display: 'contents' }}>
                                        <div style={{ fontSize: 12, color: 'var(--color-text-dim)', textAlign: 'center', paddingTop: 8 }}>{i + 1}</div>
                                        <div>
                                            <input className="form-input" value={row.barcode}
                                                onChange={e => updateRow(i, 'barcode', e.target.value)}
                                                onPaste={e => handlePaste(e, i)}
                                                placeholder="Баркод"
                                                style={{
                                                    ...inputBase, width: '100%',
                                                    borderColor: isFound ? 'var(--color-success)' : isNotFound ? 'var(--color-danger)' : undefined,
                                                    background: isFound ? 'rgba(52,199,89,0.06)' : isNotFound ? 'rgba(255,59,48,0.06)' : undefined,
                                                }} />
                                            {isFound && (
                                                <div style={{ fontSize: 10, color: 'var(--color-text-dim)', marginTop: 1 }}>
                                                    {prod ? `${prod.vendor_code}${prod.subject ? ` · ${prod.subject}` : ''}` : `nm_id ${nm}`}
                                                </div>
                                            )}
                                            {isNotFound && <div style={{ fontSize: 10, color: 'var(--color-danger)', marginTop: 1 }}>баркод не найден</div>}
                                        </div>
                                        <input className="form-input" list="bba-suggestions" value={row.value}
                                            onChange={e => updateRow(i, 'value', e.target.value)}
                                            placeholder={defaultValue ? `(${defaultValue})` : '—'}
                                            style={{ ...inputBase, fontFamily: 'inherit', width: '100%' }} />
                                    </div>
                                );
                            })}
                        </div>
                        <div style={{ display: 'flex', gap: 8, marginTop: 12, alignItems: 'center' }}>
                            <button type="button" className="btn btn-primary btn-sm" onClick={save} disabled={saving || !validRows.length}>
                                {saving ? '⏳ Применение…' : `Применить (${validRows.length})`}
                            </button>
                            <button type="button" className="btn btn-secondary btn-sm" onClick={() => { setRows(Array.from({ length: 6 }, EMPTY)); setDefaultValue(''); setResult(null); }}>Очистить</button>
                        </div>
                    </div>

                    {/* Result summary */}
                    {result && (
                        <div style={{ fontSize: 13, padding: '10px 12px', borderRadius: 10, background: result.updated ? 'rgba(52,199,89,0.08)' : 'rgba(255,159,10,0.08)' }}>
                            <div style={{ color: result.updated ? 'var(--color-success)' : 'var(--color-warning)', fontWeight: 600 }}>
                                ✓ Привязано товаров: {formatNumber(result.updated, 0)}
                            </div>
                            {result.notFound.length > 0 && (
                                <div style={{ color: 'var(--color-danger)', marginTop: 4 }}>
                                    Баркод не найден ({result.notFound.length}): {result.notFound.slice(0, 8).join(', ')}{result.notFound.length > 8 && ` …+${result.notFound.length - 8}`}
                                </div>
                            )}
                            {result.unknown.length > 0 && (
                                <div style={{ color: 'var(--color-danger)', marginTop: 4 }}>
                                    Нет такой под-категории ({result.unknown.length}): {result.unknown.join(', ')} — создайте её в «Справочниках».
                                </div>
                            )}
                        </div>
                    )}
                </>
            )}
        </div>
    );
}
