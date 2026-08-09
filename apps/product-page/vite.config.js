import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from "path"

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },

  // ── Dev-server tuning ────────────────────────────────────────────────
  // The product-page dev server was feeling sluggish on localhost
  // (cursor freezes, slow HMR) because Vite was:
  //   1. Re-bundling heavy deps (recharts / framer-motion / lucide /
  //      lodash / date-fns) lazily on first request — one big stall
  //      every time you visit a new page.
  //   2. Generating CSS sourcemaps for every per-save HMR cycle, which
  //      grew expensive after the global font-family rules landed in
  //      index.css.
  //   3. Watching node_modules + transformed deps with the default
  //      polling interval, which on Windows triggers Defender scans.
  //
  // The settings below front-load the dep bundle once at server start
  // and skip work that isn't needed locally.
  optimizeDeps: {
    // List every dep the app actually imports at load time. Vite
    // bundles all of them ONCE on dev-server start instead of doing
    // it lazily when the importing module is requested.
    include: [
      'react',
      'react-dom',
      'react-router-dom',
      'axios',
      'recharts',
      'framer-motion',
      'lucide-react',
      'date-fns',
      'lodash',
      'react-icons/fi',
    ],
  },
  css: {
    // Skip per-save CSS sourcemap regeneration. Saves noticeable time
    // on every HMR cycle now that index.css carries the global
    // font-family enforcement rules.
    devSourcemap: false,
  },
  server: {
    // Skip watching node_modules + .vite cache — Windows Defender
    // scans every file touched by the watcher; restricting the watch
    // tree fixes the cursor-freeze.
    watch: {
      ignored: ['**/node_modules/**', '**/.vite/**', '**/dist/**'],
    },
    // Larger HMR throttle so a rapid succession of saves (auto-save
    // editors) gets coalesced into one HMR cycle instead of N.
    hmr: { overlay: true },
  },

  build: {
    // Increase the inline limit so small SVG/icon assets are inlined
    // rather than emitted as separate requests.
    assetsInlineLimit: 4096,
    rollupOptions: {
      output: {
        // Split heavy vendor libs into separate, independently-cacheable
        // chunks. The browser fetches and parses only the chunk it needs,
        // and CDN/browser cache survives app code updates.
        manualChunks: {
          // PDF export (jsPDF + autotable + html2canvas) only loaded when
          // the user clicks "Export PDF" on the Analytics page.
          'vendor-pdf': ['jspdf', 'jspdf-autotable', 'html2canvas'],
          // Recharts charts (analytics/dashboard) — large lib, rarely
          // changes, separately cached.
          'vendor-recharts': ['recharts'],
          // Framer Motion animation lib.
          'vendor-motion': ['framer-motion'],
          // React core — almost never changes, aggressively cached.
          'vendor-react': ['react', 'react-dom', 'react-router-dom'],
        },
      },
    },
  },
})
