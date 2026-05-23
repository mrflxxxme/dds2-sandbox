'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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

/** Схлопнуть дубли строк по nm_id+упаковка, оставляя первую. Старые черновики
 *  могли содержать задвоенные SKU (баг прежней генерации) — это ломало
 *  сортировку/редактирование и задваивало отгрузку при commit. */
function dedupeRows(rows: AssemblyDraftRow[]): AssemblyDraftRow[] {
    const seen = new Set<string>();
    return rows.filter(r => {
        const k = `${r.nm_id}-${r.package_type || 'BOX'}`;
        if (seen.has(k)) return false;
        seen.add(k);
        return true;
    });
}

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
                setRows(dedupeRows(draftResp.distribution.rows || []));
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
    // Ручной ввод снапим вниз до целых коробов, если кратность задана (как
    // normQty на странице заявки). SKU без кратности — без снапа.
    const snapToBox = useCallback((nmId: number, qty: number) => {
        const q = Math.max(0, Math.trunc(qty || 0));
        const k = nmPpb.get(nmId);
        return k && k > 0 ? Math.floor(q / k) * k : q;
    }, [nmPpb]);

    // Потолок в штуках → вниз до целого числа коробов (неполный короб сверх
    // остатка сдать нельзя). Без кратности — потолок как есть.
    const boxCap = useCallback((nmId: number, maxUnits: number) => {
        const k = nmPpb.get(nmId);
        const m = Math.max(0, maxUnits);
        return k && k > 0 ? Math.floor(m / k) * k : m;
    }, [nmPpb]);

    // Свободный остаток SKU на ФФ. null = данных нет (stockNeed не загрузился /
    // SKU вне выборки) → кап НЕ применяем, чтобы не блокировать ввод вслепую.
    // Map вместо .find — вызывается на каждую ячейку матрицы при ререндере.
    const stockByNm = useMemo(() => {
        const m = new Map<number, StockNeedArticle>();
        for (const a of stockNeed?.articles ?? []) m.set(a.nm_id, a);
        return m;
    }, [stockNeed]);
    const availAtFfOrNull = useCallback((nmId: number, ffId: number): number | null => {
        const a = stockByNm.get(nmId);
        if (!a) return null;
        return a.rf_stocks[ffId]?.available ?? 0;
    }, [stockByNm]);
    // Сколько SKU можно отгрузить ЦЕЛЫМИ коробами суммарно по всем ФФ: per-FF
    // floor по кратности (короб собирается на одном ФФ, остаток < короба не едет).
    // null = нет данных. Это потолок для целей и авто-источника.
    const boxShipTotalOrNull = useCallback((nmId: number): number | null => {
        const a = stockByNm.get(nmId);
        if (!a) return null;
        const k = nmPpb.get(nmId) || 0;
        return sourceWarehouseIds.reduce((s, ffId) => {
            const av = a.rf_stocks[ffId]?.available ?? 0;
            return s + (k > 0 ? Math.floor(av / k) * k : av);
        }, 0);
    }, [stockByNm, sourceWarehouseIds, nmPpb]);

    // Источник: нельзя забрать с ФФ больше, чем там свободно (в целых коробах).
    const setRowSrc = useCallback((nmId: number, pkg: PackageType, ffId: number, qty: number) => {
        const snapped = snapToBox(nmId, qty);
        const avail = availAtFfOrNull(nmId, ffId);
        const q = avail === null ? snapped : Math.min(snapped, boxCap(nmId, avail));
        if (avail !== null && q < snapped) {
            setToast({ message: `На «${warehouseNameById(ffId)}» свободно ${formatNumber(avail, 0)} шт — добавлено ${formatNumber(q, 0)}`, type: 'error' });
        }
        setRows(prev => prev.map(r => {
            if (r.nm_id !== nmId || (r.package_type || 'BOX') !== pkg) return r;
            const next = { ...r.src };
            if (q > 0) next[String(ffId)] = q;
            else delete next[String(ffId)];
            return { ...r, src: next };
        }));
    }, [snapToBox, availAtFfOrNull, boxCap, warehouseNameById]);

    // Цель: ограничиваем суммой свободного остатка SKU и АВТО-набираем источник
    // (с какого ФФ сборка) жадно по убыванию свободного остатка, чтобы строка
    // осталась сбалансированной (Σ ист = Σ цель). Нет данных по остаткам —
    // старое поведение (просто пишем цель, источник не трогаем).
    const setRowTgt = useCallback((nmId: number, pkg: PackageType, whName: string, qty: number) => {
        const snapped = snapToBox(nmId, qty);
        const total = boxShipTotalOrNull(nmId); // потолок целыми коробами по всем ФФ
        const row = rows.find(r => r.nm_id === nmId && (r.package_type || 'BOX') === pkg);
        const otherTgt = row ? Object.entries(row.tgt).reduce((s, [w, v]) => w === whName ? s : s + (v || 0), 0) : 0;
        const q = total === null ? snapped : Math.min(snapped, boxCap(nmId, Math.max(0, total - otherTgt)));
        if (total !== null && q < snapped) {
            setToast({ message: `Целыми коробами доступно ${formatNumber(total, 0)} шт по SKU — на «${whName}» добавлено ${formatNumber(q, 0)}`, type: 'error' });
        }
        const k = nmPpb.get(nmId) || 0;
        setRows(prev => prev.map(r => {
            if (r.nm_id !== nmId || (r.package_type || 'BOX') !== pkg) return r;
            const nextTgt = { ...r.tgt };
            if (q > 0) nextTgt[whName] = q;
            else delete nextTgt[whName];
            if (total === null) return { ...r, tgt: nextTgt };
            // Авто-источник под Σ цели: жадно ЦЕЛЫМИ коробами по убыванию остатка ФФ
            // (короб собирается на одном ФФ). Так Σ ист = Σ цель и всё кратно коробу.
            const totalTgt = Object.values(nextTgt).reduce((s, v) => s + (v || 0), 0);
            const cands = sourceWarehouseIds
                .map(ffId => { const av = availableAtFf(nmId, ffId); return { ffId, ship: k > 0 ? Math.floor(av / k) * k : av }; })
                .sort((a, b) => b.ship - a.ship);
            const newSrc: Record<string, number> = {};
            let remaining = totalTgt;
            for (const c of cands) {
                if (remaining <= 0) break;
                const take = Math.min(remaining, c.ship);
                if (take > 0) { newSrc[String(c.ffId)] = take; remaining -= take; }
            }
            return { ...r, tgt: nextTgt, src: newSrc };
        }));
    }, [snapToBox, boxShipTotalOrNull, boxCap, rows, sourceWarehouseIds, availableAtFf, nmPpb]);

    // «Обнулить позицию» — убрать все количества строки (товар остаётся на ФФ);
    // строка остаётся в матрице для повторного ввода.
    const clearRow = useCallback((nmId: number, pkg: PackageType) => {
        setRows(prev => prev.map(r =>
            r.nm_id === nmId && (r.package_type || 'BOX') === pkg ? { ...r, src: {}, tgt: {} } : r,
        ));
    }, []);

    // «Удалить позицию» — убрать строку из распределения целиком.
    const deleteRow = useCallback((nmId: number, pkg: PackageType) => {
        setRows(prev => prev.filter(r => !(r.nm_id === nmId && (r.package_type || 'BOX') === pkg)));
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
                            `Открыть предпросмотр. Заявки (${uniqueAssemblyCount}) создаются на следующем шаге кнопкой «Создать».`
                            + `\nОбычные: ${commitBreakdown.regular} · 🆕 Новинки: ${commitBreakdown.newcomer} (раздельные заявки)`
                            + `\nГруппировка по складам-источникам + выгрузка в Excel.`
                        }
                    >
                        {saving
                            ? 'Сохранение...'
                            : `Предпросмотр: ${typeTab === 'all' ? '' : typeTab === 'newcomer' ? '🆕 Новинки · ' : 'Обычные · '}${pkgTab === 'MONOPALLET' ? 'Моно' : 'Короб'} (${uniqueAssemblyCount}) →`}
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
                        nmPpb={nmPpb}
                        sourceWarehouseIds={sourceWarehouseIds}
                        targetWarehouseNames={targetWarehouseNames}
                        warehouseNameById={warehouseNameById}
                        srcSumPerFf={srcSumPerFf}
                        tgtSumPerWb={tgtSumPerWb}
                        availableAtFf={availableAtFf}
                        availAtFfOrNull={availAtFfOrNull}
                        initialNeedByNmId={initialNeedByNmId}
                        newcomerNmIds={newcomerNmIds}
                        onSrcChange={setRowSrc}
                        onTgtChange={setRowTgt}
                        onMergeWb={handleMergeWb}
                        onClearRow={clearRow}
                        onDeleteRow={deleteRow}
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
    nmPpb: Map<number, number | null>;
    sourceWarehouseIds: number[];
    targetWarehouseNames: string[];
    warehouseNameById: (id: number) => string;
    srcSumPerFf: Record<number, number>;
    tgtSumPerWb: Record<string, number>;
    availableAtFf: (nmId: number, ffId: number) => number;
    availAtFfOrNull: (nmId: number, ffId: number) => number | null;
    initialNeedByNmId: Record<number, number>;
    newcomerNmIds: Set<number>;
    onSrcChange: (nmId: number, pkg: PackageType, ffId: number, qty: number) => void;
    onTgtChange: (nmId: number, pkg: PackageType, whName: string, qty: number) => void;
    onMergeWb: (sourceWb: string, targetWb: string, convertPkg?: PackageType) => void;
    onClearRow: (nmId: number, pkg: PackageType) => void;
    onDeleteRow: (nmId: number, pkg: PackageType) => void;
}

