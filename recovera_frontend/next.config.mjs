/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Prevent Vercel from bundling pg — it must stay as a Node.js external.
  experimental: {
    serverComponentsExternalPackages: ['pg'],
  },
};

export default nextConfig;
