import { describe, it, expect } from 'vitest';
import {
    parseBoxSize,
    boxesPerLayer,
    boxesPerPallet,
    unitsPerPallet,
    palletsForBoxes,
    maxPalletHeightCm,
    canPalletize,
    canonBoxSize,
    effectiveBoxesPerPallet,
    consolidateDistrictPallets,
    trimToWholePallets,
    consolidateMixedDistrictPallets,
    palletsForLines,
    snapToWholePallets,
    DEFAULT_MAX_PALLET_HEIGHT_CM,
    type BoxDims,
} from '@/lib/utils/boxPallet';

describe('canonBoxSize', () => {
    it('нормализует разделители и регистр', () => {
        expect(canonBoxSize('60×40×50')).toBe('60x40x50');
        expect(canonBoxSize('60X40X50')).toBe('60x40x50');
        expect(canonBoxSize('60 х 40 х 50')).toBe('60x40x50');
        expect(canonBoxSize('60*40*50')).toBe('60x40x50');
    });
    it('пусто/null → пустая строка', () => {
        expect(canonBoxSize(null)).toBe('');
        expect(canonBoxSize('')).toBe('');
    });
    it('разные написания одного размера сливаются (баг 60x40xc40 = 60*40*40)', () => {
        expect(canonBoxSize('60x40xc40')).toBe('60x40x40');
        expect(canonBoxSize('60*40*40')).toBe('60x40x40');
        expect(canonBoxSize('60x40xc40')).toBe(canonBoxSize('60*40*40'));
    });
    it('дробное 9.0 → 9', () => {
        expect(canonBoxSize('60x16x9.0')).toBe('60x16x9');
    });
});

describe('effectiveBoxesPerPallet', () => {
    it('override перебивает геометрию', () => {
        expect(effectiveBoxesPerPallet('60x40x40', 180, { '60x40x40': 99 })).toBe(99);
    });
    it('нет override → геометрия', () => {
        expect(effectiveBoxesPerPallet('60x40x40', 180, {})).toBe(16);
        expect(effectiveBoxesPerPallet('60x40x40', 180)).toBe(16);
    });
    it('override матчится по канону размера', () => {
        expect(effectiveBoxesPerPallet('60×40×40', 180, { '60x40x40': 50 })).toBe(50);
    });
    it('нет габаритов и нет override → null', () => {
        expect(effectiveBoxesPerPallet('мусор', 180, {})).toBeNull();
    });
});

describe('parseBoxSize', () => {
    it('парсит «60x40x40» → {60,40,40}', () => {
        expect(parseBoxSize('60x40x40')).toEqual({ length: 60, width: 40, height: 40 });
    });
    it('кириллический «х», пробелы, регистр', () => {
        expect(parseBoxSize(' 40Х30Х25 ')).toEqual({ length: 40, width: 30, height: 25 });
    });
    it('сепараторы * , / ', () => {
        expect(parseBoxSize('60*50*40')).toEqual({ length: 60, width: 50, height: 40 });
        expect(parseBoxSize('60×40×30')).toEqual({ length: 60, width: 40, height: 30 });
    });
    it('запятая — разделитель (габариты в см целые)', () => {
        expect(parseBoxSize('60,40,40')).toEqual({ length: 60, width: 40, height: 40 });
    });
    it('точка — десятичный разделитель', () => {
        expect(parseBoxSize('60.5x40x40')).toEqual({ length: 60.5, width: 40, height: 40 });
    });
    it('null/пусто/мусор/<3 чисел → null', () => {
        expect(parseBoxSize(null)).toBeNull();
        expect(parseBoxSize('')).toBeNull();
        expect(parseBoxSize('большая коробка')).toBeNull();
        expect(parseBoxSize('60x40')).toBeNull();
    });
    it('нулевое измерение → null', () => {
        expect(parseBoxSize('60x0x40')).toBeNull();
    });
    it('лишняя буква/опечатка между числами игнорируется (60x40xc40)', () => {
        expect(parseBoxSize('60x40xc40')).toEqual({ length: 60, width: 40, height: 40 });
    });
    it('берёт первые 3 числа', () => {
        expect(parseBoxSize('60x40x40x2')).toEqual({ length: 60, width: 40, height: 40 });
    });
});

