/**
 * Кросс-черновичный резерв и категорийный скоуп черновика — чистые хелперы.
 *
 * Параллельные черновики (в т.ч. категорийные) резервируют сток друг от друга:
 * агрегат `GET /assembly/drafts/reserved` (barcode → {ff_id → qty}) вычитается из
 * `rf_stocks[ff].available` потребности ДО любых расчётов (fill / матрица /
 * предбронь наследуют автоматически). После commit черновик исчезает, а созданные
 * заявки учитываются существующей механикой «в сборке» — резерв бесшовно переезжает.
 *
 * Все функции возвращают НОВЫЕ объекты (вход не мутируется) и чистые.
 */
import type { AssemblyDraft, AssemblyDraftRow, StockNeedArticle, StockNeedArticleRfStock } from '@/types/api';
import { allocatePairs } from '@/lib/utils/assemblyPreview';

/** Резерв: barcode → { ff_id → qty }. */
export type DraftReserveMap = Record<string, Record<string, number>>;

type ArticleSlice = Pick<StockNeedArticle, 'barcode' | 'rf_stocks'>;

/** Вычесть резерв других черновиков из доступного ФФ каждой статьи потребности.
 *  `available` не опускается ниже 0; статьи/склады без резерва возвращаются как есть. */
export function subtractReserveFromArticles<T extends ArticleSlice>(
    articles: T[],
    reserved: DraftReserveMap | null | undefined,
): T[] {
    if (!reserved || Object.keys(reserved).length === 0) return articles;
    return articles.map((a) => {
        const res = a.barcode ? reserved[a.barcode] : undefined;
        if (!res || !a.rf_stocks) return a;
        let touched = false;
        const rf: Record<number, StockNeedArticleRfStock> = {};
        for (const [ff, s] of Object.entries(a.rf_stocks)) {
            const held = res[ff] || 0;
            if (held > 0) {
                touched = true;
                rf[Number(ff)] = { ...s, available: Math.max(0, (s.available || 0) - held) };
            } else {
                rf[Number(ff)] = s;
            }
        }
        return touched ? { ...a, rf_stocks: rf } : a;
    });
}

/** Σ зарезервировано всеми другими черновиками (для бейджа «занято»). */
export function reservedTotal(reserved: DraftReserveMap | null | undefined): number {
    if (!reserved) return 0;
    let sum = 0;
    for (const byFf of Object.values(reserved)) {
        for (const q of Object.values(byFf)) sum += q || 0;
    }
    return sum;
}

/** Σ зарезервировано по конкретному (barcode, ff) — для 🔒-подстроки в ячейке «На ФФ». */
export function reservedAt(reserved: DraftReserveMap | null | undefined, barcode: string, ffId: number): number {
    return reserved?.[barcode]?.[String(ffId)] || 0;
}

/** Σ зарезервировано по баркоду на всех ФФ. */
export function reservedForBarcode(reserved: DraftReserveMap | null | undefined, barcode: string): number {
    const byFf = reserved?.[barcode];
    if (!byFf) return 0;
    return Object.values(byFf).reduce((s, q) => s + (q || 0), 0);
}

/** Ограничить доступность одним ФФ-источником (скоуп-черновик «только Газпром»):
 *  available прочих складов зануляется (сток/показ не трогаем — только источник). */
export function restrictArticlesToFf<T extends Pick<StockNeedArticle, 'rf_stocks'>>(
    articles: T[],
    ffId: number | null | undefined,
): T[] {
    if (ffId == null) return articles;
    return articles.map((a) => {
        if (!a.rf_stocks) return a;
        const rf: Record<number, StockNeedArticleRfStock> = {};
        for (const [ff, s] of Object.entries(a.rf_stocks)) {
            rf[Number(ff)] = Number(ff) === ffId ? s : { ...s, available: 0 };
        }
        return { ...a, rf_stocks: rf };
    });
}

