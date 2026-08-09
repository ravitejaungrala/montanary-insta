import React, { useEffect, useMemo, useState, useCallback, useRef } from 'react';
import { isReadOnly } from '../../lib/permissions';
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell, LabelList, PieChart, Pie,
} from 'recharts';
import {
  FiSend, FiUsers, FiClock, FiAlertCircle,
  FiCalendar, FiTarget, FiActivity, FiMessageSquare, FiBriefcase,
  FiDownload, FiEye, FiMessageCircle, FiUserX, FiLoader, FiFilter, FiChevronLeft,
  FiInfo,
} from 'react-icons/fi';

/**
 * NexusDashboard — unified overview surface for the workspace.
 *
 * Merges what used to live across three tabs (Dashboard, Analytics, Reports)
 * into a single filter-driven page. Filter dimensions:
 *
 *   1. Entity type: All | Products | Services
 *   2. Specific product (only when Products or Services is selected) —
 *      drills KPIs and download into one campaign target.
 *
 * ALL data comes from ONE backend endpoint — GET /nexus/analytics/dashboard
 * — which returns every KPI + chart series fully computed for the active
 * slice (period 30d, entity_type ∈ all|product|service|gcc, product_id).
 * This component performs NO calculations; it only renders the payload.
 *
 * Download button → /nexus/reports/export (server-rendered PDF/CSV) for the
 * same slice, so the export reconciles with what's on screen.
 */

// ──────────────────────────────────────────────────────────────────────────
// helpers
// ──────────────────────────────────────────────────────────────────────────

const fmtNum = (n) => {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—';
  const v = Number(n);
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}k`;
  return `${v}`;
};

const fmtPct = (n) => {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—';
  return `${Number(n).toFixed(1)}%`;
};

const _normUtc = (iso) => {
  if (typeof iso !== 'string') return iso;
  if (/[Zz]$|[+-]\d{2}:?\d{2}$/.test(iso)) return iso;
  return iso + 'Z';
};

const relTime = (iso) => {
  if (!iso) return '';
  try {
    const t = new Date(_normUtc(iso)).getTime();
    const diff = Date.now() - t;
    if (Number.isNaN(diff) || diff < 0) return '';
    const sec = Math.floor(diff / 1000);
    if (sec < 60) return `${sec}s ago`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m ago`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}h ago`;
    const d = Math.floor(hr / 24);
    return `${d}d ago`;
  } catch {
    return '';
  }
};

const trialDaysLeft = (me) => {
  if (!me?.trial_active || !me?.trial_ends_at) return null;
  try {
    const ms = new Date(_normUtc(me.trial_ends_at)).getTime() - Date.now();
    if (Number.isNaN(ms)) return null;
    return Math.max(0, Math.ceil(ms / (1000 * 60 * 60 * 24)));
  } catch {
    return null;
  }
};

// ── Intent palette (from old Analytics tab) — render colors for the donut
// keyed by the backend's intent label.
// Intent donut palette. Distinct HUES (not one orange at varying opacity, which
// made every slice look alike) drawn from a colorblind-validated categorical set,
// mapped semantically: greens = positive outcomes, warm = negatives, blue = a
// neutral question, grey-violet = informational. Keys are UPPERCASE; the lookup
// upper-cases the label so the synthetic `demo_booked` slice matches too.
const INTENT_COLOR = {
  DEMO_BOOKED: '#008300',    // green  — booked demo (best outcome)
  DEMO_SCHEDULED: '#008300', // green  — (retired from the donut; kept for safety)
  INTERESTED: '#1BAF7A',     // aqua   — positive interest
  QUESTION: '#2A78D6',       // blue   — an inquiry
  NOT_NOW: '#EDA100',        // yellow — deferral
  NOT_INTERESTED: '#EB6834', // orange — soft no
  UNSUBSCRIBE: '#E34948',    // red    — hard no
  LEFT_COMPANY: '#4A3AA7',   // violet — informational
  OUT_OF_OFFICE: '#E87BA4',  // magenta — transient / auto-reply
};

// Note: the client-side CSV helper was removed when /nexus/reports/export
// gained product_id + entity_type query params — both CSV and PDF now
// route through the server-rendered endpoint regardless of filter state.

// ──────────────────────────────────────────────────────────────────────────
// atoms
// ──────────────────────────────────────────────────────────────────────────

const Skeleton = ({ className = '' }) => (
  <div className={`bg-[#2B2926]/5 animate-pulse rounded-lg ${className}`} />
);

