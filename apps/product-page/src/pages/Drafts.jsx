import React, { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { FileEdit, Trash2, Send, Clock, Layers, Search, Plus, X, Image as ImageIcon, RefreshCw, PlusCircle, ChevronLeft, ChevronRight, Maximize2, Minimize2 } from 'lucide-react';
import PlatformLogo from '../components/PlatformLogo';
import { isDocumentMedia } from '../components/PostMedia';
import PdfThumbnail from '../components/PdfThumbnail';
import { formatInTimezone } from '../utils/timezones';
import { useNotification } from '../context/NotificationContext';
import { isReadOnly } from '../lib/permissions';

const EditDraftModal = ({ draft, isOpen, onClose, onSave }) => {
  // Fix — drop platforms whose account list is empty so the modal's tab
  // strip and the outer card icons stay in sync. Empty-target keys were
  // showing up as phantom tabs when the user had toggled all accounts
  // off for a platform without deleting the key.
  const targets = Object.entries(draft.targets || {})
    .filter(([, accts]) => Array.isArray(accts) && accts.length > 0)
    .map(([p]) => p);
  const platforms = ['default', ...targets];
  const [activeTab, setActiveTab] = useState('default');
  const [contents, setContents] = useState({});
  const [imageUrl, setImageUrl] = useState(draft.image_url);
  const fileInputRef = useRef(null);

  // D-6: reset ALL modal state whenever the draft prop changes (new edit) OR
  // the modal opens. Previously `contents` only got set when draft.content
  // existed, and `activeTab` never reset — so opening a second draft could
  // show the first draft's content if fields were empty, and the tab stayed
  // on whatever platform the user last clicked on the previous draft.
  useEffect(() => {
    if (!isOpen) return;
    setActiveTab('default');
    if (draft.content && draft.content.startsWith('{')) {
      try {
        setContents(JSON.parse(draft.content));
      } catch {
        setContents({ default: draft.content });
      }
    } else {
      setContents({ default: draft.content || '' });
    }
    setImageUrl(draft.image_url || null);
  }, [draft, isOpen]);

  const handleContentChange = (val) => {
    setContents(prev => ({ ...prev, [activeTab]: val }));
  };

  const handleImageChange = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onloadend = () => setImageUrl(reader.result);
    reader.readAsDataURL(file);
  };

  const handleSave = () => {
    onSave({
      ...draft,
      content: JSON.stringify(contents),
      image_url: imageUrl
    });
  };

  if (!isOpen) return null;

  // Rendered via a Portal to document.body so `position: fixed`
  // resolves against the real viewport. A transformed ancestor
  // (animate-in / framer-motion) otherwise makes `fixed` behave like
  // `absolute`, which opened the modal at the top of the page instead
  // of centred where the user clicked.
  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 animate-in fade-in duration-300">
      <div className="absolute inset-0" onClick={onClose} />
      <div className="bg-white rounded-2xl w-full max-w-4xl max-h-[90vh] overflow-hidden flex flex-col shadow-2xl animate-in zoom-in-95 duration-300 border-2 border-slate-350">
        {/* Header */}
        <div className="p-8 border-b-2 border-[#2B2926]/30 bg-white flex items-center justify-between">
          <div>
            <h2 className="text-3xl font-semibold text-[#2B2926]">Edit Draft</h2>
            <p className="text-sm text-slate-500 mt-1">Customize content for each platform</p>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-slate-100 rounded-xl transition-all text-slate-400 hover:text-slate-600">
            <X className="w-6 h-6" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-8">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-10">
            {/* Left: Text Editor */}
            <div className="space-y-6">
              {/* Platform Tabs — unselected tabs now use solid black
                  text (was faint slate-500) so every platform label is
                  clearly readable; selected tab keeps the white pill +
                  brand-orange text. */}
              <div className="flex flex-wrap gap-2 p-2 bg-[#2B2926]/5 rounded-xl w-fit">
                {platforms.map(p => (
                  <button
                    key={p}
                    onClick={() => setActiveTab(p)}
                    className={`px-4 py-2 rounded-lg text-xs font-bold transition-all duration-200 ${
                      activeTab === p
                      ? 'bg-white text-[#F55600] shadow-md'
                      : 'text-[#2B2926] hover:text-[#F55600] hover:bg-white/70'
                    }`}
                  >
                    {p === 'default' ? 'Default' : p.charAt(0).toUpperCase() + p.slice(1)}
                  </button>
                ))}
              </div>

              {/* Content Editor */}
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <label className="text-xs font-semibold text-[#2B2926] uppercase tracking-widest flex items-center gap-2">
                    {activeTab === 'default' ? <Layers className="w-4 h-4 text-[#F55600]" /> : <PlatformLogo platform={activeTab} className="w-4 h-4" />}
                    {activeTab.toUpperCase()} Content
                  </label>
                  <span className="text-[10px] font-bold text-[#2B2926] bg-[#2B2926]/5 px-3 py-1 rounded-full">
                    {(contents[activeTab] || "").length} chars
                  </span>
                </div>
                {/* D-1 fix: restored the missing <textarea>. The element had
                    been deleted at some point, leaving an orphan className
                    attribute floating inside the wrapping <div> and making
                    the Draft edit modal unusable. */}
                <textarea
                  value={contents[activeTab] || ''}
                  onChange={(e) => handleContentChange(e.target.value)}
                  placeholder={
                    activeTab === 'default'
                      ? 'Default content used for any platform that doesn\'t have its own version...'
                      : `Write the ${activeTab}-specific version here...`
                  }
                  className="w-full h-72 p-6 rounded-xl border-2 border-slate-300 focus:border-[#F55600] focus:ring-2 focus:ring-orange-100 bg-slate-50/50 outline-none resize-none text-sm leading-relaxed transition-all"
                />
              </div>
            </div>

            {/* Right: Media Upload */}
            <div className="space-y-6">
              <label className="text-xs font-semibold text-[#2B2926] uppercase tracking-widest flex items-center gap-2">
                <ImageIcon className="w-4 h-4 text-[#F55600]" /> Visual Media
              </label>
              <div className="aspect-video bg-white rounded-xl border-2 border-dashed border-slate-350 flex flex-col items-center justify-center overflow-hidden group relative transition-all hover:border-[#F55600]">
                {imageUrl ? (
                  <>
                    {isDocumentMedia({ image_url: imageUrl, media_type: draft.media_type }) ? (
                      <div className="w-full h-full flex flex-col items-center justify-center gap-2">
                        <span className="w-14 h-14 rounded-xl bg-[#F55600] text-white text-sm font-black flex items-center justify-center shadow">PDF</span>
                        <span className="text-[10px] font-bold text-[#2B2926] uppercase tracking-widest">Document Carousel</span>
                      </div>
                    ) : (draft.media_type === 'video' || /\.(mp4|mov|webm|m4v)(\?|$)/i.test(imageUrl || '')) ? (
                      // Fix — video drafts opened in the editor were also
                      // rendered as <img>, which broke. Show a native
                      // <video> element with controls so the user can
                      // review the clip inside the modal.
                      <video src={imageUrl} className="w-full h-full object-contain bg-black" controls muted playsInline preload="metadata" />
                    ) : (
                      <img src={imageUrl} alt="Draft Visual" className="w-full h-full object-contain" />
                    )}
                    <div className="absolute inset-0 bg-slate-900/50 opacity-0 group-hover:opacity-100 transition-all flex items-center justify-center gap-3">
                      <button 
                        onClick={() => fileInputRef.current.click()}
                        className="p-3 bg-white text-[#2B2926] rounded-lg hover:scale-110 transition-all shadow-xl font-bold text-xs flex items-center gap-2"
                      >
                        <RefreshCw className="w-4 h-4" /> Change
                      </button>
                      <button 
                        onClick={() => setImageUrl(null)}
                        className="p-3 bg-red-500 text-white rounded-lg hover:scale-110 transition-all shadow-xl font-bold text-xs flex items-center gap-2"
                      >
                        <Trash2 className="w-4 h-4" /> Remove
                      </button>
                    </div>
                  </>
                ) : (
                  <button 
                    onClick={() => fileInputRef.current.click()}
                    className="flex flex-col items-center gap-3 text-slate-400 hover:text-[#F55600] transition-all"
                  >
                    <div className="w-16 h-16 bg-white rounded-2xl flex items-center justify-center shadow-md shadow-slate-200/50 group-hover:scale-110 transition-all">
                      <Plus className="w-8 h-8" />
                    </div>
                    <span className="font-bold text-sm">Upload Media</span>
                  </button>
                )}
                <input ref={fileInputRef} type="file" hidden accept="image/*" onChange={handleImageChange} />
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="p-8 border-t-2 border-[#2B2926]/30 bg-slate-50/50 flex items-center justify-end gap-3">
          <button onClick={onClose} className="px-6 py-3 text-slate-600 font-bold hover:text-[#2B2926] hover:bg-slate-100 rounded-lg transition-all text-sm">
            Discard
          </button>
          <button 
            onClick={handleSave}
            className="px-10 py-3.5 bg-[#F55600] text-white rounded-lg font-bold shadow-lg shadow-orange-200/50 hover:shadow-xl hover:scale-105 transition-all text-sm flex items-center gap-2"
          >
            <FileEdit className="w-4 h-4" />
            Save Changes
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
};

