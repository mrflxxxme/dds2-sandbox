import { describe, it, expect } from 'vitest';
import { consolidatePalletsInDraftRows } from '@/lib/utils/assemblyPalletConsolidate';
import type { AssemblyDraftRow } from '@/types/api';

// box_size 60x40x40 → 16 кор/паллету при H=180; ppb=10 → 160 шт/паллету.
// Коледино/Подольск/Электросталь = 180 см (Подольск нет в таблице → дефолт 180).
const SIZE = '60x40x40';
const DISTRICT: Record<string, string> = {
    'Коледино': 'central',
    'Подольск': 'central',
    'Электросталь': 'central',
    'СПБ Шушары': 'northwest',
};
const baseOpts = {
    ppbOf: () => 10,
    boxSizeOf: () => SIZE,
    districtOf: (wb: string) => DISTRICT[wb] ?? 'unknown',
    minFill: 0.2,
};

const row = (nm: number, src: Record<string, number>, tgt: Record<string, number>, pkg?: string): AssemblyDraftRow => ({
    nm_id: nm, barcode: `bc-${nm}`, vendor_code: `VC-${nm}`, src, tgt,
    ...(pkg ? { package_type: pkg as AssemblyDraftRow['package_type'] } : {}),
});

const totalSrc = (r: AssemblyDraftRow) => Object.values(r.src).reduce((s, v) => s + v, 0);
const totalTgt = (r: AssemblyDraftRow) => Object.values(r.tgt).reduce((s, v) => s + v, 0);

