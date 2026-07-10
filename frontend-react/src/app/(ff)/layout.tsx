import type { Metadata, Viewport } from 'next';
import { Roboto } from 'next/font/google';
import '../globals.css';
import './ff.css';

const roboto = Roboto({
    subsets: ['latin', 'cyrillic'],
    weight: ['400', '500', '700', '900'],
    variable: '--font-roboto',
    display: 'swap',
});

export const metadata: Metadata = {
    title: 'ФФ-портал',
    description: 'Портал оператора фулфилмента',
};

export const viewport: Viewport = {
    width: 'device-width',
    initialScale: 1,
    viewportFit: 'cover',
};

export default function FfRootLayout({ children }: { children: React.ReactNode }) {
    return (
        <html lang="ru" className={roboto.variable}>
            <body>{children}</body>
        </html>
    );
}
