import React, { useState, useEffect, useRef} from 'react';
import { createPortal } from 'react-dom';
import { Type, Image as ImageIcon, RefreshCw, ChevronDown, CheckCircle2, Send, Clock, Filter, Trash2, Heart, MessageSquare, X, ExternalLink, Eye, Sparkles, AlertCircle } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { formatInTimezone } from '../utils/timezones';
import { useNotification } from '../context/NotificationContext';

import BrandFilter, { EMPTY_SEL as BRAND_FILTER_EMPTY } from '../components/BrandFilter';
import PlatformLogo from '../components/PlatformLogo';
// Client-side pdf.js renderer — used as the slide-1 fallback when a PDF
// carousel post has no pre-baked thumbnail_url (typical for manual PDF
// uploads and Agent Post rows created before thumbnail_url plumbing).
// Same fallback Drafts / Scheduled / Calendar now use, so preview UX is
// identical across every list.
import PdfThumbnail from '../components/PdfThumbnail';

const Published = ({ authAxios, user, connections, posts, setPosts: setGlobalPosts, fetchPublished: globalFetchPublished, loadedStatus, onRepost }) => {
  // Admin-only Brand filter (replaces the old "Company" team-member picker).
  // The cascading filter resolves to a list of member_user_ids that backend
  // uses to scope the Published-posts query.
  const [brandFilterSel, setBrandFilterSel] = useState(BRAND_FILTER_EMPTY);
  const [selectedMemberIds, setSelectedMemberIds] = useState([]);
  // Don't show spinner when data already seeded from localStorage (posts.length > 0 or loadedStatus.published).
  const [loadingPosts, setLoadingPosts] = useState(!loadedStatus.published && (!posts || posts.length === 0));
  
  // States for filters
  const [sortBy, setSortBy] = useState('Newest First');
  const [platformFilter, setPlatformFilter] = useState('All Platforms');
  const [timeFilter, setTimeFilter] = useState('All Time');
  const [accountFilter, setAccountFilter] = useState('All Accounts');
  
  // Selection states for modal.
  //
  // The modal now shows one TAB per (platform × account) instead of one per
  // platform, so a post published to LinkedIn:personal + LinkedIn:page +
  // Facebook renders 3 tabs. We track the active tab by (platform,
  // account_id) — account_id is null for platforms that don't carry the
  // per-account fanout (older rows, or platforms with a single account).
  const [selectedPost, setSelectedPost] = useState(null);
  const [activePreviewPlatform, setActivePreviewPlatform] = useState(null);
  const [activePreviewAccountId, setActivePreviewAccountId] = useState(null);
  const [activeDropdown, setActiveDropdown] = useState(null);
  const [syncingMetrics, setSyncingMetrics] = useState(false);
  const [refreshToast, setRefreshToast] = useState(null);
  useEffect(() => {
    if (!refreshToast) return;
    const t = setTimeout(() => setRefreshToast(null), 3000);
    return () => clearTimeout(t);
  }, [refreshToast]);
  // Skip backend sync if data is fresh — prevents slow refresh when user
  // mashes the Refresh button after data just loaded.
  const lastPublishedFetchAtRef = useRef(0);
  const PUBLISHED_FRESH_WINDOW_MS = 30000;  // 30 s
  // Stamp every time posts change so the fresh-window timer always reflects
  // the latest successful load (including filter-change auto-reloads).
  useEffect(() => {
    if (posts && posts.length > 0) {
      lastPublishedFetchAtRef.current = Date.now();
    }
  }, [posts]);
  // Auto-fire toast when filter dropdowns change (skip first render).
  const _filterToastFirstRunPublished = useRef(true);
  useEffect(() => {
    if (_filterToastFirstRunPublished.current) {
      _filterToastFirstRunPublished.current = false;
      return;
    }
    const bits = [];
    if (accountFilter && accountFilter !== 'All Accounts') bits.push(accountFilter);
    if (platformFilter && platformFilter !== 'All Platforms') bits.push(platformFilter);
    if (timeFilter && timeFilter !== 'All Time') bits.push(timeFilter);
    if (sortBy && sortBy !== 'Newest First') bits.push(sortBy);
    // When user is in pure defaults, fall back to a generic "Newest First" so
    // the toast always has SOMETHING to say.
    if (bits.length === 0) bits.push(sortBy || 'Newest First');
    const summary = `${bits.join(' · ')} · published posts loaded`;
    const t = setTimeout(() => setRefreshToast(summary), 200);
    return () => clearTimeout(t);
  }, [sortBy, platformFilter, timeFilter, accountFilter]);

  // P-3 fix: surface fetch + delete errors via an inline banner with the
  // actual backend detail, instead of silently console.error'ing the fetch
  // and using a generic alert() on delete.
  const [errorMsg, setErrorMsg] = useState(null);
  const extractError = (e, fallback) =>
    e?.response?.data?.detail || e?.message || fallback;
  const { toast, confirm } = useNotification();

  const fetchPosts = async (force = false) => {
    if (!authAxios) return;
    setLoadingPosts(true);
    setErrorMsg(null);
    try {
      // Map display label → backend platform key. 'X' is the user-facing
      // brand name but the backend expects 'twitter'.
      const platformKeyForBackend = platformFilter === 'All Platforms'
        ? ''
        : (platformFilter === 'X' ? 'twitter' : platformFilter.toLowerCase().replace(' ', ''));
      const platformQS = platformKeyForBackend ? `&platform=${platformKeyForBackend}` : '';
      const membersQS = selectedMemberIds.length > 0
        ? `&member_user_ids=${selectedMemberIds.join(',')}`
        : '';
      
      // Use global fetcher which tracks loadedStatus. 
      // Params are passed to maintain filtering functionality.
      await globalFetchPublished(force, `${platformQS}${membersQS}`);
    } catch (e) {
      console.error("Failed to fetch posts", e);
      setErrorMsg(extractError(e, 'Failed to load published posts.'));
    } finally {
      setLoadingPosts(false);
    }
  };

  const syncMetricsAndRefresh = async () => {
    if (!authAxios || syncingMetrics) return;
    // SHORT-CIRCUIT: if data was loaded within the last 30 s, skip the slow
    // backend metrics sync and just show an instant toast.
    const sinceLast = Date.now() - lastPublishedFetchAtRef.current;
    if (sinceLast < PUBLISHED_FRESH_WINDOW_MS && posts && posts.length > 0) {
      setRefreshToast('Published posts already up to date');
      return;
    }
    setSyncingMetrics(true);
    setErrorMsg(null);
    try {
      await authAxios.post('/analytics/sync');
      try { localStorage.setItem('pipelyt_last_metrics_sync_at', String(Date.now())); } catch {}
      await fetchPosts(true);
      lastPublishedFetchAtRef.current = Date.now();
      setRefreshToast('Published posts refreshed');
    } catch (e) {
      console.error('Failed to sync analytics metrics', e);
      setErrorMsg(extractError(e, 'Failed to sync post metrics. Please try again.'));
      setRefreshToast('Refresh failed — please try again');
    } finally {
      setSyncingMetrics(false);
    }
  };

  const handleDelete = async (postId) => {
    // Was window.confirm() — jarring OS-native dialog that broke the app's
    // design language. Match Drafts/Calendar/Posts by using the shared
    // themed confirm modal.
    const ok = await confirm({
      title: 'Delete post?',
      message: 'This will remove the post from your Published history. This action cannot be undone.',
      confirmText: 'Delete',
    });
    if (!ok) return;
    try {
      await authAxios.delete(`/posts/${postId}`);
      if (setGlobalPosts) setGlobalPosts(prev => prev.filter(p => p.id !== postId));
      setErrorMsg(null);
      toast.success('Post deleted from your history.');
    } catch (e) {
      console.error("Failed to delete post", e);
      const msg = extractError(e, 'Failed to delete post. Please try again.');
      setErrorMsg(msg);
      toast.error(msg);
    }
  };

  // Derived account options. Display the platform with its rebranded
  // label ("twitter" → "X") so the dropdown matches the rest of the
  // app, while keeping the underlying filter logic in `accountFilter`
  // intact (it only matches on the `acc.name` portion).
  const accountOptions = React.useMemo(() => {
    const all = ['All Accounts'];
    if (!connections) return all;
    Object.keys(connections).forEach(platform => {
      if (Array.isArray(connections[platform])) {
        const platLabel = platform === 'twitter' ? 'X' : (platform.charAt(0).toUpperCase() + platform.slice(1));
        connections[platform].forEach(acc => {
          all.push(`${acc.name} (${platLabel})`);
        });
      }
    });
    return all;
  }, [connections]);

  useEffect(() => {
    // Only fetch if not loaded once, or if filters changed
    if (!loadedStatus.published || selectedMemberIds.length > 0 || platformFilter !== 'All Platforms') {
      fetchPosts();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authAxios, selectedMemberIds, platformFilter]);

  // Keep Published metrics fresh even if user doesn't visit Analytics page.
  // Runs at most once every 30 minutes per browser session.
  useEffect(() => {
    if (!authAxios) return;

    const maybeSync = async () => {
      let shouldSync = true;
      try {
        const last = parseInt(localStorage.getItem('pipelyt_last_metrics_sync_at') || '0', 10);
        shouldSync = !last || (Date.now() - last) > 30 * 60 * 1000;
      } catch {
        shouldSync = true;
      }
      if (!shouldSync) return;
      await syncMetricsAndRefresh();
    };

    maybeSync();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authAxios]);

  // Filtering and Sorting Logic
  const filteredPosts = posts
    .filter(post => {
      // Platform filter — map 'X' display label to 'twitter' backend key.
      if (platformFilter !== 'All Platforms') {
        const platformKey = platformFilter === 'X'
          ? 'twitter'
          : platformFilter.toLowerCase().replace(' ', '');
        if (!post.platforms.includes(platformKey)) return false;
      }
      
      // Time filter
      if (timeFilter !== 'All Time') {
        const postDate = new Date(post.created_at);
        const now = new Date();
        if (timeFilter === 'Today') {
          if (postDate.toDateString() !== now.toDateString()) return false;
        } else if (timeFilter === 'Last 7 Days') {
          const sevenDaysAgo = new Date();
          sevenDaysAgo.setDate(now.getDate() - 7);
          if (postDate < sevenDaysAgo) return false;
        } else if (timeFilter === 'Last 30 Days') {
          const thirtyDaysAgo = new Date();
          thirtyDaysAgo.setDate(now.getDate() - 30);
          if (postDate < thirtyDaysAgo) return false;
        }
      }
      
      // Account filter — accountFilter is "<name> (<platform>)". P-7 fix:
      // regex .match(/(.+) \((.+)\)/) greedy-matches and breaks when the
      // account NAME contains its own parens (e.g. "NEUZEN AI (Beta)").
      // Use lastIndexOf to isolate the trailing "(platform)" unambiguously.
      // The display label was switched from "twitter" → "X" so we have
      // to map back to the canonical platform key before comparing
      // against post.platforms (which is still stored as "twitter").
      if (accountFilter !== 'All Accounts') {
        const lastParen = accountFilter.lastIndexOf(' (');
        if (lastParen > 0 && accountFilter.endsWith(')')) {
          let platform = accountFilter.slice(lastParen + 2, -1);
          if (platform === 'X') platform = 'twitter';
          if (!post.platforms.includes(platform)) return false;
        }
      }
      
      return true;
    })
    .sort((a, b) => {
      // Sort logic
      if (sortBy === 'Newest First') {
        return new Date(b.created_at) - new Date(a.created_at);
      } else {
        return new Date(a.created_at) - new Date(b.created_at);
      }
    });

  const toggleDropdown = (name) => {
    setActiveDropdown(activeDropdown === name ? null : name);
  };

  const FilterButton = ({ label, current, options, setter, name }) => (
    <div className="relative">
      <button
        onClick={() => toggleDropdown(name)}
        className={`w-full h-11 px-4 md:px-6 rounded-xl text-[12px] md:text-[13px] font-bold shadow-sm transition-all flex items-center justify-between gap-2 md:gap-3 whitespace-nowrap border-2 border-[#2B2926] ${
          current !== label && !current.includes('All')
            ? 'bg-white text-[#F55600] shadow-md hover:bg-[#2B2926]/[0.04] hover:shadow-lg'
            : 'bg-white text-[#2B2926] hover:bg-[#2B2926]/[0.04] hover:shadow-md'
        }`}
      >
        <span className="truncate">{current}</span>
        <ChevronDown className={`w-4 h-4 shrink-0 transition-transform duration-300 ${activeDropdown === name ? 'rotate-180 text-[#F55600]' : 'text-[#2B2926]/50'}`} />
      </button>
      
      {activeDropdown === name && (
        <div className="absolute top-full left-0 mt-3 w-56 bg-white rounded-2xl border-2 border-[#F55600]/20 shadow-2xl z-50 py-2 animate-in fade-in zoom-in-95 duration-200 overflow-hidden">
          {options.map((opt, idx) => {
            // For platform options, show the real logo instead of a dot.
            // Non-platform filters (Sort / Time / Accounts) keep the dot.
            const platKey = { 'LinkedIn': 'linkedin', 'X': 'twitter', 'Facebook': 'facebook', 'Instagram': 'instagram', 'YouTube': 'youtube', 'TikTok': 'tiktok' }[opt];
            return (
            <button
              key={opt}
              onClick={() => {
                setter(opt);
                setActiveDropdown(null);
              }}
              className={`w-full text-left px-5 py-3 text-[13px] font-bold transition-all flex items-center gap-3 border-b border-slate-50 last:border-b-0 ${
                current === opt
                  ? 'bg-[#F55600]/5 text-[#F55600]'
                  : 'text-[#2B2926] hover:bg-slate-50'
              }`}
            >
              {platKey
                ? <span className="w-4 h-4 flex items-center justify-center shrink-0"><PlatformLogo platform={platKey} className="w-4 h-4" /></span>
                : <div className={`w-2 h-2 rounded-full transition-all ${current === opt ? 'bg-[#F55600]' : 'bg-slate-300'}`} />}
              <span>{opt}</span>
            </button>
            );
          })}
        </div>
      )}
    </div>
  );

  const getContentForPlatform = (content, platform = null) => {
    if (!content) return "No content";
    try {
      if (typeof content === 'string' && content.startsWith('{')) {
        const json = JSON.parse(content);
        if (platform) return json[platform] || json.default || Object.values(json)[0] || "No content";
        // If no platform specified, try finding any available platform content
        return json.linkedin || json.twitter || json.facebook || json.instagram || json.default || Object.values(json)[0] || "No content";
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
                Successfully <span className="text-[#F55600]">Posted</span>
              </h1>
              <p className="text-sm text-[#2B2926] font-medium">Track and manage all your published content</p>
            </div>
            <div className="flex items-center gap-3">
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
                onClick={syncMetricsAndRefresh}
                className="w-11 h-11 flex items-center justify-center bg-[#F55600] rounded-xl text-white shadow-lg shadow-orange-200/20 hover:shadow-xl hover:scale-105 transition-all border-2 border-[#F55600]/20 shrink-0"
                title="Sync metrics and refresh posts"
                disabled={syncingMetrics}
              >
                <RefreshCw className={`w-5 h-5 ${(loadingPosts || syncingMetrics) ? 'animate-spin' : ''}`} />
              </button>
            </div>
          </div>
        </div>

        {/* P-3: inline error banner instead of console-only / alert() */}
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

        {/* Filter Section */}
        <div className="grid grid-cols-2 lg:flex lg:flex-wrap gap-2 md:gap-4 mb-8 relative z-30">
          <FilterButton 
            label="Sort By" 
            current={sortBy} 
            options={['Newest First', 'Oldest First']} 
            setter={setSortBy} 
            name="sort" 
          />
          <FilterButton 
            label="All Platforms" 
            current={platformFilter} 
            options={['All Platforms', 'LinkedIn', 'X', 'Facebook', 'Instagram', 'YouTube', 'TikTok']}
            setter={setPlatformFilter} 
            name="platform" 
          />
          <FilterButton 
            label="All Time" 
            current={timeFilter} 
            options={['All Time', 'Today', 'Last 7 Days', 'Last 30 Days']} 
            setter={setTimeFilter} 
            name="time" 
          />
          <FilterButton 
            label="All Accounts" 
            current={accountFilter} 
            options={accountOptions} 
            setter={setAccountFilter} 
            name="account" 
          />
        </div>

        {/* Posts Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-8">
          {loadingPosts ? (
            <div className="col-span-full py-20 text-center flex flex-col items-center">
              <RefreshCw className="w-8 h-8 text-[#2B2926] animate-spin mb-4" />
              <p className="text-[#2B2926] font-medium">Loading your posts...</p>
            </div>
          ) : filteredPosts.length === 0 ? (
            <div className="col-span-full py-24 text-center bg-white rounded-2xl border border-[#2B2926]/30 shadow-sm">
              <div className="w-20 h-20 bg-white rounded-full flex items-center justify-center mx-auto mb-4 border-2 border-[#F55600]/20 shadow-sm">
                <Send className="w-8 h-8 text-[#2B2926]" />
              </div>
              <h4 className="text-lg font-bold text-[#2B2926] mb-2">No posts found</h4>
              <p className="text-[#2B2926] text-sm">Adjust your filters to see more posts.</p>
            </div>
          ) : (
            filteredPosts.map((post, index) => {
              const isOrange = index % 2 === 0;
              return (
                <div
                  key={post.id}
                  onClick={() => {
                    setSelectedPost(post);
                    // Seed the active tab from the first metric row so the
                    // per-account tab is highlighted correctly when the
                    // modal opens.
                    const firstMetric = (post.metrics && post.metrics[0]) || null;
                    setActivePreviewPlatform(firstMetric?.platform || post.platforms[0] || null);
                    setActivePreviewAccountId(firstMetric?.account_id || null);
                  }}
                  className={`bg-white rounded-2xl border-2 border-[#2B2926] shadow-sm hover:shadow-xl transition-all duration-300 overflow-hidden flex flex-col h-full group cursor-pointer hover:scale-[1.03] hover:z-10 relative hover:border-[#F55600]`}
                >
                  {/* Header — date pill + platform icons.
                      Compact date (month + day only) so the pill doesn't
                      overflow and overlap the platform icons on narrow cards.
                      formatInTimezone merges caller options OVER defaults
                      (year/hour/minute always included), so we format via
                      Intl directly here to exclude year/time. gap-2 + flex-wrap
                      keep the layout intact if the date ever wraps. */}
                  <div className={`px-2 py-1.5 border-b-2 bg-white ${
                    isOrange ? 'border-[#F55600]/10' : 'border-green-200'
                  } flex items-center justify-between gap-1 flex-wrap`}>
                    <div className={`flex items-center gap-1.5 text-[9px] font-semibold px-2 py-1 rounded-full uppercase tracking-tight border shadow-sm ${
                      isOrange
                        ? 'bg-[#F55600] text-white border-[#F55600]'
                        : 'bg-[#10B981] text-white border-[#10B981]'
                    }`}>
                      <Clock className="w-3 h-3 text-white" />
                      {(() => {
                        try {
                          return new Intl.DateTimeFormat('en-US', {
                            month: 'short', day: 'numeric',
                            timeZone: user?.timezone || 'UTC',
                          }).format(new Date(post.created_at));
                        } catch { return ''; }
                      })()}
                    </div>
                    <div className="flex -space-x-1 shrink-0">
                      {post.platforms.map((p, i) => (
                        <div key={p} className="w-5 h-5 rounded-full bg-white ring-1 ring-slate-200 flex items-center justify-center overflow-hidden">
                          <PlatformLogo platform={p} className="w-3.5 h-3.5" />
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Media preview — routes by post.media_type. Legacy
                      rows (media_type=null) fall through to image treatment
                      for backwards compat with anything published before
                      the column existed. */}
                  {post.image_url && (() => {
                    const mt = post.media_type ||
                      (/\.(mp4|mov|webm|m4v)(\?|$)/i.test(post.image_url) ? 'video'
                        : /\.(pdf|docx?|pptx?)(\?|$)/i.test(post.image_url) ? 'document'
                        : 'image');
                    if (mt === 'video') {
                      return (
                        <div className="h-32 overflow-hidden bg-[#2B2926] border-b border-[#2B2926]/30 relative">
                          <video
                            src={post.image_url}
                            muted
                            playsInline
                            preload="metadata"
                            className="w-full h-full object-contain"
                          />
                          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                            <div className="w-10 h-10 rounded-full bg-white/90 flex items-center justify-center shadow-lg">
                              <span className="text-[#F55600] text-lg">▶</span>
                            </div>
                          </div>
                          <span className="absolute top-2 left-2 bg-[#2B2926]/70 text-white text-[8px] font-semibold uppercase tracking-widest px-2 py-0.5 rounded">Video</span>
                        </div>
                      );
                    }
                    if (mt === 'document') {
                      // Prefer the slide-1 PNG so the carousel previews as
                      // a real <img> (matches what the user posted, no ugly
                      // UUID filename). Fall back to pdf.js rendering when
                      // no pre-baked thumbnail exists (manual PDF uploads
                      // + legacy Agent Post rows). Final fallback is the
                      // "PDF + filename" tile — only reached if the URL
                      // itself is somehow missing.
                      if (post.thumbnail_url) {
                        return (
                          <div className="h-32 overflow-hidden bg-slate-50 border-b border-[#2B2926]/30 relative">
                            <img
                              src={post.thumbnail_url}
                              alt="Carousel cover slide"
                              loading="lazy"
                              className="w-full h-full object-contain group-hover:scale-105 transition-transform duration-300"
                            />
                            <span className="absolute top-2 left-2 bg-[#F55600] text-white text-[8px] font-black uppercase tracking-widest px-1.5 py-0.5 rounded shadow">
                              PDF Carousel
                            </span>
                          </div>
                        );
                      }
                      if (post.image_url) {
                        return (
                          <div className="h-32 overflow-hidden bg-slate-50 border-b border-[#2B2926]/30 relative">
                            <PdfThumbnail
                              src={post.image_url}
                              page={1}
                              className="w-full h-full"
                              alt="Carousel cover slide"
                            />
                            <span className="absolute top-2 left-2 bg-[#F55600] text-white text-[8px] font-black uppercase tracking-widest px-1.5 py-0.5 rounded shadow z-10">
                              PDF Carousel
                            </span>
                          </div>
                        );
                      }
                      const fname = (post.image_url?.split('/').pop() || 'document.pdf').split('?')[0];
                      return (
                        <div className="h-32 flex items-center gap-3 p-4 bg-white border-b-2 border-[#2B2926]/30">
                          <div className="w-14 h-14 rounded-xl bg-[#F55600] text-white font-semibold flex items-center justify-center shadow-md border-2 border-[#F55600]/30">PDF</div>
                          <div className="flex-1 min-w-0">
                            <div className="text-xs font-semibold text-[#2B2926] truncate">{fname}</div>
                            <div className="text-[9px] font-bold text-[#2B2926] uppercase tracking-widest mt-0.5">Document · LinkedIn carousel</div>
                          </div>
                        </div>
                      );
                    }
                    return (
                      <div className="h-32 overflow-hidden bg-slate-50 border-b border-[#2B2926]/30">
                        <img
                          src={post.image_url}
                          alt=""
                          className="w-full h-full object-contain group-hover:scale-105 transition-transform duration-300"
                          onError={(e) => {
                            e.target.onerror = null;
                            e.target.src = "data:image/svg+xml;utf8,%3Csvg%20xmlns%3D'http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg'%20width%3D'150'%20height%3D'150'%20viewBox%3D'0%200%20150%20150'%3E%3Crect%20width%3D'150'%20height%3D'150'%20fill%3D'%23F5F1EB'%2F%3E%3Ctext%20x%3D'50%25'%20y%3D'50%25'%20font-family%3D'system-ui%2Csans-serif'%20font-size%3D'14'%20fill%3D'%23999'%20text-anchor%3D'middle'%20dy%3D'0.3em'%3ENo%20image%3C%2Ftext%3E%3C%2Fsvg%3E";
                          }}
                        />
                      </div>
                    );
                  })()}

                  {/* Content */}
                  <div className="flex-1 p-4">
                    <p className="text-xs text-[#2B2926] leading-relaxed font-medium line-clamp-3">
                      {getContentForPlatform(post.content)}
                    </p>
                  </div>

                  {/* P-8: explicit "metrics not available yet" state so posts
                      with no platform_posts rows (orphaned) don't look the
                      same as posts that got zero engagement. */}
                  {(!post.metrics || post.metrics.length === 0) && (
                    <div className="px-4 py-2.5 bg-white border-t-2 border-[#2B2926]/30 text-[10px] font-bold text-[#2B2926] italic">
                      Metrics not available yet — will populate on next analytics sync.
                    </div>
                  )}

                  {/* Metrics — one chip per (platform × account). The
                      backend now emits per-account rows, so LinkedIn:
                      personal and LinkedIn:page each get their own chip
                      in the same grid we already use to show separate
                      Facebook / Instagram / X chips. Account name is
                      shown inline when the post landed on more than one
                      account for the same platform (otherwise the icon
                      alone is enough context). */}
                  {/* Metrics — one chip per (platform × account). Account
                      name is intentionally NOT shown on the card chip
                      (too little room, always truncates ugly). Hover shows
                      it as a tooltip; full name lives in the modal tab. */}
                  {post.metrics && post.metrics.length > 0 && (
                    <div className="px-4 py-3 bg-white border-t-2 border-[#2B2926]/30 grid grid-cols-2 gap-2 text-[9px] min-h-[84px] content-start">
                      {post.metrics.map((m, idx) => (
                        <div
                          key={`${m.platform}-${m.account_id || idx}`}
                          className={`flex items-center gap-2 py-1 px-2.5 bg-white rounded-lg border-2 shadow-sm hover:border-[#F55600]/30 transition-colors min-w-0 ${
                            m.is_personal ? 'border-[#F55600]/40' : 'border-[#2B2926]/30'
                          }`}
                          title={m.account_name ? `${m.account_name}${m.is_personal ? ' (Personal)' : ''}` : undefined}
                        >
                          <div className="flex items-center shrink-0">
                            <PlatformLogo platform={m.platform} className="w-3.5 h-3.5" />
                          </div>
                          <div className="h-4 w-[1px] bg-slate-200 shrink-0" />
                          <div className="flex items-center gap-1.5 min-w-0">
                            <div className="flex items-center gap-1">
                              <Heart className="w-3 h-3 text-red-500 fill-red-500" />
                              <span className="font-semibold text-[#2B2926] text-[10px]">{m.likes}</span>
                            </div>
                            <div className="flex items-center gap-1">
                              <MessageSquare className="w-3 h-3 text-blue-500 fill-blue-500" />
                              <span className="font-semibold text-[#2B2926] text-[10px]">{m.comments}</span>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Footer */}
                  <div className={`px-4 py-3 border-t-2 bg-white flex items-center justify-between ${
                    isOrange ? 'border-[#F55600]/10' : 'border-green-200'
                  }`}>
                    <div className="flex items-center gap-1.5 px-2.5 py-1 bg-[#10B981] text-white text-[9px] font-bold uppercase tracking-widest rounded-full border border-[#10B981] shadow-sm">
                      <CheckCircle2 className="w-3 h-3 text-white" />
                      Posted
                    </div>
                    <div className="flex items-center gap-2">
                      <button 
                        onClick={(e) => { e.stopPropagation(); onRepost(post); }}
                        className="flex items-center gap-1.5 px-3 py-1.5 bg-[#2B2926] text-white text-[9px] font-bold uppercase tracking-widest rounded-lg border border-[#2B2926] hover:bg-slate-900 transition-all shadow-sm hover:shadow-md"
                        title="Repost this content"
                      >
                        <RefreshCw className="w-3 h-3" />
                        Repost
                      </button>
                      <button 
                        onClick={(e) => { e.stopPropagation(); handleDelete(post.id); }}
                        className="p-1.5 text-[#2B2926] hover:text-red-500 hover:bg-red-50 rounded-lg transition-all"
                        title="Delete"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Preview Modal — rendered via a Portal to document.body so its
            `position: fixed` resolves against the real viewport. Without
            the portal, a transformed ancestor (framer-motion / Tailwind
            `animate-in`) makes `fixed` behave like `absolute`, which is
            why clicking a post far down the page opened the modal up at
            the top instead of centred in view. */}
        {typeof document !== 'undefined' && createPortal(
        <AnimatePresence>
          {selectedPost && (
            <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                onClick={() => setSelectedPost(null)}
                className="absolute inset-0" 
              />
              <motion.div
                initial={{ opacity: 0, scale: 0.95, y: 20 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.95, y: 20 }}
                className="bg-white w-full max-w-5xl rounded-2xl overflow-hidden shadow-2xl relative z-10 flex flex-col md:flex-row max-h-[80vh] border border-[#2B2926]/30"
              >
                {/* Left: Image Preview — widened to 2/3 with minimal
                    padding so the visual dominates the modal, similar
                    to a native LinkedIn / Facebook feed preview. */}
                <div className="md:w-2/3 bg-slate-50 flex items-center justify-center p-2 md:p-4 relative overflow-hidden ring-1 ring-inset ring-slate-100">
                  <div className="absolute inset-0 opacity-10">
                    <div className="absolute top-0 left-0 w-64 h-64 bg-orange-200 rounded-full blur-3xl -ml-32 -mt-32" />
                    <div className="absolute bottom-0 right-0 w-64 h-64 bg-blue-200 rounded-full blur-3xl -mr-32 -mb-32" />
                  </div>
                  {selectedPost.image_url ? (() => {
                    // Same media_type routing as the card grid — but render
                    // a larger preview appropriate to the modal (playable
                    // video, clickable PDF link).
                    const mt = selectedPost.media_type ||
                      (/\.(mp4|mov|webm|m4v)(\?|$)/i.test(selectedPost.image_url) ? 'video'
                        : /\.(pdf|docx?|pptx?)(\?|$)/i.test(selectedPost.image_url) ? 'document'
                        : 'image');
                    if (mt === 'video') {
                      // YouTube posts: the stored file is the uploaded clip
                      // (often not directly streamable here), so embed the
                      // YouTube player via the video id instead of <video>.
                      const ytId = (selectedPost.platforms || []).includes('youtube') ? selectedPost.youtube_video_id : null;
                      if (ytId) {
                        return (
                          <div className="w-full max-w-2xl aspect-video">
                            <iframe
                              src={`https://www.youtube.com/embed/${ytId}`}
                              title="YouTube video"
                              allow="accelerometer; autoplay; encrypted-media; picture-in-picture; fullscreen"
                              allowFullScreen
                              className="w-full h-full rounded-xl shadow-xl border-2 border-white bg-black"
                            />
                          </div>
                        );
                      }
                      return (
                        <div className="relative max-w-full max-h-full w-full">
                          <video
                            src={selectedPost.image_url}
                            controls
                            className="w-full h-auto rounded-xl shadow-xl border-2 border-white bg-[#2B2926] max-h-[72vh]"
                          />
                        </div>
                      );
                    }
                    if (mt === 'document') {
                      const fname = (selectedPost.image_url?.split('/').pop() || 'document.pdf').split('?')[0];
                      return (
                        <div className="flex flex-col items-center gap-4 p-6 bg-white rounded-2xl border-4 border-white shadow-2xl max-w-2xl max-h-[80vh]">
                          {selectedPost.thumbnail_url ? (
                            <img
                              src={selectedPost.thumbnail_url}
                              alt="Carousel cover slide"
                              className="max-w-full max-h-[60vh] object-contain rounded-xl shadow-md"
                            />
                          ) : selectedPost.image_url ? (
                            // pdf.js fallback so manual PDF uploads and
                            // legacy rows still show slide 1, not the
                            // ugly "PDF + UUID filename" tile.
                            <div className="w-full max-w-lg h-[60vh] rounded-xl shadow-md overflow-hidden bg-white flex items-center justify-center">
                              <PdfThumbnail
                                src={selectedPost.image_url}
                                page={1}
                                className="w-full h-full"
                                alt="Carousel cover slide"
                              />
                            </div>
                          ) : (
                            <>
                              <div className="w-20 h-20 rounded-2xl bg-[#F55600] text-white font-semibold text-lg flex items-center justify-center shadow-lg">PDF</div>
                              <div className="text-center">
                                <div className="text-sm font-semibold text-[#2B2926] break-all">{fname}</div>
                                <div className="text-[10px] font-bold text-[#2B2926] uppercase tracking-widest mt-1">LinkedIn Document Carousel</div>
                              </div>
                            </>
                          )}
                          <a
                            href={selectedPost.image_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="px-5 py-2.5 bg-[#F55600] text-white text-[10px] font-semibold uppercase tracking-widest rounded-xl hover:bg-orange-600 transition-all shadow-md"
                          >
                            Open Document
                          </a>
                        </div>
                      );
                    }
                    return (
                      <div className="relative group/img w-full max-h-full flex items-center justify-center">
                        <img
                          src={selectedPost.image_url}
                          className="w-full h-auto rounded-xl shadow-xl border-2 border-white object-contain max-h-[72vh]"
                          alt="Post creation"
                        />
                        <div className="absolute inset-0 rounded-2xl shadow-inner pointer-events-none" />
                      </div>
                    );
                  })() : (
                    <div className="flex flex-col items-center gap-4 text-[#2B2926]">
                      <ImageIcon className="w-16 h-16 opacity-20" />
                      <p className="text-[10px] font-semibold uppercase tracking-widest">No visual asset attached</p>
                    </div>
                  )}
                </div>

                {/* Right: Content & Tabs — narrowed to 1/3 so the
                    image dominates the modal layout. */}
                <div className="md:w-1/3 flex flex-col bg-white overflow-hidden">
                  {/* Modal Header */}
                  <div className="px-5 pt-5 pb-4 border-b border-slate-50 flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <div className="w-9 h-9 rounded-xl bg-white flex items-center justify-center border-2 border-[#F55600]/30 shadow-sm">
                        <Sparkles className="w-4 h-4 text-[#F55600]" />
                      </div>
                      <div>
                        <h4 className="font-semibold text-[#2B2926] tracking-tight leading-none mb-1 text-base">Post Preview</h4>
                        <p className="text-[10px] font-bold text-[#2B2926] uppercase tracking-widest">Review platform content and status</p>
                      </div>
                    </div>
                    <button 
                      onClick={() => setSelectedPost(null)}
                      className="p-2 hover:bg-slate-50 rounded-2xl text-[#2B2926] transition-all hover:text-[#2B2926] border border-transparent hover:border-[#2B2926]/30"
                    >
                      <X className="w-5 h-5" strokeWidth={3} />
                    </button>
                  </div>

                  {/* Platform Tabs — one tab per (platform × account).
                      When a post landed on 2 LinkedIn accounts (personal +
                      page) we render 2 LinkedIn tabs, each labeled with
                      the account name. Single-account platforms render a
                      plain platform tab. Active state is identified by
                      the (platform, account_id) pair so both LinkedIn
                      tabs can toggle independently. */}
                  {selectedPost.metrics && selectedPost.metrics.length > 0 && (() => {
                    // Same "only label with account name when >1 account
                    // for that platform" heuristic as the grid chip — keeps
                    // single-account tabs uncluttered.
                    const platformCounts = selectedPost.metrics.reduce((acc, m) => {
                      acc[m.platform] = (acc[m.platform] || 0) + 1;
                      return acc;
                    }, {});
                    return (
                      <div className="px-5 py-2.5 bg-slate-50/50 flex flex-wrap gap-2 border-b border-[#2B2926]/30">
                        {selectedPost.metrics.map((m, idx) => {
                          const isActive = activePreviewPlatform === m.platform
                            && (activePreviewAccountId || null) === (m.account_id || null);
                          const showAccountName = platformCounts[m.platform] > 1 && m.account_name;
                          const platLabel = m.platform === 'twitter'
                            ? 'X'
                            : (m.platform.charAt(0).toUpperCase() + m.platform.slice(1));
                          return (
                            <button
                              key={`${m.platform}-${m.account_id || idx}`}
                              onClick={() => {
                                setActivePreviewPlatform(m.platform);
                                setActivePreviewAccountId(m.account_id || null);
                              }}
                              className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-[11px] font-semibold tracking-wide transition-all border ${
                                isActive
                                  ? 'bg-white text-[#F55600] border-orange-100 shadow-sm'
                                  : 'text-[#2B2926] hover:text-[#2B2926] border-transparent hover:bg-white'
                              }`}
                            >
                              <PlatformLogo platform={m.platform} className="w-3.5 h-3.5" />
                              {platLabel}
                              {showAccountName && (
                                <span className={`ml-1 text-[9px] font-semibold px-1.5 py-0.5 rounded ${
                                  m.is_personal
                                    ? 'bg-[#F55600]/10 text-[#F55600]'
                                    : 'bg-slate-100 text-[#2B2926]'
                                }`}>
                                  {m.account_name}
                                </span>
                              )}
                            </button>
                          );
                        })}
                      </div>
                    );
                  })()}

                  {/* Platform Specific Content */}
                  <div className="px-8 py-6 flex-grow overflow-y-auto">
                    <div className="flex items-center gap-2 text-[#2B2926] mb-3">
                      <Type className="w-4 h-4 text-[#F55600]" />
                      <span className="text-[10px] font-semibold uppercase tracking-widest">Formatted Content</span>
                    </div>
                    <div className="bg-slate-50/50 p-6 rounded-2xl border-2 border-[#2B2926]/30 mb-6 whitespace-pre-wrap text-[#2B2926] font-medium leading-relaxed text-sm">
                      {getContentForPlatform(selectedPost.content, activePreviewPlatform)}
                    </div>

                    {/* Platform Metrics in Modal — driven by the active
                        (platform, account_id) pair so each tab shows its
                        OWN account's numbers, not the aggregate. */}
                    {(() => {
                      const m = (selectedPost.metrics || []).find(
                        x => x.platform === activePreviewPlatform
                          && (activePreviewAccountId || null) === (x.account_id || null)
                      );
                      if (!m) return null;
                      return (
                        <div className="grid grid-cols-2 gap-4">
                          {[
                            { label: 'Likes', value: m.likes || 0, icon: <Heart className="w-4 h-4 text-red-500 fill-red-500" /> },
                            { label: 'Comments', value: m.comments || 0, icon: <MessageSquare className="w-4 h-4 text-blue-500 fill-blue-500" /> }
                          ].map((stat, i) => (
                            <div key={i} className="bg-white p-5 rounded-2xl border-2 border-[#2B2926]/30 shadow-sm flex items-center justify-between group/stat hover:border-orange-100 transition-colors">
                              <div className="flex items-center gap-3">
                                <div className="w-9 h-9 rounded-xl bg-slate-50 flex items-center justify-center border border-slate-50 group-hover/stat:bg-orange-50 transition-colors">
                                  {stat.icon}
                                </div>
                                <div className="flex flex-col">
                                  <span className="text-sm font-semibold text-[#2B2926] leading-none mb-0.5">{stat.value.toLocaleString()}</span>
                                  <span className="text-[8px] font-semibold uppercase text-[#2B2926] tracking-widest">{stat.label}</span>
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                      );
                    })()}
                  </div>

                  {/* Modal Footer */}
                  <div className="p-8 pt-4 border-t border-slate-50 bg-slate-50/30 flex items-center justify-between">
                    <div className="flex items-center gap-2 text-[#2B2926] text-[10px] font-bold">
                      <Clock className="w-3.5 h-3.5" />
                      Published on {formatInTimezone(selectedPost.created_at, user?.timezone, { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' })}
                    </div>
                  </div>
                </div>
              </motion.div>
            </div>
          )}
        </AnimatePresence>,
        document.body
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

export default Published;
