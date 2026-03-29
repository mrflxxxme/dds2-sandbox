import type { Metadata, Viewport } from 'next';
import './tma/tma.css';

/* FIX #1: Route group (tma) gets its own root layout — no conflict with main app layout */
/* FIX #2: Regular <script> for Telegram SDK — next/script beforeInteractive doesn't work in route group layouts */
/* FIX #7: viewport meta with maximum-scale=1, viewport-fit=cover */

export const metadata: Metadata = {
    title: 'DDS Mini App',
    description: 'Управленческий учёт — Telegram Mini App',
};

export const viewport: Viewport = {
    width: 'device-width',
    initialScale: 1,
    maximumScale: 1,
    userScalable: false,
    viewportFit: 'cover',
};

export default function TmaRootLayout({ children }: { children: React.ReactNode }) {
    return (
        <html lang="ru">
            <head>
                {/* eslint-disable-next-line @next/next/no-sync-scripts */}
                <script src="https://telegram.org/js/telegram-web-app.js" />
                <link rel="preconnect" href="https://fonts.googleapis.com" />
                <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
                <link
                    href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap"
                    rel="stylesheet"
                />
            </head>
            <body className="tma-body">{children}</body>
        </html>
    );
}
