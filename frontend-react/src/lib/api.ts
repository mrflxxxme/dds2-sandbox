/**
 * DDS API Client — typed HTTP client with JWT auth.
 *
 * Refactored: core auth/request logic in api/client.ts,
 * domain methods in api/{auth,projects,reports,transactions,refs,
 * integrations,cost,planning,funnel,imports}.ts
 *
 * BACKWARD COMPATIBLE: all existing imports `import { api } from '@/lib/api'` work unchanged.
 */

import { ApiClient } from './api/client';
import { addAuthMethods } from './api/auth';
import { addProjectMethods } from './api/projects';
import { addReportMethods } from './api/reports';
import { addTransactionMethods } from './api/transactions';
import { addRefMethods } from './api/refs';
import { addIntegrationMethods } from './api/integrations';
import { addCostMethods } from './api/cost';
import { addPlanningMethods } from './api/planning';
import { addFunnelMethods } from './api/funnel';
import { addImportMethods } from './api/imports';

const client = new ApiClient();

export const api = Object.assign(client, {
    ...addAuthMethods(client),
    ...addProjectMethods(client),
    ...addReportMethods(client),
    ...addTransactionMethods(client),
    ...addRefMethods(client),
    ...addIntegrationMethods(client),
    ...addCostMethods(client),
    ...addPlanningMethods(client),
    ...addFunnelMethods(client),
    ...addImportMethods(client),
});
