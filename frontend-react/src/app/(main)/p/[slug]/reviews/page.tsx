'use client';

import { useState } from 'react';
import PageHeader from '@/components/PageHeader';
import TabLayout from '@/components/TabLayout';
import ReviewsSummaryTab from './components/ReviewsSummaryTab';
import ReviewsBreakdownTab from './components/ReviewsBreakdownTab';
import ReviewsListTab from './components/ReviewsListTab';
import ReviewsNewcomersTab from './components/ReviewsNewcomersTab';
import ReviewsComplaintsTab from './components/ReviewsComplaintsTab';

type Tab = 'summary' | 'dynamics' | 'list' | 'newcomers' | 'complaints';

export default function ReviewsPage() {
    const [tab, setTab] = useState<Tab>('summary');

    return (
        <div className="animate-in">
            <PageHeader
                title="Отзывы"
                subtitle="Отзывы покупателей Wildberries"
                icon="⭐"
            />

            <TabLayout
                tabs={[
                    { key: 'summary', label: '📊 Сводка' },
                    { key: 'dynamics', label: '📈 Динамика' },
                    { key: 'list', label: '💬 Отзывы' },
                    { key: 'newcomers', label: '🆕 Проблемные новинки' },
                    { key: 'complaints', label: '🚩 Жалобы' },
                ]}
                active={tab}
                onChange={(k) => setTab(k as Tab)}
            />

            {tab === 'summary' && <ReviewsSummaryTab />}
            {tab === 'dynamics' && <ReviewsBreakdownTab />}
            {tab === 'list' && <ReviewsListTab />}
            {tab === 'newcomers' && <ReviewsNewcomersTab />}
            {tab === 'complaints' && <ReviewsComplaintsTab />}
        </div>
    );
}
