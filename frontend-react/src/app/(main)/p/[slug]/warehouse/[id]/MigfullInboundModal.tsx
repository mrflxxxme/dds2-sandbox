'use client';
import { useCallback, useEffect, useState } from 'react';
import type { ClipboardEvent as ReactClipboardEvent, CSSProperties } from 'react';
import { api } from '@/lib/api';
import { exportToExcel, formatNumber } from '@/lib/utils';
import type {
    MigfullInboundDraft,
    MigfullInboundItem,
    MigfullInboundSendRequest,
    MigfullPackingLine,
    MigfullSendResult,
} from '@/types/api';

/**
 * Confirm-модалка «Создать поставку у Натали»: РЕДАКТИРУЕМЫЙ состав нашей
 * приёмки машины (per-строка: коробом/россыпью + шт в коробе + отправляемое
 * кол-во ≤ состава приёмки, prefill по цепочке кратность Натали → наша
 * кратность → россыпь) → РЕАЛЬНОЕ создание поставки (приёмки) в портале ФФ
 * «Натали» (migfull). Создание НЕОБРАТИМО — портал не даёт удалить/отменить
 * документ, повтор требует подтверждения.
 *
 * Массовое редактирование — отдельная вкладка модалки (как «наполнение машины»
 * в поставках Китая): грид нумерованных строк [ШК → упаковка → шт в коробе →
 * кол-во] со вставкой из Excel (Tab/«;»-разделитель), live-матчем по ШК к
 * составу и инлайн-ошибками; валидные строки применяются к раскладке состава.
 */

/** Состояние упаковки одной строки состава (ключ — ШК товара). */
interface PackRowState {
    boxMode: boolean;   // true — коробом, false — россыпью
    units: string;      // «шт в коробе» (input; валидно при boxMode: целое >= 2)
    boxes: string;      // «коробов» — явный сплит (input; валидно: 0..floor(qty/units))
    qty: string;        // отправляемое кол-во (input; валидно: 0..qty приёмки; 0 — строка не едет)
}

const EMPTY_ROW: PackRowState = { boxMode: false, units: '', boxes: '', qty: '' };

/** Сегмент-фильтр состава (быстрый срез по текущему состоянию упаковки). */
type SegmentFilter = 'all' | 'box' | 'loose' | 'no_mult' | 'mismatch';

/** Максимум коробов при данной кратности (пустая/невалидная кратность → ''). */
const maxBoxesStr = (qty: number, unitsStr: string): string => {
    const u = parseInt(unitsStr, 10);
    return Number.isFinite(u) && u >= 2 ? String(Math.floor(qty / u)) : '';
};

/** Эффективное отправляемое кол-во строки (невалидный input → весь состав приёмки). */
const effQty = (it: MigfullInboundItem, r: PackRowState): number => {
    const n = parseInt(r.qty, 10);
    return Number.isFinite(n) && n >= 0 && n <= it.qty ? n : it.qty;
};

/** Отчёт применения массового редактирования. */
interface PasteReport {
    applied: number;
    errors: string[];
}

/** Вкладка модалки: таблица состава / грид массового редактирования. */
type ModalTab = 'compose' | 'bulk';

/** Строка грида массового редактирования (сырые input-значения). */
interface BulkRow {
    barcode: string; // ШК товара
    pack: string;    // упаковка: «короб»/«россыпь» (или «к»/«р»)
    units: string;   // шт в коробе (опционально — фолбэк на текущее/prefill)
    qty: string;     // кол-во (опционально — без него берётся текущее)
}

const emptyBulkRow = (): BulkRow => ({ barcode: '', pack: '', units: '', qty: '' });

const BULK_MIN_ROWS = 5;

/** Инвариант грида: минимум BULK_MIN_ROWS строк + последняя строка всегда пустая. */
const padBulkRows = (rows: BulkRow[]): BulkRow[] => {
    const next = [...rows];
    while (next.length < BULK_MIN_ROWS) next.push(emptyBulkRow());
    if (next[next.length - 1].barcode.trim()) next.push(emptyBulkRow());
    return next;
};

/** «короб»/«коробом»/«к» → true, «россыпь»/«россыпью»/«р» → false, иначе null. */
const parsePackToken = (s: string): boolean | null => {
    const v = s.trim().toLowerCase();
    if (['короб', 'коробом', 'к'].includes(v)) return true;
    if (['россыпь', 'россыпью', 'р'].includes(v)) return false;
    return null;
};

/**
 * Патч упаковки строки состава: units — явное значение → текущее → prefill-кратность;
 * при коробе «коробов» пересчитывается на максимум под новое кол-во, при россыпи
 * units/boxes не трогаются (кроме явно заданной кратности).
 */
const buildPackPatch = (
    it: MigfullInboundItem,
    prev: PackRowState,
    boxMode: boolean,
    unitsStr: string,
    qty: number,
): PackRowState => {
    const units = boxMode
        ? (unitsStr || prev.units || (it.units_per_box != null ? String(it.units_per_box) : ''))
        : (unitsStr || prev.units);
    return {
        boxMode,
        units,
        boxes: boxMode ? maxBoxesStr(qty, units) : prev.boxes,
        qty: String(qty),
    };
};

interface Props {
    /** id нашей приёмки машины (InboundReceipt) — источник поставки */
    receiptId: number;
    /** номер машины (V-…) для заголовка */
    vehicleOrderNo: string;
    onClose: () => void;
    /** поставка создана (res.ok): родитель показывает тост и перезагружает списки */
    onSuccess: (res: MigfullSendResult) => void;
}

