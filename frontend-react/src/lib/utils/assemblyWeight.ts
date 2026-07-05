/**
 * assemblyWeight — какие заявки можно авто-заполнить расчётным «Общим весом».
 *
 * Кандидат = статус «В сборке» (IN_PROGRESS) И «Общий вес» ещё пуст/ноль
 * И бэкенд отдал непустой расчётный вес отгрузки (suggested_total_weight_kg).
 * suggested_total_weight_kg — Decimal, приходит СТРОКОЙ → нормализуем через Number().
 */
import type { AssemblyRequest } from '@/types/api';

/** Есть ли осмысленный (> 0) расчётный вес отгрузки. Терпит string|number|null. */
export function hasSuggestedWeight(row: Pick<AssemblyRequest, 'suggested_total_weight_kg'>): boolean {
    const s = row.suggested_total_weight_kg;
    if (s == null || s === '') return false;
    const n = Number(s);
    return Number.isFinite(n) && n > 0;
}

/** Заявка подходит под авто-заполнение «Общего веса». */
export function isWeightAutofillEligible(row: AssemblyRequest): boolean {
    if (row.status !== 'IN_PROGRESS') return false;
    const w = row.total_weight_kg;
    const weightEmpty = w == null || Number(w) <= 0;
    return weightEmpty && hasSuggestedWeight(row);
}

/** id всех подходящих под авто-заполнение заявок из переданного списка (обычно — видимые строки). */
export function weightAutofillIds(rows: AssemblyRequest[]): number[] {
    return rows.filter(isWeightAutofillEligible).map(r => r.id);
}
