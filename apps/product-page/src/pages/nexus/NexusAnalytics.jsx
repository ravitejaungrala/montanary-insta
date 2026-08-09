import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, LabelList,
} from 'recharts';
import {
  FiDownload, FiAlertCircle, FiTarget, FiSend, FiEye, FiMessageCircle,
  FiCalendar, FiUserX,
} from 'react-icons/fi';

// ---------- helpers ----------
const PERIODS = [
  { value: '7d', label: '7d' },
  { value: '30d', label: '30d' },
  { value: '90d', label: '90d' },
];

const INTENT_ORDER = [
  'DEMO_BOOKED',
  'INTERESTED',
  'QUESTION',
  'NOT_NOW',
  'NOT_INTERESTED',
  'LEFT_COMPANY',
  'OUT_OF_OFFICE',
  'UNSUBSCRIBE',
];

// Distinct HUES from a colorblind-validated categorical set (matches
// NexusDashboard), mapped semantically — no more one-orange-at-many-opacities.
const INTENT_COLOR = {
  DEMO_BOOKED: '#008300',    // green  — booked demo (best outcome)
  INTERESTED: '#1BAF7A',     // aqua   — positive interest
  QUESTION: '#2A78D6',       // blue   — an inquiry
  NOT_NOW: '#EDA100',        // yellow — deferral
  NOT_INTERESTED: '#EB6834', // orange — soft no
  UNSUBSCRIBE: '#E34948',    // red    — hard no
  LEFT_COMPANY: '#4A3AA7',   // violet — informational
  OUT_OF_OFFICE: '#E87BA4',  // magenta — transient / auto-reply
};

// Mirror the 5 stages the backend's compute_funnel returns. Keeping this
// list in sync prevents the UI from rendering 6 bars on the fallback
// path and 5 bars when /funnel/stages is present.
const FUNNEL_STAGES = [
  { key: 'sent', label: 'Sent' },
  { key: 'opens', label: 'Opened' },
  { key: 'clicks', label: 'Clicked' },
  { key: 'replies', label: 'Replied' },
  { key: 'demo_bookings', label: 'Demo Booked' },
];

const fmtNum = (n) => {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—';
  const v = Number(n);
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}k`;
  return `${v}`;
};

// Format a percentage value AS-IS. Caller passes a percentage
// (0-100 range), not a fraction. The old version had a "<=1 means
// fraction" heuristic that auto-multiplied — but that broke for
// legitimately-small percentages like 0.32% (1 reply / 314 sent),
// rendering them as "32.0%". Backend's compute_summary returns
// reply_rate=0.32 meaning 0.32 percent, so the heuristic was wrong.
const fmtPct = (n) => {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—';
  return `${Number(n).toFixed(1)}%`;
};

// rate() returns a PERCENTAGE (0-100), not a fraction, so it matches
// what fmtPct expects without scaling. Used as a fallback when the
// backend hasn't pre-computed the rate field.
const rate = (num, denom) => {
  const n = Number(num) || 0;
  const d = Number(denom) || 0;
  if (d <= 0) return null;
  return (n / d) * 100;
};

const csvEscape = (v) => {
  if (v === null || v === undefined) return '';
  const s = String(v);
  if (s.includes(',') || s.includes('"') || s.includes('\n')) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
};

const downloadCsv = (filename, rows) => {
  const text = rows.map((r) => r.map(csvEscape).join(',')).join('\n');
  const blob = new Blob([text], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 100);
};

// ---------- UI atoms ----------
const Skeleton = ({ className = '' }) => (
  <div className={`bg-[#2B2926]/5 animate-pulse rounded-lg ${className}`} />
);

const KpiTile = ({ icon: Icon, label, value, loading }) => (
  <div className="bg-white rounded-2xl border border-[#2B2926]/10 p-4 flex flex-col gap-2">
    <div className="flex items-center justify-between">
      <span className="text-[10px] font-bold uppercase tracking-widest text-[#2B2926]/60">{label}</span>
      {Icon && (
        <span className="w-7 h-7 rounded-full bg-[#F55600]/10 text-[#F55600] flex items-center justify-center">
          <Icon size={12} />
        </span>
      )}
    </div>
    {loading ? (
      <Skeleton className="h-7 w-20" />
    ) : (
      <div className="text-2xl font-black text-[#2B2926] tracking-tight">{value}</div>
    )}
  </div>
);

