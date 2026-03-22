import { NextRequest, NextResponse } from 'next/server';

const BACKEND_URL = process.env.BACKEND_URL || 'http://backend:8000';

export async function POST(request: NextRequest) {
    try {
        // Stream raw body to backend — preserve original content-type (multipart boundary)
        const body = await request.arrayBuffer();
        const contentType = request.headers.get('content-type') || '';

        const headers: Record<string, string> = {
            'content-type': contentType,
        };
        const auth = request.headers.get('authorization');
        if (auth) headers['Authorization'] = auth;
        const projectId = request.headers.get('x-project-id');
        if (projectId) headers['X-Project-Id'] = projectId;

        const res = await fetch(
            `${BACKEND_URL}/api/v1/reports/stock_analytics/upload_order_cities`,
            {
                method: 'POST',
                headers,
                body: Buffer.from(body),
            },
        );

        const data = await res.json();
        return NextResponse.json(data, { status: res.status });
    } catch (err) {
        console.error('Upload proxy error:', err);
        return NextResponse.json(
            { detail: 'Upload proxy error' },
            { status: 502 },
        );
    }
}
