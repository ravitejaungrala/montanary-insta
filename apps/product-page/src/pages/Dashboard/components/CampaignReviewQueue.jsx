import React from 'react';
import {
  Loader2, CheckCircle2, RefreshCw, AlertTriangle, Search, Sparkles,
  Clock, Calendar, Linkedin, Instagram, Facebook, MapPin, ArrowLeft,
  Edit3, Trash2,
} from 'lucide-react';
import XIcon from '../../../components/icons/XIcon';
import CalendarPicker from '../../../components/CalendarPicker';
import RadialClockPicker from '../../../components/CustomTimePicker';
import { isDocumentMedia } from '../../../components/PostMedia';
// Replaces the browser-native window.confirm() dialog on Reject with the
// app's own themed confirm modal so the UX matches the rest of the app.
import { useNotification } from '../../../context/NotificationContext';

/**
 * Review Queue — card-grid layout for the strategy/schedule flow.
 *
 * Every row coming back from /scheduled?include_awaiting_review=true is
 * one (day × platform) slot. There are two card flavours:
 *
 *   • STATIC card  (post_type=standard) — content was pre-generated at
 *                  approval time. Shows image + caption + edit/approve/
 *                  regenerate/reject actions. Status='awaiting_review'
 *                  while still generating shows a "Generating…" placeholder.
 *
 *   • RESEARCH card (post_type=agentic) — content will be generated JIT on
 *                   the scheduled date. Shows topic + editable date/time +
 *                   approve/edit/reject. NO image preview — there's nothing
 *                   to preview yet.
 *
 * Approve → flips status to 'pending' (publisher will fire on schedule).
 * Polls every 5 s so pre-generated content streams in as it lands.
 */

const platformIcon = (platform, size = 14) => {
  const px = `${size}px`;
  switch ((platform || '').toLowerCase()) {
    case 'linkedin':  return <img src="/linkedlin.jpg" style={{ width: px, height: px }} className="object-contain" alt="LinkedIn" />;
    case 'twitter':
    case 'x':         return <XIcon className="text-[#2B2926]" size={size} />;
    case 'instagram': return <img src="/instagram.jpg" style={{ width: px, height: px }} className="object-contain" alt="Instagram" />;
    case 'facebook':  return <img src="/facebook.png" style={{ width: px, height: px }} className="object-contain" alt="Facebook" />;
    default:          return <MapPin className="text-gray-400" size={size} />;
  }
};

const platformLabel = (p) => (p || '').toUpperCase();

const formatDateChip = (iso) => {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }).toUpperCase();
  } catch { return iso; }
};

const formatTime = (iso) => {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false });
  } catch { return ''; }
};

const formatDateInput = (iso) => {
  if (!iso) return '';
  try { return new Date(iso).toISOString().slice(0, 10); } catch { return ''; }
};
const formatTimeInput = (iso) => {
  if (!iso) return '09:00';
  try {
    const d = new Date(iso);
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  } catch { return '09:00'; }
};
const buildIsoFromInputs = (dateStr, timeStr) => {
  try {
    const d = new Date(`${dateStr}T${timeStr || '09:00'}:00`);
    return d.toISOString();
  } catch { return null; }
};

const isReady = (post) => {
  // Static rows are "ready" only when pre-gen has filled content.
  // Research rows are always "ready" — they have nothing to wait for.
  if (!post) return false;
  if (post.status !== 'awaiting_review') return false;
  if (post.post_type === 'agentic') return true;
  const c = (post.content || '').trim();
  return c.length > 2 && c !== '{}';
};

const extractContent = (post) => {
  if (!post?.content) return '';
  const c = post.content;
  if (typeof c !== 'string') return '';
  if (c.trim().startsWith('{')) {
    try {
      const obj = JSON.parse(c);
      const first = Object.values(obj).find(v => typeof v === 'string' && v.trim());
      return first || '';
    } catch { return c; }
  }
  return c;
};

const extractPlatform = (post) => {
  try {
    const t = post?.targets || {};
    return Object.keys(t)[0] || 'post';
  } catch { return 'post'; }
};

