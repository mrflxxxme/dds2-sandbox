'use client';
import { Fragment, useCallback, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { exportToExcel, formatNumber } from '@/lib/utils';
import { type Column } from '@/components';
import TanStackDataTable from '@/components/TanStackDataTable';
import KpiCard from '@/components/KpiCard';
import type { AssemblyDraftRow, CommitSupply, PackageType, Warehouse } from '@/types/api';
import {
    buildPreviewLines,
    trimLinesToWholePallets,
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
import { palletsForLines, maxPalletHeightCm, effectiveBoxesPerPallet, type PalletCount } from '@/lib/utils/boxPallet';
import { buildPalletManifest } from '@/lib/utils/palletManifest';

const PKG_BADGE: Record<string, string> = {
    BOX: 'badge-info', MONOPALLET: 'badge-warning', SUPERSAFE: 'badge-secondary',
};

// Эмодзи секции по упаковке (для раздельных секций «Короба / Моно / Сейф» в карточках).
const PKG_EMOJI: Record<string, string> = {
    BOX: '📦', MONOPALLET: '🟫', SUPERSAFE: '🔒',
};
// Порядок секций упаковки в виде «Карточки».
const PKG_ORDER: PackageType[] = ['BOX', 'MONOPALLET', 'SUPERSAFE'];

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

interface DraftPreviewProps {
    slug: string;
    draftId: number;
    /** Живые строки черновика из родителя (редактор их мутирует) — НЕ draft.distribution.rows. */
    rows: AssemblyDraftRow[];
    newcomerNmIds: Set<number>;
    warehouses: Warehouse[];
    nmPpb: Map<number, number | null>;
    nmMeta: Map<number, { subject: string; brand: string }>;
    nmBoxSize: Map<number, string | null>;
    palletOverrides: Record<string, number>;
    /** Геометрия коробок загружена (gate для «только целые паллеты»). */
    geomReady: boolean;
    /** Сбросить правки редактора на сервер перед commit (родитель). Возвращает успех. */
    ensureSaved: () => Promise<boolean>;
    onToast: (message: string, type: 'success' | 'error') => void;
    /** Коммит оставил строки в черновике (whole-only снял неполные) — родитель перезагружает. */
    onReloadDraft: () => void;
    /** Пересоздать черновик целыми коробами (округление вверх из ФФ / вниз). */
    onRecreateWholeBoxes?: () => Promise<void>;
    /** Перепроверить приёмку WB текущего черновика (перераспределить закрытые склады). */
    onRecheckAcceptance?: () => Promise<void>;
}

/** Предпросмотр заявок + commit. Показывает ВЕСЬ черновик (короб + моно + сейф)
 *  с бейджем упаковки на каждой отгрузке. Встроен внизу страницы «Черновик». */
export default function DraftPreview({
    slug, draftId, rows, newcomerNmIds, warehouses,
    nmPpb, nmMeta, nmBoxSize, palletOverrides, geomReady,
    ensureSaved, onToast, onReloadDraft, onRecreateWholeBoxes, onRecheckAcceptance,
}: DraftPreviewProps) {
    const router = useRouter();

    const [committing, setCommitting] = useState(false);
    const [rounding, setRounding] = useState(false);
    const [rechecking, setRechecking] = useState(false);
    const [expanded, setExpanded] = useState<Set<number>>(new Set());
    // Раскрытые манифесты паллет «📐 Раскладка» по ключу `${ffId}::${wb}::${pkg}`.
    const [manifestOpen, setManifestOpen] = useState<Set<string>>(new Set());
    const [query, setQuery] = useState('');
    const [selectedWbs, setSelectedWbs] = useState<Set<string>>(new Set());
    const [selectedSubjects, setSelectedSubjects] = useState<Set<string>>(new Set());
    const [selectedBrands, setSelectedBrands] = useState<Set<string>>(new Set());
    const [showPartial, setShowPartial] = useState(false);  // разбивка неполных коробов
    const [viewMode, setViewMode] = useState<'cards' | 'table' | 'matrix'>('cards');
    const [matrixUnit, setMatrixUnit] = useState<'qty' | 'boxes' | 'pallets'>('qty');
    // «Только целые паллеты»: срез каждой отгрузки (ФФ→склад) до целых паллет.
    // Дефолт ВКЛ — но трим влияет на данные только когда геометрия загружена
    // (effectiveWholeOnly ниже), иначе на mount предпросмотр мигнул бы пустым.
    const [wholeOnly, setWholeOnly] = useState(true);

    const warehouseNameById = useCallback(
        (id: number) => warehouses.find(w => w.id === id)?.name ?? `Склад ${id}`,
        [warehouses],
    );

    const openFf = useCallback((ffId: number, pkg: PackageType) => {
        const qs = new URLSearchParams({ draft: String(draftId), ff: String(ffId), pkg });
        router.push(`/p/${slug}/warehouse/assembly/distribute/ff?${qs.toString()}`);
    }, [draftId, router, slug]);

    // Весь черновик идёт в commit (упаковка больше не делит экран на срезы — все
    // типы показываются вместе, бейджем). commit_draft без package_type создаёт всё.
    const rawLines = useMemo(() => buildPreviewLines(rows, newcomerNmIds), [rows, newcomerNmIds]);

    // Штук в полной паллете SKU на складе-цели (короб: bpp×ppb; null — без габаритов).
    const uppForCell = useCallback((nmId: number, wbName: string): number | null => {
        const k = nmPpb.get(nmId);
        const bpp = effectiveBoxesPerPallet(nmBoxSize.get(nmId) ?? null, maxPalletHeightCm(wbName), palletOverrides);
        return bpp != null && k && k > 0 ? bpp * k : null;
    }, [nmPpb, nmBoxSize, palletOverrides]);

    const wholeTrim = useMemo(() => trimLinesToWholePallets(rawLines, uppForCell), [rawLines, uppForCell]);
    // Намерение «только целые» (wholeOnly) применяется к ДАННЫМ лишь когда геометрия
    // готова — иначе трим без габаритов снёс бы все строки → пустой предпросмотр.
    const effectiveWholeOnly = wholeOnly && geomReady;
    const allLines = useMemo(() => (effectiveWholeOnly ? wholeTrim.kept : rawLines), [effectiveWholeOnly, wholeTrim, rawLines]);

    // Явные отгрузки для commit (режим «только целые»): заявки создаются ровно из них.
    const wholeSupplies = useMemo<CommitSupply[]>(() => {
        if (!effectiveWholeOnly) return [];
        const m = new Map<string, CommitSupply>();
        for (const l of allLines) {
            if (l.qty <= 0) continue;
            const key = `${l.ffId}::${l.wbName}::${l.pkg}`;
            let s = m.get(key);
            if (!s) { s = { source_ff_id: l.ffId, target_wb_name: l.wbName, package_type: l.pkg, items: {} }; m.set(key, s); }
            s.items[l.barcode] = (s.items[l.barcode] || 0) + l.qty;
        }
        return [...m.values()];
    }, [effectiveWholeOnly, allLines]);

    const toggleWholeOnly = useCallback(() => {
        if (wholeOnly) { setWholeOnly(false); return; }
        if (!geomReady) {
            onToast('Геометрия коробок ещё загружается — повторите через секунду', 'error');
            return;
        }
        setWholeOnly(true);
        const parts: string[] = [];
        if (wholeTrim.removedSupplies > 0) parts.push(`убрано отгрузок: ${wholeTrim.removedSupplies}`);
        if (wholeTrim.droppedUnits > 0) parts.push(`снято ${formatNumber(wholeTrim.droppedUnits, 0)} шт`);
        onToast(parts.length ? `Только целые паллеты — ${parts.join(', ')}` : 'Все отгрузки уже целыми паллетами', 'success');
    }, [wholeOnly, geomReady, wholeTrim, onToast]);

    // Опции фильтров (Σ qty по всему срезу, по убыванию объёма).
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

    // Поиск + фильтры — ТОЛЬКО отображение/выгрузка. commit создаёт весь срез.
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

    // Присутствующие типы упаковки в отфильтрованном наборе (для раздельных секций
    // в виде «Карточки»). Порядок фиксированный: короб → моно → сейф.
    const presentPkgs = useMemo(
        () => PKG_ORDER.filter(pkg => lines.some(l => l.pkg === pkg)),
        [lines],
    );

    // Заявок = уникальные (ФФ, WB, упаковка); withNewcomer — сколько с 🆕.
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
        setQuery(''); setSelectedWbs(new Set()); setSelectedSubjects(new Set()); setSelectedBrands(new Set());
    }, []);
    const distinctSku = useMemo(() => new Set(allLines.map(l => l.nmId)).size, [allLines]);
    const partialBoxes = useMemo(
        () => allLines.filter(l => { const k = nmPpb.get(l.nmId); return !!k && k > 0 && l.qty % k !== 0; }).length,
        [allLines, nmPpb],
    );
    const looseUnits = useMemo(
        () => allLines.reduce((s, l) => { const k = nmPpb.get(l.nmId); return s + (k && k > 0 && l.qty % k !== 0 ? l.qty % k : 0); }, 0),
        [allLines, nmPpb],
    );
    // Разбивка неполных коробов: строки-отгрузки (SKU×склад), где qty не кратно коробу.
    const partialDetail = useMemo(() => {
        const out: { nmId: number; vendor: string; barcode: string; wb: string; qty: number; full: number; loose: number; k: number; isNew: boolean }[] = [];
        for (const l of allLines) {
            const k = nmPpb.get(l.nmId);
            if (!k || k <= 0) continue;          // без кратности короба (в т.ч. новинки россыпью) — не «неполный короб»
            const loose = l.qty % k;
            if (loose === 0) continue;
            out.push({ nmId: l.nmId, vendor: l.vendor, barcode: l.barcode, wb: l.wbName, qty: l.qty, full: Math.floor(l.qty / k), loose, k, isNew: l.isNew });
        }
        return out.sort((a, b) => b.loose - a.loose);
    }, [allLines, nmPpb]);
    const exportPartial = useCallback(() => {
        exportToExcel(
            partialDetail.map(d => ({ vendor: d.vendor, barcode: d.barcode, wb: d.wb, qty: d.qty, full: d.full, loose: d.loose, k: d.k })),
            'Неполные_коробы',
            [
                { key: 'vendor', label: 'Артикул' },
                { key: 'barcode', label: 'Баркод' },
                { key: 'wb', label: 'WB-склад' },
                { key: 'qty', label: 'Шт' },
                { key: 'full', label: 'Полных коробов' },
                { key: 'loose', label: 'Россыпь (шт)' },
                { key: 'k', label: 'В коробе (кратность)' },
            ],
        );
    }, [partialDetail]);

    const boxesOf = useCallback((l: PreviewLine) => {
        const k = nmPpb.get(l.nmId);
        return k && k > 0 ? Math.ceil(l.qty / k) : 0;
    }, [nmPpb]);
    const boxesSum = useCallback((ls: PreviewLine[]) => ls.reduce((s, l) => s + boxesOf(l), 0), [boxesOf]);

    // ─── Паллеты (геометрия box_size) ─────────────────────────────────────
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

    const palletsForFf = useCallback((ls: PreviewLine[]): PalletCount => {
        let pallets = 0, fill = 0, unknownLines = 0, unknownUnits = 0;
        for (const { wb, items } of groupByWb(ls)) {
            const r = palletsForCell(items, wb);
            pallets += r.pallets; fill += r.fill; unknownLines += r.unknownLines; unknownUnits += r.unknownUnits;
        }
        return { pallets, fill, unknownLines, unknownUnits };
    }, [palletsForCell]);

    const palletTotals = useMemo(() => {
        let pallets = 0, unknownUnits = 0;
        for (const g of groupByFf(allLines)) {
            const r = palletsForFf(g.lines);
            pallets += r.pallets; unknownUnits += r.unknownUnits;
        }
        return { pallets, unknownUnits };
    }, [allLines, palletsForFf]);

    // Map для commit: "{ffId}::{wbName}::{pkg}" → паллет (мин. 1 на заявку с товаром).
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

    const palletBadge = useCallback((pc: PalletCount) => {
        const avg = pc.pallets > 0 ? pc.fill / pc.pallets : 0;
        return { pallets: pc.pallets, pct: Math.round(avg * 100), underfilled: pc.pallets > 0 && avg < 0.6, unknownUnits: pc.unknownUnits };
    }, []);

    // Группировка строк ФФ по (WB-цель × упаковка) — одна отгрузка = одна заявка.
    const groupByWbPkg = useCallback((ls: PreviewLine[]): Array<{ wb: string; pkg: PackageType; items: PreviewLine[] }> => {
        const m = new Map<string, { wb: string; pkg: PackageType; items: PreviewLine[] }>();
        for (const l of ls) {
            const k = `${l.wbName}::${l.pkg}`;
            let e = m.get(k);
            if (!e) { e = { wb: l.wbName, pkg: l.pkg, items: [] }; m.set(k, e); }
            e.items.push(l);
        }
        return [...m.values()]
            .map(e => ({ ...e, items: e.items.slice().sort((a, b) => b.qty - a.qty || a.vendor.localeCompare(b.vendor)) }))
            .sort((a, b) => sumQty(b.items) - sumQty(a.items));
    }, []);

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
        exportToExcel(sortForExport(ls).map(l => toRow(l)), `Сборка_${excelSheetName(warehouseNameById(ffId))}_${today}`, PREVIEW_EXPORT_COLUMNS);
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

    const exportSeparate = useCallback(() => {
        ffGroups.forEach((g, i) => { setTimeout(() => exportFf(g.ffId, g.lines), i * 400); });
    }, [ffGroups, exportFf]);

    // ─── Табличный вид ───────────────────────────────────────────────────
    const tableColumns: Column[] = useMemo(() => [
        { key: 'ff', label: 'Склад-источник' },
        { key: 'wb', label: 'WB-цель' },
        { key: 'pkg', label: 'Упаковка' },
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
            pkg: PKG_LABEL_RU[l.pkg] || l.pkg,
            vendor: l.vendor,
            barcode: l.barcode,
            boxes: k && k > 0 ? Math.ceil(l.qty / k) : null,
            qty: l.qty,
            type: l.isNew ? 'Новинка' : 'Обычный',
            isNew: l.isNew,
        };
    }), [lines, nmPpb, warehouseNameById]);

    // ─── Матричный вид ───────────────────────────────────────────────────
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
            // Сбросить правки редактора на сервер — commit читает серверный черновик.
            const saved = await ensureSaved();
            if (!saved) { setCommitting(false); return; }
            // package_type не передаём → commit_draft создаёт ВЕСЬ черновик (короб+моно+сейф).
            const resp = await api.commitAssemblyDraft(draftId, undefined, palletCounts, effectiveWholeOnly ? wholeSupplies : undefined);
            const ids = resp.created_request_ids || [];
            // whole-only мог снять неполные отгрузки → проверяем остаток.
            let leftoverRows = 0;
            try {
                const fresh = await api.getAssemblyDraft(draftId);
                leftoverRows = fresh.distribution.rows?.length ?? 0;
            } catch { leftoverRows = 0; }
            onToast(`Создано заявок: ${ids.length}${leftoverRows > 0 ? `. Осталось строк: ${leftoverRows}` : ''}`, 'success');
            if (leftoverRows > 0) {
                onReloadDraft();
                setCommitting(false);
            } else {
                setTimeout(() => {
                    if (ids.length === 1) router.push(`/p/${slug}/warehouse/assembly/${ids[0]}`);
                    else if (ids.length > 1) router.push(`/p/${slug}/warehouse/assembly?just_created=${ids.join(',')}`);
                    else router.push(`/p/${slug}/warehouse/assembly`);
                }, 700);
            }
        } catch (e: unknown) {
            onToast(e instanceof Error ? e.message : 'Ошибка создания сборок', 'error');
            setCommitting(false);
        }
    }, [draftId, ensureSaved, palletCounts, effectiveWholeOnly, wholeSupplies, router, slug, onToast, onReloadDraft]);

    const pkgBadge = (pkg: PackageType) => (
        <span className={`badge ${PKG_BADGE[pkg] || 'badge-secondary'}`} style={{ fontSize: 10 }}>{PKG_LABEL_RU[pkg] || pkg}</span>
    );

    // ─── Карточка одной ФФ-группы (раскрываемая) ──────────────────────────
    // Тело строится из переданных строк (в секциях — строки ОДНОГО типа упаковки,
    // потому groupByWbPkg даст блоки этого же типа). Вынесено, чтобы переиспользовать
    // в раздельных секциях по упаковке и в общем виде «Карточки».
    const renderFfGroup = (g: { ffId: number; lines: PreviewLine[] }) => {
        const isOpen = expanded.has(g.ffId);
        const ffName = warehouseNameById(g.ffId);
        const ffPallets = palletsForFf(g.lines).pallets;
        return (
            <div key={g.ffId} className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                <div onClick={() => setExpanded(prev => { const n = new Set(prev); if (n.has(g.ffId)) n.delete(g.ffId); else n.add(g.ffId); return n; })} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px', cursor: 'pointer' }}>
                    <span style={{ fontSize: 12, width: 12, color: 'var(--color-text-muted)' }}>{isOpen ? '▾' : '▸'}</span>
                    <span style={{ fontWeight: 700, fontSize: 15 }}>📦 {ffName}</span>
                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                        Σ {formatNumber(sumQty(g.lines), 0)} шт · {formatNumber(boxesSum(g.lines), 0)} кор · <strong style={{ color: 'var(--color-accent)' }}>{formatNumber(ffPallets, 0)} пал</strong> · {reqCountOf(g.lines)} заявок · {skuCountOf(g.lines)} SKU
                    </span>
                    <div style={{ flex: 1 }} />
                    <button className="btn btn-primary btn-sm" onClick={e => { e.stopPropagation(); openFf(g.ffId, g.lines[0]?.pkg ?? 'BOX'); }} title={`Открыть склад «${ffName}»: заявки-юниты, передать на ФФ / в сборку`}>
                        Открыть →
                    </button>
                    <button className="btn btn-secondary btn-sm" onClick={e => { e.stopPropagation(); exportFf(g.ffId, g.lines); }} title={`Выгрузить пикинг-лист склада «${ffName}» в Excel`}>
                        📥 Выгрузить
                    </button>
                </div>
                {isOpen && (
                    <div style={{ padding: '4px 16px 14px 34px', borderTop: '1px solid var(--color-border)' }}>
                        {groupByWbPkg(g.lines).map(({ wb, pkg, items }) => {
                            const pb = palletBadge(palletsForCell(items, wb));
                            // Манифест физических паллет: тот же box/mono-ceil, что и бейдж выше,
                            // потому Σ pallets.length === pb.pallets (реконсиляция по построению).
                            const manifest = buildPalletManifest(
                                items.map(l => ({
                                    nmId: l.nmId, vendorCode: l.vendor, units: l.qty,
                                    boxSize: nmBoxSize.get(l.nmId) ?? null, ppb: nmPpb.get(l.nmId) ?? null,
                                })),
                                { mode: pkg === 'BOX' ? 'box' : 'mono', maxHeightCm: maxPalletHeightCm(wb), overrides: palletOverrides },
                            );
                            const mKey = `${g.ffId}::${wb}::${pkg}`;
                            const mOpen = manifestOpen.has(mKey);
                            const hasManifest = manifest.pallets.length > 0 || manifest.unpalletized.length > 0;
                            return (
                                <div key={`${wb}::${pkg}`} style={{ marginTop: 10 }}>
                                    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-warning)', marginBottom: 4, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                                        <span>→ {wb}</span>
                                        {pkgBadge(pkg)}
                                        <span style={{ color: 'var(--color-text-muted)', fontWeight: 500 }}>
                                            (Σ {formatNumber(sumQty(items), 0)} шт · {formatNumber(boxesSum(items), 0)} кор
                                            {pb.pallets > 0 && <> · <strong style={{ color: 'var(--color-accent)' }}>{formatNumber(pb.pallets, 0)} пал</strong></>}
                                            {pb.underfilled && <span style={{ color: 'var(--color-warning)' }} title={`Паллета заполнена ~${pb.pct}% — мало товара на это направление`}> · ⚠ ~{pb.pct}%</span>}
                                            {pb.unknownUnits > 0 && <span style={{ color: 'var(--color-text-muted)' }} title="Нет габаритов коробки — паллеты не считаются"> · {formatNumber(pb.unknownUnits, 0)} шт б/габ</span>})
                                        </span>
                                    </div>
                                    {hasManifest && (
                                        <div style={{ marginBottom: 6 }}>
                                            <button
                                                className="btn btn-secondary btn-sm"
                                                onClick={() => setManifestOpen(prev => toggleInSet(prev, mKey))}
                                                style={{ fontSize: 11 }}
                                                title={pkg === 'BOX'
                                                    ? 'Раскладка по физическим паллетам (смешанные короба разных артикулов)'
                                                    : 'Раскладка по физическим паллетам (по одному артикулу на паллету)'}
                                            >
                                                {mOpen ? '▾' : '▸'} 📐 Раскладка по паллетам ({formatNumber(manifest.pallets.length, 0)})
                                            </button>
                                            {mOpen && (
                                                <div style={{ marginTop: 6, paddingLeft: 6, display: 'flex', flexDirection: 'column', gap: 3 }}>
                                                    {manifest.pallets.map(p => {
                                                        const pct = Math.round(p.fillPct * 100);
                                                        const low = p.fillPct < 0.6;
                                                        return (
                                                            <div key={p.palletNo} style={{ fontSize: 12, display: 'flex', alignItems: 'baseline', gap: 6, flexWrap: 'wrap' }}>
                                                                <span className="badge badge-secondary" style={{ fontSize: 10 }}>Паллета {formatNumber(p.palletNo, 0)}</span>
                                                                <span style={{ color: 'var(--color-text)' }}>
                                                                    {p.items.map((it, i) => (
                                                                        <Fragment key={it.nmId}>
                                                                            {i > 0 && <span style={{ color: 'var(--color-text-muted)' }}> + </span>}
                                                                            <span>{it.vendorCode}</span>
                                                                            <span style={{ color: 'var(--color-text-muted)' }}>×{formatNumber(it.units, 0)}</span>
                                                                        </Fragment>
                                                                    ))}
                                                                </span>
                                                                <span style={{ color: low ? 'var(--color-warning)' : 'var(--color-text-muted)', fontWeight: low ? 600 : 400 }} title={low ? 'Паллета заполнена менее 60% — неполная' : undefined}>
                                                                    {low && '⚠ '}— {formatNumber(pct, 0)}%
                                                                </span>
                                                            </div>
                                                        );
                                                    })}
                                                    {manifest.unpalletized.length > 0 && (
                                                        <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginTop: 2 }} title="Нет габаритов короба — паллету не посчитать">
                                                            без паллетизации (нет габаритов):{' '}
                                                            {manifest.unpalletized.map((it, i) => (
                                                                <Fragment key={it.nmId}>
                                                                    {i > 0 && ', '}
                                                                    {it.vendorCode}×{formatNumber(it.units, 0)}
                                                                </Fragment>
                                                            ))}
                                                        </div>
                                                    )}
                                                </div>
                                            )}
                                        </div>
                                    )}
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
                                                        <td style={{ padding: '3px 6px' }}>{l.isNew && <span style={{ marginRight: 4, color: '#a855f7', fontWeight: 700 }}>🆕</span>}{l.vendor}</td>
                                                        <td style={{ padding: '3px 6px', color: 'var(--color-text-muted)' }}>{l.barcode}</td>
                                                        <td style={{ padding: '3px 6px', textAlign: 'right', whiteSpace: 'nowrap' }} title={k > 0 ? `${full} полн. коробов по ${k} шт${rem > 0 ? ` + ${rem} шт россыпью` : ''}` : 'Без кратности короба'}>
                                                            {k <= 0 ? <span style={{ color: 'var(--color-text-muted)' }}>—</span> : (
                                                                <><span>{formatNumber(full, 0)} кор</span>{rem > 0 && <span style={{ color: 'var(--color-warning)', fontWeight: 600 }}> + {formatNumber(rem, 0)} шт</span>}</>
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
    };

    // ─── Секция упаковки в виде «Карточки»: заголовок-подытог + ФФ-карточки ──
    // Строится на строках ОДНОГО типа упаковки. ФФ-группировка — повтор логики
    // groupByFf для подмножества (тот же helper, новый список строк).
    const renderPkgSection = (pkg: PackageType) => {
        const pkgLines = lines.filter(l => l.pkg === pkg);
        if (pkgLines.length === 0) return null;
        const pkgFfGroups = groupByFf(pkgLines);
        let pkgPallets = 0;
        for (const g of pkgFfGroups) pkgPallets += palletsForFf(g.lines).pallets;
        return (
            <div key={pkg} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div className="glass-card" style={{ padding: '10px 16px', display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                    <span style={{ fontWeight: 700, fontSize: 15 }}>{PKG_EMOJI[pkg] || ''} {PKG_LABEL_RU[pkg] || pkg}</span>
                    <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                        Σ {formatNumber(sumQty(pkgLines), 0)} шт · {formatNumber(boxesSum(pkgLines), 0)} кор · <strong style={{ color: 'var(--color-accent)' }}>{formatNumber(pkgPallets, 0)} пал</strong> · {reqCountOf(pkgLines)} заявок · {skuCountOf(pkgLines)} SKU
                    </span>
                </div>
                {pkgFfGroups.map(renderFfGroup)}
            </div>
        );
    };

    return (
        <div>
            {/* Шапка предпросмотра + действия */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
                <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700 }}>Предпросмотр заявок</h2>
                <div style={{ flex: 1 }} />
                <button className="btn btn-secondary btn-sm" onClick={exportAll} disabled={ffGroups.length === 0}>
                    {isFiltered ? '📥 Выгрузить показанное' : '📥 Выгрузить всё (1 файл)'}
                </button>
                <button className="btn btn-secondary btn-sm" onClick={exportSeparate} disabled={ffGroups.length === 0} title="Скачать отдельный Excel-файл на каждый склад-источник">
                    📥 Отдельно по складам
                </button>
                {onRecreateWholeBoxes && partialBoxes > 0 && (
                    <button
                        className="btn btn-sm btn-primary"
                        disabled={rounding}
                        title="Округлить ВСЕ короба до целых: добить вверх из свободного ФФ, при нехватке — срезать вниз. Россыпь убирается."
                        onClick={async () => {
                            setRounding(true);
                            try { await onRecreateWholeBoxes(); }
                            catch (e: unknown) { onToast(e instanceof Error ? e.message : 'Ошибка округления', 'error'); }
                            finally { setRounding(false); }
                        }}
                    >{rounding ? 'Округление…' : `📦 Округлить до целых коробов (${formatNumber(partialBoxes, 0)})`}</button>
                )}
                {onRecheckAcceptance && (
                    <button
                        className="btn btn-sm btn-secondary"
                        disabled={rechecking}
                        title="Перепроверить приёмку WB по всему черновику: закрытые склады перераспределить на открытые, тип упаковки по приёмке, пересобрать строки."
                        onClick={async () => {
                            setRechecking(true);
                            try { await onRecheckAcceptance(); }
                            catch (e: unknown) { onToast(e instanceof Error ? e.message : 'Ошибка перепроверки приёмки', 'error'); }
                            finally { setRechecking(false); }
                        }}
                    >{rechecking ? 'Проверка приёмки…' : '🚦 Перепроверить приёмку'}</button>
                )}
                <button
                    className={`btn btn-sm ${wholeOnly ? 'btn-primary' : 'btn-secondary'}`}
                    onClick={toggleWholeOnly}
                    disabled={!geomReady}
                    title={!geomReady
                        ? 'Геометрия коробок ещё загружается…'
                        : 'Оставить только ЦЕЛЫЕ паллеты на каждую отгрузку ФФ→склад: неполная отгрузка убирается полностью, у целых отгрузок неполный хвост снимается на ФФ.'}
                >
                    🟫 Только целые паллеты{wholeOnly ? ' ✓' : ''}
                </button>
                <button
                    className="btn btn-primary btn-sm"
                    onClick={handleCreate}
                    disabled={committing || totalAssemblies === 0}
                    title={wholeOnly
                        ? 'Создаёт только целые паллеты на каждую отгрузку ФФ→склад (неполные убраны).'
                        : 'Создаёт все заявки черновика (короб + моно). Поиск и фильтры влияют только на отображение.'}
                >
                    {committing ? 'Создание…' : `✓ Создать ${totalAssemblies} заявок`}
                </button>
            </div>

            {/* Сводка по срезу */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12, marginBottom: 12 }}>
                <KpiCard label="Заявок" value={formatNumber(totalAssemblies, 0)} icon="📋" color="var(--color-accent)" sub={breakdown.withNewcomer > 0 ? `🆕 с новинками: ${breakdown.withNewcomer}` : 'все обычные'} />
                <KpiCard label="Штук" value={formatNumber(sumQty(allLines), 0)} icon="🔢" color="var(--color-success)" />
                <KpiCard label="Коробок" value={formatNumber(boxesSum(allLines), 0)} icon="📦" color="var(--color-accent)" />
                <KpiCard label="Паллет" value={formatNumber(palletTotals.pallets, 0)} icon="🟫" color="var(--color-accent)" sub={palletTotals.unknownUnits > 0 ? `${formatNumber(palletTotals.unknownUnits, 0)} шт без габаритов` : 'по габаритам коробки'} />
                <KpiCard label="SKU" value={formatNumber(distinctSku, 0)} icon="🏷️" color="var(--color-warning)" sub="уникальных" />
                <div
                    onClick={() => partialBoxes > 0 && setShowPartial(s => !s)}
                    style={{ cursor: partialBoxes > 0 ? 'pointer' : 'default' }}
                    title={partialBoxes > 0 ? 'Нажми — разбивка неполных коробов по артикулам' : undefined}
                >
                    <KpiCard label="Неполных коробов" value={formatNumber(partialBoxes, 0)} icon="🟧" color={partialBoxes > 0 ? 'var(--color-warning)' : 'var(--color-text-muted)'} sub={partialBoxes > 0 ? `${formatNumber(looseUnits, 0)} шт россыпью · ${showPartial ? 'скрыть' : 'разбивка ▾'}` : 'все короба полные'} />
                </div>
            </div>

            {/* Разбивка неполных коробов (по клику на KPI) */}
            {showPartial && partialBoxes > 0 && (
                <div className="glass-card animate-in" style={{ padding: 16, marginBottom: 12 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
                        <div style={{ fontSize: 15, fontWeight: 600 }}>🟧 Неполные коробы — {formatNumber(partialBoxes, 0)} строк · {formatNumber(looseUnits, 0)} шт россыпью</div>
                        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                            {onRecreateWholeBoxes && (
                                <button
                                    className="btn btn-sm btn-primary"
                                    disabled={rounding}
                                    title="Округлить все короба до целых: добить вверх из свободного ФФ, при нехватке — срезать вниз"
                                    onClick={async () => {
                                        setRounding(true);
                                        try { await onRecreateWholeBoxes(); }
                                        catch (e: unknown) { onToast(e instanceof Error ? e.message : 'Ошибка округления', 'error'); }
                                        finally { setRounding(false); }
                                    }}
                                >{rounding ? 'Округление…' : '📦 Округлить всё до целых коробов'}</button>
                            )}
                            <button className="btn btn-sm btn-secondary" onClick={exportPartial}>⬇ Excel</button>
                        </div>
                    </div>
                    <p style={{ fontSize: 12, color: 'var(--color-text-muted)', margin: '0 0 12px', lineHeight: 1.5 }}>
                        <strong>Почему так:</strong> неполный короб появляется, когда потребность склада по артикулу <strong>не делится нацело на кратность короба</strong> (шт/короб). Остаток (qty mod кратность) едет <strong>россыпью</strong> — не округляем вверх (чтобы не передать лишнее) и не вниз (чтобы не недодать нужное). Это нормально; много россыпи = менее плотная упаковка. Уменьшить: добрать/убавить кол-во до кратности короба или объединить отгрузки одного SKU на соседние склады.
                    </p>
                    <div style={{ overflowX: 'auto', maxHeight: 360, overflowY: 'auto' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                            <thead>
                                <tr style={{ textAlign: 'right', color: 'var(--color-text-muted)', position: 'sticky', top: 0, background: 'var(--color-bg-card)' }}>
                                    <th style={{ textAlign: 'left', padding: '6px 8px' }}>Артикул</th>
                                    <th style={{ textAlign: 'left', padding: '6px 8px' }}>WB-склад</th>
                                    <th style={{ padding: '6px 8px' }}>Шт</th>
                                    <th style={{ padding: '6px 8px' }}>Полных коробов</th>
                                    <th style={{ padding: '6px 8px' }}>Россыпь</th>
                                    <th style={{ padding: '6px 8px' }}>В коробе</th>
                                </tr>
                            </thead>
                            <tbody>
                                {partialDetail.map((d, i) => (
                                    <tr key={`${d.nmId}-${d.wb}-${i}`} style={{ borderTop: '1px solid var(--color-border)' }}>
                                        <td style={{ textAlign: 'left', padding: '6px 8px', whiteSpace: 'nowrap' }}>
                                            {d.isNew && <span title="Новинка" style={{ marginRight: 4 }}>🆕</span>}{d.vendor}
                                        </td>
                                        <td style={{ textAlign: 'left', padding: '6px 8px', whiteSpace: 'nowrap' }}>{d.wb}</td>
                                        <td style={{ textAlign: 'right', padding: '6px 8px' }}>{formatNumber(d.qty, 0)}</td>
                                        <td style={{ textAlign: 'right', padding: '6px 8px' }}>{formatNumber(d.full, 0)}</td>
                                        <td style={{ textAlign: 'right', padding: '6px 8px', color: 'var(--color-warning)', fontWeight: 600 }}>+{formatNumber(d.loose, 0)}</td>
                                        <td style={{ textAlign: 'right', padding: '6px 8px', color: 'var(--color-text-muted)' }}>{formatNumber(d.k, 0)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}

            {/* Поиск + фильтры (только отображение/выгрузка) */}
            <div className="glass-card" style={{ padding: 12, marginBottom: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 11, color: 'var(--color-text-muted)' }}>Вид:</span>
                    {([['cards', '🗂 Карточки'], ['table', '📋 Таблица'], ['matrix', '🔲 Матрица']] as const).map(([m, label]) => (
                        <button key={m} className={`btn btn-sm ${viewMode === m ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setViewMode(m)}>{label}</button>
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
                    <input type="text" className="form-input" placeholder="🔍 Артикул или баркод…" value={query} onChange={e => setQuery(e.target.value)} style={{ maxWidth: 280, fontSize: 13 }} />
                    {isFiltered && (
                        <>
                            <span style={{ fontSize: 12, color: 'var(--color-text-muted)' }}>
                                Показано: <strong>{reqCountOf(lines)}</strong> заявок · Σ <strong>{formatNumber(sumQty(lines), 0)}</strong> из {totalAssemblies}
                            </span>
                            <button className="btn btn-secondary btn-sm" onClick={resetFilters}>✕ Сбросить</button>
                        </>
                    )}
                </div>
                <FilterChipRow label="Склад-цель:" options={wbOptions} selected={selectedWbs} onToggle={v => setSelectedWbs(prev => toggleInSet(prev, v))} titleFn={v => `Показать только заявки на «${v}»`} />
                <FilterChipRow label="Предмет:" options={subjectOptions} selected={selectedSubjects} onToggle={v => setSelectedSubjects(prev => toggleInSet(prev, v))} titleFn={v => `Показать только предмет «${v}»`} />
                <FilterChipRow label="Бренд:" options={brandOptions} selected={selectedBrands} onToggle={v => setSelectedBrands(prev => toggleInSet(prev, v))} titleFn={v => `Показать только бренд «${v}»`} />
            </div>

            {/* Body */}
            {ffGroups.length === 0 ? (
                <div className="glass-card" style={{ padding: 48, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                    {isFiltered ? (
                        <>Ничего не найдено по фильтру.<button className="btn btn-secondary btn-sm" style={{ marginLeft: 8 }} onClick={resetFilters}>✕ Сбросить</button></>
                    ) : 'Нет позиций в черновике — добавьте товары сверху.'}
                </div>
            ) : viewMode === 'table' ? (
                <TanStackDataTable columns={tableColumns} data={tableData} exportName={`Сборка_заявки_${draftId}`} enablePagination pageSize={100} />
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
                                const rowTotal = matrixUnit === 'pallets' ? palletsForFf(g.lines).pallets : matrixUnit === 'boxes' ? boxesSum(g.lines) : sumQty(g.lines);
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
                                        <tr onClick={() => setExpanded(prev => { const n = new Set(prev); if (n.has(g.ffId)) n.delete(g.ffId); else n.add(g.ffId); return n; })} style={{ cursor: 'pointer' }}>
                                            <td style={{ position: 'sticky', left: 0, zIndex: 1, background: isOpen ? '#eef2ff' : '#fff', fontWeight: 700, padding: '6px 10px', borderBottom: '1px solid var(--color-border)', whiteSpace: 'nowrap' }}>
                                                <span style={{ color: 'var(--color-text-muted)', marginRight: 4 }}>{isOpen ? '▾' : '▸'}</span>📦 {warehouseNameById(g.ffId)}
                                            </td>
                                            {matrixWbCols.map(wb => {
                                                const cell = matrixCells.get(`${g.ffId}::${wb}`);
                                                const val = matrixUnit === 'pallets' ? (matrixPallets.cells.get(`${g.ffId}::${wb}`) || 0) : cell ? (matrixUnit === 'boxes' ? cell.boxes : cell.qty) : 0;
                                                return (
                                                    <td key={wb} style={{ textAlign: 'right', padding: '6px 10px', borderBottom: '1px solid var(--color-border)', color: val > 0 ? 'var(--color-text)' : 'var(--color-text-muted)' }}>
                                                        {val > 0 ? formatNumber(val, 0) : '·'}
                                                    </td>
                                                );
                                            })}
                                            <td style={{ textAlign: 'right', padding: '6px 10px', borderBottom: '1px solid var(--color-border)', fontWeight: 700, background: 'rgba(59,130,246,0.04)' }}>{formatNumber(rowTotal, 0)}</td>
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
                                                <td style={{ textAlign: 'right', padding: '4px 10px', borderBottom: '1px solid var(--color-border)', fontSize: 11, fontWeight: 600, background: 'rgba(59,130,246,0.03)' }}>{formatNumber(s.total, 0)}</td>
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
                // Вид «Карточки»: раздельные секции по упаковке (короб / моно / сейф),
                // каждая со своими подытогами и ФФ-карточками. Combined-логика commit
                // не меняется — секции это только отображение.
                <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
                    {presentPkgs.map(renderPkgSection)}
                </div>
            )}
        </div>
    );
}
