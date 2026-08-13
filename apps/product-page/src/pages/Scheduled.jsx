import React, { useState, useEffect } from 'react';
import { Calendar, Clock, Trash2, Send, AlertCircle, RefreshCw, Image as ImageIcon, Type, X, ChevronRight, Save, Monitor, Edit3 } from 'lucide-react';
import { formatInTimezone } from '../utils/timezones';
import { useNotification } from '../context/NotificationContext';
import { isoToDatetimeLocalInput, datetimeLocalInputToIso } from '../utils/postContent';
import BrandFilter, { EMPTY_SEL as BRAND_FILTER_EMPTY } from '../components/BrandFilter';
import PlatformLogo from '../components/PlatformLogo';
import { isDocumentMedia } from '../components/PostMedia';
import { createPortal } from 'react-dom';
import DateTimePicker from '../components/DateTimePicker';
// Fallback renderer for manually-uploaded PDF carousels that have no
// pre-baked slide-1 thumbnail. Same component the Drafts page uses so
// scheduled cards look identical to draft cards.
import PdfThumbnail from '../components/PdfThumbnail';

const EditScheduledModal = ({ post, user, onSave, onCancel }) => {
  // Need toast for the past-date reschedule guard below.
  const { toast } = useNotification();
  const [content, setContent] = useState(post.content);
  // S-4 fix: the old `post.scheduled_for.split('Z')[0]` pattern only worked
  // when the backend emitted a Z-suffixed ISO string. With offset suffixes
  // (e.g. "+05:30") it silently produced a malformed datetime-local value
  // and the time displayed was wrong. isoToDatetimeLocalInput normalises
  // any ISO variant to the user's chosen timezone.
  const [scheduledFor, setScheduledFor] = useState(
    isoToDatetimeLocalInput(post.scheduled_for, user?.timezone)
  );
  const [activePlatform, setActivePlatform] = useState(Object.keys(post.targets)[0] || 'default');
  
  // Parse content if it's JSON
  const getParsedContent = () => {
    try {
      if (typeof content === 'string' && content.startsWith('{')) {
        return JSON.parse(content);
      }
    } catch (e) {}
    return { default: content };
  };

  const [platformContent, setPlatformContent] = useState(getParsedContent());

  const handleTextChange = (text) => {
    const updated = { ...platformContent, [activePlatform]: text };
    setPlatformContent(updated);
    setContent(JSON.stringify(updated));
  };

  const handleSave = () => {
    // S-4 fix: `new Date(scheduledFor).toISOString()` interprets the naive
    // datetime-local value as the BROWSER'S local time, not the user's chosen
    // app timezone. For a user in UTC with app timezone=Asia/Kolkata, typing
    // "09:00" would save as 09:00 UTC instead of 09:00 IST (= 03:30 UTC).
    // datetimeLocalInputToIso interprets the wall-clock in the stated tz.
    const newIso = datetimeLocalInputToIso(scheduledFor, user?.timezone);

    // Past-date guard — the modal was silently accepting a rescheduled time
    // in the past, which produced "scheduled" rows the cron worker skipped
    // over forever. Match the same validation the composer applies on
    // initial schedule.
    if (!newIso || Number.isNaN(new Date(newIso).getTime())) {
      toast.error('Pick a valid date and time.');
      return;
    }
    if (new Date(newIso).getTime() <= Date.now()) {
      toast.error('Time must be in the future — pick a later date/time.');
      return;
    }

    onSave({
      ...post,
      content: content,
      scheduled_for: newIso,
    });
  };

  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 animate-in fade-in duration-200">
      {/* Click-outside closer — must reference `onCancel` (the actual prop
          name on this component). Previously called an undeclared `onClose`
          which blew up the entire modal with ReferenceError the moment the
          admin tried to open an existing scheduled post for editing. */}
      <div className="absolute inset-0" onClick={onCancel} />
      <div className="bg-white w-full max-w-2xl rounded-2xl overflow-hidden shadow-2xl border-2 border-slate-350 flex flex-col max-h-[90vh] animate-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="p-6 border-b-2 border-[#2B2926]/30 flex items-center justify-between bg-white">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-white flex items-center justify-center border-2 border-[#F55600]">
              <RefreshCw className="w-5 h-5 text-[#F55600]" />
            </div>
            <div>
              <h3 className="text-lg font-bold text-[#2B2926]">Edit Scheduled Post</h3>
              <p className="text-[10px] text-[#2B2926] font-bold uppercase tracking-widest">Adjust before it goes live</p>
            </div>
          </div>
          <button onClick={onCancel} className="p-2 hover:bg-slate-100 rounded-lg transition-all text-slate-400 hover:text-slate-600">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-6">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Left: Platform Editing */}
            <div className="space-y-4">
              <div>
                <label className="text-[10px] font-semibold uppercase tracking-widest text-[#F55600] mb-3 block">1. Refine Content</label>
                <div className="flex flex-wrap gap-1.5 mb-4 p-1 bg-slate-100 rounded-xl">
                   {Object.keys(post.targets).map(p => (
                     <button
                        key={p}
                        onClick={() => setActivePlatform(p)}
                        className={`px-3 py-1.5 rounded-lg text-xs font-bold transition-all flex items-center gap-2 ${activePlatform === p ? 'bg-white text-[#2B2926] shadow-sm' : 'text-slate-400 hover:text-slate-600'}`}
                     >
                       <PlatformLogo platform={p} className="w-4 h-4" />
                       <span className="capitalize">{p === 'twitter' ? 'X' : p}</span>
                     </button>
                   ))}
                </div>
                <textarea 
                  value={platformContent[activePlatform] || platformContent.default || ""}
                  onChange={(e) => handleTextChange(e.target.value)}
                  className="w-full h-40 bg-white border-2 border-slate-350 rounded-xl p-4 text-sm text-slate-600 focus:outline-none focus:border-[#F55600] focus:ring-2 focus:ring-[#F55600]/20 transition-all resize-none font-medium"
                  placeholder="Refine your message..."
                />
                <div className="mt-2 text-[9px] font-bold text-[#2B2926]">
                  {(platformContent[activePlatform] || "").length} characters
                </div>
              </div>
            </div>

            {/* Right: Rescheduling */}
            <div className="space-y-4">
               <div className="p-6 bg-white rounded-2xl border-2 border-[#F55600]">
                  <label className="text-[10px] font-semibold uppercase tracking-widest text-[#F55600] mb-4 block">2. Update Schedule</label>
                  <div className="space-y-4">
                    <div className="flex flex-col gap-2">
                      <span className="text-[10px] font-bold text-[#2B2926] uppercase tracking-tight ml-1">Date & Time</span>
                      <DateTimePicker
                        value={scheduledFor}
                        onChange={setScheduledFor}
                      />
                      <div className="flex items-center gap-1.5 text-[10px] text-[#2B2926] font-bold ml-1">
                        <AlertCircle className="w-3 h-3 text-[#F55600]" />
                        <span>Timezone: {user?.timezone || 'UTC'}</span>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Media Preview */}
                {post.image_url && (() => {
                  const isVideo = post.media_type === 'video' || /\.(mp4|mov|webm|m4v|avi)(\?|$)/i.test(post.image_url || '');
                  const isDoc = isDocumentMedia(post);
                  return (
                    <div className="p-6 bg-white rounded-2xl border-2 border-slate-350">
                      <label className="text-[10px] font-semibold uppercase tracking-widest text-slate-400 mb-3 block">Media Preview</label>
                      <div className="h-44 rounded-xl overflow-hidden bg-slate-50 border border-slate-200 relative flex items-center justify-center">
                        {isDoc ? (
                          post.thumbnail_url ? (
                            <img
                              src={post.thumbnail_url}
                              alt="Carousel cover"
                              className="w-full h-full object-contain"
                            />
                          ) : (
                            <PdfThumbnail
                              src={post.image_url}
                              page={1}
                              className="w-full h-full bg-white"
                              alt="Carousel cover"
                            />
                          )
                        ) : isVideo ? (
                          <video
                            src={post.image_url}
                            controls
                            preload="metadata"
                            playsInline
                            className="w-full h-full object-contain bg-slate-900"
                          />
                        ) : (
                          <img
                            src={post.image_url}
                            alt="Post preview"
                            className="w-full h-full object-contain"
                          />
                        )}
                      </div>
                    </div>
                  );
                })()}
              </div>
            </div>
          </div>

        {/* Footer */}
        <div className="p-6 border-t-2 border-[#2B2926]/30 flex items-center gap-3 bg-white">
          <button
            onClick={onCancel}
            className="flex-1 py-2.5 text-sm font-bold text-[#2B2926] bg-white/60 backdrop-blur-md border border-[#2B2926]/15 rounded-xl shadow-sm hover:bg-white/90 hover:border-[#2B2926]/25 transition-all"
          >
            Discard
          </button>
          <button 
            onClick={handleSave}
            className="flex-[2] py-2.5 bg-[#F55600] text-white text-sm font-semibold rounded-xl hover:shadow-lg transition-all shadow-md shadow-slate-200/50 flex items-center justify-center gap-2 uppercase tracking-wide"
          >
            <Save className="w-4 h-4" /> Save & Reschedule
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
};

