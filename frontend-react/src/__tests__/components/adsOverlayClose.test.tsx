/**
 * Подложка модалок раздела рекламы: окно закрывает только «настоящий» клик по фону.
 * Выделение текста в поле, законченное за краем окна, раньше давало click на подложке
 * и захлопывало окно вместе с несохранённой формой.
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { useOverlayClose } from '@/app/(main)/p/[slug]/ads-manager/components/adsShared';

function Modal({ onClose }: { onClose: () => void }) {
    const overlay = useOverlayClose(onClose);
    return (
        <div data-testid="overlay" {...overlay}>
            <div data-testid="window"><input aria-label="Сумма" /></div>
        </div>
    );
}

describe('useOverlayClose', () => {
    it('клик по подложке закрывает окно', () => {
        const onClose = vi.fn();
        render(<Modal onClose={onClose} />);
        const overlay = screen.getByTestId('overlay');

        fireEvent.mouseDown(overlay);
        fireEvent.click(overlay);
        expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('протяжка изнутри окна на подложку не закрывает', () => {
        const onClose = vi.fn();
        render(<Modal onClose={onClose} />);

        // мышь нажата в поле ввода, отпущена уже на фоне → click всплывает до подложки
        fireEvent.mouseDown(screen.getByLabelText('Сумма'));
        fireEvent.click(screen.getByTestId('overlay'));
        expect(onClose).not.toHaveBeenCalled();
    });

    it('клик внутри окна не закрывает', () => {
        const onClose = vi.fn();
        render(<Modal onClose={onClose} />);

        fireEvent.mouseDown(screen.getByTestId('window'));
        fireEvent.click(screen.getByTestId('window'));
        expect(onClose).not.toHaveBeenCalled();
    });
});
