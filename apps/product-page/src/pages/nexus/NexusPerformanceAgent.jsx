/**
 * NexusPerformanceAgent — ranks outreach patterns (channel, persona,
 * audience segment, timing) by outcome, so winning combinations can be
 * reused in future campaigns instead of guessed at cold each time.
 *
 * Core intent (see /implementation-v2.md §0): the page's job is "here's
 * what to do next," not "here are eight tables of numbers, go interpret
 * them" — so the latest insight's top-3 recommendations lead the page,
 * ranked tables come second, and history is collapsed out of the way.
 *
 * Backend: /nexus/performance/* (nexus_performance_insights +
 * nexus_winning_examples tables). Replaces the removed NexusPerformance.jsx
 * — see /implementation.md (v1) and /implementation-v2.md (this pass).
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Loader2,
  RefreshCw,
  Sparkles,
  Trash2,
  TrendingUp,
  Zap,
} from 'lucide-react';

const STATUS_PILL = {
  running: 'bg-[#F55600]/10 text-[#F55600]',
  ready: 'bg-[#10B981]/15 text-[#10B981]',
  failed: 'bg-[#F55600]/15 text-[#F55600]',
};

// Plain-English labels for panel headers — display-layer only, the backend
// `dimension` strings are unchanged (see /implementation-v2.md §2).
const DIMENSION_LABELS = {
  channel: 'Channel',
  variant: 'Email Persona',
  cadence_step: 'Email Stage',
  'segment:industry': 'Industry',
  'segment:revenue_band': 'Company Size',
  'segment:role': 'Job Title',
  'segment:technology': 'Tech Stack',
  'segment:location': 'Location',
  timing_bucket: 'Best Send Time',
};

const CADENCE_STEP_LABELS = {
  initial: 'Initial Email',
  followup_1: 'Follow-up 1',
  followup_2: 'Follow-up 2',
  closing: 'Closing Email',
};

const WEEKDAY_LABELS = {
  mon: 'Mondays',
  tue: 'Tuesdays',
  wed: 'Wednesdays',
  thu: 'Thursdays',
  fri: 'Fridays',
  sat: 'Saturdays',
  sun: 'Sundays',
};

const DIMENSION_ORDER = [
  'channel',
  'cadence_step',
  'segment:industry',
  'segment:revenue_band',
  'segment:role',
  'variant',
  'segment:technology',
  'segment:location',
  'timing_bucket',
];

// Cycled per-row avatar colors — existing palette only (see the shadow-card
// note above); no purple/blue from the reference design.
const AVATAR_COLORS = ['#F55600', '#10B981', '#2B2926'];

const METRIC_TABS = [
  { key: 'positive_reply', label: 'Positive reply', caption: '% of sends that got a positive reply' },
  { key: 'meeting_booked', label: 'Meeting booked', caption: '% of sends that led to a booked meeting' },
  { key: 'combined', label: 'Overall Score', caption: 'Blended score — not a literal reply rate' },
];

const fmtDate = (iso) => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString();
  } catch {
    return iso;
  }
};

const fmtPct = (v) => `${((v || 0) * 100).toFixed(1)}%`;

// 'tue_09-12' -> 'Tuesdays, 9-12 IST'. Falls back to the raw value for
// anything that doesn't match the expected shape (defensive — never crash
// on a malformed bucket string). The backend buckets in IST (see
// dimensions.py's _TZ_OFFSET) — labeled explicitly here so "9-12" doesn't
// read as the viewer's own browser timezone.
const fmtTimingBucket = (value) => {
  const m = /^([a-z]{3})_(\d{2})-(\d{2})$/.exec(value || '');
  if (!m) return value;
  const [, day, from, to] = m;
  return `${WEEKDAY_LABELS[day] || day}, ${parseInt(from, 10)}-${parseInt(to, 10)} IST`;
};

function formatSliceValue(dimension, value) {
  if (dimension === 'cadence_step') return CADENCE_STEP_LABELS[value] || value;
  if (dimension === 'timing_bucket') return fmtTimingBucket(value);
  return value;
}

// The top slice per DISTINCT dimension (already-sorted list -> first
// occurrence per dimension), capped at n. Without this, a single dominant
// dimension (e.g. send time) can occupy the #1 spot for every metric, so
// the ring row showed "Best Send Time" three times over with nothing else
// — this surfaces the next-best DIFFERENT levers instead.
function topDistinctDimensions(ranked, n = 3) {
  const seen = new Set();
  const out = [];
  for (const r of ranked || []) {
    if (seen.has(r.dimension)) continue;
    seen.add(r.dimension);
    out.push(r);
    if (out.length >= n) break;
  }
  return out;
}

// Group a flat ranked-slice list by dimension, top N per dimension (5 —
// bumped from 3 per feedback), in a fixed reading order so the card doesn't
// reshuffle between refreshes.
function groupByDimension(ranked, topN = 5) {
  const byDim = {};
  for (const r of ranked || []) {
    if (!byDim[r.dimension]) byDim[r.dimension] = [];
    if (byDim[r.dimension].length < topN) byDim[r.dimension].push(r);
  }
  return DIMENSION_ORDER.filter((d) => byDim[d]?.length).map((d) => ({ dimension: d, rows: byDim[d] }));
}

// Circular progress ring — SVG stroke-dasharray, no chart library. Existing
// palette only (#F55600 / #10B981 / #2B2926 / white — see NexusLayout.jsx's
// "PIPELYT's mandatory 4 colors" comment), not the purple/yellow of the
// reference design this pattern was borrowed from.
const ProgressRing = ({ pct, color, size = 76, stroke = 7 }) => {
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const clamped = Math.max(0, Math.min(100, pct));
  const offset = circumference * (1 - clamped / 100);
  return (
    <svg width={size} height={size} className="-rotate-90 shrink-0">
      <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="#2B2926" strokeOpacity="0.08" strokeWidth={stroke} />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke={color}
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={offset}
        style={{ transition: 'stroke-dashoffset 0.4s ease' }}
      />
    </svg>
  );
};

// One "at a glance" tile: ring (sized off the #1 slice) + up to 3 DISTINCT
// winning dimensions + a confidence pill — mirrors the reference's
// "Back End 25% / Overdue" pattern, mapped onto our dimension/confidence
// data instead of a project tracker's task status.
//
// Laid out as a fixed-structure flex column (ring/label fixed at top, the
// dimension list given flex-1 so it can vary in height, the pill pushed to
// the bottom via mt-auto) so the pill lines up across all 3 tiles in the
// parent grid regardless of how much text each tile's dimension list holds
// — a plain flex-wrap row couldn't guarantee that (found via live UI review,
// 2026-07-14: the ring read as a small decoration next to a wall of text,
// with no guaranteed alignment between tiles).
const StatRingTile = ({ label, tops, color }) => {
  const top = tops?.[0];
  if (!top) {
    return (
      <div className="flex flex-col items-center text-center h-full">
        <div className="w-[92px] h-[92px] rounded-full border-[8px] border-[#2B2926]/8" />
        <p className="text-sm font-bold text-[#2B2926] mt-3">{label}</p>
        <p className="text-xs text-[#2B2926]/40 mt-1">No data yet</p>
      </div>
    );
  }
  const pct = (top.raw_rate || 0) * 100;
  return (
    <div className="flex flex-col items-center text-center h-full">
      <div className="relative w-[92px] h-[92px] flex items-center justify-center">
        <ProgressRing pct={pct} color={color} size={92} stroke={8} />
        <span className="absolute text-lg font-black text-[#2B2926]">{pct.toFixed(0)}%</span>
      </div>
      <p className="text-sm font-bold text-[#2B2926] mt-3">{label}</p>

      {/* Each distinct dimension on ONE line ("Caption: value") instead of
          two stacked lines — halves the line count so the ring stays the
          visual focus instead of getting buried under a block of text. */}
      <div className="flex-1 flex flex-col justify-center gap-1 mt-2 min-h-[64px]">
        {tops.map((t) => (
          <p key={t.dimension} className="text-xs leading-snug">
            <span className="font-bold uppercase tracking-wide text-[#2B2926]/40">
              {DIMENSION_LABELS[t.dimension] || t.dimension}:{' '}
            </span>
            <span className="font-semibold text-[#2B2926]/80">
              {formatSliceValue(t.dimension, t.slice_value)}
            </span>
          </p>
        ))}
      </div>

      <span
        className={[
          'inline-flex mt-2 px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-wide',
          top.confidence === 'ok' ? 'bg-[#10B981]/15 text-[#10B981]' : 'bg-[#2B2926]/8 text-[#2B2926]/50',
        ].join(' ')}
      >
        {top.confidence === 'ok' ? 'On track' : 'Low sample'}
      </span>
    </div>
  );
};

