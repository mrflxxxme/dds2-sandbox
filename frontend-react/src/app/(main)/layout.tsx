import type { Metadata } from "next";
import { Roboto } from "next/font/google";
import "../globals.css";

const roboto = Roboto({
    subsets: ["latin", "cyrillic"],
    weight: ["400", "500", "700", "900"],
    variable: "--font-roboto",
    display: "swap",
});

export const metadata: Metadata = {
    title: "DDS — Управление финансами",
    description: "Система управления движением денежных средств",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
    return (
        <html lang="ru" className={roboto.variable}>
            <body>{children}</body>
        </html>
    );
}
