/**
 * Модалка «Пополнить бюджет» — реальные деньги, поэтому проверяем гарантии:
 * минимум WB блокирует кнопку, источник уходит в API тем, что выбрали,
 * нехватка на кошельке только предупреждает, ошибка WB не закрывает окно.
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import DepositModal from '@/app/(main)/p/[slug]/ads-manager/components/DepositModal';
import type { AdsManagerCampaign } from '@/types/api';

const getAdsBalance = vi.fn();
const depositCampaignBudget = vi.fn();

vi.mock('@/lib/api', () => ({
    api: {
        getAdsBalance: (...a: unknown[]) => getAdsBalance(...a),
        depositCampaignBudget: (...a: unknown[]) => depositCampaignBudget(...a),
    },
}));

const campaign = {
    campaign_id: 777, name: 'Стандарт 180x200', campaign_type: 'cpm', status: 9, status_label: 'Активна',
    budget: 0, nm_ids: [123], nm_count: 1, brands: [], subjects: [], spend_today: 1.8, spend_period: 0,
    views_period: 0, clicks_period: 0, ctr: 0, cpc: 0, cpl: 0, cpo: 0, drr: 0, margin: 0, spend_per_hour: 0,
    ad_click_share: 0, cr_cart: 0, cr_order: 0, rev_yesterday: 0, budget_gap: 0, updated_at: null,
} as AdsManagerCampaign;

const wallet = (over: Record<string, unknown> = {}) => ({ ok: true, balance: 20000, net: 5000, bonus: 0, error: null, ...over });

describe('DepositModal', () => {
    beforeEach(() => {
        getAdsBalance.mockReset().mockResolvedValue(wallet());
        depositCampaignBudget.mockReset().mockResolvedValue({ ok: true, status: 'ok', budget_after: 5000, error: null });
    });

    it('сумма ниже минимума WB — кнопка выключена, запроса нет', () => {
        render(<DepositModal campaign={campaign} onClose={vi.fn()} onDeposited={vi.fn()} />);
        fireEvent.change(screen.getByLabelText('Сумма пополнения, ₽'), { target: { value: '500' } });

        const submit = screen.getByRole('button', { name: /Пополнить на/ });
        expect(submit).toBeDisabled();
        fireEvent.click(submit);
        expect(depositCampaignBudget).not.toHaveBeenCalled();
    });

    it('быстрая сумма + источник «Баланс» уходят в API, наверх — новый остаток', async () => {
        const onDeposited = vi.fn();
        const onClose = vi.fn();
        render(<DepositModal campaign={campaign} onClose={onClose} onDeposited={onDeposited} />);

        fireEvent.click(screen.getByRole('button', { name: /\+5\s?000/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Баланс' }));
        fireEvent.click(screen.getByRole('button', { name: /Пополнить на/ }));

        await waitFor(() => expect(depositCampaignBudget).toHaveBeenCalledWith(777, 5000, 1));
        expect(onDeposited).toHaveBeenCalledWith(5000, 5000);
        expect(onClose).toHaveBeenCalled();
    });

    it('на источнике меньше суммы — предупреждаем, но пополнить даём', async () => {
        getAdsBalance.mockResolvedValue(wallet({ balance: 2000 }));
        render(<DepositModal campaign={campaign} onClose={vi.fn()} onDeposited={vi.fn()} />);

        fireEvent.click(screen.getByRole('button', { name: /\+10\s?000/ }));
        await waitFor(() => expect(screen.getByText(/Запрошено больше, чем есть на источнике/)).toBeInTheDocument());
        expect(screen.getByRole('button', { name: /Пополнить на/ })).toBeEnabled();
    });

    it('отказ WB показывается в окне, окно не закрывается', async () => {
        depositCampaignBudget.mockRejectedValue(new Error('HTTP 400: {"detail":"has no budget"}'));
        const onClose = vi.fn();
        render(<DepositModal campaign={campaign} onClose={onClose} onDeposited={vi.fn()} />);

        fireEvent.click(screen.getByRole('button', { name: /Пополнить на/ }));

        await waitFor(() => expect(screen.getByText(/Недостаточно бюджета/)).toBeInTheDocument());
        expect(onClose).not.toHaveBeenCalled();
    });

    it('кошелёк WB недоступен — окно работает, показывает оговорку', async () => {
        getAdsBalance.mockResolvedValue(wallet({ ok: false, balance: 0, net: 0, error: 'WB не ответил' }));
        render(<DepositModal campaign={campaign} onClose={vi.fn()} onDeposited={vi.fn()} />);

        await waitFor(() => expect(screen.getByText(/Остатки кабинета WB сейчас не отдаёт/)).toBeInTheDocument());
        expect(screen.getByRole('button', { name: /Пополнить на/ })).toBeEnabled();
    });
});
