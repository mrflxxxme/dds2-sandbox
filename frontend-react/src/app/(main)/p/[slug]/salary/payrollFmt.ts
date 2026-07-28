/** Shared formatting helpers for the Зарплата section. */
import { formatNumber } from '@/lib/utils';
import type { PayrollPayDayShare, PayrollScopeKind, PayrollTeamScope } from '@/types/api';

// Numeric-поля бэка приходят строками — коэрсим перед formatNumber.
// Дефолт 2 знака: суммы строк обязаны биться с «Итого» и фактами выписки
// копейка в копейку (сводные KPI показывают без копеек — money(x, 0) явно).
export const money = (x: number | string | null | undefined, d = 2): string =>
    x == null || x === '' ? '—' : formatNumber(Number(x), d);

/** Доля → человекочитаемый процент без хвостовых нулей: 0.00972 → «0,972%». */
export const ratePct = (x: number | string | null | undefined): string => {
    if (x == null || x === '') return '—';
    const v = +(Number(x) * 100).toFixed(3);
    const decimals = (String(v).split('.')[1] ?? '').length;
    return `${formatNumber(v, decimals)}%`;
};

/** «2026-06-01» → «01.06». */
export const shortDate = (iso: string | null | undefined): string => {
    if (!iso) return '—';
    const [y, m, d] = iso.split('-');
    return y && m && d ? `${d}.${m}` : iso;
};

/** Неделя Пн-Вс: «01.06 – 07.06». */
export const weekLabel = (from: string, to: string): string =>
    `${shortDate(from)} – ${shortDate(to)}`;

const MONTH_NAMES: readonly string[] = [
    'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
];

const MONTH_GEN: readonly string[] = [
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
];

/** «2026-06» → «Июнь 2026». */
export const monthTitle = (iso: string | null | undefined): string => {
    if (!iso) return '—';
    const [y, m] = iso.split('-');
    const mi = m ? Number(m) - 1 : NaN;
    if (!y || Number.isNaN(mi) || mi < 0 || mi > 11) return iso;
    return `${MONTH_NAMES[mi]} ${y}`;
};

/** «2026-07» → «июля 2026» (родительный падеж, для «с …»). */
export const monthGenLabel = (iso: string | null | undefined): string => {
    if (!iso) return '—';
    const [y, m] = iso.split('-');
    const mi = m ? Number(m) - 1 : NaN;
    if (!y || Number.isNaN(mi) || mi < 0 || mi > 11) return iso;
    return `${MONTH_GEN[mi]} ${y}`;
};

/** «2026-07-10» → «10 июля». */
export const dayMonthLabel = (iso: string | null | undefined): string => {
    if (!iso) return '—';
    const [, m, d] = iso.split('-');
    const mi = m ? Number(m) - 1 : NaN;
    if (!d || Number.isNaN(mi) || mi < 0 || mi > 11) return iso;
    return `${Number(d)} ${MONTH_GEN[mi]}`;
};

/** Прошлый месяц от сегодня → 'YYYY-MM' (дефолт селектора ведомости). */
export const prevMonthIso = (): string => {
    const d = new Date();
    d.setDate(1);
    d.setMonth(d.getMonth() - 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
};

/** Сдвиг месяца 'YYYY-MM' на ±n. */
export const shiftMonth = (iso: string, n: number): string => {
    const [y, m] = iso.split('-').map(Number);
    if (!y || !m) return iso;
    const d = new Date(y, m - 1 + n, 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
};

export const SCOPE_KIND_LABEL: Record<PayrollScopeKind, string> = {
    brand: 'Бренд',
    subject: 'Категория',
};

export const scopeBadgeClass = (kind: PayrollScopeKind): string =>
    kind === 'brand' ? 'badge badge-info' : 'badge badge-secondary';

export const scopeLabel = (s: PayrollTeamScope): string =>
    `${SCOPE_KIND_LABEL[s.kind]}: ${s.value}`;

/** График фикс-оклада → человекочитаемая подпись. */
export const payScheduleLabel = (days: PayrollPayDayShare[] | null | undefined): string => {
    if (!days || days.length === 0) return '50/50 на 10-е и 25-е';
    if (days.length === 1 && Number(days[0].share) === 1) return `разово ${days[0].day}-го`;
    const sorted = [...days].sort((a, b) => a.day - b.day);
    if (
        sorted.length === 2 &&
        sorted[0].day === 10 && sorted[1].day === 25 &&
        Number(sorted[0].share) === 0.5 && Number(sorted[1].share) === 0.5
    ) return '50/50 на 10-е и 25-е';
    return sorted.map((s) => `${s.day}-го — ${ratePct(s.share)}`).join(', ');
};