const DraftCard = ({ draft, onEdit, handleDelete, handlePostNow, user, index }) => {
  const [activePlatform, setActivePlatform] = useState('default');
  const [isImagePreviewOpen, setIsImagePreviewOpen] = useState(false);
  const [isImageFullscreen, setIsImageFullscreen] = useState(false);
  const [isPdfPopupOpen, setIsPdfPopupOpen] = useState(false);
  const [pdfPage, setPdfPage] = useState(1);
  // Fix — drop platforms with empty account arrays (see EditDraftModal
  // comment above for the same fix applied to the modal tabs).
  const targets = Object.entries(draft.targets || {})
    .filter(([, accts]) => Array.isArray(accts) && accts.length > 0)
    .map(([p]) => p);
  const isOrange = index % 2 === 0;

  const getContentForPlatform = (content, platform) => {
    if (!content) return "No content";
    try {
      if (content.startsWith('{')) {
        const json = JSON.parse(content);
        if (platform === 'default') return json.default || Object.values(json)[0] || "No content";
        return json[platform] || json.default || "No content";
      }
    } catch (e) {}
    return content;
  };

  return (
    <div className="bg-white rounded-2xl border-2 border-[#2B2926] hover:border-[#F55600] shadow-sm hover:shadow-xl transition-all duration-300 group overflow-hidden flex flex-col h-full">
      {/* Header */}
      <div className="px-3 py-3 border-b-2 border-[#2B2926]/10 bg-white flex items-center justify-between gap-2">
        <div className={`flex items-center gap-1.5 px-2 py-1 rounded-full border shadow-sm animate-in fade-in duration-500 ${
          isOrange ? 'bg-[#F55600] text-white border-[#F55600]' : 'bg-[#10B981] text-white border-[#10B981]'
        }`}>
          <Clock className="w-3 h-3 text-white" />
          <span className="text-[9px] font-semibold uppercase tracking-tight leading-none">
            {draft.updated_at ? formatInTimezone(draft.updated_at, user?.timezone || 'UTC', 'MMM d, h:mm A') : 'Recently'}
          </span>
        </div>
        
        <div className="flex -space-x-2.5 hover:-space-x-1 transition-all duration-300">
          {/* Fix — was `Object.keys(draft.targets || {})` which included
              platform keys with EMPTY account arrays. When the user
              clicked a platform then de-clicked all its accounts, the key
              stayed in selectedTargets with []; that showed up here as a
              phantom icon even though the user hadn't actually selected
              the platform. Filter to only platforms with ≥1 account. */}
          {Object.entries(draft.targets || {})
            .filter(([, accts]) => Array.isArray(accts) && accts.length > 0)
            .map(([p]) => {
            return (
              <button
                key={p}
                onClick={(e) => {
                  e.stopPropagation();
                  setActivePlatform(p);
                }}
                className={`w-6 h-6 rounded-full flex items-center justify-center text-[8px] font-semibold text-white border transition-all shadow-md active:scale-90 ${
                  activePlatform === p ? 'ring-2 ring-[#F55600]/20 scale-110 z-20 border-white' : 'hover:z-10 border-white/50'
                } bg-white`}
                title={p}
              >
                {/* Fix — whitelist was 5 platforms; Pinterest + TikTok
                    fell through to the generic Layers icon. PlatformLogo
                    already handles all supported platforms, so let it
                    render for the full set instead of gating manually. */}
                {['linkedin', 'facebook', 'instagram', 'twitter', 'youtube', 'pinterest', 'tiktok'].includes(p) ? <PlatformLogo platform={p} className="w-5 h-5" /> : <Layers className="w-3 h-3" />}
              </button>
            );
          })}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 p-5 space-y-4">
        {draft.image_url && (
          isDocumentMedia(draft) ? (
            <button
              type="button"
              onClick={() => { setPdfPage(1); setIsPdfPopupOpen(true); }}
              className="relative aspect-square w-full rounded-xl overflow-hidden bg-slate-50 border border-[#2B2926]/30 mb-4 cursor-pointer hover:shadow-md hover:border-[#F55600] transition-all group block"
              title="Click to open carousel"
            >
              {/* Prefer the slide-1 PNG (fast, reliable native <img>).
                  Falls back to pdf.js rendering for legacy drafts that
                  haven't been backfilled yet — works on local + any
                  environment that serves .mjs files correctly. After
                  the backfill script runs, this branch never fires. */}
              {draft.thumbnail_url ? (
                <img
                  src={draft.thumbnail_url}
                  alt="Carousel cover slide"
                  className="absolute inset-0 w-full h-full object-cover bg-white"
                  loading="lazy"
                />
              ) : (
                <PdfThumbnail
                  src={draft.image_url}
                  page={1}
                  className="absolute inset-0 w-full h-full bg-white"
                  alt="Carousel cover slide (legacy)"
                />
              )}
              <span className="absolute top-2 left-2 bg-[#F55600] text-white text-[8px] font-black uppercase tracking-widest px-1.5 py-0.5 rounded shadow z-10">
                PDF Carousel
              </span>
              <span className="absolute bottom-2 right-2 bg-[#2B2926]/80 text-white text-[8px] font-black uppercase tracking-widest px-1.5 py-0.5 rounded opacity-0 group-hover:opacity-100 transition-opacity z-10">
                Click to open
              </span>
            </button>
          ) : (draft.media_type === 'video' || /\.(mp4|mov|webm|m4v)(\?|$)/i.test(draft.image_url || '')) ? (
            // Fix — was rendering an <img> for video drafts too, which
            // produced a broken-image icon because the browser can't
            // decode an .mp4 as an image. Now uses a native <video>
            // element with metadata preload so the first frame shows as
            // the thumbnail. Click still opens the preview modal.
            <div
              onClick={() => setIsImagePreviewOpen(true)}
              className="h-48 rounded-xl overflow-hidden bg-black border border-[#2B2926]/30 mb-4 cursor-pointer hover:shadow-md transition-all group relative"
            >
              <video
                src={draft.image_url}
                className="w-full h-full object-contain"
                preload="metadata"
                muted
                playsInline
                controls={false}
              />
              <span className="absolute top-2 left-2 bg-[#F55600] text-white text-[8px] font-black uppercase tracking-widest px-1.5 py-0.5 rounded shadow z-10">
                Video
              </span>
              <span className="absolute inset-0 flex items-center justify-center pointer-events-none">
                <span className="w-12 h-12 bg-black/40 rounded-full flex items-center justify-center">
                  <span className="w-0 h-0 border-l-[14px] border-l-white border-y-[10px] border-y-transparent ml-1"></span>
                </span>
              </span>
            </div>
          ) : (
            <div
              onClick={() => setIsImagePreviewOpen(true)}
              className="h-48 rounded-xl overflow-hidden bg-slate-50 border border-[#2B2926]/30 mb-4 cursor-pointer hover:shadow-md transition-all group"
            >
              <img src={draft.image_url} alt="Draft" className="w-full h-full object-contain group-hover:scale-105 transition-transform duration-300" />
            </div>
          )
        )}
        
        <div className="space-y-3">
          {activePlatform !== 'default' && (
            <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest block">{activePlatform.toUpperCase()} Version</span>
          )}
          <p className="text-[#2B2926] text-sm leading-relaxed line-clamp-5 font-medium whitespace-pre-wrap">
            {getContentForPlatform(draft.content, activePlatform)}
          </p>
        </div>
      </div>

      {/* Footer — action buttons always visible. Backend enforces
          permissions on the actual POST/DELETE so read-only viewers
          will see a clear server-side 403 if they click. */}
      <div className="mt-auto p-5 border-t-2 border-[#2B2926]/10 bg-white flex items-center justify-between">
        <button
          onClick={() => handlePostNow(draft)}
          disabled={isReadOnly(user)}
          className="flex items-center gap-1.5 bg-[#2B2926] text-white px-3 py-1.5 rounded-lg font-semibold text-[9.5px] uppercase tracking-widest hover:bg-slate-900 transition-all shadow-sm active:scale-95 disabled:opacity-40 disabled:cursor-not-allowed"
          title={isReadOnly(user) ? 'Read-only access — ask an admin to publish' : 'Publish this draft now'}
        >
          <Send className="w-3.5 h-3.5" />
          Post Now
        </button>
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => onEdit(draft)}
            disabled={isReadOnly(user)}
            className="p-1.5 rounded-lg transition-all shadow-sm border border-slate-800 text-[#2B2926] hover:bg-slate-50 active:scale-95 disabled:opacity-40 disabled:cursor-not-allowed"
            title={isReadOnly(user) ? 'Read-only access' : 'Edit draft'}
          >
            <FileEdit className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => handleDelete(draft.id)}
            disabled={isReadOnly(user)}
            className="p-1.5 text-red-600 hover:bg-red-50 rounded-lg transition-all shadow-sm border border-red-500 active:scale-95 disabled:opacity-40 disabled:cursor-not-allowed"
            title={isReadOnly(user) ? 'Read-only access' : 'Delete draft'}
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Image Preview Modal — Portal-rendered to document.body so its
          `position: fixed` centres against the viewport instead of
          being clipped/offset by the card's transformed ancestors. */}
      {isImagePreviewOpen && typeof document !== 'undefined' && createPortal(
        <div
          className="fixed inset-0 z-[200] flex items-center justify-center p-4 animate-in fade-in duration-300"
          onClick={() => { setIsImageFullscreen(false); setIsImagePreviewOpen(false); }}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              if (isImageFullscreen) setIsImageFullscreen(false);
              else setIsImagePreviewOpen(false);
            }
          }}
          tabIndex={0}
          autoFocus
        >
          <div className="absolute inset-0 bg-slate-900/55 backdrop-blur-sm" />
          <div
            className={
              isImageFullscreen
                ? "relative bg-black rounded-none shadow-2xl animate-in zoom-in-95 duration-300 w-screen h-screen"
                : "relative bg-white rounded-xl shadow-2xl animate-in zoom-in-95 duration-300 max-w-lg max-h-[60vh]"
            }
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => setIsImageFullscreen(f => !f)}
              className="absolute top-2 right-12 z-10 w-9 h-9 rounded-full bg-[#2B2926] text-white hover:bg-[#F55600] flex items-center justify-center shadow-lg transition-all"
              title={isImageFullscreen ? "Exit full screen" : "Full screen"}
            >
              {isImageFullscreen ? <Minimize2 className="w-5 h-5" /> : <Maximize2 className="w-5 h-5" />}
            </button>
            <button
              onClick={() => { setIsImageFullscreen(false); setIsImagePreviewOpen(false); }}
              className="absolute top-2 right-2 z-10 w-9 h-9 rounded-full bg-[#2B2926] text-white hover:bg-[#F55600] flex items-center justify-center shadow-lg transition-all"
              title="Close preview"
            >
              <X className="w-5 h-5" />
            </button>
            {/* Fix — was always rendering <img>. Video drafts have an .mp4
                URL as image_url which broke as an image. Now branches on
                the same media_type / extension rule the card uses. */}
            {(draft.media_type === 'video' || /\.(mp4|mov|webm|m4v)(\?|$)/i.test(draft.image_url || '')) ? (
              <video
                src={draft.image_url}
                className={isImageFullscreen ? "w-full h-full object-contain bg-black" : "w-full h-full object-contain rounded-xl bg-black"}
                controls
                autoPlay
                playsInline
              />
            ) : (
              <img
                src={draft.image_url}
                alt="Draft Preview"
                className={isImageFullscreen ? "w-full h-full object-contain bg-black" : "w-full h-full object-contain rounded-xl"}
              />
            )}
          </div>
        </div>,
        document.body
      )}

      {/* PDF Carousel Popup Modal — large iframe of the carousel PDF
          with left/right arrows that change the displayed page via
          the #page=N URL fragment (PDF.js native behaviour). Keyboard
          left/right also navigates. Escape closes. */}
      {isPdfPopupOpen && typeof document !== 'undefined' && (() => {
        // Derive total slide count once for arrow bounds + keyboard nav.
        // Priority: slide_thumbnail_urls JSON list (the new field) →
        // fall back to 1 when only thumbnail_url is set → 1 otherwise.
        let _slidesArr = null;
        try {
          _slidesArr = draft.slide_thumbnail_urls
            ? JSON.parse(draft.slide_thumbnail_urls) : null;
        } catch { _slidesArr = null; }
        const slideCount = Array.isArray(_slidesArr) && _slidesArr.length
          ? _slidesArr.length
          : (draft.thumbnail_url ? 1 : 1);
        return createPortal(
        <div
          className="fixed inset-0 z-[300] flex items-center justify-center p-4 animate-in fade-in duration-300"
          onClick={() => setIsPdfPopupOpen(false)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') setIsPdfPopupOpen(false);
            if (e.key === 'ArrowLeft')  { e.preventDefault(); setPdfPage(p => Math.max(1, p - 1)); }
            if (e.key === 'ArrowRight') { e.preventDefault(); setPdfPage(p => Math.min(slideCount, p + 1)); }
          }}
          tabIndex={0}
          autoFocus
        >
          <div className="absolute inset-0 bg-slate-900/70 backdrop-blur-sm" />
          <div
            className="relative bg-white rounded-2xl shadow-2xl animate-in zoom-in-95 duration-300 w-[90vw] max-w-[1024px] h-[88vh] flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Top bar */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200">
              <div className="flex items-center gap-2">
                <span className="bg-[#F55600] text-white text-[10px] font-black uppercase tracking-widest px-2 py-1 rounded">
                  PDF Carousel
                </span>
                <span className="text-[#2B2926] text-sm font-semibold">
                  Slide {pdfPage}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <a
                  href={draft.image_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[10px] font-semibold uppercase tracking-widest text-[#F55600] hover:text-[#e54a00]"
                >
                  Open in tab
                </a>
                <button
                  onClick={() => setIsPdfPopupOpen(false)}
                  className="w-9 h-9 rounded-full bg-[#2B2926] text-white hover:bg-[#F55600] flex items-center justify-center shadow-lg transition-all"
                  title="Close (Esc)"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            {/* PDF body with side arrows.
                Chrome's PDF viewer ignores view=Fit and renders at the
                native page size — so a 1024x1024 slide inside a wider-
                than-tall iframe ends up cropped vertically. Fix: wrap
                the iframe in a SQUARE aspect-ratio box sized to fit
                the modal body. Chrome then renders the page edge-to-
                edge of the square container. */}
            <div className="relative flex-1 overflow-hidden bg-slate-100 flex items-center justify-center p-4">
              <div
                className="relative bg-white shadow-md"
                style={{
                  // Pick the smaller of available width/height so the box
                  // is always fully visible. Subtract padding budget for
                  // the side arrows so the slide never sits under them.
                  width: 'min(100%, calc(88vh - 140px))',
                  aspectRatio: '1 / 1',
                  maxWidth: '100%',
                  maxHeight: '100%',
                }}
              >
                {/* Slide 1: use the cached PNG (always works).
                    Slides 2+ or legacy drafts: open the full PDF in a
                    new tab. pdf.js was unreliable on staging because
                    Amplify's SPA fallback served index.html in place
                    of the worker .mjs file, so we no longer depend on
                    it here. */}
                {(() => {
                  // Try slide_thumbnail_urls (array of all slide PNGs)
                  // first — the cleanest path, every slide renders as
                  // a native <img>. Falls back to thumbnail_url for
                  // slide 1 only. Final fallback is "open the PDF" CTA.
                  let urlsArr = null;
                  try {
                    urlsArr = draft.slide_thumbnail_urls
                      ? JSON.parse(draft.slide_thumbnail_urls)
                      : null;
                  } catch { urlsArr = null; }
                  const slideUrl = (Array.isArray(urlsArr) && urlsArr[pdfPage - 1])
                    || (pdfPage === 1 ? draft.thumbnail_url : null);
                  if (slideUrl) {
                    return (
                      <img
                        key={pdfPage}
                        src={slideUrl}
                        alt={`Carousel slide ${pdfPage}`}
                        className="absolute inset-0 w-full h-full object-contain bg-white"
                      />
                    );
                  }
                  return (
                    <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 bg-gradient-to-br from-orange-50 to-white">
                      <div className="w-24 h-24 rounded-3xl bg-[#F55600] text-white font-black text-2xl flex items-center justify-center shadow-2xl">PDF</div>
                      <div className="text-center px-6">
                        <div className="text-sm font-black text-[#2B2926] uppercase tracking-widest mb-1">Slide {pdfPage}</div>
                        <div className="text-xs text-[#2B2926]/60 mb-4">Preview not stored for this older draft. Open the full PDF to view.</div>
                        <a
                          href={draft.image_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-2 px-5 py-2.5 bg-[#F55600] text-white text-[11px] font-bold uppercase tracking-widest rounded-xl hover:bg-orange-600 transition-all shadow-md"
                        >Open PDF</a>
                      </div>
                    </div>
                  );
                })()}
              </div>

              {/* Left arrow */}
              <button
                onClick={() => setPdfPage(p => Math.max(1, p - 1))}
                disabled={pdfPage <= 1}
                aria-label="Previous slide"
                className="absolute left-3 top-1/2 -translate-y-1/2 w-12 h-12 rounded-full bg-white text-[#2B2926] hover:bg-[#F55600] hover:text-white border border-slate-200 shadow-xl flex items-center justify-center transition-all disabled:opacity-30 disabled:cursor-not-allowed"
              >
                <ChevronLeft className="w-7 h-7" strokeWidth={2.5} />
              </button>

              {/* Right arrow — disabled at last slide so users can't
                  navigate into empty "Slide N+1" territory. */}
              <button
                onClick={() => setPdfPage(p => Math.min(slideCount, p + 1))}
                disabled={pdfPage >= slideCount}
                aria-label="Next slide"
                className="absolute right-3 top-1/2 -translate-y-1/2 w-12 h-12 rounded-full bg-white text-[#2B2926] hover:bg-[#F55600] hover:text-white border border-slate-200 shadow-xl flex items-center justify-center transition-all disabled:opacity-30 disabled:cursor-not-allowed"
              >
                <ChevronRight className="w-7 h-7" strokeWidth={2.5} />
              </button>
            </div>

            {/* Footer hint */}
            <div className="px-4 py-2 border-t border-slate-200 text-center text-[10px] text-slate-500 uppercase tracking-widest font-semibold">
              Slide {pdfPage} of {slideCount} · ◄  ►  Arrows / keyboard ←→ to navigate · Esc to close
            </div>
          </div>
        </div>,
        document.body
      );
      })()}
    </div>
  );
};

const Drafts = ({ authAxios, setActiveTab, resetEditor, drafts, setDrafts, user, fetchDrafts: globalFetchDrafts, loadedStatus }) => {
  const [loading, setLoading] = useState(!loadedStatus.drafts && (!drafts || drafts.length === 0));
  const [searchTerm, setSearchTerm] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [selectedDraftForEdit, setSelectedDraftForEdit] = useState(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const { toast, confirm } = useNotification();

  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(searchTerm), 180);
    return () => clearTimeout(t);
  }, [searchTerm]);

  const fetchDrafts = async (force = false) => {
    try {
      setLoading(true);
      await globalFetchDrafts(force);
    } catch (err) {
      console.error("Failed to fetch drafts", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!loadedStatus.drafts) {
      fetchDrafts();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleDelete = async (id) => {
    const ok = await confirm({
      title: 'Delete Draft',
      message: 'Are you sure you want to delete this draft? This action cannot be undone.',
      confirmText: 'Delete Now'
    });
    if (!ok) return;
    try {
      await authAxios.delete(`/drafts/${id}`);
      setDrafts(drafts.filter(d => d.id !== id));
      toast.success("Draft deleted successfully");
    } catch (err) {
      toast.error("Failed to delete draft");
    }
  };

  const handlePostNow = async (draft) => {
    const ok = await confirm({
      title: 'Publish Now',
      message: 'Post this draft now for all selected platforms?',
      confirmText: 'Post Now'
    });
    if (!ok) return;
    try {
      // Sniff media_type for legacy drafts where it wasn't stored.
      const inferred_media = draft.media_type
        || (/\.(pdf|docx?|pptx?)(\?|$)/i.test(draft.image_url || '') ? 'document' : undefined);
      await authAxios.post('/post', {
        content: draft.content,
        image_url: draft.image_url,
        media_type: inferred_media,
        targets: draft.targets
      });
      toast.success("Published successfully! ✅");
    } catch (err) {
      toast.error("Failed to publish: " + (err.response?.data?.detail || err.message));
    }
  };

  const handleUpdateDraft = async (updatedDraft) => {
    try {
      const inferred_media = updatedDraft.media_type
        || (/\.(pdf|docx?|pptx?)(\?|$)/i.test(updatedDraft.image_url || '') ? 'document' : undefined);
      const res = await authAxios.put(`/drafts/${updatedDraft.id}`, {
        content: updatedDraft.content,
        image_url: updatedDraft.image_url,
        media_type: inferred_media,
        targets: updatedDraft.targets
      });
      setDrafts(drafts.map(d => d.id === res.data.id ? res.data : d));
      setIsModalOpen(false);
      toast.success("Draft updated successfully");
    } catch (err) {
      toast.error("Failed to update draft");
    }
  };

  const openEditModal = (draft) => {
    setSelectedDraftForEdit(draft);
    setIsModalOpen(true);
  };

  const filteredDrafts = drafts.filter(d =>
    d.content?.toLowerCase().includes(debouncedSearch.toLowerCase())
  );

  return (
    <div className="min-h-screen bg-white py-8 px-4">
      <div className="max-w-[1600px] mx-auto">
        {/* Header Section */}
        <div className="mb-10">
          <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-6">
            <div>
              <h1 className="text-4xl font-semibold text-[#2B2926] tracking-tight mb-2">
                Your <span className="text-[#F55600]">Drafts</span>
              </h1>
              <p className="text-sm text-slate-600 font-medium">Edit, refine, and publish your content across all platforms.</p>
            </div>
            
            <div className="grid grid-cols-2 md:flex items-center gap-2">
              <div className="relative flex-1 md:w-64">
                <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input 
                  type="text" 
                  placeholder="Search drafts..." 
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  className="w-full pl-11 pr-4 py-2.5 rounded-xl border border-slate-350 bg-white hover:border-orange-300 focus:border-[#F55600] focus:ring-2 focus:ring-orange-100 outline-none text-sm transition-all shadow-sm"
                />
              </div>
              <button 
                onClick={() => { resetEditor(); setActiveTab('create'); }}
                className="inline-flex items-center justify-center gap-2 h-10 px-5 text-[#F55600] bg-white border-2 border-[#F55600] rounded-full font-semibold text-[11px] uppercase tracking-wider hover:bg-orange-50 transition-all shadow-sm active:scale-95"
              >
                <span>New Draft</span>
              </button>
            </div>
          </div>
        </div>

        {/* Content */}
        {loading ? (
          <div className="flex flex-col items-center justify-center py-32">
            <div className="w-12 h-12 border-4 border-orange-100 border-t-[#F55600] rounded-full animate-spin mb-4"></div>
            <p className="text-slate-500 font-semibold">Loading your drafts...</p>
          </div>
        ) : filteredDrafts.length === 0 ? (
          <div className="bg-white rounded-2xl p-20 text-center border-2 border-slate-350 shadow-sm hover:shadow-md transition-all">
            <div className="w-24 h-24 bg-gradient-to-br from-orange-100 to-slate-50 rounded-full flex items-center justify-center mx-auto mb-6 border border-orange-300">
              <FileEdit className="w-12 h-12 text-slate-300" />
            </div>
            <h3 className="text-2xl font-semibold text-[#2B2926] mb-3">No Drafts Yet</h3>
            <p className="text-slate-500 mb-8 max-w-sm mx-auto leading-relaxed">Start creating amazing content! Click the button below to begin.</p>
            <button 
              onClick={resetEditor}
              className="inline-flex items-center gap-2 px-10 py-3.5 bg-gradient-to-r from-[#F55600] to-[#F55600] text-white rounded-xl font-bold shadow-lg shadow-orange-200/50 hover:shadow-xl hover:scale-105 transition-all"
            >
              <Plus className="w-5 h-5" />
              Create Your First Draft
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            {filteredDrafts.map((draft, index) => (
              <DraftCard 
                key={draft.id} 
                draft={draft} 
                onEdit={openEditModal} 
                handleDelete={handleDelete} 
                handlePostNow={handlePostNow} 
                user={user}
                index={index}
              />
            ))}
          </div>
        )}

        {selectedDraftForEdit && (
          <EditDraftModal 
            isOpen={isModalOpen}
            draft={selectedDraftForEdit}
            onClose={() => setIsModalOpen(false)}
            onSave={handleUpdateDraft}
          />
        )}
      </div>
    </div>
  );
};

export default Drafts;
