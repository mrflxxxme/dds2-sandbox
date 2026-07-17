'use client';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { NEED_SUPPLY_DAYS, NEED_ANALYSIS_DAYS } from '@/lib/assembly/needParams';
import { exportToExcel, formatNumber } from '@/lib/utils';
import { Toast } from '@/components';
import KpiCard from '@/components/KpiCard';
import type { AcceptanceCheckRequest, AcceptanceFlags, AssemblyDraft, AssemblyDraftUnitRef, HandedUnitItem, PackageType, Warehouse } from '@/types/api';
import {
    buildFfUnits,
    findDraftUnit,
    excelSheetName,
    PKG_LABEL_RU,
    PREVIEW_EXPORT_COLUMNS,
    type DraftUnit,
} from '@/lib/utils/assemblyPreview';

interface StockNeedArticle {
    nm_id: number;
    vendor_code: string;
    barcode?: string;
    rf_stocks?: Record<number, { stock: number; available: number }>;
}
interface StockNeedResponse { articles: StockNeedArticle[] }

interface EditItem { nmId: number; vendor: string; barcode: string; qty: number }

const STATUS_BADGE: Record<string, { label: string; bg: string; color: string }> = {
    draft: { label: 'Черновик', bg: 'rgba(142,142,147,0.16)', color: '#6e6e73' },
    handed: { label: 'Передан на ФФ', bg: 'rgba(245,158,11,0.18)', color: '#a16207' },
};

