/**
 * Чистые хелперы модала «Связать заявку» (ФФ): клиентский поиск
 * и разбивка кандидатов на «похожие по наполнению» / «все документы».
 */
import type { FfLinkCandidate } from '@/types/api';

// Зеркало `_wh_*` бэкенда (fulfillment_service.py): стем-токенное совпадение
// имён складов сдачи. Маркетплейс-маркеры ФФ («ВБ | …») места не несут.
const WH_NOISE_TOKENS = new Set(['вб', 'wb']);

// Родовые/падежные окончания прилагательных («Перспективн-ый» ↔ «Перспективн-ая»).
// Длиннее раньше — срезаем максимальный суффикс.
const WH_ADJ_SUFFIXES = [
    'ого', 'его', 'ому', 'ему', 'ыми', 'ими',
    'ая', 'яя', 'ое', 'ее', 'ые', 'ие', 'ый', 'ий', 'ой',
    'ым', 'им', 'ом', 'ем', 'ых', 'их', 'ую', 'юю',
];

/** Срезать родовое/падежное окончание прилагательного, оставив стем ≥4 символов. */
export function whStem(token: string): string {
    for (const suf of WH_ADJ_SUFFIXES) {
        if (token.endsWith(suf) && token.length - suf.length >= 4) return token.slice(0, token.length - suf.length);
    }
    return token;
}

/** Имя склада → стемы слов + числа + стемы-прилагательные (улицы).
 *  Стем со срезанным окончанием = прилагательное-улица: по нему решаем,
 *  различают ли склады числа (топоним+номер «Чехов 1» vs улица+дом). */
export function whTokens(
    value: string | null | undefined,
): { words: Set<string>; digits: Set<string>; adjectives: Set<string> } {
    const words = new Set<string>();
    const digits = new Set<string>();
    const adjectives = new Set<string>();
    for (const tok of (value ?? '').toLowerCase().match(/[0-9a-zа-яё]+/g) ?? []) {
        if (WH_NOISE_TOKENS.has(tok)) continue;
        if (/^\d+$/.test(tok)) { digits.add(tok); continue; }
        const stem = whStem(tok);
        words.add(stem);
        if (stem !== tok) adjectives.add(stem); // окончание срезано → улица, не топоним
    }
    return { words, digits, adjectives };
}

/** Один и тот же склад сдачи под разными именами двух систем?
 *  Меньшее множество слов должно входить в большее (город + улица совпали).
 *  Числа различают склады ТОЛЬКО у топонима с порядковым номером («Чехов 1» ≠
 *  «Чехов 2»): если общий токен — прилагательное-улица, число это номер дома и
 *  расходится между системами («Перспективная 14» ≡ «Перспективный 12/2»).
 *  Пустое `a` (склад неизвестен) → true: фильтровать не по чему. */
export function whNamesMatch(a: string | null | undefined, b: string | null | undefined): boolean {
    const ta = whTokens(a);
    if (ta.words.size === 0) return true;
    const tb = whTokens(b);
    if (tb.words.size === 0) return false;
    const [small, large] = ta.words.size <= tb.words.size ? [ta.words, tb.words] : [tb.words, ta.words];
    for (const w of small) if (!large.has(w)) return false;
    // Общий токен — улица (прилагательное) → числа = дома, игнорируем.
    for (const w of small) if (ta.adjectives.has(w) || tb.adjectives.has(w)) return true;
    if (ta.digits.size > 0 && tb.digits.size > 0) {
        let intersects = false;
        for (const d of ta.digits) if (tb.digits.has(d)) { intersects = true; break; }
        if (!intersects) return false;
    }
    return true;
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
