'use client';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { isForbidden } from '@/lib/api/vibe';
import { formatDate, formatNumber, pluralRu, exportToExcel, type ExcelExportColumn } from '@/lib/utils';
import type { VibeStats, VibeShipment, VibeDayVolume } from '@/types/api';

/* ─── Даты ──────────────────────────────────────────────────────────────────── */

/** Кап на длину периода — страховка от кривых границ (роутер режет период на 366). */
const MAX_DAYS = 400;

const toDate = (s: string) => new Date(`${s}T00:00:00Z`);
const isoOf = (d: Date) => d.toISOString().slice(0, 10);

function addDays(d: Date, n: number): Date {
    const x = new Date(d);
    x.setUTCDate(x.getUTCDate() + n);
    return x;
}

/** Понедельник недели, в которую попал день: календарь строится с пн. */
const mondayOf = (d: Date) => addDays(d, -((d.getUTCDay() + 6) % 7));

/** Компактная метка «дд.мм» — из formatDate (ru-RU даёт «дд.мм.гггг»), не toLocaleDateString. */
const ddmm = (s: string) => formatDate(s).slice(0, 5);

function eachDay(start: string, end: string): string[] {
    const out: string[] = [];
    const last = toDate(end);
    let cur = toDate(start);
    if (Number.isNaN(cur.getTime()) || Number.isNaN(last.getTime())) return out;
    while (cur <= last && out.length < MAX_DAYS) {
        out.push(isoOf(cur));
        cur = addDays(cur, 1);
    }
    return out;
}

/* ─── Словари ───────────────────────────────────────────────────────────────── */

/** Русские имена типов поставки — как в эталоне. */
const TYPE_RU: Record<string, string> = {
    feat: 'фича',
    fix: 'починка',
    perf: 'ускорение',
    refactor: 'рефакторинг',
    test: 'тесты',
    chore: 'рутина',
    docs: 'документация',
};

const DOWS = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'];

const shipmentsWord = (n: number) => pluralRu(n, ['поставка', 'поставки', 'поставок']);

/** Подсказка дня. Без поставок — так и говорим, а не «0 поставок». */
function dayTitle(day: string, n: number, lines?: { added: number; deleted: number }): string {
    if (!n) return `${ddmm(day)}: без поставок`;
    const vol = lines ? `+${formatNumber(lines.added, 0)} / −${formatNumber(lines.deleted, 0)} строк, ` : '';
    return `${ddmm(day)}: ${vol}${formatNumber(n, 0)} ${shipmentsWord(n)}`;
}

/* ─── Ритм ──────────────────────────────────────────────────────────────────── */

function RhythmCard({ stats, perDay }: { stats: VibeStats; perDay: Map<string, VibeDayVolume> }) {
    const { rhythm } = stats;
    // ИНВАРИАНТ: окно ритма (14 дней до `until`) обязано лежать ВНУТРИ периода отчёта —
    // иначе by_day не покроет часть пилюль и пропуск нарисуется там, где поставка была.
    // Держится сам: период по умолчанию 30 дней, и страница не шлёт since/until.
    // Появится выбор периода короче 14 дней — строку окна брать с бэка, а не из by_day.
    const days = useMemo(() => eachDay(rhythm.start, rhythm.end), [rhythm.start, rhythm.end]);

    return (
        <div className="vibe-card">
            <h2 className="vibe-card-h">Ритм</h2>
            <div className="vibe-hero">
                <span className="vibe-fig">
                    {formatNumber(rhythm.hit, 0)}
                    <span className="vibe-of">/{formatNumber(rhythm.denom, 0)}</span>
                </span>
                <span className="vibe-cap">
                    {pluralRu(rhythm.denom, ['дня', 'дней', 'дней'])} с поставкой на прод
                    {' · '}{ddmm(rhythm.start)}—{ddmm(rhythm.end)}
                    {' · '}окно {formatNumber(rhythm.window, 0)}{' '}
                    {pluralRu(rhythm.window, ['день', 'дня', 'дней'])}
                </span>
            </div>
            {/* Полоска дней окна: пауза видна пропуском, а не обнулённым счётчиком */}
            <div className="vibe-rhythm">
                {days.map(day => {
                    const n = perDay.get(day)?.shipments ?? 0;
                    return <span key={day} className={`vibe-pip${n ? ' on' : ''}`} title={dayTitle(day, n)} />;
                })}
            </div>
        </div>
    );
}

/* ─── Календарь поставок ────────────────────────────────────────────────────── */

