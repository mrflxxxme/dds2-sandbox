/** AB Photo Tests API — АБ-тесты главного фото карточки WB */
import { ApiClient } from './client';
import type { AbTestCreatePayload, AbTestListItem, AbTestResults } from '@/types/api';

export function addAbTestMethods(api: ApiClient) {
    return {
        listAbTests() {
            return api.request<{ tests: AbTestListItem[] }>('GET', '/api/v1/ab-tests');
        },
        createAbTest(payload: AbTestCreatePayload) {
            return api.request<{ id: number; status: string }>('POST', '/api/v1/ab-tests', payload);
        },
        getAbTest(testId: number) {
            return api.request<AbTestResults>('GET', `/api/v1/ab-tests/${testId}`);
        },
        uploadAbTestPhoto(testId: number, file: File) {
            const fd = new FormData();
            fd.append('file', file);
            return api.uploadFormData<{ id: number; position: number }>(`/api/v1/ab-tests/${testId}/photos`, fd);
        },
        deleteAbTestPhoto(testId: number, variantId: number) {
            return api.request<{ ok: boolean }>('DELETE', `/api/v1/ab-tests/${testId}/photos/${variantId}`);
        },
        getAbTestPhotoBlob(testId: number, variantId: number) {
            return api.requestBlob(`/api/v1/ab-tests/${testId}/photos/${variantId}`);
        },
        startAbTest(testId: number) {
            return api.request<{ id: number; status: string }>('POST', `/api/v1/ab-tests/${testId}/start`);
        },
        stopAbTest(testId: number) {
            return api.request<{ id: number; status: string; winner_variant_id: number | null }>(
                'POST', `/api/v1/ab-tests/${testId}/stop`,
            );
        },
        resumeAbTest(testId: number) {
            return api.request<{ id: number; status: string }>('POST', `/api/v1/ab-tests/${testId}/resume`);
        },
        excludeAbVariant(testId: number, variantId: number) {
            return api.request<{ ok: boolean }>('POST', `/api/v1/ab-tests/${testId}/variants/${variantId}/exclude`);
        },
        includeAbVariant(testId: number, variantId: number) {
            return api.request<{ ok: boolean }>('POST', `/api/v1/ab-tests/${testId}/variants/${variantId}/include`);
        },
        applyAbWinner(testId: number) {
            return api.request<{ ok: boolean; winner_applied_at: string }>(
                'POST', `/api/v1/ab-tests/${testId}/apply-winner`,
            );
        },
    };
}
