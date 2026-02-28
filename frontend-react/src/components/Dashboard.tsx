"use client";

import { useEffect, useState } from "react";
import { api, logout } from "@/lib/api";

interface BalanceRow {
    account: string;
    bank: string;
    currency: string;
    account_name: string | null;
    balance: number;
}

export default function Dashboard({ onLogout }: { onLogout: () => void }) {
    const [balances, setBalances] = useState<BalanceRow[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        api
            .get<BalanceRow[]>("/api/v1/reports/balance")
            .then(setBalances)
            .catch(console.error)
            .finally(() => setLoading(false));
    }, []);

    const totalRub = balances
        .filter((b) => b.currency === "RUB")
        .reduce((s, b) => s + b.balance, 0);

    const totalCny = balances
        .filter((b) => b.currency === "CNY")
        .reduce((s, b) => s + b.balance, 0);

    const handleLogout = () => {
        logout();
        onLogout();
    };

    return (
        <div className="min-h-screen bg-zinc-950">
            {/* Top bar */}
            <header className="border-b border-zinc-800 bg-zinc-900/50 backdrop-blur-sm sticky top-0 z-50">
                <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
                    <h1 className="text-xl font-bold bg-gradient-to-r from-blue-400 to-purple-500 bg-clip-text text-transparent">
                        DDS
                    </h1>
                    <nav className="flex items-center gap-6">
                        <span className="text-sm text-zinc-400">Дашборд</span>
                        <button
                            onClick={handleLogout}
                            className="text-sm text-zinc-500 hover:text-zinc-300 transition"
                        >
                            Выйти
                        </button>
                    </nav>
                </div>
            </header>

            {/* Content */}
            <main className="max-w-7xl mx-auto px-6 py-8">
                {/* Summary cards */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
                    <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-6">
                        <p className="text-sm text-zinc-400 mb-1">Баланс RUB</p>
                        <p className="text-2xl font-bold text-emerald-400">
                            {loading ? "..." : totalRub.toLocaleString("ru-RU")} ₽
                        </p>
                    </div>
                    <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-6">
                        <p className="text-sm text-zinc-400 mb-1">Баланс CNY</p>
                        <p className="text-2xl font-bold text-amber-400">
                            {loading ? "..." : totalCny.toLocaleString("ru-RU")} ¥
                        </p>
                    </div>
                    <div className="bg-zinc-900 border border-zinc-800 rounded-2xl p-6">
                        <p className="text-sm text-zinc-400 mb-1">Счета</p>
                        <p className="text-2xl font-bold text-blue-400">
                            {loading ? "..." : balances.length}
                        </p>
                    </div>
                </div>

                {/* Balances table */}
                <div className="bg-zinc-900 border border-zinc-800 rounded-2xl overflow-hidden">
                    <div className="px-6 py-4 border-b border-zinc-800">
                        <h2 className="text-lg font-semibold">Балансы по счетам</h2>
                    </div>
                    {loading ? (
                        <div className="flex items-center justify-center py-12">
                            <div className="animate-spin w-6 h-6 border-2 border-blue-500 border-t-transparent rounded-full" />
                        </div>
                    ) : (
                        <table className="w-full">
                            <thead>
                                <tr className="text-sm text-zinc-400 border-b border-zinc-800">
                                    <th className="text-left px-6 py-3 font-medium">Счёт</th>
                                    <th className="text-left px-6 py-3 font-medium">Банк</th>
                                    <th className="text-left px-6 py-3 font-medium">Валюта</th>
                                    <th className="text-right px-6 py-3 font-medium">Баланс</th>
                                </tr>
                            </thead>
                            <tbody>
                                {balances.map((b, i) => (
                                    <tr
                                        key={i}
                                        className="border-b border-zinc-800/50 hover:bg-zinc-800/30 transition"
                                    >
                                        <td className="px-6 py-3 text-sm">
                                            {b.account_name || b.account}
                                        </td>
                                        <td className="px-6 py-3 text-sm text-zinc-400">{b.bank}</td>
                                        <td className="px-6 py-3 text-sm">
                                            <span className="px-2 py-0.5 bg-zinc-800 rounded-md text-xs font-mono">
                                                {b.currency}
                                            </span>
                                        </td>
                                        <td
                                            className={`px-6 py-3 text-sm text-right font-mono ${b.balance >= 0 ? "text-emerald-400" : "text-red-400"
                                                }`}
                                        >
                                            {b.balance.toLocaleString("ru-RU", {
                                                minimumFractionDigits: 2,
                                            })}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>
            </main>
        </div>
    );
}
