'use client';
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { exportToExcel, formatNumber } from '@/lib/utils';
import { Toast, type Column } from '@/components';
import TanStackDataTable from '@/components/TanStackDataTable';
import KpiCard from '@/components/KpiCard';
import type { AssemblyDraft, PackageType, Warehouse } from '@/types/api';
import {
    buildPreviewLines,
    groupByFf,
    groupByWb,
    sumQty,
    reqCountOf,
    skuCountOf,
    excelSheetName,
    PKG_LABEL_RU,
    PREVIEW_EXPORT_COLUMNS,
    PREVIEW_EXPORT_COLUMNS_FF,
    type PreviewLine,
} from '@/lib/utils/assemblyPreview';
import { palletsForLines, maxPalletHeightCm, parseBoxSize, type PalletCount } from '@/lib/utils/boxPallet';

const toggleInSet = (s: Set<string>, value: string): Set<string> => {
    const n = new Set(s);
    if (n.has(value)) n.delete(value); else n.add(value);
    return n;
};

/** Строка-фильтр в виде чипсов (WB-цель / предмет / бренд) с Σ qty на каждом. */
function FilterChipRow({ label, options, selected, onToggle, titleFn }: {
    label: string;
    options: Array<{ value: string; qty: number }>;
    selected: Set<string>;
    onToggle: (value: string) => void;
    titleFn: (value: string) => string;
}) {
    if (options.length <= 1) return null;
    return (
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>{label}</span>
            {options.map(({ value, qty }) => (
                <button
                    key={value}
                    className={`btn btn-sm ${selected.has(value) ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={() => onToggle(value)}
                    title={titleFn(value)}
                >
                    {value} <span style={{ opacity: 0.6 }}>· {formatNumber(qty, 0)}</span>
                </button>
            ))}
        </div>
    );
}

export default function AssemblyPreviewPage() {
    const params = useParams();
    const router = useRouter();
    const searchParams = useSearchParams();
    const slug = params.slug as string;

    const draftId = Number(searchParams.get('draft')) || null;
    const pkgTab: PackageType = searchParams.get('pkg') === 'MONOPALLET' ? 'MONOPALLET' : 'BOX';

    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [committing, setCommitting] = useState(false);
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' } | null>(null);
    const [draft, setDraft] = useState<AssemblyDraft | null>(null);
    const [name, setName] = useState('');
    const [editingName, setEditingName] = useState(false);
    const [savingName, setSavingName] = useState(false);
    const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
    const [expanded, setExpanded] = useState<Set<number>>(new Set());
    const [query, setQuery] = useState('');
    const [selectedWbs, setSelectedWbs] = useState<Set<string>>(new Set());
    const [selectedSubjects, setSelectedSubjects] = useState<Set<string>>(new Set());
    const [selectedBrands, setSelectedBrands] = useState<Set<string>>(new Set());
    const [nmPpb, setNmPpb] = useState<Map<number, number | null>>(new Map());
    const [nmMeta, setNmMeta] = useState<Map<number, { subject: string; brand: string }>>(new Map());
    const [nmBoxSize, setNmBoxSize] = useState<Map<number, string | null>>(new Map());
    const [palletOverrides, setPalletOverrides] = useState<Record<string, number>>({});
    const [viewMode, setViewMode] = useState<'cards' | 'table' | 'matrix'>('cards');
    const [matrixUnit, setMatrixUnit] = useState<'qty' | 'boxes' | 'pallets'>('qty');

    const backToDistribute = useCallback(() => {
        if (draftId) router.push(`/p/${slug}/warehouse/assembly/distribute?draft=${draftId}`);
        else router.push(`/p/${slug}/warehouse/assembly`);
    }, [draftId, router, slug]);

    // «← Назад» — туда, откуда пришли (список / распределение), не насильно в редактор.
    // Fallback на список заявок, если истории нет (прямой заход / refresh).
    const goBack = useCallback(() => {
        if (typeof window !== 'undefined' && window.history.length > 1) router.back();
        else router.push(`/p/${slug}/warehouse/assembly`);
    }, [router, slug]);

    const openFf = useCallback((ffId: number) => {
        const qs = new URLSearchParams({ draft: String(draftId ?? ''), ff: String(ffId), pkg: pkgTab });
        router.push(`/p/${slug}/warehouse/assembly/distribute/ff?${qs.toString()}`);
    }, [draftId, pkgTab, router, slug]);

    // Переименование черновика — сохраняем только name (distribution не трогаем).
    // PUT возвращает обогащённый draft (newcomer_nm_ids), поэтому setDraft безопасен.
    // Единый путь сохранения — onBlur (Enter/клик-мимо). Escape ставит skip, чтобы
    // blur, который последует за размонтированием инпута, не сохранил отменённое.
    const skipSaveRef = useRef(false);
    const saveDraftName = useCallback(async () => {
        setEditingName(false);
        if (skipSaveRef.current) { skipSaveRef.current = false; setName(draft?.name ?? ''); return; }
        if (!draftId || !draft) return;
        const trimmed = name.trim();
        if (trimmed === '' || trimmed === draft.name) { setName(draft.name); return; }
        setSavingName(true);
        try {
            const updated = await api.updateAssemblyDraft(draftId, { name: trimmed });
            setDraft(updated);
            setName(updated.name);
            setToast({ message: 'Название сохранено', type: 'success' });
        } catch (e: unknown) {
            setName(draft.name);
            setToast({ message: e instanceof Error ? e.message : 'Не удалось переименовать', type: 'error' });
        } finally {
            setSavingName(false);
        }
    }, [draftId, draft, name]);

    // ─── Load draft + warehouses ─────────────────────────────────────────
    useEffect(() => {
        if (!draftId) { setError('Не указан ID черновика'); setLoading(false); return; }
        let cancelled = false;
        (async () => {
            setLoading(true);
            setError(null);
            try {
                const [d, whs] = await Promise.all([api.getAssemblyDraft(draftId), api.getWarehouses()]);
                if (cancelled) return;
                setDraft(d);
                setName(d.name);
                setWarehouses(whs);
            } catch (e: unknown) {
                if (!cancelled) setError(e instanceof Error ? e.message : 'Ошибка загрузки черновика');
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [draftId]);

    // Кратность короба per nm — для подсчёта коробок (= шт / K). Та же логика,
    // что на distribute: override > минимальный per-warehouse box_qty.
    useEffect(() => {
        let cancelled = false;
        api.getBoxMultiplicity()
            .then(resp => {
                if (cancelled) return;
                const m = new Map<number, number | null>();
                const meta = new Map<number, { subject: string; brand: string }>();
                const sizes = new Map<number, string | null>();
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
                    // Размер коробки SKU — box_size ФФ с наибольшим стоком, где он задан
                    // и парсится (зеркало resolveSkuBoxSize в WarehouseNeedView).
                    let boxSize: string | null = null;
                    let bestStock = -1;
                    for (const p of r.per_warehouse) {
                        if (!p.box_size || !parseBoxSize(p.box_size)) continue;
                        if (p.rf_stock > bestStock) { boxSize = p.box_size; bestStock = p.rf_stock; }
                    }
                    m.set(r.nm_id, ppb);
                    meta.set(r.nm_id, { subject: r.subject || '', brand: r.brand || '' });
                    sizes.set(r.nm_id, boxSize);
                }
                setNmPpb(m);
                setNmMeta(meta);
                setNmBoxSize(sizes);
            })
            .catch(() => { /* best-effort: без кратности покажем только штуки */ });
        return () => { cancelled = true; };
    }, []);

    // Ручной override «коробок на паллету» по размеру (best-effort) — перебивает
    // геометрию в palletsForLines. Без него считаем чисто по габаритам.
    useEffect(() => {
        let cancelled = false;
        api.getPalletBoxesBySize()
            .then(ov => { if (!cancelled) setPalletOverrides(ov || {}); })
            .catch(() => { /* best-effort */ });
        return () => { cancelled = true; };
    }, []);

    const warehouseNameById = useCallback(
        (id: number) => warehouses.find(w => w.id === id)?.name ?? `Склад ${id}`,
        [warehouses],
    );
    const newcomerNmIds = useMemo(() => new Set(draft?.newcomer_nm_ids ?? []), [draft]);

    // Срез, который коммитим: упаковка (короб/моно). Новинки и обычные не разделяем.
    const committableRows = useMemo(() => {
        const rows = draft?.distribution.rows ?? [];
        return rows.filter(r => (pkgTab === 'MONOPALLET' ? r.package_type === 'MONOPALLET' : r.package_type !== 'MONOPALLET'));
    }, [draft, pkgTab]);

    const allLines = useMemo(() => buildPreviewLines(committableRows, newcomerNmIds), [committableRows, newcomerNmIds]);

    // Опции фильтров (Σ qty по всему срезу, по убыванию объёма) — общий билдер.
    // WB-цель / предмет / бренд считаются по allLines (как и WB-фильтр) и
    // применяются независимыми AND-ами только к отображению/выгрузке.
    const buildOptions = useCallback((keyOf: (l: PreviewLine) => string) => {
        const m = new Map<string, number>();
        for (const l of allLines) {
            const k = keyOf(l);
            if (k) m.set(k, (m.get(k) || 0) + l.qty);
        }
        return [...m.entries()].map(([value, qty]) => ({ value, qty })).sort((a, b) => b.qty - a.qty);
    }, [allLines]);
    const wbOptions = useMemo(() => buildOptions(l => l.wbName), [buildOptions]);
    const subjectOptions = useMemo(() => buildOptions(l => nmMeta.get(l.nmId)?.subject || ''), [buildOptions, nmMeta]);
    const brandOptions = useMemo(() => buildOptions(l => nmMeta.get(l.nmId)?.brand || ''), [buildOptions, nmMeta]);

    // Поиск (артикул/баркод) + фильтр по WB-цели — ТОЛЬКО отображение и
    // выгрузка. Сам commit создаёт весь срез (упаковка × тип товара).
    const lines = useMemo(() => {
        const q = query.trim().toLowerCase();
        return allLines.filter(l => {
            if (selectedWbs.size > 0 && !selectedWbs.has(l.wbName)) return false;
            if (selectedSubjects.size > 0 && !selectedSubjects.has(nmMeta.get(l.nmId)?.subject || '')) return false;
            if (selectedBrands.size > 0 && !selectedBrands.has(nmMeta.get(l.nmId)?.brand || '')) return false;
            if (q && !(l.vendor.toLowerCase().includes(q) || l.barcode.toLowerCase().includes(q))) return false;
            return true;
        });
    }, [allLines, query, selectedWbs, selectedSubjects, selectedBrands, nmMeta]);
    const ffGroups = useMemo(() => groupByFf(lines), [lines]);

    // Сколько заявок создаст commit = уникальные (ФФ, WB, упаковка). Новинки и
    // обычные на один склад — одна заявка; withNewcomer — сколько из них с 🆕.
    const breakdown = useMemo(() => {
        const groups = new Set<string>();
        const withNewcomer = new Set<string>();
        for (const l of allLines) {
            const key = `${l.ffId}::${l.wbName}::${l.pkg}`;
            groups.add(key);
            if (l.isNew) withNewcomer.add(key);
        }
        return { total: groups.size, withNewcomer: withNewcomer.size };
    }, [allLines]);
    const totalAssemblies = breakdown.total;
    const isFiltered = query.trim() !== '' || selectedWbs.size > 0 || selectedSubjects.size > 0 || selectedBrands.size > 0;
    const resetFilters = useCallback(() => {
        setQuery('');
        setSelectedWbs(new Set());
        setSelectedSubjects(new Set());
        setSelectedBrands(new Set());
    }, []);
    // Сводка по всему срезу (allLines) — как в шапке.
    const distinctSku = useMemo(() => new Set(allLines.map(l => l.nmId)).size, [allLines]);
    const partialBoxes = useMemo(
        () => allLines.filter(l => { const k = nmPpb.get(l.nmId); return !!k && k > 0 && l.qty % k !== 0; }).length,
        [allLines, nmPpb],
    );
    const looseUnits = useMemo(
        () => allLines.reduce((s, l) => { const k = nmPpb.get(l.nmId); return s + (k && k > 0 && l.qty % k !== 0 ? l.qty % k : 0); }, 0),
        [allLines, nmPpb],
    );

    // Коробки = шт / K (распределение box-кратное, поэтому делится нацело).
    // Коробок = ⌈шт / K⌉: неполный короб считается коробом (товар всё равно кладётся в короб).
    const boxesOf = useCallback((l: PreviewLine) => {
        const k = nmPpb.get(l.nmId);
        return k && k > 0 ? Math.ceil(l.qty / k) : 0;
    }, [nmPpb]);
    const boxesSum = useCallback((ls: PreviewLine[]) => ls.reduce((s, l) => s + boxesOf(l), 0), [boxesOf]);

    // ─── Паллеты (геометрия box_size) ─────────────────────────────────────
    // Паллеты для набора линий ОДНОГО склада-цели: короб = смешанные паллеты
    // (Σ долей объёма по SKU), моно/сейф = по SKU. Высота — лимит склада-цели.
    const palletsForCell = useCallback((ls: PreviewLine[], wbName: string): PalletCount => {
        const byPkg = new Map<PackageType, PreviewLine[]>();
        for (const l of ls) {
            const arr = byPkg.get(l.pkg);
            if (arr) arr.push(l); else byPkg.set(l.pkg, [l]);
        }
        const height = maxPalletHeightCm(wbName);
        let pallets = 0, fill = 0, unknownLines = 0, unknownUnits = 0;
        for (const [pkg, lns] of byPkg) {
            const r = palletsForLines(
                lns.map(l => ({ units: l.qty, boxQty: nmPpb.get(l.nmId), boxSize: nmBoxSize.get(l.nmId) ?? null })),
                height,
                pkg === 'BOX' ? 'box' : 'mono',
                palletOverrides,
            );
            pallets += r.pallets; fill += r.fill; unknownLines += r.unknownLines; unknownUnits += r.unknownUnits;
        }
        return { pallets, fill, unknownLines, unknownUnits };
    }, [nmPpb, nmBoxSize, palletOverrides]);

    // Паллеты склада-источника = Σ по его складам-целям (каждый — отдельная поставка,
    // короба разных целей не кладутся на одну паллету).
    const palletsForFf = useCallback((ls: PreviewLine[]): PalletCount => {
        let pallets = 0, fill = 0, unknownLines = 0, unknownUnits = 0;
        for (const { wb, items } of groupByWb(ls)) {
            const r = palletsForCell(items, wb);
            pallets += r.pallets; fill += r.fill; unknownLines += r.unknownLines; unknownUnits += r.unknownUnits;
        }
        return { pallets, fill, unknownLines, unknownUnits };
    }, [palletsForCell]);

    // Итог по всему срезу (Σ паллет по всем (источник × цель)).
    const palletTotals = useMemo(() => {
        let pallets = 0, unknownUnits = 0;
        for (const g of groupByFf(allLines)) {
            const r = palletsForFf(g.lines);
            pallets += r.pallets; unknownUnits += r.unknownUnits;
        }
        return { pallets, unknownUnits };
    }, [allLines, palletsForFf]);

    // Map для commit: "{ffId}::{wbName}::{pkg}" → паллет (мин. 1 на заявку с товаром).
    // Считается по ВСЕМУ срезу (commit создаёт срез целиком, не по фильтру дисплея).
    const palletCounts = useMemo(() => {
        const groups = new Map<string, PreviewLine[]>();
        for (const l of allLines) {
            const k = `${l.ffId}::${l.wbName}::${l.pkg}`;
            const arr = groups.get(k);
            if (arr) arr.push(l); else groups.set(k, [l]);
        }
        const out: Record<string, number> = {};
        for (const [k, ls] of groups) {
            const r = palletsForLines(
                ls.map(l => ({ units: l.qty, boxQty: nmPpb.get(l.nmId), boxSize: nmBoxSize.get(l.nmId) ?? null })),
                maxPalletHeightCm(ls[0].wbName),
                ls[0].pkg === 'BOX' ? 'box' : 'mono',
                palletOverrides,
            );
            out[k] = Math.max(1, r.pallets);
        }
        return out;
    }, [allLines, nmPpb, nmBoxSize, palletOverrides]);

    // Бейдж паллет для отображения: средняя заполненность + флаг недозагрузки
    // (последняя/единственная паллета почти пустая — как Самара с 2 коробками).
    const palletBadge = useCallback((pc: PalletCount) => {
        const avg = pc.pallets > 0 ? pc.fill / pc.pallets : 0;
        return {
            pallets: pc.pallets,
            pct: Math.round(avg * 100),
            underfilled: pc.pallets > 0 && avg < 0.6,
            unknownUnits: pc.unknownUnits,
        };
    }, []);

    const scopeLabel = pkgTab === 'MONOPALLET' ? 'Моно' : 'Короб';

    // ─── Excel export ────────────────────────────────────────────────────
    const today = new Date().toISOString().slice(0, 10);
    const toRow = useCallback((l: PreviewLine, withFf = false): Record<string, string | number> => {
        const k = nmPpb.get(l.nmId) || 0;
        return {
            ...(withFf ? { ff: warehouseNameById(l.ffId) } : {}),
            vendor: l.vendor,
            barcode: l.barcode,
            wb: l.wbName,
            qty: l.qty,
            boxes: k > 0 ? Math.ceil(l.qty / k) : '',
            box_qty: k > 0 ? k : '',
            pkg: PKG_LABEL_RU[l.pkg] || l.pkg,
            type: l.isNew ? 'Новинка' : 'Обычный',
        };
    }, [warehouseNameById, nmPpb]);
    const sortForExport = (ls: PreviewLine[]) =>
        ls.slice().sort((a, b) => a.wbName.localeCompare(b.wbName) || a.vendor.localeCompare(b.vendor));

    const exportFf = useCallback((ffId: number, ls: PreviewLine[]) => {
        exportToExcel(
            sortForExport(ls).map(l => toRow(l)),
            `Сборка_${excelSheetName(warehouseNameById(ffId))}_${today}`,
            PREVIEW_EXPORT_COLUMNS,
        );
    }, [toRow, warehouseNameById, today]);

    const exportAll = useCallback(() => {
        if (!ffGroups.length) return;
        const used = new Set<string>();
        const uniqSheet = (name: string) => {
            let n = excelSheetName(name);
            let i = 2;
            while (used.has(n)) { n = `${excelSheetName(name).slice(0, 27)}_${i++}`; }
            used.add(n);
            return n;
        };
        const extra = ffGroups.map(g => ({
            sheetName: uniqSheet(warehouseNameById(g.ffId)),
            data: sortForExport(g.lines).map(l => toRow(l)),
            columns: PREVIEW_EXPORT_COLUMNS,
        }));
        exportToExcel(sortForExport(lines).map(l => toRow(l, true)), `Сборка_все_склады_${today}`, PREVIEW_EXPORT_COLUMNS_FF, extra);
    }, [ffGroups, lines, toRow, warehouseNameById, today]);

    // Выгрузка КАЖДОГО склада-источника отдельным файлом (в один клик).
    // Стаггерим во времени — иначе браузер блокирует пакет загрузок.
    const exportSeparate = useCallback(() => {
        ffGroups.forEach((g, i) => {
            setTimeout(() => exportFf(g.ffId, g.lines), i * 400);
        });
    }, [ffGroups, exportFf]);

    // ─── Табличный вид (плоский, сортируемый, со своим Excel) ────────────
    const tableColumns: Column[] = useMemo(() => [
        { key: 'ff', label: 'Склад-источник' },
        { key: 'wb', label: 'WB-цель' },
        {
            key: 'vendor', label: 'Артикул',
            render: (v: string, row: { isNew?: boolean }) => (
                <>{row.isNew && <span style={{ marginRight: 4, color: '#a855f7', fontWeight: 700 }}>🆕</span>}{v}</>
            ),
        },
        { key: 'barcode', label: 'Баркод' },
        { key: 'boxes', label: 'Коробок', align: 'right', format: 'number' },
        { key: 'qty', label: 'Шт', align: 'right', format: 'number' },
        { key: 'type', label: 'Тип' },
    ], []);
    const tableData = useMemo(() => lines.map(l => {
        const k = nmPpb.get(l.nmId);
        return {
            ff: warehouseNameById(l.ffId),
            wb: l.wbName,
            vendor: l.vendor,
            barcode: l.barcode,
            boxes: k && k > 0 ? Math.ceil(l.qty / k) : null,
            qty: l.qty,
            type: l.isNew ? 'Новинка' : 'Обычный',
            isNew: l.isNew,
        };
    }), [lines, nmPpb, warehouseNameById]);

    // ─── Матричный вид: склад-источник (строки) × WB-цель (столбцы) ──────
    const matrixWbCols = useMemo(() => {
        const m = new Map<string, number>();
        for (const l of lines) m.set(l.wbName, (m.get(l.wbName) || 0) + l.qty);
        return [...m.entries()].sort((a, b) => b[1] - a[1]).map(([wb]) => wb);
    }, [lines]);
    const matrixCells = useMemo(() => {
        const m = new Map<string, { qty: number; boxes: number }>();
        for (const l of lines) {
            const key = `${l.ffId}::${l.wbName}`;
            const cur = m.get(key) || { qty: 0, boxes: 0 };
            cur.qty += l.qty;
            cur.boxes += boxesOf(l);
            m.set(key, cur);
        }
        return m;
    }, [lines, boxesOf]);
    // Паллеты по ячейкам (источник × цель) + итоги по столбцам/всего. Считаем по
    // линиям одной ячейки (смешанные паллеты короба), а не суммой долей разных целей.
    const matrixPallets = useMemo(() => {
        const cells = new Map<string, number>();
        const colTotals = new Map<string, number>();
        let grand = 0;
        const groups = new Map<string, { wb: string; items: PreviewLine[] }>();
        for (const l of lines) {
            const key = `${l.ffId}::${l.wbName}`;
            const e = groups.get(key);
            if (e) e.items.push(l); else groups.set(key, { wb: l.wbName, items: [l] });
        }
        for (const [key, { wb, items }] of groups) {
            const p = palletsForCell(items, wb).pallets;
            cells.set(key, p);
            colTotals.set(wb, (colTotals.get(wb) || 0) + p);
            grand += p;
        }
        return { cells, colTotals, grand };
    }, [lines, palletsForCell]);
    const matrixColTotal = useCallback((wb: string) => {
        if (matrixUnit === 'pallets') return matrixPallets.colTotals.get(wb) || 0;
        return lines.reduce((s, l) => l.wbName === wb ? s + (matrixUnit === 'boxes' ? boxesOf(l) : l.qty) : s, 0);
    }, [lines, matrixUnit, boxesOf, matrixPallets]);

    // ─── Commit ──────────────────────────────────────────────────────────
    const handleCreate = useCallback(async () => {
        if (!draftId) return;
        setCommitting(true);
        try {
            const resp = await api.commitAssemblyDraft(draftId, pkgTab, palletCounts);
            const ids = resp.created_request_ids || [];
            // Остался ли черновик (другой срез) — решаем, куда уйти.
            let leftoverRows = 0;
            try {
                const fresh = await api.getAssemblyDraft(draftId);
                leftoverRows = fresh.distribution.rows?.length ?? 0;
            } catch { leftoverRows = 0; }
            setToast({
                message: `Создано заявок: ${ids.length}${leftoverRows > 0 ? `. Осталось строк: ${leftoverRows}` : ''}`,
                type: 'success',
            });
            setTimeout(() => {
                if (leftoverRows > 0) {
                    router.push(`/p/${slug}/warehouse/assembly/distribute?draft=${draftId}`);
                } else if (ids.length === 1) {
                    router.push(`/p/${slug}/warehouse/assembly/${ids[0]}`);
                } else if (ids.length > 1) {
                    router.push(`/p/${slug}/warehouse/assembly?just_created=${ids.join(',')}`);
                } else {
                    router.push(`/p/${slug}/warehouse/assembly`);
                }
            }, 700);
        } catch (e: unknown) {
            setToast({ message: e instanceof Error ? e.message : 'Ошибка создания сборок', type: 'error' });
            setCommitting(false);
        }
    }, [draftId, pkgTab, palletCounts, router, slug]);

    // ─── Render ──────────────────────────────────────────────────────────
    if (loading) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Загрузка предпросмотра…</div>
            </div>
        );
    }
    if (error) {
        return (
            <div className="animate-in">
                <div className="glass-card" style={{ padding: 32, color: 'var(--color-danger)' }}>
                    {error}
                    <div style={{ marginTop: 12 }}>
                        <button className="btn btn-secondary btn-sm" onClick={backToDistribute}>← К распределению</button>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="animate-in">
            {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}

            {/* Header */}
            <div className="page-header" style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1 }}>
                    <button className="btn btn-secondary btn-sm" onClick={goBack}>← Назад</button>
                    <div style={{ flex: 1 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                            <h1 className="page-title" style={{ margin: 0 }}>Предпросмотр заявок</h1>
                            {editingName ? (
                                <input
                                    className="form-input"
                                    autoFocus
                                    value={name}
                                    onChange={e => setName(e.target.value)}
                                    onBlur={saveDraftName}
                                    onKeyDown={e => {
                                        if (e.key === 'Enter') e.currentTarget.blur();
                                        if (e.key === 'Escape') { skipSaveRef.current = true; e.currentTarget.blur(); }
                                    }}
                                    style={{ maxWidth: 320, fontSize: 14 }}
                                />
                            ) : (
                                <button
                                    className="btn btn-secondary btn-sm"
                                    onClick={() => setEditingName(true)}
                                    disabled={savingName}
                                    title="Изменить название черновика"
                                >
                                    ✏️ {savingName ? 'Сохранение…' : (name || 'Без названия')}
                                </button>
                            )}
                        </div>
                        <p className="page-subtitle" style={{ margin: 0 }}>Срез: <strong>{scopeLabel}</strong></p>
                    </div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary" onClick={exportAll} disabled={ffGroups.length === 0}>
                        {isFiltered ? '📥 Выгрузить показанное' : '📥 Выгрузить всё (1 файл)'}
                    </button>
                    <button
                        className="btn btn-secondary"
                        onClick={exportSeparate}
                        disabled={ffGroups.length === 0}
                        title="Скачать отдельный Excel-файл на каждый склад-источник"
                    >
                        📥 Отдельно по складам
                    </button>
                    <button
                        className="btn btn-primary"
                        onClick={handleCreate}
                        disabled={committing || totalAssemblies === 0}
                        title="Создаёт весь срез (упаковка × тип товара). Поиск и фильтр по складам влияют только на отображение и выгрузку."
                    >
                        {committing ? 'Создание…' : `✓ Создать ${totalAssemblies} заявок`}
                    </button>
                </div>
            </div>

            {/* Сводка по срезу */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12, marginBottom: 12 }}>
                <KpiCard label="Заявок" value={totalAssemblies} icon="📋" color="var(--color-accent)" sub={breakdown.withNewcomer > 0 ? `🆕 с новинками: ${breakdown.withNewcomer}` : 'все обычные'} />
                <KpiCard label="Штук" value={sumQty(allLines)} icon="🔢" color="var(--color-success)" />
                <KpiCard label="Коробок" value={boxesSum(allLines)} icon="📦" color="var(--color-accent)" />
                <KpiCard
                    label="Паллет"
                    value={palletTotals.pallets}
                    icon="🟫"
                    color="var(--color-accent)"
                    sub={palletTotals.unknownUnits > 0 ? `${formatNumber(palletTotals.unknownUnits, 0)} шт без габаритов` : 'по габаритам коробки'}
                />
                <KpiCard label="SKU" value={distinctSku} icon="🏷️" color="var(--color-warning)" sub="уникальных" />
                <KpiCard
                    label="Неполных коробов"
                    value={partialBoxes}
                    icon="🟧"
                    color={partialBoxes > 0 ? 'var(--color-warning)' : 'var(--color-text-muted)'}
                    sub={partialBoxes > 0 ? `${formatNumber(looseUnits, 0)} шт россыпью` : 'все короба полные'}
                />
            </div>

            {/* Поиск + фильтр по складам-целям (только отображение/выгрузка) */}
            <div className="glass-card" style={{ padding: 12, marginBottom: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>Вид:</span>
                    {([['cards', '🗂 Карточки'], ['table', '📋 Таблица'], ['matrix', '🔲 Матрица']] as const).map(([m, label]) => (
                        <button key={m} className={`btn btn-sm ${viewMode === m ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setViewMode(m)}>
                            {label}
                        </button>
                    ))}
                    {viewMode === 'matrix' && (
                        <>
                            <div style={{ width: 1, alignSelf: 'stretch', background: 'var(--color-border)', margin: '0 4px' }} />
                            <button className={`btn btn-sm ${matrixUnit === 'qty' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setMatrixUnit('qty')}>Штуки</button>
                            <button className={`btn btn-sm ${matrixUnit === 'boxes' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setMatrixUnit('boxes')}>Коробки</button>
                            <button className={`btn btn-sm ${matrixUnit === 'pallets' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setMatrixUnit('pallets')}>Паллеты</button>
                        </>
                    )}
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <input
                        type="text"
                        className="form-input"
                        placeholder="🔍 Артикул или баркод…"
                        value={query}
                        onChange={e => setQuery(e.target.value)}
                        style={{ maxWidth: 280, fontSize: 13 }}
                    />
                    {isFiltered && (
                        <>
                            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                Показано: <strong>{reqCountOf(lines)}</strong> заявок · Σ <strong>{formatNumber(sumQty(lines), 0)}</strong> из {totalAssemblies}
                            </span>
                            <button className="btn btn-secondary btn-sm" onClick={resetFilters}>
                                ✕ Сбросить
                            </button>
                        </>
                    )}
                </div>
                <FilterChipRow
                    label="Склад-цель:"
                    options={wbOptions}
                    selected={selectedWbs}
                    onToggle={v => setSelectedWbs(prev => toggleInSet(prev, v))}
                    titleFn={v => `Показать только заявки на «${v}»`}
                />
                <FilterChipRow
                    label="Предмет:"
                    options={subjectOptions}
                    selected={selectedSubjects}
                    onToggle={v => setSelectedSubjects(prev => toggleInSet(prev, v))}
                    titleFn={v => `Показать только предмет «${v}»`}
                />
                <FilterChipRow
                    label="Бренд:"
                    options={brandOptions}
                    selected={selectedBrands}
                    onToggle={v => setSelectedBrands(prev => toggleInSet(prev, v))}
                    titleFn={v => `Показать только бренд «${v}»`}
                />
            </div>

            {/* Body: группы по складам-источникам */}
            {ffGroups.length === 0 ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    {isFiltered ? (
                        <>
                            Ничего не найдено по фильтру.
                            <button className="btn btn-secondary btn-sm" style={{ marginLeft: 8 }} onClick={resetFilters}>✕ Сбросить</button>
                        </>
                    ) : (
                        <>
                            Нет позиций в этом срезе.
                            <button className="btn btn-secondary btn-sm" style={{ marginLeft: 8 }} onClick={backToDistribute}>← К распределению</button>
                        </>
                    )}
                </div>
            ) : viewMode === 'table' ? (
                <TanStackDataTable
                    columns={tableColumns}
                    data={tableData}
                    exportName={`Сборка_заявки_${draftId ?? ''}`}
                    enablePagination
                    pageSize={100}
                />
            ) : viewMode === 'matrix' ? (
                <div className="glass-card" style={{ overflowX: 'auto', padding: 0 }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                        <thead>
                            <tr>
                                <th style={{ position: 'sticky', left: 0, zIndex: 2, background: '#f5f5f7', textAlign: 'left', padding: '8px 10px', borderBottom: '2px solid var(--color-border)', whiteSpace: 'nowrap' }}>
                                    Источник \ WB-цель ({matrixUnit === 'pallets' ? 'пал' : matrixUnit === 'boxes' ? 'кор' : 'шт'})
                                </th>
                                {matrixWbCols.map(wb => (
                                    <th key={wb} style={{ padding: '8px 10px', textAlign: 'right', borderBottom: '2px solid var(--color-border)', background: '#f5f5f7', whiteSpace: 'nowrap' }} title={wb}>
                                        <div>{wb.length > 14 ? wb.slice(0, 14) + '…' : wb}</div>
                                        <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--color-warning)' }}>{formatNumber(matrixColTotal(wb), 0)}</div>
                                    </th>
                                ))}
                                <th style={{ padding: '8px 10px', textAlign: 'right', borderBottom: '2px solid var(--color-border)', background: 'rgba(59,130,246,0.08)' }}>Σ</th>
                            </tr>
                        </thead>
                        <tbody>
                            {ffGroups.map(g => {
                                const isOpen = expanded.has(g.ffId);
                                const rowTotal = matrixUnit === 'pallets'
                                    ? palletsForFf(g.lines).pallets
                                    : matrixUnit === 'boxes' ? boxesSum(g.lines) : sumQty(g.lines);
                                // Второй уровень — товары склада, разбитые по тем же WB-колонкам.
                                const skuRows = isOpen ? (() => {
                                    const byNm = new Map<number, { nmId: number; vendor: string; isNew: boolean; cells: Map<string, number>; total: number }>();
                                    for (const l of g.lines) {
                                        let e = byNm.get(l.nmId);
                                        if (!e) { e = { nmId: l.nmId, vendor: l.vendor, isNew: l.isNew, cells: new Map(), total: 0 }; byNm.set(l.nmId, e); }
                                        const v = matrixUnit === 'boxes' ? boxesOf(l) : l.qty;
                                        e.cells.set(l.wbName, (e.cells.get(l.wbName) || 0) + v);
                                        e.total += v;
                                    }
                                    return [...byNm.values()].sort((a, b) => b.total - a.total);
                                })() : [];
                                return (
                                    <Fragment key={g.ffId}>
                                        <tr
                                            onClick={() => setExpanded(prev => {
                                                const n = new Set(prev);
                                                if (n.has(g.ffId)) n.delete(g.ffId); else n.add(g.ffId);
                                                return n;
                                            })}
                                            style={{ cursor: 'pointer' }}
                                        >
                                            <td style={{ position: 'sticky', left: 0, zIndex: 1, background: isOpen ? '#eef2ff' : '#fff', fontWeight: 700, padding: '6px 10px', borderBottom: '1px solid var(--color-border)', whiteSpace: 'nowrap' }}>
                                                <span style={{ color: 'var(--color-text-muted)', marginRight: 4 }}>{isOpen ? '▾' : '▸'}</span>📦 {warehouseNameById(g.ffId)}
                                            </td>
                                            {matrixWbCols.map(wb => {
                                                const cell = matrixCells.get(`${g.ffId}::${wb}`);
                                                const val = matrixUnit === 'pallets'
                                                    ? (matrixPallets.cells.get(`${g.ffId}::${wb}`) || 0)
                                                    : cell ? (matrixUnit === 'boxes' ? cell.boxes : cell.qty) : 0;
                                                return (
                                                    <td key={wb} style={{ textAlign: 'right', padding: '6px 10px', borderBottom: '1px solid var(--color-border)', color: val > 0 ? 'var(--color-text)' : 'var(--color-text-muted)' }}>
                                                        {val > 0 ? formatNumber(val, 0) : '·'}
                                                    </td>
                                                );
                                            })}
                                            <td style={{ textAlign: 'right', padding: '6px 10px', borderBottom: '1px solid var(--color-border)', fontWeight: 700, background: 'rgba(59,130,246,0.04)' }}>
                                                {formatNumber(rowTotal, 0)}
                                            </td>
                                        </tr>
                                        {isOpen && skuRows.map(s => (
                                            <tr key={`${g.ffId}-${s.nmId}`} style={{ background: '#fafafe' }}>
                                                <td style={{ position: 'sticky', left: 0, zIndex: 1, background: '#fafafe', padding: '4px 10px 4px 30px', borderBottom: '1px solid var(--color-border)', whiteSpace: 'nowrap', fontSize: 11 }}>
                                                    {s.isNew && <span style={{ marginRight: 4, color: '#a855f7', fontWeight: 700 }}>🆕</span>}{s.vendor}
                                                </td>
                                                {matrixWbCols.map(wb => {
                                                    const v = s.cells.get(wb) || 0;
                                                    return (
                                                        <td key={wb} style={{ textAlign: 'right', padding: '4px 10px', borderBottom: '1px solid var(--color-border)', fontSize: 11, color: v > 0 ? 'var(--color-text)' : 'var(--color-text-muted)' }}>
                                                            {v > 0 ? formatNumber(v, 0) : '·'}
                                                        </td>
                                                    );
                                                })}
                                                <td style={{ textAlign: 'right', padding: '4px 10px', borderBottom: '1px solid var(--color-border)', fontSize: 11, fontWeight: 600, background: 'rgba(59,130,246,0.03)' }}>
                                                    {formatNumber(s.total, 0)}
                                                </td>
                                            </tr>
                                        ))}
                                    </Fragment>
                                );
                            })}
                            <tr>
                                <td style={{ position: 'sticky', left: 0, zIndex: 1, background: '#f5f5f7', fontWeight: 700, padding: '8px 10px' }}>Σ</td>
                                {matrixWbCols.map(wb => (
                                    <td key={wb} style={{ textAlign: 'right', padding: '8px 10px', fontWeight: 700, background: '#f5f5f7' }}>{formatNumber(matrixColTotal(wb), 0)}</td>
                                ))}
                                <td style={{ textAlign: 'right', padding: '8px 10px', fontWeight: 700, background: 'rgba(59,130,246,0.08)' }}>
                                    {formatNumber(matrixUnit === 'pallets' ? matrixPallets.grand : matrixUnit === 'boxes' ? boxesSum(lines) : sumQty(lines), 0)}
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {ffGroups.map(g => {
                        const isOpen = expanded.has(g.ffId);
                        const ffName = warehouseNameById(g.ffId);
                        const ffPallets = palletsForFf(g.lines).pallets;
                        return (
                            <div key={g.ffId} className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                                <div
                                    onClick={() => setExpanded(prev => {
                                        const n = new Set(prev);
                                        if (n.has(g.ffId)) n.delete(g.ffId); else n.add(g.ffId);
                                        return n;
                                    })}
                                    style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px', cursor: 'pointer' }}
                                >
                                    <span style={{ fontSize: 12, width: 12, color: 'var(--color-text-muted)' }}>{isOpen ? '▾' : '▸'}</span>
                                    <span style={{ fontWeight: 700, fontSize: 15 }}>📦 {ffName}</span>
                                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                        Σ {formatNumber(sumQty(g.lines), 0)} шт · {formatNumber(boxesSum(g.lines), 0)} кор · <strong style={{ color: 'var(--color-accent)' }}>{formatNumber(ffPallets, 0)} пал</strong> · {reqCountOf(g.lines)} заявок · {skuCountOf(g.lines)} SKU
                                    </span>
                                    <div style={{ flex: 1 }} />
                                    <button
                                        className="btn btn-primary btn-sm"
                                        onClick={e => { e.stopPropagation(); openFf(g.ffId); }}
                                        title={`Открыть склад «${ffName}»: список заявок, статусы, передать на ФФ / в сборку`}
                                    >
                                        Открыть →
                                    </button>
                                    <button
                                        className="btn btn-secondary btn-sm"
                                        onClick={e => { e.stopPropagation(); exportFf(g.ffId, g.lines); }}
                                        title={`Выгрузить пикинг-лист склада «${ffName}» в Excel`}
                                    >
                                        📥 Выгрузить
                                    </button>
                                </div>
                                {isOpen && (
                                    <div style={{ padding: '4px 16px 14px 34px', borderTop: '1px solid var(--color-border)' }}>
                                        {groupByWb(g.lines).map(({ wb, items }) => {
                                            const pb = palletBadge(palletsForCell(items, wb));
                                            return (
                                            <div key={wb} style={{ marginTop: 10 }}>
                                                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-warning)', marginBottom: 4 }}>
                                                    → {wb} <span style={{ color: 'var(--color-text-muted)', fontWeight: 500 }}>(Σ {formatNumber(sumQty(items), 0)} шт · {formatNumber(boxesSum(items), 0)} кор{pb.pallets > 0 && <> · <strong style={{ color: 'var(--color-accent)' }}>{formatNumber(pb.pallets, 0)} пал</strong></>}{pb.underfilled && <span style={{ color: 'var(--color-warning)' }} title={`Паллета заполнена ~${pb.pct}% — мало товара на это направление`}> · ⚠ ~{pb.pct}%</span>}{pb.unknownUnits > 0 && <span style={{ color: 'var(--color-text-muted)' }} title="Нет габаритов коробки — паллеты не считаются"> · {formatNumber(pb.unknownUnits, 0)} шт б/габ</span>})</span>
                                                </div>
                                                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                                                    <thead>
                                                        <tr style={{ color: 'var(--color-text-muted)', fontSize: 11 }}>
                                                            <th style={{ textAlign: 'left', padding: '2px 6px', fontWeight: 600 }}>Артикул</th>
                                                            <th style={{ textAlign: 'left', padding: '2px 6px', fontWeight: 600 }}>Баркод</th>
                                                            <th style={{ textAlign: 'right', padding: '2px 6px', fontWeight: 600, width: 80 }}>Коробок</th>
                                                            <th style={{ textAlign: 'right', padding: '2px 6px', fontWeight: 600, width: 70 }}>Шт</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {items.map(l => {
                                                            const k = nmPpb.get(l.nmId) || 0;
                                                            const full = k > 0 ? Math.floor(l.qty / k) : 0;
                                                            const rem = k > 0 ? l.qty % k : 0;
                                                            return (
                                                                <tr key={`${l.nmId}-${l.pkg}`} style={{ borderBottom: '1px solid var(--color-border)' }}>
                                                                    <td style={{ padding: '3px 6px' }}>
                                                                        {l.isNew && <span style={{ marginRight: 4, color: '#a855f7', fontWeight: 700 }}>🆕</span>}{l.vendor}
                                                                    </td>
                                                                    <td style={{ padding: '3px 6px', color: 'var(--color-text-muted)' }}>{l.barcode}</td>
                                                                    <td
                                                                        style={{ padding: '3px 6px', textAlign: 'right', whiteSpace: 'nowrap' }}
                                                                        title={k > 0 ? `${full} полн. коробов по ${k} шт${rem > 0 ? ` + ${rem} шт россыпью (неполный короб)` : ''}` : 'Без кратности короба'}
                                                                    >
                                                                        {k <= 0 ? (
                                                                            <span style={{ color: 'var(--color-text-muted)' }}>—</span>
                                                                        ) : (
                                                                            <>
                                                                                <span>{formatNumber(full, 0)} кор</span>
                                                                                {rem > 0 && <span style={{ color: 'var(--color-warning)', fontWeight: 600 }}> + {formatNumber(rem, 0)} шт</span>}
                                                                            </>
                                                                        )}
                                                                    </td>
                                                                    <td style={{ padding: '3px 6px', textAlign: 'right', fontWeight: 600, whiteSpace: 'nowrap' }}>{formatNumber(l.qty, 0)}</td>
                                                                </tr>
                                                            );
                                                        })}
                                                    </tbody>
                                                </table>
                                            </div>
                                            );
                                        })}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
