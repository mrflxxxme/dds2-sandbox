/** Отзывы покупателей WB (feedbacks) API methods */
import { ApiClient } from './client';
import type { ComplaintBulkResult, ComplaintCandidatesResponse, ComplaintItem, ComplaintReason, ComplaintStatus, ComplaintsResponse, KbCreate, KbImportResult, KbItem, KbListResponse, KbProductsResponse, KbUpdate, NewcomersResponse, QuestionsListResponse, QuestionsSyncResult, RepliesListResponse, Reply, ReplyAction, ReplyAgent, ReplyAgentRunResult, ReplyAgentSave, ReplySendResult, ReplyStatus, ReplyTargetType, ReviewBreakdownGroup, ReviewBreakdownResponse, ReviewsListResponse, ReviewsPeriod, ReviewsSummaryResponse } from '@/types/api';

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
        // Жалобы на отзывы (для удаления)
        getComplaintCandidates(maxRating = 3, take = 100, onlyOpen = true) {
            const q = new URLSearchParams({ max_rating: String(maxRating), take: String(take), only_open: String(onlyOpen) });
            return api.request<ComplaintCandidatesResponse>('GET', `/api/v1/reviews/complaints/candidates?${q.toString()}`);
        },
        getComplaints(status?: ComplaintStatus) {
            const q = new URLSearchParams();
            if (status) q.set('status', status);
            const qs = q.toString();
            return api.request<ComplaintsResponse>('GET', `/api/v1/reviews/complaints${qs ? `?${qs}` : ''}`);
        },
        createComplaint(body: { wb_feedback_id: string; reason: ComplaintReason; text: string }) {
            return api.request<ComplaintItem>('POST', '/api/v1/reviews/complaints', body);
        },
        // Массовая подача жалоб на все накопившиеся отзывы 1–3★
        createComplaintsBulk(body: { reason: ComplaintReason; text: string; max_rating?: number }) {
            return api.request<ComplaintBulkResult>('POST', '/api/v1/reviews/complaints/bulk', body);
        },
        updateComplaint(id: number, body: { status: ComplaintStatus; note?: string | null }) {
            return api.request<ComplaintItem>('PATCH', `/api/v1/reviews/complaints/${id}`, body);
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
        // ─── Вопросы покупателей (зеркало wb_questions) ───
        getQuestions(params: { isAnswered?: boolean; take?: number; skip?: number } = {}) {
            const q = new URLSearchParams();
            if (params.isAnswered != null) q.set('is_answered', String(params.isAnswered));
            if (params.take != null) q.set('take', String(params.take));
            if (params.skip != null) q.set('skip', String(params.skip));
            const qs = q.toString();
            return api.request<QuestionsListResponse>('GET', `/api/v1/reviews/questions${qs ? `?${qs}` : ''}`);
        },
        // On-demand синк вопросов из WB в зеркало
        syncQuestions() {
            return api.request<QuestionsSyncResult>('POST', '/api/v1/reviews/questions/sync');
        },
        // ─── ИИ-агенты автоответов ───
        getReplyAgents() {
            return api.request<ReplyAgent[]>('GET', '/api/v1/reviews/reply-agents');
        },
        createReplyAgent(body: ReplyAgentSave) {
            return api.request<ReplyAgent>('POST', '/api/v1/reviews/reply-agents', body);
        },
        updateReplyAgent(id: number, body: ReplyAgentSave) {
            return api.request<ReplyAgent>('PATCH', `/api/v1/reviews/reply-agents/${id}`, body);
        },
        deleteReplyAgent(id: number) {
            return api.request<{ deleted: boolean; id: number }>('DELETE', `/api/v1/reviews/reply-agents/${id}`);
        },
        // Прогон агента: LLM генерирует черновики ответов
        runReplyAgent(id: number) {
            return api.request<ReplyAgentRunResult>('POST', `/api/v1/reviews/reply-agents/${id}/run`);
        },
        // ─── Ответы на отзывы/вопросы (черновики → отправка) ───
        getReplies(params: { status?: ReplyStatus; take?: number; skip?: number } = {}) {
            const q = new URLSearchParams();
            if (params.status) q.set('status', params.status);
            if (params.take != null) q.set('take', String(params.take));
            if (params.skip != null) q.set('skip', String(params.skip));
            const qs = q.toString();
            return api.request<RepliesListResponse>('GET', `/api/v1/reviews/replies${qs ? `?${qs}` : ''}`);
        },
        // Ручной черновик ответа (source=manual)
        createReply(body: { target_type: ReplyTargetType; target_wb_id: string; text: string }) {
            return api.request<Reply>('POST', '/api/v1/reviews/replies', body);
        },
        // Правка текста и/или модерация (approve|reject|reopen)
        updateReply(id: number, body: { text?: string; action?: ReplyAction }) {
            return api.request<Reply>('PATCH', `/api/v1/reviews/replies/${id}`, body);
        },
        // Отправить все approved (202, отправка фоном)
        sendReplies() {
            return api.request<ReplySendResult>('POST', '/api/v1/reviews/replies/send');
        },
        // ─── База знаний товаров (wb_product_kb) ───
        getKbProducts() {
            return api.request<KbProductsResponse>('GET', '/api/v1/reviews/kb/products');
        },
        getKbList(params: { nmId?: number; enabled?: boolean; take?: number; skip?: number } = {}) {
            const q = new URLSearchParams();
            if (params.nmId != null) q.set('nm_id', String(params.nmId));
            if (params.enabled != null) q.set('enabled', String(params.enabled));
            if (params.take != null) q.set('take', String(params.take));
            if (params.skip != null) q.set('skip', String(params.skip));
            const qs = q.toString();
            return api.request<KbListResponse>('GET', `/api/v1/reviews/kb${qs ? `?${qs}` : ''}`);
        },
        createKb(body: KbCreate) {
            return api.request<KbItem>('POST', '/api/v1/reviews/kb', body);
        },
        updateKb(id: number, body: KbUpdate) {
            return api.request<KbItem>('PATCH', `/api/v1/reviews/kb/${id}`, body);
        },
        deleteKb(id: number) {
            return api.request<{ deleted: boolean; id: number }>('DELETE', `/api/v1/reviews/kb/${id}`);
        },
        // Импорт КБ из архива отвеченных вопросов (идемпотентно)
        importKb() {
            return api.request<KbImportResult>('POST', '/api/v1/reviews/kb/import');
        },
    };
}
