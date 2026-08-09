// vite.config.js
import { defineConfig } from "file:///C:/Users/hites/OneDrive/Desktop/salman_work/29-05-2026/nai_pipelyt/apps/product-page/node_modules/vite/dist/node/index.js";
import react from "file:///C:/Users/hites/OneDrive/Desktop/salman_work/29-05-2026/nai_pipelyt/apps/product-page/node_modules/@vitejs/plugin-react/dist/index.js";
import path from "path";
var __vite_injected_original_dirname = "C:\\Users\\hites\\OneDrive\\Desktop\\salman_work\\29-05-2026\\nai_pipelyt\\apps\\product-page";
var vite_config_default = defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__vite_injected_original_dirname, "./src")
    }
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
      "react",
      "react-dom",
      "react-router-dom",
      "axios",
      "recharts",
      "framer-motion",
      "lucide-react",
      "date-fns",
      "lodash",
      "react-icons/fi"
    ]
  },
  css: {
    // Skip per-save CSS sourcemap regeneration. Saves noticeable time
    // on every HMR cycle now that index.css carries the global
    // font-family enforcement rules.
    devSourcemap: false
  },
  server: {
    // Skip watching node_modules + .vite cache — Windows Defender
    // scans every file touched by the watcher; restricting the watch
    // tree fixes the cursor-freeze.
    watch: {
      ignored: ["**/node_modules/**", "**/.vite/**", "**/dist/**"]
    },
    // Larger HMR throttle so a rapid succession of saves (auto-save
    // editors) gets coalesced into one HMR cycle instead of N.
    hmr: { overlay: true }
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
          "vendor-pdf": ["jspdf", "jspdf-autotable", "html2canvas"],
          // Recharts charts (analytics/dashboard) — large lib, rarely
          // changes, separately cached.
          "vendor-recharts": ["recharts"],
          // Framer Motion animation lib.
          "vendor-motion": ["framer-motion"],
          // React core — almost never changes, aggressively cached.
          "vendor-react": ["react", "react-dom", "react-router-dom"]
        }
      }
    }
  }
});
export {
  vite_config_default as default
};
//# sourceMappingURL=data:application/json;base64,ewogICJ2ZXJzaW9uIjogMywKICAic291cmNlcyI6IFsidml0ZS5jb25maWcuanMiXSwKICAic291cmNlc0NvbnRlbnQiOiBbImNvbnN0IF9fdml0ZV9pbmplY3RlZF9vcmlnaW5hbF9kaXJuYW1lID0gXCJDOlxcXFxVc2Vyc1xcXFxoaXRlc1xcXFxPbmVEcml2ZVxcXFxEZXNrdG9wXFxcXHNhbG1hbl93b3JrXFxcXDI5LTA1LTIwMjZcXFxcbmFpX3BpcGVseXRcXFxcYXBwc1xcXFxwcm9kdWN0LXBhZ2VcIjtjb25zdCBfX3ZpdGVfaW5qZWN0ZWRfb3JpZ2luYWxfZmlsZW5hbWUgPSBcIkM6XFxcXFVzZXJzXFxcXGhpdGVzXFxcXE9uZURyaXZlXFxcXERlc2t0b3BcXFxcc2FsbWFuX3dvcmtcXFxcMjktMDUtMjAyNlxcXFxuYWlfcGlwZWx5dFxcXFxhcHBzXFxcXHByb2R1Y3QtcGFnZVxcXFx2aXRlLmNvbmZpZy5qc1wiO2NvbnN0IF9fdml0ZV9pbmplY3RlZF9vcmlnaW5hbF9pbXBvcnRfbWV0YV91cmwgPSBcImZpbGU6Ly8vQzovVXNlcnMvaGl0ZXMvT25lRHJpdmUvRGVza3RvcC9zYWxtYW5fd29yay8yOS0wNS0yMDI2L25haV9waXBlbHl0L2FwcHMvcHJvZHVjdC1wYWdlL3ZpdGUuY29uZmlnLmpzXCI7aW1wb3J0IHsgZGVmaW5lQ29uZmlnIH0gZnJvbSAndml0ZSdcclxuaW1wb3J0IHJlYWN0IGZyb20gJ0B2aXRlanMvcGx1Z2luLXJlYWN0J1xyXG5pbXBvcnQgcGF0aCBmcm9tIFwicGF0aFwiXHJcblxyXG4vLyBodHRwczovL3ZpdGUuZGV2L2NvbmZpZy9cclxuZXhwb3J0IGRlZmF1bHQgZGVmaW5lQ29uZmlnKHtcclxuICBwbHVnaW5zOiBbcmVhY3QoKV0sXHJcbiAgcmVzb2x2ZToge1xyXG4gICAgYWxpYXM6IHtcclxuICAgICAgXCJAXCI6IHBhdGgucmVzb2x2ZShfX2Rpcm5hbWUsIFwiLi9zcmNcIiksXHJcbiAgICB9LFxyXG4gIH0sXHJcblxyXG4gIC8vIFx1MjUwMFx1MjUwMCBEZXYtc2VydmVyIHR1bmluZyBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcdTI1MDBcclxuICAvLyBUaGUgcHJvZHVjdC1wYWdlIGRldiBzZXJ2ZXIgd2FzIGZlZWxpbmcgc2x1Z2dpc2ggb24gbG9jYWxob3N0XHJcbiAgLy8gKGN1cnNvciBmcmVlemVzLCBzbG93IEhNUikgYmVjYXVzZSBWaXRlIHdhczpcclxuICAvLyAgIDEuIFJlLWJ1bmRsaW5nIGhlYXZ5IGRlcHMgKHJlY2hhcnRzIC8gZnJhbWVyLW1vdGlvbiAvIGx1Y2lkZSAvXHJcbiAgLy8gICAgICBsb2Rhc2ggLyBkYXRlLWZucykgbGF6aWx5IG9uIGZpcnN0IHJlcXVlc3QgXHUyMDE0IG9uZSBiaWcgc3RhbGxcclxuICAvLyAgICAgIGV2ZXJ5IHRpbWUgeW91IHZpc2l0IGEgbmV3IHBhZ2UuXHJcbiAgLy8gICAyLiBHZW5lcmF0aW5nIENTUyBzb3VyY2VtYXBzIGZvciBldmVyeSBwZXItc2F2ZSBITVIgY3ljbGUsIHdoaWNoXHJcbiAgLy8gICAgICBncmV3IGV4cGVuc2l2ZSBhZnRlciB0aGUgZ2xvYmFsIGZvbnQtZmFtaWx5IHJ1bGVzIGxhbmRlZCBpblxyXG4gIC8vICAgICAgaW5kZXguY3NzLlxyXG4gIC8vICAgMy4gV2F0Y2hpbmcgbm9kZV9tb2R1bGVzICsgdHJhbnNmb3JtZWQgZGVwcyB3aXRoIHRoZSBkZWZhdWx0XHJcbiAgLy8gICAgICBwb2xsaW5nIGludGVydmFsLCB3aGljaCBvbiBXaW5kb3dzIHRyaWdnZXJzIERlZmVuZGVyIHNjYW5zLlxyXG4gIC8vXHJcbiAgLy8gVGhlIHNldHRpbmdzIGJlbG93IGZyb250LWxvYWQgdGhlIGRlcCBidW5kbGUgb25jZSBhdCBzZXJ2ZXIgc3RhcnRcclxuICAvLyBhbmQgc2tpcCB3b3JrIHRoYXQgaXNuJ3QgbmVlZGVkIGxvY2FsbHkuXHJcbiAgb3B0aW1pemVEZXBzOiB7XHJcbiAgICAvLyBMaXN0IGV2ZXJ5IGRlcCB0aGUgYXBwIGFjdHVhbGx5IGltcG9ydHMgYXQgbG9hZCB0aW1lLiBWaXRlXHJcbiAgICAvLyBidW5kbGVzIGFsbCBvZiB0aGVtIE9OQ0Ugb24gZGV2LXNlcnZlciBzdGFydCBpbnN0ZWFkIG9mIGRvaW5nXHJcbiAgICAvLyBpdCBsYXppbHkgd2hlbiB0aGUgaW1wb3J0aW5nIG1vZHVsZSBpcyByZXF1ZXN0ZWQuXHJcbiAgICBpbmNsdWRlOiBbXHJcbiAgICAgICdyZWFjdCcsXHJcbiAgICAgICdyZWFjdC1kb20nLFxyXG4gICAgICAncmVhY3Qtcm91dGVyLWRvbScsXHJcbiAgICAgICdheGlvcycsXHJcbiAgICAgICdyZWNoYXJ0cycsXHJcbiAgICAgICdmcmFtZXItbW90aW9uJyxcclxuICAgICAgJ2x1Y2lkZS1yZWFjdCcsXHJcbiAgICAgICdkYXRlLWZucycsXHJcbiAgICAgICdsb2Rhc2gnLFxyXG4gICAgICAncmVhY3QtaWNvbnMvZmknLFxyXG4gICAgXSxcclxuICB9LFxyXG4gIGNzczoge1xyXG4gICAgLy8gU2tpcCBwZXItc2F2ZSBDU1Mgc291cmNlbWFwIHJlZ2VuZXJhdGlvbi4gU2F2ZXMgbm90aWNlYWJsZSB0aW1lXHJcbiAgICAvLyBvbiBldmVyeSBITVIgY3ljbGUgbm93IHRoYXQgaW5kZXguY3NzIGNhcnJpZXMgdGhlIGdsb2JhbFxyXG4gICAgLy8gZm9udC1mYW1pbHkgZW5mb3JjZW1lbnQgcnVsZXMuXHJcbiAgICBkZXZTb3VyY2VtYXA6IGZhbHNlLFxyXG4gIH0sXHJcbiAgc2VydmVyOiB7XHJcbiAgICAvLyBTa2lwIHdhdGNoaW5nIG5vZGVfbW9kdWxlcyArIC52aXRlIGNhY2hlIFx1MjAxNCBXaW5kb3dzIERlZmVuZGVyXHJcbiAgICAvLyBzY2FucyBldmVyeSBmaWxlIHRvdWNoZWQgYnkgdGhlIHdhdGNoZXI7IHJlc3RyaWN0aW5nIHRoZSB3YXRjaFxyXG4gICAgLy8gdHJlZSBmaXhlcyB0aGUgY3Vyc29yLWZyZWV6ZS5cclxuICAgIHdhdGNoOiB7XHJcbiAgICAgIGlnbm9yZWQ6IFsnKiovbm9kZV9tb2R1bGVzLyoqJywgJyoqLy52aXRlLyoqJywgJyoqL2Rpc3QvKionXSxcclxuICAgIH0sXHJcbiAgICAvLyBMYXJnZXIgSE1SIHRocm90dGxlIHNvIGEgcmFwaWQgc3VjY2Vzc2lvbiBvZiBzYXZlcyAoYXV0by1zYXZlXHJcbiAgICAvLyBlZGl0b3JzKSBnZXRzIGNvYWxlc2NlZCBpbnRvIG9uZSBITVIgY3ljbGUgaW5zdGVhZCBvZiBOLlxyXG4gICAgaG1yOiB7IG92ZXJsYXk6IHRydWUgfSxcclxuICB9LFxyXG5cclxuICBidWlsZDoge1xyXG4gICAgLy8gSW5jcmVhc2UgdGhlIGlubGluZSBsaW1pdCBzbyBzbWFsbCBTVkcvaWNvbiBhc3NldHMgYXJlIGlubGluZWRcclxuICAgIC8vIHJhdGhlciB0aGFuIGVtaXR0ZWQgYXMgc2VwYXJhdGUgcmVxdWVzdHMuXHJcbiAgICBhc3NldHNJbmxpbmVMaW1pdDogNDA5NixcclxuICAgIHJvbGx1cE9wdGlvbnM6IHtcclxuICAgICAgb3V0cHV0OiB7XHJcbiAgICAgICAgLy8gU3BsaXQgaGVhdnkgdmVuZG9yIGxpYnMgaW50byBzZXBhcmF0ZSwgaW5kZXBlbmRlbnRseS1jYWNoZWFibGVcclxuICAgICAgICAvLyBjaHVua3MuIFRoZSBicm93c2VyIGZldGNoZXMgYW5kIHBhcnNlcyBvbmx5IHRoZSBjaHVuayBpdCBuZWVkcyxcclxuICAgICAgICAvLyBhbmQgQ0ROL2Jyb3dzZXIgY2FjaGUgc3Vydml2ZXMgYXBwIGNvZGUgdXBkYXRlcy5cclxuICAgICAgICBtYW51YWxDaHVua3M6IHtcclxuICAgICAgICAgIC8vIFBERiBleHBvcnQgKGpzUERGICsgYXV0b3RhYmxlICsgaHRtbDJjYW52YXMpIG9ubHkgbG9hZGVkIHdoZW5cclxuICAgICAgICAgIC8vIHRoZSB1c2VyIGNsaWNrcyBcIkV4cG9ydCBQREZcIiBvbiB0aGUgQW5hbHl0aWNzIHBhZ2UuXHJcbiAgICAgICAgICAndmVuZG9yLXBkZic6IFsnanNwZGYnLCAnanNwZGYtYXV0b3RhYmxlJywgJ2h0bWwyY2FudmFzJ10sXHJcbiAgICAgICAgICAvLyBSZWNoYXJ0cyBjaGFydHMgKGFuYWx5dGljcy9kYXNoYm9hcmQpIFx1MjAxNCBsYXJnZSBsaWIsIHJhcmVseVxyXG4gICAgICAgICAgLy8gY2hhbmdlcywgc2VwYXJhdGVseSBjYWNoZWQuXHJcbiAgICAgICAgICAndmVuZG9yLXJlY2hhcnRzJzogWydyZWNoYXJ0cyddLFxyXG4gICAgICAgICAgLy8gRnJhbWVyIE1vdGlvbiBhbmltYXRpb24gbGliLlxyXG4gICAgICAgICAgJ3ZlbmRvci1tb3Rpb24nOiBbJ2ZyYW1lci1tb3Rpb24nXSxcclxuICAgICAgICAgIC8vIFJlYWN0IGNvcmUgXHUyMDE0IGFsbW9zdCBuZXZlciBjaGFuZ2VzLCBhZ2dyZXNzaXZlbHkgY2FjaGVkLlxyXG4gICAgICAgICAgJ3ZlbmRvci1yZWFjdCc6IFsncmVhY3QnLCAncmVhY3QtZG9tJywgJ3JlYWN0LXJvdXRlci1kb20nXSxcclxuICAgICAgICB9LFxyXG4gICAgICB9LFxyXG4gICAgfSxcclxuICB9LFxyXG59KVxyXG4iXSwKICAibWFwcGluZ3MiOiAiO0FBQWtjLFNBQVMsb0JBQW9CO0FBQy9kLE9BQU8sV0FBVztBQUNsQixPQUFPLFVBQVU7QUFGakIsSUFBTSxtQ0FBbUM7QUFLekMsSUFBTyxzQkFBUSxhQUFhO0FBQUEsRUFDMUIsU0FBUyxDQUFDLE1BQU0sQ0FBQztBQUFBLEVBQ2pCLFNBQVM7QUFBQSxJQUNQLE9BQU87QUFBQSxNQUNMLEtBQUssS0FBSyxRQUFRLGtDQUFXLE9BQU87QUFBQSxJQUN0QztBQUFBLEVBQ0Y7QUFBQTtBQUFBO0FBQUE7QUFBQTtBQUFBO0FBQUE7QUFBQTtBQUFBO0FBQUE7QUFBQTtBQUFBO0FBQUE7QUFBQTtBQUFBO0FBQUEsRUFnQkEsY0FBYztBQUFBO0FBQUE7QUFBQTtBQUFBLElBSVosU0FBUztBQUFBLE1BQ1A7QUFBQSxNQUNBO0FBQUEsTUFDQTtBQUFBLE1BQ0E7QUFBQSxNQUNBO0FBQUEsTUFDQTtBQUFBLE1BQ0E7QUFBQSxNQUNBO0FBQUEsTUFDQTtBQUFBLE1BQ0E7QUFBQSxJQUNGO0FBQUEsRUFDRjtBQUFBLEVBQ0EsS0FBSztBQUFBO0FBQUE7QUFBQTtBQUFBLElBSUgsY0FBYztBQUFBLEVBQ2hCO0FBQUEsRUFDQSxRQUFRO0FBQUE7QUFBQTtBQUFBO0FBQUEsSUFJTixPQUFPO0FBQUEsTUFDTCxTQUFTLENBQUMsc0JBQXNCLGVBQWUsWUFBWTtBQUFBLElBQzdEO0FBQUE7QUFBQTtBQUFBLElBR0EsS0FBSyxFQUFFLFNBQVMsS0FBSztBQUFBLEVBQ3ZCO0FBQUEsRUFFQSxPQUFPO0FBQUE7QUFBQTtBQUFBLElBR0wsbUJBQW1CO0FBQUEsSUFDbkIsZUFBZTtBQUFBLE1BQ2IsUUFBUTtBQUFBO0FBQUE7QUFBQTtBQUFBLFFBSU4sY0FBYztBQUFBO0FBQUE7QUFBQSxVQUdaLGNBQWMsQ0FBQyxTQUFTLG1CQUFtQixhQUFhO0FBQUE7QUFBQTtBQUFBLFVBR3hELG1CQUFtQixDQUFDLFVBQVU7QUFBQTtBQUFBLFVBRTlCLGlCQUFpQixDQUFDLGVBQWU7QUFBQTtBQUFBLFVBRWpDLGdCQUFnQixDQUFDLFNBQVMsYUFBYSxrQkFBa0I7QUFBQSxRQUMzRDtBQUFBLE1BQ0Y7QUFBQSxJQUNGO0FBQUEsRUFDRjtBQUNGLENBQUM7IiwKICAibmFtZXMiOiBbXQp9Cg==
