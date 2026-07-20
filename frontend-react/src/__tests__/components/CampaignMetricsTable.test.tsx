/**
 * Таблица «По дням»: строки-события, светофор ДРР и пометка незакрытого дня.
 *
 * Событие рисуется ПОД своим днём — так разделитель встаёт на границу «до / после»
 * (строки идут от новых к старым). Порог светофора берётся из того же localStorage,
 * что и «Цель ДРР» в кластеризаторе.
 */
import { render, screen, within } from '@testing-library/react';
import { describe, it, expect, beforeEach } from 'vitest';
import CampaignMetricsTable from '@/app/(main)/p/[slug]/ads-manager/components/CampaignMetricsTable';
import { TARGET_DRR_KEY } from '@/app/(main)/p/[slug]/ads-manager/components/adsShared';
import type { CampaignMetricRow, CampaignMetricsResponse } from '@/types/api';

const mkRow = (date: string, over: Partial<CampaignMetricRow> = {}): CampaignMetricRow => ({
    date, views: 5000, clicks: 250, ctr: 5, cpc: 4, spend: 1000,
    open_card: 1000, add_to_cart: 80, cr1: 8, orders: 10, cr2: 12.5,
    orders_sum: 20800, cpl: 12.5, cpo: 100, avg_price: 2080, customer_price: 1300,
    spp: 37, drr: 4.8, is_partial: false, ...over,
});

const mkResp = (over: Partial<CampaignMetricsResponse> = {}): CampaignMetricsResponse => ({
    campaign_id: 1, name: 'Стандарт 180x200',
    window: { from: '2026-06-29', to: '2026-06-30' },
    target_drr: 10,
    totals: mkRow('За всё время'),
    rows: [mkRow('2026-06-30'), mkRow('2026-06-29', { avg_price: 2563, drr: 12 })],
    events: [],
    ...over,
});

