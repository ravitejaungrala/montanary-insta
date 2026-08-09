import React from 'react';
// Client-side pdf.js renderer — used as the slide-1 fallback when a PDF
// carousel post has no pre-baked thumbnail_url (typical for manual PDF
// uploads and pre-migration Agent Post rows). Keeps the whole app's PDF
// preview behaviour consistent: Drafts, Scheduled, Calendar all show
// slide 1 instead of the ugly "PDF + UUID-filename" tile.
import PdfThumbnail from './PdfThumbnail';

// A LinkedIn "carousel" post is a document (PDF): its URL lives in
// `image_url` with `media_type === 'document'`. Legacy rows have a null
// media_type, so we fall back to sniffing the file extension.
export const isDocumentMedia = (post) => {
  if (!post) return false;
  const url = post.image_url || post.media_url || '';
  if (post.media_type === 'document') return true;
  if (post.media_type) return false; // explicit non-document type
  return /\.(pdf|docx?|pptx?)(\?|$)/i.test(url);
};

export const docFileName = (url) =>
  (String(url || '').split('/').pop() || 'document.pdf').split('?')[0];

// Small square PDF chip — for table cells / calendar day-cell thumbnails.
export const DocThumb = ({ className = '' }) => (
  <div
    className={`flex items-center justify-center bg-[#F55600] text-white font-black ${className}`}
    title="LinkedIn document carousel"
  >
    PDF
  </div>
);

// Full document card with filename + an "Open Document" button. Used in the
// larger preview panels (detail modals, day overview, scheduled / draft cards).
//
// When `thumbnailUrl` is provided (slide-1 PNG for a carousel), we render
// that as a native <img> instead of the ugly orange "PDF + UUID-filename"
// tile. The Open Document link stays so users can still grab the full PDF.
// Falls back to the PDF tile for legacy rows without a thumbnail.
export const DocCard = ({ url, thumbnailUrl, compact = false }) => {
  const fname = docFileName(url);
  return (
    <div
      className={`flex flex-col items-center gap-3 ${
        compact ? 'p-3' : 'p-6'
      } bg-white rounded-2xl border border-[#2B2926]/10 shadow-sm max-w-md mx-auto`}
    >
      {thumbnailUrl ? (
        <img
          src={thumbnailUrl}
          alt="Carousel cover slide"
          loading="lazy"
          className={`${
            compact ? 'max-h-32' : 'max-h-56'
          } w-auto object-contain rounded-xl shadow-sm`}
        />
      ) : url ? (
        // No pre-baked slide-1 PNG — render slide 1 client-side via pdf.js.
        // This is the same fallback Drafts and Scheduled use, now folded
        // into DocCard so the Calendar day-detail modal, Analytics feed,
        // and every other consumer inherit the same behaviour without
        // needing per-page changes.
        <div
          className={`${
            compact ? 'max-h-32 w-32' : 'max-h-56 w-56'
          } rounded-xl shadow-sm overflow-hidden bg-white flex items-center justify-center`}
        >
          <PdfThumbnail
            src={url}
            page={1}
            className="w-full h-full"
            alt="Carousel cover slide"
          />
        </div>
      ) : (
        // No URL at all — fall back to the plain PDF icon tile so we
        // still show *something* recognisable instead of an empty card.
        <>
          <div
            className={`${
              compact ? 'w-14 h-14 text-base' : 'w-20 h-20 text-lg'
            } rounded-2xl bg-[#F55600] text-white font-black flex items-center justify-center shadow`}
          >
            PDF
          </div>
          <div className="text-center">
            <div className="text-sm font-semibold text-[#2B2926] break-all">{fname}</div>
            <div className="text-[10px] font-bold text-[#2B2926]/60 uppercase tracking-widest mt-1">
              LinkedIn Document Carousel
            </div>
          </div>
        </>
      )}
      {url && (
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => e.stopPropagation()}
          className="px-5 py-2.5 bg-[#F55600] text-white text-[10px] font-bold uppercase tracking-widest rounded-xl hover:bg-orange-600 transition-all shadow"
        >
          Open Document
        </a>
      )}
    </div>
  );
};
