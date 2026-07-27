/**
 * Потоварная замена количества на вкладке «Остатки» FBS — чистая логика UI.
 *
 * Что она держит (канон владельца вместо снятой системы правил):
 *  1. пустое поле — это НЕ ноль, а «снять ограничение»: перепутать их значит
 *     снять товар с продажи там, где человек просто стёр значение;
 *  2. мусор (буквы, минус, дробь) не отправляем и откатываем поле — молчаливое
 *     превращение «-5» в 0 обнулило бы позицию без ведома пользователя;
 *  3. «что отдаём» кодируется одним полем `fbo_max_qty`: null — все остатки,
 *     0 — только то, чего нет на FBO, а «снять гейт» уезжает на бэк как -1
 *     (null в теле PATCH неотличим от «поле не передано»);
 *  4. режим склада по умолчанию — наблюдение: неизвестное значение обязано
 *     читаться как «в WB не пишем», а не наоборот.
 */
import { describe, expect, it } from 'vitest';
import type { FbsStockRow } from '@/types/api';
import {
    boxStateOf,
    matchesBoxFilter,
    parseOverrideInput,
    qtyCellInitial,
    stockAlertOf,
    stockRowClassName,
} from '@/app/(main)/p/[slug]/warehouse/fbs/stockColumns';
import {
    blockedReasonLabel,
    giveModeOf,
    giveModePayloadValue,
    isTranslating,
    stockSourceOf,
    warehouseModeLabel,
} from '@/app/(main)/p/[slug]/warehouse/fbs/fbsShared';

describe('parseOverrideInput', () => {
    it('пустое поле = снять ограничение, а не ноль', () => {
        expect(parseOverrideInput('')).toEqual({ ok: true, qty: null });
        expect(parseOverrideInput('   ')).toEqual({ ok: true, qty: null });
    });

    it('ноль сохраняется как ноль — «не отдавать»', () => {
        expect(parseOverrideInput('0')).toEqual({ ok: true, qty: 0 });
    });

    it('положительное число обрезается до целого', () => {
        expect(parseOverrideInput('12')).toEqual({ ok: true, qty: 12 });
        expect(parseOverrideInput('12,7')).toEqual({ ok: true, qty: 12 });
        expect(parseOverrideInput(' 5 ')).toEqual({ ok: true, qty: 5 });
    });

    it('мусор и отрицательные не отправляем', () => {
        expect(parseOverrideInput('abc').ok).toBe(false);
        expect(parseOverrideInput('-5').ok).toBe(false);
        expect(parseOverrideInput('1e').ok).toBe(false);
    });
});

describe('что отдаём (fbo_max_qty)', () => {
    it('null — все остатки', () => {
        expect(giveModeOf(null)).toBe('all');
        expect(giveModeOf(undefined)).toBe('all');
    });

    it('0 — только то, чего нет на FBO', () => {
        expect(giveModeOf(0)).toBe('no_fbo');
    });

    it('«снять гейт» уезжает как -1, гейт — как 0', () => {
        expect(giveModePayloadValue('all')).toBe(-1);
        expect(giveModePayloadValue('no_fbo')).toBe(0);
    });
});

/**
 * Источник остатка снова выбирается руками. Нормализация важна: карточки, заведённые
 * до возврата выбора, несут легаси-значения, и промах читался бы как «наш учёт» —
 * то есть склад, которым управляет WMS, молча отдавал бы по нашим отстающим книгам.
 */
describe('stockSourceOf', () => {
    it('явные значения проходят как есть', () => {
        expect(stockSourceOf('ledger')).toBe('ledger');
        expect(stockSourceOf('ff_mirror')).toBe('ff_mirror');
        expect(stockSourceOf('min_of_both')).toBe('min_of_both');
    });

    it('пустое и неизвестное — консервативный минимум, а не «наш учёт»', () => {
        expect(stockSourceOf(null)).toBe('min_of_both');
        expect(stockSourceOf(undefined)).toBe('min_of_both');
        expect(stockSourceOf('')).toBe('min_of_both');
        expect(stockSourceOf('какой-то новый режим')).toBe('min_of_both');
    });
});