describe('boxesPerLayer', () => {
    it('60×40 на 120×80 → 4 (2×2)', () => {
        expect(boxesPerLayer({ length: 60, width: 40, height: 40 })).toBe(4);
    });
    it('берёт лучшую ориентацию: 40×60 → тоже 4', () => {
        expect(boxesPerLayer({ length: 40, width: 60, height: 40 })).toBe(4);
    });
    it('коробка больше основания → 0', () => {
        expect(boxesPerLayer({ length: 130, width: 90, height: 40 })).toBe(0);
    });
    it('коробка ровно в основание → 1', () => {
        expect(boxesPerLayer({ length: 120, width: 80, height: 40 })).toBe(1);
    });
});

describe('boxesPerPallet', () => {
    it('60×40×40, высота 180 → 16 (4/слой × 4 слоя)', () => {
        // budget = 180 - 14.5 = 165.5; layers = floor(165.5/40) = 4
        expect(boxesPerPallet({ length: 60, width: 40, height: 40 }, 180)).toBe(16);
    });
    it('высота склада меняет число слоёв (160 → 3 слоя)', () => {
        // budget = 160 - 14.5 = 145.5; layers = floor(145.5/40) = 3 → 4×3=12
        expect(boxesPerPallet({ length: 60, width: 40, height: 40 }, 160)).toBe(12);
    });
    it('коробка не влезает в основание → null', () => {
        expect(boxesPerPallet({ length: 130, width: 90, height: 40 }, 180)).toBeNull();
    });
    it('коробка выше лимита склада (ни одного слоя) → null', () => {
        expect(boxesPerPallet({ length: 60, width: 40, height: 200 }, 180)).toBeNull();
    });
    it('дефолтная высота = 180', () => {
        expect(boxesPerPallet({ length: 60, width: 40, height: 40 })).toBe(
            boxesPerPallet({ length: 60, width: 40, height: 40 }, DEFAULT_MAX_PALLET_HEIGHT_CM),
        );
    });
});

describe('unitsPerPallet', () => {
    it('16 коробок/паллету × 22 шт/короб = 352', () => {
        expect(unitsPerPallet({ length: 60, width: 40, height: 40 }, 22, 180)).toBe(352);
    });
    it('нет кратности → null', () => {
        expect(unitsPerPallet({ length: 60, width: 40, height: 40 }, null, 180)).toBeNull();
        expect(unitsPerPallet({ length: 60, width: 40, height: 40 }, 0, 180)).toBeNull();
    });
    it('нет габаритов (коробка не палетизируется) → null', () => {
        expect(unitsPerPallet({ length: 130, width: 90, height: 40 }, 22, 180)).toBeNull();
    });
});

describe('palletsForBoxes', () => {
    it('округление вверх', () => {
        expect(palletsForBoxes(16, 16)).toBe(1);
        expect(palletsForBoxes(17, 16)).toBe(2);
        expect(palletsForBoxes(1, 16)).toBe(1);
    });
    it('ноль/невалид → 0', () => {
        expect(palletsForBoxes(0, 16)).toBe(0);
        expect(palletsForBoxes(10, 0)).toBe(0);
    });
});

describe('maxPalletHeightCm', () => {
    it('известные склады по таблице WB', () => {
        expect(maxPalletHeightCm('Воронеж')).toBe(185);
        expect(maxPalletHeightCm('Котовск')).toBe(160);
        expect(maxPalletHeightCm('Краснодар')).toBe(170);
    });
    it('со скобками/суффиксом нормализуется', () => {
        expect(maxPalletHeightCm('Тула (Алексин)')).toBe(180);
        expect(maxPalletHeightCm('Екатеринбург: Перспективная')).toBe(170);
    });
    it('неизвестный склад → дефолт 180', () => {
        expect(maxPalletHeightCm('Несуществующий')).toBe(DEFAULT_MAX_PALLET_HEIGHT_CM);
        expect(maxPalletHeightCm(null)).toBe(DEFAULT_MAX_PALLET_HEIGHT_CM);
    });
    it('префикс-матч однонаправленный: имя начинается с ключа', () => {
        expect(maxPalletHeightCm('Коледино СГТ')).toBe(180); // имя ⊃ ключ
    });
    it('обратный матч НЕ срабатывает (аббревиатура не резолвится)', () => {
        // «Во» не должно стать Воронежем (185) — только дефолт.
        expect(maxPalletHeightCm('Во')).toBe(DEFAULT_MAX_PALLET_HEIGHT_CM);
    });
});