/** Разрезать строки по предикату скоупа (nm ∈ выбранные категории). */
export function splitRowsByScope(
    rows: AssemblyDraftRow[],
    inScope: (nm: number) => boolean,
): { scoped: AssemblyDraftRow[]; rest: AssemblyDraftRow[] } {
    const scoped: AssemblyDraftRow[] = [];
    const rest: AssemblyDraftRow[] = [];
    for (const r of rows) (inScope(r.nm_id) ? scoped : rest).push(r);
    return { scoped, rest };
}

/** Разрезать строку на порцию одного ФФ и остаток (баланс Σsrc==Σtgt обеих
 *  половин by construction — через allocatePairs, как commit). null-половина =
 *  пусто. Для строки без порций этого ФФ вернёт { ff: null, rest: row }. */
function splitRowByFf(row: AssemblyDraftRow, ffId: number): { ff: AssemblyDraftRow | null; rest: AssemblyDraftRow | null } {
    const key = String(ffId);
    if (!(row.src[key] > 0)) return { ff: null, rest: row };
    const onlyFf = Object.keys(row.src).length === 1;
    if (onlyFf) return { ff: row, rest: null };
    const mk = (): AssemblyDraftRow => ({ ...row, src: {}, tgt: {} });
    const ffRow = mk();
    const restRow = mk();
    for (const [pairKey, q] of allocatePairs(row.src, row.tgt)) {
        if ((q || 0) <= 0) continue;
        const sep = pairKey.indexOf('::');
        const ff = pairKey.slice(0, sep);
        const wb = pairKey.slice(sep + 2);
        const target = ff === key ? ffRow : restRow;
        target.src[ff] = (target.src[ff] || 0) + q;
        target.tgt[wb] = (target.tgt[wb] || 0) + q;
    }
    const has = (r: AssemblyDraftRow) => Object.keys(r.tgt).length > 0;
    return { ff: has(ffRow) ? ffRow : null, rest: has(restRow) ? restRow : null };
}

/** Carve скоуп-категорий из ОСНОВНОГО черновика при создании категорийного:
 *  строки rows/prebook выбранных категорий переезжают в новый черновик (не
 *  задваиваем план), остальное остаётся байт-в-байт. При заданном `ffId`
 *  (скоуп «только один ФФ») переезжают ТОЛЬКО порции этого ФФ — порции категории
 *  с других складов остаются в основном черновике (иначе в категорийный
 *  «только натали» затекал Газпром). handed_units не трогаем (замороженные
 *  снимки). Чистая: возвращает обе половины. */
export function carveScopeFromDraft(
    draft: Pick<AssemblyDraft['distribution'], 'rows' | 'prebook'>,
    inScope: (nm: number) => boolean,
    ffId?: number | null,
): {
    movedRows: AssemblyDraftRow[];
    movedPrebook: AssemblyDraftRow[];
    restRows: AssemblyDraftRow[];
    restPrebook: AssemblyDraftRow[];
    movedUnits: number;
} {
    const carve = (list: AssemblyDraftRow[]): { moved: AssemblyDraftRow[]; rest: AssemblyDraftRow[] } => {
        const { scoped, rest } = splitRowsByScope(list, inScope);
        if (ffId == null) return { moved: scoped, rest };
        const moved: AssemblyDraftRow[] = [];
        for (const r of scoped) {
            const parts = splitRowByFf(r, ffId);
            if (parts.ff) moved.push(parts.ff);
            if (parts.rest) rest.push(parts.rest);
        }
        return { moved, rest };
    };
    const rows = carve(draft.rows ?? []);
    const prebook = carve(draft.prebook ?? []);
    const sumTgt = (rs: AssemblyDraftRow[]) =>
        rs.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);
    return {
        movedRows: rows.moved,
        movedPrebook: prebook.moved,
        restRows: rows.rest,
        restPrebook: prebook.rest,
        movedUnits: sumTgt(rows.moved) + sumTgt(prebook.moved),
    };
}
