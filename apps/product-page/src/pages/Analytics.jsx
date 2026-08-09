import React, { useState, useEffect, useCallback, useRef, useLayoutEffect } from 'react';
import { createPortal } from 'react-dom';
import { isReadOnly } from '../lib/permissions';
// A-10: removed unused recharts components (BarChart, Bar, LineChart, Line).
// PieChart + Pie ARE used by the Audience Distribution donut chart.
import {
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  PieChart, Pie, Cell, AreaChart, Area
} from 'recharts';
// A-10: removed unused FiFilter icon.
import {
  FiTrendingUp, FiUsers, FiMessageCircle, FiEye,
  FiArrowUpRight, FiArrowDownRight, FiDownload, FiRefreshCw,
  FiChevronDown, FiX, FiMaximize2, FiHeart, FiShare2, FiActivity, FiUser,
  FiPieChart
} from 'react-icons/fi';
import XIcon from '../components/icons/XIcon';
import BrandSelect from '../components/BrandSelect';
import CalendarPicker from '../components/CalendarPicker';
import { isDocumentMedia, DocThumb, DocCard } from '../components/PostMedia';
import jsPDF from 'jspdf';
import autoTable from 'jspdf-autotable';
import html2canvas from 'html2canvas';
import { formatInTimezone } from '../utils/timezones';

// Brand colors per platform (official). Keep this list in lockstep with
// the platform classifier in `getPlatformKey` below.
const PLATFORM_COLORS = {
  'linkedin':  '#0A66C2',
  'facebook':  '#1877F2',
  'twitter':   '#000000', // X
  'instagram': '#DD2A7B',
  'youtube':   '#FF0000',
  'tiktok':    '#2B2926', // TikTok's primary uses black; cyan/magenta are secondaries
  'default':   '#94A3B8',
};

// Per-platform color palettes — used when MULTIPLE companies share the
// same platform (e.g., LinkedIn for both NeuzenAI and Z-Ninth). The first
// shade is the platform's canonical brand color; subsequent shades are
// progressively lighter / shifted so each company is visually distinct
// while still reading as that platform.
const PLATFORM_PALETTES = {
  linkedin:  ['#0A66C2', '#5DA0E0', '#A8C9EC', '#1E3A5F'],
  facebook:  ['#1877F2', '#5FA3F7', '#A4CBFB', '#0E4A9C'],
  twitter:   ['#000000', '#525252', '#8C8C8C', '#1F1F1F'],
  instagram: ['#DD2A7B', '#F06595', '#F8AEC3', '#A11D4B'],
  youtube:   ['#FF0000', '#FF5252', '#FF8A8A', '#B71C1C'],
  // TikTok has no single brand color (it uses a cyan+magenta+black trio).
  // We pick black as the primary so the pie slice reads cleanly next to
  // YouTube's red, and use cyan/magenta tints as company-differentiators.
  tiktok:    ['#2B2926', '#69C9D0', '#EE1D52', '#525252'],
  default:   ['#94A3B8', '#B8C2CC', '#D7DDE4'],
};

// Canonical display order for the Audience Distribution list. Entries get
// sorted by this rank so all LinkedIn rows come first, then Facebook,
// then X (Twitter), then Instagram. Anything else falls to the bottom.
const PLATFORM_ORDER = { linkedin: 0, facebook: 1, twitter: 2, instagram: 3, youtube: 4, tiktok: 5 };

// Pretty platform labels — what we render in the breakdown list. Keeps
// casing consistent regardless of how the backend or platform API spelled
// the platform name (e.g., "Linkedin" vs "linkedin" vs "LinkedIn").
const PLATFORM_LABEL = {
  linkedin: 'LinkedIn',
  facebook: 'Facebook',
  twitter:  'X',
  instagram: 'Instagram',
  youtube:  'Youtube',
  tiktok:   'TikTok',
};

// Classify any free-text platform string into one of the four canonical
// keys. Used to drive both color and order.
const getPlatformKey = (name) => {
  const lower = (name || '').toLowerCase();
  if (lower.includes('linkedin')) return 'linkedin';
  if (lower.includes('facebook')) return 'facebook';
  if (lower.includes('twitter') || lower.includes(' x ') || lower.startsWith('x ') || lower.endsWith(' x') || lower === 'x') return 'twitter';
  if (lower.includes('instagram') || lower.includes(' ig ')) return 'instagram';
  if (lower.includes('youtube') || lower.includes('you tube')) return 'youtube';
  if (lower.includes('tiktok') || lower.includes('tik tok')) return 'tiktok';
  if (lower.includes('pinterest')) return 'pinterest';
  return 'default';
};

const getPlatformColor = (name) => PLATFORM_COLORS[getPlatformKey(name)] || PLATFORM_COLORS.default;

// Resolve a post's thumbnail. Most posts carry an `image_url`. YouTube
// video posts don't — their thumbnail is derived from the video id
// (`native_id`) via YouTube's predictable thumbnail URL, so video posts
// show the real frame instead of an empty placeholder.
const getPostThumb = (post) => {
  // YouTube posts: their `image_url` (if any) is the uploaded VIDEO file,
  // which can't render in an <img>. Always use YouTube's poster frame,
  // derived from the video id (native_id), so the cell shows a real image.
  if (post?.platform === 'youtube' && post?.native_id) {
    return `https://img.youtube.com/vi/${String(post.native_id).trim()}/hqdefault.jpg`;
  }
  if (post?.image_url) return post.image_url;
  return '';
};

// Build a YouTube embed URL (autoplay) from a post's video id.
const getYoutubeEmbed = (post) =>
  post?.platform === 'youtube' && post?.native_id
    ? `https://www.youtube.com/embed/${String(post.native_id).trim()}?autoplay=1`
    : '';

