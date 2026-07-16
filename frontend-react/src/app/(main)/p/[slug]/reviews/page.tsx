'use client';

import { useState } from 'react';
import PageHeader from '@/components/PageHeader';
import TabLayout from '@/components/TabLayout';
import ReviewsSummaryTab from './components/ReviewsSummaryTab';
import ReviewsListTab from './components/ReviewsListTab';

type Tab = 'summary' | 'list';

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
                    { key: 'list', label: '💬 Отзывы' },
                ]}
                active={tab}
                onChange={(k) => setTab(k as Tab)}
            />

            {tab === 'summary' ? <ReviewsSummaryTab /> : <ReviewsListTab />}
        </div>
    );
}