describe('canPalletize', () => {
    it('есть кратность И габариты → true', () => {
        expect(canPalletize(22, '60x40x40')).toBe(true);
    });
    it('нет кратности → false', () => {
        expect(canPalletize(null, '60x40x40')).toBe(false);
        expect(canPalletize(0, '60x40x40')).toBe(false);
    });
    it('нет габаритов → false', () => {
        expect(canPalletize(22, null)).toBe(false);
        expect(canPalletize(22, 'без размера')).toBe(false);
    });
});

describe('consolidateDistrictPallets', () => {
    // 60×40×40 @180 → 16 коробок/паллету; ppb=10 → 160 шт/паллету.
    const DIMS: BoxDims = { length: 60, width: 40, height: 40 };
    const PPB = 10;
    const H180 = () => 180;
    // Округа: ЦФО (Коледино, Подольск, Электросталь), Юг (Краснодар).
    const DISTRICT: Record<string, string> = {
        'Коледино': 'central', 'Подольск': 'central', 'Электросталь': 'central',
        'Краснодар': 'south',
    };
    const distOf = (wh: string) => DISTRICT[wh] ?? 'unknown';

    it('Подольск 3 кор + Коледино 13 кор → 1 паллета на Коледино (тот же ЦФО)', () => {
        const r = consolidateDistrictPallets(
            { 'Подольск': 30, 'Коледино': 130 }, PPB, DIMS, distOf, H180, 0,
        );
        expect(r.tgt).toEqual({ 'Коледино': 160 }); // приоритет — крупнейший
        expect(r.shipped).toBe(160);
        expect(r.droppedToFf).toBe(0);
        expect(r.usedBuffer).toBe(0);
    });

    it('целые паллеты остаются на месте, крошки пуллятся к приоритету', () => {
        // Электросталь 320 = 2 паллеты (на месте); Подольск 30 крошка → пул < паллеты → drop (буфера нет).
        const r = consolidateDistrictPallets(
            { 'Электросталь': 320, 'Подольск': 30 }, PPB, DIMS, distOf, H180, 0,
        );
        expect(r.tgt).toEqual({ 'Электросталь': 320 });
        expect(r.shipped).toBe(320);
        expect(r.droppedToFf).toBe(30);
    });

    it('добивает до паллеты из ФФ-буфера', () => {
        const r = consolidateDistrictPallets(
            { 'Коледино': 150 }, PPB, DIMS, distOf, H180, 20, // нужно 10, буфера хватает
        );
        expect(r.tgt).toEqual({ 'Коледино': 160 });
        expect(r.shipped).toBe(150);
        expect(r.usedBuffer).toBe(10);
        expect(r.droppedToFf).toBe(0);
    });

    it('буфера не хватает → направление не отгружается (qty на ФФ)', () => {
        const r = consolidateDistrictPallets(
            { 'Коледино': 150 }, PPB, DIMS, distOf, H180, 0,
        );
        expect(r.tgt).toEqual({});
        expect(r.shipped).toBe(0);
        expect(r.droppedToFf).toBe(150);
    });

    it('два округа независимы — каждый нужна своя паллета', () => {
        const r = consolidateDistrictPallets(
            { 'Коледино': 130, 'Краснодар': 130 }, PPB, DIMS, distOf, H180, 0,
        );
        expect(r.tgt).toEqual({}); // ни один округ не добрал паллету, оба на ФФ
        expect(r.droppedToFf).toBe(260);
    });

    it('несколько крошек складываются в паллету на приоритете', () => {
        // 50+50+90=190 → 1 паллета (160) на Электросталь (90 — крупнейший), остаток 30 → drop.
        const r = consolidateDistrictPallets(
            { 'Коледино': 50, 'Подольск': 50, 'Электросталь': 90 }, PPB, DIMS, distOf, H180, 0,
        );
        expect(r.tgt).toEqual({ 'Электросталь': 160 });
        expect(r.shipped).toBe(160);
        expect(r.droppedToFf).toBe(30);
    });

    it('нет кратности → возвращает как есть (no-op)', () => {
        const t = { 'Подольск': 30, 'Коледино': 130 };
        const r = consolidateDistrictPallets(t, 0, DIMS, distOf, H180, 0);
        expect(r.tgt).toEqual(t);
        expect(r.shipped).toBe(160);
        expect(r.droppedToFf).toBe(0);
    });

    it('крупногабарит (не палетизируется) → округ как есть', () => {
        const big: BoxDims = { length: 130, width: 90, height: 40 }; // не лезет на паллету
        const r = consolidateDistrictPallets(
            { 'Подольск': 30, 'Коледино': 130 }, PPB, big, distOf, H180, 0,
        );
        expect(r.tgt).toEqual({ 'Подольск': 30, 'Коледино': 130 });
        expect(r.shipped).toBe(160);
    });

    it('topUp=false — добивки нет даже при буфере (строгий режим новинок)', () => {
        const r = consolidateDistrictPallets(
            { 'Коледино': 150 }, PPB, DIMS, distOf, H180, 999, { topUp: false },
        );
        expect(r.tgt).toEqual({});
        expect(r.droppedToFf).toBe(150);
        expect(r.usedBuffer).toBe(0);
    });

    it('разные высоты складов в одном округе — каждый держит свои паллеты', () => {
        // Коледино @180 → 16 кор/пал = 160 шт; Котовск @160 → 12 кор/пал = 120 шт.
        const H = (wh: string) => (wh === 'Котовск' ? 160 : 180);
        const dist = (wh: string) => (wh === 'Котовск' ? 'central' : DISTRICT[wh] ?? 'unknown');
        const r = consolidateDistrictPallets(
            { 'Коледино': 320, 'Котовск': 120 }, PPB, DIMS, dist, H, 0,
        );
        expect(r.tgt).toEqual({ 'Коледино': 320, 'Котовск': 120 }); // 2 пал + 1 пал, оба целые
        expect(r.shipped).toBe(440);
        expect(r.droppedToFf).toBe(0);
    });

    it('boxesOverride перебивает геометрию (больше → паллета не набирается)', () => {
        // override 20 кор/пал = 200 шт; 160 < 200 без буфера → drop (геометрия дала бы 1 паллету).
        const r = consolidateDistrictPallets(
            { 'Подольск': 30, 'Коледино': 130 }, PPB, DIMS, distOf, H180, 0, { boxesOverride: 20 },
        );
        expect(r.tgt).toEqual({});
        expect(r.droppedToFf).toBe(160);
    });

    it('boxesOverride меньше геометрии → собирает паллеты на приоритете', () => {
        // override 8 кор/пал = 80 шт; Коледино 80(keep)+80(pool)=160; Подольск → 0.
        const r = consolidateDistrictPallets(
            { 'Подольск': 30, 'Коледино': 130 }, PPB, DIMS, distOf, H180, 0, { boxesOverride: 8 },
        );
        expect(r.tgt).toEqual({ 'Коледино': 160 });
        expect(r.shipped).toBe(160);
    });

    it('инвариант: Σtgt = shipped + usedBuffer', () => {
        const r = consolidateDistrictPallets(
            { 'Коледино': 150 }, PPB, DIMS, distOf, H180, 20,
        );
        const total = Object.values(r.tgt).reduce((s, v) => s + v, 0);
        expect(total).toBe(r.shipped + r.usedBuffer);
    });
});