describe('consolidatePalletsInDraftRows', () => {
    it('сливает 50%+50% одного округа на приоритетный склад → целая паллета', () => {
        const res = consolidatePalletsInDraftRows(
            [row(100, { '1': 160 }, { 'Коледино': 80, 'Подольск': 80 })], baseOpts,
        );
        expect(res.rows).toHaveLength(1);
        expect(res.rows[0].tgt).toEqual({ 'Коледино': 160 }); // Подольск свезён на anchor
        expect(res.rows[0].src).toEqual({ '1': 160 });
        expect(res.mergedDirections).toBe(1);
        expect(res.droppedUnits).toBe(0);
        expect(res.changed).toBe(true);
    });

    it('слияние + неполный хвост ≥20% едет частичной паллетой', () => {
        // Коледино 160 + Подольск 40 (свезён) = 200 = 1 целая + 0.25 паллеты ≥ 0.2.
        const res = consolidatePalletsInDraftRows(
            [row(100, { '1': 200 }, { 'Коледино': 160, 'Подольск': 40 })], baseOpts,
        );
        expect(res.rows[0].tgt).toEqual({ 'Коледино': 200 });
        expect(res.droppedUnits).toBe(0);
        expect(res.mergedDirections).toBe(1);
    });

    it('весь округ < 20% — отказ: строка удаляется, штуки остаются на ФФ', () => {
        const res = consolidatePalletsInDraftRows(
            [row(100, { '1': 24 }, { 'Коледино': 24 })], baseOpts,
        );
        expect(res.rows).toHaveLength(0);
        expect(res.droppedUnits).toBe(24);
        expect(res.changed).toBe(true);
    });

    it('моно-строки (package_type ≠ BOX) не трогаются', () => {
        const mono = row(100, { '1': 24 }, { 'Коледино': 24 }, 'MONOPALLET');
        const res = consolidatePalletsInDraftRows([mono], baseOpts);
        expect(res.rows).toHaveLength(1);
        expect(res.rows[0].tgt).toEqual({ 'Коледино': 24 });
        expect(res.droppedUnits).toBe(0);
        expect(res.changed).toBe(false);
    });

    it('склад без округа (unknown) не консолидируется', () => {
        const res = consolidatePalletsInDraftRows(
            [row(100, { '1': 24 }, { 'Неизвестный': 24 })], baseOpts,
        );
        expect(res.rows[0].tgt).toEqual({ 'Неизвестный': 24 });
        expect(res.droppedUnits).toBe(0);
        expect(res.changed).toBe(false);
    });

    it('баланс Σsrc==Σtgt держится для каждой строки результата', () => {
        const rows = [
            row(100, { '1': 200, '2': 120 }, { 'Коледино': 160, 'Подольск': 100, 'Электросталь': 60 }),
            row(101, { '1': 90 }, { 'Коледино': 50, 'Подольск': 40 }),
            row(102, { '2': 24 }, { 'СПБ Шушары': 24 }), // другой округ
        ];
        const res = consolidatePalletsInDraftRows(rows, baseOpts);
        for (const r of res.rows) {
            expect(totalSrc(r)).toBe(totalTgt(r));
        }
    });

    it('без округов вообще (карта пустая) — ничего не меняет', () => {
        const res = consolidatePalletsInDraftRows(
            [row(100, { '1': 160 }, { 'Коледино': 80, 'Подольск': 80 })],
            { ...baseOpts, districtOf: () => 'unknown' },
        );
        expect(res.changed).toBe(false);
        expect(res.rows[0].tgt).toEqual({ 'Коледино': 80, 'Подольск': 80 });
    });

    // ── Находка 1: changed считается диффом, а не из side-effects ──────────
    it('перекладка между городами округа без дропа/слияния → changed=true и применяется', () => {
        // ppb=10, 160 шт/паллету. Подольск 290 (anchor, крупнейший) + Коледино 190.
        // Коледино 190 = 1 целая (160) + 30 крошки → 30 свозятся на anchor.
        // Подольск 290+30=320 = ровно 2 целые. Итог {Подольск:320, Коледино:160}.
        // Ни дропа, ни опустевших направлений, число строк то же — старый
        // флаг changed дал бы false и ВЫБРОСИЛ бы корректную перекладку.
        const res = consolidatePalletsInDraftRows(
            [row(100, { '1': 480 }, { 'Подольск': 290, 'Коледино': 190 })], baseOpts,
        );
        expect(res.changed).toBe(true);
        expect(res.rows).toHaveLength(1);
        expect(res.rows[0].tgt).toEqual({ 'Подольск': 320, 'Коледино': 160 });
        expect(res.rows[0].src).toEqual({ '1': 480 });
        expect(res.droppedUnits).toBe(0);
        expect(res.mergedDirections).toBe(0); // оба направления остались непустыми
    });

    // ── Находка 2: несбалансированную строку не перебалансировать молча ────
    it('несбалансированная строка (Σsrc≠Σtgt) проходит как есть, не консолидируется', () => {
        // Юзер правил src в матрице (480), tgt оставил на 400 — строка рендерится «!».
        // allocatePairs форсит баланс к Σsrc=480 на обеих сторонах → переписал бы
        // отгрузку. Должны пропустить строку нетронутой.
        const r = row(100, { '1': 480 }, { 'Подольск': 290, 'Коледино': 110 });
        const res = consolidatePalletsInDraftRows([r], baseOpts);
        expect(res.rows).toHaveLength(1);
        expect(res.rows[0].src).toEqual({ '1': 480 });
        expect(res.rows[0].tgt).toEqual({ 'Подольск': 290, 'Коледино': 110 });
        expect(res.changed).toBe(false);
        expect(res.droppedUnits).toBe(0);
    });

    it('сбалансированная + несбалансированная: первую консолидируем, вторую не трогаем', () => {
        const balanced = row(100, { '1': 160 }, { 'Коледино': 80, 'Подольск': 80 });
        const unbalanced = row(101, { '1': 480 }, { 'Подольск': 290, 'Коледино': 110 });
        const res = consolidatePalletsInDraftRows([balanced, unbalanced], baseOpts);
        const byNm = Object.fromEntries(res.rows.map(r => [r.nm_id, r.tgt]));
        expect(byNm[100]).toEqual({ 'Коледино': 160 });               // слита
        expect(byNm[101]).toEqual({ 'Подольск': 290, 'Коледино': 110 }); // нетронута
        expect(res.changed).toBe(true);
    });

    // ── Регресс: уже-целый МУЛЬТИ-ФФ черновик не искажается allocatePairs ──
    it('уже-целый мульти-ФФ черновик не трогается (нет allocatePairs ±1 искажения)', () => {
        // tgt={Подольск:320,Коледино:480} — ОБА ровно целые паллеты (160 шт/паллету).
        // src по 3 ФФ с неровным сплитом: старый allocatePairs-round-trip давал по
        // городам {Подольск:319,Коледино:481} → консолидация «чинила» фантомную крошку,
        // переписывала раскладку и роняла штуки. Должно быть changed=false, no-op.
        const res = consolidatePalletsInDraftRows(
            [row(100, { '1': 548, '2': 156, '3': 96 }, { 'Подольск': 320, 'Коледино': 480 })],
            baseOpts,
        );
        expect(res.changed).toBe(false);
        expect(res.rows).toHaveLength(1);
        expect(res.rows[0].tgt).toEqual({ 'Подольск': 320, 'Коледино': 480 });
        expect(res.rows[0].src).toEqual({ '1': 548, '2': 156, '3': 96 });
        expect(res.droppedUnits).toBe(0);
    });

    it('мульти-ФФ: хвост <20% дропается, src урезается до Σtgt (баланс держится)', () => {
        // Коледино 40 свозится на Подольск → 170; хвост 170-160=10 (frac 0.0625<0.2)
        // дропается. Σtgt=160, src {1:90,2:80}=170 урезается до 160.
        const res = consolidatePalletsInDraftRows(
            [row(100, { '1': 90, '2': 80 }, { 'Подольск': 130, 'Коледино': 40 })], baseOpts,
        );
        expect(res.rows).toHaveLength(1);
        expect(res.rows[0].tgt).toEqual({ 'Подольск': 160 });
        expect(res.droppedUnits).toBe(10);
        expect(totalSrc(res.rows[0])).toBe(totalTgt(res.rows[0]));
        expect(totalSrc(res.rows[0])).toBe(160);
    });

    // ── Находка 3: canAnchorOf (приёмка короба) honored ───────────────────
    it('canAnchorOf=false для anchor → крошка SKU роняется на ФФ, не свозится', () => {
        // Без предиката: Коледино 40 свезётся на Подольск (160+40=200, частичная
        // паллета 25% ≥ 20% едет). С canAnchorOf=false: крошка не едет на anchor →
        // ФФ; anchor держит свою целую паллету 160.
        const res = consolidatePalletsInDraftRows(
            [row(100, { '1': 200 }, { 'Подольск': 160, 'Коледино': 40 })],
            { ...baseOpts, canAnchorOf: () => false },
        );
        expect(res.rows[0].tgt).toEqual({ 'Подольск': 160 });
        expect(res.rows[0].src).toEqual({ '1': 160 });
        expect(res.droppedUnits).toBe(40);
        expect(res.changed).toBe(true);
    });
});