export default function MigfullInboundModal({ receiptId, vehicleOrderNo, onClose, onSuccess }: Props) {
    const [draft, setDraft] = useState<MigfullInboundDraft | null>(null);
    const [loadingDraft, setLoadingDraft] = useState(true);
    const [draftError, setDraftError] = useState('');

    // Поля шапки (инициализируются из prefill после загрузки draft).
    const [number, setNumber] = useState('');
    const [submissionDate, setSubmissionDate] = useState('');
    const [notes, setNotes] = useState('');

    // Упаковка строк состава: ШК → {boxMode, units}. Prefill из draft.items.
    const [packRows, setPackRows] = useState<Record<string, PackRowState>>({});

    // Подтверждение повторной отправки (already_sent или ответ 409).
    const [confirmResend, setConfirmResend] = useState(false);

    // Свёртываемый блок «Нестыковки кратности» (наша ≠ у Натали).
    const [mismatchOpen, setMismatchOpen] = useState(false);

    // Поиск/срез/выбор по составу. Всё ключуется ШК (не индексом видимого
    // списка) — правки и выбор переживают фильтрацию.
    const [search, setSearch] = useState('');
    const [segment, setSegment] = useState<SegmentFilter>('all');
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [bulkUnits, setBulkUnits] = useState('');

    // Вкладка «Состав» / «Массовое редактирование». Переключение не теряет
    // правок: packRows и bulkRows живут на уровне модалки.
    const [tab, setTab] = useState<ModalTab>('compose');
    const [bulkRows, setBulkRows] = useState<BulkRow[]>(() => padBulkRows([]));
    const [bulkReport, setBulkReport] = useState<PasteReport | null>(null);

    const [submitting, setSubmitting] = useState(false);
    const [submitError, setSubmitError] = useState('');
    const [result, setResult] = useState<MigfullSendResult | null>(null);

    // ─── Загрузка draft (маунт + кнопка «Повторить»). StrictMode-safe. ──────────
    const loadDraft = useCallback(() => {
        setLoadingDraft(true);
        setDraftError('');
        const controller = new AbortController();
        api.migfullInboundDraft(receiptId).then(d => {
            if (controller.signal.aborted) return;
            setDraft(d);
            setNumber(d.prefill.number ?? '');
            setSubmissionDate(d.prefill.submission_date ?? '');
            setNotes(d.prefill.notes ?? '');
            // Prefill упаковки: строки с известной кратностью — коробом (коробов = максимум),
            // прочие — россыпью.
            const rows: Record<string, PackRowState> = {};
            for (const it of d.items) {
                const units = it.units_per_box != null ? String(it.units_per_box) : '';
                rows[it.barcode] = {
                    boxMode: it.units_per_box != null && it.units_per_box >= 2,
                    units,
                    boxes: maxBoxesStr(it.qty, units),
                    qty: String(it.qty),
                };
            }
            setPackRows(rows);
            setSelected(new Set());
            setConfirmResend(false);
        }).catch((e: unknown) => {
            if (controller.signal.aborted) return;
            setDraftError(e instanceof Error ? e.message : 'Ошибка загрузки');
        }).finally(() => {
            if (!controller.signal.aborted) setLoadingDraft(false);
        });
        return () => controller.abort();
    }, [receiptId]);

    useEffect(() => loadDraft(), [loadDraft]);

    // ─── Производные упаковки: per-строка (короба/остаток) + итоги ──────────────
    const packOf = (barcode: string): PackRowState => packRows[barcode] ?? EMPTY_ROW;
    /** Разбор строки: sendQty/unitsNum/boxesNum (NaN — пусто/мусор), короба/остаток при валидном коробе. */
    const rowCalc = (barcode: string, maxQty: number) => {
        const r = packOf(barcode);
        // Отправляемое кол-во: валидно 0..maxQty (состав приёмки); 0 — строка не едет.
        const qtyNum = parseInt(r.qty, 10);
        const qtyOk = Number.isFinite(qtyNum) && qtyNum >= 0 && qtyNum <= maxQty;
        const qty = qtyOk ? qtyNum : maxQty; // fallback для расчётов; invalidQty блокирует submit
        const unitsNum = parseInt(r.units, 10);
        const unitsOk = Number.isFinite(unitsNum) && unitsNum >= 2;
        // Явный сплит: «коробов» валидно в 0..floor(qty/units); остаток едет россыпью.
        const boxesNum = parseInt(r.boxes, 10);
        const boxesOk = unitsOk && Number.isFinite(boxesNum) && boxesNum >= 0 && boxesNum * unitsNum <= qty;
        const asBox = r.boxMode && unitsOk && boxesOk;
        return {
            boxMode: r.boxMode,
            sendQty: qty,
            unitsNum,
            boxesNum,
            invalidQty: !qtyOk,                       // кол-во пусто/мусор/вне 0..maxQty
            invalidUnits: r.boxMode && !unitsOk,      // коробом, но «шт в коробе» не задано/некорректно
            invalidBoxes: r.boxMode && unitsOk && !boxesOk, // «коробов» вне 0..максимума
            invalid: !qtyOk || (r.boxMode && (!unitsOk || !boxesOk)),
            boxes: asBox ? boxesNum : 0,
            rest: asBox ? qty - boxesNum * unitsNum : qty, // при россыпи — всё отправляемое кол-во
        };
    };
    const items = draft?.items ?? [];
    const totalBoxes = items.reduce((s, it) => s + rowCalc(it.barcode, it.qty).boxes, 0);
    const totalLoose = items.reduce((s, it) => s + rowCalc(it.barcode, it.qty).rest, 0);
    const totalSendQty = items.reduce((s, it) => s + rowCalc(it.barcode, it.qty).sendQty, 0);
    const totalReceiptQty = items.reduce((s, it) => s + it.qty, 0);
    const invalidCount = items.reduce((s, it) => s + (rowCalc(it.barcode, it.qty).invalid ? 1 : 0), 0);
    const knownCount = items.filter(it => it.units_per_box != null).length;

    const setRow = (barcode: string, patch: Partial<PackRowState>) =>
        setPackRows(prev => ({ ...prev, [barcode]: { ...(prev[barcode] ?? EMPTY_ROW), ...patch } }));

    /** Массово: коробом по prefill-кратности (коробов = максимум) все строки, где она известна. */
    const applyKnownMultiplicity = () => {
        setPackRows(prev => {
            const next = { ...prev };
            for (const it of items) {
                if (it.units_per_box != null && it.units_per_box >= 2) {
                    const r = next[it.barcode] ?? { ...EMPTY_ROW, qty: String(it.qty) };
                    const units = String(it.units_per_box);
                    next[it.barcode] = { ...r, boxMode: true, units, boxes: maxBoxesStr(effQty(it, r), units) };
                }
            }
            return next;
        });
    };
    const applyAllLoose = () => {
        setPackRows(prev => {
            const next = { ...prev };
            for (const it of items) {
                next[it.barcode] = { ...(next[it.barcode] ?? { ...EMPTY_ROW, qty: String(it.qty) }), boxMode: false };
            }
            return next;
        });
    };

    /** Нестыковки кратности: обе стороны известны И различаются. */
    const mismatches = items.filter(
        it => it.units_natali != null && it.units_ours != null && it.units_natali !== it.units_ours,
    );
    /** «Взять нашу»/«взять Натали»: подставить кратность в units строки (коробом, коробов = максимум). */
    const applyUnits = (it: MigfullInboundItem, units: number) => {
        const u = String(units);
        setRow(it.barcode, { boxMode: true, units: u, boxes: maxBoxesStr(effQty(it, packOf(it.barcode)), u) });
    };

    // ─── Поиск + сегмент-фильтр (видимый срез; итоги и массовые кнопки — по ВСЕМ) ─
    const mismatchSet = new Set(mismatches.map(it => it.barcode));
    const boxModeCount = items.reduce((s, it) => s + (packOf(it.barcode).boxMode ? 1 : 0), 0);
    const noMultCount = items.filter(it => it.units_per_box == null).length;
    const query = search.trim().toLowerCase();
    const visibleItems = items.filter(it => {
        if (query
            && !it.barcode.toLowerCase().includes(query)
            && !(it.article_seller ?? '').toLowerCase().includes(query)
            && !(it.name ?? '').toLowerCase().includes(query)) return false;
        switch (segment) {
            case 'box': return packOf(it.barcode).boxMode;
            case 'loose': return !packOf(it.barcode).boxMode;
            case 'no_mult': return it.units_per_box == null;
            case 'mismatch': return mismatchSet.has(it.barcode);
            default: return true;
        }
    });
    const segments: { key: SegmentFilter; label: string; count: number }[] = [
        { key: 'all', label: 'Все', count: items.length },
        { key: 'box', label: 'Коробом', count: boxModeCount },
        { key: 'loose', label: 'Россыпью', count: items.length - boxModeCount },
        { key: 'no_mult', label: 'Без кратности', count: noMultCount },
        { key: 'mismatch', label: 'Нестыковки', count: mismatches.length },
    ];

    // ─── Выбор строк (чекбоксы) + массовые операции по выбранным ────────────────
    const toggleSelect = (barcode: string) =>
        setSelected(prev => {
            const next = new Set(prev);
            if (next.has(barcode)) next.delete(barcode); else next.add(barcode);
            return next;
        });
    const allVisibleSelected = visibleItems.length > 0 && visibleItems.every(it => selected.has(it.barcode));
    const toggleSelectAllVisible = () =>
        setSelected(prev => {
            const next = new Set(prev);
            if (allVisibleSelected) visibleItems.forEach(it => next.delete(it.barcode));
            else visibleItems.forEach(it => next.add(it.barcode));
            return next;
        });
    const selectedItems = items.filter(it => selected.has(it.barcode));

    /** Выбранные → коробом (units: текущее значение строки → prefill-кратность). */
    const applySelectedBox = () =>
        setPackRows(prev => {
            const next = { ...prev };
            for (const it of selectedItems) {
                const r = next[it.barcode] ?? { ...EMPTY_ROW, qty: String(it.qty) };
                const units = r.units || (it.units_per_box != null ? String(it.units_per_box) : '');
                next[it.barcode] = { ...r, boxMode: true, units, boxes: r.boxes || maxBoxesStr(effQty(it, r), units) };
            }
            return next;
        });
    const applySelectedLoose = () =>
        setPackRows(prev => {
            const next = { ...prev };
            for (const it of selectedItems) {
                next[it.barcode] = { ...(next[it.barcode] ?? { ...EMPTY_ROW, qty: String(it.qty) }), boxMode: false };
            }
            return next;
        });
    const bulkUnitsNum = parseInt(bulkUnits, 10);
    const bulkUnitsOk = Number.isFinite(bulkUnitsNum) && bulkUnitsNum >= 2;
    /** Задать «шт в коробе» выбранным: коробом, коробов = максимум при этой кратности. */
    const applySelectedUnits = () => {
        if (!bulkUnitsOk) return;
        const u = String(bulkUnitsNum);
        setPackRows(prev => {
            const next = { ...prev };
            for (const it of selectedItems) {
                const r = next[it.barcode] ?? { ...EMPTY_ROW, qty: String(it.qty) };
                next[it.barcode] = { ...r, boxMode: true, units: u, boxes: maxBoxesStr(effQty(it, r), u) };
            }
            return next;
        });
    };

    // ─── Excel-экспорт текущей раскладки (ВЕСЬ состав, не видимый срез) ─────────
    const exportExcel = () => {
        exportToExcel(
            items.map(it => {
                const c = rowCalc(it.barcode, it.qty);
                const r = packOf(it.barcode);
                return {
                    'ШК': it.barcode,
                    'Наш артикул': it.article_seller ?? '',
                    'Наименование': it.name ?? '',
                    'Кол-во': c.sendQty,
                    'Упаковка': r.boxMode ? 'короб' : 'россыпь',
                    'Шт в коробе': r.boxMode && Number.isFinite(c.unitsNum) ? c.unitsNum : '',
                    'Коробов': c.boxes,
                    'Россыпью (остаток)': c.rest,
                    'ШК короба': r.boxMode ? (it.box_barcode ?? '') : '',
                };
            }),
            `natali-postavka-${vehicleOrderNo}`,
        );
    };

    // ─── Вкладка «Массовое редактирование»: грид [ШК → упаковка → шт/кор → кол-во] ─
    const itemByBarcode = new Map(items.map(it => [it.barcode, it]));

    /** Live-проверка строки грида (подсветка инпутов + гейт применения). */
    const checkBulkRow = (row: BulkRow) => {
        const bc = row.barcode.trim();
        const filled = bc !== '';
        const it = filled ? (itemByBarcode.get(bc) ?? null) : null;
        const notFound = filled && !it;
        const boxMode = parsePackToken(row.pack);
        const packInvalid = filled && boxMode == null;
        const unitsStr = row.units.trim();
        const unitsInvalid = filled && unitsStr !== ''
            && !(/^\d+$/.test(unitsStr) && parseInt(unitsStr, 10) >= 2);
        const qtyStr = row.qty.trim();
        const qtyInvalid = filled && qtyStr !== ''
            && (!/^\d+$/.test(qtyStr) || (it != null && parseInt(qtyStr, 10) > it.qty));
        const valid = filled && it != null && !packInvalid && !unitsInvalid && !qtyInvalid;
        return { it, boxMode, filled, notFound, packInvalid, unitsInvalid, qtyInvalid, valid };
    };

    const bulkValidCount = bulkRows.reduce((s, r) => s + (checkBulkRow(r).valid ? 1 : 0), 0);
    const bulkFilledCount = bulkRows.reduce((s, r) => s + (checkBulkRow(r).filled ? 1 : 0), 0);

    const updateBulkRow = (idx: number, field: keyof BulkRow, value: string) => {
        setBulkRows(prev => {
            const next = [...prev];
            next[idx] = { ...next[idx], [field]: value };
            return padBulkRows(next);
        });
    };

    /**
     * Вставка из Excel в любой инпут ШК: построчный разбор (Tab/«;»), позиционно
     * «ШК → короб/россыпь → шт в коробе → кол-во», заполняет грид вниз от строки
     * вставки. Одиночное значение (без разделителей) — обычная вставка в инпут.
     */
    const handleBulkPaste = (startIdx: number) => (e: ReactClipboardEvent) => {
        const text = e.clipboardData.getData('text/plain');
        if (!text.includes('\t') && !text.includes('\n') && !text.includes(';')) return;
        e.preventDefault();
        const parsed: BulkRow[] = [];
        for (const raw of text.split(/\r?\n/)) {
            const line = raw.trim();
            if (!line) continue;
            const parts = line.split(/[\t;]/).map(s => s.trim());
            parsed.push({
                barcode: parts[0] ?? '',
                pack: parts[1] ?? '',
                units: parts[2] ?? '',
                qty: parts[3] ?? '',
            });
        }
        if (parsed.length === 0) return;
        setBulkRows(prev => {
            const next = [...prev];
            parsed.forEach((r, i) => { next[startIdx + i] = r; });
            return padBulkRows(next);
        });
        setBulkReport(null);
    };

    /**
     * «Применить (N)»: валидные строки грида → раскладка состава (boxMode/units/qty),
     * применённые уходят из грида, ошибочные остаются подсвеченными + отчёт.
     * Незатронутые строки состава не меняются.
     */
    const applyBulk = () => {
        const errors: string[] = [];
        const patches: Record<string, PackRowState> = {};
        const kept: BulkRow[] = [];
        let applied = 0;
        bulkRows.forEach((row, idx) => {
            const bc = row.barcode.trim();
            if (!bc) return; // пустые строки просто выпадают
            const c = checkBulkRow(row);
            if (!c.valid || c.it == null || c.boxMode == null) {
                kept.push(row);
                const no = idx + 1;
                if (c.notFound) errors.push(`стр. ${no}: ШК «${bc}» не найден в составе приёмки`);
                else if (c.packInvalid) errors.push(`стр. ${no} (${bc}): упаковка «${row.pack}» не распознана — ожидается «короб»/«россыпь» (или «к»/«р»)`);
                else if (c.unitsInvalid) errors.push(`стр. ${no} (${bc}): шт в коробе «${row.units}» — ожидается целое ≥ 2`);
                else if (c.qtyInvalid) errors.push(`стр. ${no} (${bc}): кол-во «${row.qty}» — не число или больше состава приёмки (${formatNumber(c.it?.qty ?? 0, 0)})`);
                return;
            }
            // Дубли ШК в гриде применяются каскадом: patch предыдущей строки — база следующей.
            const prev = patches[bc] ?? packRows[bc] ?? { ...EMPTY_ROW, qty: String(c.it.qty) };
            const qty = row.qty.trim() !== '' ? parseInt(row.qty, 10) : effQty(c.it, prev);
            patches[bc] = buildPackPatch(c.it, prev, c.boxMode, row.units.trim(), qty);
            applied++;
        });
        if (applied > 0) setPackRows(prev => ({ ...prev, ...patches }));
        setBulkRows(padBulkRows(kept));
        setBulkReport({ applied, errors });
    };

    const clearBulk = () => {
        setBulkRows(padBulkRows([]));
        setBulkReport(null);
    };

    const hasItems = items.length > 0;
    // Кнопка требует подтверждения, если поставка уже создавалась/связана.
    const needsConfirm = !!draft?.already_sent;
    const canSubmit = !!draft && draft.eligible && hasItems && invalidCount === 0
        && totalSendQty > 0 && (!needsConfirm || confirmResend);

    const doSend = async (forceResend: boolean) => {
        if (!draft) return;
        // Per-line packing по текущему состоянию модалки — опись строится ПО НЕМУ.
        // qty — отправляемое кол-во (может быть меньше состава приёмки; 0 — строка
        // не едет, но в packing присутствует: бэк требует покрыть весь состав).
        const packing: MigfullPackingLine[] = items.map(it => {
            const c = rowCalc(it.barcode, it.qty);
            const asBox = c.boxMode && !c.invalid;
            return {
                barcode: it.barcode,
                qty: c.sendQty,
                units_per_box: asBox ? c.unitsNum : null,
                // Явный сплит: ровно N коробов, остаток россыпью (бэк не пересчитывает и не ворнит).
                boxes: asBox ? c.boxesNum : null,
            };
        });
        const body: MigfullInboundSendRequest = {
            number: number.trim() || null,
            submission_date: submissionDate || null,
            notes: notes.trim() || null,
            force_resend: forceResend,
            packing,
        };
        setSubmitting(true);
        setSubmitError('');
        try {
            const res = await api.migfullInboundSend(receiptId, body);
            if (res.ok) {
                setResult(res);
                onSuccess(res);
            } else {
                setSubmitError(res.message || 'Не удалось создать поставку');
            }
        } catch (e: unknown) {
            // 409 от бэка (повторная отправка без force_resend) → подтверждение и повтор.
            if (e && typeof e === 'object' && (e as { code?: string }).code === 'conflict') {
                setConfirmResend(true);
                setSubmitError('Поставка для этой приёмки уже есть у ФФ. Подтвердите повторную отправку и нажмите «Создать поставку» ещё раз.');
            } else {
                setSubmitError(e instanceof Error ? e.message : 'Ошибка отправки');
            }
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div
            className="modal-overlay"
            style={{ padding: '24px 16px' }}
            onClick={e => { if (e.target === e.currentTarget) onClose(); }}
        >
            <div
                className="modal-card modal-card-xl modal-card-solid"
                style={{
                    maxHeight: 'calc(100vh - 48px)',
                    padding: 0,
                    display: 'flex', flexDirection: 'column',
                    overflow: 'hidden',
                }}
                onClick={e => e.stopPropagation()}
            >
                {/* Header */}
                <div style={{
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    padding: '18px 24px', borderBottom: '1px solid var(--color-border)', flexShrink: 0,
                }}>
                    <div>
                        <h2 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>Создать поставку у Натали</h2>
                        <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 2 }}>
                            Машина {vehicleOrderNo}
                            {draft?.prefill.receipt_number ? ` · приёмка ${draft.prefill.receipt_number}` : ''}
                        </div>
                    </div>
                    <button
                        onClick={onClose}
                        style={{
                            background: 'none', border: 'none', fontSize: 24,
                            cursor: 'pointer', color: 'var(--color-text-muted)',
                            lineHeight: 1, padding: '0 4px',
                        }}
                        aria-label="Закрыть"
                    >
                        ×
                    </button>
                </div>

                {/* Body */}
                <div style={{ padding: '20px 24px', overflowY: 'auto', flex: 1 }}>

                    {loadingDraft && (
                        <div style={{ textAlign: 'center', padding: '40px 0', color: 'var(--color-text-muted)' }}>
                            Загрузка состава...
                        </div>
                    )}

                    {!loadingDraft && draftError && (
                        <div style={{ padding: 16, borderRadius: 8, background: 'rgba(239,68,68,0.1)', color: 'var(--color-danger)' }}>
                            {draftError}
                            <button className="btn btn-secondary btn-sm" onClick={loadDraft} style={{ marginLeft: 12 }}>
                                Повторить
                            </button>
                        </div>
                    )}

                    {!loadingDraft && !draftError && draft && !draft.eligible && (
                        <div style={{ padding: 16, borderRadius: 8, background: 'rgba(245,158,11,0.1)', color: 'var(--color-warning)' }}>
                            Склад приёмки не совпадает со складом интеграции ФФ Натали — поставку создать нельзя.
                        </div>
                    )}

                    {/* Success */}
                    {result && (
                        <div style={{ padding: '14px 16px', borderRadius: 8, background: 'rgba(34,197,94,0.1)', color: 'var(--color-success)' }}>
                            <div style={{ fontWeight: 600, marginBottom: 4 }}>✓ Поставка создана у Натали</div>
                            {result.shipment_number && <div style={{ fontSize: 14 }}>Номер поставки: <strong>{result.shipment_number}</strong></div>}
                            {result.message && <div style={{ fontSize: 13, marginTop: 4 }}>{result.message}</div>}
                        </div>
                    )}

                    {/* Форма + состав (пока нет результата) */}
                    {!loadingDraft && !draftError && draft && draft.eligible && !result && (
                        <>
                            {/* Уже отправляли / уже связана PVB */}
                            {draft.already_sent && (
                                <div style={{ padding: '12px 16px', borderRadius: 8, background: 'rgba(245,158,11,0.12)', color: 'var(--color-warning)', marginBottom: 16, fontSize: 14 }}>
                                    <div style={{ fontWeight: 600 }}>
                                        Поставка для этой приёмки уже есть{draft.sent_number ? ` (${draft.sent_number})` : ''}.
                                    </div>
                                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8, cursor: 'pointer' }}>
                                        <input
                                            type="checkbox"
                                            checked={confirmResend}
                                            onChange={e => setConfirmResend(e.target.checked)}
                                            style={{ width: 16, height: 16, accentColor: 'var(--color-accent)', cursor: 'pointer' }}
                                        />
                                        <span style={{ color: 'var(--color-text)' }}>Подтверждаю — создать ещё одну поставку</span>
                                    </label>
                                </div>
                            )}

                            {/* Warnings */}
                            {draft.warnings.length > 0 && (
                                <div style={{ padding: '12px 16px', borderRadius: 8, background: 'rgba(245,158,11,0.1)', color: 'var(--color-warning)', marginBottom: 16, fontSize: 13 }}>
                                    {draft.warnings.map((w, i) => (
                                        <div key={i}>⚠ {w}</div>
                                    ))}
                                </div>
                            )}

                            {/* Шапка поставки */}
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 20 }}>
                                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                    <label style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-text-muted)' }}>Номер (для оператора)</label>
                                    <input
                                        value={number}
                                        onChange={e => setNumber(e.target.value)}
                                        placeholder="V-…"
                                        style={{
                                            padding: '7px 10px', borderRadius: 8,
                                            border: '1px solid var(--color-border)',
                                            background: 'var(--color-bg-card)', color: 'var(--color-text)', fontSize: 14,
                                        }}
                                    />
                                </div>
                                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                                    <label style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-text-muted)' }}>Дата поставки</label>
                                    <input
                                        type="date"
                                        value={submissionDate}
                                        onChange={e => setSubmissionDate(e.target.value)}
                                        style={{
                                            padding: '7px 10px', borderRadius: 8,
                                            border: '1px solid var(--color-border)',
                                            background: 'var(--color-bg-card)', color: 'var(--color-text)', fontSize: 14,
                                        }}
                                    />
                                </div>
                                <div style={{ gridColumn: '1 / -1', display: 'flex', flexDirection: 'column', gap: 4 }}>
                                    <label style={{ fontSize: 12, fontWeight: 500, color: 'var(--color-text-muted)' }}>Комментарий</label>
                                    <textarea
                                        value={notes}
                                        onChange={e => setNotes(e.target.value)}
                                        rows={2}
                                        placeholder="Дополнительные комментарии..."
                                        style={{
                                            width: '100%', padding: '8px 10px', borderRadius: 8,
                                            border: '1px solid var(--color-border)',
                                            background: 'var(--color-bg-card)', color: 'var(--color-text)',
                                            fontSize: 14, resize: 'vertical', boxSizing: 'border-box',
                                        }}
                                    />
                                </div>
                            </div>

                            {/* Нестыковки кратности: наша ≠ у Натали (обе стороны известны) */}
                            {mismatches.length > 0 && (
                                <div style={{ border: '1px solid var(--color-border)', borderRadius: 8, background: 'rgba(245,158,11,0.08)', marginBottom: 16 }}>
                                    <button
                                        onClick={() => setMismatchOpen(o => !o)}
                                        style={{
                                            width: '100%', display: 'flex', alignItems: 'center', gap: 8,
                                            padding: '10px 14px', background: 'none', border: 'none', cursor: 'pointer',
                                            fontSize: 13, fontWeight: 600, color: 'var(--color-warning)', textAlign: 'left',
                                        }}
                                        aria-expanded={mismatchOpen}
                                    >
                                        <span>⚠ Нестыковки кратности: {formatNumber(mismatches.length, 0)} {mismatches.length === 1 ? 'позиция' : 'позиций'}</span>
                                        <span style={{ marginLeft: 'auto', color: 'var(--color-text-muted)', fontWeight: 400, fontSize: 12 }}>
                                            {mismatchOpen ? 'свернуть ▲' : 'показать ▼'}
                                        </span>
                                    </button>
                                    {mismatchOpen && (
                                        <div style={{ borderTop: '1px solid var(--color-border)', padding: '4px 14px 10px' }}>
                                            {mismatches.map(it => {
                                                const ours = it.units_ours;
                                                const natali = it.units_natali;
                                                if (ours == null || natali == null) return null;
                                                const current = parseInt(packOf(it.barcode).units, 10);
                                                return (
                                                    <div
                                                        key={it.barcode}
                                                        style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', fontSize: 13, flexWrap: 'wrap' }}
                                                    >
                                                        <span style={{ minWidth: 0 }}>
                                                            <strong>{it.article_seller || it.name || it.barcode}</strong>
                                                            <span style={{ color: 'var(--color-text-muted)', fontFamily: 'monospace', fontSize: 12, marginLeft: 6 }}>{it.barcode}</span>
                                                        </span>
                                                        <span style={{ whiteSpace: 'nowrap' }}>
                                                            наша: <strong>{formatNumber(ours, 0)}</strong>
                                                            <span style={{ color: 'var(--color-text-muted)' }}> · </span>
                                                            у Натали: <strong>{formatNumber(natali, 0)}</strong>
                                                        </span>
                                                        <span style={{ display: 'flex', gap: 6, marginLeft: 'auto' }}>
                                                            <button
                                                                className={`btn btn-sm ${current === ours ? 'btn-primary' : 'btn-secondary'}`}
                                                                style={{ padding: '2px 8px', fontSize: 12 }}
                                                                onClick={() => applyUnits(it, ours)}
                                                                title={`Коробом по нашей кратности: ${formatNumber(ours, 0)} шт`}
                                                            >
                                                                взять нашу
                                                            </button>
                                                            <button
                                                                className={`btn btn-sm ${current === natali ? 'btn-primary' : 'btn-secondary'}`}
                                                                style={{ padding: '2px 8px', fontSize: 12 }}
                                                                onClick={() => applyUnits(it, natali)}
                                                                title={`Коробом по кратности Натали: ${formatNumber(natali, 0)} шт`}
                                                            >
                                                                взять Натали
                                                            </button>
                                                        </span>
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    )}
                                </div>
                            )}

                            {/* Вкладки: Состав | Массовое редактирование (переключение не теряет правок) */}
                            {hasItems && (
                                <div style={{ display: 'flex', gap: 4, marginBottom: 12 }}>
                                    {([
                                        { key: 'compose' as const, label: '📋 Состав' },
                                        { key: 'bulk' as const, label: `📎 Массовое редактирование${bulkFilledCount > 0 ? ` (${formatNumber(bulkFilledCount, 0)})` : ''}` },
                                    ]).map(m => (
                                        <button
                                            key={m.key}
                                            onClick={() => setTab(m.key)}
                                            style={{
                                                padding: '6px 16px', borderRadius: 8, fontSize: 13, fontWeight: 500, cursor: 'pointer',
                                                border: tab === m.key ? '2px solid var(--color-accent)' : '1px solid var(--color-border)',
                                                background: tab === m.key ? 'rgba(0,113,227,0.08)' : 'var(--color-bg)',
                                                color: tab === m.key ? 'var(--color-accent)' : 'var(--color-text)',
                                            }}
                                        >
                                            {m.label}
                                        </button>
                                    ))}
                                </div>
                            )}

                            {/* Состав (редактируемая упаковка: коробом / россыпью) */}
                            {tab === 'compose' && (
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 10, flexWrap: 'wrap' }}>
                                    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                                        Состав поставки
                                    </div>
                                    {hasItems && (
                                        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                                            <button
                                                className="btn btn-secondary btn-sm"
                                                onClick={exportExcel}
                                                title="Выгрузить текущую раскладку в Excel (весь состав)"
                                            >
                                                📥 Excel
                                            </button>
                                            <button
                                                className="btn btn-secondary btn-sm"
                                                onClick={applyKnownMultiplicity}
                                                disabled={knownCount === 0}
                                                title="Коробом по известной кратности (Натали / наша) для всех строк, где она есть"
                                            >
                                                Коробом, где известна кратность{knownCount > 0 ? ` (${formatNumber(knownCount, 0)})` : ''}
                                            </button>
                                            <button className="btn btn-secondary btn-sm" onClick={applyAllLoose}>
                                                Всё россыпью
                                            </button>
                                        </div>
                                    )}
                                </div>
                            )}

                            {/* Поиск + сегмент-фильтр (правки при фильтрации не теряются — state по ШК) */}
                            {tab === 'compose' && hasItems && (
                                <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
                                    <input
                                        value={search}
                                        onChange={e => setSearch(e.target.value)}
                                        placeholder="Поиск по ШК или артикулу…"
                                        style={{
                                            flex: '1 1 240px', minWidth: 200, padding: '7px 10px', borderRadius: 8,
                                            border: '1px solid var(--color-border)',
                                            background: 'var(--color-bg-card)', color: 'var(--color-text)', fontSize: 14,
                                        }}
                                    />
                                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                                        {segments.map(s => (
                                            <button
                                                key={s.key}
                                                className={`btn btn-sm ${segment === s.key ? 'btn-primary' : 'btn-secondary'}`}
                                                style={{ padding: '3px 10px', fontSize: 12 }}
                                                onClick={() => setSegment(s.key)}
                                            >
                                                {s.label}{s.key === 'all' ? '' : ` (${formatNumber(s.count, 0)})`}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Итоги сверху: Σ коробов / Σ штук россыпью */}
                            {hasItems && (
                                <div style={{ display: 'flex', gap: 24, marginBottom: 10, fontSize: 14, flexWrap: 'wrap' }}>
                                    <span>Позиций: <strong>{formatNumber(items.length, 0)}</strong></span>
                                    <span>Коробов: <strong>{formatNumber(totalBoxes, 0)}</strong></span>
                                    <span>Россыпью: <strong>{formatNumber(totalLoose, 0)}</strong> шт</span>
                                    <span>
                                        Отправляем: <strong>{formatNumber(totalSendQty, 0)}</strong>
                                        {totalSendQty !== totalReceiptQty && (
                                            <span style={{ color: 'var(--color-text-muted)' }}> из {formatNumber(totalReceiptQty, 0)}</span>
                                        )} шт
                                    </span>
                                    {invalidCount > 0 && (
                                        <span style={{ color: 'var(--color-danger)' }}>
                                            ⚠ Проверьте «кол-во» / «шт в коробе» / «коробов» в {formatNumber(invalidCount, 0)} стр.
                                        </span>
                                    )}
                                    {invalidCount === 0 && totalSendQty === 0 && (
                                        <span style={{ color: 'var(--color-danger)' }}>
                                            ⚠ Отправляемое количество нулевое — поставку создать нельзя
                                        </span>
                                    )}
                                </div>
                            )}

                            {/* Панель массовых операций по выбранным */}
                            {tab === 'compose' && selected.size > 0 && (
                                <div style={{
                                    display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
                                    padding: '8px 12px', marginBottom: 10, borderRadius: 8,
                                    border: '1px solid var(--color-border)', background: 'var(--color-bg-card)', fontSize: 13,
                                }}>
                                    <span style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>
                                        Выбрано: {formatNumber(selected.size, 0)}
                                    </span>
                                    <button className="btn btn-secondary btn-sm" onClick={applySelectedBox}>
                                        Выбранные → коробом
                                    </button>
                                    <button className="btn btn-secondary btn-sm" onClick={applySelectedLoose}>
                                        → россыпью
                                    </button>
                                    <span style={{ display: 'flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap' }}>
                                        <span style={{ color: 'var(--color-text-muted)' }}>шт в коробе:</span>
                                        <input
                                            type="number"
                                            min={2}
                                            value={bulkUnits}
                                            onChange={e => setBulkUnits(e.target.value)}
                                            placeholder="шт"
                                            style={{
                                                width: 64, padding: '3px 6px', borderRadius: 8, fontSize: 13,
                                                border: '1px solid var(--color-border)',
                                                background: 'var(--color-bg)', color: 'var(--color-text)',
                                            }}
                                        />
                                        <button
                                            className="btn btn-secondary btn-sm"
                                            onClick={applySelectedUnits}
                                            disabled={!bulkUnitsOk}
                                            title="Коробом с этой кратностью, коробов = максимум"
                                        >
                                            применить к выбранным
                                        </button>
                                    </span>
                                    <button
                                        className="btn btn-secondary btn-sm"
                                        style={{ marginLeft: 'auto' }}
                                        onClick={() => setSelected(new Set())}
                                    >
                                        снять выбор
                                    </button>
                                </div>
                            )}

                            {!hasItems && (
                                <div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--color-text-muted)', fontSize: 14 }}>
                                    В приёмке нет позиций — поставку создать нельзя
                                </div>
                            )}
                            {hasItems && tab === 'compose' && (
                                <div style={{ overflowX: 'auto', border: '1px solid var(--color-border)', borderRadius: 8 }}>
                                    <table style={{ width: '100%', minWidth: 960, borderCollapse: 'collapse', fontSize: 13, tableLayout: 'fixed' }}>
                                        <colgroup>
                                            <col style={{ width: 36 }} />
                                            <col style={{ width: 170 }} />
                                            <col />
                                            <col style={{ width: 120 }} />
                                            <col style={{ width: 160 }} />
                                            <col style={{ width: 90 }} />
                                            <col style={{ width: 90 }} />
                                            <col style={{ width: 190 }} />
                                        </colgroup>
                                        <thead>
                                            <tr style={{ background: 'var(--color-bg-card)', textAlign: 'left' }}>
                                                <th style={{ padding: '8px 10px' }}>
                                                    <input
                                                        type="checkbox"
                                                        checked={allVisibleSelected}
                                                        onChange={toggleSelectAllVisible}
                                                        title="Выбрать все видимые строки"
                                                        style={{ width: 15, height: 15, accentColor: 'var(--color-accent)', cursor: 'pointer' }}
                                                    />
                                                </th>
                                                <th style={{ padding: '8px 10px', fontWeight: 600 }}>ШК</th>
                                                <th style={{ padding: '8px 10px', fontWeight: 600 }}>Наименование</th>
                                                <th style={{ padding: '8px 10px', fontWeight: 600, textAlign: 'right' }} title="Отправляемое количество (не больше состава приёмки; 0 — строка не едет)">Кол-во</th>
                                                <th style={{ padding: '8px 10px', fontWeight: 600 }}>Упаковка</th>
                                                <th style={{ padding: '8px 10px', fontWeight: 600 }} title="Штук в коробе">Шт в коробе</th>
                                                <th style={{ padding: '8px 10px', fontWeight: 600 }} title="Коробов (остаток едет россыпью)">Коробов</th>
                                                <th style={{ padding: '8px 10px', fontWeight: 600 }}>Разбивка</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {visibleItems.length === 0 && (
                                                <tr style={{ borderTop: '1px solid var(--color-border)' }}>
                                                    <td colSpan={8} style={{ padding: '20px 10px', textAlign: 'center', color: 'var(--color-text-muted)' }}>
                                                        Ничего не найдено по текущему фильтру
                                                    </td>
                                                </tr>
                                            )}
                                            {visibleItems.map(it => {
                                                const c = rowCalc(it.barcode, it.qty);
                                                const r = packOf(it.barcode);
                                                return (
                                                    <tr key={it.barcode} style={{ borderTop: '1px solid var(--color-border)' }}>
                                                        <td style={{ padding: '8px 10px' }}>
                                                            <input
                                                                type="checkbox"
                                                                checked={selected.has(it.barcode)}
                                                                onChange={() => toggleSelect(it.barcode)}
                                                                style={{ width: 15, height: 15, accentColor: 'var(--color-accent)', cursor: 'pointer' }}
                                                            />
                                                        </td>
                                                        <td style={{ padding: '8px 10px', fontFamily: 'monospace', fontSize: 12, overflow: 'hidden' }}>
                                                            {it.barcode}
                                                            {/* ШК короба — при короб-режиме: карта Натали либо выведенный
                                                                GTIN-14. Подпись — тултипом: текстом она вылезала за колонку
                                                                и наезжала на «Наименование». */}
                                                            {r.boxMode && it.box_barcode && (
                                                                <div
                                                                    style={{
                                                                        color: 'var(--color-text-muted)', fontSize: 11, marginTop: 2,
                                                                        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                                                                    }}
                                                                    title={it.box_barcode_source === 'natali'
                                                                        ? `${it.box_barcode} — ШК короба из карты Натали`
                                                                        : `${it.box_barcode} — ШК короба выведен нами (GTIN-14), у Натали карточки короба ещё нет`}
                                                                >
                                                                    📦 {it.box_barcode}
                                                                    {it.box_barcode_source !== 'natali' && (
                                                                        <span style={{ fontFamily: 'Inter, sans-serif' }}> *</span>
                                                                    )}
                                                                </div>
                                                            )}
                                                        </td>
                                                        <td style={{ padding: '8px 10px', overflowWrap: 'break-word' }}>
                                                            {it.name || '—'}
                                                            {it.pack_source !== 'none' && (
                                                                <span
                                                                    className={`badge ${it.pack_source === 'natali' ? 'badge-info' : 'badge-success'}`}
                                                                    style={{ fontSize: 10, marginLeft: 6 }}
                                                                    title={it.pack_source === 'natali'
                                                                        ? `Кратность из карты Натали: короб ${formatNumber(it.units_per_box ?? 0, 0)} шт`
                                                                        : `Наша кратность отгрузки: короб ${formatNumber(it.units_per_box ?? 0, 0)} шт`}
                                                                >
                                                                    {it.pack_source === 'natali' ? 'Натали' : 'наша'}
                                                                </span>
                                                            )}
                                                            {it.article_seller && (
                                                                <div style={{ color: 'var(--color-text-muted)', fontSize: 12, marginTop: 1 }}>
                                                                    {it.article_seller}
                                                                </div>
                                                            )}
                                                        </td>
                                                        <td style={{ padding: '8px 10px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                                                            <input
                                                                type="number"
                                                                min={0}
                                                                max={it.qty}
                                                                value={r.qty}
                                                                // Смена кол-ва при коробе → «коробов» пересчитывается на максимум.
                                                                onChange={e => {
                                                                    const v = e.target.value;
                                                                    const n = parseInt(v, 10);
                                                                    const ok = Number.isFinite(n) && n >= 0 && n <= it.qty;
                                                                    setRow(it.barcode, r.boxMode && ok
                                                                        ? { qty: v, boxes: maxBoxesStr(n, r.units) }
                                                                        : { qty: v });
                                                                }}
                                                                title={`Отправляемое количество (из ${formatNumber(it.qty, 0)} в приёмке; 0 — строка не едет)`}
                                                                style={{
                                                                    width: 62, padding: '3px 6px', borderRadius: 8, fontSize: 13,
                                                                    textAlign: 'right',
                                                                    border: `1px solid ${c.invalidQty ? 'var(--color-danger)' : 'var(--color-border)'}`,
                                                                    background: 'var(--color-bg-card)', color: 'var(--color-text)',
                                                                }}
                                                            />
                                                            <div style={{ color: 'var(--color-text-muted)', fontSize: 11, marginTop: 2 }}>
                                                                из {formatNumber(it.qty, 0)}
                                                            </div>
                                                        </td>
                                                        <td style={{ padding: '8px 10px', whiteSpace: 'nowrap' }}>
                                                            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                                                <button
                                                                    className={`btn btn-sm ${r.boxMode ? 'btn-primary' : 'btn-secondary'}`}
                                                                    style={{ padding: '2px 8px', fontSize: 12 }}
                                                                    onClick={() => {
                                                                        // При включении короба без units — подставить prefill; коробов — максимум.
                                                                        const units = r.units || (it.units_per_box != null ? String(it.units_per_box) : '');
                                                                        setRow(it.barcode, {
                                                                            boxMode: true,
                                                                            units,
                                                                            boxes: r.boxes || maxBoxesStr(effQty(it, r), units),
                                                                        });
                                                                    }}
                                                                >
                                                                    короб
                                                                </button>
                                                                <button
                                                                    className={`btn btn-sm ${!r.boxMode ? 'btn-primary' : 'btn-secondary'}`}
                                                                    style={{ padding: '2px 8px', fontSize: 12 }}
                                                                    onClick={() => setRow(it.barcode, { boxMode: false })}
                                                                >
                                                                    россыпь
                                                                </button>
                                                            </div>
                                                        </td>
                                                        <td style={{ padding: '8px 10px' }}>
                                                            {r.boxMode ? (
                                                                <input
                                                                    type="number"
                                                                    min={2}
                                                                    value={r.units}
                                                                    // Смена кратности → «коробов» пересчитывается на максимум.
                                                                    onChange={e => setRow(it.barcode, {
                                                                        units: e.target.value,
                                                                        boxes: maxBoxesStr(effQty(it, r), e.target.value),
                                                                    })}
                                                                    placeholder="шт"
                                                                    title="Штук в коробе"
                                                                    style={{
                                                                        width: 62, padding: '3px 6px', borderRadius: 8, fontSize: 13,
                                                                        border: `1px solid ${c.invalidUnits ? 'var(--color-danger)' : 'var(--color-border)'}`,
                                                                        background: 'var(--color-bg-card)', color: 'var(--color-text)',
                                                                    }}
                                                                />
                                                            ) : (
                                                                <span style={{ color: 'var(--color-text-muted)' }}>—</span>
                                                            )}
                                                        </td>
                                                        <td style={{ padding: '8px 10px' }}>
                                                            {r.boxMode ? (
                                                                <input
                                                                    type="number"
                                                                    min={0}
                                                                    max={Number.isFinite(c.unitsNum) && c.unitsNum >= 2 ? Math.floor(c.sendQty / c.unitsNum) : undefined}
                                                                    value={r.boxes}
                                                                    onChange={e => setRow(it.barcode, { boxes: e.target.value })}
                                                                    placeholder="кор."
                                                                    title="Коробов (остаток qty − коробов × шт едет россыпью)"
                                                                    style={{
                                                                        width: 62, padding: '3px 6px', borderRadius: 8, fontSize: 13,
                                                                        border: `1px solid ${c.invalidBoxes ? 'var(--color-danger)' : 'var(--color-border)'}`,
                                                                        background: 'var(--color-bg-card)', color: 'var(--color-text)',
                                                                    }}
                                                                />
                                                            ) : (
                                                                <span style={{ color: 'var(--color-text-muted)' }}>—</span>
                                                            )}
                                                        </td>
                                                        <td style={{ padding: '8px 10px', whiteSpace: 'nowrap', fontSize: 12 }}>
                                                            {c.invalid ? (
                                                                <span style={{ color: 'var(--color-danger)' }}>
                                                                    {c.invalidQty
                                                                        ? `кол-во: 0–${formatNumber(it.qty, 0)}`
                                                                        : c.invalidUnits
                                                                            ? 'укажите шт в коробе'
                                                                            : `коробов: 0–${formatNumber(Math.floor(c.sendQty / c.unitsNum), 0)}`}
                                                                </span>
                                                            ) : c.sendQty === 0 ? (
                                                                <span style={{ color: 'var(--color-text-muted)' }}>не отправляется (0 шт)</span>
                                                            ) : c.boxMode ? (
                                                                <>
                                                                    <strong>{formatNumber(c.boxes, 0)}</strong> кор. × {formatNumber(c.unitsNum, 0)} шт
                                                                    {c.rest > 0 && (
                                                                        <span style={{ color: 'var(--color-warning)' }}> + {formatNumber(c.rest, 0)} россыпью</span>
                                                                    )}
                                                                </>
                                                            ) : (
                                                                <span style={{ color: 'var(--color-text-muted)' }}>{formatNumber(c.rest, 0)} шт россыпью</span>
                                                            )}
                                                        </td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                            )}

                            {/* ═══ Вкладка «Массовое редактирование»: грид со вставкой из Excel ═══ */}
                            {hasItems && tab === 'bulk' && (
                                <>
                                    <div style={{
                                        padding: 10, marginBottom: 12, fontSize: 13, color: 'var(--color-text-muted)',
                                        background: 'var(--color-bg-card)', borderRadius: 8,
                                    }}>
                                        Вставьте из Excel в любой инпут ШК: <strong>ШК</strong>, <strong>короб/россыпь</strong> (можно
                                        «к»/«р»), <strong>шт в коробе</strong>, <strong>кол-во</strong>. Шт в коробе и кол-во опциональны
                                        (без них — текущие значения строки). Разделитель — Tab (Excel) или «;». Незатронутые строки
                                        состава не меняются.
                                    </div>

                                    <div style={{ overflowX: 'auto', border: '1px solid var(--color-border)', borderRadius: 8 }}>
                                        <table style={{ width: '100%', minWidth: 860, borderCollapse: 'collapse', fontSize: 13 }}>
                                            <thead>
                                                <tr style={{ background: 'var(--color-bg-card)', textAlign: 'left' }}>
                                                    <th style={{ padding: '8px 10px', width: 36, textAlign: 'center', color: 'var(--color-text-muted)' }}>#</th>
                                                    <th style={{ padding: '8px 10px', fontWeight: 600, minWidth: 170 }}>ШК</th>
                                                    <th style={{ padding: '8px 10px', fontWeight: 600 }}>Наименование</th>
                                                    <th style={{ padding: '8px 10px', fontWeight: 600, textAlign: 'right', width: 90 }} title="Состав приёмки (максимум для «кол-во»)">Доступно</th>
                                                    <th style={{ padding: '8px 10px', fontWeight: 600, width: 130 }} title="«короб» / «россыпь» (или «к» / «р»)">Упаковка</th>
                                                    <th style={{ padding: '8px 10px', fontWeight: 600, width: 110 }} title="Штук в коробе (опционально — текущее / prefill-кратность)">Шт в коробе</th>
                                                    <th style={{ padding: '8px 10px', fontWeight: 600, width: 110 }} title="Отправляемое кол-во (опционально — текущее)">Кол-во</th>
                                                    <th style={{ padding: '8px 10px', width: 44, textAlign: 'center' }}></th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {bulkRows.map((row, i) => {
                                                    const c = checkBulkRow(row);
                                                    const cellInput: CSSProperties = {
                                                        width: '100%', padding: '5px 8px', borderRadius: 8, fontSize: 13,
                                                        border: '1px solid var(--color-border)',
                                                        background: 'var(--color-bg-card)', color: 'var(--color-text)',
                                                        boxSizing: 'border-box',
                                                    };
                                                    return (
                                                        <tr
                                                            key={i}
                                                            style={{
                                                                borderTop: '1px solid var(--color-border)',
                                                                background: c.notFound ? 'rgba(239,68,68,0.06)' : undefined,
                                                            }}
                                                        >
                                                            <td style={{ padding: '6px 10px', textAlign: 'center', fontSize: 12, color: 'var(--color-text-muted)' }}>{i + 1}</td>
                                                            <td style={{ padding: '6px 10px' }}>
                                                                <input
                                                                    value={row.barcode}
                                                                    onChange={e => updateBulkRow(i, 'barcode', e.target.value)}
                                                                    onPaste={handleBulkPaste(i)}
                                                                    placeholder="ШК"
                                                                    autoComplete="off"
                                                                    style={{
                                                                        ...cellInput, fontFamily: 'monospace', fontSize: 12,
                                                                        borderColor: c.notFound ? 'var(--color-danger)' : 'var(--color-border)',
                                                                        color: c.notFound ? 'var(--color-danger)' : 'var(--color-text)',
                                                                    }}
                                                                />
                                                            </td>
                                                            <td style={{ padding: '6px 10px', fontSize: 12, overflowWrap: 'break-word' }}>
                                                                {c.notFound ? (
                                                                    <span style={{ color: 'var(--color-danger)' }}>ШК не найден в составе приёмки</span>
                                                                ) : c.it ? (
                                                                    <>
                                                                        {c.it.name || '—'}
                                                                        {c.it.article_seller && (
                                                                            <div style={{ color: 'var(--color-text-muted)', fontSize: 11, marginTop: 1 }}>
                                                                                {c.it.article_seller}
                                                                            </div>
                                                                        )}
                                                                    </>
                                                                ) : ''}
                                                            </td>
                                                            <td style={{ padding: '6px 10px', textAlign: 'right', fontSize: 12, color: c.qtyInvalid ? 'var(--color-danger)' : 'var(--color-text-muted)' }}>
                                                                {c.it ? formatNumber(c.it.qty, 0) : c.filled ? '—' : ''}
                                                            </td>
                                                            <td style={{ padding: '6px 10px' }}>
                                                                <input
                                                                    value={row.pack}
                                                                    onChange={e => updateBulkRow(i, 'pack', e.target.value)}
                                                                    placeholder="короб / россыпь"
                                                                    autoComplete="off"
                                                                    style={{
                                                                        ...cellInput,
                                                                        borderColor: c.packInvalid ? 'var(--color-danger)' : 'var(--color-border)',
                                                                    }}
                                                                />
                                                            </td>
                                                            <td style={{ padding: '6px 10px' }}>
                                                                <input
                                                                    type="number"
                                                                    min={2}
                                                                    value={row.units}
                                                                    onChange={e => updateBulkRow(i, 'units', e.target.value)}
                                                                    placeholder={c.boxMode ? 'шт' : '—'}
                                                                    autoComplete="off"
                                                                    style={{
                                                                        ...cellInput, textAlign: 'right',
                                                                        borderColor: c.unitsInvalid ? 'var(--color-danger)' : 'var(--color-border)',
                                                                    }}
                                                                />
                                                            </td>
                                                            <td style={{ padding: '6px 10px' }}>
                                                                <input
                                                                    type="number"
                                                                    min={0}
                                                                    max={c.it?.qty}
                                                                    value={row.qty}
                                                                    onChange={e => updateBulkRow(i, 'qty', e.target.value)}
                                                                    placeholder="тек."
                                                                    autoComplete="off"
                                                                    style={{
                                                                        ...cellInput, textAlign: 'right',
                                                                        borderColor: c.qtyInvalid ? 'var(--color-danger)' : 'var(--color-border)',
                                                                    }}
                                                                />
                                                            </td>
                                                            <td style={{ padding: '6px 10px', textAlign: 'center', fontSize: 12 }}>
                                                                {!c.filled ? '' :
                                                                    c.notFound ? <span style={{ color: 'var(--color-danger)' }}>✗</span> :
                                                                    c.packInvalid ? <span style={{ color: 'var(--color-danger)' }} title="Укажите «короб»/«россыпь» (или «к»/«р»)">к/р?</span> :
                                                                    c.unitsInvalid || c.qtyInvalid ? <span style={{ color: 'var(--color-danger)' }}>!</span> :
                                                                    <span style={{ color: 'var(--color-success)' }}>✓</span>}
                                                            </td>
                                                        </tr>
                                                    );
                                                })}
                                            </tbody>
                                        </table>
                                    </div>

                                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 10 }}>
                                        <button
                                            className="btn btn-primary btn-sm"
                                            onClick={applyBulk}
                                            disabled={bulkValidCount === 0}
                                            title="Применить валидные строки к раскладке состава (ошибочные останутся в гриде)"
                                        >
                                            Применить ({formatNumber(bulkValidCount, 0)})
                                        </button>
                                        <button
                                            className="btn btn-secondary btn-sm"
                                            onClick={clearBulk}
                                            disabled={bulkFilledCount === 0 && !bulkReport}
                                        >
                                            Очистить
                                        </button>
                                        {bulkFilledCount > bulkValidCount && (
                                            <span style={{ fontSize: 13, color: 'var(--color-danger)' }}>
                                                ⚠ Ошибочных строк: {formatNumber(bulkFilledCount - bulkValidCount, 0)}
                                            </span>
                                        )}
                                    </div>

                                    {bulkReport && (
                                        <div style={{ marginTop: 8, fontSize: 13 }}>
                                            <div style={{ color: bulkReport.applied > 0 ? 'var(--color-success)' : 'var(--color-text-muted)' }}>
                                                ✓ Применено строк: {formatNumber(bulkReport.applied, 0)}
                                            </div>
                                            {bulkReport.errors.length > 0 && (
                                                <div style={{ color: 'var(--color-danger)', marginTop: 4 }}>
                                                    <div style={{ fontWeight: 600 }}>
                                                        Не применено ({formatNumber(bulkReport.errors.length, 0)}):
                                                    </div>
                                                    {bulkReport.errors.map((er, i) => (
                                                        <div key={i}>• {er}</div>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </>
                            )}
                        </>
                    )}
                </div>{/* /body */}

                {/* Footer */}
                <div style={{
                    padding: '14px 24px', borderTop: '1px solid var(--color-border)',
                    flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 10,
                }}>
                    {submitError && (
                        <div style={{ padding: '8px 12px', borderRadius: 8, background: 'rgba(239,68,68,0.1)', color: 'var(--color-danger)', fontSize: 13 }}>
                            {submitError}
                        </div>
                    )}
                    <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
                        <button className="btn btn-secondary" onClick={onClose} disabled={submitting}>
                            {result ? 'Закрыть' : 'Отмена'}
                        </button>
                        {!result && draft?.eligible && (
                            <button
                                className="btn btn-primary"
                                onClick={() => doSend(confirmResend)}
                                disabled={submitting || !canSubmit}
                            >
                                {submitting ? 'Отправка...' : 'Создать поставку'}
                            </button>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
