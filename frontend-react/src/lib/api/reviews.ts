/** Отзывы покупателей WB (feedbacks) API methods */
import { ApiClient } from './client';
import type { ReviewsListResponse } from '@/types/api';

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
    };
}
