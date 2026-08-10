import React, { useState, useEffect } from 'react';
import {
  Calendar,
  CheckCircle2,
  FileEdit,
  Plug,
  ArrowRight,
  TrendingUp,
  Clock,
  Zap,
  Users,
  Activity
} from 'lucide-react';
import BrandFilter, { EMPTY_SEL } from '../components/BrandFilter';
import { isReadOnly, roleLabel } from '../lib/permissions';

// Module-level cache so toggling between filters that the user has
// already viewed in this session paints instantly. Keyed by
// `${userId}::${qs}` — the userId prefix is the H1 fix. Previously the
// cache was keyed by qs only, which leaked User A's numbers to User B
// on the same tab after a logout+login (no reload). Value is the
// resolved payload bundle. Cleared on hard reload OR explicit call to
// clearDashboardCache() (see App.jsx handleLogout).
const _dashboardFilterCache = new Map(); // "userId::qs" -> {...}
// In-flight de-dup so rapid filter toggles don't pile up parallel
// requests for the same qs — every caller awaits the same Promise.
const _dashboardFilterInflight = new Map(); // "userId::qs" -> Promise

// H1 fix — exported so App.jsx's handleLogout can wipe the cache before
// the next user logs in on the same tab.
export const clearDashboardCache = () => {
  _dashboardFilterCache.clear();
  _dashboardFilterInflight.clear();
};