const Scheduled = ({ authAxios, connections, user, posts: scheduledPosts, setPosts: setGlobalPosts, fetchScheduled: globalFetchScheduled, loadedStatus }) => {
  // Don't show spinner if we already have data (seeded from localStorage via App.jsx).
  const [loading, setLoading] = useState(!loadedStatus.scheduled && (!scheduledPosts || scheduledPosts.length === 0));
  const [editingPost, setEditingPost] = useState(null);
  const [brandFilterSel, setBrandFilterSel] = useState(BRAND_FILTER_EMPTY);
  const [selectedMemberIds, setSelectedMemberIds] = useState([]);
  const [errorMsg, setErrorMsg] = useState(null);
  const { toast, confirm } = useNotification();
  const [refreshToast, setRefreshToast] = useState(null);
  useEffect(() => {
    if (!refreshToast) return;
    const t = setTimeout(() => setRefreshToast(null), 3000);
    return () => clearTimeout(t);
  }, [refreshToast]);

  const extractError = (err, fallback) =>
    err?.response?.data?.detail || err?.message || fallback;

  const fetchScheduled = async (force = false) => {
    setLoading(true);
    setErrorMsg(null);
    try {
      const qs = selectedMemberIds.length > 0
        ? `?member_user_ids=${selectedMemberIds.join(',')}`
        : '';
      // Use global fetcher which tracks loadedStatus
      await globalFetchScheduled(force, qs);
    } catch (err) {
      setErrorMsg(extractError(err, 'Failed to load scheduled posts.'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // Only fetch if not loaded once, or if member filters changed
    if (!loadedStatus.scheduled || selectedMemberIds.length > 0) {
      fetchScheduled();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedMemberIds]);

  const handleUpdate = async (updatedPost) => {
    try {
      await authAxios.put(`/scheduled/${updatedPost.id}`, {
        content: updatedPost.content,
        scheduled_for: updatedPost.scheduled_for
      });
      setGlobalPosts(prev => prev.map(p => p.id === updatedPost.id ? updatedPost : p));
      setEditingPost(null);
      setErrorMsg(null);
    } catch (err) {
      setErrorMsg(extractError(err, 'Failed to update scheduled post.'));
    }
  };

  const handleCancel = async (e, id) => {
    e.stopPropagation();
    const ok = await confirm({
      title: 'Cancel Post',
      message: 'Are you sure you want to cancel this scheduled post? It will be permanently removed.',
      confirmText: 'Yes, Cancel'
    });
    if (!ok) return;
    try {
      await authAxios.delete(`/scheduled/${id}`);
      setGlobalPosts(prev => prev.filter(p => p.id !== id));
      if (setGlobalPosts) setGlobalPosts(prev => prev.filter(p => p.id !== id));
    } catch (err) {
      setErrorMsg(extractError(err, 'Failed to cancel scheduled post.'));
    }
  };

  const handlePostNow = async (e, post) => {
    e.stopPropagation();
    const ok = await confirm({
      title: 'Post Immediately',
      message: 'Are you sure you want to post this content immediately to all selected channels?',
      confirmText: 'Post Now'
    });
    if (!ok) return;
    // S-10 fix: if /post succeeds but /scheduled/:id DELETE fails, previously
    // the UI showed a generic "Failed to publish now" and the scheduled entry
    // stayed in the list — implying the post never went out when in fact it
    // did. Track the two phases independently and roll back local state on
    // the exact failure mode.
    let posted = false;
    try {
      await authAxios.post('/post', {
        content: post.content,
        image_url: post.image_url,
        targets: post.targets,
        media_type: post.media_type,
      });
      posted = true;
      await authAxios.delete(`/scheduled/${post.id}`);
      setGlobalPosts(prev => prev.filter(p => p.id !== post.id));
      setErrorMsg(null);
      toast.success('Published successfully! ✅');
    } catch (err) {
      if (posted) {
        // Published but cleanup failed — refresh from server so list matches
        // server truth instead of leaving a stale entry.
        setErrorMsg('Posted successfully, but could not remove from schedule. Refreshing...');
        fetchScheduled();
      } else {
        setErrorMsg(extractError(err, 'Failed to publish now.'));
      }
    }
  };

  const getPlatformIcon = (p) => <PlatformLogo platform={p} className="w-4 h-4" />;

  const getContentForPlatform = (post, platform = 'default') => {
    const { content, post_type, campaign_brief } = post;
    
    // For Agentic posts that are waiting to be generated
    if (post_type === 'agentic' && (!content || content === '{}' || content === '')) {
      const topicLine = campaign_brief?.split('\n').find(l => l.includes('SPECIFIC POST TOPIC:'));
      const topic = topicLine ? topicLine.replace('SPECIFIC POST TOPIC: ', '') : 'Autonomous AI Strategy';
      return `🤖 AI Agent Goal: ${topic}`;
    }

    if (!content) return "No content";
    try {
      if (typeof content === 'string' && content.startsWith('{')) {
        const json = JSON.parse(content);
        return json[platform] || json.default || Object.values(json)[0] || "No content";
      }
    } catch (e) {}
    return content;
  };

  return (
    <div className="min-h-screen bg-white py-8 px-4">
      <div className="max-w-[1600px] mx-auto">
        {/* Header Section */}
        <div className="mb-4">
          <div className="flex flex-col md:flex-row items-center justify-between gap-4 md:gap-6">
            <div>
              <h1 className="text-4xl font-semibold text-[#2B2926] tracking-tight mb-2">
                Scheduled <span className="text-[#F55600]">Posts</span>
              </h1>
              <p className="text-sm text-slate-600 font-medium">Manage your upcoming social media content</p>
            </div>
            <div className="flex flex-col md:flex-row items-stretch md:items-center gap-2 md:gap-3 w-full md:w-auto">
              <BrandFilter
                user={user}
                authAxios={authAxios}
                value={brandFilterSel}
                onChange={({ sel, selectedMemberIds: ids }) => {
                  setBrandFilterSel(sel);
                  setSelectedMemberIds(ids || []);
                }}
              />
              <button
                onClick={async () => {
                  await fetchScheduled(true);
                  setRefreshToast('Scheduled posts loaded');
                }}
                className="w-full md:w-auto p-3 bg-[#F55600] rounded-xl text-white shadow-lg shadow-orange-200/20 hover:shadow-xl hover:scale-105 transition-all border-2 border-[#F55600]/20 flex items-center justify-center"
                title="Refresh scheduled posts"
              >
                <RefreshCw className={`w-5 h-5 ${loading ? 'animate-spin' : ''}`} />
              </button>
            </div>
          </div>
        </div>

        {/* S-10: inline error banner so async failures (fetch / update / cancel
            / post-now) are visible instead of silently console.error'd */}
        {errorMsg && (
          <div
            role="alert"
            className="mb-4 flex items-start gap-3 p-4 rounded-xl border-2 border-red-200 bg-red-50 text-red-800"
          >
            <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" />
            <div className="flex-1 text-sm font-medium leading-snug">{errorMsg}</div>
            <button
              onClick={() => setErrorMsg(null)}
              className="p-1 rounded hover:bg-red-100 text-red-400 hover:text-red-700 shrink-0"
              aria-label="Dismiss error"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        )}

        {/* Content */}
        {loading ? (
          <div className="py-20 text-center flex flex-col items-center">
            <RefreshCw className="w-8 h-8 text-slate-300 animate-spin mb-4" />
            <p className="text-slate-500 font-medium">Loading your scheduled posts...</p>
          </div>
        ) : scheduledPosts.length === 0 ? (
          <div className="bg-white rounded-2xl p-20 border-2 border-slate-350 shadow-sm flex flex-col items-center text-center">
            <div className="w-20 h-20 bg-white rounded-full flex items-center justify-center mx-auto mb-4 border-2 border-[#F55600]/20 shadow-sm">
              <Clock className="w-10 h-10 text-slate-300" />
            </div>
            <h4 className="text-lg font-bold text-[#2B2926] mb-2">No scheduled posts yet</h4>
            <p className="text-slate-500 text-sm">Create and schedule content to grow your presence.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            {scheduledPosts.map((post, index) => {
              const isOrange = index % 2 === 0;
              return (
                <div 
                  key={post.id} 
                  onClick={() => setEditingPost(post)}
                  className={`bg-white rounded-2xl border-2 border-[#2B2926] shadow-sm hover:shadow-lg transition-all duration-300 overflow-hidden flex flex-col min-h-[480px] group cursor-pointer hover:scale-105 relative hover:border-[#F55600]`}
                >
                  {/* Edit Icon - Top Right */}
                  <div className="absolute top-16 right-4 z-20 opacity-0 group-hover:opacity-100 transition-opacity">
                    <div className="bg-white shadow-md rounded-full p-2 border border-[#2B2926]/30 text-[#F55600]">
                      <Edit3 className="w-4 h-4" />
                    </div>
                  </div>

                  {/* Header */}
                  <div className={`px-2 py-1.5 border-b-2 bg-white ${
                    !isOrange ? 'border-[#F55600]/10' : 'border-[#10B981]/20'
                  } flex items-center justify-between gap-1 flex-wrap`}>
                    <div className={`flex items-center gap-1.5 text-[9px] font-semibold px-2 py-1 rounded-full uppercase tracking-tight border shadow-sm ${
                      isOrange 
                        ? 'bg-[#F55600] text-white border-[#F55600]' 
                        : 'bg-[#10B981] text-white border-[#10B981]'
                    }`}>
                      <Clock className="w-3 h-3 text-white" />
                      {formatInTimezone(post.scheduled_for, user?.timezone, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                    </div>
                    <div className="flex -space-x-1">
                      {/* Only show platforms that actually have an account
                          targeted — a key with an empty array (e.g. a platform
                          toggled on then off) must not render an icon. */}
                      {Object.entries(post.targets)
                        .filter(([, ids]) => Array.isArray(ids) && ids.length > 0)
                        .map(([p]) => (
                        <div key={p} className="w-5 h-5 rounded-full bg-white ring-1 ring-slate-200 flex items-center justify-center overflow-hidden">
                          <PlatformLogo platform={p} className="w-3.5 h-3.5" />
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Media preview. Video posts (media_type === 'video' or a
                      video file URL) show a lightweight Video placeholder
                      instead of trying to load the clip into an <img>, which
                      previously failed and rendered "No image". */}
                  {post.image_url && (() => {
                    const isVideo = post.media_type === 'video' || /\.(mp4|mov|webm|m4v|avi)(\?|$)/i.test(post.image_url || '');
                    const isDoc = isDocumentMedia(post);
                    return (
                    <div className="h-40 overflow-hidden bg-slate-50 border-b border-[#2B2926]/30 relative">
                      {isDoc ? (
                        post.thumbnail_url ? (
                          <a
                            href={post.image_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="block w-full h-full relative bg-white"
                          >
                            <img
                              src={post.thumbnail_url}
                              alt="Carousel cover slide"
                              loading="lazy"
                              className="w-full h-full object-contain"
                            />
                            <span className="absolute top-2 left-2 bg-[#F55600] text-white text-[8px] font-black uppercase tracking-widest px-1.5 py-0.5 rounded shadow">
                              PDF Carousel
                            </span>
                          </a>
                        ) : (
                          // No pre-baked slide-1 PNG (typical for manual PDF
                          // uploads or legacy Agent Post rows created before
                          // thumbnail_url was wired through). Render slide 1
                          // client-side via pdf.js — same fallback the
                          // Drafts page uses. This is what the user was
                          // seeing rendered in Drafts but not here.
                          <a
                            href={post.image_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="block w-full h-full relative bg-white"
                          >
                            <PdfThumbnail
                              src={post.image_url}
                              page={1}
                              className="absolute inset-0 w-full h-full bg-white"
                              alt="Carousel cover slide"
                            />
                            <span className="absolute top-2 left-2 bg-[#F55600] text-white text-[8px] font-black uppercase tracking-widest px-1.5 py-0.5 rounded shadow z-10">
                              PDF Carousel
                            </span>
                          </a>
                        )
                      ) : isVideo ? (
                        <video
                          src={post.image_url}
                          controls
                          preload="metadata"
                          playsInline
                          className="w-full h-full object-contain bg-slate-900"
                        />
                      ) : (
                        <img
                          src={post.image_url}
                          alt=""
                          className="w-full h-full object-contain group-hover:scale-105 transition-transform duration-300"
                          onError={(e) => {
                            // via.placeholder.com is unreliable — use local inline SVG data URI.
                            e.target.onerror = null;
                            e.target.src = "data:image/svg+xml;utf8,%3Csvg%20xmlns%3D'http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg'%20width%3D'150'%20height%3D'150'%20viewBox%3D'0%200%20150%20150'%3E%3Crect%20width%3D'150'%20height%3D'150'%20fill%3D'%23F5F1EB'%2F%3E%3Ctext%20x%3D'50%25'%20y%3D'50%25'%20font-family%3D'system-ui%2Csans-serif'%20font-size%3D'14'%20fill%3D'%23999'%20text-anchor%3D'middle'%20dy%3D'0.3em'%3ENo%20image%3C%2Ftext%3E%3C%2Fsvg%3E";
                          }}
                        />
                      )}
                    </div>
                    );
                  })()}

                  {/* Content */}
                  <div className="flex-1 p-5 flex flex-col">
                    <p className="text-sm text-[#2B2926] leading-relaxed font-medium line-clamp-2 italic mb-4">
                      "{getContentForPlatform(post)}"
                    </p>
                    
                    {post.post_type === 'agentic' && (
                      <div className="space-y-3 flex-1 flex flex-col">
                        <div className="flex items-center gap-1.5 text-[9px] font-semibold text-[#F55600] uppercase tracking-widest bg-white w-fit px-2.5 py-1.5 rounded-md border-2 border-[#F55600] shadow-sm">
                          <RefreshCw className="w-2.5 h-2.5 animate-spin-slow" /> AI Agent
                        </div>
                        <div className="text-[11px] text-[#2B2926] font-medium p-4 bg-white rounded-lg border-2 border-[#F55600] line-clamp-6 leading-relaxed flex-1 overflow-y-auto shadow-sm">
                          <span className="font-bold text-[#F55600] block mb-2">AI Campaign Brief:</span>
                          <span className="text-[#2B2926]">{post.campaign_brief || "Generating campaign strategy..."}</span>
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Footer */}
                  <div className={`px-5 py-3 border-t-2 bg-white flex items-center gap-2 ${
                    isOrange ? 'border-[#F55600]/10' : 'border-green-200'
                  }`}>
                    <button 
                      onClick={(e) => handlePostNow(e, post)}
                      className={`flex-1 flex items-center justify-center gap-2 px-4 py-2.5 text-white text-[11px] font-bold rounded-lg transition-all shadow-sm ${
                        isOrange 
                          ? 'bg-[#10B981] hover:bg-[#059669]' 
                          : 'bg-[#F55600] hover:bg-[#F55600]/90'
                      }`}
                    >
                      <Send className="w-4 h-4" /> Post Now
                    </button>
                    <button 
                      onClick={(e) => handleCancel(e, post.id)}
                      className="flex items-center justify-center p-2 text-slate-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-all border border-transparent hover:border-red-200"
                      title="Cancel"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {editingPost && (
          <EditScheduledModal 
            post={editingPost} 
            user={user}
            onSave={handleUpdate}
            onCancel={() => setEditingPost(null)}
          />
        )}
      </div>
{refreshToast && typeof document !== 'undefined' && createPortal(
    <div className="fixed left-1/2 -translate-x-1/2 top-4 pointer-events-none" style={{ zIndex: 99999 }}>
      <div className="pointer-events-auto inline-flex items-center gap-2.5 bg-white border-2 border-[#10B981]/45 shadow-[0_18px_40px_rgba(43,41,38,0.22)] rounded-full pl-4 pr-3 py-2.5">
        <span className="relative flex items-center justify-center w-2 h-2">
          <span className="absolute inline-flex h-full w-full rounded-full bg-[#10B981] opacity-60 animate-ping" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-[#10B981]" />
        </span>
        <span className="text-[12px] font-bold text-[#2B2926] whitespace-nowrap">{refreshToast}</span>
        <button type="button" onClick={() => setRefreshToast(null)} className="ml-1 inline-flex items-center justify-center w-5 h-5 rounded-full text-[#2B2926]/45 hover:text-[#2B2926] hover:bg-[#2B2926]/[0.05] transition-colors" aria-label="Dismiss">
          <X className="w-3 h-3" strokeWidth={2.4} />
        </button>
      </div>
    </div>,
    document.body
  )}
    </div>

  );
};

export default Scheduled;
