'use client';

/**
 * Страница «Приоритет складов WB» — карта скорости доставки в города РФ.
 *
 * Логика:
 *  1. Грузим карту приоритетов (meta + okrug-info) — статичный референс.
 *  2. Берём текущий портфель проекта (api.getStockNeed → warehouses[].name
 *     с total_need > 0 OR stock > 0) → это и есть «корзина».
 *  3. Шлём её в /warehouse/speed/evaluate → получаем «реалистичный потолок ИЛ»,
 *     warnings (например: «в корзине есть Тула — это воришка у ЦЧР»),
 *     per-ФО статистику.
 *
 *  Источник `loaded_warehouses` см. ТЗ — вариант 1 (через getStockNeed).
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import type {
    BasketEvaluation,
    BasketWarningSeverity,
    OkrugInfo,
    SpeedMeta,
} from '@/types/api';

/* ── helpers ── */

function pctColor(pct: number): string {
    if (pct >= 50) return 'var(--color-success)';
    if (pct >= 30) return 'var(--color-warning)';
    return 'var(--color-danger)';
}

function severityBg(severity: BasketWarningSeverity): string {
    if (severity === 'error') return 'rgba(255, 59, 48, 0.10)';
    if (severity === 'warning') return 'rgba(255, 159, 10, 0.10)';
    return 'rgba(0, 113, 227, 0.08)';
}

function severityBorder(severity: BasketWarningSeverity): string {
    if (severity === 'error') return 'var(--color-danger)';
    if (severity === 'warning') return 'var(--color-warning)';
    return 'var(--color-accent)';
}

function severityIcon(severity: BasketWarningSeverity): string {
    if (severity === 'error') return '!';
    if (severity === 'warning') return '!';
    return 'i';
}

/* ── small UI atoms ── */

function Chip({
    label,
    tone,
    title,
}: {
    label: string;
    tone?: 'neutral' | 'anchor' | 'stealer' | 'success' | 'muted';
    title?: string;
}) {
    const palette = {
        neutral: { bg: 'rgba(0,0,0,0.04)', fg: 'var(--color-text)', border: 'transparent' },
        anchor: { bg: 'rgba(52, 199, 89, 0.12)', fg: '#1f7a3a', border: 'rgba(52,199,89,0.35)' },
        stealer: { bg: 'rgba(255, 59, 48, 0.10)', fg: '#a4271e', border: 'rgba(255,59,48,0.35)' },
        success: { bg: 'rgba(52, 199, 89, 0.18)', fg: '#1f7a3a', border: 'rgba(52,199,89,0.45)' },
        muted: { bg: 'rgba(0,0,0,0.04)', fg: 'var(--color-text-muted)', border: 'transparent' },
    }[tone || 'neutral'];
    return (
        <span
            title={title}
            style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                padding: '2px 8px',
                borderRadius: 24,
                background: palette.bg,
                color: palette.fg,
                border: `1px solid ${palette.border}`,
                fontSize: 12,
                fontWeight: 500,
                whiteSpace: 'nowrap',
                lineHeight: '18px',
            }}
        >
            {label}
        </span>
    );
}

function ProgressBar({ value, max, color }: { value: number; max: number; color?: string }) {
    const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
    return (
        <div
            style={{
                width: '100%',
                height: 6,
                borderRadius: 4,
                background: 'rgba(0,0,0,0.06)',
                overflow: 'hidden',
            }}
        >
            <div
                style={{
                    width: `${pct}%`,
                    height: '100%',
                    background: color || 'var(--color-accent)',
                    transition: 'width 0.3s cubic-bezier(0.25, 1, 0.5, 1)',
                }}
            />
        </div>
    );
}

