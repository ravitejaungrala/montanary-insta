import React, { useState, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { useNotification } from '../../../context/NotificationContext';
import {
  Image as ImageIcon,
  CheckCircle2,
  RefreshCw,
  X,
  Maximize2
} from 'lucide-react';
import CarouselSwiper from '../../../components/CarouselSwiper';

/**
 * Flatten the freeform pipeline's `visuals[]` (each variant is one
 * finished image with its own copy baked in) into a list of tiles.
 *
 * Each variant from the backend now looks like:
 *   { url, headline, subheading, cta, template_chosen, scene_type,
 *     campaign_id, background_index, templates: {FF: url} }
 *
 * Falls back gracefully if the backend ever returns the legacy
 * 11-templates-per-bg shape.
 *
 * Exported so Dashboard.jsx can use the same indexing when posting.
 */
export const flattenVisuals = (generatedData) => {
  const out = [];
  const visuals = generatedData?.visuals || [];
  visuals.forEach((v, bgIdx) => {
    // CAROUSEL — Document post type. ONE tile per carousel; the
    // selectable URL is the full PDF (LinkedIn publishes a single PDF
    // doc, not individual slides). slide_count + slides[] surface to the
    // sidebar / enlarge view for swipe-through.
    if (v?.media_type === 'document' || v?.pipeline === 'carousel-gpt-image-2') {
      out.push({
        url:               v.url,                                  // the PDF S3 URL
        media_type:        'document',
        pipeline:          v.pipeline || 'carousel-gpt-image-2',
        pdf_title:         v.pdf_title || 'Carousel',
        // Slide-1 PNG — required by Drafts.jsx to render a fast <img>
        // preview instead of falling back to a PDF iframe/tile. Without
        // forwarding this the normal-pick save path lost it and every
        // saved carousel ended up with thumbnail_url=NULL.
        thumbnail_url:     v.thumbnail_url
                            || (Array.isArray(v.slides) && v.slides[0]?.png_s3_url)
                            || null,
        slide_count:       v.slide_count || (v.slides ? v.slides.length : 1),
        slides:            v.slides || [],                          // per-slide png_s3_url + headline + role
        template_id:       'carousel',
        background_index:  bgIdx,
        scene_type:        `Carousel · ${v.slide_count || (v.slides ? v.slides.length : 1)} slides`,
        heading:           v.pdf_title || '',
      });
      return;
    }
    // FREEFORM variants — one tile per variant. Differentiated by
    // pipeline === 'freeform_v3' (preferred) OR by primary_template ===
    // 'FF' (fallback for older payloads). DO NOT branch on `headline`
    // alone, because V2 PIL variants ALSO carry a headline and would
    // collapse to one tile instead of exploding into 11 templates.
    const isFreeform = v?.pipeline === 'freeform_v3'
      || v?.primary_template === 'FF'
      || (v?.templates && Object.keys(v.templates).length === 1
          && v.templates.FF);
    if (isFreeform && v?.url) {
      out.push({
        url: v.url,
        template_id: v.primary_template || 'FF',
        background_index: bgIdx,
        scene_type: v.template_chosen || v.scene_type || '',
        heading: v.headline || '',
        subheading: v.subheading || '',
        cta: v.cta || '',
        rating_out_of_10: null,
        campaign_id: v.campaign_id,
        pipeline: 'freeform_v3',
      });
      return;
    }
    // V2 PIL — explode the nested {templates: {T1..T11}} shape into one
    // tile per template per background.
    if (v?.templates && typeof v.templates === 'object' && Object.keys(v.templates).length) {
      const overlay = generatedData?.visualist_meta?.overlay_copy || {};
      Object.entries(v.templates).forEach(([tid, url]) => {
        out.push({
          url,
          template_id: tid,
          background_index: bgIdx,
          scene_type: v.scene_type,
          heading: v.headline || overlay.headline,
          subheading: v.subheading || overlay.subheading,
          cta: v.cta || overlay.cta,
          rating_out_of_10: v.background_rating,
          campaign_id: v.campaign_id,
          pipeline: v.pipeline || 'v2_pil',
        });
      });
    } else if (v?.url) {
      out.push({ ...v, background_index: bgIdx, template_id: 'legacy' });
    }
  });
  return out;
};

const VisualGallery = ({
  generatedData,
  selectedVisual,
  setSelectedVisual,
  uploadedImageUrl,
  fileInputRef,
  handleImageUpload,
  isUploadingImage,
  authAxios,
  onRegenerate,
  onCustomPrompt,
  user
}) => {
  const { toast } = useNotification();
  const [enlargedImage, setEnlargedImage] = useState(null);
  const [showCustomPrompt, setShowCustomPrompt] = useState(false);
  const [customPromptText, setCustomPromptText] = useState('');
  // Freeform pipeline: each tile is its own variant, no per-scene grouping.
  // The legacy per-scene tab filter has been removed.
  const flatVariants = useMemo(() => flattenVisuals(generatedData), [generatedData]);
  const filteredVariants = flatVariants;

  const toggleSelection = (e, index) => {
    e.stopPropagation();
    setSelectedVisual(prev => prev === index ? 'none' : index);
  };

  const handleCustomSubmit = (e) => {
    e.preventDefault();
    if (customPromptText.trim()) {
      onCustomPrompt(customPromptText);
      setCustomPromptText('');
      setShowCustomPrompt(false);
    }
  };

  return (
    <div className="bg-white rounded-2xl p-4 border-2 border-slate-350 shadow-lg relative">
      <div className="flex items-center justify-between mb-3 relative z-10">
        <div className="flex items-center gap-2.5 text-[#2B2926]">
          <ImageIcon className="w-5 h-5 text-[#2B2926]" />
          <h3 className="text-lg font-bold tracking-tight">AI-Generated Images</h3>
        </div>
        <span className="text-[10px] font-semibold text-[#2B2926] bg-white px-3 py-1 rounded-full border border-[#2B2926]/20">
          Eva · {flatVariants.length} Options
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3 mb-3 relative z-10">
        {filteredVariants.map((variant) => {
          const idx = flatVariants.indexOf(variant);
          const aiImgUrl = variant.url;
          // Freeform: use the agent's template tag (e.g. "T-K Editorial...")
          // when present, otherwise fall back to a sequential variant label.
          const tplShort = (variant.scene_type || '').match(/^T-[A-Z]/)?.[0] || '';
          const label = tplShort
            ? `Variant ${variant.background_index + 1} · ${tplShort}`
            : (variant.template_id === 'legacy'
                ? `Variant ${variant.background_index + 1}`
                : `Variant ${variant.background_index + 1} · ${variant.template_id}`);

          return (
            <div
              key={idx}
              onClick={() => setSelectedVisual(idx)}
              className={`aspect-square rounded-[14px] relative cursor-pointer overflow-hidden transition-all shadow-sm group border-2
                            ${selectedVisual === idx ? 'border-[#F55600] ring-4 ring-[#F55600]/10 shadow-lg' : 'border-slate-200 hover:border-[#F55600]/40'}`}>

              {aiImgUrl ? (
                <>
                  {variant.media_type === 'document' ? (
                    // Carousel — swipeable per-slide PNG viewer with arrows.
                    // Stops the slide-PNG <img> drag from bubbling but does
                    // NOT stop the tile click (selection still works).
                    <div className="relative w-full h-full bg-slate-100">
                      <CarouselSwiper
                        slides={variant.slides || []}
                        variant="tile"
                        enableKeyboard={false}
                        showRoleBadge={true}
                      />
                    </div>
                  ) : (
                    <img src={aiImgUrl} alt={label} className="w-full h-full object-cover group-hover:scale-110 transition-transform duration-700" />
                  )}
                  <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/20 to-transparent opacity-0 group-hover:opacity-100 transition-all duration-300 flex flex-col justify-end p-3">
                    <p className="text-[9px] font-semibold text-[#F55600] uppercase tracking-widest mb-1">{label}</p>
                    <p className="text-[8px] text-white font-bold leading-tight line-clamp-2">{variant.scene_type || "Template variant"}</p>

                    <div 
                      onClick={(e) => {
                        e.stopPropagation();
                        setEnlargedImage({ ...variant, index: idx });
                      }}
                      className="absolute bottom-3 right-3 transform scale-0 group-hover:scale-100 transition-transform duration-300 cursor-pointer"
                    >
                      <div className="w-6 h-6 bg-[#F55600] hover:bg-[#e65a2b] rounded-md flex items-center justify-center border border-white/30 shadow-lg transition-colors">
                        <Maximize2 className="w-3 h-3 text-white" />
                      </div>
                    </div>
                  </div>

                  {/* Template badge (top-left, always visible) */}
                  {variant.template_id !== 'legacy' && (
                    <div className="absolute top-2 left-2 bg-[#2B2926]/60 text-white text-[9px] font-semibold px-1.5 py-0.5 rounded z-10">
                      {variant.template_id}
                    </div>
                  )}

                  {/* Selection checkmark (top-right) */}
                  <div
                    onClick={(e) => toggleSelection(e, idx)}
                    className={`absolute top-2 right-2 w-5 h-5 rounded-full flex items-center justify-center shadow-md z-20 transition-all duration-200
                                ${selectedVisual === idx ? 'bg-[#F55600] scale-110' : 'bg-white/40 border border-white/50 opacity-0 group-hover:opacity-100'}`}>
                    <CheckCircle2 className={`w-3.5 h-3.5 ${selectedVisual === idx ? 'text-white' : 'text-slate-200 hover:text-white'}`} />
                  </div>
                </>
              ) : (
                <div className="absolute inset-0 bg-white flex flex-col items-center justify-center p-4 text-center">
                  <p className="font-bold text-[9px] text-[#F55600]">{label}</p>
                  <RefreshCw className="w-3 h-3 text-[#F55600] animate-spin mt-1" />
                </div>
              )}
            </div>
          );
        })}

        {uploadedImageUrl && (
          <div
            onClick={() => setEnlargedImage({ url: uploadedImageUrl, name: 'Uploaded Image', index: 'uploaded' })}
            className={`aspect-square rounded-[16px] relative cursor-pointer overflow-hidden transition-all shadow-sm group border-2
                          ${selectedVisual === 'uploaded' ? 'border-[#F55600] ring-4 ring-[#F55600]/10 shadow-lg' : 'border-slate-200 hover:border-[#F55600]/40'}`}>
            <img src={uploadedImageUrl} alt="Uploaded" className="w-full h-full object-contain bg-slate-50 transition-transform duration-700 group-hover:scale-105" />
            
            <div className="absolute inset-0 bg-[#2B2926]/40 flex flex-col items-center justify-center opacity-0 group-hover:opacity-100 transition-all">
               <Maximize2 className="w-5 h-5 text-white mb-2" />
               <p className="font-bold text-[10px] text-white tracking-widest uppercase">Expand View</p>
            </div>

            <div 
               onClick={(e) => toggleSelection(e, 'uploaded')}
               className={`absolute top-3 right-3 w-6 h-6 rounded-full flex items-center justify-center shadow-md z-20 transition-all duration-200 
                           ${selectedVisual === 'uploaded' ? 'bg-[#F55600] scale-110' : 'bg-white/40 border border-white/50 opacity-0 group-hover:opacity-100'}`}>
               <CheckCircle2 className={`w-4 h-4 ${selectedVisual === 'uploaded' ? 'text-white' : 'text-slate-200 hover:text-white'}`} />
            </div>
          </div>
        )}
      </div>

      {showCustomPrompt && (
        <form onSubmit={handleCustomSubmit} className="mb-6 animate-in slide-in-from-top-2 duration-300 relative z-10 bg-white p-4 rounded-2xl border-2 border-[#F55600]/20 shadow-xl">
          <p className="text-[10px] font-semibold text-[#F55600] uppercase tracking-widest mb-3 flex items-center gap-2">
            <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="m21.64 3.64-1.28-1.28a1.21 1.21 0 0 0-1.72 0L2.36 18.64a1.21 1.21 0 0 0 0 1.72l1.28 1.28a1.2 1.2 0 0 0 1.72 0L21.64 5.36a1.2 1.2 0 0 0 0-1.72Z" /><path d="m14 7 3 3" /></svg>
            Describe your custom visual
          </p>
          <div className="flex gap-2">
            <input 
              autoFocus
              type="text" 
              value={customPromptText} 
              onChange={(e) => setCustomPromptText(e.target.value)}
              placeholder="e.g., A professional team collaborating in a modern office with orange accents..."
              className="flex-1 bg-white border border-slate-200 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#F55600]/20 focus:border-[#F55600] transition-all"
            />
            <button 
              type="submit"
              className="bg-[#F55600] text-white px-6 py-2.5 rounded-xl font-bold text-sm shadow-lg shadow-orange-500/20 hover:scale-105 active:scale-95 transition-all"
            >
              Generate
            </button>
            <button 
              type="button"
              onClick={() => setShowCustomPrompt(false)}
              className="p-2.5 text-slate-400 hover:text-slate-600 transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </form>
      )}

      {/* Lightbox Modal - Simplified Popup Refinement */}
      {enlargedImage && createPortal(
        <div className="fixed inset-0 z-[9999] flex items-center justify-center p-4 md:p-8 backdrop-blur-md bg-[#2B2926]/60 animate-in fade-in duration-300">
           {/* Backdrop - Click to close */}
           <div 
             className="absolute inset-0 cursor-pointer"
             onClick={() => setEnlargedImage(null)}
           ></div>

              {/* Main Image Card. Sized to leave clear viewport margins
                  (max-w-[88vw] / max-h-[88vh]) so the close button stays
                  fully on-screen rather than disappearing above the fold. */}
              <div className="relative w-full max-w-[88vw] max-h-[88vh] z-10 animate-in zoom-in-95 duration-200">
                {/* Close Button — placed INSIDE the card (top-right) so it's
                    always visible regardless of viewport size. White circle,
                    large hit area, clear icon. */}
                <button
                  onClick={() => setEnlargedImage(null)}
                  aria-label="Close"
                  className="absolute top-3 right-3 z-20 w-10 h-10 rounded-full bg-white/95 hover:bg-white text-[#2B2926] hover:text-[#F55600] flex items-center justify-center shadow-2xl border border-white/40 transition-all"
                >
                  <X className="w-5 h-5" strokeWidth={3} />
                </button>

                <div className="relative w-full bg-slate-950 rounded-[24px] overflow-hidden shadow-[0_30px_90px_rgba(0,0,0,0.6)] flex flex-col border border-white/10">
                  <div className="relative overflow-hidden flex items-center justify-center min-h-[50vh] max-h-[85vh]">
                      {(enlargedImage.media_type === 'document' || /\.pdf(\?|$)/i.test(enlargedImage.url || '')) ? (
                        <div className="w-[80vw] max-w-[1024px] h-[80vh] bg-white">
                          <CarouselSwiper
                            slides={enlargedImage.slides || []}
                            variant="full"
                            enableKeyboard={true}
                            pdfTitle={enlargedImage.pdf_title || enlargedImage.heading}
                            showRoleBadge={true}
                          />
                        </div>
                      ) : (
                        <img
                          src={enlargedImage.url}
                          alt={enlargedImage.name}
                          className="w-auto h-auto max-w-full max-h-[85vh] object-contain"
                        />
                      )}
                      
                  </div>

                  {/* Action bar — sits BELOW the image so the SELECTED /
                      CHOOSE THIS DESIGN button never covers artwork. */}
                  <div className="flex items-center justify-center gap-3 px-4 py-4 bg-[#1a1918] border-t border-white/10">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleSelection(e, enlargedImage.index);
                      }}
                      className={`flex items-center gap-2 px-10 py-3 rounded-2xl font-semibold text-xs transition-all duration-300 border-2 shadow-lg
                                  ${selectedVisual === enlargedImage.index
                                    ? 'border-[#F55600] bg-[#F55600] text-white'
                                    : 'border-white/20 bg-[#2B2926]/40 text-white hover:bg-[#2B2926]/60'}`}
                    >
                      <CheckCircle2 className="w-5 h-5" />
                      {selectedVisual === enlargedImage.index ? 'SELECTED' : 'CHOOSE THIS DESIGN'}
                    </button>
                  </div>
                </div>
              </div>
        </div>,
        document.body
      )}

      <div className="grid grid-cols-2 sm:flex sm:flex-wrap gap-2.5 relative z-10 w-full">
        <button
          onClick={onRegenerate}
          className="flex items-center justify-center gap-2 px-6 py-2.5 bg-[#2B2926] text-white rounded-xl text-[13px] font-semibold tracking-wide border-2 border-[#2B2926] hover:bg-[#F55600] hover:border-[#F55600] shadow-lg transition-all active:scale-95"
        >
          <RefreshCw className="w-4 h-4" /> Regenerate
        </button>
        <button
          onClick={() => setShowCustomPrompt(!showCustomPrompt)}
          className={`flex items-center justify-center gap-2 px-6 py-2.5 rounded-xl text-[13px] font-semibold tracking-wide border-2 shadow-lg transition-all active:scale-95
                      ${showCustomPrompt ? 'bg-[#F55600] text-white border-[#F55600]' : 'bg-[#2B2926] text-white border-[#2B2926] hover:bg-[#F55600] hover:border-[#F55600]'}`}
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-wand-2"><path d="m21.64 3.64-1.28-1.28a1.21 1.21 0 0 0-1.72 0L2.36 18.64a1.21 1.21 0 0 0 0 1.72l1.28 1.28a1.2 1.2 0 0 0 1.72 0L21.64 5.36a1.2 1.2 0 0 0 0-1.72Z" /><path d="m14 7 3 3" /><path d="M5 6v4" /><path d="M19 14v4" /><path d="M10 2v2" /><path d="M7 8H3" /><path d="M21 16h-4" /><path d="M11 3H9" /></svg>
          Custom Prompt
        </button>
        <input type="file" ref={fileInputRef} className="hidden" accept="image/*" onChange={handleImageUpload} />
        
        {!['free', 'starter'].includes(user?.pricing_plan) && (
          <button
            onClick={async () => {
              const width = 1000, height = 800, left = window.screen.width / 2 - width / 2, top = window.screen.height / 2 - height / 2;
              try {
                const response = await authAxios.get('/auth/canva/authorize');
                if (response.data.url) {
                  window.open(response.data.url, 'Design with Canva', `width=${width},height=${height},top=${top},left=${left}`);
                } else {
                  alert("Could not initialize Canva. Please check your credentials.");
                }
              } catch (err) {
                console.error("Auth failed", err);
                toast.error("Canva integration error. Please contact support.");
              }
            }}
            className="flex items-center justify-center gap-2 px-6 py-2.5 bg-[#10B981] text-white rounded-xl text-[13px] font-semibold tracking-wide border-2 border-[#10B981] hover:bg-[#10B981]/90 transition-all shadow-lg active:scale-95">
            <span className="text-base font-semibold">C</span>
            Design with Canva
          </button>
        )}

        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={isUploadingImage}
          className="flex items-center justify-center gap-2 px-6 py-2.5 bg-[#F55600] text-white rounded-xl text-[13px] font-semibold tracking-wide border-2 border-[#F55600] hover:bg-[#F55600]/90 transition-all shadow-lg active:scale-95">
          {isUploadingImage ? <RefreshCw className="w-4 h-4 animate-spin" /> : <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-upload"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" x2="12" y1="3" y2="15" /></svg>}
          {isUploadingImage ? 'Uploading...' : 'Upload Own'}
        </button>
      </div>
    </div>
  );
};

export default VisualGallery;
