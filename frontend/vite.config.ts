import { createLogger, defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'

import { isCompatibilityId } from './src/utils/compatibilityId.ts'

const root = path.resolve(__dirname, '..')
const compatibilityId = process.env.PYRIT_COMPATIBILITY_ID ?? execFileSync(
  process.env.PYRIT_PYTHON ?? 'python',
  [path.join(root, 'build_scripts/stamp_compatibility.py'), '--development'],
  { cwd: root, encoding: 'utf8' },
).trim()
const stamp = JSON.parse(readFileSync(path.join(root, 'pyrit/_compatibility.json'), 'utf8'))
if (!isCompatibilityId(compatibilityId)
    || stamp.compatibility_id !== compatibilityId
    || compatibilityId !== `${stamp.version}+g${stamp.commit}`
    || typeof stamp.dirty !== 'boolean') {
  throw new Error('Missing or inconsistent PyRIT frontend compatibility provenance')
}

// Suppress noisy ECONNREFUSED proxy errors while the backend is starting.
// Without this, Vite logs dozens of "http proxy error" stack traces.
const logger = createLogger()
const originalError = logger.error
const backendUrl = process.env.PYRIT_BACKEND_URL ?? 'http://127.0.0.1:8000'
let proxyWarned = false
logger.error = (msg, options) => {
  if (typeof msg === 'string' && msg.includes('http proxy error')) {
    if (!proxyWarned) {
      console.log(`[vite] Waiting for backend at ${backendUrl}...`)
      proxyWarned = true
    }
    return
  }
  originalError(msg, options)
}

// https://vitejs.dev/config/
export default defineConfig({
  customLogger: logger,
  define: { __PYRIT_COMPATIBILITY_ID__: JSON.stringify(compatibilityId) },
  plugins: [react(), {
    name: 'pyrit-compatibility-stamp',
    generateBundle() {
      this.emitFile({
        type: 'asset',
        fileName: 'compatibility.json',
        source: JSON.stringify({ compatibility_id: compatibilityId }),
      })
    },
  }],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3000,
    host: true, // Listen on all interfaces for devcontainer
    // Improve HMR performance for devcontainer
    hmr: {
      overlay: false,
      clientPort: Number.parseInt(process.env.E2E_FRONTEND_PORT ?? '3000', 10),
    },
    // Same-origin API requests do not need CORS. Do not bypass backend preflights.
    cors: false,
    proxy: {
      '/api': {
        // Use 127.0.0.1 to avoid Node.js 17+ resolving localhost to IPv6 ::1
        target: backendUrl,
        changeOrigin: true,
        // Return 502 on proxy errors so in-flight requests fail fast
        // instead of hanging until the backend comes up.
        configure: (proxy) => {
          proxy.on('error', (_err, _req, res) => {
            if (res && 'writeHead' in res && !res.headersSent) {
              (res as import('http').ServerResponse).writeHead(502);
              (res as import('http').ServerResponse).end();
            }
          });
        },
      },
    },
    watch: {
      // Use polling for bind mounts in devcontainer (slower but reliable)
      usePolling: true,
      interval: 300,
      // Exclude parent directory and heavy folders
      ignored: [
        '**/node_modules/**',
        '**/.git/**',
        '**/dist/**',
        '**/build/**',
        '**/__pycache__/**',
        '**/pyrit.egg-info/**',
        '**/doc/**',
        '**/tests/**',
        '**/dbdata/**',
        '**/assets/**',
        '../pyrit/**',  // Don't watch Python backend
        '../.devcontainer/**',
        '../docker/**',
      ],
    },
    // Reduce initial page load time
    fs: {
      // Only allow serving files from frontend directory
      strict: true,
      allow: ['.'],
    },
  },
  // Optimize build performance for devcontainer
  optimizeDeps: {
    // Force pre-bundling of large dependencies
    include: [
      'react',
      'react-dom',
      'react/jsx-runtime',
      '@fluentui/react-components',
      '@fluentui/react-icons',
      'axios',
    ],
    // Vite 8 uses Rolldown for dependency pre-bundling
    rolldownOptions: {
      target: 'esnext',
    },
    exclude: [],
  },
  // Reduce CSS-in-JS transform overhead from Griffel (Fluent UI)
  css: {
    devSourcemap: false, // Disable sourcemaps in dev
  },
  build: {
    // Optimize chunk splitting
    rollupOptions: {
      output: {
        // Vite 8 / Rolldown requires manualChunks to be a function, not an object
        manualChunks: (id) => {
          if (id.includes('node_modules')) {
            if (id.includes('@fluentui/')) {
              return 'fluent-vendor'
            }
            if (
              id.includes('/react/') ||
              id.includes('/react-dom/') ||
              id.includes('/scheduler/')
            ) {
              return 'react-vendor'
            }
          }
        },
      },
    },
    chunkSizeWarningLimit: 1000,
    sourcemap: false, // Disable sourcemaps for faster builds
  },
  // Reduce logging noise
  logLevel: 'warn',
})
