/** @type {import('next').NextConfig} */
const nextConfig = {
    output: "standalone",
    // Allow API requests to backend
    async rewrites() {
        return [
            {
                source: "/api/:path*",
                destination: `${process.env.NEXT_PUBLIC_API_URL || "http://backend:8000"}/api/:path*`,
            },
        ];
    },
};

export default nextConfig;
