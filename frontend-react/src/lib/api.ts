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
import { addTelegramMethods } from './api/telegram';
import { addWarehouseMethods } from './api/warehouse';
import { addWarehouseSpeedMethods } from './api/warehouse-speed';
import { addFulfillmentMethods } from './api/fulfillment';
import { addMonitoringMethods } from './api/monitoring';
import { addRawDataMethods } from './api/rawData';
import { addSupplyChainMethods } from './api/supply-chain';
import { addAiChatMethods } from './api/ai-chat';
import { addCounterpartyMethods } from './api/counterparty';
import { addLoanMethods } from './api/loans';
import { addWbReturnsMethods } from './api/wb-returns';
import { addLocalizationMethods } from './api/localization';
import { addPaymentRequestMethods } from './api/payment-requests';
import { addPricingMethods } from './api/pricing';
import { addMeasurementMethods } from './api/measurements';
import { addReviewMethods } from './api/reviews';
import { addAbTestMethods } from './api/abTests';
import { addVibeMethods } from './api/vibe';
import { addFfBillingMethods } from './api/ffBilling';
import { addFbsMethods } from './api/fbs';
import { addPayrollMethods } from './api/payroll';
import { addCardExchangeMethods } from './api/card-exchange';

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
    ...addTelegramMethods(client),
    ...addWarehouseMethods(client),
    ...addWarehouseSpeedMethods(client),
    ...addFulfillmentMethods(client),
    ...addMonitoringMethods(client),
    ...addRawDataMethods(client),
    ...addSupplyChainMethods(client),
    ...addAiChatMethods(client),
    ...addCounterpartyMethods(client),
    ...addLoanMethods(client),
    ...addWbReturnsMethods(client),
    ...addLocalizationMethods(client),
    ...addPaymentRequestMethods(client),
    ...addPricingMethods(client),
    ...addMeasurementMethods(client),
    ...addReviewMethods(client),
    ...addAbTestMethods(client),
    ...addVibeMethods(client),
    ...addFfBillingMethods(client),
    ...addFbsMethods(client),
    ...addPayrollMethods(client),
    ...addCardExchangeMethods(client),
});