export default function AssemblyRequestUnitPage() {
    const params = useParams();
    const router = useRouter();
    const searchParams = useSearchParams();
    const slug = params.slug as string;

    const draftId = Number(searchParams.get('draft')) || null;
    const ffId = Number(searchParams.get('ff')) || null;
    const wbName = searchParams.get('wb') || '';
    const pkgRaw = searchParams.get('pkg');
    // Целевой WB при «Сменить склад WB»: выставляется синхронно в момент переноса,
    // ДО того как router.replace(...wb=newWb) протолкнёт новый параметр в URL. На
    // happy-path searchParams отстают на один рендер → без этого lookup юнита по
    // старому wbName вернул бы null и мелькнул бы empty-state «Заявка не найдена».
    const [pendingWb, setPendingWb] = useState<string | null>(null);
    // Эффективный WB: предпочитаем pendingWb, пока URL не догнал перенос.
    const effectiveWb = pendingWb ?? wbName;
    const pkg: PackageType = pkgRaw === 'MONOPALLET' ? 'MONOPALLET' : pkgRaw === 'SUPERSAFE' ? 'SUPERSAFE' : 'BOX';

    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
    const [draft, setDraft] = useState<AssemblyDraft | null>(null);
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [nmPpb, setNmPpb] = useState<Map<number, number | null>>(new Map());
    const [stockNeed, setStockNeed] = useState<StockNeedResponse | null>(null);

    const [editMode, setEditMode] = useState(false);
    const [editItems, setEditItems] = useState<EditItem[]>([]);
    const [addQuery, setAddQuery] = useState('');
    const [moveOpen, setMoveOpen] = useState(false);
    // Приёмка WB для «Сменить склад WB»: nm_id → {wb_name: флаги}. Грузим лениво
    // при открытии дропдауна (live-вызов WB API, кэш 5 мин на backend).
    const [moveAcc, setMoveAcc] = useState<Map<number, Record<string, AcceptanceFlags>>>(new Map());
    const [accLoading, setAccLoading] = useState(false);
    const [accError, setAccError] = useState<string | null>(null);

    useEffect(() => {
        if (!draftId) { setError('Не указан ID черновика'); setLoading(false); return; }
        let cancelled = false;
        (async () => {
            setLoading(true);
            setError(null);
            try {
                const [d, whs, sn] = await Promise.all([
                    api.getAssemblyDraft(draftId),
                    api.getWarehouses(),
                    api.getStockNeed(NEED_SUPPLY_DAYS, NEED_ANALYSIS_DAYS, 'actual').catch(() => null) as Promise<StockNeedResponse | null>,
                ]);
                if (cancelled) return;
                setDraft(d);
                setWarehouses(whs);
                setStockNeed(sn);
            } catch (e: unknown) {
                if (!cancelled) setError(e instanceof Error ? e.message : 'Ошибка загрузки');
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [draftId]);

    useEffect(() => {
        let cancelled = false;
        api.getBoxMultiplicity()
            .then(resp => {
                if (cancelled) return;
                const m = new Map<number, number | null>();
                for (const r of resp.items) {
                    let ppb: number | null = null;
                    // Действующая кратность склада ЭТОЙ страницы (ffId) в приоритете —
                    // товар физически едет его коробами (machine-first). Min по складам —
                    // только фолбэк (давал псевдо-россыпь на целых коробах чужого склада).
                    const own = ffId != null ? r.per_warehouse.find(p => p.warehouse_id === ffId) : undefined;
                    if (own?.box_qty && own.box_qty > 0 && own.use_box_multiplicity) {
                        ppb = own.box_qty;
                    } else if (r.box_qty_override && r.box_qty_override > 0 && r.use_box_multiplicity) {
                        ppb = r.box_qty_override;
                    } else {
                        let best = 0;
                        for (const p of r.per_warehouse) {
                            if (p.box_qty && p.box_qty > 0 && p.use_box_multiplicity && (best === 0 || p.box_qty < best)) best = p.box_qty;
                        }
                        ppb = best > 0 ? best : null;
                    }
                    m.set(r.nm_id, ppb);
                }
                setNmPpb(m);
            })
            .catch(() => { /* best-effort */ });
        return () => { cancelled = true; };
    }, [ffId]);

    const warehouseNameById = useCallback(
        (id: number) => warehouses.find(w => w.id === id)?.name ?? `Склад ${id}`,
        [warehouses],
    );
    const newcomerNmIds = useMemo(() => new Set(draft?.newcomer_nm_ids ?? []), [draft]);

    const unit = useMemo<DraftUnit | null>(() => {
        if (!draft || ffId == null) return null;
        return findDraftUnit(
            draft.distribution.rows ?? [],
            draft.distribution.handed_units ?? [],
            newcomerNmIds, ffId, effectiveWb, pkg,
        );
    }, [draft, ffId, effectiveWb, pkg, newcomerNmIds]);

    // Перенос завершён, URL догнал pendingWb → перестаём его предпочитать.
    useEffect(() => {
        if (pendingWb !== null && wbName === pendingWb) setPendingWb(null);
    }, [pendingWb, wbName]);

    // Доступные WB-склады черновика для переноса (кроме текущего).
    const availableWbTargets = useMemo(
        () => (draft?.distribution.target_warehouse_names ?? []).filter(n => n && n !== effectiveWb),
        [draft, effectiveWb],
    );

    const items = useMemo(
        () => (unit ? [...unit.items].sort((a, b) => b.qty - a.qty || a.vendor.localeCompare(b.vendor)) : []),
        [unit],
    );
    const ffName = ffId != null ? warehouseNameById(ffId) : '';
    const kOf = useCallback((nmId: number) => nmPpb.get(nmId) || 0, [nmPpb]);
    // Коробок = ⌈шт / K⌉: неполный короб тоже считается коробом (товар всё равно кладётся в короб).
    const boxesOf = useCallback((nmId: number, qty: number) => { const k = kOf(nmId); return k > 0 ? Math.ceil(qty / k) : 0; }, [kOf]);

    // Существующая загрузка этого ФФ по каждому WB-складу (текущий срез pkg×новизна) —
    // подсказка в дропдауне «Сменить склад WB»: куда вольётся перенос.
    const wbTargetStats = useMemo(() => {
        const m = new Map<string, { qty: number; boxes: number; sku: number }>();
        if (!draft || ffId == null) return m;
        const units = buildFfUnits(draft.distribution.rows ?? [], draft.distribution.handed_units ?? [], newcomerNmIds, ffId);
        for (const u of units) {
            if (u.pkg !== pkg) continue;
            const boxes = u.items.reduce((s, it) => s + boxesOf(it.nmId, it.qty), 0);
            m.set(u.wbName, { qty: u.qty, boxes, sku: u.items.length });
        }
        return m;
    }, [draft, ffId, newcomerNmIds, pkg, boxesOf]);

    // ─── Остаток ФФ для проверки правок ──────────────────────────────────
    const stockByNm = useMemo(() => {
        const m = new Map<number, StockNeedArticle>();
        for (const a of stockNeed?.articles ?? []) m.set(a.nm_id, a);
        return m;
    }, [stockNeed]);
    const availOf = useCallback((nmId: number) => {
        if (ffId == null) return 0;
        return stockByNm.get(nmId)?.rf_stocks?.[ffId]?.available ?? 0;
    }, [stockByNm, ffId]);
    // Сколько (nm,ff) уже занято во всём черновике (rows + все handed_units).
    const usedByNm = useMemo(() => {
        const m = new Map<number, number>();
        if (!draft || ffId == null) return m;
        const sid = String(ffId);
        for (const r of draft.distribution.rows ?? []) {
            const q = r.src?.[sid] || 0;
            if (q) m.set(r.nm_id, (m.get(r.nm_id) || 0) + q);
        }
        for (const h of draft.distribution.handed_units ?? []) {
            if (h.source_ff_id !== ffId) continue;
            for (const it of h.items) m.set(it.nm_id, (m.get(it.nm_id) || 0) + it.qty);
        }
        return m;
    }, [draft, ffId]);
    const origQtyByNm = useMemo(() => {
        // Σ per nm: юнит может нести НЕСКОЛЬКО баркодов одного nm (размерные
        // варианты) — set() последним значением занижал бы исходный вклад.
        const m = new Map<number, number>();
        for (const it of unit?.items ?? []) m.set(it.nmId, (m.get(it.nmId) || 0) + it.qty);
        return m;
    }, [unit]);
    // Максимум для позиции = свободный остаток ФФ + текущий вклад этой заявки.
    // Пол по исходному кол-ву: уже набранное не урезаем принудительно (в т.ч.
    // когда остаток ФФ не загрузился), но превысить свободное нельзя.
    const maxForNm = useCallback((nmId: number) => {
        const used = usedByNm.get(nmId) || 0;
        const orig = origQtyByNm.get(nmId) || 0;
        return Math.max(orig, availOf(nmId) - used + orig);
    }, [usedByNm, origQtyByNm, availOf]);
    // Кратность: к ближайшему меньшему числу коробов, в пределах max.
    const normQty = useCallback((nmId: number, raw: number) => {
        const k = kOf(nmId);
        const max = maxForNm(nmId);
        let v = Math.max(0, Math.round(raw || 0));
        if (k > 0) v = Math.floor(v / k) * k;
        const cap = k > 0 ? Math.floor(max / k) * k : max;
        return Math.min(v, Math.max(0, cap));
    }, [kOf, maxForNm]);

    const editableNm = useMemo(() => new Set(editItems.map(e => e.nmId)), [editItems]);
    const addCandidates = useMemo(() => {
        if (!editMode) return [];
        const q = addQuery.trim().toLowerCase();
        const out: Array<{ nmId: number; vendor: string; barcode: string; free: number }> = [];
        for (const a of stockNeed?.articles ?? []) {
            if (editableNm.has(a.nm_id) || !a.barcode) continue;
            const k = kOf(a.nm_id);
            const free = availOf(a.nm_id) - (usedByNm.get(a.nm_id) || 0);
            const cap = k > 0 ? Math.floor(free / k) * k : free;
            if (cap <= 0) continue;
            if (q && !((a.vendor_code || '').toLowerCase().includes(q) || a.barcode.toLowerCase().includes(q))) continue;
            out.push({ nmId: a.nm_id, vendor: a.vendor_code || `nm:${a.nm_id}`, barcode: a.barcode, free: cap });
        }
        return out.sort((a, b) => b.free - a.free).slice(0, 30);
    }, [editMode, addQuery, stockNeed, editableNm, kOf, availOf, usedByNm]);

    const totalBoxes = items.reduce((s, it) => s + boxesOf(it.nmId, it.qty), 0);
    const withoutK = items.filter(it => kOf(it.nmId) <= 0).length;
    // Неполные короба: SKU с кратностью, где кол-во не делится на K (есть остаток).
    // Каждый такой артикул = ровно 1 неполный короб; looseUnits — штук в них россыпью.
    const partialItems = items.filter(it => { const k = kOf(it.nmId); return k > 0 && it.qty % k !== 0; });
    const partialCount = partialItems.length;
    const looseUnits = partialItems.reduce((s, it) => s + (it.qty % kOf(it.nmId)), 0);

    const unitRef: AssemblyDraftUnitRef = useMemo(() => ({
        source_ff_id: ffId ?? 0, target_wb_name: effectiveWb, package_type: pkg,
    }), [ffId, effectiveWb, pkg]);

    const backToFf = useCallback(() => {
        const qs = new URLSearchParams({ draft: String(draftId ?? ''), ff: String(ffId ?? ''), pkg });
        router.push(`/p/${slug}/warehouse/assembly/distribute/ff?${qs.toString()}`);
    }, [draftId, ffId, pkg, router, slug]);

    // ─── Edit handlers ───────────────────────────────────────────────────
    const enterEdit = useCallback(() => {
        setEditItems((unit?.items ?? []).map(it => ({ nmId: it.nmId, vendor: it.vendor, barcode: it.barcode, qty: it.qty })));
        setAddQuery('');
        setEditMode(true);
    }, [unit]);
    // Адресация позиции — по БАРКОДУ: он уникален в юните (бэк сливает по barcode),
    // а nmId дублируется у размерных вариантов — правка/удаление по nmId затирала
    // qty второго баркода того же nm (и 🗑 сносил оба).
    const setItemQty = useCallback((barcode: string, nmId: number, qty: number) => {
        setEditItems(prev => prev.map(e => e.barcode === barcode ? { ...e, qty: normQty(nmId, qty) } : e));
    }, [normQty]);
    const removeItem = useCallback((barcode: string) => {
        setEditItems(prev => prev.filter(e => e.barcode !== barcode));
    }, []);
    const addItem = useCallback((c: { nmId: number; vendor: string; barcode: string; free: number }) => {
        const k = kOf(c.nmId);
        const def = k > 0 ? Math.min(k, c.free) : Math.min(1, c.free);
        setEditItems(prev => [...prev, { nmId: c.nmId, vendor: c.vendor, barcode: c.barcode, qty: def }]);
        setAddQuery('');
    }, [kOf]);

    const saveEdit = useCallback(async () => {
        if (!draftId) return;
        const payload: HandedUnitItem[] = editItems
            .filter(e => e.qty > 0)
            .map(e => ({ nm_id: e.nmId, barcode: e.barcode, vendor_code: e.vendor, qty: normQty(e.nmId, e.qty) }))
            .filter(e => e.qty > 0);
        if (payload.length === 0) {
            setToast({ message: 'Заявка не может быть пустой — удалите её кнопкой «🗑 Удалить заявку».', type: 'error' });
            return;
        }
        setBusy(true);
        try {
            const d = await api.setDraftUnitItems(draftId, unitRef, payload);
            setDraft(d);
            setEditMode(false);
            setToast({ message: 'Наполнение сохранено (заявка зафиксирована)', type: 'success' });
        } catch (e: unknown) {
            setToast({ message: e instanceof Error ? e.message : 'Ошибка сохранения', type: 'error' });
        } finally {
            setBusy(false);
        }
    }, [draftId, editItems, normQty, unitRef]);

    const exportUnit = useCallback(() => {
        if (!unit) return;
        const data = items.map(it => {
            const k = kOf(it.nmId);
            return {
                vendor: it.vendor, barcode: it.barcode, wb: unit.wbName, qty: it.qty,
                boxes: k > 0 ? Math.ceil(it.qty / k) : '', box_qty: k > 0 ? k : '',
                pkg: PKG_LABEL_RU[unit.pkg] || unit.pkg, type: it.isNew ? 'Новинка' : 'Обычный',
            };
        });
        exportToExcel(data, `Заявка_${excelSheetName(ffName)}_${excelSheetName(unit.wbName)}_${new Date().toISOString().slice(0, 10)}`, PREVIEW_EXPORT_COLUMNS);
    }, [unit, items, kOf, ffName]);

    const handleDelete = useCallback(async () => {
        if (!draftId) return;
        if (!window.confirm(`Удалить заявку «${ffName} → ${effectiveWb}»? Товар останется на ФФ (не отгружается).`)) return;
        setBusy(true);
        try {
            const d = await api.deleteDraftUnit(draftId, unitRef);
            const empty = (d.distribution.rows?.length ?? 0) === 0 && (d.distribution.handed_units?.length ?? 0) === 0;
            setToast({ message: 'Заявка удалена', type: 'success' });
            setTimeout(() => {
                if (empty) router.push(`/p/${slug}/warehouse/assembly`);
                else backToFf();
            }, 600);
        } catch (e: unknown) {
            setToast({ message: e instanceof Error ? e.message : 'Ошибка', type: 'error' });
            setBusy(false);
        }
    }, [draftId, unitRef, ffName, effectiveWb, router, slug, backToFf]);

    const handleRevert = useCallback(async () => {
        if (!draftId) return;
        setBusy(true);
        try { setDraft(await api.revertDraftUnit(draftId, unitRef)); setToast({ message: 'Возвращён в авто-распределение', type: 'success' }); }
        catch (e: unknown) { setToast({ message: e instanceof Error ? e.message : 'Ошибка', type: 'error' }); }
        finally { setBusy(false); }
    }, [draftId, unitRef]);

    const handleCommit = useCallback(async () => {
        if (!draftId) return;
        setBusy(true);
        try {
            const resp = await api.commitDraftUnit(draftId, unitRef);
            const id = resp.created_request_ids[0];
            setToast({ message: `Заявка на сборку создана${id ? ` (#${id})` : ''}`, type: 'success' });
            setTimeout(() => { if (id) router.push(`/p/${slug}/warehouse/assembly/${id}`); else backToFf(); }, 700);
        } catch (e: unknown) {
            setToast({ message: e instanceof Error ? e.message : 'Ошибка', type: 'error' });
            setBusy(false);
        }
    }, [draftId, unitRef, router, slug, backToFf]);

    // ─── Проверка приёмки WB для переноса ────────────────────────────────
    // Флаг приёмки, соответствующий упаковке этого юнита.
    const pkgFlag: 'can_box' | 'can_monopallet' | 'can_supersafe' =
        pkg === 'MONOPALLET' ? 'can_monopallet' : pkg === 'SUPERSAFE' ? 'can_supersafe' : 'can_box';
    const accReady = moveAcc.size > 0;

    // Склад-получатель блокируется, если WB явно не принимает хотя бы один SKU
    // юнита этой упаковкой. blocked — число таких SKU; known — есть ли данные.
    const targetAcceptance = useCallback((wb: string): { ok: boolean; blocked: number; known: boolean } => {
        if (!unit) return { ok: false, blocked: 0, known: false };
        let blocked = 0;
        let known = false;
        // Блокируем только при ЯВНОМ отказе WB по этой упаковке (флаг есть и false).
        // Нет данных по складу → benefit of the doubt (как isClosed в WarehouseNeedView).
        for (const it of unit.items) {
            const flags = moveAcc.get(it.nmId)?.[wb];
            if (flags) { known = true; if (!flags[pkgFlag]) blocked++; }
        }
        return { ok: blocked === 0, blocked, known };
    }, [unit, moveAcc, pkgFlag]);

    const loadAcceptance = useCallback(async (force = false) => {
        if (!unit || unit.items.length === 0) return;
        setAccLoading(true);
        setAccError(null);
        try {
            const body: AcceptanceCheckRequest = {
                items: unit.items.map(it => ({ nm_id: it.nmId, barcode: it.barcode, distribution: { [effectiveWb]: it.qty } })),
            };
            const resp = await api.checkWbAcceptance(body, force);
            const m = new Map<number, Record<string, AcceptanceFlags>>();
            for (const it of resp.items) m.set(it.nm_id, it.availability);
            setMoveAcc(m);
        } catch (e: unknown) {
            setAccError(e instanceof Error ? e.message : 'Не удалось проверить приёмку WB');
        } finally {
            setAccLoading(false);
        }
    }, [unit, effectiveWb]);

    const openMove = useCallback(() => {
        const willOpen = !moveOpen;
        setMoveOpen(willOpen);
        if (willOpen && moveAcc.size === 0 && !accLoading) loadAcceptance();
    }, [moveOpen, moveAcc, accLoading, loadAcceptance]);

    const handleMoveToWb = useCallback(async (newWb: string) => {
        if (!draftId) return;
        const acc = targetAcceptance(newWb);
        if (!acc.ok) {
            setToast({
                message: `На «${newWb}» нельзя сдать ${acc.blocked} из ${unit?.items.length ?? 0} SKU упаковкой «${PKG_LABEL_RU[pkg] || pkg}» — перенос отменён`,
                type: 'error',
            });
            return;
        }
        if (!window.confirm(`Перенести заявку «${ffName} → ${effectiveWb}» на склад «${newWb}»? Поедет только товар этого ФФ; если на «${newWb}» уже есть заявка с этого ФФ — позиции сольются.`)) return;
        setMoveOpen(false);
        setBusy(true);
        try {
            const d = await api.moveDraftUnit(draftId, unitRef, newWb);
            // pendingWb выставляем СИНХРОННО вместе с setDraft: новые rows уже под
            // newWb, а searchParams (router.replace ниже) догонят лишь через рендер.
            // effectiveWb=newWb сразу → findDraftUnit находит юнит, empty-state не мелькает.
            setPendingWb(newWb);
            setDraft(d);
            setToast({ message: `Перенесено на «${newWb}»`, type: 'success' });
            const qs = new URLSearchParams({
                draft: String(draftId), ff: String(ffId ?? ''), wb: newWb, pkg,
            });
            router.replace(`/p/${slug}/warehouse/assembly/distribute/request?${qs.toString()}`);
        } catch (e: unknown) {
            // Перенос не состоялся — сбрасываем pendingWb, иначе lookup поедет на WB,
            // которого нет в (неизменённом) черновике.
            setPendingWb(null);
            setToast({ message: e instanceof Error ? e.message : 'Ошибка переноса', type: 'error' });
        } finally {
            setBusy(false);
        }
    }, [draftId, unitRef, ffName, effectiveWb, ffId, pkg, router, slug, targetAcceptance, unit]);

    if (loading) {
        return <div className="animate-in"><div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка…</div></div>;
    }
    if (error) {
        return (
            <div className="animate-in"><div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>
                {error}
                <div style={{ marginTop: 12 }}><button className="btn btn-secondary btn-sm" onClick={backToFf}>← К складу</button></div>
            </div></div>
        );
    }
    if (!unit) {
        return (
            <div className="animate-in"><div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                Заявка не найдена — возможно, уже создана (в сборке) или возвращена.
                <div style={{ marginTop: 12 }}><button className="btn btn-secondary btn-sm" onClick={backToFf}>← К складу</button></div>
            </div></div>
        );
    }

    const badge = STATUS_BADGE[unit.status];
    const editable = unit.status === 'draft';

    return (
        <div className="animate-in">
            {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}

            <div className="page-header" style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1 }}>
                    <button className="btn btn-secondary btn-sm" onClick={backToFf} disabled={busy}>← Назад</button>
                    <div>
                        <h1 className="page-title" style={{ margin: 0 }}>📦 {ffName} → {unit.wbName}</h1>
                        <p className="page-subtitle" style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                            <span style={{ padding: '2px 8px', borderRadius: 24, background: badge.bg, color: badge.color, fontWeight: 700, fontSize: 12 }}>{badge.label}</span>
                            {unit.frozen && unit.status === 'draft' && (
                                <span style={{ padding: '2px 8px', borderRadius: 24, background: 'rgba(14,165,233,0.16)', color: '#0369a1', fontWeight: 700, fontSize: 11 }}>ручной</span>
                            )}
                            <span>{PKG_LABEL_RU[unit.pkg] || unit.pkg}{unit.hasNewcomer ? ' · 🆕 есть новинки' : ''}</span>
                            <span>· Σ {formatNumber(unit.qty, 0)} шт · {formatNumber(totalBoxes, 0)} кор · {items.length} SKU</span>
                        </p>
                    </div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    {editMode ? (
                        <>
                            <button className="btn btn-danger" onClick={handleDelete} disabled={busy}>🗑 Удалить заявку</button>
                            <button className="btn btn-secondary" onClick={() => setEditMode(false)} disabled={busy}>Отмена</button>
                            <button
                                className="btn btn-primary"
                                onClick={saveEdit}
                                disabled={busy || !editItems.some(e => e.qty > 0)}
                                title={editItems.some(e => e.qty > 0) ? undefined : 'Нет позиций — удалите заявку или добавьте позицию'}
                            >
                                {busy ? 'Сохранение…' : '💾 Сохранить'}
                            </button>
                        </>
                    ) : (
                        <>
                            <button className="btn btn-secondary" onClick={exportUnit}>📥 Excel</button>
                            {editable && availableWbTargets.length > 0 && (
                                <div style={{ position: 'relative' }}>
                                    <button className="btn btn-secondary" onClick={openMove} disabled={busy} title="Перенести заявку этого ФФ на другой WB-склад">🔀 Сменить склад WB</button>
                                    {moveOpen && (
                                        <>
                                            <div onClick={() => setMoveOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 19 }} />
                                            <div style={{ position: 'absolute', top: '100%', right: 0, marginTop: 4, background: 'var(--color-bg-card)', border: '1px solid var(--color-border)', borderRadius: 12, boxShadow: 'var(--shadow-glass)', zIndex: 20, minWidth: 340, maxHeight: 420, overflowY: 'auto', padding: 8 }}>
                                                <div style={{ fontSize: 12, color: 'var(--color-text-muted)', padding: '4px 8px 10px' }}>
                                                    Перенести «{unit.wbName}» на склад{accLoading ? ' · проверка приёмки WB…' : ''}:
                                                </div>
                                                {accError && (
                                                    <div style={{ fontSize: 12, color: 'var(--color-danger)', padding: '0 8px 8px', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                                                        ⚠️ {accError}
                                                        <button className="btn btn-secondary btn-sm" onClick={() => loadAcceptance(true)} disabled={accLoading}>🔄 Повторить</button>
                                                    </div>
                                                )}
                                                {availableWbTargets.map(wb => {
                                                    const st = wbTargetStats.get(wb);
                                                    const acc = targetAcceptance(wb);
                                                    const blocked = accReady && !acc.ok;
                                                    const disabled = busy || accLoading || !accReady || blocked;
                                                    return (
                                                        <button
                                                            key={wb}
                                                            className="btn btn-secondary"
                                                            style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 3, width: '100%', textAlign: 'left', marginBottom: 6, padding: '8px 12px', height: 'auto', opacity: blocked ? 0.6 : 1 }}
                                                            onClick={() => handleMoveToWb(wb)}
                                                            disabled={disabled}
                                                            title={blocked ? `«${wb}» не принимает ${acc.blocked} из ${unit.items.length} SKU упаковкой «${PKG_LABEL_RU[pkg] || pkg}»` : undefined}
                                                        >
                                                            <span style={{ fontWeight: 600, fontSize: 14 }}>
                                                                {blocked ? '⛔' : acc.known ? '✅' : '→'} {wb}
                                                            </span>
                                                            <span style={{ fontSize: 12, color: blocked ? 'var(--color-danger)' : 'var(--color-text-muted)', fontWeight: 400 }}>
                                                                {blocked
                                                                    ? `не примут ${acc.blocked}/${unit.items.length} SKU (${PKG_LABEL_RU[pkg] || pkg})`
                                                                    : st
                                                                        ? `📦 ${formatNumber(st.boxes, 0)} кор · 🏷️ ${formatNumber(st.sku, 0)} SKU · ${formatNumber(st.qty, 0)} шт`
                                                                        : 'пусто — новая линия'}
                                                            </span>
                                                        </button>
                                                    );
                                                })}
                                            </div>
                                        </>
                                    )}
                                </div>
                            )}
                            {editable && <button className="btn btn-secondary" onClick={enterEdit} disabled={busy}>✏️ Редактировать</button>}
                            {unit.frozen && unit.status === 'draft' && (
                                <button className="btn btn-secondary" onClick={handleRevert} disabled={busy} title="Вернуть в авто-распределение (правки сбросятся)">↩ В авто</button>
                            )}
                            {unit.status === 'draft' ? (
                                <>
                                    <button className="btn btn-danger" onClick={handleDelete} disabled={busy} title="Удалить заявку целиком (товар останется на ФФ)">🗑 Удалить</button>
                                    <button className="btn btn-primary" onClick={handleCommit} disabled={busy} title="Создать заявку на сборку — появится в «Заявки на сборку»">{busy ? 'Создание…' : 'В сборку ✓'}</button>
                                </>
                            ) : (
                                <>
                                    <button className="btn btn-secondary" onClick={handleRevert} disabled={busy}>Вернуть в черновик</button>
                                    <button className="btn btn-primary" onClick={handleCommit} disabled={busy}>{busy ? 'Создание…' : 'В сборку ✓'}</button>
                                </>
                            )}
                        </>
                    )}
                </div>
            </div>

            {/* Сводка */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12, marginBottom: 16 }}>
                <KpiCard label="Коробок" value={Math.round(totalBoxes)} icon="📦" color="var(--color-accent)" sub={PKG_LABEL_RU[unit.pkg] || unit.pkg} />
                <KpiCard label="Штук" value={unit.qty} icon="🔢" color="var(--color-success)" sub={`→ ${unit.wbName}`} />
                <KpiCard label="Позиций (SKU)" value={items.length} icon="🏷️" color="var(--color-warning)" sub={unit.hasNewcomer ? '🆕 есть новинки' : 'обычные'} />
                <KpiCard
                    label="Неполных коробов"
                    value={partialCount}
                    icon="🟧"
                    color={partialCount > 0 ? 'var(--color-warning)' : 'var(--color-text-muted)'}
                    sub={partialCount > 0 ? `${partialCount} арт. · ${formatNumber(looseUnits, 0)} шт россыпью` : 'все короба полные'}
                />
                <KpiCard label="Без кратности" value={withoutK} icon="⚠️" color={withoutK > 0 ? 'var(--color-danger)' : 'var(--color-text-muted)'} sub={withoutK > 0 ? 'идут россыпью' : 'все коробами'} />
            </div>

            {editMode ? (
                <div className="glass-card" style={{ padding: 16 }}>
                    <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 12 }}>
                        Правка фиксирует заявку (вырезает из авто-распределения). Кол-во кратно коробу где задана кратность; ограничено свободным остатком ФФ.
                    </div>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                        <thead>
                            <tr style={{ background: '#f5f5f7' }}>
                                <th style={{ textAlign: 'left', padding: '8px 12px', fontSize: 11, fontWeight: 700 }}>Артикул</th>
                                <th style={{ textAlign: 'left', padding: '8px 12px', fontSize: 11, fontWeight: 700 }}>Баркод</th>
                                <th style={{ textAlign: 'center', padding: '8px 12px', fontSize: 11, fontWeight: 700, width: 200 }}>Количество</th>
                                <th style={{ textAlign: 'right', padding: '8px 12px', fontSize: 11, fontWeight: 700, width: 110 }}>Макс (ФФ)</th>
                                <th style={{ width: 44 }} />
                            </tr>
                        </thead>
                        <tbody>
                            {editItems.map(e => {
                                const k = kOf(e.nmId);
                                const step = k > 0 ? k : 1;
                                const max = maxForNm(e.nmId);
                                const over = e.qty > max;
                                return (
                                    <tr key={`${e.nmId}::${e.barcode}`} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                        <td style={{ padding: '6px 12px', fontWeight: 500 }}>{e.vendor || `nm:${e.nmId}`}</td>
                                        <td style={{ padding: '6px 12px', color: 'var(--color-text-muted)' }}>{e.barcode}</td>
                                        <td style={{ padding: '6px 12px' }}>
                                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
                                                <button className="btn btn-secondary btn-sm" style={{ width: 30, padding: '2px 0' }} onClick={() => setItemQty(e.barcode, e.nmId, e.qty - step)} disabled={e.qty <= 0}>−</button>
                                                <input
                                                    type="number" min={0} value={e.qty}
                                                    onChange={ev => setItemQty(e.barcode, e.nmId, Number(ev.target.value))}
                                                    style={{ width: 72, textAlign: 'center', padding: '4px 6px', borderRadius: 6, border: `1px solid ${over ? 'var(--color-danger)' : 'var(--color-border)'}` }}
                                                />
                                                <button className="btn btn-secondary btn-sm" style={{ width: 30, padding: '2px 0' }} onClick={() => setItemQty(e.barcode, e.nmId, e.qty + step)} disabled={e.qty >= max}>+</button>
                                                <span style={{ fontSize: 11, color: 'var(--color-text-muted)', minWidth: 54 }}>
                                                    {k > 0 ? `${formatNumber(e.qty / k, 0)} кор ×${k}` : 'россыпь'}
                                                </span>
                                            </div>
                                        </td>
                                        <td style={{ padding: '6px 12px', textAlign: 'right', color: over ? 'var(--color-danger)' : 'var(--color-text-muted)' }}>{formatNumber(max, 0)}</td>
                                        <td style={{ padding: '6px 12px', textAlign: 'center' }}>
                                            <button className="btn btn-secondary btn-sm" title="Удалить позицию" onClick={() => removeItem(e.barcode)}>🗑</button>
                                        </td>
                                    </tr>
                                );
                            })}
                            {editItems.length === 0 && (
                                <tr><td colSpan={5} style={{ padding: 16, textAlign: 'center', color: 'var(--color-text-muted)' }}>Нет позиций — добавьте ниже</td></tr>
                            )}
                        </tbody>
                    </table>

                    {/* Добавить позицию */}
                    <div style={{ marginTop: 16, borderTop: '1px solid var(--color-border)', paddingTop: 12 }}>
                        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Добавить позицию (доступные на «{ffName}»)</div>
                        <input
                            type="text" className="form-input" placeholder="🔍 Артикул или баркод…"
                            value={addQuery} onChange={e => setAddQuery(e.target.value)} style={{ maxWidth: 320, fontSize: 13, marginBottom: 8 }}
                        />
                        {addQuery.trim() !== '' && (
                            <div style={{ maxHeight: 240, overflowY: 'auto', border: '1px solid var(--color-border)', borderRadius: 10 }}>
                                {addCandidates.length === 0 ? (
                                    <div style={{ padding: 12, color: 'var(--color-text-muted)', fontSize: 12 }}>Нет доступных артикулов с остатком на этом ФФ</div>
                                ) : addCandidates.map(c => (
                                    <div key={c.nmId} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px', borderBottom: '1px solid var(--color-border)' }}>
                                        <div style={{ flex: 1, minWidth: 0 }}>
                                            <div style={{ fontSize: 13, fontWeight: 500 }}>{c.vendor}</div>
                                            <div style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>{c.barcode} · свободно {formatNumber(c.free, 0)} шт</div>
                                        </div>
                                        <button className="btn btn-primary btn-sm" onClick={() => addItem(c)}>+ Добавить</button>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </div>
            ) : (
                <div className="glass-card" style={{ padding: 0, overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                        <thead>
                            <tr style={{ background: '#f5f5f7' }}>
                                <th style={{ textAlign: 'left', padding: '10px 12px', fontSize: 11, fontWeight: 700, borderBottom: '2px solid var(--color-border)' }}>Артикул</th>
                                <th style={{ textAlign: 'left', padding: '10px 12px', fontSize: 11, fontWeight: 700, borderBottom: '2px solid var(--color-border)' }}>Баркод</th>
                                <th style={{ textAlign: 'right', padding: '10px 12px', fontSize: 11, fontWeight: 700, borderBottom: '2px solid var(--color-border)', width: 110 }}>Коробок</th>
                                <th style={{ textAlign: 'right', padding: '10px 12px', fontSize: 11, fontWeight: 700, borderBottom: '2px solid var(--color-border)', width: 90 }}>Шт</th>
                            </tr>
                        </thead>
                        <tbody>
                            {items.map(it => {
                                const k = kOf(it.nmId);
                                const full = k > 0 ? Math.floor(it.qty / k) : 0;
                                const rem = k > 0 ? it.qty % k : 0;
                                return (
                                    <tr key={it.nmId} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                        <td style={{ padding: '6px 12px', fontWeight: 500 }}>
                                            {it.isNew && <span title="Новинка" style={{ marginRight: 4, color: '#a855f7', fontWeight: 700 }}>🆕</span>}
                                            {it.vendor || `nm:${it.nmId}`}
                                        </td>
                                        <td style={{ padding: '6px 12px', color: 'var(--color-text-muted)' }}>{it.barcode}</td>
                                        <td
                                            style={{ padding: '6px 12px', textAlign: 'right', whiteSpace: 'nowrap' }}
                                            title={k > 0 ? `${full} полн. коробов по ${k} шт${rem > 0 ? ` + ${rem} шт россыпью (неполный короб)` : ''}` : 'Без кратности короба'}
                                        >
                                            {k <= 0 ? (
                                                <span style={{ color: 'var(--color-text-muted)' }}>—</span>
                                            ) : (
                                                <>
                                                    <span style={{ fontWeight: 600 }}>{formatNumber(full, 0)} кор</span>
                                                    {rem > 0 && (
                                                        <span style={{ color: 'var(--color-warning)', fontWeight: 600 }}> + {formatNumber(rem, 0)} шт</span>
                                                    )}
                                                </>
                                            )}
                                        </td>
                                        <td style={{ padding: '6px 12px', textAlign: 'right', fontWeight: 600, whiteSpace: 'nowrap' }}>{formatNumber(it.qty, 0)}</td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}
