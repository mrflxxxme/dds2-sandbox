import type { RawRefreshProgress } from '@/types/api';

export type ProgressMap = Record<string, RawRefreshProgress>;

// Предохранитель от «вечно загружается…»: если бэк перестал сообщать про
// running-джоб (рестарт воркера / очищенная in-memory карта), а по нашим часам
// он висит дольше лимита — снимаем бейдж, чтобы поллинг не залип навсегда.
export const MAX_RUNNING_MS = 8 * 60_000;

/** Слить серверный прогресс с оптимистичным, не залипая и не обрываясь рано.
 *  Сервер — источник истины; но одиночный пустой ответ (гонка/рестарт) НЕ
 *  затирает наш оптимистичный `running` — только по таймауту MAX_RUNNING_MS. */
export function reconcileProgress(prev: ProgressMap, server: ProgressMap): ProgressMap {
    const merged: ProgressMap = { ...prev, ...server };
    for (const [key, p] of Object.entries(merged)) {
        if (p.status === 'running' && !server[key]) {
            const started = new Date(p.started_at).getTime();
            if (Number.isFinite(started) && Date.now() - started > MAX_RUNNING_MS) {
                delete merged[key];
            }
        }
    }
    return merged;
}
