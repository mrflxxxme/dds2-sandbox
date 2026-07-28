'use client';

import { useState } from 'react';
import TabLayout from '@/components/TabLayout';
import LoansDashboard from './LoansDashboard';
import LoansRegistry from './LoansRegistry';
import LoansLenders from './LoansLenders';
import LoansForecast from './LoansForecast';
import LoansStuck from './LoansStuck';
import LoansCreditLines from './LoansCreditLines';
import LoanFormModal from './LoanFormModal';
import ImportModal from './ImportModal';

const TABS = [
    { key: 'dashboard', label: '📊 Дашборд' },
    { key: 'registry', label: '📒 Реестр' },
    { key: 'lenders', label: '👤 Заёмщики' },
    { key: 'lines', label: '💳 Кредитные линии' },
    { key: 'stuck', label: '⏳ Зависшие' },
    { key: 'forecast', label: '🔮 Прогноз' },
];

export default function LoansSectionPage() {
    const [tab, setTab] = useState('dashboard');
    const [nonce, setNonce] = useState(0);
    const [showCreate, setShowCreate] = useState(false);
    const [showImport, setShowImport] = useState(false);

    const refresh = () => setNonce((n) => n + 1);

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Займы</h1>
                    <p className="page-subtitle">Учёт займов, проценты, прогноз и доступ заёмщиков</p>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary btn-sm" onClick={() => setShowImport(true)}>
                        ⬆ Импорт из Excel
                    </button>
                    <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(true)}>
                        + Добавить займ
                    </button>
                </div>
            </div>

            <TabLayout tabs={TABS} active={tab} onChange={setTab} />

            <div style={{ marginTop: 16 }}>
                {tab === 'dashboard' && <LoansDashboard nonce={nonce} />}
                {tab === 'registry' && <LoansRegistry nonce={nonce} />}
                {tab === 'lenders' && <LoansLenders nonce={nonce} onChanged={refresh} />}
                {tab === 'lines' && <LoansCreditLines nonce={nonce} onChanged={refresh} />}
                {tab === 'stuck' && <LoansStuck nonce={nonce} onChanged={refresh} />}
                {tab === 'forecast' && <LoansForecast nonce={nonce} />}
            </div>

            {showCreate && (
                <LoanFormModal
                    mode="create"
                    onClose={() => setShowCreate(false)}
                    onSaved={() => {
                        setShowCreate(false);
                        refresh();
                    }}
                />
            )}

            {showImport && (
                <ImportModal
                    onClose={() => setShowImport(false)}
                    onDone={() => {
                        refresh();
                    }}
                />
            )}
        </div>
    );
}