// Soft custom dropdown — replaces native <select>. Selected option =
// brand orange, hovered option = very-light mint (#10B981/15). Closes
// on outside click or Esc.
const SoftDropdown = ({ value, options, onChange, size = 'md', minWidth = 90 }) => {
  const [open, setOpen] = useState(false);
  const [hoverIdx, setHoverIdx] = useState(-1);
  const wrapRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => {
      if (!wrapRef.current?.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const selected = options.find((o) => o.value === value);
  const label = selected ? selected.label : '';
  const padCls = size === 'sm' ? 'px-[10px] pr-[22px] py-[5px]' : 'px-[10px] pr-[22px] py-[7px]';
  const fontCls = size === 'sm' ? 'text-[11px]' : 'text-[12px]';

  return (
    <div ref={wrapRef} className="relative inline-flex items-center" style={{ minWidth }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`cursor-pointer ${padCls} rounded-[8px] bg-white text-[#111] ${fontCls}  leading-none focus:outline-none flex items-center justify-between gap-2 w-full`}
        style={{ border: '1px solid #E5E7EB' }}
      >
        <span className="truncate">{label}</span>
      </button>
      <svg className="absolute right-[8px] pointer-events-none" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#2B2926" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="6 9 12 15 18 9" />
      </svg>
      {open && (
        <div
          className="absolute top-full left-0 mt-1 w-full bg-white rounded-[8px] z-50 overflow-hidden"
          style={{ border: '1px solid #E5E7EB', boxShadow: '0 12px 28px rgba(17,24,39,0.12)', minWidth }}
        >
          {options.map((opt, i) => {
            const isSelected = opt.value === value;
            const isHovered = i === hoverIdx;
            const optStyle = isSelected
              ? { color: '#F55600', background: 'rgba(245,86,0,0.06)' }
              : isHovered
              ? { color: '#10B981', background: 'rgba(16,185,129,0.15)' }
              : { color: '#2B2926', background: '#fff' };
            return (
              <button
                key={opt.value}
                type="button"
                onMouseEnter={() => setHoverIdx(i)}
                onMouseLeave={() => setHoverIdx(-1)}
                onClick={() => { onChange(opt.value); setOpen(false); }}
                className={`block w-full text-left px-[12px] py-[7px] ${fontCls}  transition-colors`}
                style={optStyle}
              >
                {opt.label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};

// Soft Aurora KpiTile — matches v02-soft.html .kpi.kp-iconbg style:
// rounded-[20px], border #B5BBC3, soft floating shadow, label + icon in
// rounded square top row, big 32px value, meta line below. `tone="green"`
// switches the icon swatch to mint (#10B981).
const KpiTile = ({ icon: Icon, label, value, sub, loading, accent = false, tone = 'orange' }) => {
  const isGreen = tone === 'green';
  return (
    <div
      // Apollo-style surface: soft alpha border + low-blur shadow stack
      // (var(--ap-elevation-2)) so the KPI tile reads as a calm floating
      // card instead of an outlined box. Falls back to the previous
      // #B5BBC3 hex if the variable isn't loaded.
      className="relative overflow-hidden bg-white rounded-[16px] px-[16px] py-[6px]"
      style={{
        border: '1px solid var(--ap-border-soft, #B5BBC3)',
        boxShadow: 'var(--ap-elevation-2, 0 4px 8px -2px rgba(46,43,41,0.05))',
        fontFamily: 'Inter, system-ui, -apple-system, sans-serif',
      }}
    >
      <div className="flex items-center justify-between mb-[1px]">
        <span className="text-[10.5px] font-bold uppercase tracking-[0.8px] text-[#0F1115]">
          {label}
        </span>
        {Icon && (
          <span
            className="w-[32px] h-[32px] rounded-[10px] grid place-items-center border"
            style={
              isGreen
                ? { background: 'rgba(16,185,129,0.12)', color: '#10B981', borderColor: 'rgba(16,185,129,0.18)' }
                : { background: 'rgba(245,86,0,0.10)', color: '#F55600', borderColor: 'rgba(245,86,0,0.14)' }
            }
          >
            <Icon size={15} />
          </span>
        )}
      </div>
      {loading ? (
        <Skeleton className="h-8 w-24" />
      ) : (
        <div className="text-[24px] font-bold text-[#0F1115] tracking-[-0.5px] leading-none">
          {value}
        </div>
      )}
      {!loading && sub && (
        <div className="mt-[2px] text-[12px] text-[#2B2926]">{sub}</div>
      )}
    </div>
  );
};

// Soft Aurora SectionCard — matches v02-soft.html .panel / .tablecard:
// rounded-[20px], #B5BBC3 border, soft shadow, 24px padding. Title is
// 15.5px semibold (not uppercase) with a small grey subtitle below.
// Small ⓘ icon with a plain-language hover tooltip explaining the chart.
// Pure CSS hover (group/info) so it needs no state — matches the lightweight
// tooltip pattern already used by the Top Roles bars.
const InfoHint = ({ text }) => (
  <span className="relative inline-flex group/info align-middle ml-[6px]">
    <FiInfo
      className="text-[#9A9590] hover:text-[#585450] cursor-help"
      size={14}
      aria-label={text}
    />
    <span
      className="pointer-events-none opacity-0 group-hover/info:opacity-100 transition-opacity absolute z-40 left-1/2 top-[22px] -translate-x-1/2"
    >
      <span
        className="block bg-white rounded-[8px] px-[12px] py-[8px] text-[11.5px] leading-[1.45] text-[#2B2926] font-normal normal-case tracking-normal text-left"
        style={{
          border: '1px solid #E5E7EB',
          boxShadow: '0 8px 22px rgba(17,24,39,0.12)',
          width: 240,
        }}
      >
        {text}
      </span>
    </span>
  </span>
);

const SectionCard = ({ title, subtitle, action, info, children, className = '' }) => (
  // Apollo-style: soft alpha hairline + low-blur shadow stack, 16px corner
  // (Apollo uses --radius-lg = 16). Reads as a quiet floating card rather
  // than a hard-bordered box.
  <div
    className={`bg-white rounded-[16px] p-6 min-w-0 ${className}`}
    style={{
      border: '1px solid var(--ap-border-soft, #B5BBC3)',
      boxShadow: 'var(--ap-elevation-2, 0 4px 8px -2px rgba(46,43,41,0.05))',
    }}
  >
    {(title || action) && (
      <div className="flex items-start justify-between mb-4 gap-3">
        <div>
          {title && (
            // Section title back to bold per request — applies to
            // Outreach Activity, Intent Breakdown, Conversion Funnel,
            // Top 10 Targeted Roles, Top Products, all consumers of
            // <SectionCard title="...">.
            <h3 className="text-[15.5px] font-semibold text-[#2B2926] m-0 inline-flex items-center">
              {title}
              {info && <InfoHint text={info} />}
            </h3>
          )}
          {subtitle && (
            <p
              className="text-[12.5px] mt-[3px] m-0"
              style={{ color: 'var(--ap-text-tertiary, #585450)' }}
            >
              {subtitle}
            </p>
          )}
        </div>
        {action}
      </div>
    )}
    {children}
  </div>
);

const IntentBadge = ({ intent }) => {
  const key = (intent || '').toUpperCase();
  const positive = ['DEMO_SCHEDULED', 'DEMO_BOOKED', 'INTERESTED', 'QUESTION'].includes(key);
  const negative = ['NOT_INTERESTED', 'UNSUBSCRIBE'].includes(key);
  const cls = positive
    ? 'bg-[#10B981]/10 text-[#10B981]'
    : negative
    ? 'bg-[#F55600]/10 text-[#F55600]'
    : 'bg-[#2B2926]/5 text-[#2B2926]';
  return (
    <span className={`text-[10px]  uppercase tracking-wider px-2 py-0.5 rounded-full ${cls}`}>
      {key.replace(/_/g, ' ') || 'NEW'}
    </span>
  );
};

const EmptyState = ({ icon: Icon, title, hint }) => (
  <div className="h-48 flex flex-col items-center justify-center text-center gap-2 px-4">
    {Icon && <Icon className="text-[#2B2926]" size={28} />}
    <div className="text-sm  text-[#2B2926]">{title}</div>
    {hint && <div className="text-xs text-[#2B2926]">{hint}</div>}
  </div>
);

// ──────────────────────────────────────────────────────────────────────────
// main component
// ──────────────────────────────────────────────────────────────────────────

const NexusDashboard = ({ authAxios, user, setMessage, apiBase, onNavigate, onBack }) => {
  const [me, setMe] = useState(null);
  // Single pre-computed analytics payload from /nexus/analytics/dashboard.
  // Backend computes every KPI / chart / funnel / table — the client does
  // ZERO arithmetic on metrics (only field-renaming + rendering geometry).
  const [dashboard, setDashboard] = useState(null);
  // perProduct is kept ONLY to populate the product-picker dropdown options
  // (the picker needs the full list of products/services/GCCs to choose
  // from). All KPIs/charts/table come from `dashboard`, not this list.
  const [perProduct, setPerProduct] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // ── Filter state ────────────────────────────────────────────────────────
  // entityFilter: 'all' | 'product' | 'service'
  // selectedProductId: null (all of that type) | number (specific product)
  // downloadFormat: 'csv' | 'pdf'  (pdf only available for workspace-wide)
  const [entityFilter, setEntityFilter] = useState('all');
  const [selectedProductId, setSelectedProductId] = useState(null);
  // Date-range filter (FROM/TO). Empty = all-time / entire period (default).
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate] = useState('');
  const [downloadFormat, setDownloadFormat] = useState('csv');
  const [downloading, setDownloading] = useState(false);
  // Capture target for the client-side PDF export — wraps the KPI tiles +
  // every chart so html2canvas snapshots exactly what's on screen.
  const reportRef = useRef(null);

  // Auto-pick the first matching item whenever the entity filter changes
  // to Products or Services. If the user then toggles that pill off
  // (deselect), we DON'T re-auto-pick — they explicitly want an empty
  // slice, which triggers the "Pick a product/service to view its data"
  // empty-state below. A ref tracks "needs auto-pick on next perProduct
  // refresh" so the auto-pick can also kick in once perProduct finishes
  // loading after a filter switch.
  const needsAutoPickRef = useRef(false);
  useEffect(() => {
    if (entityFilter === 'all') {
      setSelectedProductId(null);
      needsAutoPickRef.current = false;
      return;
    }
    needsAutoPickRef.current = true;
    setSelectedProductId(null);
  }, [entityFilter]);

  useEffect(() => {
    if (!needsAutoPickRef.current) return;
    if (entityFilter === 'all') return;
    const firstMatch = (perProduct || []).find(
      (p) => (p.entity_type || 'product') === entityFilter,
    );
    if (firstMatch) {
      setSelectedProductId(firstMatch.product_id);
      needsAutoPickRef.current = false;
    }
  }, [entityFilter, perProduct]);

  // ── Reusable refresher for workspace-scoped surfaces ────────────────────
  // Pulled out of the initial-load effect so we can re-call it on:
  //   - a 60s heartbeat (catches new products / new replies without a reload)
  //   - tab visibility coming back (catches the case where the user goes to
  //     "+ New Run", creates a product, then returns to the dashboard)
  //   - clicking a filter pill (so newly-created products show up in the
  //     picker immediately for the natural "I just made it" moment)
  const refreshWorkspace = useCallback(async () => {
    if (!authAxios) return;
    // Re-resolve workspace id each call — it can change if the user
    // switches workspaces in another tab.
    let meData = me;
    if (!meData) {
      try {
        const res = await authAxios.get('/nexus/me');
        meData = res?.data || null;
        if (meData) setMe(meData);
      } catch {
        meData = null;
      }
    }
    const wsId = meData?.default_workspace_id;
    const wsAmp = wsId ? `&workspace_id=${encodeURIComponent(wsId)}` : '';

    // Lightweight per-product fetch JUST to populate the product-picker
    // dropdown options. The dashboard KPIs/charts/table come from
    // /nexus/analytics/dashboard (refetched by the filter-scoped effect).
    const [perProdR] = await Promise.allSettled([
      authAxios.get(`/nexus/analytics/per-product?entity_type=all&period=30d${wsAmp}`),
    ]);

    if (perProdR.status === 'fulfilled') {
      const raw = perProdR.value?.data;
      setPerProduct(Array.isArray(raw?.products) ? raw.products : []);
    }
  }, [authAxios, me]);

  // ── Workspace-scoped loads that don't depend on the slice filter ──────
  // First-load effect: gets /me, then calls refreshWorkspace once. After
  // this initial run, the auto-refresh effects below take over.
  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      setLoading(true);
      setError(null);

      let meData = null;
      try {
        const res = await authAxios.get('/nexus/me');
        meData = res?.data || null;
      } catch (e) {
        if (!cancelled) {
          setError('Could not load workspace profile.');
          setLoading(false);
        }
        return;
      }
      if (cancelled) return;
      setMe(meData);

      const wsId = meData?.default_workspace_id;
      const wsAmp = wsId ? `&workspace_id=${encodeURIComponent(wsId)}` : '';

      // The dashboard payload (KPIs/charts/funnel/roles/table) is owned by
      // the filter-scoped effect below — it runs as soon as `me` is set
      // (right below), so every panel populates on first load. Here we only
      // pull the per-product list to seed the product-picker dropdown.
      const [perProdR] = await Promise.allSettled([
        authAxios.get(`/nexus/analytics/per-product?entity_type=all&period=30d${wsAmp}`),
      ]);
      if (cancelled) return;

      let pp = [];
      if (perProdR.status === 'fulfilled') {
        const raw = perProdR.value?.data;
        pp = Array.isArray(raw?.products) ? raw.products : [];
      }
      setPerProduct(pp);

      setLoading(false);
    };

    if (authAxios) load();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authAxios]);

  // ── Auto-refresh: silent 60s heartbeat + tab-visibility trigger ─────────
  // Heartbeat picks up new products / replies without the user reloading.
  // Visibility trigger catches the common "I made a product in another tab
  // / via + New Run, came back" flow. Both run silently so the page
  // doesn't blink.
  useEffect(() => {
    if (!authAxios || !me) return undefined;
    const id = setInterval(() => {
      if (document.visibilityState === 'visible') {
        refreshWorkspace();
      }
    }, 60_000);
    const onVisible = () => {
      if (document.visibilityState === 'visible') refreshWorkspace();
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
    };
  }, [authAxios, me, refreshWorkspace]);

  // Refresh when user clicks a Products / Services pill — covers the
  // "I just created one, where is it?" moment. Skip the initial mount
  // (entityFilter starts at 'all' which is the default and the
  // first-load effect already pulled per-product fresh).
  const prevEntityFilter = useRef(entityFilter);
  useEffect(() => {
    if (prevEntityFilter.current === entityFilter) return;
    prevEntityFilter.current = entityFilter;
    if (entityFilter !== 'all') refreshWorkspace();
  }, [entityFilter, refreshWorkspace]);

  // ── Filter-scoped load — single dashboard fetch, re-run on filter change
  // /nexus/analytics/dashboard returns EVERY KPI, chart series, funnel,
  // role list and product table PRE-COMPUTED for the requested slice. We
  // build the querystring from the current filter state and store the whole
  // response in one `dashboard` object — the client does no arithmetic.
  useEffect(() => {
    if (!authAxios || !me) return undefined;
    let cancelled = false;

    const wsId = me?.default_workspace_id;
    const params = new URLSearchParams();
    // No `period` — the window is driven entirely by the FROM/TO date range
    // below; when both are empty the backend defaults to ALL-TIME.
    if (entityFilter !== 'all') params.set('entity_type', entityFilter);
    if (selectedProductId != null) params.set('product_id', String(selectedProductId));
    // Date window — omitted when empty so the backend defaults to all-time.
    if (fromDate) params.set('start_date', fromDate);
    if (toDate) params.set('end_date', toDate);
    if (wsId) params.set('workspace_id', String(wsId));
    const qs = params.toString();

    (async () => {
      const [dashR] = await Promise.allSettled([
        authAxios.get(`/nexus/analytics/dashboard?${qs}`),
      ]);
      if (cancelled) return;
      setDashboard(dashR.status === 'fulfilled' ? dashR.value?.data || null : null);
    })();

    return () => {
      cancelled = true;
    };
  }, [authAxios, me, entityFilter, selectedProductId, fromDate, toDate]);

  // Whether ANY filter is active. The backend already scopes the dashboard
  // payload to the slice, so `isFiltered` is only used for render copy
  // (header label, tile labels) and routing the Download button.
  const isFiltered = entityFilter !== 'all' || selectedProductId != null;

  // When the user has chosen Products or Services as the entity filter
  // but hasn't picked a specific item yet, gate the data sections behind
  // an empty-state prompt. This forces an explicit slice selection so the
  // KPIs/charts always reflect a single, intentional choice.
  const awaitingPickerChoice = entityFilter !== 'all' && selectedProductId == null;

  // ── Render variables — ALL sourced from the pre-computed `dashboard`
  // payload. The client performs ZERO arithmetic on metrics; the only
  // transforms below are field-renaming and rendering geometry (date
  // labels, intent colors) which the chart libs require.
  const kpis = dashboard?.kpis || null;
  const sent30d = kpis?.emails_sent ?? 0;
  const replies30d = kpis?.replies ?? 0;
  const demos30d = kpis?.demos_booked ?? 0;

  // Lead-count tile: total_leads already reflects the active slice.
  const tileLeadsValue = kpis?.total_leads ?? 0;

  // ── Activity chart — already merged per-day + sorted server-side.
  // Only add a display `label` (formatted date) for the chart axis.
  const chartData = useMemo(
    () =>
      (dashboard?.outreach_activity ?? []).map((p) => ({
        ...p,
        label: (() => {
          try {
            const d = new Date(_normUtc(p.date));
            return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
          } catch {
            return p.date;
          }
        })(),
      })),
    [dashboard],
  );

  // ── Funnel — pct already computed server-side. Rename label→name for
  // the chart's YAxis dataKey; keep count + pct as-is.
  const funnelData = useMemo(
    () =>
      (dashboard?.funnel ?? []).map((s) => ({
        name: s.label,
        count: s.count,
        pct: s.pct,
      })),
    [dashboard],
  );

  // ── LinkedIn funnel (GTM LinkedIn Agent) — backend returns a `linkedin`
  // block {enabled, kpis, funnel}; `enabled` is false (and counts zero) until
  // the feature is live, so the whole section is hidden then. Same shape as the
  // email funnel — rename label→name for the chart.
  const li = dashboard?.linkedin || null;
  const liFunnel = useMemo(
    () =>
      (li?.funnel ?? []).map((s) => ({ name: s.label, count: s.count, pct: s.pct })),
    [dashboard],
  );

  // ── Intent breakdown donut — backend returns {label,count} sorted desc
  // (count>0 only). Rename to {name,value} and attach a render color from
  // the intent palette (pure presentation, no aggregation).
  const intentData = useMemo(
    () =>
      (dashboard?.intent_breakdown ?? []).map((d) => ({
        name: d.label,
        value: d.count,
        color: INTENT_COLOR[String(d.label).toUpperCase()] || '#64748B',
      })),
    [dashboard],
  );

  // ── Top products table — already filtered/sorted/sliced server-side.
  const topProducts = dashboard?.top_products ?? [];

  // ── Top roles — already top-10 with share_pct computed server-side.
  const topRoles = dashboard?.top_roles ?? [];

  // Specific-product chip options. When the entity filter is 'all' we
  // return every product + service so the user can scan and pick any
  // single item from the merged list. When it's narrowed to product or
  // service we return only the matching entries.
  const productOptions = useMemo(() => {
    const rows = perProduct || [];
    if (entityFilter === 'all') {
      return rows.map((p) => ({
        id: p.product_id,
        name: p.name || `Product ${p.product_id}`,
        kind: p.entity_type || 'product',
      }));
    }
    return rows
      .filter((p) => (p.entity_type || 'product') === entityFilter)
      .map((p) => ({
        id: p.product_id,
        name: p.name || `Product ${p.product_id}`,
        kind: p.entity_type || 'product',
      }));
  }, [perProduct, entityFilter]);

  const trialDays = trialDaysLeft(me);
  const workspaceName = useMemo(() => {
    if (!me) return '';
    const ws = (me.workspaces || []).find((w) => w.id === me.default_workspace_id);
    return ws?.name || me.workspaces?.[0]?.name || 'Default workspace';
  }, [me]);

  // ── Filter label (header subtitle) ─────────────────────────────────────
  const filterLabel = useMemo(() => {
    if (!isFiltered) return 'Workspace';
    const typeLabel =
      entityFilter === 'product' ? 'Products' :
      entityFilter === 'service' ? 'Services' :
      entityFilter === 'gcc'     ? 'GCC' : 'All';
    if (selectedProductId != null) {
      const match = (perProduct || []).find((p) => p.product_id === selectedProductId);
      return `${match?.name || 'Selected'} · ${typeLabel}`;
    }
    return `${typeLabel}`;
  }, [isFiltered, entityFilter, selectedProductId, perProduct]);

  // ── Download handler ───────────────────────────────────────────────────
  // Always routes through GET /nexus/reports/export — the endpoint now
  // accepts optional product_id + entity_type query params, so the
  // server renders the report (CSV or PDF) for whatever slice is
  // selected. When no filter is set, no slice params are sent, and
  // the server falls through to its original workspace-wide SQL path
  // (additive splices evaluate to "", SQL identical to pre-change).
  const handleDownload = useCallback(async (fmtArg) => {
    if (downloading) return;
    setDownloading(true);
    try {
      // Accept an explicit format from the caller (Export PDF / Export CSV
      // buttons) and fall back to whatever's currently in state. setState
      // is async so the buttons must pass the format directly to avoid a
      // stale read on the first click.
      const fmt = fmtArg || downloadFormat;
      const qs = new URLSearchParams();
      qs.set('format', fmt);
      if (entityFilter !== 'all') qs.set('entity_type', entityFilter);
      if (selectedProductId != null) qs.set('product_id', String(selectedProductId));
      // Same FROM/TO window as the dashboard, so the export matches the view.
      if (fromDate) qs.set('start_date', fromDate);
      if (toDate) qs.set('end_date', toDate);

      const res = await authAxios.get(
        `/nexus/reports/export?${qs.toString()}`,
        { responseType: 'blob' },
      );
      const blob = new Blob([res.data], {
        type: fmt === 'pdf' ? 'application/pdf' : 'text/csv;charset=utf-8',
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      // Prefer the server's Content-Disposition filename when present
      // (it embeds the slice tag and date). Falls back to a sensible
      // client-derived name when the header isn't readable (CORS).
      const cd = res?.headers?.['content-disposition'] || '';
      const match = /filename="?([^";]+)"?/i.exec(cd);
      const stamp = new Date().toISOString().slice(0, 10);
      const sliceTag = selectedProductId != null
        ? `-product-${selectedProductId}`
        : (entityFilter !== 'all' ? `-${entityFilter}` : '');
      link.download = match ? match[1] : `nexus-report${sliceTag}-${stamp}.${fmt}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setMessage && setMessage(`Downloaded ${link.download}`);
    } catch (err) {
      setMessage &&
        setMessage(
          err?.response?.data?.detail || err?.message || 'Report download failed',
        );
    } finally {
      setDownloading(false);
    }
  }, [
    authAxios, downloading, downloadFormat, entityFilter, selectedProductId,
    fromDate, toDate, setMessage,
  ]);

  // ── Client-side PDF export ─────────────────────────────────────────────
  // Renders the PDF in the browser so the charts are PIXEL-IDENTICAL to the
  // UI (they're already drawn by Recharts). We snapshot the report container
  // with html2canvas, slice that image across A4 pages, then append the
  // leads table (fetched as JSON, same 9 columns as the CSV) via autoTable.
  // No headless browser on the backend — ideal for Lambda hosting.
  const exportPdf = useCallback(async () => {
    if (downloading) return;
    const node = reportRef.current;
    if (!node) return;
    setDownloading(true);
    try {
      const [{ default: html2canvas }, { jsPDF }, { default: autoTable }] =
        await Promise.all([
          import('html2canvas'),
          import('jspdf'),
          import('jspdf-autotable'),
        ]);

      const pdf = new jsPDF('p', 'pt', 'a4');
      const pageW = pdf.internal.pageSize.getWidth();
      const pageH = pdf.internal.pageSize.getHeight();
      const margin = 24;
      const maxW = pageW - margin * 2;
      const maxH = pageH - margin * 2;

      // Snapshot each top-level section (KPI row, then each chart card) on its
      // OWN — so a chart is never sliced across a page boundary and its numbers
      // / labels stay intact. A section taller than one page is scaled to fit.
      const sections = Array.from(node.children).filter(
        (el) => el.offsetHeight > 4 && el.offsetWidth > 4,
      );
      let y = margin;
      let first = true;
      for (const el of sections) {
        // NOTE: do NOT pass windowWidth here. Overriding it re-flows the
        // cloned DOM at a different width than the live layout, but the
        // Recharts/donut SVGs keep their on-screen pixel width (no JS re-measure
        // in the static clone) — so they overflow their card into the neighbour
        // and the Top Products cells get squeezed/clipped. Capturing each
        // section at its real rendered size keeps everything aligned.
        // eslint-disable-next-line no-await-in-loop
        const c = await html2canvas(el, {
          scale: 2,
          backgroundColor: '#ffffff',
          useCORS: true,
          logging: false,
          scrollX: 0,
          scrollY: 0,
          width: el.offsetWidth,
          // +10px bottom buffer so each card's drop-shadow + bottom border
          // (which sit just outside offsetHeight) aren't clipped — that's why
          // the KPI tiles looked cut off at the bottom.
          height: el.offsetHeight + 10,
          windowWidth: document.documentElement.clientWidth,
          // The Top Products table has two-line cells (name + category) with
          // `truncate` (overflow:hidden). html2canvas clips those tighter than
          // the browser, cutting the product names + descenders. For the
          // export only, give table cells breathing room and show the full
          // text (un-truncate + allow wrap) so nothing is cut off.
          onclone: (doc) => {
            // Un-clip every truncated label (Intent legend, product cells) so
            // the export shows full text instead of html2canvas's tight clip.
            doc.querySelectorAll('.truncate').forEach((s) => {
              s.style.overflow = 'visible';
              s.style.textOverflow = 'clip';
              s.style.maxWidth = 'none';
              s.style.whiteSpace = 'nowrap';
            });
            // Two-line Top Products cells should wrap (not run off the column).
            doc.querySelectorAll('table .truncate').forEach((s) => {
              s.style.whiteSpace = 'normal';
            });
            // KPI values use `leading-none` (line-height:1); html2canvas clips
            // their glyph bottoms. Give tight line-heights a hair of room.
            doc.querySelectorAll('.leading-none').forEach((s) => {
              s.style.lineHeight = '1.15';
            });
            // Table cells: vertical breathing room for the two-line rows.
            doc.querySelectorAll('table td, table th').forEach((cell) => {
              cell.style.paddingTop = '8px';
              cell.style.paddingBottom = '8px';
              cell.style.lineHeight = '1.5';
              cell.style.verticalAlign = 'middle';
            });
          },
        });
        let w = maxW;
        let h = (c.height * w) / c.width;
        if (h > maxH) { h = maxH; w = (c.width * h) / c.height; }
        if (!first && y + h > pageH - margin) { pdf.addPage(); y = margin; }
        pdf.addImage(c.toDataURL('image/png'), 'PNG', margin, y, w, h, undefined, 'FAST');
        y += h + 14;
        first = false;
      }

      // 3) Leads table on fresh page(s) — same slice + date window as the view.
      const qs = new URLSearchParams();
      if (entityFilter !== 'all') qs.set('entity_type', entityFilter);
      if (selectedProductId != null) qs.set('product_id', String(selectedProductId));
      if (fromDate) qs.set('start_date', fromDate);
      if (toDate) qs.set('end_date', toDate);
      let columns = ['S.No', 'Name', 'Role', 'Email', 'Company', 'Product / Entity', 'Sent Emails', 'Last Contacted', 'Demos Booked'];
      let rows = [];
      try {
        const res = await authAxios.get(`/nexus/reports/leads?${qs.toString()}`);
        columns = res?.data?.columns || columns;
        rows = res?.data?.rows || [];
      } catch (_e) {
        // Leave the leads table empty if the fetch fails — the charts PDF
        // still downloads rather than failing the whole export.
      }

      pdf.addPage();
      autoTable(pdf, {
        head: [columns],
        body: rows.length ? rows : [['No leads for this selection', '', '', '', '', '', '', '', '']],
        startY: margin + 22,
        margin: { left: margin, right: margin },
        styles: { fontSize: 7, cellPadding: 3, overflow: 'linebreak' },
        headStyles: { fillColor: [245, 86, 0], textColor: 255, fontStyle: 'bold', fontSize: 7.5 },
        alternateRowStyles: { fillColor: [250, 250, 250] },
        columnStyles: { 0: { halign: 'right', cellWidth: 28 }, 6: { halign: 'right' }, 8: { halign: 'right' } },
        didDrawPage: () => {
          pdf.setFontSize(12);
          pdf.setTextColor(43, 41, 38);
          pdf.text(`Leads (${rows.length})`, margin, margin + 10);
        },
      });

      const stamp = new Date().toISOString().slice(0, 10);
      const sliceTag = selectedProductId != null
        ? `-product-${selectedProductId}`
        : (entityFilter !== 'all' ? `-${entityFilter}` : '');
      const filename = `gtm-dashboard${sliceTag}-${stamp}.pdf`;
      pdf.save(filename);
      setMessage && setMessage(`Downloaded ${filename}`);
    } catch (err) {
      setMessage && setMessage(err?.message || 'PDF export failed');
    } finally {
      setDownloading(false);
    }
  }, [
    authAxios, downloading, entityFilter, selectedProductId,
    fromDate, toDate, setMessage,
  ]);

  const activeCampaigns = useMemo(() => {
    if (!perProduct?.length) return 0;
    const withActivity = perProduct.filter((p) => (p.sent || 0) > 0).length;
    return withActivity || perProduct.length;
  }, [perProduct]);

  // ──────────────────────────────────────────────────────────────────────
  // render
  // ──────────────────────────────────────────────────────────────────────
  return (
    <div
      className="w-full px-4 md:px-6 pt-2 pb-10 space-y-[14px] bg-white min-h-screen"
      style={{ fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"', color: '#2B2926' }}
    >
      {/* Page title removed per request to reclaim vertical space — the
          filter bar (with actions) is now the top element. */}


      {error && (
        <div className="rounded-2xl border border-[#F55600]/30 bg-[#F55600]/5 p-4 flex items-center gap-3">
          <FiAlertCircle className="text-[#F55600]" />
          <span className="text-sm text-[#2B2926]">{error}</span>
        </div>
      )}

      {/* ── Filter bar — Soft Aurora .filterbar style (slim) ──────────── */}
      <section
        className="flex items-center justify-between gap-3 flex-wrap bg-white rounded-[14px] px-[14px] py-[5px] mb-[10px]"
        style={{ border: '1px solid #B5BBC3' }}
      >
        <div className="flex items-center gap-[12px] flex-wrap">
          {onBack && (
            <button
              type="button"
              onClick={onBack}
              className="inline-flex items-center gap-1.5 text-xs  text-white shrink-0 mr-2 sm:mr-8"
              style={{ background: '#0F1115', borderRadius: 8, padding: '6px 12px' }}
              onMouseEnter={(e) => (e.currentTarget.style.background = '#1c2128')}
              onMouseLeave={(e) => (e.currentTarget.style.background = '#0F1115')}
            >
              <FiChevronLeft />
              Back
            </button>
          )}
          <span className="text-[10px] uppercase tracking-[0.9px]  text-[#2B2926]">
            Filter
          </span>
          {/* Category pill toggle (All / Products / Services / GCC). */}
          <div className="inline-flex items-center gap-1 bg-white rounded-full border border-[#2B2926]/10 p-1">
            {[
              { id: 'all', label: 'All' },
              { id: 'product', label: 'Products' },
              { id: 'service', label: 'Services' },
              { id: 'gcc', label: 'GCC' },
            ].map((opt) => {
              const active = entityFilter === opt.id;
              return (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => { setEntityFilter(opt.id); setSelectedProductId(null); }}
                  className={[
                    'px-3 py-1.5 rounded-full text-xs  transition-all',
                    active ? 'bg-[#F55600] text-white' : 'text-[#2B2926] hover:text-[#2B2926]',
                  ].join(' ')}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>

          {/* Product picker — only shown once a specific category is
              chosen (Products / Services / GCC). Hidden on "All". */}
          {entityFilter !== 'all' && productOptions.length > 0 && (
            <>
              <span className="text-[10px] uppercase tracking-[0.9px]  text-[#2B2926]">
                {entityFilter === 'service' ? 'Service'
                  : entityFilter === 'gcc' ? 'GCC' : 'Product'}
              </span>
              <SoftDropdown
                value={selectedProductId == null ? '' : selectedProductId}
                onChange={(v) => setSelectedProductId(v === '' ? null : v)}
                options={productOptions.map((p) => ({ value: p.id, label: p.name }))}
                minWidth={150}
              />
            </>
          )}

          {/* Date range (FROM / TO). Empty = all-time / entire period. */}
          <div className="inline-flex items-center gap-1.5">
            <span className="text-[10px] uppercase tracking-[0.9px] text-[#2B2926]">From</span>
            <input
              type="date"
              value={fromDate}
              max={toDate || undefined}
              onChange={(e) => setFromDate(e.target.value)}
              className="text-[12px] rounded-[8px] bg-white px-[10px] py-[6px] text-[#111] focus:outline-none"
              style={{ border: '1px solid #E5E7EB' }}
            />
            <span className="text-[10px] uppercase tracking-[0.9px] text-[#2B2926]">To</span>
            <input
              type="date"
              value={toDate}
              min={fromDate || undefined}
              onChange={(e) => setToDate(e.target.value)}
              className="text-[12px] rounded-[8px] bg-white px-[10px] py-[6px] text-[#111] focus:outline-none"
              style={{ border: '1px solid #E5E7EB' }}
            />
          </div>
        </div>
        {/* Right cluster — Clear filters + export controls + New Run. Stays
            right-aligned even when the extra product/service picker widens the
            left group and pushes this cluster onto a second row (sm:ml-auto +
            justify-end), so the wrapped state looks balanced, not cramped. */}
        <div className="flex items-center gap-2 flex-wrap w-full sm:w-auto justify-end sm:ml-auto">
          {(isFiltered || fromDate || toDate) && (
            <button
              type="button"
              onClick={() => { setEntityFilter('all'); setSelectedProductId(null); setFromDate(''); setToDate(''); }}
              className="text-[#F55600] text-[11.5px]  tracking-[0.6px] uppercase hover:underline mr-1"
            >
              Clear filters
            </button>
          )}
          <button
            type="button"
            onClick={() => { setDownloadFormat('pdf'); exportPdf(); }}
            disabled={downloading}
            className="inline-flex items-center justify-center gap-[6px] bg-white text-[#F55600]  text-[12px] hover:bg-[rgba(245,86,0,0.06)] hover:-translate-y-[1px] transition-all disabled:opacity-50"
            style={{ height: 30, padding: '0 12px', borderRadius: 9, border: '1px solid rgba(245,86,0,0.55)' }}
          >
            {downloading && downloadFormat === 'pdf'
              ? <FiLoader className="animate-spin" size={12} />
              : <FiDownload size={12} />}
            Export PDF
          </button>
          <button
            type="button"
            onClick={() => { setDownloadFormat('csv'); handleDownload('csv'); }}
            disabled={downloading}
            className="inline-flex items-center justify-center gap-[6px] bg-white text-[#F55600]  text-[12px] hover:bg-[rgba(245,86,0,0.06)] hover:-translate-y-[1px] transition-all disabled:opacity-50"
            style={{ height: 30, padding: '0 12px', borderRadius: 9, border: '1px solid rgba(245,86,0,0.55)' }}
          >
            {downloading && downloadFormat === 'csv'
              ? <FiLoader className="animate-spin" size={12} />
              : <FiDownload size={12} />}
            Export CSV
          </button>
          {onNavigate && !isReadOnly(user) && (
            <button
              type="button"
              onClick={() => onNavigate('new-campaign')}
              className="inline-flex items-center justify-center gap-[6px] text-white  text-[12px] hover:-translate-y-[1px] transition-all"
              style={{
                height: 30,
                padding: '0 12px',
                borderRadius: 9,
                background: '#F55600',
                boxShadow: '0 2px 6px rgba(245,86,0,0.18)',
              }}
            >
              New Campaign
            </button>
          )}
        </div>
      </section>

      {/* When the entity filter is on but no specific item is selected,
          show an inline empty-state prompting the user to pick one. We
          short-circuit the rest of the page so KPIs/charts don't render
          stale or aggregated data for an unselected slice. */}
      {awaitingPickerChoice ? (
        <section
          className="bg-white rounded-[18px] px-[24px] py-[40px] flex flex-col items-center text-center"
          style={{ border: '1px solid #B5BBC3' }}
        >
          <div
            className="w-[56px] h-[56px] rounded-[14px] grid place-items-center mb-[14px] text-[#F55600]"
            style={{ background: 'rgba(245,86,0,0.08)', border: '1px solid rgba(245,86,0,0.18)' }}
          >
            <FiFilter size={22} />
          </div>
          {productOptions.length === 0 ? (
            <>
              {/* Category selected but the workspace has none of them yet —
                  tell the user the data isn't there (don't imply they can pick). */}
              <strong className="text-[16px] text-[#2B2926] mb-[4px]">
                No {entityFilter === 'service' ? 'services' : entityFilter === 'gcc' ? 'GCCs' : 'products'} in this workspace yet
              </strong>
              <small className="text-[13px] text-[#2B2926] max-w-[360px]">
                There’s no {entityFilter === 'service' ? 'service' : entityFilter === 'gcc' ? 'GCC' : 'product'} data to show here.
                Add one from New Campaign, or switch back to All to see your workspace overview.
              </small>
            </>
          ) : (
            <>
              <strong className="text-[16px] text-[#2B2926] mb-[4px]">
                Pick {entityFilter === 'service' ? 'a service' : entityFilter === 'gcc' ? 'a GCC' : 'a product'} to view its data
              </strong>
              <small className="text-[13px] text-[#2B2926] max-w-[360px]">
                Choose {entityFilter === 'service' ? 'a service' : entityFilter === 'gcc' ? 'a GCC' : 'a product'} from the picker above to load the
                enrolled leads, outreach activity, and intent breakdown for that slice.
              </small>
            </>
          )}
        </section>
      ) : (
      <div ref={reportRef} className="bg-white">
      {/* ── KPI row — 4-up grid (Soft Aurora) ─────────────────────────── */}
      <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-[18px] mb-[18px]">
        <KpiTile
          icon={FiUsers}
          label="Total Leads"
          value={fmtNum(tileLeadsValue)}
          loading={loading && !dashboard}
          tone="orange"
        />
        <KpiTile
          icon={FiSend}
          label="Emails Sent"
          value={fmtNum(sent30d)}
          loading={loading && !dashboard}
          tone="orange"
        />
        <KpiTile
          icon={FiMessageSquare}
          label="Replies"
          value={fmtNum(replies30d)}
          loading={loading && !dashboard}
          tone="green"
        />
        <KpiTile
          icon={FiCalendar}
          label="Demos Booked"
          value={fmtNum(demos30d)}
          loading={loading && !dashboard}
          tone="green"
        />
      </section>

      {/* ── Main row — Outreach Activity (left) + Recent Inbox (right) ── */}
      <section className="grid grid-cols-1 lg:grid-cols-[1.95fr_1fr] gap-[18px] mb-[18px]">
        <SectionCard
          title="Outreach Activity"
          info="How many emails you sent each day, and how many people wrote back."
          subtitle={
            isFiltered
              ? `Emails sent vs replies — ${filterLabel}`
              : 'Emails sent vs replies received per day'
          }
        >
          {loading && chartData.length === 0 ? (
            <Skeleton className="h-64 w-full" />
          ) : chartData.length === 0 ? (
            <EmptyState
              icon={FiActivity}
              title="No activity yet"
              hint="Launch a new campaign from the wizard to start outreach."
            />
          ) : (
            // `overflow-y-hidden` is explicit on mobile so the
            // horizontal-scroll wrapper doesn't grow a vertical
            // scrollbar alongside. At `md:` BOTH axes are reset to
            // visible — CSS forces overflow-x back to auto if
            // overflow-y is left as hidden, so without
            // `md:overflow-y-visible` desktop still showed a horizontal
            // scrollbar even though it wasn't needed.
            <div className="overflow-x-auto overflow-y-hidden md:overflow-x-visible md:overflow-y-visible">
            {/* Height 290 — gives the X-axis date labels
                ("May 5, May 9, …") room inside the chart box. */}
            <div className="min-w-[520px] md:min-w-0" style={{ height: 290 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartData} margin={{ top: 8, right: 16, bottom: 30, left: 6 }}>
                  <defs>
                    <linearGradient id="sentGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#F55600" stopOpacity={0.25} />
                      <stop offset="100%" stopColor="#F55600" stopOpacity={0.02} />
                    </linearGradient>
                    <linearGradient id="replyGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#10B981" stopOpacity={0.3} />
                      <stop offset="100%" stopColor="#10B981" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="rgba(0,0,0,0.06)" strokeDasharray="3 3" vertical={false} />
                  <XAxis
                    dataKey="label"
                    tick={{ fill: 'rgba(0,0,0,0.6)', fontSize: 11 }}
                    tickLine={false}
                    axisLine={{ stroke: 'rgba(0,0,0,0.1)' }}
                    label={{ value: 'Date', position: 'insideBottom', offset: -6, fontSize: 11, fill: '#585450' }}
                  />
                  <YAxis
                    tick={{ fill: 'rgba(0,0,0,0.6)', fontSize: 11 }}
                    tickLine={false}
                    axisLine={false}
                    allowDecimals={false}
                    label={{ value: 'Emails / replies', angle: -90, position: 'insideLeft', offset: 14, fontSize: 11, fill: '#585450', style: { textAnchor: 'middle' } }}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#FFFFFF',
                      border: '1px solid rgba(0,0,0,0.1)',
                      borderRadius: 8,
                      fontSize: 12,
                    }}
                    labelStyle={{ color: '#2B2926', fontWeight: 400 }}
                  />
                  <Area
                    type="monotone"
                    dataKey="sent"
                    name="Sent"
                    stroke="#F55600"
                    strokeWidth={2.5}
                    fill="url(#sentGrad)"
                  />
                  <Area
                    type="monotone"
                    dataKey="replies"
                    name="Replies"
                    stroke="#10B981"
                    strokeWidth={2.5}
                    fill="url(#replyGrad)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
            </div>
          )}
          <div className="flex items-center gap-[18px] mt-[14px] text-[12px]  text-[#2B2926]">
            <span className="inline-flex items-center gap-[7px]">
              <i className="w-[11px] h-[11px] rounded-[3px]" style={{ background: '#F55600' }} />
              Sent
            </span>
            <span className="inline-flex items-center gap-[7px]">
              <i className="w-[11px] h-[11px] rounded-[3px]" style={{ background: '#10B981' }} />
              Replies
            </span>
          </div>
        </SectionCard>

        {/* Intent Breakdown — donut chart showing how replies classify
            (INTERESTED, QUESTION, DEMO BOOKED, etc.). Sits beside the
            Outreach Activity line chart in the same 2-col row. */}
        <SectionCard
          title="Intent Breakdown"
          info="Of the people who replied, how many were interested, not interested, or asked for a meeting."
          subtitle="How replies classify"
        >
          {intentData.length === 0 ? (
            <div className="flex flex-col items-center justify-center text-center px-2 py-[18px]" style={{ minHeight: 200 }}>
              <div
                className="w-[62px] h-[62px] rounded-[16px] grid place-items-center mb-4 text-[#2B2926]"
                style={{ background: '#F9FAFB', border: '1px solid #E5E7EB' }}
              >
                <FiMessageCircle size={26} />
              </div>
              <strong className="text-[15px] text-[#2B2926]">No replies yet</strong>
              <small className="text-[12.5px] text-[#2B2926] mt-1.5 max-w-[220px]">
                Intent breakdown appears once leads engage with your outreach.
              </small>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-3">
              {/* Raw-SVG donut — recharts' ResponsiveContainer rendered
                  unpredictably on mobile and bled into the legend below.
                  A plain SVG with stroke-dasharray arcs gives exact,
                  self-contained sizing (no overflow, no overlap). */}
              {(() => {
                const total = intentData.reduce((s, d) => s + (Number(d.value) || 0), 0) || 1;
                const R = 16; // circle radius in viewBox units
                const C = 2 * Math.PI * R; // circumference
                // A hair of surface between segments so adjacent slices read as
                // separate marks, not one continuous ring (~2px at this size).
                const GAP = intentData.length > 1 ? 0.55 : 0;
                let acc = 0; // cumulative fraction (0..1)
                return (
                  <svg
                    viewBox="0 0 40 40"
                    className="shrink-0"
                    style={{ width: 150, height: 150, display: 'block' }}
                  >
                    {/* track */}
                    <circle cx="20" cy="20" r={R} fill="none" stroke="#F1F2F4" strokeWidth="7" />
                    {intentData.map((d) => {
                      const frac = (Number(d.value) || 0) / total;
                      const dash = frac * C;
                      // Trim each arc by GAP so a sliver of surface shows after
                      // it; the offset still steps by the full fraction so the
                      // gap sits between neighbours (min 0.5 keeps tiny slices).
                      const visible = Math.max(0.5, dash - GAP);
                      // rotate(-90) starts the path at 12 o'clock; offset by
                      // the cumulative fraction so segments sit end-to-end.
                      const dashoffset = -C * acc;
                      acc += frac;
                      return (
                        <circle
                          key={d.name}
                          cx="20"
                          cy="20"
                          r={R}
                          fill="none"
                          stroke={d.color}
                          strokeWidth="7"
                          strokeLinecap="butt"
                          strokeDasharray={`${visible} ${C - visible}`}
                          strokeDashoffset={dashoffset}
                          transform="rotate(-90 20 20)"
                        />
                      );
                    })}
                  </svg>
                );
              })()}
              <ul className="w-full flex flex-col gap-2">
                {intentData.map((row) => (
                  <li
                    key={row.name}
                    className="flex items-center justify-between text-[12.5px]"
                  >
                    <span className="flex items-center gap-2 min-w-0">
                      <span
                        className="w-2.5 h-2.5 rounded-full shrink-0"
                        style={{ backgroundColor: row.color }}
                      />
                      <span className="text-[#2B2926]  uppercase tracking-[0.04em] truncate">
                        {row.name.replace(/_/g, ' ')}
                      </span>
                    </span>
                    <span className=" text-[#2B2926] ml-2 tabular-nums">
                      {fmtNum(row.value)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </SectionCard>
      </section>

      {/* ── Analytics insights ──────────────────────────────────────────
          All panels read from the single /nexus/analytics/dashboard payload,
          which the backend computes for the active slice (period 30d +
          entity_type + product_id). Every series/total is pre-computed
          server-side — this component does no math, only renders. */}
      {(
        <>
          <SectionCard
            title="Conversion Funnel"
            info="How many people moved through each step — from getting your email, to opening it, to finally booking a meeting. Shows where most people drop off."
            subtitle="Sent → Opened → Clicked → Replied → Booked"
          >
            {loading && funnelData.length === 0 ? (
              <Skeleton className="h-72 w-full" />
            ) : funnelData.every((s) => s.count === 0) ? (
              <div className="h-48 flex items-center justify-center text-sm text-[#2B2926]">
                No funnel data for this period.
              </div>
            ) : (
              // Same pattern as the Outreach Activity chart above:
              // explicit `overflow-y-hidden` so the horizontal-scroll
              // wrapper doesn't grow a vertical scrollbar alongside,
              // and explicit `md:overflow-y-visible` so the desktop
              // breakpoint actually un-locks both axes (otherwise
              // overflow-y:hidden forces overflow-x back to auto).
              <div className="overflow-x-auto overflow-y-hidden md:overflow-x-visible md:overflow-y-visible">
              <div className="min-w-[460px] md:min-w-0" style={{ height: 280 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={funnelData}
                    layout="vertical"
                    margin={{ top: 12, right: 60, bottom: 30, left: 40 }}
                  >
                    <CartesianGrid stroke="rgba(0,0,0,0.1)" strokeDasharray="3 3" horizontal={false} />
                    <XAxis
                      type="number"
                      tick={{ fill: 'rgba(0,0,0,0.6)', fontSize: 11 }}
                      axisLine={{ stroke: 'rgba(0,0,0,0.1)' }}
                      tickLine={false}
                      label={{ value: 'Number of leads', position: 'insideBottom', offset: -6, fontSize: 11, fill: '#585450' }}
                    />
                    <YAxis
                      type="category"
                      dataKey="name"
                      width={120}
                      tick={{ fill: 'rgba(0,0,0,0.6)', fontSize: 12, fontWeight: 600 }}
                      axisLine={false}
                      tickLine={false}
                      label={{ value: 'Stage', angle: -90, position: 'insideLeft', offset: 4, fontSize: 11, fill: '#585450', style: { textAnchor: 'middle' } }}
                    />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: '#FFFFFF',
                        border: '1px solid rgba(0,0,0,0.1)',
                        borderRadius: 8,
                        fontSize: 12,
                      }}
                      formatter={(value) => [value, 'Count']}
                    />
                    <Bar dataKey="count" fill="#F55600" radius={[0, 6, 6, 0]} barSize={22}>
                      <LabelList
                        dataKey="count"
                        position="right"
                        style={{ fill: 'rgba(0,0,0,0.6)', fontSize: 11, fontWeight: 600 }}
                      />
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
              </div>
            )}
          </SectionCard>

          {/* Intent Breakdown moved up — now lives next to Outreach
              Activity in the main 2-col row. */}

          {/* ── LinkedIn Outreach funnel (GTM LinkedIn Agent) — only shown
              when the backend reports activity (linkedin.enabled). Mirrors the
              email funnel chart; LinkedIn-blue bars to distinguish the channel. */}
          {li?.enabled && (
            <SectionCard
              title="LinkedIn Outreach"
              info="Your LinkedIn connection + message funnel: requests sent, how many accepted, how many you messaged, and replies received."
              subtitle="Requests Sent → Accepted → Messaged → Replied"
            >
              <div className="flex flex-wrap gap-x-6 gap-y-2 mb-4 text-[13px] text-[#585450]">
                <span><b className="text-[#2B2926]">{li.kpis.connection_requests}</b> requests</span>
                <span><b className="text-[#2B2926]">{li.kpis.acceptance_rate}%</b> accepted</span>
                <span><b className="text-[#2B2926]">{li.kpis.messages_sent}</b> messaged</span>
                <span><b className="text-[#2B2926]">{li.kpis.reply_rate}%</b> reply rate</span>
              </div>
              <div className="overflow-x-auto overflow-y-hidden md:overflow-x-visible md:overflow-y-visible">
                <div className="min-w-[460px] md:min-w-0" style={{ height: 240 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={liFunnel}
                      layout="vertical"
                      margin={{ top: 12, right: 60, bottom: 30, left: 40 }}
                    >
                      <CartesianGrid stroke="rgba(0,0,0,0.1)" strokeDasharray="3 3" horizontal={false} />
                      <XAxis
                        type="number"
                        tick={{ fill: 'rgba(0,0,0,0.6)', fontSize: 11 }}
                        axisLine={{ stroke: 'rgba(0,0,0,0.1)' }}
                        tickLine={false}
                        label={{ value: 'Number of leads', position: 'insideBottom', offset: -6, fontSize: 11, fill: '#585450' }}
                      />
                      <YAxis
                        type="category"
                        dataKey="name"
                        width={120}
                        tick={{ fill: 'rgba(0,0,0,0.6)', fontSize: 12, fontWeight: 600 }}
                        axisLine={false}
                        tickLine={false}
                        label={{ value: 'Stage', angle: -90, position: 'insideLeft', offset: 4, fontSize: 11, fill: '#585450', style: { textAnchor: 'middle' } }}
                      />
                      <Tooltip
                        contentStyle={{
                          backgroundColor: '#FFFFFF',
                          border: '1px solid rgba(0,0,0,0.1)',
                          borderRadius: 8,
                          fontSize: 12,
                        }}
                        formatter={(value) => [value, 'Count']}
                      />
                      <Bar dataKey="count" fill="#0A66C2" radius={[0, 6, 6, 0]} barSize={22}>
                        <LabelList
                          dataKey="count"
                          position="right"
                          style={{ fill: 'rgba(0,0,0,0.6)', fontSize: 11, fontWeight: 600 }}
                        />
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </SectionCard>
          )}
        </>
      )}

      {/* ── Top Targeted Roles — horizontal bar chart ──────────────────
          /top-roles now accepts product_id + entity_type, so the chart
          reflects whatever slice the filter pills + product picker
          select. Subtitle changes copy when filtered so the operator
          knows the data is scoped, not workspace-wide. */}
      {(
        <SectionCard
          title="Top 10 Targeted Roles"
          info="The job titles you've reached out to most often. Helps you check you're talking to the right people."
          subtitle={
            isFiltered
              ? `Most-contacted job titles — ${filterLabel}`
              : 'Most-contacted job titles across all campaigns'
          }
        >
          {loading && topRoles.length === 0 ? (
            <Skeleton className="h-72 w-full" />
          ) : topRoles.length === 0 ? (
            <EmptyState
              icon={FiBriefcase}
              title="No role data yet"
              hint="Roles populate as leads are discovered with title metadata."
            />
          ) : (() => {
            // Every bar uses the brand orange #F55600 (solid) per user
            // request — no light/dark gradient ramp anymore.
            const fills = ['f6', 'f5', 'f4', 'f3', 'f2', 'f1'];
            const fillStyles = {
              f1: '#F55600',
              f2: '#F55600',
              f3: '#F55600',
              f4: '#F55600',
              f5: '#F55600',
              f6: '#F55600',
            };
            const maxCount = Math.max(...topRoles.map((r) => Number(r.lead_count) || 0), 1);
            return (
              <>
                {/* Horizontal scroll on mobile so the 210px role label +
                    bar keep full width (labels no longer truncate). */}
                <div className="overflow-x-auto md:overflow-x-visible">
                <div className="flex flex-col gap-[8px] mt-[12px] min-w-[520px] md:min-w-0">
                  {topRoles.map((entry, i) => {
                    const pct = (Number(entry.lead_count) / maxCount) * 100;
                    const tone = fills[Math.min(i, fills.length - 1)];
                    return (
                      <div
                        key={entry.role}
                        className="grid items-center gap-[14px] group/row"
                        style={{ gridTemplateColumns: '260px 1fr 30px' }}
                      >
                        <span className="text-[12.5px] text-[#2B2926] text-right leading-[1.25] break-words">
                          {entry.role}
                        </span>
                        {/* Track is the tooltip's positioning context so
                            `${pct}%` aligns with the end of the fill bar
                            (not the row's full width). overflow-visible
                            lets the tooltip pop outside the track. */}
                        <div
                          className="h-[14px] rounded-[6px] relative"
                          style={{ background: 'rgba(15,23,42,0.05)', overflow: 'visible' }}
                        >
                          <div
                            className="h-full rounded-[6px]"
                            style={{
                              width: `${pct}%`,
                              background: fillStyles[tone],
                              boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.35)',
                            }}
                          />
                          {/* Hover tooltip — anchored to the END of the
                              fill bar (`left: ${pct}%`). For the first row
                              (Agency Owner) the tooltip drops BELOW so it
                              doesn't get clipped by the card top edge. */}
                          <div
                            className="pointer-events-none opacity-0 group-hover/row:opacity-100 transition-opacity absolute z-30"
                            style={{
                              left: `${pct}%`,
                              // First two rows: tooltip ENDS at the bar edge
                              // and drops BELOW (so it's not clipped at the
                              // card top). Rows 3+: tooltip STARTS at the bar
                              // edge and extends right (so short bars don't
                              // push it left over the role labels).
                              top: i <= 1 ? '100%' : '50%',
                              transform: i <= 1
                                ? 'translate(-100%, 6px)'
                                : 'translate(8px, -120%)',
                            }}
                          >
                            <div
                              className="bg-white rounded-[8px] px-[12px] py-[8px] whitespace-nowrap"
                              style={{
                                border: '1px solid #E5E7EB',
                                boxShadow: '0 8px 22px rgba(17,24,39,0.12)',
                                minWidth: 160,
                              }}
                            >
                              <div className="text-[12.5px]  text-[#111] leading-tight">
                                {entry.role}
                              </div>
                              <div className="text-[11px] text-[#2B2926] mt-[2px]">
                                Targeted : <b className="text-[#111]">{entry.lead_count}</b> {entry.lead_count === 1 ? 'lead' : 'leads'}
                              </div>
                            </div>
                          </div>
                        </div>
                        <span className="text-[12.5px]  text-[#111]">
                          {entry.lead_count}
                        </span>
                      </div>
                    );
                  })}
                </div>
                <div className="grid gap-[14px] mt-[14px] min-w-[520px] md:min-w-0" style={{ gridTemplateColumns: '260px 1fr 30px' }}>
                  <span />
                  <div className="flex justify-between text-[11px] text-[#2B2926]">
                    {(() => {
                      // Build distinct integer ticks. For small maxCount the
                      // 25/50/75% rounding collided (e.g. max 2 → 0,1,1,2,2),
                      // so step by 1 when the range is tiny.
                      const ticks =
                        maxCount <= 4
                          ? Array.from({ length: maxCount + 1 }, (_, i) => i)
                          : [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(maxCount * f));
                      return [...new Set(ticks)].map((t, i) => (
                        <span key={i}>{t}</span>
                      ));
                    })()}
                  </div>
                  <span />
                </div>
                {/* Axis labels: role names down the left (Y), lead count across
                    the bars (X) — mirrors the recharts axis titles above. */}
                <div className="grid gap-[14px] mt-[4px] min-w-[520px] md:min-w-0" style={{ gridTemplateColumns: '260px 1fr 30px' }}>
                  <span className="text-[11px] text-[#585450] text-right">Job title</span>
                  <span className="text-[11px] text-[#585450] text-center">Number of leads</span>
                  <span />
                </div>
                </div>
              </>
            );
          })()}
        </SectionCard>
      )}

      <SectionCard
        title={
          isFiltered
            ? selectedProductId != null
              ? 'Selected Target'
              : entityFilter === 'service'
              ? 'Services in slice'
              : entityFilter === 'gcc'
              ? 'GCC in slice'
              : 'Products in slice'
            : 'Top Products'
        }
        info="How each product is doing — how many people you contacted, how many opened or replied, and how many meetings you booked."
        subtitle={
          isFiltered
            ? `${topProducts.length} row${topProducts.length === 1 ? '' : 's'} matched`
            : 'By send volume'
        }
      >
        {loading && topProducts.length === 0 ? (
          <div className="space-y-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : topProducts.length === 0 ? (
          <EmptyState
            icon={FiTarget}
            title={isFiltered ? 'No targets match the current filter' : 'No campaign activity'}
            hint="Launch a campaign to see per-product performance."
          />
        ) : (
          <div className="overflow-x-auto mt-4">
            <table className="w-full text-[13.5px] border-collapse" style={{ minWidth: 560 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid #E5E7EB' }}>
                  <th className="text-left text-[#2B2926] uppercase text-[11px] font-bold tracking-[0.14em] pb-3 px-[10px]">Product</th>
                  <th className="text-right text-[#2B2926] uppercase text-[11px] font-bold tracking-[0.14em] pb-3 px-[10px]">Leads</th>
                  <th className="text-right text-[#2B2926] uppercase text-[11px] font-bold tracking-[0.14em] pb-3 px-[10px]">Sent</th>
                  <th className="text-right text-[#2B2926] uppercase text-[11px] font-bold tracking-[0.14em] pb-3 px-[10px]">Opened</th>
                  <th className="text-right text-[#2B2926] uppercase text-[11px] font-bold tracking-[0.14em] pb-3 px-[10px]">Replied</th>
                  <th className="text-right text-[#2B2926] uppercase text-[11px] font-bold tracking-[0.14em] pb-3 px-[10px]">Demos</th>
                </tr>
              </thead>
              <tbody>
                {topProducts.map((p) => {
                  // Entity-type chip (Product / Service / GCC) — kept from the
                  // HEAD feature but folded INLINE next to the product name so
                  // it doesn't add a column the 6-col header doesn't have.
                  const et = p.entity_type || 'product';
                  const cfg = et === 'service'
                    ? { label: 'Service', cls: 'text-[#065F46] bg-[#10B981]/18 border border-[#10B981]/45' }
                    : et === 'gcc'
                    ? { label: 'GCC', cls: 'text-[#2B2926] bg-[#2B2926]/8 border border-[#2B2926]/30' }
                    : { label: 'Product', cls: 'text-[#F55600] bg-[#F55600]/12 border border-[#F55600]/35' };
                  return (
                    <tr key={p.product_id} style={{ borderBottom: '1px solid rgba(229,231,235,0.7)' }}>
                      <td className="text-left py-[6px] px-[10px]">
                        <div className="min-w-0">
                          <span className="flex items-center gap-2 min-w-0">
                            <span className=" text-[#111] text-[14px] truncate max-w-[240px]">
                              {p.name || 'Unnamed'}
                            </span>
                            <span
                              className={[
                                'shrink-0 inline-flex items-center text-[10px] font-bold uppercase tracking-[0.1em] px-2 py-0.5 rounded-md',
                                cfg.cls,
                              ].join(' ')}
                            >
                              {cfg.label}
                            </span>
                          </span>
                          {p.category && (
                            <span className="block text-[#2B2926] text-[11.5px] font-medium mt-[1px] truncate max-w-[240px]">
                              {p.category}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="text-right py-[6px] px-[10px] tabular-nums text-[#111] ">{fmtNum(p.enrollments)}</td>
                      <td className="text-right py-[6px] px-[10px] tabular-nums text-[#111] ">{fmtNum(p.sent)}</td>
                      <td className="text-right py-[6px] px-[10px] tabular-nums text-[#111] ">{fmtNum(p.opened)}</td>
                      <td className="text-right py-[6px] px-[10px] tabular-nums text-[#111] ">{fmtNum(p.replied)}</td>
                      <td
                        className="text-right py-[6px] px-[10px] "
                        style={{ color: Number(p.demos) > 0 ? '#10B981' : '#2B2926' }}
                      >
                        {fmtNum(p.demos)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>

      {/* Bottom spacer so the last section isn't flush against the page edge. */}
      <div className="h-8" />

      {/* Footer line "N active campaigns · M leads in pipeline · Updated
          just now" removed per user request. Block is gone so the space
          it used to occupy collapses too. */}
      </div>
      )}
    </div>
  );
};

export default NexusDashboard;
