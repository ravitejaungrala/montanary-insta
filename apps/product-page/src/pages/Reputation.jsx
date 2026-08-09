import React, { useState, useEffect, useMemo } from 'react';
import { MessageSquare, Filter, Calendar, Search, MoreVertical, Send, Sparkles, CheckCircle2, AlertCircle, Clock, ArrowLeft, ChevronRight, ChevronLeft, PanelLeftOpen, User, Zap, Globe, Mail, Briefcase, ShoppingBag, Loader2, RefreshCw, Linkedin as LinkedinIcon, Facebook as FacebookIcon, Instagram as InstagramIcon, Youtube as YoutubeIcon } from 'lucide-react';
import XIcon from '../components/icons/XIcon';
import BrandSelect from '../components/BrandSelect';
import { motion, AnimatePresence } from 'framer-motion';

// Dynamic platform → logo image map. Drives every platform badge so adding
// a platform is just one entry here (no per-platform JSX branches). Any
// platform without an image (e.g. X) falls back to its vector icon.
const PLATFORM_IMAGES = {
  linkedin:  '/linkedlin.jpg',
  facebook:  '/facebook.png',
  instagram: '/instagram.jpg',
  youtube:   '/youtube-icon.png',
};

// Format a date string for display; returns null for missing/invalid
// values so callers can fall back instead of showing today's date.
const fmtDate = (v) => {
  if (!v) return null;
  const d = new Date(v);
  return isNaN(d.getTime()) ? null : d.toLocaleDateString();
};

const AutoResizeTextarea = ({ value, onChange, className, placeholder }) => {
  const textareaRef = React.useRef(null);
  
  React.useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = textareaRef.current.scrollHeight + 'px';
    }
  }, [value]);

  return (
    <textarea
      ref={textareaRef}
      value={value}
      onChange={onChange}
      className={`${className} overflow-hidden resize-none`}
      rows={1}
      placeholder={placeholder}
    />
  );
};

// Module-level cache so toggling filters or re-visiting the page paints
// the previous result instantly. Keyed by `${platform}|${date}`. A
// silent background refetch updates the entries in place.
const _reputationPostsCache = new Map(); // key -> { rows, fetchedAt }
const _reputationPostsInflight = new Map(); // key -> Promise (de-dup)

// Per-post comments cache so clicking a post you've already viewed
// paints the thread INSTANTLY, with a silent background refetch in the
// background. Keyed by post.instance_id. 5-minute TTL.
const _reputationCommentsCache = new Map(); // instance_id -> { comments, fetchedAt }
const _COMMENTS_CACHE_TTL_MS = 5 * 60 * 1000;

// LocalStorage persistence — survives full browser reloads so the very
// first visit of a new session can also paint instantly (stale-while-
// revalidate against fresh data from the network). Capped to a couple
// of recent filter combinations to keep the storage footprint small.
const _LS_KEY = 'pipelyt_reputation_cache_v1';
const _MAX_CACHE_AGE_MS = 10 * 60 * 1000; // 10 minutes

