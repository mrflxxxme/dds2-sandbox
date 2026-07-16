import { describe, it, expect, vi, afterEach } from 'vitest';
import { reconcileProgress } from '@/lib/rawDataProgress';
import type { RawRefreshProgress } from '@/types/api';

const running = (started: string): RawRefreshProgress => ({
    status: 'running', started_at: started, finished_at: null, error: null,
});
const ok = (): RawRefreshProgress => ({
    status: 'ok', started_at: '2026-07-16T04:00:00Z', finished_at: '2026-07-16T04:00:05Z', error: null,
});

describe('reconcileProgress — сверка прогресса дозагрузок', () => {
    afterEach(() => vi.useRealTimers());

    it('серверный статус ok перекрывает оптимистичный running', () => {
        const prev = { orders: running('2026-07-16T04:00:00Z') };
        const out = reconcileProgress(prev, { orders: ok() });
        expect(out.orders.status).toBe('ok');
    });

    it('одиночный пустой ответ сервера НЕ обрывает свежий running (гонка/рестарт)', () => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date('2026-07-16T04:00:30Z')); // 30с с момента старта
        const prev = { orders: running('2026-07-16T04:00:00Z') };
        const out = reconcileProgress(prev, {}); // сервер временно не сообщил про джоб
        expect(out.orders?.status).toBe('running'); // всё ещё крутится — поллинг не залипнет и не оборвётся
    });

    it('running снимается по таймауту, если сервер давно про него молчит (защита от вечного «загружается…»)', () => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date('2026-07-16T04:10:00Z')); // 10 мин > MAX_RUNNING_MS (8 мин)
        const prev = { orders: running('2026-07-16T04:00:00Z') };
        const out = reconcileProgress(prev, {});
        expect(out.orders).toBeUndefined();
    });

    it('running с ключом в серверном ответе не снимается по таймауту (сервер подтверждает)', () => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date('2026-07-16T04:10:00Z'));
        const prev = { orders: running('2026-07-16T04:00:00Z') };
        const out = reconcileProgress(prev, { orders: running('2026-07-16T04:00:00Z') });
        expect(out.orders?.status).toBe('running');
    });
});
