'use client';
import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber } from '@/lib/utils';
import { IcX } from './icons';
import WbThumb from './WbThumb';
import AddProductsModal from './AddProductsModal';
import Switch from './Switch';
import { useToast } from './Toasts';
import { buildAutoCampaignName } from './adsShared';

const MAX = 50;
const MIN_BUDGET = 1000;

type Payment = 'cpc' | 'cpm';
type Bid = 'unified' | 'manual';
type Result = { nm: number; ok: boolean; id?: number | null; error?: string };

/** «Много кампаний»: 1 товар = 1 кампания, до 50 сразу. Общие настройки применяются ко всем;
 *  название каждой можно поправить. Создание — последовательные вызовы save-ad с прогрессом. */
export default function BulkCreate({ slug }: { slug: string }) {
    const router = useRouter();
    const toast = useToast();
    const [pickerOpen, setPickerOpen] = useState(false);
    const [products, setProducts] = useState<Map<number, string>>(() => new Map());  // nm → title
    const [names, setNames] = useState<Map<number, string>>(() => new Map());          // nm → имя кампании
    const [editedNames, setEditedNames] = useState<Set<number>>(() => new Set());      // nm с ручной правкой имени
    const [vendorByNm, setVendorByNm] = useState<Map<number, string>>(() => new Map()); // nm → артикул продавца
    const [payment, setPayment] = useState<Payment>('cpm');
    const [bidType, setBidType] = useState<Bid>('unified');
    const [zones, setZones] = useState<Set<string>>(() => new Set(['search', 'recommendations']));
    const [bidSearch, setBidSearch] = useState('150');
    const [bidRec, setBidRec] = useState('150');
    const [budget, setBudget] = useState('3000');
    const [busy, setBusy] = useState(false);
    const [progress, setProgress] = useState('');
    const [results, setResults] = useState<Result[] | null>(null);
    const [error, setError] = useState('');

    const zonesEditable = payment === 'cpm' && bidType === 'manual';
    const budgetNum = Math.round(Number(budget) || 0);

    // Каталог артикулов для авто-имени «nmID артикул ТИП»
    useEffect(() => {
        api.getAdArticleCatalog()
            .then(rows => setVendorByNm(new Map(rows.map(r => [r.nm_id, r.vendor_code || '']))))
            .catch(() => {});
    }, []);

    const applyProducts = (sel: Map<number, string>) => {
        setProducts(sel);
        // Имя по умолчанию — «nmID артикул ТИП»; ручные правки и удалённые товары учитываем
        setNames(prev => {
            const next = new Map(prev);
            for (const nm of sel.keys()) if (!next.has(nm)) next.set(nm, buildAutoCampaignName(nm, vendorByNm.get(nm) || '', payment, bidType));
            for (const nm of [...next.keys()]) if (!sel.has(nm)) next.delete(nm);
            return next;
        });
        setEditedNames(prev => { const n = new Set(prev); for (const nm of [...n]) if (!sel.has(nm)) n.delete(nm); return n; });
        setPickerOpen(false);
    };

    const removeProduct = (nm: number) => {
        setProducts(prev => { const n = new Map(prev); n.delete(nm); return n; });
        setNames(prev => { const n = new Map(prev); n.delete(nm); return n; });
        setEditedNames(prev => { const n = new Set(prev); n.delete(nm); return n; });
    };

    // Пересобираем авто-имена при смене типа/каталога — кроме тех, что правили вручную
    useEffect(() => {
        setNames(prev => {
            const next = new Map(prev);
            for (const nm of products.keys()) if (!editedNames.has(nm)) next.set(nm, buildAutoCampaignName(nm, vendorByNm.get(nm) || '', payment, bidType));
            return next;
        });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [payment, bidType, vendorByNm, products]);

    const canSubmit = products.size > 0 && !busy && budgetNum >= MIN_BUDGET && (!zonesEditable || zones.size > 0);

    const submit = async () => {
        setBusy(true); setError(''); setResults(null);
        const out: Result[] = [];
        const entries = [...products.entries()];
        for (let i = 0; i < entries.length; i++) {
            const [nm, title] = entries[i];
            setProgress(`Создаём ${i + 1} из ${entries.length}…`);
            try {
                const res = await api.createCampaign({
                    name: (names.get(nm) || title).trim().slice(0, 50),
                    nms: [nm],
                    bid_type: payment === 'cpc' ? 'manual' : bidType,
                    payment_type: payment,
                    placement_types: zonesEditable ? [...zones] : null,
                });
                if (res.ok && res.campaign_id) {
                    const id = res.campaign_id;
                    try { if (budgetNum >= MIN_BUDGET) await api.depositCampaignBudget(id, budgetNum, 0); } catch { /* бюджет добавит пользователь */ }
                    // Ставки по зонам (ручная CPM) — на карточку этой кампании
                    try {
                        if (zonesEditable) {
                            const bs = Math.round(Number(bidSearch) || 0);
                            const br = Math.round(Number(bidRec) || 0);
                            const bids = [
                                ...(zones.has('search') && bs > 0 ? [{ nm_id: nm, bid_rub: bs, placement: 'search' }] : []),
                                ...(zones.has('recommendations') && br > 0 ? [{ nm_id: nm, bid_rub: br, placement: 'recommendations' }] : []),
                            ];
                            if (bids.length) await api.setCardBids(id, bids);
                        }
                    } catch { /* ставки настроит пользователь */ }
                    out.push({ nm, ok: true, id });
                } else out.push({ nm, ok: false, error: res.error || 'отказ WB' });
            } catch (e) { out.push({ nm, ok: false, error: e instanceof Error ? e.message : 'ошибка' }); }
        }
        setResults(out); setProgress(''); setBusy(false);
        const okCount = out.filter(r => r.ok).length;
        if (okCount === entries.length) { toast.success(`Создано кампаний: ${okCount}`); router.push(`/p/${slug}/ads-manager`); }
        else if (okCount > 0) toast.warning(`Создано ${okCount} из ${entries.length}, часть с ошибками`);
        else toast.error('Ни одна кампания не создана — см. ошибки в строках');
    };

    const card: React.CSSProperties = { background: '#fff', borderRadius: 16, padding: 20, marginBottom: 16, border: '1px solid #eef0f2' };
    const h2: React.CSSProperties = { fontSize: 17, fontWeight: 700, margin: '0 0 14px' };

    return (
        <div className="animate-in" style={{ maxWidth: 1100 }}>
            <Link href={`/p/${slug}/ads-manager`} className="btn btn-secondary btn-sm" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }}>← Все кампании</Link>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, margin: '10px 0 16px' }}>
                <h1 style={{ fontSize: 24, fontWeight: 700, margin: 0 }}>Много кампаний</h1>
                <span style={{ fontSize: 13, color: '#9ca3af' }}>1 товар = 1 кампания · до {MAX}</span>
            </div>

            {/* Общие настройки */}
            <div style={card}>
                <h2 style={h2}>Настройки для всех кампаний</h2>
                <div style={{ display: 'flex', gap: 12, marginBottom: 14, flexWrap: 'wrap' }}>
                    {([['cpc', 'Оплата за клики (CPC)'], ['cpm', 'Оплата за показы (CPM)']] as const).map(([k, t]) => (
                        <button key={k} onClick={() => setPayment(k)}
                            style={{ padding: '10px 16px', borderRadius: 12, cursor: 'pointer', fontSize: 13.5, fontWeight: 600, background: payment === k ? '#f3f0ff' : '#fff', border: `1px solid ${payment === k ? '#8b5cf6' : '#e5e7eb'}`, color: payment === k ? '#5b21b6' : '#374151' }}>{t}</button>
                    ))}
                </div>
                {payment === 'cpm' && (
                    <div style={{ marginBottom: 12 }}>
                        {([['unified', 'Единая ставка: поиск, каталог и рекомендации'], ['manual', 'Ручная ставка: выбранные зоны']] as const).map(([k, label]) => (
                            <label key={k} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 0', cursor: 'pointer' }}>
                                <input type="radio" checked={bidType === k} onChange={() => setBidType(k)} />
                                <span style={{ fontSize: 13.5, color: '#374151' }}>{label}</span>
                            </label>
                        ))}
                        {zonesEditable && (
                            <div style={{ display: 'flex', gap: 20, marginTop: 8, marginLeft: 24, flexWrap: 'wrap' }}>
                                {([['search', 'Поиск', bidSearch, setBidSearch], ['recommendations', 'Рекомендации', bidRec, setBidRec]] as const).map(([k, label, bidVal, setBid]) => {
                                    const on = zones.has(k);
                                    return (
                                        <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 8, opacity: on ? 1 : 0.55 }}>
                                            <Switch on={on} ariaLabel={label} onClick={() => setZones(prev => { const n = new Set(prev); n.has(k) ? n.delete(k) : n.add(k); return n; })} />
                                            <span style={{ fontSize: 13, color: '#374151' }}>{label}</span>
                                            <input type="number" min={0} value={bidVal} disabled={!on} onChange={e => setBid(e.target.value)}
                                                style={{ width: 72, fontSize: 12.5, textAlign: 'right', padding: '4px 7px', borderRadius: 8, border: '1px solid #d1d5db' }} />
                                            <span style={{ fontSize: 11, color: '#9ca3af' }}>₽</span>
                                        </span>
                                    );
                                })}
                            </div>
                        )}
                    </div>
                )}
                <div>
                    <label style={{ fontSize: 12, color: '#6b7280', display: 'block', marginBottom: 4 }}>Бюджет каждой кампании, ₽</label>
                    <input type="number" min={MIN_BUDGET} step={100} value={budget} onChange={e => setBudget(e.target.value)}
                        style={{ fontSize: 15, padding: '9px 12px', borderRadius: 10, border: `1px solid ${budgetNum < MIN_BUDGET ? '#ef4444' : '#d1d5db'}`, width: 200 }} />
                    <span style={{ fontSize: 11, color: '#9ca3af', marginLeft: 10 }}>минимум {MIN_BUDGET} ₽ · всего {formatNumber(budgetNum * products.size, 0)} ₽ за {products.size}</span>
                </div>
            </div>

            {/* Товары = кампании */}
            <div style={card}>
                <div style={{ display: 'flex', alignItems: 'center', marginBottom: 14 }}>
                    <h2 style={{ ...h2, margin: 0 }}>Товары · {products.size} кампаний</h2>
                    <button onClick={() => setPickerOpen(true)} className="btn btn-secondary btn-sm" style={{ fontSize: 13, marginLeft: 'auto' }}>+ Добавить товары</button>
                </div>
                {products.size === 0 ? (
                    <div style={{ padding: 30, textAlign: 'center', color: '#9ca3af', fontSize: 13 }}>Добавьте товары — на каждый создадим свою кампанию</div>
                ) : (
                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                        <thead>
                            <tr>
                                <th style={{ width: 54, padding: '8px', textAlign: 'left', fontSize: 11, color: '#6b7280' }}>ТОВАР</th>
                                <th style={{ padding: '8px', textAlign: 'left', fontSize: 11, color: '#6b7280' }}>НАЗВАНИЕ КАМПАНИИ</th>
                                <th style={{ width: 120, padding: '8px', textAlign: 'left', fontSize: 11, color: '#6b7280' }}>АРТИКУЛ</th>
                                <th style={{ width: 40 }} />
                            </tr>
                        </thead>
                        <tbody>
                            {[...products.entries()].map(([nm, title]) => {
                                const res = results?.find(r => r.nm === nm);
                                return (
                                    <tr key={nm} style={{ borderTop: '1px solid #f4f4f5' }}>
                                        <td style={{ padding: '8px' }}><WbThumb nmId={nm} size={40} /></td>
                                        <td style={{ padding: '8px' }}>
                                            <input value={names.get(nm) ?? title} maxLength={50}
                                                onChange={e => { setNames(prev => new Map(prev).set(nm, e.target.value)); setEditedNames(prev => new Set(prev).add(nm)); }}
                                                style={{ width: '100%', fontSize: 13, padding: '6px 9px', borderRadius: 8, border: '1px solid #e5e7eb' }} />
                                            {res && !res.ok && <div style={{ fontSize: 11, color: '#ef4444', marginTop: 3 }}>{res.error}</div>}
                                            {res && res.ok && <div style={{ fontSize: 11, color: '#10b981', marginTop: 3 }}>создана ✓</div>}
                                        </td>
                                        <td style={{ padding: '8px', fontSize: 12, color: '#6b7280' }}>{nm}</td>
                                        <td style={{ padding: '8px', textAlign: 'center' }}>
                                            <button onClick={() => removeProduct(nm)} disabled={busy} style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#9ca3af', display: 'inline-flex' }}><IcX size={15} /></button>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                )}
            </div>

            {error && <div style={{ fontSize: 13, color: '#ef4444', marginBottom: 12 }}>{error}</div>}
            {results && (
                <div style={{ fontSize: 13, marginBottom: 12, color: '#374151' }}>
                    Создано {results.filter(r => r.ok).length} из {results.length}.{results.some(r => !r.ok) ? ' Часть кампаний не создалась — см. ошибки в строках.' : ''}
                </div>
            )}

            <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 40 }}>
                <button onClick={submit} disabled={!canSubmit} className="btn btn-primary" style={{ fontSize: 14 }}>
                    {busy ? (progress || 'Создаём…') : `Создать ${products.size || ''} кампаний`}
                </button>
                <Link href={`/p/${slug}/ads-manager`} className="btn btn-secondary" style={{ fontSize: 14 }}>Отмена</Link>
            </div>

            {pickerOpen && (
                <AddProductsModal
                    initialSelected={products}
                    onClose={() => setPickerOpen(false)}
                    onApply={applyProducts}
                />
            )}
        </div>
    );
}
