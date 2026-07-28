/** Payroll (Зарплата) API methods */
import { ApiClient } from './client';
import type {
    PayrollEmployeeIn,
    PayrollEmployeeUpdate,
    PayrollEmployee,
    PayrollEmployeeListResponse,
    PayrollTeamIn,
    PayrollTeamUpdate,
    PayrollTeam,
    PayrollTeamListResponse,
    PayrollTeamScope,
    PayrollScopeOptions,
    PayrollTariffReplace,
    PayrollTariffResponse,
    PayrollSheetResponse,
    PayrollPayoutMarkIn,
    PayrollOkResponse,
    PayrollAgencySheetResponse,
    PayrollClientEntryKind,
    PayrollClientEntryUpsert,
    PayrollClientProject,
    PayrollClientProjectIn,
    PayrollClientProjectListResponse,
    PayrollClientProjectUpdate,
    PayrollProjectOptionsResponse,
} from '@/types/api';

export function addPayrollMethods(api: ApiClient) {
    return {
        // ─── Сотрудники ───────────────────────────────────────────────────────
        listPayrollEmployees() {
            return api.request<PayrollEmployeeListResponse>('GET', '/api/v1/payroll/employees');
        },

        createPayrollEmployee(data: PayrollEmployeeIn) {
            return api.request<PayrollEmployee>('POST', '/api/v1/payroll/employees', data);
        },

        updatePayrollEmployee(id: number, data: PayrollEmployeeUpdate) {
            return api.request<PayrollEmployee>('PATCH', `/api/v1/payroll/employees/${id}`, data);
        },

        deletePayrollEmployee(id: number) {
            return api.request<PayrollOkResponse>('DELETE', `/api/v1/payroll/employees/${id}`);
        },

        // ─── Команды ──────────────────────────────────────────────────────────
        listPayrollTeams() {
            return api.request<PayrollTeamListResponse>('GET', '/api/v1/payroll/teams');
        },

        createPayrollTeam(data: PayrollTeamIn) {
            return api.request<PayrollTeam>('POST', '/api/v1/payroll/teams', data);
        },

        updatePayrollTeam(id: number, data: PayrollTeamUpdate) {
            return api.request<PayrollTeam>('PATCH', `/api/v1/payroll/teams/${id}`, data);
        },

        deletePayrollTeam(id: number) {
            return api.request<PayrollOkResponse>('DELETE', `/api/v1/payroll/teams/${id}`);
        },

        replacePayrollTeamScopes(id: number, scopes: PayrollTeamScope[]) {
            return api.request<PayrollTeam>('PUT', `/api/v1/payroll/teams/${id}/scopes`, { scopes });
        },

        replacePayrollTeamMembers(id: number, employee_ids: number[]) {
            return api.request<PayrollTeam>('PUT', `/api/v1/payroll/teams/${id}/members`, { employee_ids });
        },

        payrollScopeOptions() {
            return api.request<PayrollScopeOptions>('GET', '/api/v1/payroll/scope-options');
        },

        // ─── Тарифная лестница ────────────────────────────────────────────────
        payrollTariff(onDate?: string) {
            const qs = onDate ? `?${new URLSearchParams({ on_date: onDate })}` : '';
            return api.request<PayrollTariffResponse>('GET', `/api/v1/payroll/tariff${qs}`);
        },

        replacePayrollTariff(data: PayrollTariffReplace) {
            return api.request<PayrollTariffResponse>('PUT', '/api/v1/payroll/tariff', data);
        },

        // ─── Месячная ведомость ───────────────────────────────────────────────
        payrollSheet(month: string) {
            const qs = `?${new URLSearchParams({ month })}`;
            return api.request<PayrollSheetResponse>('GET', `/api/v1/payroll/sheet${qs}`);
        },

        markPayrollPayout(data: PayrollPayoutMarkIn) {
            return api.request<PayrollOkResponse>('PUT', '/api/v1/payroll/payout-mark', data);
        },

        // ─── Агентство (консалтинг) ───────────────────────────────────────────
        listPayrollAgencyClients() {
            return api.request<PayrollClientProjectListResponse>('GET', '/api/v1/payroll/agency/clients');
        },

        createPayrollAgencyClient(data: PayrollClientProjectIn) {
            return api.request<PayrollClientProject>('POST', '/api/v1/payroll/agency/clients', data);
        },

        updatePayrollAgencyClient(id: number, data: PayrollClientProjectUpdate) {
            return api.request<PayrollClientProject>('PATCH', `/api/v1/payroll/agency/clients/${id}`, data);
        },

        deletePayrollAgencyClient(id: number) {
            return api.request<PayrollOkResponse>('DELETE', `/api/v1/payroll/agency/clients/${id}`);
        },

        /** Ручная сумма клиента: недельная база внешнего кабинета либо ЧП месяца. */
        upsertPayrollAgencyEntry(clientId: number, data: PayrollClientEntryUpsert) {
            return api.request<PayrollOkResponse>('PUT', `/api/v1/payroll/agency/clients/${clientId}/entry`, data);
        },

        deletePayrollAgencyEntry(clientId: number, kind: PayrollClientEntryKind, dateFrom: string) {
            const qs = new URLSearchParams({ kind, date_from: dateFrom });
            return api.request<PayrollOkResponse>(
                'DELETE', `/api/v1/payroll/agency/clients/${clientId}/entry?${qs}`
            );
        },

        payrollAgencySheet(month: string) {
            const qs = `?${new URLSearchParams({ month })}`;
            return api.request<PayrollAgencySheetResponse>('GET', `/api/v1/payroll/agency/sheet${qs}`);
        },

        payrollAgencyProjectOptions() {
            return api.request<PayrollProjectOptionsResponse>('GET', '/api/v1/payroll/agency/project-options');
        },
    };
}
