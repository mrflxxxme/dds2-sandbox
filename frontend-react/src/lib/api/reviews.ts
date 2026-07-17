/** Отзывы покупателей WB (feedbacks) API methods */
import { ApiClient } from './client';
import type { NewcomersResponse, ReviewBreakdownGroup, ReviewBreakdownResponse, ReviewsListResponse, ReviewsPeriod, ReviewsSummaryResponse } from '@/types/api';

export interface GetReviewsParams {
    isAnswered?: boolean;
    take?: number;
    skip?: number;
    nmId?: number;
}

export interface GetBreakdownParams {
    groupBy: ReviewBreakdownGroup;
    dateFrom?: string;
    dateTo?: string;
    subject?: string;
    brand?: string;
    nmId?: number;
}

export function addReviewMethods(api: ApiClient) {
    return {
        getReviews(params: GetReviewsParams = {}) {
            const q = new URLSearchParams();
            if (params.isAnswered != null) q.set('is_answered', String(params.isAnswered));
            if (params.take != null) q.set('take', String(params.take));
            if (params.skip != null) q.set('skip', String(params.skip));
            if (params.nmId != null) q.set('nm_id', String(params.nmId));
            const qs = q.toString();
            return api.request<ReviewsListResponse>('GET', `/api/v1/reviews${qs ? `?${qs}` : ''}`);
        },
        // Детальная таблица отзывов с группировкой (Динамика)
        getReviewsBreakdown(params: GetBreakdownParams) {
            const q = new URLSearchParams();
            q.set('group_by', params.groupBy);
            if (params.dateFrom) q.set('date_from', params.dateFrom);
            if (params.dateTo) q.set('date_to', params.dateTo);
            if (params.subject) q.set('subject', params.subject);
            if (params.brand) q.set('brand', params.brand);
            if (params.nmId != null) q.set('nm_id', String(params.nmId));
            return api.request<ReviewBreakdownResponse>('GET', `/api/v1/reviews/breakdown?${q.toString()}`);
        },
        // Негативные отзывы проблемных новинок, содержащие слово `term` (клик по теме жалоб)
        getComplaintReviews(term: string, days?: number, maxRating?: number) {
            const q = new URLSearchParams();
            q.set('term', term);
            if (days != null) q.set('days', String(days));
            if (maxRating != null) q.set('max_rating', String(maxRating));
            return api.request<ReviewsListResponse>('GET', `/api/v1/reviews/complaint-reviews?${q.toString()}`);
        },
        getReviewsNewcomers(days?: number, maxRating?: number) {
            const q = new URLSearchParams();
            if (days != null) q.set('days', String(days));
            if (maxRating != null) q.set('max_rating', String(maxRating));
            const qs = q.toString();
            return api.request<NewcomersResponse>('GET', `/api/v1/reviews/newcomers${qs ? `?${qs}` : ''}`);
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
