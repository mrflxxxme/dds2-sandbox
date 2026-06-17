/**
 * Чистые хелперы модала «Связать заявку» (ФФ): клиентский поиск
 * и разбивка кандидатов на «похожие по наполнению» / «все документы».
 */
import type { FfLinkCandidate } from '@/types/api';

/**
 * Нормализация имени склада для сопоставления (зеркало `_norm_wh_name` бэкенда):
 * lower-case + только буквы/цифры. «Коледино» ↔ «МСК Коледино» матчатся по вхождению.
 */
export function normWhName(value: string | null | undefined): string {
    return (value ?? '').toLowerCase().replace(/[^0-9a-zа-яё]+/g, '');
}

/** Совпадение складов сдачи по нормализованным именам (вхождение в обе стороны).
 *  Пустой `a` (склад неизвестен) → true: фильтровать не по чему. */
export function whNamesMatch(a: string | null | undefined, b: string | null | undefined): boolean {
    const na = normWhName(a);
    if (!na) return true;
    const nb = normWhName(b);
    return !!nb && (na.includes(nb) || nb.includes(na));
}

/** Поиск по номеру / ФБО-поставке / складу назначения (case-insensitive) */
export function filterFfLinkCandidates(candidates: FfLinkCandidate[], query: string): FfLinkCandidate[] {
    const q = query.trim().toLowerCase();
    if (!q) return candidates;
    return candidates.filter(c =>
        c.number.toLowerCase().includes(q)
        || (c.fbo_supply_number ?? '').toLowerCase().includes(q)
        || (c.dest_warehouse ?? '').toLowerCase().includes(q));
}

/**
 * Разбивка: scored — кандидаты со score != null (по убыванию score),
 * others — остальные (по убыванию created_at; без даты — в конце).
 */
export function splitFfLinkCandidates(candidates: FfLinkCandidate[]): {
    scored: FfLinkCandidate[];
    others: FfLinkCandidate[];
} {
    const scored = candidates
        .filter(c => c.score != null)
        .sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
    const others = candidates
        .filter(c => c.score == null)
        .sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? ''));
    return { scored, others };
}
