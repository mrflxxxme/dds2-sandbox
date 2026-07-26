/**
 * Поставки FBS: состояние словами кабинета WB, число заданий и итог обратной
 * загрузки истории.
 *
 * Инварианты, закреплённые тут:
 *  1. Состояний ЧЕТЫРЕ, а не два. `done ? 'Передана' : 'Активная'` схлопывал
 *     «Отгрузите поставку» (лежит у нас, QR не отсканирован), «В доставке» и
 *     «Отклонена» в одну строку — экран расходился с кабинетом WB.
 *  2. Разложение зеркалит `models.wb_fbs.supply_status`: `reject_dt` перебивает
 *     всё (отклонённая поставка остаётся `done=true` со сканом), `scan_dt`
 *     отделяет «не отгружена» от «уехала».
 *  3. Заданий показываем СТОЛЬКО, СКОЛЬКО ЗНАЕТ WB: наше зеркало до бэкфилла
 *     истории почти везде пустое, и «0» там, где WB говорит «11», — ложь.
 */
import { describe, expect, it } from 'vitest';
import {
    SUPPLY_STATUSES,
    SUPPLY_STATUS_LABEL,
    backfillPeriodLabel,
    backfillResultMessage,
    supplyOrdersCount,
    supplyOrdersMirrorHint,
    supplyStatusOf,
} from '@/app/(main)/p/[slug]/warehouse/fbs/fbsShared';
import type { FbsOrderBackfillResult, FbsSupply } from '@/types/api';

type StatusInput = Pick<FbsSupply, 'status' | 'done' | 'scan_dt' | 'reject_dt'>;

function supply(over: Partial<StatusInput> = {}): StatusInput {
    return { status: '', done: false, scan_dt: null, reject_dt: null, ...over };
}

describe('supplyStatusOf', () => {
    it('готовое поле статуса с бэка берётся как есть', () => {
        expect(supplyStatusOf(supply({ status: 'in_delivery', done: true }))).toBe('in_delivery');
        expect(supplyStatusOf(supply({ status: 'rejected', done: true }))).toBe('rejected');
    });

    it('фолбэк без поля статуса: активная / к отгрузке / в доставке', () => {
        expect(supplyStatusOf(supply({ done: false }))).toBe('active');
        expect(supplyStatusOf(supply({ done: true }))).toBe('to_ship');
        expect(supplyStatusOf(supply({ done: true, scan_dt: '2026-07-20T10:00:00' }))).toBe('in_delivery');
    });

    it('reject_dt перебивает done и scan_dt — отклонённая остаётся отклонённой', () => {
        expect(supplyStatusOf(supply({
            done: true, scan_dt: '2026-07-20T10:00:00', reject_dt: '2026-07-21T09:00:00',
        }))).toBe('rejected');
    });

    it('незнакомое значение статуса не роняет бейдж — раскладываем по флагам', () => {
        expect(supplyStatusOf(supply({ status: 'что-то новое', done: true }))).toBe('to_ship');
    });
});

describe('SUPPLY_STATUS_LABEL', () => {
    it('покрывает все четыре состояния и не оставляет пустых ярлыков', () => {
        expect(SUPPLY_STATUSES).toEqual(['active', 'to_ship', 'in_delivery', 'rejected']);
        for (const st of SUPPLY_STATUSES) {
            expect(SUPPLY_STATUS_LABEL[st].label.length).toBeGreaterThan(0);
            expect(SUPPLY_STATUS_LABEL[st].hint.length).toBeGreaterThan(0);
        }
    });

    it('классы бейджей — из дизайн-системы, по смыслу состояния', () => {
        expect(SUPPLY_STATUS_LABEL.active.badge).toBe('badge-info');
        expect(SUPPLY_STATUS_LABEL.to_ship.badge).toBe('badge-warning');
        expect(SUPPLY_STATUS_LABEL.in_delivery.badge).toBe('badge-success');
        expect(SUPPLY_STATUS_LABEL.rejected.badge).toBe('badge-danger');
    });
});

describe('supplyOrdersCount', () => {
    it('данные WB важнее зеркала: 11 у WB против 0 у нас → показываем 11', () => {
        expect(supplyOrdersCount({ orders_count: 0, wb_orders_count: 11 })).toBe(11);
    });

    it('WB честно сказал «ноль» — фолбэка на зеркало НЕ делаем', () => {
        expect(supplyOrdersCount({ orders_count: 5, wb_orders_count: 0 })).toBe(0);
    });

    it('состав у WB не спрашивали (null) — остаётся наше зеркало', () => {
        expect(supplyOrdersCount({ orders_count: 7, wb_orders_count: null })).toBe(7);
        expect(supplyOrdersCount({ orders_count: 7 })).toBe(7);
    });
});

describe('supplyOrdersMirrorHint', () => {
    it('расхождение подписывается человеком', () => {
        const hint = supplyOrdersMirrorHint({ orders_count: 0, wb_orders_count: 11 });
        expect(hint).toContain('в зеркале 0 из 11');
    });

    it('цифры сошлись или состав не спрашивали — подсказки нет', () => {
        expect(supplyOrdersMirrorHint({ orders_count: 11, wb_orders_count: 11 })).toBeNull();
        expect(supplyOrdersMirrorHint({ orders_count: 3, wb_orders_count: null })).toBeNull();
    });
});

describe('backfillPeriodLabel', () => {
    it('месяцы и недели читаются словами, остальное — днями', () => {
        expect(backfillPeriodLabel(90)).toBe('за 3 месяца');
        expect(backfillPeriodLabel(30)).toBe('за 1 месяц');
        expect(backfillPeriodLabel(7)).toBe('за неделю');
        expect(backfillPeriodLabel(5)).toBe('за 5 дней');
        expect(backfillPeriodLabel(1)).toBe('за 1 день');
    });
});

describe('backfillResultMessage', () => {
    const base: FbsOrderBackfillResult = {
        ok: true, fetched: 1240, upserted: 1240, written_off_marked: 0, windows: 3, message: null,
    };

    it('итог — человеческой фразой, а не сырыми полями', () => {
        // `formatNumber` разделяет разряды НЕРАЗРЫВНЫМ пробелом (ru-RU) —
        // сравниваем по нормализованной строке, а не по литералу с обычным.
        expect(backfillResultMessage(base, 90).replace(/\s/g, ' '))
            .toBe('Загружено 1 240 заданий за 3 месяца');
    });

    it('текст бэкенда приоритетнее — он знает про усечения окон', () => {
        expect(backfillResultMessage({ ...base, message: 'WB отдал не весь период' }, 90))
            .toBe('WB отдал не весь период');
    });

    it('пометки списания показываем отдельно — это движение склада', () => {
        expect(backfillResultMessage({ ...base, written_off_marked: 900 }, 90))
            .toContain('списание отмечено у 900');
    });
});
