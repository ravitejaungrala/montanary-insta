import React, { useState, useEffect, useCallback } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';

/**
 * Left/right swipeable viewer for an AI-generated LinkedIn carousel.
 *
 * Renders ONE slide PNG at a time (using `png_s3_url` from each slide
 * record). Has on-screen arrow buttons, optional keyboard navigation,
 * and a slide-N-of-M counter. PDF iframe scrolls top-bottom which feels
 * wrong for a carousel - this viewer swipes left-right like the real
 * LinkedIn feed.
 *
 * Props:
 *   slides        : array of { png_s3_url, slide_no, role, headline }
 *   variant       : 'tile' | 'preview' | 'full' - controls sizing/chrome
 *   showCounter   : default true; shows "1 / 3" badge
 *   showRoleBadge : default true; shows hook/body/cta tag on each slide
 *   enableKeyboard: default false; opt-in for ← → arrow keys (use on
 *                   modal/full only - don't steal keys from forms)
 *   onSlideChange : (idx) => void (optional)
 *   pdfTitle      : optional title shown in 'full' variant header
 */
const CarouselSwiper = ({
  slides = [],
  variant = 'preview',
  showCounter = true,
  showRoleBadge = true,
  enableKeyboard = false,
  onSlideChange,
  pdfTitle,
}) => {
  const [idx, setIdx] = useState(0);
  const total = slides.length;

  const go = useCallback((next) => {
    setIdx(prev => {
      const clamped = Math.max(0, Math.min(total - 1, next));
      if (onSlideChange && clamped !== prev) onSlideChange(clamped);
      return clamped;
    });
  }, [total, onSlideChange]);

  // Optional keyboard nav (only attach when explicitly enabled to avoid
  // hijacking arrow keys from caption textareas etc).
  useEffect(() => {
    if (!enableKeyboard) return;
    const onKey = (e) => {
      if (e.key === 'ArrowLeft')  { e.preventDefault(); go(idx - 1); }
      if (e.key === 'ArrowRight') { e.preventDefault(); go(idx + 1); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [enableKeyboard, idx, go]);

  if (!total) return null;

  const slide = slides[idx] || {};
  const url   = slide.png_s3_url;
  const atFirst = idx === 0;
  const atLast  = idx === total - 1;

  // Chrome sizes by variant.
  const arrowSize  = variant === 'full' ? 'w-11 h-11' : variant === 'preview' ? 'w-7 h-7' : 'w-7 h-7';
  const arrowIcon  = variant === 'full' ? 'w-6 h-6' : 'w-4 h-4';
  const badgeText  = variant === 'full' ? 'text-[11px]' : 'text-[9px]';

  return (
    <div className="relative w-full h-full bg-slate-100 group">
      {url ? (
        <img
          src={url}
          alt={slide.headline || `Slide ${slide.slide_no || idx + 1}`}
          className="w-full h-full object-contain bg-white"
          draggable={false}
        />
      ) : (
        <div className="w-full h-full flex items-center justify-center text-slate-400 text-[10px]">
          Slide {slide.slide_no || idx + 1} - no preview
        </div>
      )}

      {/* Optional role badge (hook / body / cta) - top-right so it
          doesn't overlap any logo the model may have rendered top-left. */}
      {showRoleBadge && slide.role && (
        <div className={`absolute top-2 right-2 bg-[#2B2926]/85 text-white ${badgeText} font-black uppercase tracking-widest px-1.5 py-0.5 rounded`}>
          {slide.role}
        </div>
      )}

      {/* Slide counter */}
      {showCounter && total > 1 && (
        <div className={`absolute bottom-2 right-2 bg-[#2B2926]/85 text-white ${badgeText} font-bold px-2 py-0.5 rounded`}>
          {idx + 1} / {total}
        </div>
      )}

      {/* Left arrow */}
      {total > 1 && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); go(idx - 1); }}
          disabled={atFirst}
          aria-label="Previous slide"
          className={`absolute left-2 top-1/2 -translate-y-1/2 ${arrowSize} rounded-full flex items-center justify-center shadow-lg border transition-all
            ${atFirst
              ? 'bg-white/40 text-slate-400 border-white/40 cursor-not-allowed opacity-0 group-hover:opacity-100'
              : 'bg-white text-[#2B2926] border-white hover:bg-[#F55600] hover:text-white opacity-80 group-hover:opacity-100'}
          `}
        >
          <ChevronLeft className={arrowIcon} strokeWidth={2.5} />
        </button>
      )}

      {/* Right arrow */}
      {total > 1 && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); go(idx + 1); }}
          disabled={atLast}
          aria-label="Next slide"
          className={`absolute right-2 top-1/2 -translate-y-1/2 ${arrowSize} rounded-full flex items-center justify-center shadow-lg border transition-all
            ${atLast
              ? 'bg-white/40 text-slate-400 border-white/40 cursor-not-allowed opacity-0 group-hover:opacity-100'
              : 'bg-white text-[#2B2926] border-white hover:bg-[#F55600] hover:text-white opacity-80 group-hover:opacity-100'}
          `}
        >
          <ChevronRight className={arrowIcon} strokeWidth={2.5} />
        </button>
      )}

      {/* Optional title bar for 'full' variant. The pdf_title isn't
          shown on the slide itself so we surface it in the modal chrome. */}
      {variant === 'full' && pdfTitle && (
        <div className="absolute top-0 left-0 right-0 px-4 py-2 bg-gradient-to-b from-black/60 to-transparent text-white text-xs font-semibold tracking-wide">
          {pdfTitle}
        </div>
      )}
    </div>
  );
};

export default CarouselSwiper;