(function _hydrateFromStorage() {
  try {
    const raw = localStorage.getItem(_LS_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return;
    Object.entries(parsed).forEach(([k, v]) => {
      if (v && Array.isArray(v.rows) && typeof v.fetchedAt === 'number') {
        // Drop entries older than 10 minutes — the underlying social
        // posts may have new comments, fresh likes, etc.
        if (Date.now() - v.fetchedAt < _MAX_CACHE_AGE_MS * 6) {
          _reputationPostsCache.set(k, v);
        }
      }
    });
  } catch { /* corrupted cache — ignore */ }
})();

const _persistCache = () => {
  try {
    const obj = {};
    for (const [k, v] of _reputationPostsCache.entries()) obj[k] = v;
    localStorage.setItem(_LS_KEY, JSON.stringify(obj));
  } catch { /* quota exceeded — ignore */ }
};

const Reputation = ({ authAxios, user, initialPosts = [], setInitialPosts }) => {
  // Seed from the persisted cache when the prop is empty (fresh app load
  // / full page reload). Falls back to whatever the App-level state has
  // — which itself populates from the cache on first fetch.
  const [posts, setPosts] = useState(() => {
    if (initialPosts && initialPosts.length > 0) return initialPosts;
    const cached = _reputationPostsCache.get('all|all');
    return cached?.rows || [];
  });
  const [selectedPost, setSelectedPost] = useState(null);
  // Collapse the left posts list to an icon strip on iPad-range (md..lg) when
  // a post is open, so the comments panel gets the full width. User taps the
  // icon to expand back. Desktop (xl+) always shows both panels.
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  useEffect(() => {
    if (selectedPost && typeof window !== 'undefined' && window.innerWidth >= 768 && window.innerWidth < 1280) {
      setLeftCollapsed(true);
    }
    if (!selectedPost) setLeftCollapsed(false);
  }, [selectedPost]);
  const [comments, setComments] = useState([]);
  // Live mirror of `comments` for use inside async callbacks that must NOT
  // capture a stale snapshot (e.g. deciding whether a background refresh
  // failure should wipe the thread or just surface a yellow warning pill).
  const commentsRef = React.useRef(comments);
  React.useEffect(() => { commentsRef.current = comments; }, [comments]);
  const [loading, setLoading] = useState(false);
  const [commentsLoading, setCommentsLoading] = useState(false);
  const [commentError, setCommentError] = useState(null);
  // Distinct from `commentError`: this fires when a BACKGROUND refresh on
  // the currently-open post fails BUT we already have comments displayed.
  // Rendered as a small yellow pill above the thread so the thread stays
  // visible — never wipes.
  const [bgRefreshError, setBgRefreshError] = useState(null);
  const latestSelectedId = React.useRef(null);
  const [filterDate, setFilterDate] = useState('all');
  const [filterPlatform, setFilterPlatform] = useState('all');
  // Default high→low so the loudest threads (most comments) surface first.
  // 'desc' = high→low, 'asc' = low→high. Sort runs client-side off the
  // posts array; no refetch needed when the user flips it.
  const [sortOrder, setSortOrder] = useState('desc');
  // Auto-Reply is now a PAGE-WIDE, SERVER-PERSISTED setting.
  // When ON, the backend worker (services/auto_reply_worker.py, invoked
  // by main.py's local sequencer every ~5 min) auto-generates + posts AI
  // replies to any un-replied comments across every connected account.
  // The old per-post-view local state is gone — the whole flow is
  // headless once the toggle is ON.
  const [autoCommenting, setAutoCommenting] = useState(false);
  const [autoReplyStats, setAutoReplyStats] = useState({
    total_auto_replies: 0,
    last_reply_at: null,
  });
  const [autoReplyLoading, setAutoReplyLoading] = useState(false);
  const [aiReplies, setAiReplies] = useState({}); // {comment_id: reply}
  const [replyInputs, setReplyInputs] = useState({}); // {comment_id: text}
  const [confirmingReplies, setConfirmingReplies] = useState(false);
  const [generatingReplies, setGeneratingReplies] = useState(false);
  const [selectedReplies, setSelectedReplies] = useState([]);
  // Set of post instance_ids currently being refreshed. Used to show a
  // subtle spinner on the post card while the live-comments fetch runs.
  const [refreshingInstanceIds, setRefreshingInstanceIds] = useState(new Set());
  const [isRefreshingAll, setIsRefreshingAll] = useState(false);

  // Live-refresh comments for a batch of posts. Bypasses the client cache.
  // Rate-limited: `concurrency=4` in-flight, 200ms stagger.
  //
  // Semantics (2026-08 rework):
  //  - **Success + non-empty comments**: update cache, update thread if this
  //    post is currently open, CLEAR _liveFetchError + _rateLimitedUntil so
  //    the left-card badge stops lying.
  //  - **Success + empty comments**: same as above (the platform legitimately
  //    reported zero comments). Still clears the warning state.
  //  - **Error response (data.error set) OR thrown exception**:
  //      * NEVER wipe already-displayed comments in the detail panel.
  //      * Mark _liveFetchError so the left-card badge shows an amber warning.
  //      * If the error smells like a rate limit (429 / "rate limit" /
  //        "quota"), set _rateLimitedUntil = now + 15 min so the auto-tick
  //        skips this post until the window resets.
  //      * If this post is currently open AND we already had comments
  //        displayed, keep the thread, expose a background-refresh warning
  //        via _bgRefreshError so the UI can render a small yellow pill
  //        above the thread.
  //      * If this post is currently open AND we had NO comments, surface
  //        the friendly error message via setCommentError (single source).
  const _refreshAllCommentsLive = React.useCallback(async (postsList) => {
    if (!authAxios || !Array.isArray(postsList) || postsList.length === 0) return;
    setIsRefreshingAll(true);
    const CONCURRENCY = 4;
    let idx = 0;
    const worker = async () => {
      while (idx < postsList.length) {
        const my = idx++;
        const post = postsList[my];
        if (!post || !post.id || !post.instance_id) continue;
        // Rate-limit backoff: skip a post whose last error was a 429
        // < 15 min ago. Retrying it would 429 again and waste quota.
        if (post._rateLimitedUntil && Date.now() < post._rateLimitedUntil) {
          continue;
        }
        setRefreshingInstanceIds((prev) => {
          const next = new Set(prev); next.add(post.instance_id); return next;
        });
        try {
          const r = await authAxios.get(
            `/analytics/posts/${post.id}/comments?platform=${post.platform}&account_id=${post.account_id}&native_id=${post.native_id}&instance_id=${post.instance_id}`
          );
          const arr = Array.isArray(r?.data?.comments) ? r.data.comments : [];
          const platformComments = arr
            .map((c) => ({ ...c, platform: post.platform }))
            .sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
          const errMsg = r?.data?.error || null;

          // Cache latest snapshot regardless — sentinel `error` field lets
          // handleSelectPost decide if it should fall back to a cache read.
          _reputationCommentsCache.set(post.instance_id, {
            comments: platformComments,
            fetchedAt: Date.now(),
            error: errMsg,
          });

          const liveCount = platformComments.length;
          const isRateLimit = errMsg && /rate limit|429|quota|throttle/i.test(errMsg);

          setPosts((prev) => prev.map((p) => {
            if (p.instance_id !== post.instance_id) return p;
            // ERROR path: keep any displayed comments (right panel handled
            // separately below), mark the warning badge, apply 15-min
            // backoff if it was a rate limit.
            if (errMsg) {
              return {
                ...p,
                _liveFetchError: errMsg,
                _rateLimitedUntil: isRateLimit
                  ? (Date.now() + 15 * 60 * 1000)
                  : (p._rateLimitedUntil || null),
              };
            }
            // SUCCESS path: reconcile badge count DOWN when live disagrees,
            // clear warning state + backoff.
            const badgeCount = (p.metrics && p.metrics.comments) || 0;
            const base = {
              ...p,
              _liveFetchError: null,
              _rateLimitedUntil: null,
            };
            if (liveCount === badgeCount) return base;
            return {
              ...base,
              metrics: { ...(p.metrics || {}), comments: liveCount },
            };
          }));

          // Right panel: only touch it if this is the currently-open post.
          if (latestSelectedId.current === post.instance_id) {
            if (errMsg) {
              // Preserve any comments that were already displayed — a
              // background refresh failure must not blank the thread.
              const currentDisplayed = commentsRef.current || [];
              if (currentDisplayed.length > 0) {
                // We had comments — keep them, expose a yellow pill via
                // _bgRefreshError (rendered above the thread by the UI).
                setBgRefreshError(errMsg);
              } else {
                // We had nothing — this is the primary error state.
                setCommentError(errMsg);
              }
            } else {
              setComments(platformComments);
              setCommentError(null);
              setBgRefreshError(null);
            }
          }
        } catch (e) {
          const msg = e?.message || 'Fetch failed';
          setPosts((prev) => prev.map((p) =>
            p.instance_id === post.instance_id
              ? { ...p, _liveFetchError: msg }
              : p
          ));
          // Same "don't wipe" rule for network exceptions on the open post.
          if (latestSelectedId.current === post.instance_id) {
            const currentDisplayed = commentsRef.current || [];
            if (currentDisplayed.length > 0) {
              setBgRefreshError(msg);
            }
          }
        } finally {
          setRefreshingInstanceIds((prev) => {
            const next = new Set(prev); next.delete(post.instance_id); return next;
          });
          // 200ms stagger before this worker picks up the next post.
          await new Promise((res) => setTimeout(res, 200));
        }
      }
    };
    const workers = Array.from({ length: Math.min(CONCURRENCY, postsList.length) }, worker);
    await Promise.all(workers);
    setIsRefreshingAll(false);
  }, [authAxios]);

  // NOTE: The old client-side "auto-generate AI replies when Agent is ON
  // + a post opens" effect was removed. Auto-Reply is now a page-wide,
  // server-persisted setting handled entirely by the backend worker
  // (services/auto_reply_worker.py, invoked by the local sequencer
  // every ~5 minutes). The user no longer needs to open a post for its
  // comments to be auto-replied to — the worker sweeps EVERY connected
  // account's recent posts every tick, regardless of what the frontend
  // is showing. Turning the page-level toggle ON is a one-click,
  // fire-and-forget action; replies land on the platforms directly.

  // Build a properly threaded, de-duplicated comment list for rendering.
  // Platform APIs sometimes return a reply BEFORE its parent comment, and
  // the auto-comment agent occasionally posted the SAME reply more than
  // once (before the platform surfaced the first) — both made the thread
  // look wrong. Here every reply is grouped directly under its parent, and
  // identical replies from the same author under one parent collapse to a
  // single row.
  const threadedComments = useMemo(() => {
    const list = Array.isArray(comments) ? comments : [];
    const roots = list.filter((c) => !c.parent_id);
    const repliesByParent = {};
    list.filter((c) => c.parent_id).forEach((r) => {
      (repliesByParent[r.parent_id] = repliesByParent[r.parent_id] || []).push(r);
    });
    const dedupe = (arr) => {
      const seen = new Set();
      return arr.filter((r) => {
        const key = `${(r.author_name || '').trim().toLowerCase()}|${(r.message || r.text || '').trim().toLowerCase()}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
    };
    const ordered = [];
    roots.forEach((root) => {
      ordered.push(root);
      dedupe(repliesByParent[root.id] || []).forEach((r) => ordered.push(r));
      delete repliesByParent[root.id];
    });
    // Orphan replies (parent not in the fetched set) — still shown at the
    // end, de-duplicated, rather than dropped.
    Object.keys(repliesByParent).forEach((pid) => {
      dedupe(repliesByParent[pid]).forEach((r) => ordered.push(r));
    });
    return ordered;
  }, [comments]);

  const platforms = [
    { id: 'all', name: 'All Platforms', icon: Globe },
    { id: 'linkedin', name: 'LinkedIn', icon: LinkedinIcon },
    { id: 'facebook', name: 'Facebook', icon: FacebookIcon },
    { id: 'instagram', name: 'Instagram', icon: InstagramIcon },
    { id: 'twitter', name: 'X', icon: XIcon },
    { id: 'youtube', name: 'YouTube', icon: YoutubeIcon },
    { id: 'tiktok', name: 'TikTok', icon: ({ className, size }) => (
      <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-label="TikTok">
        <path d="M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.08-.14 1.62.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z" />
      </svg>
    ) }
  ];

  const dateOptions = [
    { id: 'all', name: 'All Time' },
    { id: 'week', name: 'Past Week' },
    { id: 'month', name: 'Past Month' },
    { id: 'year', name: 'Past Year' }
  ];

  useEffect(() => {
    // Always run fetchPosts on mount / filter change — the function
    // itself handles the stale-while-revalidate logic: cache hit paints
    // instantly with no spinner, while a silent network request refreshes
    // the cached entry in place. Never blanks the list.
    fetchPosts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterPlatform, filterDate]);

  // Load the server-persisted Auto-Reply toggle state on mount so the
  // page-level chip reflects the user's saved preference, not stale
  // local state. Also fetches the stats block ({total, last_reply_at})
  // shown next to the toggle so the user can see it's actually running.
  useEffect(() => {
    if (!authAxios) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await authAxios.get('/reputation/auto-reply');
        if (cancelled) return;
        setAutoCommenting(!!r.data?.enabled);
        setAutoReplyStats({
          total_auto_replies: r.data?.total_auto_replies || 0,
          last_reply_at: r.data?.last_reply_at || null,
        });
      } catch (err) {
        console.error('Failed to load auto-reply setting', err);
      }
    })();
    return () => { cancelled = true; };
  }, [authAxios]);

  // Flip the toggle server-side. Uses optimistic update — flip the UI
  // immediately, then reconcile with the server response (or revert on
  // failure) so the click feels instant.
  const handleToggleAutoReply = React.useCallback(async () => {
    if (!authAxios || autoReplyLoading) return;
    const next = !autoCommenting;
    setAutoCommenting(next);
    setAutoReplyLoading(true);
    try {
      const r = await authAxios.put('/reputation/auto-reply', { enabled: next });
      setAutoCommenting(!!r.data?.enabled);
    } catch (err) {
      // Revert on failure so the UI doesn't lie about what's persisted.
      setAutoCommenting(!next);
      console.error('Failed to toggle auto-reply', err);
    } finally {
      setAutoReplyLoading(false);
    }
  }, [authAxios, autoCommenting, autoReplyLoading]);

  // Auto-refresh live comments every 5 minutes while the tab is visible.
  // Skipped when the page is hidden (tab in background) so we don't blast
  // platform APIs when nobody's watching.
  //
  // 2026-08 — narrowed scope: previously refetched EVERY visible post
  // (~30 calls per tick, blew X's 300/15min limit). Now only refetches
  // the currently-open post — the badge counts on other cards stay
  // whatever the hourly analytics-sync last stored, which is fine.
  // Users who want fresh badges NOW use the manual 🔄 button.
  useEffect(() => {
    if (!authAxios) return;
    const AUTO_REFRESH_MS = 5 * 60 * 1000;
    const tick = () => {
      if (typeof document !== 'undefined' && document.hidden) return;
      const openId = latestSelectedId.current;
      if (!openId) return;
      const openPost = (posts || []).find(p => p.instance_id === openId);
      if (!openPost) return;
      // Rate-limit backoff: skip this tick if we 429'd in the last 15 min.
      if (openPost._rateLimitedUntil && Date.now() < openPost._rateLimitedUntil) {
        return;
      }
      _refreshAllCommentsLive([openPost]);
    };
    const handle = setInterval(tick, AUTO_REFRESH_MS);
    return () => clearInterval(handle);
  }, [authAxios, posts, _refreshAllCommentsLive]);

  const fetchPosts = async () => {
    if (!authAxios) return;
    const cacheKey = `${filterPlatform}|${filterDate}`;

    // Stale-while-revalidate. If we have a cached result for this exact
    // filter combo, paint it instantly (no skeleton) and only run the
    // network request in the background to refresh in place. Avoids
    // showing the empty grey-bar skeleton on every filter toggle.
    const cached = _reputationPostsCache.get(cacheKey);
    if (cached) {
      setPosts(cached.rows);
      if (cached.rows.length > 0 && !selectedPost) {
        handleSelectPost(cached.rows[0]);
      }
      // Silent revalidate — don't show the spinner.
    } else {
      // Cold cache: only show the skeleton if we genuinely have nothing
      // to render yet. If the user already has posts visible (e.g. from
      // a different filter), keep them up while the new query runs so
      // the panel doesn't flash empty between requests.
      if (posts.length === 0) setLoading(true);
    }

    // De-dup in-flight requests for the same filter combo.
    if (_reputationPostsInflight.has(cacheKey)) {
      try { await _reputationPostsInflight.get(cacheKey); } catch {}
      return;
    }

    const work = (async () => {
      try {
        let url = `/reputation/posts?platform=${filterPlatform}`;

        if (filterDate === 'week') {
          const d = new Date(); d.setDate(d.getDate() - 7);
          url += `&start_date=${d.toISOString()}`;
        } else if (filterDate === 'month') {
          const d = new Date(); d.setMonth(d.getMonth() - 1);
          url += `&start_date=${d.toISOString()}`;
        } else if (filterDate === 'year') {
          const d = new Date(); d.setFullYear(d.getFullYear() - 1);
          url += `&start_date=${d.toISOString()}`;
        }

        const response = await authAxios.get(url);
        setPosts(response.data);
        _reputationPostsCache.set(cacheKey, { rows: response.data, fetchedAt: Date.now() });
        _persistCache();
        if (setInitialPosts && filterPlatform === 'all' && filterDate === 'all') {
          setInitialPosts(response.data);
        }
        if (response.data.length > 0 && !selectedPost) {
          handleSelectPost(response.data[0]);
        }
        // NOTE: The old "refresh comments for EVERY visible post on mount"
        // storm has been removed. It fired ~30 parallel API calls in 3-5s
        // and blew X's 300/15min limit before the page could paint,
        // stamping half the left-list cards with orange 429-warning
        // badges. Now we only refresh the currently-open post — the
        // manual 🔄 button in the filters row stays as an escape hatch
        // for users who want fresh badges NOW and are willing to pay the
        // quota cost.
      } catch (err) {
        console.error("Failed to fetch reputation posts", err);
      } finally {
        setLoading(false);
        _reputationPostsInflight.delete(cacheKey);
      }
    })();

    _reputationPostsInflight.set(cacheKey, work);
    await work;
  };

  const handleSelectPost = async (post, opts = {}) => {
    const { preserveLocalReplies = false } = opts;
    setSelectedPost(post);
    latestSelectedId.current = post.instance_id;

    // Stale-while-revalidate: if we have cached comments for this post
    // (within the TTL), paint them IMMEDIATELY — no spinner, no empty
    // panel — then silently refetch in the background to pick up new
    // comments / replies.
    const cached = _reputationCommentsCache.get(post.instance_id);
    const haveFreshCache = cached
      && Array.isArray(cached.comments)
      && (Date.now() - cached.fetchedAt) < _COMMENTS_CACHE_TTL_MS;
    if (!preserveLocalReplies) {
      if (haveFreshCache) {
        setComments(cached.comments);
        setCommentsLoading(false);
      } else {
        setComments([]);
        setCommentsLoading(true);
      }
    } else {
      setCommentsLoading(true);
    }
    setCommentError(null);
    setBgRefreshError(null);
    if (!preserveLocalReplies) {
      setAiReplies({});
      setReplyInputs({});
      setSelectedReplies([]);
    }

    // Capture our optimistic replies BEFORE the fetch starts so we can
    // re-merge them after — without this, every refetch wipes any
    // reply we just sent because LinkedIn's API takes 30 s+ to surface
    // org-page replies on a fresh GET.
    const localReplies = preserveLocalReplies
      ? comments.filter(c => c._local)
      : [];

    try {
      // Fetch comments ONLY for this specific platform instance
      const response = await authAxios.get(`/analytics/posts/${post.id}/comments?platform=${post.platform}&account_id=${post.account_id}&native_id=${post.native_id}&instance_id=${post.instance_id}`);
      // The post's comment-count badge comes from the last metrics sync, but
      // this is a LIVE fetch. If the platform returns an error (e.g. Instagram
      // needs instagram_manage_comments permission, or the token expired) the
      // live list comes back empty — surface that reason instead of silently
      // showing "No comments yet" when the badge says the post has comments.
      if ((response.data.error || response.data.supported === false) &&
          (!response.data.comments || response.data.comments.length === 0)) {
        if (latestSelectedId.current === post.instance_id) {
          const errMsg = response.data.error || 'Comments are not available for this platform yet.';
          // If we're on a preserve-local-replies refetch and there ARE
          // already comments on screen, don't wipe them — this is the
          // "background refresh failed" case. Show yellow pill above the
          // existing thread instead.
          if (preserveLocalReplies && (commentsRef.current || []).length > 0) {
            setBgRefreshError(errMsg);
          } else {
            setCommentError(errMsg);
            setComments([]);
          }
          // Rate-limit backoff on the post row so the auto-refresh
          // tick skips this post for 15 min.
          const isRateLimit = /rate limit|429|quota|throttle/i.test(errMsg);
          setPosts((prev) => prev.map((p) => (
            p.instance_id === post.instance_id
              ? {
                  ...p,
                  _liveFetchError: errMsg,
                  _rateLimitedUntil: isRateLimit
                    ? (Date.now() + 15 * 60 * 1000)
                    : (p._rateLimitedUntil || null),
                }
              : p
          )));
        }
        return;
      }
      if (response.data.comments) {
        // Add platform context to each comment
        const platformComments = response.data.comments.map(c => ({
          ...c,
          platform: post.platform
        }));
        // Sort comments by timestamp (newest first)
        platformComments.sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));

        // Ensure we are still on the same post before updating state
        if (latestSelectedId.current !== post.instance_id) return;

        // Merge: keep any local optimistic reply whose `message` text
        // doesn't already appear in the freshly-fetched list. This way
        // a Send that the platform hasn't surfaced yet still shows up,
        // and once the platform DOES surface it the duplicate gets
        // dropped silently.
        const fetchedMessages = new Set(platformComments.map(c => (c.message || '').trim()));
        const survivingLocals = localReplies.filter(
          r => !fetchedMessages.has((r.message || '').trim())
        );
        const merged = [...platformComments, ...survivingLocals].sort(
          (a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0)
        );
        setComments(merged);
        // Success path — clear all stale warning states so the left-card
        // badge stops lying and the yellow "background refresh failed"
        // pill (if any) disappears.
        setBgRefreshError(null);
        setCommentError(null);
        setPosts((prev) => prev.map((p) => (
          p.instance_id === post.instance_id
            ? { ...p, _liveFetchError: null, _rateLimitedUntil: null }
            : p
        )));
        // Cache the merged thread so the next click on this post paints
        // instantly. Optimistic local replies are included so a user
        // who just sent a reply, navigated away, and came back still
        // sees it.
        _reputationCommentsCache.set(post.instance_id, {
          comments: merged,
          fetchedAt: Date.now(),
        });

        // NOTE: Auto-trigger of AI replies when Agent is ON is handled by
        // the dedicated useEffect at the top of the component (watches
        // autoCommenting, selectedPost.instance_id, and comments.length).
        // Doing it there instead of inline here covers BOTH cases:
        //   1. user clicks a post while Agent is already ON
        //   2. user toggles Agent ON after a post is already open
        // The previous inline trigger only handled case 1.
      }
    } catch (err) {
      console.error("Failed to fetch comments", err);
      if (latestSelectedId.current === post.instance_id) {
        const msg = err?.response?.data?.detail || err?.message || "Failed to load comments";
        // Same "don't wipe on background failure" rule for network
        // exceptions: if the user was refreshing an already-visible
        // thread, keep the thread + show yellow pill. Otherwise
        // it's a first-load failure → full error state is appropriate.
        if (preserveLocalReplies && (commentsRef.current || []).length > 0) {
          setBgRefreshError(msg);
        } else {
          setCommentError(msg);
        }
      }
    } finally {
      if (latestSelectedId.current === post.instance_id) {
        setCommentsLoading(false);
      }
    }
  };

  // Helper for auto-triggering without relying on component-wide 'comments' state updates
  const handleGenerateAiRepliesSpecific = async (commentsToProcess, platform, postContent) => {
    if (commentsToProcess.length === 0) return;
    
    // Check if we already have replies for all these comments to avoid double-processing
    const pendingCount = commentsToProcess.filter(c => aiReplies[c.id]).length;
    if (pendingCount === commentsToProcess.length) return;

    setGeneratingReplies(true);
    try {
      // Extract correct context for the specific platform
      let contextText = postContent;
      if (contextText && contextText.startsWith('{')) {
        try {
          const parsed = JSON.parse(contextText);
          contextText = parsed[platform] || parsed.default || Object.values(parsed)[0];
        } catch(e) {}
      }

      const response = await authAxios.post('/reputation/generate-replies', {
        comments: commentsToProcess.map(c => ({ 
          id: c.id, 
          message: c.message || c.text || "",
          author_name: c.author_name,
          author_handle: c.author_handle
        })),
        post_context: contextText
      });
      
      // Ensure we are still on the same post before updating AI replies
      if (latestSelectedId.current && response.data.replies) {
        const newReplies = {};
        const newSelected = [...selectedReplies];
        response.data.replies.forEach(r => {
          newReplies[r.id] = r.generated_reply;
          if (!newSelected.includes(r.id)) {
            newSelected.push(r.id);
          }
        });
        setAiReplies(prev => ({ ...prev, ...newReplies }));
        setSelectedReplies(newSelected);
      }
    } catch (err) {
      console.error("Failed to generate AI replies", err);
    } finally {
      setGeneratingReplies(false);
    }
  };

  const handleGenerateAiReplies = async () => {
    if (!selectedPost || comments.length === 0) return;

    // Only generate replies for ROOT user comments that haven't been answered.
    // Skip:
    //   - the agent's own replies (have `parent_id`)
    //   - comments whose author is one of the connected accounts (also a reply
    //     by us, but flagged via `is_self`)
    //   - comments that already have a `reply_count > 0` from the platform API
    //   - comments that already have a freshly-staged AI suggestion in
    //     `aiReplies` (don't regenerate while one is pending)
    const candidates = comments.filter(c =>
      !c.parent_id &&
      !c.is_self &&
      !c.reply_count &&
      !aiReplies[c.id]
    );

    if (candidates.length === 0) {
      // All comments already have replies (or have a pending suggestion)
      // — nothing new to generate. Silently no-op so the button click is
      // a clear "nothing to do" rather than a confusing duplicate.
      return;
    }

    setGeneratingReplies(true);
    try {
      const response = await authAxios.post('/reputation/generate-replies', {
        comments: candidates.map(c => ({ id: c.id, message: c.message })),
        post_context: selectedPost.content
      });

      const newReplies = {};
      const newSelected = [...selectedReplies];
      response.data.replies.forEach(r => {
        newReplies[r.id] = r.generated_reply;
        if (!newSelected.includes(r.id)) newSelected.push(r.id);
      });
      setAiReplies(prev => ({ ...prev, ...newReplies }));
      setSelectedReplies(newSelected);
    } catch (err) {
      console.error("Failed to generate AI replies", err);
    } finally {
      setGeneratingReplies(false);
    }
  };

  const handleConfirmReplies = async () => {
    const repliesToConfirm = [];
    selectedReplies.forEach(cid => {
      const comment = comments.find(c => c.id === cid);
      if (comment && aiReplies[cid]) {
        // The selectedPost is now a single platform instance
        repliesToConfirm.push({
          platform: selectedPost.platform,
          account_id: selectedPost.account_id,
          native_post_id: selectedPost.native_id,
          comment_id: cid,
          message: aiReplies[cid]
        });
      }
    });

    if (repliesToConfirm.length === 0) return;

    setConfirmingReplies(true);
    try {
      await authAxios.post('/reputation/confirm-replies', { replies: repliesToConfirm });
      
      const newAiReplies = { ...aiReplies };
      selectedReplies.forEach(cid => delete newAiReplies[cid]);
      setAiReplies(newAiReplies);
      setSelectedReplies([]);
      
      // Refresh comments or show success
      handleSelectPost(selectedPost);
    } catch (err) {
      console.error("Failed to confirm replies", err);
    } finally {
      setConfirmingReplies(false);
    }
  };

  const handleManualReply = async (commentId, manualMessage = null) => {
    const message = manualMessage || replyInputs[commentId];
    if (!message) return;

    try {
      await authAxios.post('/reputation/confirm-replies', {
        replies: [{
          platform: selectedPost.platform,
          account_id: selectedPost.account_id,
          native_post_id: selectedPost.native_id,
          comment_id: commentId,
          message: message
        }]
      });
      // Optimistic insert — show our reply immediately so the user
      // can see it landed (LinkedIn / FB can take 30s+ to surface
      // org replies on the next GET, which made the Send button
      // look like it did nothing).
      const optimistic = {
        id: `local-${Date.now()}`,
        parent_id: commentId,
        author_name: 'You',
        author_picture: null,
        author_handle: '',
        message,
        created_at: new Date().toISOString(),
        like_count: 0,
        reply_count: 0,
        platform: selectedPost.platform,
        _local: true,
      };
      setComments(prev => [...prev, optimistic]);
      setReplyInputs(prev => ({ ...prev, [commentId]: '' }));
      // Background refetch with `preserveLocalReplies` so the optimistic
      // row survives the refetch when the platform hasn't surfaced our
      // reply yet (LinkedIn org-page replies take 30 s+ to show up via
      // GET socialActions). The merge step inside handleSelectPost
      // dedupes by message text once the platform finally returns it.
      setTimeout(() => handleSelectPost(selectedPost, { preserveLocalReplies: true }), 2000);
    } catch (err) {
      console.error("Manual reply failed", err);
    }
  };

  return (
    <div className="flex flex-col md:flex-row min-h-screen md:h-full bg-white relative">
      {/* iPad / md-range collapsed icon strip — only icon to reopen the posts list.
          Hidden when not collapsed and on xl+ (where left panel is always full). */}
      {leftCollapsed && (
        <div className="hidden md:flex xl:hidden w-[56px] md:h-full border-r border-[#2B2926]/30 bg-white flex-col items-center pt-5 shrink-0">
          <button
            onClick={() => setLeftCollapsed(false)}
            className="w-10 h-10 rounded-xl bg-[#F55600]/10 text-[#F55600] flex items-center justify-center hover:bg-[#F55600] hover:text-white transition-all shadow-sm"
            title="Show posts list"
            aria-label="Open posts list"
          >
            <PanelLeftOpen className="w-5 h-5" />
          </button>
          <div className="mt-3 text-[8px] font-semibold text-[#2B2926] uppercase tracking-widest writing-mode-vertical text-center px-1" style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)', letterSpacing: '0.18em' }}>
            Posts
          </div>
        </div>
      )}

      {/* Left Sidebar: Filter & Posts */}
      <div className={`w-full md:w-[380px] md:h-full border-r border-[#2B2926]/30 bg-white flex-col shrink-0 ${selectedPost ? 'hidden' : 'flex'} ${leftCollapsed ? 'md:hidden xl:flex' : 'md:flex'}`}>
        <div className="p-5 border-b border-[#2B2926]/30 bg-white">
          <div className="flex items-center gap-3 mb-4">
            <div className="w-10 h-10 rounded-xl bg-[#F55600]/10 flex items-center justify-center text-[#F55600] shadow-sm">
              <MessageSquare className="w-6 h-6" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-[#2B2926] leading-tight">Reputation</h2>
              <p className="text-[12px] text-[#2B2926] font-bold uppercase tracking-[0.14em] mt-0.5">Community Management</p>
            </div>
          </div>

          {/* Page-wide Auto-Reply toggle — replaces the old per-post
              toggle. When ON, the backend worker (5-min tick) fetches
              new comments on every connected account and posts an AI
              reply automatically. State is persisted server-side so
              the setting survives reload + applies whether or not the
              user has the tab open. */}
          <button
            type="button"
            onClick={handleToggleAutoReply}
            disabled={autoReplyLoading}
            aria-pressed={autoCommenting}
            className={`w-full mb-4 flex items-center justify-between gap-3 px-4 py-3 rounded-xl border-2 transition-all ${
              autoCommenting
                ? 'bg-[#F55600] text-white border-[#F55600] shadow-[0_3px_10px_rgba(245,86,0,0.32)]'
                : 'bg-white text-[#2B2926] border-[#2B2926]/30 hover:border-[#F55600]/50'
            } ${autoReplyLoading ? 'opacity-70 cursor-wait' : ''}`}
            title={
              autoCommenting
                ? 'Auto-Reply is ON. New comments across all platforms will be replied to within ~5 minutes.'
                : 'Auto-Reply is OFF. Click to enable — the AI will reply to every new comment automatically.'
            }
          >
            <div className="flex items-center gap-3 min-w-0">
              <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${
                autoCommenting ? 'bg-white/20' : 'bg-[#F55600]/10 text-[#F55600]'
              }`}>
                <Zap className="w-4 h-4" />
              </div>
              <div className="min-w-0 text-left">
                <div className={`text-[11px] font-bold uppercase tracking-[0.14em] ${autoCommenting ? 'text-white' : 'text-[#2B2926]'}`}>
                  Auto-Reply {autoCommenting ? 'On' : 'Off'}
                </div>
                <div className={`text-[10px] mt-0.5 truncate ${autoCommenting ? 'text-white/85' : 'text-[#2B2926]/60'}`}>
                  {autoCommenting
                    ? `AI replies live on every platform${autoReplyStats.total_auto_replies > 0
                        ? ` · ${autoReplyStats.total_auto_replies} sent`
                        : ''}`
                    : 'Turn on for hands-free replies across all platforms'}
                </div>
              </div>
            </div>
            {/* Native-looking switch pill */}
            <span
              className={`relative shrink-0 inline-block w-10 h-5 rounded-full transition-colors ${
                autoCommenting ? 'bg-white/30' : 'bg-slate-300'
              }`}
              aria-hidden="true"
            >
              <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${
                autoCommenting ? 'translate-x-5' : 'translate-x-0'
              }`} />
            </span>
          </button>

          {/* Filter row — STACK by default in this narrow left panel, then
              go to 3 columns only on `xl` (≥1280px container) where there's
              actually room for full labels. At smaller widths every filter
              gets the full panel width so labels like "All Platforms" /
              "High to Low" never get truncated. */}
          <div className="grid grid-cols-1 2xl:grid-cols-3 gap-2">
            <div className="flex-1 relative">
              {/* Custom BrandSelect — gives us brand-orange selected + mint-green
                  hover instead of the native OS dropdown blue/grey highlight. */}
              <BrandSelect
                value={filterPlatform}
                onChange={setFilterPlatform}
                options={platforms.map((p) => ({ value: p.id, label: p.name }))}
                size="md"
                className="w-full"
              />
            </div>
            <div className="flex-1 relative">
              <BrandSelect
                value={filterDate}
                onChange={setFilterDate}
                options={dateOptions.map((o) => ({ value: o.id, label: o.name }))}
                size="md"
                className="w-full"
              />
            </div>
            {/* Sort by comment count. Client-side, default high→low so the
                most-engaged threads bubble to the top of the left list. */}
            <div className="flex-1 relative">
              <BrandSelect
                value={sortOrder}
                onChange={setSortOrder}
                options={[{ value: 'desc', label: 'High to Low' }, { value: 'asc', label: 'Low to High' }]}
                size="md"
                className="w-full"
                title="Sort posts by number of comments"
              />
            </div>
            {/* Manual "refresh all comments" — bypasses the 5-min cache
                and re-fetches live comments for every visible post so
                users can force a resync when they know a comment just
                landed. The mount + 5-min interval already do this
                automatically; this is the escape hatch. */}
            <button
              type="button"
              onClick={() => _refreshAllCommentsLive(posts)}
              disabled={isRefreshingAll || !posts || posts.length === 0}
              className="shrink-0 w-9 h-9 flex items-center justify-center rounded-lg bg-white border border-[#2B2926]/30 hover:border-[#F55600] hover:text-[#F55600] transition-all disabled:opacity-50"
              title={isRefreshingAll ? 'Refreshing…' : 'Refresh comments for every visible post'}
            >
              <RefreshCw className={`w-4 h-4 ${isRefreshingAll ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-3 custom-scrollbar bg-white">
          {loading ? (
            // Visible skeleton on the left list while a filter / refetch
            // is in flight. Previously used bg-white on a white panel which
            // made the pulse invisible — switch to slate-100 so the
            // animation actually reads on screen.
            <div className="space-y-3">
              {[1, 2, 3, 4].map(i => (
                <div
                  key={i}
                  className="h-24 rounded-xl bg-slate-100 border border-[#2B2926]/30 animate-pulse"
                />
              ))}
            </div>
          ) : posts.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-center px-6">
              <div className="w-16 h-16 rounded-full bg-slate-100 flex items-center justify-center text-[#2B2926] mb-4">
                <Search className="w-8 h-8" />
              </div>
              <p className="text-sm font-bold text-[#2B2926]">No published posts found for the selected filter.</p>
            </div>
          ) : (
            // Sort happens client-side off `sortOrder` ('desc' = high→low).
            // We slice() first so we don't mutate the source array.
            posts
              .slice()
              .sort((a, b) => {
                const ca = (a.metrics?.comments || 0);
                const cb = (b.metrics?.comments || 0);
                return sortOrder === 'asc' ? ca - cb : cb - ca;
              })
              .map(post => {
              const currentPlatform = post.platform?.toLowerCase();
              const platformConfig = platforms.find(p => p.id === currentPlatform) || { icon: Globe, name: 'Platform' };
              const platformIcon = platformConfig.icon;
              // YouTube posts have no usable image_url (it's the video file),
              // so derive the poster frame from the video id (native_id).
              const thumb = currentPlatform === 'youtube' && post.native_id
                ? `https://img.youtube.com/vi/${String(post.native_id).trim()}/hqdefault.jpg`
                : post.image_url;

              return (
              <button
                key={post.instance_id}
                onClick={() => handleSelectPost(post)}
                className={`w-full text-left p-4 rounded-xl transition-all border ${selectedPost?.instance_id === post.instance_id ? 'bg-white border-orange-300 shadow-xl shadow-orange-100/50 ring-1 ring-orange-200' : 'bg-white border-[#2B2926]/30 hover:border-[#2B2926]/30 hover:shadow-md'}`}
              >
                <div className="flex gap-3">
                  {thumb && (
                    <div className="w-16 h-16 rounded-lg overflow-hidden shrink-0 border border-[#2B2926]/30 bg-white relative">
                      <img
                        src={thumb}
                        alt="Post"
                        className="w-full h-full object-cover"
                        onError={(e) => { e.currentTarget.style.display = 'none'; }}
                      />
                      {currentPlatform === 'youtube' && (
                        <div className="absolute inset-0 flex items-center justify-center bg-slate-900/15">
                          <span className="w-0 h-0 border-y-[6px] border-y-transparent border-l-[10px] border-l-white ml-0.5 drop-shadow" />
                        </div>
                      )}
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5 mb-1.5">
                      <div className="flex items-center gap-1">
                          {PLATFORM_IMAGES[currentPlatform] ? (
                            <img
                              src={PLATFORM_IMAGES[currentPlatform]}
                              alt={currentPlatform}
                              className="w-5 h-5 rounded object-contain"
                            />
                          ) : (
                            <div className={`w-5 h-5 rounded flex items-center justify-center ${
                              currentPlatform === 'twitter' ? 'bg-[#2B2926] text-white' : 'bg-slate-100 text-[#2B2926]'
                            }`}>
                              {React.createElement(platformIcon, { className: "w-3 h-3" })}
                            </div>
                          )}
                          <span className="text-[10px] font-semibold uppercase text-[#2B2926] tracking-tighter">
                            {currentPlatform === 'twitter' ? 'X' : currentPlatform}
                          </span>
                      </div>

                      {/* Comment Count Badge + live-refresh state */}
                      <div className={`ml-1 px-1.5 py-0.5 rounded-md text-[9px] font-semibold uppercase tracking-tighter flex items-center gap-1 ${post._liveFetchError ? 'bg-amber-500 text-white' : 'bg-[#2B2926] text-white'}`}
                        title={post._liveFetchError || ''}
                      >
                        {refreshingInstanceIds.has(post.instance_id) && (
                          <Loader2 className="w-2.5 h-2.5 animate-spin" />
                        )}
                        {post._liveFetchError && !refreshingInstanceIds.has(post.instance_id) && (
                          <AlertCircle className="w-2.5 h-2.5" />
                        )}
                        {post.metrics?.comments || 0} Comments
                      </div>

                      <span className="text-[10px] font-bold text-[#2B2926] uppercase tracking-tight ml-auto">
                        {new Date(post.created_at).toLocaleDateString()}
                      </span>
                    </div>
                    <p className="text-xs font-medium text-[#2B2926] line-clamp-2 leading-relaxed">
                      {post.content && post.content.startsWith('{') 
                        ? (() => {
                            try {
                              const parsed = JSON.parse(post.content);
                              return parsed[post.platform] || parsed.default || Object.values(parsed)[0];
                            } catch(e) { return "Post content"; }
                          })()
                        : post.content}
                    </p>
                  </div>
                </div>
              </button>
            )})
          )}
        </div>
      </div>

      {/* Right Detail: Post and Comments */}
      <div className={`flex-1 md:h-full flex flex-col bg-white overflow-hidden ${!selectedPost ? 'hidden md:flex' : 'flex'}`}>
        {selectedPost ? (
          <>
            <header className="px-4 md:px-6 py-4 border-b border-[#2B2926]/30 flex items-center justify-between bg-white z-10">
              <div className="flex items-center gap-2 md:gap-4 min-w-0">
                <button
                  onClick={() => setSelectedPost(null)}
                  className="md:hidden p-2 text-[#2B2926] hover:text-[#F55600] hover:bg-orange-50 rounded-xl transition-all"
                >
                  <ArrowLeft className="w-5 h-5" />
                </button>
                {/* iPad-range manual collapse toggle: only visible on md..lg
                    when the left panel is currently expanded. */}
                {!leftCollapsed && (
                  <button
                    onClick={() => setLeftCollapsed(true)}
                    className="hidden md:flex xl:hidden p-2 text-[#2B2926] hover:text-[#F55600] hover:bg-orange-50 rounded-xl transition-all"
                    title="Collapse posts list"
                    aria-label="Collapse posts list"
                  >
                    <ChevronLeft className="w-5 h-5" />
                  </button>
                )}
                <div className="w-12 h-12 rounded-xl bg-[#F55600]/10 flex items-center justify-center text-[#F55600]">
                  <Clock className="w-6 h-6" />
                </div>
                <div className="min-w-0">
                  <h3 className="text-base md:text-lg font-semibold text-[#2B2926] tracking-tight truncate">Post Details</h3>
                  <p className="text-[10px] md:text-[12px] text-[#2B2926] font-bold uppercase tracking-[0.1em] md:tracking-[0.14em] mt-0.5 truncate">Comments & Conversations</p>
                </div>
              </div>

              {/* Per-post Auto-Reply toggle removed — Auto-Reply is now a
                  page-wide, server-persisted setting shown in the left
                  sidebar. A passive status pill here just tells the user
                  what the page-level toggle is currently set to. */}
              {autoCommenting && (
                <div className="shrink-0 flex items-center gap-2 px-3 py-1.5 rounded-full bg-[#F55600]/10 border border-[#F55600]/30">
                  <span className="w-2 h-2 rounded-full bg-[#F55600] animate-pulse" />
                  <span className="text-[10px] font-bold uppercase tracking-[0.14em] text-[#F55600] whitespace-nowrap">
                    Auto-Reply Active
                  </span>
                </div>
              )}
            </header>

            <div className="flex-1 overflow-y-auto p-5 px-6 custom-scrollbar bg-white">
              <div className="max-w-4xl mx-auto space-y-4">
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <h4 className="text-xs font-semibold text-[#2B2926] uppercase tracking-wider flex items-center gap-2">
                       <MessageSquare className="w-3.5 h-3.5 text-orange-500" />
                       Community Voices
                    </h4>
                    
                    {/* Manual "Generate AI Replies" button has been removed
                        from the UI entirely. AI replies are now generated
                        only when Agent is ON — flipping the toggle is the
                        single source of truth. With Agent OFF, the user
                        replies manually via the per-comment "Write a custom
                        reply..." input below each card. */}

                    {/* When Agent is ON, show a passive status pill that
                        confirms auto-reply is active. Pulses while a
                        generation is currently in flight. */}
                    {autoCommenting && (
                      <div className="flex items-center gap-2 px-4 py-2 bg-[#F55600]/10 border border-[#F55600]/30 rounded-lg">
                        <div className={`w-2 h-2 rounded-full bg-[#F55600] ${generatingReplies ? 'animate-pulse' : ''}`} />
                        <span className="text-[11px] font-semibold text-[#F55600] uppercase tracking-widest">
                          {generatingReplies ? 'Generating Auto-Replies...' : 'Auto-Reply Active'}
                        </span>
                      </div>
                    )}

                    {Object.keys(aiReplies).length > 0 && (
                      <div className="flex items-center gap-3 animate-in fade-in slide-in-from-right-4">
                          <span className="text-[10px] font-semibold uppercase tracking-widest text-[#F55600]">Pending Review</span>
                          
                          <button 
                            onClick={() => {
                              setAiReplies({});
                              setSelectedReplies([]);
                            }}
                            className="text-[10px] font-semibold uppercase text-[#2B2926] hover:text-[#2B2926] transition-colors"
                          >
                            Cancel All
                          </button>

                          <button 
                           onClick={handleConfirmReplies}
                           disabled={confirmingReplies || selectedReplies.length === 0}
                           className="flex items-center gap-2 px-5 py-2.5 bg-[#F55600] text-white rounded-xl text-xs font-semibold shadow-xl shadow-[#F55600]/30 hover:bg-[#F55600]/90 transition-all active:scale-95 disabled:opacity-50"
                         >
                           {confirmingReplies ? <Clock className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5" />}
                           Confirm & Send Selected ({selectedReplies.length})
                         </button>
                       </div>
                    )}
                  </div>

                  {commentsLoading ? (
                    <div className="space-y-4">
                      {[1, 2].map(i => (
                        <div key={i} className="h-32 bg-white rounded-2xl border border-[#2B2926]/30 animate-pulse" />
                      ))}
                    </div>
                  ) : commentError ? (
                    <div className="bg-white border-2 border-[#2B2926]/15 rounded-2xl p-8 text-center">
                      <AlertCircle className="w-8 h-8 text-[#F55600] mx-auto mb-3" />
                      <p className="text-[#2B2926] text-sm font-bold max-w-[280px] mx-auto mb-4">{commentError}</p>
                      <button
                        onClick={() => handleSelectPost(selectedPost)}
                        disabled={commentsLoading}
                        className="px-4 py-2 bg-[#2B2926] text-white border border-[#2B2926] rounded-xl text-xs font-semibold shadow-sm hover:bg-[#F55600] hover:border-[#F55600] transition-all active:scale-95 disabled:opacity-50"
                      >
                        {commentsLoading ? 'Retrying…' : 'Retry Fetch'}
                      </button>
                    </div>
                  ) : comments.length === 0 ? (
                    <div className="bg-white rounded-2xl border border-dashed border-[#2B2926]/30 p-12 text-center">
                      <div className="w-16 h-16 rounded-full bg-white flex items-center justify-center text-[#2B2926] mx-auto mb-4">
                        <MessageSquare className="w-8 h-8" />
                      </div>
                      {(selectedPost?.metrics?.comments || 0) > 0 ? (
                        <>
                          <p className="text-[#2B2926] font-bold text-sm">
                            {selectedPost.metrics.comments} comment{selectedPost.metrics.comments === 1 ? '' : 's'} reported, but we couldn't fetch them right now.
                          </p>
                          <p className="text-[#2B2926] text-xs mt-2 max-w-sm mx-auto leading-relaxed">
                            The platform's search index may be slightly behind.
                            Try refreshing in a few minutes.
                          </p>
                          <button
                            onClick={() => handleSelectPost(selectedPost)}
                            disabled={commentsLoading}
                            className="mt-4 px-4 py-2 bg-[#2B2926] text-white border border-[#2B2926] rounded-xl text-xs font-semibold shadow-sm hover:bg-[#F55600] hover:border-[#F55600] transition-all active:scale-95 disabled:opacity-50"
                          >
                            {commentsLoading ? 'Retrying…' : 'Retry Fetch'}
                          </button>
                        </>
                      ) : (
                        <>
                          <p className="text-[#2B2926] font-bold text-sm">No comments yet on this post.</p>
                          <p className="text-[#2B2926] text-xs mt-1">Check back later or try another post.</p>
                        </>
                      )}
                    </div>
                  ) : (
                    <div className="space-y-4 pb-12">
                        {/* Yellow "background refresh failed" pill — fires
                            ONLY when we have comments displayed AND the last
                            background refresh (auto-tick or preserve-local
                            refetch) errored. Never wipes the thread. The
                            15-min rate-limit backoff is already applied at
                            the fetch layer, so this message is truthful:
                            we'll actually retry after the window resets. */}
                        {bgRefreshError && (
                          <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-amber-50 border border-amber-200 text-amber-900 text-[11px] font-medium">
                            <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5 text-amber-600" />
                            <span>{bgRefreshError}</span>
                          </div>
                        )}
                        {threadedComments.map((comment, idx) => (
                        <motion.div
                          layout
                          key={`${comment.platform}-${comment.id || idx}`}
                          className={`bg-white rounded-xl border transition-all ${aiReplies[comment.id] ? 'border-orange-200 shadow-lg shadow-orange-100/30' : 'border-[#2B2926]/30 shadow-sm'}`}
                        >
                          <div className="p-2.5 px-3">
                            <div className="flex items-start justify-between mb-1 gap-2 min-w-0">
                              <div className="flex items-center gap-2.5 min-w-0 flex-1">
                                <div className="w-8 h-8 rounded-full bg-gradient-to-br from-slate-100 to-slate-200 border border-[#2B2926]/30 flex items-center justify-center text-[#2B2926] font-bold text-xs overflow-hidden shrink-0">
                                  {comment.author_picture ? (
                                    <img src={comment.author_picture} alt={comment.author_name} className="w-full h-full object-cover" />
                                  ) : (
                                    comment.author_name ? comment.author_name[0] : <User className="w-4 h-4" />
                                  )}
                                </div>
                                <div className="min-w-0 flex-1">
                                  <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                                    <p className="text-xs font-semibold text-[#2B2926] truncate min-w-0">{comment.author_name || "Social User"}</p>
                                    <span className="text-[10px] text-[#2B2926] font-semibold uppercase tracking-[0.12em] whitespace-nowrap">
                                      {fmtDate(comment.created_at) || fmtDate(selectedPost?.created_at) || '—'}
                                    </span>
                                  </div>
                                  <div className="flex items-center gap-2">
                                    {/* Hide opaque platform IDs from the UI:
                                        - LinkedIn returns `urn:li:person:…` /
                                          `urn:li:organization:…` strings as the
                                          handle, which is not human-friendly.
                                        - Other platforms (Twitter/Instagram)
                                          give a real `@handle`, so we keep
                                          those visible. */}
                                    {comment.author_handle &&
                                      !/^urn:/i.test(comment.author_handle) && (
                                      <span className="text-[9px] text-orange-500 font-bold block truncate">{comment.author_handle}</span>
                                    )}
                                    {comment.parent_id && (
                                      <span className="text-[9px] bg-[#F55600] text-white px-2 py-0.5 rounded uppercase font-semibold tracking-widest shadow-sm">Reply</span>
                                    )}
                                  </div>
                                </div>
                              </div>
                            </div>
                            <p className="text-[#2B2926] text-xs leading-snug mb-1 pl-0.5">
                              {comment.message || comment.text}
                            </p>

                            {/* Reply Block — hidden for the agent's own
                                replies (parent_id, is_self) and for comments
                                that already have a reply (reply_count > 0).
                                These don't need a "Write a custom reply..."
                                input or an AI suggestion. */}
                            {!(comment.parent_id || comment.is_self || comment.reply_count > 0) && (
                            <div className="mt-2 pt-2 border-t border-slate-50 flex flex-col gap-1.5">
                              {aiReplies[comment.id] ? (
                                  <motion.div 
                                  initial={{ opacity: 0, y: 10 }}
                                  animate={{ opacity: 1, y: 0 }}
                                  className={`rounded-xl p-2.5 px-3 border relative group transition-colors ${selectedReplies.includes(comment.id) ? 'bg-[#F55600]/5 border-[#F55600]/30 shadow-sm' : 'bg-white border-[#2B2926]/30'}`}
                                >
                                  <div className="flex items-center justify-between mb-2">
                                    <div className="flex items-center gap-2">
                                      <Sparkles className={`w-3 h-3 ${selectedReplies.includes(comment.id) ? 'text-[#F55600]' : 'text-[#2B2926]'}`} />
                                      <span className={`text-[10px] font-semibold uppercase tracking-widest ${selectedReplies.includes(comment.id) ? 'text-[#F55600]' : 'text-[#2B2926]'}`}>Pipelyt Suggested Reply</span>
                                    </div>
                                    <div className="flex items-center">
                                      <input
                                        type="checkbox"
                                        checked={selectedReplies.includes(comment.id)}
                                        onChange={(e) => {
                                          if (e.target.checked) {
                                            setSelectedReplies(prev => [...prev, comment.id]);
                                          } else {
                                            setSelectedReplies(prev => prev.filter(id => id !== comment.id));
                                          }
                                        }}
                                        className="w-4 h-4 text-green-400 accent-green-400 bg-white border-[#2B2926]/30 rounded focus:ring-green-400 focus:ring-2 cursor-pointer shadow-sm transition-all outline-none"
                                      />
                                    </div>
                                  </div>
                                  <AutoResizeTextarea
                                    value={aiReplies[comment.id]}
                                    onChange={(e) => setAiReplies(prev => ({ ...prev, [comment.id]: e.target.value }))}
                                    className="w-full bg-transparent border-none focus:ring-0 text-xs text-[#2B2926] leading-snug font-medium p-0 min-h-[20px] mb-1.5"
                                  />
                                  <div className="flex items-center justify-end gap-3 pt-1.5 border-t border-[#2B2926]/30/80">
                                    <button 
                                      onClick={() => {
                                        const next = { ...aiReplies };
                                        delete next[comment.id];
                                        setAiReplies(next);
                                        setSelectedReplies(prev => prev.filter(id => id !== comment.id));
                                      }}
                                      className="text-[10px] font-semibold uppercase text-[#2B2926] hover:text-[#2B2926] transition-colors"
                                    >
                                      Dismiss
                                    </button>
                                    <button 
                                      onClick={() => handleManualReply(comment.id, aiReplies[comment.id])}
                                      className="flex items-center gap-1.5 px-2.5 py-1 bg-[#F55600] text-white rounded-md text-[10px] font-semibold uppercase tracking-wider hover:bg-[#F55600]/90 transition-all active:scale-95 shadow-sm"
                                    >
                                      <Send className="w-3 h-3" />
                                      Send This
                                    </button>
                                  </div>
                                </motion.div>
                              ) : (
                                <div className="flex gap-2 min-w-0 w-full">
                                  <input
                                    type="text"
                                    placeholder="Write a custom reply..."
                                    value={replyInputs[comment.id] || ''}
                                    onChange={(e) => setReplyInputs(prev => ({ ...prev, [comment.id]: e.target.value }))}
                                    onKeyPress={(e) => e.key === 'Enter' && handleManualReply(comment.id)}
                                    className="flex-1 min-w-0 bg-white border border-[#2B2926]/30 rounded-xl px-3 py-2 text-sm focus:ring-orange-500 focus:border-orange-500 transition-all font-medium"
                                  />
                                  <button
                                    onClick={() => handleManualReply(comment.id)}
                                    className="shrink-0 px-3 py-2 bg-slate-800 text-white rounded-xl text-xs font-bold hover:bg-slate-900 transition-all active:scale-95"
                                  >
                                    Send
                                  </button>
                                </div>
                              )}
                            </div>
                            )}
                          </div>
                        </motion.div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          </>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center p-12 text-center bg-white">
             <div className="w-20 h-20 mb-6 bg-white rounded-3xl border border-[#2B2926]/30 shadow-sm flex items-center justify-center text-[#2B2926]">
                <MessageSquare className="w-10 h-10" />
             </div>
             
             <h3 className="text-2xl font-semibold text-[#2B2926] tracking-tight mb-2">Select a Post to Begin</h3>
             <p className="text-[#2B2926] font-bold max-w-sm leading-relaxed uppercase tracking-widest text-[10px]">Select any published post from the left to manage comments and generate AI replies.</p>
          </div>
        )}
      </div>

    </div>
  );
};

export default Reputation;
