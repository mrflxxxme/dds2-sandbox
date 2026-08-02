/** Design Tasks API — модуль «Дизайн карточек» (docs/specs/design/CONTRACT.md, FROZEN). */
import { ApiClient } from './client';
import type {
    DesignAssignPayload,
    DesignBoardResponse,
    DesignCalendarOut,
    DesignCommentInPayload,
    DesignCommentOut,
    DesignMaterialInPayload,
    DesignMaterialOut,
    DesignMovePayload,
    DesignProductSuggestion,
    DesignStatsOut,
    DesignStatusChangePayload,
    DesignTaskCreatePayload,
    DesignTaskDetail,
    DesignTaskListItem,
    DesignTaskListParams,
    DesignTaskUpdatePayload,
    DesignVerdictInPayload,
    DesignWorkloadRow,
} from '@/types/api';

const BASE = '/api/v1/design-tasks';

function listQuery(params: DesignTaskListParams): string {
    const qs = new URLSearchParams();
    for (const s of params.status ?? []) qs.append('status', s);
    if (params.work_type) qs.set('work_type', params.work_type);
    if (params.assignee_user_id != null) qs.set('assignee_user_id', String(params.assignee_user_id));
    if (params.author_user_id != null) qs.set('author_user_id', String(params.author_user_id));
    if (params.is_urgent != null) qs.set('is_urgent', String(params.is_urgent));
    if (params.overdue != null) qs.set('overdue', String(params.overdue));
    if (params.q) qs.set('q', params.q);
    if (params.limit != null) qs.set('limit', String(params.limit));
    if (params.offset != null) qs.set('offset', String(params.offset));
    const query = qs.toString();
    return query ? `?${query}` : '';
}

export function addDesignTaskMethods(api: ApiClient) {
    return {
        // ── Доска / списки ──
        getDesignBoard() {
            return api.request<DesignBoardResponse>('GET', `${BASE}/board`);
        },
        listDesignTasks(params: DesignTaskListParams = {}) {
            return api.request<DesignTaskListItem[]>('GET', `${BASE}${listQuery(params)}`);
        },
        listDesignTasksAllProjects(params: Pick<DesignTaskListParams, 'status' | 'limit' | 'offset'> = {}) {
            return api.request<DesignTaskListItem[]>('GET', `${BASE}/all-projects${listQuery(params)}`);
        },
        getDesignWorkload() {
            return api.request<DesignWorkloadRow[]>('GET', `${BASE}/workload`);
        },
        getDesignCalendar(month: string) {
            const qs = new URLSearchParams({ month });
            return api.request<DesignCalendarOut>('GET', `${BASE}/calendar?${qs.toString()}`);
        },
        getDesignStats(dateFrom?: string, dateTo?: string) {
            const qs = new URLSearchParams();
            if (dateFrom) qs.set('date_from', dateFrom);
            if (dateTo) qs.set('date_to', dateTo);
            const query = qs.toString();
            return api.request<DesignStatsOut>('GET', `${BASE}/stats${query ? `?${query}` : ''}`);
        },
        designProductSuggest(q: string) {
            const qs = new URLSearchParams({ q });
            return api.request<DesignProductSuggestion[]>('GET', `${BASE}/product-suggest?${qs.toString()}`);
        },

        // ── Задача ──
        createDesignTask(payload: DesignTaskCreatePayload) {
            return api.request<DesignTaskDetail>('POST', BASE, payload);
        },
        getDesignTask(taskId: number) {
            return api.request<DesignTaskDetail>('GET', `${BASE}/${taskId}`);
        },
        updateDesignTask(taskId: number, payload: DesignTaskUpdatePayload) {
            return api.request<DesignTaskDetail>('PUT', `${BASE}/${taskId}`, payload);
        },
        deleteDesignTask(taskId: number) {
            return api.request<void>('DELETE', `${BASE}/${taskId}`);
        },
        changeDesignTaskStatus(taskId: number, payload: DesignStatusChangePayload) {
            return api.request<DesignTaskDetail>('POST', `${BASE}/${taskId}/status`, payload);
        },
        moveDesignTask(taskId: number, payload: DesignMovePayload) {
            return api.request<DesignTaskDetail>('POST', `${BASE}/${taskId}/move`, payload);
        },
        assignDesignTask(taskId: number, payload: DesignAssignPayload) {
            return api.request<DesignTaskDetail>('POST', `${BASE}/${taskId}/assign`, payload);
        },
        markDesignTaskViewed(taskId: number) {
            return api.request<DesignTaskDetail>('POST', `${BASE}/${taskId}/viewed`);
        },

        // ── Материалы ──
        addDesignMaterial(taskId: number, payload: DesignMaterialInPayload) {
            return api.request<DesignMaterialOut>('POST', `${BASE}/${taskId}/materials`, payload);
        },
        uploadDesignMaterialFile(taskId: number, file: File) {
            const fd = new FormData();
            fd.append('file', file);
            // retries: транзиентные 5xx/сетевые обрывы (деплой) — канон learnings.
            return api.uploadFormData<DesignMaterialOut>(`${BASE}/${taskId}/materials/file`, fd, { retries: 2 });
        },
        getDesignMaterialFileBlob(taskId: number, matId: number) {
            return api.requestBlob(`${BASE}/${taskId}/materials/${matId}/file`);
        },
        deleteDesignMaterial(taskId: number, matId: number) {
            return api.request<void>('DELETE', `${BASE}/${taskId}/materials/${matId}`);
        },

        // ── Версии сдач ──
        submitDesignVersion(taskId: number, files: File[], comment?: string) {
            const fd = new FormData();
            for (const f of files) fd.append('files', f);
            if (comment) fd.append('comment', comment);
            // 503 (обрыв MinIO) = «версия создана, повторите»: пустая PENDING-версия
            // переиспользуется тем же version_no — повтор безопасен (retries на 5xx).
            return api.uploadFormData<DesignTaskDetail>(`${BASE}/${taskId}/submissions`, fd, { retries: 2 });
        },
        getDesignSubmissionFileBlob(taskId: number, subId: number, fileId: number) {
            return api.requestBlob(`${BASE}/${taskId}/submissions/${subId}/files/${fileId}`);
        },
        setDesignVerdict(taskId: number, subId: number, payload: DesignVerdictInPayload) {
            return api.request<DesignTaskDetail>('POST', `${BASE}/${taskId}/submissions/${subId}/verdict`, payload);
        },

        // ── Комментарии ──
        addDesignComment(taskId: number, payload: DesignCommentInPayload) {
            return api.request<DesignCommentOut>('POST', `${BASE}/${taskId}/comments`, payload);
        },

        // POST /{task_id}/ab-test — 501 до Ф6, метод и кнопка не строятся (контракт).
    };
}