/** Ступень рампы по числу поставок — как в эталоне: 0 → пусто, дальше по парам. */
const tierOf = (n: number) => (n === 0 ? 0 : Math.min(4, Math.floor((n + 1) / 2)));

function CalendarCard({ stats, perDay }: { stats: VibeStats; perDay: Map<string, VibeDayVolume> }) {
    const weeks = useMemo(() => {
        const first = toDate(stats.since);
        const last = toDate(stats.until);
        if (Number.isNaN(first.getTime()) || Number.isNaN(last.getTime())) return [];
        const out: { key: string; days: (string | null)[] }[] = [];
        let week = mondayOf(first);
        while (week <= last && out.length < 60) {
            const cells = Array.from({ length: 7 }, (_, i) => {
                const day = addDays(week, i);
                // День вне периода — не «ноль поставок», а отсутствие дня: пустая клетка
                return day < first || day > last ? null : isoOf(day);
            });
            out.push({ key: isoOf(week), days: cells });
            week = addDays(week, 7);
        }
        return out;
    }, [stats.since, stats.until]);

    return (
        <div className="vibe-card">
            <h2 className="vibe-card-h">Календарь поставок</h2>
            <div className="vibe-dows">
                <span />
                {DOWS.map(d => <span key={d} className="vibe-dow">{d}</span>)}
            </div>
            <div className="vibe-weeks">
                {weeks.map(w => (
                    <div key={w.key} className="vibe-week">
                        <span className="vibe-wlab">{ddmm(w.key)}</span>
                        {w.days.map((day, i) => {
                            if (day === null) return <span key={i} className="vibe-cell void" />;
                            const n = perDay.get(day)?.shipments ?? 0;
                            const tier = tierOf(n);
                            return (
                                <span key={day} className={`vibe-cell${tier ? ` n${tier}` : ''}`}
                                    title={dayTitle(day, n)}>
                                    {n || ''}
                                </span>
                            );
                        })}
                    </div>
                ))}
            </div>
            <div className="vibe-legend">
                <span>меньше</span>
                <i className="s0" /><i className="s2" /><i className="s3" /><i className="s4" /><i className="s5" />
                <span>больше</span>
            </div>
        </div>
    );
}

/* ─── Объём по дням ─────────────────────────────────────────────────────────── */

/** Сколько столбцов ещё терпят подпись над каждым. Больше — подписи прячем:
 *  они не влезают (nowrap + min-width:auto не дают колонке сжаться, график вылезал
 *  за карточку), да и число над каждым столбцом — анти-паттерн. Значения — в тултипе. */
const DENSE_COLS = 12;

function ByDayCard({ stats }: { stats: VibeStats }) {
    const peak = Math.max(...stats.by_day.map(d => d.added), 1);
    const dense = stats.by_day.length > DENSE_COLS;

    return (
        <div className="vibe-card">
            <h2 className="vibe-card-h">Объём по дням</h2>
            <div className={dense ? 'vibe-cols vibe-cols-dense' : 'vibe-cols'}>
                {stats.by_day.map(d => (
                    <div key={d.day} className="vibe-col" title={dayTitle(d.day, d.shipments, d)}>
                        <span className="vibe-cv">{d.added ? `+${formatNumber(d.added, 0)}` : ''}</span>
                        {/* min-height у .vibe-cbar не даёт потеряться мелким значениям, но для НУЛЯ
                            рисовал бы столбик там, где работы не было: день без поставок — пустое место */}
                        {d.added > 0 && (
                            <span className="vibe-cbar" style={{ height: `${(100 * d.added) / peak}%` }} />
                        )}
                        <span className="vibe-cd">{ddmm(d.day)}</span>
                    </div>
                ))}
            </div>
        </div>
    );
}

/* ─── Масштаб ───────────────────────────────────────────────────────────────── */

