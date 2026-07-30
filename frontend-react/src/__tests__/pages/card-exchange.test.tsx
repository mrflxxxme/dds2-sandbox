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
        getCardExchangeBrands: vi.fn(),
        getCardExchangeSuppliers: vi.fn(),
        getCardExchangeSubjects: vi.fn(),
        getCardExchangeCounters: vi.fn(),
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
const fromSupply = vi.mocked(api.useCardExchangeSessionFromSupply);

function makeAd(over: Partial<ShowcaseAd> = {}): ShowcaseAd {
    return {
        ad_id: 1, nm_id: 100, imt_id: 101, title: 'Тестовая карточка', brand: 'BRND',
        supplier_name: 'ИП', imt_count: 3, stock_qty: 50, photo: 'http://x/1.webp',
        contact_countries: ['Китай'], is_kiz: false, total_price: 1000, rating: 4.8,
        feedbacks_count: 10, has_in_cart: false, is_card_owner: false, is_ours: false,
        categories: [], our_categories: [], ...over,
    };
}
function makeResp(over: Partial<ShowcaseResponse> = {}): ShowcaseResponse {
    return { ads: [makeAd()], next_cursor: null, has_more: false, unmatched_subjects: [], ...over };
}

describe('Страница «Биржа карточек»', () => {
    beforeEach(() => {
        getShowcase.mockReset();
        getCategories.mockReset();
        getCategories.mockResolvedValue([
            { category: 'Автоаксессуары', subject_count: 249, is_ours: true, our_count: 12 },
            { category: 'Красота', subject_count: 316, is_ours: false, our_count: 0 },
        ]);
        vi.mocked(api.getCardExchangeBrands).mockResolvedValue(['AUTOPROFI', 'CARFORT']);
        vi.mocked(api.getCardExchangeSuppliers).mockResolvedValue([{ id: 1, name: 'ИП Смирнов' }]);
        vi.mocked(api.getCardExchangeSubjects).mockResolvedValue([
            { id: 100, name: 'Компрессоры автомобильные', root_category: 'Автоаксессуары' },
        ]);
        vi.mocked(api.getCardExchangeCounters).mockResolvedValue({ showcase: 10916 });
        addToCart.mockReset();
        addToCart.mockResolvedValue({ ok: true });
        getSession.mockReset();
        getSession.mockResolvedValue({ status: 'ACTIVE', updated_at: '2026-07-30T00:00:00' });
        setSession.mockReset();
        setSession.mockResolvedValue({ status: 'ACTIVE', updated_at: '2026-07-30T00:00:00' });
        fromSupply.mockReset();
        fromSupply.mockResolvedValue({ status: 'ACTIVE', updated_at: '2026-07-30T00:00:00' });
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
        expect(screen.getByText('3 вариантов')).toBeInTheDocument();
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
        await screen.findByRole('button', { name: 'Убрать' });
    });

    it('доступа нет — подхватывается автоматически, пользователь ничего не вводит', async () => {
        getSession.mockResolvedValue({ status: 'NONE' });
        getShowcase.mockResolvedValue(makeResp());
        render(<CardExchangePage />);
        await screen.findByText('Тестовая карточка', { exact: false });
        expect(fromSupply).toHaveBeenCalled();
        // никаких токенов/команд на экране
        expect(screen.queryByPlaceholderText('Вставьте сюда скопированный доступ')).toBeNull();
        expect(screen.queryByText(/authorizev3/)).toBeNull();
        expect(screen.queryByText(/Console/)).toBeNull();
    });

    it('доступ подхватить не удалось — короткое сообщение без техники', async () => {
        getSession.mockResolvedValue({ status: 'NONE' });
        fromSupply.mockRejectedValue(new Error('Нет активного доступа WB для поставок'));
        render(<CardExchangePage />);
        await screen.findByText('Нет доступа к бирже WB');
        expect(screen.getByText(/Обновите доступ WB в разделе/)).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Сохранить доступ' })).toBeNull();
        expect(getShowcase).not.toHaveBeenCalled();
    });

    it('переключение в «Список» — таблица с колонками как в рекламе', async () => {
        getShowcase.mockResolvedValue(makeResp());
        render(<CardExchangePage />);
        await screen.findByText('Тестовая карточка', { exact: false });
        fireEvent.click(screen.getByRole('button', { name: 'Список' }));
        expect(await screen.findByText('ПРОДАВЕЦ')).toBeInTheDocument();
        expect(screen.getByText('ЦЕНА ₽')).toBeInTheDocument();
        expect(screen.getByText('ИП')).toBeInTheDocument();
    });

    it('клик по заголовку колонки — перезапрос с новой сортировкой', async () => {
        getShowcase.mockResolvedValue(makeResp());
        render(<CardExchangePage />);
        await screen.findByText('Тестовая карточка', { exact: false });
        fireEvent.click(screen.getByRole('button', { name: 'Список' }));
        const priceTh = await screen.findByText(/ЦЕНА ₽/);
        getShowcase.mockClear();
        fireEvent.click(priceTh);
        await waitFor(() => expect(getShowcase).toHaveBeenCalled());
        expect(getShowcase.mock.calls.at(-1)?.[0]).toMatchObject({ sort_field: 'totalPrice', sort_order: 'desc' });
        // повторный клик разворачивает порядок
        fireEvent.click(await screen.findByText(/ЦЕНА ₽/));
        await waitFor(() => expect(getShowcase.mock.calls.at(-1)?.[0]).toMatchObject({ sort_order: 'asc' }));
    });

    it('бейдж показывает нашу категорию, а при нескольких — «+N»', async () => {
        getShowcase.mockResolvedValue(makeResp({
            ads: [makeAd({ our_categories: ['Посуда и инвентарь', 'Красота'], categories: ['Посуда и инвентарь', 'Красота'] })],
        }));
        render(<CardExchangePage />);
        expect(await screen.findByText('Посуда и инвентарь')).toBeInTheDocument();
        expect(screen.getByText(/\+1/)).toBeInTheDocument();
        expect(screen.queryByText('Наша')).toBeNull();
    });

    it('без наших категорий бейджа нет', async () => {
        getShowcase.mockResolvedValue(makeResp({ ads: [makeAd({ our_categories: [] })] }));
        render(<CardExchangePage />);
        await screen.findByText('Тестовая карточка', { exact: false });
        expect(screen.queryByText(/Подходит к наш/)).toBeNull();
    });

    it('в фильтре категорий — все категории, наши сверху с числом', async () => {
        getShowcase.mockResolvedValue(makeResp());
        render(<CardExchangePage />);
        await screen.findByText('Тестовая карточка', { exact: false });
        fireEvent.click(screen.getByRole('button', { name: /Фильтры/ }));
        fireEvent.click(await screen.findByRole('button', { name: 'Корневая категория' }));
        // наши — с числом артикулов и сверху, но доступны ВСЕ категории справочника
        expect(await screen.findByText('Автоаксессуары (12)')).toBeInTheDocument();
        expect(screen.getByText('Красота')).toBeInTheDocument();
    });

    it('выбор предмета сам отмечает его корневую категорию', async () => {
        getShowcase.mockResolvedValue(makeResp());
        render(<CardExchangePage />);
        await screen.findByText('Тестовая карточка', { exact: false });
        fireEvent.click(screen.getByRole('button', { name: /Фильтры/ }));
        fireEvent.click(await screen.findByRole('button', { name: 'Предмет' }));
        fireEvent.click(await screen.findByLabelText('Компрессоры автомобильные'));
        // категория подтянулась автоматически
        fireEvent.click(screen.getByRole('button', { name: /Корневая категория/ }));
        expect((await screen.findByLabelText('Автоаксессуары (12)')) as HTMLInputElement).toBeChecked();
        fireEvent.click(screen.getByRole('button', { name: 'Применить' }));
        await waitFor(() => expect(getShowcase.mock.calls.at(-1)?.[0]).toMatchObject({
            subject_ids: [100], root_categories: ['Автоаксессуары'],
        }));
    });

    it('селектор слева — наши категории; выбор уходит в root_categories', async () => {
        getShowcase.mockResolvedValue(makeResp());
        render(<CardExchangePage />);
        await screen.findByText('Тестовая карточка', { exact: false });
        fireEvent.click(screen.getByRole('button', { name: /Категория: все/ }));
        // в селекторе только наши категории
        expect(await screen.findByText('Автоаксессуары (12)')).toBeInTheDocument();
        expect(screen.queryByText('Красота')).toBeNull();
        fireEvent.click(screen.getByText('Автоаксессуары (12)'));
        await waitFor(() => expect(getShowcase.mock.calls.at(-1)?.[0]).toMatchObject({ root_categories: ['Автоаксессуары'] }));
    });

    it('счётчик корзины — ссылка в корзину биржи WB (когда что-то добавлено)', async () => {
        getShowcase.mockResolvedValue(makeResp({ ads: [makeAd({ has_in_cart: true })] }));
        render(<CardExchangePage />);
        const link = await screen.findByRole('link', { name: /В корзине: 1/ });
        expect(link).toHaveAttribute('href', 'https://seller.wildberries.ru/card-exchange/cart');
        expect(link).toHaveAttribute('target', '_blank');
        expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'));
    });

    it('пустая корзина — просто текст, без ссылки', async () => {
        getShowcase.mockResolvedValue(makeResp());
        render(<CardExchangePage />);
        await screen.findByText('Тестовая карточка', { exact: false });
        expect(screen.queryByRole('link', { name: /В корзине/ })).toBeNull();
    });
});
