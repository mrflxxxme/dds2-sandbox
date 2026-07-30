import { ApiClient } from './client';
import type {
    CartActionResult,
    RootCategory,
    ShowcaseQueryPayload,
    ShowcaseResponse,
} from '@/types/api';

/** Раздел «Биржа карточек товаров» — витрина/справочник/корзина биржи WB.
 *  project_id проставляется клиентом автоматически (X-Project-Id). */
export function addCardExchangeMethods(api: ApiClient) {
    return {
        getCardExchangeCategories() {
            return api.request<RootCategory[]>('GET', '/api/v1/card-exchange/categories');
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
