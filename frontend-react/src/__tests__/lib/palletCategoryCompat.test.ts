/**
 * Правила совместимости категорий на паллете: классы (`categoryCompat`) +
 * партиция смешанных BOX-паллет во всех трёх слоях (факт = KPI = показ):
 * `trimLinesToWholePallets` / `palletsForLines` / `buildPalletManifest`,
 * плюс конвейер `normalizeDraft` / `consolidatePrebookWholePallets`.
 */
import { describe, it, expect } from 'vitest';
import {
    buildCategoryOf, buildCompatClassOf, classLabelOf, inScopeOf,
    NO_CATEGORY, COMPAT_CLASS_ANY,
} from '@/lib/assembly/categoryCompat';
import { trimLinesToWholePallets, type PreviewLine } from '@/lib/utils/assemblyPreview';
import { palletsForLines } from '@/lib/utils/boxPallet';
import { buildPalletManifest, type ManifestLine } from '@/lib/utils/palletManifest';
import { normalizeDraft, consolidatePrebookWholePallets, type NormalizeDraftCtx } from '@/lib/utils/normalizeDraft';
import type { AssemblyDraftRow, PalletCategoryCompat } from '@/types/api';

const RULES: PalletCategoryCompat = {
    enabled: true,
    groups: [{ name: 'текстиль', categories: ['Чехлы', 'Пледы'] }],
};

// nm 1 = Чехлы, nm 2 = Пледы (группа «текстиль»), nm 3 = Ковры (соло), nm 4 — без предмета.
const SUBJECTS: Record<number, string | null> = { 1: 'Чехлы', 2: 'Пледы', 3: 'Ковры', 4: null };
const categoryOf = buildCategoryOf({}, (nm) => SUBJECTS[nm]);

describe('categoryCompat', () => {
    it('эффективная категория: override ?? subject ?? «Без категории»', () => {
        const withOv = buildCategoryOf({ '3': 'Ковровые дорожки' }, (nm) => SUBJECTS[nm]);
        expect(withOv(3)).toBe('Ковровые дорожки');
        expect(withOv(1)).toBe('Чехлы');
        expect(withOv(4)).toBe(NO_CATEGORY);
    });

    it('классы: группа делит класс, прочие — соло, выключено — все «*»', () => {
        const cls = buildCompatClassOf(RULES, categoryOf);
        expect(cls(1)).toBe(cls(2));      // Чехлы + Пледы — одна группа
        expect(cls(1)).toBe('g:0');
        expect(cls(3)).toBe('c:Ковры');   // соло-класс
        expect(cls(4)).toBe(`c:${NO_CATEGORY}`);
        const off = buildCompatClassOf({ enabled: false, groups: RULES.groups }, categoryOf);
        expect(off(1)).toBe(COMPAT_CLASS_ANY);
        expect(off(3)).toBe(COMPAT_CLASS_ANY);
    });

    it('classLabelOf: имя группы / список категорий / категория соло', () => {
        expect(classLabelOf('g:0', RULES)).toBe('текстиль');
        expect(classLabelOf('g:0', { enabled: true, groups: [{ categories: ['А', 'Б'] }] })).toBe('А + Б');
        expect(classLabelOf('c:Ковры', RULES)).toBe('Ковры');
        expect(classLabelOf(COMPAT_CLASS_ANY, RULES)).toBe('');
    });

    it('inScopeOf: пустой скоуп пропускает всё, непустой — только свои категории', () => {
        expect(inScopeOf(null, categoryOf)(3)).toBe(true);
        const scoped = inScopeOf(['Ковры'], categoryOf);
        expect(scoped(3)).toBe(true);
        expect(scoped(1)).toBe(false);
    });
});

// ─── Партиция среза до целых паллет ──────────────────────────────────────────
// Геометрия стабами: upp = 100 шт/паллета, короб = 10 шт.
const UPP = 100;
const PPB = 10;
const uppOf = () => UPP;
const boxOf = () => PPB;
const line = (nmId: number, qty: number): PreviewLine =>
    ({ ffId: 1, wbName: 'Казань', nmId, vendor: `v${nmId}`, barcode: `bc${nmId}`, pkg: 'BOX', isNew: false, qty });
