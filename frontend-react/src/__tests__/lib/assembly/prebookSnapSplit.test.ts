/**
 * Split под-вкладок «Предбронь → МОНО»: «Дозабить/Предзаявка» идёт по ЛИМИТУ приёмки
 * (⌛ noLimit), а не по footprint. Предикат — `isPrebookingByLimit`.
 */
import { describe, it, expect } from 'vitest';
import { isPrebookingByLimit } from '@/lib/assembly/prebookFootprint';

describe('isPrebookingByLimit — split по ⌛, не по footprint', () => {
    it('⌛ без лимита (noLimit) → в «Предзаявку»', () => {
        expect(isPrebookingByLimit({ noLimit: true })).toBe(true);
    });
    it('есть лимит → в «Дозабить»', () => {
        expect(isPrebookingByLimit({ noLimit: false })).toBe(false);
    });
    it('нет данных приёмки (undefined) → в «Дозабить» (не теряем карточку)', () => {
        expect(isPrebookingByLimit(undefined)).toBe(false);
    });
    it('приёмка не проверена → в «Дозабить»', () => {
        // Реальная метка приёмки шире — helper смотрит только noLimit (лишние поля ок).
        expect(isPrebookingByLimit({ checked: false, noLimit: false } as { noLimit?: boolean })).toBe(false);
    });
});