const DashboardStats = ({
  authAxios,
  connections,
  setActiveTab,
  activeTab,
  stats,
  setStats,
  analytics,
  setAnalytics,
  user,
  fetchScheduled,
  fetchPublished,
  fetchDrafts,
  fetchAnalytics,
  loadedStatus
}) => {
  // L1 fix — was `!analytics && !stats.posted`. A fresh user genuinely
  // has 0 posts, which made `loading` start as true even after the fetch
  // resolved. Only `analytics` (the API bundle) reliably distinguishes
  // "still fetching" from "zero data".
  const [loading, setLoading] = useState(!analytics);
  const [syncing, setSyncing] = useState(false);
  const [usage, setUsage] = useState(null);
  // H2 fix — surface fetch errors instead of console.error only. Cleared
  // on the next successful metrics fetch or via the Retry button.
  const [error, setError] = useState('');
  // Unified cascading brand-filter state. `brandFilter.sel` is the raw
  // picker state; `brandFilter.selectedMemberIds` is the resolved csv-ready
  // list the fetch functions should use (admin-only sentinel when no
  // filter is picked).
  const [brandFilter, setBrandFilter] = useState({
    sel: { ...EMPTY_SEL }, selectedMemberIds: [], hasAnyFilter: false,
  });

  // Build the query-string every fetch on this page should share.
  const buildFilterQs = React.useCallback((existingLeader = '?') => {
    const params = new URLSearchParams();
    const { sel, selectedMemberIds } = brandFilter;
    if (selectedMemberIds.length > 0) params.set('member_user_ids', selectedMemberIds.join(','));
    if (sel.brandIds?.length > 0) params.set('dna_product_ids', sel.brandIds.join(','));
    if (sel.country) params.set('filter_country', sel.country);
    if (sel.state) params.set('filter_state', sel.state);
    if (sel.city) params.set('filter_city', sel.city);
    if (sel.pin_code) params.set('filter_pin_code', sel.pin_code);
    const qs = params.toString();
    if (!qs) return '';
    return (existingLeader === '?' ? '?' : '&') + qs;
  }, [brandFilter]);

  const fetchDashboardMetrics = async () => {
    const qs = buildFilterQs('?');
    // H1 fix — user-scoped cache key. Two different accounts logged in
    // on the same tab now maintain independent cache entries; `handleLogout`
    // additionally calls `clearDashboardCache()` to nuke the previous
    // user's payloads on sign-out.
    const cacheKey = `${user?.id ?? 'anon'}::${qs}`;

    // Cache hit — paint instantly and trigger a background refresh so
    // the user sees stale-while-revalidate behaviour. The "spinner" is
    // suppressed (loading=false) so changing filters feels instant.
    const cached = _dashboardFilterCache.get(cacheKey);
    if (cached) {
      setAnalytics(cached.analytics);
      setUsage(cached.usage);
      if (cached.connectionCount != null) {
        setStats((prev) => ({ ...prev, connections: cached.connectionCount }));
      }
      setLoading(false);
      // Kick off the four detail-list fetchers in parallel without
      // awaiting — each updates its own state slice.
      fetchScheduled(true, qs);
      fetchPublished(true, qs);
      fetchDrafts(true, qs);
      // Continue below to silently revalidate.
    }

    // De-dup: if a request for this exact key is already in flight, await
    // it instead of stacking another round-trip on top.
    if (_dashboardFilterInflight.has(cacheKey)) {
      try { await _dashboardFilterInflight.get(cacheKey); } catch {}
      return;
    }

    const work = (async () => {
      try {
        // Run EVERY fetch in parallel — summary, usage, connections, plus
        // the three detail-list fetchers. The previous code awaited
        // summary+usage first, then awaited connections separately,
        // serialising three round-trips. This collapses them into one
        // wave so the slowest endpoint sets the wall-clock time.
        const [a, u, c] = await Promise.all([
          authAxios.get(`/analytics/summary${qs}`),
          authAxios.get('/usage'),
          authAxios.get(`/connections${qs}`),
        ]);
        // Detail lists fire alongside the summaries (don't block the UI
        // on them — each fetcher updates its own state when ready).
        fetchScheduled(true, qs);
        fetchPublished(true, qs);
        fetchDrafts(true, qs);

        setAnalytics(a.data);
        setUsage(u.data);
        const connectionCount = Object.values(c.data || {}).reduce(
          (acc, curr) => acc + (curr?.length || 0), 0,
        );
        setStats((prev) => ({ ...prev, connections: connectionCount }));

        // Cache the resolved bundle so the next time this key is selected
        // the dashboard paints instantly. Keyed by (userId, qs) per H1 fix.
        _dashboardFilterCache.set(cacheKey, {
          analytics: a.data,
          usage: u.data,
          connectionCount,
          fetchedAt: Date.now(),
        });

        // Persist dashboard analytics to local cache (unscoped — used as
        // the boot-time fallback when the page first mounts).
        try {
          localStorage.setItem('pipelyt_dashboard_analytics', JSON.stringify(a.data));
        } catch (err) {
          // L2 fix — silently ignore quota/permission errors. In private
          // browsing localStorage.setItem throws SecurityError; we can't
          // do anything meaningful about it and it's non-critical.
        }
        // H2 fix — clear any lingering error from a prior failed fetch
        // so the retry banner disappears once things recover.
        setError('');
      } catch (e) {
        // H2 fix — surface the error inline via a Retry banner instead
        // of only console.error. If the cache had a previous payload,
        // the UI still shows those numbers (stale) and the banner tells
        // the user we couldn't refresh.
        console.error('Dashboard metrics fetch failed', e);
        setError(
          e?.response?.data?.detail
          || 'Could not load dashboard data. Check your connection and retry.'
        );
      } finally {
        setLoading(false);
        setSyncing(false);
        _dashboardFilterInflight.delete(cacheKey);
      }
    })();

    _dashboardFilterInflight.set(cacheKey, work);
    await work;
  };

  // M2 fix — derive a stable primitive count so that a background
  // `connections` state update (which changes reference every setState)
  // no longer forces a full 3-endpoint refetch. Only the actual TOTAL
  // count matters here.
  const _connectionsCount = React.useMemo(
    () => Object.values(connections || {}).reduce((n, arr) => n + (arr?.length || 0), 0),
    [connections]
  );

  useEffect(() => {
    // M1 fix — skip when the user has navigated to another tab. This
    // component may briefly stay mounted during the unmount transition;
    // if we fetched then, three parallel network calls would race a
    // component that's about to disappear. Also silences the setState-
    // after-unmount warning.
    if (activeTab !== 'dashboard') return;
    if (!user) return;
    fetchDashboardMetrics();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    _connectionsCount, user?.id,
    // Stable primitive deps so `setBrandFilter({...})` with identical
    // content doesn't refetch unnecessarily.
    brandFilter.selectedMemberIds.join(','),
    brandFilter.sel.brandIds.join(','),
    brandFilter.sel.country, brandFilter.sel.state,
    brandFilter.sel.city, brandFilter.sel.pin_code,
    // NOTE: `activeTab` intentionally NOT in deps — the guard above uses
    // it, but adding it to deps would cause an unwanted refetch when
    // the user clicks a stat tile and navigates away.
  ]);

  // Stat card definitions — kept identical in behaviour to the previous
  // implementation; styling now uses Pipelyt brand palette only
  // (`#F55600` orange + `#10B981` mint + black/white with opacity).
  const statCards = [
    { label: 'Scheduled Posts', value: stats.scheduled, icon: Calendar, tone: 'orange', foot: 'ready to publish', tab: 'scheduled' },
    { label: 'Successful Posts', value: stats.posted, icon: CheckCircle2, tone: 'mint', foot: 'all-time live', tab: 'published' },
    { label: 'Saved Drafts', value: stats.drafts, icon: FileEdit, tone: 'orange', foot: 'in progress', tab: 'drafts' },
    { label: 'Connected Accounts', value: stats.connections, icon: Plug, tone: 'mint', foot: 'synced', tab: 'connections' },
  ];

  // Plan label for the inline badge in the welcome heading.
  // M3 fix — Stripe stores billing cycle in the same field
  // (e.g. 'agency_yearly', 'growth_monthly'). The badge should display
  // just the tier — "AGENCY PLAN" not "AGENCY_YEARLY PLAN".
  const planName = ((usage?.plan || user?.pricing_plan || 'free') + '')
    .toLowerCase()
    .replace(/_yearly$|_monthly$/, '');
  const showPlanBadge = !isReadOnly(user) && (usage || user?.pricing_plan);

  return (
    <div className="w-full max-w-none px-2 sm:px-4 xl:px-6 pt-4 pb-6 lg:pb-2 lg:h-full relative overflow-x-hidden lg:overflow-hidden animate-in fade-in duration-700 bg-white">
      {/* Ambient orange + mint radial-gradient blobs were removed per
          request — the welcome page background must be solid #fff with
          no decorative tint. If you want the soft-aura look back, the
          two divs (orange top-right, mint bottom-left) lived here. */}

      <div className="relative z-10 space-y-6">

      {/* H2 fix — dashboard-metrics error banner. Rendered above the
          proactive notifications so users see it first. Dismiss (×)
          clears the banner; Retry re-runs fetchDashboardMetrics(). If
          the cache had a prior payload, the KPI numbers below are still
          visible (stale) while this banner acknowledges the refresh
          failure. */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-3xl px-5 py-3 flex items-start gap-3 shadow-[0_10px_30px_rgba(0,0,0,0.04)]">
          <div className="w-6 h-6 rounded-full bg-red-500 text-white flex items-center justify-center flex-shrink-0 text-xs font-black mt-0.5">!</div>
          <div className="flex-1 text-xs font-bold text-red-700 leading-relaxed">{error}</div>
          <button
            type="button"
            onClick={() => { setError(''); fetchDashboardMetrics(); }}
            className="px-3 py-1 text-[10px] font-black text-red-700 hover:text-white hover:bg-red-500 uppercase tracking-widest rounded-lg border border-red-300 transition-colors"
          >
            Retry
          </button>
          <button
            type="button"
            onClick={() => setError('')}
            className="text-red-500 hover:text-red-700 text-lg leading-none flex-shrink-0"
            aria-label="Dismiss"
          >×</button>
        </div>
      )}

      {/* ⚠️ Proactive Notifications */}
      {usage && (
        <div className="space-y-4">
          {usage.end_date && (() => {
            const daysLeft = Math.ceil((new Date(usage.end_date) - new Date()) / (1000 * 60 * 60 * 24));
            if (daysLeft >= 0 && daysLeft <= 14) {
              return (
                <div className="bg-white border border-[#F55600]/15 p-4 rounded-3xl flex items-center justify-between shadow-[0_10px_30px_rgba(0,0,0,0.05)] animate-in slide-in-from-top-4 duration-500">
                  <div className="flex items-center gap-4">
                    <div className="w-10 h-10 bg-[#F55600]/10 rounded-2xl flex items-center justify-center">
                      <Clock className="w-5 h-5 text-[#F55600]" />
                    </div>
                    <div>
                      <p className="text-sm font-black text-[#2B2926] tracking-tight">Subscription Renewing Soon</p>
                      <p className="text-xs text-[#2B2926]/70 font-bold">Your {usage.plan} plan will renew in <span className="text-[#F55600] font-black">{daysLeft} days</span>. Ensure your payment method is active.</p>
                    </div>
                  </div>
                  <button
                    onClick={() => setActiveTab('settings')}
                    className="px-5 py-2.5 bg-white text-[#F55600] font-black text-[10px] uppercase tracking-widest rounded-xl border border-[#F55600]/20 shadow-sm hover:bg-[#F55600]/5 transition-all"
                  >
                    Manage Billing
                  </button>
                </div>
              );
            }
            return null;
          })()}

          {usage && (usage.usage?.posts / usage.limits?.posts >= 0.85) && (
            <div className="bg-white border border-[#2B2926]/30 p-4 rounded-3xl flex items-center justify-between shadow-[0_10px_30px_rgba(0,0,0,0.05)] animate-in slide-in-from-top-4 duration-500">
              <div className="flex items-center gap-4">
                <div className="w-10 h-10 bg-[#F55600]/10 rounded-2xl flex items-center justify-center">
                  <Activity className="w-5 h-5 text-[#F55600]" />
                </div>
                <div>
                  <p className="text-sm font-black text-[#2B2926] tracking-tight">Approaching Post Limit</p>
                  <p className="text-xs text-[#2B2926]/70 font-bold">You have used <span className="text-[#F55600] font-black">{usage.usage.posts}/{usage.limits.posts}</span> of your monthly posts. Upgrade to avoid interruption.</p>
                </div>
              </div>
              <button
                onClick={() => setActiveTab('settings')}
                className="px-5 py-2.5 bg-[#F55600] text-white font-black text-[10px] uppercase tracking-widest rounded-xl shadow-lg shadow-[#F55600]/20 hover:scale-[1.02] transition-all"
              >
                Upgrade Now
              </button>
            </div>
          )}
        </div>
      )}

      {/* === Welcome Header — large gradient title + plan badge + actions === */}
      <header className="flex flex-col lg:flex-row lg:items-start justify-between gap-5">
        <div>
          {/* Welcome heading — softer weight (semibold 600) + smaller
              size (38px), no over-tracked plan badge. Reads as a
              friendly greeting, not a heavy display title. */}
          <h1
            className="tracking-tight leading-[1.1]"
            style={{ fontSize: '38px', fontWeight: 600, color: '#ffffff' }}
          >
            Welcome Back,&nbsp;
            <span
              style={{
                background: 'linear-gradient(120deg, #F55600, #ff7a3d)',
                WebkitBackgroundClip: 'text',
                backgroundClip: 'text',
                color: 'transparent',
              }}
            >
              {user?.full_name || roleLabel(user?.role)}!
            </span>
            {showPlanBadge && (
              <span className="inline-flex items-center gap-1.5 ml-3 px-2.5 py-1 rounded-full text-[10px] uppercase text-white bg-[#2B2926] align-middle" style={{ fontWeight: 600, letterSpacing: '0.08em' }}>
                <Zap className="w-3 h-3 fill-current" />
                {planName} Plan
              </span>
            )}
          </h1>
          <p className="text-[#2B2926]/60 font-medium mt-3 text-[15px]">Here's what's happening with your content pipeline today.</p>
        </div>

        <div className="flex flex-col sm:flex-row sm:items-center gap-3 w-full sm:w-auto">
          {/* Admin-only unified cascading Brand Filter — replaces the old
              team-members-only picker. Filter changes refetch every KPI
              (Scheduled / Successful / Drafts / Connections / Live
              Performance / Upcoming Schedule) via `brandFilter` state. */}
          <BrandFilter
            user={user}
            authAxios={authAxios}
            value={brandFilter.sel}
            onChange={setBrandFilter}
          />

          {!isReadOnly(user) && (
          <button
            onClick={() => setActiveTab('create')}
            className="group inline-flex w-full sm:w-auto items-center justify-center gap-2 h-11 px-5 sm:px-6 rounded-2xl text-white uppercase tracking-widest whitespace-nowrap transition-all duration-300 active:scale-95 hover:-translate-y-0.5"
            style={{
              background: 'linear-gradient(120deg, #ff5a1f, #ff7e3d)',
              fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"',
              fontSize: '13px',
              fontWeight: 700,
            }}
          >
            <Zap className="w-3.5 h-3.5 fill-white shrink-0" />
            <span>Create New Post</span>
          </button>
          )}
        </div>
      </header>

      {/* === Stats Grid — 4 compact cards (slimmed to fit on screen
          without scrolling alongside the Live Performance row below) === */}
      <section className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
        {statCards.map((card, i) => {
          const Icon = card.icon;
          const isOrange = card.tone === 'orange';
          return (
            <button
              key={card.tab}
              type="button"
              onClick={() => setActiveTab(card.tab)}
              style={{ animationDelay: `${i * 80}ms` }}
              className="group text-left bg-white border border-[#2B2926]/30 rounded-2xl px-6 py-5 transition-all duration-300 hover:-translate-y-0.5 hover:shadow-[0_8px_20px_rgba(46,43,41,0.08)] animate-in zoom-in-95 slide-in-from-bottom-4 duration-700 fill-mode-both"
            >
              {/* Soft gradient icon swatch — same colours, slightly
                  smaller (32×32) + softer corner (10px) so it doesn't
                  compete with the larger KPI number. */}
              <div
                className="w-8 h-8 rounded-[10px] grid place-items-center mb-3"
                style={
                  isOrange
                    ? { background: 'linear-gradient(135deg, #ffe3d3, #ffd0b8)', color: '#F55600' }
                    : { background: 'linear-gradient(135deg, #d5f6ed, #c2f0e2)', color: '#10B981' }
                }
              >
                <Icon className="w-4 h-4" strokeWidth={2} />
              </div>
              {/* KPI label — 13px semibold uppercase. Bigger + slightly
                  bolder than the previous 11px medium so the eyebrow is
                  comfortably readable. */}
              <div
                className="uppercase text-[#2B2926]"
                style={{ fontSize: '13px', fontWeight: 600, letterSpacing: '0.06em' }}
              >
                {card.label}
              </div>
              {/* Number — semibold 36px, tight tracking. Restores the
                  number presence from the original design without going
                  back to font-black chunkiness. */}
              <div
                className="leading-none mt-2 text-[#2B2926]"
                style={{ fontSize: '36px', fontWeight: 600, letterSpacing: '-0.02em' }}
              >
                {card.value}
              </div>
              {/* Foot — semibold 12px in the accent color, with the
                  arrow that lifts on hover. */}
              <div
                className={`flex items-center gap-1 mt-2.5 ${isOrange ? 'text-[#F55600]' : 'text-[#10B981]'}`}
                style={{ fontSize: '12px', fontWeight: 600, letterSpacing: '0.02em' }}
              >
                <ArrowRight
                  className="w-3 h-3 -rotate-45 transition-transform duration-300 ease-out group-hover:translate-x-1 group-hover:-translate-y-1 group-hover:scale-110"
                />
                {card.foot}
              </div>
            </button>
          );
        })}
      </section>

      {/* === Mid-section — Live Performance (left) + Upcoming Schedule (right) === */}
      <section className="grid grid-cols-1 lg:grid-cols-[1.25fr_0.9fr] gap-4">
        {/* Live Performance */}
        <div className="bg-white rounded-[26px] border border-[#2B2926]/30 px-6 py-6 shadow-[0_12px_30px_rgba(0,0,0,0.06)] flex flex-col">
          <div className="flex items-center gap-3 mb-5">
            <div
              className="w-10 h-10 rounded-xl grid place-items-center"
              style={{ background: 'linear-gradient(135deg, #ffe3d3, #ffd0b8)', color: '#F55600' }}
            >
              <TrendingUp className="w-4 h-4" />
            </div>
            <h3
              className="text-[#2B2926] uppercase"
              style={{ fontSize: '14px', fontWeight: 600, letterSpacing: '0.06em' }}
            >
              Live Performance
            </h3>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div
              className="rounded-2xl p-5"
              style={{ background: 'linear-gradient(135deg, #111a2e, #0a1120)' }}
            >
              {/* Followers sub-label — 12px semibold sand-70 instead of
                  the chunky 11px font-black /55 it was. */}
              <div
                className="flex items-center gap-2 uppercase text-[#2B2926]"
                style={{ fontSize: '12px', fontWeight: 600, letterSpacing: '0.06em' }}
              >
                <Users className="w-3.5 h-3.5" />
                Followers
              </div>
              <div
                className="leading-none tracking-tight mt-2.5 text-[#2B2926] tabular-nums"
                style={{ fontSize: '36px', fontWeight: 600 }}
              >
                {analytics?.summary?.total_followers?.toLocaleString() || '0'}
              </div>
            </div>
            <div
              className="rounded-2xl p-5"
              style={{ background: 'linear-gradient(135deg, #09201a, #051410)' }}
            >
              {/* Engagement sub-label — same Apollo treatment as
                  Followers above. */}
              <div
                className="flex items-center gap-2 uppercase text-[#2B2926]"
                style={{ fontSize: '12px', fontWeight: 600, letterSpacing: '0.06em' }}
              >
                <Activity className="w-3.5 h-3.5" />
                Engagement
              </div>
              <div
                className="leading-none tracking-tight mt-2.5 tabular-nums"
                style={{ fontSize: '36px', fontWeight: 600, color: '#10B981' }}
              >
                {analytics?.summary?.engagement_rate || '0%'}
              </div>
            </div>
          </div>

          {/* Apollo-style soft CTA — semibold (not font-black), no
              over-tracked letterspacing, lighter resting bg with a
              clean hover state. */}
          <button
            onClick={() => setActiveTab('analytics')}
            className="mt-5 w-full py-3 rounded-2xl transition-all hover:bg-[#F55600]/15"
            style={{
              background: 'rgba(245, 86, 0, 0.12)',
              color: '#F55600',
              fontSize: '13px',
              fontWeight: 600,
              letterSpacing: '0.02em',
            }}
          >
            View Detailed Analytics →
          </button>
        </div>

        {/* Upcoming Schedule — restored to its orange hero card per
            user request. The ambient orange blob on the page itself
            (separate decorative element) was the unwanted bleed and
            is already removed; the brand colour stays here on the
            panel where it's intentional. */}
        <div
          className="relative rounded-[16px] px-6 py-6 overflow-hidden flex flex-col text-white"
          style={{ background: '#F55600' }}
        >
          <div className="relative z-10 flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl grid place-items-center bg-white/15 backdrop-blur-sm">
              <Clock className="w-4 h-4 text-white" />
            </div>
            <span
              className="uppercase text-white"
              style={{ fontSize: '13px', fontWeight: 600, letterSpacing: '0.06em' }}
            >
              Upcoming Schedule
            </span>
          </div>

          <h3
            className="relative z-10 leading-[1.15] tracking-tight my-5 text-white"
            style={{ fontSize: '30px', fontWeight: 600 }}
          >
            You have <span style={{ fontWeight: 700 }}>{stats.scheduled}</span> {stats.scheduled === 1 ? 'post' : 'posts'} ready for flight.
          </h3>

          <button
            onClick={() => setActiveTab('calendar')}
            className="relative z-10 self-start px-5 py-2.5 rounded-full transition-all duration-200 hover:bg-white/95"
            style={{
              background: '#ffffff',
              color: '#F55600',
              fontSize: '12px',
              fontWeight: 600,
              letterSpacing: '0.06em',
              textTransform: 'uppercase',
            }}
          >
            Launch Calendar
          </button>
        </div>
      </section>
      </div>
    </div>
  );
};

export default DashboardStats;