describe('trimToWholePallets', () => {
    const PU = 160; // 16 кор × ppb 10 = 160 шт/паллету (одинаково на всех складах)
    const palletUnits = () => PU;

    it('Σ ≤ max → распределение не меняется', () => {
        const t = { 'Коледино': 320, 'Краснодар': 160 };
        expect(trimToWholePallets(t, 480, palletUnits)).toEqual(t);
        expect(trimToWholePallets(t, 9999, palletUnits)).toEqual(t);
    });

    it('снимает целые паллеты со склада с наименьшим qty первым', () => {
        // Σ=640 (Коледино 480=3пал, Краснодар 160=1пал); max=480 → снять 1 паллету.
        // Наименьший qty = Краснодар (160) → его и роняем целиком.
        const r = trimToWholePallets({ 'Коледино': 480, 'Краснодар': 160 }, 480, palletUnits);
        expect(r).toEqual({ 'Коледино': 480 });
    });

    it('результат — кратен паллете и Σ ≤ max (дробный max округляется вниз по паллетам)', () => {
        // Σ=480 (3 пал по 160); max=330 → можно оставить max 2 паллеты (320).
        const r = trimToWholePallets({ 'Коледино': 320, 'Подольск': 160 }, 330, palletUnits);
        const sum = Object.values(r).reduce((s, v) => s + v, 0);
        expect(sum).toBeLessThanOrEqual(330);
        expect(sum % PU).toBe(0);
        expect(sum).toBe(320);
        // Подольск (наименьший) ушёл целиком, осталась 2-паллетная Коледино? Нет:
        // снимаем по одной паллете с наименьшего: Подольск 160→0, затем Σ=320 ≤ 330 стоп.
        expect(r).toEqual({ 'Коледино': 320 });
    });

    it('max=0 → роняет всё, что палетизируется', () => {
        expect(trimToWholePallets({ 'Коледино': 320, 'Краснодар': 160 }, 0, palletUnits)).toEqual({});
    });

    it('склад без паллет-расчёта (palletUnits=null) не трогаем', () => {
        // Краснодар крупногабарит → null; снять с него паллету нельзя, остаётся как есть.
        const puMixed = (wh: string) => (wh === 'Краснодар' ? null : PU);
        const r = trimToWholePallets({ 'Коледино': 320, 'Краснодар': 50 }, 100, puMixed);
        // Коледино 320 → снимаем 2 паллеты до 0; Краснодар 50 не трогаем (null).
        expect(r).toEqual({ 'Краснодар': 50 });
    });

    it('не уходит ниже целой паллеты, даже если max между границами', () => {
        // Σ=480; max=150 (< 1 паллеты) → снимаем все целые паллеты → {} (дробную не оставляем).
        const r = trimToWholePallets({ 'Коледино': 320, 'Подольск': 160 }, 150, palletUnits);
        expect(r).toEqual({});
    });
});

