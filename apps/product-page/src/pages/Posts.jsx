import React from 'react';
import { useNotification } from '../context/NotificationContext';
import CustomTimePicker from '../components/CustomTimePicker';
import DateTimePicker from '../components/DateTimePicker';
import { Type, Image as ImageIcon, Video, RefreshCw, ChevronLeft, Users, CheckCircle2, Send, Trash2, Calendar, Clock, FileEdit, Plus, AlertCircle } from 'lucide-react';
import XIcon from '../components/icons/XIcon';
import { isReadOnly } from '../lib/permissions';
// L1 + H3 — share the same media-URL resolver + API-error formatter with
// App.jsx so the publish and schedule paths behave identically.
import { resolveMediaUrl, formatApiError } from '../utils/postContent';

// Dynamic platform → logo image. Add a platform here and its logo shows up
// in the channel chips automatically. X has no image asset, so it falls back
// to its vector mark.
const PLATFORM_IMG = {
  linkedin:  '/linkedlin.jpg',
  facebook:  '/facebook.png',
  instagram: '/instagram.jpg',
  youtube:   '/youtube-icon.png',
  pinterest: '/pinterest-logo.png',
};
// Inline TikTok glyph — same SVG path the Connections tile uses, so the
// chip on this screen visually matches what the user clicked to connect.
const TikTokGlyph = ({ className = 'w-3 h-3 fill-current' }) => (
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden="true">
    <path d="M12.525.02c1.31-.02 2.61-.01 3.91-.01.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.06-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.59-1.01-.14 3.39-.12 6.79-.12 10.18.06 2.1-.69 4.31-2.31 5.74-1.61 1.48-3.95 1.89-6.02 1.25-2.07-.63-3.75-2.58-4.14-4.63-.48-2.61.64-5.59 2.92-6.94 1.48-.88 3.3-.96 4.9-.4v4.25c-2.4-.64-5.11.75-5.38 3.23-.21 1.9 1.56 3.82 3.48 3.73 1.48.06 2.87-1 3.19-2.45.1-.38.12-.77.12-1.16-.01-5.1-.01-10.19-.01-15.29-.01-2.5 1.61-4.75 4-5.36z" />
  </svg>
);

const PlatformLogo = ({ platform, className = 'w-4 h-4' }) => {
  if (PLATFORM_IMG[platform]) {
    return <img src={PLATFORM_IMG[platform]} className={`${className} object-contain rounded-sm`} alt={platform} />;
  }
  if (platform === 'tiktok') {
    return <TikTokGlyph className="w-3 h-3 fill-current" />;
  }
  return <XIcon className="w-3 h-3 fill-current" />;
};

// YouTube standard categories. Used to populate the dropdown in YouTube
// Mode. IDs come straight from the Data API v3 — they're stable, no need
// to fetch them from /videoCategories every render. Default selected = 22
// ("People & Blogs"), most generic + no algorithm assumptions.
const YOUTUBE_CATEGORIES = [
  { id: '22', label: 'People & Blogs' },
  { id: '24', label: 'Entertainment' },
  { id: '27', label: 'Education' },
  { id: '28', label: 'Science & Technology' },
  { id: '26', label: 'How-to & Style' },
  { id: '25', label: 'News & Politics' },
  { id: '23', label: 'Comedy' },
  { id: '10', label: 'Music' },
  { id: '20', label: 'Gaming' },
  { id: '17', label: 'Sports' },
  { id: '19', label: 'Travel & Events' },
  { id: '15', label: 'Pets & Animals' },
  { id: '2',  label: 'Autos & Vehicles' },
  { id: '1',  label: 'Film & Animation' },
];

