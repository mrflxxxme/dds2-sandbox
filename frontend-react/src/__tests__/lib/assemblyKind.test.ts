/**
 * Типы заявок на сборку (kind) — контракт словаря и фильтра.
 *
 * Что закрепляется:
 *  1. ALLOWED_ASSEMBLY_KINDS — зеркало backend/schemas/assembly.py: коды,
 *     которые реально отдаёт `AssemblyRequestResponse.kind` и принимает
 *     query `kind` списка заявок. Промах словаря в рантайме невидим —
 *     бейдж просто не отрисуется, контракт держится этим тестом;
 *  2. ярлык и цвет бейджа kind=fbs закреплены дословно — их видит
 *     пользователь в списке заявок;
 *  3. опции фильтра «Тип» соответствуют ALLOWED_ASSEMBLY_KINDS: «все» +
 *     ровно по опции на каждый код, без мёртвых и без пропущенных;
 *  4. assemblyKindOf null-безопасен: старый бэк (окно деплоя) поле не шлёт —
 *     отсутствие/мусор читается как fbo (зеркало дефолта Pydantic-схемы).
 */
import { describe, expect, it } from 'vitest';
import {
    ALLOWED_ASSEMBLY_KINDS,
    KIND_BADGE_CLASS,
    KIND_FILTER_OPTIONS,
    KIND_LABEL,
    assemblyKindOf,
} from '@/lib/assembly-kind';

/** Коды, которые РЕАЛЬНО отдаёт бэкенд (ALLOWED_ASSEMBLY_KINDS схемы). */
const BACKEND_KINDS = ['fbo', 'fbs'];

describe('ALLOWED_ASSEMBLY_KINDS', () => {
    it('зеркалит коды бэкенда один в один', () => {
        expect([...ALLOWED_ASSEMBLY_KINDS].sort()).toEqual([...BACKEND_KINDS].sort());
    });
});

describe('KIND_LABEL / KIND_BADGE_CLASS', () => {
    it('покрывают все коды бэкенда', () => {
        for (const code of BACKEND_KINDS) {
            expect(KIND_LABEL[code as keyof typeof KIND_LABEL], `код ${code} без ярлыка`).toBeTruthy();
            expect(KIND_BADGE_CLASS[code as keyof typeof KIND_BADGE_CLASS], `код ${code} без цвета`).toBeTruthy();
        }
    });

    it('ярлык и цвет бейджа FBS закреплены дословно — их видит пользователь', () => {
        expect(KIND_LABEL.fbs).toBe('FBS');
        expect(KIND_BADGE_CLASS.fbs).toBe('badge-info');
    });

    it('в словарях нет мёртвых ключей, о которых бэкенд не знает', () => {
        expect(Object.keys(KIND_LABEL).sort()).toEqual([...BACKEND_KINDS].sort());
        expect(Object.keys(KIND_BADGE_CLASS).sort()).toEqual([...BACKEND_KINDS].sort());
    });

    it('цвета — классы дизайн-системы badge-*', () => {
        for (const cls of Object.values(KIND_BADGE_CLASS)) {
            expect(cls).toMatch(/^badge-[a-z]+$/);
        }
    });
});

describe('KIND_FILTER_OPTIONS (фильтр «Тип» на списке заявок)', () => {
    it('первая опция — «все» с пустым value (kind не передаётся в запрос)', () => {
        expect(KIND_FILTER_OPTIONS[0]).toEqual({ value: '', label: 'Тип: все' });
    });

    it('остальные опции — ровно ALLOWED_ASSEMBLY_KINDS, в том же порядке', () => {
        expect(KIND_FILTER_OPTIONS.slice(1).map(o => o.value)).toEqual([...ALLOWED_ASSEMBLY_KINDS]);
    });

    it('ярлыки опций берутся из KIND_LABEL, а не дублируются строками', () => {
        for (const o of KIND_FILTER_OPTIONS.slice(1)) {
            expect(o.label).toBe(KIND_LABEL[o.value as keyof typeof KIND_LABEL]);
        }
    });
});

describe('assemblyKindOf', () => {
    it('валидные коды проходят как есть', () => {
        expect(assemblyKindOf('fbo')).toBe('fbo');
        expect(assemblyKindOf('fbs')).toBe('fbs');
    });

    it('отсутствие поля (старый бэк) и мусор — fbo, зеркало дефолта схемы', () => {
        expect(assemblyKindOf(undefined)).toBe('fbo');
        expect(assemblyKindOf(null)).toBe('fbo');
        expect(assemblyKindOf('')).toBe('fbo');
        expect(assemblyKindOf('mystery')).toBe('fbo');
    });
});