describe('consolidateMixedDistrictPallets', () => {
    // Все артикулы одного размера: ppb=10, 160 шт/паллету на любом складе.
    const PU = 160;
    const ppbOf = () => 10;
    const palletUnitsOf = () => PU;
    const sum = (o: MixedPalletOutputLike) =>
        Object.values(o.byWhKey).reduce((s, km) => s + Object.values(km).reduce((a, b) => a + b, 0), 0)
        + Object.values(o.droppedToFf).reduce((a, b) => a + b, 0);
    type MixedPalletOutputLike = { byWhKey: Record<string, Record<string, number>>; droppedToFf: Record<string, number> };

    it('один артикул, один склад — целые паллеты, остаток на ФФ', () => {
        // A=350 на anchor: 2 паллеты (320) + 30 крошка → drop 30.
        const r = consolidateMixedDistrictPallets({
            byWhKey: { 'Коледино': { A: 350 } }, anchorWh: 'Коледино', ppbOf, palletUnitsOf,
        });
        expect(r.byWhKey).toEqual({ 'Коледино': { A: 320 } });
        expect(r.droppedToFf).toEqual({ A: 30 });
    });

    it('МИКС двух артикулов на anchor собирает целую смешанную паллету', () => {
        // A=100 (0.625) + B=80 (0.5) = 1.125 паллеты → 1 целая (160) + 0.125 (20) на ФФ.
        // Крошку снимаем с меньшего (B): B 80→60. Итог A100+B60=160 = ровно паллета.
        const r = consolidateMixedDistrictPallets({
            byWhKey: { 'Коледино': { A: 100, B: 80 } }, anchorWh: 'Коледино', ppbOf, palletUnitsOf,
        });
        expect(r.byWhKey['Коледино']).toEqual({ A: 100, B: 60 });
        expect(r.droppedToFf).toEqual({ B: 20 });
        // Инвариант сохранения: ничего не потерялось.
        expect(sum(r)).toBe(180);
    });

    it('низкооборотный артикул со склада-сателлита свозится на anchor и едет в миксе', () => {
        // Anchor A=100 (0.625), сателлит S: A=80 (0.5) <1 паллеты → весь на anchor.
        // Anchor: A=180 → 1 паллета (160) + 20 на ФФ.
        const r = consolidateMixedDistrictPallets({
            byWhKey: { 'Коледино': { A: 100 }, 'Подольск': { A: 80 } },
            anchorWh: 'Коледино', ppbOf, palletUnitsOf,
        });
        expect(r.byWhKey['Подольск']).toBeUndefined();          // сателлит опустел
        expect(r.byWhKey['Коледино']).toEqual({ A: 160 });
        expect(r.droppedToFf).toEqual({ A: 20 });
    });

    it('сателлит с ≥1 целой паллетой держит её, свозит только крошку', () => {
        // Сателлит S: A=350 (2.1875) → держит 2 паллеты (320), крошку 30 → anchor.
        // Anchor: B=130 (0.8125) + пришло A=30 (0.1875) = 1.0 паллета ровно → ничего на ФФ.
        const r = consolidateMixedDistrictPallets({
            byWhKey: { 'Коледино': { B: 130 }, 'Подольск': { A: 350 } },
            anchorWh: 'Коледино', ppbOf, palletUnitsOf,
        });
        expect(r.byWhKey['Подольск']).toEqual({ A: 320 });       // сателлит держит 2 целые
        expect(r.byWhKey['Коледино']).toEqual({ A: 30, B: 130 }); // микс-паллета на anchor
        expect(r.droppedToFf).toEqual({});                        // ровно целое
        expect(sum(r)).toBe(480);
    });

    it('округ с суммой < 1 паллеты не отгружается (всё на ФФ, строго)', () => {
        // A=50 + B=40 = 0.5625 паллеты <1 → ничего не едет.
        const r = consolidateMixedDistrictPallets({
            byWhKey: { 'Коледино': { A: 50 }, 'Подольск': { B: 40 } },
            anchorWh: 'Коледино', ppbOf, palletUnitsOf,
        });
        expect(r.byWhKey).toEqual({});
        expect(r.droppedToFf).toEqual({ A: 50, B: 40 });
    });

    it('крупногабарит (palletUnits=null) не трогаем — едет как есть', () => {
        // X не палетизируется (null), A=200 (1.25) на anchor.
        const puMixed = (key: string) => (key === 'X' ? null : PU);
        const r = consolidateMixedDistrictPallets({
            byWhKey: { 'Коледино': { A: 200, X: 33 } }, anchorWh: 'Коледино', ppbOf, palletUnitsOf: puMixed,
        });
        // A: 1.25 → 1 паллета (160) + 40 на ФФ; X не трогаем.
        expect(r.byWhKey['Коледино']).toEqual({ A: 160, X: 33 });
        expect(r.droppedToFf).toEqual({ A: 40 });
    });

    it('инвариант сохранения единиц: Σ(byWhKey) + Σ(droppedToFf) == вход', () => {
        const r = consolidateMixedDistrictPallets({
            byWhKey: { 'Коледино': { A: 230, B: 90 }, 'Подольск': { A: 170, C: 60 }, 'Электросталь': { B: 40 } },
            anchorWh: 'Коледино', ppbOf, palletUnitsOf,
        });
        expect(sum(r)).toBe(230 + 90 + 170 + 60 + 40);
    });

    it('крошка SKU, не box-приёмного на anchor, роняется на ФФ (а не едет на anchor)', () => {
        // Сателлит S: B=40 (0.25 паллеты) <1 → крошка. B НЕ приёмный коробом на anchor
        // Коледино → не свозим, роняем на ФФ. A на anchor=100 (0.625) тоже крошка → но
        // A приёмный, остаётся... один на anchor < паллеты → тоже на ФФ (строго).
        const canAnchor = (key: string) => key !== 'B'; // B нельзя на anchor
        const r = consolidateMixedDistrictPallets({
            byWhKey: { 'Коледино': { A: 100 }, 'Подольск': { B: 40 } },
            anchorWh: 'Коледино', ppbOf, palletUnitsOf, canAnchor,
        });
        // B не доехал до anchor → на ФФ; A один на anchor 0.625 <1 → на ФФ.
        expect(r.byWhKey).toEqual({});
        expect(r.droppedToFf).toEqual({ A: 100, B: 40 });
        expect(sum(r)).toBe(140);
    });
});

