'use client';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { buildDraftRows, type DraftSkuInput } from '@/lib/assembly/buildDraftRows';
import type { StockNeedResponse, ColdStartTableRow, AssemblyDraftRow } from '@/types/api';

interface Candidate {
    nm: number;
    vendor: string;
    brand: string;
    subject: string;
    /** Потребность WB (сколько надо). Для новинок — n/a (allocation-driven). */
    need: number;
    /** Реально можно отгрузить сейчас: обычные — can_send (с учётом свободного ФФ,
     *  в сборке, в пути), новинки — total_allocated (кап cold-start по свободному ФФ). */
    canSend: number;
    free: number;
    isNew: boolean;
}

interface AddFromNeedPanelProps {
    /** Полный ответ потребности (из родителя) — обычные кандидаты + per-WB need. */
    stockNeed: StockNeedResponse | null;
    /** nm_id уже в черновике (rows + handed_units) — исключаем из кандидатов. */
    existingNmIds: Set<number>;
    nmPpb: Map<number, number | null>;
    nmBoxSize: Map<number, string | null>;
    palletOverrides: Record<string, number>;
    /** WB-склад → ключ округа (для кросс-SKU палет-консолидации в buildDraftRows). */
    districtMap: Record<string, string>;
    /** Дозалить строки в черновик (родитель сначала флашит правки, потом merge+reload). */
    onAddRows: (rows: AssemblyDraftRow[]) => Promise<void>;
    onToast: (message: string, type: 'success' | 'error') => void;
}

const ROW_CAP = 400;

/** Блок «Добавить из потребности» (req A): упрощённая таблица обычных SKU и новинок
 *  с вычетом уже добавленных. Отмеченные раскладываются целыми паллетами и
 *  дозаливаются в черновик через серверный merge. */