// Generic collapsible multi-select dropdown for the Brand Filter.
// Saves vertical space in the panel while keeping the cascading feel.
const FilterDropdown = ({ label, options, selected, placeholder = "All", renderOption, subtitle, noBorder = false }) => {
  const [isOpen, setIsOpen] = useState(false);
  return (
    <div className={`p-2 ${noBorder ? '' : 'border-b border-[#2B2926]/30 last:border-0'}`}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-[9px] font-semibold uppercase tracking-widest text-[#2B2926]">
          {label} {subtitle && <span className="text-[#2B2926] normal-case font-normal ml-1">({subtitle})</span>}
        </span>
      </div>
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className={`w-full flex items-center justify-between text-[11px] font-bold rounded-lg border px-2 py-1.5 bg-white transition-all text-left ${isOpen ? 'border-[#F55600]/40 shadow-sm' : 'border-[#2B2926]/30 hover:border-[#F55600]/20 shadow-sm shadow-slate-50'}`}
      >
        <span className="truncate text-[#2B2926]">
          {selected.length === 0 ? placeholder : `${selected.length} Selected`}
        </span>
        <FiChevronDown className={`transition-transform duration-300 text-[#2B2926] ${isOpen ? 'rotate-180' : ''}`} size={12} />
      </button>
      {isOpen && (
        <div className="mt-1 space-y-1 max-h-[160px] overflow-y-auto pr-1 animate-in zoom-in-95 duration-200 custom-scrollbar border border-slate-50 p-1.5 rounded-lg bg-slate-50/30">
          {options.length === 0 ? (
            <div className="text-[10px] text-[#2B2926] italic py-2 text-center bg-white rounded-lg">No options available</div>
          ) : (
            options.map(opt => renderOption(opt))
          )}
        </div>
      )}
    </div>
  );
};

// Collapsible section for filter groups to keep the panel tidy.
const CollapsibleSection = ({ title, children, defaultOpen = true }) => {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  return (
    <div className="pb-2 last:pb-0" style={{ fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}>
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center justify-between gap-2.5 py-1.5 pb-3 text-left"
      >
        <span
          className="uppercase tracking-[0.1em]"
          style={{ color: '#F55600', fontSize: '11px', fontWeight: 600, letterSpacing: '0.1em' }}
        >
          {title}
        </span>
        <span
          className={`inline-flex items-center justify-center w-[22px] h-[22px] rounded-[7px] border transition-transform ${isOpen ? 'rotate-0' : 'rotate-180'}`}
          style={{ borderColor: 'rgba(255,106,44,0.2)', color: '#F55600', padding: '3px' }}
        >
          <FiChevronDown size={11} strokeWidth={2.4} className="rotate-180" />
        </span>
      </button>
      {isOpen && (
        <div className="animate-in slide-in-from-top-1 duration-200">
          {children}
        </div>
      )}
    </div>
  );
};

// Module-level caches so the Analytics + Campaign Performance pages
// survive navigation AND filter changes. Without these, leaving the
// tab and returning re-fires the full `/analytics/summary` +
// `/analytics/posts` load (5-7 s on 7d windows) every time. The cache
// lets the page render immediately on re-mount / filter change with
// the previous result while a silent background refetch updates it.
// Keyed by mode + full filter signature so changing time period or
// brand filter still benefits — the previous filter combo's data
// remains cached for instant return.
const _analyticsPostsCache = new Map();   // key -> { rows, fetchedAt }
const _analyticsSummaryCache = new Map(); // key -> { data, fetchedAt }
const _analyticsInflight = new Map();      // key -> Promise (de-dup)

// LocalStorage persistence for the SUMMARY cache only (posts can be
// large; summary is the heavy query that hurts most). Hydrated once on
// module load, persisted per successful fetch. Capped at 60 minutes
// so the cold-start view isn't wildly out of date.
// v4 (May 2026): per-platform engagement/reach now scoped to selected
// time window + brand filter (was lifetime). Drops cached v3 payloads
// whose per-platform totals exceeded the All-platforms KPI.
// v5 (May 2026): drops v4 payloads captured between the v3→v4 bump and
// the Bug A backend fix landing. Those carried inflated per-platform
// totals (e.g. LinkedIn 583 eng / 7600 reach vs the DB-true 296 / 3909).
const _ANALYTICS_LS_KEY = 'pipelyt_analytics_summary_cache_v5';
const _ANALYTICS_MAX_AGE_MS = 60 * 60 * 1000;
(function _hydrateAnalyticsCache() {
  try {
    const raw = localStorage.getItem(_ANALYTICS_LS_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return;
    Object.entries(parsed).forEach(([k, v]) => {
      if (v && v.data && typeof v.fetchedAt === 'number'
          && Date.now() - v.fetchedAt < _ANALYTICS_MAX_AGE_MS) {
        _analyticsSummaryCache.set(k, v);
      }
    });
  } catch { /* corrupted — ignore */ }
})();
const _persistAnalyticsCache = () => {
  try {
    const obj = {};
    for (const [k, v] of _analyticsSummaryCache.entries()) obj[k] = v;
    localStorage.setItem(_ANALYTICS_LS_KEY, JSON.stringify(obj));
  } catch { /* quota — ignore */ }
};

// LocalStorage persistence for the POSTS cache. Hydrated once on
// module load + persisted per successful fetch so the Campaign
// Performance table paints from disk on a fresh browser session
// instead of staring at the empty table for a few seconds.
const _ANALYTICS_POSTS_LS_KEY = 'pipelyt_analytics_posts_cache_v1';
const _ANALYTICS_POSTS_MAX_AGE_MS = 30 * 60 * 1000; // 30 minutes
(function _hydrateAnalyticsPostsCache() {
  try {
    const raw = localStorage.getItem(_ANALYTICS_POSTS_LS_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return;
    Object.entries(parsed).forEach(([k, v]) => {
      if (v && Array.isArray(v.rows) && typeof v.fetchedAt === 'number'
          && Date.now() - v.fetchedAt < _ANALYTICS_POSTS_MAX_AGE_MS) {
        _analyticsPostsCache.set(k, v);
      }
    });
  } catch { /* corrupted — ignore */ }
})();
const _persistAnalyticsPostsCache = () => {
  try {
    const obj = {};
    for (const [k, v] of _analyticsPostsCache.entries()) obj[k] = v;
    localStorage.setItem(_ANALYTICS_POSTS_LS_KEY, JSON.stringify(obj));
  } catch { /* quota — ignore */ }
};

// Module-level backing stores for sentiment + comments so they survive
// tab switches (component remount). React state is seeded from these on
// first mount and writes back through the wrapper setters below, keeping
// both the module store and React state in sync at all times.
const _sentimentStore = {};
const _commentsStore = {};

const Analytics = ({ authAxios, initialData, user, mode }) => {
  // `mode === 'post-performance-only'` is set by the Campaign Performance
  // tab in the sidebar. It hides the page header, KPI ribbon, charts, and
  // platform-performance section so only the Post Performance table renders.
  const isPostOnly = mode === 'post-performance-only';
  // Seed `data` and `posts` from the cache on mount so the second visit
  // to the page paints the previous results instantly. The key MUST use
  // the full 10-segment format produced by fetchAnalytics / fetchDetailedPosts:
  //   [mode, timePeriod, platform, memberIds, brandIds, country, state, city, pin, custom]
  // Previously used a 3-segment key ("mode|7d|all") that never matched
  // the 10-segment stored keys → cache ALWAYS missed → spinner on every visit.
  const _defaultCacheKey = [mode || 'main', '7d', 'all', '', '', '', '', '', '', ''].join('|');
  const _cachedSummary = _analyticsSummaryCache.get(_defaultCacheKey)?.data || null;
  const _cachedPosts   = _analyticsPostsCache.get(_defaultCacheKey)?.rows || [];
  const [loading, setLoading] = useState(!initialData && !_cachedSummary);
  const [syncing, setSyncing] = useState(false);
  // Refresh toast — shows the active filter combination after Refresh completes
  // so the user has visible confirmation of what data just loaded.
  const [refreshToast, setRefreshToast] = useState(null);
  // Track when the detailed posts were last fetched. Used by handleSync to
  // skip a redundant network round-trip when the user mashes Refresh after
  // data just loaded — gives instant feedback instead of a long syncing spin.
  const lastPostsFetchAtRef = useRef(0);
  const FRESH_WINDOW_MS = 30000;  // 30 seconds — data considered "fresh"
  // Full /analytics/sync hits every connected platform's API (30+ s). Treat
  // the page-load data as freshly synced, and throttle repeat syncs so a
  // Refresh click on already-fresh data does a quick re-fetch instead of the
  // slow platform round-trip. Platform follower/metric counts don't change
  // meaningfully within a couple of minutes.
  const lastSyncAtRef = useRef(Date.now());
  const SYNC_FRESH_WINDOW_MS = 120000;  // 2 minutes
  const [data, setData] = useState(initialData || _cachedSummary || null);
  const [timePeriod, setTimePeriod] = useState('7d');
  const [selectedPlatform, setSelectedPlatform] = useState('all');
  const [isPlatformMenuOpen, setIsPlatformMenuOpen] = useState(false);
  const [activeMetric, setActiveMetric] = useState('all');
  const [posts, setPosts] = useState(_cachedPosts);
  const [loadingPosts, setLoadingPosts] = useState(false);
  const [customDates, setCustomDates] = useState({ start: '', end: '' });
  const [selectedPost, setSelectedPost] = useState(null);

  // Admin-only unified "Brand Filter" (April 2026) — collapses the old
  // separate Company button + Filters button into one cascading dropdown.
  //
  // CASCADE ORDER:
  //   1. Companies (multi) — narrows which members' brands appear next.
  //   2. Brands    (multi) — narrows the list of selectable members.
  //   3. Members   (multi) — final narrowing by specific team-member ids.
  //   4. Location  (country → state → city → pin) — applied on top.
  //
  // Each upstream selection dynamically updates the downstream dropdowns
  // via `useMemo` below so the admin never sees options that would
  // produce zero results.
  const isMember = isReadOnly(user);
  // Seed filterOptions from localStorage so the per-card company
  // dropdowns ("All companies", "NeuzenAI", "Z-NINTH") render
  // immediately on mount instead of being hidden until /team/filter-
  // options returns. Without this seed, the dropdowns appear ~1-2s
  // late and the user sees the cards "missing" their filter UI.
  const [filterOptions, setFilterOptions] = useState(() => {
    try {
      const raw = localStorage.getItem('pipelyt_filter_options_cache_v1');
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === 'object'
            && Date.now() - (parsed.fetchedAt || 0) < 60 * 60 * 1000) {
          return parsed.data || {
            members: [], brands: [], companies: [],
            countries: [], states: [], cities: [], pin_codes: [],
          };
        }
      }
    } catch { /* corrupted — ignore */ }
    return {
      members: [], brands: [], companies: [],
      countries: [], states: [], cities: [], pin_codes: [],
    };
  });
  const [filterOpen, setFilterOpen] = useState(false);
  // Brand Filter popover is portalled to document.body and positioned from
  // the button's measured rect — product pages sit inside transformed
  // (framer-motion) ancestors, which break plain fixed/absolute placement
  // and pushed the popover off-screen on mobile.
  const filterBtnRef = useRef(null);
  const [filterMenuRect, setFilterMenuRect] = useState(null);
  useLayoutEffect(() => {
    if (!filterOpen) { setFilterMenuRect(null); return undefined; }
    const reposition = () => {
      const t = filterBtnRef.current;
      if (!t) return;
      const r = t.getBoundingClientRect();
      const mobile = window.innerWidth < 640;
      // Use clientWidth (excludes the vertical scrollbar) and clamp a LEFT
      // position so the popover can never spill under the scrollbar or off
      // either edge. Right-align to the button, then clamp into [8, vw-W-8].
      const vw = document.documentElement.clientWidth;
      const PW = 336;
      const left = Math.min(Math.max(8, r.right - PW), vw - PW - 8);
      setFilterMenuRect(mobile
        ? { mobile: true, top: r.bottom + 6 }
        : { mobile: false, top: r.bottom + 6, left });
    };
    reposition();
    window.addEventListener('scroll', reposition, true);
    window.addEventListener('resize', reposition);
    return () => {
      window.removeEventListener('scroll', reposition, true);
      window.removeEventListener('resize', reposition);
    };
  }, [filterOpen]);
  // Per-graph company filters. The page-level Brand Filter at the top
  // governs the underlying fetch; these per-graph dropdowns scope a
  // single chart without affecting the rest of the page.
  //   - audienceCompanyFilter: filters Audience Distribution client-side
  //     (the platform_split data is per-brand already).
  //   - trendsCompanyFilter: triggers a SEPARATE backend re-fetch for the
  //     Performance Trends graph (the time-series in the main response is
  //     aggregated, so we have to ask the API to filter by brand and store
  //     the response in a graph-local state).
  const [audienceCompanyFilter, setAudienceCompanyFilter] = useState('');
  const [trendsCompanyFilter, setTrendsCompanyFilter] = useState('');
  // Heatmap (Platform Performance) — supports multi-select of companies.
  // Empty array means "show all companies" (default).
  const [heatmapCompanies, setHeatmapCompanies] = useState([]);
  const [heatmapPickerOpen, setHeatmapPickerOpen] = useState(false);
  // Multi-select state for the Performance Trends + Audience Distribution
  // dropdowns. Empty array = "All companies" (no filter).
  const [trendsPickerOpen, setTrendsPickerOpen] = useState(false);
  const [audiencePickerOpen, setAudiencePickerOpen] = useState(false);
  const [trendsCompanies, setTrendsCompanies] = useState([]);
  const [audienceSel, setAudienceSel] = useState([]);
  // Sync the multi-select arrays back to the single-string filters so the
  // existing backend (trends) / client-side (audience) filtering keeps
  // working when exactly one company is checked. Empty / multiple selections
  // both collapse to "all".
  useEffect(() => {
    setTrendsCompanyFilter(trendsCompanies.length === 1 ? trendsCompanies[0] : '');
  }, [trendsCompanies]);
  useEffect(() => {
    setAudienceCompanyFilter(audienceSel.length === 1 ? audienceSel[0] : '');
  }, [audienceSel]);
  // Which halves of the heatmap cells are visible. Click the legend
  // swatch at the top to isolate just Followers or just Performance;
  // click again to bring both back. At least one must always be shown.
  const [showFollowersHalf, setShowFollowersHalf] = useState(true);
  const [showPerformanceHalf, setShowPerformanceHalf] = useState(true);
  const [trendsData, setTrendsData] = useState(null);
  const [trendsLoading, setTrendsLoading] = useState(false);
  // Unified selection state. Arrays = multi-select; strings = single.
  const [sel, setSel] = useState({
    companies: [],
    brandIds: [],
    memberIds: [],
    country: '', state: '', city: '', pin_code: '',
  });

  // Fetch filter-options once on mount, then refresh each time the Brand
  // Filter dropdown opens (so brands/members added in another tab show up
  // without a full page reload). Previously we called `refreshFilterOptions`
  // both in a mount useEffect and on button click → double fetch on page
  // load. Mount fetch uses a stable ref-guard so it fires exactly once per
  // component lifetime.
  const refreshFilterOptions = useCallback(async () => {
    if (isMember) return;
    try {
      const res = await authAxios.get('/team/filter-options');
      setFilterOptions(res.data || {});
      // Persist so the dropdowns render instantly on the next visit.
      try {
        localStorage.setItem(
          'pipelyt_filter_options_cache_v1',
          JSON.stringify({ data: res.data, fetchedAt: Date.now() }),
        );
      } catch { /* quota — ignore */ }
    } catch (e) {
      console.warn('[Analytics] /team/filter-options failed:', e?.response?.status, e?.response?.data?.detail || e?.message);
    }
  }, [authAxios, isMember]);
  const filterOptionsMountedRef = React.useRef(false);
  useEffect(() => {
    if (filterOptionsMountedRef.current) return;
    filterOptionsMountedRef.current = true;
    refreshFilterOptions();
  }, [refreshFilterOptions]);

  // Cascading available-options — each computed from all upstream selections.
  // Example: picking Companies=[NeuzenAI] means `availableBrands` shrinks to
  // only the brands actually used by NeuzenAI members, and `availableMembers`
  // shrinks to only NeuzenAI members. Picking Brands=[spenzo] then shrinks
  // `availableMembers` further to those assigned Spenzo AI.
  const allMembers = filterOptions.members || [];
  const allBrands = filterOptions.brands || [];
  const membersAfterCompany = React.useMemo(() => (
    sel.companies.length === 0 ? allMembers
      : allMembers.filter(m => m.company && sel.companies.includes(m.company))
  ), [allMembers, sel.companies]);
  const availableBrands = React.useMemo(() => {
    // When NO company filter is active, offer every brand the admin owns —
    // even ones no member is assigned yet, so the admin can still filter
    // their OWN posts by that brand. When companies ARE picked, narrow to
    // brands actually used by members at those companies (cascade).
    if (sel.companies.length === 0) return allBrands;
    const used = new Set(membersAfterCompany.flatMap(m => m.brands || []));
    return allBrands.filter(b => used.has(b.id));
  }, [allBrands, membersAfterCompany, sel.companies.length]);
  const membersAfterBrand = React.useMemo(() => (
    sel.brandIds.length === 0 ? membersAfterCompany
      : membersAfterCompany.filter(m => (m.brands || []).some(b => sel.brandIds.includes(b)))
  ), [membersAfterCompany, sel.brandIds]);
  const availableMembers = membersAfterBrand;
  // Location dropdown options stay derived from the CASCADED member set so
  // picking a brand automatically narrows the list of countries/states/etc.
  const availableCountries = React.useMemo(() => {
    const seen = new Map();
    membersAfterBrand.forEach(m => {
      if (m.country && !seen.has(m.country)) seen.set(m.country, m.country_name || m.country);
    });
    return Array.from(seen, ([code, name]) => ({ code, name })).sort((a, b) => a.name.localeCompare(b.name));
  }, [membersAfterBrand]);
  const availableStates = React.useMemo(() => {
    const seen = new Map();
    membersAfterBrand
      .filter(m => !sel.country || m.country === sel.country)
      .forEach(m => {
        if (m.state && !seen.has(m.state)) seen.set(m.state, m.state_name || m.state);
      });
    return Array.from(seen, ([code, name]) => ({ code, name })).sort((a, b) => a.name.localeCompare(b.name));
  }, [membersAfterBrand, sel.country]);
  const availableCities = React.useMemo(() => {
    const seen = new Set();
    membersAfterBrand
      .filter(m => (!sel.country || m.country === sel.country) && (!sel.state || m.state === sel.state))
      .forEach(m => { if (m.city) seen.add(m.city); });
    return Array.from(seen).sort();
  }, [membersAfterBrand, sel.country, sel.state]);
  const availablePins = React.useMemo(() => {
    const seen = new Set();
    membersAfterBrand
      .filter(m =>
        (!sel.country || m.country === sel.country) &&
        (!sel.state || m.state === sel.state) &&
        (!sel.city || m.city === sel.city)
      )
      .forEach(m => { if (m.pin_code) seen.add(m.pin_code); });
    return Array.from(seen).sort();
  }, [membersAfterBrand, sel.country, sel.state, sel.city]);

  const activeFilterCount = (
    sel.companies.length +
    sel.brandIds.length +
    sel.memberIds.length +
    (sel.country ? 1 : 0) + (sel.state ? 1 : 0) +
    (sel.city ? 1 : 0) + (sel.pin_code ? 1 : 0)
  );

  // Back-compat aliases + "admin only by default" semantic. The backend
  // treats `member_user_ids=<admin_id>` as "only admin's own data" and an
  // empty csv as "all team scope" (admin + every member). Per product
  // spec: the clear / no-filter state should mean ADMIN ONLY, not all
  // team scope — the admin sees their team's aggregate only when they
  // actively include members via the Brand Filter. So:
  //
  //   memberIds explicitly picked        → those ids
  //   companies / brands picked          → cascaded members from those
  //   nothing picked (empty selection)   → just the admin
  //
  // `defaultAdminOnly` becomes TRUE when the filter is fully cleared, so
  // fetch URLs can send the admin's own id.
  //
  // MEMOISATION NOTE: these derived values used to be plain expressions
  // recomputed every render, which produced new array references each
  // time. They flowed into `fetchAnalytics` / `fetchDetailedPosts` via
  // useCallback deps, which then flipped identity every render and
  // triggered the effect chain continuously — a network-request storm.
  // Wrapping in useMemo keyed on the primitive shape of `sel` stops it.
  const cascadedMemberIds = React.useMemo(
    () => availableMembers.map(m => m.id),
    [availableMembers]
  );
  const adminUserId = user?.id || user?.user_id || null;
  const adminCompany = (user?.company_name || '').trim();
  const adminBrandIds = React.useMemo(
    () => (allBrands || []).map(b => b.id),
    [allBrands]
  );

  // Decide if the admin's own data should be included alongside the
  // cascaded team members. Without this, picking "NeuzenAI" (admin's own
  // company) excluded the admin's posts from the dashboard — they only
  // saw their team members, never themselves.
  const adminMatchesFilter = React.useCallback((s) => {
    if (!adminUserId) return false;
    if (s.companies.length > 0 && adminCompany && s.companies.includes(adminCompany)) return true;
    if (s.brandIds.length > 0 && s.brandIds.some(id => adminBrandIds.includes(id))) return true;
    if (s.country && user?.country === s.country) return true;
    if (s.state && user?.state === s.state) return true;
    if (s.city && user?.city === s.city) return true;
    if (s.pin_code && user?.pin_code === s.pin_code) return true;
    return false;
  }, [adminUserId, adminCompany, adminBrandIds, user?.country, user?.state, user?.city, user?.pin_code]);

  const selectedMemberIds = React.useMemo(() => {
    if (sel.memberIds.length > 0) return sel.memberIds;
    if (sel.companies.length + sel.brandIds.length > 0) {
      const ids = [...cascadedMemberIds];
      if (adminMatchesFilter(sel) && !ids.includes(adminUserId)) ids.push(adminUserId);
      return ids;
    }
    if (sel.country || sel.state || sel.city || sel.pin_code) {
      const ids = [...cascadedMemberIds];
      if (adminMatchesFilter(sel) && !ids.includes(adminUserId)) ids.push(adminUserId);
      return ids;
    }
    // No filter selected ("All") → return empty so the backend's
    // `member_user_ids` query param is omitted and the response defaults
    // to the FULL team scope (admin + all members combined). Previously
    // returned [adminUserId] which narrowed "All" to admin only and
    // confused users — they expected "All" to literally show all data.
    return [];
  }, [
    sel.memberIds.join(','),
    sel.companies.join(','), sel.brandIds.join(','),
    sel.country, sel.state, sel.city, sel.pin_code,
    cascadedMemberIds, adminUserId, adminMatchesFilter,
  ]);

  const [fullscreenImage, setFullscreenImage] = useState(null);
  const [sentimentPost, setSentimentPost] = useState(null);
  const [sentimentData, setSentimentData] = useState(null);
  const [isAnalyzingSentiment, setIsAnalyzingSentiment] = useState(false);
  const [sentimentError, setSentimentError] = useState(null);
  // Sentiment + comments caches backed by module-level stores so they
  // survive component remounts (tab switching). Each setter writes through
  // to the backing store so remounted instances start with prior data.
  const [sentimentCache, _setSentimentCacheRaw] = useState(_sentimentStore);
  const setSentimentCache = useCallback((updater) => {
    _setSentimentCacheRaw(prev => {
      const next = typeof updater === 'function' ? updater(prev) : updater;
      Object.assign(_sentimentStore, next);
      return next;
    });
  }, []);
  const [commentsCache, _setCommentsCacheRaw] = useState(_commentsStore);
  const setCommentsCache = useCallback((updater) => {
    _setCommentsCacheRaw(prev => {
      const next = typeof updater === 'function' ? updater(prev) : updater;
      Object.assign(_commentsStore, next);
      return next;
    });
  }, []);
  const [backgroundQueue, setBackgroundQueue] = useState([]);
  const analysisTrackRef = React.useRef(new Set());

  // Data derived from state - defined early to avoid initialization errors.
  // A-12: removed dead `engagement_stats: [0, 0]` — never consumed anywhere.
  const summary = data?.summary || { total_followers: 0, total_engagement: 0, total_reach: 0 };

  // Extract unique brand/company names from platform_split entries. The
  // backend returns names like "Linkedin (Z-Ninth)" or "Instagram (NEUZEN AI)"
  // — we parse the parenthetical to derive the brand options for the
  // per-graph filters. The dedupe key strips whitespace AND non-alphanumeric
  // characters so "NEUZEN AI", "Neuzenai", and "neuzen-ai" all collapse to
  // the same entry (the first-seen casing wins as the display label).
  const _normalizeBrand = (s) => (s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const platformSplit = data?.summary?.platform_split || [];
  const audienceCompanies = React.useMemo(() => {
    const seen = new Map(); // key = normalized, value = first-seen original
    platformSplit.forEach((e) => {
      const m = (e?.name || '').match(/\(([^)]+)\)/);
      if (m && m[1]) {
        const original = m[1].trim();
        const key = _normalizeBrand(original);
        if (key && !seen.has(key)) seen.set(key, original);
      }
    });
    return Array.from(seen.values()).sort((a, b) => a.localeCompare(b));
  }, [platformSplit]);

  // Build the list shown in the Audience Distribution breakdown:
  //   1. Apply the company filter (case + punctuation insensitive)
  //   2. Rebuild each entry's display name as `${PrettyPlatform} (${Brand})`
  //      where Brand uses the same casing as the dropdown's company list,
  //      so what you see in the breakdown matches what you see in the
  //      filter dropdown — no more "NeuzenAI" in the dropdown vs
  //      "NEUZEN AI" in the list.
  //   3. Sort by platform-rank so all LinkedIn entries cluster, then
  //      Facebook, then X, then Instagram. Within a platform, sort by
  //      brand alphabetically.
  const filteredPlatformSplit = React.useMemo(() => {
    // Build a lookup so we can map any case/punctuation variant of a
    // brand name back to the canonical form shown in the dropdown.
    // Source of truth = filterOptions.companies (admin + members).
    const canonicalBrandByKey = {};
    (filterOptions.companies || []).forEach((c) => {
      const k = _normalizeBrand(c);
      if (k) canonicalBrandByKey[k] = c;
    });

    let entries = platformSplit;
    if (audienceCompanyFilter) {
      const target = _normalizeBrand(audienceCompanyFilter);
      entries = entries.filter((e) => {
        const m = (e?.name || '').match(/\(([^)]+)\)/);
        return m && _normalizeBrand(m[1].trim()) === target;
      });
    }
    // Normalize names: pretty platform + canonical brand from the
    // companies list (falls back to the raw brand if no match found).
    const normalized = entries.map((e) => {
      const key = getPlatformKey(e?.name);
      const platformPretty = PLATFORM_LABEL[key] || (e?.name || '').split('(')[0].trim();
      const m = (e?.name || '').match(/\(([^)]+)\)/);
      const rawBrand = (m && m[1] ? m[1].trim() : '').trim();
      const brandKey = _normalizeBrand(rawBrand);
      const canonicalBrand = canonicalBrandByKey[brandKey] || rawBrand;
      return {
        ...e,
        name: canonicalBrand ? `${platformPretty} (${canonicalBrand})` : platformPretty,
        _platformKey: key,
        _brandKey: brandKey,
      };
    });
    // Sort by platform-rank, then by brand name.
    normalized.sort((a, b) => {
      const rankA = PLATFORM_ORDER[a._platformKey] ?? 99;
      const rankB = PLATFORM_ORDER[b._platformKey] ?? 99;
      if (rankA !== rankB) return rankA - rankB;
      return (a._brandKey || '').localeCompare(b._brandKey || '');
    });
    // Assign each entry a color from its platform's palette. Multiple
    // companies on the same platform get progressively-shifted shades so
    // they're visually distinct while still reading as that platform.
    const seenWithinPlatform = {};
    normalized.forEach((entry) => {
      const palette = PLATFORM_PALETTES[entry._platformKey] || PLATFORM_PALETTES.default;
      const idx = seenWithinPlatform[entry._platformKey] || 0;
      entry._color = palette[idx] || palette[palette.length - 1];
      seenWithinPlatform[entry._platformKey] = idx + 1;
    });
    return normalized;
  }, [platformSplit, audienceCompanyFilter, filterOptions.companies]);

  const filteredAudienceTotal = React.useMemo(
    () => filteredPlatformSplit.reduce((sum, e) => sum + (e?.value || 0), 0),
    [filteredPlatformSplit]
  );
  // Use the per-graph trends fetch when the Performance Trends brand
  // filter is set AND a fetch result is available. Otherwise fall back to
  // the main analytics history so the chart never goes blank when the
  // brand-id lookup misses.
  const rawHistory = (trendsCompanyFilter && trendsData?.history?.length
    ? trendsData.history
    : data?.history) || [];
  const platforms = data?.platforms || {};
  const hasData = summary.total_followers > 0 || summary.total_engagement > 0 || summary.total_reach > 0;

  // Defensive chart-data sanitise.
  //
  // Engagement / Likes / Comments / Shares / Reach: per user request, KEEP
  // zero values as 0 (not null) so the lines render from the very first
  // data tick — even when activity is flat at zero — so it's obvious WHEN
  // each metric starts moving up. Previously we nulled zeros which made
  // recharts skip those points and the lines only appeared once activity
  // started, hiding the baseline.
  //
  // Followers: still null when zero, because the follower-count line on
  // this chart is paired with `follower_change_pct`; rendering 0 followers
  // would imply the brand has no audience at all, which is misleading
  // before the first analytics sync lands.
  //
  // (A-13 Fix: per-platform history is now backend-authoritative — the old
  // proportional-scaling frontend pass was removed; `rawHistory` already
  // carries the platform-filtered values when `platform=` was sent.)
  const chartHistory = React.useMemo(() => {
    return rawHistory.map((row) => {
      const out = { ...row };
      // Followers count: still null at zero (see comment above).
      if (!row.followers || row.followers <= 0) out.followers = null;
      // Activity metrics: coerce any null/undefined to a literal 0 so the
      // line renders flat at the x-axis from the first tick onward.
      for (const k of ['engagement', 'likes', 'comments', 'shares', 'reach']) {
        const v = row[k];
        out[k] = (v === null || v === undefined || Number.isNaN(v)) ? 0 : v;
      }
      // A-13 Fix: Also sanitize follower_change_pct if the followers count is null.
      // Per user request: round any negative growth to zero so the graph
      // doesn't show dips, only positive progress.
      if (out.followers === null || (out.follower_change_pct || 0) < 0) {
        out.follower_change_pct = 0;
      }
      return out;
    });
  }, [rawHistory]);


  // A-13 Fix: Calculate custom ticks for the right Y-axis (Follower KPI %)
  // to ensure 0.10% granularity as requested (0.10, 0.20, 0.30, etc).
  // Per user request: Start with zero and only show positive increments.
  const rightAxisTicks = React.useMemo(() => {
    const values = chartHistory.map(d => d.follower_change_pct).filter(v => v !== null);
    const max = values.length > 0 ? Math.max(...values) : 0.1;
    
    // Start at 0 per user request
    const start = 0;
    const end = Math.max(0.1, Math.ceil(max * 10) / 10);
    
    const ticks = [];
    for (let i = start; i <= end + 0.01; i += 0.1) {
      ticks.push(Number(i.toFixed(2)));
    }
    // Limit number of ticks to avoid vertical crowding
    if (ticks.length > 12) {
       const sparse = [];
       for (let i = 0; i < ticks.length; i += 2) sparse.push(ticks[i]);
       return sparse;
    }
    return ticks;
  }, [chartHistory]);

  // A-3 fix: wrap fetch functions in useCallback so the useEffect deps are
  // stable. A-4 fix: fetchDetailedPosts no longer depends on `data`, so
  // setting data inside fetchAnalytics doesn't re-trigger the posts fetch
  // (previously caused a double-fetch on mount). Instead the posts effect
  // depends only on [selectedPlatform, timePeriod, customDates].
  const fetchAnalytics = useCallback(async () => {
    // Campaign Performance page renders only the posts table — none of
    // the KPI ribbon, charts, or platform breakdowns from /analytics/
    // summary are ever drawn. Skip the call entirely so the page loads
    // immediately instead of blocking on the heavy summary endpoint.
    if (isPostOnly) {
      setLoading(false);
      return;
    }

    // Build a full-filter cache key so the cache distinguishes between
    // 24h vs 7d, NeuzenAI vs Z-NINTH, etc. — the previous mode-only key
    // overwrote itself on every filter change.
    const cacheKey = [
      mode || 'main',
      timePeriod,
      selectedPlatform,
      (selectedMemberIds || []).join(','),
      (sel.brandIds || []).join(','),
      sel.country || '', sel.state || '', sel.city || '', sel.pin_code || '',
      timePeriod === 'custom' ? `${customDates.start}~${customDates.end}` : '',
    ].join('|');

    // Stale-while-revalidate: paint cached data immediately if we have
    // it for THIS filter combo, then silently refresh in the background.
    const cached = _analyticsSummaryCache.get(cacheKey);
    if (cached?.data) {
      setData(cached.data);
      setLoading(false);
    } else if (!data) {
      // No cache and no data on screen → first-ever load, show spinner.
      setLoading(true);
    }
    // Else (no cache, but data on screen from a different filter): keep
    // the previous data visible while the new query runs — no spinner.

    // De-dup: a request for this exact qs already in flight → reuse it.
    if (_analyticsInflight.has(cacheKey)) {
      try { await _analyticsInflight.get(cacheKey); } catch {}
      return;
    }

    const work = (async () => {
      try {
        const ts = new Date().getTime();
        let url = `/analytics/summary?time_period=${timePeriod}&platform=${selectedPlatform}&_t=${ts}`;
        if (timePeriod === 'custom' && customDates.start && customDates.end) {
          url += `&start_date=${customDates.start}&end_date=${customDates.end}`;
        }
        if (selectedMemberIds.length > 0) {
          url += `&member_user_ids=${selectedMemberIds.join(',')}`;
        }
        if (sel.brandIds.length > 0) {
          url += `&dna_product_ids=${sel.brandIds.join(',')}`;
        }
        if (sel.country)  url += `&filter_country=${encodeURIComponent(sel.country)}`;
        if (sel.state)    url += `&filter_state=${encodeURIComponent(sel.state)}`;
        if (sel.city)     url += `&filter_city=${encodeURIComponent(sel.city)}`;
        if (sel.pin_code) url += `&filter_pin_code=${encodeURIComponent(sel.pin_code)}`;
        const res = await authAxios.get(url);
        setData(res.data);
        _analyticsSummaryCache.set(cacheKey, { data: res.data, fetchedAt: Date.now() });
        _persistAnalyticsCache();
      } catch (err) {
        console.error("Failed to fetch analytics:", err);
      } finally {
        setLoading(false);
        _analyticsInflight.delete(cacheKey);
      }
    })();

    _analyticsInflight.set(cacheKey, work);
    await work;
  }, [
    isPostOnly,
    mode,
    authAxios, timePeriod, selectedPlatform, selectedMemberIds.join(','),
    // Use primitive string deps for objects/arrays — passing the object
    // itself caused fetchAnalytics' identity to flip on every parent
    // re-render, which re-triggered the useEffect that calls it, which
    // set state, which re-rendered, etc. — endless refetch loop.
    customDates.start, customDates.end,
    sel.brandIds.join(','),
    sel.country, sel.state, sel.city, sel.pin_code,
  ]);

  const fetchDetailedPosts = useCallback(async () => {
    // Build the same full-filter cache key so each filter combo gets
    // its own cache slot (24h vs 7d, NeuzenAI vs Z-NINTH, etc.).
    const postsCacheKey = [
      mode || 'main',
      timePeriod,
      selectedPlatform,
      (selectedMemberIds || []).join(','),
      (sel.brandIds || []).join(','),
      sel.country || '', sel.state || '', sel.city || '', sel.pin_code || '',
      timePeriod === 'custom' ? `${customDates.start}~${customDates.end}` : '',
    ].join('|');

    // Stale-while-revalidate. Cache hit → paint cached rows, no spinner.
    // Cache miss + nothing on screen → show spinner. Cache miss + data
    // already shown → keep it up while refetching (no blanking).
    const cachedPosts = _analyticsPostsCache.get(postsCacheKey);
    if (cachedPosts?.rows) {
      setPosts(cachedPosts.rows);
      setLoadingPosts(false);
    } else if (posts.length === 0) {
      setLoadingPosts(true);
    }

    try {
      const params = new URLSearchParams();
      if (selectedPlatform !== 'all') params.append('platform', selectedPlatform);
      params.append('time_period', timePeriod);
      if (timePeriod === 'custom' && customDates.start && customDates.end) {
        params.append('start_date', customDates.start);
        params.append('end_date', customDates.end);
      }
      if (selectedMemberIds.length > 0) {
        params.append('member_user_ids', selectedMemberIds.join(','));
      }
      if (sel.brandIds.length > 0) {
        params.append('dna_product_ids', sel.brandIds.join(','));
      }
      if (sel.country)  params.append('filter_country', sel.country);
      if (sel.state)    params.append('filter_state', sel.state);
      if (sel.city)     params.append('filter_city', sel.city);
      if (sel.pin_code) params.append('filter_pin_code', sel.pin_code);
      const res = await authAxios.get(`/analytics/posts?${params.toString()}`);
      setPosts(res.data);
      // Cache the rows under the FULL filter signature so each combo
      // has its own slot — no overwrite when the user toggles filters.
      _analyticsPostsCache.set(postsCacheKey, { rows: res.data, fetchedAt: Date.now() });
      _persistAnalyticsPostsCache(); // survive full-page reloads

      // Pre-fill cache with existing sentiment scores
      const initialSentiment = {};
      res.data.forEach(p => {
        // Enforce the 5-comment rule even for pre-loaded sentiment
        if (p.sentiment && (p.comments || 0) >= 5) {
          initialSentiment[`${p.platform}_${p.native_id}`] = p.sentiment;
        }
      });
      if (Object.keys(initialSentiment).length > 0) {
        setSentimentCache(prev => ({ ...prev, ...initialSentiment }));
      }
    } catch (err) {
      console.error("Failed to fetch detailed posts:", err);
    } finally {
      setLoadingPosts(false);
    }
  }, [
    mode,
    authAxios, selectedPlatform, timePeriod,
    selectedMemberIds.join(','),
    customDates.start, customDates.end,
    sel.brandIds.join(','),
    sel.country, sel.state, sel.city, sel.pin_code,
    // Note: posts.length intentionally NOT a dep here — including it makes
    // fetchDetailedPosts re-fire after setPosts(), which is itself called
    // from inside fetchDetailedPosts. That loop is what was causing the
    // page to "keep refreshing" with the same data.
  ]);

  useEffect(() => {
    fetchAnalytics();
  }, [fetchAnalytics]);

  useEffect(() => {
    fetchDetailedPosts();
  }, [fetchDetailedPosts]);

  // Helper that resolves a company-name filter to the list of user_ids
  // we should narrow on (admin + matching team members). Returns null
  // when no users match (caller should fall back to the main payload).
  const _resolveCompanyMemberIds = React.useCallback((companyName) => {
    if (!companyName) return null;
    const targetCompany = _normalizeBrand(companyName);
    const matchedIds = [];
    if (_normalizeBrand(user?.company_name || '') === targetCompany) {
      const adminId = user?.id || user?.user_id;
      if (adminId) matchedIds.push(adminId);
    }
    (filterOptions.members || []).forEach((m) => {
      if (_normalizeBrand(m.company || '') === targetCompany) {
        matchedIds.push(m.id);
      }
    });
    return matchedIds.length > 0 ? matchedIds : null;
  }, [user?.id, user?.user_id, user?.company_name, filterOptions.members]);

  // Trends-only fetch: when the per-card company dropdown on Performance
  // Trends is set, resolve the picked company name to a list of user_ids
  // and hit `/analytics/summary` again with `member_user_ids` narrowed.
  // Uses an in-memory cache keyed by (company|timePeriod|platform|custom)
  // so toggling between companies the user has already viewed paints
  // INSTANTLY — and a background prefetch effect below pre-warms entries
  // for every company in the dropdown right after the page loads, so
  // even the FIRST click on a company filter is a cache hit.
  useEffect(() => {
    if (!trendsCompanyFilter) {
      setTrendsData(null);
      return;
    }
    const matchedIds = _resolveCompanyMemberIds(trendsCompanyFilter);
    if (!matchedIds) {
      setTrendsData(null);
      return;
    }
    const cacheKey = [
      'trends',
      _normalizeBrand(trendsCompanyFilter),
      timePeriod,
      selectedPlatform,
      timePeriod === 'custom' ? `${customDates.start}~${customDates.end}` : '',
    ].join('|');

    // Cache hit → paint instantly, no spinner. Silent revalidate below.
    const cached = _analyticsSummaryCache.get(cacheKey);
    if (cached?.data) {
      setTrendsData(cached.data);
      setTrendsLoading(false);
    } else {
      // Cache miss → keep the previous trendsData visible (no flash to
      // null) while the network call runs.
      setTrendsLoading(true);
    }

    let cancelled = false;
    (async () => {
      try {
        const ts = new Date().getTime();
        let url = `/analytics/summary?time_period=${timePeriod}&platform=${selectedPlatform}&member_user_ids=${matchedIds.join(',')}&_t=${ts}`;
        if (timePeriod === 'custom' && customDates.start && customDates.end) {
          url += `&start_date=${customDates.start}&end_date=${customDates.end}`;
        }
        const res = await authAxios.get(url);
        if (cancelled) return;
        setTrendsData(res.data);
        _analyticsSummaryCache.set(cacheKey, { data: res.data, fetchedAt: Date.now() });
        _persistAnalyticsCache(); // survive full-page reloads
      } catch (err) {
        console.error('Trends-only fetch failed:', err);
        if (!cancelled) setTrendsData(null);
      } finally {
        if (!cancelled) setTrendsLoading(false);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trendsCompanyFilter, timePeriod, selectedPlatform, customDates.start, customDates.end, user?.id, user?.company_name, filterOptions.members]);

  // Pre-warm the trends-only cache for every company in the dropdown
  // right after the main fetch completes. This way, the FIRST time the
  // user clicks a company in the chart's filter dropdown the response
  // is already on disk — no spinner, no wait. Re-runs whenever the
  // time-period or platform change so the prefetch always matches the
  // current global filter.
  useEffect(() => {
    if (isPostOnly) return;
    const companies = filterOptions.companies || [];
    if (companies.length === 0) return;

    let cancelled = false;
    // Fire EVERY company's trends fetch in parallel — no stagger. The
    // backend handles 2-3 concurrent /analytics/summary calls fine,
    // and the prefetch needs to finish before the user gets to the
    // chart's filter dropdown. Each fetch lands in the in-memory cache
    // AND localStorage, so it survives reloads too.
    //
    // Important: this effect doesn't wait for the main `data` payload
    // — it kicks off the moment companies are known, racing the main
    // fetch in parallel.
    const tasks = companies.map((company) => {
      return (async () => {
        if (cancelled) return;
        const cacheKey = [
          'trends',
          _normalizeBrand(company),
          timePeriod,
          selectedPlatform,
          timePeriod === 'custom' ? `${customDates.start}~${customDates.end}` : '',
        ].join('|');
        // Already cached and recent → skip.
        const cached = _analyticsSummaryCache.get(cacheKey);
        if (cached?.data && (Date.now() - cached.fetchedAt) < 5 * 60 * 1000) return;
        const matchedIds = _resolveCompanyMemberIds(company);
        if (!matchedIds) return;
        try {
          let url = `/analytics/summary?time_period=${timePeriod}&platform=${selectedPlatform}&member_user_ids=${matchedIds.join(',')}`;
          if (timePeriod === 'custom' && customDates.start && customDates.end) {
            url += `&start_date=${customDates.start}&end_date=${customDates.end}`;
          }
          const res = await authAxios.get(url);
          if (cancelled) return;
          _analyticsSummaryCache.set(cacheKey, { data: res.data, fetchedAt: Date.now() });
          _persistAnalyticsCache(); // survive full-page reloads
        } catch { /* prefetch failure is harmless */ }
      })();
    });
    Promise.all(tasks).catch(() => {});
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    isPostOnly,
    timePeriod,
    selectedPlatform,
    customDates.start,
    customDates.end,
    (filterOptions.companies || []).join('|'),
  ]);

  // Refs to current fetch fns so the auto-sync effect can call them
  // without depending on their identity (deps changing was re-firing the
  // effect every time a fetch finished — defeating autoSyncRanRef).
  const fetchAnalyticsRef = React.useRef(fetchAnalytics);
  const fetchDetailedPostsRef = React.useRef(fetchDetailedPosts);
  React.useEffect(() => { fetchAnalyticsRef.current = fetchAnalytics; }, [fetchAnalytics]);
  React.useEffect(() => { fetchDetailedPostsRef.current = fetchDetailedPosts; }, [fetchDetailedPosts]);

  const autoSyncRanRef = React.useRef(false);
  useEffect(() => {
    if (autoSyncRanRef.current) return;
    if (!data) return;
    const hasSynced = !!data?.summary?.last_sync;
    if (!hasSynced) {
      autoSyncRanRef.current = true;
      (async () => {
        try {
          setSyncing(true);
          await authAxios.post(`/analytics/sync`);
          await fetchAnalyticsRef.current();
          await fetchDetailedPostsRef.current();
        } catch (err) {
          console.error('Auto-sync failed:', err);
        } finally {
          setSyncing(false);
        }
      })();
    }
    // Intentionally NOT depending on fetchAnalytics / fetchDetailedPosts —
    // they're called via refs above. Including them retriggered this
    // effect every time the user changed a filter, which fired a redundant
    // POST /analytics/sync.
  }, [data, authAxios]);
  

  // Build a human-readable summary of the active filters so the Refresh
  // toast can tell the user exactly which slice they just loaded
  // (e.g. "30 days performance loaded · Instagram").
  const buildFilterSummary = () => {
    const periodMap = { '24h': 'Last 24 hours', '7d': 'Last 7 days', '30d': 'Last 30 days', 'custom': 'Custom range' };
    const periodLabel = periodMap[timePeriod] || timePeriod;
    const platformLabel = selectedPlatform === 'all'
      ? null
      : (selectedPlatform === 'twitter' ? 'X' : selectedPlatform.charAt(0).toUpperCase() + selectedPlatform.slice(1));
    const bits = [periodLabel];
    if (platformLabel) bits.push(platformLabel);
    return `${bits.join(' · ')} loaded`;
  };

  const handleSync = async () => {
    // Campaign Performance page only renders the posts table — calling
    // /analytics/sync (full social-media follower / metrics sync, 30+ s)
    // is overkill and leaves the button stuck in "Syncing…" state while
    // the user just wanted to refresh the table. On this page, treat
    // Refresh as a quick re-fetch of /analytics/posts.
    if (isPostOnly) {
      // SHORT-CIRCUIT: if the table was loaded within the last 30 s, skip
      // the network re-fetch and just show an instant "already up to date"
      // toast. Refresh mashing on already-loaded data felt slow because
      // the backend round-trip ran every click.
      const sinceLast = Date.now() - lastPostsFetchAtRef.current;
      if (sinceLast < FRESH_WINDOW_MS && posts && posts.length > 0) {
        const _summary = buildFilterSummary();
        setRefreshToast(`${_summary} (already up to date)`);
        return;
      }
      try {
        setSyncing(true);
        await fetchDetailedPosts();
        lastPostsFetchAtRef.current = Date.now();
        const _summary = buildFilterSummary();
        console.log('[Refresh toast]', _summary);
        setRefreshToast(_summary);
      } catch (err) {
        console.error("Refresh failed:", err);
        setRefreshToast('Refresh failed — please try again');
      } finally {
        setSyncing(false);
      }
      return;
    }
    // Fast path FIRST: re-pull the already-synced data so the user gets an
    // instant refresh. The button only spins for this quick round-trip.
    try {
      setSyncing(true);
      await fetchAnalytics();
      await fetchDetailedPosts();
      lastPostsFetchAtRef.current = Date.now();
      setRefreshToast(buildFilterSummary());
    } catch (err) {
      console.error("Refresh failed:", err);
      setRefreshToast('Refresh failed — please try again');
    } finally {
      // Clear the spinner now — do NOT keep the button stuck on the slow
      // platform sync below.
      setSyncing(false);
    }

    // Heavy platform sync (30s+) runs in the BACKGROUND, and only when the
    // data is actually stale. When it finishes it silently refreshes the
    // numbers. The user never waits on it.
    const sinceSync = Date.now() - lastSyncAtRef.current;
    if (sinceSync >= SYNC_FRESH_WINDOW_MS) {
      lastSyncAtRef.current = Date.now();
      authAxios.post(`/analytics/sync`)
        .then(() => { fetchAnalytics(); })
        .catch((err) => { console.error("Background sync failed:", err); });
    }
  };

  // Auto-dismiss the toast after 3 seconds so it never sticks around.
  useEffect(() => {
    if (!refreshToast) return;
    const t = setTimeout(() => setRefreshToast(null), 3000);
    return () => clearTimeout(t);
  }, [refreshToast]);

  // Auto-fire the toast whenever a filter that triggers a data reload changes
  // (period / platform / custom date range). Skips the very first render so
  // the toast doesn't pop on page load. Brief debounce so rapid clicks on
  // 24h → 7d → 30d don't queue multiple toasts.
  const _filterToastFirstRun = useRef(true);
  useEffect(() => {
    if (_filterToastFirstRun.current) {
      _filterToastFirstRun.current = false;
      return;
    }
    // For the Custom period, only fire the toast once BOTH dates are picked.
    // Clicking "Custom" alone (no range chosen yet) shouldn't pop the toast.
    if (timePeriod === 'custom' && (!customDates.start || !customDates.end)) {
      return;
    }
    const summary = buildFilterSummary();
    const t = setTimeout(() => setRefreshToast(summary), 200);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timePeriod, selectedPlatform, customDates.start, customDates.end]);

  // Track when posts auto-reload (via filter change) completes — this stamps
  // the fresh-window timer so the next Refresh click gets the instant
  // "already up to date" feedback if it happens quickly.
  useEffect(() => {
    if (posts && posts.length > 0) {
      lastPostsFetchAtRef.current = Date.now();
    }
  }, [posts]);

  const handleExport = async () => {
    if (!posts || posts.length === 0) return;
    const doc = new jsPDF();

    // Helper to sanitize text for PDF
    const cleanText = (text) => {
      if (!text) return "";
      // Remove emojis and non-printable characters that break jspdf standard fonts
      return String(text).replace(/[^\x20-\x7E\n\r\t]/g, "");
    };

    // Helper to add chart image to PDF
    const addChartToDoc = async (id, title, x, y, w, h) => {
      const element = document.getElementById(id);
      if (!element) return y;
      try {
        const canvas = await html2canvas(element, { 
          scale: 2, 
          useCORS: true,
          logging: false 
        });
        const imgData = canvas.toDataURL('image/png');
        doc.setFontSize(10);
        doc.setTextColor(100, 116, 139);
        doc.setFont("helvetica", "bold");
        doc.text(title, x, y - 2);
        doc.addImage(imgData, 'PNG', x, y, w, h);
        return y + h + 15;
      } catch (err) {
        console.error(`Error capturing ${id}:`, err);
        return y;
      }
    };

    // Header
    doc.setFontSize(22);
    doc.setTextColor(255, 107, 53); // Pipelyt Orange
    doc.setFont("helvetica", "bold");
    doc.text("Analytics Report", 14, 22);

    doc.setFontSize(10);
    doc.setTextColor(100, 116, 139); // Slate-500
    doc.setFont("helvetica", "normal");
    const dateStr = new Date().toLocaleString(undefined, { 
      timeZone: user?.timezone || 'UTC',
      timeZoneName: 'short' 
    });
    doc.text(`${user?.company_name || 'Organization'} • Digital Ecosystem Performance`, 14, 28);
    doc.text(`Generated on: ${dateStr}`, 14, 33);

    // KPI Summary Section
    doc.setFontSize(12);
    doc.setTextColor(30, 41, 59); // Slate-800
    doc.setFont("helvetica", "bold");
    doc.text("Business Ecosystem Summary", 14, 50);

    const kpiRows = [
      ['Total Followers', summary.total_followers.toLocaleString(), summary.follower_change || '0%'],
    ];

    // Add individual platform followers
    if (summary.platform_split) {
      summary.platform_split.forEach(p => {
        if (p) {
          const name = p.name || 'Unknown';
          const val = (p.value !== undefined) ? p.value : (p.count || 0);
          const chg = p.change || '';
          kpiRows.push([`   • ${name}`, val.toLocaleString(), chg]);
        }
      });
    }

    kpiRows.push(['Total Engagement', summary.total_engagement.toLocaleString(), summary.engagement_change || '0%']);
    kpiRows.push(['Total Reach', summary.total_reach.toLocaleString(), summary.reach_change || '0%']);
    kpiRows.push(['Avg. Engagement Rate', summary.engagement_rate || '0%', summary.engagement_rate_change || '0%']);
    
    const scoreVal = summary.sentiment_overall || 0;
    kpiRows.push(['Brand Sentiment Score', `${scoreVal}%`, scoreVal > 85 ? 'Positive' : scoreVal < 50 ? 'Negative' : 'Neutral']);

    autoTable(doc, {
      startY: 55,
      head: [['Metric', 'Value', 'Growth']],
      body: kpiRows,
      theme: 'plain',
      styles: { fontSize: 9, cellPadding: 3 },
      headStyles: { fontStyle: 'bold', textColor: [100, 116, 139] },
      columnStyles: {
        0: { fontStyle: 'bold', cellWidth: 80 },
        2: { textColor: [22, 163, 74] } // Green-600
      }
    });

    // Page 2: Primary Visualizations
    doc.addPage();
    doc.setFontSize(14);
    doc.setTextColor(30, 41, 59);
    doc.text("Platform Trends & Performance", 14, 20);
    
    let nextY = 30;
    // 1) Engagement, Reach, Followers (Line Chart)
    nextY = await addChartToDoc('export-chart-line', 'Performance Trends (Engagement & Audience)', 14, nextY, 182, 110);
    
    // 2) Platform Performance (Bars)
    if (selectedPlatform === 'all') {
      if (nextY > 180) { doc.addPage(); nextY = 20; }
      nextY = await addChartToDoc('export-performance-bars', 'Platform Comparison Share', 14, nextY, 182, 110);
    }

    // 3) Audience Distribution (Exclusive Page for clarity)
    if (selectedPlatform === 'all') {
      doc.addPage();
      doc.setFontSize(14);
      doc.setTextColor(30, 41, 59);
      doc.text("Audience Segmentation", 14, 20);
      // Increased height for solitary Audience chart
      await addChartToDoc('export-chart-pie', 'Audience Distribution Breakdown', 14, 30, 182, 150);
    }

    // Page 4: Top Performing Content
    doc.addPage();
    doc.setFontSize(14);
    doc.setTextColor(30, 41, 59);
    doc.text("Top Performing Content", 14, 20);
    
    const topPosts = [...posts]
      .sort((a, b) => (b.engagement || 0) - (a.engagement || 0))
      .slice(0, 3);

    const topColumns = ["Rank", "Platform", "Content", "Eng.", "Reach", "Likes"];
    const topRows = topPosts.map((post, index) => {
      let content = "(Media Post)";
      if (post.content) {
        try {
          const parsed = typeof post.content === 'string' && post.content.trim().startsWith('{') ? JSON.parse(post.content) : null;
          content = parsed ? (parsed.caption || parsed.text || Object.values(parsed).find(v => typeof v === 'string') || post.content) : post.content;
        } catch { content = post.content; }
      }
      const cleaned = cleanText(content);
      return [
        `#${index + 1}`,
        post.platform.toUpperCase(),
        cleaned.length > 80 ? cleaned.substring(0, 77) + "..." : cleaned,
        post.engagement || 0,
        post.reach || 0,
        post.likes || 0
      ];
    });

    autoTable(doc, {
      head: [topColumns],
      body: topRows,
      startY: 30,
      styles: { fontSize: 9, cellPadding: 5, font: "helvetica" },
      headStyles: { fillColor: [30, 41, 59], textColor: [255, 255, 255], fontStyle: 'bold' },
      columnStyles: {
        0: { fontStyle: 'bold', cellWidth: 15 },
        2: { cellWidth: 85 },
      }
    });

    // Detailed Post Analysis used to be appended here as a final page,
    // but the per-post performance table now lives on its own dedicated
    // tab (Campaign Performance) — exporting it again from the
    // Analytics Dashboard duplicated content the user can already see
    // via the Campaign Performance page's own export. The Analytics
    // PDF is now scoped to: KPI ribbon, Performance Trends chart,
    // Audience Distribution chart, Top 3 Posts, and Platform
    // Performance breakdown.
    const finalY = doc.lastAutoTable?.finalY || 200;
    doc.setFontSize(8);
    doc.setTextColor(148, 163, 184);
    doc.text("Confidential Analytics Report • Powered by Pipelyt", 14, finalY + 10);

    doc.save(`pipelyt_analytics_${new Date().toISOString().split('T')[0]}.pdf`);
  };

  const handleSentimentAnalysis = async (post, forceRefresh = false) => {
    const cacheKey = `${post.platform}_${post.native_id}`;

    // Standard guard check
    const commentCount = post.comments || 0;
    if (commentCount < 5) {
      setSentimentError(`Analysis requires at least 5 comments (found ${commentCount}).`);
      setSentimentPost(post);
      setSentimentData(null);
      return;
    }

    // Check cache first if not explicitly refreshing
    if (!forceRefresh && sentimentCache[cacheKey]) {
      setSentimentPost(post);
      setSentimentData(sentimentCache[cacheKey]);
      setSentimentError(null);
      return;
    }

    setSentimentPost(post);
    setSentimentData(null);
    setSentimentError(null);
    setIsAnalyzingSentiment(true);

    try {
      const res = await authAxios.get(`/analytics/post/${post.platform}/${post.native_id}/sentiment`);
      if (res.data.status === 'success') {
        const results = res.data.data;
        setSentimentData(results);
        // Save to cache
        setSentimentCache(prev => ({ ...prev, [cacheKey]: results }));
      } else {
        setSentimentError(res.data.message || "Failed to analyze sentiment.");
      }
    } catch (err) {
      setSentimentError(err.response?.data?.detail || "AI analysis failed. Please try again later.");
    } finally {
      setIsAnalyzingSentiment(false);
    }
  };

  // Dynamic KPI Switching Logic - MOVED UP TO FOLLOW RULES OF HOOKS
  const displaySummary = React.useMemo(() => {
    // Check if we have aggregated stats for the selected platform (e.g. 'linkedin')
    const platKey = selectedPlatform?.toLowerCase();
    if (platKey === 'all' || !data?.aggregated_platforms?.[platKey]) {
      return summary;
    }
    
    const pData = data.aggregated_platforms[platKey];
    return {
      total_followers: pData.total_followers,
      follower_change: pData.follower_change,
      total_engagement: pData.total_engagement,
      engagement_change: pData.engagement_change,
      total_reach: pData.total_reach,
      reach_change: pData.reach_change,
      engagement_rate: pData.engagement_rate,
      engagement_rate_change: pData.engagement_rate_change
    };
  }, [selectedPlatform, summary, data]);

  if (loading && !data) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-[#F55600]"></div>
      </div>
    );
  }

  // A-15: replaced full-page spinner with local overlays for better UX
  const isRefreshing = loading && data;


  return (
    <div className="px-4 pt-0 pb-8 space-y-4 xl:space-y-5 animate-in fade-in duration-500">
      {/* Refresh toast — portalled to document.body so it lives OUTSIDE the
          flex space-y container (otherwise the next sibling header would
          shift 16px when the toast appears). Positioned top-center so it
          doesn't overlap the Refresh button at top-right. */}
      {refreshToast && typeof document !== 'undefined' && createPortal(
        <div
          className={`fixed pointer-events-none bottom-4 right-4 sm:bottom-auto sm:right-auto sm:-translate-x-1/2 ${timePeriod === 'custom' ? 'sm:left-[52%] sm:top-[104px]' : 'sm:left-[40%] sm:top-4'}`}
          style={{ zIndex: 99999 }}
        >
          <div className="pointer-events-auto inline-flex items-center gap-2 bg-white border-2 border-[#10B981]/45 shadow-[0_18px_40px_rgba(43,41,38,0.22)] rounded-full pl-3 pr-2 py-1.5">
            <span className="relative flex items-center justify-center w-1.5 h-1.5">
              <span className="absolute inline-flex h-full w-full rounded-full bg-[#10B981] opacity-60 animate-ping" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-[#10B981]" />
            </span>
            <span className="text-[11px] font-bold text-[#2B2926] whitespace-nowrap">{refreshToast}</span>
            <button
              type="button"
              onClick={() => setRefreshToast(null)}
              className="ml-1 inline-flex items-center justify-center w-5 h-5 rounded-full text-[#2B2926]/45 hover:text-[#2B2926] hover:bg-[#2B2926]/[0.05] transition-colors"
              aria-label="Dismiss"
            >
              <FiX size={12} strokeWidth={2.4} />
            </button>
          </div>
        </div>,
        document.body
      )}

      {/* Header & Filter — title swaps to "Campaign Performance" when this
          page is rendered as the Campaign Performance sidebar tab. New
          "Aurora Glass" reference: no underline border, larger title,
          dark-pill sync badge with mint dot. */}
      <div className="flex flex-col xl:flex-row xl:items-start justify-between gap-5">
        <div>
          <h1
            className="font-semibold tracking-[-0.02em] leading-none text-[clamp(22px,2.6vw,30px)]"
            style={{ fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
          >
            {isPostOnly
              ? <><span className="text-[#2B2926]">Campaign</span> <span style={{ color: '#F55600' }}>Performance</span></>
              : <><span className="text-[#2B2926]">Analytics</span> <span style={{ color: '#F55600' }}>Dashboard</span></>
            }
          </h1>
          <div className="flex items-center gap-3 mt-3 flex-wrap">
            <p className="text-[#2B2926] text-[14px] font-medium">Performance insights across your digital ecosystem</p>
            {data?.summary?.last_sync && (
              <span
                className="inline-flex items-center gap-1.5 text-[11px] px-2.5 py-1 rounded-lg font-bold uppercase tracking-[0.05em]"
                style={{
                  background: '#0e1116',
                  color: '#ffffff',
                  fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"',
                }}
              >
                Sync: {new Date(data.summary.last_sync).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </span>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 w-full md:w-auto">
          {/* Platform Filter Dropdown */}
          <div className="relative flex-1 sm:flex-none sm:min-w-[100px]">
              <button
                onClick={() => setIsPlatformMenuOpen(!isPlatformMenuOpen)}
                className="w-full inline-flex items-center justify-between gap-1.5 px-2.5 h-9 bg-white border rounded-lg text-[12px] font-semibold text-[#2B2926] hover:border-[#2B2926]/40 transition-colors"
                style={{ borderColor: 'rgba(0,0,0,0.10)' }}
              >
              <div className="flex items-center gap-2 min-w-0">
                <div className="p-1 bg-slate-50 rounded-md shrink-0">
                  {selectedPlatform === 'all'
                    ? <FiActivity className="w-3 h-3" />
                    : <PlatformIcon platform={selectedPlatform} size={12} />}
                </div>
                <span
                  className="capitalize tracking-widest truncate"
                  style={{ color: '#2B2926', fontSize: '12px', fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
                >
                  {selectedPlatform === 'all' ? 'All' : (selectedPlatform === 'twitter' ? 'X' : selectedPlatform)}
                </span>
              </div>
              <FiChevronDown className={`w-3.5 h-3.5 shrink-0 transition-transform ${isPlatformMenuOpen ? 'rotate-180' : ''}`} />
            </button>

            {isPlatformMenuOpen && (
              <>
                <div
                  className="fixed inset-0 z-[90]"
                  onClick={() => setIsPlatformMenuOpen(false)}
                />
                <div className="absolute top-full left-0 mt-2 w-48 bg-white border border-[#2B2926]/30 rounded-2xl shadow-2xl z-[100] py-2 animate-in fade-in zoom-in-95 duration-200">
                  {['all', 'linkedin', 'twitter', 'facebook', 'instagram', 'youtube', 'tiktok'].map((p) => (
                    <button
                      key={p}
                      onClick={() => {
                        setSelectedPlatform(p);
                        setIsPlatformMenuOpen(false);
                      }}
                      className={`w-full flex items-center gap-3 px-4 py-2.5 text-[10px] font-semibold capitalize tracking-widest transition-all ${
                        selectedPlatform === p
                          ? 'bg-orange-50 text-[#F55600]'
                          : 'text-[#2B2926] hover:bg-slate-50 hover:text-[#F55600]'
                      }`}
                    >
                      <div className={`p-1.5 rounded-lg ${selectedPlatform === p ? 'bg-white' : 'bg-slate-50'}`}>
                        {p === 'all'
                          ? <FiActivity className="w-3 h-3" />
                          : <PlatformIcon platform={p} size={12} />}
                      </div>
                      {p === 'twitter' ? 'X' : p}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>

          <div className="h-6 w-px bg-slate-200 hidden lg:block mx-1" />

          {/* Period Selector — Aurora Glass segmented control: grey-tinted
              track, orange-fill active pill, restored 24h / 7d / 30d /
              Custom options. */}
          <div
            className="inline-flex gap-0.5 p-0.5 rounded-lg border flex-1 sm:flex-none"
            style={{ background: 'rgba(0,0,0,0.04)', borderColor: 'rgba(0,0,0,0.08)' }}
          >
            {[
              { key: '24h',    label: '24h'    },
              { key: '7d',     label: '7d'     },
              { key: '30d',    label: '30d'    },
              { key: 'custom', label: 'Custom' },
            ].map((r) => (
              <button
                key={r.key}
                onClick={() => setTimePeriod(r.key)}
                className={`flex-1 sm:flex-none px-1.5 sm:px-2.5 py-1.5 rounded-md text-[11px] font-semibold transition-all ${
                  timePeriod === r.key
                    ? 'bg-[#F55600] text-white shadow-sm'
                    : 'text-[#2B2926] hover:text-[#2B2926]'
                }`}
              >
                {r.label}
              </button>
            ))}
          </div>

          {/* Custom Date Inputs — inline FROM/TO labels INSIDE the input
              wrapper (no absolute -top labels) so the parent flex row never
              clips them and the controls stay compact. */}
          {timePeriod === 'custom' && (
            <div className="flex flex-col sm:flex-row sm:flex-wrap sm:items-center gap-2 w-full sm:w-auto animate-in zoom-in-95 duration-300">
              <div className="flex items-center gap-1.5 w-full sm:w-auto">
                <span className="text-[9px] font-bold text-[#2B2926] uppercase tracking-[0.14em] shrink-0 w-8 sm:w-auto">From</span>
                <div className="flex-1 sm:flex-none sm:w-[140px]">
                  <CalendarPicker
                    value={customDates.start || ''}
                    onChange={(d) => setCustomDates(prev => ({ ...prev, start: d }))}
                    placeholder="Pick date"
                  />
                </div>
              </div>
              <div className="flex items-center gap-1.5 w-full sm:w-auto">
                <span className="text-[9px] font-bold text-[#2B2926] uppercase tracking-[0.14em] shrink-0 w-8 sm:w-auto">To</span>
                <div className="flex-1 sm:flex-none sm:w-[140px]">
                  <CalendarPicker
                    value={customDates.end || ''}
                    onChange={(d) => setCustomDates(prev => ({ ...prev, end: d }))}
                    placeholder="Pick date"
                    minDate={customDates.start || undefined}
                  />
                </div>
              </div>
            </div>
          )}

          <div className="h-6 w-px bg-gray-100 mx-1 hidden lg:block" />

          {/* Unified cascading Brand Filter — admin only.
              Sections: Companies → Brands (narrowed by companies) →
              Members (narrowed by companies+brands) → Location
              (narrowed by the cascaded member set). Every downstream
              dropdown shrinks as upstream selections are made, so no
              option ever produces zero results. Single dropdown replaces
              the old "Company" + "Filters" buttons. */}
          {/* Always render for admins so the button is reachable even when
              filter-options hasn't loaded yet (plan gating / auth hiccups).
              Inside the panel we show inline "no data" messages for empty
              sections so admins can diagnose at a glance. */}
          {!isMember && (
            <div className="relative flex-1 sm:flex-none">
              <button
                ref={filterBtnRef}
                type="button"
                onClick={() => {
                  const next = !filterOpen;
                  setFilterOpen(next);
                  // Refresh the brand + member + company lists on open so
                  // changes made elsewhere (Settings → Business DNA, Team
                  // page) show up without a page reload.
                  if (next) refreshFilterOptions();
                }}
                style={{ WebkitTapHighlightColor: 'transparent', ...(activeFilterCount === 0 ? { borderColor: 'rgba(43,41,38,0.20)' } : {}) }}
                className={`w-full inline-flex items-center justify-center sm:justify-start gap-2 bg-white border rounded-lg px-3 py-2 outline-none focus:outline-none focus-visible:outline-none focus:ring-0 whitespace-nowrap transition-all hover:border-[#2B2926]/40 ${activeFilterCount > 0 ? 'border-[#F55600]' : ''}`}
              >
                <span className="uppercase tracking-[0.08em] text-[10px] font-semibold text-[#2B2926]">Brand Filter</span>
                <span className="text-[12px] font-semibold" style={{ color: '#F55600' }}>
                  {activeFilterCount === 0 ? 'All' : (
                    [
                      sel.brandIds.length && `${sel.brandIds.length} brand${sel.brandIds.length > 1 ? 's' : ''}`,
                      sel.companies.length && `${sel.companies.length} co.`,
                      sel.memberIds.length && `${sel.memberIds.length} mem.`,
                      (sel.country || sel.state || sel.city || sel.pin_code) && 'loc',
                    ].filter(Boolean).join(' · ')
                  )}
                </span>
                <FiChevronDown size={13} strokeWidth={2} className="text-[#F55600]" />
              </button>
              {filterOpen && filterMenuRect && createPortal((
                <>
                  <div className="fixed inset-0 z-[1900]" onClick={() => setFilterOpen(false)} />
                  {/* Portalled to document.body: mobile = viewport-centred
                      card, desktop = anchored under the Brand Filter button. */}
                  <div
                    style={filterMenuRect.mobile
                      ? { position: 'fixed', left: '50%', top: filterMenuRect.top, transform: 'translateX(-50%)', width: 'calc(100vw - 24px)', maxWidth: 336, maxHeight: '78vh', zIndex: 1901, fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }
                      : { position: 'fixed', top: filterMenuRect.top, left: filterMenuRect.left, width: 336, maxHeight: 'min(74vh, 540px)', zIndex: 1901, fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
                    className="bg-white rounded-[20px] shadow-[0_28px_70px_rgba(43,36,64,0.22)] border border-white/85 overflow-y-auto overflow-x-hidden p-3.5">
                    {/* Companies — always rendered. Empty placeholder keeps
                        the layout stable and tells the admin *why* nothing
                        is pickable ("no company set on any member yet")
                        instead of the section silently disappearing. */}
                    {/* Companies + Business DNA + Team Members as proper
                        single-select dropdowns (BrandSelect) instead of
                        always-expanded checkbox lists. Picking "All"
                        clears that level and any deeper filters. */}
                    <CollapsibleSection title="Brand &amp; Team Details">
                      <div className="flex flex-col gap-3.5">
                        <div className="grid grid-cols-2 gap-3">
                          <div className="flex flex-col gap-1.5 min-w-0">
                            <span
                              className="uppercase tracking-[0.06em]"
                              style={{ color: '#2B2926', fontSize: '11px', fontWeight: 600, letterSpacing: '0.08em', fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
                            >
                              Companies
                            </span>
                            <BrandSelect
                              size="md"
                              value={sel.companies[0] || ''}
                              onChange={(v) => setSel((s) => ({
                                ...s,
                                companies: v ? [v] : [],
                                brandIds: [], memberIds: [],
                                country: '', state: '', city: '', pin_code: '',
                              }))}
                              options={[
                                { value: '', label: 'All' },
                                ...(filterOptions.companies || []).map((c) => ({ value: c, label: c })),
                              ]}
                              placeholder="All"
                            />
                          </div>
                          <div className="flex flex-col gap-1.5 min-w-0">
                            <span
                              className="uppercase tracking-[0.06em]"
                              style={{ color: '#2B2926', fontSize: '11px', fontWeight: 600, letterSpacing: '0.08em', fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
                            >
                              Business DNA
                            </span>
                            <BrandSelect
                              size="md"
                              value={sel.brandIds[0] || ''}
                              onChange={(v) => setSel((s) => ({
                                ...s,
                                brandIds: v ? [v] : [],
                                memberIds: [], country: '', state: '', city: '', pin_code: '',
                              }))}
                              options={[
                                { value: '', label: 'All' },
                                ...availableBrands.map((b) => ({ value: b.id, label: b.name })),
                              ]}
                              placeholder="All"
                            />
                          </div>
                        </div>
                        <div className="flex flex-col gap-1.5 min-w-0">
                          <span
                            className="uppercase tracking-[0.06em]"
                            style={{ color: '#2B2926', fontSize: '11px', fontWeight: 600, letterSpacing: '0.08em', fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
                          >
                            Team Members
                          </span>
                          <BrandSelect
                            size="md"
                            value={sel.memberIds[0] || ''}
                            onChange={(v) => setSel((s) => ({
                              ...s,
                              memberIds: v ? [v] : [],
                            }))}
                            options={[
                              { value: '', label: 'All' },
                              ...availableMembers.map((m) => ({ value: m.id, label: m.full_name || m.email })),
                            ]}
                            placeholder="All"
                          />
                        </div>
                      </div>
                    </CollapsibleSection>

                    <CollapsibleSection title="Regional Filters" defaultOpen={false}>
                      <div className="flex flex-col gap-3.5">
                        <div className="grid grid-cols-2 gap-3">
                          <div className="flex flex-col gap-1.5 min-w-0">
                            <span
                              className="uppercase tracking-[0.06em]"
                              style={{ color: '#2B2926', fontSize: '11px', fontWeight: 600, letterSpacing: '0.08em', fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
                            >
                              Country
                            </span>
                            <BrandSelect
                              value={sel.country}
                              onChange={(v) => setSel(s => ({ ...s, country: v, state: '', city: '', pin_code: '' }))}
                              options={[{ value: '', label: 'All' }, ...availableCountries.map(c => ({ value: c.code, label: c.name }))]}
                              placeholder="All"
                              size="md"
                            />
                          </div>
                          <div className="flex flex-col gap-1.5 min-w-0">
                            <span
                              className="uppercase tracking-[0.06em]"
                              style={{ color: '#2B2926', fontSize: '11px', fontWeight: 600, letterSpacing: '0.08em', fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
                            >
                              State
                            </span>
                            <BrandSelect
                              value={sel.state}
                              onChange={(v) => setSel(s => ({ ...s, state: v, city: '', pin_code: '' }))}
                              options={[{ value: '', label: 'All' }, ...availableStates.map(s => ({ value: s.code, label: s.name }))]}
                              placeholder="All"
                              size="md"
                            />
                          </div>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                          <div className="flex flex-col gap-1.5 min-w-0">
                            <span
                              className="uppercase tracking-[0.06em]"
                              style={{ color: '#2B2926', fontSize: '11px', fontWeight: 600, letterSpacing: '0.08em', fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
                            >
                              City
                            </span>
                            <BrandSelect
                              value={sel.city}
                              onChange={(v) => setSel(s => ({ ...s, city: v, pin_code: '' }))}
                              options={[{ value: '', label: 'All' }, ...availableCities.map(c => ({ value: c, label: c }))]}
                              placeholder="All"
                              size="md"
                            />
                          </div>
                          <div className="flex flex-col gap-1.5 min-w-0">
                            <span
                              className="uppercase tracking-[0.06em]"
                              style={{ color: '#2B2926', fontSize: '11px', fontWeight: 600, letterSpacing: '0.08em', fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
                            >
                              Pin / ZIP
                            </span>
                            <BrandSelect
                              value={sel.pin_code}
                              onChange={(v) => setSel(s => ({ ...s, pin_code: v }))}
                              options={[{ value: '', label: 'All' }, ...availablePins.map(p => ({ value: p, label: p }))]}
                              placeholder="All"
                              size="md"
                            />
                          </div>
                        </div>
                      </div>
                    </CollapsibleSection>

                    {activeFilterCount > 0 && (
                      <button
                        type="button"
                        onClick={() => setSel({
                          companies: [], brandIds: [], memberIds: [],
                          country: '', state: '', city: '', pin_code: '',
                        })}
                        className="w-full mt-3 py-2 uppercase tracking-[0.1em] text-[#2B2926] hover:text-[#F55600] hover:bg-[#F55600]/5 rounded-lg transition-colors"
                        style={{ fontSize: '11px', fontWeight: 700, fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
                      >
                        Clear all filters
                      </button>
                    )}
                  </div>
                </>
              ), document.body)}
            </div>
          )}

          <div className="h-6 w-px bg-gray-100 mx-1 hidden lg:block" />

          {/* Actions — Export (white outlined w/ download icon) + Refresh
              (solid orange CTA w/ refresh icon). "Aurora Glass" reference:
              both buttons are 11px-padded pill-rounded with leading icons.
              Export is hidden on the Campaign Performance page since the
              PDF export builds chart screenshots that don't exist there. */}
          <div className="flex items-center gap-2 flex-1 sm:flex-none shrink-0 ml-auto sm:ml-0">
            {!isPostOnly && (
              <button
                onClick={handleExport}
                disabled={!posts || posts.length === 0}
                className="flex-1 sm:flex-none inline-flex items-center justify-center gap-1.5 px-3 py-2 bg-white border rounded-lg font-semibold disabled:opacity-50 disabled:cursor-not-allowed transition-colors hover:border-[#2B2926]/40 active:scale-95"
                style={{ color: '#2B2926', fontSize: '12px', borderColor: 'rgba(43,41,38,0.20)' }}
              >
                <FiDownload size={13} strokeWidth={2} />
                Export
              </button>
            )}

            <button
              onClick={handleSync}
              disabled={syncing}
              className={`flex-1 sm:flex-none inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-[12px] font-semibold transition-colors active:scale-95 whitespace-nowrap ${
                syncing
                  ? 'bg-[#F55600]/80 text-white cursor-wait'
                  : 'bg-[#F55600] text-white hover:bg-[#e63e00]'
              }`}
            >
              <FiRefreshCw size={13} strokeWidth={2} className={syncing ? 'animate-spin' : ''} />
              {syncing ? (isPostOnly ? 'Refreshing…' : 'Syncing…') : 'Refresh'}
            </button>
          </div>
        </div>
      </div>

      {/* KPI Ribbon — clean 4-up grid without an outer container border.
          Each KpiCard is now a self-contained tile with its own soft shadow
          and rounded corners (Aurora-Glass reference design).
          Hidden in Campaign Performance mode (post-only view). */}
      {!isPostOnly && (
      <div>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 xl:gap-4 analytics-kpi-grid">
        <KpiCard
          title="Total Followers"
          value={displaySummary.total_followers.toLocaleString()}
          change={displaySummary.follower_change || "0.0%"}
          isUp={!(displaySummary.follower_change || "").includes('-')}
          icon={<FiUsers size={20} />}
          color="text-emerald-600"
          bg="bg-emerald-50"
          breakdown={selectedPlatform === 'all' ? summary.platform_split?.map((p, i) => ({
            id: i,
            name: p.name,
            type: p.name.split(' ')[0].toLowerCase(),
            value: p.value || 0,
            change: p.change || "0.00%"
          })) : null}
        />
        <KpiCard
          title="Total Engagement"
          value={displaySummary.total_engagement.toLocaleString()}
          change={displaySummary.engagement_change || "0.0%"}
          isUp={!(displaySummary.engagement_change || "").includes('-')}
          icon={<FiMessageCircle size={20} />}
          color="text-orange-600"
          bg="bg-orange-50"
        />
        <KpiCard
          title="Total Reach"
          value={displaySummary.total_reach.toLocaleString()}
          change={displaySummary.reach_change || "0.0%"}
          isUp={!(displaySummary.reach_change || "").includes('-')}
          icon={<FiEye size={20} />}
          color="text-purple-600"
          bg="bg-purple-50"
        />
        <KpiCard
          title="Avg. Engagement Rate"
          value={displaySummary.engagement_rate || `${(summary.total_followers > 0 ? (summary.total_engagement / summary.total_followers) * 100 : 0).toFixed(1)}%`}
          change={displaySummary.engagement_rate_change || "+0.0%"}
          isUp={!(displaySummary.engagement_rate_change || "").includes('-')}
          icon={<FiTrendingUp size={20} />}
          color="text-emerald-600"
          bg="bg-emerald-50"
        />
        </div>
      </div>
      )}

      {/* Charts Section — hidden in Campaign Performance mode */}
      {!isPostOnly && (
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-stretch">
        {/* Growth Chart */}
        <div
          id="export-chart-line"
          className="lg:col-span-2 bg-white px-5 py-4 rounded-[22px] border border-[#2B2926]/30 shadow-[0_12px_34px_rgba(17,17,17,0.05)] relative overflow-hidden"
        >          <div className="flex flex-col gap-4">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <div
                  className="w-10 h-10 rounded-xl grid place-items-center"
                  style={{ background: 'rgba(0,0,0,0.04)', color: '#F55600' }}
                >
                  <FiActivity className="w-5 h-5" />
                </div>
                <h3
                  className="font-bold text-[#2B2926] tracking-tight"
                  style={{ fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"', fontSize: '19px' }}
                >
                  Performance Trends &amp; Follower Growth
                </h3>
                {trendsLoading && <FiRefreshCw size={12} className="text-[#F55600] animate-spin" />}
              </div>
              {/* Per-graph company filter — sourced from the COMPANIES
                  list (admin's own company + each team member's company,
                  deduped) rather than from brand DNAs. Triggers a separate
                  /analytics/summary fetch narrowed by `member_user_ids` of
                  users whose company matches the picked option. */}
              {(filterOptions.companies || []).length > 0 && (
                <div className="relative shrink-0">
                  <button
                    type="button"
                    onClick={() => setTrendsPickerOpen((v) => !v)}
                    className="h-9 px-3 inline-flex items-center gap-2 bg-white border border-[#2B2926]/15 rounded-lg text-[11px] font-bold text-[#2B2926] hover:border-[#F55600]/50 transition-colors"
                  >
                    <span>{trendsCompanies.length === 0 ? 'All companies' : `${trendsCompanies.length} selected`}</span>
                    <FiChevronDown size={12} className="text-[#F55600]" />
                  </button>
                  {trendsPickerOpen && (
                    <>
                      <div className="fixed inset-0 z-[90]" onClick={() => setTrendsPickerOpen(false)} />
                      <div className="absolute right-0 mt-1 z-[100] bg-white border border-[#2B2926]/25 rounded-lg shadow-xl min-w-[200px] py-1">
                        <button
                          type="button"
                          onClick={() => setTrendsCompanies([])}
                          className={`w-full text-left text-[11px] px-3 py-1.5 font-bold transition-colors ${trendsCompanies.length === 0 ? 'bg-[#F55600] text-white' : 'text-[#2B2926] hover:bg-[#2B2926]/[0.05]'}`}
                        >
                          All companies
                        </button>
                        <div className="border-t border-[#2B2926]/20 my-1" />
                        {(filterOptions.companies || []).map((c) => {
                          // Empty array means "All companies", so show each
                          // checkbox as checked. Unchecking from the all-mode
                          // expands to the explicit list of OTHER companies.
                          const allMode = trendsCompanies.length === 0;
                          const checked = allMode || trendsCompanies.includes(c);
                          return (
                            <label
                              key={c}
                              className="flex items-center gap-2 px-3 py-1.5 cursor-pointer text-[11px] font-bold hover:bg-[#2B2926]/[0.05]"
                            >
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => setTrendsCompanies((prev) => {
                                  if (prev.length === 0) {
                                    // All-mode → unchecking one means "all except this"
                                    return (filterOptions.companies || []).filter((x) => x !== c);
                                  }
                                  return prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c];
                                })}
                                className="accent-[#F55600]"
                              />
                              <span className="truncate">{c}</span>
                            </label>
                          );
                        })}
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>
 
            {/* Graph Container */}
            <div className={`rounded-xl transition-opacity duration-300 ${isRefreshing ? 'opacity-50 pointer-events-none' : 'opacity-100'}`}>
              <div className="flex flex-col gap-6">
                {/* Dot Legend - Interactive. Colours match the Aurora-Glass
                    reference: Engagement (pink), Likes (rose), Comments
                    (teal), Shares (cyan), Reach (purple), Follower Growth %
                    (cyan). Click a chip to isolate that series. */}
                <div className="flex flex-wrap items-center gap-x-5 gap-y-2.5 text-[12px] font-semibold text-[#2B2926] pb-1">
                  {[
                    { id: 'engagement', label: 'Engagement', color: '#ec4899' },
                    { id: 'likes', label: 'Likes', color: '#f43f5e' },
                    { id: 'comments', label: 'Comments', color: '#14b8a6' },
                    { id: 'shares', label: 'Shares', color: '#22d3ee' },
                    { id: 'reach', label: 'Reach', color: '#7c3aed' },
                    { id: 'follower_change_pct', label: 'Follower Growth %', color: '#06b6d4' }
                  ].map((m) => (
                    <button
                      key={m.id}
                      onClick={() => setActiveMetric(prev => prev === m.id ? 'all' : m.id)}
                      className={`inline-flex items-center gap-2 text-left leading-tight transition-all duration-300 hover:scale-105 active:scale-95 ${
                        activeMetric !== 'all' && activeMetric !== m.id ? 'opacity-30 grayscale' : 'opacity-100'
                      }`}
                    >
                      <i className="w-2.5 h-2.5 rounded-full shrink-0 inline-block" style={{ background: m.color }} />
                      <span>{m.label}</span>
                    </button>
                  ))}
                </div>
 
                <div className="overflow-x-auto -mx-1 px-1">
                <div className="h-[300px] xl:h-[330px] 2xl:h-[310px] w-full min-w-[560px] sm:min-w-0 relative">

            {chartHistory.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%" key={`${timePeriod}-${selectedPlatform}`}>
                  <AreaChart
                    data={chartHistory}
                    margin={{ top: 10, right: 55, left: 30, bottom: 10 }}
                  >
                    <defs>
                      <linearGradient id="colorEng" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#ec4899" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#ec4899" stopOpacity={0.02} />
                      </linearGradient>
                      <linearGradient id="colorReach" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#7c3aed" stopOpacity={0.34} />
                        <stop offset="95%" stopColor="#7c3aed" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="rgba(17,17,17,0.07)" />
                    <XAxis
                      dataKey="date"
                      axisLine={false}
                      tickLine={false}
                      stroke="none"
                      minTickGap={timePeriod === '24h' ? 120 : 40}
                      tick={{ fill: '#2B2926', fontSize: 11, fontWeight: 500 }}
                      dy={15}
                      tickFormatter={(str) => {
                        const d = new Date(str);
                        if (timePeriod === '24h') {
                          return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
                        }
                        return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
                      }}
                    />
                    <YAxis
                      axisLine={false}
                      tickLine={false}
                      stroke="none"
                      tick={{ fill: '#2B2926', fontSize: 11, fontWeight: 500 }}
                      dx={-10}
                      width={64}
                      label={{
                        value: 'TOTAL REACH',
                        angle: -90,
                        position: 'insideLeft',
                        dx: -18,
                        offset: 0,
                        style: { fill: '#2B2926', fontSize: 9, fontWeight: 600, letterSpacing: '0.14em', textAnchor: 'middle' },
                      }}
                    />
                  <Tooltip
                    content={({ active, payload, label }) => {
                      if (active && payload && payload.length) {
                        const data = payload[0].payload;
                        // chartHistory replaces 0 metrics with null so recharts
                        // can `connectNulls` over failed-sync gaps. The tooltip
                        // therefore must coerce null → 0 before formatting, or
                        // it blows up with "Cannot read properties of null".
                        const nz = (v) => (typeof v === 'number' ? v : 0);
                        const hasBreakdown = (nz(data.likes)) + (nz(data.comments)) + (nz(data.shares)) > 0;
                        return (
                          <div className="bg-white p-4 rounded-2xl shadow-2xl border border-[#2B2926]/30 flex flex-col gap-2 min-w-[160px]">
                            <p className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest mb-1">{label}</p>
                            <div className="space-y-1.5">
                              <div className="flex items-center justify-between gap-4">
                                <span className="flex items-center gap-2 text-xs font-bold text-[#2B2926]">
                                  <div className="w-2 h-2 rounded-full" style={{ background: '#ec4899' }}></div> Engagement
                                </span>
                                <span className="text-xs font-semibold text-[#2B2926]">{nz(data.engagement).toLocaleString()}</span>
                              </div>
                              <div className="flex items-center justify-between gap-4">
                                <span className="flex items-center gap-2 text-xs font-bold text-[#2B2926]">
                                  <div className="w-2 h-2 rounded-full" style={{ background: '#7c3aed' }}></div> Reach
                                </span>
                                <span className="text-xs font-semibold text-[#2B2926]">{nz(data.reach).toLocaleString()}</span>
                              </div>
                              {data.followers !== undefined && (
                                <div className="flex items-center justify-between gap-4">
                                  <span className="flex items-center gap-2 text-xs font-bold text-[#2B2926]">
                                    <div className="w-2 h-2 rounded-full" style={{ background: '#06b6d4' }}></div> Followers
                                  </span>
                                  <span className="text-xs font-semibold text-[#2B2926] flex items-center gap-1.5">
                                    {nz(data.followers).toLocaleString()}
                                    {typeof data.follower_change_pct === 'number' && (
                                      <span className={`text-[9px] font-semibold px-1.5 py-0.5 rounded-md ${data.follower_change_pct >= 0 ? 'bg-emerald-50 text-emerald-600' : 'bg-red-50 text-red-500'}`}>
                                        {data.follower_change_pct >= 0 ? '+' : ''}{Number(data.follower_change_pct).toFixed(2)}%
                                      </span>
                                    )}
                                  </span>
                                </div>
                              )}
                              
                              {hasBreakdown && (
                                <div className="pt-2 mt-2 border-t border-slate-50 space-y-1.5 animate-in fade-in duration-300">
                                  <div className="flex items-center justify-between gap-4">
                                    <span className="flex items-center gap-2 text-[10px] font-bold text-[#2B2926]">
                                      <div className="w-1.5 h-1.5 rounded-full" style={{ background: '#f43f5e' }}></div> Likes
                                    </span>
                                    <span className="text-[10px] font-semibold text-[#2B2926]">{nz(data.likes).toLocaleString()}</span>
                                  </div>
                                  <div className="flex items-center justify-between gap-4">
                                    <span className="flex items-center gap-2 text-[10px] font-bold text-[#2B2926]">
                                      <div className="w-1.5 h-1.5 rounded-full" style={{ background: '#14b8a6' }}></div> Comments
                                    </span>
                                    <span className="text-[10px] font-semibold text-[#2B2926]">{nz(data.comments).toLocaleString()}</span>
                                  </div>
                                  <div className="flex items-center justify-between gap-4">
                                    <span className="flex items-center gap-2 text-[10px] font-bold text-[#2B2926]">
                                      <div className="w-1.5 h-1.5 rounded-full" style={{ background: '#22d3ee' }}></div> Shares
                                    </span>
                                    <span className="text-[10px] font-semibold text-[#2B2926]">{nz(data.shares).toLocaleString()}</span>
                                  </div>
                                </div>
                              )}
                            </div>
                          </div>
                        );
                      }
                      return null;
                    }}
                  />
                  {/* Series colours match the Aurora-Glass reference.
                      Engagement = pink, Likes = rose, Comments = teal,
                      Shares = cyan, Reach = purple (with gradient area
                      fill). Series stay individually toggleable via the
                      legend chips above. */}
                  <Area
                    connectNulls={true}
                    type="monotone"
                    dataKey="engagement"
                    stroke="#ec4899"
                    strokeWidth={2.4}
                    dot={false}
                    activeDot={{ r: 5, strokeWidth: 0 }}
                    hide={activeMetric !== 'all' && activeMetric !== 'engagement'}
                    fillOpacity={0}
                  />
                  <Area
                    connectNulls={true}
                    type="monotone"
                    dataKey="likes"
                    stroke="#f43f5e"
                    strokeWidth={2}
                    dot={false}
                    activeDot={{ r: 5, strokeWidth: 0 }}
                    hide={activeMetric !== 'all' && activeMetric !== 'likes'}
                    fillOpacity={0}
                  />
                  <Area
                    connectNulls={true}
                    type="monotone"
                    dataKey="comments"
                    stroke="#14b8a6"
                    strokeWidth={2}
                    dot={false}
                    activeDot={{ r: 5, strokeWidth: 0 }}
                    hide={activeMetric !== 'all' && activeMetric !== 'comments'}
                    fillOpacity={0}
                  />
                  <Area
                    connectNulls={true}
                    type="monotone"
                    dataKey="shares"
                    stroke="#22d3ee"
                    strokeWidth={2}
                    dot={false}
                    activeDot={{ r: 5, strokeWidth: 0 }}
                    hide={activeMetric !== 'all' && activeMetric !== 'shares'}
                    fillOpacity={0}
                  />
                  <Area
                    connectNulls={true}
                    type="monotone"
                    dataKey="reach"
                    stroke="#7c3aed"
                    strokeWidth={3}
                    dot={{ r: 3, strokeWidth: 0, fill: '#7c3aed' }}
                    activeDot={{ r: 5, strokeWidth: 0 }}
                    hide={activeMetric !== 'all' && activeMetric !== 'reach'}
                    fillOpacity={1}
                    fill="url(#colorReach)"
                  />
                  {/* Followers line — now plotted as % growth from the
                      window baseline (not raw count). Raw counts made the
                      line look flat (12,390 → 12,408 is invisible on a
                      0–14k axis); the % view shows growth/decline clearly
                      even for tiny absolute movements. */}
                  <YAxis
                    yAxisId="right"
                    orientation="right"
                    axisLine={false}
                    tickLine={false}
                    stroke="none"
                    domain={[0, 'auto']}
                    ticks={rightAxisTicks}
                    hide={activeMetric !== 'all' && activeMetric !== 'follower_change_pct'}
                    tick={{ fill: '#06b6d4', fontSize: 11, fontWeight: 500 }}
                    dx={10}
                    width={68}
                    tickFormatter={(n) => parseFloat(n).toFixed(2)}
                    label={{
                      value: 'FOLLOWER KPI %',
                      angle: 90,
                      position: 'insideRight',
                      dx: 22,
                      offset: 0,
                      style: { fill: '#06b6d4', fontSize: 9, fontWeight: 600, letterSpacing: '0.14em', textAnchor: 'middle' },
                    }}
                  />
                  <Area
                    yAxisId="right"
                    connectNulls={true}
                    type="monotone"
                    dataKey="follower_change_pct"
                    stroke="#06b6d4"
                    strokeWidth={2.6}
                    dot={false}
                    activeDot={{ r: 5, strokeWidth: 0 }}
                    hide={activeMetric !== 'all' && activeMetric !== 'follower_change_pct'}
                    fillOpacity={0}
                  />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <div className="absolute inset-0 flex flex-col items-center justify-center text-center p-12 bg-slate-50/50 rounded-2xl border-2 border-dashed border-slate-300">
                <FiRefreshCw className="w-12 h-12 text-slate-200 mb-4" />
                <h4 className="font-bold text-[#2B2926]">Waiting for Sync Data</h4>
                <p className="text-sm text-[#2B2926] max-w-[200px]">Historical data will appear here after your accounts are synchronized.</p>
              </div>
            )}
            </div>
            </div>
          </div>
        </div>
      </div>
    </div>

        {/* Platform Split — pie chart and breakdown list compacted so users
            with many connected platforms / brands can see most of them
            without scrolling. Aurora-Glass card styling: soft shadow,
            black/[0.07] border, no orange outline. */}
        <div
          id="export-chart-pie"
          className="bg-white px-5 py-4 rounded-[22px] border border-[#2B2926]/30 shadow-[0_12px_34px_rgba(17,17,17,0.05)] flex flex-col"
        >
          <div className="flex items-center justify-between mb-2 gap-2">
            <div className="flex items-center gap-3">
              <div
                className="w-10 h-10 rounded-xl grid place-items-center"
                style={{ background: 'rgba(0,0,0,0.04)', color: '#F55600' }}
              >
                <FiPieChart className="w-5 h-5" />
              </div>
              <h3
                className="font-bold text-[#2B2926] tracking-tight"
                style={{ fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"', fontSize: '19px' }}
              >
                Audience Distribution
              </h3>
            </div>
            {/* Per-graph company filter — matches the Performance Trends
                dropdown so both cards offer the same options
                ("All companies" + admin's own + each team member's). */}
            {(filterOptions.companies || []).length > 0 && (
              <div className="relative shrink-0">
                <button
                  type="button"
                  onClick={() => setAudiencePickerOpen((v) => !v)}
                  className="h-9 px-3 inline-flex items-center gap-2 bg-white border border-[#2B2926]/15 rounded-lg text-[11px] font-bold text-[#2B2926] hover:border-[#F55600]/50 transition-colors"
                >
                  <span>{audienceSel.length === 0 ? 'All companies' : `${audienceSel.length} selected`}</span>
                  <FiChevronDown size={12} className="text-[#F55600]" />
                </button>
                {audiencePickerOpen && (
                  <>
                    <div className="fixed inset-0 z-[90]" onClick={() => setAudiencePickerOpen(false)} />
                    <div className="absolute right-0 mt-1 z-[100] bg-white border border-[#2B2926]/25 rounded-lg shadow-xl min-w-[200px] py-1">
                      <button
                        type="button"
                        onClick={() => setAudienceSel([])}
                        className={`w-full text-left text-[11px] px-3 py-1.5 font-bold transition-colors ${audienceSel.length === 0 ? 'bg-[#F55600] text-white' : 'text-[#2B2926] hover:bg-[#2B2926]/[0.05]'}`}
                      >
                        All companies
                      </button>
                      <div className="border-t border-[#2B2926]/20 my-1" />
                      {(filterOptions.companies || []).map((c) => {
                        const allMode = audienceSel.length === 0;
                        const checked = allMode || audienceSel.includes(c);
                        return (
                          <label
                            key={c}
                            className="flex items-center gap-2 px-3 py-1.5 cursor-pointer text-[11px] font-bold hover:bg-[#2B2926]/[0.05]"
                          >
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => setAudienceSel((prev) => {
                                if (prev.length === 0) {
                                  return (filterOptions.companies || []).filter((x) => x !== c);
                                }
                                return prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c];
                              })}
                              className="accent-[#F55600]"
                            />
                            <span className="truncate">{c}</span>
                          </label>
                        );
                      })}
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
          {/* Donut chart — raw SVG (not recharts) so the size is exactly
              what we specify with NO ResponsiveContainer quirks. Each
              platform is drawn as a stroke-dasharray arc on a circle.
              The center label is rendered as a sibling overlay div with
              absolute + translate centering — guaranteed dead-center. */}
          <div className="relative flex items-center justify-center mt-0 mb-2">
            <div
              className="donut-chart-box relative"
              style={{ width: '190px', height: '190px', flexShrink: 0 }}
            >
              {filteredAudienceTotal > 0 ? (() => {
                // viewBox is 200×200, circle centered at (100,100) with
                // radius 72 — same as the HTML reference. circumference
                // = 2πr ≈ 452.39. Each arc's stroke-dasharray = arcLen +
                // (circumference − arcLen). stroke-dashoffset slides each
                // arc to the right starting angle.
                const radius = 72;
                const circumference = 2 * Math.PI * radius;
                let cumulative = 0;
                return (
                  <svg viewBox="0 0 200 200" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg">
                    {filteredPlatformSplit.map((entry, index) => {
                      const isSelected = selectedPlatform === 'all' || getPlatformKey(entry.name) === selectedPlatform;
                      const fill = entry._color || getPlatformColor(entry.name);
                      const stroke = isSelected ? fill : '#E2E8F0';
                      const share = entry.value / filteredAudienceTotal;
                      const arcLen = Math.max(0.5, share * circumference - 2); // -2 = small padding gap
                      const gap = circumference - arcLen;
                      const offset = -cumulative;
                      cumulative += share * circumference;
                      return (
                        <circle
                          key={`arc-${index}`}
                          cx="100"
                          cy="100"
                          r={radius}
                          fill="none"
                          stroke={stroke}
                          strokeWidth="26"
                          strokeDasharray={`${arcLen} ${gap}`}
                          strokeDashoffset={offset}
                          transform="rotate(-90 100 100)"
                          opacity={isSelected ? 1 : 0.5}
                        />
                      );
                    })}
                  </svg>
                );
              })() : (
                <div className="h-full w-full rounded-full border-[16px] border-[#2B2926]/[0.05] flex items-center justify-center">
                  <span className="text-[10px] font-bold text-[#2B2926]/30 uppercase tracking-widest text-center px-2">Connect accounts to see split</span>
                </div>
              )}

              {/* Centered total — absolutely positioned dead-center of the
                  donut box using top/left 50% + translate(-50%, -50%). This
                  is more reliable than flex centering when recharts'
                  ResponsiveContainer applies its own SVG sizing. */}
              {filteredAudienceTotal > 0 && (
                <div
                  className="pointer-events-none text-center"
                  style={{
                    position: 'absolute',
                    top: '50%',
                    left: '50%',
                    transform: 'translate(-50%, -50%)',
                    width: '110px',
                  }}
                >
                  <div
                    className="font-semibold text-[#2B2926] tracking-tight leading-none"
                    style={{ fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"', fontSize: '24px' }}
                  >
                    {filteredAudienceTotal.toLocaleString()}
                  </div>
                  <div
                    className="font-semibold text-[#2B2926] uppercase"
                    style={{ fontSize: '9px', letterSpacing: '0.1em', marginTop: '4px' }}
                  >
                    FOLLOWERS
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Breakdown list — coloured square swatches + label + bold raw
              total. Font size + row gap shrink as the list grows so all
              rows always fit inside the card without scrolling. */}
          {(() => {
            const n = filteredPlatformSplit.length;
            // Tiered scaling: 4 or fewer → 14px, 5-6 → 13px,
            // 7-8 → 12px, 9+ → 11px. Row gap shrinks in parallel.
            const fontSize = n <= 4 ? 14 : n <= 6 ? 13 : n <= 8 ? 12 : 11;
            const rowGapClass = n <= 4 ? 'gap-1.5' : n <= 6 ? 'gap-1' : n <= 8 ? 'gap-[2px]' : 'gap-px';
            return (
          <div className={`flex flex-col ${rowGapClass} mt-1 pr-1`}>
            {filteredPlatformSplit.length > 0 ? filteredPlatformSplit.map((entry) => {
              const isSelected = selectedPlatform === 'all' || getPlatformKey(entry.name) === selectedPlatform;
              return (
                <div
                  key={entry.name}
                  className={`flex items-center justify-between gap-2 transition-opacity ${isSelected ? 'opacity-100' : 'opacity-40'}`}
                  style={{ fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"', fontSize: `${fontSize}px` }}
                >
                  <span className="flex items-center gap-2 min-w-0 flex-1 text-[#2B2926] font-medium overflow-hidden">
                    <span className="shrink-0 inline-flex items-center justify-center w-4 h-4">
                      <PlatformIcon platform={getPlatformKey(entry.name)} size={16} />
                    </span>
                    <span className="truncate flex-1 min-w-0">{entry.name}</span>
                  </span>
                  <b
                    className="font-bold text-[#2B2926] shrink-0 tabular-nums text-right whitespace-nowrap"
                    style={{ fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
                  >
                    {entry.value.toLocaleString()}
                  </b>
                </div>
              );
            }) : (
              <p className="text-center text-[12px] text-[#2B2926]/40 italic py-3">No distribution data available yet.</p>
            )}
          </div>
            );
          })()}
        </div>
      </div>

      )}

      {/* Detailed Post Analytics Table — ONLY visible in Campaign Performance
          mode. Removed from the main Analytics page so the user navigates to
          the dedicated Campaign Performance tab to see post-level details. */}
      {isPostOnly && (
      <div className="bg-white rounded-[22px] border border-[#2B2926]/30 shadow-[0_12px_34px_rgba(17,17,17,0.05)] overflow-hidden flex flex-col">
        {/* Card heading is hidden in Campaign Performance mode — the
            big "Campaign Performance" page title already appears at the
            top of the page, so a second heading inside the card is
            redundant. Kept for the legacy Analytics-page case. */}
        {!isPostOnly && (
          <div className="p-8 border-b border-gray-50 flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div>
              <h3 className="font-bold text-[#2B2926] text-lg">Post Performance</h3>
            </div>
          </div>
        )}

        {/* Fixed height scrollable container */}
        <div className="max-h-[500px] xl:max-h-[550px] overflow-x-auto overflow-y-auto scrollbar-thin scrollbar-thumb-gray-200 scrollbar-track-transparent rounded-b-3xl detailed-table-container" style={{ scrollbarGutter: 'stable' }}>
          <table className="w-full text-left border-separate border-spacing-0">
            <thead>
              <tr className="bg-white text-[#2B2926] text-[11px] uppercase font-semibold tracking-[0.12em]">
                <th className="sticky top-0 z-50 px-4 py-3 bg-white border-b border-[#2B2926]/[0.06] text-center">Content</th>
                <th className="sticky top-0 z-50 w-[1%] px-2 py-3 bg-white border-b border-[#2B2926]/[0.06] text-center whitespace-nowrap">Preview</th>
                <th className="sticky top-0 z-50 w-[1%] px-2 py-3 bg-white border-b border-[#2B2926]/[0.06] text-center whitespace-nowrap">Platform</th>
                <th className="sticky top-0 z-50 w-[1%] px-2 py-3 bg-white border-b border-[#2B2926]/[0.06] text-center whitespace-nowrap">Likes</th>
                <th className="sticky top-0 z-50 w-[1%] px-2 py-3 bg-white border-b border-[#2B2926]/[0.06] text-center whitespace-nowrap">Comments</th>
                <th className="sticky top-0 z-50 w-[1%] px-2 py-3 bg-white border-b border-[#2B2926]/[0.06] text-center whitespace-nowrap">Shares</th>
                <th className="sticky top-0 z-50 w-[1%] px-2 py-3 bg-white border-b border-[#2B2926]/[0.06] text-center whitespace-nowrap">Reach</th>
                <th className="sticky top-0 z-50 w-[1%] px-2 py-3 bg-white border-b border-[#2B2926]/[0.06] text-center whitespace-nowrap">Engagement</th>
                <th className="sticky top-0 z-50 w-[1%] pl-2 pr-6 py-3 bg-white border-b border-[#2B2926]/[0.06] text-center whitespace-nowrap">
                  <div className="flex items-center justify-center">
                    Semantic Score
                  </div>
                </th>
              </tr>
            </thead>
                <tbody className="divide-y divide-black/[0.05]">
                  {posts.length > 0 ? posts.map((post, i) => (
                      <tr
                        key={i}
                        className="hover:bg-[rgba(245,86,0,0.04)] transition-colors group"
                      >
                      <td className="px-4 py-3 min-w-[200px] max-w-[300px]">
                        <div className="flex items-center gap-4">
                          {(() => {
                            const thumb = getPostThumb(post);
                            const isVideo = post.platform === 'youtube' || post.media_type === 'video';
                            const isDoc = isDocumentMedia(post);
                            if (isDoc) {
                              return (
                                <div
                                  onClick={() => post.image_url && window.open(post.image_url, '_blank', 'noopener')}
                                >
                                  <DocThumb className="w-10 h-10 rounded-xl flex-shrink-0 text-[9px] cursor-pointer shadow-sm" />
                                </div>
                              );
                            }
                            return (
                          <div
                            className="w-10 h-10 rounded-xl bg-slate-50 flex-shrink-0 flex items-center justify-center overflow-hidden border border-[#2B2926]/30 relative group/img cursor-zoom-in shadow-sm"
                            onClick={() => {
                              const embed = getYoutubeEmbed(post);
                              if (embed) setFullscreenImage(embed);
                              else if (thumb) setFullscreenImage(thumb);
                            }}
                          >
                            {thumb ? (
                              <>
                                {/\.(mp4|mov|webm|m4v)(\?|$)/i.test(thumb) ? (
                                  <video
                                    src={thumb}
                                    className="w-full h-full object-cover"
                                    muted
                                    playsInline
                                    preload="metadata"
                                  />
                                ) : (
                                  <img
                                    src={thumb}
                                    className="w-full h-full object-cover group-hover/img:scale-110 transition-transform duration-500"
                                    alt=""
                                    onError={(e) => { e.currentTarget.style.display = 'none'; }}
                                  />
                                )}
                                {isVideo ? (
                                  <div className="absolute inset-0 flex items-center justify-center bg-slate-900/20 group-hover/img:bg-slate-900/40 transition-colors">
                                    <span className="w-0 h-0 border-y-[5px] border-y-transparent border-l-[8px] border-l-white ml-0.5 drop-shadow" />
                                  </div>
                                ) : (
                                  <div className="absolute inset-0 bg-slate-900/40 opacity-0 group-hover/img:opacity-100 transition-opacity flex items-center justify-center">
                                    <FiMaximize2 className="text-white w-4 h-4" />
                                  </div>
                                )}
                              </>
                            ) : (
                              <FiActivity className="text-[#2B2926] w-4 h-4" />
                            )}
                          </div>
                            );
                          })()}
                          <div
                            className="flex-1 min-w-0 cursor-pointer"
                            onClick={() => setSelectedPost(post)}
                          >
                            <p className="text-[12px] text-[#2B2926]/80 font-semibold leading-tight line-clamp-2 group-hover:text-[#2B2926] group-hover:font-bold transition-all">
                              {(() => {
                                if (!post.content) return "Post #" + (post.id || i);
                                if (post.content.trim().startsWith('{')) {
                                  try {
                                    const parsed = JSON.parse(post.content);
                                    return parsed[post.platform] || parsed.default || Object.values(parsed)[0] || post.content;
                                  } catch (e) {
                                    return post.content;
                                  }
                                }
                                return post.content;
                              })()}
                            </p>
                            <div className="text-[10px] text-[#2B2926]/40 font-bold uppercase tracking-tighter mt-0.5 group-hover:text-[#2B2926] transition-colors">
                              {formatInTimezone(post.publish_date, user?.timezone)}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td className="px-0 py-3 text-center">
                        <button 
                          onClick={() => setSelectedPost(post)}
                          className="p-1.5 hover:bg-orange-100 hover:text-[#F55600] text-[#2B2926] rounded-lg transition-all"
                          title="Preview Post"
                        >
                          <FiEye size={16} />
                        </button>
                      </td>
                      <td className="px-2 py-3 text-center">
                        <div className="flex flex-col items-center gap-1">
                          <PlatformIcon platform={post.platform} size={18} />
                          {post.account_name && (
                            <span
                              className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-md text-[8px] font-semibold border max-w-[130px] ${
                                post.is_personal
                                  ? 'bg-[#F55600]/10 text-[#F55600] border-[#F55600]/30'
                                  : 'bg-slate-100 text-[#2B2926] border-[#2B2926]/20'
                              }`}
                              title={`${post.account_name} — ${post.is_personal ? 'Personal profile' : 'Page'}`}
                            >
                              <span className="truncate">{post.account_name}</span>
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-0 py-3 text-center">
                        <div className="flex justify-center">
                          <div className="w-8 h-8 rounded-full bg-emerald-500 text-white flex items-center justify-center text-[10px] font-semibold shadow-sm border-2 border-white">
                            {(post.likes || 0).toLocaleString()}
                          </div>
                        </div>
                      </td>
                      <td className="px-0 py-3 text-center relative">
                        <div className="flex justify-center">
                          <button
                            onClick={() => handleSentimentAnalysis(post)}
                            className="w-8 h-8 rounded-full bg-slate-900 text-white flex items-center justify-center text-[10px] font-semibold shadow-md border-2 border-white hover:bg-[#2B2926] transition-all"
                            title="Analyze Sentiment"
                          >
                            {(post.comments || 0).toLocaleString()}
                          </button>
                        </div>
                      </td>
                      <td className="px-0 py-3 text-center">
                        <div className="flex justify-center">
                          <div className="w-8 h-8 rounded-full bg-emerald-500 text-white flex items-center justify-center text-[10px] font-semibold shadow-sm border-2 border-white">
                            {(post.shares || 0).toLocaleString()}
                          </div>
                        </div>
                      </td>
                      <td className="px-0 py-3 text-center">
                        <div className="flex justify-center">
                          <div className="w-8 h-8 rounded-full bg-slate-900 text-white flex items-center justify-center text-[10px] font-semibold shadow-sm border-2 border-white">
                            {(post.reach || 0).toLocaleString()}
                          </div>
                        </div>
                      </td>
                      <td className="px-1 py-3 text-center">
                        <div className="flex justify-center">
                          <div className="w-8 h-8 rounded-full bg-[#F55600] text-white flex items-center justify-center text-[10px] font-semibold shadow-sm border-2 border-white">
                            {(post.engagement || 0).toLocaleString()}
                          </div>
                        </div>
                      </td>
                      {/* Semantic Score — last column. Identical logic to the
                          previous in-table position; only the column order
                          changed so engagement KPIs stay grouped together and
                          the AI-derived score sits at the far right. */}
                      <td className="pl-2 pr-6 py-3 text-center">
                        {(() => {
                          const k = `${post.platform}_${post.native_id}`;
                          const cache = sentimentCache[k];
                          const commentCount = post.comments || 0;

                          // Only show analysis if there are at least 5 comments
                          if (commentCount < 5) {
                            return <span className="text-[10px] font-bold text-[#2B2926] uppercase tracking-widest">—</span>;
                          }

                          if (!cache) {
                            const isQueued = backgroundQueue.some(q => `${q.platform}_${q.native_id}` === k) || analysisTrackRef.current.has(k);
                            if (isQueued) {
                              return (
                                <div className="flex flex-col items-center justify-center gap-1 animate-pulse">
                                  <FiActivity className="text-emerald-500" size={12} />
                                  <span className="text-[8px] font-semibold text-emerald-600 uppercase tracking-widest">Analyzing</span>
                                </div>
                              );
                            }
                            return <span className="text-[10px] font-bold text-[#2B2926] uppercase tracking-widest">—</span>;
                          }

                          const score = cache.overall_score || 0;
                          let label = 'Neutral';
                          if (score > 85) label = 'Positive';
                          else if (score < 50) label = 'Negative';

                          const colorMap = {
                            'Positive': 'text-emerald-600 bg-emerald-50 border-emerald-200',
                            'Neutral': 'text-[#F55600] bg-orange-50 border-orange-200',
                            'Negative': 'text-red-600 bg-red-50 border-red-200'
                          };
                          const style = colorMap[label] || 'text-[#2B2926] bg-slate-50 border-[#2B2926]/30';

                          return (
                            <div className={`inline-flex flex-col items-center justify-center px-3 py-1.5 rounded-xl border-2 ${style} min-w-[70px] shadow-sm transition-all animate-in zoom-in-95 duration-300`}>
                              <span className="text-[14px] font-semibold mb-0.5 leading-none">{score}%</span>
                              <span className="text-[9px] font-semibold uppercase tracking-widest leading-none">
                                {label}
                              </span>
                            </div>
                          );
                        })()}
                      </td>
                    </tr>
                  )) : loadingPosts ? (
                    // Show a friendly loading state on first paint instead of
                    // the misleading "No posts found" message — the user sees
                    // posts ARE coming, they just need a moment.
                    <tr>
                      <td colSpan="9" className="px-8 py-20 text-center">
                        <div className="flex flex-col items-center justify-center gap-3">
                          <FiRefreshCw className="w-7 h-7 text-[#F55600] animate-spin" />
                          <div className="text-sm font-semibold text-[#2B2926]/80 tracking-tight">
                            Loading posts…
                          </div>
                          <div className="text-[11px] font-medium text-[#2B2926]/50 max-w-[280px]">
                            Pulling the latest engagement data — this usually takes a few seconds.
                          </div>
                        </div>
                      </td>
                    </tr>
                  ) : (
                    <tr>
                      <td colSpan="9" className="px-8 py-20 text-center text-[#2B2926] font-bold italic">
                        No posts found for this platform.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

      )}

      {/* Platform Performance + Top 3 Posts (same row).
          Top 3 ALWAYS shows — filters automatically by selectedPlatform.
          Platform Performance only shows on "all" (single-platform view
          of a bar chart is meaningless).
          Both sections are hidden in Campaign Performance mode (post-only). */}
      {!isPostOnly && (
      <div className="grid grid-cols-1 gap-6 pb-12">
          {/* Top 3 Posts */}
          <div
            id="export-top-posts"
            className="bg-white rounded-[22px] border border-[#2B2926]/30 shadow-[0_12px_34px_rgba(17,17,17,0.05)] p-6"
          >
            <div className="flex items-center justify-between mb-4">
              <h3
                className="font-bold text-[#2B2926] tracking-tight"
                style={{ fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"', fontSize: '20px' }}
              >
                Top 3 Posts{selectedPlatform !== 'all' ? ` — ${selectedPlatform === 'twitter' ? 'X' : (selectedPlatform.charAt(0).toUpperCase() + selectedPlatform.slice(1))}` : ''}
              </h3>
              <span className="text-[11px] font-bold text-white bg-[#2B2926] px-3 py-1.5 rounded-full uppercase tracking-[0.08em]">
                By engagement
              </span>
            </div>
            {(() => {
              // Rank by combined engagement score: likes + comments + shares + 0.01 × reach.
              // Reach is weighted down because it's usually 10-100× larger than interactions
              // and would otherwise dominate the sort without being a direct quality signal.
              const ranked = [...(posts || [])]
                .map((p) => ({
                  ...p,
                  _score: (p.likes || 0) + (p.comments || 0) + (p.shares || 0) + (p.reach || 0) * 0.01,
                }))
                .filter((p) => p._score > 0)
                .sort((a, b) => b._score - a._score)
                .slice(0, 3);

              if (ranked.length === 0) {
                return (
                  <div className="text-xs text-[#2B2926] italic py-6 text-center">
                    No post engagement yet — data will appear after your accounts sync.
                  </div>
                );
              }

              // Ghost-numeral colours per rank — gold, silver, bronze (from
              // the Aurora-Glass reference). Pulled out so the JSX below stays
              // tidy and each card just looks up by index.
              const ghostColors = ['#f3c46a', '#b9c4d6', '#e3a173'];
              return (
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4 items-stretch">
                  {/* Defensive second slice — the outer .slice(0,3) at line ~2834
                      already caps, but a duplicate in the API response could still
                      inflate ranked.length. This guarantees the DOM never shows
                      more than 3 cards regardless of upstream data shape. */}
                  {ranked.slice(0, 3).map((post, idx) => {
                    const title = (() => {
                      if (!post.content) return `Post #${post.id || idx}`;
                      const raw = post.content.trim();
                      if (raw.startsWith('{')) {
                        try {
                          const parsed = JSON.parse(raw);
                          return parsed[post.platform] || parsed.default || Object.values(parsed)[0] || raw;
                        } catch (e) {
                          return raw;
                        }
                      }
                      return raw;
                    })();
                    // Trim to ~10 words so the title fits the card without
                    // wrapping more than ~2 lines on a normal viewport.
                    const words = title.split(/\s+/).filter(Boolean);
                    const titleTrunc = words.length > 10
                      ? words.slice(0, 10).join(' ') + '…'
                      : title;

                    // Resolve the platform icon — uses the existing
                    // PlatformIcon component so brand colours stay correct
                    // (LinkedIn blue, X black, etc.).
                    const platKey = (post.platform || '').toLowerCase();
                    const iconColor = platKey === 'linkedin' ? '#0a66c2'
                      : platKey === 'facebook' ? '#1877F2'
                      : platKey === 'instagram' ? '#E1306C'
                      : '#0a66c2';

                    return (
                      <article
                        key={post.id || idx}
                        onClick={() => setSelectedPost(post)}
                        className="relative bg-white border border-[#2B2926]/30 rounded-2xl px-6 pt-7 pb-6 overflow-hidden cursor-pointer transition-all duration-300 hover:-translate-y-1 hover:shadow-[0_20px_36px_-22px_rgba(15,23,42,0.28)] hover:border-[#2B2926]/15 flex flex-col h-full"
                      >
                        {/* Huge ghost numeral in the background */}
                        <span
                          aria-hidden="true"
                          className="absolute select-none pointer-events-none font-semibold leading-none"
                          style={{
                            top: '-16px',
                            right: '8px',
                            fontSize: '104px',
                            letterSpacing: '-0.05em',
                            fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"',
                            color: ghostColors[idx] || '#e3a173',
                            zIndex: 0,
                          }}
                        >
                          {idx + 1}
                        </span>

                        <div className="relative z-10 flex flex-col flex-1">
                          {/* Top-left: platform icon + small image thumbnail side
                              by side. Leaves the top-right entirely to the giant
                              ghost numeral so they don't clash. */}
                          <div className="flex items-center gap-2 mb-4 max-w-[72%]">
                            <span className="inline-flex shrink-0" style={{ color: iconColor }}>
                              <PlatformIcon platform={post.platform} size={20} />
                            </span>
                            {post.image_url ? (
                              <img
                                src={post.image_url}
                                alt=""
                                className="w-9 h-9 rounded-lg object-cover border border-[#2B2926]/25 shrink-0"
                                onError={(e) => { e.target.style.display = 'none'; }}
                              />
                            ) : null}
                          </div>

                          {/* Post title */}
                          <h4
                            className="font-normal tracking-tight leading-snug max-w-[72%] text-[#2B2926]"
                            style={{ fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"', fontSize: '17px' }}
                          >
                            {titleTrunc}
                          </h4>

                          {/* Time */}
                          <time className="block mt-2.5 text-[12px] font-bold text-[#2B2926] uppercase tracking-[0.14em]">
                            {formatInTimezone(post.publish_date, user?.timezone)}
                          </time>

                          {/* Spacer pushes the bottom group (divider + stats row)
                              to the bottom of the card so every card's stats row
                              lines up horizontally with the others, regardless of
                              title length. */}
                          <div className="flex-1" />

                          {/* Divider */}
                          <div className="h-px bg-[#2B2926]/[0.06] my-5" />

                          {/* Stats row — 4 evenly-distributed cells so all
                              four metrics always fit inside the card width on
                              both desktop and mobile (no truncation of "Reach"). */}
                          <div
                            className="grid grid-cols-4 gap-1 items-center text-[11.5px] sm:text-[12.5px] font-semibold text-[#2B2926]"
                            style={{ fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
                          >
                            <span className="whitespace-nowrap border-r border-[#2B2926]/[0.06] pr-1">
                              Likes <b className="text-[#2B2926] font-semibold ml-0.5">{(post.likes || 0).toLocaleString()}</b>
                            </span>
                            <span className="whitespace-nowrap border-r border-[#2B2926]/[0.06] px-1">
                              Comm. <b className="text-[#2B2926] font-semibold ml-0.5">{(post.comments || 0).toLocaleString()}</b>
                            </span>
                            <span className="whitespace-nowrap border-r border-[#2B2926]/[0.06] px-1">
                              Shares <b className="text-[#2B2926] font-semibold ml-0.5">{(post.shares || 0).toLocaleString()}</b>
                            </span>
                            <span className="whitespace-nowrap pl-1">
                              Reach <b className="font-semibold ml-0.5" style={{ color: '#10B981' }}>{(post.reach || 0).toLocaleString()}</b>
                            </span>
                          </div>
                        </div>
                      </article>
                    );
                  })}
                </div>
              );
            })()}
          </div>

          {/* Platform Performance — rendered as a Companies × Platforms
              HEATMAP. Each cell is split in two halves: left = Followers
              Share (orange intensity), right = Performance Share (green
              intensity). Darker shade = larger share of the visible
              total. A multi-select dropdown above the grid lets the
              admin scope the heatmap to a subset of companies. */}
          {selectedPlatform === 'all' && (() => {
            // Full canonical platform list — order matters (defines column
            // order in the heatmap). We'll filter this down to only the
            // platforms the user actually has connected accounts on, so
            // an empty column like "TikTok 0%" doesn't clutter the grid
            // for a user who hasn't connected TikTok.
            const ALL_PLATFORM_COLS = [
              { key: 'linkedin',  label: 'LinkedIn'  },
              { key: 'instagram', label: 'Instagram' },
              { key: 'twitter',   label: 'X'         },
              { key: 'facebook',  label: 'Facebook'  },
              { key: 'youtube',   label: 'Youtube'   },
              { key: 'tiktok',    label: 'TikTok'    },
              { key: 'pinterest', label: 'Pinterest' },
            ];

            // Group existing per-account `platforms` payload by company
            // (parsed out of the entry name, e.g. "Linkedin (NEUZEN AI)"
            // → company "NEUZEN AI"). Each cell aggregates followers +
            // engagement for that (company, platform) pair.
            const matrix = {}; // { company: { linkedin: {followers, engagement}, ... } }
            Object.values(platforms).forEach((p) => {
              const m = (p?.name || '').match(/\(([^)]+)\)/);
              const company = m && m[1] ? m[1].trim() : '—';
              const pKey = getPlatformKey(p?.name);
              if (!matrix[company]) matrix[company] = {};
              if (!matrix[company][pKey]) matrix[company][pKey] = { followers: 0, engagement: 0 };
              matrix[company][pKey].followers  += Number(p.followers || 0);
              // Performance Share uses LIFETIME engagement so it
              // compares apples-to-apples with the lifetime follower
              // count — `engagement` alone is window-scoped and reads 0
              // for accounts whose posts predate the selected window.
              matrix[company][pKey].engagement += Number(
                p.lifetime_engagement != null ? p.lifetime_engagement : (p.engagement || 0)
              );
            });

            // Canonical row list — start with registered companies from
            // filterOptions (case-preserved dropdown labels) then APPEND
            // any parsed row-key from the matrix that isn't already in the
            // list. This surfaces personal profiles (e.g. "Salman Shaik")
            // as their OWN row in the heatmap alongside company rows
            // ("NeuzenAI"), instead of dropping them or merging them.
            const _normKey = (s) => (s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
            const canonicalCompanies = (filterOptions.companies || []).length
              ? [...filterOptions.companies]
              : Object.keys(matrix);
            const _canonSet = new Set(canonicalCompanies.map(_normKey));
            Object.keys(matrix).forEach((raw) => {
              if (raw === '—') return; // skip the un-parseable placeholder
              if (!_canonSet.has(_normKey(raw))) {
                canonicalCompanies.push(raw);
                _canonSet.add(_normKey(raw));
              }
            });
            canonicalCompanies.forEach((c) => {
              if (!matrix[c]) matrix[c] = {};
            });
            const matrixByCanonical = {};
            canonicalCompanies.forEach((c) => {
              matrixByCanonical[c] = {};
              const ck = _normKey(c);
              Object.entries(matrix).forEach(([raw, plats]) => {
                if (_normKey(raw) === ck) {
                  Object.entries(plats).forEach(([pk, v]) => {
                    if (!matrixByCanonical[c][pk]) matrixByCanonical[c][pk] = { followers: 0, engagement: 0 };
                    matrixByCanonical[c][pk].followers  += v.followers;
                    matrixByCanonical[c][pk].engagement += v.engagement;
                  });
                }
              });
            });

            // Visible rows = filter selection (empty = all).
            const visibleCompanies = heatmapCompanies.length > 0
              ? canonicalCompanies.filter((c) => heatmapCompanies.includes(c))
              : canonicalCompanies;

            // Dynamic column set — only render columns for platforms the
            // user actually has a connected account on. Signal: a platform
            // appears in matrixByCanonical for at least one company. This
            // drops "TikTok 0%" columns for users who haven't connected
            // TikTok, keeping the grid clean instead of a wall of 0.0%.
            // Fallback: if no accounts are connected at all (shouldn't
            // happen inside Analytics, but defensive), show every column
            // so the empty-state renders sensibly.
            const connectedPlatformKeys = new Set();
            Object.values(matrixByCanonical).forEach((platsForCompany) => {
              Object.keys(platsForCompany).forEach((pk) => connectedPlatformKeys.add(pk));
            });
            const PLATFORM_COLS = connectedPlatformKeys.size > 0
              ? ALL_PLATFORM_COLS.filter((c) => connectedPlatformKeys.has(c.key))
              : ALL_PLATFORM_COLS;

            // Totals for share denominators — across the VISIBLE cells.
            let totalFollowers = 0;
            let totalEngagement = 0;
            visibleCompanies.forEach((c) => {
              PLATFORM_COLS.forEach(({ key }) => {
                const cell = matrixByCanonical[c]?.[key];
                if (cell) {
                  totalFollowers  += cell.followers;
                  totalEngagement += cell.engagement;
                }
              });
            });

            // Cell background intensity = share % of the two brand
            // colours: orange (#F55600) for Followers and green
            // (#10B981) for Performance. Higher share → darker, lower
            // share → lighter. Alpha is CAPPED low (max ~0.62) so even
            // a 100% cell stays light enough that solid black text
            // reads clearly — the user flagged that the previous
            // mapping (max 1.0) went too dark to read.
            const orangeBg = (share) => {
              const s = Math.max(0, Math.min(1, share));
              const a = 0.10 + 0.52 * Math.sqrt(s);
              return `rgba(245, 86, 0, ${a})`;
            };
            const greenBg = (share) => {
              const s = Math.max(0, Math.min(1, share));
              const a = 0.10 + 0.52 * Math.sqrt(s);
              return `rgba(16, 185, 129, ${a})`;
            };
            // All cell text is ALWAYS solid black. With the lower alpha
            // ceiling above, no white halo is needed any more — black
            // stays readable on every shade.
            const textOn = () => '#000000';
            const textHalo = () => 'none';
            const toggleCompany = (c) => setHeatmapCompanies((prev) =>
              prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c]
            );

            return (
              <div
                id="export-performance-heatmap"
                className="bg-white rounded-[22px] border border-[#2B2926]/30 shadow-[0_12px_34px_rgba(17,17,17,0.05)] p-6"
              >
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-2">
                  <div className="flex items-center gap-3 min-w-0">
                    <div
                      className="w-10 h-10 rounded-xl grid place-items-center shrink-0"
                      style={{ background: 'rgba(0,0,0,0.04)', color: '#F55600' }}
                    >
                      <FiActivity className="w-5 h-5" />
                    </div>
                    <div className="min-w-0">
                      <h3
                        className="font-bold text-[#2B2926] tracking-tight leading-tight"
                        style={{ fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"', fontSize: '20px' }}
                      >
                        Platform Performance
                      </h3>
                      <p className="text-[13px] text-[#2B2926] mt-1">
                        Followers share vs Performance share by company &amp; platform
                      </p>
                    </div>
                  </div>
                  {/* Company multi-select. Custom popover so we can use
                      the brand palette (no native blue option highlight). */}
                  <div className="relative self-start sm:self-auto shrink-0">
                    <button
                      type="button"
                      onClick={() => setHeatmapPickerOpen((v) => !v)}
                      className="h-9 px-3 inline-flex items-center gap-2 bg-white border border-[#2B2926]/15 rounded-lg text-[11px] font-bold text-[#2B2926] hover:border-[#F55600]/50 transition-colors"
                    >
                      <span>
                        {heatmapCompanies.length === 0
                          ? 'All companies'
                          : `${heatmapCompanies.length} selected`}
                      </span>
                      <FiChevronDown size={12} className="text-[#F55600]" />
                    </button>
                    {heatmapPickerOpen && (
                      <>
                        <div className="fixed inset-0 z-[90]" onClick={() => setHeatmapPickerOpen(false)} />
                        <div className="absolute right-0 mt-1 z-[100] bg-white border border-[#2B2926]/25 rounded-lg shadow-xl min-w-[200px] py-1">
                          <button
                            type="button"
                            onClick={() => setHeatmapCompanies([])}
                            className={`w-full text-left text-[11px] px-3 py-1.5 font-bold transition-colors ${
                              heatmapCompanies.length === 0
                                ? 'bg-[#F55600] text-white'
                                : 'text-[#2B2926] hover:bg-[#2B2926]/[0.05]'
                            }`}
                          >
                            All companies
                          </button>
                          <div className="border-t border-[#2B2926]/20 my-1" />
                          {canonicalCompanies.map((c) => {
                            const allMode = heatmapCompanies.length === 0;
                            const checked = allMode || heatmapCompanies.includes(c);
                            return (
                              <label
                                key={c}
                                className="flex items-center gap-2 px-3 py-1.5 cursor-pointer text-[11px] font-bold hover:bg-[#2B2926]/[0.05]"
                              >
                                <input
                                  type="checkbox"
                                  checked={checked}
                                  onChange={() => setHeatmapCompanies((prev) => {
                                    if (prev.length === 0) {
                                      return canonicalCompanies.filter((x) => x !== c);
                                    }
                                    return prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c];
                                  })}
                                  className="accent-[#F55600]"
                                />
                                <span className="truncate">{c}</span>
                              </label>
                            );
                          })}
                        </div>
                      </>
                    )}
                  </div>
                </div>

                {/* Legend — two dots: orange "Followers Share", green
                    "Performance Share" — matches the HTML reference. */}
                <div className="flex items-center gap-5 mt-2 mb-5">
                  <span className="inline-flex items-center gap-2 text-[12px] font-semibold text-[#2B2926]">
                    <i className="w-2.5 h-2.5 rounded-full inline-block" style={{ background: '#F55600' }} />
                    Followers Share
                  </span>
                  <span className="inline-flex items-center gap-2 text-[12px] font-semibold text-[#2B2926]">
                    <i className="w-2.5 h-2.5 rounded-full inline-block" style={{ background: '#10B981' }} />
                    Performance Share
                  </span>
                </div>

                {/* Per-company × per-platform matrix. Each row is one company
                    (NeuzenAI / Z-NINTH / ...), each row has 4 platform cards
                    (LinkedIn / Instagram / X / Facebook). Followers and
                    Performance share are computed as that (company,platform)
                    cell's share of the visible total — so the percentages
                    across the whole matrix sum to 100% per metric. */}
                {(() => {
                  if (visibleCompanies.length === 0) {
                    return (
                      <div className="text-center text-[11px] italic text-[#2B2926]/40 py-6">
                        No companies match the current filter selection.
                      </div>
                    );
                  }

                  // Dynamic column template — driven by the number of
                  // platforms in PLATFORM_COLS so the grid auto-adjusts when
                  // platforms are added/removed (no longer hard-locked to 4).
                  //
                  // Label column is 140px so full names like "Salman Shaik"
                  // fit without being truncated to "Salman Sha...". Long
                  // company names past 140px still ellipsize via the label
                  // cell's `truncate` class, but 140px covers the common
                  // real-world names.
                  //
                  // Alignment rule: with 4+ columns, cards use 1fr and
                  // compress to fit the row. With 1-3 columns, 1fr would
                  // stretch each card way too wide (a single LinkedIn card
                  // would fill the row and look absurd) — cap those at a
                  // sensible max so cards stay proportional to their
                  // 4-column form-factor. Extra space fills at the right
                  // with a spacer track.
                  const colCount = PLATFORM_COLS.length;
                  const desktopCols = colCount >= 4
                    ? `140px repeat(${colCount}, minmax(0, 1fr))`
                    : `140px repeat(${colCount}, minmax(180px, 260px)) 1fr`;

                  // Column header strip — rendered ONCE above all rows so
                  // every company row aligns under the same labels.
                  const HeaderStrip = (
                    <div className="hidden sm:grid gap-3 mb-1 pl-1" style={{ gridTemplateColumns: desktopCols }}>
                      <div />
                      {PLATFORM_COLS.map(({ key, label }) => (
                        <div
                          key={key}
                          className="text-center font-bold uppercase tracking-[0.06em] text-[#2B2926]"
                          style={{ fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"', fontSize: '14px' }}
                        >
                          {label}
                        </div>
                      ))}
                    </div>
                  );

                  return (
                    <div className="flex flex-col gap-3">
                      {/* Scoped rule: stacks on mobile (grid-cols-1), switches
                          to the dynamic N-column template on sm+. Inline style
                          can't carry a media query, so this drives the desktop
                          breakpoint while staying fully dynamic. */}
                      <style>{`@media (min-width:640px){.phm-cols{grid-template-columns:${desktopCols} !important;}}`}</style>
                      {HeaderStrip}
                      {visibleCompanies.map((company) => (
                        <div
                          key={company}
                          className="phm-cols grid grid-cols-1 gap-3 items-stretch"
                        >
                          {/* Company label cell — centered on mobile (single
                              column stack), left-aligned on sm+ (grid layout). */}
                          <div
                            className="flex items-center justify-center sm:justify-start font-semibold text-[#2B2926] truncate"
                            style={{ fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"', fontSize: '14px' }}
                          >
                            {company}
                          </div>
                          {/* 4 platform cards for this company */}
                          {PLATFORM_COLS.map(({ key, label }) => {
                            const cell = matrixByCanonical[company]?.[key] || { followers: 0, engagement: 0 };
                            const fShare = totalFollowers  > 0 ? (cell.followers  / totalFollowers)  * 100 : 0;
                            const pShare = totalEngagement > 0 ? (cell.engagement / totalEngagement) * 100 : 0;
                            return (
                              <div
                                key={key}
                                className="rounded-2xl overflow-hidden border border-[#2B2926]/30 bg-white"
                              >
                                {/* Mobile-only platform label (header strip is hidden <sm) */}
                                <div
                                  className="sm:hidden text-center font-bold py-2 px-3 border-b border-[#2B2926]/30 uppercase tracking-[0.06em] text-[#2B2926]"
                                  style={{ fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"', fontSize: '14px' }}
                                >
                                  {label}
                                </div>
                                <div className="grid grid-cols-2">
                                  <div
                                    className="p-2.5 sm:p-3"
                                    style={{ background: 'linear-gradient(135deg, rgba(245,86,0,0.13), rgba(245,86,0,0.05))', fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
                                  >
                                    <div className="text-[9px] font-bold tracking-[0.02em] text-[#2B2926] truncate">FOLLOWERS</div>
                                    <div
                                      className="font-semibold mt-1.5 leading-none"
                                      style={{ fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"', fontSize: '20px', color: '#F55600' }}
                                    >
                                      {fShare.toFixed(1)}%
                                    </div>
                                    <small className="text-[12px] font-semibold text-[#2B2926]">
                                      {cell.followers.toLocaleString()}
                                    </small>
                                  </div>
                                  <div
                                    className="p-2.5 sm:p-3"
                                    style={{ background: 'linear-gradient(135deg, rgba(22,163,74,0.13), rgba(22,163,74,0.05))', fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
                                  >
                                    <div className="text-[9px] font-bold tracking-[0.02em] text-[#2B2926] truncate">PERFORMANCE</div>
                                    <div
                                      className="font-semibold mt-1.5 leading-none"
                                      style={{ fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"', fontSize: '20px', color: '#10B981' }}
                                    >
                                      {pShare.toFixed(1)}%
                                    </div>
                                    <small className="text-[12px] font-semibold text-[#2B2926]">
                                      {cell.engagement.toLocaleString()}
                                    </small>
                                  </div>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      ))}
                    </div>
                  );
                })()}
              </div>
            );
          })()}
        </div>
      )}

      {/* Post Detail Modal */}
      {selectedPost && (
        <PostDetailModal
          post={selectedPost}
          onClose={() => setSelectedPost(null)}
          timezone={user?.timezone}
          onExpandImage={(url) => setFullscreenImage(url)}
          authAxios={authAxios}
          cachedComments={commentsCache[selectedPost.id]}
          onUpdateCache={(id, comments) => setCommentsCache(prev => ({ ...prev, [id]: comments }))}
        />
      )}

      {/* Fullscreen Image Lightbox — sized to fit any viewport so the image
          never spills off-screen. Close button sits INSIDE the container's
          top-right corner so it's always visible, even on small Mac screens. */}
      {fullscreenImage && (
        <div className="fixed inset-0 z-[200] flex items-center justify-center p-3 sm:p-6">
          <div className="absolute inset-0 bg-slate-900/55 backdrop-blur-sm" onClick={() => setFullscreenImage(null)} />
          <div className="relative z-10 inline-flex flex-col items-center max-w-[min(90vw,640px)] max-h-[88vh]">
            <button
              onClick={() => setFullscreenImage(null)}
              className="absolute top-2 right-2 z-20 w-9 h-9 rounded-full bg-white/95 text-[#2B2926] hover:bg-white hover:scale-105 active:scale-95 shadow-lg flex items-center justify-center transition-all"
              aria-label="Close preview"
            >
              <FiX size={18} strokeWidth={2.5} />
            </button>
            {(() => {
              const url = String(fullscreenImage);
              // YouTube embed iframe — same as before.
              if (url.includes('youtube.com/embed')) {
                return (
                  <div className="w-[min(90vw,800px)] aspect-video rounded-xl overflow-hidden shadow-[0_24px_60px_-12px_rgba(0,0,0,0.35)] border-4 border-white bg-black">
                    <iframe
                      src={fullscreenImage}
                      className="w-full h-full"
                      title="Video preview"
                      allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
                      allowFullScreen
                    />
                  </div>
                );
              }
              // Raw video file (TikTok publishes leave the S3 mp4 URL on
              // image_url since there's no public thumbnail). Detect any
              // common video extension and play it natively. We strip the
              // query string before checking so signed URLs still match.
              const path = url.split('?')[0].toLowerCase();
              const isVideoFile = /\.(mp4|mov|webm|m4v)$/.test(path);
              if (isVideoFile) {
                return (
                  <div className="w-[min(90vw,800px)] aspect-video rounded-xl overflow-hidden shadow-[0_24px_60px_-12px_rgba(0,0,0,0.35)] border-4 border-white bg-black">
                    <video
                      src={fullscreenImage}
                      controls
                      autoPlay
                      className="w-full h-full object-contain"
                    />
                  </div>
                );
              }
              // Plain image — original fallback.
              return (
                <img
                  src={fullscreenImage}
                  className="block max-w-full max-h-[88vh] object-contain rounded-xl shadow-[0_24px_60px_-12px_rgba(0,0,0,0.35)] border-4 border-white"
                  alt="Fullscreen view"
                />
              );
            })()}
          </div>
        </div>
      )}

      {/* Sentiment Analysis Modal */}
      {sentimentPost && (
        <SentimentAnalysisModal
          post={sentimentPost}
          data={sentimentData}
          loading={isAnalyzingSentiment}
          error={sentimentError}
          authAxios={authAxios}
          onRefresh={() => handleSentimentAnalysis(sentimentPost, true)}
          cachedComments={commentsCache[`${sentimentPost.platform}_${sentimentPost.native_id}`]}
          onClose={() => {
            setSentimentPost(null);
            setSentimentData(null);
            setSentimentError(null);
          }}
        />
      )}
    </div>
  );
};

const PostDetailModal = ({ post, onClose, timezone, onExpandImage, authAxios, cachedComments, onUpdateCache }) => {
  const content = React.useMemo(() => {
    if (!post.content) return "";
    if (post.content.trim().startsWith('{')) {
      try {
        const parsed = JSON.parse(post.content);
        return parsed[post.platform] || parsed.default || Object.values(parsed)[0] || post.content;
      } catch (e) { return post.content; }
    }
    return post.content;
  }, [post]);

  // Lock background page scroll while the post-detail modal is open so
  // the page doesn't shift around behind the lightbox (which made the
  // modal appear to "jump" up/down depending on where the user clicked).
  //
  // The actual scrolling element is the <main className="overflow-auto">
  // wrapper inside App.jsx, NOT document.body — locking body.overflow
  // alone has no effect. We lock html, body, AND every <main> in the
  // document tree. Restored on unmount.
  React.useEffect(() => {
    const html = document.documentElement;
    const body = document.body;
    const mains = Array.from(document.querySelectorAll('main'));

    const restorers = [];
    const lock = (el) => {
      const prev = el.style.overflow;
      el.style.overflow = 'hidden';
      restorers.push(() => { el.style.overflow = prev; });
    };
    lock(html);
    lock(body);
    mains.forEach(lock);

    return () => { restorers.forEach((r) => r()); };
  }, []);

  const commentsSupported = ['linkedin', 'facebook', 'instagram', 'twitter', 'youtube', 'tiktok'].includes(post.platform);
  const [comments, setComments] = useState([]);
  // Mirror `comments` into a ref so loadComments can read the freshest
  // value (including optimistic _local replies) without depending on
  // `comments` in its deps array — that would re-create the callback
  // and re-fire the mount useEffect, causing an infinite refetch loop.
  const commentsRef = React.useRef([]);
  React.useEffect(() => { commentsRef.current = comments; }, [comments]);
  const [loadingComments, setLoadingComments] = useState(false);
  const [commentsError, setCommentsError] = useState(null);
  const [commentsNote, setCommentsNote] = useState(null);
  const [replyingTo, setReplyingTo] = useState(null); // comment id for FB/IG; 'root' for LinkedIn
  const [replyText, setReplyText] = useState("");
  const [replyBusy, setReplyBusy] = useState(false);
  // Agent Post — when enabled, the modal calls /reputation/generate-replies
  // for unreplied comments and renders the AI suggestion next to each one.
  // Same backend that powers the Reputation page so behaviour stays consistent.
  const [agentMode, setAgentMode] = useState(false);
  const [aiReplies, setAiReplies] = useState({}); // {comment_id: suggested_text}
  const [generatingReplies, setGeneratingReplies] = useState(false);

  // Live per-post metrics. Seeded from the post prop so the tiles paint
  // instantly with whatever numbers the parent list already loaded, then
  // replaced with the freshest counts once the auto-refresh finishes.
  // The Refresh Stats button below hits the same endpoint on demand.
  const [liveMetrics, setLiveMetrics] = React.useState({
    likes: post.likes || 0,
    comments: post.comments || 0,
    shares: post.shares || 0,
    reach: post.reach || 0,
  });
  const [refreshingStats, setRefreshingStats] = React.useState(false);

  // Background sync hint — set to true when the last /refresh response
  // told us the account-wide sync was just queued. Auto-clears after a
  // minute so it doesn't linger forever.
  const [bgSyncing, setBgSyncing] = React.useState(false);
  const refreshStats = React.useCallback(async () => {
    if (!post?.id) return;
    setRefreshingStats(true);
    try {
      // Scope the request to THIS platform/native_id — the Campaign
      // Performance table shows one row per (post × platform), so
      // clicking "Instagram Post" must load Instagram-only numbers.
      // Without these params the backend previously SUMMED across
      // every platform the post was cross-posted to (IG+FB+X+LinkedIn),
      // producing 4× the like count vs the row that was clicked.
      const params = {};
      if (post.platform) params.platform = post.platform;
      if (post.native_id) params.native_id = post.native_id;
      const res = await authAxios.post(`/analytics/posts/${post.id}/refresh`, null, { params });
      const m = res?.data?.metrics;
      if (m) {
        setLiveMetrics({
          likes: m.likes || 0,
          comments: m.comments || 0,
          shares: m.shares || 0,
          reach: m.reach || 0,
        });
      }
      // If backend queued the account-wide sync, show a subtle hint so
      // the user knows fresher numbers are on their way (next click).
      if (res?.data?.sync_status === 'queued') {
        setBgSyncing(true);
        // Auto-clear after 60s — the sync typically finishes in that window
        // and we don't want the hint to hang around indefinitely.
        setTimeout(() => setBgSyncing(false), 60000);
      }
    } catch (e) {
      // Non-fatal — tiles keep the stale numbers already visible.
      console.warn('[stats-refresh] failed', e?.response?.data?.detail || e?.message);
    } finally {
      setRefreshingStats(false);
    }
  }, [post?.id, authAxios]);

  // Auto-refresh on modal open. Runs once per post — reopening a different
  // post re-fires because post.id changes.
  React.useEffect(() => {
    refreshStats();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [post?.id]);

  const generateAiReplies = React.useCallback(async () => {
    const candidates = comments.filter(c => !c.parent_id && !aiReplies[c.id]);
    if (candidates.length === 0) return;
    setGeneratingReplies(true);
    try {
      // Pull the per-platform copy out of the JSON post.content, falling
      // back to whatever default text we have. Mirrors Reputation page.
      let contextText = post.content || "";
      if (typeof contextText === 'string' && contextText.startsWith('{')) {
        try {
          const parsed = JSON.parse(contextText);
          contextText = parsed[post.platform] || parsed.default || Object.values(parsed)[0] || "";
        } catch (e) { /* fall through */ }
      }
      const res = await authAxios.post('/reputation/generate-replies', {
        comments: candidates.map(c => ({
          id: c.id,
          message: c.message || c.text || "",
          author_name: c.author_name,
          author_handle: c.author_handle,
        })),
        post_context: contextText,
      });
      if (res.data?.replies) {
        setAiReplies(prev => {
          const next = { ...prev };
          for (const r of res.data.replies) {
            if (r.id && r.generated_reply) next[r.id] = r.generated_reply;
          }
          return next;
        });
      }
    } catch (err) {
      console.error("Agent post generate failed:", err);
    } finally {
      setGeneratingReplies(false);
    }
  }, [comments, aiReplies, post.content, post.platform, authAxios]);

  // When agentMode flips ON, kick off generation. When OFF, clear the
  // suggestions so the UI resets cleanly.
  React.useEffect(() => {
    if (agentMode && comments.length > 0 && !generatingReplies) {
      generateAiReplies();
    }
    if (!agentMode) {
      setAiReplies({});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentMode, comments.length]);

  const loadComments = React.useCallback(async (forceRefresh = false) => {
    if (!authAxios || !commentsSupported || !post?.id) return;
    
    // Check cache first
    if (!forceRefresh && cachedComments) {
      setComments(cachedComments);
      return;
    }

    setLoadingComments(true);
    setCommentsError(null);
    setCommentsNote(null);
    // Capture in-flight optimistic replies so the refetch doesn't wipe them.
    // LinkedIn / Facebook can take 30 s+ to surface org replies in the
    // GET response, but the user wants their reply to stay visible
    // immediately after Send.
    const localReplies = (commentsRef.current || []).filter(c => c._local);
    try {
      // Pass instance_id so the backend hits the RIGHT platform_post row
      // when a single PublishedPost has multiple instances (e.g. LinkedIn
      // personal + LinkedIn page). Without it the backend picks whichever
      // row `.first()` returns, and the modal renders the wrong account's
      // comment state.
      const params = new URLSearchParams({ platform: post.platform });
      // The Analytics detail-modal shape sometimes carries these as
      // `instance_id` and sometimes just uses the post row's account
      // fields. Send whatever we have; the backend ignores unknown keys.
      if (post._platformInstanceId) params.set('instance_id', String(post._platformInstanceId));
      if (post.native_id) params.set('native_id', String(post.native_id));
      if (post.account_id) params.set('account_id', String(post.account_id));
      const res = await authAxios.get(`/analytics/posts/${post.id}/comments?${params.toString()}`);
      const data = res.data || {};
      if (data.supported === false) {
        setCommentsNote(data.reason || "Comments not available for this platform.");
        setComments(localReplies);
      } else {
        const fetchedComments = Array.isArray(data.comments) ? data.comments : [];
        // Dedupe: if the platform finally surfaced a reply that we'd
        // inserted optimistically (matched by trimmed message text),
        // drop our local copy in favour of the platform's authoritative
        // version (which has the real id + author).
        const fetchedMessages = new Set(fetchedComments.map(c => (c.message || '').trim()));
        const survivingLocals = localReplies.filter(
          r => !fetchedMessages.has((r.message || '').trim())
        );
        const merged = [...fetchedComments, ...survivingLocals];
        setComments(merged);
        onUpdateCache(post.id, merged);
        if (data.error) setCommentsError(data.error);
      }
    } catch (err) {
      setCommentsError(err?.response?.data?.detail || err?.message || "Failed to load comments");
    } finally {
      setLoadingComments(false);
    }
  }, [authAxios, post?.id, post?.platform, commentsSupported, cachedComments, onUpdateCache]);

  useEffect(() => { loadComments(); }, [loadComments]);

  const submitReply = async () => {
    const text = replyText.trim();
    if (!text || !replyingTo) return;
    setReplyBusy(true);
    try {
      const body = { message: text };
      // 'root' means top-level comment on the post; otherwise it's a reply
      // to a specific comment — pass its id/URN as parent_comment_id.
      if (replyingTo !== 'root') body.parent_comment_id = replyingTo;
      const sendRes = await authAxios.post(
        `/analytics/posts/${post.id}/comments/reply?platform=${post.platform}`,
        body,
      );

      // Optimistic insert — show the reply we just sent immediately,
      // before the next comment refetch (LinkedIn's API can take 30s+
      // to surface our own reply on a fresh GET, which made it look
      // like the Send button did nothing). Backend echoes the new
      // comment id when it can; otherwise we generate a temp one.
      const echoed = sendRes?.data?.comment || {};
      const tempId = echoed.id || `local-${Date.now()}`;
      const optimistic = {
        id: tempId,
        parent_id: replyingTo === 'root' ? '' : replyingTo,
        author_name: echoed.author_name || 'You',
        author_picture: echoed.author_picture || null,
        author_handle: echoed.author_handle || '',
        message: text,
        created_at: echoed.created_at || new Date().toISOString(),
        like_count: 0,
        reply_count: 0,
        platform: post.platform,
        _local: true, // flag so the next refetch can dedupe if needed
      };
      setComments(prev => [...prev, optimistic]);
      onUpdateCache && onUpdateCache([...comments, optimistic]);

      const sentTo = replyingTo;
      setReplyText("");
      setReplyingTo(null);
      // Background refetch — replaces the optimistic row with the real
      // one once LinkedIn / Facebook / etc. surfaces it.
      setTimeout(() => loadComments(true), 1500);
      void sentTo;
    } catch (err) {
      setCommentsError(err?.response?.data?.detail || err?.message || "Reply failed");
    } finally {
      setReplyBusy(false);
    }
  };

  // Portal the modal to document.body. The Analytics page is wrapped in
  // an `animate-in fade-in` parent that applies `transform`, which creates
  // a new containing block for `position: fixed`. That made the modal
  // scroll with the page (the screenshots showing the popup cut off at
  // the top with page content visible below it). Rendering through a
  // portal escapes that ancestor and pins the modal to the viewport.
  return createPortal(
    <div className="fixed inset-0 z-[150] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-slate-900/60 backdrop-blur-sm" onClick={onClose} />
      <div className="bg-white w-full max-w-5xl max-h-[90vh] rounded-3xl overflow-hidden shadow-2xl relative z-10 animate-in zoom-in-95 fade-in duration-300 border-2 border-orange-200 flex flex-col">
        <div className="flex items-center justify-between p-6 border-b-2 border-[#2B2926]/30 flex-shrink-0 bg-white">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-slate-50 flex items-center justify-center border border-[#2B2926]/30">
              <PlatformIcon platform={post.platform} />
            </div>
            <div>
              <h4 className="font-bold text-[#2B2926] capitalize">{post.platform} Post</h4>
              <p className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest leading-none">
                Published on {formatInTimezone(post.publish_date, timezone)}
              </p>
            </div>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-slate-100 rounded-xl text-[#2B2926] transition-all">
            <FiX size={20} />
          </button>
        </div>
        
        <div className="p-6 lg:p-8 overflow-y-auto custom-scrollbar">
          {/* Two-column layout (responsive): image on the LEFT, content +
              stats stacked on the RIGHT. On narrow screens it falls back
              to a single column with image-on-top so the post is still
              readable on tablet / mobile. */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 lg:gap-8 mb-8">
            {/* LEFT — playable YouTube video, image, or text-only fallback */}
            <div className="relative group/modalimg">
              {(() => {
                // YouTube video posts store the uploaded clip in image_url,
                // which can't render in an <img>. Embed the real player
                // (derived from native_id) so the video actually plays.
                const ytId = post.platform === 'youtube' && post.native_id
                  ? String(post.native_id).trim() : '';
                const thumb = getPostThumb(post);
                if (isDocumentMedia(post)) {
                  return <DocCard url={post.image_url} thumbnailUrl={post.thumbnail_url} />;
                }
                if (ytId) {
                  return (
                    <div className="w-full aspect-video rounded-2xl overflow-hidden border border-[#2B2926]/30 bg-black">
                      <iframe
                        src={`https://www.youtube.com/embed/${ytId}`}
                        title="YouTube video"
                        allow="accelerometer; autoplay; encrypted-media; picture-in-picture; fullscreen"
                        allowFullScreen
                        className="w-full h-full"
                      />
                    </div>
                  );
                }
                // TikTok video posts: image_url holds the S3 URL of the
                // uploaded clip. TikTok's own embed iframe only works for
                // public videos, and our sandbox publishes default to
                // SELF_ONLY which TikTok wouldn't embed anyway. The
                // pre-publish S3 file plays directly in <video>, which
                // also lets the user verify the exact file that was sent.
                if (post.platform === 'tiktok' && post.image_url) {
                  return (
                    <div className="w-full aspect-video rounded-2xl overflow-hidden border border-[#2B2926]/30 bg-black">
                      <video
                        src={post.image_url}
                        controls
                        className="w-full h-full object-contain"
                      />
                    </div>
                  );
                }
                if (thumb) {
                  return (
                    <>
                      <div className="w-full rounded-2xl overflow-hidden border border-[#2B2926]/30 bg-slate-50">
                        <img
                          src={thumb}
                          className="w-full h-auto max-h-[520px] object-contain mx-auto"
                          alt="Post design"
                        />
                      </div>
                      <button
                        onClick={() => onExpandImage(thumb)}
                        className="absolute top-4 right-4 p-3 bg-white/90 backdrop-blur shadow-xl rounded-xl text-[#2B2926] opacity-0 group-hover/modalimg:opacity-100 transition-all active:scale-95 flex items-center gap-2 text-xs font-semibold uppercase tracking-widest"
                      >
                        <FiMaximize2 size={16} /> Full View
                      </button>
                    </>
                  );
                }
                return (
                  <div className="w-full h-full min-h-[260px] rounded-2xl border-2 border-dashed border-[#2B2926]/30 bg-slate-50/50 flex flex-col items-center justify-center text-[#2B2926]">
                    <FiActivity className="w-10 h-10 mb-2" />
                    <span className="text-[10px] font-semibold uppercase tracking-widest">Text-only post</span>
                  </div>
                );
              })()}
            </div>

            {/* RIGHT — content + stats */}
            <div className="flex flex-col gap-5 min-w-0">
              <div className="bg-slate-50/50 p-5 rounded-2xl border border-[#2B2926]/30 whitespace-pre-wrap text-[#2B2926] font-medium leading-relaxed text-sm max-h-[400px] overflow-y-auto custom-scrollbar">
                {content}
              </div>

              {/* Stats header + Refresh Stats button — auto-refreshes
                  on modal open so users always see the freshest counts
                  without waiting for the global 30-90s /analytics/sync.
                  Manual button re-fires the same lightweight per-post
                  sync (only touches the accounts that published the
                  post, ~5s). */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-semibold uppercase tracking-widest text-[#2B2926]">Post Stats</span>
                  {bgSyncing && (
                    <span
                      title="Fresh numbers are syncing from the platform in the background. Click Refresh again in ~30s to see them."
                      className="flex items-center gap-1 text-[9px] font-semibold uppercase tracking-widest text-[#F55600] bg-orange-50 px-2 py-0.5 rounded-full"
                    >
                      <FiRefreshCw size={9} className="animate-spin" />
                      Syncing…
                    </span>
                  )}
                </div>
                <button
                  type="button"
                  onClick={refreshStats}
                  disabled={refreshingStats}
                  className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-widest text-[#2B2926] hover:text-[#F55600] disabled:opacity-40"
                  title="Refresh stats for this post"
                >
                  <FiRefreshCw size={12} className={refreshingStats ? 'animate-spin' : ''} />
                  {refreshingStats ? 'Refreshing…' : 'Refresh'}
                </button>
              </div>
              <div className={`grid grid-cols-2 sm:grid-cols-4 gap-3 transition-opacity ${refreshingStats ? 'opacity-70' : 'opacity-100'}`}>
                {[
                  { label: 'Likes',    value: liveMetrics.likes    || 0, icon: <FiArrowUpRight  className="text-orange-500" /> },
                  { label: 'Comments', value: liveMetrics.comments || 0, icon: <FiMessageCircle className="text-blue-500" />   },
                  { label: 'Shares',   value: liveMetrics.shares   || 0, icon: <FiRefreshCw     className="text-green-500" />  },
                  { label: 'Reach',    value: liveMetrics.reach    || 0, icon: <FiEye           className="text-purple-500" /> },
                ].map((stat, i) => (
                  <div key={i} className="bg-white p-3 rounded-2xl border border-[#2B2926]/30 shadow-sm text-center">
                    <div className="flex justify-center mb-1">{stat.icon}</div>
                    <div className="text-sm font-semibold text-[#2B2926]">{stat.value.toLocaleString()}</div>
                    <div className="text-[8px] font-semibold uppercase text-[#2B2926] tracking-widest">{stat.label}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Comments section */}
          <div className="mt-8 border-t-2 border-[#2B2926]/30 pt-6">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <FiMessageCircle className="text-[#F55600]" size={18} />
                <h5 className="font-semibold text-[#2B2926] text-sm">Comments</h5>
                {comments.length > 0 && (
                  <span className="text-[10px] font-semibold bg-orange-50 text-[#F55600] px-2 py-0.5 rounded-full">
                    {comments.length}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-3">
                {/* Agent Post toggle — same behaviour as Reputation page.
                    When ON, fires /reputation/generate-replies for the
                    loaded comments and shows a suggested reply next to
                    each one. */}
                {commentsSupported && (
                  <label className="flex items-center gap-2 cursor-pointer select-none" title="Use AI to suggest replies for these comments">
                    <span className="text-[10px] font-semibold uppercase tracking-widest text-[#2B2926]">
                      Agent Post {generatingReplies && '…'}
                    </span>
                    <span
                      onClick={() => setAgentMode(v => !v)}
                      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${agentMode ? 'bg-[#F55600]' : 'bg-slate-200'}`}
                    >
                      <span
                        className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform ${agentMode ? 'translate-x-5' : 'translate-x-1'}`}
                      />
                    </span>
                  </label>
                )}
                <button
                  onClick={() => loadComments(true)}
                  disabled={loadingComments || !commentsSupported}
                  className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-widest text-[#2B2926] hover:text-[#F55600] disabled:opacity-40"
                >
                  <FiRefreshCw size={12} className={loadingComments ? 'animate-spin' : ''} />
                  Refresh
                </button>
              </div>
            </div>

            {!commentsSupported && (
              <div className="text-xs text-[#2B2926] italic py-4">
                Comment management isn't supported for {post.platform} yet.
              </div>
            )}

            {commentsSupported && commentsNote && (
              <div className="text-xs text-amber-600 bg-amber-50 border border-amber-100 rounded-xl px-3 py-2 mb-3">
                {commentsNote}
              </div>
            )}

            {commentsSupported && commentsError && (
              <div className="text-xs text-red-600 bg-red-50 border border-red-100 rounded-xl px-3 py-2 mb-3">
                {commentsError}
              </div>
            )}

            {commentsSupported && loadingComments && (
              <div className="flex items-center justify-center py-6">
                <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-[#F55600]"></div>
              </div>
            )}

            {commentsSupported && !loadingComments && !commentsNote && comments.length === 0 && !commentsError && (
              <div className="text-xs text-[#2B2926] italic py-4 text-center">
                No comments yet on this post.
              </div>
            )}

            {commentsSupported && comments.length > 0 && (
              <div className="space-y-3 max-h-[320px] overflow-y-auto pr-1">
                {comments.map((c) => {
                  const isReply = !!c.parent_id;
                  // LinkedIn: reply to top-level comments only (nested replies
                  // use the same parent). FB/IG: allow reply to any comment.
                  const canReply = post.platform === 'linkedin' ? !isReply : true;
                  const replyKey = c.id;
                  const showingReplyBox = replyingTo === replyKey;
                  return (
                    <div
                      key={c.id}
                      className={`bg-slate-50/70 rounded-xl p-3 border border-[#2B2926]/30 ${isReply ? 'ml-6 border-l-2 border-l-orange-200' : ''}`}
                    >
                      <div className="flex items-start gap-3">
                        <div className="w-8 h-8 rounded-full bg-gradient-to-br from-orange-200 to-orange-400 flex-shrink-0 flex items-center justify-center overflow-hidden text-[10px] font-semibold text-white uppercase">
                          {c.author_picture ? (
                            <img src={c.author_picture} alt="" className="w-full h-full object-cover" />
                          ) : (
                            (c.author_name || '?').slice(0, 2)
                          )}
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-0.5">
                            <span className="text-xs font-semibold text-[#2B2926] truncate">{c.author_name}</span>
                            {c.created_at && (
                              <span className="text-[9px] font-bold text-[#2B2926] uppercase tracking-wider">
                                {new Date(c.created_at).toLocaleString()}
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-[#2B2926] whitespace-pre-wrap break-words leading-relaxed">
                            {c.message || <em className="text-[#2B2926]">(no text)</em>}
                          </p>

                          {/* Agent-Post suggestion (only on root comments).
                              Click "Use" to drop the suggested text into
                              the reply textarea; user still hits Send Reply
                              themselves so they stay in control. */}
                          {agentMode && !isReply && aiReplies[c.id] && (
                            <div className="mt-2 rounded-xl border-2 border-orange-200 bg-orange-50/50 p-2.5">
                              <div className="text-[8px] font-semibold uppercase tracking-widest text-[#F55600] mb-1 flex items-center gap-1.5">
                                <FiActivity size={10} /> AI suggested reply
                              </div>
                              <div className="text-xs text-[#2B2926] leading-relaxed mb-2 whitespace-pre-wrap break-words">
                                {aiReplies[c.id]}
                              </div>
                              <div className="flex items-center justify-end gap-2">
                                <button
                                  onClick={() => {
                                    setReplyingTo(replyKey);
                                    setReplyText(aiReplies[c.id]);
                                  }}
                                  className="text-[10px] font-semibold uppercase tracking-widest text-[#F55600] hover:underline"
                                >
                                  Use this reply
                                </button>
                              </div>
                            </div>
                          )}

                          <div className="flex items-center gap-3 mt-2 text-[10px] font-bold text-[#2B2926]">
                            {c.like_count > 0 && (
                              <span className="flex items-center gap-1"><FiHeart size={10} /> {c.like_count}</span>
                            )}
                            {c.reply_count > 0 && (() => {
                              // LinkedIn org pages report a reply_count via
                              // commentsSummary.aggregatedTotalComments but
                              // /socialActions/{commentUrn}/comments returns
                              // an empty list unless the calling app has
                              // Community Management partner access. Detect
                              // the "phantom reply" case (count > 0 but no
                              // actual replies in the local list with this
                              // comment as parent) and tell the user instead
                              // of leaving them confused.
                              const fetchedRepliesForThis = (comments || []).filter(
                                x => x.parent_id && (
                                  x.parent_id === replyKey ||
                                  (typeof x.parent_id === 'string' && x.parent_id.endsWith(`,${replyKey})`))
                                )
                              ).length;
                              const phantom = post.platform === 'linkedin' && fetchedRepliesForThis === 0 && c.reply_count > 0;
                              return phantom ? (
                                <span
                                  className="flex items-center gap-1 text-amber-500"
                                  title="LinkedIn reports replies on this comment but its API doesn't expose them to non-partner apps."
                                >
                                  <FiMessageCircle size={10} /> {c.reply_count} (hidden by LinkedIn)
                                </span>
                              ) : (
                                <span className="flex items-center gap-1"><FiMessageCircle size={10} /> {c.reply_count}</span>
                              );
                            })()}
                            {canReply && (
                              <button
                                onClick={() => {
                                  setReplyingTo(showingReplyBox ? null : replyKey);
                                  setReplyText(showingReplyBox ? "" : (aiReplies[c.id] || ""));
                                }}
                                className="ml-auto text-[#F55600] hover:underline uppercase tracking-widest"
                              >
                                {showingReplyBox ? 'Cancel' : 'Reply'}
                              </button>
                            )}
                          </div>

                          {showingReplyBox && (
                            <div className="mt-3 flex flex-col gap-2">
                              <textarea
                                value={replyText}
                                onChange={(e) => setReplyText(e.target.value)}
                                rows={2}
                                placeholder={`Reply as your ${post.platform} account…`}
                                className="w-full text-xs rounded-xl border border-[#2B2926]/30 bg-white px-3 py-2 focus:outline-none focus:border-[#F55600]/50 focus:ring-2 focus:ring-orange-50"
                              />
                              <div className="flex items-center justify-end gap-2">
                                <button
                                  onClick={() => { setReplyingTo(null); setReplyText(""); }}
                                  className="text-[10px] font-semibold uppercase tracking-widest text-[#2B2926] hover:text-[#2B2926] px-3 py-1.5"
                                >
                                  Cancel
                                </button>
                                <button
                                  onClick={submitReply}
                                  disabled={replyBusy || !replyText.trim()}
                                  className="text-[10px] font-semibold uppercase tracking-widest bg-[#F55600] text-white px-4 py-1.5 rounded-lg disabled:opacity-40 hover:bg-[#F55600] transition-colors"
                                >
                                  {replyBusy ? 'Sending…' : 'Send Reply'}
                                </button>
                              </div>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {/* LinkedIn Org-wide reply composer (reply as top-level comment on post).
                Two states:
                  (a) collapsed → dashed-orange button that opens the composer.
                  (b) open      → textarea + Send button. submitReply() POSTs
                      to /analytics/posts/{id}/comments/reply with no
                      parent_comment_id (because replyingTo === 'root') so the
                      backend creates a top-level comment on the post itself. */}
            {commentsSupported && post.platform === 'linkedin' && replyingTo !== 'root' && (
              <button
                onClick={() => { setReplyingTo('root'); setReplyText(""); }}
                className="mt-3 w-full text-[10px] font-semibold uppercase tracking-widest text-[#F55600] border-2 border-dashed border-orange-200 rounded-xl py-2.5 hover:bg-orange-50 transition-colors"
              >
                + Add a comment from your page
              </button>
            )}
            {commentsSupported && post.platform === 'linkedin' && replyingTo === 'root' && (
              <div className="mt-3 flex flex-col gap-2 border-2 border-orange-200 rounded-xl bg-orange-50/30 p-3">
                <span className="text-[10px] font-semibold uppercase tracking-widest text-[#F55600]">
                  Comment as your page
                </span>
                <textarea
                  value={replyText}
                  onChange={(e) => setReplyText(e.target.value)}
                  rows={3}
                  autoFocus
                  placeholder="Write your comment…"
                  className="w-full text-xs rounded-xl border border-[#2B2926]/30 bg-white px-3 py-2 focus:outline-none focus:border-[#F55600]/50 focus:ring-2 focus:ring-orange-50"
                />
                <div className="flex items-center justify-end gap-2">
                  <button
                    onClick={() => { setReplyingTo(null); setReplyText(""); }}
                    className="text-[10px] font-semibold uppercase tracking-widest text-[#2B2926] hover:text-[#2B2926] px-3 py-1.5"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={submitReply}
                    disabled={replyBusy || !replyText.trim()}
                    className="text-[10px] font-semibold uppercase tracking-widest bg-[#F55600] text-white px-4 py-1.5 rounded-lg disabled:opacity-40 hover:bg-[#F55600] transition-colors"
                  >
                    {replyBusy ? 'Posting…' : 'Post Comment'}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>,
    document.body
  );
};

const KpiCard = ({ title, value, change, isUp, icon, color, bg, breakdown }) => {
  const [showDropdown, setShowDropdown] = useState(false);
  const cardRef = useRef(null);
  const [menuRect, setMenuRect] = useState(null);

  // Compute menu position when dropdown opens / on scroll / resize, so it
  // tracks the card. Portal-rendered to document.body so the dropdown
  // never gets trapped behind a sibling card's stacking context (caused
  // by the parent grid's transformed children).
  useLayoutEffect(() => {
    if (!showDropdown) { setMenuRect(null); return undefined; }
    const reposition = () => {
      const el = cardRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      setMenuRect({ left: r.left, top: r.bottom + 6, width: Math.max(240, r.width) });
    };
    reposition();
    window.addEventListener('scroll', reposition, true);
    window.addEventListener('resize', reposition);
    return () => {
      window.removeEventListener('scroll', reposition, true);
      window.removeEventListener('resize', reposition);
    };
  }, [showDropdown]);

  return (
    <div
      ref={cardRef}
      className="relative px-4 py-3 xl:px-5 xl:py-3.5 rounded-2xl border border-[#2B2926]/30 bg-white shadow-[0_10px_30px_rgba(17,17,17,0.07)] hover:shadow-[0_18px_42px_rgba(17,17,17,0.10)] hover:-translate-y-0.5 transition-all duration-300 group cursor-pointer"
      onClick={() => breakdown && setShowDropdown(!showDropdown)}
    >
      {/* Soft orange radial glow in top-left corner — purely decorative.
          Wrapped in its OWN overflow-hidden + rounded-2xl so the card
          itself can be overflow-visible (the Network Split dropdown lives
          below the card and would otherwise be clipped). */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 rounded-2xl overflow-hidden"
      >
        <div
          className="absolute inset-0"
          style={{
            background: 'radial-gradient(120% 80% at 0% 0%, rgba(245,86,0,0.06), transparent 55%)',
          }}
        />
      </div>


      <div className="relative z-10 flex flex-col h-full">
        {/* Top row: icon + change pill */}
        <div className="flex items-start justify-between">
          <div
            className="w-8 h-8 rounded-xl grid place-items-center"
            style={{ background: 'rgba(0,0,0,0.04)', color: '#F55600' }}
          >
            {icon}
          </div>
          {(() => {
            // "New" badge: backend returns "New" when the prior-period
            // baseline is too small to compute a meaningful percentage
            // (e.g. previous 30d had ≤10 engagement). Render as a neutral
            // grey pill — no up/down arrow — so it doesn't masquerade
            // as a +1290% growth story when the baseline is just noise.
            const isNew = (change || "").trim() === "New";
            if (isNew) {
              return (
                <div className="flex items-center gap-0.5 text-[12px] font-bold text-[#2B2926]/50">
                  New
                </div>
              );
            }
            return (
              <div
                className="flex items-center gap-0.5 text-[12px] font-bold"
                style={{ color: isUp ? '#10B981' : '#ef4444' }}
              >
                {isUp ? <FiArrowUpRight size={12} /> : <FiArrowDownRight size={12} />}
                {change}
              </div>
            );
          })()}
        </div>

        {/* Label */}
        <p
          className="font-bold uppercase tracking-[0.08em] text-[#2B2926] mt-2"
          style={{ fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"', fontSize: '11px' }}
        >
          {title}
        </p>

        {/* Value */}
        <h2 className="text-[26px] font-semibold text-[#2B2926] tracking-tight leading-none mt-1 flex items-center gap-1.5">
          {value}
          {breakdown && <FiChevronDown size={16} className="text-[#F55600]/40 group-hover:text-[#F55600] transition-colors" />}
        </h2>
      </div>

      {showDropdown && breakdown && menuRect && typeof document !== 'undefined' && createPortal((
        <>
        {/* Global backdrop (portalled to body so it sits ABOVE the chart and
            captures hover/clicks). Without this the chart underneath keeps
            firing its own tooltip, which overlaps this dropdown. */}
        <div
          onClick={() => setShowDropdown(false)}
          style={{ position: 'fixed', inset: 0, zIndex: 1999, background: 'transparent' }}
        />
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            position: 'fixed',
            top: menuRect.top,
            left: menuRect.left,
            width: menuRect.width,
            zIndex: 2000,
          }}
          className="bg-white border border-[#2B2926]/25 rounded-xl shadow-2xl p-3 animate-in fade-in slide-in-from-top-2 duration-200"
        >
          <p className="text-[8px] font-semibold uppercase text-[#2B2926] mb-3 tracking-widest px-1">Network Split</p>
          <div className="space-y-3">
            {(breakdown || []).map((item) => {
              const isItemUp = !(item.change || "").includes('-');
              return (
                <div key={item.id || item.name} className="flex items-center justify-between group/item">
                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    <div className="w-1.5 h-1.5 rounded-full shrink-0" style={{ backgroundColor: getPlatformColor(item.type || item.name) }}></div>
                    <span className="text-[10px] font-bold text-[#2B2926] truncate">
                      {(() => {
                        const platformPart = (item.type || "").charAt(0).toUpperCase() + (item.type || "").slice(1).toLowerCase();
                        const accPart = item.name.toLowerCase()
                          .replace((item.type || "").toLowerCase(), "")
                          .replace(/[()]/g, "")
                          .trim()
                          .toUpperCase();
                        return `${platformPart} (${accPart})`;
                      })()}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0 ml-2">
                    <span className="text-[10px] font-semibold text-[#2B2926]">{item.value.toLocaleString()}</span>
                    <span className={`text-[8px] font-semibold ${isItemUp ? 'text-green-600' : 'text-red-500'} bg-slate-50 px-1.5 py-0.5 rounded-md`}>
                      {item.change}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
        </>
      ), document.body)}
    </div>
  );
};

// PlatformIcon — uses the canonical PLATFORM_COLORS palette
// (linkedin #0A66C2, facebook #1877F2, twitter/X #000000, instagram
// #E1306C). Previously used slightly off shades and rendered the X
// icon in the legacy Twitter-blue, which looked teal next to the rest
// of the dashboard. Inline `style` is required because Tailwind
// arbitrary-value classes for hex codes are emitted at build time and
// the brand palette isn't part of the Tailwind config.
const PlatformIcon = ({ platform, size = 18 }) => {
  const p = (platform || '').toLowerCase();
  const cls = "mx-auto object-contain";
  const dim = { width: size, height: size };
  if (p === 'linkedin')  return <img src="/linkedlin.jpg" className={cls} style={dim} alt="LinkedIn" />;
  if (p === 'facebook')  return <img src="/facebook.png"  className={cls} style={dim} alt="Facebook" />;
  if (p === 'instagram') return <img src="/instagram.jpg" className={cls} style={dim} alt="Instagram" />;
  if (p === 'twitter' || p === 'x') return <XIcon className="mx-auto" size={size} style={{ color: '#2B2926' }} />;
  if (p === 'youtube') return <img src="/youtube-icon.png" className={cls} style={dim} alt="YouTube" />;
  if (p === 'pinterest') return <img src="/pinterest-logo.png" className={cls} style={dim} alt="Pinterest" />;
  if (p === 'tiktok') return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width={size} height={size} className="mx-auto" fill="#2B2926" aria-hidden="true">
      <path d="M12.525.02c1.31-.02 2.61-.01 3.91-.01.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.06-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.59-1.01-.14 3.39-.12 6.79-.12 10.18.06 2.1-.69 4.31-2.31 5.74-1.61 1.48-3.95 1.89-6.02 1.25-2.07-.63-3.75-2.58-4.14-4.63-.48-2.61.64-5.59 2.92-6.94 1.48-.88 3.3-.96 4.9-.4v4.25c-2.4-.64-5.11.75-5.38 3.23-.21 1.9 1.56 3.82 3.48 3.73 1.48.06 2.87-1 3.19-2.45.1-.38.12-.77.12-1.16-.01-5.1-.01-10.19-.01-15.29-.01-2.5 1.61-4.75 4-5.36z" />
    </svg>
  );
  return <FiTrendingUp className="mx-auto" size={size} style={{ color: '#94A3B8' }} />;
};

export const SentimentAnalysisModal = ({ post, data, loading, error, authAxios, onRefresh, cachedComments, onClose }) => {
  const [rawComments, setRawComments] = useState([]);
  const [loadingRaw, setLoadingRaw] = useState(false);
  const [rawError, setRawError] = useState(null);
  const [sentimentFilter, setSentimentFilter] = useState('all');

  const score = data?.overall_score || 0;

  // If we have an "insufficient data" error but many comments are actually there (less than 5),
  // we fetch them manually to display what is there.
  useEffect(() => {
    const fetchRaw = async () => {
      // Use cached comments if they exist
      if (cachedComments && cachedComments.length > 0) {
        setRawComments(cachedComments);
        return;
      }

      if (!post?.id || !authAxios) return;
      
      setLoadingRaw(true);
      try {
        // Include instance_id / native_id / account_id so the right
        // platform_post row is resolved (same reason as loadComments()).
        const params = new URLSearchParams({ platform: post.platform });
        if (post._platformInstanceId) params.set('instance_id', String(post._platformInstanceId));
        if (post.native_id) params.set('native_id', String(post.native_id));
        if (post.account_id) params.set('account_id', String(post.account_id));
        const res = await authAxios.get(`/analytics/posts/${post.id}/comments?${params.toString()}`);
        const cData = res.data || {};
        setRawComments(Array.isArray(cData.comments) ? cData.comments : []);
      } catch (err) {
        setRawError("Failed to load audience feedback.");
      } finally {
        setLoadingRaw(false);
      }
    };

    fetchRaw();
  }, [post?.id, post?.platform, authAxios, cachedComments]);
  
  // Gauge color logic (Red < 40, Yellow 40-70, Green >= 70)
  const getGaugeColor = (val) => {
    if (val > 85) return '#10B981'; // Positive Green
    if (val < 50) return '#EF4444'; // Negative Red
    return '#F55600'; // Neutral Orange
  };

  const sentimentColor = getGaugeColor(score);

  return (
    <div className="fixed inset-0 z-[160] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-slate-900/60 backdrop-blur-sm" onClick={onClose} />
      <div className="bg-white w-full max-w-xl max-h-[90vh] rounded-[2rem] overflow-hidden shadow-2xl relative z-10 animate-in zoom-in-95 fade-in duration-500 border-2 border-[#2B2926]/30 flex flex-col">
        <div className="p-5 border-b-2 border-slate-50 flex items-center justify-between bg-gradient-to-r from-slate-50 to-white flex-shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-white border-2 border-[#2B2926]/30 flex items-center justify-center shadow-sm">
              <PlatformIcon platform={post.platform} />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-[#2B2926] leading-tight">Sentiment Analysis</h3>
              <p className="text-[9px] font-semibold text-[#2B2926] border-b-2 border-[#F55600] inline-block uppercase tracking-widest">Post Audience Intel</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {data?.overall_score !== undefined && (
              <div 
                className="px-2.5 py-1 rounded-full border-2 flex items-center gap-2 shadow-sm animate-in zoom-in-50 duration-500"
                style={{ borderColor: sentimentColor, backgroundColor: `${sentimentColor}10` }}
              >
                <div className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ backgroundColor: sentimentColor }} />
                <span className="text-xs font-semibold tracking-tight" style={{ color: sentimentColor }}>
                  {score}% {score > 85 ? 'Positive' : score < 50 ? 'Negative' : 'Neutral'}
                </span>
              </div>
            )}
            <button 
              onClick={onRefresh}
              disabled={loading}
              className="w-9 h-9 flex items-center justify-center rounded-xl bg-orange-50 text-[#F55600] hover:bg-orange-100 transition-all disabled:opacity-50"
              title="Refresh analysis"
            >
              <FiRefreshCw size={16} className={loading ? 'animate-spin' : ''} />
            </button>
            <button onClick={onClose} className="w-9 h-9 flex items-center justify-center rounded-xl bg-slate-100 text-[#2B2926] hover:bg-orange-50 hover:text-[#F55600] transition-all">
              <FiX size={18} />
            </button>
          </div>
        </div>

        <div className="px-6 py-4 overflow-y-auto custom-scrollbar flex-1">
          {/* Quick Performance Context */}
          {!loading && !error && (
            <div className="flex items-center gap-1.5 mb-5 animate-in fade-in slide-in-from-top-2 duration-500">
              {[
                { label: 'Likes', value: post.likes, icon: FiHeart, color: 'text-emerald-500', bg: 'bg-emerald-50' },
                { label: 'Comments', value: post.comments, icon: FiMessageCircle, color: 'text-[#2B2926]', bg: 'bg-slate-100' },
                { label: 'Shares', value: post.shares, icon: FiShare2, color: 'text-emerald-500', bg: 'bg-emerald-50' },
                { label: 'Reach', value: post.reach, icon: FiEye, color: 'text-[#2B2926]', bg: 'bg-slate-100' },
                { label: 'Engagement', value: post.engagement, icon: FiTrendingUp, color: 'text-[#F55600]', bg: 'bg-orange-50' },
              ].map((m, idx) => (
                <div key={idx} className={`flex-1 ${m.bg} rounded-xl p-2.5 border-2 border-white shadow-sm flex flex-col items-center gap-0.5 group hover:scale-[1.02] transition-transform`}>
                  <m.icon className={`${m.color} group-hover:scale-110 transition-transform`} size={14} />
                  <span className="text-[12px] font-semibold text-[#2B2926] leading-none">{(m.value || 0).toLocaleString()}</span>
                  <span className="text-[7.5px] font-semibold uppercase text-[#2B2926] tracking-widest">{m.label}</span>
                </div>
              ))}
            </div>
          )}

          {loading ? (
            <div className="space-y-4 animate-in fade-in duration-500">
              <div className="bg-orange-50 border-2 border-orange-200/50 px-4 py-2.5 rounded-full flex items-center gap-3 overflow-hidden shadow-sm">
                <FiActivity className="text-[#F55600] shrink-0 animate-pulse" size={16} />
                <div className="flex items-center gap-3 w-full">
                  <h4 className="text-[11px] font-semibold text-[#2B2926] border-r border-orange-200 pr-3 leading-none whitespace-nowrap">AI Insight Active</h4>
                  <p className="text-[10px] font-bold text-[#2B2926] leading-none truncate flex-1">
                    Analyzing up to {post.comments} comments. This can take a few minutes...
                  </p>
                  <div className="ml-auto w-3.5 h-3.5 border-2 border-[#F55600]/30 border-t-[#F55600] rounded-full animate-spin shrink-0" />
                </div>
              </div>

              {rawComments.length > 0 && (
                <div className="space-y-3 opacity-60">
                  <div className="flex items-center gap-2 px-1">
                    <FiMessageCircle className="text-[#2B2926]" size={14} />
                    <h5 className="text-[9px] font-semibold uppercase text-[#2B2926] tracking-widest">Audience Feedback (Loading AI Tags...)</h5>
                  </div>
                  <div className="grid gap-1.5 grayscale-[20%] pointer-events-none">
                    {rawComments.map((c, i) => (
                      <div key={i} className="bg-white p-2.5 rounded-xl border-2 border-slate-50 flex items-start gap-3 shadow-sm">
                        <div className="w-8 h-8 rounded-xl bg-slate-50 border border-[#2B2926]/30 flex-shrink-0 flex items-center justify-center overflow-hidden">
                          {c.author_picture ? (
                            <img src={c.author_picture} alt="" className="w-full h-full object-cover" />
                          ) : (
                            <span className="text-[9px] font-semibold text-[#2B2926] uppercase">{(c.author_name || "?").slice(0, 2)}</span>
                          )}
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center justify-between mb-0.5">
                            <span className="text-[11px] font-semibold text-[#2B2926]">{c.author_name}</span>
                            <span className="text-[8px] font-semibold text-orange-400 uppercase tracking-widest flex items-center gap-1.5 bg-orange-50 px-1.5 py-0.5 rounded-full">
                              <span className="w-1.5 h-1.5 bg-orange-400 rounded-full animate-ping" /> Analyzing
                            </span>
                          </div>
                          <p className="text-[11px] font-bold text-[#2B2926] leading-snug italic border-l-2 border-[#2B2926]/30 pl-3 line-clamp-2">
                            "{c.message || "(No text)"}"
                          </p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {loadingRaw && rawComments.length === 0 && (
                <div className="py-12 flex flex-col items-center justify-center gap-4">
                  <div className="w-10 h-10 border-4 border-[#2B2926]/30 border-t-[#F55600] animate-spin rounded-full shadow-sm" />
                  <p className="text-[10px] font-semibold text-[#2B2926] tracking-widest uppercase">Fetching raw comments...</p>
                </div>
              )}
            </div>
          ) : (error && rawComments.length > 0) ? (
            <div className="space-y-4 animate-in fade-in slide-in-from-bottom-4 duration-700">
               {/* Fallback View: Show raw comments when < 5 */}
               <div className="bg-emerald-50 border-2 border-emerald-200/50 px-4 py-2.5 rounded-full flex items-center gap-3">
                 <FiActivity className="text-emerald-500 shrink-0" size={16} />
                 <div className="flex items-center gap-3">
                   <h4 className="text-[11px] font-semibold text-[#2B2926] border-r border-emerald-200 pr-3 leading-none">AI Insight Active</h4>
                   <p className="text-[10px] font-bold text-[#2B2926] leading-none">
                     Analyzing <span className="text-emerald-600">{rawComments.length} comments</span>. Processing deep audience sentiment.
                   </p>
                 </div>
               </div>

               <div className="space-y-3">
                 <div className="flex items-center gap-2 px-1">
                   <FiMessageCircle className="text-[#2B2926]" size={14} />
                   <h5 className="text-[9px] font-semibold uppercase text-[#2B2926] tracking-widest">Audience Feedback</h5>
                 </div>
                 <div className="grid gap-1.5">
                   {rawComments.map((c, i) => (
                     <div key={i} className="bg-white p-2.5 rounded-xl border-2 border-slate-50 flex items-start gap-3 hover:border-orange-100 transition-all shadow-sm group">
                       <div className="w-8 h-8 rounded-xl bg-slate-50 border border-[#2B2926]/30 flex-shrink-0 flex items-center justify-center overflow-hidden">
                         {c.author_picture ? (
                           <img src={c.author_picture} alt="" className="w-full h-full object-cover" />
                         ) : (
                           <span className="text-[9px] font-semibold text-[#2B2926] uppercase">{(c.author_name || "?").slice(0, 2)}</span>
                         )}
                       </div>
                       <div className="flex-1 min-w-0">
                         <div className="flex items-center justify-between mb-0.5">
                           <span className="text-[11px] font-semibold text-[#2B2926]">{c.author_name}</span>
                           <span className="text-[8px] font-semibold text-[#2B2926] uppercase tracking-widest">
                             {c.created_at ? new Date(c.created_at).toLocaleDateString() : ''}
                           </span>
                         </div>
                         <p className="text-[11px] font-bold text-[#2B2926] leading-snug italic border-l-2 border-[#2B2926]/30 pl-3 line-clamp-2 group-hover:line-clamp-none transition-all duration-300">
                           "{c.message || "(No text)"}"
                         </p>
                       </div>
                     </div>
                   ))}
                 </div>
                 
                 <div className="pt-4 flex justify-center">
                    <span className="text-[9px] font-semibold text-[#2B2926] uppercase tracking-[0.2em] bg-slate-50 px-4 py-2 rounded-full border border-[#2B2926]/30">
                       Collecting more feedback for AI analysis
                    </span>
                 </div>
               </div>
            </div>
          ) : error ? (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <div className="w-20 h-20 rounded-full bg-red-50 flex items-center justify-center mb-6 border-2 border-red-100">
                <FiActivity className="text-[#F55600]" size={32} />
              </div>
              <h4 className="text-lg font-semibold text-[#F55600] mb-2">Analysis Unavailable</h4>
              <p className="text-sm font-bold text-[#2B2926] max-w-sm px-4 leading-relaxed">
                {error}
              </p>
              {error.includes('least 5') && (
                <p className="mt-4 text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest bg-slate-50 px-3 py-2 rounded-lg">
                  Insight Requires More Feedback Data
                </p>
              )}
            </div>
          ) : data ? (
            <div className="space-y-6">
              {/* Sentiment Distribution Bar Graph */}
              <div className="animate-in fade-in slide-in-from-top-2 duration-700 bg-white py-2.5 px-4 rounded-[1.25rem] border-2 border-[#2B2926]/30 shadow-sm">
                <div className="flex items-center justify-between mb-2">
                  <h5 className="text-[10px] font-semibold uppercase text-[#2B2926] tracking-widest">Sentiment Distribution</h5>
                  <button 
                    onClick={() => setSentimentFilter('all')}
                    className={`text-[9px] font-semibold transition-all px-2 py-0.5 rounded-full ${sentimentFilter === 'all' ? 'text-[#F55600] bg-orange-50' : 'text-[#2B2926] hover:text-[#2B2926] uppercase tracking-widest'}`}
                  >
                    {(() => {
                      // Robust calculation for Total Comments shown in header
                      if (data.analyzed_comments && data.analyzed_comments.length > 0) {
                        return data.analyzed_comments.length;
                      }
                      const sc = data.sentiment_counts || {};
                      return (sc.positive || 0) + (sc.neutral || 0) + (sc.negative || 0);
                    })()} TOTAL COMMENTS
                  </button>
                </div>
                
                <div className="space-y-3">
                  {(() => {
                    // Derive counts from analyzed_comments if present, otherwise fallback to sentiment_counts
                    let counts = data.sentiment_counts || { positive: 0, neutral: 0, negative: 0 };
                    
                    if (data.analyzed_comments && data.analyzed_comments.length > 0) {
                      counts = {
                        positive: data.analyzed_comments.filter(c => {
                          const l = c.sentiment_label?.toLowerCase() || "";
                          return l.includes('positive') || l.includes('success');
                        }).length,
                        neutral: data.analyzed_comments.filter(c => (c.sentiment_label?.toLowerCase() || "").includes('neutral')).length,
                        negative: data.analyzed_comments.filter(c => {
                          const l = c.sentiment_label?.toLowerCase() || "";
                          return l.includes('negative') || l.includes('irrelevant');
                        }).length
                      };
                    }

                    const total = (counts.positive + counts.neutral + counts.negative) || 1;
                    
                    const categories = [
                      { label: 'Positive', count: counts.positive, color: '#10B981' }, // emerald-500
                      { label: 'Neutral', count: counts.neutral, color: '#F55600' },  // Requested Orange
                      { label: 'Negative', count: counts.negative, color: '#EF4444' }  // red-500
                    ];

                    return (
                      <>
                        {/* Rectangular Stacked Distribution Bar */}
                        <div className="h-4 w-full bg-slate-100 flex overflow-hidden rounded-sm">
                          {categories.map((cat, idx) => {
                             const pct = (cat.count / total) * 100;
                             if (pct === 0) return null;
                             return (
                               <div 
                                 key={idx}
                                 style={{ width: `${pct}%`, backgroundColor: cat.color }} 
                                 className="h-full transition-all duration-1000 ease-out"
                                 title={`${cat.label}: ${cat.count}`}
                               />
                             );
                          })}
                        </div>

                        {/* Legend row */}
                        <div className="flex items-center justify-between gap-4 px-1 mt-1">
                          {categories.map((cat, idx) => {
                             const pct = (cat.count / total) * 100;
                             const isActive = sentimentFilter === cat.label.toLowerCase();
                             return (
                               <button 
                                 key={idx} 
                                 onClick={() => setSentimentFilter(isActive ? 'all' : cat.label.toLowerCase())}
                                 className={`flex flex-col text-left transition-all p-1.5 rounded-xl hover:bg-slate-50 ${isActive ? 'ring-2 ring-slate-200 bg-slate-50/50 scale-[1.02]' : ''}`}
                               >
                                 <div className="flex items-center gap-1.5 mb-0.5">
                                   <div className="w-2 h-2" style={{ backgroundColor: cat.color }} />
                                   <span className="text-[9px] font-semibold uppercase text-[#2B2926] tracking-wider">
                                     {cat.label}
                                   </span>
                                 </div>
                                 <div className="text-sm font-semibold tracking-tight text-[#2B2926] leading-none">
                                   {cat.count}
                                   <span className="text-[10px] font-bold text-[#2B2926] ml-1">({Math.round(pct)}%)</span>
                                 </div>
                               </button>
                             );
                          })}
                        </div>
                      </>
                    );
                  })()}
                </div>
              </div>


              {/* Unified Comments List with Sentiment Decorators */}
              <div className="space-y-3">
                <div className="flex items-center justify-between px-1">
                  <div className="flex items-center gap-2">
                    <FiMessageCircle className="text-[#2B2926]" size={14} />
                    <h5 className="text-[9px] font-semibold uppercase text-[#2B2926] tracking-widest">
                      {sentimentFilter === 'all' ? 'Audience Feedback' : `${sentimentFilter} Feedback`}
                    </h5>
                  </div>
                  {sentimentFilter !== 'all' && (
                    <button 
                      onClick={() => setSentimentFilter('all')}
                      className="text-[8px] font-semibold text-[#F55600] uppercase tracking-widest hover:underline"
                    >
                      Clear Filter
                    </button>
                  )}
                </div>
                <div className="grid gap-1.5">
                  {rawComments
                    .filter(c => {
                      if (sentimentFilter === 'all') return true;
                      const analysis = data.analyzed_comments?.find(a => String(a.id) === String(c.id));
                      const score = analysis?.sentiment_score;
                      const rawLabel = analysis?.sentiment_label?.toLowerCase() || "";
                      let label = 'neutral';
                      
                      if (score !== undefined) {
                        if (score > 85) label = 'positive';
                        else if (score < 50) label = 'negative';
                        else label = 'neutral';
                      } else if (analysis) {
                        label = rawLabel.includes('positive') || rawLabel.includes('success') ? 'positive' : 
                                rawLabel.includes('negative') || rawLabel.includes('irrelevant') ? 'negative' : 'neutral';
                      } else {
                        const msg = (c.message || "").toLowerCase();
                        if (msg.match(/(good|great|awesome|love|thanks|interested|best|yes|opportunity)/)) label = 'positive';
                        else if (msg.match(/(bad|terrible|hate|worst|poor|no|scam)/)) label = 'negative';
                      }
                      return label === sentimentFilter;
                    })
                    .map((c, i) => {
                    const analysis = data.analyzed_comments?.find(a => String(a.id) === String(c.id));
                    const score = analysis?.sentiment_score;
                    const rawLabel = analysis?.sentiment_label?.toLowerCase() || "";
                    let label = 'neutral';
                    
                    if (score !== undefined) {
                      if (score > 85) label = 'positive';
                      else if (score < 50) label = 'negative';
                      else label = 'neutral';
                    } else if (analysis) {
                      label = rawLabel.includes('positive') || rawLabel.includes('success') ? 'positive' : 
                              rawLabel.includes('negative') || rawLabel.includes('irrelevant') ? 'negative' : 'neutral';
                    } else {
                      const msg = (c.message || "").toLowerCase();
                      if (msg.match(/(good|great|awesome|love|thanks|interested|best|yes|opportunity)/)) label = 'positive';
                      else if (msg.match(/(bad|terrible|hate|worst|poor|no|scam)/)) label = 'negative';
                    }

                    const colorMap = {
                      positive: 'bg-white text-[#10B981] border-[#10B981]/30',
                      neutral: 'bg-white text-[#F55600] border-orange-200',
                      negative: 'bg-white text-red-600 border-red-200',
                      unknown: 'bg-white text-[#2B2926] border-[#2B2926]/30'
                    };
                    return (
                      <div key={i} className="bg-white p-3 rounded-xl border-2 border-slate-50 hover:border-[#2B2926]/30 transition-all shadow-sm group animate-in fade-in slide-in-from-bottom-1 duration-300">
                        <div className="min-w-0">
                          <div className="flex items-center justify-between mb-0.5">
                            <div className="flex items-center gap-2">
                              <span className="text-[11px] font-semibold text-[#2B2926]">{c.author_name}</span>
                              <div className={`px-1.5 py-0.5 rounded text-[7px] font-semibold uppercase tracking-widest border border-dashed ${colorMap[label] || 'bg-slate-50 text-[#2B2926]'}`}>
                                {label} {score !== undefined && `(${score}%)`}
                              </div>
                            </div>
                            <span className="text-[8px] font-semibold text-[#2B2926] uppercase tracking-widest">
                              {c.created_at ? new Date(c.created_at).toLocaleDateString() : ''}
                            </span>
                          </div>
                          <p className="text-[11px] font-bold text-[#2B2926] leading-snug italic border-l-2 border-[#2B2926]/30 pl-3 line-clamp-2 group-hover:line-clamp-none transition-all duration-300">
                            "{c.message || "(No text)"}"
                          </p>
                          {analysis?.reasoning && (
                            <p className="text-[9.5px] font-bold text-[#2B2926] mt-1.5 animate-in fade-in slide-in-from-top-1">
                              <span className="text-emerald-700 font-semibold uppercase tracking-widest text-[8px] mr-1.5 underline decoration-emerald-100 decoration-2 underline-offset-2">AI Insight:</span>
                              {analysis.reasoning}
                            </p>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          ) : null}
        </div>
        
        <div className="p-6 bg-white border-t-2 border-[#2B2926]/30 text-center">
          <p className="text-[8px] font-semibold text-[#2B2926] uppercase tracking-[0.2em]">Insights powered by Pipelyt AI Engine</p>
        </div>
      </div>
    </div>
  );
};

export default Analytics;