describe('consolidateMixedDistrictPallets · minAnchorFill (частичная паллета)', () => {
    const PU = 160; // ppb=10, 16 кор/паллету
    const ppbOf = () => 10;
    const palletUnitsOf = () => PU;

    it('хвост ≥ порога (50% при minFill=0.2) — частичная паллета ЕДЕТ', () => {
        // A=400 = 2 целые (320) + 80 (0.5 паллеты) ≥ 0.2 → везём всё.
        const r = consolidateMixedDistrictPallets({
            byWhKey: { 'Коледино': { A: 400 } }, anchorWh: 'Коледино', ppbOf, palletUnitsOf, minAnchorFill: 0.2,
        });
        expect(r.byWhKey['Коледино']).toEqual({ A: 400 });
        expect(r.droppedToFf).toEqual({});
    });

    it('хвост < порога (0.1875 при minFill=0.2) — остаток на ФФ', () => {
        // A=350 = 2 целые (320) + 30 (0.1875) < 0.2 → 30 на ФФ.
        const r = consolidateMixedDistrictPallets({
            byWhKey: { 'Коледино': { A: 350 } }, anchorWh: 'Коледино', ppbOf, palletUnitsOf, minAnchorFill: 0.2,
        });
        expect(r.byWhKey['Коледино']).toEqual({ A: 320 });
        expect(r.droppedToFf).toEqual({ A: 30 });
    });

    it('весь округ < порога (0.15) — полный отказ (всё на ФФ)', () => {
        const r = consolidateMixedDistrictPallets({
            byWhKey: { 'Коледино': { A: 24 } }, anchorWh: 'Коледино', ppbOf, palletUnitsOf, minAnchorFill: 0.2,
        });
        expect(r.byWhKey).toEqual({});
        expect(r.droppedToFf).toEqual({ A: 24 });
    });

    it('слияние 50%+30% одного округа → 0.8 паллеты ≥ порога — едет частичной', () => {
        // Сателлит Подольск A=48 (0.3) свозится на anchor Коледино A=80; итог 128 (0.8) ≥ 0.2.
        const r = consolidateMixedDistrictPallets({
            byWhKey: { 'Коледино': { A: 80 }, 'Подольск': { A: 48 } },
            anchorWh: 'Коледино', ppbOf, palletUnitsOf, minAnchorFill: 0.2,
        });
        expect(r.byWhKey).toEqual({ 'Коледино': { A: 128 } });
        expect(r.droppedToFf).toEqual({});
    });

    it('без minAnchorFill (по умолчанию) — строгое поведение: хвост 0.5 дропается', () => {
        const r = consolidateMixedDistrictPallets({
            byWhKey: { 'Коледино': { A: 400 } }, anchorWh: 'Коледино', ppbOf, palletUnitsOf,
        });
        expect(r.byWhKey['Коледино']).toEqual({ A: 320 });
        expect(r.droppedToFf).toEqual({ A: 80 });
    });
});