describe('CampaignMetricsTable', () => {
    beforeEach(() => localStorage.clear());

    it('событие цены — метка в своём дне, строк не прибавляется', () => {
        const rowsBefore = render(<CampaignMetricsTable resp={mkResp()} />).container
            .querySelectorAll('tbody tr').length;
        const { container } = render(<CampaignMetricsTable resp={mkResp({
            events: [{ date: '2026-06-30', kind: 'price', short: 'цена', value: '-19%', dir: -1, text: 'наша цена 2 563 ₽ → 2 080 ₽ (-19%)' }],
        })} />);
        expect(container.querySelectorAll('tbody tr').length).toBe(rowsBefore);
        const dayRow = within(container).getAllByRole('row').find(r => within(r).queryByText('30.06.26'))!;
        expect(within(dayRow).getByText('-19%')).toBeTruthy();
    });

    it('наша цена и скидка ВБ различаются цветом — это разные решения', () => {
        const { container } = render(<CampaignMetricsTable resp={mkResp({
            events: [
                { date: '2026-06-30', kind: 'price', short: 'цена', value: '-19%', dir: -1, text: 'наша цена 2 563 ₽ → 2 080 ₽ (-19%)' },
                { date: '2026-06-30', kind: 'spp', short: 'СПП', value: '+5 п.п.', dir: 1, text: 'скидка ВБ (СПП) 36% → 41% · клиенту 1 342 ₽ → 1 227 ₽' },
            ],
        })} />);
        const dayRow = within(container).getAllByRole('row').find(r => within(r).queryByText('30.06.26'))!;
        const our = within(dayRow).getByText('-19%').parentElement!.getAttribute('style') ?? '';
        const wb = within(dayRow).getByText('+5 п.п.').parentElement!.getAttribute('style') ?? '';
        expect(our).toContain('219, 234, 254');   // синий фон — наше решение
        expect(wb).toContain('237, 233, 254');    // фиолетовый фон — решение маркетплейса
    });

    it('метка остановки несёт час прямо в строке, а не только в подсказке', () => {
        // Колонку дат просматривают сверху вниз — час должен читаться без наведения
        const { container } = render(<CampaignMetricsTable resp={mkResp({
            events: [{ date: '2026-06-30', kind: 'budget', short: 'стоп', value: '21:43', dir: 0, text: 'бюджет кончился в 21:43 — день неполный' }],
        })} />);
        const dayRow = within(container).getAllByRole('row').find(r => within(r).queryByText('30.06.26'))!;
        expect(within(dayRow).getByText('21:43')).toBeTruthy();
    });

    it('остановка по бюджету — метка в дате, а не отдельная строка', () => {
        // У кампании с хроническим недобором событие приходится на КАЖДЫЙ день:
        // строками-разделителями таблица превращалась бы в зебру.
        const plain = render(<CampaignMetricsTable resp={mkResp()} />).container;
        const rowsBefore = plain.querySelectorAll('tbody tr').length;
        const { container } = render(<CampaignMetricsTable resp={mkResp({
            events: [
                { date: '2026-06-30', kind: 'budget', short: 'стоп', value: '18:40', dir: 0, text: 'бюджет кончился в 18:40 — день неполный' },
                { date: '2026-06-29', kind: 'budget', short: 'стоп', value: '20:10', dir: 0, text: 'бюджет кончился в 20:10 — день неполный' },
            ],
        })} />);
        expect(container.querySelectorAll('tbody tr').length).toBe(rowsBefore);  // строк не прибавилось
        const dayRow = within(container).getAllByRole('row').find(r => within(r).queryByText('30.06.26'))!;
        expect(within(dayRow).getByText('18:40')).toBeTruthy();
    });

    it('красит число по знаку: минус красным, плюс зелёным', () => {
        // Фон уже сказал, ЧЬЁ это решение; цвет цифры — в какую сторону оно сдвинуло
        const { container } = render(<CampaignMetricsTable resp={mkResp({
            events: [
                { date: '2026-06-30', kind: 'price', short: 'цена', value: '-19%', dir: -1, text: 'наша цена 2 563 ₽ → 2 080 ₽ (-19%)' },
                { date: '2026-06-29', kind: 'price', short: 'цена', value: '+31%', dir: 1, text: 'наша цена 2 080 ₽ → 2 725 ₽ (+31%)' },
            ],
        })} />);
        expect(within(container).getByText('-19%').getAttribute('style')).toContain('220, 38, 38');
        expect(within(container).getByText('+31%').getAttribute('style')).toContain('22, 163, 74');
    });

    it('у часа остановки направления нет — цифру не красим', () => {
        const { container } = render(<CampaignMetricsTable resp={mkResp({
            events: [{ date: '2026-06-30', kind: 'budget', short: 'стоп', value: '21:43', dir: 0, text: 'бюджет кончился в 21:43 — день неполный' }],
        })} />);
        const v = within(container).getByText('21:43').getAttribute('style') ?? '';
        expect(v).not.toContain('220, 38, 38');
        expect(v).not.toContain('22, 163, 74');
    });

    it('без событий таблица остаётся прежней', () => {
        render(<CampaignMetricsTable resp={mkResp({ events: undefined })} />);
        expect(screen.getByText('30.06.26')).toBeTruthy();
        expect(screen.queryByText(/цена -/)).toBeNull();
    });

    it('красит ДРР по цели из localStorage, а не по значению кампании', () => {
        localStorage.setItem(TARGET_DRR_KEY, '4');  // цель строже, чем target_drr=10 из ответа
        render(<CampaignMetricsTable resp={mkResp()} />);
        // ДРР 4.8% при цели 4% — уже не зелёный (≤ 1.5×цели → янтарь).
        // Ищем в строке дня: то же значение стоит и в итоге «За всё время».
        const dayRow = screen.getAllByRole('row').find(r => within(r).queryByText('30.06.26'))!;
        expect(within(dayRow).getByText('4.8%').getAttribute('style')).toContain('245, 158, 11');
    });

    it('день без продаж — ДРР не определён, а не «идеальный»', () => {
        // Реклама открутилась, заказов ноль: ДРР=0 арифметически. Зелёный здесь —
        // ровно обратный сигнал тому, что произошло.
        render(<CampaignMetricsTable resp={mkResp({
            rows: [mkRow('2026-06-30', { orders: 0, orders_sum: 0, drr: 0, spend: 570 }), mkRow('2026-06-29')],
        })} />);
        const dayRow = screen.getAllByRole('row').find(r => within(r).queryByText('30.06.26'))!;
        const cells = within(dayRow).getAllByRole('cell');
        const drrCell = cells[cells.length - 1];
        expect(drrCell.textContent).toBe('—');
        expect(drrCell.getAttribute('style') ?? '').not.toContain('16, 185, 129');  // не зелёный
    });

    it('незакрытый день помечен и не красится светофором', () => {
        render(<CampaignMetricsTable resp={mkResp({
            rows: [mkRow('2026-06-30', { is_partial: true, drr: 40 }), mkRow('2026-06-29')],
        })} />);
        expect(screen.getByText('идёт')).toBeTruthy();
        // ДРР 40% при цели 10% был бы красным, но день неполный — цвет не навязываем
        expect(screen.getByText('40.0%').getAttribute('style') ?? '').not.toContain('239, 68, 68');
    });
});
