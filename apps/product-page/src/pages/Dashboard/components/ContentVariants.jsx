import React from 'react';
import {
  Zap,
  ThumbsUp,
  Cpu,
  CheckCircle2,
  FileEdit,
  RefreshCw,
  Sparkles
} from 'lucide-react';

const ContentVariants = ({
  generatedData,
  activeReviewPlatform,
  selectedVariants,
  setSelectedVariants,
  editingVariant,
  setEditingVariant,
  tempText,
  setTempText,
  handleSaveEdit,
  handleGenerateContent
}) => {
  return (
    <div className="bg-white rounded-[20px] p-5 border border-[#2B2926]/30 shadow-lg relative overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-5 relative z-10">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-7 h-7 rounded-xl bg-[#2B2926]/[0.04] flex items-center justify-center border border-[#2B2926]/15 text-[#2B2926] shrink-0">
            <Cpu className="w-3.5 h-3.5" />
          </div>
          <h3 className="text-base font-semibold text-[#2B2926] tracking-tight">AI Content</h3>
        </div>
        <div className="flex flex-wrap gap-1.5 shrink-0">
          <button
            onClick={handleGenerateContent}
            className="px-2.5 sm:px-3 py-1.5 bg-[#2B2926]/5 text-[#2B2926] rounded-lg text-[9px] font-semibold border-2 border-[#2B2926]/15 hover:bg-[#F55600] hover:text-white hover:border-[#F55600] transition-all flex items-center gap-1.5 uppercase tracking-widest whitespace-nowrap"
          >
            <RefreshCw className="w-3 h-3" /> Re-Analyze
          </button>
          <div className="px-2.5 sm:px-3 py-1.5 bg-white text-[#2B2926] rounded-lg text-[9px] font-semibold border border-[#2B2926]/20 uppercase tracking-widest whitespace-nowrap">
            {(() => {
              const platformContent = generatedData?.content?.[activeReviewPlatform?.toLowerCase()] || {};
              const hasFestivalVariant = typeof platformContent.festival_variant === 'string' && platformContent.festival_variant.trim().length > 0;
              const count = hasFestivalVariant ? 4 : 3;
              return (
                <>
                  <span className="sm:hidden">{count} Variants</span>
                  <span className="hidden sm:inline">{count} Strategic Variants</span>
                </>
              );
            })()}
          </div>
        </div>
      </div>

      <div className="space-y-2">
        {(() => {
          // v3: festival_variant is rendered as a 4th card ONLY when the
          // copywriter actually emitted it (research.festival_alerts was
          // non-empty). When the brief mentioned the festival or there was
          // no festival today/tomorrow, the key is absent and we hide the card.
          const platformContent = generatedData?.content?.[activeReviewPlatform?.toLowerCase()] || {};
          const hasFestivalVariant = typeof platformContent.festival_variant === 'string' && platformContent.festival_variant.trim().length > 0;
          return ['viral_reach', 'high_interaction', 'follower_growth', ...(hasFestivalVariant ? ['festival_variant'] : [])];
        })().map((vType, idx) => {
          const isSelected = selectedVariants[activeReviewPlatform] === vType;
          const isRecommended = generatedData?.recommendation?.best_variant === vType;
          const Icon =
            vType === 'viral_reach' ? Zap
            : vType === 'high_interaction' ? ThumbsUp
            : vType === 'follower_growth' ? Cpu
            : Sparkles;

          const label =
            vType === 'viral_reach' ? 'reach'
            : vType === 'high_interaction' ? 'engagement'
            : vType === 'follower_growth' ? 'brand'
            : 'festival';
          const description =
            vType === 'viral_reach' ? 'Optimized for viral discovery'
            : vType === 'high_interaction' ? 'Designed for high interaction'
            : vType === 'follower_growth' ? 'Focused on brand conversion'
            : 'Festive moment your brief forgot';
          
          return (
            <div 
              key={vType} 
              style={{ animationDelay: `${idx * 150}ms` }}
              className={`animate-fade-in-up p-3 rounded-[24px] border-2 transition-all relative group cursor-pointer
                ${isSelected ? 'border-[#F55600] bg-white shadow-xl ring-4 ring-[#F55600]/5' : 'border-[#2B2926]/30 bg-white hover:border-[#F55600]/30 hover:bg-white hover:shadow-md'}`}
              onClick={() => setSelectedVariants(v => ({ ...v, [activeReviewPlatform]: vType }))}
            >
              <div className="flex items-start justify-between mb-2.5">
                <div className="flex items-center gap-4">
                    <div className={`w-8 h-8 rounded-lg flex items-center justify-center transition-all duration-500 scale-100 group-hover:scale-105 shadow-sm border-2 ${isSelected ? 'bg-[#F55600] border-[#F55600] text-white shadow-[#F55600]/20' : 'bg-white border-[#2B2926]/30 text-[#2B2926]'}`}>
                      <Icon className={`w-4 h-4 ${isSelected ? 'animate-pulse-soft text-white' : ''}`} />
                    </div>
                   <div>
                      <div className="flex items-center gap-2 mb-1">
                        <h4 className={`text-[12px] font-semibold uppercase tracking-widest text-[#F55600]`}>{label}</h4>
                        {isRecommended && (
                          <span className="px-2 py-1 bg-[#10B981] text-white text-[10px] font-semibold rounded-md uppercase tracking-widest shadow-lg animate-pulse">AI Recommended Strategy</span>
                        )}
                      </div>
                      <p className="text-[12px] font-semibold text-[#2B2926] uppercase tracking-tight opacity-100">
                        {description}
                      </p>
                   </div>
                </div>
                 {isSelected && (
                  <div className="bg-green-50 text-green-600 p-1 rounded-md border border-green-100 shadow-sm animate-in zoom-in-50">
                    <CheckCircle2 className="w-3 h-3" />
                  </div>
                )}
              </div>

              {editingVariant === vType ? (
                <div className="space-y-3 animate-in fade-in slide-in-from-top-1">
                  <textarea
                    value={tempText}
                    onChange={(e) => setTempText(e.target.value)}
                    className="w-full h-32 p-4 bg-white border-2 border-[#F55600]/20 rounded-xl text-xs text-[#2B2926] leading-relaxed focus:outline-none focus:border-[#F55600] focus:ring-4 focus:ring-[#F55600]/10 transition-all font-bold placeholder:text-[#2B2926]"
                    autoFocus
                  />
                  <div className="flex gap-2 justify-end">
                    <button onClick={(e) => { e.stopPropagation(); setEditingVariant(null); }} className="px-4 py-2 text-[9px] font-semibold uppercase tracking-widest text-[#2B2926] hover:text-[#2B2926] transition-colors">Cancel</button>
                    <button onClick={(e) => { e.stopPropagation(); handleSaveEdit(vType); }} className="px-6 py-2 bg-[#F55600] text-white rounded-lg text-[9px] font-semibold uppercase tracking-widest shadow-lg shadow-orange-100 active:scale-95 transition-all">Save Changes</button>
                  </div>
                </div>
              ) : (
                <div className="relative group/content">
                  <div className="absolute -right-1 -top-1 opacity-0 group-hover/content:opacity-100 transition-all z-20">
                    <button 
                      onClick={(e) => {
                        e.stopPropagation();
                        setEditingVariant(vType);
                        setTempText(generatedData.content?.[activeReviewPlatform.toLowerCase()]?.[vType] || "");
                      }}
                      className="p-2 bg-white text-[#F55600] rounded-xl shadow-lg border border-orange-50 hover:scale-110 active:scale-95 transition-all"
                    >
                      <FileEdit className="w-3.5 h-3.5" />
                    </button>
                  </div>
                    {/* Outer box owns the border + radius and clips the
                        scrollbar to the rounded corners; the inner div does
                        the scrolling with a native `overflow-y-auto` that
                        only appears when the text actually overflows (the
                        global `custom-scrollbar` class force-shows an empty
                        track, so it is intentionally not used here). */}
                    <div className={`rounded-xl transition-all bg-white border-2 border-[#2B2926]/15 group-hover:border-[#F55600]/30 overflow-hidden ${isSelected ? 'bg-white shadow-sm ring-2 ring-[#F55600]/5 !border-[#F55600]/40' : ''}`}>
                      <div className="p-3 max-h-48 overflow-y-auto">
                        <p className={`text-[11px] font-semibold leading-relaxed whitespace-pre-wrap italic ${isSelected ? 'text-[#2B2926]' : 'text-[#2B2926]'}`}>
                          "{generatedData?.content?.[activeReviewPlatform.toLowerCase()]?.[vType] || "Drafting strategy..."}"
                        </p>
                      </div>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default ContentVariants;
