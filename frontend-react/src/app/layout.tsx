import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
    title: "DDS — Управленческий учёт",
    description: "Система управления денежными потоками (ДДС)",
};

export default function RootLayout({
    children,
}: {
    children: React.ReactNode;
}) {
    return (
        <html lang="ru" className="dark">
            <body className="bg-zinc-950 text-zinc-100 antialiased min-h-screen">
                {children}
            </body>
        </html>
    );
}
