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
import { parseOverrideInput } from '@/app/(main)/p/[slug]/warehouse/fbs/stockColumns';
import {
    blockedReasonLabel,
    giveModeOf,
    giveModePayloadValue,
    isTranslating,
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
