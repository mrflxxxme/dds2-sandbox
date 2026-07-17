'use client';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { isForbidden } from '@/lib/api/vibe';
import { formatDate, formatNumber, pluralRu } from '@/lib/utils';
import PageHeader from '@/components/PageHeader';
import KpiCard from '@/components/KpiCard';
import TanStackDataTable from '@/components/TanStackDataTable';
import type { Column } from '@/components/DataTable';
import type { VibeStats, VibeShipment } from '@/types/api';

/** Дни периода включительно. Кап — страховка от кривых границ с бэка. */
const MAX_DAYS = 400;

function eachDay(start: string, end: string): string[] {
    const out: string[] = [];
    const cur = new Date(`${start}T00:00:00Z`);
    const last = new Date(`${end}T00:00:00Z`);
    if (Number.isNaN(cur.getTime()) || Number.isNaN(last.getTime())) return out;
    while (cur <= last && out.length < MAX_DAYS) {
        out.push(cur.toISOString().slice(0, 10));
        cur.setUTCDate(cur.getUTCDate() + 1);
    }
    return out;
}

/** Цвет типа поставки. Тип — из conventional commits (feat/fix/perf/...). */
const CTYPE_BADGE: Record<string, string> = {
    feat: 'badge-success',
    fix: 'badge-warning',
    perf: 'badge-info',
    refactor: 'badge-secondary',
    infra: 'badge-secondary',
    test: 'badge-secondary',
};

function CtypeBadge({ ctype }: { ctype: string }) {
    return <span className={`badge ${CTYPE_BADGE[ctype] ?? 'badge-secondary'}`}>{ctype}</span>;
}

/* ─── Ритм ──────────────────────────────────────────────────────────────────── */

function RhythmCard({ stats }: { stats: VibeStats }) {
    const { rhythm } = stats;
    // День закрашен, если в нём была хоть одна поставка. Пауза — пустая клетка:
    // окно к ней равнодушно, в отличие от стрика, который бы обнулился.
    //
    // ИНВАРИАНТ: окно ритма (14 дней до `until`) обязано лежать ВНУТРИ периода отчёта —
    // иначе by_day не покроет часть клеток и пропуск нарисуется там, где поставка была.
    // Держится сам: период по умолчанию 30 дней, и страница не шлёт since/until.
    // Появится выбор периода короче 14 дней — строку окна брать с бэка, а не из by_day.
    const shipped = useMemo(() => {
        const set = new Set<string>();
        for (const d of stats.by_day) if (d.shipments > 0) set.add(d.day);
        return set;
    }, [stats.by_day]);

    const days = useMemo(() => eachDay(rhythm.start, rhythm.end), [rhythm.start, rhythm.end]);

    return (
        <div className="glass-card">
            <div className="vibe-rhythm">
                <div>
                    <div className="vibe-card-title">Ритм</div>
                    <div className="vibe-rhythm-figure">
                        <span className="vibe-rhythm-hit">{formatNumber(rhythm.hit, 0)}</span>
                        <span className="vibe-rhythm-denom">/ {formatNumber(rhythm.denom, 0)}</span>
                    </div>
                    <div className="vibe-rhythm-caption">
                        {pluralRu(rhythm.hit, ['день', 'дня', 'дней'])} с поставкой на прод
                        {' · '}{formatDate(rhythm.start)} — {formatDate(rhythm.end)}
                        {' · '}окно {formatNumber(rhythm.window, 0)}{' '}
                        {pluralRu(rhythm.window, ['день', 'дня', 'дней'])}
                    </div>
                </div>

                <div className="vibe-rhythm-days">
                    <div className="vibe-days">
                        {days.map(day => {
                            const hit = shipped.has(day);
                            return (
                                <div
                                    key={day}
                                    className={`vibe-day ${hit ? 'hit' : ''}`}
                                    title={`${formatDate(day)} — ${hit ? 'поставка была' : 'без поставки'}`}
                                />
                            );
                        })}
                    </div>
                    <div className="vibe-legend">Закрашенный день — была поставка. Пропуск — пауза, ритм не сбрасывается.</div>
                </div>
            </div>
        </div>
    );
}

/* ─── Объём по дням ─────────────────────────────────────────────────────────── */

