/**
 * Окно «Автопополнение бюджета» — зеркало настройки кабинета ВБ (реальное правило трат).
 * Проверяем: форма приходит из кабинета, изменения уходят туда же, минимум ВБ держит
 * кнопку, а потерянный доступ к кабинету НЕ выглядит как «автопополнение выключено».
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import AutopayModal from '@/app/(main)/p/[slug]/ads-manager/components/AutopayModal';
import type { AdsManagerCampaign, WbAutorefillSetting } from '@/types/api';

const getCampaignAutorefill = vi.fn();
const setCampaignAutorefill = vi.fn();

vi.mock('@/lib/api', () => ({
    api: {
        getCampaignAutorefill: (...a: unknown[]) => getCampaignAutorefill(...a),
        setCampaignAutorefill: (...a: unknown[]) => setCampaignAutorefill(...a),
    },
}));

const campaign = {
    campaign_id: 37227684, name: 'BR Compressor', campaign_type: 'cpc', status: 9, status_label: 'Активна',
    budget: 3437, nm_ids: [953038730], nm_count: 1, brands: [], subjects: [], spend_today: 1190, spend_period: 0,
    views_period: 0, clicks_period: 0, ctr: 0, cpc: 0, cpl: 0, cpo: 0, drr: 0, margin: 0, spend_per_hour: 0,
    ad_click_share: 0, cr_cart: 0, cr_order: 0, rev_yesterday: 0, budget_gap: 0, updated_at: null,
} as AdsManagerCampaign;

// то, что реально отдаёт кабинет (после нормализации бэкендом)
const setting = (over: Partial<WbAutorefillSetting> = {}): WbAutorefillSetting => ({
    enabled: true, threshold: 100, amount: 5000, daily_limit: true, limit: 1, unified_account: true,
    status: 'working',
    history: [{ id: '286368544', date: '2026-07-31T14:07:34Z', source: 'net', sum: 5000 }],
    ...over,
});

describe('AutopayModal (настройка кабинета ВБ)', () => {
    beforeEach(() => {
        getCampaignAutorefill.mockReset().mockResolvedValue({ session: 'ACTIVE', settings: setting() });
        setCampaignAutorefill.mockReset().mockImplementation(async (_id: number, s: WbAutorefillSetting) => ({
            ok: true, session: 'ACTIVE', error: null, settings: { ...setting(), ...s },
        }));
    });

    it('показывает правило и историю доливов из кабинета', async () => {
        render(<AutopayModal campaign={campaign} onClose={vi.fn()} />);

        await waitFor(() => expect(screen.getByLabelText('Порог остатка, ₽')).toHaveValue(100));
        expect(screen.getByLabelText('Сумма долива, ₽')).toHaveValue(5000);
        expect(screen.getByLabelText('Пополнений в день')).toHaveValue(1);
        expect(screen.getByText(/\+ 5 ?000 ₽/)).toBeInTheDocument();
    });

    it('изменения уходят в кабинет и наверх', async () => {
        const onSaved = vi.fn();
        const onClose = vi.fn();
        render(<AutopayModal campaign={campaign} onClose={onClose} onSaved={onSaved} />);

        await waitFor(() => expect(screen.getByLabelText('Сумма долива, ₽')).toHaveValue(5000));
        fireEvent.change(screen.getByLabelText('Порог остатка, ₽'), { target: { value: '300' } });
        fireEvent.change(screen.getByLabelText('Сумма долива, ₽'), { target: { value: '7000' } });
        fireEvent.click(screen.getByLabelText('Ограничить число пополнений в день'));
        fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

        await waitFor(() => expect(setCampaignAutorefill).toHaveBeenCalledWith(37227684, {
            enabled: true, threshold: 300, amount: 7000, daily_limit: false, limit: 1, unified_account: true,
        }));
        expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ amount: 7000 }));
        expect(onClose).toHaveBeenCalled();
    });

    it('сумма ниже минимума ВБ — кнопка заблокирована', async () => {
        render(<AutopayModal campaign={campaign} onClose={vi.fn()} />);

        await waitFor(() => expect(screen.getByLabelText('Сумма долива, ₽')).toHaveValue(5000));
        fireEvent.change(screen.getByLabelText('Сумма долива, ₽'), { target: { value: '300' } });

        expect(screen.getByText(/Минимальный бюджет — 1 ?000 ₽/)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
        expect(setCampaignAutorefill).not.toHaveBeenCalled();
    });

    it('протухшая сессия кабинета — предупреждение вместо формы, сохранить нечего', async () => {
        getCampaignAutorefill.mockResolvedValue({ session: 'EXPIRED', settings: null });
        render(<AutopayModal campaign={campaign} onClose={vi.fn()} />);

        await waitFor(() => expect(screen.getByText(/Доступ к кабинету ВБ истёк/)).toBeInTheDocument());
        expect(screen.queryByRole('button', { name: 'Сохранить' })).toBeNull();
        // важно: не выдаём отсутствие доступа за «автопополнение выключено»
        expect(screen.queryByLabelText('Сумма долива, ₽')).toBeNull();
    });

    it('доступ к кабинету не заведён — зовём настроить интеграцию', async () => {
        getCampaignAutorefill.mockResolvedValue({ session: 'NONE', settings: null });
        render(<AutopayModal campaign={campaign} onClose={vi.fn()} />);

        await waitFor(() => expect(screen.getByText(/Доступ к кабинету ВБ не настроен/)).toBeInTheDocument());
    });

    it('отказ ВБ при сохранении показывается в окне, окно не закрывается', async () => {
        setCampaignAutorefill.mockRejectedValue(new Error('HTTP 400: {"detail":"rate limit"}'));
        const onClose = vi.fn();
        render(<AutopayModal campaign={campaign} onClose={onClose} />);

        await waitFor(() => expect(screen.getByLabelText('Сумма долива, ₽')).toHaveValue(5000));
        fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

        await waitFor(() => expect(screen.getByText(/ограничил частоту запросов/)).toBeInTheDocument());
        expect(onClose).not.toHaveBeenCalled();
    });
});
