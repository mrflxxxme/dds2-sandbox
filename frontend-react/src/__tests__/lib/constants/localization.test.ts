import { describe, it, expect } from 'vitest';
import {
    KTR_TABLE,
    KRP_TABLE,
    getKtr,
    getKrp,
    IDEAL_KTR,
    IDEAL_KRP,
    DISTRICT_ORDER,
    DISTRICT_LABELS,
} from '@/lib/constants/localization';

describe('localization constants', () => {
    it('DISTRICT_ORDER and DISTRICT_LABELS cover the same keys (plus unknown)', () => {
        for (const key of DISTRICT_ORDER) {
            expect(DISTRICT_LABELS[key]).toBeTruthy();
        }
        // unknown — fallback bucket, не в DISTRICT_ORDER, но должен иметь label
        expect(DISTRICT_LABELS.unknown).toBeTruthy();
    });

    it('KTR_TABLE покрывает диапазон [0, 100] без пробелов и пересечений', () => {
        // Sort by `lo` ascending, проверяем что hi предыдущего == lo следующего.
        const sorted = [...KTR_TABLE].sort((a, b) => a[0] - b[0]);
        expect(sorted[0][0]).toBe(0);
        // Верхняя граница верхнего диапазона = 100.01 (чтобы 100% попадало).
        expect(sorted[sorted.length - 1][1]).toBeCloseTo(100.01, 2);
        for (let i = 1; i < sorted.length; i++) {
            expect(sorted[i][0]).toBeCloseTo(sorted[i - 1][1], 2);
        }
    });

    it('KRP_TABLE покрывает [0, 100] без пробелов', () => {
        const sorted = [...KRP_TABLE].sort((a, b) => a[0] - b[0]);
        expect(sorted[0][0]).toBe(0);
        expect(sorted[sorted.length - 1][1]).toBeCloseTo(100.01, 2);
        for (let i = 1; i < sorted.length; i++) {
            expect(sorted[i][0]).toBeCloseTo(sorted[i - 1][1], 2);
        }
    });

    it('getKtr возвращает идеальный 0.50 при ≥95% и худший 2.00 при <5%', () => {
        expect(getKtr(100)).toBe(IDEAL_KTR);
        expect(getKtr(95)).toBe(0.5);
        expect(getKtr(2)).toBe(2.0);
        expect(getKtr(0)).toBe(2.0);
    });

    it('getKtr на границе бакета берёт ВЕРХНИЙ бакет (lo ≤ pct < hi)', () => {
        // 95.0 → попадает в [95, 100.01) = 0.50
        expect(getKtr(95.0)).toBe(0.5);
        // 94.99 → попадает в [90, 95) = 0.60
        expect(getKtr(94.99)).toBeCloseTo(0.6, 2);
        // 60.0 → попадает в [60, 75) = 1.00
        expect(getKtr(60.0)).toBe(1.0);
        // 59.99 → [55, 60) = 1.05
        expect(getKtr(59.99)).toBeCloseTo(1.05, 2);
    });

    it('getKrp возвращает 0% (≥60%) и 2.50% (<5%)', () => {
        expect(getKrp(100)).toBe(IDEAL_KRP);
        expect(getKrp(60)).toBe(0);
        expect(getKrp(0)).toBe(2.5);
        expect(getKrp(59)).toBe(2.0);
    });

    it('эталон из DOMAIN_LOCALIZATION: 37% → ИРП ~2.10', () => {
        // Согласно тесту backend: loc=37% → KTR=1.40, KRP=2.10
        expect(getKtr(37)).toBe(1.4);
        expect(getKrp(37)).toBe(2.1);
    });

    it('эталон из DOMAIN_LOCALIZATION: 62% → KTR=1.00, KRP=0', () => {
        expect(getKtr(62)).toBe(1.0);
        expect(getKrp(62)).toBe(0);
    });
});
