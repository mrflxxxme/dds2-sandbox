/**
 * Совместимость категорий на паллете + категорийный скоуп черновика.
 *
 * «Эффективная категория» товара = ручной override (CategoryOverride, тот же
 * механизм, что группирует воронку) ?? предмет WB (`Nomenclature.subject`) ??
 * «Без категории». Правила (`PalletCategoryCompat`, настройка проекта) работают
 * в СТРОГОМ режиме: при `enabled=true` каждая категория едет ТОЛЬКО своей
 * смешанной BOX-паллетой; `groups` разрешают перечисленным категориям грузиться
 * вместе. Моно-паллеты (≤3 артикулов) правила НЕ трогают (решение юзера).
 *
 * «Класс совместимости» — ключ партиции набора SKU:
 *   • правила выключены → у всех один класс `*` (поведение как раньше);
 *   • категория в группе i → `g:<i>`;
 *   • иначе → `c:<категория>` (только своя категория на паллете).
 *
 * Чистые функции, без I/O — полностью юнит-тестируются.
 */
import type { PalletCategoryCompat } from '@/types/api';

/** Категория товаров без предмета WB и без override. */
export const NO_CATEGORY = 'Без категории';

/** Класс «все совместимы» (правила выключены / не загружены). */
export const COMPAT_CLASS_ANY = '*';

export type CategoryOf = (nm: number) => string;
export type CompatClassOf = (nm: number) => string;

/** Эффективная категория: override ?? subject ?? «Без категории».
 *  `overrides` — карта из GET /refs/category-overrides (ключи JSON — строки). */
export function buildCategoryOf(
    overrides: Record<string, string> | null | undefined,
    subjectOf: (nm: number) => string | null | undefined,
): CategoryOf {
    return (nm: number): string => {
        const ov = overrides?.[String(nm)];
        if (ov && ov.trim()) return ov.trim();
        const subj = subjectOf(nm);
        if (subj && subj.trim()) return subj.trim();
        return NO_CATEGORY;
    };
}

/** Класс совместимости по правилам. Выключенные/пустые правила → всегда `*`. */
export function buildCompatClassOf(
    rules: PalletCategoryCompat | null | undefined,
    categoryOf: CategoryOf,
): CompatClassOf {
    if (!rules || !rules.enabled) return () => COMPAT_CLASS_ANY;
    const groupByCat = new Map<string, number>();
    rules.groups.forEach((g, i) => {
        for (const c of g.categories) {
            const cat = c.trim();
            if (cat && !groupByCat.has(cat)) groupByCat.set(cat, i);
        }
    });
    return (nm: number): string => {
        const cat = categoryOf(nm);
        const gi = groupByCat.get(cat);
        return gi != null ? `g:${gi}` : `c:${cat}`;
    };
}

/** Человекочитаемая подпись класса для бейджей на паллетах/предброни. */
export function classLabelOf(cls: string, rules: PalletCategoryCompat | null | undefined): string {
    if (cls === COMPAT_CLASS_ANY) return '';
    if (cls.startsWith('g:')) {
        const i = Number(cls.slice(2));
        const g = rules?.groups?.[i];
        const name = g?.name?.trim();
        if (name) return name;
        const cats = g?.categories ?? [];
        return cats.length ? cats.join(' + ') : `Группа ${i + 1}`;
    }
    return cls.startsWith('c:') ? cls.slice(2) : cls;
}

/** Предикат категорийного скоупа черновика. null/[] = скоупа нет (всё проходит). */
export function inScopeOf(
    scope: string[] | null | undefined,
    categoryOf: CategoryOf,
): (nm: number) => boolean {
    const set = new Set((scope ?? []).map((s) => s.trim()).filter(Boolean));
    if (set.size === 0) return () => true;
    return (nm: number) => set.has(categoryOf(nm));
}

/**
 * ВЛАДЕНИЕ КАТЕГОРИЕЙ: срез статей расчёта по скоупу черновика.
 *  • свой скоуп есть → только свои категории (позитивный фильтр, как раньше);
 *  • скоупа нет → МИНУС категории живых категорийных черновиков
 *    (`foreignScopeCategories`) — их планируют владельцы, бесскоупный черновик
 *    не дублирует план даже при свободном ФФ-стоке (прод 2026-07-22: швабра
 *    EP-800 — 402 обещанных из 282 физических двумя черновиками).
 * Обе стороны матчат категорию ОДНОЙ функцией принадлежности (`inScopeOf`) —
 * override/предмет резолвятся одинаково. Чистая.
 */
export function filterArticlesByCategoryScope<T extends { nm_id: number }>(
    articles: T[],
    scope: string[] | null | undefined,
    categoryOf: CategoryOf,
    foreignScopeCategories?: ReadonlySet<string> | null,
): T[] {
    if (scope?.length) {
        const inSc = inScopeOf(scope, categoryOf);
        return articles.filter((a) => inSc(a.nm_id));
    }
    if (foreignScopeCategories && foreignScopeCategories.size > 0) {
        const inForeign = inScopeOf([...foreignScopeCategories], categoryOf);
        return articles.filter((a) => !inForeign(a.nm_id));
    }
    return articles;
}
