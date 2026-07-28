'use client';

import { useState } from 'react';
import TabLayout from '@/components/TabLayout';
import PageGuard from '@/components/PageGuard';
import SalarySheet from './SalarySheet';
import SalaryTeams from './SalaryTeams';
import SalaryEmployees from './SalaryEmployees';
import SalaryTariff from './SalaryTariff';

const TABS = [
    { key: 'sheet', label: '📋 Ведомость' },
    { key: 'teams', label: '👥 Команды' },
    { key: 'employees', label: '🧑‍💼 Сотрудники' },
    { key: 'tariff', label: '📶 Тарифная сетка' },
];

export default function SalarySectionPage() {
    const [tab, setTab] = useState('sheet');
    const [nonce, setNonce] = useState(0);

    const refresh = () => setNonce((n) => n + 1);

    return (
        <PageGuard page="salary">
            <div className="animate-in">
                <div className="page-header">
                    <div>
                        <h1 className="page-title">Зарплата</h1>
                        <p className="page-subtitle">
                            Ведомость начислений: % от «Чистой выплаты» WB по командам + фикс-оклады
                        </p>
                    </div>
                </div>

                <TabLayout tabs={TABS} active={tab} onChange={setTab} />

                <div style={{ marginTop: 16 }}>
                    {tab === 'sheet' && <SalarySheet nonce={nonce} onNavigate={setTab} />}
                    {tab === 'teams' && <SalaryTeams nonce={nonce} onChanged={refresh} />}
                    {tab === 'employees' && <SalaryEmployees nonce={nonce} onChanged={refresh} />}
                    {tab === 'tariff' && <SalaryTariff nonce={nonce} onChanged={refresh} />}
                </div>
            </div>
        </PageGuard>
    );
}