const sum = (ls: PreviewLine[]) => ls.reduce((s, l) => s + l.qty, 0);

describe('trimLinesToWholePallets + classOf', () => {
    const cls = buildCompatClassOf(RULES, categoryOf);

    it('без classOf два SKU разных категорий делят паллету (легаси-микс)', () => {
        const res = trimLinesToWholePallets([line(1, 50), line(3, 50)], uppOf, boxOf);
        expect(sum(res.kept)).toBe(100);
        expect(res.droppedUnits).toBe(0);
    });

    it('с classOf разные классы НЕ делят паллету: оба полу-хвоста уходят', () => {
        const res = trimLinesToWholePallets([line(1, 50), line(3, 50)], uppOf, boxOf, cls);
        expect(sum(res.kept)).toBe(0);
        expect(res.droppedUnits).toBe(100);
        // Консервация: kept + dropped == вход.
        expect(sum(res.kept) + sum(res.droppedLines)).toBe(100);
    });

    it('одна группа («текстиль») по-прежнему миксуется в целую паллету', () => {
        const res = trimLinesToWholePallets([line(1, 50), line(2, 50)], uppOf, boxOf, cls);
        expect(sum(res.kept)).toBe(100);
        expect(res.droppedUnits).toBe(0);
    });

    it('идемпотентность: повторный срез kept ничего не снимает', () => {
        const first = trimLinesToWholePallets([line(1, 150), line(2, 60), line(3, 230)], uppOf, boxOf, cls);
        const second = trimLinesToWholePallets(first.kept, uppOf, boxOf, cls);
        expect(sum(second.kept)).toBe(sum(first.kept));
        expect(second.droppedUnits).toBe(0);
    });

    it('моно классами не режется (решение юзера): микс ≤3 артикулов сохраняется', () => {
        const mono = (nmId: number, qty: number): PreviewLine => ({ ...line(nmId, qty), pkg: 'MONOPALLET' });
        const withCls = trimLinesToWholePallets([mono(1, 50), mono(3, 50)], uppOf, boxOf, cls);
        const without = trimLinesToWholePallets([mono(1, 50), mono(3, 50)], uppOf, boxOf);
        expect(sum(withCls.kept)).toBe(sum(without.kept));
        expect(withCls.droppedUnits).toBe(without.droppedUnits);
    });
});

// ─── KPI == показ ────────────────────────────────────────────────────────────
const SIZE = '50x50x50';
const OVERRIDES = { [SIZE]: 10 }; // 10 коробов/паллету × ppb 10 = upp 100

describe('palletsForLines / buildPalletManifest per-класс', () => {
    it('box: линии разных групп считаются отдельными паллетами', () => {
        const mk = (units: number, group?: string) => ({ units, boxQty: PPB, boxSize: SIZE, group });
        const grouped = palletsForLines([mk(50, 'g:0'), mk(50, 'c:Ковры')], 180, 'box', OVERRIDES);
        const mixed = palletsForLines([mk(50), mk(50)], 180, 'box', OVERRIDES);
        expect(mixed.pallets).toBe(1);   // легаси: 0.5+0.5 = одна общая
        expect(grouped.pallets).toBe(2); // классы не делят паллету
        expect(grouped.fill).toBeCloseTo(mixed.fill, 9); // fill — суммарный, не меняется
    });

    it('манифест: бины не смешивают классы, счёт бинов == KPI, group проставлен', () => {
        const cls = buildCompatClassOf(RULES, categoryOf);
        const ml = (nmId: number, units: number): ManifestLine =>
            ({ nmId, vendorCode: `v${nmId}`, units, boxSize: SIZE, ppb: PPB });
        // Целые паллеты: текстиль 100 (=1 пал) + ковры 200 (=2 пал).
        const lines = [ml(1, 50), ml(2, 50), ml(3, 200)];
        const manifest = buildPalletManifest(lines, { mode: 'box', overrides: OVERRIDES, classOf: cls });
        expect(manifest.pallets.length).toBe(3);
        for (const p of manifest.pallets) {
            const classes = new Set(p.items.map((it) => cls(it.nmId)));
            expect(classes.size).toBe(1);
            expect(p.group).toBe([...classes][0]);
        }
        const kpi = palletsForLines(
            lines.map((l) => ({ units: l.units, boxQty: l.ppb, boxSize: l.boxSize, group: cls(l.nmId) })),
            180, 'box', OVERRIDES,
        );
        expect(kpi.pallets).toBe(manifest.pallets.length);
        // Номера сквозные 1..N.
        expect(manifest.pallets.map((p) => p.palletNo)).toEqual([1, 2, 3]);
    });

    it('без classOf поведение прежнее (одна смешанная паллета)', () => {
        const ml = (nmId: number, units: number): ManifestLine =>
            ({ nmId, vendorCode: `v${nmId}`, units, boxSize: SIZE, ppb: PPB });
        const manifest = buildPalletManifest([ml(1, 50), ml(3, 50)], { mode: 'box', overrides: OVERRIDES });
        expect(manifest.pallets.length).toBe(1);
        expect(manifest.pallets[0].group).toBeUndefined();
    });
});

