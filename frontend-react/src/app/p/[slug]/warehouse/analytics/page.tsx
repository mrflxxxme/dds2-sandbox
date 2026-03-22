'use client';
import { StockAnalytics } from './components/StockAnalytics';

export default function WarehouseAnalyticsPage() {
    return (
        <div>
            <div className="page-header">
                <h1>Аналитика остатков</h1>
            </div>
            <StockAnalytics />
        </div>
    );
}
