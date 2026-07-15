'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import PageHeader from '@/components/PageHeader';
import PageGuard from '@/components/PageGuard';
import type { AdsManagerCampaign } from '@/types/api';

/** Форма нового теста: артикул → кампания (из списка, с фильтром по артикулу) → параметры.
 *  После создания черновика фото загружаются на странице теста. */
export default function AbTestCreatePage() {
    const params = useParams<{ slug: string }>();
    const router = useRouter();

    const [campaigns, setCampaigns] = useState<AdsManagerCampaign[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const [nmId, setNmId] = useState('');
    const [campaignId, setCampaignId] = useState<number | null>(null);
    const [viewsPerRound, setViewsPerRound] = useState(5000);
    const [roundMinutes, setRoundMinutes] = useState(45);
    const [targetViews, setTargetViews] = useState(10000);
    const [maxDays, setMaxDays] = useState(7);
    const [comment, setComment] = useState('');
    const [submitting, setSubmitting] = useState(false);
    const [submitError, setSubmitError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            setCampaigns(await api.getAdCampaignsList());
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Не удалось загрузить кампании');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void load();
    }, [load]);

    const nm = Number(nmId);
    const matching = useMemo(() => {
        if (!nm) return [];
        return campaigns.filter((c) => (c.nm_ids || []).includes(nm));
    }, [campaigns, nm]);

    const selected = campaigns.find((c) => c.campaign_id === campaignId) ?? null;

    const submit = useCallback(async () => {
        if (!nm || !campaignId) return;
        setSubmitting(true);
        setSubmitError(null);
        try {
            const res = await api.createAbTest({
                nm_id: nm,
                campaign_id: campaignId,
                comment: comment || null,
                views_per_round: viewsPerRound,
                round_minutes: roundMinutes,
                target_views: targetViews,
                max_days: maxDays,
            });
            router.push(`/p/${params.slug}/ab-tests/${res.id}`);
        } catch (e) {
            setSubmitError(e instanceof Error ? e.message : 'Не удалось создать тест');
            setSubmitting(false);
        }
    }, [nm, campaignId, comment, viewsPerRound, roundMinutes, targetViews, maxDays, params.slug, router]);

    const labelStyle = { display: 'block', fontSize: 13, color: 'var(--color-muted)', marginBottom: 6 } as const;
    const inputStyle = {
        width: '100%', padding: '10px 12px', borderRadius: 12,
        border: '1px solid var(--color-border)', background: 'transparent', color: 'var(--color-text)', fontSize: 14,
    } as const;

    return (
        <PageGuard page="ab-tests">
            <PageHeader title="🧪 Новый АБ-тест фото" subtitle="Артикул → рекламная кампания → параметры. Фото загрузите на следующем шаге." />

            <div className="glass-card" style={{ marginBottom: 16, background: 'var(--color-bg-card)', borderLeft: '3px solid var(--color-warning)' }}>
                <div style={{ fontSize: 13, color: 'var(--color-muted)', lineHeight: 1.6 }}>
                    ⚠️ Не рекомендуем запускать тест на <b>автоматических кампаниях (АРК)</b> — WB отдаёт по ним
                    некорректную статистику, и на кампаниях с зонами <b>«Поиск» и «Полки» одновременно</b> —
                    CTR по таким тестам сравнивать нельзя. Лучший вариант — отдельная аукционная кампания только
                    с этим артикулом.
                </div>
            </div>

            {loading && <div className="glass-card" style={{ textAlign: 'center', color: 'var(--color-muted)' }}>Загрузка кампаний…</div>}
            {error && !loading && (
                <div className="glass-card" style={{ color: 'var(--color-danger)' }}>
                    {error} <button className="btn btn-sm btn-secondary" onClick={() => void load()}>Повторить</button>
                </div>
            )}

            {!loading && !error && (
                <div className="glass-card" style={{ maxWidth: 640, display: 'flex', flexDirection: 'column', gap: 16 }}>
                    <div>
                        <label style={labelStyle}>Артикул WB (nmID)</label>
                        <input
                            style={inputStyle}
                            value={nmId}
                            onChange={(e) => { setNmId(e.target.value.replace(/\D/g, '')); setCampaignId(null); }}
                            placeholder="Например, 123456789"
                            inputMode="numeric"
                        />
                    </div>

                    <div>
                        <label style={labelStyle}>
                            Рекламная кампания {nm ? `(с этим артикулом: ${matching.length})` : ''}
                        </label>
                        <select
                            style={inputStyle}
                            value={campaignId ?? ''}
                            onChange={(e) => setCampaignId(e.target.value ? Number(e.target.value) : null)}
                            disabled={!nm}
                        >
                            <option value="">— выберите кампанию —</option>
                            {(matching.length ? matching : campaigns).map((c) => (
                                <option key={c.campaign_id} value={c.campaign_id}>
                                    {c.campaign_id} · {c.name || 'Без названия'} · {c.status_label}
                                    {c.advert_type === 8 ? ' · ⚠️ Авто' : c.advert_type === 9 ? ' · Аукцион' : ''}
                                </option>
                            ))}
                        </select>
                        <div style={{ fontSize: 12, color: 'var(--color-dim)', marginTop: 4 }}>
                            Лучше всего — аукционная кампания в «Поиске» только с этим артикулом.
                            Авто (АРК) не рекомендуется: WB отдаёт по ним некорректную статистику.
                        </div>
                        {selected?.advert_type === 8 && (
                            <div style={{ fontSize: 12, color: 'var(--color-danger)', marginTop: 6 }}>
                                ⚠️ Выбрана автоматическая кампания — CTR по такому тесту будет ненадёжным.
                                Если есть возможность, возьмите аукционную в «Поиске».
                            </div>
                        )}
                        {nm > 0 && matching.length === 0 && campaigns.length > 0 && (
                            <div style={{ fontSize: 12, color: 'var(--color-warning)', marginTop: 6 }}>
                                Кампаний с этим артикулом не нашли — показан весь список. Проверьте артикул или создайте
                                кампанию в «Управлении рекламой».
                            </div>
                        )}
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                        <div>
                            <label style={labelStyle}>Время круга, мин</label>
                            <input style={inputStyle} type="number" min={15} value={roundMinutes}
                                   onChange={(e) => setRoundMinutes(Number(e.target.value))} />
                            <div style={{ fontSize: 12, color: 'var(--color-dim)', marginTop: 4 }}>
                                Фото гарантированно сменится через это время. Рекомендуем 45–60: статистика WB и CDN
                                обновляются с лагом — короткие круги «смазывают» края
                            </div>
                        </div>
                        <div>
                            <label style={labelStyle}>Показов на круг (досрочная смена)</label>
                            <input style={inputStyle} type="number" min={100} value={viewsPerRound}
                                   onChange={(e) => setViewsPerRound(Number(e.target.value))} />
                            <div style={{ fontSize: 12, color: 'var(--color-dim)', marginTop: 4 }}>
                                Если показы наберутся раньше времени круга — фото сменится досрочно
                            </div>
                        </div>
                        <div>
                            <label style={labelStyle}>Показов на фото (цель)</label>
                            <input style={inputStyle} type="number" min={1000} step={1000} value={targetViews}
                                   onChange={(e) => setTargetViews(Number(e.target.value))} />
                            <div style={{ fontSize: 12, color: 'var(--color-dim)', marginTop: 4 }}>
                                Тест завершится, когда каждое фото наберёт цель
                            </div>
                        </div>
                        <div>
                            <label style={labelStyle}>Максимум дней</label>
                            <input style={inputStyle} type="number" min={1} max={30} value={maxDays}
                                   onChange={(e) => setMaxDays(Number(e.target.value))} />
                            <div style={{ fontSize: 12, color: 'var(--color-dim)', marginTop: 4 }}>
                                Предохранитель: завершить, даже если цель не набрана
                            </div>
                        </div>
                    </div>

                    <div>
                        <label style={labelStyle}>Комментарий (необязательно)</label>
                        <input style={inputStyle} value={comment} onChange={(e) => setComment(e.target.value)}
                               placeholder="Что тестируем, гипотеза…" />
                    </div>

                    {selected && (
                        <div style={{ fontSize: 13, color: 'var(--color-muted)' }}>
                            Круг: {roundMinutes} мин (досрочно при {formatNumber(viewsPerRound, 0)} показах) ·
                            цель {formatNumber(targetViews, 0)} на фото · до {maxDays} дн.
                        </div>
                    )}

                    {submitError && <div style={{ color: 'var(--color-danger)', fontSize: 13 }}>{submitError}</div>}

                    <div style={{ display: 'flex', gap: 12 }}>
                        <button className="btn btn-secondary" onClick={() => router.push(`/p/${params.slug}/ab-tests`)}>
                            Отмена
                        </button>
                        <button className="btn btn-primary" disabled={!nm || !campaignId || submitting} onClick={() => void submit()}>
                            {submitting ? 'Создаём…' : 'Создать черновик → загрузить фото'}
                        </button>
                    </div>
                </div>
            )}
        </PageGuard>
    );
}