const SectionCard = ({ title, action, children, className = '' }) => (
  // `min-w-0` is load-bearing — grid items default to min-width: auto
  // (intrinsic size). recharts' ResponsiveContainer measures its parent
  // and the intrinsic size of a chart can exceed the column width,
  // producing width(-1)/height(-1) warnings on first mount inside a
  // CSS grid. min-w-0 lets the card shrink to its grid track so the
  // chart's percentage-based sizing has a sane parent to measure.
  <div className={`bg-white rounded-2xl border border-[#2B2926]/10 p-5 min-w-0 ${className}`}>
    {(title || action) && (
      <div className="flex items-center justify-between mb-4">
        {title && <h3 className="text-sm font-black uppercase tracking-widest text-[#2B2926]">{title}</h3>}
        {action}
      </div>
    )}
    {children}
  </div>
);

const PillButton = ({ active, onClick, children }) => (
  <button
    type="button"
    onClick={onClick}
    className={`px-3 py-1.5 text-xs font-bold rounded-full border transition-colors ${
      active
        ? 'text-white border-transparent'
        : 'text-[#2B2926]/60 border-[#2B2926]/10 hover:bg-[#F55600]/5 hover:text-[#F55600]'
    }`}
    style={active ? { backgroundColor: '#F55600' } : undefined}
  >
    {children}
  </button>
);

