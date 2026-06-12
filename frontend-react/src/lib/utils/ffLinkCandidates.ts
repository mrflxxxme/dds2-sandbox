/**
 * Чистые хелперы модала «Связать заявку» (ФФ): клиентский поиск
 * и разбивка кандидатов на «похожие по наполнению» / «все документы».
 */
import type { FfLinkCandidate } from '@/types/api';

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
