import { useEffect, useRef, useState } from 'react';

// PDF.js renders the first page of a PDF to a canvas.
//
// Why this exists: Chrome's built-in PDF viewer refuses to render PDFs in
// iframes smaller than ~400x400 px. The draft thumbnail card is way smaller
// than that, so the iframe approach produced a blank/black box. pdf.js
// renders directly to a <canvas> at any size, no browser quirks.
//
// Performance: each carousel PDF is ~9 MB. With many draft cards on screen
// we'd download many tens of MB and lock the main thread rendering them
// all. So:
//   1. IntersectionObserver gates rendering — we wait until the card is in
//      (or near) the viewport before fetching the PDF.
//   2. Lower render scale (target width 400px) — still crisp for a small
//      thumbnail, much faster than full-resolution.
//   3. In-memory cache per URL so navigating away + back is instant.

import * as pdfjsLib from 'pdfjs-dist';
import workerSrc from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

pdfjsLib.GlobalWorkerOptions.workerSrc = workerSrc;

const _cache = new Map(); // url+page -> dataURL (PNG)

export default function PdfThumbnail({ src, page = 1, scale = 1.0, className = '', alt = 'PDF preview' }) {
  const containerRef = useRef(null);
  const cacheKey = `${src}#${page}`;
  const [dataUrl, setDataUrl] = useState(() => _cache.get(cacheKey) || null);
  const [error, setError] = useState(null);
  const [shouldRender, setShouldRender] = useState(() => !!_cache.get(cacheKey));

  // ── IntersectionObserver — defer rendering until visible ────────────
  useEffect(() => {
    if (shouldRender) return;            // already triggered
    const el = containerRef.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setShouldRender(true);
            obs.disconnect();
            break;
          }
        }
      },
      // Start loading slightly before the card enters the viewport so
      // it's already rendered by the time the user scrolls there.
      { rootMargin: '400px 0px' },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [shouldRender]);

  // ── Actual render once visible ──────────────────────────────────────
  useEffect(() => {
    if (!src || !shouldRender) return;
    const cached = _cache.get(cacheKey);
    if (cached) {
      setDataUrl(cached);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        // Range requests + lazy chunk fetch: instead of downloading the
        // whole 9 MB carousel PDF, pdf.js only fetches the bytes it needs
        // for the requested page. S3 supports HTTP range requests so
        // typical first-page render uses <500 KB instead of the full file.
        const pdf = await pdfjsLib.getDocument({
          url: src,
          disableAutoFetch: true,   // don't prefetch ALL pages
          disableStream: false,     // allow streamed responses
          rangeChunkSize: 65536,    // 64 KB per range request
        }).promise;
        const pdfPage = await pdf.getPage(page);
        // Target ~400px wide is plenty for thumbnails and ~4x faster to
        // render than 800px. The modal pre-loads at a bigger scale via
        // the `scale` prop.
        const baseViewport = pdfPage.getViewport({ scale: 1.0 });
        const targetWidth = 400 * scale;
        const renderScale = (targetWidth / baseViewport.width);
        const viewport = pdfPage.getViewport({ scale: renderScale });

        const canvas = document.createElement('canvas');
        canvas.width = Math.ceil(viewport.width);
        canvas.height = Math.ceil(viewport.height);
        const ctx = canvas.getContext('2d');
        await pdfPage.render({ canvasContext: ctx, viewport }).promise;
        if (cancelled) return;
        const png = canvas.toDataURL('image/png');
        _cache.set(cacheKey, png);
        setDataUrl(png);
      } catch (e) {
        // eslint-disable-next-line no-console
        console.error('[PdfThumbnail] render failed for', src, e);
        if (!cancelled) setError(e?.message || 'Failed to render PDF preview');
      }
    })();
    return () => { cancelled = true; };
  }, [src, page, scale, shouldRender, cacheKey]);

  if (error) {
    return (
      <div ref={containerRef} className={`flex items-center justify-center text-[10px] text-slate-400 ${className}`}>
        Preview unavailable
      </div>
    );
  }
  if (!dataUrl) {
    return (
      <div ref={containerRef} className={`flex items-center justify-center text-[10px] text-slate-400 ${className}`}>
        Loading preview…
      </div>
    );
  }
  return (
    // eslint-disable-next-line jsx-a11y/img-redundant-alt
    <img src={dataUrl} alt={alt} className={`object-contain ${className}`} ref={containerRef} />
  );
}