// ---------- main component ----------
const NexusAnalytics = ({ authAxios, user, setMessage, apiBase }) => {
  const [me, setMe] = useState(null);
  const [workspaceId, setWorkspaceId] = useState('');
  const [period, setPeriod] = useState('30d');
  const [summary, setSummary] = useState(null);
  const [funnel, setFunnel] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Entity-type breakdown — 'all' | 'product' | 'service'. Drives both
  // the /per-product fetch filter and the heading label.
  const [entityFilter, setEntityFilter] = useState('all');
  const [perProductRows, setPerProductRows] = useState([]);
  const [perProductLoading, setPerProductLoading] = useState(false);

  // Initial /nexus/me fetch
  useEffect(() => {
    let cancelled = false;
    if (!authAxios) return undefined;
    (async () => {
      try {
        const res = await authAxios.get('/nexus/me');
        if (cancelled) return;
        const data = res?.data || null;
        setMe(data);
        setWorkspaceId(data?.default_workspace_id || (data?.workspaces?.[0]?.id ?? ''));
      } catch (e) {
        if (!cancelled) setError('Could not load Nexus profile.');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authAxios]);

  // Re-fetch analytics when workspace/period changes
  const loadAnalytics = useCallback(async () => {
    if (!authAxios) return;
    setLoading(true);
    setError(null);
    const wsParam = workspaceId ? `&workspace_id=${encodeURIComponent(workspaceId)}` : '';
    const wsQuery = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : '';

    const tasks = [
      authAxios.get(`/nexus/analytics/summary?period=${encodeURIComponent(period)}${wsParam}`),
      authAxios.get(`/nexus/analytics/funnel${wsQuery}`),
    ];
    const results = await Promise.allSettled(tasks);
    const [sumR, funR] = results;
    // /nexus/analytics/summary returns the WHOLE AnalyticsResponse:
    // { summary: AnalyticsSummary, funnel: [...], timeseries: [...] }.
    // The page reads fields like `summary.sent` directly, so unwrap the
    // nested `summary` here — otherwise every KPI tile renders as "—"
    // because `response.sent` is undefined (lives at `response.summary.sent`).
    setSummary(
      sumR.status === 'fulfilled' ? sumR.value?.data?.summary || null : null,
    );
    setFunnel(funR.status === 'fulfilled' ? funR.value?.data || null : null);

    if (sumR.status === 'rejected' && funR.status === 'rejected') {
      setError('Could not load analytics. Try again in a moment.');
    }
    setLoading(false);
  }, [authAxios, workspaceId, period]);

  useEffect(() => {
    loadAnalytics();
  }, [loadAnalytics]);

  // Per-product breakdown — re-fetches when workspace, entity_type, or
  // period changes. The period parameter is now mandatory so per-product
  // numbers reconcile with the time-windowed /summary endpoint (previously
  // /per-product was unbounded → it never matched any summary value).
  useEffect(() => {
    if (!authAxios) return undefined;
    let cancelled = false;
    setPerProductLoading(true);
    (async () => {
      try {
        const wsParam = workspaceId
          ? `&workspace_id=${encodeURIComponent(workspaceId)}`
          : '';
        const res = await authAxios.get(
          `/nexus/analytics/per-product?entity_type=${encodeURIComponent(entityFilter)}&period=${encodeURIComponent(period)}${wsParam}`,
        );
        if (cancelled) return;
        setPerProductRows(Array.isArray(res?.data?.products) ? res.data.products : []);
      } catch {
        if (!cancelled) setPerProductRows([]);
      } finally {
        if (!cancelled) setPerProductLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authAxios, workspaceId, entityFilter, period]);

  // ---------- derived data ----------
  const workspaces = me?.workspaces || [];

  const sent = Number(summary?.sent || 0);
  const opens = Number(summary?.opens || 0);
  const replies = Number(summary?.replies || 0);
  const unsubs = Number(summary?.unsubscribes ?? summary?.unsubscribe_count ?? 0);

  const openRate = summary?.open_rate ?? rate(opens, sent);
  const replyRate = summary?.reply_rate ?? rate(replies, sent);
  const intentRate = summary?.intent_signal_rate;
  const demoRate = summary?.demo_book_rate;
  const unsubRate = summary?.unsubscribe_rate ?? rate(unsubs, sent);

  // Funnel: prefer stages from /funnel; otherwise reconstruct from summary
  const funnelData = useMemo(() => {
    let stages = [];
    if (funnel?.stages && Array.isArray(funnel.stages) && funnel.stages.length > 0) {
      stages = funnel.stages.map((s) => ({
        name: s.name || s.label || s.key || '',
        count: Number(s.count ?? s.value ?? 0),
      }));
    } else if (summary) {
      // Backend funnel was unavailable — reconstruct from summary fields.
      // Stage order + keys must match backend compute_funnel() so the
      // chart shape stays identical regardless of which path served.
      stages = FUNNEL_STAGES.map((st) => {
        const raw = summary?.[st.key];
        return { name: st.label, count: Number(raw || 0) };
      });
    }
    const base = stages[0]?.count || 0;
    return stages.map((s) => ({
      ...s,
      pct: base > 0 ? (s.count / base) * 100 : 0,
    }));
  }, [funnel, summary]);

  // Per-step reply rate
  const perStepData = useMemo(() => {
    const src =
      summary?.per_step_reply_rate ||
      summary?.reply_rate_by_step ||
      summary?.replies_by_step ||
      null;
    if (!src) return [];
    if (Array.isArray(src)) {
      return src.map((row, i) => ({
        step: row.step !== undefined ? `Step ${row.step}` : `Step ${i}`,
        rate: Number(row.rate ?? row.reply_rate ?? row.value ?? 0),
      }));
    }
    if (typeof src === 'object') {
      return Object.keys(src)
        .sort()
        .map((k) => ({
          step: /^\d+$/.test(k) ? `Step ${k}` : k,
          rate: Number(src[k]) || 0,
        }));
    }
    return [];
  }, [summary]);

  // Intent breakdown for pie
  const intentData = useMemo(() => {
    const src = { ...(summary?.replies_breakdown_by_intent || {}) };
    // Fold the retired QUESTION_PRICE into QUESTION; show the concrete
    // demo_booked outcome instead of the transient DEMO_SCHEDULED (mirrors the
    // backend's chart_intent_breakdown so both views agree).
    if (src.QUESTION_PRICE) {
      src.QUESTION = (Number(src.QUESTION) || 0) + Number(src.QUESTION_PRICE);
      delete src.QUESTION_PRICE;
    }
    delete src.DEMO_SCHEDULED;
    const demos = Number(summary?.demo_bookings || 0);
    if (demos) src.DEMO_BOOKED = demos;
    const rows = INTENT_ORDER.map((k) => ({
      name: k,
      value: Number(src[k] || 0),
      color: INTENT_COLOR[k] || '#64748B',
    })).filter((r) => r.value > 0);
    return rows;
  }, [summary]);

  // ---------- CSV export ----------
  const handleExport = useCallback(() => {
    if (!summary) {
      setMessage && setMessage('Nothing to export yet');
      return;
    }
    const rows = [
      ['Metric', 'Value'],
      ['Period', period],
      ['Workspace', workspaceId || ''],
      ['Sent', sent],
      ['Opens', opens],
      ['Open rate', fmtPct(openRate)],
      ['Replies', replies],
      ['Reply rate', fmtPct(replyRate)],
      ['Intent signal rate', fmtPct(intentRate)],
      ['Demo book rate', fmtPct(demoRate)],
      ['Unsubscribe rate', fmtPct(unsubRate)],
      [],
      ['Funnel stage', 'Count', '% of Sent'],
      ...funnelData.map((s) => [s.name, s.count, `${s.pct.toFixed(1)}%`]),
      [],
      ['Intent', 'Replies'],
      ...intentData.map((r) => [r.name, r.value]),
    ];
    const stamp = new Date().toISOString().slice(0, 10);
    downloadCsv(`nexus-analytics-${period}-${stamp}.csv`, rows);
    setMessage && setMessage('Exported CSV');
  }, [summary, period, workspaceId, sent, opens, openRate, replies, replyRate, intentRate, demoRate, unsubRate, funnelData, intentData, setMessage]);

  // ---------- render ----------
  return (
    <div className="px-6 py-6 space-y-6 bg-white min-h-screen">
      {/* Header */}
      <div className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-4">
        <div>
          <h1 className="text-3xl font-black tracking-tight text-[#2B2926]">Campaign Analytics</h1>
          <p className="text-sm text-[#2B2926]/60 mt-1">
            Outbound performance across your Nexus workspace.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {workspaces.length > 1 && (
            <select
              value={workspaceId}
              onChange={(e) => setWorkspaceId(e.target.value)}
              className="text-xs font-semibold rounded-full border border-[#2B2926]/10 px-3 py-1.5 bg-white text-[#2B2926] focus:outline-none focus:ring-2 focus:ring-[#F55600]/40"
            >
              {workspaces.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name || w.id}
                </option>
              ))}
            </select>
          )}
          <div className="flex items-center gap-1.5 bg-white rounded-full border border-[#2B2926]/10 p-1">
            {PERIODS.map((p) => (
              <PillButton key={p.value} active={period === p.value} onClick={() => setPeriod(p.value)}>
                {p.label}
              </PillButton>
            ))}
          </div>
          <button
            type="button"
            onClick={handleExport}
            disabled={!summary}
            className="inline-flex items-center gap-2 text-xs font-bold uppercase tracking-wider px-3 py-2 rounded-full text-white disabled:opacity-50"
            style={{ backgroundColor: '#F55600' }}
          >
            <FiDownload size={12} />
            Export CSV
          </button>
        </div>
      </div>

      {/* ── Entity-type filter + per-product breakdown ────────────────── */}
      <div className="bg-white border border-[#2B2926]/10 rounded-2xl p-5">
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 mb-4">
          <div>
            <h2 className="text-lg font-black tracking-tight text-[#2B2926]">
              {entityFilter === 'service'
                ? 'Per-company analytics (Services)'
                : entityFilter === 'product'
                ? 'Per-product analytics (Products)'
                : 'Per-product analytics'}
            </h2>
            <p className="text-xs text-[#2B2926]/60 mt-0.5">
              Each row is a campaign target. Stats roll up sends, opens,
              replies, and demos across all sequences attached to it.
            </p>
          </div>
          <div className="inline-flex items-center gap-1 bg-white rounded-full border border-[#2B2926]/10 p-1 self-start">
            {[
              { id: 'all',     label: 'All' },
              { id: 'product', label: '📦 Product' },
              { id: 'service', label: '🛠 Service' },
            ].map((opt) => {
              const active = entityFilter === opt.id;
              return (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => setEntityFilter(opt.id)}
                  className={[
                    'px-3 py-1.5 rounded-full text-xs font-bold transition-all',
                    active
                      ? 'bg-[#F55600] text-white'
                      : 'text-[#2B2926]/60 hover:text-[#2B2926]',
                  ].join(' ')}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>
        </div>

        {perProductLoading ? (
          <div className="py-8 text-center text-xs text-[#2B2926]/40">Loading…</div>
        ) : perProductRows.length === 0 ? (
          <div className="py-8 text-center text-xs text-[#2B2926]/40">
            {entityFilter === 'service'
              ? 'No service-company campaigns yet. Create one via New Campaign and choose Service.'
              : entityFilter === 'product'
              ? 'No product campaigns yet. Create one via New Campaign and choose Product.'
              : 'No products yet. Create one via New Campaign.'}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {perProductRows.map((p) => (
              <div
                key={p.product_id}
                className="rounded-xl border border-[#2B2926]/10 p-4 hover:border-[#F55600]/40 transition-all"
              >
                <div className="flex items-start justify-between gap-2 mb-3">
                  <div className="min-w-0">
                    <div className="text-sm font-black text-[#2B2926] truncate">
                      {p.name || '(unnamed)'}
                    </div>
                    {p.category && (
                      <div className="text-[11px] text-[#2B2926]/50 truncate mt-0.5">
                        {p.category}
                      </div>
                    )}
                  </div>
                  <span
                    className={[
                      'shrink-0 inline-flex items-center text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded',
                      p.entity_type === 'service'
                        ? 'text-[#10B981] bg-[#10B981]/10'
                        : 'text-[#2B2926] bg-[#2B2926]/5',
                    ].join(' ')}
                  >
                    {p.entity_type === 'service' ? '🛠 Service' : '📦 Product'}
                  </span>
                </div>

                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div className="rounded-lg bg-[#2B2926]/[0.02] px-2.5 py-2">
                    <div className="text-[10px] uppercase tracking-wider text-[#2B2926]/50">Sent</div>
                    <div className="text-base font-black text-[#2B2926] mt-0.5">{p.sent}</div>
                  </div>
                  <div className="rounded-lg bg-[#2B2926]/[0.02] px-2.5 py-2">
                    <div className="text-[10px] uppercase tracking-wider text-[#2B2926]/50">Enrolled</div>
                    <div className="text-base font-black text-[#2B2926] mt-0.5">{p.enrollments}</div>
                  </div>
                  <div className="rounded-lg bg-[#2B2926]/[0.02] px-2.5 py-2">
                    <div className="text-[10px] uppercase tracking-wider text-[#2B2926]/50">Opens</div>
                    <div className="text-base font-black text-[#2B2926] mt-0.5">
                      {p.opened}{' '}
                      <span className="text-[10px] font-bold text-[#2B2926]/40">
                        ({p.open_rate}%)
                      </span>
                    </div>
                  </div>
                  <div className="rounded-lg bg-[#2B2926]/[0.02] px-2.5 py-2">
                    <div className="text-[10px] uppercase tracking-wider text-[#2B2926]/50">Replies</div>
                    <div className="text-base font-black text-[#2B2926] mt-0.5">
                      {p.replied}{' '}
                      <span className="text-[10px] font-bold text-[#2B2926]/40">
                        ({p.reply_rate}%)
                      </span>
                    </div>
                  </div>
                  <div className="rounded-lg bg-[#F55600]/5 px-2.5 py-2 col-span-2">
                    <div className="text-[10px] uppercase tracking-wider text-[#F55600]">Demos booked</div>
                    <div className="text-base font-black text-[#2B2926] mt-0.5">{p.demos}</div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {error && (
        <div
          className="rounded-2xl border p-4 flex items-center gap-3"
          style={{ borderColor: 'rgba(245,86,0,0.3)', backgroundColor: 'rgba(245,86,0,0.05)' }}
        >
          <FiAlertCircle style={{ color: '#F55600' }} />
          <span className="text-sm text-[#2B2926]">{error}</span>
        </div>
      )}

      {/* KPI row (6 tiles) */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <KpiTile icon={FiSend} label="Sent" value={fmtNum(sent)} loading={loading && !summary} />
        <KpiTile icon={FiEye} label="Open Rate" value={fmtPct(openRate)} loading={loading && !summary} />
        <KpiTile icon={FiMessageCircle} label="Reply Rate" value={fmtPct(replyRate)} loading={loading && !summary} />
        <KpiTile icon={FiTarget} label="Intent Rate" value={fmtPct(intentRate)} loading={loading && !summary} />
        <KpiTile icon={FiCalendar} label="Demo Book Rate" value={fmtPct(demoRate)} loading={loading && !summary} />
        <KpiTile icon={FiUserX} label="Unsubscribe" value={fmtPct(unsubRate)} loading={loading && !summary} />
      </div>

      {/* Funnel */}
      <SectionCard title="Conversion funnel">
        {loading && funnelData.length === 0 ? (
          <Skeleton className="h-72 w-full" />
        ) : funnelData.every((s) => s.count === 0) ? (
          <div className="h-48 flex items-center justify-center text-sm text-[#2B2926]/60">
            No funnel data for this period.
          </div>
        ) : (
          <div style={{ width: '100%', height: 320 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={funnelData}
                layout="vertical"
                margin={{ top: 8, right: 60, bottom: 8, left: 32 }}
              >
                <CartesianGrid stroke="rgba(0,0,0,0.1)" strokeDasharray="3 3" horizontal={false} />
                <XAxis
                  type="number"
                  tick={{ fill: 'rgba(0,0,0,0.6)', fontSize: 11 }}
                  axisLine={{ stroke: 'rgba(0,0,0,0.1)' }}
                  tickLine={false}
                />
                <YAxis
                  type="category"
                  dataKey="name"
                  width={120}
                  tick={{ fill: 'rgba(0,0,0,0.6)', fontSize: 12, fontWeight: 600 }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#FFFFFF',
                    border: '1px solid rgba(0,0,0,0.1)',
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  formatter={(value, _name, ctx) => {
                    const pct = ctx?.payload?.pct;
                    return [`${value} (${pct ? pct.toFixed(1) : '0.0'}%)`, 'Count'];
                  }}
                />
                <Bar dataKey="count" fill="#F55600" radius={[0, 6, 6, 0]} barSize={22}>
                  <LabelList
                    dataKey="pct"
                    position="right"
                    formatter={(v) => `${Number(v).toFixed(1)}%`}
                    style={{ fill: 'rgba(0,0,0,0.6)', fontSize: 11, fontWeight: 600 }}
                  />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </SectionCard>

      {/* Stratified panels */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <SectionCard title="Per-step reply rate">
          {loading && perStepData.length === 0 ? (
            <Skeleton className="h-64 w-full" />
          ) : perStepData.length === 0 ? (
            <div className="h-48 flex items-center justify-center text-sm text-[#2B2926]/60">
              No per-step data available.
            </div>
          ) : (
            <div style={{ width: '100%', height: 256 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={perStepData} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke="rgba(0,0,0,0.1)" strokeDasharray="3 3" vertical={false} />
                  <XAxis
                    dataKey="step"
                    tick={{ fill: 'rgba(0,0,0,0.6)', fontSize: 11 }}
                    axisLine={{ stroke: 'rgba(0,0,0,0.1)' }}
                    tickLine={false}
                  />
                  <YAxis
                    tick={{ fill: 'rgba(0,0,0,0.6)', fontSize: 11 }}
                    axisLine={false}
                    tickLine={false}
                    tickFormatter={(v) => `${(Math.abs(v) <= 1 ? v * 100 : v).toFixed(0)}%`}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: '#FFFFFF',
                      border: '1px solid rgba(0,0,0,0.1)',
                      borderRadius: 8,
                      fontSize: 12,
                    }}
                    formatter={(v) => [fmtPct(v), 'Reply rate']}
                  />
                  <Bar dataKey="rate" fill="#F55600" radius={[6, 6, 0, 0]} barSize={36} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </SectionCard>

        <SectionCard title="Intent breakdown">
          {loading && intentData.length === 0 ? (
            <Skeleton className="h-64 w-full" />
          ) : intentData.length === 0 ? (
            <div className="h-48 flex items-center justify-center text-sm text-[#2B2926]/60">
              No replies classified yet.
            </div>
          ) : (
            <div className="flex flex-col md:flex-row items-center gap-4">
              <div style={{ width: 200, height: 200 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={intentData}
                      dataKey="value"
                      nameKey="name"
                      cx="50%"
                      cy="50%"
                      innerRadius={50}
                      outerRadius={90}
                      paddingAngle={2}
                    >
                      {intentData.map((entry) => (
                        <Cell key={entry.name} fill={entry.color} stroke="#FFFFFF" strokeWidth={1} />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={{
                        backgroundColor: '#FFFFFF',
                        border: '1px solid rgba(0,0,0,0.1)',
                        borderRadius: 8,
                        fontSize: 12,
                      }}
                      formatter={(v, n) => [v, String(n).replace(/_/g, ' ')]}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <ul className="flex-1 space-y-1.5 w-full">
                {intentData.map((row) => (
                  <li key={row.name} className="flex items-center justify-between text-xs">
                    <span className="flex items-center gap-2 min-w-0">
                      <span
                        className="w-2.5 h-2.5 rounded-full shrink-0"
                        style={{ backgroundColor: row.color }}
                      />
                      <span className="text-[#2B2926]/60 truncate">
                        {row.name.replace(/_/g, ' ')}
                      </span>
                    </span>
                    <span className="font-bold text-[#2B2926] ml-2">{fmtNum(row.value)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </SectionCard>
      </div>
    </div>
  );
};

export default NexusAnalytics;
