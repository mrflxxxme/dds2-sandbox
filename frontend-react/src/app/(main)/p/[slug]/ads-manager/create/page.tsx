'use client';
import React, { useState, useEffect } from 'react';
import Link from 'next/link';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import PageGuard from '@/components/PageGuard';
import type { AdsAutopaySetting } from '@/types/api';
import { IcX } from '../components/icons';
import { DEFAULT_AUTOPAY, buildAutoCampaignName } from '../components/adsShared';
import AutopaySettingsModal from '../components/AutopaySettingsModal';
import AddProductsModal from '../components/AddProductsModal';
import BulkCreate from '../components/BulkCreate';
import EditableName from '../components/EditableName';
import Switch from '../components/Switch';
import { useToast } from '../components/Toasts';

const MAX_NMS = 50;
const MIN_BUDGET = 1000;

type Payment = 'cpc' | 'cpm';
type Bid = 'unified' | 'manual';

function defaultName(): string {
    const d = new Date();
    const p = (n: number) => String(n).padStart(2, '0');
    return `Кампания от ${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()}`;
}

/** Раздел создания рекламной кампании (по кабинету WB Продвижение).
 *  Один вызов save-ad создаёт кампанию; бюджет и автопополнение применяются следом. */
export default function CreateCampaignPage() {
    const routeParams = useParams();
    const router = useRouter();
    const toast = useToast();
    const sp = useSearchParams();
    const slug = typeof routeParams?.slug === 'string' ? routeParams.slug : Array.isArray(routeParams?.slug) ? routeParams.slug[0] : '';
    const bulk = sp.get('bulk') === '1';

    // Настройки
    const [name, setName] = useState(defaultName());
    // Имя правил вручную → авто-предзаполнение больше не перезаписывает его
    const [nameEdited, setNameEdited] = useState(false);
    // Каталог артикулов: nm → артикул продавца (для авто-имени «nmID артикул ТИП»)
    const [vendorByNm, setVendorByNm] = useState<Map<number, string>>(() => new Map());
    const [payment, setPayment] = useState<Payment>('cpm');
    const [bidType, setBidType] = useState<Bid>('unified');
    const [zones, setZones] = useState<Set<string>>(() => new Set(['search', 'recommendations']));
    // Ставки по зонам, ₽ (только ручная CPM) — применяются к карточкам после создания
    const [bidSearch, setBidSearch] = useState('150');
    const [bidRec, setBidRec] = useState('150');
    const [budget, setBudget] = useState('3000');
    const [autopay, setAutopay] = useState<AdsAutopaySetting | null>(null);
    const [autopayOpen, setAutopayOpen] = useState(false);

    // Товары
    const [pickerOpen, setPickerOpen] = useState(false);
    const [selected, setSelected] = useState<Map<number, string>>(() => new Map());  // nm → title

    // Предзаполнение из URL (клик по товару в разделах «Высокий ДРР» / «Не работает реклама»):
    // ?nm= — артикул; ?payment=cpm|cpc, ?bid=unified|manual, ?zones=search,recommendations — тип/зоны.
    // Название товара тянем из каталога; если не нашли — показываем nm_id.
    useEffect(() => {
        const p = sp.get('payment'); const b = sp.get('bid');
        const pay = p === 'cpm' || p === 'cpc' ? p : null;
        const bd = b === 'unified' || b === 'manual' ? b : null;
        if (pay) setPayment(pay);
        if (bd) setBidType(bd);
        const z = sp.get('zones'); if (z) setZones(new Set(z.split(',').filter(s => s === 'search' || s === 'recommendations')));
        const nmParam = sp.get('nm');
        const nms = bulk || !nmParam ? [] : nmParam.split(',').map(Number).filter(n => Number.isFinite(n) && n > 0);
        // Каталог: артикулы для авто-имени + подстановка названий предзаполненных из URL товаров
        api.getAdArticleCatalog()
            .then(rows => {
                setVendorByNm(new Map(rows.map(r => [r.nm_id, r.vendor_code || ''])));
                if (nms.length) {
                    const by = new Map(rows.map(r => [r.nm_id, r.vendor_code]));
                    setSelected(prev => { const n = new Map(prev); for (const nm of nms) if (!n.has(nm)) n.set(nm, by.get(nm) || String(nm)); return n; });
                }
            })
            .catch(() => { if (nms.length) setSelected(prev => { const n = new Map(prev); for (const nm of nms) if (!n.has(nm)) n.set(nm, String(nm)); return n; }); });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Первый выбранный товар → авто-имя «nmID артикул ТИП», пока имя не правили вручную
    const firstNm = [...selected.keys()][0] ?? null;
    useEffect(() => {
        if (nameEdited || firstNm == null) return;
        setName(buildAutoCampaignName(firstNm, vendorByNm.get(firstNm) || '', payment, bidType));
    }, [firstNm, vendorByNm, payment, bidType, nameEdited]);

    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');

    // Зоны редактируются только у ручной CPM; CPC — только поиск, единая — обе (WB сам)
    const zonesEditable = payment === 'cpm' && bidType === 'manual';

    const budgetNum = Math.round(Number(budget) || 0);
    // Бюджет 0/пусто = черновик без пополнения (деньги не списываются); иначе — минимум WB
    const canSubmit = name.trim() && selected.size > 0 && !busy
        && (!zonesEditable || zones.size > 0) && (budgetNum === 0 || budgetNum >= MIN_BUDGET);

    const submit = async () => {
        setBusy(true); setError('');
        try {
            // 1. Создаём кампанию
            const created = await api.createCampaign({
                name: name.trim(),
                nms: [...selected.keys()],
                bid_type: payment === 'cpc' ? 'manual' : bidType,
                payment_type: payment,
                placement_types: zonesEditable ? [...zones] : null,
            });
            if (!created.ok || !created.campaign_id) { toast.error(created.error || 'Не удалось создать кампанию'); return; }
            const id = created.campaign_id;

            // 2–4: кампания уже существует — провалы шагов не критичны, но о них НАДО сказать
            const stepErrors: string[] = [];
            try { if (budgetNum >= MIN_BUDGET) await api.depositCampaignBudget(id, budgetNum, 0); }
            catch { stepErrors.push(`пополнение на ${formatNumber(budgetNum, 0)} ₽ не прошло`); }

            // 3. Ставки по зонам (ручная CPM) — применяем к каждой карточке товара
            try {
                if (zonesEditable) {
                    const bs = Math.round(Number(bidSearch) || 0);
                    const br = Math.round(Number(bidRec) || 0);
                    const bids = [...selected.keys()].flatMap(nm => [
                        ...(zones.has('search') && bs > 0 ? [{ nm_id: nm, bid_rub: bs, placement: 'search' }] : []),
                        ...(zones.has('recommendations') && br > 0 ? [{ nm_id: nm, bid_rub: br, placement: 'recommendations' }] : []),
                    ]);
                    if (bids.length) await api.setCardBids(id, bids);
                }
            } catch { stepErrors.push('ставки зон не применились'); }

            // 4. Автопополнение
            try { if (autopay?.enabled) await api.setCampaignAutopay(id, autopay); }
            catch { stepErrors.push('автопополнение не настроилось'); }

            if (stepErrors.length) toast.warning(`Кампания создана, но: ${stepErrors.join('; ')}. Донастройте на странице кампании.`);
            else toast.success('Кампания создана');
            router.push(`/p/${slug}/ads-manager/campaign/${id}`);
        } catch (e) { toast.error(e instanceof Error ? e.message : 'Не удалось создать кампанию'); }
        finally { setBusy(false); }
    };

    const card: React.CSSProperties = { background: '#fff', borderRadius: 16, padding: 20, marginBottom: 16, border: '1px solid #eef0f2' };
    const h2: React.CSSProperties = { fontSize: 17, fontWeight: 700, margin: '0 0 14px' };

    if (bulk) {
        return <PageGuard page="ads-manager"><BulkCreate slug={slug} /></PageGuard>;
    }

    return (
        <PageGuard page="ads-manager">
            <div className="animate-in" style={{ maxWidth: 1000 }}>
                <Link href={`/p/${slug}/ads-manager`} className="btn btn-secondary btn-sm" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }}>← Все кампании</Link>

                {/* Название */}
                <div style={{ ...card, marginTop: 10 }}>
                    <EditableName value={name} onChange={v => { setName(v); setNameEdited(true); }} maxLength={50} />
                    <div style={{ display: 'flex', gap: 12, marginTop: 14, flexWrap: 'wrap' }}>
                        {([['cpc', 'Оплата за клики (CPC)', 'Вы платите за 1 клик'], ['cpm', 'Оплата за показы (CPM)', 'Вы платите за 1000 показов']] as const).map(([k, t, sub]) => (
                            <button key={k} onClick={() => setPayment(k)}
                                style={{ flex: 1, minWidth: 240, textAlign: 'left', padding: '12px 16px', borderRadius: 12, cursor: 'pointer', background: payment === k ? '#f3f0ff' : '#fff', border: `1px solid ${payment === k ? '#8b5cf6' : '#e5e7eb'}` }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                    <span style={{ width: 14, height: 14, borderRadius: '50%', border: `2px solid ${payment === k ? '#8b5cf6' : '#d1d5db'}`, background: payment === k ? '#8b5cf6' : '#fff', boxShadow: payment === k ? 'inset 0 0 0 3px #fff' : 'none' }} />
                                    <span style={{ fontWeight: 600, fontSize: 14 }}>{t}</span>
                                </div>
                                <div style={{ fontSize: 12, color: '#9ca3af', marginLeft: 22 }}>{sub}</div>
                            </button>
                        ))}
                    </div>
                </div>

                {/* Бюджет + автопополнение */}
                <div style={card}>
                    <h2 style={h2}>Бюджет</h2>
                    <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                        <div>
                            <label style={{ fontSize: 12, color: '#6b7280', display: 'block', marginBottom: 4 }}>Бюджет кампании, ₽</label>
                            <input type="number" min={0} step={100} value={budget} onChange={e => setBudget(e.target.value)}
                                style={{ fontSize: 15, padding: '9px 12px', borderRadius: 10, border: `1px solid ${budgetNum !== 0 && budgetNum < MIN_BUDGET ? '#ef4444' : '#d1d5db'}`, width: 240 }} />
                            <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 4 }}>Минимальный бюджет — {MIN_BUDGET} ₽ · 0 или пусто — создать черновик без пополнения</div>
                        </div>
                        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 9, marginTop: 22 }}>
                            {/* Тумблер переключает: выкл (сброс настройки) ↔ вкл (через модалку). Текст всегда открывает модалку. */}
                            <Switch on={!!autopay?.enabled} ariaLabel="Автопополнение вкл/выкл"
                                onClick={() => autopay?.enabled ? setAutopay(null) : setAutopayOpen(true)} />
                            <button onClick={() => setAutopayOpen(true)} style={{ border: 'none', background: 'none', cursor: 'pointer', fontSize: 14, color: 'var(--color-accent)' }}>
                                Автопополнение бюджета{autopay?.enabled ? ` · ${formatNumber(autopay.amount, 0)} ₽` : ''}
                            </button>
                        </div>
                    </div>
                </div>

                {/* Зоны показов */}
                <div style={card}>
                    <h2 style={h2}>Зоны показов</h2>
                    {payment === 'cpc' ? (
                        <div style={{ fontSize: 13, color: '#6b7280' }}>
                            <b style={{ color: '#111827' }}>В кампаниях с оплатой за клики недоступны рекомендации.</b><br />
                            Товары продвигаются только по запросам покупателей — в поиске и в каталоге.
                        </div>
                    ) : (
                        <>
                            {([['unified', 'Единая ставка: продвижение сразу в поиске, каталоге и рекомендациях'], ['manual', 'Ручная ставка: выбирайте зоны показов и ставку для них']] as const).map(([k, label]) => (
                                <label key={k} style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '7px 0', cursor: 'pointer' }}>
                                    <input type="radio" checked={bidType === k} onChange={() => setBidType(k)} />
                                    <span style={{ fontSize: 13.5, color: '#374151' }}>{label}</span>
                                </label>
                            ))}
                            {zonesEditable && (
                                <div style={{ display: 'flex', gap: 12, marginTop: 10, flexWrap: 'wrap' }}>
                                    {([['search', 'Поиск', 'Когда запустите кампанию, сможете управлять поисковыми фразами', bidSearch, setBidSearch], ['recommendations', 'Рекомендации', 'Действует дисконтирование — ставка на продвижение будет ниже указанной', bidRec, setBidRec]] as const).map(([k, t, sub, bidVal, setBid]) => {
                                        const on = zones.has(k);
                                        return (
                                            <div key={k} style={{ flex: 1, minWidth: 260, padding: '12px 14px', borderRadius: 12, border: '1px solid #e5e7eb', opacity: on ? 1 : 0.55 }}>
                                                <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                                                    <Switch on={on} size="sm" ariaLabel={t} onClick={() => setZones(prev => { const n = new Set(prev); n.has(k) ? n.delete(k) : n.add(k); return n; })} />
                                                    <b style={{ fontSize: 14 }}>{t}</b>
                                                    <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 4 }} onClick={e => e.preventDefault()}>
                                                        <input type="number" min={0} step={1} value={bidVal} disabled={!on}
                                                            onChange={e => setBid(e.target.value)}
                                                            style={{ width: 74, fontSize: 13, textAlign: 'right', padding: '5px 8px', borderRadius: 8, border: '1px solid #d1d5db' }} />
                                                        <span style={{ fontSize: 12, color: '#9ca3af' }}>₽</span>
                                                    </span>
                                                </label>
                                                <div style={{ fontSize: 11.5, color: '#9ca3af', marginTop: 6 }}>{sub}</div>
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </>
                    )}
                </div>

                {/* Товары */}
                <div style={card}>
                    <h2 style={h2}>Товары для продвижения</h2>
                    {selected.size > 0 && (
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 12 }}>
                            {[...selected.entries()].map(([nm, title]) => (
                                <span key={nm} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, padding: '4px 8px', borderRadius: 8, background: '#f3f0ff', color: '#5b21b6' }}>
                                    {title.slice(0, 32)}{title.length > 32 ? '…' : ''}
                                    <button onClick={() => setSelected(prev => { const n = new Map(prev); n.delete(nm); return n; })} style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#8b5cf6', display: 'inline-flex' }}><IcX size={12} /></button>
                                </span>
                            ))}
                        </div>
                    )}
                    <button onClick={() => setPickerOpen(true)} className="btn btn-secondary btn-sm" style={{ fontSize: 13 }}>
                        + Добавить товары{selected.size > 0 ? ` (${selected.size}/${MAX_NMS})` : ''}
                    </button>
                </div>

                {error && <div style={{ fontSize: 13, color: '#ef4444', marginBottom: 12 }}>{error}</div>}

                <div style={{ display: 'flex', gap: 10, marginBottom: 40 }}>
                    <button onClick={submit} disabled={!canSubmit} className="btn btn-primary" style={{ fontSize: 14 }}>
                        {busy ? 'Создаём…' : 'Создать кампанию'}
                    </button>
                    <Link href={`/p/${slug}/ads-manager`} className="btn btn-secondary" style={{ fontSize: 14 }}>Отмена</Link>
                </div>

                {autopayOpen && (
                    <AutopaySettingsModal
                        initial={autopay ?? { ...DEFAULT_AUTOPAY, amount: Math.max(MIN_BUDGET, budgetNum) }}
                        onClose={() => setAutopayOpen(false)}
                        onSave={s => { setAutopay(s); setAutopayOpen(false); }}
                    />
                )}
                {pickerOpen && (
                    <AddProductsModal
                        initialSelected={selected}
                        onClose={() => setPickerOpen(false)}
                        onApply={sel => { setSelected(sel); setPickerOpen(false); }}
                    />
                )}
            </div>
        </PageGuard>
    );
}
