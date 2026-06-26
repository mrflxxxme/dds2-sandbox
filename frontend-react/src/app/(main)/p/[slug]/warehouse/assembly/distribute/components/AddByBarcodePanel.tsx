'use client';
import { useCallback, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { buildDraftRows, type DraftSkuInput } from '@/lib/assembly/buildDraftRows';
import type { BarcodeEligibilityResponse, BarcodeEligibilityItem, BarcodeEligibilityTarget, PackageType, AssemblyDraftRow } from '@/types/api';

const PKG_SHORT: Record<string, string> = { BOX: 'Короб', MONOPALLET: 'Моно', SUPERSAFE: 'Сейф' };
const PKG_BADGE: Record<string, string> = { BOX: 'badge-info', MONOPALLET: 'badge-warning', SUPERSAFE: 'badge-secondary' };
// Кап = бэкенд max_length (BarcodeEligibilityRequest): большой paste каталога раздул
// бы фан-аут в WB acceptance/options (≤150/чанк, бюджет 6 req/min) и выжег лимит проекта.
const MAX_BARCODES = 500;

/** Лучшая упаковка склада по приёмке (короб > моно > сейф); null — нигде не принимается. */
function bestPkg(t: BarcodeEligibilityTarget): PackageType | null {
    if (t.can_box) return 'BOX';
    if (t.can_monopallet) return 'MONOPALLET';
    if (t.can_supersafe) return 'SUPERSAFE';
    return null;
}

interface AddByBarcodePanelProps {
    nmPpb: Map<number, number | null>;
    nmBoxSize: Map<number, string | null>;
    palletOverrides: Record<string, number>;
    districtMap: Record<string, string>;
    /** Дозалить строки в черновик (родитель сначала флашит правки, потом merge+reload). */
    onAddRows: (rows: AssemblyDraftRow[]) => Promise<void>;
    onToast: (message: string, type: 'success' | 'error') => void;
}

/** Блок «Добавить по баркодам» (req B): массовый paste → eligibility (склады по
 *  лимитам приёмки + свободный сток ФФ) → авто-раскладка по свободному стоку
 *  целыми паллетами → дозалив в черновик. */
export default function AddByBarcodePanel({
    nmPpb, nmBoxSize, palletOverrides, districtMap, onAddRows, onToast,
}: AddByBarcodePanelProps) {
    const [open, setOpen] = useState(false);
    const [text, setText] = useState('');
    const [checking, setChecking] = useState(false);
    const [resp, setResp] = useState<BarcodeEligibilityResponse | null>(null);
    // checked по БАРКОДУ (не nm_id): items приходят per-barcode, у одной карточки
    // может быть несколько баркодов — ключ по nm_id коллизил бы их чекбоксы.
    const [checked, setChecked] = useState<Set<string>>(new Set());
    const [wholePallets, setWholePallets] = useState(true);
    const [adding, setAdding] = useState(false);

    const parsedBarcodes = useMemo(() => [...new Set(text.split(/[\s,;]+/).map(s => s.trim()).filter(Boolean))], [text]);

    const handleCheck = useCallback(async () => {
        if (parsedBarcodes.length === 0) { onToast('Вставьте баркоды', 'error'); return; }
        const list = parsedBarcodes.slice(0, MAX_BARCODES);
        if (parsedBarcodes.length > MAX_BARCODES) onToast(`Слишком много баркодов — проверены первые ${MAX_BARCODES}`, 'error');
        setChecking(true);
        try {
            const r = await api.getBarcodeEligibility(list);
            setResp(r);
            setChecked(new Set(r.items.filter(i => i.targets.some(t => bestPkg(t)) && i.ff_stock.some(s => s.available > 0)).map(i => i.barcode)));
        } catch (e: unknown) {
            onToast(e instanceof Error ? e.message : 'Ошибка проверки приёмки', 'error');
        } finally {
            setChecking(false);
        }
    }, [parsedBarcodes, onToast]);

    const toggleOne = useCallback((bc: string) => {
        setChecked(prev => { const n = new Set(prev); if (n.has(bc)) n.delete(bc); else n.add(bc); return n; });
    }, []);

    const handleAdd = useCallback(async () => {
        if (!resp || checked.size === 0) return;
        setAdding(true);
        try {
            const skus: DraftSkuInput[] = [];
            for (const item of resp.items) {
                if (!checked.has(item.barcode)) continue;
                const packageByWh: Record<string, PackageType> = {};
                const eligible: string[] = [];
                for (const t of item.targets) {
                    const p = bestPkg(t);
                    if (p) { packageByWh[t.wb_name] = p; eligible.push(t.wb_name); }
                }
                const ffStock: Record<number, number> = {};
                for (const s of item.ff_stock) if (s.available > 0) ffStock[s.ff_id] = s.available;
                const totalFree = Object.values(ffStock).reduce((a, v) => a + v, 0);
                if (eligible.length === 0 || totalFree <= 0) continue;
                // Авто-раскладка: равный вес по eligible-складам — buildDraftRows распределит
                // свободный сток целыми коробами и сведёт крошки в целые паллеты по округу.
                const target: Record<string, number> = {};
                for (const wb of eligible) target[wb] = totalFree;
                skus.push({ nm_id: item.nm_id, barcode: item.barcode, vendor_code: item.vendor_code, target, ffStock, ppb: nmPpb.get(item.nm_id), box_size: nmBoxSize.get(item.nm_id), packageByWh });
            }
            if (skus.length === 0) { onToast('У выбранных баркодов нет свободного стока или доступных складов', 'error'); setAdding(false); return; }
            const rows = buildDraftRows({ skus, wholePalletsOnly: wholePallets, warehouseToDistrict: districtMap, palletOverrides });
            if (rows.length === 0) {
                onToast(wholePallets ? 'Не набралось целых паллет — снимите «только целые»' : 'Не удалось разложить', 'error');
                setAdding(false);
                return;
            }
            const units = rows.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
            // Часть выбранных SKU могла не набрать целой паллеты → buildDraftRows их
            // не вернул. Сообщаем счётчик и ОСТАВЛЯЕМ их отмеченными (не теряем выбор).
            const addedBc = new Set(rows.map(r => r.barcode));
            const droppedBc = skus.filter(s => !addedBc.has(s.barcode)).map(s => s.barcode);
            await onAddRows(rows);
            onToast(`Добавлено строк: ${formatNumber(rows.length, 0)} · Σ ${formatNumber(units, 0)} шт${droppedBc.length > 0 ? ` · ${formatNumber(droppedBc.length, 0)} пропущено (< целой паллеты)` : ''}`, 'success');
            setChecked(new Set(droppedBc));
        } catch (e: unknown) {
            onToast(e instanceof Error ? e.message : 'Ошибка добавления', 'error');
        } finally {
            setAdding(false);
        }
    }, [resp, checked, nmPpb, nmBoxSize, wholePallets, districtMap, palletOverrides, onToast, onAddRows]);

    const renderTarget = (t: BarcodeEligibilityTarget) => {
        const p = bestPkg(t);
        return (
            <span key={t.wb_name} style={{ display: 'inline-flex', alignItems: 'center', gap: 3, marginRight: 6, marginBottom: 3 }}>
                <span style={{ fontSize: 11 }}>{t.wb_name}</span>
                {p && <span className={`badge ${PKG_BADGE[p]}`} style={{ fontSize: 9 }}>{PKG_SHORT[p]}</span>}
                {t.no_limit
                    ? <span title="Нет лимита — нужна предзаявка" style={{ fontSize: 10, color: 'var(--color-warning)' }}>⌛</span>
                    : <span title={`Бесплатных дней: ${t.free_days_14}/14`} style={{ fontSize: 10, color: t.free_days_14 > 0 ? 'var(--color-success)' : 'var(--color-text-muted)' }}>{t.free_days_14 > 0 ? `✓${t.free_days_14}` : '₽'}</span>}
            </span>
        );
    };

    return (
        <div className="glass-card" style={{ padding: 12, marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <button className="btn btn-secondary btn-sm" onClick={() => setOpen(o => !o)}>
                    {open ? '▾' : '▸'} 📋 Добавить по баркодам
                </button>
                {!open && <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>массовая загрузка баркодов → доступные склады по лимитам и свободному стоку</span>}
            </div>

            {open && (
                <div style={{ marginTop: 12 }}>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                        <textarea
                            className="form-input"
                            placeholder="Вставьте баркоды (по одному в строке или через пробел/запятую)…"
                            value={text}
                            onChange={e => setText(e.target.value)}
                            rows={3}
                            style={{ flex: 1, minWidth: 260, fontSize: 12, fontFamily: 'monospace' }}
                        />
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                            <button className="btn btn-secondary btn-sm" onClick={handleCheck} disabled={checking || parsedBarcodes.length === 0}>
                                {checking ? 'Проверка…' : `🔍 Проверить (${parsedBarcodes.length})`}
                            </button>
                        </div>
                    </div>

                    {resp && (
                        <div style={{ marginTop: 10 }}>
                            {resp.unknown.length > 0 && (
                                <div style={{ fontSize: 12, color: 'var(--color-warning)', marginBottom: 6 }}>
                                    ⚠ Не найдено в номенклатуре: {resp.unknown.join(', ')}
                                </div>
                            )}
                            {resp.items.length === 0 ? (
                                <div style={{ padding: 12, textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 13 }}>Ни один баркод не распознан.</div>
                            ) : (
                                <>
                                    <div style={{ maxHeight: 340, overflowY: 'auto' }}>
                                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                                            <thead style={{ position: 'sticky', top: 0, background: 'var(--color-bg-card)' }}>
                                                <tr style={{ color: 'var(--color-text-muted)', fontSize: 11, textAlign: 'left' }}>
                                                    <th style={{ padding: '4px 8px', width: 28 }} />
                                                    <th style={{ padding: '4px 8px', fontWeight: 600 }}>Артикул / баркод</th>
                                                    <th style={{ padding: '4px 8px', fontWeight: 600 }}>Доступные склады (упаковка · лимит)</th>
                                                    <th style={{ padding: '4px 8px', fontWeight: 600, textAlign: 'right' }}>Свободно ФФ</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {resp.items.map((item: BarcodeEligibilityItem) => {
                                                    const free = item.ff_stock.reduce((s, x) => s + x.available, 0);
                                                    const usable = item.targets.some(t => bestPkg(t)) && free > 0;
                                                    return (
                                                        <tr key={item.barcode} style={{ borderBottom: '1px solid var(--color-border)', verticalAlign: 'top', opacity: usable ? 1 : 0.5 }}>
                                                            <td style={{ padding: '5px 8px' }}>
                                                                <input type="checkbox" checked={checked.has(item.barcode)} disabled={!usable} onChange={() => toggleOne(item.barcode)} />
                                                            </td>
                                                            <td style={{ padding: '5px 8px' }}>
                                                                <div style={{ fontWeight: 600 }}>{item.vendor_code}</div>
                                                                <div style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>{item.barcode}</div>
                                                            </td>
                                                            <td style={{ padding: '5px 8px' }}>
                                                                {item.targets.length === 0
                                                                    ? <span style={{ color: 'var(--color-danger)', fontSize: 11 }}>нет доступных складов</span>
                                                                    : <div style={{ display: 'flex', flexWrap: 'wrap' }}>{item.targets.map(renderTarget)}</div>}
                                                            </td>
                                                            <td style={{ padding: '5px 8px', textAlign: 'right', color: free > 0 ? 'var(--color-success)' : 'var(--color-text-muted)' }}>{formatNumber(free, 0)}</td>
                                                        </tr>
                                                    );
                                                })}
                                            </tbody>
                                        </table>
                                    </div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 8, flexWrap: 'wrap' }}>
                                        <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 4 }} title="Класть только целые паллеты">
                                            <input type="checkbox" checked={wholePallets} onChange={e => setWholePallets(e.target.checked)} /> только целые паллеты
                                        </label>
                                        <button className="btn btn-primary btn-sm" onClick={handleAdd} disabled={adding || checked.size === 0}>
                                            {adding ? 'Добавление…' : `Разложить и добавить (${checked.size})`}
                                        </button>
                                        <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>раскладка по свободному стоку; поправьте в редакторе ниже</span>
                                    </div>
                                </>
                            )}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