const RankedTable = ({ ranked }) => {
  const groups = groupByDimension(ranked);
  if (!groups.length) {
    return <p className="text-[11px] text-[#2B2926]/50 py-3">No ranked patterns for this metric yet.</p>;
  }
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 py-4">
      {groups.map((g) => (
        <div key={g.dimension} className="rounded-xl shadow-[0_1px_2px_rgba(43,41,38,0.06)] p-3.5">
          <p className="text-[11px] font-bold uppercase tracking-wider text-[#2B2926]/50 mb-2.5">
            {DIMENSION_LABELS[g.dimension] || g.dimension}
          </p>
          <div className="space-y-2.5">
            {g.rows.map((r, idx) => (
              <div key={r.slice_value} className="flex items-center justify-between gap-3">
                <span className="text-sm text-[#2B2926] truncate flex items-center gap-1.5 min-w-0">
                  {idx === 0 && <TrendingUp className="w-3.5 h-3.5 text-[#10B981] shrink-0" />}
                  {formatSliceValue(g.dimension, r.slice_value)}
                </span>
                <span className="shrink-0 flex items-baseline gap-1.5">
                  <span className="text-base font-black text-[#2B2926]">{fmtPct(r.raw_rate)}</span>
                  <span className="text-xs text-[#2B2926]/50">{r.sends} sends</span>
                </span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
};

// The body shared by both the always-open "latest" card and each collapsed
// History entry once expanded — metric tabs + ranked panels + raw summary.
const InsightBody = ({ insight, metricTab, setMetricTab }) => (
  <div>
    {insight.status === 'failed' && insight.error && (
      <div className="px-4 pb-4 text-xs text-[#F55600]">{insight.error}</div>
    )}
    {insight.outreach_insights && (
      <div className="px-4 pb-4">
        <div className="flex items-center gap-1 border-b border-[#2B2926]/10">
          {METRIC_TABS.map((m) => (
            <button
              key={m.key}
              type="button"
              onClick={() => setMetricTab(m.key)}
              className={[
                'px-3 py-2 text-xs font-bold border-b-2 -mb-px',
                metricTab === m.key
                  ? 'border-[#F55600] text-[#F55600]'
                  : 'border-transparent text-[#2B2926]/50 hover:text-[#2B2926]',
              ].join(' ')}
            >
              {m.label}
            </button>
          ))}
        </div>
        <p className="text-xs text-[#2B2926]/40 pt-2">
          {METRIC_TABS.find((m) => m.key === metricTab)?.caption}
        </p>
        <RankedTable ranked={insight.outreach_insights[metricTab]} />
      </div>
    )}
  </div>
);

const DeleteButton = ({ onConfirm }) => {
  const [confirming, setConfirming] = useState(false);
  if (confirming) {
    return (
      <span className="flex items-center gap-1 text-[10px]" onClick={(e) => e.stopPropagation()}>
        <span className="text-[#2B2926]/50">Delete?</span>
        <button
          type="button"
          onClick={onConfirm}
          className="px-1.5 py-0.5 rounded bg-[#F55600] text-white font-bold"
        >
          Confirm
        </button>
        <button
          type="button"
          onClick={() => setConfirming(false)}
          className="px-1.5 py-0.5 rounded border border-[#2B2926]/10 text-[#2B2926]/60"
        >
          Cancel
        </button>
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        setConfirming(true);
      }}
      className="p-1 rounded hover:bg-[#F55600]/10 text-[#2B2926]/30 hover:text-[#F55600] shrink-0"
      title="Delete this insight"
    >
      <Trash2 className="w-3.5 h-3.5" />
    </button>
  );
};

const NexusPerformanceAgent = ({ authAxios, apiBase, user, setMessage }) => {
  const [insights, setInsights] = useState([]);
  const [winningExamples, setWinningExamples] = useState([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState('');
  const [expandedHistoryId, setExpandedHistoryId] = useState(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [metricTab, setMetricTab] = useState('positive_reply');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [insightsRes, examplesRes] = await Promise.all([
        authAxios.get(`${apiBase}/performance`),
        authAxios.get(`${apiBase}/performance/winning-examples`),
      ]);
      const list = insightsRes.data?.insights || insightsRes.data || [];
      setInsights(list);
      setWinningExamples(examplesRes.data?.examples || []);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  }, [authAxios, apiBase]);

  useEffect(() => {
    load();
  }, [load]);

  const generateNew = async () => {
    setGenerating(true);
    setError('');
    try {
      await authAxios.post(`${apiBase}/performance/generate`, {});
      await load();
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setGenerating(false);
    }
  };

  const deleteInsight = async (id) => {
    setError('');
    try {
      await authAxios.delete(`${apiBase}/performance/${id}`);
      setInsights((prev) => prev.filter((it) => it.id !== id));
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    }
  };

  // Latest READY insight leads the page; everything else (older insights,
  // plus any newer running/failed row) is history — see /implementation-v2.md §5.
  const { latest, history } = useMemo(() => {
    const latestReady = insights.find((it) => it.status === 'ready');
    const rest = insights.filter((it) => it.id !== latestReady?.id);
    return { latest: latestReady, history: rest };
  }, [insights]);

  const isSimulated = latest?.data_source === 'simulated';

  // Top 3 DISTINCT-dimension slices per metric (already sorted by the
  // scorer), feeding the "at a glance" ring row — see topDistinctDimensions().
  const topByMetric = useMemo(() => {
    const oi = latest?.outreach_insights;
    if (!oi) return {};
    return {
      positive_reply: topDistinctDimensions(oi.positive_reply, 3),
      meeting_booked: topDistinctDimensions(oi.meeting_booked, 3),
      combined: topDistinctDimensions(oi.combined, 3),
    };
  }, [latest]);

  return (
    <div className="bg-white">
      <div className="px-5 py-4 border-b border-[#2B2926]/10 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-black text-[#2B2926] tracking-tight">Performance</h1>
          <p className="text-xs text-[#2B2926]/60 mt-0.5">
            What's working right now, so you can do more of it.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={generateNew}
            disabled={generating}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#F55600] text-white text-xs font-bold hover:opacity-90 disabled:opacity-50"
          >
            {generating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
            Generate insights
          </button>
          <button
            type="button"
            onClick={load}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#2B2926]/10 text-xs font-bold text-[#2B2926] hover:bg-[#F55600]/5 disabled:opacity-50"
          >
            <RefreshCw className={['w-3.5 h-3.5', loading ? 'animate-spin' : ''].join(' ')} />
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="mx-5 mt-3 px-3 py-2 rounded-lg bg-[#F55600]/10 border border-[#F55600]/30 inline-flex items-center gap-2 text-xs text-[#F55600]">
          <AlertCircle className="w-3.5 h-3.5" />
          {error}
        </div>
      )}

      <div className="p-5 space-y-5">
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-[#2B2926]/60">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading insights…
          </div>
        ) : insights.length === 0 ? (
          <div className="text-center py-12">
            <TrendingUp className="w-10 h-10 mx-auto text-[#2B2926]/15 mb-2" />
            <p className="text-xs text-[#2B2926]/60">
              No insights yet. Click <span className="font-bold text-[#F55600]">Generate insights</span> to create your first one.
            </p>
          </div>
        ) : (
          <>
            {/* ── Hero: what's working right now ─────────────────────────── */}
            {latest && (
              <div className="rounded-2xl shadow-[0_1px_3px_rgba(43,41,38,0.06),0_8px_24px_rgba(43,41,38,0.06)] bg-white overflow-hidden">
                <div className="px-5 pt-5 pb-4">
                  <div className="flex items-start justify-between gap-3 mb-3">
                    <div className="flex items-center gap-2">
                      <Zap className="w-4 h-4 text-[#F55600] shrink-0" />
                      <p className="text-xs font-bold uppercase tracking-wider text-[#F55600]">
                        What's working right now
                      </p>
                      {isSimulated && (
                        <span
                          title="Not enough campaigns yet — showing typical outreach patterns, not your own campaigns."
                          className="inline-flex items-center px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 text-[10px] font-bold uppercase tracking-wide cursor-help"
                        >
                          Demo Data
                        </span>
                      )}
                    </div>
                    <DeleteButton onConfirm={() => deleteInsight(latest.id)} />
                  </div>

                  <h2 className="text-xl font-black text-[#2B2926] leading-tight mb-2">
                    {latest.recommendations?.headline || 'Generating recommendations…'}
                  </h2>

                  {latest.recommendations?.summary && (
                    <p className="text-sm text-[#2B2926]/70 leading-relaxed max-w-3xl">
                      {latest.recommendations.summary}
                    </p>
                  )}

                  {latest.recommendations?.caveat && !isSimulated && (
                    <p className="mt-2 text-xs text-[#2B2926]/40 italic">
                      {latest.recommendations.caveat}
                    </p>
                  )}

                  {/* 3-point quick-win TL;DR — condensed, distinct from the
                      5 detailed action cards below (same source data,
                      terser phrasing — see /implementation-v2.md follow-up). */}
                  {latest.recommendations?.quick_wins?.length > 0 && (
                    <div className="flex flex-wrap gap-2 mt-3">
                      {latest.recommendations.quick_wins.slice(0, 3).map((qw, i) => (
                        <span
                          key={i}
                          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-[#F55600]/8 text-[#2B2926] text-xs font-semibold"
                        >
                          <span className="flex items-center justify-center w-4 h-4 rounded-full bg-[#F55600] text-white text-[9px] font-black shrink-0">
                            {i + 1}
                          </span>
                          {qw}
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                {/* At-a-glance rings — top 3 distinct dimensions per metric.
                    CSS grid (not flex-wrap) guarantees 3 equal-width columns
                    that stretch to the same row height, so StatRingTile's
                    mt-auto pill lines up across all three regardless of how
                    much text any one tile's dimension list holds. */}
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-6 px-5 py-6 bg-[#2B2926]/[0.015] border-y border-[#2B2926]/5">
                  <div className="sm:border-r sm:border-[#2B2926]/8 sm:pr-6">
                    <StatRingTile label="Positive Reply" tops={topByMetric.positive_reply} color="#F55600" />
                  </div>
                  <div className="sm:border-r sm:border-[#2B2926]/8 sm:pr-6">
                    <StatRingTile label="Meeting Booked" tops={topByMetric.meeting_booked} color="#10B981" />
                  </div>
                  <div>
                    <StatRingTile label="Overall Score" tops={topByMetric.combined} color="#2B2926" />
                  </div>
                </div>

                {latest.recommendations?.actions?.length > 0 && (
                  <div className="px-5 py-5">
                    <p className="text-xs font-bold uppercase tracking-wider text-[#2B2926]/40 mb-3">
                      5 recommendations for your next campaign
                    </p>
                    <ul className="space-y-2">
                      {latest.recommendations.actions.slice(0, 5).map((action, i) => (
                        <li key={i} className="flex items-start gap-2.5">
                          <CheckCircle2 className="w-4 h-4 text-[#10B981] shrink-0 mt-0.5" />
                          <span className="text-sm font-semibold text-[#2B2926] leading-snug">{action.title}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="border-t border-[#2B2926]/5">
                  <InsightBody insight={latest} metricTab={metricTab} setMetricTab={setMetricTab} />
                </div>
              </div>
            )}

            {/* ── What's working right now: reusable patterns ────────────── */}
            {winningExamples.length > 0 && (
              <div>
                <p className="text-sm font-bold text-[#2B2926] mb-2.5">Patterns to reuse in your next campaign</p>
                <div className="rounded-2xl shadow-[0_1px_3px_rgba(43,41,38,0.06),0_8px_24px_rgba(43,41,38,0.06)] bg-white p-3.5 space-y-1">
                  {winningExamples.map((ex, i) => {
                    const label = ex.text || ex.email_subject || ex.example_type || '';
                    const initial = label.trim().charAt(0).toUpperCase() || '?';
                    const bg = AVATAR_COLORS[i % AVATAR_COLORS.length];
                    return (
                      <div key={i} className="flex items-center gap-3 py-1.5">
                        <div
                          className="w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-bold shrink-0"
                          style={{ backgroundColor: bg }}
                        >
                          {initial}
                        </div>
                        <span className="flex-1 min-w-0 truncate text-sm text-[#2B2926]/80">{label}</span>
                        <span className="shrink-0 text-[#10B981] font-bold text-sm">{fmtPct(ex.reply_rate)}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* ── History (everything else, collapsed) ────────────────────── */}
            {history.length > 0 && (
              <div>
                <button
                  type="button"
                  onClick={() => setHistoryOpen((v) => !v)}
                  className="w-full flex items-center gap-1.5 text-xs font-bold text-[#2B2926]/60 hover:text-[#2B2926] py-1"
                >
                  {historyOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                  History ({history.length})
                </button>

                {historyOpen && (
                  <div className="space-y-2 mt-2">
                    {history.map((it) => {
                      const isExp = expandedHistoryId === it.id;
                      return (
                        <div key={it.id} className="rounded-xl shadow-[0_1px_2px_rgba(43,41,38,0.06)] bg-white overflow-hidden">
                          <div className="w-full flex items-center justify-between gap-3 px-3.5 py-3 hover:bg-[#F55600]/5 transition-all">
                            <button
                              type="button"
                              onClick={() => setExpandedHistoryId(isExp ? null : it.id)}
                              className="flex items-center gap-2 min-w-0 flex-1 text-left"
                            >
                              <Sparkles className="w-3.5 h-3.5 text-[#F55600] shrink-0" />
                              <span className="text-xs font-bold text-[#2B2926] truncate">
                                {fmtDate(it.period_start)} — {fmtDate(it.period_end)}
                              </span>
                              <span
                                className={[
                                  'inline-flex px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider shrink-0',
                                  STATUS_PILL[it.status] || 'bg-[#2B2926]/5 text-[#2B2926]/60',
                                ].join(' ')}
                              >
                                {it.status || 'unknown'}
                              </span>
                            </button>
                            <DeleteButton onConfirm={() => deleteInsight(it.id)} />
                          </div>
                          {isExp && (
                            <div className="border-t border-[#2B2926]/5">
                              {it.recommendations?.headline && (
                                <p className="px-3.5 pt-3.5 text-sm text-[#2B2926]/70">
                                  {it.recommendations.headline}
                                </p>
                              )}
                              <InsightBody insight={it} metricTab={metricTab} setMetricTab={setMetricTab} />
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};

export default NexusPerformanceAgent;