const Posts = ({
  postType, setPostType,
  setActiveTab,
  connections,
  toggleTarget, selectedTargets, setSelectedTargets,
  handleImageChange, imagePreview, setImagePreview, fileInputRef,
  base64Image, setBase64Image,
  mediaType, setMediaType,
  mediaUploadProgress,
  videoDurationSec,
  postContent, setPostContent,
  handlePublish,
  editingPlatform, setEditingPlatform,
  loading,
  message,
  authAxios,
  user,
  fetchPublished,
  fetchScheduled,
  // Fix — `fetchDrafts` was never passed by App.jsx, so `handleSaveDraft`
  // could POST /drafts successfully, show the toast, and leave the parent
  // `drafts` state untouched. When the user then navigated to the Drafts
  // page, the just-saved draft was invisible until a full page reload.
  // Optional so existing renders that don't wire it still show the toast.
  fetchDrafts,
  // YouTube-Mode fields. Lifted to the parent so handlePublish can read them
  // out of one place. If the parent doesn't pass them (older App.jsx) we
  // fall back to local state so the form still works for everyone else.
  youtubeTitle: ytTitleProp,
  setYoutubeTitle: setYtTitleProp,
  youtubeTags: ytTagsProp,
  setYoutubeTags: setYtTagsProp,
  youtubeCategory: ytCatProp,
  setYoutubeCategory: setYtCatProp,
  youtubePrivacy: ytPrivProp,
  setYoutubePrivacy: setYtPrivProp,
  // TikTok-Mode field. Caption shares the main composer text box; only
  // privacy needs a TikTok-specific control.
  tiktokPrivacy: ttPrivProp,
  setTiktokPrivacy: setTtPrivProp,
}) => {
  // Local fallbacks when the parent hasn't lifted these yet.
  const [_ytTitle, _setYtTitle] = React.useState('');
  const [_ytTags, _setYtTags] = React.useState('');
  const [_ytCategory, _setYtCategory] = React.useState('22');
  const [_ytPrivacy, _setYtPrivacy] = React.useState('public');
  const [_ttPrivacy, _setTtPrivacy] = React.useState('SELF_ONLY');
  const youtubeTitle    = ytTitleProp ?? _ytTitle;
  const setYoutubeTitle = setYtTitleProp ?? _setYtTitle;
  const youtubeTags     = ytTagsProp ?? _ytTags;
  const setYoutubeTags  = setYtTagsProp ?? _setYtTags;
  const youtubeCategory = ytCatProp ?? _ytCategory;
  const setYoutubeCategory = setYtCatProp ?? _setYtCategory;
  const youtubePrivacy  = ytPrivProp ?? _ytPrivacy;
  const setYoutubePrivacy = setYtPrivProp ?? _setYtPrivacy;
  const tiktokPrivacy   = ttPrivProp ?? _ttPrivacy;
  const setTiktokPrivacy = setTtPrivProp ?? _setTtPrivacy;

  // Derived: is YouTube currently the active target? Used to gate the
  // YouTube-only fields panel and to toggle the caption label.
  const isYoutubeMode = !!(selectedTargets?.youtube?.length);
  const isTiktokMode = !!(selectedTargets?.tiktok?.length);

  // Wrap toggleTarget with the YouTube-exclusivity rule the product spec
  // calls for: YouTube can never coexist with FB / LinkedIn / IG / X on the
  // same post (different APIs, different field shapes, no clean cross-
  // platform mapping). Clicking YouTube clears the others; clicking a non-
  // YouTube platform clears YouTube. The parent's existing toggleTarget is
  // platform-agnostic — we just call it multiple times around it.
  const handleExclusiveToggle = React.useCallback((platform, accountId) => {
    if (!setSelectedTargets) {
      // Parent didn't lift setSelectedTargets — fall back to single toggle
      // (matches old behaviour, can't enforce exclusivity).
      toggleTarget(platform, accountId);
      return;
    }
    setSelectedTargets(prev => {
      const next = { ...prev };
      const list = next[platform] || [];
      const has = list.includes(accountId);
      // Platforms that take over the composer (video-only, format-specific
      // caption/title rules). Selecting one clears EVERY other platform and
      // selecting any other platform clears these. YouTube needs separate
      // title + tags + category; TikTok uses a TikTok-specific privacy enum
      // and is video-only — neither can share a row with image+text platforms.
      const EXCLUSIVES = ['youtube', 'tiktok'];
      if (EXCLUSIVES.includes(platform)) {
        // Clicking an exclusive clears every non-self selection, then toggles self.
        Object.keys(next).forEach(p => { if (p !== platform) next[p] = []; });
        next[platform] = has ? list.filter(id => id !== accountId) : [...list, accountId];
      } else {
        // Clicking any other platform clears the exclusives + toggles the clicked one.
        EXCLUSIVES.forEach(ex => { next[ex] = []; });
        next[platform] = has ? list.filter(id => id !== accountId) : [...list, accountId];
      }
      return next;
    });
  }, [setSelectedTargets, toggleTarget]);
  const [scheduledDate, setScheduledDate] = React.useState("");
  const [scheduledTime, setScheduledTime] = React.useState("12:00");
  const [isScheduling, setIsScheduling] = React.useState(false);
  const [activeActionTab, setActiveActionTab] = React.useState('publish'); // 'publish', 'schedule'
  const { toast, confirm } = useNotification();

  const handleSchedule = async () => {
    if (!scheduledDate || !scheduledTime) {
      toast.error("Please select both date and time to schedule");
      return;
    }

    // M1 fix — defensive validation matching the button's disabled
    // condition. Button *should* be disabled when these fail, but stale
    // state / keyboard shortcuts / rapid double-clicks can still land here.
    const hasAnyTarget = Object.values(selectedTargets || {}).some(a => a?.length);
    if (!hasAnyTarget) {
      toast.error("Select at least one account before scheduling.");
      return;
    }

    // H2 fix — mirror the needsMedia guard from App.jsx handlePublish.
    // Previously handleSchedule accepted any state and let the backend
    // silently drop image-required platforms at cron-fire time, so the
    // user thought the schedule worked and only saw failures when they
    // checked the Scheduled tab hours later.
    // L1 fix — use the shared resolveMediaUrl helper.
    const finalImage = resolveMediaUrl(imagePreview, base64Image);
    const isPinterestPost = !!(selectedTargets?.pinterest?.length);
    const needsMedia = isPinterestPost || isYoutubeMode || isTiktokMode
      || !!selectedTargets?.instagram?.length;
    if (needsMedia && !finalImage) {
      const stillUploading = imagePreview && !imagePreview.startsWith('http');
      toast.error(stillUploading
        ? 'Image is still uploading — wait for the preview to load, then schedule.'
        : 'This post needs an image. Upload one before scheduling.');
      return;
    }

    // H4 fix — YouTube requires a video title even at schedule time (the
    // cron worker calls videos.insert which validates it).
    if (isYoutubeMode && !(youtubeTitle || '').trim()) {
      toast.error('YouTube video needs a title. Add one and try again.');
      return;
    }

    setIsScheduling(true);
    try {
      const scheduled_for = new Date(`${scheduledDate}T${scheduledTime}`).toISOString();
      const timezone = user?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;

      const schedulePayload = {
        content: postContent,
        image_url: finalImage,
        media_type: mediaType || (postType === 'text' ? null : postType),
        targets: selectedTargets,
        scheduled_for,
        timezone,
      };
      // YouTube-mode fields ride along on the scheduled row so the cron
      // fire path has everything videos.insert needs at publish time.
      // Backend only reads these when targets.youtube is non-empty.
      if (isYoutubeMode) {
        schedulePayload.youtube_title = (youtubeTitle || '').trim();
        schedulePayload.youtube_description = postContent;
        schedulePayload.youtube_tags = (youtubeTags || '')
          .split(',')
          .map(t => t.trim())
          .filter(Boolean);
        schedulePayload.youtube_category_id = youtubeCategory || '22';
        schedulePayload.youtube_privacy = youtubePrivacy || 'public';
      }
      if (isTiktokMode) {
        // TikTok-Mode field. Caption comes from postContent on the backend.
        schedulePayload.tiktok_caption = postContent;
        schedulePayload.tiktok_privacy = tiktokPrivacy || 'SELF_ONLY';
      }
      await authAxios.post('/schedule', schedulePayload);

      // Was `alert(...)` — hostile UX (browser-native dialog blocks the
      // whole page). Component already uses `toast` for the error path;
      // just mirror it here for the success path too.
      toast.success("Post scheduled successfully! 🚀");
      if (fetchScheduled) fetchScheduled(true);
      setPostType(null); // Back to selection
      setActiveActionTab('publish');
      setPostContent('');
      setImagePreview(null);
      if (setBase64Image) setBase64Image(null);
    } catch (err) {
      // H3 fix — show the backend's actual reason instead of a generic
      // "Failed to schedule post". FastAPI validation errors are array-of-
      // objects which used to render as [object Object].
      console.error(err);
      const msg = formatApiError(err?.response?.data?.detail, 'Failed to schedule post');
      toast.error(msg);
    } finally {
      setIsScheduling(false);
    }
  };

  const handleSaveDraft = async () => {
    try {
      // Fix — was missing `media_type`. Without it, video and PDF-carousel
      // drafts saved as media_type=NULL and the Drafts page couldn't render
      // the right preview (video / PDF chip vs img) when reopened.
      // resolveMediaUrl also correctly picks base64 vs http URL so local
      // uploads don't get silently dropped from the payload.
      const finalImage = resolveMediaUrl(imagePreview, base64Image);
      const draftMediaType = mediaType || (postType && postType !== 'text' ? postType : null);
      // Fix — filter out platform keys with empty account arrays. When the
      // user clicked a platform, added an account, then removed all
      // accounts, selectedTargets kept the platform key with []. That
      // phantom key showed up as a ghost icon on the Drafts card even
      // though the user had un-selected the platform. Pruning here means
      // the DB row (and every downstream render) only carries the truly
      // selected platforms.
      const prunedTargets = Object.fromEntries(
        Object.entries(selectedTargets || {})
          .filter(([, accts]) => Array.isArray(accts) && accts.length > 0)
      );
      await authAxios.post('/drafts', {
        content: postContent,
        image_url: finalImage,
        media_type: draftMediaType,
        targets: prunedTargets,
      });
      // Fix — refresh the parent `drafts` state so the just-saved draft is
      // visible when the user navigates to the Drafts page. Without this
      // call the toast was misleading — the row existed in the DB but the
      // UI still showed the empty state until a hard reload.
      if (typeof fetchDrafts === 'function') {
        try { fetchDrafts(true); } catch (_e) {}
      }
      toast.success("Draft saved! 📝");
    } catch (err) {
      toast.error("Failed to save draft");
    }
  };
  return (
    <div className="min-h-screen bg-white">
      <div className="max-w-6xl mx-auto px-4 py-6">
      <button
        onClick={() => setActiveTab('create')}
        className="mb-6 self-start w-fit inline-flex items-center gap-2 bg-[#2B2926] hover:bg-[#F55600] text-white rounded-lg px-3.5 py-2 font-semibold text-[10px] uppercase tracking-widest shadow-sm transition-colors"
      >
        <ChevronLeft className="w-3.5 h-3.5" />
        Back to Selection
      </button>
      {postType === null ? (
        <div className="space-y-4 animate-in fade-in slide-in-from-top-4 duration-700">
          <div className="text-center mb-4">
            <h3 className="text-4xl font-semibold text-[#F55600] mb-2 tracking-tight">Create a new campaign</h3>
            <p className="text-slate-500 font-bold uppercase text-[10px] tracking-[0.2em]">Pick your campaign type and reach your audience</p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-5">
            {[
              { id: 'text', title: 'Text Post', description: 'Share your thoughts and updates', icon: Type, color: 'orange' },
              { id: 'image', title: 'Image Post', description: 'Upload single or multiple images', icon: ImageIcon, color: 'orange' },
              { id: 'video', title: 'Video Post', description: 'Share short or long-form videos', icon: Video, color: 'orange' },
              { id: 'document', title: 'Document Post', description: 'LinkedIn-only · PDF carousel', icon: FileEdit, color: 'orange' },
            ].map((card) => (
              <div
                key={card.id}
                onClick={() => setPostType(card.id)}
                className={`group bg-white p-6 rounded-[32px] border-2 border-dashed transition-all cursor-pointer text-center relative overflow-hidden
                  border-orange-300 hover:border-[#F55600] hover:shadow-[0_20px_50px_rgba(255,107,53,0.1)] hover:-translate-y-1`}
              >
                <div className="flex flex-col items-center gap-4 relative z-10">
                  <div className="w-16 h-16 rounded-2xl flex items-center justify-center transition-all duration-500 border-2 bg-orange-50/50 border-orange-100 group-hover:bg-[#F55600] group-hover:border-[#F55600]">
                    <card.icon className="w-8 h-8 transition-colors duration-500 text-[#F55600] group-hover:text-white" />
                  </div>
                  <div>
                    <h4 className="text-xl font-semibold text-[#2B2926] mb-1 tracking-tight">{card.title}</h4>
                    <p className="text-[10px] text-[#2B2926] font-semibold uppercase tracking-widest">{card.description}</p>
                  </div>
                </div>
                {card.id === 'document' && <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-20 bg-[#10B981] text-white px-3 py-1 rounded-full text-[10px] font-semibold uppercase tracking-wider shadow-sm whitespace-nowrap">LinkedIn only</div>}

                {/* Decorative background element */}
                <div className="absolute -bottom-6 -right-6 w-24 h-24 bg-gradient-to-br from-orange-50/50 to-transparent rounded-full blur-2xl group-hover:from-[#F55600]/10 group-hover:scale-150 transition-all duration-700"></div>
              </div>
            ))}
          </div>
          <div className="flex justify-center mt-6">
            <button
              onClick={() => setActiveTab('connections')}
              className="group inline-flex items-center gap-3 px-5 py-2.5 bg-[#2B2926] text-white hover:bg-slate-900 rounded-full border border-[#2B2926] transition-all shadow-sm hover:shadow-md cursor-pointer"
            >
              <RefreshCw className="w-4 h-4 transition-transform group-hover:rotate-180 duration-500" />
              <span className="text-[11px] font-semibold uppercase tracking-widest">Connect more accounts</span>
              <div className="w-5 h-5 rounded-full bg-white/10 flex items-center justify-center border border-white/20 group-hover:border-white transition-colors">
                <Plus className="w-3 h-3 text-white" />
              </div>
            </button>
          </div>
        </div>
      ) : (
        <div className="bg-white rounded-[32px] p-6 shadow-xl border-2 border-[#F55600]/40 border-t-4 border-t-[#F55600] animate-in fade-in slide-in-from-right-4 duration-500">
          <div className="flex items-center gap-2.5 mb-5 p-1 bg-slate-50/50 rounded-2xl border border-[#2B2926]/30 max-w-fit">
            <button
              onClick={() => setPostType(null)}
              className="p-1.5 rounded-xl hover:bg-white hover:text-[#2B2926] text-slate-400 transition-all border border-transparent hover:border-[#2B2926]/50 hover:shadow-sm"
            >
              <ChevronLeft className="w-5 h-5" />
            </button>
            <div className="h-4 w-px bg-slate-200 mx-1"></div>
            <div className="flex flex-col pr-4">
              <p className="text-[8px] uppercase tracking-[0.2em] text-[#F55600] font-semibold leading-none mb-1">Campaign Builder</p>
              <h3 className="text-sm font-semibold text-[#2B2926] tracking-tight leading-none uppercase">
                {postType} mode {editingPlatform !== 'default' && <span className="text-[#F55600] opacity-80 decoration-orange-200">({editingPlatform === 'twitter' ? 'x' : editingPlatform})</span>}
              </h3>
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 space-y-4">
              {/* Account Selection (BACK TO LEFT) */}
              <div className="bg-white rounded-[24px] p-4 border-2 border-[#2B2926] shadow-sm space-y-3">
                <div className="flex items-center gap-2 ml-1">
                  <Users className="w-3.5 h-3.5 text-[#F55600]" />
                  <h4 className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest leading-none">Target Channels</h4>
                </div>

                {postType === 'document' && (
                  <div className="text-[10px] font-bold text-emerald-700 bg-emerald-50 border border-emerald-100 rounded-lg px-3 py-2 inline-block">
                    Document posts are LinkedIn-only.
                  </div>
                )}

                <div className="flex flex-wrap gap-2.5">
                  {['linkedin', 'facebook', 'instagram', 'twitter', 'youtube', 'tiktok', 'pinterest'].map(platform => (
                    <React.Fragment key={platform}>
                      {!(postType === 'document' && platform !== 'linkedin') &&
                        !(postType === 'text' && platform === 'instagram') &&
                        !(postType === 'video' && platform === 'twitter' && videoDurationSec > 120) &&
                        // YouTube is video-only — hide the chip for text/image/document posts.
                        !(platform === 'youtube' && postType !== 'video') &&
                        // TikTok is video-only — same rule as YouTube.
                        !(platform === 'tiktok' && postType !== 'video') &&
                        // Pinterest is image-only for now (video pin support is Phase 2 —
                        // it needs the multi-step /v5/media upload flow).
                        !(platform === 'pinterest' && postType !== 'image') &&
                        connections[platform]?.map(acc => {
                          const isSelected = selectedTargets[platform]?.includes(acc.id);
                          return (
                            <button
                              key={acc.id}
                              onClick={() => handleExclusiveToggle(platform, acc.id)}
                              className={`group relative flex items-center gap-2 pl-1.5 pr-4 py-2 rounded-full border-2 transition-all cursor-pointer bg-white ${
                                isSelected ? 'border-[#F55600] bg-orange-50 shadow-md ring-2 ring-orange-100' : 'border-[#2B2926]/30 hover:border-[#F55600]/50'
                              }`}
                              title={acc.name}
                            >
                              <div className="shrink-0">
                                <PlatformLogo platform={platform} className="w-4 h-4" />
                              </div>
                              <span className={`text-[10px] font-semibold uppercase tracking-tight ${isSelected ? 'text-[#F55600]' : 'text-[#2B2926]'}`}>
                                {acc.name}
                              </span>
                              {isSelected && (
                                <div className="absolute -top-1 -right-1 bg-[#F55600] rounded-full p-0.5 border border-white shadow-sm z-20">
                                  <CheckCircle2 className="w-2 h-2 text-white" />
                                </div>
                              )}
                            </button>
                          );
                        })}
                    </React.Fragment>
                  ))}
                </div>

                {/* Exclusivity notice — appears only while YouTube is selected
                    so the user understands why the other platform chips are
                    locked out (it doesn't replace them, just tells the truth). */}
                {isYoutubeMode && (
                  <div className="mt-3 text-[10px] font-semibold text-emerald-700 bg-emerald-50/50 backdrop-blur-md border border-emerald-200/50 rounded-lg px-3 py-2 inline-flex items-center gap-2 shadow-sm">
                    <AlertCircle className="w-3 h-3" />
                    YouTube posts can't be combined with other platforms — they use different fields.
                  </div>
                )}
              </div>

              {/* YouTube fields panel — only when YouTube is the active target.
                  Sits between Target Channels and the Post Caption so the user
                  fills it in the natural left-to-right reading order. */}
              {isYoutubeMode && (
                <div className="bg-white rounded-[24px] p-4 border-2 border-[#2B2926] shadow-sm space-y-3">
                  <div className="flex items-center gap-2 ml-1">
                    <img src="/youtube-icon.png" className="w-3.5 h-3.5 object-contain" alt="YouTube" />
                    <h4 className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest leading-none">YouTube Details</h4>
                  </div>

                  {/* Title — required */}
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <label className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest">
                        Title <span className="text-[#FF0000]">*</span>
                      </label>
                      <span className="text-[8px] font-semibold text-slate-400 uppercase tracking-tighter">
                        {(youtubeTitle || '').length}/100
                      </span>
                    </div>
                    <input
                      type="text"
                      value={youtubeTitle}
                      onChange={(e) => setYoutubeTitle((e.target.value || '').slice(0, 100))}
                      placeholder="A clear, click-worthy video title (max 100 chars)"
                      className="w-full px-3 py-2 rounded-lg border-2 border-slate-200 focus:border-[#FF0000] outline-none text-sm font-medium transition-colors"
                    />
                  </div>

                  {/* Tags — optional */}
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <label className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest">
                        Tags <span className="text-slate-400 normal-case font-normal">(comma-separated, optional)</span>
                      </label>
                    </div>
                    <input
                      type="text"
                      value={youtubeTags}
                      onChange={(e) => setYoutubeTags(e.target.value)}
                      placeholder="e.g. marketing, analytics, spenzo"
                      className="w-full px-3 py-2 rounded-lg border-2 border-slate-200 focus:border-[#FF0000] outline-none text-sm font-medium transition-colors"
                    />
                  </div>

                  {/* Category + Privacy on one row */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                      <label className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest mb-1.5 block">
                        Category
                      </label>
                      <select
                        value={youtubeCategory}
                        onChange={(e) => setYoutubeCategory(e.target.value)}
                        className="w-full px-3 py-2 rounded-lg border-2 border-slate-200 focus:border-[#FF0000] outline-none text-sm font-medium transition-colors bg-white"
                      >
                        {YOUTUBE_CATEGORIES.map(c => (
                          <option key={c.id} value={c.id}>{c.label}</option>
                        ))}
                      </select>
                    </div>

                    <div>
                      <label className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest mb-1.5 block">
                        Privacy
                      </label>
                      <div className="flex gap-2">
                        {['public', 'unlisted', 'private'].map(p => (
                          <button
                            key={p}
                            type="button"
                            onClick={() => setYoutubePrivacy(p)}
                            className={`flex-1 px-3 py-2 rounded-lg border-2 text-[10px] font-semibold uppercase tracking-widest transition-all ${
                              youtubePrivacy === p
                                ? 'border-[#F55600] bg-orange-50 text-[#F55600]'
                                : 'border-slate-200 text-[#2B2926] hover:border-slate-300'
                            }`}
                          >
                            {p}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* TikTok-Only fields panel — renders when TikTok is the active
                  target. TikTok publishing is video-only and uses one caption
                  field plus a privacy enum. Caption comes from the main
                  composer below; this panel just exposes Privacy. */}
              {isTiktokMode && (
                <div className="bg-slate-100 rounded-2xl p-4 border-2 border-[#2B2926] space-y-4">
                  <div className="flex items-center gap-2 mb-2">
                    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="#2B2926">
                      <path d="M12.525.02c1.31-.02 2.61-.01 3.91-.01.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.06-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.59-1.01-.14 3.39-.12 6.79-.12 10.18.06 2.1-.69 4.31-2.31 5.74-1.61 1.48-3.95 1.89-6.02 1.25-2.07-.63-3.75-2.58-4.14-4.63-.48-2.61.64-5.59 2.92-6.94 1.48-.88 3.3-.96 4.9-.4v4.25c-2.4-.64-5.11.75-5.38 3.23-.21 1.9 1.56 3.82 3.48 3.73 1.48.06 2.87-1 3.19-2.45.1-.38.12-.77.12-1.16-.01-5.1-.01-10.19-.01-15.29-.01-2.5 1.61-4.75 4-5.36z" />
                    </svg>
                    <h4 className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest leading-none">
                      TikTok Details
                    </h4>
                  </div>

                  <div>
                    <label className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest mb-1.5 block">
                      Privacy
                    </label>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                      {[
                        { v: 'PUBLIC_TO_EVERYONE', label: 'Public' },
                        { v: 'MUTUAL_FOLLOW_FRIENDS', label: 'Friends' },
                        { v: 'FOLLOWER_OF_CREATOR', label: 'Followers' },
                        { v: 'SELF_ONLY', label: 'Only Me' },
                      ].map(opt => (
                        <button
                          key={opt.v}
                          type="button"
                          onClick={() => setTiktokPrivacy(opt.v)}
                          className={`px-2 py-2 rounded-lg border-2 text-[10px] font-semibold uppercase tracking-widest transition-all ${
                            tiktokPrivacy === opt.v
                              ? 'bg-[#2B2926] text-white border-[#2B2926]'
                              : 'bg-white text-[#2B2926] border-slate-200 hover:border-[#2B2926]'
                          }`}
                        >
                          {opt.label}
                        </button>
                      ))}
                    </div>
                    <p className="text-[10px] text-slate-500 mt-2 leading-relaxed">
                      Sandbox apps default to <strong>Only Me</strong> until TikTok approves the production app review. Production accounts can choose any privacy level.
                    </p>
                  </div>
                </div>
              )}

              {/* Content Editor */}
              <div className="bg-slate-50/50 rounded-[28px] p-5 border-2 border-[#2B2926] shadow-inner">
                <div className="flex items-center justify-between mb-3 px-1">
                  <div className="flex items-center gap-2">
                    <FileEdit className="w-3.5 h-3.5 text-[#F55600]" />
                    <h4 className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest leading-none">Post Caption</h4>
                  </div>
                  <span className="text-[8px] font-semibold text-slate-400 bg-white px-2 py-0.5 rounded border border-[#2B2926]/30 uppercase tracking-tighter">
                    {postContent.length}/2200
                  </span>
                </div>
                <textarea
                  value={postContent}
                  onChange={(e) => setPostContent(e.target.value)}
                  placeholder="Start writing your post here..."
                  className="w-full h-48 p-5 bg-white rounded-[24px] outline-none border-2 border-transparent focus:border-[#F55600] transition-all text-sm font-medium resize-none shadow-sm"
                />
              </div>
            </div>

            <div className="space-y-4">
              {/* Media Upload Area (TOP OF SIDEBAR) */}
              {(postType === 'image' || postType === 'video' || postType === 'document') && (
                <div className="bg-slate-50 rounded-[32px] p-5 border-2 border-[#2B2926] shadow-sm space-y-3">
                  <div className="flex items-center gap-2 mb-1">
                    <ImageIcon className="w-3.5 h-3.5 text-[#F55600]" />
                    <h4 className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest leading-none">
                      Media {postType === 'document' ? ' (LinkedIn)' : ''}
                    </h4>
                  </div>

                  <div className="relative border-2 border-dashed border-orange-200 rounded-2xl p-4 flex flex-col items-center justify-center transition-all bg-white group hover:border-[#F55600]">
                    <input
                      type="file" id="media-upload-final-2" ref={fileInputRef} className="hidden"
                      onChange={handleImageChange}
                      accept={postType === 'video' ? 'video/*' : postType === 'document' ? '.pdf,.doc,.docx' : 'image/*'}
                    />
                    <label htmlFor="media-upload-final-2" className="cursor-pointer w-full flex flex-col items-center justify-center gap-3 text-center">
                      {imagePreview ? (
                        <div className="relative group/img w-full flex justify-center">
                          {postType === 'video' || mediaType === 'video' ? (
                            <video src={imagePreview} controls className="max-h-40 rounded-xl bg-[#2B2926]" />
                          ) : postType === 'document' || mediaType === 'document' ? (
                            <div className="flex items-center gap-2 px-3 py-2 bg-slate-50 rounded-xl border border-[#2B2926]/30">
                              <FileEdit className="w-5 h-5 text-[#F55600]" />
                              <span className="text-[10px] font-bold truncate max-w-[100px]">{imagePreview.split('/').pop()}</span>
                            </div>
                          ) : (
                            <img src={imagePreview} className="max-h-40 rounded-xl object-cover" alt="" />
                          )}
                          <button
                            onClick={(e) => { e.preventDefault(); setImagePreview(null); }}
                            className="absolute top-1 right-1 bg-red-500 text-white p-1 rounded-full opacity-0 group-hover/img:opacity-100 transition-opacity"
                          >
                            <Trash2 className="w-3 h-3" />
                          </button>
                        </div>
                      ) : (
                        <div className="py-2">
                          <Plus className="w-8 h-8 text-slate-300 group-hover:text-[#F55600] transition-colors mx-auto mb-2" />
                          <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-widest">Add {postType}</p>
                        </div>
                      )}
                    </label>
                  </div>
                  {mediaUploadProgress > 0 && mediaUploadProgress < 100 && (
                    <div className="w-full bg-slate-100 rounded-full h-1.5 overflow-hidden">
                      <div className="h-full bg-[#F55600]" style={{ width: `${mediaUploadProgress}%` }} />
                    </div>
                  )}
                  {postType === 'image' && !['free', 'starter'].includes(user?.pricing_plan) && (
                    <button
                      onClick={async (e) => {
                        e.preventDefault();
                        const response = await authAxios.get('/auth/canva/authorize');
                        if (response.data.url) window.open(response.data.url, 'Canva', 'width=1000,height=800');
                      }}
                      className="w-full py-2 bg-blue-50 text-blue-600 rounded-xl text-[10px] font-semibold border border-blue-100 hover:bg-blue-600 hover:text-white transition-all shadow-sm"
                    >
                      C DESIGN WITH CANVA
                    </button>
                  )}
                </div>
              )}

              {/* Action Center (FINALIZE POST) */}
              <div className="bg-white/70 backdrop-blur-xl rounded-[32px] p-6 text-center shadow-xl border-2 border-[#2B2926] relative overflow-hidden">
                <div className="absolute top-0 right-0 w-24 h-24 bg-gradient-to-br from-orange-100/30 to-transparent rounded-full blur-2xl"></div>
                <h4 className="font-semibold text-[#2B2926] mb-4 uppercase tracking-widest text-[9px] relative z-10">Finalize Post</h4>

                <div className="flex bg-slate-100/50 p-1 rounded-2xl mb-4 border border-[#2B2926]/30 shadow-inner relative z-10">
                  <button
                    onClick={() => setActiveActionTab('publish')}
                    className={`flex-1 py-2 text-[10px] font-semibold rounded-xl transition-all uppercase tracking-widest ${activeActionTab === 'publish' ? 'text-white bg-[#F55600] shadow-lg' : 'text-[#2B2926] hover:text-[#F55600]'}`}
                  >
                    Publish
                  </button>
                  <button
                    onClick={() => setActiveActionTab('schedule')}
                    className={`flex-1 py-2 text-[10px] font-semibold rounded-xl transition-all uppercase tracking-widest ${activeActionTab === 'schedule' ? 'text-white bg-[#F55600] shadow-lg' : 'text-[#2B2926] hover:text-slate-600'}`}
                  >
                    Schedule
                  </button>
                </div>

                {activeActionTab === 'schedule' && (
                  <div className="space-y-3 mb-6 text-left p-4 bg-orange-50/50 rounded-2xl border border-orange-100 animate-in fade-in zoom-in-95">
                    <DateTimePicker
                      mode="date"
                      value={scheduledDate}
                      onChange={setScheduledDate}
                    />
                    <CustomTimePicker
                      selectedTime={scheduledTime}
                      onTimeChange={setScheduledTime}
                      timezone={user?.timezone || 'UTC'}
                      variant="light"
                    />
                  </div>
                )}

                {!isReadOnly(user) && (() => {
                  // Per-mode "ready to publish" rules. Was uniform across all
                  // post types, which produced two bugs:
                  //   • Video Mode allowed publish with no video uploaded
                  //     (backend then failed silently) — user report 2026-07-10.
                  //   • Image Mode blocked media-only posts (LinkedIn / FB /
                  //     Instagram accept those) — H1 fix from earlier.
                  //
                  // Rules now:
                  //   text     → caption required (media not applicable)
                  //   image    → caption OR media (H1 behaviour preserved)
                  //   video    → caption AND video successfully uploaded
                  //   document → caption AND document successfully uploaded
                  //
                  // "Successfully uploaded" = imagePreview is an http/https URL
                  // (S3-hosted). A blob: URL means the upload is still in flight
                  // and the payload would be invalid on the backend.
                  const captionEmpty = !postContent.trim();
                  const noTargets = Object.values(selectedTargets).flat().length === 0;
                  const mediaReady =
                    !!base64Image ||
                    (imagePreview && (imagePreview.startsWith('http://') || imagePreview.startsWith('https://')));
                  const anyMediaPresent = !!base64Image || !!imagePreview;

                  let modeIncomplete = false;
                  if (postType === 'video' || postType === 'document') {
                    // Video / carousel modes require the media asset to have
                    // finished uploading + a caption. Enforces the user's ask:
                    // "publish now only enable when we both caption and video
                    // uploaded successfully".
                    modeIncomplete = captionEmpty || !mediaReady;
                  } else if (postType === 'image') {
                    // Image mode allows either caption-only OR media-only, but
                    // if media IS present it must be finished uploading.
                    modeIncomplete = (captionEmpty && !anyMediaPresent) ||
                                     (!!imagePreview && !mediaReady);
                  } else {
                    // text (or any unspecified) — caption only.
                    modeIncomplete = captionEmpty;
                  }
                  const isBusy = loading || isScheduling;
                  return (
                <button
                  onClick={activeActionTab === 'publish' ? handlePublish : handleSchedule}
                  disabled={isBusy || noTargets || modeIncomplete}
                  className="w-full py-3 bg-[#F55600] text-white rounded-2xl font-semibold text-sm uppercase tracking-widest shadow-2xl shadow-orange-200/40 hover:scale-[1.02] active:scale-95 transition-all flex items-center justify-center gap-3 disabled:opacity-40"
                >
                  {loading || isScheduling ? <RefreshCw className="w-5 h-5 animate-spin" /> : activeActionTab === 'publish' ? <Send className="w-5 h-5" /> : <Calendar className="w-5 h-5" />}
                  {loading ? 'Publishing...' : isScheduling ? 'Scheduling...' : activeActionTab === 'publish' ? 'Publish Now' : 'Schedule'}
                </button>
                  );
                })()}

                {!isReadOnly(user) && (
                <button
                  onClick={handleSaveDraft}
                  className="w-full mt-3 py-2 bg-[#2B2926] text-white text-[10px] font-semibold uppercase tracking-widest border-2 border-[#2B2926] rounded-2xl hover:bg-[#F55600] hover:border-[#F55600] shadow-sm transition-all active:scale-95 flex items-center justify-center gap-2"
                >
                  Save to Drafts
                </button>
                )}
              </div>
            </div>
          </div>

          {message && (
            <div className={`mt-8 p-5 rounded-2xl text-center font-bold text-sm ${message.includes('✅') ? 'bg-green-50 text-green-700 border border-green-100' : 'bg-red-50 text-red-700 border border-red-100'}`}>
              {message}
            </div>
          )}
        </div>
      )}
      </div>
    </div>
  );
};

export default Posts;