const extractTopicFromBrief = (brief) => {
  if (!brief) return '';
  const m = brief.match(/SPECIFIC POST TOPIC:\s*(.+)/);
  return m ? m[1].trim() : '';
};

const buildBriefWithTopic = (originalBrief, newTopic) => {
  if (!originalBrief) return `SPECIFIC POST TOPIC: ${newTopic}`;
  if (originalBrief.includes('SPECIFIC POST TOPIC:')) {
    return originalBrief.replace(/SPECIFIC POST TOPIC:.*/, `SPECIFIC POST TOPIC: ${newTopic}`);
  }
  return `${originalBrief}\n\nSPECIFIC POST TOPIC: ${newTopic}`;
};

const CampaignReviewQueue = ({ authAxios, setDashboardStep, onAllApproved }) => {
  const { toast, confirm } = useNotification();
  const [posts, setPosts] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState('');
  const [editingId, setEditingId] = React.useState(null);
  const [editFields, setEditFields] = React.useState({ content: '', topic: '', date: '', time: '' });
  const [busyIds, setBusyIds] = React.useState({});
  // Booking from POST /start-campaign runs in a backend BackgroundTask, so
  // the first few fetches may legitimately return an empty array before any
  // row has been inserted. Without this flag we'd auto-bounce back to the
  // brief screen the instant the review opens.
  const [hasSeenRows, setHasSeenRows] = React.useState(false);
  // Timestamp of when this component mounted — used as a hard timeout floor
  // before we declare "nothing showed up, bail back to brief".
  const mountedAtRef = React.useRef(Date.now());

  const fetchPosts = React.useCallback(async () => {
    try {
      const res = await authAxios.get('/scheduled', { params: { include_awaiting_review: true } });
      const all = Array.isArray(res.data) ? res.data : [];
      const filtered = all.filter(p => p.status === 'awaiting_review' || p.status === 'failed');
      setPosts(filtered);
      if (filtered.length > 0) setHasSeenRows(true);
      setError('');
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to load review queue.');
    } finally {
      setLoading(false);
    }
  }, [authAxios]);

  // Fixed-cadence polling. Previous version had `posts` in the dep array
  // which caused the interval to be torn down + re-created on every poll
  // (since setPosts always creates a new array reference), making the
  // effect run continuously and re-mount the timer on every render.
  React.useEffect(() => {
    fetchPosts();
    const t = setInterval(fetchPosts, 2500);
    return () => clearInterval(t);
  }, [fetchPosts]);

  // Sort by scheduled_for ascending so the calendar reads naturally.
  const ordered = React.useMemo(
    () => [...posts].sort((a, b) => new Date(a.scheduled_for) - new Date(b.scheduled_for)),
    [posts]
  );

  const setBusy = (id, kind) => setBusyIds(s => ({ ...s, [id]: kind }));
  const clearBusy = (id) => setBusyIds(s => { const { [id]: _, ...rest } = s; return rest; });

  const beginEdit = (post) => {
    setEditingId(post.id);
    setEditFields({
      content: extractContent(post),
      topic: extractTopicFromBrief(post.campaign_brief),
      date: formatDateInput(post.scheduled_for),
      time: formatTimeInput(post.scheduled_for),
    });
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditFields({ content: '', topic: '', date: '', time: '' });
  };

  const buildApproveBody = (post) => {
    if (editingId !== post.id) return {};
    const body = {};
    const newIso = buildIsoFromInputs(editFields.date, editFields.time);
    if (newIso && new Date(newIso).toISOString() !== new Date(post.scheduled_for).toISOString()) {
      body.scheduled_for = newIso;
    }
    if (post.post_type === 'agentic') {
      const originalTopic = extractTopicFromBrief(post.campaign_brief);
      if (editFields.topic && editFields.topic !== originalTopic) {
        body.campaign_brief = buildBriefWithTopic(post.campaign_brief, editFields.topic);
      }
    } else {
      // Static: edited caption goes into content
      if (typeof editFields.content === 'string' && editFields.content !== extractContent(post)) {
        body.content = editFields.content;
      }
    }
    return body;
  };

  const handleApprove = async (post) => {
    setBusy(post.id, 'approve');
    try {
      const body = buildApproveBody(post);
      await authAxios.post(`/scheduled/${post.id}/approve`, body);
      if (editingId === post.id) cancelEdit();
      await fetchPosts();
    } catch (e) {
      setError(e?.response?.data?.detail || 'Approve failed.');
    } finally {
      clearBusy(post.id);
    }
  };

  const handleRegenerate = async (post) => {
    setBusy(post.id, 'regen');
    try {
      await authAxios.post(`/scheduled/${post.id}/regenerate`);
      await fetchPosts();
    } catch (e) {
      setError(e?.response?.data?.detail || 'Regenerate failed.');
    } finally {
      clearBusy(post.id);
    }
  };

  const handleReject = async (post) => {
    // Was window.confirm() — the ugly browser-native dialog didn't match
    // the app's design system. The shared confirm() renders the same
    // themed modal Drafts/Calendar/Posts use for their delete actions.
    const ok = await confirm({
      title: 'Reject post?',
      message: 'This post will be removed from the schedule. This action cannot be undone.',
      confirmText: 'Reject',
    });
    if (!ok) return;
    setBusy(post.id, 'reject');
    try {
      await authAxios.delete(`/scheduled/${post.id}`);
      await fetchPosts();
      toast.success('Post rejected and removed from the schedule.');
    } catch (e) {
      const msg = e?.response?.data?.detail || 'Reject failed.';
      setError(msg);
      toast.error(msg);
    } finally {
      clearBusy(post.id);
    }
  };

  const handleApproveAll = async () => {
    const ready = ordered.filter(p => isReady(p) && p.status !== 'failed');
    for (const p of ready) {
      try {
        setBusy(p.id, 'approve');
        // eslint-disable-next-line no-await-in-loop
        await authAxios.post(`/scheduled/${p.id}/approve`, {});
      } catch (_) { /* errors surface on the next fetch */ } finally {
        clearBusy(p.id);
      }
    }
    await fetchPosts();
  };

  // Empty queue → tell parent to go back home, but ONLY after we've
  // genuinely seen rows at least once. The backend's process_bulk_campaign
  // runs in a BackgroundTask, so the first few polls legitimately return
  // an empty array before any row has been inserted. Bouncing back on
  // those early empties would kick the user out of the review screen
  // right after they hit Approve.
  React.useEffect(() => {
    if (!loading && hasSeenRows && ordered.length === 0 && onAllApproved) {
      const t = setTimeout(() => onAllApproved(), 50);
      return () => clearTimeout(t);
    }
  }, [loading, hasSeenRows, ordered.length, onAllApproved]);

  const readyCount     = ordered.filter(p => isReady(p)).length;
  const generatingCount = ordered.filter(p => p.status === 'awaiting_review' && !isReady(p)).length;
  const failedCount    = ordered.filter(p => p.status === 'failed').length;
  const researchCount  = ordered.filter(p => p.post_type === 'agentic').length;
  const staticCount    = ordered.filter(p => p.post_type === 'standard').length;

  if (loading && posts.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-slate-500">
        <Loader2 className="w-8 h-8 animate-spin text-[#F55600] mb-3" />
        <p className="text-xs font-semibold uppercase tracking-widest">Loading review queue…</p>
      </div>
    );
  }

  return (
    <div className="space-y-4 animate-in fade-in slide-in-from-bottom-4 duration-700">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-3">
        <div>
          <button
            onClick={() => setDashboardStep('plan')}
            className="flex items-center gap-2 text-slate-500 hover:text-[#F55600] font-bold text-[10px] mb-2 transition-colors group uppercase tracking-widest"
          >
            <ArrowLeft className="w-3.5 h-3.5 transition-transform group-hover:-translate-x-1" />
            Back to plan
          </button>
          <h2 className="text-2xl font-semibold text-[#2B2926] tracking-tight">Review & Approve Posts</h2>
          <p className="text-[#2B2926] text-xs font-semibold mt-1">
            Approve each post to schedule it · static posts show generated content · research posts generate on their post date
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {generatingCount > 0 && (
            <div className="px-3 py-1.5 bg-amber-50 text-amber-700 rounded-lg border-2 border-amber-200 flex items-center gap-2 shadow-sm">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              <span className="text-[9px] font-semibold uppercase tracking-widest">{generatingCount} Generating</span>
            </div>
          )}
          {staticCount > 0 && (
            <div className="px-3 py-1.5 bg-emerald-50 text-emerald-700 rounded-lg border-2 border-emerald-200 flex items-center gap-2 shadow-sm">
              <Sparkles className="w-3.5 h-3.5" />
              <span className="text-[9px] font-semibold uppercase tracking-widest">{staticCount} Static</span>
            </div>
          )}
          {researchCount > 0 && (
            <div className="px-3 py-1.5 bg-blue-50 text-blue-700 rounded-lg border-2 border-blue-200 flex items-center gap-2 shadow-sm">
              <Search className="w-3.5 h-3.5" />
              <span className="text-[9px] font-semibold uppercase tracking-widest">{researchCount} Research</span>
            </div>
          )}
          {failedCount > 0 && (
            <div className="px-3 py-1.5 bg-red-50 text-red-700 rounded-lg border-2 border-red-200 flex items-center gap-2 shadow-sm">
              <AlertTriangle className="w-3.5 h-3.5" />
              <span className="text-[9px] font-semibold uppercase tracking-widest">{failedCount} Failed</span>
            </div>
          )}
          {readyCount > 0 && (
            <button
              onClick={handleApproveAll}
              className="px-4 py-2 bg-[#F55600] hover:bg-[#e65a2b] text-white rounded-xl font-semibold text-[10px] flex items-center gap-2 transition-all shadow-md uppercase tracking-widest"
            >
              Approve all ({readyCount})
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="p-3 bg-red-50 border-2 border-red-200 rounded-xl text-xs text-red-700 font-semibold flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" />
          {error}
        </div>
      )}

      {/* Card grid */}
      {ordered.length === 0 ? (
        hasSeenRows ? (
          <div className="bg-white rounded-2xl border-2 border-slate-200 p-12 text-center">
            <Sparkles className="w-10 h-10 text-emerald-500 mx-auto mb-3" />
            <p className="text-sm font-semibold text-[#2B2926]">Nothing left to review.</p>
            <p className="text-xs text-slate-500 mt-1">All posts approved · returning to brief…</p>
          </div>
        ) : (
          <div className="bg-white rounded-2xl border-2 border-slate-200 p-12 text-center">
            <Loader2 className="w-10 h-10 text-[#F55600] animate-spin mx-auto mb-3" />
            <p className="text-sm font-semibold text-[#2B2926]">Setting up your campaign…</p>
            <p className="text-xs text-slate-500 mt-1">Booking slots and pre-generating static posts. This can take a minute.</p>
          </div>
        )
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
          {ordered.map((post) => {
            const isResearch = post.post_type === 'agentic';
            const platform = extractPlatform(post);
            const content = extractContent(post);
            const topic = extractTopicFromBrief(post.campaign_brief);
            const ready = isReady(post);
            const failed = post.status === 'failed' || !!post.error_message;
            const busy = busyIds[post.id];
            const inEdit = editingId === post.id;

            return (
              <div
                key={post.id}
                className={`bg-white rounded-2xl border-2 ${
                  failed ? 'border-red-300' :
                  isResearch ? 'border-blue-200' : 'border-slate-200'
                } shadow-sm overflow-hidden flex flex-col relative`}
              >
                {/* Top date chip + platform icon */}
                <div className="flex items-center justify-between px-3 pt-3 pb-2">
                  <div className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-white text-[10px] font-semibold uppercase tracking-widest shadow-sm ${
                    isResearch ? 'bg-blue-500' : 'bg-emerald-500'
                  }`}>
                    <Clock className="w-3 h-3" />
                    {formatDateChip(post.scheduled_for)}
                  </div>
                  <div className="flex items-center gap-1">
                    {platformIcon(platform, 16)}
                  </div>
                </div>

                {/* Image / PDF preview — STATIC ONLY when ready.
                    Document posts (LinkedIn carousels) ship as PDFs so an
                    <img> tag can't render them; we use an <iframe> with
                    LinkedIn-feed-styled chrome around it. */}
                {!isResearch && ready && post.image_url && isDocumentMedia(post) ? (
                  <div className="px-3">
                    <div className="relative w-full h-40 rounded-xl border border-slate-200 overflow-hidden bg-slate-50">
                      <iframe
                        src={`${post.image_url}#toolbar=0&navpanes=0&scrollbar=0&view=Fit`}
                        title="Carousel PDF preview"
                        className="w-full h-full pointer-events-none"
                      />
                      <div className="absolute top-1 left-1 bg-[#F55600] text-white text-[8px] font-black uppercase tracking-widest px-1.5 py-0.5 rounded">
                        PDF Carousel
                      </div>
                    </div>
                  </div>
                ) : !isResearch && ready && post.image_url ? (
                  <div className="px-3">
                    <img
                      src={post.image_url}
                      alt=""
                      className="w-full h-40 object-cover rounded-xl border border-slate-200"
                    />
                  </div>
                ) : !isResearch && !ready && !failed ? (
                  <div className="px-3">
                    <div className="w-full h-40 rounded-xl border-2 border-dashed border-[#F55600]/50 bg-transparent flex flex-col items-center justify-center text-[#2B2926]">
                      <Loader2 className="w-5 h-5 animate-spin mb-1.5 text-[#F55600]" />
                      <p className="text-[9px] font-semibold uppercase tracking-widest">Generating…</p>
                    </div>
                  </div>
                ) : isResearch ? (
                  <div className="px-3">
                    <div className="w-full h-40 rounded-xl border border-dashed border-blue-200 bg-blue-50/40 flex flex-col items-center justify-center text-blue-600 px-3 text-center">
                      <Search className="w-5 h-5 mb-1.5" />
                      <p className="text-[9px] font-semibold uppercase tracking-widest">Research post</p>
                      <p className="text-[10px] text-blue-700/80 mt-1">
                        Content generates on {formatDateChip(post.scheduled_for)} at {formatTime(post.scheduled_for)}
                      </p>
                    </div>
                  </div>
                ) : null}

                {/* Body */}
                <div className="px-4 pt-3 pb-2 flex-1 flex flex-col gap-2">
                  {/* Topic (research-card editable; static-card readonly header) */}
                  {isResearch ? (
                    inEdit ? (
                      <input
                        type="text"
                        value={editFields.topic}
                        onChange={e => setEditFields(f => ({ ...f, topic: e.target.value }))}
                        className="w-full text-xs font-bold text-[#2B2926] border-2 border-orange-200 rounded px-2 py-1 outline-none focus:ring-2 focus:ring-orange-100"
                        placeholder="Topic / Title"
                      />
                    ) : (
                      <p className="text-xs font-bold text-[#2B2926] line-clamp-2">
                        {topic || '(no topic)'}
                      </p>
                    )
                  ) : (
                    // Static caption preview
                    ready ? (
                      inEdit ? (
                        <textarea
                          value={editFields.content}
                          onChange={e => setEditFields(f => ({ ...f, content: e.target.value }))}
                          rows={6}
                          className="w-full text-[11px] text-[#2B2926] border-2 border-orange-200 rounded p-2 outline-none focus:ring-2 focus:ring-orange-100"
                        />
                      ) : (
                        <p className="text-[11px] text-[#2B2926] line-clamp-5 whitespace-pre-line leading-relaxed">
                          {content || '(no content)'}
                        </p>
                      )
                    ) : failed ? (
                      <p className="text-[11px] text-red-700 italic">
                        {post.error_message || 'Generation failed.'}
                      </p>
                    ) : (
                      <p className="text-[11px] text-slate-400 italic">Pre-generating caption…</p>
                    )
                  )}

                  {/* Date/time row (always shown; editable when editing) */}
                  <div className="flex flex-wrap items-center gap-2 mt-auto pt-2 border-t border-slate-100">
                    {inEdit ? (
                      <>
                        <div className="w-[150px] shrink-0">
                          <CalendarPicker
                            value={editFields.date}
                            onChange={(d) => setEditFields(f => ({ ...f, date: d }))}
                            placeholder="Pick date"
                          />
                        </div>
                        <div className="w-[160px] shrink-0">
                          <RadialClockPicker
                            selectedTime={editFields.time || '09:00'}
                            onTimeChange={(t) => setEditFields(f => ({ ...f, time: t }))}
                          />
                        </div>
                      </>
                    ) : (
                      <>
                        <div className="text-[10px] font-semibold text-slate-600 flex items-center gap-1">
                          <Calendar className="w-3 h-3" />
                          {new Date(post.scheduled_for).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })}
                        </div>
                        <div className="text-[10px] font-semibold text-[#F55600] flex items-center gap-1 ml-auto">
                          <Clock className="w-3 h-3" />
                          {formatTime(post.scheduled_for)}
                        </div>
                      </>
                    )}
                  </div>
                </div>

                {/* Action footer */}
                <div className="px-3 pb-3 pt-2 flex items-center gap-1.5 flex-wrap">
                  {ready || isResearch ? (
                    <>
                      <button
                        onClick={() => handleApprove(post)}
                        disabled={!!busy}
                        className="flex-1 min-w-[110px] inline-flex items-center justify-center gap-1.5 px-3 py-2 bg-[#F55600] hover:bg-[#e65a2b] disabled:opacity-50 text-white rounded-lg font-semibold text-[10px] uppercase tracking-widest shadow-sm transition-colors"
                      >
                        {busy === 'approve' && <Loader2 className="w-3 h-3 animate-spin" />}
                        {inEdit ? 'Save & approve' : 'Approve'}
                      </button>
                      {inEdit ? (
                        <button
                          onClick={cancelEdit}
                          className="px-2.5 py-2 bg-slate-100 hover:bg-slate-200 text-slate-600 rounded-lg font-semibold text-[10px] uppercase tracking-widest"
                          title="Cancel edit"
                        >
                          Cancel
                        </button>
                      ) : (
                        <button
                          onClick={() => beginEdit(post)}
                          className="px-2.5 py-2 bg-[#2B2926] hover:bg-[#1a1816] text-white rounded-lg font-semibold text-[10px] uppercase tracking-widest flex items-center gap-1"
                          title="Edit"
                        >
                          <Edit3 className="w-3 h-3" />
                        </button>
                      )}
                      {!isResearch && !inEdit && (
                        <button
                          onClick={() => handleRegenerate(post)}
                          disabled={!!busy}
                          className="px-2.5 py-2 bg-emerald-50 hover:bg-emerald-100 disabled:opacity-50 text-[#065F46] border border-emerald-200 rounded-lg font-semibold text-[10px] uppercase tracking-widest flex items-center gap-1"
                          title="Regenerate"
                        >
                          {busy === 'regen' ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
                        </button>
                      )}
                      {!inEdit && (
                        <button
                          onClick={() => handleReject(post)}
                          disabled={!!busy}
                          className="px-2.5 py-2 bg-red-50 hover:bg-red-100 disabled:opacity-50 text-red-700 border border-red-200 rounded-lg font-semibold text-[10px] uppercase tracking-widest"
                          title="Reject"
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      )}
                    </>
                  ) : failed ? (
                    <>
                      <button
                        onClick={() => handleRegenerate(post)}
                        disabled={!!busy}
                        className="flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-2 bg-blue-50 hover:bg-blue-100 text-blue-700 border border-blue-200 rounded-lg font-semibold text-[10px] uppercase tracking-widest"
                      >
                        {busy === 'regen' ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
                        Retry
                      </button>
                      <button
                        onClick={() => handleReject(post)}
                        disabled={!!busy}
                        className="px-2.5 py-2 bg-red-50 hover:bg-red-100 text-red-700 border border-red-200 rounded-lg font-semibold text-[10px] uppercase tracking-widest"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </>
                  ) : (
                    <div className="w-full text-center py-1.5 text-[10px] font-semibold text-[#F55600] bg-orange-50 rounded-lg border border-[#F55600]/30 uppercase tracking-widest flex items-center justify-center gap-1.5">
                      <Loader2 className="w-3 h-3 animate-spin" />
                      Generating content…
                    </div>
                  )}
                </div>

                {/* Subtle status badge top-right corner */}
                {ready && !failed && (
                  <div className="absolute top-3 right-3 pointer-events-none">
                    {/* Empty — already showed platform icon there; reserved for future status icon */}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default CampaignReviewQueue;