describe('palletsForLines', () => {
    const SIZE = '60x40x40'; // bpp@180 = 16; boxQty 10 → 160 шт/паллету
    const BOXQTY = 10;
    const H = 180;

    it('короб: смешанные паллеты — Σ долей объёма, округление вверх', () => {
        const r = palletsForLines(
            [{ units: 160, boxQty: BOXQTY, boxSize: SIZE }, { units: 160, boxQty: BOXQTY, boxSize: SIZE }],
            H, 'box',
        );
        expect(r.pallets).toBe(2); // 1.0 + 1.0
        expect(r.fill).toBeCloseTo(2, 6);
        expect(r.unknownUnits).toBe(0);
    });

    it('короб: недозагруженная паллета (2 короба) = 1 паллета, малая заполненность', () => {
        const r = palletsForLines([{ units: 20, boxQty: BOXQTY, boxSize: SIZE }], H, 'box');
        expect(r.pallets).toBe(1); // 20/160 = 0.125 → ⌈⌉ = 1
        expect(r.fill).toBeCloseTo(0.125, 6);
    });

    it('моно ≤3: до 3 артикулов собираются на паллету, >3 — разносятся (короб — без капа)', () => {
        const mk = (u: number) => ({ units: u, boxQty: BOXQTY, boxSize: SIZE }); // cap = 160
        // 3 моно-SKU по 0.25 паллеты → собираются в 1 (≤3 артикула).
        expect(palletsForLines([mk(40), mk(40), mk(40)], H, 'mono').pallets).toBe(1);
        // 4 моно-SKU по 0.25 = Σ1 паллета, но ≤3/паллету → 2; короб (без капа) → 1.
        const four = [mk(40), mk(40), mk(40), mk(40)];
        expect(palletsForLines(four, H, 'mono').pallets).toBe(2);
        expect(palletsForLines(four, H, 'box').pallets).toBe(1);
    });

    it('без габаритов/кратности — в unknown, не в pallets', () => {
        const r = palletsForLines(
            [{ units: 100, boxQty: BOXQTY, boxSize: null }, { units: 160, boxQty: BOXQTY, boxSize: SIZE }],
            H, 'box',
        );
        expect(r.pallets).toBe(1); // только палетизируемая линия (160/160=1)
        expect(r.unknownLines).toBe(1);
        expect(r.unknownUnits).toBe(100);
    });

    it('override «коробок на паллету» перебивает геометрию', () => {
        const overrides = { [canonBoxSize(SIZE)]: 5 }; // 5 коробов × 10 = 50 шт/паллету
        const r = palletsForLines([{ units: 100, boxQty: BOXQTY, boxSize: SIZE }], H, 'box', overrides);
        expect(r.pallets).toBe(2); // 100/50 = 2
    });

    it('пустой/нулевой ввод → 0 паллет', () => {
        expect(palletsForLines([], H, 'box').pallets).toBe(0);
        expect(palletsForLines([{ units: 0, boxQty: BOXQTY, boxSize: SIZE }], H, 'box').pallets).toBe(0);
    });
});