function ByDayCard({ stats }: { stats: VibeStats }) {
    const max = Math.max(...stats.by_day.map(d => d.added), 0);

    return (
        <div className="glass-card">
            <div className="vibe-card-title">Объём по дням</div>
            <div className="vibe-card-sub">Высота столбца — строк добавлено</div>
            <div className="vibe-days-chart">
                {stats.by_day.map(d => {
                    // День с нулём — ПУСТОЕ место. min-height нужен мелким значениям,
                    // но для нуля он рисовал бы работу, которой не было.
                    const pct = max > 0 && d.added > 0 ? Math.max((d.added / max) * 100, 2) : 0;
                    const tip = `${formatDate(d.day)}\n+${formatNumber(d.added, 0)} / −${formatNumber(d.deleted, 0)} строк\n${formatNumber(d.shipments, 0)} ${pluralRu(d.shipments, ['поставка', 'поставки', 'поставок'])}`;
                    return (
                        <div key={d.day} className="vibe-day-col" title={tip}>
                            {pct > 0 && <div className="vibe-day-bar" style={{ height: `${pct}%` }} />}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

/* ─── Масштаб ───────────────────────────────────────────────────────────────── */

function ScaleCard({ stats }: { stats: VibeStats }) {
    const { scale } = stats;
    const nums: { label: string; value: number }[] = [
        { label: 'файлов', value: scale.files },
        { label: 'новых файлов', value: scale.new_files },
        { label: 'компонентов', value: scale.components },
        { label: 'миграций', value: scale.migrations },
        { label: 'разделов', value: scale.sections },
    ];

    // Бар по строкам (добавлено + удалено) — это объём правки, а не «прирост».
    const lines = (a: { added: number; deleted: number }) => a.added + a.deleted;
    const max = Math.max(...scale.by_area.map(lines), 0);

    return (
        <div className="glass-card">
            <div className="vibe-card-title">Масштаб</div>
            <div className="vibe-card-sub">Сколько сделано — опись объёма</div>

            <div className="vibe-scale-nums">
                {nums.map(n => (
                    <div key={n.label} className="vibe-scale-num">
                        <div className="vibe-scale-num-value">{formatNumber(n.value, 0)}</div>
                        <div className="vibe-scale-num-label">{n.label}</div>
                    </div>
                ))}
            </div>

            {scale.by_area.length > 0 && (
                <div className="vibe-bars">
                    {scale.by_area.map(a => {
                        const total = lines(a);
                        const pct = max > 0 ? (total / max) * 100 : 0;
                        return (
                            <div key={a.area} className="vibe-bar-row">
                                <div>
                                    <div className="vibe-bar-name">{a.area}</div>
                                    <div className="vibe-bar-name-sub">
                                        {formatNumber(a.files, 0)} {pluralRu(a.files, ['файл', 'файла', 'файлов'])}
                                    </div>
                                </div>
                                <div className="vibe-bar-track"
                                    title={`${a.area}: +${formatNumber(a.added, 0)} / −${formatNumber(a.deleted, 0)} строк`}>
                                    <div className="vibe-bar-fill" style={{ width: `${pct}%` }} />
                                </div>
                                <div className="vibe-bar-value">{formatNumber(total, 0)}</div>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

/* ─── Поставки по разделам ──────────────────────────────────────────────────── */

function BySectionCard({ stats }: { stats: VibeStats }) {
    const max = Math.max(...stats.by_section.map(s => s.count), 0);

    return (
        <div className="glass-card">
            <div className="vibe-card-title">Поставки по разделам</div>
            {/* Бэкенд считает тут только продуктовые: раздел есть у того, что видит юзер */}
            <div className="vibe-card-sub">Куда уходила работа — продуктовые поставки</div>
            <div className="vibe-bars">
                {stats.by_section.map(s => (
                    <div key={s.section} className="vibe-bar-row">
                        <div className="vibe-bar-name" title={s.section}>{s.section}</div>
                        <div className="vibe-bar-track"
                            title={`${s.section}: ${formatNumber(s.count, 0)} ${pluralRu(s.count, ['поставка', 'поставки', 'поставок'])}`}>
                            <div className="vibe-bar-fill added" style={{ width: `${max > 0 ? (s.count / max) * 100 : 0}%` }} />
                        </div>
                        <div className="vibe-bar-value">{formatNumber(s.count, 0)}</div>
                    </div>
                ))}
            </div>
        </div>
    );
}

/* ─── Лента поставок ────────────────────────────────────────────────────────── */

const FEED_COLUMNS: Column[] = [
    { key: 'day', label: 'Дата', format: 'date', width: '110px' },
    { key: 'ctype', label: 'Тип', width: '90px', render: (v: unknown) => <CtypeBadge ctype={String(v)} /> },
    { key: 'title', label: 'Что уехало' },
    { key: 'section', label: 'Раздел', width: '200px' },
    {
        key: 'short', label: 'SHA', width: '100px',
        // Полный sha — в подсказке и в выгрузке: короткий годится глазу, но не для поиска коммита
        render: (v: unknown, row: VibeShipment) => <span className="vibe-feed-sha" title={row.sha}>{String(v)}</span>,
        exportValue: (row: VibeShipment) => row.sha,
    },
];

function FeedCard({ shipments }: { shipments: VibeShipment[] }) {
    return (
        <TanStackDataTable
            title="Лента поставок"
            columns={FEED_COLUMNS}
            data={shipments}
            exportName="vibecoding_shipments"
            emptyIcon="📭"
            emptyText="Поставок нет"
        />
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

    const subtitle = stats
        ? `${stats.display_name} · ${formatDate(stats.since)} — ${formatDate(stats.until)}`
        : 'Опись работы: что уехало на прод';

    return (
        <div className="animate-in">
            <PageHeader title="Вайбкодинг" icon="🤖" subtitle={subtitle} />

            {loading && (
                <div className="glass-card" style={{ padding: 40, color: 'var(--color-text-muted)' }}>
                    Загрузка...
                </div>
            )}

            {!loading && forbidden && (
                <div className="glass-card vibe-empty">
                    <div className="vibe-empty-icon">🤖</div>
                    <div className="vibe-empty-title">Вкладка не для вас</div>
                    <p className="vibe-empty-text">
                        «Вайбкодинг» — внутренняя опись работы над самим DDS2. Она показывается только
                        тем, кто этот код пишет. С вашим аккаунтом здесь ничего не будет.
                    </p>
                </div>
            )}

            {!loading && !forbidden && error && (
                <div className="glass-card vibe-empty">
                    <div className="vibe-empty-icon">⚠️</div>
                    <div className="vibe-empty-title">Не удалось загрузить статистику</div>
                    <p className="vibe-empty-text">{error}</p>
                    <button className="btn btn-secondary btn-sm" style={{ marginTop: 16 }} onClick={load}>
                        Повторить
                    </button>
                </div>
            )}

            {!loading && !forbidden && !error && stats && stats.shipments_total === 0 && (
                <div className="glass-card vibe-empty">
                    <div className="vibe-empty-icon">📭</div>
                    <div className="vibe-empty-title">За этот период поставок на прод нет</div>
                    <p className="vibe-empty-text">
                        Пусто — не ошибка: данные есть, поставок в периоде не было.
                        {stats.last_ingest && ` CI последний раз обновлял статистику ${formatDate(stats.last_ingest)}.`}
                    </p>
                </div>
            )}

            {!loading && !forbidden && !error && stats && stats.shipments_total > 0 && (
                <div className="vibe-stack">
                    <RhythmCard stats={stats} />

                    {/* value — СТРОКОЙ: KpiCard форматирует number с 2 знаками («3,00 поставки») */}
                    <div className="vibe-kpis">
                        <KpiCard
                            label="Поставок на прод"
                            value={formatNumber(stats.shipments_total, 0)}
                            sub={`из них продуктовых: ${formatNumber(stats.shipments_product, 0)}`}
                            icon="🚀"
                        />
                        <KpiCard
                            label="Строк добавлено"
                            value={formatNumber(stats.scale.added, 0)}
                            sub={`−${formatNumber(stats.scale.deleted, 0)} удалено`}
                            icon="📝"
                            color="#22c55e"
                        />
                    </div>

                    <ByDayCard stats={stats} />
                    <ScaleCard stats={stats} />
                    {stats.by_section.length > 0 && <BySectionCard stats={stats} />}
                    {stats.shipments.length > 0 && <FeedCard shipments={stats.shipments} />}
                </div>
            )}
        </div>
    );
}
