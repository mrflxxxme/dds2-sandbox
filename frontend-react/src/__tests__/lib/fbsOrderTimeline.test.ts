/**
 * Словарь таймлайна «Статус заказа» (`GET /fbs/orders/{id}/timeline`) —
 * чистая логика UI.
 *
 * Что закрепляется:
 *  1. базовые коды событий (`created` / `assembling` / `assembled` / `scanned`
 *     / `written_off`) имеют человеческий ярлык. Промах словаря в рантайме
 *     невидим — сработает фолбэк «показать код как есть», и в модалке вылезет
 *     `written_off`; контракт держится только этой парой тестов (зеркало на
 *     бэке — docstring FbsOrderTimelineEvent);
 *  2. префиксные коды резолвятся словарями осей: `wb:<status>` →
 *     WB_STATUS_LABEL, `supplier:<status>` → SUPPLIER_STATUS_LABEL — тексты
 *     статусов в таймлайне ОБЯЗАНЫ совпадать с бейджами списков, иначе один
 *     и тот же переход называется в двух местах по-разному;
 *  3. неизвестный код любого вида показывается как есть, а не «—».
 */
import { describe, expect, it } from 'vitest';
import {
    SUPPLIER_STATUS_LABEL,
    TIMELINE_APPROX_HINT,
    TIMELINE_LABEL,
    WB_STATUS_LABEL,
    timelineLabel,
} from '@/app/(main)/p/[slug]/warehouse/fbs/fbsShared';

/**
 * Базовые коды, которые РЕАЛЬНО отдаёт бэкенд (docstring
 * `FbsOrderTimelineEvent`: created | assembling | assembled | scanned |
 * written_off | wb:<status> | supplier:<status>).
 */
const BASE_CODES = ['created', 'assembling', 'assembled', 'scanned', 'written_off'];

describe('TIMELINE_LABEL', () => {
    it('покрывает все базовые коды человеческим текстом', () => {
        for (const code of BASE_CODES) {
            const label = timelineLabel(code);
            expect(label, `код ${code} без перевода`).not.toBe(code);
            expect(label.length).toBeGreaterThan(3);
        }
    });

    it('тексты закреплены дословно — их видит пользователь в модалке', () => {
        expect(timelineLabel('created')).toBe('Оформлен');
        expect(timelineLabel('assembled')).toBe('Продавец собрал заказ');
        expect(timelineLabel('scanned')).toBe('Принят сортировочным центром (скан QR)');
        expect(timelineLabel('written_off')).toBe('Списано со склада (учёт DDS)');
    });

    it('в словаре нет мёртвых ключей, о которых бэкенд не знает', () => {
        expect(Object.keys(TIMELINE_LABEL).sort()).toEqual([...BASE_CODES].sort());
    });
});

describe('timelineLabel — резолв префиксных кодов', () => {
    it('wb:<status> берёт текст из словаря оси WB (тот же, что в списках)', () => {
        expect(timelineLabel('wb:sorted')).toBe(WB_STATUS_LABEL.sorted);
        expect(timelineLabel('wb:sold')).toBe('Получено покупателем');
        expect(timelineLabel('wb:ready_for_pickup')).toBe('Готово к выдаче');
    });

    it('supplier:<status> берёт текст из словаря оси продавца', () => {
        expect(timelineLabel('supplier:new')).toBe(SUPPLIER_STATUS_LABEL.new);
        expect(timelineLabel('supplier:confirm')).toBe('На сборке');
        expect(timelineLabel('supplier:complete')).toBe('В доставке');
        // Терминальные тоже резолвятся (разрез отмен в таймлайне): отмена
        // перевозчиком не должна вылезать машинным кодом.
        expect(timelineLabel('supplier:cancel')).toBe('Отменено');
        expect(timelineLabel('supplier:cancel_carrier')).toBe('Отменено перевозчиком');
    });

    it('wb:<клиентская отмена> резолвится словами покупателя', () => {
        expect(timelineLabel('wb:canceled_by_client')).toBe('Отмена покупателем');
        expect(timelineLabel('wb:declined_by_client')).toBe('Отказ покупателя');
    });

    it('неизвестный код любого вида показываем как есть, а не «—»', () => {
        expect(timelineLabel('mystery_code')).toBe('mystery_code');
        expect(timelineLabel('wb:mystery_status')).toBe('wb:mystery_status');
        expect(timelineLabel('supplier:mystery_status')).toBe('supplier:mystery_status');
    });
});

describe('TIMELINE_APPROX_HINT', () => {
    it('подсказка «≈» объясняет источник неточности (синк, ~5 мин)', () => {
        expect(TIMELINE_APPROX_HINT).toContain('синхронизац');
        expect(TIMELINE_APPROX_HINT).toContain('5 мин');
    });
});
