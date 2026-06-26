'use client';

// Ретайр-редирект: предпросмотр заявок переехал внутрь страницы «Черновик
// сборки» (.../distribute?draft=N). Этот роут оставлен как тонкий редирект,
// чтобы старые ссылки и закладки продолжали работать.
import { useEffect } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';

export default function AssemblyPreviewRedirectPage() {
    const params = useParams();
    const router = useRouter();
    const searchParams = useSearchParams();
    const slug = params.slug as string;

    useEffect(() => {
        if (!slug) return;
        const raw = searchParams.get('draft') ?? '';
        if (raw) {
            router.replace(`/p/${slug}/warehouse/assembly/distribute?draft=${raw}`);
            return;
        }
        // useSearchParams пуст на первом рендере до гидратации — не редиректим
        // в корень, пока не убедимся, что draft реально отсутствует в URL
        // (сырая query-строка не содержит draft).
        if (typeof window !== 'undefined' && !window.location.search.includes('draft=')) {
            router.replace(`/p/${slug}/warehouse/assembly`);
        }
    }, [searchParams, router, slug]);

    return (
        <div className="animate-in">
            <div className="glass-card" style={{ padding: 32, textAlign: 'center' }}>Открываем черновик…</div>
        </div>
    );
}
