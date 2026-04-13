import withBundleAnalyzer from '@next/bundle-analyzer';

const analyzer = withBundleAnalyzer({
    enabled: process.env.ANALYZE === 'true',
});

/** @type {import('next').NextConfig} */
const nextConfig = {
    output: 'standalone',
    typescript: {
        ignoreBuildErrors: true,
    },
    eslint: {
        ignoreDuringBuilds: true,
    },
    // Increase proxy timeout for large file uploads (e.g. 11MB order feed)
    experimental: {
        proxyTimeout: 120_000,
    },
    async rewrites() {
        const backendUrl = process.env.BACKEND_URL || 'http://backend:8000';
        return [
            {
                source: '/api/:path*',
                destination: `${backendUrl}/api/:path*`,
            },
        ];
    },
};

export default analyzer(nextConfig);
