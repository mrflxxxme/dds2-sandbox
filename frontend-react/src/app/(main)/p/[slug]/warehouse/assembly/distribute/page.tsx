'use client';
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { Toast } from '@/components';
import type {
    AssemblyDraft,
    AssemblyDraftDistribution,
    AssemblyDraftRow,
    HandedUnit,
    PackageType,
    Warehouse,
} from '@/types/api';

interface StockNeedArticle {
    nm_id: number;
    vendor_code: string;
    barcode?: string;
    total_need: number;
    rf_stocks: Record<number, { stock: number; available: number }>;
}

interface StockNeedWarehouseRow {
    name: string;
    articles: Record<number, { need: number; stock: number; avg_daily: number }>;
}

interface StockNeedResponse {
    warehouses: StockNeedWarehouseRow[];
    articles: StockNeedArticle[];
}

const AUTOSAVE_DEBOUNCE_MS = 5000;

export default function AssemblyDistributePage() {
    const params = useParams();
    const router = useRouter();
    const searchParams = useSearchParams();
    const slug = params.slug as string;

    const draftIdParam = searchParams.get('draft');
    const draftId = draftIdParam ? Number(draftIdParam) : null;

    // ─── State ───────────────────────────────────────────────────────────
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

    const [draft, setDraft] = useState<AssemblyDraft | null>(null);
    const [name, setName] = useState('');
    const [editingName, setEditingName] = useState(false);
    const [comment, setComment] = useState('');
    const [estimatedReadyDate, setEstimatedReadyDate] = useState<string>('');
    const [palletsCount, setPalletsCount] = useState<number>(1);
    const [palletWeightKg, setPalletWeightKg] = useState<number>(0);

    const [sourceWarehouseIds, setSourceWarehouseIds] = useState<number[]>([]);
    const [targetWarehouseNames, setTargetWarehouseNames] = useState<string[]>([]);
    const [rows, setRows] = useState<AssemblyDraftRow[]>([]);
    const [pkgTab, setPkgTab] = useState<'BOX' | 'MONOPALLET'>('BOX');
    const [multFilter, setMultFilter] = useState<'none' | 'with' | 'without'>('none');
    const [typeTab, setTypeTab] = useState<'all' | 'regular' | 'newcomer'>('all');
    const [nmPpb, setNmPpb] = useState<Map<number, number | null>>(new Map());
    const [coldStartShares, setColdStartShares] = useState<Record<string, number> | null>(null);
    // Замороженные заявки (передан на ФФ) — вырезаны из rows, на distribute не
    // редактируются. Храним, чтобы автосейв/сохранение не затёрло их.
    const [handedUnits, setHandedUnits] = useState<HandedUnit[]>([]);
    const [newcomerNmIds, setNewcomerNmIds] = useState<Set<number>>(new Set());

    // Reference data
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [stockNeed, setStockNeed] = useState<StockNeedResponse | null>(null);

    // Track what's saved so we don't loop autosaves
    const lastSavedJsonRef = useRef<string>('');
    const initialLoadRef = useRef(false);

    // ─── Load draft + reference data ─────────────────────────────────────
    useEffect(() => {
        if (!draftId) {
            setError('Не указан ID черновика');
            setLoading(false);
            return;
        }

        let cancelled = false;
        const load = async () => {
            setLoading(true);
            setError(null);
            try {
                const [draftResp, whs, stockNeedResp] = await Promise.all([
                    api.getAssemblyDraft(draftId),
                    api.getWarehouses(),
                    api.getStockNeed(14, 14, 'actual').catch(() => null) as Promise<StockNeedResponse | null>,
                ]);
                if (cancelled) return;
                setDraft(draftResp);
                setName(draftResp.name);
                setComment(draftResp.comment || '');
                setEstimatedReadyDate(draftResp.distribution.estimated_ready_date || '');
                setPalletsCount(draftResp.distribution.pallets_count || 1);
                setPalletWeightKg(draftResp.distribution.pallet_weight_kg || 0);
                setSourceWarehouseIds(draftResp.distribution.source_warehouse_ids || []);
                setTargetWarehouseNames(draftResp.distribution.target_warehouse_names || []);
                setRows(draftResp.distribution.rows || []);
                setColdStartShares(draftResp.distribution.cold_start_shares || null);
                setHandedUnits(draftResp.distribution.handed_units || []);
                setNewcomerNmIds(new Set(draftResp.newcomer_nm_ids || []));
                setWarehouses(whs);
                setStockNeed(stockNeedResp);
                lastSavedJsonRef.current = JSON.stringify(draftResp.distribution);
                initialLoadRef.current = true;
            } catch (e: unknown) {
                if (!cancelled) {
                    setError(e instanceof Error ? e.message : 'Ошибка загрузки черновика');
                }
            } finally {
                if (!cancelled) setLoading(false);
            }
        };

        load();
        return () => { cancelled = true; };
    }, [draftId]);

    // Кратность коробки per nm — для бейджа у артикула.
    useEffect(() => {
        let cancelled = false;
        api.getBoxMultiplicity()
            .then(resp => {
                if (cancelled) return;
                const m = new Map<number, number | null>();
                for (const r of resp.items) {
                    let ppb: number | null = null;
                    if (r.box_qty_override && r.box_qty_override > 0 && r.use_box_multiplicity) {
                        ppb = r.box_qty_override;
                    } else {
                        let best = 0;
                        for (const p of r.per_warehouse) {
                            if (p.box_qty && p.box_qty > 0 && p.use_box_multiplicity && (best === 0 || p.box_qty < best)) {
                                best = p.box_qty;
                            }
                        }
                        ppb = best > 0 ? best : null;
                    }
                    m.set(r.nm_id, ppb);
                }
                setNmPpb(m);
            })
            .catch(() => { /* best-effort */ });
        return () => { cancelled = true; };
    }, []);

    // ─── Build current distribution snapshot ─────────────────────────────
    const buildDistribution = useCallback((): AssemblyDraftDistribution => ({
        source_warehouse_ids: sourceWarehouseIds,
        target_warehouse_names: targetWarehouseNames,
        rows,
        pallets_count: palletsCount,
        pallet_weight_kg: palletWeightKg,
        estimated_ready_date: estimatedReadyDate || null,
        cold_start_shares: coldStartShares,
        handed_units: handedUnits,
    }), [sourceWarehouseIds, targetWarehouseNames, rows, palletsCount, palletWeightKg, estimatedReadyDate, coldStartShares, handedUnits]);

    // ─── Save draft (manual + autosave) ──────────────────────────────────
    const saveDraft = useCallback(async (silent = false): Promise<boolean> => {
        if (!draftId) return false;
        const dist = buildDistribution();
        const json = JSON.stringify(dist);
        if (json === lastSavedJsonRef.current && !silent) return true;

        setSaving(true);
        try {
            const updated = await api.updateAssemblyDraft(draftId, {
                name,
                distribution: dist,
                comment: comment || null,
            });
            lastSavedJsonRef.current = JSON.stringify(updated.distribution);
            if (!silent) setToast({ message: 'Черновик сохранён', type: 'success' });
            return true;
        } catch (e: unknown) {
            setToast({ message: e instanceof Error ? e.message : 'Ошибка сохранения', type: 'error' });
            return false;
        } finally {
            setSaving(false);
        }
    }, [draftId, buildDistribution, name, comment]);

    // ─── Autosave: debounce 5s after any change ──────────────────────────
    useEffect(() => {
        if (!initialLoadRef.current || !draftId) return;
        const timer = setTimeout(() => {
            const json = JSON.stringify(buildDistribution());
            if (json !== lastSavedJsonRef.current) {
                saveDraft(true).catch(() => {});
            }
        }, AUTOSAVE_DEBOUNCE_MS);
        return () => clearTimeout(timer);
    }, [rows, sourceWarehouseIds, targetWarehouseNames, palletsCount, palletWeightKg, estimatedReadyDate, name, comment, draftId, buildDistribution, saveDraft]);

    // ─── Lookup helpers ──────────────────────────────────────────────────
    const warehouseNameById = useCallback((id: number): string => {
        const wh = warehouses.find(w => w.id === id);
        return wh ? wh.name : `Склад ${id}`;
    }, [warehouses]);

    const initialNeedByNmId = useMemo(() => {
        const m: Record<number, number> = {};
        if (stockNeed?.articles) {
            for (const a of stockNeed.articles) {
                m[a.nm_id] = a.total_need || 0;
            }
        }
        return m;
    }, [stockNeed]);

    const wbNeedByNmIdAndWh = useCallback((nmId: number, whName: string): number => {
        if (!stockNeed?.warehouses) return 0;
        const wh = stockNeed.warehouses.find(w => w.name === whName);
        return wh?.articles?.[nmId]?.need || 0;
    }, [stockNeed]);

    const availableAtFf = useCallback((nmId: number, ffId: number): number => {
        if (!stockNeed?.articles) return 0;
        const a = stockNeed.articles.find(art => art.nm_id === nmId);
        return a?.rf_stocks?.[ffId]?.available || 0;
    }, [stockNeed]);

    // ─── Aggregate Σ src per FF (across all rows) ───────────────────────
    // ─── Вкладки Короб/Моно: строки активного типа упаковки ───────────────
    // Короб-вкладка показывает всё кроме моно (BOX + редкий SUPERSAFE), чтобы
    // ни одна строка не потерялась между двумя вкладками.
    // ─── Две независимых оси фильтра: упаковка (короб/моно) × тип товара ───
    const pkgMatch = useCallback(
        (r: AssemblyDraftRow) => pkgTab === 'MONOPALLET'
            ? r.package_type === 'MONOPALLET'
            : r.package_type !== 'MONOPALLET',
        [pkgTab],
    );
    const typeMatch = useCallback((r: AssemblyDraftRow) => {
        if (typeTab === 'all') return true;
        const isNew = newcomerNmIds.has(r.nm_id);
        return typeTab === 'newcomer' ? isNew : !isNew;
    }, [typeTab, newcomerNmIds]);

    // Есть ли в черновике новинки — определяет показ сегмента «Тип товара».
    const hasNewcomerRows = useMemo(
        () => rows.some(r => newcomerNmIds.has(r.nm_id)), [rows, newcomerNmIds],
    );

    // committableRows — то, что реально уйдёт в commit (упаковка × тип товара).
    // multFilter («Кратные / Без кратности») — только визуальный фильтр и на
    // состав сборки НЕ влияет.
    const committableRows = useMemo(
        () => rows.filter(r => pkgMatch(r) && typeMatch(r)),
        [rows, pkgMatch, typeMatch],
    );
    const visibleRows = useMemo(
        () => committableRows.filter(r => {
            if (multFilter === 'none') return true;
            const hasK = !!nmPpb.get(r.nm_id);
            return multFilter === 'with' ? hasK : !hasK;
        }),
        [committableRows, multFilter, nmPpb],
    );

    // Счётчики сегментов — каждый учитывает выбор по ВТОРОЙ оси.
    const rowsForPkg = useMemo(() => rows.filter(pkgMatch), [rows, pkgMatch]);
    const rowsForType = useMemo(() => rows.filter(typeMatch), [rows, typeMatch]);
    const allTypeCount = rowsForPkg.length;
    const regularCount = useMemo(
        () => rowsForPkg.filter(r => !newcomerNmIds.has(r.nm_id)).length, [rowsForPkg, newcomerNmIds],
    );
    const newcomerCount = useMemo(
        () => rowsForPkg.filter(r => newcomerNmIds.has(r.nm_id)).length, [rowsForPkg, newcomerNmIds],
    );
    const boxRowCount = useMemo(() => rowsForType.filter(r => r.package_type !== 'MONOPALLET').length, [rowsForType]);
    const monoRowCount = useMemo(() => rowsForType.filter(r => r.package_type === 'MONOPALLET').length, [rowsForType]);
    const withKCount = useMemo(() => committableRows.filter(r => !!nmPpb.get(r.nm_id)).length, [committableRows, nmPpb]);
    const withoutKCount = useMemo(() => committableRows.filter(r => !nmPpb.get(r.nm_id)).length, [committableRows, nmPpb]);
    const newcomerRowsTotal = useMemo(() => rows.filter(r => newcomerNmIds.has(r.nm_id)).length, [rows, newcomerNmIds]);

    const srcSumPerFf = useMemo(() => {
        const m: Record<number, number> = {};
        for (const r of visibleRows) {
            for (const [ffIdStr, qty] of Object.entries(r.src)) {
                const ffId = Number(ffIdStr);
                m[ffId] = (m[ffId] || 0) + (qty || 0);
            }
        }
        return m;
    }, [visibleRows]);

    // ─── Aggregate Σ tgt per WB (по активной вкладке) ───────────────────
    const tgtSumPerWb = useMemo(() => {
        const m: Record<string, number> = {};
        for (const r of visibleRows) {
            for (const [whName, qty] of Object.entries(r.tgt)) {
                m[whName] = (m[whName] || 0) + (qty || 0);
            }
        }
        return m;
    }, [visibleRows]);

    // ─── Cell editors ────────────────────────────────────────────────────
    // Матч по nm_id И типу упаковки: у SKU может быть две строки (короб + моно)
    // с одним nm_id — без проверки package_type правка задела бы обе.
    const setRowSrc = useCallback((nmId: number, pkg: PackageType, ffId: number, qty: number) => {
        setRows(prev => prev.map(r => {
            if (r.nm_id !== nmId || (r.package_type || 'BOX') !== pkg) return r;
            const next = { ...r.src };
            if (qty > 0) next[String(ffId)] = qty;
            else delete next[String(ffId)];
            return { ...r, src: next };
        }));
    }, []);

    const setRowTgt = useCallback((nmId: number, pkg: PackageType, whName: string, qty: number) => {
        setRows(prev => prev.map(r => {
            if (r.nm_id !== nmId || (r.package_type || 'BOX') !== pkg) return r;
            const next = { ...r.tgt };
            if (qty > 0) next[whName] = qty;
            else delete next[whName];
            return { ...r, tgt: next };
        }));
    }, []);

    // ─── Merge two WB target columns (drag & drop) ────────────────────────
    // convertPkg: when set, also changes package_type of every moved row
    // (used when user drags a MONO column onto a BOX column or vice versa).
    const handleMergeWb = useCallback((sourceWb: string, targetWb: string, convertPkg?: PackageType) => {
        if (sourceWb === targetWb) return;
        setRows(prev => prev.map(r => {
            const srcQty = r.tgt[sourceWb] || 0;
            const next = { ...r.tgt };
            delete next[sourceWb];
            if (srcQty > 0) {
                next[targetWb] = (next[targetWb] || 0) + srcQty;
            }
            return {
                ...r,
                tgt: next,
                ...(convertPkg !== undefined && srcQty > 0 ? { package_type: convertPkg } : {}),
            };
        }));
        setTargetWarehouseNames(prev => prev.filter(n => n !== sourceWb));
    }, []);

    // ─── Auto-balance ────────────────────────────────────────────────────
    const handleAutoBalance = useCallback(() => {
        const newRows: AssemblyDraftRow[] = rows.map(r => {
            // Quote: src приоритетнее tgt. Если пользователь явно ввёл src qty
            // (например уменьшил с 36 до 32), используем именно эту цифру —
            // tgt пересчитается. Если src=0, fallback на tgt как «желаемое».
            const srcSum = Object.values(r.src).reduce((s, v) => s + (v || 0), 0);
            const tgtSum = Object.values(r.tgt).reduce((s, v) => s + (v || 0), 0);
            const quote = srcSum > 0 ? srcSum : tgtSum;
            if (quote <= 0) {
                return { ...r, src: {}, tgt: {} };
            }

            // src: greedy by descending available_at_ff
            const srcCandidates = sourceWarehouseIds
                .map(ffId => ({ ffId, avail: availableAtFf(r.nm_id, ffId) }))
                .sort((a, b) => b.avail - a.avail);

            const newSrc: Record<string, number> = {};
            let remainingSrc = quote;
            for (const c of srcCandidates) {
                if (remainingSrc <= 0) break;
                const take = Math.min(remainingSrc, c.avail);
                if (take > 0) {
                    newSrc[String(c.ffId)] = take;
                    remainingSrc -= take;
                }
            }

            const newTgt: Record<string, number> = {};
            const actualSent = quote - remainingSrc;

            // Приоритет: cold_start_shares (для cold-start сборок без wbNeed),
            // далее wbNeed pro-rata, далее fallback в первый склад.
            const coldStartCandidates = coldStartShares
                ? targetWarehouseNames
                    .map(whName => ({ whName, share: coldStartShares[whName] || 0 }))
                    .filter(x => x.share > 0)
                : [];
            const wbNeeds = targetWarehouseNames
                .map(whName => ({ whName, need: wbNeedByNmIdAndWh(r.nm_id, whName) }))
                .filter(x => x.need > 0);
            const totalNeed = wbNeeds.reduce((s, x) => s + x.need, 0);

            const proRata = (items: Array<{ whName: string; weight: number }>, total: number): void => {
                const sumW = items.reduce((s, x) => s + x.weight, 0) || 1;
                let allocated = 0;
                for (let i = 0; i < items.length; i++) {
                    const x = items[i];
                    const isLast = i === items.length - 1;
                    const portion = isLast
                        ? total - allocated
                        : Math.floor((x.weight / sumW) * total);
                    if (portion > 0) {
                        newTgt[x.whName] = portion;
                        allocated += portion;
                    }
                }
            };

            if (actualSent > 0 && coldStartCandidates.length > 0) {
                proRata(coldStartCandidates.map(x => ({ whName: x.whName, weight: x.share })), actualSent);
            } else if (totalNeed > 0 && actualSent > 0) {
                proRata(wbNeeds.map(x => ({ whName: x.whName, weight: x.need })), actualSent);
            } else if (actualSent > 0 && targetWarehouseNames.length > 0) {
                newTgt[targetWarehouseNames[0]] = actualSent;
            }

            return { ...r, src: newSrc, tgt: newTgt };
        });
        setRows(newRows);
        const mode = coldStartShares ? 'по cold-start долям' : 'по wbNeed';
        setToast({ message: `Распределено автоматически (${mode})`, type: 'success' });
    }, [rows, sourceWarehouseIds, targetWarehouseNames, availableAtFf, wbNeedByNmIdAndWh, coldStartShares]);

    // ─── Commit ──────────────────────────────────────────────────────────
    // Должно совпадать с backend `commit_draft` группировкой:
    // (source_ff_id, target_wb_name, package_type, is_newcomer) → 1 AssemblyRequest.
    // Кол-во заявок, которые создаст commit текущего среза — совпадает с backend
    // группировкой (ФФ, WB, упаковка, новинка?). Считаем по committableRows
    // (НЕ visibleRows): multFilter на состав сборки не влияет. Разбивка
    // обычные/новинки показывает, что они уйдут раздельными заявками.
    const commitBreakdown = useMemo(() => {
        const reg = new Set<string>();
        const nw = new Set<string>();
        for (const r of committableRows) {
            const pkg = r.package_type || 'BOX';
            const isNew = newcomerNmIds.has(r.nm_id);
            for (const [ffId, srcQty] of Object.entries(r.src)) {
                if ((srcQty || 0) <= 0) continue;
                for (const [wbName, tgtQty] of Object.entries(r.tgt)) {
                    if ((tgtQty || 0) <= 0) continue;
                    (isNew ? nw : reg).add(`${ffId}::${wbName}::${pkg}`);
                }
            }
        }
        return { regular: reg.size, newcomer: nw.size, total: reg.size + nw.size };
    }, [committableRows, newcomerNmIds]);
    const uniqueAssemblyCount = commitBreakdown.total;

    // «Создать» → сохраняем черновик и уходим на отдельную страницу
    // предпросмотра заявок (группировка по складам + выгрузка + сам commit).
    const handleGoToPreview = useCallback(async () => {
        if (!draftId) return;
        const ok = await saveDraft(true);
        if (!ok) return;
        const qs = new URLSearchParams({ draft: String(draftId), pkg: pkgTab, type: typeTab });
        router.push(`/p/${slug}/warehouse/assembly/distribute/preview?${qs.toString()}`);
    }, [draftId, saveDraft, router, slug, pkgTab, typeTab]);

    // ─── Render ──────────────────────────────────────────────────────────
    if (loading) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>
                    Загрузка распределения...
                </div>
            </div>
        );
    }

    if (error) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>
                    {error}
                    <div style={{ marginTop: 12 }}>
                        <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => router.push(`/p/${slug}/warehouse/assembly`)}
                        >
                            К списку сборок
                        </button>
                    </div>
                </div>
            </div>
        );
    }

    if (!draft) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 32 }}>Черновик не найден</div>
            </div>
        );
    }

    return (
        <div className="animate-in">
            {toast && (
                <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />
            )}

            {/* Header */}
            <div className="page-header" style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1 }}>
                    <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => router.push(`/p/${slug}/warehouse/assembly`)}
                    >
                        ← Назад
                    </button>
                    <div style={{ flex: 1 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <h1 className="page-title" style={{ margin: 0 }}>Распределение сборки</h1>
                            {editingName ? (
                                <input
                                    className="form-input"
                                    autoFocus
                                    value={name}
                                    onChange={e => setName(e.target.value)}
                                    onBlur={() => setEditingName(false)}
                                    onKeyDown={e => {
                                        if (e.key === 'Enter') setEditingName(false);
                                        if (e.key === 'Escape') {
                                            setName(draft.name);
                                            setEditingName(false);
                                        }
                                    }}
                                    style={{ maxWidth: 320, fontSize: 14 }}
                                />
                            ) : (
                                <button
                                    className="btn btn-secondary btn-sm"
                                    onClick={() => setEditingName(true)}
                                    title="Изменить название черновика"
                                >
                                    {name || 'Без названия'}
                                </button>
                            )}
                        </div>
                        <p className="page-subtitle" style={{ margin: 0 }}>
                            Источников: {sourceWarehouseIds.length} · Целей: {targetWarehouseNames.length} · Артикулов: {rows.length}
                            {newcomerRowsTotal > 0 && (
                                <> · <span style={{ color: '#a855f7', fontWeight: 600 }}>🆕 Новинок: {newcomerRowsTotal}</span></>
                            )}
                        </p>
                    </div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button
                        className="btn btn-secondary"
                        onClick={() => saveDraft(false)}
                        disabled={saving}
                    >
                        {saving ? 'Сохранение...' : '💾 Сохранить'}
                    </button>
                    <button
                        className="btn btn-primary"
                        onClick={handleGoToPreview}
                        disabled={saving || uniqueAssemblyCount === 0}
                        title={
                            `Предпросмотр и создание заявок: ${uniqueAssemblyCount}`
                            + `\nОбычные: ${commitBreakdown.regular} · 🆕 Новинки: ${commitBreakdown.newcomer} (раздельные заявки)`
                            + `\nГруппировка по складам-источникам + выгрузка в Excel.`
                        }
                    >
                        {saving
                            ? 'Сохранение...'
                            : `✓ Создать: ${typeTab === 'all' ? '' : typeTab === 'newcomer' ? '🆕 Новинки · ' : 'Обычные · '}${pkgTab === 'MONOPALLET' ? 'Моно' : 'Короб'} (${uniqueAssemblyCount})`}
                    </button>
                </div>
            </div>

            {/* Settings row */}
            <div className="glass-card" style={{ padding: 16, marginBottom: 16, display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'end' }}>
                <div className="form-group">
                    <label className="form-label" style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                        Дата готовности
                    </label>
                    <input
                        type="date"
                        className="form-input"
                        value={estimatedReadyDate}
                        onChange={e => setEstimatedReadyDate(e.target.value)}
                    />
                </div>
                <div className="form-group">
                    <label className="form-label" style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                        Палеты (шт)
                    </label>
                    <input
                        type="number"
                        min={1}
                        className="form-input"
                        value={palletsCount}
                        onChange={e => setPalletsCount(Math.max(1, Number(e.target.value) || 1))}
                        style={{ width: 100 }}
                    />
                </div>
                <div className="form-group">
                    <label className="form-label" style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                        Вес палеты (кг)
                    </label>
                    <input
                        type="number"
                        min={0}
                        step={0.1}
                        className="form-input"
                        value={palletWeightKg}
                        onChange={e => setPalletWeightKg(Math.max(0, Number(e.target.value) || 0))}
                        style={{ width: 120 }}
                    />
                </div>
                <div className="form-group" style={{ flex: 1, minWidth: 200 }}>
                    <label className="form-label" style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>
                        Комментарий
                    </label>
                    <input
                        type="text"
                        className="form-input"
                        value={comment}
                        onChange={e => setComment(e.target.value)}
                        placeholder="Необязательно"
                    />
                </div>
                <div>
                    <button className="btn btn-secondary" onClick={handleAutoBalance}>
                        ↺ Авто-баланс
                    </button>
                </div>
            </div>

            {/* Вкладки: Тип товара (новинки/обычные) × Упаковка (короб/моно) —
                каждое измерение уходит раздельными заявками при commit. */}
            {rows.length > 0 && (
                <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap', alignItems: 'center' }}>
                    {hasNewcomerRows && (
                        <>
                            <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>Тип:</span>
                            <button
                                className={`btn btn-sm ${typeTab === 'all' ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setTypeTab('all')}
                            >
                                Все ({allTypeCount})
                            </button>
                            <button
                                className={`btn btn-sm ${typeTab === 'regular' ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setTypeTab('regular')}
                            >
                                Обычные ({regularCount})
                            </button>
                            <button
                                className={`btn btn-sm ${typeTab === 'newcomer' ? 'btn-primary' : 'btn-secondary'}`}
                                style={typeTab !== 'newcomer' ? { borderColor: '#a855f7', color: '#a855f7' } : {}}
                                onClick={() => setTypeTab('newcomer')}
                                title="Новинки (cold-start) — уйдут отдельными заявками от обычных"
                            >
                                🆕 Новинки ({newcomerCount})
                            </button>
                            <div style={{ width: 1, alignSelf: 'stretch', background: 'var(--color-border)', margin: '0 4px' }} />
                        </>
                    )}
                    <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>Упак:</span>
                    <button
                        className={`btn btn-sm ${pkgTab === 'BOX' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setPkgTab('BOX')}
                    >
                        📦 Короб ({boxRowCount})
                    </button>
                    <button
                        className={`btn btn-sm ${pkgTab === 'MONOPALLET' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setPkgTab('MONOPALLET')}
                    >
                        📐 Моно ({monoRowCount})
                    </button>
                    <div style={{ width: 1, alignSelf: 'stretch', background: 'var(--color-border)', margin: '0 4px' }} />
                    <button
                        className={`btn btn-sm ${multFilter === 'with' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setMultFilter(multFilter === 'with' ? 'none' : 'with')}
                        title="Только SKU с заданной кратностью короба"
                    >
                        Кратные ({withKCount})
                    </button>
                    <button
                        className={`btn btn-sm ${multFilter === 'without' ? 'btn-primary' : 'btn-secondary'}`}
                        onClick={() => setMultFilter(multFilter === 'without' ? 'none' : 'without')}
                        title="SKU без заданной кратности короба"
                    >
                        Без кратности ({withoutKCount})
                    </button>
                </div>
            )}

            {/* Matrix */}
            {rows.length === 0 ? (
                <div className="glass-card" style={{ padding: 64, textAlign: 'center' }}>
                    <div style={{ fontSize: 48, marginBottom: 16 }}>📋</div>
                    <div style={{ fontSize: 16, fontWeight: 500, marginBottom: 8 }}>Нет позиций для распределения</div>
                    <div style={{ color: 'var(--color-text-muted)', fontSize: 14 }}>
                        Добавьте позиции из &laquo;Потребности по складам&raquo;
                    </div>
                </div>
            ) : visibleRows.length === 0 ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    Нет позиций в выбранном срезе
                    {' «'}{typeTab === 'newcomer' ? '🆕 Новинки' : typeTab === 'regular' ? 'Обычные' : 'Все'}
                    {' · '}{pkgTab === 'MONOPALLET' ? 'Моно' : 'Короб'}{'»'}
                    {multFilter !== 'none' && ` (${multFilter === 'with' ? 'кратные' : 'без кратности'})`}
                </div>
            ) : (
                <div className="glass-card" style={{ overflowX: 'auto', padding: 0 }}>
                    <DistributeMatrix
                        rows={visibleRows}
                        allRows={rows}
                        groupByType={typeTab === 'all' && hasNewcomerRows}
                        nmPpb={nmPpb}
                        sourceWarehouseIds={sourceWarehouseIds}
                        targetWarehouseNames={targetWarehouseNames}
                        warehouseNameById={warehouseNameById}
                        srcSumPerFf={srcSumPerFf}
                        tgtSumPerWb={tgtSumPerWb}
                        availableAtFf={availableAtFf}
                        initialNeedByNmId={initialNeedByNmId}
                        newcomerNmIds={newcomerNmIds}
                        onSrcChange={setRowSrc}
                        onTgtChange={setRowTgt}
                        onMergeWb={handleMergeWb}
                    />
                </div>
            )}
        </div>
    );
}

// ─── Distribute Matrix Component ────────────────────────────────────────────

interface DistributeMatrixProps {
    rows: AssemblyDraftRow[];
    /** All rows across all package-type tabs — used for cross-type drag detection. */
    allRows: AssemblyDraftRow[];
    groupByType: boolean;
    nmPpb: Map<number, number | null>;
    sourceWarehouseIds: number[];
    targetWarehouseNames: string[];
    warehouseNameById: (id: number) => string;
    srcSumPerFf: Record<number, number>;
    tgtSumPerWb: Record<string, number>;
    availableAtFf: (nmId: number, ffId: number) => number;
    initialNeedByNmId: Record<number, number>;
    newcomerNmIds: Set<number>;
    onSrcChange: (nmId: number, pkg: PackageType, ffId: number, qty: number) => void;
    onTgtChange: (nmId: number, pkg: PackageType, whName: string, qty: number) => void;
    onMergeWb: (sourceWb: string, targetWb: string, convertPkg?: PackageType) => void;
}

function DistributeMatrix({
    rows,
    allRows,
    groupByType,
    nmPpb,
    sourceWarehouseIds,
    targetWarehouseNames,
    warehouseNameById,
    srcSumPerFf,
    tgtSumPerWb,
    availableAtFf,
    initialNeedByNmId,
    newcomerNmIds,
    onSrcChange,
    onTgtChange,
    onMergeWb,
}: DistributeMatrixProps) {
    const [dragSourceWb, setDragSourceWb] = useState<string | null>(null);
    const [dragOverWb, setDragOverWb] = useState<string | null>(null);

    // Sum of tgt quantities across ALL rows (both tabs) — used to show a
    // secondary "other-type" count in the column header so the user can
    // find cross-type drop targets (e.g. BOX columns while on MONO tab).
    const tgtSumAllPkgs = useMemo(() => {
        const m: Record<string, number> = {};
        for (const r of allRows) {
            for (const [wb, qty] of Object.entries(r.tgt)) {
                m[wb] = (m[wb] || 0) + (qty || 0);
            }
        }
        return m;
    }, [allRows]);

    // Icon for the "other" package type: if visible rows are MONO → other is BOX, vice versa.
    const otherPkgLabel = rows.some(r => r.package_type === 'MONOPALLET') ? '📦' : '📐';

    const thStyle: React.CSSProperties = {
        textAlign: 'right', fontSize: 11, fontWeight: 600,
        padding: '10px 8px', whiteSpace: 'nowrap',
        borderBottom: '2px solid var(--color-border)',
        background: '#f5f5f7',
    };
    const tdStyle: React.CSSProperties = {
        padding: '6px 8px', textAlign: 'right', fontSize: 12,
        borderBottom: '1px solid var(--color-border)',
    };

    return (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
                {/* Group headers */}
                <tr>
                    <th style={{ ...thStyle, textAlign: 'left', minWidth: 200, position: 'sticky', left: 0, zIndex: 3 }}>
                        Артикул
                    </th>
                    <th style={{ ...thStyle, minWidth: 92 }} title="Отгружаем (Σ источников строки) / Потребность по SKU из «Потребности по складам». Оранжевым — отгрузка больше потребности (лишек).">Отгр. / Потреб.</th>
                    <th
                        colSpan={sourceWarehouseIds.length + 1}
                        style={{ ...thStyle, textAlign: 'center', background: 'rgba(59,130,246,0.08)', letterSpacing: 1, fontWeight: 700 }}
                    >
                        ИСТОЧНИКИ (ФФ)
                    </th>
                    <th
                        colSpan={targetWarehouseNames.length + 1}
                        style={{ ...thStyle, textAlign: 'center', background: 'rgba(245,158,11,0.08)', letterSpacing: 1, fontWeight: 700 }}
                        title="Перетащи заголовок WB-склада на другой, чтобы объединить колонки"
                    >
                        ЦЕЛИ (WB) <span style={{ fontWeight: 400, fontSize: 10, opacity: 0.6, letterSpacing: 0 }}>· перетащи столбец чтобы объединить</span>
                    </th>
                </tr>
                {/* Column headers — название + сумма «к отгрузке» прямо под названием */}
                <tr>
                    <th style={{ ...thStyle, textAlign: 'left', position: 'sticky', left: 0, zIndex: 3 }} />
                    <th style={thStyle} />
                    {sourceWarehouseIds.map(ffId => {
                        const total = srcSumPerFf[ffId] || 0;
                        return (
                            <th key={`hdr-src-${ffId}`} style={{ ...thStyle, background: 'rgba(59,130,246,0.04)' }}>
                                <div>{warehouseNameById(ffId)}</div>
                                <div style={{ fontSize: 13, fontWeight: 700, color: total > 0 ? 'var(--color-accent)' : 'var(--color-text-muted)', marginTop: 2 }}>
                                    {total > 0 ? formatNumber(total, 0) : '—'}
                                </div>
                            </th>
                        );
                    })}
                    <th style={{ ...thStyle, background: 'rgba(59,130,246,0.08)' }}>
                        <div>Σ ист</div>
                        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--color-accent)', marginTop: 2 }}>
                            {formatNumber(Object.values(srcSumPerFf).reduce((s, v) => s + v, 0), 0)}
                        </div>
                    </th>
                    {targetWarehouseNames.map(whName => {
                        const isDragging = dragSourceWb === whName;
                        const isOver = dragOverWb === whName && dragSourceWb !== null && dragSourceWb !== whName;
                        const total = tgtSumPerWb[whName] || 0;
                        const otherTotal = (tgtSumAllPkgs[whName] || 0) - total;
                        return (
                            <th
                                key={`hdr-tgt-${whName}`}
                                draggable
                                onDragStart={e => {
                                    e.dataTransfer.effectAllowed = 'move';
                                    e.dataTransfer.setData('text/plain', whName);
                                    setDragSourceWb(whName);
                                }}
                                onDragEnd={() => {
                                    setDragSourceWb(null);
                                    setDragOverWb(null);
                                }}
                                onDragOver={e => {
                                    if (dragSourceWb && dragSourceWb !== whName) {
                                        e.preventDefault();
                                        e.dataTransfer.dropEffect = 'move';
                                        setDragOverWb(whName);
                                    }
                                }}
                                onDragLeave={() => {
                                    if (dragOverWb === whName) setDragOverWb(null);
                                }}
                                onDrop={e => {
                                    e.preventDefault();
                                    const src = e.dataTransfer.getData('text/plain') || dragSourceWb;
                                    if (src && src !== whName) {
                                        // Determine package types going to each column (across ALL rows,
                                        // not just the currently-visible tab) so we can detect cross-type drag.
                                        const srcPkgs = new Set(
                                            allRows.filter(r => (r.tgt[src] || 0) > 0).map(r => r.package_type || 'BOX'),
                                        );
                                        const tgtPkgs = new Set(
                                            allRows.filter(r => (r.tgt[whName] || 0) > 0).map(r => r.package_type || 'BOX'),
                                        );
                                        const srcOnlyMono = srcPkgs.size === 1 && srcPkgs.has('MONOPALLET');
                                        const srcOnlyBox  = srcPkgs.size > 0 && !srcPkgs.has('MONOPALLET');
                                        const tgtHasMono  = tgtPkgs.has('MONOPALLET');
                                        const tgtHasBox   = !tgtPkgs.has('MONOPALLET') && tgtPkgs.size > 0;

                                        // Cross-type: source is unambiguously one type, target is unambiguously the other
                                        const crossToBox  = srcOnlyMono && tgtHasBox && !tgtHasMono;
                                        const crossToMono = srcOnlyBox  && tgtHasMono && !tgtHasBox;

                                        if (crossToBox || crossToMono) {
                                            const srcLabel  = srcOnlyMono ? 'Моно' : 'Короб';
                                            const tgtLabel  = crossToBox  ? 'Короб' : 'Моно';
                                            const convertPkg: PackageType = crossToBox ? 'BOX' : 'MONOPALLET';
                                            if (window.confirm(
                                                `Объединить «${src}» (${srcLabel}) в «${whName}» (${tgtLabel})?\n\n` +
                                                `Тип упаковки перемещаемых строк изменится с «${srcLabel}» на «${tgtLabel}».`,
                                            )) {
                                                onMergeWb(src, whName, convertPkg);
                                            }
                                        } else {
                                            if (window.confirm(`Объединить «${src}» в «${whName}»? Все количества будут перенесены.`)) {
                                                onMergeWb(src, whName);
                                            }
                                        }
                                    }
                                    setDragSourceWb(null);
                                    setDragOverWb(null);
                                }}
                                title={[
                                    `Перетащи на другой WB-склад чтобы объединить колонки`,
                                    whName,
                                    `К отгрузке: ${formatNumber(total, 0)}`,
                                    otherTotal > 0
                                        ? `${otherPkgLabel} другой тип: ${formatNumber(otherTotal, 0)} шт. (перетащи сюда для смены типа)`
                                        : null,
                                ].filter(Boolean).join('\n')}
                                style={{
                                    ...thStyle,
                                    background: isOver
                                        ? 'rgba(34,197,94,0.18)'
                                        : total === 0 && otherTotal > 0
                                            ? 'rgba(245,158,11,0.06)'
                                            : 'rgba(245,158,11,0.04)',
                                    opacity: isDragging ? 0.4 : 1,
                                    cursor: 'grab',
                                    transition: 'background 0.12s',
                                    border: isOver ? '2px dashed var(--color-success, #22c55e)' : undefined,
                                }}
                            >
                                <div>{whName.length > 14 ? whName.slice(0, 14) + '…' : whName}</div>
                                <div style={{ fontSize: 13, fontWeight: 700, color: total > 0 ? 'var(--color-warning)' : 'var(--color-text-muted)', marginTop: 2 }}>
                                    {total > 0 ? formatNumber(total, 0) : '—'}
                                </div>
                                {total === 0 && otherTotal > 0 && (
                                    <div style={{ fontSize: 10, color: 'var(--color-text-muted)', opacity: 0.7, marginTop: 1 }}>
                                        {otherPkgLabel} {formatNumber(otherTotal, 0)}
                                    </div>
                                )}
                            </th>
                        );
                    })}
                    <th style={{ ...thStyle, background: 'rgba(245,158,11,0.08)' }}>
                        <div>Σ цель</div>
                        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--color-warning)', marginTop: 2 }}>
                            {formatNumber(Object.values(tgtSumPerWb).reduce((s, v) => s + v, 0), 0)}
                        </div>
                    </th>
                </tr>
            </thead>
            <tbody>
                {(() => {
                    const renderRow = (row: AssemblyDraftRow) => {
                    const srcSum = Object.values(row.src).reduce((s, v) => s + (v || 0), 0);
                    const tgtSum = Object.values(row.tgt).reduce((s, v) => s + (v || 0), 0);
                    const balanced = srcSum === tgtSum;
                    const initialNeed = initialNeedByNmId[row.nm_id] || 0;
                    const overflow = initialNeed > 0 && srcSum > initialNeed;

                    return (
                        <tr key={`row-${row.nm_id}-${row.package_type || 'BOX'}`}>
                            <td
                                style={{
                                    ...tdStyle,
                                    textAlign: 'left',
                                    position: 'sticky',
                                    left: 0,
                                    background: '#fff',
                                    zIndex: 2,
                                    fontWeight: 500,
                                }}
                            >
                                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                    {newcomerNmIds.has(row.nm_id) && (
                                        <span
                                            title="Новинка (≤14 дней с первой продажи или без продаж) — уйдёт отдельной заявкой"
                                            style={{
                                                padding: '1px 6px', borderRadius: 6,
                                                background: '#a855f7', color: '#fff',
                                                fontSize: 9, fontWeight: 700, letterSpacing: 0.3,
                                            }}
                                        >
                                            🆕
                                        </span>
                                    )}
                                    {row.package_type === 'MONOPALLET' && (
                                        <span title="Монопаллета — отдельная заявка от короба"
                                            style={{
                                                padding: '1px 6px', borderRadius: 6,
                                                background: 'rgba(34,197,94,0.16)', color: '#15803d',
                                                fontSize: 9, fontWeight: 700,
                                            }}>📐 моно</span>
                                    )}
                                    {row.package_type === 'SUPERSAFE' && (
                                        <span title="Суперсейф — отдельная заявка"
                                            style={{
                                                padding: '1px 6px', borderRadius: 6,
                                                background: 'rgba(168,85,247,0.16)', color: '#7e22ce',
                                                fontSize: 9, fontWeight: 700,
                                            }}>🛡 суп</span>
                                    )}
                                    {(!row.package_type || row.package_type === 'BOX') && (
                                        <span title="Короб (по умолчанию)"
                                            style={{
                                                padding: '1px 6px', borderRadius: 6,
                                                background: 'rgba(59,130,246,0.14)', color: '#1d4ed8',
                                                fontSize: 9, fontWeight: 700,
                                            }}>📦 кор</span>
                                    )}
                                    <span>{row.vendor_code || `nm:${row.nm_id}`}</span>
                                </div>
                                <div style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>
                                    {row.barcode}
                                </div>
                                {(() => {
                                    const kPpb = nmPpb.get(row.nm_id);
                                    const hasK = !!(kPpb && kPpb > 0);
                                    return (
                                        <span
                                            title={hasK ? `Кратность короба: ${kPpb} шт` : 'Кратность не задана'}
                                            style={{
                                                display: 'inline-block', marginTop: 2,
                                                padding: '1px 6px', borderRadius: 6,
                                                fontSize: 9, fontWeight: 600,
                                                background: hasK ? 'rgba(52,199,89,0.14)' : 'rgba(142,142,147,0.16)',
                                                color: hasK ? '#1f7a3a' : '#6e6e73',
                                            }}
                                        >
                                            {hasK ? `📦 кратно ${kPpb}` : 'без кратности'}
                                        </span>
                                    );
                                })()}
                            </td>
                            <td
                                style={{
                                    ...tdStyle,
                                    fontWeight: 600,
                                    background: overflow ? 'rgba(255,159,10,0.12)' : undefined,
                                    color: overflow ? 'var(--color-warning)' : undefined,
                                }}
                                title={overflow ? `Лишек: ${srcSum - initialNeed}` : undefined}
                            >
                                {srcSum}/{initialNeed > 0 ? initialNeed : '?'}
                            </td>

                            {sourceWarehouseIds.map(ffId => {
                                const qty = row.src[String(ffId)] || 0;
                                const avail = availableAtFf(row.nm_id, ffId);
                                const usedOnFf = srcSumPerFf[ffId] || 0;
                                const overload = avail > 0 && usedOnFf > avail;
                                return (
                                    <td
                                        key={`src-${row.nm_id}-${ffId}`}
                                        style={{
                                            ...tdStyle,
                                            background: !balanced && qty > 0 ? 'rgba(255,59,48,0.06)' : 'rgba(59,130,246,0.02)',
                                        }}
                                    >
                                        <NumericCell
                                            value={qty}
                                            onChange={(v) => onSrcChange(row.nm_id, (row.package_type || 'BOX') as PackageType, ffId, v)}
                                            invalid={!balanced && qty > 0}
                                        />
                                        <div
                                            style={{
                                                fontSize: 10,
                                                color: overload ? 'var(--color-danger)' : 'var(--color-text-muted)',
                                                marginTop: 2,
                                            }}
                                            title={`На ФФ свободно: ${formatNumber(avail, 0)} · Σ занято: ${formatNumber(usedOnFf, 0)}`}
                                        >
                                            {formatNumber(usedOnFf, 0)}/{formatNumber(avail, 0)}
                                        </div>
                                    </td>
                                );
                            })}
                            <td
                                style={{
                                    ...tdStyle,
                                    fontWeight: 700,
                                    background: 'rgba(59,130,246,0.04)',
                                    color: balanced ? 'var(--color-success)' : 'var(--color-danger)',
                                }}
                            >
                                {srcSum} {balanced ? '✓' : '!'}
                            </td>

                            {targetWarehouseNames.map(whName => {
                                const qty = row.tgt[whName] || 0;
                                return (
                                    <td
                                        key={`tgt-${row.nm_id}-${whName}`}
                                        style={{
                                            ...tdStyle,
                                            background: !balanced && qty > 0 ? 'rgba(255,59,48,0.06)' : 'rgba(245,158,11,0.02)',
                                        }}
                                    >
                                        <NumericCell
                                            value={qty}
                                            onChange={(v) => onTgtChange(row.nm_id, (row.package_type || 'BOX') as PackageType, whName, v)}
                                            invalid={!balanced && qty > 0}
                                        />
                                    </td>
                                );
                            })}
                            <td
                                style={{
                                    ...tdStyle,
                                    fontWeight: 700,
                                    background: 'rgba(245,158,11,0.04)',
                                    color: balanced ? 'var(--color-success)' : 'var(--color-danger)',
                                }}
                            >
                                {tgtSum} {balanced ? '✓' : '!'}
                            </td>
                        </tr>
                    );
                    };
                    if (!groupByType) return rows.map(renderRow);

                    // «Все»: группируем строки секциями обычные / новинки —
                    // делает раздельность будущих заявок видимой глазами.
                    const regular = rows.filter(r => !newcomerNmIds.has(r.nm_id));
                    const newcomers = rows.filter(r => newcomerNmIds.has(r.nm_id));
                    const totalCols = 2 + sourceWarehouseIds.length + 1 + targetWarehouseNames.length + 1;
                    const renderSection = (label: string, list: AssemblyDraftRow[], isNew: boolean) => {
                        if (list.length === 0) return null;
                        const sSrc = list.reduce((s, r) => s + Object.values(r.src).reduce((a, b) => a + (b || 0), 0), 0);
                        const sTgt = list.reduce((s, r) => s + Object.values(r.tgt).reduce((a, b) => a + (b || 0), 0), 0);
                        return (
                            <Fragment key={`sec-${isNew ? 'new' : 'reg'}`}>
                                <tr>
                                    <td colSpan={totalCols} style={{
                                        padding: '8px 12px',
                                        background: isNew ? 'rgba(168,85,247,0.08)' : 'rgba(59,130,246,0.06)',
                                        borderTop: '2px solid var(--color-border)',
                                        borderBottom: '1px solid var(--color-border)',
                                    }}>
                                        <div style={{
                                            position: 'sticky', left: 12, width: 'fit-content',
                                            display: 'flex', gap: 16, alignItems: 'center',
                                            fontSize: 12, fontWeight: 700,
                                        }}>
                                            <span style={{ color: isNew ? '#7e22ce' : 'var(--color-text)' }}>
                                                {label} · {list.length}
                                            </span>
                                            <span style={{ fontWeight: 600, color: 'var(--color-text-muted)' }}>
                                                Σ ист {formatNumber(sSrc, 0)} · Σ цель {formatNumber(sTgt, 0)}
                                            </span>
                                        </div>
                                    </td>
                                </tr>
                                {list.map(renderRow)}
                            </Fragment>
                        );
                    };
                    return (
                        <>
                            {renderSection('Обычные товары', regular, false)}
                            {renderSection('🆕 Новинки', newcomers, true)}
                        </>
                    );
                })()}
            </tbody>
        </table>
    );
}

// ─── Numeric Cell ──────────────────────────────────────────────────────────

interface NumericCellProps {
    value: number;
    onChange: (v: number) => void;
    invalid?: boolean;
}

function NumericCell({ value, onChange, invalid }: NumericCellProps) {
    const [editing, setEditing] = useState(false);
    const [text, setText] = useState('');
    const inputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (editing && inputRef.current) {
            inputRef.current.focus();
            inputRef.current.select();
        }
    }, [editing]);

    const commit = () => {
        setEditing(false);
        const n = parseInt(text, 10);
        if (!isNaN(n) && n !== value) {
            onChange(Math.max(0, n));
        }
    };

    if (editing) {
        return (
            <input
                ref={inputRef}
                type="number"
                min={0}
                value={text}
                onChange={e => setText(e.target.value)}
                onBlur={commit}
                onKeyDown={e => {
                    if (e.key === 'Enter') inputRef.current?.blur();
                    if (e.key === 'Escape') {
                        setText(String(value || ''));
                        setEditing(false);
                    }
                }}
                style={{
                    width: 70,
                    padding: '3px 6px',
                    borderRadius: 6,
                    border: '2px solid var(--color-accent)',
                    fontSize: 12,
                    textAlign: 'right',
                    outline: 'none',
                }}
            />
        );
    }

    return (
        <div
            onClick={() => {
                setText(String(value || ''));
                setEditing(true);
            }}
            style={{
                cursor: 'pointer',
                padding: '3px 6px',
                borderRadius: 6,
                border: invalid ? '1px solid var(--color-danger)' : '1px solid transparent',
                color: value > 0 ? 'var(--color-text)' : 'var(--color-text-muted)',
                fontWeight: value > 0 ? 600 : 400,
                minHeight: 22,
            }}
            title="Нажмите для редактирования"
        >
            {value > 0 ? formatNumber(value, 0) : '—'}
        </div>
    );
}
