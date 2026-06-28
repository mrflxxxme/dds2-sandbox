'use client';
import React, { useEffect, useState, useCallback, useMemo } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';
import type {
    DashboardSummary, BalanceAccount, DashboardFunnelSummary,
    DailyCashflowRow, ExpenseCategoryPie, ExpenseTypeGroup, IncomeCounterparty, IncomeTypeSlice,
    DashboardTransaction, CategoryCounterparty,
    ChartTooltipProps, ChartTooltipPayloadItem, PieLabelProps,
} from '@/types/api';
import {
    XAxis, YAxis, CartesianGrid, Tooltip, Legend,
    ResponsiveContainer, PieChart, Pie, Cell,
    AreaChart, Area, BarChart, Bar,
} from 'recharts';
import KpiCard from '@/components/KpiCard';

/* ─── Цвета ────────────────────────────────────────────────────── */
const C = { income: '#22c55e', expense: '#ef4444', accent: '#a78bfa', warning: '#f59e0b', info: '#3b82f6', muted: '#64748b' };
const PIE_COLORS = ['#a78bfa','#f472b6','#38bdf8','#22c55e','#f59e0b','#ef4444','#6366f1','#14b8a6','#e879f9','#fb923c'];
/* Цвета типов прихода (маркетплейс — выручка, финансирование — займы, прочее) */
const INCOME_TYPE_COLORS: Record<string,string> = { marketplace: '#22c55e', financing: '#3b82f6', other: '#94a3b8' };

/* ─── Period ───────────────────────────────────────────────────── */
type PeriodKey = 'month'|'prev_month'|'7d'|'30d'|'90d'|'all'|'custom';
function getPeriodDates(key: PeriodKey, cf?: string, ct?: string) {
    const t = new Date(), y = t.getFullYear(), m = String(t.getMonth()+1).padStart(2,'0'), d = String(t.getDate()).padStart(2,'0'), ts = `${y}-${m}-${d}`;
    switch(key) {
        case 'month': return { from:`${y}-${m}-01`, to: ts, label:'Текущий месяц' };
        case 'prev_month': { const p=new Date(y,t.getMonth()-1,1),e=new Date(y,t.getMonth(),0); return { from:`${p.getFullYear()}-${String(p.getMonth()+1).padStart(2,'0')}-01`, to:`${p.getFullYear()}-${String(p.getMonth()+1).padStart(2,'0')}-${String(e.getDate()).padStart(2,'0')}`, label:'Прошлый месяц' }; }
        case '7d': { const s=new Date(t); s.setDate(s.getDate()-6); return { from:`${s.getFullYear()}-${String(s.getMonth()+1).padStart(2,'0')}-${String(s.getDate()).padStart(2,'0')}`, to:ts, label:'7 дней' }; }
        case '30d': { const s=new Date(t); s.setDate(s.getDate()-29); return { from:`${s.getFullYear()}-${String(s.getMonth()+1).padStart(2,'0')}-${String(s.getDate()).padStart(2,'0')}`, to:ts, label:'30 дней' }; }
        case '90d': { const s=new Date(t); s.setDate(s.getDate()-89); return { from:`${s.getFullYear()}-${String(s.getMonth()+1).padStart(2,'0')}-${String(s.getDate()).padStart(2,'0')}`, to:ts, label:'90 дней' }; }
        case 'custom': return { from: cf||ts, to: ct||ts, label:'Произвольный' };
        case 'all': return { from:'2020-01-01', to:ts, label:'Всё время' };
    }
}

/* ─── Helpers ──────────────────────────────────────────────────── */
function fmtK(v: number) { if(Math.abs(v)>=1e6) return (v/1e6).toFixed(1)+'М'; if(Math.abs(v)>=1e3) return (v/1e3).toFixed(0)+'К'; return v.toFixed(0); }
function shortDay(iso: string) { return new Date(iso+'T00:00:00').toLocaleDateString('ru-RU',{day:'numeric',month:'short'}); }
function truncate(s: string, n: number) { return s.length>n ? s.slice(0,n)+'…' : s; }

function ChartTooltip({active,payload,label}:ChartTooltipProps) {
    if(!active||!payload?.length) return null;
    return (<div style={{background:'rgba(15,17,26,0.95)',border:'1px solid rgba(255,255,255,0.1)',borderRadius:8,padding:'10px 14px',fontSize:13}}>
        <div style={{color:'#94a3b8',marginBottom:4}}>{label}</div>
        {payload.map((p:ChartTooltipPayloadItem,i:number)=>(<div key={i} style={{color:p.color||p.fill,fontWeight:600}}>{p.name}: {formatNumber(p.value)}</div>))}
    </div>);
}
function renderPieLabel({cx,cy,midAngle,outerRadius,name,percent,index}:PieLabelProps) {
    const R=Math.PI/180,r=outerRadius+20,x=cx+r*Math.cos(-midAngle*R),y=cy+r*Math.sin(-midAngle*R);
    if(percent<0.03) return null;
    return <text x={x} y={y} fill={PIE_COLORS[index%PIE_COLORS.length]} textAnchor={x>cx?'start':'end'} dominantBaseline="central" fontSize={11} fontWeight={600}>{truncate(name,12)} {(percent*100).toFixed(0)}%</text>;
}
/* Тултип для стэка прихода по типам — скрывает нулевые серии, добавляет «Итого» */
function IncomeTypeTooltip({active,payload,label}:ChartTooltipProps) {
    if(!active||!payload?.length) return null;
    const items=payload.filter((p:ChartTooltipPayloadItem)=>Number(p.value)>0);
    if(!items.length) return null;
    const total=items.reduce((s:number,p:ChartTooltipPayloadItem)=>s+Number(p.value),0);
    return (<div style={{background:'rgba(15,17,26,0.95)',border:'1px solid rgba(255,255,255,0.1)',borderRadius:8,padding:'10px 14px',fontSize:13}}>
        <div style={{color:'#94a3b8',marginBottom:4}}>{label}</div>
        {items.map((p:ChartTooltipPayloadItem,i:number)=>(<div key={i} style={{color:p.color||p.fill,fontWeight:600}}>{p.name}: {formatNumber(p.value)} ₽</div>))}
        {items.length>1&&<div style={{color:'#e2e8f0',fontWeight:600,marginTop:4,borderTop:'1px solid rgba(255,255,255,0.1)',paddingTop:4}}>Итого: {formatNumber(total)} ₽</div>}
    </div>);
}
/* KpiCard imported from @/components/KpiCard */