describe('режим склада', () => {
    it('пишем в WB только в явной трансляции', () => {
        expect(isTranslating('translate')).toBe(true);
        expect(isTranslating('observe')).toBe(false);
        expect(isTranslating(null)).toBe(false);
        expect(isTranslating('какая-то новая строка')).toBe(false);
    });

    it('подпись режима — человеческая, неизвестное значение показываем как есть', () => {
        expect(warehouseModeLabel('observe')).toBe('Наблюдение');
        expect(warehouseModeLabel('translate')).toBe('Трансляция');
        expect(warehouseModeLabel(null)).toBe('Наблюдение');
        expect(warehouseModeLabel('new_mode')).toBe('new_mode');
    });
});

/**
 * Коды `blocked_reason`, которые РЕАЛЬНО отдаёт бэкенд
 * (`stock_service.BLOCKED_REASONS`). Тот же список закреплён в
 * `tests/test_wb_fbs_overrides.py::test_blocked_reason_codes_are_stable`:
 * промах словаря невидим в рантайме (сработает фолбэк «показать код как есть»),
 * и машинный код уезжает в колонку «Причина» и в Excel-выгрузку.
 */
const BACKEND_BLOCKED_REASONS = ['no_chrt', 'override_zero', 'fbo_in_stock'];

describe('blockedReasonLabel', () => {
    it('блокировка по FBO объясняется словами — по коду, который шлёт бэкенд', () => {
        expect(blockedReasonLabel('fbo_in_stock')).toBe('есть на FBO — в FBS не отдаём');
    });

    it('исторический ключ fbo_present остался алиасом на тот же текст', () => {
        expect(blockedReasonLabel('fbo_present')).toBe('есть на FBO — в FBS не отдаём');
    });

    it('словарь покрывает ВСЕ коды бэкенда — фолбэка на сырой код не случается', () => {
        for (const code of BACKEND_BLOCKED_REASONS) {
            expect(blockedReasonLabel(code)).not.toBe(code);
        }
    });

    it('неизвестная причина показывается как есть, а не пустой ячейкой', () => {
        expect(blockedReasonLabel('brand_new')).toBe('brand_new');
    });
});

/**
 * Расхождения с кабинетом WB — два состояния, которые стоят денег, и оба
 * невидимы без подсветки: «в WB больше, чем отдадим» (WB продаёт то, чего на
 * складе нет → отмена и штраф) и «в WB ноль, а товар есть» (позиция просто не
 * продаётся). Ключевая граница — `qty_wb == null`: это «кабинет не прочитан или
 * нет chrtId», и принять его за ноль значит поджечь ВЕСЬ каталог ложным
 * «в WB ноль».
 */
describe('stockAlertOf', () => {
    const row = (qty_wb: number | null, qty_available: number): Pick<FbsStockRow, 'qty_wb' | 'qty_available'> =>
        ({ qty_wb, qty_available });

    it('кабинет не прочитан — сигнала нет, а не «в WB ноль»', () => {
        expect(stockAlertOf(row(null, 50))).toBeNull();
        expect(stockAlertOf(row(null, 0))).toBeNull();
    });

    it('в кабинете больше, чем отдадим — WB продаёт то, чего нет', () => {
        expect(stockAlertOf(row(100, 30))).toBe('over');
        // «Не отдавать» (0), а в кабинете товар живой — то же расхождение
        expect(stockAlertOf(row(5, 0))).toBe('over');
    });

    it('в кабинете ноль при живом остатке — позиция не продаётся', () => {
        expect(stockAlertOf(row(0, 12))).toBe('missing');
    });

    it('честный ноль с обеих сторон и совпадение — не сигнал', () => {
        expect(stockAlertOf(row(0, 0))).toBeNull();
        expect(stockAlertOf(row(30, 30))).toBeNull();
    });

    it('недоотдача (в кабинете меньше, но не ноль) сигналом не считается — её видно дельтой', () => {
        expect(stockAlertOf(row(5, 30))).toBeNull();
    });

    it('класс строки идёт из того же правила — подсветка не расходится со счётчиком', () => {
        expect(stockRowClassName(row(100, 30))).toBe('fbs-row-over');
        expect(stockRowClassName(row(0, 12))).toBe('fbs-row-missing');
        expect(stockRowClassName(row(30, 30))).toBe('');
        expect(stockRowClassName(row(null, 30))).toBe('');
    });

    // Numeric-поля бэка приезжают СТРОКОЙ — сравнение строк дало бы '100' > '30' === false
    it('строковые числа из JSON сравниваются как числа', () => {
        const raw = { qty_wb: '100', qty_available: '30' } as unknown as Pick<FbsStockRow, 'qty_wb' | 'qty_available'>;
        expect(stockAlertOf(raw)).toBe('over');
    });
});