function DistributeMatrix({
    rows,
    allRows,
    nmPpb,
    sourceWarehouseIds,
    targetWarehouseNames,
    warehouseNameById,
    srcSumPerFf,
    tgtSumPerWb,
    availableAtFf,
    availAtFfOrNull,
    initialNeedByNmId,
    newcomerNmIds,
    onSrcChange,
    onTgtChange,
    onMergeWb,
    onClearRow,
    onDeleteRow,
}: DistributeMatrixProps) {
    const [dragSourceWb, setDragSourceWb] = useState<string | null>(null);
    const [dragOverWb, setDragOverWb] = useState<string | null>(null);
    // Сколько строк рендерить (для скорости): по умолчанию 100, «показать ещё».
    const [visibleLimit, setVisibleLimit] = useState(100);

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

    // Свободный остаток SKU по всем ФФ: raw (физический остаток) + ship (сколько
    // уйдёт целыми коробами, per-FF floor). Для колонки «Свободно» и капа целей.
    // null = данных нет. Считаем один раз на ререндер.
    const freeByNm = useMemo(() => {
        const m = new Map<number, { raw: number; ship: number } | null>();
        for (const r of rows) {
            if (m.has(r.nm_id)) continue;
            const k = nmPpb.get(r.nm_id) || 0;
            let raw = 0, ship = 0, has = false;
            for (const ffId of sourceWarehouseIds) {
                const a = availAtFfOrNull(r.nm_id, ffId);
                if (a !== null) { has = true; raw += a; ship += k > 0 ? Math.floor(a / k) * k : a; }
            }
            m.set(r.nm_id, has ? { raw, ship } : null);
        }
        return m;
    }, [rows, sourceWarehouseIds, availAtFfOrNull, nmPpb]);

    // Стабильный ключ строки: один SKU (nm_id+упаковка) может встречаться в
    // черновике несколько раз, поэтому ключ по nm_id+pkg коллизирует и React
    // не переставляет строки при сортировке. Индекс в исходном массиве уникален
    // и стабилен — sortRows сохраняет ссылки на объекты строк.
    const rowIndex = useMemo(() => {
        const m = new Map<AssemblyDraftRow, number>();
        rows.forEach((r, i) => m.set(r, i));
        return m;
    }, [rows]);

    // ─── Сортировка строк кликом по заголовку столбца ──────────────────────
    // desc → asc → выкл (исходный порядок). WB-цели сортируются по клику; их же
    // drag (merge) браузер различает по движению мыши — клик ≠ перетаскивание.
    const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' } | null>(null);
    const toggleSort = useCallback((key: string) => {
        setSort(prev => {
            if (!prev || prev.key !== key) return { key, dir: 'desc' };
            return prev.dir === 'desc' ? { key, dir: 'asc' } : null;
        });
    }, []);
    const sortValue = useCallback((row: AssemblyDraftRow, key: string): number | string => {
        if (key === 'art') return (row.vendor_code || `nm:${row.nm_id}`).toLowerCase();
        if (key === 'ship' || key === 'srcSum') return Object.values(row.src).reduce((s, v) => s + (v || 0), 0);
        if (key === 'tgtSum') return Object.values(row.tgt).reduce((s, v) => s + (v || 0), 0);
        if (key === 'free') { const f = freeByNm.get(row.nm_id); return f ? f.raw : -1; }
        if (key.startsWith('src:')) return row.src[key.slice(4)] || 0;
        if (key.startsWith('tgt:')) return row.tgt[key.slice(4)] || 0;
        return 0;
    }, [freeByNm]);
    const sortRows = useCallback((list: AssemblyDraftRow[]): AssemblyDraftRow[] => {
        if (!sort) return list;
        const dir = sort.dir === 'asc' ? 1 : -1;
        return [...list].sort((a, b) => {
            const va = sortValue(a, sort.key), vb = sortValue(b, sort.key);
            const cmp = typeof va === 'string' || typeof vb === 'string' ? String(va).localeCompare(String(vb)) : va - vb;
            return cmp * dir;
        });
    }, [sort, sortValue]);
    const sortArrow = (key: string) => (sort?.key === key ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : '');

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
                    <th onClick={() => toggleSort('art')} style={{ ...thStyle, textAlign: 'left', minWidth: 200, position: 'sticky', left: 0, zIndex: 3, cursor: 'pointer', userSelect: 'none' }} title="Клик — сортировать по артикулу">
                        Артикул{sortArrow('art')}
                    </th>
                    <th onClick={() => toggleSort('ship')} style={{ ...thStyle, minWidth: 92, cursor: 'pointer', userSelect: 'none' }} title="Отгружаем (Σ источников строки) / Потребность по SKU. Клик — сортировать по отгрузке.">Отгр. / Потреб.{sortArrow('ship')}</th>
                    <th onClick={() => toggleSort('free')} style={{ ...thStyle, minWidth: 76, cursor: 'pointer', userSelect: 'none' }} title="Остаток, ещё не разложенный: свободно на ФФ минус уже взято. Клик — сортировать.">Свободно{sortArrow('free')}</th>
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
                    <th style={thStyle} />
                    {sourceWarehouseIds.map(ffId => {
                        const total = srcSumPerFf[ffId] || 0;
                        return (
                            <th key={`hdr-src-${ffId}`} onClick={() => toggleSort(`src:${ffId}`)} style={{ ...thStyle, background: 'rgba(59,130,246,0.04)', cursor: 'pointer', userSelect: 'none' }} title={`Клик — сортировать по «${warehouseNameById(ffId)}»`}>
                                <div>{warehouseNameById(ffId)}{sortArrow(`src:${ffId}`)}</div>
                                <div style={{ fontSize: 13, fontWeight: 700, color: total > 0 ? 'var(--color-accent)' : 'var(--color-text-muted)', marginTop: 2 }}>
                                    {total > 0 ? formatNumber(total, 0) : '—'}
                                </div>
                            </th>
                        );
                    })}
                    <th onClick={() => toggleSort('srcSum')} style={{ ...thStyle, background: 'rgba(59,130,246,0.08)', cursor: 'pointer', userSelect: 'none' }} title="Клик — сортировать по Σ источников">
                        <div>Σ ист{sortArrow('srcSum')}</div>
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
                                onClick={() => toggleSort(`tgt:${whName}`)}
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
                                        // "only" = exactly one package type, not mixed
                                        const tgtOnlyMono = tgtPkgs.size === 1 && tgtPkgs.has('MONOPALLET');
                                        const tgtOnlyBox  = tgtPkgs.size > 0 && tgtPkgs.has('BOX') && !tgtPkgs.has('MONOPALLET');

                                        // Cross-type: both sides are unambiguously different single types.
                                        // Mixed targets (MONO+BOX) intentionally skip conversion.
                                        const crossToBox  = srcOnlyMono && tgtOnlyBox;
                                        const crossToMono = srcOnlyBox  && tgtOnlyMono;

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
                                    `Клик — сортировать по этому складу · перетащи на другой WB-склад чтобы объединить`,
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
                                <div>{whName.length > 14 ? whName.slice(0, 14) + '…' : whName}{sortArrow(`tgt:${whName}`)}</div>
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
                    <th onClick={() => toggleSort('tgtSum')} style={{ ...thStyle, background: 'rgba(245,158,11,0.08)', cursor: 'pointer', userSelect: 'none' }} title="Клик — сортировать по Σ целей">
                        <div>Σ цель{sortArrow('tgtSum')}</div>
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
                        <tr key={`row-${rowIndex.get(row) ?? `${row.nm_id}-${row.package_type || 'BOX'}`}`}>
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
                                    <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 2 }}>
                                        <button
                                            type="button"
                                            title="Обнулить позицию: убрать все количества (товар останется на ФФ)"
                                            onClick={() => onClearRow(row.nm_id, (row.package_type || 'BOX') as PackageType)}
                                            disabled={srcSum === 0 && tgtSum === 0}
                                            style={{
                                                border: 'none', background: 'transparent',
                                                cursor: srcSum === 0 && tgtSum === 0 ? 'default' : 'pointer',
                                                fontSize: 12, lineHeight: 1, padding: '2px 4px', borderRadius: 6,
                                                color: 'var(--color-text-muted)', opacity: srcSum === 0 && tgtSum === 0 ? 0.25 : 0.6,
                                            }}
                                        >
                                            ⌫
                                        </button>
                                        <button
                                            type="button"
                                            title="Удалить позицию из распределения"
                                            onClick={() => {
                                                if (window.confirm(`Удалить позицию «${row.vendor_code || `nm:${row.nm_id}`}» из распределения?`)) {
                                                    onDeleteRow(row.nm_id, (row.package_type || 'BOX') as PackageType);
                                                }
                                            }}
                                            style={{
                                                border: 'none', background: 'transparent', cursor: 'pointer',
                                                fontSize: 12, lineHeight: 1, padding: '2px 4px', borderRadius: 6,
                                                color: 'var(--color-danger)', opacity: 0.6,
                                            }}
                                        >
                                            ✕
                                        </button>
                                    </span>
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
                            <td style={{ ...tdStyle }} title="Сколько ещё можно разложить: свободно на ФФ минус уже взято в источники этой строки. Снизу — в целых коробах.">
                                {(() => {
                                    const free = freeByNm.get(row.nm_id);
                                    if (!free) return <span style={{ color: 'var(--color-text-muted)' }}>—</span>;
                                    const k = nmPpb.get(row.nm_id) || 0;
                                    // Остаток = свободно − уже взято в источники (srcSum). Убывает по мере раскладки.
                                    const remRaw = Math.max(0, free.raw - srcSum);
                                    const remBoxes = k > 0 ? Math.max(0, Math.floor((free.ship - srcSum) / k)) : 0;
                                    return (
                                        <>
                                            <div style={{ fontWeight: 600, color: remRaw > 0 ? 'var(--color-text)' : 'var(--color-text-muted)' }} title={`Всего свободно: ${formatNumber(free.raw, 0)} шт`}>{formatNumber(remRaw, 0)}</div>
                                            {k > 0 ? <div style={{ fontSize: 10, color: 'var(--color-text-muted)' }}>{formatNumber(remBoxes, 0)} кор</div> : null}
                                        </>
                                    );
                                })()}
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
                                            box={nmPpb.get(row.nm_id) || 0}
                                            max={availAtFfOrNull(row.nm_id, ffId) ?? undefined}
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
                                const free = freeByNm.get(row.nm_id);
                                const tgtMax = !free ? undefined : Math.max(0, free.ship - (tgtSum - qty));
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
                                            box={nmPpb.get(row.nm_id) || 0}
                                            max={tgtMax}
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
                    const totalCols = 3 + sourceWarehouseIds.length + 1 + targetWarehouseNames.length + 1;
                    // Рендерим строки частями (по умолчанию 100) — иначе 400+ строк × ~20
                    // колонок тормозят перерисовку при каждой правке. Суммы в шапке и
                    // в заголовках секций считаются по ВСЕМ строкам — режется только показ.
                    const moreRow = (shown: number, total: number) => total > shown ? (
                        <tr key="more-row">
                            <td colSpan={totalCols} style={{ padding: '10px 12px', background: '#fafafe', borderTop: '1px solid var(--color-border)', borderBottom: '1px solid var(--color-border)' }}>
                                <div style={{ position: 'sticky', left: 12, width: 'fit-content', display: 'inline-flex', gap: 8, alignItems: 'center' }}>
                                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>Показано {formatNumber(shown, 0)} из {formatNumber(total, 0)}</span>
                                    <button className="btn btn-secondary btn-sm" onClick={() => setVisibleLimit(l => l + 100)}>Показать ещё 100</button>
                                    <button className="btn btn-secondary btn-sm" onClick={() => setVisibleLimit(total)}>Показать все</button>
                                </div>
                            </td>
                        </tr>
                    ) : null;

                    // Единый список (без разделения на обычные/новинки) — чтобы
                    // сортировка действовала на все строки сразу. Новинки помечены
                    // 🆕 в строке; на commit они всё равно уйдут отдельными заявками.
                    const sorted = sortRows(rows);
                    return <>{sorted.slice(0, visibleLimit).map(renderRow)}{moreRow(Math.min(visibleLimit, sorted.length), sorted.length)}</>;
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
    /** Кратность короба (K). >0 → у активной ячейки кнопки ±короб и подсказка «N кор». */
    box?: number;
    /** Потолок в штуках (свободный остаток ФФ). undefined → без ограничения. */
    max?: number;
}

