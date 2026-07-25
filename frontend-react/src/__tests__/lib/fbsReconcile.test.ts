/**
 * Первичная сверка склада продавца FBS — чистая логика UI.
 *
 * Регресс, который она закрывает: трансляция остатков ДЕЛЬТОВАЯ. Позиция,
 * которую мы считаем в ноль и ни разу не отправляли, в WB не уезжает вовсе,
 * поэтому остаток, проставленный в кабинете руками до подключения, продолжает
 * продаваться сам по себе. Отсюда три инварианта экрана:
 *  1. обнуляем ТОЛЬКО `unmanaged` — у `mismatch` товар мы отдаём, там разошлась
 *     лишь цифра, и обнуление сняло бы живой товар с продажи;
 *  2. подтверждение обязано называть число позиций И штук — иначе согласие вслепую;
 *  3. «склад ещё не транслировался» определяется по отсутствию `qty_sent`:
 *     состояние остатка пишется только тем, что мы реально отправляли.
 */
import { describe, expect, it } from 'vitest';
import type { FbsReconcileOut, FbsReconcileRow, FbsStockRow } from '@/types/api';
import {
    RECONCILE_REASON_LABEL,
    confirmZeroText,
    isFirstTimeWarehouse,
    reconcileHeadline,
    reconcileReasonLabel,
    unmanagedRows,
    unmanagedUnits,
    zeroBlockReason,
} from '@/app/(main)/p/[slug]/warehouse/fbs/fbsReconcile';

/**
 * Коды причин, которые РЕАЛЬНО отдаёт бэкенд (`stock_service.RECONCILE_REASONS`).
 * Тот же список закреплён в `tests/test_wb_fbs_reconcile.py::test_reason_codes_are_stable`:
 * промах словаря невидим в рантайме (сработает фолбэк «показать как есть»),
 * поэтому контракт держится только этой парой тестов.
 */
const BACKEND_REASONS = [
    'not_linked', 'override_zero', 'zero', 'no_stock', 'wb_zero',
];

function row(over: Partial<FbsReconcileRow> = {}): FbsReconcileRow {
    return {
        chrt_id: 1,
        barcode: '2043788808268',
        article_seller: 'KOVER-1',
        nm_id: 100,
        wb_amount: 125,
        our_amount: 0,
        kind: 'unmanaged',
        reason: 'not_linked',
        ...over,
    };
}

function out(over: Partial<FbsReconcileOut> = {}): FbsReconcileOut {
    return {
        wb_warehouse_id: 1,
        wb_warehouse_name: 'Фрунзе Мигфул',
        checked: 0,
        unmanaged_positions: 0,
        unmanaged_units: 0,
        mismatch_positions: 0,
        rows: [],
        rows_no_chrt: 0,
        ...over,
    };
}

function stockRow(over: Partial<FbsStockRow> = {}): FbsStockRow {
    return {
        barcode: '111',
        chrt_id: 1,
        qty_ledger: 0,
        qty_ff_mirror: null,
        qty_source: 0,
        reserved_assembly: 0,
        fbs_open: 0,
        defect: 0,
        buffer: 0,
        qty_computed: 0,
        qty_available: 0,
        qty_sent: null,
        ...over,
    };
}

describe('unmanagedRows', () => {
    it('берёт только «не управляем»: расхождение цифры обнулять нельзя', () => {
        const res = out({
            rows: [
                row({ chrt_id: 1 }),
                row({ chrt_id: 2, kind: 'mismatch', our_amount: 10, wb_amount: 7, reason: null }),
                row({ chrt_id: 3 }),
            ],
        });
        expect(unmanagedRows(res).map(r => r.chrt_id)).toEqual([1, 3]);
    });

    it('сверки нет — обнулять нечего', () => {
        expect(unmanagedRows(null)).toEqual([]);
    });
});

describe('unmanagedUnits', () => {
    it('суммирует то, что реально висит в кабинете', () => {
        expect(unmanagedUnits([row({ wb_amount: 125 }), row({ chrt_id: 2, wb_amount: 5 })])).toBe(130);
    });

    it('число из JSON строкой не превращает сумму в конкатенацию', () => {
        const raw = [
            row({ wb_amount: '125' as unknown as number }),
            row({ chrt_id: 2, wb_amount: '5' as unknown as number }),
        ];
        expect(unmanagedUnits(raw)).toBe(130);
    });
});

