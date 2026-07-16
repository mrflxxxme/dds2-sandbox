/** Отзывы покупателей WB (feedbacks) API methods */
import { ApiClient } from './client';
import type { ReviewsListResponse, ReviewsPeriod, ReviewsSummaryResponse } from '@/types/api';

export interface GetReviewsParams {
    isAnswered?: boolean;
    take?: number;
    skip?: number;
}

export function addReviewMethods(api: ApiClient) {
    return {
        getReviews(params: GetReviewsParams = {}) {
            const q = new URLSearchParams();
            if (params.isAnswered != null) q.set('is_answered', String(params.isAnswered));
            if (params.take != null) q.set('take', String(params.take));
            if (params.skip != null) q.set('skip', String(params.skip));
            const qs = q.toString();
            return api.request<ReviewsListResponse>('GET', `/api/v1/reviews${qs ? `?${qs}` : ''}`);
        },
        getReviewsSummary(tag?: string, period?: ReviewsPeriod) {
            const q = new URLSearchParams();
            if (tag) q.set('tag', tag);
            if (period) q.set('period', period);
            const qs = q.toString();
            return api.request<ReviewsSummaryResponse>('GET', `/api/v1/reviews/summary${qs ? `?${qs}` : ''}`);
        },
        // On-demand подтяжка отзывов из WB → возвращает свежую сводку (опц. по ярлыку/периоду)
        syncReviews(tag?: string, period?: ReviewsPeriod) {
            const q = new URLSearchParams();
            if (tag) q.set('tag', tag);
            if (period) q.set('period', period);
            const qs = q.toString();
            return api.request<ReviewsSummaryResponse>('POST', `/api/v1/reviews/sync${qs ? `?${qs}` : ''}`);
        },
    };
}