/**
 * Коробá у Натали: товар лежит, но продать штуку из невскрытого короба нельзя.
 * Три среза от «просто лежит» к «мёртвый груз» — это очередь на поштучную приёмку,
 * и приоритет в ней задаёт именно `dead`: ни по FBS, ни с FBO такой товар не идёт.
 */
describe('boxStateOf', () => {
    const row = (boxed: number, source: number, fbo: number | null | undefined) =>
        ({ qty_ff_boxed: boxed, qty_source: source, fbo_qty: fbo }) as
            Pick<FbsStockRow, 'qty_ff_boxed' | 'qty_source' | 'fbo_qty'>;

    it('коробов нет — состояние неприменимо', () => {
        expect(boxStateOf(row(0, 50, 0))).toBeNull();
    });

    it('есть и коробá, и россыпь — товар продаётся, короба про запас', () => {
        expect(boxStateOf(row(500, 24, 0))).toBe('boxed');
    });

    it('коробá есть, россыпи нет, но на FBO продаётся', () => {
        expect(boxStateOf(row(500, 0, 137))).toBe('no_loose');
    });

    it('ни россыпи, ни FBO — мёртвый груз, первый на вскрытие', () => {
        expect(boxStateOf(row(500, 0, 0))).toBe('dead');
    });

    // FBO не прочитан — это не подтверждённый ноль, и в «не продаётся нигде» такое
    // попасть не должно: иначе список приоритетов раздувается догадками.
    it('FBO неизвестен — не «мёртвый», а просто «нет в остатке»', () => {
        expect(boxStateOf(row(500, 0, null))).toBe('no_loose');
        expect(boxStateOf(row(500, 0, undefined))).toBe('no_loose');
    });

    it('фильтры вложены: dead ⊂ no_loose ⊂ boxed', () => {
        const dead = row(500, 0, 0);
        expect(matchesBoxFilter(dead, 'boxed')).toBe(true);
        expect(matchesBoxFilter(dead, 'no_loose')).toBe(true);
        expect(matchesBoxFilter(dead, 'dead')).toBe(true);

        const withLoose = row(500, 24, 0);
        expect(matchesBoxFilter(withLoose, 'boxed')).toBe(true);
        expect(matchesBoxFilter(withLoose, 'no_loose')).toBe(false);

        expect(matchesBoxFilter(row(0, 24, 0), 'boxed')).toBe(false);
    });
});

/**
 * Поле «Кол-во» — редактируемая версия цифры кабинета: пока своего числа нет,
 * показываем то, что реально стоит в WB. Пустое поле осталось только там, где
 * подставлять нечего (кабинет не прочитан) — иначе «расчёт» и «ноль в WB»
 * выглядели бы одинаково.
 */
describe('qtyCellInitial', () => {
    it('своё ручное количество важнее цифры кабинета', () => {
        expect(qtyCellInitial({ override_qty: 5, qty_wb: 100 })).toBe('5');
        expect(qtyCellInitial({ override_qty: 0, qty_wb: 100 })).toBe('0');
    });

    it('без ручного количества подставляется остаток кабинета — включая ноль', () => {
        expect(qtyCellInitial({ override_qty: null, qty_wb: 100 })).toBe('100');
        expect(qtyCellInitial({ override_qty: null, qty_wb: 0 })).toBe('0');
    });

    it('кабинет не прочитан — поле пустое, работает обычный расчёт', () => {
        expect(qtyCellInitial({ override_qty: null, qty_wb: null })).toBe('');
        expect(qtyCellInitial({})).toBe('');
    });
});