export default function AddFromNeedPanel({
    stockNeed, existingNmIds, nmPpb, nmBoxSize, palletOverrides, districtMap, onAddRows, onToast,
}: AddFromNeedPanelProps) {
    const [open, setOpen] = useState(false);
    const [tab, setTab] = useState<'regular' | 'newcomer'>('regular');
    const [query, setQuery] = useState('');
    const [brand, setBrand] = useState('');
    const [subject, setSubject] = useState('');
    const [deficitOnly, setDeficitOnly] = useState(false);
    const [wholePallets, setWholePallets] = useState(true);
    const [checked, setChecked] = useState<Set<number>>(new Set());
    const [adding, setAdding] = useState(false);

    const [coldRows, setColdRows] = useState<ColdStartTableRow[]>([]);
    const [coldLoaded, setColdLoaded] = useState(false);

    // Новинки грузим лениво при первом открытии вкладки.
    useEffect(() => {
        if (!open || coldLoaded) return;
        let cancelled = false;
        api.getColdStartTable()
            .then(resp => { if (!cancelled) { setColdRows(resp.rows || []); setColdLoaded(true); } })
            .catch(() => { if (!cancelled) setColdLoaded(true); });
        return () => { cancelled = true; };
    }, [open, coldLoaded]);

    const regularByNm = useMemo(() => {
        const m = new Map<number, StockNeedResponse['articles'][number]>();
        for (const a of stockNeed?.articles ?? []) m.set(a.nm_id, a);
        return m;
    }, [stockNeed]);
    const newcomerByNm = useMemo(() => {
        const m = new Map<number, ColdStartTableRow>();
        for (const r of coldRows) m.set(r.nm_id, r);
        return m;
    }, [coldRows]);

    const regularCandidates = useMemo<Candidate[]>(() => {
        const out: Candidate[] = [];
        for (const a of stockNeed?.articles ?? []) {
            // Только то, что РЕАЛЬНО можно сдать сейчас: can_send>0 (свободный ФФ под
            // потребность за вычетом уже-в-сборке/в-пути). Потребность без отгружаемого
            // остатка не показываем — её всё равно нельзя добавить в заявку.
            if (existingNmIds.has(a.nm_id) || a.can_send <= 0) continue;
            const free = Object.values(a.rf_stocks || {}).reduce((s, v) => s + (v.available || 0), 0);
            out.push({ nm: a.nm_id, vendor: a.vendor_code, brand: a.brand, subject: a.subject, need: a.total_need, canSend: a.can_send, free, isNew: false });
        }
        return out.sort((x, y) => y.canSend - x.canSend);
    }, [stockNeed, existingNmIds]);

    const newcomerCandidates = useMemo<Candidate[]>(() => {
        const out: Candidate[] = [];
        for (const r of coldRows) {
            // total_allocated — сколько cold-start реально отгружает (кап по свободному ФФ).
            if (existingNmIds.has(r.nm_id) || r.total_allocated <= 0) continue;
            out.push({ nm: r.nm_id, vendor: r.article_seller || `nm:${r.nm_id}`, brand: r.brand || '', subject: r.subject || '', need: r.total_allocated, canSend: r.total_allocated, free: r.rf_qty, isNew: true });
        }
        return out.sort((x, y) => y.canSend - x.canSend);
    }, [coldRows, existingNmIds]);

    const baseCandidates = tab === 'regular' ? regularCandidates : newcomerCandidates;

    const brandOptions = useMemo(() => [...new Set(baseCandidates.map(c => c.brand).filter(Boolean))].sort(), [baseCandidates]);
    const subjectOptions = useMemo(() => [...new Set(baseCandidates.map(c => c.subject).filter(Boolean))].sort(), [baseCandidates]);

    const filtered = useMemo(() => {
        const q = query.trim().toLowerCase();
        return baseCandidates.filter(c => {
            if (brand && c.brand !== brand) return false;
            if (subject && c.subject !== subject) return false;
            if (deficitOnly && tab === 'regular') {
                const a = regularByNm.get(c.nm);
                if (!a || a.deficit <= 0) return false;
            }
            if (q && !c.vendor.toLowerCase().includes(q)) return false;
            return true;
        });
    }, [baseCandidates, brand, subject, deficitOnly, tab, query, regularByNm]);

    const shown = filtered.slice(0, ROW_CAP);
    const checkedCount = checked.size;

    const toggleOne = useCallback((nm: number) => {
        setChecked(prev => { const n = new Set(prev); if (n.has(nm)) n.delete(nm); else n.add(nm); return n; });
    }, []);
    const toggleAllShown = useCallback(() => {
        setChecked(prev => {
            const allOn = shown.every(c => prev.has(c.nm));
            const n = new Set(prev);
            for (const c of shown) { if (allOn) n.delete(c.nm); else n.add(c.nm); }
            return n;
        });
    }, [shown]);

    const handleAdd = useCallback(async () => {
        if (checked.size === 0) return;
        setAdding(true);
        try {
            const skus: DraftSkuInput[] = [];
            for (const nm of checked) {
                const reg = regularByNm.get(nm);
                if (reg) {
                    const target: Record<string, number> = {};
                    for (const w of stockNeed?.warehouses ?? []) {
                        const need = w.articles?.[nm]?.need || 0;
                        if (need > 0) target[w.name] = need;
                    }
                    const ffStock: Record<number, number> = {};
                    for (const [ff, st] of Object.entries(reg.rf_stocks || {})) {
                        if ((st.available || 0) > 0) ffStock[Number(ff)] = st.available;
                    }
                    if (Object.keys(target).length === 0 || Object.keys(ffStock).length === 0) continue;
                    skus.push({ nm_id: nm, barcode: reg.barcode, vendor_code: reg.vendor_code, target, ffStock, ppb: nmPpb.get(nm), box_size: nmBoxSize.get(nm), packageType: 'BOX' });
                    continue;
                }
                const nc = newcomerByNm.get(nm);
                if (nc) {
                    const target = Object.fromEntries(Object.entries(nc.allocations || {}).filter(([, q]) => q > 0));
                    const ffStock: Record<number, number> = {};
                    for (const [ff, q] of Object.entries(nc.rf_by_warehouse || {})) if ((q || 0) > 0) ffStock[Number(ff)] = q;
                    if (Object.keys(target).length === 0 || Object.keys(ffStock).length === 0) continue;
                    skus.push({ nm_id: nm, barcode: nc.barcode || '', vendor_code: nc.article_seller || `nm:${nm}`, target, ffStock, ppb: nmPpb.get(nm), box_size: nmBoxSize.get(nm), packageType: 'BOX' });
                }
            }
            if (skus.length === 0) { onToast('Нет свободного стока/потребности у выбранных SKU', 'error'); setAdding(false); return; }
            const rows = buildDraftRows({ skus, wholePalletsOnly: wholePallets, warehouseToDistrict: districtMap, palletOverrides });
            if (rows.length === 0) {
                onToast(wholePallets ? 'Не набралось целых паллет — снимите «только целые» или добавьте больше SKU' : 'Не удалось разложить выбранные SKU', 'error');
                setAdding(false);
                return;
            }
            const units = rows.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
            // Часть выбранных SKU могла не набрать целой паллеты → не вернулись строкой.
            // Сообщаем счётчик и оставляем их отмеченными (не теряем выбор молча).
            const addedKeys = new Set(rows.map(r => `${r.nm_id}::${r.barcode}`));
            const droppedNms = skus.filter(s => !addedKeys.has(`${s.nm_id}::${s.barcode}`)).map(s => s.nm_id);
            await onAddRows(rows);
            onToast(`Добавлено строк: ${formatNumber(rows.length, 0)} · Σ ${formatNumber(units, 0)} шт${droppedNms.length > 0 ? ` · ${formatNumber(droppedNms.length, 0)} пропущено (< целой паллеты)` : ''}`, 'success');
            setChecked(new Set(droppedNms));
        } catch (e: unknown) {
            onToast(e instanceof Error ? e.message : 'Ошибка добавления', 'error');
        } finally {
            setAdding(false);
        }
    }, [checked, regularByNm, newcomerByNm, stockNeed, nmPpb, nmBoxSize, wholePallets, districtMap, palletOverrides, onToast, onAddRows]);

    return (
        <div className="glass-card" style={{ padding: 12, marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <button className="btn btn-secondary btn-sm" onClick={() => setOpen(o => !o)}>
                    {open ? '▾' : '▸'} ➕ Добавить из потребности
                </button>
                {!open && <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>обычные и новинки — только то, что реально можно отгрузить сейчас (свободный ФФ), целыми паллетами</span>}
                {open && checkedCount > 0 && (
                    <button className="btn btn-primary btn-sm" onClick={handleAdd} disabled={adding}>
                        {adding ? 'Добавление…' : `Добавить отмеченные (${checkedCount})`}
                    </button>
                )}
            </div>

            {open && (
                <div style={{ marginTop: 12 }}>
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8 }}>
                        <button className={`btn btn-sm ${tab === 'regular' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setTab('regular')}>
                            Обычные ({formatNumber(regularCandidates.length, 0)})
                        </button>
                        <button className={`btn btn-sm ${tab === 'newcomer' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setTab('newcomer')}>
                            🆕 Новинки ({coldLoaded ? formatNumber(newcomerCandidates.length, 0) : '…'})
                        </button>
                        <div style={{ width: 1, alignSelf: 'stretch', background: 'var(--color-border)', margin: '0 4px' }} />
                        <input type="text" className="form-input" placeholder="🔍 Артикул…" value={query} onChange={e => setQuery(e.target.value)} style={{ maxWidth: 180, fontSize: 12 }} />
                        <select className="form-input" value={brand} onChange={e => setBrand(e.target.value)} style={{ maxWidth: 150, fontSize: 12 }}>
                            <option value="">Все бренды</option>
                            {brandOptions.map(b => <option key={b} value={b}>{b}</option>)}
                        </select>
                        <select className="form-input" value={subject} onChange={e => setSubject(e.target.value)} style={{ maxWidth: 150, fontSize: 12 }}>
                            <option value="">Все категории</option>
                            {subjectOptions.map(s => <option key={s} value={s}>{s}</option>)}
                        </select>
                        {tab === 'regular' && (
                            <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 4 }}>
                                <input type="checkbox" checked={deficitOnly} onChange={e => setDeficitOnly(e.target.checked)} /> только дефицит
                            </label>
                        )}
                        <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 4 }} title="Класть только целые паллеты (хвост < паллеты остаётся на ФФ)">
                            <input type="checkbox" checked={wholePallets} onChange={e => setWholePallets(e.target.checked)} /> только целые паллеты
                        </label>
                    </div>

                    {!stockNeed && tab === 'regular' ? (
                        <div style={{ padding: 16, textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 13 }}>Потребность не загружена.</div>
                    ) : shown.length === 0 ? (
                        <div style={{ padding: 16, textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 13 }}>
                            {tab === 'newcomer' && !coldLoaded ? 'Загрузка новинок…' : 'Нет кандидатов (всё уже в черновике или не проходит фильтр).'}
                        </div>
                    ) : (
                        <div style={{ maxHeight: 360, overflowY: 'auto' }}>
                            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                                <thead style={{ position: 'sticky', top: 0, background: 'var(--color-bg-card)' }}>
                                    <tr style={{ color: 'var(--color-text-muted)', fontSize: 11, textAlign: 'left' }}>
                                        <th style={{ padding: '4px 8px', width: 28 }}>
                                            <input type="checkbox" checked={shown.length > 0 && shown.every(c => checked.has(c.nm))} onChange={toggleAllShown} title="Отметить всё показанное" />
                                        </th>
                                        <th style={{ padding: '4px 8px', fontWeight: 600 }}>Артикул</th>
                                        <th style={{ padding: '4px 8px', fontWeight: 600 }}>Бренд · Категория</th>
                                        <th style={{ padding: '4px 8px', fontWeight: 600, textAlign: 'right' }} title="Сколько надо WB (потребность)">Потребность</th>
                                        <th style={{ padding: '4px 8px', fontWeight: 600, textAlign: 'right' }} title="Реально можно отгрузить сейчас — попадёт в заявку">✅ Можно отправить</th>
                                        <th style={{ padding: '4px 8px', fontWeight: 600, textAlign: 'right' }} title="Свободный остаток на ФФ">Свободно ФФ</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {shown.map(c => (
                                        <tr key={c.nm} style={{ borderBottom: '1px solid var(--color-border)', cursor: 'pointer' }} onClick={() => toggleOne(c.nm)}>
                                            <td style={{ padding: '5px 8px' }} onClick={e => e.stopPropagation()}>
                                                <input type="checkbox" checked={checked.has(c.nm)} onChange={() => toggleOne(c.nm)} />
                                            </td>
                                            <td style={{ padding: '5px 8px', fontWeight: 600 }}>
                                                {c.isNew && <span style={{ marginRight: 4, color: '#a855f7', fontWeight: 700 }}>🆕</span>}{c.vendor}
                                            </td>
                                            <td style={{ padding: '5px 8px', color: 'var(--color-text-muted)' }}>{[c.brand, c.subject].filter(Boolean).join(' · ')}</td>
                                            <td style={{ padding: '5px 8px', textAlign: 'right', color: 'var(--color-text-muted)' }}>{c.isNew ? '—' : formatNumber(c.need, 0)}</td>
                                            <td style={{ padding: '5px 8px', textAlign: 'right', fontWeight: 700, color: 'var(--color-success)' }} title="Реально отгружаемое сейчас">{formatNumber(c.canSend, 0)}</td>
                                            <td style={{ padding: '5px 8px', textAlign: 'right', color: c.free > 0 ? 'var(--color-text)' : 'var(--color-text-muted)' }}>{formatNumber(c.free, 0)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                            {filtered.length > ROW_CAP && (
                                <div style={{ padding: '6px 8px', fontSize: 11, color: 'var(--color-text-muted)' }}>
                                    Показано {ROW_CAP} из {formatNumber(filtered.length, 0)} — уточните фильтр.
                                </div>
                            )}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
