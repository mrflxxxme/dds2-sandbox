import { ApiClient } from './client';
import type {
    CartActionResult,
    ExchangeSubject,
    ExchangeSupplier,
    ExchangeSessionStatus,
    RootCategory,
    ShowcaseQueryPayload,
    ShowcaseResponse,
} from '@/types/api';

/** Раздел «Биржа карточек товаров» — витрина/справочник/корзина биржи WB.
 *  project_id проставляется клиентом автоматически (X-Project-Id). */
export function addCardExchangeMethods(api: ApiClient) {
    return {
        getCardExchangeSessionStatus() {
            return api.request<ExchangeSessionStatus>('GET', '/api/v1/card-exchange/session/status');
        },
        setCardExchangeSession(authorizev3: string) {
            return api.request<ExchangeSessionStatus>('POST', '/api/v1/card-exchange/session', { authorizev3 });
        },
        useCardExchangeSessionFromSupply() {
            return api.request<ExchangeSessionStatus>('POST', '/api/v1/card-exchange/session/from-supply');
        },
        getCardExchangeCategories() {
            return api.request<RootCategory[]>('GET', '/api/v1/card-exchange/categories');
        },
        getCardExchangeCounters() {
            return api.request<{ showcase?: number }>('GET', '/api/v1/card-exchange/counters');
        },
        getCardExchangeBrands() {
            return api.request<string[]>('GET', '/api/v1/card-exchange/brands');
        },
        getCardExchangeSuppliers() {
            return api.request<ExchangeSupplier[]>('GET', '/api/v1/card-exchange/suppliers');
        },
        getCardExchangeSubjects() {
            return api.request<ExchangeSubject[]>('GET', '/api/v1/card-exchange/subjects');
        },
        getCardExchangeShowcase(payload: ShowcaseQueryPayload) {
            return api.request<ShowcaseResponse>('POST', '/api/v1/card-exchange/showcase', payload);
        },
        getCardExchangeCart() {
            return api.request<Record<string, unknown>>('GET', '/api/v1/card-exchange/cart');
        },
        addCardToCart(adId: number) {
            return api.request<CartActionResult>('POST', '/api/v1/card-exchange/cart/add', { ad_id: adId });
        },
        deleteCardsFromCart(adIds: number[]) {
            return api.request<CartActionResult>('POST', '/api/v1/card-exchange/cart/delete', { ad_ids: adIds });
        },
    };
}
