'use client';

import { useState, useEffect, useMemo } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';

interface CityRow { city: string; region: string; okrug: string; order_count: number }
interface DailyRow { date: string; count: number }
interface GeoData {
  cities: CityRow[];
  daily: DailyRow[];
  dates: string[];
  totals: { total_orders: number; unique_cities: number };
  filters: { brands: string[]; categories: string[]; articles: string[] };
}

export default function OrderGeographyPage() {
  const [data, setData] = useState<GeoData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [brand, setBrand] = useState('');
  const [category, setCategory] = useState('');
  const [article, setArticle] = useState('');
  const [sortBy, setSortBy] = useState<'order_count' | 'city'>('order_count');
  const [sortDesc, setSortDesc] = useState(true);
  const [search, setSearch] = useState('');

  useEffect(() => {
    const now = new Date();
    const to = now.toISOString().slice(0, 10);
    const from = new Date(now.getTime() - 14 * 86400000).toISOString().slice(0, 10);
    setDateFrom(from);
    setDateTo(to);
  }, []);

  useEffect(() => {
    if (!dateFrom || !dateTo) return;
    let cancelled = false;
    (async () => {
      try {
        setLoading(true);
        setError('');
        const result = await api.getOrderGeography(
          dateFrom, dateTo,
          brand || undefined, category || undefined, article || undefined,
        );
        if (!cancelled) setData(result);
      } catch (err: unknown) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Ошибка загрузки');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [dateFrom, dateTo, brand, category, article]);

  const cities = useMemo(() => {
    if (!data) return [];
    let items = [...data.cities];
    if (search) {
      const q = search.toLowerCase();
      items = items.filter(c =>
        c.city.toLowerCase().includes(q) ||
        c.region.toLowerCase().includes(q) ||
        c.okrug.toLowerCase().includes(q)
      );
    }
    items.sort((a, b) => {
      if (sortBy === 'order_count') return sortDesc ? b.order_count - a.order_count : a.order_count - b.order_count;
      return sortDesc ? b.city.localeCompare(a.city) : a.city.localeCompare(b.city);
    });
    return items;
  }, [data, search, sortBy, sortDesc]);

  const chartMax = useMemo(() => {
    if (!data?.daily.length) return 1;
    return Math.max(...data.daily.map(d => d.count));
  }, [data]);

  const calendarDates = useMemo(() => {
    if (!data) return new Set<string>();
    return new Set(data.dates);
  }, [data]);

  const calendarMonths = useMemo(() => {
    if (!data?.dates.length) return [];
    const months = new Map<string, boolean>();
    data.dates.forEach(d => {
      const dt = new Date(d);
      const key = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}`;
      months.set(key, true);
    });
    const result: Array<{ key: string; year: number; month: number; daysInMonth: number; startDay: number }> = [];
    months.forEach((_, key) => {
      const [year, month] = key.split('-').map(Number);
      const daysInMonth = new Date(year, month, 0).getDate();
      let startDay = new Date(year, month - 1, 1).getDay();
      if (startDay === 0) startDay = 7;
      result.push({ key, year, month, daysInMonth, startDay });
    });
    return result;
  }, [data]);

  const regionStats = useMemo(() => {
    if (!data) return [];
    const map = new Map<string, number>();
    data.cities.forEach(c => map.set(c.region, (map.get(c.region) || 0) + c.order_count));
    return Array.from(map.entries()).map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count);
  }, [data]);

  const okrugStats = useMemo(() => {
    if (!data) return [];
    const map = new Map<string, number>();
    data.cities.forEach(c => map.set(c.okrug, (map.get(c.okrug) || 0) + c.order_count));
    return Array.from(map.entries()).map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count);
  }, [data]);

  const handleSort = (col: 'order_count' | 'city') => {
    if (sortBy === col) setSortDesc(!sortDesc);
    else { setSortBy(col); setSortDesc(col === 'order_count'); }
  };

  const monthNames = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

  return (
    <div className="page-container">
      <style>{`
        .geo-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 1rem; margin-bottom: 1.5rem; }
        .geo-header h1 { font-size: 1.5rem; font-weight: 700; margin: 0; }
        .geo-stats { display: flex; gap: 2rem; }
        .geo-stat { text-align: center; }
        .geo-stat-value { font-size: 1.8rem; font-weight: 700; color: var(--accent, #6366f1); }
        .geo-stat-label { font-size: 0.75rem; color: var(--text-secondary, #888); text-transform: uppercase; }
        .geo-filters { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; margin-bottom: 1.5rem; }
        .geo-filters select, .geo-filters input { padding: 0.5rem 0.75rem; border-radius: 8px; border: 1px solid var(--border, #ddd); background: var(--bg-card, #fff); color: inherit; font-size: 0.85rem; }
        .geo-layout { display: grid; grid-template-columns: 1fr 300px; gap: 1.5rem; }
        @media (max-width: 900px) { .geo-layout { grid-template-columns: 1fr; } }
        .chart-bars { display: flex; align-items: flex-end; gap: 2px; height: 120px; }
        .chart-bar { flex: 1; border-radius: 3px 3px 0 0; background: var(--accent, #6366f1); opacity: 0.8; min-width: 4px; cursor: pointer; }
        .chart-bar:hover { opacity: 1; }
        .chart-label { display: flex; justify-content: space-between; font-size: 0.7rem; color: var(--text-secondary, #888); margin-top: 4px; }
        .cal-month { margin-bottom: 1rem; }
        .cal-month-title { font-weight: 600; font-size: 0.85rem; margin-bottom: 0.5rem; }
        .cal-header, .cal-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; }
        .cal-cell { width: 32px; height: 28px; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; border-radius: 4px; cursor: default; color: var(--text-secondary, #888); }
        .cal-day-name { font-weight: 600; font-size: 0.65rem; }
        .cal-has-data { background: rgba(99, 102, 241, 0.2); color: inherit; cursor: pointer; font-weight: 600; }
        .cal-has-data:hover { background: rgba(99, 102, 241, 0.4); }
        .cal-in-range.cal-has-data { background: var(--accent, #6366f1); color: white; }
        .geo-search { display: flex; gap: 0.75rem; margin-bottom: 1rem; align-items: center; }
        .geo-search input { flex: 1; padding: 0.5rem 0.75rem; border-radius: 8px; border: 1px solid var(--border, #ddd); background: var(--bg-card, #fff); color: inherit; font-size: 0.85rem; }
        .city-table { width: 100%; border-collapse: collapse; }
        .city-table th { text-align: left; padding: 0.6rem 0.75rem; font-size: 0.75rem; text-transform: uppercase; color: var(--text-secondary, #888); border-bottom: 1px solid var(--border, #333); cursor: pointer; }
        .city-table td { padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--border, #333); font-size: 0.85rem; }
        .city-table tr:hover td { background: rgba(99, 102, 241, 0.05); }
        .city-count { font-weight: 600; color: var(--accent, #6366f1); }
        .city-bar { height: 4px; border-radius: 2px; background: var(--accent, #6366f1); opacity: 0.3; margin-top: 4px; }
        .reset-btn { padding: 0.4rem 0.75rem; border-radius: 8px; border: 1px solid var(--border, #333); background: transparent; color: var(--text-secondary, #888); font-size: 0.8rem; cursor: pointer; }
        .hbar-charts { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-bottom: 1rem; }
        @media (max-width: 900px) { .hbar-charts { grid-template-columns: 1fr; } }
        .hbar-row { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 4px; font-size: 0.8rem; }
        .hbar-label { min-width: 140px; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: inherit; text-align: right; font-weight: 500; }
        .hbar-track { flex: 1; height: 20px; background: rgba(99, 102, 241, 0.08); border-radius: 4px; overflow: hidden; position: relative; }
        .hbar-fill { height: 100%; border-radius: 4px; transition: width 0.3s; }
        .hbar-value { min-width: 50px; font-weight: 600; font-size: 0.75rem; color: var(--text-secondary, #888); }
      `}</style>

      <div className="geo-header">
        <h1>🗺️ Куда заказывают</h1>
        {data && (
          <div className="geo-stats">
            <div className="geo-stat">
              <div className="geo-stat-value">{formatNumber(data.totals.total_orders, 0)}</div>
              <div className="geo-stat-label">Заказов</div>
            </div>
            <div className="geo-stat">
              <div className="geo-stat-value">{formatNumber(data.totals.unique_cities, 0)}</div>
              <div className="geo-stat-label">Городов</div>
            </div>
          </div>
        )}
      </div>

      <div className="geo-filters">
        <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
        <span style={{ color: '#888' }}>—</span>
        <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} />
        <select value={brand} onChange={e => setBrand(e.target.value)}>
          <option value="">Все бренды</option>
          {data?.filters.brands.map(b => <option key={b} value={b}>{b}</option>)}
        </select>
        <select value={category} onChange={e => setCategory(e.target.value)}>
          <option value="">Все категории</option>
          {data?.filters.categories.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={article} onChange={e => setArticle(e.target.value)}>
          <option value="">Все артикулы</option>
          {data?.filters.articles.map(a => <option key={a} value={a}>{a}</option>)}
        </select>
        {(brand || category || article) && (
          <button className="reset-btn" onClick={() => { setBrand(''); setCategory(''); setArticle(''); }}>✕ Сбросить</button>
        )}
      </div>

      {loading && <div style={{ padding: 40, textAlign: 'center' }}>Загрузка...</div>}
      {error && <div style={{ padding: 20, color: '#ef4444', textAlign: 'center' }}>{error}</div>}

      {!loading && !error && data && (
        <>
          {/* Region & Okrug horizontal bar charts */}
          <div className="hbar-charts">
            <div className="glass-card" style={{ padding: '1rem' }}>
              <div style={{ fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.75rem' }}>📍 По регионам</div>
              {regionStats.slice(0, 15).map(r => {
                const maxR = regionStats[0]?.count || 1;
                return (
                  <div key={r.name} className="hbar-row">
                    <div className="hbar-label" title={r.name}>{r.name}</div>
                    <div className="hbar-track">
                      <div className="hbar-fill" style={{ width: `${(r.count / maxR) * 100}%`, background: 'linear-gradient(90deg, #6366f1, #818cf8)' }} />
                    </div>
                    <div className="hbar-value">{formatNumber(r.count, 0)}</div>
                  </div>
                );
              })}
              {regionStats.length > 15 && (
                <div style={{ fontSize: '0.7rem', color: '#888', marginTop: 4 }}>и ещё {regionStats.length - 15} регионов</div>
              )}
            </div>
            <div className="glass-card" style={{ padding: '1rem' }}>
              <div style={{ fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.75rem' }}>🗺️ По округам</div>
              {okrugStats.map(o => {
                const maxO = okrugStats[0]?.count || 1;
                const colors = ['#8b5cf6', '#6366f1', '#3b82f6', '#06b6d4', '#10b981', '#f59e0b', '#ef4444', '#ec4899', '#f97316'];
                const idx = okrugStats.indexOf(o);
                const color = colors[idx % colors.length];
                return (
                  <div key={o.name} className="hbar-row">
                    <div className="hbar-label" title={o.name}>{o.name}</div>
                    <div className="hbar-track">
                      <div className="hbar-fill" style={{ width: `${(o.count / maxO) * 100}%`, background: color }} />
                    </div>
                    <div className="hbar-value">{formatNumber(o.count, 0)}</div>
                  </div>
                );
              })}
            </div>
          </div>

        <div className="geo-layout">
          <div>
            {data.daily.length > 0 && (
              <div className="glass-card" style={{ padding: '1rem', marginBottom: '1rem' }}>
                <div style={{ fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.75rem' }}>Заказы по дням</div>
                <div className="chart-bars">
                  {data.daily.map(d => (
                    <div key={d.date} className="chart-bar"
                      style={{ height: `${Math.max((d.count / chartMax) * 100, 2)}%` }}
                      title={`${formatDate(d.date)}: ${formatNumber(d.count, 0)}`}
                      onClick={() => { setDateFrom(d.date); setDateTo(d.date); }}
                    />
                  ))}
                </div>
                <div className="chart-label">
                  <span>{formatDate(data.daily[0].date)}</span>
                  <span>{formatDate(data.daily[data.daily.length - 1].date)}</span>
                </div>
              </div>
            )}

            <div className="geo-search">
              <input placeholder="Поиск по городу, региону..." value={search} onChange={e => setSearch(e.target.value)} />
              <button className="btn-secondary" onClick={() => {
                if (!cities.length) return;
                exportToExcel(cities.map(c => ({ 'Город': c.city, 'Регион': c.region, 'Округ': c.okrug, 'Заказы': c.order_count })), 'order_geography');
              }}>📥 Excel</button>
            </div>

            <div className="glass-card" style={{ overflow: 'auto', maxHeight: 600 }}>
              {cities.length === 0 ? (
                <div style={{ textAlign: 'center', padding: '2rem', color: '#888' }}>Нет данных за выбранный период</div>
              ) : (
                <table className="city-table">
                  <thead>
                    <tr>
                      <th style={{ width: 40 }}>#</th>
                      <th onClick={() => handleSort('city')}>Город {sortBy === 'city' ? (sortDesc ? '↓' : '↑') : ''}</th>
                      <th>Регион</th>
                      <th>Округ</th>
                      <th onClick={() => handleSort('order_count')} style={{ textAlign: 'right' }}>
                        Заказы {sortBy === 'order_count' ? (sortDesc ? '↓' : '↑') : ''}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {cities.slice(0, 200).map((c, i) => {
                      const maxCount = cities[0]?.order_count || 1;
                      return (
                        <tr key={`${c.city}-${i}`}>
                          <td style={{ color: '#888', fontSize: '0.75rem' }}>{i + 1}</td>
                          <td style={{ fontWeight: 500 }}>{c.city}</td>
                          <td style={{ color: '#888', fontSize: '0.8rem' }}>{c.region}</td>
                          <td style={{ color: '#888', fontSize: '0.8rem' }}>{c.okrug}</td>
                          <td style={{ textAlign: 'right' }}>
                            <span className="city-count">{formatNumber(c.order_count, 0)}</span>
                            <div className="city-bar" style={{ width: `${(c.order_count / maxCount) * 100}%` }} />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          <div>
            <div className="glass-card" style={{ padding: '1rem' }}>
              <div style={{ fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.75rem' }}>📅 Календарь данных</div>
              {calendarMonths.map(cm => {
                const emptyCells: React.ReactNode[] = [];
                for (let i = 1; i < cm.startDay; i++) {
                  emptyCells.push(<div key={`e${i}`} className="cal-cell" />);
                }
                const dayCells: React.ReactNode[] = [];
                for (let d = 1; d <= cm.daysInMonth; d++) {
                  const ds = `${cm.year}-${String(cm.month).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
                  const has = calendarDates.has(ds);
                  const inR = ds >= dateFrom && ds <= dateTo;
                  dayCells.push(
                    <div key={d}
                      className={`cal-cell${has ? ' cal-has-data' : ''}${inR ? ' cal-in-range' : ''}`}
                      onClick={() => { if (has) { setDateFrom(ds); setDateTo(ds); } }}
                      title={has ? `${ds} — есть данные` : ds}
                    >{d}</div>
                  );
                }
                return (
                  <div key={cm.key} className="cal-month">
                    <div className="cal-month-title">{monthNames[cm.month - 1]} {cm.year}</div>
                    <div className="cal-header">
                      {['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'].map(n => (
                        <div key={n} className="cal-cell cal-day-name">{n}</div>
                      ))}
                    </div>
                    <div className="cal-grid">{emptyCells}{dayCells}</div>
                  </div>
                );
              })}
              {dateFrom === dateTo && dateFrom && (
                <button className="reset-btn" style={{ width: '100%', marginTop: '0.5rem' }}
                  onClick={() => {
                    const now = new Date();
                    setDateTo(now.toISOString().slice(0, 10));
                    setDateFrom(new Date(now.getTime() - 14 * 86400000).toISOString().slice(0, 10));
                  }}>Показать все 14 дней</button>
              )}
            </div>
          </div>
        </div>
        </>
      )}
    </div>
  );
}
