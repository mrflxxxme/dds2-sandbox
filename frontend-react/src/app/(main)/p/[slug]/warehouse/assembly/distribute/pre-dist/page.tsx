'use client';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatDate, formatNumber } from '@/lib/utils';
import { parseBoxSize, palletsForLines, maxPalletHeightCm, type PalletLine } from '@/lib/utils/boxPallet';
import { DISTRICT_ORDER, DISTRICT_LABELS, DISTRICT_COLORS } from '@/lib/constants/localization';
import { Toast } from '@/components';
import BoxDetailCell from '@/components/BoxDetailCell';
import {
    applyAcceptanceSplits,
    buildPoolSkus,
    enrichPoolRows,
    finalizePoolRows,
    rowsToPreDistRows,
    type AcceptanceSplitMap,
    type EnrichedSku,
    type PoolDistInput,
} from '@/lib/assembly/preDistribution';
import { seedNewcomerWholeBoxes, type SeedAnchor } from '@/lib/assembly/coldStartSeed';
import type {
    AssemblyDraftRow,
    PackageType,
    PreDistVehiclePool,
    StockNeedResponse,
} from '@/types/api';

const PKG_LABEL: Record<PackageType, string> = { BOX: 'Короб', MONOPALLET: 'Моно', SUPERSAFE: 'Сейф' };

/** Сколько коробов из штук при кратности `ppb`. */
const boxesOf = (qty: number, ppb: number | null | undefined): number =>
    ppb && ppb > 0 ? Math.ceil(qty / ppb) : 0;

const districtRank = (d: string): number => {
    const i = (DISTRICT_ORDER as readonly string[]).indexOf(d);
    return i < 0 ? DISTRICT_ORDER.length : i;
};

/** Экран «Распределить машину» — открывается из вкладки «🚚 Предраспределение» (?vehicle=<id>).
 *  Полноэкранная матрица как «Потребность по складам», но источник = остатки машины (пул):
 *  per-WB-склад остаток 🏬 / в сборке-в пути 🚚 / потребность + что отправляем (коробá),
 *  бейджи «новинка» / «кратно N», счётчик коробов и паллет. Заявки создаются со статусом
 *  «Предраспределение» (без фейкового стока); при разгрузке станут обычными сборками. */
