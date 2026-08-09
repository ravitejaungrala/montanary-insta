import React, { useState, useEffect, useMemo } from 'react';
import { useNotification } from '../context/NotificationContext';
import { ChevronLeft, ChevronRight, Calendar as CalendarIcon, Facebook, Linkedin, Instagram, Type, Clock, Image as ImageIcon, Plus, Brain, Send, Check, Zap, Globe, Settings, X, FileEdit, Trash2, ChevronDown } from 'lucide-react';
import XIcon from '../components/icons/XIcon';
import { datetimeLocalInputToIso } from '../utils/postContent';
import TeamMembersFilter from '../components/TeamMembersFilter';
import { isReadOnly } from '../lib/permissions';
import { isDocumentMedia, DocCard } from '../components/PostMedia';

/**
 * ConnAvatar — account avatar that gracefully falls back to the user's
 * initial letter when there's no profile picture URL OR the image fails to
 * load (a broken image src previously rendered an ugly broken-image icon).
 */
const ConnAvatar = ({ url, name }) => {
  const [errored, setErrored] = useState(false);
  const letter = (name?.charAt(0) || '?').toUpperCase();
  if (url && !errored) {
    return (
      <img
        src={url}
        onError={() => setErrored(true)}
        className="w-full h-full object-cover"
        alt=""
      />
    );
  }
  return (
    <div className="w-full h-full flex items-center justify-center text-[13px] font-semibold text-white bg-[#F55600] uppercase">
      {letter}
    </div>
  );
};

