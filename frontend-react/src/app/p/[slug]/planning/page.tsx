'use client';
import { useState } from 'react';
import { CostOrders } from './components/CostOrders';
import { PlanPayments } from './components/PlanPayments';
import { PlanIncomes, WbPayouts, Cashflow, CustomsDt } from './components/SmallPlanningTabs';

export default function PlanningPage() {
    const [tab, setTab] = useState<'orders' | 'payments' | 'incomes' | 'wb' | 'cashflow' | 'customs'>('orders');

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">📦 Планирование</h1>
                    <p className="page-subtitle">Заказы, платежи, поступления WB, кэшфлоу, таможня</p>
                </div>
            </div>
            <div style={{ display: 'flex', gap: 4, marginBottom: 20, flexWrap: 'wrap' }}>
                {[
                    { key: 'orders' as const, label: '📦 Заказы' },
                    { key: 'payments' as const, label: '💳 Платежи' },
                    { key: 'incomes' as const, label: '📥 Поступления WB' },
                    { key: 'wb' as const, label: '💰 WB Payouts' },
                    { key: 'cashflow' as const, label: '📊 Кэшфлоу' },
                    { key: 'customs' as const, label: '🛃 Таможня' },
                ].map(t => (
                    <button key={t.key} className={`btn ${tab === t.key ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                        onClick={() => setTab(t.key)}>{t.label}</button>
                ))}
            </div>
            {tab === 'orders' && <CostOrders />}
            {tab === 'payments' && <PlanPayments />}
            {tab === 'incomes' && <PlanIncomes />}
            {tab === 'wb' && <WbPayouts />}
            {tab === 'cashflow' && <Cashflow />}
            {tab === 'customs' && <CustomsDt />}
        </div>
    );
}
