/**
 * Экран результата «Поставки готовы» не должен закрываться «тихо».
 *
 * Регресс: подложка результата висела на `onClose`, а вся постобработка
 * (сброс выделения, пересчёт сводки, перезагрузка списка) живёт в `onDone`.
 * Клик мимо карточки закрывал модалку — поставки в WB созданы, а таблица
 * по-прежнему показывала задания как «Новые» и с пустой колонкой «Поставка».
 * Повторный «В поставку» отдавал план, где всё заблокировано «уже в поставке
 * WB-GI-…», и пользователь читал это как проглоченный сбой создания.
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SupplyPlanModal from '@/app/(main)/p/[slug]/warehouse/fbs/SupplyPlanModal';

const plan = {
    groups: [
        {
            wb_warehouse_id: 555001,
            wb_warehouse_name: 'Склад Москва',
            cargo_type: 1,
            cross_border_type: null,
            order_ids: [8001, 8002],
            orders_count: 2,
            existing_supply_id: null,
            blocked_reason: null,
        },
    ],
    total_orders: 2,
    supplies_count: 1,
};

const bulkResult = {
    created: [{ wb_supply_id: 'WB-GI-NEW-1', name: 'FBS · Склад Москва', orders_count: 2 }],
    reused: [],
    orders_attached: 2,
    errors: [],
};

vi.mock('@/lib/api', () => ({
    api: {
        planFbsSupplies: vi.fn(async () => plan),
        createFbsSuppliesBulk: vi.fn(async () => bulkResult),
    },
}));

async function renderUpToResult() {
    const onDone = vi.fn();
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(
        <SupplyPlanModal
            orderIds={[8001, 8002]}
            writeEnabled
            writeHint=""
            onClose={onClose}
            onDone={onDone}
        />,
    );
    await user.click(await screen.findByRole('button', { name: /Создать/ }));
    await screen.findByText('Поставки готовы');
    return { onDone, onClose, user };
}

describe('SupplyPlanModal — экран результата', () => {
    it('клик по подложке ведёт в onDone, а не в тихий onClose', async () => {
        const { onDone, onClose, user } = await renderUpToResult();

        const overlay = document.querySelector('.modal-overlay');
        expect(overlay).not.toBeNull();
        await user.click(overlay as Element);

        await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
        expect(onClose).not.toHaveBeenCalled();
        expect(onDone.mock.calls[0][0]).toContain('заданий: 2');
    });

    it('«Готово» даёт тот же результат, что и клик по подложке', async () => {
        const { onDone, onClose, user } = await renderUpToResult();

        await user.click(screen.getByRole('button', { name: 'Готово' }));

        await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
        expect(onClose).not.toHaveBeenCalled();
    });

    it('клик по карточке модалку не закрывает', async () => {
        const { onDone, onClose, user } = await renderUpToResult();

        await user.click(screen.getByText('Поставки готовы'));

        expect(onDone).not.toHaveBeenCalled();
        expect(onClose).not.toHaveBeenCalled();
    });
});