function NumericCell({ value, onChange, invalid, box, max }: NumericCellProps) {
    const [editing, setEditing] = useState(false);
    const [text, setText] = useState('');
    const inputRef = useRef<HTMLInputElement>(null);
    const k = box && box > 0 ? box : 0;
    // Потолок, выровненный вниз по коробу (если остаток задан).
    const cap = max === undefined ? Infinity : (k > 0 ? Math.floor(Math.max(0, max) / k) * k : Math.max(0, max));

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

    // Текущее значение из ввода (или value), для шага и подсказки.
    const curVal = (() => { const n = parseInt(text, 10); return isNaN(n) ? value : Math.max(0, n); })();
    // Шаг ±один короб: выровнять на сетку короба (вниз) и прибавить/убавить K.
    // onMouseDown+preventDefault — чтобы инпут не терял фокус (иначе onBlur закроет ввод).
    const stepBox = (dir: 1 | -1) => {
        const step = k > 0 ? k : 1;
        const base = k > 0 ? Math.floor(curVal / k) * k : curVal;
        const next = Math.min(cap, Math.max(0, base + dir * step));
        setText(String(next));
        onChange(next);
        inputRef.current?.focus();
    };

    const stepBtnStyle = {
        width: 22, height: 24, padding: 0, flex: '0 0 auto',
        border: '1px solid var(--color-border)', borderRadius: 6,
        background: 'var(--color-bg-card)', cursor: 'pointer',
        fontSize: 15, lineHeight: 1, color: 'var(--color-accent)',
    } as const;

    if (editing) {
        return (
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                {k > 0 && (
                    <button type="button" tabIndex={-1} title={`− короб (${formatNumber(k, 0)} шт)`}
                        onMouseDown={e => { e.preventDefault(); stepBox(-1); }}
                        style={stepBtnStyle}
                    >−</button>
                )}
                <input
                    ref={inputRef}
                    type="number"
                    min={0}
                    step={k > 0 ? k : 1}
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
                        width: k > 0 ? 54 : 70,
                        padding: '3px 6px',
                        borderRadius: 6,
                        border: '2px solid var(--color-accent)',
                        fontSize: 12,
                        textAlign: 'right',
                        outline: 'none',
                    }}
                />
                {k > 0 && (
                    <button type="button" tabIndex={-1} disabled={curVal >= cap}
                        title={curVal >= cap ? 'Достигнут свободный остаток ФФ' : `+ короб (${formatNumber(k, 0)} шт)`}
                        onMouseDown={e => { e.preventDefault(); if (curVal < cap) stepBox(1); }}
                        style={{ ...stepBtnStyle, opacity: curVal >= cap ? 0.4 : 1, cursor: curVal >= cap ? 'default' : 'pointer' }}
                    >+</button>
                )}
                {k > 0 && (
                    <span style={{ fontSize: 10, color: 'var(--color-text-muted)', whiteSpace: 'nowrap' }}>
                        {formatNumber(Math.floor(curVal / k), 0)} кор{cap !== Infinity ? ` / макс ${formatNumber(Math.floor(cap / k), 0)}` : ''}
                    </span>
                )}
            </div>
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
            title={k > 0 ? `${formatNumber(value, 0)} шт = ${formatNumber(Math.floor(value / k), 0)} кор · кратно ${formatNumber(k, 0)}` : 'Нажмите для редактирования'}
        >
            {value > 0 ? formatNumber(value, 0) : '—'}
        </div>
    );
}
