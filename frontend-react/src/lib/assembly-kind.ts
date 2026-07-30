import type { AssemblyKind } from '@/types/api';

/**
 * Типы заявок на сборку — зеркало backend/schemas/assembly.py::ALLOWED_ASSEMBLY_KINDS.
 *
 * fbo — операционная заявка логиста (создаётся и ведётся руками);
 * fbs — учётное зеркало сборки, которую ведёт сам фулфилмент по поставке FBS
 * WB (WB-GI-…): одна заявка = одна поставка, статусы двигает джоб, руками
 * заявка не редактируется и не удаляется.
 */
export const ALLOWED_ASSEMBLY_KINDS: readonly AssemblyKind[] = ['fbo', 'fbs'];

/** Ярлык бейджа типа (в списке бейдж показывается только у kind=fbs). */
export const KIND_LABEL: Record<AssemblyKind, string> = {
    fbo: 'FBO',
    fbs: 'FBS',
};

/** Цвет бейджа типа — классы из дизайн-системы (badge badge-*). */
export const KIND_BADGE_CLASS: Record<AssemblyKind, string> = {
    fbo: 'badge-secondary',
    fbs: 'badge-info',
};

/**
 * null-безопасный резолвер: старый бэк (окно деплоя) поле не шлёт —
 * отсутствие/мусор читаем как 'fbo' (зеркало дефолта Pydantic-схемы).
 */
export function assemblyKindOf(kind: string | null | undefined): AssemblyKind {
    return kind === 'fbs' ? 'fbs' : 'fbo';
}

/**
 * Опции фильтра «Тип» на списке заявок: «все» + ровно ALLOWED_ASSEMBLY_KINDS
 * (контракт закреплён тестом assemblyKind.test.ts).
 */
export const KIND_FILTER_OPTIONS: { value: '' | AssemblyKind; label: string }[] = [
    { value: '', label: 'Тип: все' },
    ...ALLOWED_ASSEMBLY_KINDS.map(k => ({ value: k, label: KIND_LABEL[k] })),
];
