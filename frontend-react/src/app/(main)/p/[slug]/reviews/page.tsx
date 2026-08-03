'use client';

import { useState } from 'react';
import PageHeader from '@/components/PageHeader';
import TabLayout from '@/components/TabLayout';
import ReviewsSummaryTab from './components/ReviewsSummaryTab';
import ReviewsBreakdownTab from './components/ReviewsBreakdownTab';
import ReviewsListTab from './components/ReviewsListTab';
import ReviewsNewcomersTab from './components/ReviewsNewcomersTab';
import ReviewsComplaintsTab from './components/ReviewsComplaintsTab';
import KnowledgeBaseTab from './components/KnowledgeBaseTab';
import QuestionsTab from './components/QuestionsTab';
import AutoRepliesTab from './components/AutoRepliesTab';
import StockWatchTab from './components/StockWatchTab';

type Tab = 'summary' | 'dynamics' | 'list' | 'newcomers' | 'complaints' | 'kb' | 'questions' | 'replies_feedback' | 'replies_questions' | 'stock';

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
                    { key: 'kb', label: '📚 База знаний' },
                    { key: 'questions', label: '❓ Вопросы' },
                    { key: 'replies_feedback', label: '🤖 Отзывы' },
                    { key: 'replies_questions', label: '💬 Вопросы-автоответы' },
                    { key: 'stock', label: '📦 Поступления' },
                ]}
                active={tab}
                onChange={(k) => setTab(k as Tab)}
            />

            {tab === 'summary' && <ReviewsSummaryTab />}
            {tab === 'dynamics' && <ReviewsBreakdownTab />}
            {tab === 'list' && <ReviewsListTab />}
            {tab === 'newcomers' && <ReviewsNewcomersTab />}
            {tab === 'complaints' && <ReviewsComplaintsTab />}
            {tab === 'kb' && <KnowledgeBaseTab />}
            {tab === 'questions' && <QuestionsTab />}
            {tab === 'replies_feedback' && <AutoRepliesTab targetType="feedback" />}
            {tab === 'replies_questions' && <AutoRepliesTab targetType="question" />}
            {tab === 'stock' && <StockWatchTab onGoToDrafts={() => setTab('replies_questions')} />}
        </div>
    );
}