function ScaleCard({ stats }: { stats: VibeStats }) {
    const { scale } = stats;
    const totals: { label: string; value: number }[] = [
        { label: 'файлов затронуто', value: scale.files },
        { label: 'создано с нуля', value: scale.new_files },
        { label: 'React-компонентов', value: scale.components },
        { label: 'миграций БД', value: scale.migrations },
        {
            label: pluralRu(scale.sections, ['раздел продукта', 'раздела продукта', 'разделов продукта']),
            value: scale.sections,
        },
    ];

    // Бары по СТРОКАМ (объём), файлы — в подпись: 9 миграций и 54 компонента несопоставимы
    // по счётчику файлов, а по объёму видно, где на самом деле работа.
    const ranked = useMemo(
        () => [...scale.by_area].sort((a, b) => (b.added + b.deleted) - (a.added + a.deleted)),
        [scale.by_area],
    );
    const top = Math.max(...ranked.map(a => a.added + a.deleted), 1);

    return (
        <div className="vibe-card">
            <h2 className="vibe-card-h">Масштаб</h2>
            <div className="vibe-totals">
                {totals.map(t => (
                    <div key={t.label}>
                        <div className="vibe-tv">{formatNumber(t.value, 0)}</div>
                        <div className="vibe-tl">{t.label}</div>
                    </div>
                ))}
            </div>
            <div className="vibe-bars">
                {ranked.map(a => (
                    <div key={a.area} className="vibe-bar">
                        <span className="vibe-bar-nm">
                            {a.area}<br />
                            <span className="vibe-bar-sub2">
                                {formatNumber(a.files, 0)} {pluralRu(a.files, ['файл', 'файла', 'файлов'])}
                            </span>
                        </span>
                        <span className="vibe-bar-track">
                            <span className="vibe-bar-fill"
                                style={{ width: `${(100 * (a.added + a.deleted)) / top}%` }} />
                            <span className="vibe-bar-val">
                                +{formatNumber(a.added, 0)} / −{formatNumber(a.deleted, 0)}
                            </span>
                        </span>
                    </div>
                ))}
            </div>
        </div>
    );
}

/* ─── Поставки по разделам ──────────────────────────────────────────────────── */

/** Сколько разделов показывать поимённо. Хвост сворачиваем в «Прочее»: у активного
 *  вайбкодера их под сорок (gazelka, api, cache, box-multiplicity…), и полный список
 *  сырых слагов — стена текста вместо графика. Больше 7–8 категорий читаются как шум. */
const TOP_SECTIONS = 8;

function BySectionCard({ stats }: { stats: VibeStats }) {
    const rows = useMemo(() => {
        const sorted = [...stats.by_section].sort((a, b) => b.count - a.count);
        if (sorted.length <= TOP_SECTIONS + 1) return sorted;
        const head = sorted.slice(0, TOP_SECTIONS);
        const tail = sorted.slice(TOP_SECTIONS);
        const rest = tail.reduce((sum, s) => sum + s.count, 0);
        return [...head, { section: `Прочее (${tail.length})`, count: rest }];
    }, [stats.by_section]);

    const top = Math.max(...rows.map(s => s.count), 1);

    return (
        <div className="vibe-card">
            <h2 className="vibe-card-h">Поставки по разделам</h2>
            <div className="vibe-bars">
                {rows.map(s => (
                    <div key={s.section} className="vibe-bar">
                        <span className="vibe-bar-nm">{s.section}</span>
                        <span className="vibe-bar-track">
                            <span className="vibe-bar-fill" style={{ width: `${(100 * s.count) / top}%` }} />
                            <span className="vibe-bar-val">{formatNumber(s.count, 0)}</span>
                        </span>
                    </div>
                ))}
            </div>
        </div>
    );
}

/* ─── Лента поставок ────────────────────────────────────────────────────────── */

// Выгрузка несёт больше ленты: в Excel идут полный sha и объём — по ним ищут коммит и
// считают, а на экране они бы забили таблицу.
const FEED_EXPORT: ExcelExportColumn[] = [
    { key: 'day', label: 'Дата' },
    { key: 'ctype', label: 'Тип', getValue: (r: VibeShipment) => TYPE_RU[r.ctype] ?? r.ctype },
    { key: 'title', label: 'Что уехало' },
    { key: 'section', label: 'Раздел' },
    { key: 'sha', label: 'Коммит' },
    { key: 'added', label: 'Строк добавлено' },
    { key: 'deleted', label: 'Строк удалено' },
    { key: 'files', label: 'Файлов' },
    { key: 'is_product', label: 'Продуктовая', getValue: (r: VibeShipment) => (r.is_product ? 'да' : 'нет') },
];

