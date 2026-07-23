import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('@/lib/api', () => ({
    api: {
        getAllWbWarehouses: vi.fn(),
        getExcludedWarehouses: vi.fn(),
        getPreorderAllowedWarehouses: vi.fn(),
        getStockIgnoredWarehouses: vi.fn(),
        getForecastRfDefaultDays: vi.fn(),
        getPalletCategoryCompat: vi.fn(),
        getBoxMultiplicity: vi.fn(),
        getCategoryOverrides: vi.fn(),
        setExcludedWarehouses: vi.fn(),
        setPreorderAllowedWarehouses: vi.fn(),
        setStockIgnoredWarehouses: vi.fn(),
        setPalletCategoryCompat: vi.fn(),
        setForecastRfDefaultDays: vi.fn(),
    },
}));

import { api } from '@/lib/api';
import { WarehouseExclusionSettings } from '@/app/(main)/p/[slug]/warehouse/analytics/components/WarehouseExclusionSettings';

const FIRE_TITLE = 'Остатки не учитывать (сгорел/потерян)';

// Кнопка «💾 Сохранить» есть и у блока «Время РФ» (задизейблена без изменений) —
// главная кнопка сохранения находится по признаку доступности.
function mainSaveButton() {
    const btn = screen
        .getAllByRole('button', { name: '💾 Сохранить' })
        .find(b => !(b as HTMLButtonElement).disabled);
    expect(btn).toBeDefined();
    return btn as HTMLButtonElement;
}

describe('WarehouseExclusionSettings — 🔥 остатки не учитывать', () => {
    beforeEach(() => {
        vi.mocked(api.getAllWbWarehouses).mockResolvedValue([
            { name: 'Краснодар', lat: 0, lng: 0 },
            { name: 'Коледино', lat: 0, lng: 0 },
        ]);
        vi.mocked(api.getExcludedWarehouses).mockResolvedValue([]);
        vi.mocked(api.getPreorderAllowedWarehouses).mockResolvedValue([]);
        vi.mocked(api.getStockIgnoredWarehouses).mockResolvedValue([]);
        vi.mocked(api.getForecastRfDefaultDays).mockResolvedValue({ days: 8 });
        vi.mocked(api.getPalletCategoryCompat).mockResolvedValue({ enabled: false, groups: [] });
        vi.mocked(api.getBoxMultiplicity).mockResolvedValue({ items: [] } as never);
        vi.mocked(api.getCategoryOverrides).mockResolvedValue({});
        vi.mocked(api.setExcludedWarehouses).mockResolvedValue({ ok: true, excluded: [] });
        vi.mocked(api.setPreorderAllowedWarehouses).mockResolvedValue({ ok: true, preorder_allowed: [] });
        vi.mocked(api.setStockIgnoredWarehouses).mockResolvedValue({ ok: true, stock_ignored: ['Краснодар'] });
        vi.mocked(api.setPalletCategoryCompat).mockResolvedValue({ ok: true, enabled: false, groups: [] });
    });

    it('клик по 🔥 НЕ флипает excluded-чекбокс склада', async () => {
        const user = userEvent.setup();
        render(<WarehouseExclusionSettings />);

        const [fireKrasnodar] = await screen.findAllByTitle(FIRE_TITLE);
        await user.click(fireKrasnodar);

        // Склад не исключился: счётчика «⛔ Исключено» нет, а 🔥-счётчик появился
        expect(screen.queryByText(/⛔ Исключено/)).not.toBeInTheDocument();
        expect(screen.getByText(/🔥 Остатки игнорируются: 1/)).toBeInTheDocument();
    });

    it('save шлёт setStockIgnoredWarehouses с выбранными складами', async () => {
        const user = userEvent.setup();
        render(<WarehouseExclusionSettings />);

        const [fireKrasnodar] = await screen.findAllByTitle(FIRE_TITLE);
        await user.click(fireKrasnodar);
        await user.click(mainSaveButton());

        expect(await screen.findByText('✅ Сохранено')).toBeInTheDocument();
        expect(api.setStockIgnoredWarehouses).toHaveBeenCalledWith(['Краснодар']);
        // excluded при этом не изменился
        expect(api.setExcludedWarehouses).toHaveBeenCalledWith([]);
    });

    it('повторный клик по 🔥 снимает игнор', async () => {
        const user = userEvent.setup();
        render(<WarehouseExclusionSettings />);

        const [fireKrasnodar] = await screen.findAllByTitle(FIRE_TITLE);
        await user.click(fireKrasnodar);
        await user.click(fireKrasnodar);

        expect(screen.queryByText(/🔥 Остатки игнорируются/)).not.toBeInTheDocument();
    });

    it('загруженный список 🔥 показывает счётчик и нижнюю сводку', async () => {
        vi.mocked(api.getStockIgnoredWarehouses).mockResolvedValue(['Краснодар', 'Коледино']);
        render(<WarehouseExclusionSettings />);

        expect(await screen.findByText(/🔥 Остатки игнорируются: 2/)).toBeInTheDocument();
        expect(screen.getByText(/остатки не учитываются/i)).toBeInTheDocument();
    });
});
