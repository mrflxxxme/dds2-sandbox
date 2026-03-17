'use client';
import { useState } from 'react';
import { Integrations } from './components/Integrations';
import { Nomenclature, LeadTimes } from './components/SmallSettingsTabs';
import { DutyRules } from './components/DutyRules';
import { TaxRates } from './components/TaxRates';
import { WbTariffs } from './components/WbTariffs';

export default function SettingsPage() {
    const [tab, setTab] = useState<'integrations' | 'nomenclature' | 'leadtimes' | 'duties' | 'taxrates' | 'tariffs'>('integrations');

    return (
        <div className="animate-in">
            <div className="page-header">
                <div>
                    <h1 className="page-title">⚙️ Настройки проекта</h1>
                    <p className="page-subtitle">API интеграции, номенклатура, себестоимость, lead times, пошлины, тарифы</p>
                </div>
            </div>
            <div style={{ display: 'flex', gap: 4, marginBottom: 20, flexWrap: 'wrap' }}>
                {[
                    { key: 'integrations' as const, label: '🔌 API Интеграции' },
                    { key: 'nomenclature' as const, label: '📋 Номенклатура' },
                    { key: 'leadtimes' as const, label: '⏱ Lead Times' },
                    { key: 'duties' as const, label: '⚖️ Пошлины / Утиль' },
                    { key: 'taxrates' as const, label: '📋 Налоговые ставки' },
                    { key: 'tariffs' as const, label: '📊 Тарифы WB' },
                ].map(t => (
                    <button key={t.key} className={`btn ${tab === t.key ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                        onClick={() => setTab(t.key)}>{t.label}</button>
                ))}
            </div>
            {tab === 'integrations' && <Integrations />}
            {tab === 'nomenclature' && <Nomenclature />}
            {tab === 'leadtimes' && <LeadTimes />}
            {tab === 'duties' && <DutyRules />}
            {tab === 'taxrates' && <TaxRates />}
            {tab === 'tariffs' && <WbTariffs />}
        </div>
    );
}
