/** Refs API methods */
import { ApiClient } from './client';
import type { Account, CounterpartyCategory, CategoryRef, MessageResponse } from '@/types/api';

export function addRefMethods(api: ApiClient) {
    return {
        // Accounts
        getAccounts() { return api.request<Account[]>('GET', '/api/v1/refs/accounts'); },
        upsertAccount(data: Partial<Account>) { return api.request<Account>('POST', '/api/v1/refs/accounts', data); },
        deleteAccount(id: number) { return api.request<MessageResponse>('DELETE', `/api/v1/refs/accounts/${id}`); },

        // CP Categories
        getCpCategories() { return api.request<CounterpartyCategory[]>('GET', '/api/v1/refs/cp_categories'); },
        upsertCpCategory(data: Partial<CounterpartyCategory>) { return api.request<CounterpartyCategory>('POST', '/api/v1/refs/cp_categories', data); },
        deleteCpCategory(id: number) { return api.request<MessageResponse>('DELETE', `/api/v1/refs/cp_categories/${id}`); },

        // Overrides
        getOverrides() { return api.request<Array<{ id: number; field: string; pattern: string; value: string }>>('GET', '/api/v1/refs/overrides'); },
        deleteOverride(id: number) { return api.request<MessageResponse>('DELETE', `/api/v1/refs/overrides/${id}`); },

        // Opening balances
        getOpeningBalances() { return api.request<Array<{ id: number; account: string; currency: string; amount: number; date: string }>>('GET', '/api/v1/refs/opening_balances'); },
        upsertOpeningBalance(data: { account: string; currency: string; amount: number; date: string }) { return api.request<MessageResponse>('POST', '/api/v1/refs/opening_balances', data); },

        // Category reference
        getCategoryRef() { return api.request<CategoryRef[]>('GET', '/api/v1/refs/categories'); },
        addCategoryRef(data: Partial<CategoryRef>) { return api.request<CategoryRef>('POST', '/api/v1/refs/categories', data); },
        deleteCategoryRef(id: number) { return api.request<MessageResponse>('DELETE', `/api/v1/refs/categories/${id}`); },

        // Warehouses
        getWarehouses() { return api.request<Array<{ name: string; lat: number; lng: number }>>('GET', '/api/v1/refs/warehouses'); },
        getExcludedWarehouses() { return api.request<string[]>('GET', '/api/v1/refs/excluded-warehouses'); },
        setExcludedWarehouses(warehouses: string[]) { return api.request<{ ok: boolean; excluded: string[] }>('PUT', '/api/v1/refs/excluded-warehouses', { warehouses }); },
    };
}
