'use client';

import { useState, useEffect, useCallback, useMemo } from 'react';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import type { OrderGeographyResponse, CityOrderCount } from '@/types/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';

export default function OrderGeographyPage() {
  const params = useParams();
  const slug = params.slug as string;

  const [data, setData] = useState<OrderGeographyResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Date range — default last 14 days
  const today = new Date().toISOString().slice(0, 10);
  const twoWeeksAgo = new Date(Date.now() - 14 * 86400000).toISOString().slice(0, 10);
  const [dateFrom, setDateFrom] = useState(twoWeeksAgo);
  const [dateTo, setDateTo] = useState(today);

  // Filters
  const [brand, setBrand] = useState('');
  const [category, setCategory] = useState('');
  const [article, setArticle] = useState('');

  // Sort
  const [sortBy, setSortBy] = useState<'order_count' | 'city'>('order_count');
  const [sortDesc, setSortDesc] = useState(true);

  // Search
  const [search, setSearch] = useState('');

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      setError('');
      const result = await api.getOrderGeography(
        dateFrom, dateTo,
        brand || undefined, category || undefined, article || undefined,
      );
      setData(result);
    } catch (err: any) {
      setError(err.message || 'Ошибка загрузки');
    } finally {
      setLoading(false);
    }
  }, [dateFrom, dateTo, brand, category, article]);

  useEffect(() => { loadData(); }, [loadData]);

  // Sorted + filtered cities
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

  // Chart max for scaling
  const chartMax = useMemo(() => {
    if (!data?.daily.length) return 1;
    return Math.max(...data.daily.map(d => d.count));
  }, [data]);

  // Calendar data
  const calendarDates = useMemo(() => {
    if (!data) return new Set<string>();
    return new Set(data.dates);
  }, [data]);

  const handleSort = (col: 'order_count' | 'city') => {
    if (sortBy === col) setSortDesc(!sortDesc);
    else { setSortBy(col); setSortDesc(col === 'order_count'); }
  };

  const handleExcel = () => {
    if (!cities.length) return;
    exportToExcel(cities.map(c => ({
      'Город': c.city,
      'Регион': c.region,
      'Округ': c.okrug,
      'Заказы': c.order_count,
    })), 'order_geography');
  };

  // Mini calendar component
  const renderCalendar = () => {
    if (!data?.dates.length) return null;
    const allDates = data.dates.map(d => new Date(d));
    const months = new Map<string, Date[]>();
    allDates.forEach(d => {
      const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
      if (!months.has(key)) months.set(key, []);
      months.get(key)!.push(d);
    });

    const monthNames = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
      'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

    return Array.from(months.entries()).map(([key, dates]) => {
      const [year, month] = key.split('-').map(Number);
      const firstDay = new Date(year, month - 1, 1);
      const daysInMonth = new Date(year, month, 0).getDate();
      let startDay = firstDay.getDay(); // 0=Sun
      if (startDay === 0) startDay = 7; // Mon=1

      const cells: JSX.Element[] = [];
      // Empty cells before first day
      for (let i = 1; i < startDay; i++) {
        cells.push(<div key={`e${i}`} className="cal-cell cal-empty" />);
      }
      for (let d = 1; d <= daysInMonth; d++) {
        const dateStr = `${year}-${String(month).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
        const hasData = calendarDates.has(dateStr);
        const isInRange = dateStr >= dateFrom && dateStr <= dateTo;
        cells.push(
          <div
            key={d}
            className={`cal-cell${hasData ? ' cal-has-data' : ''}${isInRange ? ' cal-in-range' : ''}`}
            onClick={() => {
              if (hasData) {
                setDateFrom(dateStr);
                setDateTo(dateStr);
              }
            }}
            title={hasData ? `${dateStr} — есть данные` : dateStr}
          >
            {d}
          </div>
        );
      }

      return (
        <div key={key} className="cal-month">
          <div className="cal-month-title">{monthNames[month - 1]} {year}</div>
          <div className="cal-header">
            {['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'].map(d => (
              <div key={d} className="cal-cell cal-day-name">{d}</div>
            ))}
          </div>
          <div className="cal-grid">{cells}</div>
        </div>
      );
    });
  };

  if (loading) return <div className="page-loading">Загрузка...</div>;
  if (error) return <div className="error-message">{error}</div>;

  return (
    <div className="page-container">
      <style>{`
        .geo-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 1rem; margin-bottom: 1.5rem; }
        .geo-header h1 { font-size: 1.5rem; font-weight: 700; margin: 0; }
        .geo-stats { display: flex; gap: 2rem; }
        .geo-stat { text-align: center; }
        .geo-stat-value { font-size: 1.8rem; font-weight: 700; color: var(--accent); }
        .geo-stat-label { font-size: 0.75rem; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.05em; }
        .geo-filters { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; margin-bottom: 1.5rem; }
        .geo-filters select, .geo-filters input { padding: 0.5rem 0.75rem; border-radius: 8px; border: 1px solid var(--border); background: var(--bg-card); color: var(--text-primary); font-size: 0.85rem; min-width: 140px; }
        .geo-filters input[type="date"] { min-width: 130px; }
        .geo-layout { display: grid; grid-template-columns: 1fr 300px; gap: 1.5rem; }
        @media (max-width: 900px) { .geo-layout { grid-template-columns: 1fr; } }
        .geo-chart { padding: 1rem; }
        .chart-bars { display: flex; align-items: flex-end; gap: 2px; height: 120px; }
        .chart-bar { flex: 1; border-radius: 3px 3px 0 0; background: var(--accent); opacity: 0.8; transition: opacity 0.2s; min-width: 4px; cursor: pointer; position: relative; }
        .chart-bar:hover { opacity: 1; }
        .chart-bar:hover::after { content: attr(data-tooltip); position: absolute; bottom: 100%; left: 50%; transform: translateX(-50%); background: var(--bg-card); color: var(--text-primary); padding: 4px 8px; border-radius: 6px; font-size: 0.7rem; white-space: nowrap; border: 1px solid var(--border); z-index: 10; }
        .chart-label { display: flex; justify-content: space-between; font-size: 0.7rem; color: var(--text-secondary); margin-top: 4px; }
        .cal-month { margin-bottom: 1rem; }
        .cal-month-title { font-weight: 600; font-size: 0.85rem; margin-bottom: 0.5rem; color: var(--text-primary); }
        .cal-header, .cal-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; }
        .cal-cell { width: 32px; height: 28px; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; border-radius: 4px; cursor: default; color: var(--text-secondary); }
        .cal-day-name { font-weight: 600; font-size: 0.65rem; color: var(--text-secondary); }
        .cal-has-data { background: rgba(99, 102, 241, 0.2); color: var(--text-primary); cursor: pointer; font-weight: 600; }
        .cal-has-data:hover { background: rgba(99, 102, 241, 0.4); }
        .cal-in-range.cal-has-data { background: var(--accent); color: white; }
        .geo-search { display: flex; gap: 0.75rem; margin-bottom: 1rem; align-items: center; }
        .geo-search input { flex: 1; padding: 0.5rem 0.75rem; border-radius: 8px; border: 1px solid var(--border); background: var(--bg-card); color: var(--text-primary); font-size: 0.85rem; }
        .city-table { width: 100%; border-collapse: collapse; }
        .city-table th { text-align: left; padding: 0.6rem 0.75rem; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-secondary); border-bottom: 1px solid var(--border); cursor: pointer; user-select: none; }
        .city-table th:hover { color: var(--text-primary); }
        .city-table td { padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--border); font-size: 0.85rem; }
        .city-table tr:hover td { background: rgba(99, 102, 241, 0.05); }
        .city-count { font-weight: 600; color: var(--accent); }
        .city-bar { height: 4px; border-radius: 2px; background: var(--accent); opacity: 0.3; margin-top: 4px; }
        .reset-btn { padding: 0.4rem 0.75rem; border-radius: 8px; border: 1px solid var(--border); background: transparent; color: var(--text-secondary); font-size: 0.8rem; cursor: pointer; }
        .reset-btn:hover { background: var(--bg-card); color: var(--text-primary); }
      `}</style>

      <div className="geo-header">
        <h1>🗺️ Куда заказывают</h1>
        {data && (
          <div className="geo-stats">
            <div className="geo-stat">
              <div className="geo-stat-value">{formatNumber(data.totals.total_orders)}</div>
              <div className="geo-stat-label">Заказов</div>
            </div>
            <div className="geo-stat">
              <div className="geo-stat-value">{formatNumber(data.totals.unique_cities)}</div>
              <div className="geo-stat-label">Городов</div>
            </div>
          </div>
        )}
      </div>

      {/* Filters */}
      <div className="geo-filters">
        <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
        <span style={{ color: 'var(--text-secondary)' }}>—</span>
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
          <button className="reset-btn" onClick={() => { setBrand(''); setCategory(''); setArticle(''); }}>
            ✕ Сбросить
          </button>
        )}
      </div>

      <div className="geo-layout">
        {/* Left: Table + Chart */}
        <div>
          {/* Mini bar chart */}
          {data && data.daily.length > 0 && (
            <div className="glass-card geo-chart">
              <div style={{ fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.75rem' }}>
                Заказы по дням
              </div>
              <div className="chart-bars">
                {data.daily.map(d => (
                  <div
                    key={d.date}
                    className="chart-bar"
                    style={{ height: `${Math.max((d.count / chartMax) * 100, 2)}%` }}
                    data-tooltip={`${formatDate(d.date)}: ${formatNumber(d.count)}`}
                    onClick={() => { setDateFrom(d.date); setDateTo(d.date); }}
                  />
                ))}
              </div>
              <div className="chart-label">
                <span>{data.daily.length > 0 ? formatDate(data.daily[0].date) : ''}</span>
                <span>{data.daily.length > 0 ? formatDate(data.daily[data.daily.length - 1].date) : ''}</span>
              </div>
            </div>
          )}

          {/* Search + Export */}
          <div className="geo-search">
            <input
              placeholder="Поиск по городу, региону..."
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
            <button className="btn-secondary" onClick={handleExcel}>📥 Excel</button>
          </div>

          {/* Cities table */}
          <div className="glass-card" style={{ overflow: 'auto', maxHeight: '600px' }}>
            {cities.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-secondary)' }}>
                Нет данных за выбранный период
              </div>
            ) : (
              <table className="city-table">
                <thead>
                  <tr>
                    <th style={{ width: 40 }}>#</th>
                    <th onClick={() => handleSort('city')}>
                      Город {sortBy === 'city' ? (sortDesc ? '↓' : '↑') : ''}
                    </th>
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
                      <tr key={`${c.city}-${c.region}`}>
                        <td style={{ color: 'var(--text-secondary)', fontSize: '0.75rem' }}>{i + 1}</td>
                        <td style={{ fontWeight: 500 }}>{c.city}</td>
                        <td style={{ color: 'var(--text-secondary)', fontSize: '0.8rem' }}>{c.region}</td>
                        <td style={{ color: 'var(--text-secondary)', fontSize: '0.8rem' }}>{c.okrug}</td>
                        <td style={{ textAlign: 'right' }}>
                          <span className="city-count">{formatNumber(c.order_count)}</span>
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

        {/* Right: Calendar */}
        <div>
          <div className="glass-card" style={{ padding: '1rem' }}>
            <div style={{ fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.75rem' }}>
              📅 Календарь данных
            </div>
            {renderCalendar()}
            {dateFrom === dateTo && dateFrom !== twoWeeksAgo && (
              <button
                className="reset-btn"
                style={{ width: '100%', marginTop: '0.5rem' }}
                onClick={() => { setDateFrom(twoWeeksAgo); setDateTo(today); }}
              >
                Показать все 14 дней
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