export default function PreDistVehiclePage() {
    const params = useParams();
    const router = useRouter();
    const searchParams = useSearchParams();
    const slug = params.slug as string;
    const vehicleId = Number(searchParams.get('vehicle')) || null;

    const [pool, setPool] = useState<PreDistVehiclePool | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

    // Справочники движка (зеркало PreDistributionView / distribute page).
    const [stockNeed, setStockNeed] = useState<StockNeedResponse | null>(null);
    const [newcomerSet, setNewcomerSet] = useState<Set<number>>(new Set());
    const [anchors, setAnchors] = useState<SeedAnchor[]>([]);
    const [nmPpb, setNmPpb] = useState<Map<number, number | null>>(new Map());
    const [nmBoxSize, setNmBoxSize] = useState<Map<number, string | null>>(new Map());
    const [palletOverrides, setPalletOverrides] = useState<Record<string, number>>({});
    const [geomReady, setGeomReady] = useState(false);

    // Раскладка.
    const [distRows, setDistRows] = useState<AssemblyDraftRow[] | null>(null);
    const [distComputing, setDistComputing] = useState(false);
    const [acceptanceNote, setAcceptanceNote] = useState<{ moved: number; dropped: number; failed: boolean } | null>(null);
    const [submitting, setSubmitting] = useState(false);
    // Округление: false = целые коробы (частичные паллеты ок), true = строго целые паллеты.
    // По умолчанию «коробами» — целые паллеты часто обнуляют мелкую потребность машины.
    const [wholePallets, setWholePallets] = useState(false);

    const showToast = useCallback((message: string, type: 'success' | 'error') => setToast({ message, type }), []);
    const backToList = useCallback(
        () => router.push(`/p/${slug}/warehouse/assembly/distribute?tab=pre-dist`),
        [router, slug],
    );

    // ─── Загрузка пула + всех справочников разом ───────────────────────────
    useEffect(() => {
        if (!vehicleId) { setLoading(false); return; }
        const controller = new AbortController();
        (async () => {
            setLoading(true);
            setError(null);
            try {
                const [poolData, need, cold, boxMult, palletOv] = await Promise.all([
                    api.getPreDistVehiclePool(vehicleId),
                    (api.getStockNeed(14, 14, 'actual') as Promise<StockNeedResponse | null>).catch(() => null),
                    api.getColdStartTable(14).catch(() => null),  // окно 14д — как getStockNeed(14,14) и «Потребность»
                    api.getBoxMultiplicity().catch(() => null),
                    api.getPalletBoxesBySize().catch(() => ({} as Record<string, number>)),
                ]);
                if (controller.signal.aborted) return;
                setPool(poolData);
                setStockNeed(need);

                const ncs = new Set<number>();
                for (const r of cold?.rows ?? []) if (r.is_newcomer) ncs.add(r.nm_id);
                setNewcomerSet(ncs);
                setAnchors((cold?.main_warehouses ?? []).map(w => ({ warehouse: w.warehouse, share_pct: w.share_pct })));

                const ppbMap = new Map<number, number | null>();
                const sizeMap = new Map<number, string | null>();
                for (const r of boxMult?.items ?? []) {
                    let ppb: number | null = null;
                    if (r.box_qty_override && r.box_qty_override > 0 && r.use_box_multiplicity) {
                        ppb = r.box_qty_override;
                    } else {
                        let best = 0;
                        for (const p of r.per_warehouse) {
                            if (p.box_qty && p.box_qty > 0 && p.use_box_multiplicity && (best === 0 || p.box_qty < best)) best = p.box_qty;
                        }
                        ppb = best > 0 ? best : null;
                    }
                    let boxSize: string | null = null;
                    let bestStock = -1;
                    for (const p of r.per_warehouse) {
                        if (!p.box_size || !parseBoxSize(p.box_size)) continue;
                        if (p.rf_stock > bestStock) { boxSize = p.box_size; bestStock = p.rf_stock; }
                    }
                    ppbMap.set(r.nm_id, ppb);
                    sizeMap.set(r.nm_id, boxSize);
                }
                setNmPpb(ppbMap);
                setNmBoxSize(sizeMap);
                setPalletOverrides(palletOv || {});
                setGeomReady(true);
            } catch (e) {
                if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Ошибка загрузки машины');
            } finally {
                if (!controller.signal.aborted) setLoading(false);
            }
        })();
        return () => controller.abort();
    }, [vehicleId]);

    const vehicle = pool?.vehicle ?? null;

    // ─── Авто-раскладка: пул × потребность → приёмка → целые коробы/паллеты ──
    const computeDistribution = useCallback(async (poolData: PreDistVehiclePool, signal: AbortSignal) => {
        const targetWh = poolData.vehicle.target_warehouse_id;
        if (targetWh == null) { setDistRows([]); setAcceptanceNote(null); return; }
        setDistComputing(true);
        try {
            const distInput: PoolDistInput = {
                poolRows: poolData.rows,
                targetWarehouseId: targetWh,
                stockNeed,
                nmPpb,
                nmBoxSize,
                palletOverrides,
            };
            const { skus } = buildPoolSkus(distInput);
            if (skus.length === 0) {
                if (!signal.aborted) { setDistRows([]); setAcceptanceNote(null); }
                return;
            }
            let splitMap: AcceptanceSplitMap | null = null;
            let moved = 0, dropped = 0, failed = false;
            try {
                const resp = await api.checkWbAcceptance({
                    items: skus.map(s => ({ nm_id: s.nm_id, barcode: s.barcode, distribution: s.target })),
                });
                splitMap = new Map();
                for (const it of resp.items) {
                    const splits = it.splits?.length
                        ? it.splits.map(sp => ({ package_type: sp.package_type, distribution: sp.distribution }))
                        : [{ package_type: it.package_type, distribution: it.distribution }];
                    splitMap.set(`${it.nm_id}::${it.barcode}`, splits);
                }
                for (const m of resp.moves ?? []) { if (m.to_warehouse) moved += m.quantity; else dropped += m.quantity; }
            } catch {
                failed = true;
            }
            const effective = applyAcceptanceSplits(skus, splitMap);
            const needRows = finalizePoolRows(effective, distInput, wholePallets);

            // Засев НОВИНОК (cold-start): у них нет истории продаж → потребность=0 → они
            // не попадают в раскладку по потребности (buildPoolSkus их пропускает). По
            // требованию — раскладываем остаток машины целыми коробами по топ-складам
            // округов (по доле), а не держим на ФФ. Только в режиме «📦 Коробами»: на
            // мелком засеве целые паллеты обнуляются, а экран в режиме «🚚 Паллеты»
            // обещает «хвост < паллеты остаётся на ФФ» — не противоречим.
            const seededRows: AssemblyDraftRow[] = [];
            if (!wholePallets && anchors.length > 0) {
                // Уже запланировано по потребности: всего по баркоду (кап остатка) и
                // per WB-склад (чтобы засев не пилил ПОВЕРХ того же склада — это тоже покрытие).
                const shippedByBc = new Map<string, number>();
                const needShipByBcWh = new Map<string, Map<string, number>>();
                for (const r of rowsToPreDistRows(needRows)) {
                    shippedByBc.set(r.barcode, (shippedByBc.get(r.barcode) ?? 0) + r.qty);
                    const wh = needShipByBcWh.get(r.barcode) ?? new Map<string, number>();
                    wh.set(r.wb_warehouse_name, (wh.get(r.wb_warehouse_name) ?? 0) + r.qty);
                    needShipByBcWh.set(r.barcode, wh);
                }
                // Покрытие per nm per WB-склад (остаток WB + в сборке + в пути).
                const enrich = enrichPoolRows(poolData.rows, stockNeed, newcomerSet);
                for (const pr of poolData.rows) {
                    const nm = pr.article_wb ? Number(pr.article_wb) : 0;
                    if (!nm || !newcomerSet.has(nm)) continue;
                    const avail = Math.max(0, Math.floor(Number(pr.available_qty) || 0));
                    const remaining = avail - (shippedByBc.get(pr.barcode) ?? 0);
                    const byWh = enrich.get(nm)?.byWh;
                    const needWh = needShipByBcWh.get(pr.barcode);
                    const covAnchors = anchors.map(a => {
                        const c = byWh?.[a.warehouse];
                        // покрытие = остаток WB + сборка + пути + уже запланированное по потребности сюда в этом же расчёте.
                        const existing = (c ? c.stock + c.asm + c.transit : 0) + (needWh?.get(a.warehouse) ?? 0);
                        return { warehouse: a.warehouse, share_pct: a.share_pct, existing };
                    });
                    const seeded = seedNewcomerWholeBoxes(remaining, nmPpb.get(nm), covAnchors);
                    const tot = Object.values(seeded).reduce((s, v) => s + v, 0);
                    if (tot > 0) {
                        seededRows.push({
                            nm_id: nm, barcode: pr.barcode, vendor_code: pr.article_seller || String(nm),
                            src: { [String(targetWh)]: tot }, tgt: seeded, package_type: 'BOX',
                        });
                    }
                }
            }
            const rows = [...needRows, ...seededRows];
            if (!signal.aborted) {
                setDistRows(rows);
                setAcceptanceNote({ moved, dropped, failed });
            }
        } catch (e) {
            if (!signal.aborted) { setDistRows([]); showToast(e instanceof Error ? e.message : 'Ошибка раскладки', 'error'); }
        } finally {
            if (!signal.aborted) setDistComputing(false);
        }
    }, [stockNeed, nmPpb, nmBoxSize, palletOverrides, wholePallets, newcomerSet, anchors, showToast]);

    useEffect(() => {
        if (!pool || !geomReady) return;
        const controller = new AbortController();
        computeDistribution(pool, controller.signal);
        return () => controller.abort();
    }, [pool, geomReady, computeDistribution]);

    // ─── Производные: позиции к созданию + обогащение + матрица ─────────────
    const submitRows = useMemo(() => (distRows ? rowsToPreDistRows(distRows) : []), [distRows]);

    const enrichMap = useMemo(
        () => enrichPoolRows(pool?.rows ?? [], stockNeed, newcomerSet),
        [pool, stockNeed, newcomerSet],
    );

    // barcode → nm_id (для геометрии коробов/паллет по строкам отправки).
    const nmByBc = useMemo(() => {
        const m = new Map<string, number>();
        for (const r of pool?.rows ?? []) m.set(r.barcode, r.article_wb ? Number(r.article_wb) : 0);
        return m;
    }, [pool]);

    const derived = useMemo(() => {
        const allocByBc = new Map<string, number>();
        const cellByBc = new Map<string, Map<string, { qty: number; pkg: PackageType }>>();
        for (const r of submitRows) {
            allocByBc.set(r.barcode, (allocByBc.get(r.barcode) ?? 0) + r.qty);
            const cell = cellByBc.get(r.barcode) ?? new Map();
            const cur = cell.get(r.wb_warehouse_name);
            cell.set(r.wb_warehouse_name, { qty: (cur?.qty ?? 0) + r.qty, pkg: r.package_type });
            cellByBc.set(r.barcode, cell);
        }
        const groupKeys = new Set(submitRows.map(r => `${r.wb_warehouse_name}::${r.package_type}`));
        const totalShip = submitRows.reduce((s, r) => s + r.qty, 0);
        const totalBoxes = submitRows.reduce((s, r) => s + boxesOf(r.qty, nmPpb.get(nmByBc.get(r.barcode) ?? 0)), 0);
        return { allocByBc, cellByBc, requestCount: groupKeys.size, totalShip, totalBoxes };
    }, [submitRows, nmPpb, nmByBc]);

    // Колонки WB-складов (релевантные: куда отправляем ИЛИ где есть потребность) + округа.
    const wbCols = useMemo(() => {
        const distByWh = new Map<string, string>();
        for (const w of stockNeed?.warehouses ?? []) if (w.name) distByWh.set(w.name, w.district_key || 'unknown');
        const names = new Set<string>();
        for (const r of submitRows) names.add(r.wb_warehouse_name);
        for (const e of enrichMap.values()) {
            for (const [wh, c] of Object.entries(e.byWh)) if (c.need > 0) names.add(wh);
        }
        const arr = [...names].map(name => ({ name, district: distByWh.get(name) || 'unknown' }));
        arr.sort((a, b) => {
            const ra = districtRank(a.district), rb = districtRank(b.district);
            return ra !== rb ? ra - rb : a.name.localeCompare(b.name, 'ru');
        });
        return arr;
    }, [submitRows, enrichMap, stockNeed]);

    const districtGroups = useMemo(() => {
        const groups: { label: string; color: string; count: number }[] = [];
        for (const c of wbCols) {
            const label = DISTRICT_LABELS[c.district] || 'Прочие';
            const color = DISTRICT_COLORS[c.district] || 'var(--color-muted)';
            const last = groups[groups.length - 1];
            if (last && last.label === label) last.count++;
            else groups.push({ label, color, count: 1 });
        }
        return groups;
    }, [wbCols]);

    // Итоги по WB-складам (низ матрицы): отправить / коробов / паллет.
    const footer = useMemo(() => {
        const shipByWh = new Map<string, number>();
        const boxesByWh = new Map<string, number>();
        const linesByWhPkg = new Map<string, { wh: string; pkg: PackageType; lines: PalletLine[] }>();
        for (const r of submitRows) {
            const nm = nmByBc.get(r.barcode) ?? 0;
            shipByWh.set(r.wb_warehouse_name, (shipByWh.get(r.wb_warehouse_name) ?? 0) + r.qty);
            boxesByWh.set(r.wb_warehouse_name, (boxesByWh.get(r.wb_warehouse_name) ?? 0) + boxesOf(r.qty, nmPpb.get(nm)));
            const key = `${r.wb_warehouse_name}::${r.package_type}`;
            const g = linesByWhPkg.get(key) ?? { wh: r.wb_warehouse_name, pkg: r.package_type, lines: [] };
            g.lines.push({ units: r.qty, boxQty: nmPpb.get(nm), boxSize: nmBoxSize.get(nm) ?? null });
            linesByWhPkg.set(key, g);
        }
        const palletsByWh = new Map<string, number>();
        let totalPallets = 0;
        for (const g of linesByWhPkg.values()) {
            const p = palletsForLines(g.lines, maxPalletHeightCm(g.wh), g.pkg === 'BOX' ? 'box' : 'mono', palletOverrides).pallets;
            palletsByWh.set(g.wh, (palletsByWh.get(g.wh) ?? 0) + p);
            totalPallets += p;
        }
        return { shipByWh, boxesByWh, palletsByWh, totalPallets };
    }, [submitRows, nmByBc, nmPpb, nmBoxSize, palletOverrides]);

    const onHoldQty = useMemo(
        () => (pool?.rows ?? []).reduce((s, r) => s + Math.max(0, (Number(r.available_qty) || 0) - (derived.allocByBc.get(r.barcode) ?? 0)), 0),
        [pool, derived],
    );

    // Строки таблицы: сначала те, что отправляем (по убыванию), потом остальные.
    const sortedRows = useMemo(() => {
        const rows = [...(pool?.rows ?? [])];
        rows.sort((a, b) => {
            const sa = derived.allocByBc.get(a.barcode) ?? 0;
            const sb = derived.allocByBc.get(b.barcode) ?? 0;
            if (sa !== sb) return sb - sa;
            return (a.article_seller || a.barcode).localeCompare(b.article_seller || b.barcode, 'ru');
        });
        return rows;
    }, [pool, derived]);

    const handleSubmit = useCallback(async () => {
        if (!vehicleId || submitRows.length === 0 || submitting) return;
        setSubmitting(true);
        try {
            const res = await api.createPreDistribution({ vehicle_id: vehicleId, rows: submitRows });
            showToast(`Создано ${formatNumber(res.created, 0)} заявок`, 'success');
            backToList();
        } catch (e) {
            showToast(e instanceof Error ? e.message : 'Ошибка создания заявок', 'error');
        } finally {
            setSubmitting(false);
        }
    }, [vehicleId, submitRows, submitting, showToast, backToList]);

    // ─── States ────────────────────────────────────────────────────────────
    const header = (
        <div className="page-header" style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <button className="btn btn-secondary btn-sm" onClick={backToList}>← Назад</button>
            <h1 className="page-title" style={{ margin: 0 }}>
                Распределение машины{vehicle ? ` ${vehicle.order_no}` : ''}
            </h1>
            {vehicle && (
                <>
                    <span className="badge badge-info">{vehicle.status}</span>
                    <span style={{ fontSize: 13, color: 'var(--color-muted)' }}>
                        Склад: <b style={{ color: 'var(--color-text)' }}>{vehicle.target_warehouse_name || '—'}</b>
                        {vehicle.eta ? ` · ETA ${formatDate(vehicle.eta)}` : ''}
                    </span>
                </>
            )}
        </div>
    );

    if (!vehicleId) {
        return (
            <div className="animate-in">
                {header}
                <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-muted)' }}>
                    Машина не выбрана. <button className="btn btn-secondary btn-sm" style={{ marginLeft: 8 }} onClick={backToList}>К списку машин</button>
                </div>
            </div>
        );
    }
    if (loading) {
        return (
            <div className="animate-in">
                {header}
                <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-muted)' }}>Загрузка пула и потребности…</div>
            </div>
        );
    }
    if (error) {
        return (
            <div className="animate-in">
                {header}
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>
                    <div style={{ color: 'var(--color-danger)', marginBottom: 16 }}>{error}</div>
                    <button className="btn btn-secondary" onClick={() => router.refresh()}>Повторить</button>
                </div>
            </div>
        );
    }
    if (!pool || pool.rows.length === 0) {
        return (
            <div className="animate-in">
                {header}
                <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-muted)' }}>На машине нет товара для распределения</div>
            </div>
        );
    }

    const computing = distComputing || distRows === null;

    return (
        <div className="animate-in">
            {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}
            {header}

            <div className="glass-card" style={{ padding: 16, marginBottom: 16, color: 'var(--color-muted)', fontSize: 13 }}>
                Раскладка груза машины по WB-складам как в «Потребность по складам» (потребность · приёмка · целые коробы и паллеты),
                но источник — остатки этой машины. Колонки складов: 🏬 остаток на WB · 🚚 в сборке/в пути · потребность · <b style={{ color: 'var(--color-text)' }}>что отправляем</b>.
                Заявки создаются со статусом «Предраспределение» (без фейкового стока) — при разгрузке машины станут обычными сборками.
            </div>

            {/* KPI-сводка */}
            <div className="glass-card" style={{ padding: 16, marginBottom: 16, display: 'flex', gap: 28, flexWrap: 'wrap', alignItems: 'center', fontSize: 14 }}>
                <span>К отправке: <b style={{ fontSize: 18 }}>{formatNumber(derived.totalShip, 0)}</b> шт</span>
                <span>📦 Коробов: <b style={{ fontSize: 18 }}>{formatNumber(derived.totalBoxes, 0)}</b></span>
                <span>🚚 Паллет: <b style={{ fontSize: 18 }}>{formatNumber(footer.totalPallets, 0)}</b></span>
                <span style={{ color: 'var(--color-muted)' }}>Заявок: <b style={{ color: 'var(--color-text)' }}>{formatNumber(derived.requestCount, 0)}</b></span>
                <span style={{ color: 'var(--color-muted)' }}>На хранение (ФФ): <b style={{ color: 'var(--color-text)' }}>{formatNumber(onHoldQty, 0)}</b> шт</span>
                <div style={{ marginLeft: 'auto', display: 'flex', gap: 12, alignItems: 'center' }}>
                    <div style={{ display: 'flex', gap: 4, alignItems: 'center' }} title="Целые паллеты часто обнуляют мелкую потребность машины — «Коробами» показывает то, что набирается коробами">
                        <span style={{ fontSize: 12, color: 'var(--color-muted)' }}>Округление:</span>
                        <button className={`btn btn-sm ${!wholePallets ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setWholePallets(false)}>📦 Коробами</button>
                        <button className={`btn btn-sm ${wholePallets ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setWholePallets(true)}>🚚 Паллеты</button>
                    </div>
                    <button className="btn btn-primary" onClick={handleSubmit} disabled={submitting || submitRows.length === 0}>
                        {submitting ? 'Создание…' : `Создать заявки (${formatNumber(derived.requestCount, 0)})`}
                    </button>
                </div>
            </div>

            {acceptanceNote && (acceptanceNote.failed || acceptanceNote.moved > 0 || acceptanceNote.dropped > 0) && (
                <div style={{ marginBottom: 12, fontSize: 13, color: acceptanceNote.failed ? 'var(--color-warning)' : 'var(--color-muted)' }}>
                    {acceptanceNote.failed
                        ? '⚠️ Приёмка WB недоступна — разложено без проверки складов.'
                        : `Приёмка:${acceptanceNote.moved > 0 ? ` ↪ ${formatNumber(acceptanceNote.moved, 0)} перераспределено с закрытых` : ''}${acceptanceNote.dropped > 0 ? ` · ⛔ ${formatNumber(acceptanceNote.dropped, 0)} на закрытых складах` : ''}`}
                </div>
            )}

            {computing ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-muted)' }}>Считаю раскладку (потребность · приёмка · коробы · паллеты)…</div>
            ) : derived.totalShip === 0 ? (
                <div className="glass-card" style={{ padding: 32, textAlign: 'center', color: 'var(--color-muted)' }}>
                    {wholePallets
                        ? 'В режиме «🚚 Паллеты» ничего не набирается на целую паллету — мелкая потребность по складам остаётся на ФФ. Переключите на «📦 Коробами», чтобы отгрузить целыми коробами.'
                        : 'По товару машины нет потребности WB (или не набирается целый короб) — весь груз остаётся на ФФ.'}
                </div>
            ) : (
                <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                        <thead>
                            {/* Шапка округов */}
                            <tr style={{ borderBottom: '1px solid var(--color-border)' }}>
                                <th colSpan={4} style={{ position: 'sticky', left: 0, background: 'var(--color-bg-card)', zIndex: 2 }} />
                                {districtGroups.map((g, i) => (
                                    <th key={i} colSpan={g.count} style={{ padding: '6px 8px', textAlign: 'center', color: '#fff', background: g.color, fontSize: 11, letterSpacing: 0.4, textTransform: 'uppercase' }}>
                                        {g.label}
                                    </th>
                                ))}
                                {/* хвост: Σ отпр. + Мест + Остаётся ФФ = 3 колонки */}
                                <th colSpan={3} />
                            </tr>
                            {/* Шапка колонок */}
                            <tr style={{ borderBottom: '1px solid var(--color-border)', color: 'var(--color-muted)', textAlign: 'right' }}>
                                <th style={{ padding: '8px 12px', textAlign: 'left', position: 'sticky', left: 0, background: 'var(--color-bg-card)', zIndex: 2 }}>Товар</th>
                                <th style={{ padding: '8px 8px' }}>На машине</th>
                                <th style={{ padding: '8px 8px' }} title="Уже в сборке на WB">В сборке</th>
                                <th style={{ padding: '8px 8px' }} title="Остаток на Wildberries">На WB</th>
                                {wbCols.map(c => (
                                    <th key={c.name} style={{ padding: '8px 8px', whiteSpace: 'nowrap' }}>{c.name}</th>
                                ))}
                                <th style={{ padding: '8px 8px' }}>Σ отпр.</th>
                                <th style={{ padding: '8px 8px' }} title="Коробов к отправке">Мест</th>
                                <th style={{ padding: '8px 8px' }}>Остаётся ФФ</th>
                            </tr>
                        </thead>
                        <tbody>
                            {sortedRows.map(row => {
                                const nm = nmByBc.get(row.barcode) ?? 0;
                                const e: EnrichedSku | undefined = enrichMap.get(nm);
                                const avail = Number(row.available_qty) || 0;
                                const ship = derived.allocByBc.get(row.barcode) ?? 0;
                                const stays = Math.max(0, avail - ship);
                                const cells = derived.cellByBc.get(row.barcode);
                                const ppb = nmPpb.get(nm);
                                const label = row.article_seller || row.article_wb || row.barcode;
                                const rowBoxes = boxesOf(ship, ppb);
                                return (
                                    <tr key={row.barcode} style={{ borderBottom: '1px solid var(--color-border)', background: ship > 0 ? 'rgba(59,130,246,0.04)' : undefined }}>
                                        <td style={{ padding: '6px 12px', position: 'sticky', left: 0, background: ship > 0 ? 'var(--color-bg-card)' : 'var(--color-bg-card)', zIndex: 1 }}>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                                                <span style={{ fontWeight: 600 }}>{label}</span>
                                                {e?.isNew && <span className="badge" style={{ background: 'rgba(168,85,247,0.16)', color: '#a855f7', fontSize: 10, padding: '1px 6px' }}>🆕 новинка</span>}
                                                {ppb ? <span className="badge badge-secondary" style={{ fontSize: 10, padding: '1px 6px' }}>📦 кратно {formatNumber(ppb, 0)}</span> : <span className="badge" style={{ background: 'rgba(255,159,10,0.14)', color: 'var(--color-warning)', fontSize: 10, padding: '1px 6px' }}>без кратности</span>}
                                            </div>
                                            <div style={{ fontSize: 11, color: 'var(--color-muted)' }}>{row.name ? `${row.name} · ` : ''}ШК {row.barcode}</div>
                                        </td>
                                        <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(avail, 0)}</td>
                                        <td style={{ padding: '6px 8px', textAlign: 'right', color: e && e.inAssembly > 0 ? 'var(--color-text)' : 'var(--color-dim)' }}>{e && e.inAssembly > 0 ? formatNumber(e.inAssembly, 0) : '·'}</td>
                                        <td style={{ padding: '6px 8px', textAlign: 'right', color: e && e.stocksWb > 0 ? 'var(--color-text)' : 'var(--color-dim)' }}>{e && e.stocksWb > 0 ? formatNumber(e.stocksWb, 0) : '·'}</td>
                                        {wbCols.map(c => {
                                            const cell = cells?.get(c.name);
                                            const ctx = e?.byWh[c.name];
                                            const ctxBusy = (ctx?.asm ?? 0) + (ctx?.transit ?? 0);
                                            const hasCtx = ctx && (ctx.stock > 0 || ctxBusy > 0 || ctx.need > 0);
                                            return (
                                                <td key={c.name} style={{ padding: '6px 8px', textAlign: 'right', verticalAlign: 'top' }}>
                                                    {cell ? (
                                                        <div style={{ fontWeight: 700, color: 'var(--color-accent)' }}>
                                                            {formatNumber(cell.qty, 0)}{cell.pkg !== 'BOX' && <span style={{ fontSize: 9, color: 'var(--color-muted)' }}> {PKG_LABEL[cell.pkg]}</span>}
                                                            <span style={{ fontSize: 10, color: 'var(--color-muted)', marginLeft: 4 }}>📦<BoxDetailCell qty={cell.qty} pcsPerBox={ppb ?? 0} /></span>
                                                        </div>
                                                    ) : (ctx && ctx.need > 0 ? <div style={{ color: 'var(--color-dim)' }}>↗{formatNumber(ctx.need, 0)}</div> : <div style={{ color: 'var(--color-dim)' }}>·</div>)}
                                                    {hasCtx && (
                                                        <div style={{ fontSize: 10, color: 'var(--color-muted)', whiteSpace: 'nowrap' }}>
                                                            {ctx.stock > 0 && <span title="Остаток на WB">🏬{formatNumber(ctx.stock, 0)}</span>}
                                                            {ctxBusy > 0 && <span title="В сборке / в пути" style={{ marginLeft: 4 }}>🚚{formatNumber(ctxBusy, 0)}</span>}
                                                        </div>
                                                    )}
                                                </td>
                                            );
                                        })}
                                        <td style={{ padding: '6px 8px', textAlign: 'right', fontWeight: 600 }}>{ship > 0 ? formatNumber(ship, 0) : '·'}</td>
                                        <td style={{ padding: '6px 8px', textAlign: 'right' }}>{rowBoxes > 0 ? formatNumber(rowBoxes, 0) : '·'}</td>
                                        <td style={{ padding: '6px 8px', textAlign: 'right', color: 'var(--color-muted)' }}>{formatNumber(stays, 0)}</td>
                                    </tr>
                                );
                            })}
                        </tbody>
                        <tfoot>
                            <tr style={{ borderTop: '2px solid var(--color-border)', fontWeight: 600 }}>
                                <td style={{ padding: '8px 12px', position: 'sticky', left: 0, background: 'var(--color-bg-card)' }}>Отправить, шт</td>
                                <td colSpan={3} />
                                {wbCols.map(c => (
                                    <td key={c.name} style={{ padding: '8px 8px', textAlign: 'right', color: 'var(--color-accent)' }}>{formatNumber(footer.shipByWh.get(c.name) ?? 0, 0)}</td>
                                ))}
                                <td style={{ padding: '8px 8px', textAlign: 'right' }}>{formatNumber(derived.totalShip, 0)}</td>
                                <td colSpan={2} />
                            </tr>
                            <tr style={{ color: 'var(--color-muted)' }}>
                                <td style={{ padding: '6px 12px', position: 'sticky', left: 0, background: 'var(--color-bg-card)' }}>📦 Коробов</td>
                                <td colSpan={3} />
                                {wbCols.map(c => (
                                    <td key={c.name} style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(footer.boxesByWh.get(c.name) ?? 0, 0)}</td>
                                ))}
                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(derived.totalBoxes, 0)}</td>
                                <td colSpan={2} />
                            </tr>
                            <tr style={{ color: 'var(--color-muted)' }}>
                                <td style={{ padding: '6px 12px', position: 'sticky', left: 0, background: 'var(--color-bg-card)' }}>🚚 Паллет</td>
                                <td colSpan={3} />
                                {wbCols.map(c => (
                                    <td key={c.name} style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(footer.palletsByWh.get(c.name) ?? 0, 0)}</td>
                                ))}
                                <td style={{ padding: '6px 8px', textAlign: 'right' }}>{formatNumber(footer.totalPallets, 0)}</td>
                                <td colSpan={2} />
                            </tr>
                        </tfoot>
                    </table>
                </div>
            )}
        </div>
    );
}
