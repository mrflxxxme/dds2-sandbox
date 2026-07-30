import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import type { ShowcaseAd, ShowcaseResponse } from '@/types/api';

// PageGuard завязан на usePermissions (API) — в тесте пробрасываем children.
vi.mock('@/components/PageGuard', () => ({ default: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock('@/lib/api', () => ({
    api: {
        getCardExchangeSessionStatus: vi.fn(),
        setCardExchangeSession: vi.fn(),
        useCardExchangeSessionFromSupply: vi.fn(),
        getCardExchangeCategories: vi.fn(),
        getCardExchangeShowcase: vi.fn(),
        addCardToCart: vi.fn(),
        deleteCardsFromCart: vi.fn(),
    },
}));

import CardExchangePage from '@/app/(main)/p/[slug]/card-exchange/page';
import { api } from '@/lib/api';

const getShowcase = vi.mocked(api.getCardExchangeShowcase);
const getCategories = vi.mocked(api.getCardExchangeCategories);
const addToCart = vi.mocked(api.addCardToCart);
const getSession = vi.mocked(api.getCardExchangeSessionStatus);
const setSession = vi.mocked(api.setCardExchangeSession);

function makeAd(over: Partial<ShowcaseAd> = {}): ShowcaseAd {
    return {
        ad_id: 1, nm_id: 100, imt_id: 101, title: 'Тестовая карточка', brand: 'BRND',
        supplier_name: 'ИП', imt_count: 3, stock_qty: 50, photo: 'http://x/1.webp',
        contact_countries: ['Китай'], is_kiz: false, total_price: 1000, rating: 4.8,
        feedbacks_count: 10, has_in_cart: false, is_card_owner: false, is_ours: false, ...over,
    };
}
function makeResp(over: Partial<ShowcaseResponse> = {}): ShowcaseResponse {
    return { ads: [makeAd()], next_cursor: null, has_more: false, unmatched_subjects: [], ...over };
}

describe('Страница «Биржа карточек»', () => {
    beforeEach(() => {
        getShowcase.mockReset();
        getCategories.mockReset();
        getCategories.mockResolvedValue([{ category: 'Автоаксессуары', subject_count: 5 }]);
        addToCart.mockReset();
        addToCart.mockResolvedValue({ ok: true });
        getSession.mockReset();
        getSession.mockResolvedValue({ status: 'ACTIVE', updated_at: '2026-07-30T00:00:00' });
        setSession.mockReset();
        setSession.mockResolvedValue({ status: 'ACTIVE', updated_at: '2026-07-30T00:00:00' });
    });

    it('loading — пока запрос витрины не ответил', () => {
        getShowcase.mockReturnValue(new Promise(() => { }));
        render(<CardExchangePage />);
        expect(screen.getByText('Загрузка…')).toBeInTheDocument();
    });

    it('data — карточка с ценой, рейтингом и кнопкой «Добавить»', async () => {
        getShowcase.mockResolvedValue(makeResp());
        render(<CardExchangePage />);
        await screen.findByText('Тестовая карточка', { exact: false });
        expect(screen.getByText(/10 отзывов/)).toBeInTheDocument();
        expect(screen.getByText('3 вариантов товара')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Добавить' })).toBeInTheDocument();
    });

    it('empty — сообщение «Ничего не найдено»', async () => {
        getShowcase.mockResolvedValue(makeResp({ ads: [] }));
        render(<CardExchangePage />);
        await screen.findByText(/Ничего не найдено/);
    });

    it('error — текст ошибки и кнопка «Повторить»', async () => {
        getShowcase.mockRejectedValue(new Error('WB недоступен'));
        render(<CardExchangePage />);
        await screen.findByText(/WB недоступен/);
        expect(screen.getByRole('button', { name: 'Повторить' })).toBeInTheDocument();
    });

    it('корзина — «Добавить» шлёт add и переключается в «Удалить из корзины»', async () => {
        getShowcase.mockResolvedValue(makeResp());
        render(<CardExchangePage />);
        const btn = await screen.findByRole('button', { name: 'Добавить' });
        fireEvent.click(btn);
        await waitFor(() => expect(addToCart).toHaveBeenCalledWith(1));
        await screen.findByRole('button', { name: 'Удалить из корзины' });
    });

    it('нет сессии биржи — форма доступа, витрина не запрашивается', async () => {
        getSession.mockResolvedValue({ status: 'NONE' });
        getShowcase.mockResolvedValue(makeResp());
        render(<CardExchangePage />);
        await screen.findByText(/Нужен доступ к бирже WB/);
        expect(screen.getByPlaceholderText('Вставьте сюда скопированный доступ')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Взять доступ из поставок' })).toBeInTheDocument();
        expect(getShowcase).not.toHaveBeenCalled();
    });

    it('истёкшая сессия — просит свежий токен', async () => {
        getSession.mockResolvedValue({ status: 'EXPIRED' });
        render(<CardExchangePage />);
        await screen.findByText(/Доступ к бирже истёк/);
    });

    it('сохранение доступа — шлёт токен и грузит витрину', async () => {
        getSession.mockResolvedValue({ status: 'NONE' });
        getShowcase.mockResolvedValue(makeResp());
        render(<CardExchangePage />);
        const input = await screen.findByPlaceholderText('Вставьте сюда скопированный доступ');
        fireEvent.change(input, { target: { value: 'tok123' } });
        fireEvent.click(screen.getByRole('button', { name: 'Сохранить доступ' }));
        await waitFor(() => expect(setSession).toHaveBeenCalledWith('tok123'));
        await screen.findByText('Тестовая карточка', { exact: false });
    });

    it('«наша» карточка помечена бейджем и мы её видим', async () => {
        getShowcase.mockResolvedValue(makeResp({ ads: [makeAd({ is_ours: true })] }));
        render(<CardExchangePage />);
        expect(await screen.findByText('Наша')).toBeInTheDocument();
    });
});
