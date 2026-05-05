import type { NextConfig } from '@/next'
import createMDX from '@next/mdx'
import { codeInspectorPlugin } from 'code-inspector-plugin'
import { env } from './env'

const isDev = process.env.NODE_ENV === 'development'
const withMDX = createMDX()
const envAllowedDevOrigins = process.env.NEXT_ALLOWED_DEV_ORIGINS?.split(',')
  .map(origin => origin.trim())
  .filter(Boolean)
const allowedDevOrigins = envAllowedDevOrigins?.length
  ? envAllowedDevOrigins
  : ['127.0.0.1', 'localhost', '192.168.1.25', '10.154.22.10', '10.152.17.53']

const nextConfig: NextConfig = {
  basePath: env.NEXT_PUBLIC_BASE_PATH,
  allowedDevOrigins,
  transpilePackages: ['@t3-oss/env-core', '@t3-oss/env-nextjs', 'echarts', 'zrender'],
  webpack: isDev
    ? (config) => {
        config.plugins.push(codeInspectorPlugin({ bundler: 'webpack' }))
        return config
      }
    : undefined,
  productionBrowserSourceMaps: false, // enable browser source map generation during the production build
  // Configure pageExtensions to include md and mdx
  pageExtensions: ['ts', 'tsx', 'js', 'jsx', 'md', 'mdx'],
  typescript: {
    // https://nextjs.org/docs/api-reference/next.config.js/ignoring-typescript-errors
    ignoreBuildErrors: true,
  },
  async redirects() {
    return [
      {
        source: '/',
        destination: '/apps',
        permanent: false,
      },
    ]
  },
  async rewrites() {
    if (!isDev)
      return []
    return [
      { source: '/console/api/:path*', destination: 'http://127.0.0.1:9120/console/api/:path*' },
      { source: '/api/:path*', destination: 'http://127.0.0.1:9120/api/:path*' },
      { source: '/v1/:path*', destination: 'http://127.0.0.1:9120/v1/:path*' },
      { source: '/files/:path*', destination: 'http://127.0.0.1:9120/files/:path*' },
    ]
  },
  output: 'standalone',
  compiler: {
    removeConsole: isDev ? false : { exclude: ['warn', 'error'] },
  },
}

export default withMDX(nextConfig)
