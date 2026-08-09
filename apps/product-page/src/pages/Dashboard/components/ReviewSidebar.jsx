import React, { useState } from 'react';
import { Cpu, CheckCircle2, Zap, Eye, Linkedin, Instagram, Facebook, Image as ImageIcon, ThumbsUp, MessageSquare, Repeat2, Send } from 'lucide-react';
import { flattenVisuals } from './VisualGallery';
import XIcon from '../../../components/icons/XIcon';
import CarouselSwiper from '../../../components/CarouselSwiper';

// Treat a visual tile as a document/PDF if it carries media_type='document'
// OR the URL ends in .pdf - mirrors the helper used by Drafts/Posts.
const isDocVisual = (v) => !!v && (v.media_type === 'document' || /\.pdf(\?|$)/i.test(v.url || ''));

const ReviewSidebar = ({
  selectedDashboardPlatforms,
  previewPlatform,
  setPreviewPlatform,
  previewAccount,
  generatedData,
  selectedVariants,
  selectedVisual,
  uploadedImageUrl
}) => {
  const [showFullText, setShowFullText] = useState(false);

  return (
    <div className="space-y-2">
      {/* Pipeline Card */}
      <div className="bg-white rounded-[20px] p-4 border border-[#2B2926]/30 shadow-[0_8px_24px_rgba(43,41,38,0.06)] relative overflow-hidden">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2 text-[#2B2926] font-semibold text-[13px]">
            <Cpu className="w-3.5 h-3.5 text-[#F55600]" />
            <span>Review Pipeline</span>
          </div>
          <div className="bg-white text-[#2B2926] text-[9px] font-semibold px-2 py-0.5 rounded-full uppercase tracking-[0.14em] border border-[#2B2926]/20">In Review</div>
        </div>
 
        <div className="space-y-2 relative">
          <div className="absolute left-[11px] top-2 bottom-2 w-px bg-slate-50"></div>
          {[
            { label: 'Strategy Refined', sub: 'Brief optimized by Agent', time: '2m ago', status: 'done' },
            { label: 'Deep Research', sub: 'Found trending contexts', time: '1m ago', status: 'done' },
            { label: 'Strategic Drafting', sub: '3-variant content ready', time: '30s ago', status: 'done' },
            {
              label: 'Visual Design',
              sub: (() => {
                const v0 = generatedData?.visuals?.[0];
                if (v0?.pipeline === 'carousel-gpt-image-2') {
                  return `Carousel PDF · ${v0.slide_count || 3} slides`;
                }
                if (v0?.pipeline === 'magic-gpt-image-2') {
                  return 'Magic image variants synced';
                }
                return 'Imagen 4.0 variants synced';
              })(),
              time: '10s ago',
              status: 'done',
            },
            { label: 'Your Final Review', sub: 'Select, edit, or approve', time: 'Now', status: 'active' },
          ].map((step, i) => (
            <div key={i} className="flex gap-3 relative z-10">
              <div className={`w-5 h-5 rounded-full flex items-center justify-center border-2 transition-all ${step.status === 'done' ? 'bg-[#50C878] border-[#50C878] text-white' :
                  step.status === 'active' ? 'bg-white border-[#F55600] text-[#F55600] shadow-lg shadow-orange-50' :
                    'bg-white border-[#2B2926]/30 text-slate-200'
                }`}>
                {step.status === 'done' ? <CheckCircle2 className="w-3 h-3" /> :
                  step.status === 'active' ? <Zap className="w-3 h-3 fill-[#F55600]" /> :
                    <span className="text-[9px]">{i + 1}</span>}
              </div>
              <div className="flex-1">
                <div className="flex justify-between items-start">
                  <p className={`text-[11px] font-semibold text-[#2B2926]`}>{step.label}</p>
                  <span className="text-[9px] text-[#67655E] font-medium">{step.time}</span>
                </div>
                <p className="text-[10px] text-[#67655E] font-normal leading-snug mt-0.5">{step.sub}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
 
      {/* Live Preview Card */}
      <div className="bg-white rounded-[20px] p-4 border border-[#2B2926]/30 shadow-[0_8px_24px_rgba(43,41,38,0.06)] relative overflow-hidden">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2 text-[#2B2926] font-semibold text-[13px]">
            <Eye className="w-3.5 h-3.5 text-[#F55600]" />
            <span>Live Preview</span>
          </div>
          <div className="flex gap-1">
            {selectedDashboardPlatforms.map(p => (
              <button
                key={p}
                onClick={() => setPreviewPlatform(p)}
                className={`w-6 h-6 rounded-full flex items-center justify-center text-[9px] font-semibold border-2 transition-all ${previewPlatform === p
                    ? (p === 'linkedin' ? 'bg-[#0A66C2] border-[#0A66C2] text-white' :
                       p === 'twitter'  ? 'bg-[#2B2926] border-[#2B2926] text-white' :
                       p === 'instagram'? 'bg-[#E4405F] border-[#E4405F] text-white' :
                                          'bg-[#1877F2] border-[#1877F2] text-white')
                    : 'bg-white border-[#2B2926]/30 text-[#2B2926] hover:border-[#2B2926] hover:text-[#2B2926]'
                  }`}
              >
                {p === 'linkedin' ? <img src="/linkedlin.jpg" className="w-4 h-4 object-contain rounded-sm" alt="LinkedIn" />
                 : p === 'twitter' ? 'X'
                 : p === 'instagram' ? <img src="/instagram.jpg" className="w-4 h-4 object-contain rounded-sm" alt="Instagram" />
                 : <img src="/facebook.png" className="w-4 h-4 object-contain rounded-sm" alt="Facebook" />}
              </button>
            ))}
          </div>
        </div>
 
        {/* Mock Social Post (Light Theme) */}
        <div className="bg-[#FAF8F4] rounded-xl p-4 border border-[#2B2926]/15">
          <div className="flex items-start justify-between mb-3">
            <div className="flex items-center gap-2.5">
              {previewAccount.profile_picture_url ? (
                <img src={previewAccount.profile_picture_url} alt="" className="w-8 h-8 rounded-full object-cover shadow-lg shadow-orange-100" />
              ) : (
                <div className={`w-8 h-8 rounded-full flex items-center justify-center text-white shadow-lg shadow-orange-100 ${
                  previewPlatform === 'linkedin' ? 'bg-[#0077b5]' :
                  previewPlatform === 'twitter' ? 'bg-slate-900' :
                  previewPlatform === 'facebook' ? 'bg-[#1877F2]' :
                  'bg-gradient-to-tr from-[#f09433] via-[#e6683c] to-[#bc1888]'
                }`}>
                  {previewPlatform === 'linkedin' && <img src="/linkedlin.jpg" className="w-4 h-4 object-contain" alt="LinkedIn" />}
                  {previewPlatform === 'twitter' && <XIcon className="w-4 h-4" />}
                  {previewPlatform === 'instagram' && <img src="/instagram.jpg" className="w-4 h-4 object-contain" alt="Instagram" />}
                  {previewPlatform === 'facebook' && <img src="/facebook.png" className="w-4 h-4 object-contain" alt="Facebook" />}
                </div>
              )}
              <div>
                <p className="text-[#2B2926] font-semibold text-[12px] leading-none">{previewAccount.name}</p>
                <p className="text-[#67655E] text-[10px] mt-0.5 font-normal capitalize tracking-tight">{previewPlatform}</p>
              </div>
            </div>
            <div className={`w-5 h-5 rounded flex items-center justify-center ${previewPlatform === 'linkedin' ? 'bg-[#0077b5]' :
                previewPlatform === 'twitter' ? 'bg-slate-900' :
                  'bg-gradient-to-tr from-yellow-400 via-red-500 to-purple-600'
              }`}>
              {previewPlatform === 'linkedin' && <img src="/linkedlin.jpg" className="w-2.5 h-2.5 object-contain rounded-sm" alt="LinkedIn" />}
              {previewPlatform === 'twitter' && <XIcon className="w-2.5 h-2.5 text-white fill-white" />}
              {previewPlatform === 'instagram' && <img src="/instagram.jpg" className="w-2.5 h-2.5 object-contain rounded-sm" alt="Instagram" />}
              {previewPlatform === 'facebook' && <img src="/facebook.png" className="w-2.5 h-2.5 object-contain rounded-sm" alt="Facebook" />}
            </div>
          </div>
 
          <div className="bg-white rounded-lg p-3 border border-[#2B2926]/15 mb-2 relative group/text">
            <p className={`text-[#2B2926] text-[11px] font-normal leading-[1.65] transition-all duration-300 ${!showFullText ? 'line-clamp-4' : ''}`}>
              {generatedData?.content?.[previewPlatform]?.[selectedVariants[previewPlatform]] || "Select a variant to preview..."}
            </p>
            {generatedData?.content?.[previewPlatform]?.[selectedVariants[previewPlatform]]?.length > 150 && (
              <button 
                onClick={() => setShowFullText(!showFullText)}
                className="mt-1 text-[#F55600] text-[10px] font-semibold uppercase tracking-[0.14em] hover:underline flex items-center gap-1"
              >
                {showFullText ? 'Show Less' : 'Read Full Post'}
              </button>
            )}
          </div>
 
          {(selectedVisual === 'uploaded' && uploadedImageUrl) ? (
            <div className="aspect-square rounded-lg mb-3 relative overflow-hidden border border-[#2B2926]/30">
              {/\.pdf(\?|$)/i.test(uploadedImageUrl) ? (
                <>
                  <iframe
                    src={`${uploadedImageUrl}#toolbar=0&navpanes=0&scrollbar=0&view=Fit`}
                    title="Uploaded PDF preview"
                    className="w-full h-full pointer-events-none bg-white"
                  />
                  <div className="absolute top-1 left-1 bg-[#F55600] text-white text-[8px] font-black uppercase tracking-widest px-1.5 py-0.5 rounded">
                    PDF Carousel
                  </div>
                </>
              ) : (
                <img src={uploadedImageUrl} alt="Uploaded Preview" className="w-full h-full object-contain max-h-64" />
              )}
            </div>
          ) : (typeof selectedVisual === 'number' && flattenVisuals(generatedData)[selectedVisual]?.url) ? (
            <div className="aspect-square rounded-lg mb-3 relative overflow-hidden border border-[#2B2926]/30">
              {isDocVisual(flattenVisuals(generatedData)[selectedVisual]) ? (
                <CarouselSwiper
                  slides={flattenVisuals(generatedData)[selectedVisual].slides || []}
                  variant="preview"
                  enableKeyboard={false}
                />
              ) : (
                <img src={flattenVisuals(generatedData)[selectedVisual].url} alt="AI Preview" className="w-full h-full object-contain max-h-64" />
              )}
            </div>
          ) : (
            <div className="aspect-square rounded-lg mb-3 flex items-center justify-center relative overflow-hidden border border-[#2B2926]/30 bg-white">
              <div className="absolute inset-0 opacity-5 bg-[radial-gradient(circle_at_center,_var(--tw-gradient-stops))] from-orange-500 via-transparent to-transparent"></div>
              <div className="flex items-center gap-2">
                 <ImageIcon className="w-3.5 h-3.5 text-[#2B2926]" />
                 <span className="text-[10px] font-semibold text-[#67655E] uppercase tracking-[0.14em]">No Visual</span>
              </div>
            </div>
          )}
 
          <div className="flex items-center justify-between pt-2 border-t border-[#2B2926]/30">
            <div className="flex gap-3.5">
              <ThumbsUp strokeWidth={2.5} className="w-[18px] h-[18px] text-[#2B2926] hover:text-[#F55600] transition-all cursor-pointer" />
              <MessageSquare strokeWidth={2.5} className="w-[18px] h-[18px] text-[#2B2926] hover:text-[#F55600] transition-all cursor-pointer" />
              <Repeat2 strokeWidth={2.5} className="w-[18px] h-[18px] text-[#2B2926] hover:text-[#F55600] transition-all cursor-pointer" />
              <Send strokeWidth={2.5} className="w-[18px] h-[18px] text-[#2B2926] hover:text-[#F55600] transition-all cursor-pointer" />
            </div>
            <div className="w-1 h-1 rounded-full bg-orange-200"></div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ReviewSidebar;
