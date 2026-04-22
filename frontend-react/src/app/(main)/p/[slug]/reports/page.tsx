'use client';
import { useState } from 'react';
import { DDSPnL } from './components/DDSPnL';
import { BalanceDaily, FxControl, CustomsControl } from './components/SmallReports';
import { WbBdr } from './components/WbBdr';
import { CostHistory } from './components/CostHistory';
import { CounterpartyTurnovers } from './components/CounterpartyTurnovers';

export default function ReportsPage() {
    const [tab, setTab] = useState<'dds' | 'bdr' | 'balance' | 'fx' | 'customs' | 'cost_history' | 'cp_turnovers'>('dds');

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">📊 Отчёты</h1>
                    <p className="page-subtitle">ДДС, БДР, баланс, FX, таможня, себестоимость, обороты</p>
                </div>
            </div>

            <div style={{ display: 'flex', gap: 4, marginBottom: 20, flexWrap: 'wrap' }}>
                {[
                    { key: 'dds' as const, label: 'ДДС за месяц' },
                    { key: 'bdr' as const, label: 'БДР (WB)' },
                    { key: 'balance' as const, label: 'Баланс по дням' },
                    { key: 'fx' as const, label: 'FX Контроль' },
                    { key: 'customs' as const, label: 'Таможня' },
                    { key: 'cost_history' as const, label: 'Себестоимость' },
                    { key: 'cp_turnovers' as const, label: 'Обороты по контрагентам' },
                ].map(t => (
                    <button key={t.key} className={`btn ${tab === t.key ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                        onClick={() => setTab(t.key)}>{t.label}</button>
                ))}
            </div>

            {tab === 'dds' && <DDSPnL />}
            {tab === 'bdr' && <WbBdr />}
            {tab === 'balance' && <BalanceDaily />}
            {tab === 'fx' && <FxControl />}
            {tab === 'customs' && <CustomsControl />}
            {tab === 'cost_history' && <CostHistory />}
            {tab === 'cp_turnovers' && <CounterpartyTurnovers />}
        </div>
    );
}
