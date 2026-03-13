'use client';

import { useState, useEffect } from 'react';
import { api } from '@/lib/api';
import { formatNumber, formatDate, exportToExcel } from '@/lib/utils';

export default function OrderGeographyPage() {
  const [count, setCount] = useState(0);

  useEffect(() => {
    setCount(42);
  }, []);

  return (
    <div className="page-container">
      <h1>🗺️ Куда заказывают</h1>
      <p>Test: count = {count}</p>
      <p>formatNumber(1234) = {formatNumber(1234, 0)}</p>
      <p>formatDate(&quot;2025-01-15&quot;) = {formatDate("2025-01-15")}</p>
      <button onClick={() => alert('api exists: ' + !!api.getOrderGeography)}>Test API</button>
    </div>
  );
}