describe('confirmZeroText', () => {
    it('называет позиции, штуки и склад', () => {
        const text = confirmZeroText(12, 1250, 'Фрунзе Мигфул');
        expect(text).toContain('12');
        // formatNumber(ru-RU) ставит неразрывный пробел разрядов — сверяем по цифрам
        expect(text.replace(/\s/g, '')).toContain('1250');
        expect(text).toContain('Фрунзе Мигфул');
        expect(text).toContain('перестанут продаваться');
    });
});

describe('isFirstTimeWarehouse', () => {
    it('ни одной отправленной позиции — склад ещё не транслировался', () => {
        expect(isFirstTimeWarehouse([stockRow(), stockRow({ chrt_id: 2 })])).toBe(true);
    });

    it('есть qty_sent — уже транслировался', () => {
        expect(isFirstTimeWarehouse([stockRow(), stockRow({ chrt_id: 2, qty_sent: 0 })])).toBe(false);
    });

    it('пустое превью считаем «не транслировался» — предупреждение дешевле молчания', () => {
        expect(isFirstTimeWarehouse([])).toBe(true);
    });
});

describe('reconcileHeadline', () => {
    it('есть неуправляемые — называет позиции и штуки', () => {
        const text = reconcileHeadline(out({ unmanaged_positions: 3, unmanaged_units: 340 }));
        expect(text).toContain('3');
        expect(text).toContain('340');
        expect(text).toContain('не управляем');
    });

    it('чисто — отдельное «расхождений нет», а не пустая фраза', () => {
        expect(reconcileHeadline(out({ checked: 500 }))).toBe('Расхождений нет');
    });

    it('только расхождение количеств — про них и говорит', () => {
        expect(reconcileHeadline(out({ mismatch_positions: 2 }))).toContain('расходится количество');
    });
});

describe('reconcileReasonLabel', () => {
    it('известная причина переводится', () => {
        expect(reconcileReasonLabel('override_zero')).toBe('не отдаём: ручное кол-во 0');
    });

    it('неизвестный код показываем как есть, а не пустую ячейку', () => {
        expect(reconcileReasonLabel('brand_new_reason')).toBe('brand_new_reason');
    });

    it('причины нет — прочерк', () => {
        expect(reconcileReasonLabel(null)).toBe('—');
    });

    it('словарь покрывает РОВНО коды бэкенда — мёртвых записей и промахов нет', () => {
        expect(Object.keys(RECONCILE_REASON_LABEL).sort()).toEqual([...BACKEND_REASONS].sort());
        for (const code of BACKEND_REASONS) {
            expect(reconcileReasonLabel(code)).not.toBe(code);
        }
    });
});

describe('zeroBlockReason', () => {
    const base = { writeEnabled: true, writeHint: 'режим safe: запись в WB выключена' };

    it('всё разрешено — пустая строка', () => {
        expect(zeroBlockReason(base)).toBe('');
    });

    it('нет привязок — блокируем: иначе обнулится весь каталог кабинета', () => {
        expect(zeroBlockReason({ ...base, noLinks: true })).toContain('не привязан');
    });

    it('склад в обработке — блокируем: WB вернёт 409 и сожжёт бакет', () => {
        expect(zeroBlockReason({ ...base, isProcessing: true })).toContain('в обработке');
    });

    it('режим наблюдения — блокируем: обнуление это запись, бэкенд отдаст 409', () => {
        expect(zeroBlockReason({ ...base, translating: false })).toContain('Наблюдение');
    });

    it('режим трансляции обнулению не мешает', () => {
        expect(zeroBlockReason({ ...base, translating: true })).toBe('');
    });

    it('запись в WB выключена режимом — показываем тот же хинт, что у пуша', () => {
        expect(zeroBlockReason({ ...base, writeEnabled: false })).toBe(base.writeHint);
    });

    it('нет привязок важнее прочего — самая дорогая ошибка называется первой', () => {
        expect(zeroBlockReason({ ...base, noLinks: true, isProcessing: true, writeEnabled: false }))
            .toContain('не привязан');
    });
});