describe('snapToWholePallets', () => {
    // upp=160 для SKU 'a','b' (упрощённо: 16 кор/пал × ppb 10). Разный upp — 'c'=150.
    const UPP: Record<string, number> = { a: 160, b: 160, c: 150 };
    const upp = (k: string): number | null => UPP[k] ?? null;
    const sum = (o: Record<string, number>) => Object.values(o).reduce((s, v) => s + v, 0);
    const fillOf = (km: Record<string, number>) =>
        Object.entries(km).reduce((s, [k, u]) => { const p = upp(k); return s + (p ? u / p : 0); }, 0);

    it('один SKU: 165 → ровно 160 (1 целая), снято 5', () => {
        const r = snapToWholePallets({ a: 165 }, upp);
        expect(r.kept).toEqual({ a: 160 });
        expect(r.dropped).toEqual({ a: 5 });
    });

    it('один SKU: 175 → 160 (снято 15)', () => {
        const r = snapToWholePallets({ a: 175 }, upp);
        expect(r.kept).toEqual({ a: 160 });
        expect(r.dropped).toEqual({ a: 15 });
    });

    it('один SKU: 331 → 320 (2 целых, снято 11)', () => {
        const r = snapToWholePallets({ a: 331 }, upp);
        expect(r.kept).toEqual({ a: 320 });
        expect(sum(r.dropped)).toBe(11);
    });

    it('город < целой паллеты (120 = 0.75) → снято всё, kept пуст', () => {
        const r = snapToWholePallets({ a: 120 }, upp);
        expect(r.kept).toEqual({});
        expect(r.dropped).toEqual({ a: 120 });
    });

    it('ровно целые (320 = 2 пал) → без изменений', () => {
        const r = snapToWholePallets({ a: 320 }, upp);
        expect(r.kept).toEqual({ a: 320 });
        expect(r.dropped).toEqual({});
    });

    it('смешанная одинаковый upp: {a:160,b:171} → footprint ровно 2.0', () => {
        const r = snapToWholePallets({ a: 160, b: 171 }, upp);
        expect(Math.abs(fillOf(r.kept) - 2)).toBeLessThan(1e-9); // целое
        expect(sum(r.kept) + sum(r.dropped)).toBe(331);          // сохранение
    });

    it('смешанная РАЗНЫЙ upp: footprint ≥ keep (не ниже целого → идемпотентно), в пределах штуки', () => {
        // a:163 (1.019) + c:151 (1.007) = 2.025 → keep 2; снимаем избыток floor-ом, чтобы
        // footprint НЕ ушёл ниже 2 целых паллет (иначе повторный прогон уронил бы остаток).
        const r = snapToWholePallets({ a: 163, c: 151 }, upp);
        const f = fillOf(r.kept);
        expect(f).toBeGreaterThanOrEqual(2 - 1e-9);
        expect(Math.abs(f - Math.round(f))).toBeLessThan(0.02);
        expect(sum(r.kept) + sum(r.dropped)).toBe(314);
    });

    it('ИДЕМПОТЕНТНОСТЬ: повторный snap ничего не меняет (смешанный разный upp)', () => {
        const once = snapToWholePallets({ a: 163, c: 151 }, upp).kept;
        const twice = snapToWholePallets(once, upp);
        expect(twice.kept).toEqual(once);
        expect(twice.dropped).toEqual({});
    });

    it('не палетизируемый SKU (upp=null) не режется; считается только палетизируемый', () => {
        // 'x' без upp остаётся; 'a' 120 (0.75) < паллеты снимается.
        const r = snapToWholePallets({ a: 120, x: 999 }, (k) => (k === 'a' ? 160 : null));
        expect(r.kept).toEqual({ x: 999 });
        expect(r.dropped).toEqual({ a: 120 });
    });

    it('всё непалетизируемое → без изменений (паллеты не посчитать)', () => {
        const r = snapToWholePallets({ x: 50, y: 30 }, () => null);
        expect(r.kept).toEqual({ x: 50, y: 30 });
        expect(r.dropped).toEqual({});
    });
});