function KpiCardLite({
    label,
    value,
    sub,
    valueColor,
}: {
    label: string;
    value: string;
    sub?: string;
    valueColor?: string;
}) {
    return (
        <div className="glass-card" style={{ padding: 20 }}>
            <div
                style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: 'var(--color-text-muted)',
                    textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                    marginBottom: 8,
                }}
            >
                {label}
            </div>
            <div
                style={{
                    fontSize: 32,
                    fontWeight: 700,
                    letterSpacing: '-0.03em',
                    color: valueColor || 'var(--color-text)',
                    lineHeight: 1.1,
                }}
            >
                {value}
            </div>
            {sub && (
                <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 4 }}>
                    {sub}
                </div>
            )}
        </div>
    );
}

function SkeletonCard({ height = 96 }: { height?: number }) {
    return (
        <div
            className="glass-card"
            style={{
                height,
                background: 'linear-gradient(90deg, rgba(0,0,0,0.04) 0%, rgba(0,0,0,0.07) 50%, rgba(0,0,0,0.04) 100%)',
                backgroundSize: '200% 100%',
                animation: 'shimmer 1.4s linear infinite',
            }}
        />
    );
}

/* ── page ── */

export default function WarehouseSpeedPage() {
    const [meta, setMeta] = useState<SpeedMeta | null>(null);
    const [okrugInfo, setOkrugInfo] = useState<OkrugInfo[]>([]);
    const [basket, setBasket] = useState<BasketEvaluation | null>(null);
    const [loadedWarehouses, setLoadedWarehouses] = useState<string[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const loadData = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            // 1. Параллельно: meta, okrug-info, портфель проекта.
            const [metaRes, okrugRes, portfolioRes] = await Promise.all([
                api.warehouseSpeed.getMeta(),
                api.warehouseSpeed.getOkrugInfo(),
                api.getStockNeed(14, 14, 'actual', false, false).catch(() => null),
            ]);
            setMeta(metaRes);
            setOkrugInfo(okrugRes);

            // 2. Извлекаем имена складов из портфеля (warehouses[] из stock_need).
            //    Любой склад где есть потребность или остаток — считается «загруженным».
            let warehouseNames: string[] = [];
            if (portfolioRes && Array.isArray(portfolioRes.warehouses)) {
                const seen = new Set<string>();
                for (const wh of portfolioRes.warehouses) {
                    const name = (wh?.name || '').trim();
                    if (!name || seen.has(name)) continue;
                    const totalNeed = Number(wh?.total_need ?? 0);
                    // Любой WB-склад из портфеля. Backend сам отфильтрует
                    // неизвестные ему в speed-карте имена.
                    if (totalNeed > 0 || Object.keys(wh?.articles ?? {}).length > 0) {
                        warehouseNames.push(name);
                        seen.add(name);
                    }
                }
            }
            setLoadedWarehouses(warehouseNames);

            // 3. Если есть склады — оцениваем корзину.
            if (warehouseNames.length > 0) {
                const evalRes = await api.warehouseSpeed.evaluate(warehouseNames, false);
                setBasket(evalRes);
            } else {
                setBasket(null);
            }
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Ошибка загрузки');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        loadData();
    }, [loadData]);

    /* ── derived ── */

    const anchorsInBasketByOkrug = useMemo(() => {
        const m = new Map<string, Set<string>>();
        if (basket) {
            for (const o of basket.per_okrug) {
                m.set(o.okrug_key, new Set(o.anchors_in_basket));
            }
        }
        return m;
    }, [basket]);

    const stealersInBasketByOkrug = useMemo(() => {
        const m = new Map<string, Set<string>>();
        if (basket) {
            for (const o of basket.per_okrug) {
                m.set(o.okrug_key, new Set(o.stealers_in_basket));
            }
        }
        return m;
    }, [basket]);

    /** Для конкретного склада определяем, является ли он anchor / stealer и в каких ФО. */
    const classifyWarehouse = useCallback(
        (name: string): { anchorOkrugs: string[]; stealerOkrugs: string[] } => {
            const anchorOkrugs: string[] = [];
            const stealerOkrugs: string[] = [];
            for (const [okrug, set] of anchorsInBasketByOkrug.entries()) {
                if (set.has(name)) anchorOkrugs.push(okrug);
            }
            for (const [okrug, set] of stealersInBasketByOkrug.entries()) {
                if (set.has(name)) stealerOkrugs.push(okrug);
            }
            return { anchorOkrugs, stealerOkrugs };
        },
        [anchorsInBasketByOkrug, stealersInBasketByOkrug],
    );

    /* ── render ── */

    return (
        <div className="animate-in">
            {/* Шапка */}
            <div className="page-header" style={{ marginBottom: 24 }}>
                <div>
                    <h1>Приоритет складов WB</h1>
                    <div style={{ fontSize: 13, color: 'var(--color-text-muted)', marginTop: 4 }}>
                        {meta ? (
                            <>
                                Источник: {meta.source} · версия {meta.version} ·{' '}
                                {formatNumber(meta.cities_count, 0)} городов
                            </>
                        ) : loading ? (
                            'Загрузка карты приоритетов…'
                        ) : (
                            'Карта приоритетов недоступна'
                        )}
                    </div>
                </div>
                <button
                    type="button"
                    onClick={loadData}
                    disabled={loading}
                    className="btn btn-secondary btn-sm"
                >
                    {loading ? 'Обновляем…' : 'Обновить'}
                </button>
            </div>

            {/* Error */}
            {error && !loading && (
                <div
                    className="glass-card"
                    style={{ padding: 20, color: 'var(--color-danger)', marginBottom: 16 }}
                >
                    <div style={{ fontWeight: 600, marginBottom: 8 }}>Не удалось загрузить</div>
                    <div style={{ fontSize: 13, marginBottom: 12 }}>{error}</div>
                    <button type="button" onClick={loadData} className="btn btn-danger btn-sm">
                        Повторить
                    </button>
                </div>
            )}

            {/* Loading skeleton */}
            {loading && !error && (
                <>
                    <div className="stats-grid" style={{ marginBottom: 16 }}>
                        <SkeletonCard />
                        <SkeletonCard />
                        <SkeletonCard />
                        <SkeletonCard />
                    </div>
                    <SkeletonCard height={200} />
                </>
            )}

            {/* Empty state — нет складов в портфеле */}
            {!loading && !error && loadedWarehouses.length === 0 && (
                <div className="empty-state">
                    <div className="empty-state-icon">📋</div>
                    <div className="empty-state-text">
                        Не выбрано ни одного склада — данные о портфеле проекта не доступны
                    </div>
                    <div
                        style={{
                            fontSize: 13,
                            color: 'var(--color-text-muted)',
                            marginTop: 8,
                            maxWidth: 480,
                            textAlign: 'center',
                        }}
                    >
                        Заполните остатки или потребность в WB-складах на странице «Аналитика
                        остатков», чтобы увидеть реалистичный потолок индекса локализации.
                    </div>
                </div>
            )}

            {/* Main content */}
            {!loading && !error && basket && (
                <>
                    {/* KPI */}
                    <div className="stats-grid" style={{ marginBottom: 16 }}>
                        <KpiCardLite
                            label="Реалистичный потолок ИЛ"
                            value={`${formatNumber(basket.realistic_ceiling_pct, 1)}%`}
                            sub={`по ${formatNumber(basket.cities_total, 0)} городам`}
                            valueColor={pctColor(basket.realistic_ceiling_pct)}
                        />
                        <KpiCardLite
                            label="Локально доставится"
                            value={`${formatNumber(basket.cities_local, 0)} / ${formatNumber(basket.cities_total, 0)}`}
                            sub={
                                basket.cities_total > 0
                                    ? `${formatNumber(
                                          (basket.cities_local / basket.cities_total) * 100,
                                          1,
                                      )}% городов`
                                    : undefined
                            }
                            valueColor="var(--color-success)"
                        />
                        <KpiCardLite
                            label="Через воришек"
                            value={formatNumber(basket.cities_stealer, 0)}
                            sub={
                                basket.cities_total > 0
                                    ? `${formatNumber(
                                          (basket.cities_stealer / basket.cities_total) * 100,
                                          1,
                                      )}% — крадутся у соседних ФО`
                                    : 'городов покрыто чужим ФО'
                            }
                            valueColor="var(--color-warning)"
                        />
                        <KpiCardLite
                            label="Не покрыто"
                            value={formatNumber(basket.cities_uncovered, 0)}
                            sub={
                                basket.cities_total > 0
                                    ? `${formatNumber(
                                          (basket.cities_uncovered / basket.cities_total) * 100,
                                          1,
                                      )}% — нет ни одного склада`
                                    : undefined
                            }
                            valueColor={
                                basket.cities_uncovered > 0
                                    ? 'var(--color-danger)'
                                    : 'var(--color-text-muted)'
                            }
                        />
                    </div>

                    {/* Корзина (loaded warehouses) */}
                    <div className="glass-card" style={{ padding: 20, marginBottom: 16 }}>
                        <div
                            style={{
                                fontSize: 12,
                                fontWeight: 600,
                                color: 'var(--color-text-muted)',
                                textTransform: 'uppercase',
                                letterSpacing: '0.05em',
                                marginBottom: 12,
                            }}
                        >
                            Корзина проекта · {formatNumber(loadedWarehouses.length, 0)}{' '}
                            {loadedWarehouses.length === 1 ? 'склад' : 'складов'}
                        </div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                            {loadedWarehouses.map((name) => {
                                const { anchorOkrugs, stealerOkrugs } = classifyWarehouse(name);
                                const isAnchor = anchorOkrugs.length > 0;
                                const isStealer = stealerOkrugs.length > 0;
                                let tone: 'anchor' | 'stealer' | 'neutral' = 'neutral';
                                if (isAnchor && !isStealer) tone = 'anchor';
                                else if (isStealer) tone = 'stealer';
                                const labelParts: string[] = [name];
                                if (isAnchor) labelParts.push('⚓');
                                if (isStealer) labelParts.push('🐭');
                                const titleParts: string[] = [];
                                if (isAnchor)
                                    titleParts.push(`якорь для: ${anchorOkrugs.join(', ')}`);
                                if (isStealer)
                                    titleParts.push(
                                        `крадёт у: ${stealerOkrugs.join(', ')}`,
                                    );
                                return (
                                    <Chip
                                        key={name}
                                        tone={tone}
                                        label={labelParts.join(' ')}
                                        title={titleParts.join(' · ') || undefined}
                                    />
                                );
                            })}
                        </div>
                    </div>

                    {/* Warnings */}
                    {basket.warnings.length > 0 && (
                        <div style={{ marginBottom: 16, display: 'flex', flexDirection: 'column', gap: 8 }}>
                            {basket.warnings.map((w, idx) => (
                                <div
                                    key={`${w.kind}-${w.okrug}-${idx}`}
                                    style={{
                                        padding: 16,
                                        borderRadius: 12,
                                        background: severityBg(w.severity),
                                        borderLeft: `3px solid ${severityBorder(w.severity)}`,
                                        display: 'flex',
                                        gap: 12,
                                        alignItems: 'flex-start',
                                    }}
                                >
                                    <div
                                        style={{
                                            width: 24,
                                            height: 24,
                                            borderRadius: '50%',
                                            background: severityBorder(w.severity),
                                            color: '#fff',
                                            display: 'flex',
                                            alignItems: 'center',
                                            justifyContent: 'center',
                                            fontWeight: 700,
                                            fontSize: 14,
                                            flexShrink: 0,
                                        }}
                                    >
                                        {severityIcon(w.severity)}
                                    </div>
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                        <div
                                            style={{
                                                fontSize: 14,
                                                fontWeight: 600,
                                                marginBottom: 4,
                                            }}
                                        >
                                            [{w.okrug_label}] {w.message}
                                        </div>
                                        {w.suggested_anchors.length > 0 && (
                                            <div
                                                style={{
                                                    display: 'flex',
                                                    flexWrap: 'wrap',
                                                    gap: 6,
                                                    marginTop: 8,
                                                    alignItems: 'center',
                                                }}
                                            >
                                                <span
                                                    style={{
                                                        fontSize: 12,
                                                        color: 'var(--color-text-muted)',
                                                    }}
                                                >
                                                    Рекомендуем добавить:
                                                </span>
                                                {w.suggested_anchors.map((anchor) => (
                                                    <Chip
                                                        key={anchor}
                                                        tone="success"
                                                        label={anchor}
                                                    />
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Таблица по округам */}
                    <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
                        <div
                            style={{
                                padding: '16px 20px',
                                borderBottom: '1px solid var(--color-border)',
                                fontSize: 12,
                                fontWeight: 600,
                                color: 'var(--color-text-muted)',
                                textTransform: 'uppercase',
                                letterSpacing: '0.05em',
                            }}
                        >
                            По округам
                        </div>
                        <div style={{ overflowX: 'auto' }}>
                            <table
                                style={{
                                    width: '100%',
                                    borderCollapse: 'collapse',
                                    fontSize: 14,
                                }}
                            >
                                <thead>
                                    <tr style={{ background: 'rgba(0,0,0,0.02)' }}>
                                        <th style={thStyle}>Округ</th>
                                        <th style={thStyle}>Якоря (топ-3)</th>
                                        <th style={thStyle}>Воришки (топ-3)</th>
                                        <th style={{ ...thStyle, width: 220 }}>Покрытие локально</th>
                                        <th style={thStyle}>Загружены в корзину</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {okrugInfo.map((okrug) => {
                                        const stats = basket.per_okrug.find(
                                            (p) => p.okrug_key === okrug.okrug_key,
                                        );
                                        const anchorsInBasket = new Set(
                                            stats?.anchors_in_basket ?? [],
                                        );
                                        const stealersInBasket = new Set(
                                            stats?.stealers_in_basket ?? [],
                                        );
                                        const citiesCovered = stats?.cities_covered_locally ?? 0;
                                        const citiesTotal =
                                            stats?.cities_total ??
                                            okrug.anchors_top.reduce(
                                                (s, a) => s + a.cities_count,
                                                0,
                                            );
                                        const coveragePct =
                                            citiesTotal > 0
                                                ? (citiesCovered / citiesTotal) * 100
                                                : 0;
                                        const inBasketList = [
                                            ...anchorsInBasket,
                                            ...stealersInBasket,
                                        ];
                                        return (
                                            <tr
                                                key={okrug.okrug_key}
                                                style={{
                                                    borderTop: '1px solid var(--color-border)',
                                                }}
                                            >
                                                <td style={tdStyle}>
                                                    <div style={{ fontWeight: 600 }}>
                                                        {okrug.okrug_label}
                                                    </div>
                                                    <div
                                                        style={{
                                                            fontSize: 12,
                                                            color: 'var(--color-text-muted)',
                                                        }}
                                                    >
                                                        {formatNumber(citiesTotal, 0)} городов
                                                    </div>
                                                </td>
                                                <td style={tdStyle}>
                                                    <div
                                                        style={{
                                                            display: 'flex',
                                                            flexWrap: 'wrap',
                                                            gap: 4,
                                                        }}
                                                    >
                                                        {okrug.anchors_top.slice(0, 3).map((a) => (
                                                            <Chip
                                                                key={a.warehouse_name}
                                                                tone={
                                                                    anchorsInBasket.has(
                                                                        a.warehouse_name,
                                                                    )
                                                                        ? 'anchor'
                                                                        : 'muted'
                                                                }
                                                                label={`${a.warehouse_name} (${a.cities_count})`}
                                                                title={`${a.cities_count} городов`}
                                                            />
                                                        ))}
                                                        {okrug.anchors_top.length === 0 && (
                                                            <span
                                                                style={{
                                                                    color: 'var(--color-text-dim)',
                                                                    fontSize: 12,
                                                                }}
                                                            >
                                                                —
                                                            </span>
                                                        )}
                                                    </div>
                                                </td>
                                                <td style={tdStyle}>
                                                    <div
                                                        style={{
                                                            display: 'flex',
                                                            flexWrap: 'wrap',
                                                            gap: 4,
                                                        }}
                                                    >
                                                        {okrug.stealers_top.slice(0, 3).map((s) => (
                                                            <Chip
                                                                key={s.warehouse_name}
                                                                tone={
                                                                    stealersInBasket.has(
                                                                        s.warehouse_name,
                                                                    )
                                                                        ? 'stealer'
                                                                        : 'muted'
                                                                }
                                                                label={`${s.warehouse_name} (${s.cities_count})`}
                                                                title={`${s.cities_count} городов`}
                                                            />
                                                        ))}
                                                        {okrug.stealers_top.length === 0 && (
                                                            <span
                                                                style={{
                                                                    color: 'var(--color-text-dim)',
                                                                    fontSize: 12,
                                                                }}
                                                            >
                                                                —
                                                            </span>
                                                        )}
                                                    </div>
                                                </td>
                                                <td style={tdStyle}>
                                                    <div
                                                        style={{
                                                            fontSize: 13,
                                                            marginBottom: 6,
                                                        }}
                                                    >
                                                        {formatNumber(citiesCovered, 0)} /{' '}
                                                        {formatNumber(citiesTotal, 0)}{' '}
                                                        <span
                                                            style={{
                                                                color: 'var(--color-text-muted)',
                                                                fontSize: 12,
                                                            }}
                                                        >
                                                            ({formatNumber(coveragePct, 0)}%)
                                                        </span>
                                                    </div>
                                                    <ProgressBar
                                                        value={citiesCovered}
                                                        max={citiesTotal}
                                                        color={pctColor(coveragePct)}
                                                    />
                                                </td>
                                                <td style={tdStyle}>
                                                    <div
                                                        style={{
                                                            display: 'flex',
                                                            flexWrap: 'wrap',
                                                            gap: 4,
                                                        }}
                                                    >
                                                        {inBasketList.length === 0 ? (
                                                            <span
                                                                style={{
                                                                    color: 'var(--color-text-dim)',
                                                                    fontSize: 12,
                                                                }}
                                                            >
                                                                нет
                                                            </span>
                                                        ) : (
                                                            inBasketList.map((name) => (
                                                                <Chip
                                                                    key={name}
                                                                    tone={
                                                                        anchorsInBasket.has(name)
                                                                            ? 'anchor'
                                                                            : 'stealer'
                                                                    }
                                                                    label={
                                                                        anchorsInBasket.has(name)
                                                                            ? `${name} ⚓`
                                                                            : `${name} 🐭`
                                                                    }
                                                                />
                                                            ))
                                                        )}
                                                    </div>
                                                </td>
                                            </tr>
                                        );
                                    })}
                                    {okrugInfo.length === 0 && (
                                        <tr>
                                            <td colSpan={5} style={{ ...tdStyle, textAlign: 'center', color: 'var(--color-text-muted)' }}>
                                                Нет данных по округам
                                            </td>
                                        </tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    {/* Footnote */}
                    <div
                        style={{
                            marginTop: 16,
                            fontSize: 12,
                            color: 'var(--color-text-muted)',
                            display: 'flex',
                            flexWrap: 'wrap',
                            gap: 16,
                        }}
                    >
                        <div>
                            <span style={{ marginRight: 4 }}>⚓</span> якорь — основной WB-склад
                            округа
                        </div>
                        <div>
                            <span style={{ marginRight: 4 }}>🐭</span> воришка — стягивает города
                            у соседнего ФО, замедляет доставку
                        </div>
                    </div>

                </>
            )}

            <style jsx>{`
                @keyframes shimmer {
                    0% {
                        background-position: 200% 0;
                    }
                    100% {
                        background-position: -200% 0;
                    }
                }
            `}</style>
        </div>
    );
}

/* ── inline styles ── */

const thStyle: React.CSSProperties = {
    padding: '12px 16px',
    textAlign: 'left',
    fontSize: 12,
    fontWeight: 600,
    color: 'var(--color-text-muted)',
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
    whiteSpace: 'nowrap',
};

const tdStyle: React.CSSProperties = {
    padding: '12px 16px',
    verticalAlign: 'top',
    fontSize: 14,
};
