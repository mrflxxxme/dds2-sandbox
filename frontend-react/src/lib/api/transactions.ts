/** Transactions API methods */
import { ApiClient } from './client';
import type { Transaction, UnassignedGroupRow, MessageResponse } from '@/types/api';

export function addTransactionMethods(api: ApiClient) {
    return {
        searchTransactions(params: Record<string, unknown> = {}) {
            return api.request<Transaction[]>('POST', '/api/v1/transactions/search', params);
        },
        getUnassigned(limit = 500) {
            return api.request<Transaction[]>('GET', `/api/v1/transactions/unassigned?limit=${limit}`);
        },
        getUnassignedGrouped() {
            return api.request<UnassignedGroupRow[]>('GET', '/api/v1/transactions/unassigned_grouped');
        },
        assignCategory(data: { txn_id: string; cat_lvl1: string; cat_lvl2?: string; scope?: string; cp_key?: string }) {
            return api.request<MessageResponse>('POST', '/api/v1/transactions/assign_category', data);
        },
        assignCategoryBulk(data: { cp_key: string; counterparty?: string; cat_lvl1: string; cat_lvl2?: string }) {
            return api.request<{ updated: number }>('POST', '/api/v1/transactions/assign_category_bulk', data);
        },
        assignCategoryByIds(data: { txn_ids: string[]; cat_lvl1: string; cat_lvl2?: string }) {
            return api.request<{ updated: number }>('POST', '/api/v1/transactions/assign_category_by_ids', data);
        },
        getAutoCategorizeRules() {
            return api.request<Array<{ id: number; keyword: string; direction: string; cat_lvl1: string; cat_lvl2: string | null; priority: number; is_active: boolean }>>('GET', '/api/v1/transactions/auto_categorize/rules');
        },
        createAutoCategorizeRule(data: { keyword: string; cat_lvl1: string; cat_lvl2?: string; direction?: string; priority?: number }) {
            return api.request<{ id: number; keyword: string; direction: string; cat_lvl1: string; cat_lvl2: string | null }>('POST', '/api/v1/transactions/auto_categorize/rules', data);
        },
        deleteAutoCategorizeRule(ruleId: number) {
            return api.request<{ ok: boolean }>('DELETE', `/api/v1/transactions/auto_categorize/rules/${ruleId}`);
        },
        previewAutoCategorize() {
            return api.request<Array<{ txn_id: string; date: string; counterparty: string; purpose: string; expense: number; income: number; currency: string; matched_keyword: string; suggested_cat_lvl1: string; suggested_cat_lvl2: string | null }>>('GET', '/api/v1/transactions/auto_categorize/preview');
        },
        applyAutoCategorize() {
            return api.request<{ ok: boolean; updated: number; details: Array<{ txn_id: string; cat_lvl1: string; cat_lvl2: string | null; keyword: string }> }>('POST', '/api/v1/transactions/auto_categorize/apply');
        },
    };
}
