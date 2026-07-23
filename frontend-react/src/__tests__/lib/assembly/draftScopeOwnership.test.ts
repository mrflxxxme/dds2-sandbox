/**
 * Владение категорией (прод-кейс 2026-07-22: divan_darkblue и швабра EP-800 —
 * категорийный черновик планирует категорию, а бесскоупный ДОПОЛНИТЕЛЬНО держит
 * её же строки → 402 обещанных из 282 физических): категория живого
 * категорийного черновика принадлежит ЕМУ — черновик без скоупа её не планирует
 * даже при свободном ФФ-стоке. Матчинг категории — той же функцией
 * принадлежности, что позитивный скоуп (`inScopeOf`).
 */
import { describe, expect, it } from 'vitest';
import { buildCategoryOf, filterArticlesByCategoryScope } from '@/lib/assembly/categoryCompat';

const categoryOf = buildCategoryOf(
    { '4': 'Швабры' }, // override: nm 4 переопределён в чужую категорию
    (nm) => (nm === 1 ? 'Швабры' : nm === 2 ? 'Диваны' : 'Ковры'),
);
const arts = [{ nm_id: 1 }, { nm_id: 2 }, { nm_id: 3 }, { nm_id: 4 }];
const ids = (out: { nm_id: number }[]) => out.map((a) => a.nm_id);

describe('filterArticlesByCategoryScope — владение категорией', () => {
    it('черновик БЕЗ скоупа не планирует категории живых категорийных черновиков', () => {
        const out = filterArticlesByCategoryScope(arts, null, categoryOf, new Set(['Швабры']));
        // nm 1 (предмет) и nm 4 (override) — обе «Швабры» → исключены одинаково.
        expect(ids(out)).toEqual([2, 3]);
    });

    it('скоупнутый черновик планирует свою категорию как раньше (позитивный фильтр)', () => {
        // Чужие скоупы на скоупнутый черновик не влияют — владение своё.
        const out = filterArticlesByCategoryScope(arts, ['Швабры'], categoryOf, new Set(['Диваны']));
        expect(ids(out)).toEqual([1, 4]);
    });

    it('без скоупа и без чужих скоупов — все статьи проходят', () => {
        expect(ids(filterArticlesByCategoryScope(arts, null, categoryOf, new Set()))).toEqual([1, 2, 3, 4]);
        expect(ids(filterArticlesByCategoryScope(arts, null, categoryOf, undefined))).toEqual([1, 2, 3, 4]);
        expect(ids(filterArticlesByCategoryScope(arts, [], categoryOf, undefined))).toEqual([1, 2, 3, 4]);
    });
});
