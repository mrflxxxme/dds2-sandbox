/** Import & Upload API methods */
import { ApiClient } from './client';
import type { ImportResult } from '@/types/api';

export function addImportMethods(api: ApiClient) {
    return {
        getImportLogs() {
            return api.request<Array<{ id: number; filename: string; source_type: string; imported_at: string; rows_raw: number; rows_inserted: number; rows_skipped: number; status: string }>>('GET', '/api/v1/import/logs');
        },
        async uploadFile(file: File, sourceType: string, accountNo: string) {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('source_type', sourceType);
            if (accountNo) formData.append('account_no', accountNo);
            return api.uploadFormData<ImportResult>('/api/v1/import/upload', formData);
        },
    };
}
