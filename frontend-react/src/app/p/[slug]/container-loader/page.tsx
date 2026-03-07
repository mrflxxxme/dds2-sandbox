'use client';

import { useState, useCallback } from 'react';
import dynamic from 'next/dynamic';
import { formatNumber } from '@/lib/utils';
import {
    CONTAINERS, BOX_COLORS,
    calculatePack,
    type BoxType, type PackResult,
} from './lib/packer';

const ContainerScene = dynamic(() => import('./components/ContainerScene'), { ssr: false });

const cm = (m: number) => (m * 100).toFixed(0);

export default function ContainerLoaderPage() {
    const [cType, setCType] = useState('truck2');
    const [boxes, setBoxes] = useState<BoxType[]>([{ l: 60, w: 40, h: 40, qty: 50, label: 'Коробка 1' }]);
    const [result, setResult] = useState<PackResult | null>(null);
    const [dCost, setDCost] = useState('');
    const [curr, setCurr] = useState('$');
    const [showFree, setShowFree] = useState(true);

    const addBox = () => setBoxes([...boxes, { l: 40, w: 30, h: 30, qty: 10, label: `Коробка ${boxes.length + 1}` }]);
    const removeBox = (i: number) => { if (boxes.length > 1) setBoxes(boxes.filter((_: BoxType, j: number) => j !== i)); };
    const updateBox = (i: number, f: keyof BoxType, v: string) => {
        const u = [...boxes];
        u[i] = { ...u[i], [f]: f === 'label' ? v : (parseFloat(v) || 0) };
        setBoxes(u);
    };

    const calc = useCallback(() => {
        setResult(calculatePack(cType, boxes));
    }, [cType, boxes]);

    const c = CONTAINERS[cType];
    const cost = parseFloat(dCost) || 0;

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">🚛 Загрузка контейнера</h1>
                    <p className="page-subtitle">Раскладка • Себестоимость доставки • 3D визуализация</p>
                </div>
            </div>

            {/* Container type */}
            <div className="glass-card" style={{ marginBottom: 16 }}>
                <label className="form-label" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '1px', marginBottom: 8, display: 'block' }}>
                    Тип контейнера / транспорта
                </label>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {Object.entries(CONTAINERS).map(([k, v]) => (
                        <button key={k}
                            className={`btn btn-sm ${cType === k ? 'btn-primary' : 'btn-secondary'}`}
                            onClick={() => setCType(k)}
                            style={{ flexDirection: 'column', alignItems: 'center', padding: '8px 14px' }}>
                            {v.name}
                            <span style={{ fontSize: 10, opacity: 0.7, marginTop: 2 }}>{v.l}×{v.w}×{v.h} м</span>
                        </button>
                    ))}
                </div>
            </div>

            {/* Delivery cost */}
            <div className="glass-card" style={{ marginBottom: 16 }}>
                <label className="form-label" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '1px', marginBottom: 8, display: 'block' }}>
                    💰 Стоимость доставки (за рейс)
                </label>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <select className="form-input" value={curr} onChange={e => setCurr(e.target.value)}
                        style={{ width: 70, textAlign: 'center' }}>
                        {['$', '€', '₽', '¥', '£'].map(x => <option key={x} value={x}>{x}</option>)}
                    </select>
                    <input className="form-input" type="number" placeholder="Стоимость..."
                        value={dCost} onChange={e => setDCost(e.target.value)}
                        style={{ width: 200 }} />
                </div>
            </div>

            {/* Boxes */}
            <div className="glass-card" style={{ marginBottom: 16 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                    <label className="form-label" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '1px' }}>
                        Коробки (размеры в см)
                    </label>
                    <button className="btn btn-success btn-sm" onClick={addBox}>+ Добавить</button>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {boxes.map((b, i) => (
                        <div key={i} className="box-row">
                            <div className="box-color-dot" style={{ background: BOX_COLORS[i % BOX_COLORS.length] }} />
                            <input className="form-input" value={b.label}
                                onChange={e => updateBox(i, 'label', e.target.value)}
                                style={{ width: 100, padding: '6px 8px', fontSize: 13 }} />
                            {(['l', 'w', 'h'] as const).map(d => (
                                <div key={d} style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
                                    <span style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>
                                        {d === 'l' ? 'Д' : d === 'w' ? 'Ш' : 'В'}
                                    </span>
                                    <input className="form-input" type="number" value={b[d]}
                                        onChange={e => updateBox(i, d, e.target.value)}
                                        style={{ width: 60, padding: '6px 8px', fontSize: 13, textAlign: 'center' }} />
                                </div>
                            ))}
                            <div style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
                                <span style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>Кол:</span>
                                <input className="form-input" type="number" value={b.qty}
                                    onChange={e => updateBox(i, 'qty', e.target.value)}
                                    style={{ width: 60, padding: '6px 8px', fontSize: 13, textAlign: 'center' }} />
                            </div>
                            <span style={{ fontSize: 10, color: 'var(--color-text-dim)' }}>
                                {((b.l * b.w * b.h * b.qty) / 1e6).toFixed(2)} м³
                            </span>
                            {boxes.length > 1 && (
                                <button className="btn btn-danger btn-sm"
                                    style={{ padding: '2px 8px', marginLeft: 'auto' }}
                                    onClick={() => removeBox(i)}>×</button>
                            )}
                        </div>
                    ))}
                </div>
            </div>

            {/* Calculate button */}
            <button className="btn btn-primary" onClick={calc}
                style={{ width: '100%', marginBottom: 20, padding: '14px', fontSize: 15, fontWeight: 700 }}>
                ▶ РАССЧИТАТЬ ЗАГРУЗКУ
            </button>

            {/* Results */}
            {result && (
                <>
                    {/* Stats */}
                    <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
                        <div className="stat-card" style={{
                            borderColor: parseFloat(result.fillPercent) > 85 ? 'rgba(34,197,94,0.4)' : parseFloat(result.fillPercent) > 60 ? 'rgba(245,158,11,0.4)' : 'rgba(239,68,68,0.4)',
                            textAlign: 'center',
                        }}>
                            <div className="stat-card-value" style={{
                                color: parseFloat(result.fillPercent) > 85 ? 'var(--color-success)' : parseFloat(result.fillPercent) > 60 ? 'var(--color-warning)' : 'var(--color-danger)',
                            }}>{result.fillPercent}%</div>
                            <div className="stat-card-label">Заполнение</div>
                        </div>
                        <div className="stat-card" style={{ textAlign: 'center' }}>
                            <div className="stat-card-value" style={{ color: 'var(--color-accent)' }}>
                                {result.totalPlaced}<span style={{ fontSize: 14, color: 'var(--color-text-dim)' }}>/{result.totalRequested}</span>
                            </div>
                            <div className="stat-card-label">Размещено</div>
                        </div>
                        <div className="stat-card" style={{ textAlign: 'center' }}>
                            <div className="stat-card-value" style={{ fontSize: 20 }}>
                                {result.boxVol.toFixed(1)}<span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}> м³</span>
                            </div>
                            <div className="stat-card-label">Груз из {result.containerVol.toFixed(1)}</div>
                        </div>
                        <div className="stat-card" style={{ textAlign: 'center' }}>
                            <div className="stat-card-value" style={{ fontSize: 20, color: 'var(--color-danger)' }}>
                                {result.freeVol.toFixed(1)}<span style={{ fontSize: 12, color: 'var(--color-text-dim)' }}> м³</span>
                            </div>
                            <div className="stat-card-label">Свободно</div>
                        </div>
                    </div>

                    {/* Per-type badges */}
                    <div className="glass-card" style={{ marginBottom: 16, padding: 14 }}>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                            {boxes.map((b, i) => {
                                const p = result.placedPerType[i] || 0;
                                const np = b.qty - p;
                                return (
                                    <div key={i} className={`badge ${np > 0 ? 'badge-danger' : 'badge-success'}`}
                                        style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px' }}>
                                        <div className="box-color-dot" style={{ background: BOX_COLORS[i % BOX_COLORS.length] }} />
                                        <span>{b.label}:</span>
                                        <span style={{ fontWeight: 600 }}>{p}/{b.qty}</span>
                                        {np > 0 && <span style={{ fontSize: 10 }}>⚠ -{np}</span>}
                                    </div>
                                );
                            })}
                        </div>
                    </div>

                    {/* Free space section */}
                    <div className="glass-card" style={{ marginBottom: 16 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                            <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--color-danger)', margin: 0 }}>
                                🔴 Свободное пространство
                            </h3>
                            <button className={`btn btn-sm ${showFree ? 'btn-danger' : 'btn-secondary'}`}
                                onClick={() => setShowFree(!showFree)}>
                                {showFree ? '🔴 Скрыть в 3D' : '⭕ Показать в 3D'}
                            </button>
                        </div>

                        {/* Progress bar */}
                        <div className="cl-progress-bar" style={{ marginBottom: 16 }}>
                            <div className="cl-progress-fill" style={{ width: `${result.fillPercent}%` }} />
                            <div className="cl-progress-text">
                                {result.fillPercent}% занято → {(100 - parseFloat(result.fillPercent)).toFixed(1)}% свободно
                            </div>
                        </div>

                        {/* Free space cards */}
                        <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
                            <div className="stat-card" style={{ borderColor: 'rgba(239,68,68,0.2)' }}>
                                <div className="stat-card-label">Свободно по длине</div>
                                <div className="stat-card-value" style={{ fontSize: 22, color: 'var(--color-danger)' }}>
                                    {cm(result.freeLength)} <span style={{ fontSize: 12, fontWeight: 400 }}>см</span>
                                </div>
                                <div className="stat-card-sub">Загружено: {cm(result.maxBoxX)} из {cm(c.l)} см</div>
                            </div>
                            {result.largestFree && (
                                <div className="stat-card" style={{ borderColor: 'rgba(239,68,68,0.2)' }}>
                                    <div className="stat-card-label">Крупнейшая пустая зона</div>
                                    <div className="stat-card-value" style={{ fontSize: 18, color: 'var(--color-danger)' }}>
                                        {cm(result.largestFree.l)} × {cm(result.largestFree.w)} × {cm(result.largestFree.h)}
                                        <span style={{ fontSize: 11, fontWeight: 400 }}> см</span>
                                    </div>
                                    <div className="stat-card-sub">
                                        Объём: {(result.largestFree.l * result.largestFree.w * result.largestFree.h).toFixed(2)} м³
                                    </div>
                                </div>
                            )}
                            <div className="stat-card" style={{ borderColor: 'rgba(239,68,68,0.2)' }}>
                                <div className="stat-card-label">Пустых зон</div>
                                <div className="stat-card-value" style={{ fontSize: 22, color: 'var(--color-danger)' }}>
                                    {result.freeCount}
                                </div>
                                <div className="stat-card-sub">Общий объём: {result.freeVol.toFixed(2)} м³</div>
                            </div>
                        </div>

                        {/* Top free spaces table */}
                        {result.freeSpaces.length > 0 && (
                            <div style={{ marginTop: 14 }}>
                                <div style={{ fontSize: 10, color: 'var(--color-text-dim)', textTransform: 'uppercase', marginBottom: 6 }}>
                                    Топ пустых зон
                                </div>
                                <div style={{ overflowX: 'auto' }}>
                                    <table className="data-table">
                                        <thead>
                                            <tr>
                                                <th>#</th>
                                                <th style={{ textAlign: 'center' }}>Размеры (Д×Ш×В), см</th>
                                                <th style={{ textAlign: 'center' }}>Объём, м³</th>
                                                <th style={{ textAlign: 'center' }}>Позиция, см</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {[...result.freeSpaces]
                                                .sort((a, b) => (b.l * b.w * b.h) - (a.l * a.w * a.h))
                                                .slice(0, 5)
                                                .map((s, i) => (
                                                    <tr key={i}>
                                                        <td style={{ color: 'var(--color-danger)', fontWeight: 600 }}>{i + 1}</td>
                                                        <td style={{ textAlign: 'center' }}>{cm(s.l)} × {cm(s.w)} × {cm(s.h)}</td>
                                                        <td style={{ textAlign: 'center', color: 'var(--color-danger)', fontWeight: 600 }}>
                                                            {(s.l * s.w * s.h).toFixed(3)}
                                                        </td>
                                                        <td style={{ textAlign: 'center', color: 'var(--color-text-dim)' }}>
                                                            X:{cm(s.x)} Y:{cm(s.y)} Z:{cm(s.z)}
                                                        </td>
                                                    </tr>
                                                ))}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Cost table */}
                    {cost > 0 ? (
                        <div className="glass-card" style={{ marginBottom: 16 }}>
                            <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>
                                💰 Себестоимость доставки за 1 коробку
                            </h3>
                            <div style={{ overflowX: 'auto' }}>
                                <table className="data-table">
                                    <thead>
                                        <tr>
                                            <th>Тип</th>
                                            <th style={{ textAlign: 'center' }}>Шт</th>
                                            <th style={{ textAlign: 'center', color: 'var(--color-warning)' }}>
                                                Цена/кор<br /><span style={{ fontSize: 9, fontWeight: 400 }}>(текущая)</span>
                                            </th>
                                            <th style={{ textAlign: 'center' }}>
                                                Макс<br /><span style={{ fontSize: 9 }}>(полная)</span>
                                            </th>
                                            <th style={{ textAlign: 'center', color: 'var(--color-success)' }}>
                                                Цена/кор<br /><span style={{ fontSize: 9, fontWeight: 400 }}>(полная)</span>
                                            </th>
                                            <th style={{ textAlign: 'center' }}>Экономия</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {boxes.map((b, i) => {
                                            const pc = result.placedPerType[i] || 0;
                                            const mc = result.maxPerType[i] || 0;
                                            const bvs = (b.l * b.w * b.h) / 1e6;
                                            const tv = result.boxVol;
                                            const tVol = pc * bvs;
                                            const tShare = tv > 0 ? tVol / tv : 0;
                                            const tCost = cost * tShare;
                                            const cpb = pc > 0 ? tCost / pc : 0;
                                            const cpbf = mc > 0 ? cost / mc : 0;
                                            const sav = cpb > 0 && cpbf > 0 ? ((1 - cpbf / cpb) * 100).toFixed(0) : '0';

                                            return (
                                                <tr key={i}>
                                                    <td>
                                                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                                            <div className="box-color-dot" style={{ background: BOX_COLORS[i % BOX_COLORS.length] }} />
                                                            <span>{b.label}</span>
                                                            <span style={{ color: 'var(--color-text-dim)', fontSize: 10 }}>{b.l}×{b.w}×{b.h}</span>
                                                        </div>
                                                    </td>
                                                    <td style={{ textAlign: 'center' }}>{pc}</td>
                                                    <td style={{ textAlign: 'center', color: 'var(--color-warning)', fontWeight: 700 }}>
                                                        {formatNumber(cpb)} {curr}
                                                    </td>
                                                    <td style={{ textAlign: 'center', color: 'var(--color-text-dim)' }}>{mc}</td>
                                                    <td style={{ textAlign: 'center', color: 'var(--color-success)', fontWeight: 700 }}>
                                                        {formatNumber(cpbf)} {curr}
                                                    </td>
                                                    <td style={{ textAlign: 'center' }}>
                                                        {Number(sav) > 0 ? (
                                                            <span className="badge badge-success">−{sav}%</span>
                                                        ) : (
                                                            <span style={{ color: 'var(--color-text-dim)' }}>—</span>
                                                        )}
                                                    </td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>
                            <div style={{
                                marginTop: 12, padding: '10px 14px', borderRadius: 8,
                                background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.25)',
                                display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10, fontSize: 13,
                            }}>
                                <div>
                                    <span style={{ color: 'var(--color-text-dim)' }}>Рейс: </span>
                                    <span style={{ fontWeight: 700 }}>{formatNumber(cost)} {curr}</span>
                                </div>
                                <div>
                                    <span style={{ color: 'var(--color-text-dim)' }}>Средняя/кор: </span>
                                    <span style={{ color: 'var(--color-warning)', fontWeight: 700 }}>
                                        {result.totalPlaced > 0 ? formatNumber(cost / result.totalPlaced) : '—'} {curr}
                                    </span>
                                </div>
                            </div>
                        </div>
                    ) : (
                        <div className="glass-card" style={{ marginBottom: 16, color: 'var(--color-text-dim)', fontStyle: 'italic', fontSize: 13 }}>
                            💡 Введите стоимость доставки, чтобы увидеть себестоимость за 1 коробку
                        </div>
                    )}

                    {/* 3D Scene */}
                    <div className="glass-card" style={{ padding: 0, overflow: 'hidden', position: 'relative' }}>
                        <ContainerScene
                            container={c}
                            placedBoxes={result.placed}
                            freeSpaces={result.freeSpaces}
                            showFree={showFree}
                        />
                        <div className="scene-overlay scene-overlay-bottom-left">
                            🖱 Вращение • Колёсико — масштаб
                        </div>
                        <div className="scene-overlay scene-overlay-top-right">
                            {c.name}: {c.l}×{c.w}×{c.h} м
                        </div>
                        {showFree && (
                            <div className="scene-overlay scene-overlay-top-left" style={{
                                background: 'rgba(239,68,68,0.15)', borderColor: 'rgba(239,68,68,0.3)',
                                color: 'var(--color-danger)',
                            }}>
                                🔴 Красные зоны — свободное место
                            </div>
                        )}
                    </div>
                </>
            )}
        </div>
    );
}
