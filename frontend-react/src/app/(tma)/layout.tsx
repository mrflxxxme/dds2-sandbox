import type { Metadata, Viewport } from 'next';
import Script from 'next/script';
import { Roboto } from 'next/font/google';
import './tma/tma.css';

const roboto = Roboto({
    subsets: ['latin', 'cyrillic'],
    weight: ['400', '500', '700', '900'],
    variable: '--font-roboto',
    display: 'swap',
});

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
        <html lang="ru" className={roboto.variable}>
            <head>
                <Script
                    src="https://telegram.org/js/telegram-web-app.js"
                    strategy="afterInteractive"
                />
            </head>
            <body className="tma-body">{children}</body>
        </html>
    );
}