// Module-level stale-while-revalidate cache for Calendar posts.
// Survives tab switches (in-memory) and full reloads (localStorage).
// Keyed by member-filter string so each filter combo has its own slot.
const _CAL_LS_KEY = 'pipelyt_calendar_cache_v1';
const _CAL_MAX_AGE_MS = 5 * 60 * 1000; // 5 min
const _calCache = new Map(); // key -> { rows, fetchedAt }
const _calInflight = new Map(); // key -> Promise (de-dup)
(function _hydrateCalendarCache() {
  try {
    const raw = localStorage.getItem(_CAL_LS_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return;
    Object.entries(parsed).forEach(([k, v]) => {
      if (v && Array.isArray(v.rows) && typeof v.fetchedAt === 'number'
          && Date.now() - v.fetchedAt < _CAL_MAX_AGE_MS * 12) {
        _calCache.set(k, v);
      }
    });
  } catch { /* corrupted — ignore */ }
})();
const _persistCalCache = () => {
  try {
    const obj = {};
    for (const [k, v] of _calCache.entries()) obj[k] = v;
    localStorage.setItem(_CAL_LS_KEY, JSON.stringify(obj));
  } catch { /* quota — ignore */ }
};

const Calendar = ({ authAxios, posts: globalPosts, setPosts: setGlobalPosts, user }) => {
  // Admin-only team-members filter. Empty = full team scope.
  const [selectedMemberIds, setSelectedMemberIds] = useState([]);
  const [currentDate, setCurrentDate] = useState(new Date());
  const [publishedPosts, setPublishedPosts] = useState(globalPosts || []);
  // Seed from cache so repeat visits paint instantly without a spinner.
  const _calCacheKey = selectedMemberIds.length > 0 ? selectedMemberIds.join(',') : 'all';
  const _calCachedRows = _calCache.get(_calCacheKey)?.rows;
  const [loadingPosts, setLoadingPosts] = useState(
    !globalPosts?.length && !_calCachedRows?.length
  );
  const [viewMode, setViewMode] = useState('month'); // 'month', 'week'
  const [selectedDayPosts, setSelectedDayPosts] = useState(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedPostId, setSelectedPostId] = useState(null);
  const [isScheduleModalOpen, setIsScheduleModalOpen] = useState(false);
  const [selectedDate, setSelectedDate] = useState(null);
  const [scheduledTime, setScheduledTime] = useState("09:00");

  // Scheduling States
  const [postType, setPostType] = useState('standard'); // 'standard', 'agentic'
  const [selectedAccounts, setSelectedAccounts] = useState({}); // {platform: [id, ...]}
  const [campaignBrief, setCampaignBrief] = useState('');
  const [scheduling, setScheduling] = useState(false);
  const [savingDraft, setSavingDraft] = useState(false);
  const [availableAccounts, setAvailableAccounts] = useState([]);
  const [unsupportedNotice, setUnsupportedNotice] = useState('');
  const { toast, confirm } = useNotification();

  const VIDEO_ONLY_PLATFORMS = ['pinterest', 'youtube', 'tiktok'];

  const fetchAccounts = async () => {
    try {
      const res = await authAxios.get('/connections');
      // Flatten the {linkedin: [], facebook: [], ...} object into a single array
      const allAccounts = Object.values(res.data).flat();
      setAvailableAccounts(allAccounts);
    } catch (e) {
      console.error("Failed to fetch connections:", e);
    }
  };

  const fetchPosts = async () => {
    if (!authAxios) return;
    const cacheKey = selectedMemberIds.length > 0 ? selectedMemberIds.join(',') : 'all';
    const cached = _calCache.get(cacheKey);
    if (cached?.rows?.length) {
      setPublishedPosts(cached.rows);
      if (setGlobalPosts) setGlobalPosts(cached.rows);
    } else {
      setLoadingPosts(true);
    }
    if (_calInflight.has(cacheKey)) {
      try { await _calInflight.get(cacheKey); } catch {}
      return;
    }
    const work = (async () => {
      try {
        const memberQs = selectedMemberIds.length > 0
          ? `?member_user_ids=${selectedMemberIds.join(',')}`
          : '';
        const [pubRes, schedRes] = await Promise.all([
          authAxios.get(`/posts${memberQs}`),
          authAxios.get(`/scheduled${memberQs}`)
        ]);
        const combined = [
          ...(pubRes.data || []).map(p => ({ ...p, isScheduled: false, platforms: p.platforms })),
          ...(schedRes.data || []).map(p => ({
            ...p,
            isScheduled: true,
            created_at: p.scheduled_for,
            platforms: Object.entries(p.targets || {})
              .filter(([, ids]) => ids.length > 0)
              .map(([plat]) => plat)
          }))
        ];
        setPublishedPosts(combined);
        if (setGlobalPosts) setGlobalPosts(combined);
        _calCache.set(cacheKey, { rows: combined, fetchedAt: Date.now() });
        _persistCalCache();
      } catch (e) {
        console.error("Failed to fetch posts", e);
      } finally {
        setLoadingPosts(false);
        _calInflight.delete(cacheKey);
      }
    })();
    _calInflight.set(cacheKey, work);
    await work;
  };

  const handleDeletePost = async (post) => {
    const ok = await confirm({
      title: post.isScheduled ? 'Cancel Scheduled Post' : 'Remove Published Post',
      message: `Are you sure you want to ${post.isScheduled ? 'cancel this scheduled' : 'remove this published'} post?`,
      confirmText: post.isScheduled ? 'Cancel Now' : 'Remove Now'
    });
    if (!ok) return;

    try {
      const endpoint = post.isScheduled ? `/scheduled/${post.id}` : `/posts/${post.id}`;
      await authAxios.delete(endpoint);

      // Update local state
      const updatedPosts = publishedPosts.filter(p => !(p.id === post.id && p.isScheduled === post.isScheduled));
      setPublishedPosts(updatedPosts);
      if (setGlobalPosts) setGlobalPosts(updatedPosts);

      // Update modal state if open
      if (selectedDayPosts) {
        const remaining = selectedDayPosts.posts.filter(p => !(p.id === post.id && p.isScheduled === post.isScheduled));
        if (remaining.length === 0) {
          setIsModalOpen(false);
          setSelectedDayPosts(null);
        } else {
          setSelectedDayPosts({ ...selectedDayPosts, posts: remaining });
        }
      }
    } catch (e) {
      console.error("Failed to delete post", e);
      alert("Failed to delete post. Please try again.");
    }
  };

  useEffect(() => {
    fetchPosts();
    fetchAccounts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedMemberIds]);

  const getDaysInMonth = (year, month) => new Date(year, month + 1, 0).getDate();
  const getFirstDayOfMonth = (year, month) => new Date(year, month, 1).getDay();

  const year = currentDate.getFullYear();
  const month = currentDate.getMonth();

  const daysInMonth = getDaysInMonth(year, month);
  const firstDay = getFirstDayOfMonth(year, month);
  const prevMonthDays = getDaysInMonth(year, month - 1);

  // Month View Days
  const calendarDays = [];
  for (let i = firstDay - 1; i >= 0; i--) {
    const d = new Date(year, month - 1, prevMonthDays - i);
    calendarDays.push({ date: d, isCurrentMonth: false });
  }
  for (let i = 1; i <= daysInMonth; i++) {
    const d = new Date(year, month, i);
    calendarDays.push({ date: d, isCurrentMonth: true });
  }
  const totalSlots = calendarDays.length > 35 ? 42 : 35;
  for (let i = 1; calendarDays.length < totalSlots; i++) {
    const d = new Date(year, month + 1, i);
    calendarDays.push({ date: d, isCurrentMonth: false });
  }

  // Rolling Week View Days - Normalized to start on Monday
  const rollingWeekDays = [];
  const weekStart = new Date(currentDate);
  const dayOfWeek = weekStart.getDay(); // 0 = Sun, 1 = Mon ...
  // Find previous Monday: if Sun(0), go back 6. If Mon(1), go back 0. If Tue(2), go back 1.
  const diffToMonday = dayOfWeek === 0 ? -6 : 1 - dayOfWeek;
  weekStart.setDate(weekStart.getDate() + diffToMonday);
  weekStart.setHours(0, 0, 0, 0); // Normalize to start of day

  for (let i = 0; i < 7; i++) {
    const d = new Date(weekStart);
    d.setDate(weekStart.getDate() + i);
    rollingWeekDays.push({ date: d, isCurrentMonth: d.getMonth() === month });
  }

  // Month Table grouping
  const weeks = [];
  for (let i = 0; i < calendarDays.length; i += 7) {
    weeks.push(calendarDays.slice(i, i + 7));
  }

  // Group posts by date
  const postsByDate = useMemo(() => {
    const map = {};
    publishedPosts.forEach(post => {
      if (!post.created_at) return;
      const d = new Date(post.created_at);
      const dateKey = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
      if (!map[dateKey]) map[dateKey] = [];
      map[dateKey].push(post);
    });
    return map;
  }, [publishedPosts]);

  const monthNames = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

  const handlePrev = () => {
    const nextDate = new Date(currentDate);
    if (viewMode === 'month') {
      nextDate.setMonth(currentDate.getMonth() - 1);
      nextDate.setDate(1);
    } else {
      nextDate.setDate(currentDate.getDate() - 7);
    }
    setCurrentDate(nextDate);
  };

  const handleNext = () => {
    const nextDate = new Date(currentDate);
    if (viewMode === 'month') {
      nextDate.setMonth(currentDate.getMonth() + 1);
      nextDate.setDate(1);
    } else {
      nextDate.setDate(currentDate.getDate() + 7);
    }
    setCurrentDate(nextDate);
  };

  const getPlatformIcon = (p, className) => {
    switch (p) {
      case 'linkedin': return <img src="/linkedlin.jpg" className={className + " object-contain"} alt="LinkedIn" />;
      case 'twitter': return <XIcon className={className} />;
      case 'facebook': return <img src="/facebook.png" className={className + " object-contain"} alt="Facebook" />;
      case 'instagram': return <img src="/instagram.jpg" className={className + " object-contain"} alt="Instagram" />;
      case 'reddit': return <img src="/reddit-icon.webp" className={className} alt="Reddit" />;
      case 'youtube': return <img src="/youtube-icon.png" className={className} alt="YouTube" />;
      case 'tiktok': return (
        <svg className={className} viewBox="0 0 24 24" fill="#010101" aria-label="TikTok">
          <path d="M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.08-.14 1.62.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z" />
        </svg>
      );
      case 'pinterest': return <img src="/pinterest-logo.png" className={className} alt="Pinterest" />;
      case 'google': return (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
          <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
          <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z" fill="#FBBC05"/>
          <path d="M12 5.38c1.62 0 3.06.56 4.21 1.66l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
        </svg>
      );
      case 'canva': return <img src="/canava-icon.webp" className={className} alt="Canva" />;
      default: return null;
    }
  };

  const today = new Date();
  const isToday = (d) => {
    return d.getDate() === today.getDate() &&
      d.getMonth() === today.getMonth() &&
      d.getFullYear() === today.getFullYear();
  };

  const getPostMetadata = (post) => {
    const brief = post.campaign_brief || '';
    const content = post.content || '';

    // Default values
    let label = null;
    let theme = 'rose'; // Default pinkish theme

    if (post.post_type === 'agentic') {
      const topicLine = brief.split('\n').find(l => l.includes('SPECIFIC POST TOPIC:'));
      if (topicLine) {
        label = topicLine.replace('SPECIFIC POST TOPIC: ', '').trim();
      } else {
        label = 'Autonomous';
      }
      theme = 'emerald';
    }

    // Heuristics for categories based on content/brief keywords
    const lowerBrief = brief.toLowerCase();
    const lowerContent = content.toLowerCase();

    if (lowerBrief.includes('educational') || lowerContent.includes('learn') || lowerContent.includes('how to')) {
      label = 'Educational';
      theme = 'amber';
    } else if (lowerBrief.includes('blog') || lowerBrief.includes('article') || lowerContent.includes('read more')) {
      label = 'Blog Posts';
      theme = 'emerald';
    } else if (lowerBrief.includes('product') || lowerContent.includes('launch')) {
      label = 'Product';
      theme = 'blue';
    }

    // Color mapping
    const themes = {
      rose: {
        bg: 'bg-white',
        border: 'border-rose-200',
        text: 'text-rose-950',
        headerBg: 'bg-rose-50',
        accent: 'text-rose-600'
      },
      emerald: {
        bg: 'bg-white',
        border: 'border-emerald-200',
        text: 'text-[#10B981]',
        headerBg: 'bg-emerald-50',
        accent: 'text-[#10B981]'
      },
      amber: {
        bg: 'bg-white',
        border: 'border-amber-200',
        text: 'text-amber-950',
        headerBg: 'bg-amber-100',
        accent: 'text-amber-800'
      },
      blue: {
        bg: 'bg-white',
        border: 'border-blue-200',
        text: 'text-blue-950',
        headerBg: 'bg-blue-50',
        accent: 'text-blue-700'
      }
    };

    return { label, ...themes[theme] };
  };

  const WeeklyPostCard = ({ post, onClick }) => {
    const meta = getPostMetadata(post);
    const platforms = post.platforms || [];
    const timeStr = new Date(post.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    // Content snippet logic: extract from JSON platforms, or fallback to brief topic
    let snippet = post.content;
    if (typeof post.content === 'string' && post.content.trim().startsWith('{')) {
      try {
        const json = JSON.parse(post.content);
        // Try all platforms or default
        snippet = json.linkedin || json.twitter || json.instagram || json.facebook || json.default || Object.values(json)[0];
      } catch (e) { snippet = post.content; }
    }

    // Fallback: If snippet is empty, "{}" or null, try getting topic from brief
    if (!snippet || snippet === "{}" || (typeof snippet === 'string' && snippet.trim() === "")) {
      const topicLine = (post.campaign_brief || '').split('\n').find(l => l.includes('SPECIFIC POST TOPIC:'));
      if (topicLine) {
        snippet = topicLine.replace('SPECIFIC POST TOPIC: ', '').trim();
      } else {
        snippet = post.post_type === 'agentic' ? 'Agent drafting content...' : 'Click to add content';
      }
    }

    // Trim snippet for display
    const maxLen = 140; // Slightly more space for better context
    const trimmedSnippet = (snippet || '').length > maxLen ? snippet.substring(0, maxLen) + '...' : snippet;

    return (
      <div
        onClick={(e) => {
          e.stopPropagation();
          onClick(post);
        }}
        className={`w-full ${meta.bg} ${meta.border} border rounded-xl flex flex-col overflow-hidden hover:shadow-lg transition-all mb-4 cursor-pointer group animate-in fade-in zoom-in-95 duration-300`}
      >
        {/* Header */}
        <div className={`px-2.5 py-1.5 flex items-center justify-between ${meta.headerBg}`}>
          <div className="flex items-center gap-1.5">
            <div className="w-4 h-4 rounded-full bg-white flex items-center justify-center shadow-sm">
              {getPlatformIcon(platforms[0], "w-2.5 h-2.5 " + (
                platforms[0] === 'linkedin' ? 'text-[#0077b5]' :
                  platforms[0] === 'twitter' ? 'text-[#2B2926]' :
                    platforms[0] === 'facebook' ? 'text-[#1877F2]' : 'text-[#E4405F]'
              ))}
            </div>
          </div>
          <span className={`text-[10px] font-semibold ${meta.text}`}>{timeStr}</span>
        </div>

        <div className="p-2.5 bg-white border-t border-[#2B2926]/30 flex-1 flex flex-col gap-1.5">
          <div className="flex items-center justify-between opacity-60">
            <ImageIcon className="w-2.5 h-2.5 text-[#2B2926]" />
            <Globe className="w-2.5 h-2.5 text-[#2B2926]" />
          </div>

          <p className="text-[11px] items-center leading-relaxed text-[#2B2926] font-semibold line-clamp-3 md:line-clamp-4">
            {trimmedSnippet}
          </p>

          {post.image_url && (
            isDocumentMedia(post) ? (
              <div className="mt-0.5 rounded-lg overflow-hidden border border-[#2B2926]/30 bg-white shadow-sm flex items-center gap-2 px-2 py-2">
                <span className="w-8 h-8 rounded-md bg-[#F55600] text-white text-[8px] font-black flex items-center justify-center shrink-0">PDF</span>
                <span className="text-[10px] font-bold text-[#2B2926] truncate">Document carousel</span>
              </div>
            ) : (
              <div className="mt-0.5 rounded-lg overflow-hidden border border-[#2B2926]/30 bg-white shadow-sm">
                <img src={post.image_url} alt="" className="w-full h-24 object-cover" />
              </div>
            )
          )}
        </div>
      </div>
    );
  };

  const renderDayCell = (dayObj, isWeekView = false) => {
    const dateKey = `${dayObj.date.getFullYear()}-${dayObj.date.getMonth()}-${dayObj.date.getDate()}`;
    const dayPosts = postsByDate[dateKey] || [];
    const _isToday = isToday(dayObj.date);

    if (isWeekView) {
      return (
        <div
          key={dayObj.date.toISOString()}
          className={`flex flex-col h-full border-r-2 border-[#2B2926]/30 last:border-r-0 bg-white`}
        >
          {/* Header for Week View Day */}
          <div className="p-3 border-b border-[#2B2926]/30 flex items-center justify-between sticky top-0 bg-inherit z-10">
            <div className="flex flex-col">
              <span className={`text-[10px] font-semibold uppercase tracking-widest ${_isToday ? 'text-[#F55600]' : 'text-[#2B2926]'}`}>
                {dayNames[dayObj.date.getDay()]} {dayObj.date.getDate()}
              </span>
              {_isToday && <span className="text-[8px] font-semibold uppercase tracking-tighter text-[#F55600]">Today</span>}
            </div>
          </div>

          <div className="flex-1 p-3 overflow-y-auto max-h-[800px] custom-scrollbar bg-white">
            {dayPosts.map((post) => (
              <WeeklyPostCard
                key={post.id}
                post={post}
                onClick={(p) => {
                  setSelectedDayPosts({ date: dayObj.date, posts: dayPosts });
                  setSelectedPostId(p.id);
                  setIsModalOpen(true);
                }}
              />
            ))}
            
            {/* Show New Post button ONLY for current or future dates */}
            {!isReadOnly(user) && (dayObj.date.getTime() >= new Date().setHours(0,0,0,0)) && (
              <button
                onClick={() => {
                  setSelectedDate(dayObj.date);
                  setIsScheduleModalOpen(true);
                }}
                className="w-full py-2.5 bg-[#F55600] text-white text-[10px] font-semibold rounded-xl uppercase tracking-widest hover:bg-[#e85a20] transition-all shadow-md mt-1 flex items-center justify-center gap-2"
              >
                New Post
              </button>
            )}
          </div>
        </div>
      );
    }

    return (
      <div
        key={dayObj.date.toISOString()}
        className={`p-1 border-r-2 border-b-2 border-[#2B2926]/30 last:border-r-0 flex flex-col transition-all duration-300 relative cursor-pointer group/cell calendar-day-cell min-h-[56px] xl:min-h-[68px] 2xl:min-h-[78px] ${_isToday ? 'bg-white ring-2 ring-inset ring-orange-400 z-10 shadow-sm' : (!dayObj.isCurrentMonth ? 'bg-white' : 'bg-white')
          } hover:bg-white hover:z-20 hover:shadow-[0_0_20px_rgba(245,86,0,0.1)]`}
        onClick={(e) => {
          if (dayPosts.length > 0) {
            setSelectedDayPosts({ date: dayObj.date, posts: dayPosts });
            setSelectedPostId(null); // Show all posts if clicked from month cell
            setIsModalOpen(true);
          }
        }}
      >
        {/* Plus Icon on Hover */}
        {!isReadOnly(user) && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              setSelectedDate(dayObj.date);
              setIsScheduleModalOpen(true);
            }}
            className="absolute top-3 right-3 w-7 h-7 rounded-lg bg-[#F55600] text-white flex items-center justify-center opacity-0 group-hover/cell:opacity-100 transition-all shadow-md hover:scale-110 active:scale-95 z-10"
          >
            <Plus className="w-4 h-4" strokeWidth={3} />
          </button>
        )}

        {/* Date Header */}
        <div className="flex items-center justify-between mb-0.5">
          <div className={`text-[10px] font-semibold uppercase tracking-tighter flex items-center gap-1 ${_isToday
            ? 'text-[#F55600] bg-white px-2 py-0.5 rounded-md shadow-sm'
            : (!dayObj.isCurrentMonth
              ? 'text-slate-400'
              : 'text-[#2B2926]')
            }`}>
            <span className="font-semibold">{dayObj.date.getDate()}</span>
            <span className={`text-[8px] ${!dayObj.isCurrentMonth ? 'text-slate-500' : 'text-[#2B2926]'} font-bold`}>{monthNames[dayObj.date.getMonth()].slice(0, 3)}</span>
          </div>
        </div>

        {/* Posts Display */}
        <div className="flex-1 flex flex-col gap-0.5 items-center justify-center">
          {dayPosts.length > 0 ? (
            <>
              <div className="flex -space-x-1.5 mb-0.5">
                {Array.from(new Set(dayPosts.flatMap(p => p.platforms))).map((p, i) => (
                  <div key={i} className="w-5 h-5 rounded-full bg-white border-2 border-[#2B2926]/30 flex items-center justify-center shadow-md hover:shadow-lg hover:scale-110 transition-all">
                    {getPlatformIcon(p, "w-2.5 h-2.5 " + (
                      p === 'linkedin' ? 'text-[#0077b5]' :
                        p === 'twitter' ? 'text-[#2B2926]' :
                          p === 'facebook' ? 'text-[#1877F2]' : 'text-[#E4405F]'
                    ))}
                  </div>
                ))}
              </div>
              {/* Post-count badge — bumped to a solid brand-green pill
                  with white bold text so the "N Posts" count is clearly
                  legible on the calendar grid (was a faint 8px emerald
                  label that the user couldn't read). Brand palette only:
                  #10B981 green resting, #F55600 orange on cell hover. */}
              <span className="text-[11px] font-semibold text-white bg-[#10B981] px-2 py-0.5 rounded-full shadow-sm group-hover/cell:bg-[#F55600] transition-all">
                {dayPosts.length} Post{dayPosts.length > 1 ? 's' : ''}
              </span>
            </>
          ) : (
            <div className="opacity-0 group-hover/cell:opacity-80 transition-opacity">
              <span className="text-[8px] font-semibold text-[#2B2926] uppercase tracking-widest">Available</span>
            </div>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="h-full min-h-0 bg-white py-0.5 px-2 md:px-4 overflow-hidden">
      <div className="max-w-[1400px] w-full mx-auto flex flex-col animate-in fade-in duration-700 pb-1 h-full min-h-0">
          {/* Single toolbar row — month-nav + Month/Week toggle stay
              visually centred (via mx-auto on desktop) and the COMPANY
              filter is parked at the right corner sharing the row with the
              Month/Week toggle. On mobile it wraps to a second line. */}
          <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 md:gap-4 w-full pb-4 px-1">

            {/* Centre group — month-nav pill + Month/Week toggle. */}
            <div className="flex flex-row items-center justify-center gap-2 sm:gap-4 md:mx-auto">

              {/* Apollo-style month-nav pill: clean #2B2926/30 hairline,
                  no shadow, calmer chevron icon colour. */}
              <div className="flex items-center gap-1 bg-white p-1 rounded-full border border-[#2B2926]/30 w-full md:w-auto md:min-w-[260px] justify-between">
                <button onClick={handlePrev} className="w-7 h-7 flex items-center justify-center hover:bg-[#2B2926]/[0.04] rounded-full text-[#2B2926] hover:text-[#F55600] transition-all">
                  <ChevronLeft className="w-4 h-4" />
                </button>
                <h3 className="text-[12px] font-semibold text-[#2B2926] px-3 text-center leading-none">
                  {viewMode === 'month' ? `${monthNames[month]} ${year}` : `Week of ${rollingWeekDays[0]?.date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`}
                </h3>
                <button onClick={handleNext} className="w-7 h-7 flex items-center justify-center hover:bg-[#2B2926]/[0.04] rounded-full text-[#2B2926] hover:text-[#F55600] transition-all">
                  <ChevronRight className="w-4 h-4" />
                </button>
              </div>

              {/* Month / Week segmented control — single-pill border,
                  active pill orange-filled, inactive sand-text. No heavy
                  shadow or thick border. */}
              <div className="flex items-center gap-0.5 bg-white p-1 rounded-full border border-[#2B2926]/30 w-full md:w-auto md:min-w-[180px] justify-center">
                <button
                  onClick={() => setViewMode('month')}
                  className={`flex-1 px-4 py-1 text-[11px] font-semibold rounded-full transition-all ${viewMode === 'month' ? 'bg-[#F55600] text-white' : 'text-[#2B2926] hover:bg-[#2B2926]/[0.04]'}`}
                >
                  Month
                </button>
                <button
                  onClick={() => setViewMode('week')}
                  className={`flex-1 px-4 py-1 text-[11px] font-semibold rounded-full transition-all ${viewMode === 'week' ? 'bg-[#F55600] text-white' : 'text-[#2B2926] hover:bg-[#2B2926]/[0.04]'}`}
                >
                  Week
                </button>
              </div>
            </div>

            {/* COMPANY filter — pinned to the right edge on desktop so it
                sits on the same row as the Month/Week toggle. */}
            <div className="w-auto shrink-0 min-w-[120px] md:ml-auto">
              <TeamMembersFilter
                user={user}
                authAxios={authAxios}
                value={selectedMemberIds}
                onChange={setSelectedMemberIds}
              />
            </div>
          </div>

        <div className="flex-1 min-h-0 bg-white rounded-3xl border-2 border-slate-400 flex flex-col shadow-xl relative overflow-x-auto custom-scrollbar-horizontal">
          <div className="min-w-[1120px] sm:min-w-[840px] lg:min-w-[700px] flex flex-col flex-1">
            {/* Header Row */}
            <div className="grid grid-cols-7 border-b-2 border-slate-400 bg-white shadow-sm">
              {viewMode === 'month' ? (
                dayNames.map(day => (
                  <div key={day} className="py-2.5 text-center text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest calendar-weekday-text">
                    <span className="hidden sm:inline">{day}</span>
                    <span className="inline sm:hidden">{day.substring(0, 3)}</span>
                  </div>
                ))
              ) : (
                rollingWeekDays.map(dayObj => (
                  <div key={dayObj.date.toISOString()} className="py-2.5 text-center text-[10px] font-semibold text-[#F55600] uppercase tracking-widest">
                    {dayNames[dayObj.date.getDay()]}
                  </div>
                ))
              )}
            </div>

            {/* Dynamic View */}
            <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar bg-white">
              {viewMode === 'month' ? (
                <div className="flex flex-col min-h-full">
                  {weeks.map((week, wIdx) => (
                    <div
                      key={wIdx}
                      className="grid grid-cols-7 border-b border-[#2B2926]/30 last:border-b-0 flex-1"
                    >
                      {week.map(dayObj => renderDayCell(dayObj))}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="grid grid-cols-7 bg-white h-auto border-t-2 border-slate-400">
                  {rollingWeekDays.map((dayObj, idx) => renderDayCell(dayObj, true))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Simplified Light Create Post Modal — two-column layout
            (left: accounts + time, right: campaign brief) so the brief
            textarea has more writing room without scrolling. Falls back to
            single column on small screens. */}
        {isScheduleModalOpen && (
          <div className="fixed inset-0 z-[110] flex items-center justify-center p-6 bg-transparent animate-in fade-in duration-300">
            <div className="bg-white rounded-[32px] w-full max-w-3xl shadow-2xl overflow-hidden flex flex-col border-2 border-[#2B2926]/30 animate-in zoom-in-95 duration-300">
              {/* Modal Header */}
              <div className="px-6 py-5 border-b border-[#2B2926]/30 flex items-center justify-between bg-slate-50/50">
                <div>
                  <h4 className="text-xl font-bold text-[#2B2926] tracking-tight">Schedule New Post</h4>
                  <p className="text-[10px] font-semibold text-[#F55600] uppercase tracking-widest mt-1">
                    {selectedDate?.toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' })}
                  </p>
                </div>
                <button
                  onClick={() => setIsScheduleModalOpen(false)}
                  className="w-10 h-10 rounded-full bg-white border border-[#2B2926]/30 flex items-center justify-center text-slate-400 hover:bg-slate-50 hover:text-[#2B2926] transition-all shadow-sm"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* Modal Content — two-column grid (50/50 split) */}
              <div className="p-6 grid grid-cols-1 lg:grid-cols-2 gap-6 overflow-y-auto max-h-[70vh] custom-scrollbar">
                {/* LEFT COLUMN — accounts + posting time */}
                <div className="space-y-5">
                {/* 1. Account Selection */}
                <div>
                  <label className="text-[10px] font-semibold uppercase text-[#2B2926] tracking-[0.2em] mb-4 block underline decoration-orange-500/30 underline-offset-4">Select Connection Accounts</label>
                  <div className="flex flex-wrap gap-3">
                    {availableAccounts.map(acc => {
                      const isSelected = (selectedAccounts[acc.platform] || []).includes(acc.id);
                      const isUnsupported = VIDEO_ONLY_PLATFORMS.includes(acc.platform);
                      return (
                        <button
                          key={acc.id + acc.platform}
                          title={isUnsupported ? `${acc.platform} scheduling is not available yet` : (acc.name || acc.platform)}
                          onClick={() => {
                            if (isUnsupported) {
                              setUnsupportedNotice(`${acc.platform.charAt(0).toUpperCase() + acc.platform.slice(1)} is currently not available to schedule — video generation coming soon.`);
                              return;
                            }
                            setUnsupportedNotice('');
                            setSelectedAccounts(prev => {
                              const current = prev[acc.platform] || [];
                              const updated = current.includes(acc.id)
                                ? current.filter(id => id !== acc.id)
                                : [...current, acc.id];
                              return { ...prev, [acc.platform]: updated };
                            });
                          }}
                          className={`flex flex-col items-center gap-1.5 w-[56px] group/acc ${isUnsupported ? 'opacity-50 cursor-not-allowed' : ''}`}
                        >
                          <div className={`relative w-10 h-10 rounded-full p-0.5 border-2 transition-all ${isSelected ? 'border-[#F55600] shadow-md shadow-[#F55600]/20' : 'border-[#2B2926]/20 group-hover/acc:border-[#F55600]/60'}`}>
                            <div className="w-full h-full rounded-full overflow-hidden relative border-2 border-white">
                              <ConnAvatar url={acc.profile_picture_url} name={acc.name} />
                            </div>
                            {isSelected && (
                              <div className="absolute -top-1 -right-1 w-4 h-4 bg-[#F55600] rounded-full flex items-center justify-center text-white shadow-md border-2 border-white animate-in zoom-in duration-200">
                                <Check className="w-2.5 h-2.5" strokeWidth={4} />
                              </div>
                            )}
                            <div className={`absolute -bottom-1 -right-1 w-4 h-4 rounded-full border-2 border-white flex items-center justify-center ${acc.platform === 'linkedin' ? 'bg-[#0077b5]' : acc.platform === 'twitter' ? 'bg-[#2B2926]' : acc.platform === 'facebook' ? 'bg-[#1877F2]' : 'bg-[#E4405F]'}`}>
                              {getPlatformIcon(acc.platform, "w-2 h-2 text-white")}
                            </div>
                          </div>
                          <span className={`text-[9px] font-bold truncate max-w-[54px] ${isSelected ? 'text-[#F55600]' : 'text-[#2B2926]'}`}>
                            {acc.name || acc.platform}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                  {unsupportedNotice && (
                    <div className="mt-3 text-[10px] font-semibold text-[#F55600] bg-[#F55600]/8 border border-[#F55600]/25 rounded-lg px-3 py-2">
                      {unsupportedNotice}
                    </div>
                  )}
                </div>

                {/* 2. Time Selection (left column, under accounts) */}
                <div className="space-y-4">
                  <label className="text-[10px] font-semibold uppercase text-[#2B2926] tracking-[0.2em] block">Select Posting Time</label>
                  <div className="flex items-center gap-4 p-4 bg-white border-2 border-[#F55600]/20 rounded-2xl shadow-sm">
                    <Clock className="w-5 h-5 text-[#F55600]" />
                    <div className="flex-1">
                      <input
                        type="time"
                        value={scheduledTime}
                        onChange={(e) => setScheduledTime(e.target.value)}
                        className="bg-transparent border-none text-sm font-bold text-[#2B2926] outline-none focus:ring-0 w-full"
                      />
                    </div>
                    <div className="px-3 py-1 bg-white rounded-lg border-2 border-[#F55600]/20 text-[9px] font-bold text-[#F55600] uppercase tracking-widest shadow-sm">
                      Optimal View
                    </div>
                  </div>
                </div>
                </div>

                {/* RIGHT COLUMN — campaign brief (the main writing surface) */}
                <div className="space-y-4 flex flex-col">
                  <div className="flex items-center justify-between">
                    <label className="text-[10px] font-semibold uppercase text-[#2B2926] tracking-[0.2em] block">Campaign Brief</label>
                  </div>
                  <textarea
                    placeholder={postType === 'agentic' ? "Describe your campaign goal..." : "Write your social post content..."}
                    value={campaignBrief}
                    onChange={(e) => setCampaignBrief(e.target.value)}
                    className="w-full flex-1 min-h-[200px] lg:min-h-[260px] bg-white border-2 border-[#F55600] rounded-[20px] p-5 text-sm font-medium text-[#2B2926] outline-none focus:ring-2 focus:ring-[#F55600]/15 focus:border-[#F55600] transition-all hover:shadow-md resize-none shadow-inner"
                  />
                </div>
              </div>

              {/* Modal Footer */}
              <div className="p-6 border-t border-[#2B2926]/30 bg-slate-50/30 flex gap-4">
                {!isReadOnly(user) && (
                <button
                  onClick={async () => {
                    setSavingDraft(true);
                    try {
                      const targets = {};
                      Object.entries(selectedAccounts).forEach(([plat, ids]) => {
                        if (ids.length > 0) targets[plat] = ids;
                      });
                      await authAxios.post('/drafts', {
                        content: JSON.stringify({ default: campaignBrief }),
                        targets: targets
                      });
                      setIsScheduleModalOpen(false);
                      // No refresh needed for draft as it doesn't show in calendar
                    } catch (e) {
                      console.error(e);
                    } finally {
                      setSavingDraft(false);
                    }
                  }}
                  disabled={savingDraft || scheduling}
                  className="flex-1 py-4 bg-white text-slate-600 text-[10px] font-semibold uppercase tracking-widest border border-[#2B2926]/30 rounded-2xl hover:bg-slate-50 hover:text-[#2B2926] transition-all shadow-sm flex items-center justify-center gap-2"
                >
                  {savingDraft ? 'Saving...' : <><FileEdit className="w-3.5 h-3.5" /> Save Draft</>}
                </button>
                )}
                {!isReadOnly(user) && (
                <button
                  onClick={async () => {
                    setScheduling(true);
                    try {
                      const targets = {};
                      Object.entries(selectedAccounts).forEach(([plat, ids]) => {
                        if (ids.length > 0) targets[plat] = ids;
                      });

                      // C-11 fix: the old code constructed `schedDate` in the
                      // BROWSER'S local timezone (new Date + setHours) then
                      // stamped the saved record with the browser's IANA zone
                      // — ignoring the user's app-level timezone preference
                      // entirely. datetimeLocalInputToIso interprets the
                      // naive wall-clock value AS the user's chosen tz so the
                      // post fires at the intended local time regardless of
                      // where the user's browser clock is set.
                      const tz = user?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
                      const y = selectedDate.getFullYear();
                      const m = String(selectedDate.getMonth() + 1).padStart(2, '0');
                      const d = String(selectedDate.getDate()).padStart(2, '0');
                      const localValue = `${y}-${m}-${d}T${scheduledTime}`;
                      const scheduledIso = datetimeLocalInputToIso(localValue, tz);

                      await authAxios.post('/schedule', {
                        post_type: postType,
                        campaign_brief: postType === 'agentic' ? campaignBrief : null,
                        content: postType === 'standard' ? campaignBrief : JSON.stringify({}),
                        targets,
                        scheduled_for: scheduledIso,
                        timezone: tz,
                      });

                      setIsScheduleModalOpen(false);
                      fetchPosts(); // MUST Refresh to update calendar
                    } catch (e) {
                      console.error(e);
                    } finally {
                      setScheduling(false);
                    }
                  }}
                  disabled={scheduling || savingDraft || Object.values(selectedAccounts).flat().length === 0 || !campaignBrief}
                  className="flex-[1.5] py-4 bg-[#F55600] text-white text-[10px] font-semibold uppercase tracking-widest rounded-2xl hover:bg-[#e85a20] shadow-lg shadow-orange-100 disabled:opacity-50 transition-all flex items-center justify-center gap-2"
                >
                  {scheduling ? 'Scheduling...' : <><Zap className="w-3.5 h-3.5" /> Schedule Post</>}
                </button>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Day Details Modal */}
        {/* C-14: harden against a malformed selectedDayPosts.date. Previously
            if .date was not a Date instance (e.g. a raw ISO string from a
            stale state update), toLocaleDateString() threw and unmounted the
            whole page. Guard by coercing to Date and falling back to a blank
            label on failure. */}
        {isModalOpen && selectedDayPosts && (() => {
          let headerLabel = '';
          try {
            const d = selectedDayPosts.date instanceof Date
              ? selectedDayPosts.date
              : new Date(selectedDayPosts.date);
            if (!isNaN(d.getTime())) {
              headerLabel = d.toLocaleDateString(undefined, {
                weekday: 'long', month: 'long', day: 'numeric', year: 'numeric',
              });
            }
          } catch { /* headerLabel stays empty */ }
          return (
          <div
            className="fixed inset-0 z-[100] flex items-center justify-center p-3 sm:p-4 bg-slate-900/50 backdrop-blur-sm transition-all duration-300"
            onClick={(e) => { if (e.target === e.currentTarget) setIsModalOpen(false); }}
          >
            <div className="relative bg-white rounded-[32px] w-full max-w-4xl max-h-[92vh] sm:max-h-[88vh] flex flex-col shadow-2xl animate-in zoom-in-95 fade-in duration-300 overflow-hidden border-2 border-slate-350">
              {/* Floating close X — guaranteed visible regardless of header layout.
                  Dark background + white icon so it's impossible to miss against
                  the white modal header. */}
              <button
                onClick={() => setIsModalOpen(false)}
                aria-label="Close"
                className="absolute top-4 right-4 z-30 w-11 h-11 rounded-full bg-slate-900 text-white border-2 border-white flex items-center justify-center hover:bg-red-500 hover:scale-110 transition-all shadow-xl shadow-slate-400/40"
              >
                <X className="w-5 h-5" strokeWidth={2.5} />
              </button>

              {/* Modal Header */}
              <div className="px-8 py-7 pr-16 flex items-center justify-between bg-white border-b border-[#2B2926]/30 shrink-0">
                <div>
                  <h4 className="text-xl font-bold text-[#2B2926] tracking-tight">
                    {headerLabel}
                  </h4>
                  <p className="text-[12px] font-bold text-[#2B2926] uppercase tracking-[0.14em] mt-1">
                    {selectedPostId ? 'Post Details' : `${selectedDayPosts.posts.length} Content Piece${selectedDayPosts.posts.length > 1 ? 's' : ''} Published`}
                  </p>
                </div>
              </div>

              {/* Modal Content */}
              <div className="flex-1 overflow-y-auto p-8 custom-scrollbar space-y-10">
                {(selectedPostId ? selectedDayPosts.posts.filter(p => p.id === selectedPostId) : selectedDayPosts.posts).map((post, idx) => {
                  // Content Parsing Logic
                  let displayContent = post.content;

                  // For Agentic posts that haven't been generated yet, show the COMPLETE Campaign Brief
                  if (post.isScheduled && post.post_type === 'agentic' && (!post.content || post.content === '{}' || post.content === '')) {
                    displayContent = post.campaign_brief || 'Autonomous Content';
                    if (displayContent.includes('SPECIFIC POST TOPIC:')) {
                       // Optional: bold the topic if it's there
                       displayContent = displayContent.replace('SPECIFIC POST TOPIC:', '\n\n**Strategic Topic:**');
                    }
                  } else if (typeof post.content === 'string' && post.content.trim().startsWith('{')) {
                    try {
                      const json = JSON.parse(post.content);
                      displayContent = json.linkedin || json.twitter || json.instagram || json.facebook || json.default || Object.values(json)[0] || post.content;
                    } catch (e) { displayContent = post.content; }
                  }

                  return (
                    <div key={post.id} className="group/post flex flex-col gap-6 pb-10 last:pb-0">
                      {/* Post Header */}
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          <div className="w-9 h-9 rounded-[14px] bg-orange-50 flex items-center justify-center border border-orange-100 shadow-sm">
                            <Clock className="w-4 h-4 text-[#F55600]" />
                          </div>
                          <div className="flex flex-col">
                            <p className="text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.14em] leading-none mb-1">Published At</p>
                            <p className="text-xs font-bold text-[#2B2926]">
                              {new Date(post.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                            </p>
                          </div>
                        </div>
                        <div className="flex -space-x-1.5">
                          {post.platforms.map((p, i) => (
                            <div key={i} className="w-8 h-8 rounded-full bg-white border border-[#2B2926]/30 flex items-center justify-center z-10 shadow-sm ring-4 ring-white">
                              {getPlatformIcon(p, "w-4 h-4 " + (
                                p === 'linkedin' ? 'text-[#0077b5]' :
                                  p === 'twitter' ? 'text-[#2B2926]' :
                                    p === 'facebook' ? 'text-[#1877F2]' : 'text-[#E4405F]'
                              ))}
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* Post Body: Content LEFT, Image RIGHT on desktop.
                          Stacks vertically on mobile (content first), with image
                          below. Grid only kicks in at md: so side-by-side appears
                          when there's room. When no image, content spans full width. */}
                      <div className={`grid gap-5 ${(post.image_url || post.youtube_video_id) ? 'md:grid-cols-2 md:items-start' : 'grid-cols-1'}`}>
                        {/* Image card — LEFT on desktop, FIRST on mobile (visual context).
                            YouTube posts have no usable image_url (it's the video
                            file); derive the poster frame from the video id. */}
                        {(() => {
                          const isYoutube = (post.platforms || []).includes('youtube');
                          const ytId = isYoutube ? post.youtube_video_id : null;
                          // Only YouTube gives us a poster PNG. For every other video
                          // source (Facebook/LinkedIn/Instagram video, direct upload),
                          // `post.image_url` is the .mp4 file itself — an <img> would
                          // render as a broken icon. Route those to a native <video>.
                          const isNonYtVideo =
                            !isYoutube && (
                              post.media_type === 'video'
                              || /\.(mp4|mov|webm|m4v)(\?|$)/i.test(post.image_url || '')
                            );
                          const isVideo = isYoutube || isNonYtVideo;
                          const thumb = ytId ? `https://img.youtube.com/vi/${ytId}/hqdefault.jpg` : post.image_url;
                          if (isDocumentMedia(post)) {
                            return (
                              <div className="order-1 md:order-1 w-full flex items-center justify-center">
                                <DocCard url={post.image_url} thumbnailUrl={post.thumbnail_url} />
                              </div>
                            );
                          }
                          // Non-YouTube video — play the actual clip inline so users
                          // can preview before publish, same behaviour Drafts uses.
                          if (isNonYtVideo && post.image_url) {
                            return (
                              <div className="w-full max-h-[72vh] rounded-[24px] overflow-hidden shadow-xl shadow-orange-100/20 relative order-1 md:order-1 bg-slate-900">
                                <video
                                  src={post.image_url}
                                  controls
                                  preload="metadata"
                                  playsInline
                                  className="w-full h-auto max-h-[72vh] object-contain"
                                />
                                <div className="absolute top-3 left-3 bg-white/90 px-3 py-1.5 rounded-full text-[9px] font-semibold uppercase text-[#2B2926] tracking-widest flex items-center gap-2">
                                  <ImageIcon className="w-3 h-3" /> Video
                                </div>
                              </div>
                            );
                          }
                          if (!thumb) return null;
                          return (
                          <div className="w-full max-h-[72vh] rounded-[24px] overflow-hidden shadow-xl shadow-orange-100/20 relative group/img order-1 md:order-1 bg-slate-50">
                            <img src={thumb} alt="" className="w-full h-auto object-contain transition-transform duration-700 group-hover/img:scale-110" onError={(e) => { e.currentTarget.style.display = 'none'; }} />
                            {isVideo && (
                              <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                                <span className="w-0 h-0 border-y-[10px] border-y-transparent border-l-[16px] border-l-white drop-shadow ml-1" />
                              </div>
                            )}
                            <div className="absolute inset-0 bg-gradient-to-t from-slate-900/40 to-transparent flex items-end p-4 sm:p-6">
                              <div className="bg-white/90 px-3 sm:px-4 py-1.5 rounded-full text-[9px] font-semibold uppercase text-[#2B2926] tracking-widest flex items-center gap-2">
                                <ImageIcon className="w-3 h-3" /> Attached Media
                              </div>
                            </div>
                          </div>
                          );
                        })()}
                        {/* Content card — RIGHT on desktop, SECOND on mobile */}
                        <div className="bg-white border border-[#2B2926]/30 p-5 sm:p-6 rounded-[24px] relative overflow-hidden group/content order-2 md:order-2">
                          <p className="text-sm font-medium text-[#2B2926] leading-relaxed relative z-10 whitespace-pre-line">
                            {displayContent}
                          </p>
                        </div>
                      </div>

                      {/* Post Actions (Status Badge). Solid-fill pills +
                          a solid-fill delete button so the status and
                          the delete action are clearly legible — the
                          previous faint tinted versions were hard to
                          read. Brand palette: #F55600 for scheduled,
                          #10B981 for live. */}
                      <div className="flex items-center gap-3">
                        <div className="flex-1 h-px bg-[#2B2926]/10"></div>
                        <div className={`flex items-center gap-2 px-3.5 py-1.5 text-[10px] font-semibold uppercase tracking-widest rounded-full text-white shadow-sm ${post.isScheduled
                          ? 'bg-[#F55600]'
                          : 'bg-[#10B981]'
                          }`}>
                          Status: {post.isScheduled ? 'Scheduled' : 'Live & Syncing'}
                          {post.isScheduled && post.post_type === 'agentic' && (
                            <span className="ml-2 pl-2 border-l border-white/40">AI Agent Active</span>
                          )}
                        </div>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDeletePost(post);
                          }}
                          className="w-9 h-9 rounded-xl bg-[#2B2926] text-white flex items-center justify-center hover:bg-[#F55600] transition-all shadow-sm"
                          title="Delete Post"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                        <div className="flex-1 h-px bg-[#2B2926]/10"></div>
                      </div>
                      
                      {selectedPostId && selectedDayPosts.posts.length > 1 && (
                        <div className="pt-4 flex justify-center">
                          <button 
                            onClick={() => setSelectedPostId(null)}
                            className="bg-slate-100 hover:bg-slate-200 text-slate-600 text-[9px] font-semibold uppercase tracking-widest px-4 py-2 rounded-xl transition-all"
                          >
                            View All {selectedDayPosts.posts.length} Posts for this Day
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Modal Footer */}
              <div className="px-8 py-6 bg-white border-t border-[#2B2926]/30 flex justify-end shrink-0">
                <button
                  onClick={() => setIsModalOpen(false)}
                  className="px-6 py-2.5 bg-slate-800 text-white text-[10px] font-semibold uppercase tracking-widest rounded-xl hover:bg-slate-900 transition-all shadow-lg shadow-slate-100"
                >
                  Close Overview
                </button>
              </div>
            </div>
          </div>
          );
        })()}
      </div>
    </div>
  );
};

export default Calendar;
