import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { computeProductStatus, NEW_PRODUCT_THRESHOLD_DAYS } from '@/lib/utils/productStatus';

describe('computeProductStatus', () => {
    beforeEach(() => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date('2026-05-07T12:00:00Z'));
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it('returns "Без продаж" with badge-info for null', () => {
        const status = computeProductStatus(null);
        expect(status.label).toBe('Без продаж');
        expect(status.className).toContain('badge-info');
    });

    it('returns "Без продаж" with badge-info for undefined', () => {
        const status = computeProductStatus(undefined);
        expect(status.label).toBe('Без продаж');
        expect(status.className).toContain('badge-info');
    });

    it('returns "Без продаж" for empty string', () => {
        const status = computeProductStatus('');
        expect(status.label).toBe('Без продаж');
        expect(status.className).toContain('badge-info');
    });

    it('returns "Без продаж" for invalid date string (graceful)', () => {
        const status = computeProductStatus('not-a-date');
        expect(status.label).toBe('Без продаж');
        expect(status.className).toContain('badge-info');
    });

    it('returns "Новинка" for today\'s date', () => {
        const status = computeProductStatus('2026-05-07');
        expect(status.label).toBe('Новинка');
        expect(status.className).toContain('badge-success');
    });

    it('returns "Новинка" for 39 days ago', () => {
        // 2026-05-07 - 39 days = 2026-03-29
        const status = computeProductStatus('2026-03-29');
        expect(status.label).toBe('Новинка');
        expect(status.className).toContain('badge-success');
    });

    it('returns "Новинка" for exactly 40 days ago (boundary)', () => {
        // 2026-05-07 - 40 days = 2026-03-28
        const status = computeProductStatus('2026-03-28');
        expect(status.label).toBe('Новинка');
        expect(status.className).toContain('badge-success');
    });

    it('returns "Активный" for 41 days ago (just past boundary)', () => {
        // 2026-05-07 - 41 days = 2026-03-27
        const status = computeProductStatus('2026-03-27');
        expect(status.label).toBe('Активный');
        expect(status.className).toContain('badge-secondary');
    });

    it('returns "Активный" for one year ago', () => {
        const status = computeProductStatus('2025-05-07');
        expect(status.label).toBe('Активный');
        expect(status.className).toContain('badge-secondary');
    });

    it('exports threshold constant equal to 40', () => {
        expect(NEW_PRODUCT_THRESHOLD_DAYS).toBe(40);
    });
});