// ─── Конвейер черновика ──────────────────────────────────────────────────────
const row = (nmId: number, qty: number, asIs = false): AssemblyDraftRow => ({
    nm_id: nmId, barcode: `bc${nmId}`, vendor_code: `v${nmId}`,
    src: { '1': qty }, tgt: { 'Казань': qty }, package_type: 'BOX', ...(asIs ? { as_is: true } : {}),
});
const ctxWith = (classOf?: (nm: number) => string): NormalizeDraftCtx => ({
    ppbOf: () => PPB,
    boxSizeOf: () => SIZE,
    overrides: OVERRIDES,
    freeByNm: {},
    classOf,
});
const sumTgt = (rs: AssemblyDraftRow[]) => rs.reduce((s, r) => s + Object.values(r.tgt).reduce((a, v) => a + (v || 0), 0), 0);

describe('normalizeDraft / consolidatePrebookWholePallets + classOf', () => {
    const cls = buildCompatClassOf(RULES, categoryOf);

    it('разные классы по полпаллеты → обе строки в предбронь (dropped)', () => {
        const res = normalizeDraft([row(1, 50), row(3, 50)], ctxWith(cls));
        expect(sumTgt(res.rows)).toBe(0);
        expect(sumTgt(res.dropped)).toBe(100);
    });

    it('одна группа по полпаллеты → едет целой смешанной паллетой', () => {
        const res = normalizeDraft([row(1, 50), row(2, 50)], ctxWith(cls));
        expect(sumTgt(res.rows)).toBe(100);
        expect(res.dropped.length).toBe(0);
    });

    it('фикс-поинт: повторный normalize по kept стабилен (changed=false, dropped пуст)', () => {
        const first = normalizeDraft([row(1, 150), row(2, 60), row(3, 230)], ctxWith(cls));
        const second = normalizeDraft(first.rows, ctxWith(cls));
        expect(second.changed).toBe(false);
        expect(second.dropped.length).toBe(0);
        expect(sumTgt(second.rows)).toBe(sumTgt(first.rows));
    });

    it('as_is минует паллет-срез и с классами (неполная паллета едет осознанно)', () => {
        const res = normalizeDraft([row(3, 50, true)], ctxWith(cls));
        expect(sumTgt(res.rows)).toBe(50);
    });

    it('консолидация предброни не собирает «целую» из разных классов', () => {
        const mixed = consolidatePrebookWholePallets([row(1, 50), row(3, 50)], ctxWith(cls));
        expect(mixed.changed).toBe(false);
        expect(sumTgt(mixed.prebook)).toBe(100);
        const sameGroup = consolidatePrebookWholePallets([row(1, 50), row(2, 50)], ctxWith(cls));
        expect(sameGroup.changed).toBe(true);
        expect(sumTgt(sameGroup.toDraft)).toBe(100);
    });
});