function FeedCard({ shipments }: { shipments: VibeShipment[] }) {
    return (
        <div className="vibe-card">
            <div className="vibe-card-head">
                <h2 className="vibe-card-h">Лента поставок</h2>
                <button type="button" className="vibe-btn"
                    onClick={() => exportToExcel(shipments, 'vibecoding_shipments', FEED_EXPORT)}>
                    Выгрузить в Excel
                </button>
            </div>
            <table className="vibe-table">
                <thead>
                    <tr>
                        <th>Дата</th><th>Тип</th><th>Что уехало</th><th>Раздел</th><th>Коммит</th>
                    </tr>
                </thead>
                <tbody>
                    {shipments.map(s => (
                        <tr key={s.sha}>
                            <td className="d">{ddmm(s.day)}</td>
                            <td><span className="vibe-tag">{TYPE_RU[s.ctype] ?? s.ctype ?? '—'}</span></td>
                            <td className="t">{s.title}</td>
                            <td>{s.section}</td>
                            <td className="h" title={s.sha}>{s.short}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

/* ─── Страница ──────────────────────────────────────────────────────────────── */

export default function VibecodingPage() {
    const [stats, setStats] = useState<VibeStats | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    // 403 — не ошибка, а «вкладка не для вас»: у сервиса есть внешние пользователи
    // и клиенты-селлеры, для них это нормальный ответ, а не сбой.
    const [forbidden, setForbidden] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        setForbidden(false);
        try {
            const res = await api.getVibeStats();
            setStats(res);
        } catch (e) {
            if (isForbidden(e)) setForbidden(true);
            else setError(e instanceof Error ? e.message : 'Не удалось загрузить статистику');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const perDay = useMemo(
        () => new Map((stats?.by_day ?? []).map(d => [d.day, d])),
        [stats?.by_day],
    );

    const hasData = !!stats && stats.shipments_total > 0;

    return (
        <div className="vibe-root animate-in">
            <h1 className="vibe-h1">Вайбкодинг</h1>
            <p className="vibe-sub">
                {stats
                    ? `${stats.display_name} · ${formatDate(stats.since)} — ${formatDate(stats.until)} · поставка = коммит, доехавший до прода`
                    : 'Опись работы: что уехало на прод'}
            </p>

            {loading && <div className="vibe-card vibe-loading">Загрузка...</div>}

            {!loading && forbidden && (
                <div className="vibe-card vibe-empty">
                    <div className="vibe-empty-icon">🤖</div>
                    <div className="vibe-empty-title">Вкладка не для вас</div>
                    <p className="vibe-empty-text">
                        «Вайбкодинг» — внутренняя опись работы над самим DDS2. Она показывается только
                        тем, кто этот код пишет. С вашим аккаунтом здесь ничего не будет.
                    </p>
                </div>
            )}

            {!loading && !forbidden && error && (
                <div className="vibe-card vibe-empty">
                    <div className="vibe-empty-icon">⚠️</div>
                    <div className="vibe-empty-title">Не удалось загрузить статистику</div>
                    <p className="vibe-empty-text">{error}</p>
                    <button type="button" className="vibe-btn" onClick={load}>Повторить</button>
                </div>
            )}

            {!loading && !forbidden && !error && stats && !hasData && (
                <div className="vibe-card vibe-empty">
                    <div className="vibe-empty-icon">📭</div>
                    <div className="vibe-empty-title">За этот период поставок на прод нет</div>
                    <p className="vibe-empty-text">
                        Пусто — не ошибка: данные есть, поставок в периоде не было.
                        {stats.last_ingest && ` CI последний раз обновлял статистику ${formatDate(stats.last_ingest)}.`}
                    </p>
                </div>
            )}

            {!loading && !forbidden && !error && stats && hasData && (
                <>
                    <RhythmCard stats={stats} perDay={perDay} />

                    <div className="vibe-kpis">
                        <div className="vibe-kpi">
                            <div className="vibe-kpi-v">{formatNumber(stats.shipments_total, 0)}</div>
                            <div className="vibe-kpi-l">поставок на прод</div>
                            <div className="vibe-kpi-n">
                                из них продуктовых: {formatNumber(stats.shipments_product, 0)}
                            </div>
                        </div>
                        <div className="vibe-kpi">
                            <div className="vibe-kpi-v">+{formatNumber(stats.scale.added, 0)}</div>
                            <div className="vibe-kpi-l">строк добавлено</div>
                            <div className="vibe-kpi-n">−{formatNumber(stats.scale.deleted, 0)} удалено</div>
                        </div>
                    </div>

                    <CalendarCard stats={stats} perDay={perDay} />
                    <ByDayCard stats={stats} />
                    <ScaleCard stats={stats} />
                    {stats.by_section.length > 0 && <BySectionCard stats={stats} />}
                    {stats.shipments.length > 0 && <FeedCard shipments={stats.shipments} />}

                    <p className="vibe-foot">
                        Собрано из git: поставка = коммит, доехавший до прода.
                        {stats.last_ingest && ` CI последний раз обновлял данные ${formatDate(stats.last_ingest)}.`}
                    </p>
                </>
            )}
        </div>
    );
}
