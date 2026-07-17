/** Vibecoding API — опись работы вайбкодера: ритм, объём, лента поставок. */
import { ApiClient } from './client';
import type { VibeStats } from '@/types/api';

/** 403 = «не вайбкодер». Не ошибка загрузки, а отсутствие доступа к вкладке. */
export function isForbidden(e: unknown): boolean {
    return (e as { status?: number } | null)?.status === 403;
}

export function addVibeMethods(api: ApiClient) {
    return {
        /** Статистика текущего пользователя за период. 403 — не вайбкодер. */
        getVibeStats(params?: { since?: string; until?: string }) {
            const q = new URLSearchParams();
            if (params?.since) q.set('since', params.since);
            if (params?.until) q.set('until', params.until);
            const qs = q.toString();
            return api.request<VibeStats>('GET', `/api/v1/vibe/me${qs ? `?${qs}` : ''}`);
        },

        /**
         * Показывать ли пункт сайдбара. Доступ решает строка в vibe_authors, а НЕ роль:
         * клиент-селлер — owner своего проекта и прошёл бы любую проверку по роли.
         *
         * Ответ нормализуется в boolean: терпим и голый `true`, и `{is_vibecoder: true}`.
         * Любой сбой (сеть, 403, старый бэкенд без эндпоинта) → false: вкладку видно
         * только при ЯВНОМ «да» — незнание закрывает, а не открывает.
         */
        async getIsVibecoder(): Promise<boolean> {
            try {
                const res = await api.request<boolean | { is_vibecoder?: boolean }>(
                    'GET', '/api/v1/vibe/is-vibecoder',
                );
                if (typeof res === 'boolean') return res;
                return res?.is_vibecoder === true;
            } catch {
                return false;
            }
        },
    };
}