/* ─── Inline transaction mini-table ────────────────────────────── */
function InlineTxnRows({txnList,txnTotal,txnFlow,onFlowChange,filterLoading,colSpan}:{
    txnList:DashboardTransaction[];txnTotal:number;txnFlow:string;onFlowChange:(f:'all'|'income'|'expense')=>void;filterLoading:boolean;colSpan:number;
}) {
    return (
        <tr><td colSpan={colSpan} style={{padding:0,background:'rgba(255,255,255,0.02)'}}>
            <div style={{padding:'12px 16px',borderTop:'1px solid rgba(255,255,255,0.06)'}}>
                <div style={{display:'flex',gap:4,marginBottom:10,alignItems:'center'}}>
                    {(['all','income','expense'] as const).map(f=>(
                        <button key={f} className={`btn btn-sm ${txnFlow===f?'btn-primary':'btn-secondary'}`}
                            onClick={e=>{e.stopPropagation();onFlowChange(f);}}
                            style={txnFlow===f?{background:f==='income'?C.income:f==='expense'?C.expense:'var(--color-accent)',borderColor:'transparent',fontSize:11,padding:'2px 8px'}:{fontSize:11,padding:'2px 8px'}}
                        >{f==='all'?'Все':f==='income'?'Приходы':'Расходы'}</button>
                    ))}
                    <button className="btn btn-sm btn-secondary" style={{fontSize:11,padding:'2px 8px',marginLeft:'auto'}}
                        onClick={e=>{e.stopPropagation();exportToExcel(txnList,'transactions');}}>📥 Excel</button>
                    <span style={{fontSize:11,color:'#64748b'}}>{txnTotal} шт.</span>
                </div>
                {filterLoading?(<div style={{padding:10,textAlign:'center',color:'#94a3b8',fontSize:13}}>⏳ Загрузка...</div>
                ):txnList.length===0?(<div style={{padding:10,textAlign:'center',color:'#94a3b8',fontSize:13}}>Нет операций</div>
                ):(
                    <table style={{width:'100%',borderCollapse:'collapse'}}>
                        <thead><tr style={{fontSize:11,color:'#64748b',textTransform:'uppercase' as const,letterSpacing:'0.05em'}}>
                            <th style={{padding:'4px 8px',textAlign:'left',fontWeight:500}}>Дата</th>
                            <th style={{padding:'4px 8px',textAlign:'left',fontWeight:500}}>Контрагент</th>
                            <th style={{padding:'4px 8px',textAlign:'right',fontWeight:500}}>Приход</th>
                            <th style={{padding:'4px 8px',textAlign:'right',fontWeight:500}}>Расход</th>
                            <th style={{padding:'4px 8px',textAlign:'left',fontWeight:500}}>Назначение</th>
                        </tr></thead>
                        <tbody>{txnList.map((t:DashboardTransaction,i:number)=>(
                            <tr key={i} style={{borderTop:'1px solid rgba(255,255,255,0.04)',fontSize:12}}>
                                <td style={{padding:'5px 8px',whiteSpace:'nowrap',color:'#94a3b8'}}>{formatDate(t.date)}</td>
                                <td style={{padding:'5px 8px',maxWidth:180,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{t.counterparty||'—'}</td>
                                <td style={{padding:'5px 8px',textAlign:'right',fontWeight:600,color:t.income>0?C.income:'#475569'}}>{t.income>0?formatNumber(t.income):'—'}</td>
                                <td style={{padding:'5px 8px',textAlign:'right',fontWeight:600,color:t.expense>0?C.expense:'#475569'}}>{t.expense>0?formatNumber(t.expense):'—'}</td>
                                <td style={{padding:'5px 8px',color:'#94a3b8',fontSize:11}}>{t.purpose||'—'}</td>
                            </tr>
                        ))}</tbody>
                    </table>
                )}
                {txnTotal>50&&(<div style={{padding:'6px 8px',fontSize:11,color:'#64748b',borderTop:'1px solid rgba(255,255,255,0.04)'}}>Показано 50 из {txnTotal}</div>)}
            </div>
        </td></tr>
    );
}

/* ═══════════════════════════════════════════════════════════════ */
export default function DashboardPage() {
    const params=useParams();
    const slug=(params?.slug as string)||'';
    const [data,setData]=useState<DashboardSummary|null>(null);
    const [balance,setBalance]=useState<BalanceAccount[]>([]);
    const [funnel,setFunnel]=useState<DashboardFunnelSummary|null>(null);
    const [loading,setLoading]=useState(true);
    const [error,setError]=useState('');
    const [period,setPeriod]=useState<PeriodKey>('month');
    const [customFrom,setCustomFrom]=useState('');
    const [customTo,setCustomTo]=useState('');
    const [expandedType,setExpandedType]=useState<string|null>(null);

    /* ─── Filter state ────────────────────────────────────────── */
    const [selectedCp,setSelectedCp]=useState<IncomeCounterparty|null>(null);
    const [selectedExpCat,setSelectedExpCat]=useState<string|null>(null);
    const [filteredDaily,setFilteredDaily]=useState<DailyCashflowRow[]|null>(null);
    const [filterLoading,setFilterLoading]=useState(false);

    // Income cp → transactions
    const [cpTxnList,setCpTxnList]=useState<DashboardTransaction[]>([]);
    const [cpTxnTotal,setCpTxnTotal]=useState(0);
    const [cpTxnFlow,setCpTxnFlow]=useState<'all'|'income'|'expense'>('all');

    // Expense category → counterparties (level 1)
    const [catCps,setCatCps]=useState<CategoryCounterparty[]>([]);
    const [catCpsLoading,setCatCpsLoading]=useState(false);
    // Expense category → selected counterparty → transactions (level 2)
    const [selectedCatCp,setSelectedCatCp]=useState<CategoryCounterparty|null>(null);
    const [catCpTxnList,setCatCpTxnList]=useState<DashboardTransaction[]>([]);
    const [catCpTxnTotal,setCatCpTxnTotal]=useState(0);
    const [catCpTxnFlow,setCatCpTxnFlow]=useState<'all'|'income'|'expense'>('all');
    const [catCpTxnLoading,setCatCpTxnLoading]=useState(false);

    const {from:dateFrom,to:dateTo,label:periodLabel}=getPeriodDates(period,customFrom,customTo);

    const loadData=useCallback(async()=>{
        try { setLoading(true);setError('');
            const {from,to}=getPeriodDates(period,customFrom,customTo);
            const [summary,bal,fun]=await Promise.all([
                api.getDashboardSummary(from,to),api.getBalance(),api.getFunnelSummary(from,to).catch(()=>null),
            ]);
            setData(summary);setBalance(bal);setFunnel(fun);resetFilters();
        } catch(e:unknown){setError(e instanceof Error ? e.message : 'Ошибка загрузки');} finally{setLoading(false);}
    },[period,customFrom,customTo]);
    useEffect(()=>{loadData();},[loadData]);

    /* ─── Reset ────────────────────────────────────────────────── */
    const resetFilters=useCallback(()=>{
        setSelectedCp(null);setSelectedExpCat(null);setFilteredDaily(null);
        setCpTxnList([]);setCpTxnTotal(0);setCpTxnFlow('all');
        setCatCps([]);setSelectedCatCp(null);setCatCpTxnList([]);setCatCpTxnTotal(0);setCatCpTxnFlow('all');
    },[]);

    /* ─── Income CP click → chart + transactions ──────────────── */
    const handleCpClick=useCallback(async(cp:IncomeCounterparty)=>{
        if(selectedCp?.name===cp.name){resetFilters();return;}
        setSelectedCp(cp);setSelectedExpCat(null);setCatCps([]);setSelectedCatCp(null);setCpTxnFlow('all');
        setFilterLoading(true);
        try {
            const {from,to}=getPeriodDates(period,customFrom,customTo);
            const [daily,txns]=await Promise.all([
                api.getDailyFiltered(from,to,cp.key,undefined),
                api.getFilteredTransactions(from,to,{cpKey:cp.key,limit:50}),
            ]);
            setFilteredDaily(daily);setCpTxnList(txns.items);setCpTxnTotal(txns.total);
        } catch{setFilteredDaily(null);setCpTxnList([]);}
        finally{setFilterLoading(false);}
    },[selectedCp,period,customFrom,customTo,resetFilters]);

    const handleCpFlowChange=useCallback(async(flow:'all'|'income'|'expense')=>{
        setCpTxnFlow(flow); setFilterLoading(true);
        try {
            const {from,to}=getPeriodDates(period,customFrom,customTo);
            const txns=await api.getFilteredTransactions(from,to,{cpKey:selectedCp?.key,flow,limit:50});
            setCpTxnList(txns.items);setCpTxnTotal(txns.total);
        } catch{} finally{setFilterLoading(false);}
    },[selectedCp,period,customFrom,customTo]);

    /* ─── Expense category click → counterparties list (level 1) ─ */
    const handleExpCatClick=useCallback(async(catName:string)=>{
        if(selectedExpCat===catName){resetFilters();return;}
        setSelectedExpCat(catName);setSelectedCp(null);setCpTxnList([]);
        setSelectedCatCp(null);setCatCpTxnList([]);setCatCpTxnFlow('all');
        setCatCpsLoading(true);setFilterLoading(true);
        try {
            const {from,to}=getPeriodDates(period,customFrom,customTo);
            const [daily,cps]=await Promise.all([
                api.getDailyFiltered(from,to,undefined,catName),
                api.getCategoryCounterparties(from,to,catName),
            ]);
            setFilteredDaily(daily);setCatCps(cps);
        } catch{setFilteredDaily(null);setCatCps([]);}
        finally{setCatCpsLoading(false);setFilterLoading(false);}
    },[selectedExpCat,period,customFrom,customTo,resetFilters]);

    /* ─── Expense category → CP click → transactions (level 2) ── */
    const handleCatCpClick=useCallback(async(cp:CategoryCounterparty)=>{
        if(selectedCatCp?.key===cp.key){setSelectedCatCp(null);setCatCpTxnList([]);return;}
        setSelectedCatCp(cp);setCatCpTxnFlow('all');setCatCpTxnLoading(true);
        try {
            const {from,to}=getPeriodDates(period,customFrom,customTo);
            const txns=await api.getFilteredTransactions(from,to,{
                cpKey:cp.key,category:selectedExpCat||undefined,limit:50,
            });
            setCatCpTxnList(txns.items);setCatCpTxnTotal(txns.total);
        } catch{setCatCpTxnList([]);}
        finally{setCatCpTxnLoading(false);}
    },[selectedExpCat,period,customFrom,customTo,selectedCatCp]);

    const handleCatCpFlowChange=useCallback(async(flow:'all'|'income'|'expense')=>{
        setCatCpTxnFlow(flow);setCatCpTxnLoading(true);
        try {
            const {from,to}=getPeriodDates(period,customFrom,customTo);
            const txns=await api.getFilteredTransactions(from,to,{
                cpKey:selectedCatCp?.key,category:selectedExpCat||undefined,flow,limit:50,
            });
            setCatCpTxnList(txns.items);setCatCpTxnTotal(txns.total);
        } catch{} finally{setCatCpTxnLoading(false);}
    },[selectedCatCp,selectedExpCat,period,customFrom,customTo]);

    const allPeriods:{key:PeriodKey;label:string}[]=[
        {key:'month',label:'Месяц'},{key:'prev_month',label:'Пред.'},
        {key:'7d',label:'7д'},{key:'30d',label:'30д'},
        {key:'90d',label:'90д'},{key:'all',label:'Всё'},
    ];
    const incomeCounterparties=data?.income_counterparties||[];
    const expensePie:ExpenseCategoryPie[]=data?.expense_by_category||[];
    const expenseByType:ExpenseTypeGroup[]=data?.expense_by_type||[];
    const dailyChart=useMemo(()=>{
        const raw=filteredDaily||data?.daily_cashflow||[];
        return raw.map((d:DailyCashflowRow)=>({...d,label:shortDay(d.date)}));
    },[data,filteredDaily]);
    const incomeByTypeChart=useMemo(()=>(data?.daily_income_by_type||[]).map(d=>({...d,label:shortDay(d.date)})),[data]);
    const incomeTypeSummary:IncomeTypeSlice[]=data?.income_by_type||[];
    const incomeTypeTotal=useMemo(()=>incomeTypeSummary.reduce((s,t)=>s+t.value,0),[incomeTypeSummary]);
    const uncatExpPct=useMemo(()=>{
        const exp=data?.month_expense||0;
        const u=(data?.expense_by_category||[]).find((c:ExpenseCategoryPie)=>c.name==='Без категории');
        return exp>0&&u?(u.value/exp*100):0;
    },[data]);
    const hasFilter=selectedCp||selectedExpCat;
    const filterLabel=selectedCp?truncate(selectedCp.name,20):selectedExpCat?truncate(selectedExpCat,20):'';

    if(loading) return <div style={{padding:40,color:'var(--color-text-muted)',display:'flex',alignItems:'center',gap:12}}><div className="spinner"/>Загрузка дашборда...</div>;
    if(error) return <div style={{padding:40,color:'var(--color-danger)'}}>❌ {error}</div>;
    if(!data) return null;

    const netCashflow=data.month_income-data.month_expense;
    const funnelDRR=funnel&&(funnel.orders_sum_rub??0)>0?((funnel.adv_sum??0)/(funnel.orders_sum_rub??1)*100):0;

    return (
        <div className="animate-in">
            {/* Header */}
            <div className="page-header" style={{display:'flex',justifyContent:'space-between',alignItems:'center',flexWrap:'wrap',gap:12}}>
                <div><h1 className="page-title">Дашборд</h1><p className="page-subtitle">{periodLabel} ({dateFrom} — {dateTo})</p></div>
                <div style={{display:'flex',gap:4,flexWrap:'wrap',alignItems:'center'}}>
                    {allPeriods.map(p=>(<button key={p.key} className={`btn btn-sm ${period===p.key?'btn-primary':'btn-secondary'}`} onClick={()=>setPeriod(p.key)} style={period===p.key?{background:'var(--color-accent)',borderColor:'var(--color-accent)'}:{}}>{p.label}</button>))}
                    <span style={{color:'var(--color-text-muted)',fontSize:12,margin:'0 4px'}}>|</span>
                    <input type="date" value={period==='custom'?customFrom:dateFrom} onChange={e=>{setCustomFrom(e.target.value);setPeriod('custom');}} style={{background:'var(--color-bg-elevated)',border:'1px solid var(--color-border)',borderRadius:6,padding:'4px 8px',color:'var(--color-text)',fontSize:12}}/>
                    <span style={{color:'var(--color-text-muted)',fontSize:12}}>—</span>
                    <input type="date" value={period==='custom'?customTo:dateTo} onChange={e=>{setCustomTo(e.target.value);setPeriod('custom');}} style={{background:'var(--color-bg-elevated)',border:'1px solid var(--color-border)',borderRadius:6,padding:'4px 8px',color:'var(--color-text)',fontSize:12}}/>
                </div>
            </div>

            {/* KPI Row 1 */}
            <div className="stats-grid" style={{gridTemplateColumns:'repeat(auto-fit,minmax(200px,1fr))'}}>
                <KpiCard icon="💰" label="Баланс RUB" value={`${formatNumber(data.balance_rub)} ₽`} color={C.income}/>
                <KpiCard icon="💴" label="Баланс CNY" value={`${formatNumber(data.balance_cny)} ¥`} color={C.warning}/>
                <KpiCard icon={netCashflow>=0?'📈':'📉'} label="Cashflow" value={`${netCashflow>=0?'+':''}${formatNumber(netCashflow)} ₽`} sub={`Приход ${fmtK(data.month_income)} / Расход ${fmtK(data.month_expense)}`} color={netCashflow>=0?C.income:C.expense} sparkData={(data.daily_cashflow||[]).map((d:DailyCashflowRow)=>d.income-d.expense)}/>
                <KpiCard icon="📊" label="ДРР" value={funnel?`${funnelDRR.toFixed(1)}%`:'—'} sub={funnel?`${fmtK(funnel.adv_sum??0)} / ${fmtK(funnel.orders_sum_rub??0)} ₽`:''} color={funnelDRR>15?C.expense:C.income}/>
            </div>

            {/* KPI Row 2 */}
            <div className="stats-grid" style={{gridTemplateColumns:'repeat(auto-fit,minmax(200px,1fr))',marginTop:0}}>
                <KpiCard icon="📦" label="Заказы" value={`${data.orders_count}`} sub={`¥ ${formatNumber(data.orders_total_cny,0)}`} color={C.info}/>
                <KpiCard icon="💳" label="Долг" value={data.debt_cny>0?`${formatNumber(data.debt_cny,0)} ¥`:(data.debt_rub>0?`${formatNumber(data.debt_rub,0)} ₽`:'0')} sub={data.debt_cny>0&&data.debt_rub>0?`+ ${formatNumber(data.debt_rub,0)} ₽`:'неоплаченные'} color={data.debt_rub>0||data.debt_cny>0?C.expense:C.income}/>
                <KpiCard icon="📥" label="INBOX" value={`${data.inbox_count}`} sub="нераспределённых" color={data.inbox_count>0?C.warning:C.income}/>
                {funnel?(<KpiCard icon="🛒" label="Заказы WB" value={funnel.orders_count?.toLocaleString('ru-RU')||'—'} sub={`${formatNumber(funnel.orders_sum_rub,0)} ₽`} color={C.info}/>):(<KpiCard icon="📊" label="Счета" value={`${data.accounts_count}`} sub="активных" color={C.accent}/>)}
            </div>

            {/* ─── Поступления по дням (по типам) ── */}
            {incomeByTypeChart.length>0&&incomeTypeTotal>0&&(
                <div className="glass-card" style={{marginTop:20,padding:'20px 16px'}}>
                    <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16,flexWrap:'wrap',gap:10}}>
                        <h3 style={{fontSize:15,fontWeight:600,color:'var(--color-text)'}}>💰 Поступления по дням
                            <span style={{fontSize:12,color:C.muted,fontWeight:400,marginLeft:8}}>чистый приход, без переводов между счетами</span>
                        </h3>
                        <div style={{display:'flex',gap:16,flexWrap:'wrap'}}>
                            {incomeTypeSummary.map((t:IncomeTypeSlice)=>{
                                const pct=incomeTypeTotal>0?(t.value/incomeTypeTotal*100):0;
                                return (<div key={t.key} style={{display:'flex',alignItems:'center',gap:6}}>
                                    <span style={{width:10,height:10,borderRadius:2,background:INCOME_TYPE_COLORS[t.key]||C.muted,display:'inline-block'}}/>
                                    <span style={{fontSize:12,color:'#94a3b8'}}>{t.name}</span>
                                    <span style={{fontSize:13,fontWeight:600,color:INCOME_TYPE_COLORS[t.key]||C.muted}}>{formatNumber(t.value,0)} ₽</span>
                                    <span style={{fontSize:11,color:C.muted}}>{pct.toFixed(0)}% · {t.count}</span>
                                </div>);
                            })}
                        </div>
                    </div>
                    <ResponsiveContainer width="100%" height={300}>
                        <BarChart data={incomeByTypeChart} margin={{left:10,right:10}}>
                            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" vertical={false}/>
                            <XAxis dataKey="label" tick={{fill:'#94a3b8',fontSize:11}}/>
                            <YAxis tickFormatter={fmtK} tick={{fill:'#94a3b8',fontSize:12}}/>
                            <Tooltip content={<IncomeTypeTooltip/>} cursor={{fill:'rgba(255,255,255,0.04)'}}/>
                            <Bar dataKey="marketplace" name="Маркетплейс" stackId="inc" fill={INCOME_TYPE_COLORS.marketplace} maxBarSize={48}/>
                            <Bar dataKey="financing" name="Финансирование" stackId="inc" fill={INCOME_TYPE_COLORS.financing} maxBarSize={48}/>
                            <Bar dataKey="other" name="Прочее" stackId="inc" fill={INCOME_TYPE_COLORS.other} maxBarSize={48}/>
                        </BarChart>
                    </ResponsiveContainer>
                </div>
            )}

            {/* Charts */}
            <div style={{display:'grid',gridTemplateColumns:dailyChart.length>0&&expensePie.length>0?'1.6fr 1fr':'1fr',gap:20,marginTop:20}}>
                {dailyChart.length>0&&(
                    <div className="glass-card" style={{padding:'20px 16px'}}>
                        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16}}>
                            <h3 style={{fontSize:15,fontWeight:600,color:'var(--color-text)'}}>📈 Доходы и расходы по дням
                                {hasFilter&&<span style={{fontSize:12,color:C.accent,marginLeft:8}}>— {filterLabel}</span>}
                                {filterLoading&&<span style={{fontSize:12,color:C.muted,marginLeft:8}}>⏳</span>}
                            </h3>
                            {hasFilter&&<button className="btn btn-sm btn-secondary" onClick={resetFilters}>Сбросить</button>}
                        </div>
                        <ResponsiveContainer width="100%" height={320}>
                            <AreaChart data={dailyChart} margin={{left:10,right:10}}>
                                <defs>
                                    <linearGradient id="gradIncome" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor={C.income} stopOpacity={0.3}/><stop offset="95%" stopColor={C.income} stopOpacity={0.02}/></linearGradient>
                                    <linearGradient id="gradExpense" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor={C.expense} stopOpacity={0.3}/><stop offset="95%" stopColor={C.expense} stopOpacity={0.02}/></linearGradient>
                                </defs>
                                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)"/>
                                <XAxis dataKey="label" tick={{fill:'#94a3b8',fontSize:11}}/>
                                <YAxis tickFormatter={fmtK} tick={{fill:'#94a3b8',fontSize:12}}/>
                                <Tooltip content={<ChartTooltip/>}/><Legend wrapperStyle={{fontSize:12,color:'#94a3b8'}}/>
                                <Area type="monotone" dataKey="income" name="Приход" stroke={C.income} fill="url(#gradIncome)" strokeWidth={2}/>
                                <Area type="monotone" dataKey="expense" name="Расход" stroke={C.expense} fill="url(#gradExpense)" strokeWidth={2}/>
                            </AreaChart>
                        </ResponsiveContainer>
                    </div>
                )}
                {expensePie.length>0&&(
                    <div className="glass-card" style={{padding:'20px 16px'}}>
                        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16}}>
                            <h3 style={{fontSize:15,fontWeight:600,color:'var(--color-text)'}}>🥧 Структура расходов</h3>
                            {selectedExpCat&&<button className="btn btn-sm btn-secondary" onClick={resetFilters}>Все</button>}
                        </div>
                        <ResponsiveContainer width="100%" height={320}>
                            <PieChart><Pie data={expensePie} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius={50} outerRadius={90} paddingAngle={2}
                                label={renderPieLabel} labelLine={{stroke:'#475569',strokeWidth:1}}
                                onClick={(entry:ExpenseCategoryPie)=>handleExpCatClick(entry.name)} style={{cursor:'pointer'}}>
                                {expensePie.map((_:ExpenseCategoryPie,i:number)=>(<Cell key={i} fill={PIE_COLORS[i%PIE_COLORS.length]}
                                    opacity={!selectedExpCat||selectedExpCat===expensePie[i]?.name?1:0.3}
                                    stroke={selectedExpCat===expensePie[i]?.name?'#fff':'none'}
                                    strokeWidth={selectedExpCat===expensePie[i]?.name?2:0}/>))}
                            </Pie>
                            <Tooltip formatter={(v:string|number)=>formatNumber(Number(v))+' ₽'} contentStyle={{background:'rgba(15,17,26,0.95)',border:'1px solid rgba(255,255,255,0.1)',borderRadius:8,color:'#e2e8f0'}}/>
                            </PieChart>
                        </ResponsiveContainer>
                    </div>
                )}
            </div>

            {/* ─── Расходы: тип контрагента → категория (2 уровня) ── */}
            {expenseByType.length>0&&(
                <div className="glass-card" style={{marginTop:20}}>
                    <div className="table-toolbar">
                        <h3 style={{fontSize:16,fontWeight:600}}>🗂 Расходы по типам контрагентов</h3>
                        <span style={{fontSize:12,color:C.muted}}>тип → категория · клик для раскрытия</span>
                    </div>
                    <table className="data-table"><thead><tr>
                        <th>Тип / Категория</th><th style={{textAlign:'right'}}>Сумма</th><th style={{textAlign:'right'}}>Операций</th><th style={{textAlign:'right'}}>% от расходов</th>
                    </tr></thead><tbody>
                        {expenseByType.map((g:ExpenseTypeGroup,i:number)=>{
                            const pct=data.month_expense>0?(g.value/data.month_expense*100):0;
                            const key=g.type??'__none__';
                            const isOpen=expandedType===key;
                            return (<React.Fragment key={key}>
                                <tr onClick={()=>setExpandedType(isOpen?null:key)} style={{cursor:'pointer',fontWeight:600}}>
                                    <td style={{display:'flex',alignItems:'center',gap:8}}>
                                        <span style={{width:10,height:10,borderRadius:3,display:'inline-block',background:PIE_COLORS[i%PIE_COLORS.length]}}/>
                                        <span style={{display:'inline-block',width:14,fontSize:10,color:'#94a3b8'}}>{isOpen?'▼':'▶'}</span>
                                        {g.type_label}
                                    </td>
                                    <td style={{textAlign:'right',color:'var(--color-danger)'}}>{formatNumber(g.value)} ₽</td>
                                    <td style={{textAlign:'right'}}>{g.count}</td>
                                    <td style={{textAlign:'right'}}><span className="badge badge-warning">{pct.toFixed(1)}%</span></td>
                                </tr>
                                {isOpen&&g.categories.map((c:ExpenseCategoryPie,j:number)=>{
                                    const cpct=g.value>0?(c.value/g.value*100):0;
                                    return (<tr key={j} style={{fontSize:13,background:'rgba(255,255,255,0.015)'}}>
                                        <td style={{paddingLeft:48,color:c.name==='Без категории'?C.muted:'var(--color-text)'}}>{c.name}</td>
                                        <td style={{textAlign:'right'}}>{formatNumber(c.value)} ₽</td>
                                        <td style={{textAlign:'right'}}>{c.count??'—'}</td>
                                        <td style={{textAlign:'right',color:C.muted,fontSize:12}}>{cpct.toFixed(0)}%</td>
                                    </tr>);
                                })}
                            </React.Fragment>);
                        })}
                    </tbody></table>
                </div>
            )}

            {/* ─── Income counterparties (1-level: click → txn list) ── */}
            {/* TODO: migrate to TanStackDataTable — interactive drill-down table with click-to-expand transaction list */}
            {incomeCounterparties.length>0&&(
                <div className="glass-card" style={{marginTop:20}}>
                    <div className="table-toolbar">
                        <h3 style={{fontSize:16,fontWeight:600}}>Приходы по контрагентам</h3>
                        {selectedCp&&<button className="btn btn-sm btn-secondary" onClick={resetFilters}>Все</button>}
                    </div>
                    <table className="data-table"><thead><tr>
                        <th>Контрагент</th><th style={{textAlign:'right'}}>Сумма</th><th style={{textAlign:'right'}}>Операций</th><th style={{textAlign:'right'}}>% от общего</th>
                    </tr></thead><tbody>
                        {incomeCounterparties.map((c:IncomeCounterparty,i:number)=>{
                            const pct=data.month_income>0?(c.total/data.month_income*100):0;
                            const isSelected=selectedCp?.name===c.name;
                            return (<React.Fragment key={i}>
                                <tr onClick={()=>handleCpClick(c)} style={{cursor:'pointer',background:isSelected?'rgba(167,139,250,0.1)':undefined}}>
                                    <td style={{maxWidth:300,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
                                        <span style={{display:'inline-block',width:14,fontSize:10,color:'#94a3b8'}}>{isSelected?'▼':'▶'}</span>{c.name}
                                    </td>
                                    <td style={{textAlign:'right',fontWeight:600,color:'var(--color-success)'}}>{formatNumber(c.total)} ₽</td>
                                    <td style={{textAlign:'right'}}>{c.count}</td>
                                    <td style={{textAlign:'right'}}><span className="badge badge-info">{pct.toFixed(1)}%</span></td>
                                </tr>
                                {isSelected&&<InlineTxnRows txnList={cpTxnList} txnTotal={cpTxnTotal} txnFlow={cpTxnFlow} onFlowChange={handleCpFlowChange} filterLoading={filterLoading} colSpan={4}/>}
                            </React.Fragment>);
                        })}
                    </tbody></table>
                </div>
            )}

            {/* ─── Expense categories (2-level: cat → counterparties → txns) ── */}
            {/* TODO: migrate to TanStackDataTable — interactive 2-level drill-down table */}
            {expensePie.length>0&&(
                <div className="glass-card" style={{marginTop:20}}>
                    <div className="table-toolbar">
                        <h3 style={{fontSize:16,fontWeight:600}}>Расходы по категориям</h3>
                        <div style={{display:'flex',alignItems:'center',gap:12,marginLeft:'auto'}}>
                            {uncatExpPct>=20&&slug&&(
                                <Link href={`/p/${slug}/refs`} style={{fontSize:12,color:C.warning,textDecoration:'none'}}>
                                    ⚠ {uncatExpPct.toFixed(0)}% без категории — назначить →
                                </Link>
                            )}
                            {selectedExpCat&&<button className="btn btn-sm btn-secondary" onClick={resetFilters}>Все</button>}
                        </div>
                    </div>
                    <table className="data-table"><thead><tr>
                        <th>Категория</th><th style={{textAlign:'right'}}>Сумма</th><th style={{textAlign:'right'}}>Операций</th><th style={{textAlign:'right'}}>% от расходов</th>
                    </tr></thead><tbody>
                        {expensePie.map((c:ExpenseCategoryPie,i:number)=>{
                            const pct=data.month_expense>0?(c.value/data.month_expense*100):0;
                            const isSelected=selectedExpCat===c.name;
                            return (<React.Fragment key={i}>
                                <tr onClick={()=>handleExpCatClick(c.name)} style={{cursor:'pointer',background:isSelected?'rgba(239,68,68,0.1)':undefined}}>
                                    <td style={{display:'flex',alignItems:'center',gap:8}}>
                                        <span style={{width:10,height:10,borderRadius:'50%',display:'inline-block',background:PIE_COLORS[i%PIE_COLORS.length]}}/>
                                        <span style={{display:'inline-block',width:14,fontSize:10,color:'#94a3b8'}}>{isSelected?'▼':'▶'}</span>
                                        {c.name}
                                    </td>
                                    <td style={{textAlign:'right',fontWeight:600,color:'var(--color-danger)'}}>{formatNumber(c.value)} ₽</td>
                                    <td style={{textAlign:'right'}}>{c.count||'—'}</td>
                                    <td style={{textAlign:'right'}}><span className="badge badge-warning">{pct.toFixed(1)}%</span></td>
                                </tr>
                                {/* Level 1: counterparties within category */}
                                {isSelected&&(
                                    <tr><td colSpan={4} style={{padding:0,background:'rgba(255,255,255,0.015)'}}>
                                        {catCpsLoading?(<div style={{padding:12,textAlign:'center',color:'#94a3b8',fontSize:13}}>⏳ Загрузка контрагентов...</div>
                                        ):catCps.length===0?(<div style={{padding:12,textAlign:'center',color:'#94a3b8',fontSize:13}}>Нет контрагентов</div>
                                        ):(
                                            <table style={{width:'100%',borderCollapse:'collapse'}}>
                                                <thead><tr style={{fontSize:11,color:'#64748b',textTransform:'uppercase' as const,letterSpacing:'0.05em'}}>
                                                    <th style={{padding:'6px 16px 6px 32px',textAlign:'left',fontWeight:500}}>Контрагент</th>
                                                    <th style={{padding:'6px 8px',textAlign:'right',fontWeight:500}}>Сумма</th>
                                                    <th style={{padding:'6px 8px',textAlign:'right',fontWeight:500}}>Кол-во</th>
                                                </tr></thead>
                                                <tbody>
                                                    {catCps.map((cp:CategoryCounterparty,j:number)=>{
                                                        const isCpSel=selectedCatCp?.key===cp.key;
                                                        return (<React.Fragment key={j}>
                                                            <tr onClick={e=>{e.stopPropagation();handleCatCpClick(cp);}}
                                                                style={{cursor:'pointer',borderTop:'1px solid rgba(255,255,255,0.04)',background:isCpSel?'rgba(167,139,250,0.08)':undefined}}>
                                                                <td style={{padding:'6px 16px 6px 32px',fontSize:13}}>
                                                                    <span style={{display:'inline-block',width:14,fontSize:10,color:'#94a3b8'}}>{isCpSel?'▼':'▶'}</span>
                                                                    {cp.name}
                                                                </td>
                                                                <td style={{padding:'6px 8px',textAlign:'right',fontWeight:600,color:C.expense,fontSize:13}}>{formatNumber(cp.total)} ₽</td>
                                                                <td style={{padding:'6px 8px',textAlign:'right',fontSize:13}}>{cp.count}</td>
                                                            </tr>
                                                            {/* Level 2: transactions for this counterparty */}
                                                            {isCpSel&&(
                                                                <tr><td colSpan={3} style={{padding:0}}>
                                                                    <InlineTxnRows txnList={catCpTxnList} txnTotal={catCpTxnTotal} txnFlow={catCpTxnFlow}
                                                                        onFlowChange={handleCatCpFlowChange} filterLoading={catCpTxnLoading} colSpan={3}/>
                                                                </td></tr>
                                                            )}
                                                        </React.Fragment>);
                                                    })}
                                                </tbody>
                                            </table>
                                        )}
                                    </td></tr>
                                )}
                            </React.Fragment>);
                        })}
                    </tbody></table>
                </div>
            )}

            {/* Balance */}
            {/* TODO: migrate to TanStackDataTable — balance table with conditional coloring */}
            <div className="glass-card" style={{marginTop:20}}>
                <div className="table-toolbar">
                    <h3 style={{fontSize:16,fontWeight:600}}>Остатки на счетах</h3>
                    <button className="btn btn-secondary btn-sm" onClick={()=>exportToExcel(balance,'balance')}>📥 Excel</button>
                </div>
                <table className="data-table">
                    <thead><tr><th>Счёт</th><th>Название</th><th>Валюта</th><th style={{textAlign:'right'}}>Остаток</th></tr></thead>
                    <tbody>{balance.map((b,i)=>(<tr key={i}>
                        <td style={{fontFamily:'monospace',fontSize:13}}>{b.account}</td><td>{b.account_name||'—'}</td>
                        <td><span className="badge badge-info">{b.currency}</span></td>
                        <td style={{textAlign:'right',fontWeight:600,color:b.balance>=0?'var(--color-success)':'var(--color-danger)'}}>{formatNumber(b.balance)} {b.currency==='RUB'?'₽':'¥'}</td>
                    </tr>))}</tbody>
                </table>
            </div>
        </div>
    );
}
