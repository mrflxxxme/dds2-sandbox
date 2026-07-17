import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import VibecodingPage from '@/app/(main)/p/[slug]/vibecoding/page';
import type { VibeStats } from '@/types/api';

vi.mock('@/lib/api', () => ({ api: { getVibeStats: vi.fn() } }));

import { api } from '@/lib/api';

const getVibeStats = vi.mocked(api.getVibeStats);

function makeStats(over: Partial<VibeStats> = {}): VibeStats {
    return {
        display_name: 'Денис',
        since: '2026-07-01',
        until: '2026-07-03',
        shipments_total: 3,
        shipments_product: 2,
        rhythm: { hit: 2, denom: 3, window: 3, start: '2026-07-01', end: '2026-07-03' },
        scale: {
            files: 12, new_files: 4, components: 2, migrations: 1, sections: 2,
            added: 900, deleted: 120,
            by_area: [{ area: 'frontend', files: 8, added: 700, deleted: 100 }],
        },
        by_day: [
            { day: '2026-07-01', shipments: 2, added: 600, deleted: 100 },
            { day: '2026-07-02', shipments: 0, added: 0, deleted: 0 },
            { day: '2026-07-03', shipments: 1, added: 300, deleted: 20 },
        ],
        by_section: [{ section: 'Управление рекламой', count: 3 }],
        shipments: [{
            sha: 'f96487ba1111111111111111111111111111aaaa', short: 'f96487ba', day: '2026-07-03',
            ctype: 'feat', scope: 'ads', section: 'Управление рекламой',
            title: 'колонка «Реком. ставка»', added: 300, deleted: 20, files: 4, is_product: true,
        }],
        last_ingest: '2026-07-03',
        ...over,
    };
}

function forbidden(): Error & { status: number } {
    const e = new Error('Нет доступа') as Error & { status: number };
    e.status = 403;
    return e;
}

describe('Страница «Вайбкодинг»', () => {
    beforeEach(() => { getVibeStats.mockReset(); });

    it('loading — пока запрос не ответил', () => {
        getVibeStats.mockReturnValue(new Promise(() => { }));

        render(<VibecodingPage />);

        expect(screen.getByText('Загрузка...')).toBeInTheDocument();
    });

    it('data — ритм, KPI, масштаб, разделы и лента', async () => {
        getVibeStats.mockResolvedValue(makeStats());

        const { container } = render(<VibecodingPage />);

        // Ритм: герой hit/denom + окно
        await screen.findByText(/с поставкой на прод/);
        expect(container.querySelector('.vibe-fig')).toHaveTextContent('2/3');
        expect(screen.getByText(/окно 3 дня/)).toBeInTheDocument();
        // KPI — штуки без «,00», подпись о продуктовых
        expect(screen.getByText('из них продуктовых: 2')).toBeInTheDocument();
        expect(screen.getByText('−120 удалено')).toBeInTheDocument();
        // Масштаб + лента (тип поставки — по-русски, как в эталоне)
        expect(screen.getByText('8 файлов')).toBeInTheDocument();
        expect(screen.getByText('колонка «Реком. ставка»')).toBeInTheDocument();
        expect(screen.getByText('фича')).toBeInTheDocument();
        expect(screen.getByText('f96487ba')).toBeInTheDocument();
    });

    it('полоска ритма: день с поставкой закрашен, пауза — пропуск', async () => {
        getVibeStats.mockResolvedValue(makeStats());

        const { container } = render(<VibecodingPage />);
        await screen.findByText(/с поставкой на прод/);

        // Окно 01.07—03.07: три пилюли, средний день без поставки не закрашен
        expect(container.querySelectorAll('.vibe-pip')).toHaveLength(3);
        expect(container.querySelectorAll('.vibe-pip.on')).toHaveLength(2);
    });

    it('календарь: ячейка красится по ступени рампы, пустой день — нулевая ступень', async () => {
        getVibeStats.mockResolvedValue(makeStats());

        const { container } = render(<VibecodingPage />);
        await screen.findByText(/с поставкой на прод/);

        const cells = [...container.querySelectorAll('.vibe-cell:not(.void)')];
        expect(cells).toHaveLength(3);
        // 2 поставки → ступень n1, 0 → без класса ступени, 1 → n1
        expect(cells[0]).toHaveClass('n1');
        expect(cells[0]).toHaveTextContent('2');
        expect(cells[1].className).toBe('vibe-cell');
        expect(cells[1]).toHaveTextContent('');
        // Дни вне периода — пустые клетки, а не «ноль поставок»
        expect(container.querySelectorAll('.vibe-cell.void').length).toBeGreaterThan(0);
    });

    it('день с нулём — пустое место, а не полоска', async () => {
        getVibeStats.mockResolvedValue(makeStats());

        const { container } = render(<VibecodingPage />);
        await screen.findByText(/с поставкой на прод/);

        // 3 дня в by_day держат место, но полоска — только у двух непустых:
        // min-height нужен мелким значениям, для нуля он рисовал бы работу, которой не было
        expect(container.querySelectorAll('.vibe-col')).toHaveLength(3);
        expect(container.querySelectorAll('.vibe-cbar')).toHaveLength(2);
    });

    it('лента поставок выгружается в Excel — канон требует экспорт у таблиц', async () => {
        getVibeStats.mockResolvedValue(makeStats());

        render(<VibecodingPage />);

        expect(await screen.findByRole('button', { name: 'Выгрузить в Excel' })).toBeInTheDocument();
    });

    it('empty — данные есть, поставок в периоде нет', async () => {
        getVibeStats.mockResolvedValue(makeStats({ shipments_total: 0 }));

        render(<VibecodingPage />);

        expect(await screen.findByText('За этот период поставок на прод нет')).toBeInTheDocument();
        expect(screen.queryByText('Лента поставок')).not.toBeInTheDocument();
    });

    it('error — сбой загрузки показывает причину и кнопку повтора', async () => {
        getVibeStats.mockRejectedValue(new Error('Сервер временно недоступен'));

        render(<VibecodingPage />);

        expect(await screen.findByText('Не удалось загрузить статистику')).toBeInTheDocument();
        expect(screen.getByText('Сервер временно недоступен')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Повторить' })).toBeInTheDocument();
    });

    it('403 — «вкладка не для вас», а не красная ошибка', async () => {
        getVibeStats.mockRejectedValue(forbidden());

        render(<VibecodingPage />);

        expect(await screen.findByText('Вкладка не для вас')).toBeInTheDocument();
        expect(screen.queryByText('Не удалось загрузить статистику')).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Повторить' })).not.toBeInTheDocument();
    });
});
