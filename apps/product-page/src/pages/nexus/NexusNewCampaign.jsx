/**
 * NexusNewCampaign — conversational campaign-creation wizard.
 *
 * Mirrors apps/nexus-legacy/client/src/pages/NewCampaign.jsx (the
 * ChatGPT-style 4-step flow), rebuilt in Tailwind to match the
 * PIPELYT brand palette (#F55600, #10B981, black, white).
 *
 * Flow:
 *   1. url       — enter product URL (live DuckDuckGo favicon next to input)
 *   2. scraping  — animated progress while we scrape + Gemini-analyse
 *   3. summary   — chat-style review of AI summary; user can edit directly
 *                  or ask AI to refine via a chat input
 *   4. targeting — auto-filled MultiSelect filters (locations / industries
 *                  / revenue / roles); user can adjust
 *   5. kb        — paste a knowledge base (pricing, case studies, etc.)
 *   6. launching — POST /nexus/analyze → creates Product + Campaign and
 *                  runs lead discovery; navigate to GTM Journey
 *
 * Skipped vs legacy (deliberate, v1):
 *   - Deepgram voice input on the refine step (third-party SDK)
 *
 * KB file handling (added 2026-05-26 Pinecone migration):
 *   Files dropped on the URL step are held in browser memory only —
 *   no backend call happens at drop time. When the user clicks Launch,
 *   /analyze fires (foreground; scrapes URL + creates product) and
 *   IN PARALLEL /nexus/kb/upload fires with the raw files. The kb/upload
 *   endpoint returns immediately; extract + chunk + embed + Pinecone
 *   all happen in a backend background task. A polling effect watches
 *   each asset's status and shows ONE "Knowledge base saved" toast when
 *   all files are done.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { isReadOnly } from '../../lib/permissions';
import {
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  Edit3,
  FileText,
  Globe,
  Loader2,
  Plus,
  RefreshCw,
  Send,
  Sparkles,
  Upload,
  X,
} from 'lucide-react';
import {
  ALL_TECHNOLOGIES,
  INDUSTRIES,
  REVENUE_OPTIONS,
  REVENUE_BAND_BOUNDS,
  ROLES,
  // SENIORITIES removed 2026-06-02 — titles imply seniority.
} from './targetingData';
import LeadsTable from './LeadsTable';
import LocationSelect from './LocationSelect';
import RepresentativeCard from './RepresentativeCard';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Walk up the DOM from `el` and collect every ancestor with scrollable
// overflow. We snapshot their scrollTop before the resize and restore
// after, so the brief height='auto' reflow can't jump the user away
// from the cursor while they're typing.
function _collectScrollableAncestors(el) {
  const out = [];
  let n = el && el.parentElement;
  while (n && n !== document.body) {
    const cs = window.getComputedStyle(n);
    if (/(auto|scroll)/.test(cs.overflowY) || /(auto|scroll)/.test(cs.overflow)) {
      out.push(n);
    }
    n = n.parentElement;
  }
  return out;
}

function autoResize(el) {
  if (!el) return;
  // Snapshot scroll positions of every scrollable ancestor + the window
  // before we mutate textarea height. Setting `height='auto'` momentarily
  // collapses the textarea to one row, which causes any ancestor with
  // overflow:auto to recompute scrollTop — that's what was visibly
  // jumping the page "up" while the user typed.
  const ancestors = _collectScrollableAncestors(el);
  const snapshots = ancestors.map((n) => n.scrollTop);
  const winY = window.scrollY;
  const winX = window.scrollX;

  el.style.height = 'auto';
  el.style.height = el.scrollHeight + 'px';

  // Restore. We restore inside a microtask so the browser has applied
  // the new height before we set scrollTop, otherwise the restore is
  // clamped to the still-collapsed scroll height.
  ancestors.forEach((n, i) => {
    if (n.scrollTop !== snapshots[i]) n.scrollTop = snapshots[i];
  });
  if (window.scrollY !== winY || window.scrollX !== winX) {
    window.scrollTo(winX, winY);
  }
}

function normaliseUrl(raw) {
  const u = (raw || '').trim();
  if (!u) return '';
  return /^https?:\/\//i.test(u) ? u : `https://${u}`;
}

function getDuckDuckGoFavicon(rawUrl) {
  try {
    const u = normaliseUrl(rawUrl);
    if (!u) return '';
    const { hostname } = new URL(u);
    if (!hostname.includes('.')) return '';
    return `https://icons.duckduckgo.com/ip3/${hostname.replace(/^www\./, '')}.ico`;
  } catch {
    return '';
  }
}

function getDirectFavicon(rawUrl) {
  try {
    const u = normaliseUrl(rawUrl);
    if (!u) return '';
    const { origin } = new URL(u);
    return `${origin}/favicon.ico`;
  } catch {
    return '';
  }
}

function FaviconImg({ src, url, size = 22, className = '' }) {
  if (!src) {
    return (
      <span className={className} style={{ width: size, height: size }}>
        <Globe className="w-full h-full text-[#2B2926]/40" />
      </span>
    );
  }
  return (
    <img
      src={src}
      width={size}
      height={size}
      className={className}
      alt=""
      onError={(e) => {
        const direct = getDirectFavicon(url);
        if (direct && e.target.src !== direct) {
          e.target.src = direct;
        } else {
          e.target.style.visibility = 'hidden';
        }
      }}
    />
  );
}

// Loader copy — generic so it reads correctly whether the user picked
// Product or Service at the URL step.
const SCRAPE_STEPS = [
  { emoji: '🌐', label: 'Visiting your website...' },
  { emoji: '📖', label: 'Reading your pages...' },
  { emoji: '🔍', label: 'Extracting key details...' },
  { emoji: '✍️', label: 'Writing your summary...' },
];

// Steps shown for the no-website (paste / upload) path — no scraping happens.
// Same length as SCRAPE_STEPS so the progress logic is unchanged.
const CONTENT_STEPS = [
  { emoji: '📄', label: 'Reading your content...' },
  { emoji: '🔍', label: 'Extracting key details...' },
  { emoji: '🧠', label: 'Understanding your product...' },
  { emoji: '🎯', label: 'Building your targeting...' },
];

// ---------------------------------------------------------------------------
// Attach-menu categories — shown when the "+" composer button is clicked.
// Picking one mutates kbInputRef.current.accept before opening the native
// file picker, so the OS dialog filters to that category's extensions.
// The backend's _ALLOWED_EXTS set in kb.py already accepts all of these.
// ---------------------------------------------------------------------------
const ATTACH_CATEGORIES = [
  {
    id: 'csv',
    label: 'CSV',
    ext: '.csv',
    accept: '.csv,text/csv',
  },
  {
    id: 'excel',
    label: 'Excel',
    ext: '.xlsx',
    accept: '.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  },
  {
    id: 'pdf',
    label: 'PDF',
    ext: '.pdf',
    accept: '.pdf,application/pdf',
  },
  {
    id: 'document',
    label: 'Document',
    ext: '.docx',
    accept: '.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  },
  {
    id: 'ppt',
    label: 'PPT',
    ext: '.pptx',
    accept: '.pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation',
  },
];

// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// Chip display formatter — 2026-05-29
// Apollo's canonical filter values come in mixed casing (e.g. "c_suite",
// "it", "engineering"), but the UI should show consistent Title Case
// ("C-Suite", "IT", "Engineering"). This helper does the cosmetic conversion
// on render only — the underlying VALUE that ships to the backend stays
// in its canonical form so Apollo recognises it.
// ---------------------------------------------------------------------------
const _DISPLAY_OVERRIDES = {
  c_suite:        'C-Suite',
  it:             'IT',
  hr:             'HR',
  pr:             'PR',
  rd:             'R&D',
  vp:             'VP',
  evp:            'EVP',
  svp:            'SVP',
  ceo:            'CEO',
  cfo:            'CFO',
  coo:            'COO',
  cto:            'CTO',
  cmo:            'CMO',
  cio:            'CIO',
  cro:            'CRO',
  chro:           'CHRO',
  ciso:           'CISO',
  saas:           'SaaS',
  paas:           'PaaS',
  iaas:           'IaaS',
  api:            'API',
  ai:             'AI',
  ml:             'ML',
  ux:             'UX',
  ui:             'UI',
  mulesoft:       'MuleSoft',
  sap:            'SAP',
  aws:            'AWS',
  gcp:            'GCP',
};

function formatChipLabel(value) {
  if (value == null) return '';
  const s = String(value).trim();
  if (!s) return '';
  const lower = s.toLowerCase();
  // 1. Hard-coded override (acronyms / brand-name casing)
  if (_DISPLAY_OVERRIDES[lower]) return _DISPLAY_OVERRIDES[lower];
  // 2. Already Title Case / has uppercase letters → leave alone
  if (/[A-Z]/.test(s)) return s;
  // 3. Snake_case / kebab-case → Title Case ("data" → "Data",
  //    "human_resources" → "Human Resources", "go-to-market" → "Go To Market")
  return s
    .replace(/[_-]+/g, ' ')
    .split(' ')
    .filter(Boolean)
    .map((w) => {
      const wl = w.toLowerCase();
      // Per-word override inside compound terms.
      return _DISPLAY_OVERRIDES[wl] || (w.charAt(0).toUpperCase() + w.slice(1));
    })
    .join(' ');
}

// ---------------------------------------------------------------------------
// AutofillBadge — tiny confidence indicator shown beside a targeting field's
// label. Reflects the deep-research pass's per-field confidence (the `_meta`
// returned by /analyze/suggest-targeting). The whole point of the 2026-06-01
// confidence gate: a field is auto-filled ONLY when the model is HIGH
// confidence. So:
//   • filled field  → "AI · High" (green), evidence on hover.
//   • suppressed     → "Low confidence · add manually" (the model had a guess
//                      but we deliberately left it blank rather than risk a
//                      wrong filter narrowing/widening Apollo discovery).
//   • no guess / fallback path → no badge.
// ---------------------------------------------------------------------------
// 2026-06-02: backend now emits numeric confidence (int 0-100). Older
// campaigns may still carry the legacy "high"/"medium"/"low" strings, so
// `toNumericConfidence` accepts both.
const _LEGACY_CONF_TO_NUM = { high: 90, medium: 60, low: 20 };
function toNumericConfidence(raw) {
  if (typeof raw === 'number' && Number.isFinite(raw)) {
    return Math.max(0, Math.min(100, Math.round(raw)));
  }
  if (typeof raw === 'string') {
    const s = raw.trim().toLowerCase();
    if (!s) return null;
    if (s in _LEGACY_CONF_TO_NUM) return _LEGACY_CONF_TO_NUM[s];
    const n = Number(s);
    if (Number.isFinite(n)) return Math.max(0, Math.min(100, Math.round(n)));
  }
  return null;
}

// 2026-06-02 — the AutofillBadge ("AI · 80", "Low (45) · add manually",
// etc.) was removed from the wizard per UX request — the confidence
// number was more noise than signal for the operator. The component is
// kept as a no-op so the 6 callsites further down stay compiling; the
// underlying `targetMeta` data is still populated (so a future
// "show evidence quote on hover" feature can re-enable it).
//
// To restore: revert this stub to the previous body that branched on
// `toNumericConfidence(meta.confidence)`.
// eslint-disable-next-line no-unused-vars
function AutofillBadge({ meta }) {
  return null;
}

// MultiSelect — searchable tag picker, brand palette only
// ---------------------------------------------------------------------------
function MultiSelect({ label, options, selected, onChange, placeholder, badge = null }) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);

  useEffect(() => {
    function handleClick(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const filtered = useMemo(
    () =>
      options
        .filter((o) => !selected.includes(o))
        .filter((o) => o.toLowerCase().includes(query.toLowerCase()))
        .slice(0, 60),
    [options, selected, query],
  );

  // LIST-ONLY: Enter selects the TOP MATCHING OPTION — custom typed values
  // are NOT accepted here. Only the Technologies field (FreeTextChips) allows
  // arbitrary custom entries; Industries/Roles are restricted to their lists.
  function selectTopMatch() {
    if (!query.trim()) return;
    const exact = options.find(
      (o) => o.toLowerCase() === query.trim().toLowerCase() && !selected.includes(o),
    );
    const top = exact || filtered[0];
    if (top) {
      onChange([...selected, top]);
      setQuery('');
    }
  }

  return (
    <div className="mb-2.5" ref={wrapRef}>
      <div className="flex items-center justify-between mb-1">
        <label className="block text-[11px] font-black uppercase tracking-wider text-[#2B2926]/60">
          {label}
        </label>
        {badge}
      </div>
      <div
        className="min-h-[40px] bg-white px-3 py-1.5 transition-all"
        style={{
          border: `1px solid ${open ? '#ff4d0d' : '#cdd1d9'}`,
          borderRadius: 12,
          boxShadow: '0 1px 2px rgba(15,17,21,0.04)',
        }}
      >
        <div className="flex flex-wrap items-center gap-2">
          {selected.map((s) => (
            <span
              key={s}
              className="inline-flex items-center gap-1.5 rounded-md text-[12.5px] font-semibold capitalize"
              style={{ background: '#f1f2f4', color: '#1c2128', padding: '4px 9px' }}
            >
              {formatChipLabel(s)}
              <button
                type="button"
                className="rounded"
                style={{ color: '#ff4d0d', lineHeight: 1, fontWeight: 700 }}
                onClick={() => onChange(selected.filter((x) => x !== s))}
                aria-label={`Remove ${formatChipLabel(s)}`}
              >
                <X className="w-3 h-3" />
              </button>
            </span>
          ))}
          <input
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                selectTopMatch();
              } else if (e.key === 'Backspace' && !query && selected.length) {
                onChange(selected.slice(0, -1));
              }
            }}
            placeholder={selected.length === 0 ? placeholder : 'Search…'}
            className="flex-1 min-w-[120px] bg-transparent text-sm text-[#2B2926] placeholder:text-[#2B2926]/40 focus:outline-none"
          />
        </div>
        {open && (filtered.length > 0 || query.trim()) && (
          <div className="mt-2 -mx-2 -mb-1.5 border-t border-[#2B2926]/10 max-h-56 overflow-y-auto bg-white">
            {filtered.map((o) => (
              <div
                key={o}
                className="px-3 py-2 text-sm text-[#2B2926] hover:bg-[#F55600]/5 cursor-pointer"
                onMouseDown={() => {
                  onChange([...selected, o]);
                  setQuery('');
                }}
              >
                {formatChipLabel(o)}
              </div>
            ))}
            {/* No "+ Add custom" row — Industries/Roles are list-only. Custom
                free-text entry is allowed ONLY on the Technologies field. */}
            {query.trim() && filtered.length === 0 && (
              <div className="px-3 py-2 text-xs text-[#2B2926]/40">
                No match — pick from the list.
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// RevenueCombo — free multi-band selection OR custom min/max range.
//
// Apollo's revenue filter is a SINGLE contiguous {min, max} window — there is
// no array form (verified against Apollo's People Search API docs). So the
// wizard lets the user either:
//   • pick one or more preset bands (any combination), collapsed into one
//     window spanning the lowest selected min → highest selected max, or
//   • type a Custom min/max.
// Picks needn't be adjacent; a non-adjacent pick just spans the gap (Apollo
// includes it — the UI flags this). The value flowing to the parent is:
//   null  →  no filter
//   { min, max, label, bands }  →  bands = selected band labels, or null when
//                                   the range came from the Custom inputs.
// ---------------------------------------------------------------------------
const REVENUE_OPEN_TOP = 1_000_000_000_000; // sentinel max for "$1B+" / open top

function fmtMoney(n) {
  if (n >= 1_000_000_000) return `$${Number((n / 1_000_000_000).toFixed(1))}B`;
  if (n >= 1_000_000) return `$${Number((n / 1_000_000).toFixed(1))}M`;
  if (n >= 1_000) return `$${Number((n / 1_000).toFixed(1))}K`;
  return `$${n}`;
}

function revenueLabel(min, max) {
  const openBottom = !min || min <= 0;
  const openTop = max >= REVENUE_OPEN_TOP;
  if (openBottom && openTop) return 'Any revenue';
  if (openBottom) return `< ${fmtMoney(max)}`;
  if (openTop) return `${fmtMoney(min)}+`;
  return `${fmtMoney(min)} – ${fmtMoney(max)}`;
}

// Split sorted band indices into runs of consecutive indices.
// e.g. [0,1,3] → [[0,1],[3,3]]
function contiguousGroups(idxs) {
  const groups = [];
  let start = idxs[0];
  let prev = idxs[0];
  for (let k = 1; k < idxs.length; k++) {
    if (idxs[k] === prev + 1) {
      prev = idxs[k];
    } else {
      groups.push([start, prev]);
      start = idxs[k];
      prev = idxs[k];
    }
  }
  groups.push([start, prev]);
  return groups;
}

// Max number of revenue windows Apollo can be searched on (one search each).
// Matches MAX_REVENUE_WINDOWS in apps/backend/.../discover_for_campaign.py.
const MAX_REVENUE_WINDOWS = 3;

// Build the revenue value from selected bands AND custom ranges, which COEXIST.
// `ranges` (the windows sent to the backend) = one per contiguous band group,
// FOLLOWED by the custom ranges, in that order. Returns null when empty. Does
// not cap — callers enforce MAX_REVENUE_WINDOWS before committing.
//   value = { bands:[labels], customs:[{min,max}], ranges:[{min,max}], label, min, max }
function buildRevenue(bandLabels, customs) {
  const idxs = (bandLabels || [])
    .map((b) => REVENUE_OPTIONS.indexOf(b))
    .filter((i) => i >= 0)
    .sort((a, b) => a - b);
  const bandRanges = idxs.length
    ? contiguousGroups(idxs).map(([a, b]) => ({
        min: REVENUE_BAND_BOUNDS[REVENUE_OPTIONS[a]].min,
        max: REVENUE_BAND_BOUNDS[REVENUE_OPTIONS[b]].max,
      }))
    : [];
  const cust = customs || [];
  const ranges = [...bandRanges, ...cust];
  if (!ranges.length) return null;
  return {
    bands: idxs.map((i) => REVENUE_OPTIONS[i]),
    customs: cust,
    ranges,
    label: ranges.map((r) => revenueLabel(r.min, r.max)).join(', '),
    min: Math.min(...ranges.map((r) => r.min)),
    max: Math.max(...ranges.map((r) => r.max)),
  };
}

// Build the value object from whatever was persisted in product.icp.revenue_range
// — a band-label string (Gemini autofill), a {min,max} object, or a list of
// {min,max} windows (a prior save). Restored windows are treated as custom
// ranges (we can't tell which came from bands) and capped at MAX_REVENUE_WINDOWS.
function revenueFromStored(stored) {
  if (!stored || stored === 'Any') return null;
  if (typeof stored === 'string') {
    return REVENUE_BAND_BOUNDS[stored] ? buildRevenue([stored], []) : null;
  }
  if (Array.isArray(stored)) {
    const ranges = stored
      .filter((r) => r && (r.min != null || r.max != null))
      .map((r) => ({
        min: Number(r.min) || 0,
        max: r.max != null ? Number(r.max) : REVENUE_OPEN_TOP,
      }))
      .slice(0, MAX_REVENUE_WINDOWS);
    return ranges.length ? buildRevenue([], ranges) : null;
  }
  if (typeof stored === 'object' && (stored.min != null || stored.max != null)) {
    const lo = Number(stored.min) || 0;
    const hi = stored.max != null ? Number(stored.max) : REVENUE_OPEN_TOP;
    return buildRevenue([], [{ min: lo, max: hi }]);
  }
  return null;
}

function RevenueCombo({ label, value, onChange, badge = null }) {
  const [open, setOpen] = useState(false);
  const [customOpen, setCustomOpen] = useState(false);
  const [minM, setMinM] = useState('');
  const [maxM, setMaxM] = useState('');
  const wrapRef = useRef(null);

  useEffect(() => {
    function handleClick(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const selectedIdxs = (value?.bands || [])
    .map((b) => REVENUE_OPTIONS.indexOf(b))
    .filter((i) => i >= 0)
    .sort((a, b) => a - b);

  // Free multi-select — any bands may be picked, adjacent or not. Apollo only
  // supports a single contiguous window, so a multi-band pick is collapsed to
  // span the lowest selected min → highest selected max (see mergeBands). When
  // the picks aren't adjacent that span necessarily includes the gap between
  // them (flagged below), which is the accepted trade-off for free selection.
  const atLimit = (value?.ranges?.length || 0) >= MAX_REVENUE_WINDOWS;

  function toggleBand(i) {
    const removing = selectedIdxs.includes(i);
    const nextIdxs = removing
      ? selectedIdxs.filter((x) => x !== i)
      : [...selectedIdxs, i].sort((a, b) => a - b);
    const next = buildRevenue(
      nextIdxs.map((x) => REVENUE_OPTIONS[x]),
      value?.customs || [],
    );
    // Adding may create a NEW window — block if that would exceed the cap.
    if (!removing && next && next.ranges.length > MAX_REVENUE_WINDOWS) return;
    onChange(next);
  }

  function applyCustom() {
    if (atLimit) return; // already at 3 windows
    const lo = minM.trim() ? Math.round(parseFloat(minM) * 1_000_000) : 0;
    const hi = maxM.trim() ? Math.round(parseFloat(maxM) * 1_000_000) : REVENUE_OPEN_TOP;
    if (Number.isNaN(lo) || Number.isNaN(hi)) return;
    if (lo <= 0 && hi >= REVENUE_OPEN_TOP) {
      // "any" isn't a real filter — just clear the inputs.
      setMinM('');
      setMaxM('');
      return;
    }
    const [a, b] = hi < lo ? [hi, lo] : [lo, hi];
    const next = buildRevenue(value?.bands || [], [
      ...(value?.customs || []),
      { min: a, max: b },
    ]);
    if (next.ranges.length > MAX_REVENUE_WINDOWS) return;
    onChange(next);
    setMinM('');
    setMaxM('');
  }

  const isSelected = (i) => selectedIdxs.includes(i);

  // Remove the i-th window chip. ranges = [band-group windows…, custom windows…],
  // so map the chip index back to either a band group or a custom range.
  function removeRange(i) {
    const idxs = (value?.bands || [])
      .map((b) => REVENUE_OPTIONS.indexOf(b))
      .filter((x) => x >= 0)
      .sort((a, b) => a - b);
    const groups = idxs.length ? contiguousGroups(idxs) : [];
    if (i < groups.length) {
      const [a, b] = groups[i];
      const remainingBands = idxs
        .filter((x) => x < a || x > b)
        .map((x) => REVENUE_OPTIONS[x]);
      onChange(buildRevenue(remainingBands, value?.customs || []));
    } else {
      const ci = i - groups.length;
      const remainingCustoms = (value?.customs || []).filter((_, k) => k !== ci);
      onChange(buildRevenue(value?.bands || [], remainingCustoms));
    }
  }

  return (
    <div className="mb-4" ref={wrapRef}>
      <div className="flex items-center justify-between mb-2">
        <label className="block text-xs font-black uppercase tracking-wider text-[#2B2926]/60">
          {label}
        </label>
        {badge}
      </div>
      <div
        className={[
          'relative rounded-lg border bg-white transition-all',
          open ? 'border-[#F55600]' : 'border-[#2B2926]/10',
        ].join(' ')}
      >
        <div
          onClick={() => setOpen((o) => !o)}
          className="w-full flex items-center justify-between gap-2 px-3 py-1.5 min-h-[42px] rounded-lg cursor-pointer"
        >
          <div className="flex flex-wrap items-center gap-2 flex-1 min-w-0">
            {value ? (
              value.ranges.map((r, i) => (
                <span
                  key={i}
                  className="inline-flex items-center gap-1.5 rounded-md text-[12.5px] font-semibold"
                  style={{ background: '#f1f2f4', color: '#1c2128', padding: '4px 9px' }}
                >
                  {revenueLabel(r.min, r.max)}
                  <button
                    type="button"
                    className="rounded"
                    style={{ color: '#ff4d0d', lineHeight: 1, fontWeight: 700 }}
                    onClick={(e) => {
                      e.stopPropagation();
                      removeRange(i);
                    }}
                    aria-label={`Remove ${revenueLabel(r.min, r.max)}`}
                  >
                    <X className="w-3 h-3" />
                  </button>
                </span>
              ))
            ) : (
              <span className="text-sm text-[#2B2926]/40">Any revenue</span>
            )}
          </div>
          <ChevronDown className="w-4 h-4 text-[#2B2926]/40 shrink-0" />
        </div>
        {open && (
          <div className="absolute left-0 right-0 top-full mt-1 z-10 border border-[#2B2926]/10 rounded-lg bg-white shadow-lg overflow-hidden">
            <div className="px-3 pt-2 pb-1 text-[11px] text-[#2B2926]/50">
              {atLimit
                ? 'Maximum 3 revenue filters reached — remove one to add another.'
                : 'Pick bands and/or add custom ranges (up to 3).'}
            </div>
            <div className="max-h-52 overflow-y-auto">
              {REVENUE_OPTIONS.map((opt, i) => {
                const isSel = isSelected(i);
                return (
                  <button
                    key={opt}
                    type="button"
                    onClick={() => toggleBand(i)}
                    className="w-full flex items-center gap-2 px-3 py-2 text-sm text-left text-[#2B2926] hover:bg-[#F55600]/5 cursor-pointer transition-colors"
                  >
                    <span
                      className={[
                        'w-4 h-4 rounded border flex items-center justify-center shrink-0',
                        isSel ? 'bg-[#F55600] border-[#F55600]' : 'border-[#2B2926]/25',
                      ].join(' ')}
                    >
                      {isSel && <Check className="w-3 h-3 text-white" />}
                    </span>
                    {opt}
                  </button>
                );
              })}
            </div>
            {/* Custom range */}
            <div className="border-t border-[#2B2926]/10">
              <button
                type="button"
                onClick={() => setCustomOpen((c) => !c)}
                className="w-full flex items-center justify-between px-3 py-2 text-sm text-[#2B2926] hover:bg-[#F55600]/5"
              >
                <span className="font-semibold">Custom range</span>
                <ChevronDown
                  className={`w-4 h-4 text-[#2B2926]/40 transition-transform ${customOpen ? 'rotate-180' : ''}`}
                />
              </button>
              {customOpen && (
                <div className="px-3 pb-3 pt-1 space-y-2">
                  <div className="flex items-center gap-2">
                    <div className="flex-1">
                      <label className="block text-[10px] font-bold uppercase tracking-wider text-[#2B2926]/45 mb-1">
                        Min ($M)
                      </label>
                      <input
                        type="number"
                        min="0"
                        value={minM}
                        onChange={(e) => setMinM(e.target.value)}
                        placeholder="0"
                        className="w-full px-2 py-1.5 rounded-md border border-[#2B2926]/15 text-sm focus:outline-none focus:border-[#F55600]"
                      />
                    </div>
                    <div className="flex-1">
                      <label className="block text-[10px] font-bold uppercase tracking-wider text-[#2B2926]/45 mb-1">
                        Max ($M)
                      </label>
                      <input
                        type="number"
                        min="0"
                        value={maxM}
                        onChange={(e) => setMaxM(e.target.value)}
                        placeholder="∞"
                        className="w-full px-2 py-1.5 rounded-md border border-[#2B2926]/15 text-sm focus:outline-none focus:border-[#F55600]"
                      />
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={applyCustom}
                    disabled={atLimit}
                    className="w-full py-1.5 rounded-md bg-[#2B2926] text-white text-[13px] font-bold hover:bg-[#2B2926]/85 disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {atLimit ? 'Max 3 filters reached' : 'Add custom range'}
                  </button>
                </div>
              )}
            </div>
            {value && (
              <button
                type="button"
                onClick={() => {
                  onChange(null);
                  setMinM('');
                  setMaxM('');
                  setCustomOpen(false);
                }}
                className="w-full px-3 py-2 text-[12px] font-bold text-[#2B2926]/50 hover:text-[#F55600] border-t border-[#2B2926]/10 text-left"
              >
                Clear revenue filter
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// FreeTextChips — multi-select chip input for TARGET TECHNOLOGIES. LIST-ONLY:
// `suggestions` is the full Apollo technology taxonomy (~5k) and the user can
// only pick from it — arbitrary typed values are not accepted. (Differs from
// MultiSelect only in its prefix-ranked autocomplete, tuned for the big list.)
// ---------------------------------------------------------------------------
function FreeTextChips({ label, suggestions = [], selected, onChange, placeholder, badge = null }) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);

  useEffect(() => {
    function handleClick(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  // Rank matches so the query is treated as a PREFIX first, not just any
  // substring: typing "a" should surface "AWS"/"Adobe…" before "SAP" (which
  // only contains an 'a' mid-word). Rank 0 = name starts with the query,
  // 1 = a word inside the name starts with it, 2 = appears anywhere. The sort
  // is stable, so within a rank the original order (COMMON_TECHNOLOGIES first)
  // is preserved.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const pool = suggestions.filter((o) => !selected.includes(o));
    if (!q) return pool.slice(0, 30);
    const ranked = [];
    for (const o of pool) {
      const lo = o.toLowerCase();
      const idx = lo.indexOf(q);
      if (idx === -1) continue;
      const rank = idx === 0 ? 0 : lo[idx - 1] === ' ' ? 1 : 2;
      ranked.push({ o, rank });
    }
    ranked.sort((a, b) => a.rank - b.rank);
    return ranked.slice(0, 30).map((r) => r.o);
  }, [suggestions, selected, query]);

  // LIST-ONLY: Enter selects the TOP MATCHING suggestion — arbitrary typed
  // values are NOT accepted (Technologies is restricted to Apollo's taxonomy,
  // same as Industries/Roles).
  function selectTopMatch() {
    if (!query.trim()) return;
    const exact = suggestions.find(
      (o) => o.toLowerCase() === query.trim().toLowerCase() && !selected.includes(o),
    );
    const top = exact || filtered[0];
    if (top) {
      onChange([...selected, top]);
      setQuery('');
    }
  }

  return (
    <div className="mb-2.5" ref={wrapRef}>
      <div className="flex items-center justify-between mb-1">
        <label className="block text-[11px] font-black uppercase tracking-wider text-[#2B2926]/60">
          {label}
        </label>
        {badge}
      </div>
      <div
        className="min-h-[40px] bg-white px-3 py-1.5 transition-all"
        style={{
          border: `1px solid ${open ? '#ff4d0d' : '#cdd1d9'}`,
          borderRadius: 12,
          boxShadow: '0 1px 2px rgba(15,17,21,0.04)',
        }}
      >
        <div className="flex flex-wrap items-center gap-2">
          {selected.map((s) => (
            <span
              key={s}
              className="inline-flex items-center gap-1.5 rounded-md text-[12.5px] font-semibold capitalize"
              style={{ background: '#f1f2f4', color: '#1c2128', padding: '4px 9px' }}
            >
              {formatChipLabel(s)}
              <button
                type="button"
                className="rounded"
                style={{ color: '#ff4d0d', lineHeight: 1, fontWeight: 700 }}
                onClick={() => onChange(selected.filter((x) => x !== s))}
                aria-label={`Remove ${formatChipLabel(s)}`}
              >
                <X className="w-3 h-3" />
              </button>
            </span>
          ))}
          <input
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                selectTopMatch();
              } else if (e.key === 'Backspace' && !query && selected.length) {
                // Backspace on empty input removes the last chip — matches
                // Notion / Linear chip-input conventions.
                onChange(selected.slice(0, -1));
              }
            }}
            placeholder={selected.length === 0 ? placeholder : 'Search…'}
            className="flex-1 min-w-[160px] bg-transparent text-sm text-[#2B2926] placeholder:text-[#2B2926]/40 focus:outline-none"
          />
        </div>
        {open && (filtered.length > 0 || query.trim()) && (
          <div className="mt-2 -mx-2 -mb-1.5 border-t border-[#2B2926]/10 max-h-56 overflow-y-auto bg-white">
            {filtered.map((o) => (
              <div
                key={o}
                className="px-3 py-2 text-sm text-[#2B2926] hover:bg-[#F55600]/5 cursor-pointer"
                onMouseDown={() => {
                  onChange([...selected, o]);
                  setQuery('');
                }}
              >
                {formatChipLabel(o)}
              </div>
            ))}
            {/* No "+ Add custom" row — Technologies is list-only (restricted to
                Apollo's taxonomy), same as Industries/Roles. */}
            {query.trim() && filtered.length === 0 && (
              <div className="px-3 py-2 text-xs text-[#2B2926]/40">
                No match — pick from the list.
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Product-description card pieces — HOISTED to module scope on purpose.
// Defining these inside the parent's render (as before) recreated the
// component on every keystroke, which unmounted/remounted the <textarea> and
// blurred it after each character → the editor felt "not writable". At module
// scope their identity is stable, so the inputs keep focus while typing.
// ---------------------------------------------------------------------------
function DescBullets({ items }) {
  const clean = (items || []).filter((x) => x && x.trim());
  if (clean.length === 0) return null;
  return (
    <ul className="list-disc pl-5 mt-1.5 space-y-0.5">
      {clean.map((c, i) => (
        <li key={`${c}-${i}`} className="text-[12px] text-[#2B2926]/85 leading-relaxed">
          {c}
        </li>
      ))}
    </ul>
  );
}

// One section of the editable description card. `editing` drives read vs edit;
// `onChange(field, value)` patches the parent's editableDescription.
function DescSection({ sectionKey, title, body, lists, editing, onChange }) {
  const allListsEmpty = (lists || []).every((l) => !l.items || l.items.length === 0);
  const bodyEmpty = !body || !body.trim();
  if (!editing && bodyEmpty && allListsEmpty) return null;
  const bodyField =
    sectionKey === 'what_is'
      ? 'what_the_company_is'
      : sectionKey === 'what_do'
      ? 'what_they_do'
      : 'who_they_serve';
  return (
    <div className="mb-3 last:mb-0">
      <div className="text-[10px] font-black uppercase tracking-wider text-[#2B2926] mb-1">
        {title}
      </div>
      {!editing ? (
        <>
          {body && (
            <p className="text-[13px] text-[#2B2926]/85 leading-relaxed">{body}</p>
          )}
          {(lists || []).map((lst) => (
            <DescBullets key={lst.label} items={lst.items} />
          ))}
        </>
      ) : (
        <div className="space-y-3 mt-1">
          {/* autoGrow ref + onInput keep the box fitted to its content on the
              first render AND while typing, so blank lines / gaps the user adds
              stay visible and editable (overflow-hidden hides the scrollbar). */}
          <textarea
            className="w-full text-[15px] text-[#2B2926] bg-transparent border-0 px-0 py-1 leading-7 resize-none overflow-hidden focus:outline-none focus:ring-0 placeholder:text-[#2B2926]/30"
            rows={2}
            ref={(el) => {
              if (el) {
                el.style.height = 'auto';
                el.style.height = `${el.scrollHeight}px`;
              }
            }}
            value={body || ''}
            onChange={(e) => onChange(bodyField, e.target.value)}
            onInput={(e) => {
              e.target.style.height = 'auto';
              e.target.style.height = `${e.target.scrollHeight}px`;
            }}
            placeholder="Write here…"
          />
          {/* List sub-labels (Services / Industries) removed per request — the
              list is just clean editable lines under its section heading. */}
          {(lists || []).map((lst) => (
            <textarea
              key={lst.label}
              className="w-full text-[14px] text-[#2B2926] bg-transparent border-0 px-0 py-1 leading-7 resize-none overflow-hidden focus:outline-none focus:ring-0 placeholder:text-[#2B2926]/30"
              rows={2}
              ref={(el) => {
                if (el) {
                  el.style.height = 'auto';
                  el.style.height = `${el.scrollHeight}px`;
                }
              }}
              value={(lst.items || []).join('\n')}
              onChange={(e) =>
                // Keep empty lines while typing (don't filter) so pressing
                // Enter creates a new line/gap. Empties are filtered for
                // display (DescBullets) and when the description is saved.
                onChange(lst.field, e.target.value.split('\n'))
              }
              onInput={(e) => {
                e.target.style.height = 'auto';
                e.target.style.height = `${e.target.scrollHeight}px`;
              }}
              placeholder="Add one per line…"
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
const NexusNewCampaign = ({ authAxios, user, setMessage, apiBase, onNavigate }) => {
  const [step, setStep] = useState('url'); // url | scraping | summary | targeting | kb | launching | launched
  const [error, setError] = useState('');

  // URL + scrape state
  const [url, setUrl] = useState('');
  const [faviconUrl, setFaviconUrl] = useState('');
  const [productName, setProductName] = useState('');
  const [summaryText, setSummaryText] = useState('');
  const [rawAnalysis, setRawAnalysis] = useState(null);
  // Full scraped site text (homepage + subpages) returned by /scrape-preview.
  // Forwarded to /analyze/suggest-targeting so the grounded ICP research reads
  // the REAL page content (incl. customer pages), not just the summary.
  const [scrapedContent, setScrapedContent] = useState('');
  // ── No-URL input mode (2026-06-12) ───────────────────────────────────────
  // 'url' = scrape a website (default). 'content' = the user has no website and
  // pastes text or uploads a .pdf/.docx/.pptx → we extract its text, generate
  // the description + ICP from it, SKIP the product-description page, and land
  // straight on the ICP filters page.
  // Set by the submit handlers (not a toggle): 'url' = website was submitted;
  // 'content' = pasted text / uploaded file was submitted. Drives the launch
  // payload (url vs content). `submittedContent` holds the exact text used.
  const [inputMode, setInputMode] = useState('url'); // 'url' | 'content'
  const [submittedContent, setSubmittedContent] = useState('');
  const [pastedContent, setPastedContent] = useState('');   // typed/uploaded file text
  const [productNameInput, setProductNameInput] = useState('');
  const [contentFileName, setContentFileName] = useState('');
  const [contentUploadBusy, setContentUploadBusy] = useState(false);
  // Editable mirror of rawAnalysis.product_description so the user can
  // edit each of the 3 sections (what_is / what_do / who_serve) inline
  // on the summary screen. Initialized whenever rawAnalysis updates,
  // and the launch payload pulls from this (not the legacy summaryText
  // textarea) when product_description is present.
  const [editableDescription, setEditableDescription] = useState(null);
  // 2026-05-29 — Switched from per-section edit pencils to a single
  // top-level Edit toggle per user request: "include only edit button to
  // click and edit entire rather than each section edit in ui".
  const [editingAll, setEditingAll] = useState(false);
  const [brandColors, setBrandColors] = useState([]);
  const [scrapeStep, setScrapeStep] = useState(0);

  // Product vs. service detection — Gemini's call is a hint that the user
  // can override with one click. Drives the suggest-targeting prompt and
  // downstream service-aware labels in analytics + GTM journey.
  const [entityType, setEntityType] = useState('product'); // 'product' | 'service'
  // ── Representative (2026-07-30) ──────────────────────────────────────────
  // The real person outbound email is signed by. Without it every email closes
  // "<Product> Team", which reads as bulk mail and leaves the prospect nobody
  // to reply to. Defaults to the logged-in user; `repIsMe` only drives the
  // radio — the name field stays editable either way.
  const [repIsMe, setRepIsMe] = useState(true);
  const [repName, setRepName] = useState('');
  const [repTitle, setRepTitle] = useState('');

  // Seed the name from the logged-in user once it arrives. Only while "You" is
  // selected and the field is untouched, so we never overwrite typing.
  useEffect(() => {
    if (repIsMe && !repName && user?.full_name) setRepName(user.full_name);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.full_name, repIsMe]);
  const [entityMenuOpen, setEntityMenuOpen] = useState(false);
  const [kbMenuOpen, setKbMenuOpen] = useState(false);
  const [entityConfidence, setEntityConfidence] = useState('medium');
  const [entityRationale, setEntityRationale] = useState('');
  const [regeneratingEntity, setRegeneratingEntity] = useState(false);

  // Refine state
  const [refineMsgs, setRefineMsgs] = useState([]);
  const [refineInput, setRefineInput] = useState('');
  const [isRefining, setIsRefining] = useState(false);
  // Which AI message is currently in "edit mode". null = everything is
  // read-only (the default). 'summary' = the initial AI-generated product
  // description. Otherwise the id of a pending refinement message. Gating
  // edits behind an explicit Edit button makes it obvious the text is
  // editable — previously the textarea looked identical to a static block.
  const [editingId, setEditingId] = useState(null);
  const msgIdRef = useRef(0);

  // Targeting state — variable names mirror the CANONICAL Apollo filter
  // keys used in the backend (see analyze_targeting.py and
  // discovery_apollo._icp_to_apollo_body). The state-variable naming
  // follows React's camelCase convention; the wire-format key sent to the
  // backend is the snake_case canonical name. The mapping is:
  //   targetLocations         -> person_locations
  //   targetIndustries        -> organization_industries
  //   targetRevenue           -> revenue_range
  //   targetRoles             -> person_titles
  //   (person_seniorities removed 2026-06-02 — titles imply seniority)
  //   (person_departments removed 2026-06-08 — titles imply department)
  //   targetTechnologies      -> buyer_technologies          (NEW 2026-05-28)
  const [targetLocations, setTargetLocations] = useState([]);
  const [targetIndustries, setTargetIndustries] = useState([]);
  // Revenue is an object { min, max, label, bands } or null (no filter) —
  // see the RevenueCombo helpers. NOT a plain band string anymore.
  const [targetRevenue, setTargetRevenue] = useState(null);
  const [targetRoles, setTargetRoles] = useState([]);
  const [targetTechnologies, setTargetTechnologies] = useState([]);
  // How many QUALIFIED leads this run should target (user-set via the stepper
  // next to "Launch AI Run"). Default 20, min 1, no max cap. Sent to /analyze
  // as `lead_count`. May briefly be '' while the user clears the field.
  const [leadCount, setLeadCount] = useState(20);
  // Per-field confidence metadata from /analyze/suggest-targeting `_meta`
  // (keyed by canonical filter name → {confidence, evidence, suppressed}).
  // Drives the AutofillBadge next to each field's label.
  const [targetMeta, setTargetMeta] = useState({});
  const [icpLoading, setIcpLoading] = useState(false);

  // KB
  const [knowledgeBase, setKnowledgeBase] = useState('');

  // KB file upload — accepts .pdf, .docx, .pptx.
  //
  // New flow (2026-05-26): files dropped on the URL step are NOT
  // processed immediately. We just hold the raw File objects in memory
  // until Launch. At Launch, the files get POSTed to /nexus/kb/upload
  // (which returns instantly — extraction + chunking + embedding +
  // Pinecone all happen in a backend background task) and a silent
  // polling effect watches each asset's status. When all are done,
  // a single "Knowledge base saved" toast appears.
  //
  // Shape of one item in kbFiles:
  //   {
  //     filename: string,
  //     file:     File | null,        // raw browser File ref (null after upload completes)
  //     status:   'pending' | 'queued' | 'processing' | 'indexed' | 'failed',
  //     asset_id: number | null,      // populated after /kb/upload returns
  //     error:    string | null,
  //   }
  const [kbFiles, setKbFiles] = useState([]);
  const [isDragOver, setIsDragOver] = useState(false);
  const kbInputRef = useRef(null);
  // Attach-menu state — when the "+" composer button is clicked we
  // show a dropdown of file-type categories (CSV / Excel / PDF /
  // Document / PPT). Picking one sets the native file picker's
  // `accept` attribute to that category's MIME list before opening
  // the picker, so the OS dialog filters to only the relevant
  // extensions. The dropdown closes on outside-click via the
  // attachMenuRef wired below.
  const [showAttachMenu, setShowAttachMenu] = useState(false);
  const attachMenuRef = useRef(null);
  // Tracks which category the user picked from the dropdown so
  // addKbFiles() can validate the selected file matches. A ref (not
  // state) because we read it inside the input's `onChange` handler
  // synchronously — state updates would lag a render. Cleared after
  // each picker close so drag-and-drop is never accidentally
  // category-restricted.
  const pendingAttachCategoryRef = useRef(null);

  // Close the attach menu when the user clicks outside it (mouse) or
  // hits Escape (keyboard). Mirrors the pattern already used by
  // MultiSelect higher up in this file.
  useEffect(() => {
    if (!showAttachMenu) return undefined;
    function handleClick(e) {
      if (attachMenuRef.current && !attachMenuRef.current.contains(e.target)) {
        setShowAttachMenu(false);
      }
    }
    function handleKey(e) {
      if (e.key === 'Escape') setShowAttachMenu(false);
    }
    document.addEventListener('mousedown', handleClick);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handleClick);
      document.removeEventListener('keydown', handleKey);
    };
  }, [showAttachMenu]);

  // Set to true once we've shown the "Knowledge base saved" toast, so the
  // polling effect doesn't toast again on subsequent re-renders.
  const [kbToastShown, setKbToastShown] = useState(false);

  // Launch result
  const [launchResult, setLaunchResult] = useState(null);

  // Workspace lookup — same pattern used by NexusDashboard / NexusAnalytics
  const [workspaceId, setWorkspaceId] = useState(null);

  // Refs
  const urlInputRef = useRef(null);
  const summaryTextareaRef = useRef(null);
  const scrapeTimerRef = useRef(null);
  const abortControllerRef = useRef(null);
  const messagesEndRef = useRef(null);

  // ── Effects ────────────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await authAxios.get('/nexus/me');
        if (cancelled) return;
        const data = res.data || {};
        setWorkspaceId(
          data.default_workspace_id ||
            (Array.isArray(data.workspaces) ? data.workspaces[0]?.id : null) ||
            null,
        );
      } catch {
        /* leave workspaceId null — launch will fail loudly */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authAxios]);

  useEffect(() => {
    if (step !== 'scraping') {
      clearInterval(scrapeTimerRef.current);
      return;
    }
    setScrapeStep(0);
    scrapeTimerRef.current = setInterval(() => {
      setScrapeStep((prev) => {
        if (prev >= SCRAPE_STEPS.length - 1) {
          clearInterval(scrapeTimerRef.current);
          return prev;
        }
        return prev + 1;
      });
    }, 2200);
    return () => clearInterval(scrapeTimerRef.current);
  }, [step]);

  // Resize the summary textarea ONLY when the step changes (i.e. when we
  // first enter the summary screen with content populated, or after a
  // programmatic content swap via setSummaryText). The onChange handler
  // already resizes on user typing — running this effect on every
  // keystroke duplicated the work and caused a perceptible page jump
  // because two height='auto' reflows landed back-to-back per character.
  useEffect(() => {
    if (step === 'summary') {
      autoResize(summaryTextareaRef.current);
    }
  }, [step]);

  useEffect(() => {
    if (refineMsgs.length > 2) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [refineMsgs]);

  useEffect(
    () => () => {
      abortControllerRef.current?.abort();
    },
    [],
  );

  // ── Step 1 → 2: scrape ─────────────────────────────────────────────────
  async function handleUrlSubmit(e) {
    e?.preventDefault?.();
    setError('');
    setInputMode('url');
    setSubmittedContent('');
    let finalUrl = url.trim();
    if (!finalUrl) {
      setError('Please enter your product URL');
      urlInputRef.current?.focus();
      return;
    }
    if (!/^https?:\/\//i.test(finalUrl)) finalUrl = `https://${finalUrl}`;
    try {
      new URL(finalUrl);
    } catch {
      setError('Enter a valid URL (e.g. https://yourproduct.com)');
      return;
    }

    // Reset downstream state for a fresh run
    setTargetLocations([]);
    setTargetIndustries([]);
    setTargetRevenue(null);
    setTargetRoles([]);
    setTargetTechnologies([]);
    setTargetMeta({});
    setScrapedContent('');
    setBrandColors([]);
    setKnowledgeBase('');
    setLaunchResult(null);

    abortControllerRef.current?.abort();
    abortControllerRef.current = new AbortController();

    setStep('scraping');
    try {
      const res = await authAxios.post(
        `${apiBase}/scrape-preview`,
        { product_url: finalUrl, entity_type: entityType },
        { signal: abortControllerRef.current.signal },
      );
      const {
        summary_text = '',
        product_name = '',
        favicon_url = '',
        brand_colors = [],
        raw_analysis = null,
        scraped_content = '',
      } = res.data || {};
      setScrapedContent(scraped_content || '');

      setUrl(finalUrl);
      setFaviconUrl(favicon_url || getDuckDuckGoFavicon(finalUrl));
      setProductName(product_name);
      setSummaryText(summary_text);
      setBrandColors(brand_colors);
      setRawAnalysis(raw_analysis);
      // Seed editable 3-section copy from Gemini's structured output.
      // The user edits this directly on the summary screen via the
      // per-section edit pencils.
      const _pd = raw_analysis?.product_description || null;
      if (_pd) {
        setEditableDescription({
          what_the_company_is: _pd.what_the_company_is || '',
          what_they_do:        _pd.what_they_do || '',
          who_they_serve:      _pd.who_they_serve || '',
          key_capabilities:    Array.isArray(_pd.key_capabilities) ? [..._pd.key_capabilities] : [],
          target_industries:   Array.isArray(_pd.target_industries) ? [..._pd.target_industries] : [],
          // target_geographies removed — not part of the schema anymore
          // (geography lives in the targeting step's ICP filter only).
        });
      } else {
        setEditableDescription(null);
      }

      // Backend echoes back the entity_type it actually used. The user
      // chose this on the URL step, so this should match — but keep the
      // round-trip in case of drift. confidence/rationale are still
      // useful for the tooltip in the pill.
      setEntityType(res.data?.entity_type === 'service' ? 'service' : 'product');
      setEntityConfidence(res.data?.entity_confidence || 'high');
      setEntityRationale(res.data?.entity_rationale || '');

      msgIdRef.current = 2;
      setRefineMsgs([
        { id: 1, role: 'user', type: 'url', content: finalUrl },
        { id: 2, role: 'ai', type: 'summary', content: summary_text },
      ]);
      setRefineInput('');
      setStep('summary');
    } catch (err) {
      if (err?.code === 'ERR_CANCELED' || err?.name === 'CanceledError') return;
      setError(
        err?.response?.data?.detail ||
          err?.message ||
          'Could not reach the website. Check the URL and try again.',
      );
      setStep('url');
    }
  }

  // Heuristic: is this string a website URL (vs pasted product text)? A URL has
  // no spaces and is either http(s):// or a bare domain (example.com / a.b.co.uk).
  function looksLikeUrl(s) {
    const t = (s || '').trim();
    if (!t || /\s/.test(t)) return false;
    if (/^https?:\/\//i.test(t)) return true;
    return /^[a-z0-9-]+(\.[a-z0-9-]+)+(\/.*)?$/i.test(t);
  }

  // ── Smart submit: one box. Detect URL vs pasted text vs uploaded file and
  // route — URL → scrape flow; text/file → content flow (skips description). ──
  function handleSmartSubmit(e) {
    e?.preventDefault?.();
    setError('');
    const boxVal = (url || '').trim();
    const fileText = (pastedContent || '').trim();   // set when a file was uploaded
    if (!boxVal && !fileText) {
      setError('Enter a website URL, paste your product details, or upload a file.');
      return;
    }
    // A file is attached, or the box holds free text (not a URL) → CONTENT flow.
    if (fileText || (boxVal && !looksLikeUrl(boxVal))) {
      const typed = boxVal && !looksLikeUrl(boxVal) ? boxVal : '';
      const combined = [fileText, typed].filter(Boolean).join('\n\n');
      return handleContentSubmit(combined);
    }
    // Otherwise the box is a URL → SCRAPE flow.
    return handleUrlSubmit(e);
  }

  // ── No-URL path: pasted/uploaded content → /scrape-preview (no scrape) →
  // /suggest-targeting → ICP filters, SKIPPING the product-description page. ──
  async function handleContentSubmit(contentArg) {
    setError('');
    const text = (typeof contentArg === 'string' ? contentArg : (pastedContent || '')).trim();
    if (!text) {
      setError('Paste your product details or upload a file first.');
      return;
    }
    setInputMode('content');
    setSubmittedContent(text);   // reused by the launch /analyze call
    // Reset downstream state for a fresh run
    setTargetLocations([]); setTargetIndustries([]); setTargetRevenue(null);
    setTargetRoles([]); setTargetTechnologies([]); setTargetMeta({});
    setScrapedContent(''); setBrandColors([]); setKnowledgeBase(''); setLaunchResult(null);

    abortControllerRef.current?.abort();
    abortControllerRef.current = new AbortController();

    setStep('scraping');
    try {
      // 1) scrape-preview with CONTENT (no scrape) → description + ICP triple.
      const res = await authAxios.post(
        `${apiBase}/scrape-preview`,
        {
          content: text,
          product_name: (productNameInput || '').trim() || undefined,
          entity_type: entityType,
        },
        { signal: abortControllerRef.current.signal },
      );
      const {
        summary_text = '',
        product_name = '',
        raw_analysis = null,
        scraped_content = '',
      } = res.data || {};
      const finalName = (productNameInput || '').trim() || product_name;
      setUrl('');
      setProductName(finalName);
      setSummaryText(summary_text);
      setScrapedContent(scraped_content || text);
      setRawAnalysis(raw_analysis);
      setEntityType(res.data?.entity_type === 'service' ? 'service' : 'product');

      // 2) suggest-targeting from the generated ICP, then land on the filters —
      //    skipping the product-description page. Use response values directly
      //    (the state setters above are async).
      setStep('targeting');
      setIcpLoading(true);
      try {
        const st = await authAxios.post(`${apiBase}/analyze/suggest-targeting`, {
          product_summary: summary_text,
          product_name: finalName,
          entity_type: res.data?.entity_type || entityType,
          url: '',                       // no website in content mode
          icp: raw_analysis?.icp || null,
          scraped_content: scraped_content || text,
        });
        const {
          person_locations, organization_industries, revenue_range,
          person_titles, buyer_technologies, _meta,
        } = st.data || {};
        if (_meta && typeof _meta === 'object') setTargetMeta(_meta);
        if (Array.isArray(person_locations) && person_locations.length) setTargetLocations(person_locations);
        if (Array.isArray(organization_industries) && organization_industries.length) setTargetIndustries(organization_industries);
        const rev = revenueFromStored(revenue_range);
        if (rev) setTargetRevenue(rev);
        if (Array.isArray(person_titles) && person_titles.length) setTargetRoles(person_titles);
        if (Array.isArray(buyer_technologies) && buyer_technologies.length) setTargetTechnologies(buyer_technologies);
      } catch {
        // Non-fatal — user can fill the filters manually.
      } finally {
        setIcpLoading(false);
      }
    } catch (err) {
      if (err?.code === 'ERR_CANCELED' || err?.name === 'CanceledError') return;
      setError(
        err?.response?.data?.detail || err?.message ||
          'Could not analyze the content. Try again.',
      );
      setStep('url');
    }
  }

  // Upload .pdf/.docx/.pptx → extract its text (via /kb/extract) and pass it
  // STRAIGHT to the model — the extracted text is NEVER shown in the UI; the
  // file flows directly into the content flow (→ ICP filters).
  async function handleContentFileUpload(file) {
    if (!file) return;
    setError('');
    setContentUploadBusy(true);
    try {
      const form = new FormData();
      form.append('files', file, file.name);
      const res = await authAxios.post(`${apiBase}/kb/extract`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      const extracted = (res.data?.combined_text || '').trim();
      if (!extracted) {
        setError('Could not read any text from that file.');
        return;
      }
      // Fold in any free text already typed in the box (ignore if it's a URL),
      // then pass the combined content straight to the model — no display.
      const typed = (url || '').trim();
      const combined = [extracted, typed && !looksLikeUrl(typed) ? typed : '']
        .filter(Boolean)
        .join('\n\n');
      setContentFileName(file.name);
      await handleContentSubmit(combined);
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to read the file.');
    } finally {
      setContentUploadBusy(false);
    }
  }

  // Toggle Product ↔ Service and ask the backend to re-render the
  // Product Description in the new mode. Cheap: no Gemini call, just
  // re-runs the prose composer on the cached raw_analysis. The user's
  // free-text edits are intentionally overwritten — the toggle's
  // purpose IS to rewrite the framing.
  async function handleToggleEntity() {
    if (regeneratingEntity) return;
    // Cycle: product -> service -> gcc -> product. 2026-05-28: GCC
    // (Global Capability Center) added as third entity type.
    const nextType =
      entityType === 'product'
        ? 'service'
        : entityType === 'service'
        ? 'gcc'
        : 'product';
    setEntityType(nextType);
    if (!rawAnalysis) return; // toggle still flips state; nothing to regenerate
    setRegeneratingEntity(true);
    try {
      const res = await authAxios.post(`${apiBase}/scrape-preview/rebuild`, {
        raw_analysis: rawAnalysis,
        product_name: productName,
        entity_type: nextType,
      });
      const next = res.data?.summary_text || '';
      if (next) {
        setSummaryText(next);
        // Programmatic content swap — resize on the next tick so the
        // textarea expands to fit the new (often-longer) refined copy.
        // The keystroke-driven onChange resize doesn't fire here.
        setTimeout(() => autoResize(summaryTextareaRef.current), 0);
        // Keep the chat thread in sync so the bubble shows the new text
        setRefineMsgs((prev) =>
          prev.map((m) =>
            m.role === 'ai' && m.type === 'summary' ? { ...m, content: next } : m,
          ),
        );
      }
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || 'Could not regenerate description.');
    } finally {
      setRegeneratingEntity(false);
    }
  }

  function handleCancelScrape() {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    clearInterval(scrapeTimerRef.current);
    setStep('url');
    setError('');
  }

  // ── Step 3: refine summary via chat ────────────────────────────────────
  async function handleRefineSubmit(e) {
    e?.preventDefault?.();
    const instruction = refineInput.trim();
    if (!instruction || isRefining) return;

    const userMsgId = ++msgIdRef.current;
    const aiMsgId = ++msgIdRef.current;
    setRefineMsgs((prev) => [
      ...prev,
      { id: userMsgId, role: 'user', type: 'instruction', content: instruction },
      {
        id: aiMsgId,
        role: 'ai',
        type: 'refined-summary',
        content: '',
        status: 'loading',
      },
    ]);
    setRefineInput('');
    setError('');
    setIsRefining(true);

    try {
      const res = await authAxios.post(`${apiBase}/refine-summary/preview`, {
        current_summary: summaryText,
        instruction,
      });
      const refined = res.data?.refined_summary || '';
      setRefineMsgs((prev) =>
        prev.map((m) =>
          m.id === aiMsgId ? { ...m, content: refined, status: 'pending' } : m,
        ),
      );
    } catch (err) {
      setRefineMsgs((prev) =>
        prev.map((m) => (m.id === aiMsgId ? { ...m, status: 'error' } : m)),
      );
      setError(
        err?.response?.data?.detail || err?.message || 'Refinement failed — try again.',
      );
    } finally {
      setIsRefining(false);
    }
  }

  function handleApproveRefinement(id, content) {
    setSummaryText(content);
    // Resize after the programmatic content swap so the textarea grows
    // to fit the applied refinement (no onChange fires for setState).
    setTimeout(() => autoResize(summaryTextareaRef.current), 0);
    setRefineMsgs((prev) =>
      prev.map((m) => (m.id === id ? { ...m, status: 'approved' } : m)),
    );
  }

  function handleDeclineRefinement(id) {
    setRefineMsgs((prev) =>
      prev.map((m) => (m.id === id ? { ...m, status: 'declined' } : m)),
    );
  }

  // ── Step 3 → 4: approve summary, fetch targeting ──────────────────────
  async function handleApproveSummary() {
    // If the user edited the structured 3-section view, serialize those
    // edits back into summaryText so the backend (which only knows about
    // product_summary, not the 3-section object) gets the latest content.
    // The legacy textarea path falls through unchanged.
    if (editableDescription) {
      const pd = editableDescription;
      const lines = [];
      if (pd.what_the_company_is?.trim()) {
        lines.push(pd.what_the_company_is.trim());
      }
      if (pd.what_they_do?.trim()) {
        lines.push(pd.what_they_do.trim());
      }
      // Clean the editable lists: the document editor keeps blank lines while
      // typing (so Enter adds a bullet), so trim + drop empties here.
      const caps = (pd.key_capabilities || []).map((c) => (c || '').trim()).filter(Boolean);
      if (caps.length) {
        // Dynamic header — products have Features, service+gcc have Services.
        lines.push(entityType === 'product' ? 'Features:' : 'Services:');
        caps.forEach((c) => lines.push(`- ${c}`));
      }
      if (pd.who_they_serve?.trim()) {
        lines.push(pd.who_they_serve.trim());
      }
      const inds = (pd.target_industries || []).map((c) => (c || '').trim()).filter(Boolean);
      if (inds.length) {
        lines.push('Industries:');
        inds.forEach((c) => lines.push(`- ${c}`));
      }
      // target_geographies intentionally NOT serialized — removed from
      // product description per user request. Geography is captured via
      // the ICP `person_locations` filter on the targeting step.
      const composed = lines.join('\n\n');
      if (composed.trim()) {
        setSummaryText(composed);
        // Update rawAnalysis.product_description so the launch payload carries
        // the user's edits — store the CLEANED version (trimmed, no empties).
        const cleanedPd = {
          ...pd,
          what_the_company_is: (pd.what_the_company_is || '').trim(),
          what_they_do: (pd.what_they_do || '').trim(),
          who_they_serve: (pd.who_they_serve || '').trim(),
          key_capabilities: caps,
          target_industries: inds,
        };
        setRawAnalysis((prev) =>
          prev ? { ...prev, product_description: cleanedPd } : prev,
        );
      }
    }
    if (!summaryText.trim() && !editableDescription) {
      setError('Please add a summary before continuing.');
      return;
    }
    setError('');
    // The industries the user kept/added in the product description are the
    // authoritative source for the ICP "Industries" filter — so adding
    // (e.g. "Telecom") or removing one there SYNCS to the filter on the next
    // step. `/suggest-targeting` only fills industries when the description
    // carried none (see below).
    const userInds = editableDescription
      ? (editableDescription.target_industries || [])
          .map((c) => (c || '').trim())
          .filter(Boolean)
      : [];
    // Seed immediately so the user's list shows even if suggest-targeting
    // is slow or fails.
    if (userInds.length) setTargetIndustries(userInds);
    setStep('targeting');
    setIcpLoading(true);
    try {
      const res = await authAxios.post(`${apiBase}/analyze/suggest-targeting`, {
        product_summary: summaryText,
        product_name: productName,
        entity_type: entityType,
        // 2026-06-01: `url` lets the backend run GROUNDED (Google Search)
        // deep research on the actual company. `icp` (scrape-preview's
        // raw_analysis.icp triples) is the page-analysis fallback if grounding
        // is unavailable. Either way the backend gates to HIGH confidence —
        // "better an empty field than a wrong one".
        url,
        icp: rawAnalysis?.icp || null,
        // Full scraped site text so the grounded research reads the REAL
        // page content (incl. customer/case-study pages), not just a summary.
        scraped_content: scrapedContent || null,
      });
      // 2026-05-28: suggest-targeting returns 7 canonical keys (one per
      // Apollo filter dimension) + a `_meta` map (2026-06-01) carrying
      // per-field {confidence, evidence, suppressed} for the AutofillBadge.
      // Names match the wire format used by analyze.py and discovery_apollo.py
      // — see the state block above for the mapping.
      const {
        person_locations,
        organization_industries,
        revenue_range,
        person_titles,
        buyer_technologies,
        _meta,
      } = res.data || {};
      if (_meta && typeof _meta === 'object') setTargetMeta(_meta);
      if (Array.isArray(person_locations) && person_locations.length)
        setTargetLocations(person_locations);
      // Industries: the user's product-description list wins (so their
      // add/delete edits are honoured). Only fall back to the grounded
      // suggestion when the description carried no industries at all.
      if (userInds.length) {
        setTargetIndustries(userInds);
      } else if (Array.isArray(organization_industries) && organization_industries.length) {
        setTargetIndustries(organization_industries);
      }
      // Gemini autofill returns a band-label string; convert to the object shape.
      const rev = revenueFromStored(revenue_range);
      if (rev) setTargetRevenue(rev);
      if (Array.isArray(person_titles) && person_titles.length)
        setTargetRoles(person_titles);
      // person_seniorities removed 2026-06-02, person_departments removed
      // 2026-06-08 — titles imply both.
      if (Array.isArray(buyer_technologies) && buyer_technologies.length)
        setTargetTechnologies(buyer_technologies);
    } catch {
      // Non-fatal — user can fill the fields manually.
    } finally {
      setIcpLoading(false);
    }
  }

  // ── KB indexing status polling ─────────────────────────────────────────
  //
  // After /kb/upload returns, each accepted file has an asset_id and
  // status='queued'. The backend processes them in parallel background
  // tasks (S3 + extract + chunk + embed + Pinecone). This effect polls
  // GET /kb/asset/{id}/status every 5 seconds for any still-in-flight
  // file. Once every file reaches a terminal state ('indexed' or
  // 'failed'), we show ONE "Knowledge base saved" toast — never per-file.
  //
  // The effect is keyed on the JSON of kbFiles so it re-runs cleanly
  // each time a status changes. The kbToastShown ref prevents the toast
  // from firing a second time on subsequent re-renders.
  useEffect(() => {
    const inFlight = kbFiles.filter(
      (kb) => kb.asset_id && (kb.status === 'queued' || kb.status === 'processing'),
    );

    // Nothing in flight: check whether the batch finished and we owe a toast.
    if (inFlight.length === 0) {
      const hasAnyTerminal = kbFiles.some(
        (kb) => kb.status === 'indexed' || kb.status === 'failed',
      );
      const allDone =
        hasAnyTerminal &&
        kbFiles.every(
          (kb) =>
            kb.status === 'indexed' ||
            kb.status === 'failed' ||
            kb.status === 'pending',  // pending = file was never uploaded (e.g. Launch errored)
        );
      if (allDone && !kbToastShown) {
        const indexedCount = kbFiles.filter((kb) => kb.status === 'indexed').length;
        const failedCount = kbFiles.filter((kb) => kb.status === 'failed').length;
        if (indexedCount > 0 || failedCount > 0) {
          const msg =
            failedCount > 0
              ? `Knowledge base saved (${failedCount} file${failedCount === 1 ? '' : 's'} failed)`
              : 'Knowledge base saved';
          if (setMessage) setMessage(msg);
          setKbToastShown(true);
        }
      }
      return;
    }

    let cancelled = false;
    const timerId = setInterval(async () => {
      if (cancelled) return;
      // Poll all in-flight assets in parallel; ignore errors per file
      // (the next tick will try again).
      const results = await Promise.all(
        inFlight.map(async (kb) => {
          try {
            const res = await authAxios.get(`${apiBase}/kb/asset/${kb.asset_id}/status`);
            return { asset_id: kb.asset_id, data: res.data };
          } catch {
            return null;
          }
        }),
      );
      if (cancelled) return;
      setKbFiles((prev) =>
        prev.map((kb) => {
          if (!kb.asset_id) return kb;
          const update = results.find((r) => r?.asset_id === kb.asset_id);
          if (!update) return kb;
          return {
            ...kb,
            status: update.data.status || kb.status,
            error: update.data.last_error || kb.error,
          };
        }),
      );
    }, 5000);

    return () => {
      cancelled = true;
      clearInterval(timerId);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(kbFiles.map((kb) => ({ id: kb.asset_id, s: kb.status }))), kbToastShown]);

  // ── KB upload helpers (PDF / DOCX / PPTX) ──────────────────────────────
  //
  // Drop = stash the raw File objects in state ONLY. No backend call
  // happens at drop time. The actual upload + background indexing is
  // kicked off later in `handleLaunch` after /analyze has created the
  // product.
  function addKbFiles(fileList) {
    const files = Array.from(fileList || []);
    // Always consume the pending-category ref so it never sticks
    // across calls (e.g. user picks "Excel" → cancels → drags a PDF
    // — the drag should NOT be category-restricted).
    const pendingCat = pendingAttachCategoryRef.current;
    pendingAttachCategoryRef.current = null;

    if (!files.length) return;

    const ALLOWED = ['.pdf', '.docx', '.pptx', '.xlsx', '.csv'];

    // Category-scoped validation: if the user opened the picker from
    // a specific dropdown item (e.g. "Excel" → expects .xlsx), reject
    // anything else with a clear error naming what was expected vs.
    // what they actually picked. Drops (no pendingCat) fall through
    // to the broader ALLOWED check.
    if (pendingCat) {
      const expectedExt = pendingCat.ext.toLowerCase();
      const mismatched = files.filter(
        (f) => !(f.name || '').toLowerCase().endsWith(expectedExt),
      );
      if (mismatched.length) {
        const gotExt = (mismatched[0].name.split('.').pop() || '').toLowerCase();
        setError(
          `You selected "${pendingCat.label}" but uploaded a .${gotExt} file. ` +
            `Please upload a ${expectedExt} file, or pick a different category from the + menu.`,
        );
        if (kbInputRef.current) kbInputRef.current.value = '';
        return;
      }
    }

    const validFiles = files.filter((f) => {
      const lower = (f.name || '').toLowerCase();
      return ALLOWED.some((ext) => lower.endsWith(ext));
    });
    if (!validFiles.length) {
      setError('Only PDF, DOCX, PPTX, XLSX, or CSV files are supported.');
      return;
    }

    setError('');
    setKbFiles((prev) => [
      ...prev,
      ...validFiles.map((f) => ({
        filename: f.name,
        file: f,
        status: 'pending',  // not yet uploaded — will become 'queued' after Launch
        asset_id: null,
        error: null,
      })),
    ]);
    if (kbInputRef.current) kbInputRef.current.value = '';
  }

  function handleKbInputChange(e) {
    addKbFiles(e.target.files);
  }
  function handleKbDragOver(e) {
    e.preventDefault();
    setIsDragOver(true);
  }
  function handleKbDragLeave(e) {
    if (!e.currentTarget.contains(e.relatedTarget)) setIsDragOver(false);
  }
  function handleKbDrop(e) {
    e.preventDefault();
    setIsDragOver(false);
    addKbFiles(e.dataTransfer.files);
  }
  function removeKbFile(filename) {
    setKbFiles((prev) => prev.filter((f) => f.filename !== filename));
  }

  // ── Step 5 → 6: launch ─────────────────────────────────────────────────
  async function handleLaunch(e) {
    e?.preventDefault?.();
    setError('');
    if (!workspaceId) {
      setError('Workspace not loaded yet. Please refresh and try again.');
      return;
    }
    if (!summaryText || !summaryText.trim()) {
      setError(
        'Product summary is empty — please generate or paste a description before launching.',
      );
      return;
    }
    // Targeting must not be entirely empty. An empty ICP filter pipes
    // through to Apollo as a wide-open search which returns either zero
    // leads or thousands of unscoped contacts. Block the Launch click
    // with a clear message instead.
    const hasAnyTarget =
      (targetLocations && targetLocations.length > 0) ||
      (targetIndustries && targetIndustries.length > 0) ||
      !!targetRevenue ||
      (targetRoles && targetRoles.length > 0) ||
      (targetTechnologies && targetTechnologies.length > 0);
    if (!hasAnyTarget) {
      setError(
        'Pick at least one targeting criterion before launching — otherwise discovery has nothing to filter on.',
      );
      return;
    }
    // Hard gate: without a representative every email signs "<Product> Team".
    // That is the whole point of this step, so it blocks the launch rather
    // than warning and letting a faceless campaign go out.
    if (!repName.trim() || !repTitle.trim()) {
      setError(
        'Add the representative name and role — outbound email is signed by a real person, not the product team.',
      );
      return;
    }
    setStep('launching');
    // CRITICAL: clear any prior launch result BEFORE firing the new request.
    // Otherwise the success screen briefly renders with the previous campaign's
    // id, and the outbound-emails poll picks up an OLD campaign's touchpoints,
    // which looks to the operator like leftover/stale emails appearing in a
    // brand-new launch. Reset → success screen waits for the fresh response.
    setLaunchResult(null);

    // Build targeting block; gets fed back into the analyze prompt as
    // user-provided description so Gemini's re-ICP run respects it.
    const tgtLines = [];
    if (targetLocations.length) tgtLines.push(`Locations: ${targetLocations.join(', ')}`);
    if (targetIndustries.length) tgtLines.push(`Industries: ${targetIndustries.join(', ')}`);
    if (targetRevenue) tgtLines.push(`Company Revenue: ${targetRevenue.label}`);
    if (targetRoles.length) tgtLines.push(`Target Roles: ${targetRoles.join(', ')}`);
    if (targetTechnologies.length)
      tgtLines.push(`Technologies: ${targetTechnologies.join(', ')}`);

    // The product description already includes everything (name, value
    // prop, benefits, pricing) as natural prose — backend's
    // _build_summary_text composes it that way. We only append the
    // targeting block since that's structured ICP filter data.
    const finalDescription = tgtLines.length
      ? `${summaryText.trim()}\n\n---\nTargeting Criteria\n${tgtLines.join('\n')}`
      : summaryText.trim();

    try {
      const res = await authAxios.post(`${apiBase}/analyze`, {
        // Content mode (no website): send `content` + `product_name`, url null.
        // URL mode: send `url`. The backend requires exactly one.
        url: inputMode === 'content' ? null : url,
        content: inputMode === 'content' ? (submittedContent || null) : null,
        product_name:
          inputMode === 'content'
            ? ((productNameInput || '').trim() || productName || null)
            : null,
        workspace_id: workspaceId,
        product_description: finalDescription,
        // URL-only analysis from now on. File knowledge is shipped
        // separately via /kb/upload (background indexing). The optional
        // pasted-text knowledgeBase still goes through here so users who
        // never dropped a file but typed notes in the textarea aren't
        // broken.
        knowledge_base: knowledgeBase.trim() || null,
        // Persist the user's Product/Service/GCC choice into
        // product.icp.entity_type so GTM Journey can split its filter
        // rows by type. Coerced to the exact three allowed values —
        // defends against any future state-bleed (e.g. accidental
        // capitalization) breaking the backend filter.
        entity_type:
          entityType === 'service'
            ? 'service'
            : entityType === 'gcc'
            ? 'gcc'
            : 'product',
        // Signs every outbound email for this product. Stored in
        // product.icp['brand'], which the sender context already reads.
        rep_name: repName.trim(),
        rep_title: repTitle.trim(),
        // ── Canonical Apollo filter overrides (2026-05-28) ────────────
        // The user's chip-row edits on the targeting page WIN over the
        // Gemini-extracted defaults. We send all 7 dimensions every
        // time — the backend treats missing/null as "no override".
        person_titles: targetRoles,
        person_locations: targetLocations,
        // person_states briefly existed (2026-06-02) — reverted same
        // day. State granularity now travels inside person_locations
        // values themselves (e.g. "Texas, United States").
        // person_seniorities REMOVED 2026-06-02, person_departments
        // REMOVED 2026-06-08 (backend dropped both too).
        organization_industries: targetIndustries,
        // Revenue windows: one {min,max} object (single range / custom / merged
        // adjacent bands) OR an array of them (non-adjacent bands). The backend
        // runs one Apollo search per window and unions the leads into this
        // campaign (see discover_for_campaign + revenue_ranges_from_icp).
        // Capped at 3 windows (the 6 bands can form at most 3 non-adjacent
        // groups anyway; the slice is a defensive guard, matched on the backend).
        revenue_range: targetRevenue
          ? targetRevenue.ranges.length === 1
            ? targetRevenue.ranges[0]
            : targetRevenue.ranges.slice(0, 3)
          : null,
        buyer_technologies: targetTechnologies,
        // How many QUALIFIED leads to target this run (user-chosen stepper).
        // Fall back to 20 if the field was left empty/invalid.
        lead_count: Number(leadCount) >= 1 ? Number(leadCount) : 20,
      });
      setLaunchResult(res.data || {});
      setStep('launched');
      // Toast: surface the QUALIFIED-lead count + any shortfall reason here
      // (the inline banner was removed). leads_attached is the QUALIFIED
      // count under B-mode. When discovery is still running we say so rather
      // than claiming "0 found".
      if (setMessage) {
        const d = res.data?.discovery;
        const got = d?.leads_attached || 0;
        const req = d?.requested || 0;
        const reason = d?.reason;
        // "Already in campaign" rows this run surfaced (2026-06-11) — when
        // the pool is mined, the table still shows these, so the toast must
        // never claim "0 leads found".
        const dups = d?.duplicates || 0;
        let msg;
        if (!d || d.status !== 'completed') {
          msg = 'Campaign launched · discovery is running…';
        } else if (reason === 'ok' || (got >= req && req > 0)) {
          msg = `Campaign launched · ${got} qualified lead${got === 1 ? '' : 's'} found`;
        } else if (reason === 'out_of_credits' || d.out_of_credits) {
          msg = `Found ${got} lead${got === 1 ? '' : 's'}. You are out of credits, so only these were delivered. Buy more credits to find additional leads.`;
        } else if (reason === 'credits' || d.credit_error) {
          msg = `Found ${got} qualified lead${got === 1 ? '' : 's'} — Apollo credits are exhausted. Top up, then re-run to find more.`;
        } else if (got === 0 && dups > 0) {
          msg = `${dups} matching lead${dups === 1 ? '' : 's'} found — all already contacted in earlier campaigns (shown in the table). Broaden the filters to reach new people.`;
        } else if (reason === 'no_matches' || got === 0) {
          msg = 'No qualified leads found for these filters. Try broadening the location, industry, revenue, or roles.';
        } else {
          msg = `Found ${got} of ${req} qualified leads${
            dups > 0 ? ` (+${dups} already in campaign)` : ''
          }. Apollo has no more matches for these filters — broaden them to find more.`;
        }
        setMessage(msg);
      }

      // Warn (but don't block) if this product has no connected mailbox yet —
      // leads are found + emails are written, but sending HOLDS until a
      // mailbox is connected in the Connectors tab.
      try {
        const et =
          entityType === 'service' ? 'service' : entityType === 'gcc' ? 'gcc' : 'product';
        const ms = await authAxios.get(`${apiBase}/connectors/brand-status`, {
          params: { url, entity_type: et },
        });
        if (ms.data && ms.data.has_mailbox === false && setMessage) {
          setMessage(
            '⚠️ No mailbox connected for this product — leads are found, but emails will send only after you connect a mailbox in the Connectors tab.',
          );
        }
      } catch {
        /* non-fatal — the warning is best-effort */
      }

      // ── Fire-and-forget: upload dropped files to /kb/upload ─────────
      // /analyze just returned the new product_id. Files were held in
      // memory until now; ship them to the background indexer. We don't
      // await this — the user is already moving into the launched view.
      // The polling effect (further down) watches each asset_id's status
      // and shows a single "Knowledge base saved" toast once all done.
      const productIdFromAnalyze = res.data?.product?.id || res.data?.product_id;
      const filesToUpload = kbFiles.filter((kb) => kb.file && kb.status === 'pending');
      if (productIdFromAnalyze && filesToUpload.length > 0) {
        const form = new FormData();
        form.append('product_id', String(productIdFromAnalyze));
        filesToUpload.forEach((kb) => form.append('files', kb.file, kb.filename));

        // Mark them as 'uploading' so the polling effect doesn't fire
        // before the asset_ids come back.
        setKbFiles((prev) =>
          prev.map((kb) => (kb.status === 'pending' ? { ...kb, status: 'uploading' } : kb)),
        );

        authAxios
          .post(`${apiBase}/kb/upload`, form, {
            headers: { 'Content-Type': 'multipart/form-data' },
          })
          .then((uploadRes) => {
            const items = uploadRes.data?.items || [];
            // Match each returned item to its kbFiles entry by filename.
            // The backend preserves order, but matching by name is more
            // robust against any reordering.
            setKbFiles((prev) =>
              prev.map((kb) => {
                if (kb.status !== 'uploading') return kb;
                const match = items.find((it) => it.filename === kb.filename);
                if (!match) return kb;
                return {
                  ...kb,
                  file: null,                 // raw bytes no longer needed
                  asset_id: match.asset?.id || null,
                  status: match.accepted ? 'queued' : 'failed',
                  error: match.error || null,
                };
              }),
            );
          })
          .catch((err) => {
            // Upload itself failed (network, 5xx, etc.). Mark the
            // uploading rows as failed so the toast logic can surface it.
            const reason =
              err?.response?.data?.detail ||
              err?.message ||
              'File upload to backend failed.';
            setKbFiles((prev) =>
              prev.map((kb) =>
                kb.status === 'uploading'
                  ? { ...kb, status: 'failed', error: reason, file: null }
                  : kb,
              ),
            );
          });
      }
    } catch (err) {
      setError(
        err?.response?.data?.detail || err?.message || 'Server error — is the backend running?',
      );
      // KB step is now part of the URL step — land on targeting so the
      // operator can adjust filters and re-launch.
      setStep('targeting');
    }
  }

  // ── Derived ────────────────────────────────────────────────────────────
  const liveFavicon = getDuckDuckGoFavicon(url);
  const hasPending = refineMsgs.some((m) => m.status === 'pending');

  // ── Render ─────────────────────────────────────────────────────────────
  // Read-only Users may view GTM but not create campaigns — show a view-only
  // notice instead of the creation wizard.
  if (isReadOnly(user)) {
    return (
      <div className="bg-white min-h-full">
        <div className="max-w-4xl mx-auto px-8 py-20 text-center">
          <p className="text-base font-bold text-[#2B2926]">View-only access</p>
          <p className="text-sm text-[#2B2926]/60 mt-2">
            Creating campaigns is available to Admins and the Master Admin.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white min-h-full">
      {/* Header / "Start over" bar removed entirely per user request — the
          wizard steps orient the user well enough on their own. */}

      <div
        className={[
          'mx-auto py-8',
          // The launched step shows a wide 8-column leads table — give it
          // much more room so it fills the page (no left/right scroll),
          // while the other steps stay in the comfortable reading column.
          step === 'launched'
            ? 'max-w-[1400px] px-4 md:px-8'
            : 'max-w-5xl px-5 lg:px-10',
        ].join(' ')}
      >
        {/* ── url step — Soft Cards composer layout ─────────────────── */}
        {step === 'url' && (
          <div className="relative" style={{ animation: 'fadeIn .3s ease', padding: '40px 0 60px' }}>
            <style>{`@keyframes fadeIn{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}`}</style>

            {/* Floating value-prop pill */}
            <div
              style={{
                position: 'absolute',
                left: 0,
                right: 0,
                top: 8,
                display: 'flex',
                justifyContent: 'center',
                pointerEvents: 'none',
              }}
            >
              <div
                className="inline-flex items-center gap-2.5"
                style={{
                  background: '#fff',
                  border: '1px solid #e7eaee',
                  borderRadius: 999,
                  boxShadow: '0 2px 4px rgba(18,20,24,0.04), 0 18px 44px -22px rgba(18,20,24,0.22)',
                  padding: '8px 16px 8px 11px',
                }}
              >
                <span
                  style={{
                    width: 24,
                    height: 24,
                    borderRadius: '50%',
                    background: '#fff3ee',
                    display: 'grid',
                    placeItems: 'center',
                    color: '#F55600',
                    flex: '0 0 auto',
                  }}
                >
                  <Sparkles className="w-3.5 h-3.5" />
                </span>
                <span style={{ fontSize: 13.5, fontWeight: 600, color: '#3a3e46', whiteSpace: 'nowrap' }}>
                  We'll find the right leads for you
                </span>
              </div>
            </div>

            {/* Extra top padding pushes the headline + composer lower on
                the screen (subtitle + caption rows removed per request). */}
            <div className="relative mx-auto" style={{ maxWidth: 720, paddingTop: 'clamp(56px, 14vw, 130px)' }}>
              <h1
                style={{
                  fontWeight: 800,
                  fontSize: 'clamp(24px, 6vw, 35px)',
                  lineHeight: 1.1,
                  textAlign: 'center',
                  margin: '0 0 26px',
                  letterSpacing: '-0.025em',
                  color: '#15171c',
                }}
              >
                {entityType === 'service'
                  ? 'What service do you offer?'
                  : entityType === 'gcc'
                  ? 'What does your GCC firm offer?'
                  : "What's your product?"}
              </h1>
              {/* Extra breathing room between the headline and composer. */}
              <div style={{ height: 24 }} />

              {/* One smart composer: paste a website URL, paste product text, or
                  upload a file (+). handleSmartSubmit auto-detects which. */}
              <form onSubmit={handleSmartSubmit}>
                <div
                  className="relative mx-auto"
                  style={{
                    maxWidth: 600,
                    margin: '0 auto 12px',
                    background: '#fff',
                    borderRadius: 24,
                    boxShadow: '0 0 0 1.5px #F55600, 0 2px 10px rgba(18,20,24,0.06)',
                    padding: 6,
                  }}
                >
                  <textarea
                    ref={urlInputRef}
                    rows={1}
                    style={{
                      width: '100%',
                      boxSizing: 'border-box',
                      border: 'none',
                      outline: 'none',
                      background: 'transparent',
                      fontSize: 16,
                      color: '#15171c',
                      padding: '16px 14px 8px',
                      resize: 'none',
                      lineHeight: 1.5,
                      maxHeight: 220,        // grows with content, then scrolls
                      overflowY: 'auto',
                      fontFamily: 'inherit',
                    }}
                    placeholder="Website URL or product details…"
                    value={url}
                    onChange={(e) => {
                      setUrl(e.target.value);
                      setError('');
                      // Auto-grow up to maxHeight, then scroll for long pastes.
                      e.target.style.height = 'auto';
                      e.target.style.height = Math.min(e.target.scrollHeight, 220) + 'px';
                    }}
                    onKeyDown={(e) => {
                      // Enter submits (matches the URL box); Shift+Enter = newline.
                      if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault();
                        handleSmartSubmit(e);
                      }
                    }}
                    autoFocus
                  />
                  <div className="flex items-center gap-2" style={{ padding: '4px 6px 6px' }}>
                    {/* + button — opens a file-type menu (PDF / DOCX /
                        PPTX / CSV). Picking any opens the OS file picker
                        scoped to that type via the hidden kbInputRef. */}
                    <div style={{ position: 'relative' }}>
                      <button
                        type="button"
                        onClick={() => setKbMenuOpen((v) => !v)}
                        title={`Add ${entityType === 'service' ? 'service' : 'product'} knowledge`}
                        style={{
                          width: 40,
                          height: 40,
                          borderRadius: 12,
                          border: 'none',
                          background: kbMenuOpen ? '#F55600' : '#2B2926',
                          color: '#fff',
                          display: 'grid',
                          placeItems: 'center',
                          cursor: 'pointer',
                          flex: '0 0 auto',
                          boxShadow: kbMenuOpen
                            ? '0 4px 12px rgba(245,86,0,0.30)'
                            : '0 2px 6px rgba(43,41,38,0.20)',
                          transition: 'all 0.2s ease',
                          fontWeight: 700,
                        }}
                      >
                        <Plus className="w-[18px] h-[18px]" />
                      </button>
                      {kbMenuOpen && (
                        <>
                          <div
                            onClick={() => setKbMenuOpen(false)}
                            style={{ position: 'fixed', inset: 0, zIndex: 90 }}
                          />
                          <div
                            style={{
                              position: 'absolute',
                              top: 'calc(100% + 8px)',
                              left: 0,
                              zIndex: 100,
                              background: '#fff',
                              border: '1px solid #e7eaee',
                              borderRadius: 14,
                              boxShadow: '0 12px 28px rgba(18,20,24,0.16)',
                              padding: 4,
                              minWidth: 200,
                            }}
                          >
                            <div
                              style={{
                                fontSize: 10,
                                fontWeight: 700,
                                letterSpacing: '0.06em',
                                textTransform: 'uppercase',
                                color: '#9aa0ab',
                                padding: '6px 10px 4px',
                              }}
                            >
                              Add {entityType === 'service' ? 'service' : 'product'} knowledge
                            </div>
                            {[
                              { ext: '.pdf',  label: 'PDF document' },
                              { ext: '.docx', label: 'Word (DOCX)' },
                              { ext: '.pptx', label: 'PowerPoint (PPTX)' },
                              { ext: '.csv',  label: 'CSV / Excel' },
                            ].map((opt) => (
                              <button
                                key={opt.ext}
                                type="button"
                                onClick={() => {
                                  setKbMenuOpen(false);
                                  if (kbInputRef.current) {
                                    kbInputRef.current.setAttribute('accept', opt.ext);
                                    kbInputRef.current.click();
                                  }
                                }}
                                className="flex items-center gap-2.5 w-full text-left"
                                style={{
                                  padding: '8px 10px',
                                  borderRadius: 9,
                                  fontSize: 13,
                                  fontWeight: 600,
                                  color: '#15171c',
                                  background: '#fff',
                                  border: 'none',
                                  cursor: 'pointer',
                                }}
                                onMouseEnter={(e) => (e.currentTarget.style.background = '#f4f6f7')}
                                onMouseLeave={(e) => (e.currentTarget.style.background = '#fff')}
                              >
                                <FileText className="w-4 h-4" style={{ color: '#F55600' }} />
                                {opt.label}
                              </button>
                            ))}
                            {/* "Any file…" option removed per request. */}
                          </div>
                        </>
                      )}
                    </div>

                    {/* Product / Service dropdown */}
                    <div style={{ position: 'relative' }}>
                      <button
                        type="button"
                        onClick={() => setEntityMenuOpen((v) => !v)}
                        className="inline-flex items-center gap-2"
                        style={{
                          height: 40,
                          padding: '0 14px',
                          borderRadius: 12,
                          border: '1.5px solid rgba(43,41,38,0.30)',
                          background: '#fff',
                          fontWeight: 700,
                          fontSize: 13.5,
                          cursor: 'pointer',
                          color: '#2B2926',
                          boxShadow: '0 2px 6px rgba(43,41,38,0.06)',
                          transition: 'all 0.2s ease',
                        }}
                      >
                        <span style={{ color: '#F55600', display: 'grid', placeItems: 'center', width: 16, height: 16 }}>
                          {entityType === 'service' ? (
                            <Edit3 className="w-4 h-4" />
                          ) : (
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" width="16" height="16">
                              <path d="M21 8 12 3 3 8v8l9 5 9-5z" />
                              <path d="m3 8 9 5 9-5M12 13v8" />
                            </svg>
                          )}
                        </span>
                        {entityType === 'service' ? 'Service' : entityType === 'gcc' ? 'GCC' : 'Product'}
                        <ChevronDown className="w-3.5 h-3.5" style={{ color: '#9aa0ab' }} />
                      </button>
                      {entityMenuOpen && (
                        <>
                          <div
                            onClick={() => setEntityMenuOpen(false)}
                            style={{ position: 'fixed', inset: 0, zIndex: 90 }}
                          />
                          <div
                            style={{
                              position: 'absolute',
                              top: 'calc(100% + 6px)',
                              left: 0,
                              zIndex: 100,
                              background: '#fff',
                              border: '1px solid #e7eaee',
                              borderRadius: 14,
                              boxShadow: '0 12px 28px rgba(18,20,24,0.14)',
                              padding: 4,
                              minWidth: 160,
                            }}
                          >
                            {[
                              { v: 'product', label: 'Product' },
                              { v: 'service', label: 'Service' },
                              { v: 'gcc', label: 'GCC' },
                            ].map((opt) => {
                              const sel = entityType === opt.v;
                              return (
                                <button
                                  key={opt.v}
                                  type="button"
                                  onClick={() => {
                                    setEntityType(opt.v);
                                    setEntityMenuOpen(false);
                                  }}
                                  className="flex items-center gap-2 w-full text-left"
                                  style={{
                                    padding: '8px 12px',
                                    borderRadius: 10,
                                    fontSize: 13.5,
                                    fontWeight: 600,
                                    background: sel ? '#fff3ee' : '#fff',
                                    color: sel ? '#F55600' : '#15171c',
                                    border: 'none',
                                    cursor: 'pointer',
                                  }}
                                  onMouseEnter={(e) => {
                                    if (!sel) e.currentTarget.style.background = '#f4f6f7';
                                  }}
                                  onMouseLeave={(e) => {
                                    if (!sel) e.currentTarget.style.background = '#fff';
                                  }}
                                >
                                  {opt.label}
                                </button>
                              );
                            })}
                          </div>
                        </>
                      )}
                    </div>

                    <span style={{ flex: 1 }} />

                    {/* Live favicon removed per request. */}

                    {/* Send */}
                    <button
                      type="submit"
                      style={{
                        width: 38,
                        height: 38,
                        borderRadius: 14,
                        background: '#F55600',
                        color: '#fff',
                        border: 'none',
                        display: 'grid',
                        placeItems: 'center',
                        cursor: 'pointer',
                        flex: '0 0 auto',
                      }}
                      aria-label="Analyse URL"
                    >
                      <Send className="w-[17px] h-[17px]" />
                    </button>
                  </div>
                </div>

                {error && (
                  <p style={{ textAlign: 'center', marginTop: 10, fontSize: 12.5, fontWeight: 700, color: '#F55600' }}>
                    {error}
                  </p>
                )}
              </form>

              {/* Extraction-in-progress indicator. The extracted text is passed
                  STRAIGHT to the model — it's never displayed. */}
              {contentUploadBusy && (
                <div className="flex items-center justify-center" style={{ marginTop: 10 }}>
                  <span
                    className="inline-flex items-center gap-2"
                    style={{
                      padding: '6px 12px', borderRadius: 999, fontSize: 12.5, fontWeight: 600,
                      background: '#fff3ee', color: '#F55600', border: '1px solid #ffd9c4',
                    }}
                  >
                    <FileText className="w-3.5 h-3.5" />
                    Reading {contentFileName || 'file'}…
                  </span>
                </div>
              )}

              {/* Hidden file input — opened by the + menu options below.
                  Caption rows ("Product — …", "Takes ~15s", "Add product
                  knowledge … browse") removed per request. Drag-and-drop
                  still works anywhere over the composer area. */}
              <input
                ref={kbInputRef}
                type="file"
                accept=".pdf,.docx,.pptx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.presentationml.presentation"
                style={{ display: 'none' }}
                onChange={(e) => {
                  // The + uploads a product doc AS CONTENT — extract its text so
                  // the smart box treats it like pasted content (no website).
                  const f = e.target.files?.[0];
                  e.target.value = '';
                  if (f) handleContentFileUpload(f);
                }}
              />

              {kbFiles.length > 0 && kbFiles.every((f) => f.status === 'pending') && (
                <div
                  className="flex items-center justify-center gap-2 mx-auto"
                  style={{ marginTop: 10, fontSize: 12.5, color: '#15171c' }}
                >
                  <FileText className="w-3.5 h-3.5" style={{ color: '#9aa0ab' }} />
                  <span style={{ fontWeight: 700 }}>
                    {kbFiles.length} file{kbFiles.length === 1 ? '' : 's'} attached
                  </span>
                  <button
                    type="button"
                    onClick={() => setKbFiles([])}
                    style={{ marginLeft: 4, textDecoration: 'underline', color: '#9aa0ab', background: 'none', border: 'none', cursor: 'pointer' }}
                  >
                    Clear
                  </button>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── scraping step — Soft Cards modal + circular % ring ──────── */}
        {step === 'scraping' && (
          <div
            className="relative"
            style={{
              minHeight: '70vh',
              display: 'grid',
              placeItems: 'center',
              padding: '40px 20px',
              background: 'linear-gradient(180deg,#fbfbfc,#f6f7f9)',
              borderRadius: 20,
              animation: 'fadeIn .3s ease',
            }}
          >
            <div
              style={{
                position: 'relative',
                width: '100%',
                maxWidth: 460,
                background: '#fff',
                borderRadius: 24,
                boxShadow: '0 30px 70px -28px rgba(18,20,24,0.32), 0 2px 6px rgba(18,20,24,0.05)',
                padding: '34px 30px 26px',
                textAlign: 'center',
              }}
            >
              {/* Circular progress ring (replaces the book icon) */}
              {(() => {
                const total = SCRAPE_STEPS.length;
                const pct = Math.min(
                  99,
                  Math.max(8, Math.round(((scrapeStep + 1) / total) * 100)),
                );
                const r = 26;
                const c = 2 * Math.PI * r;
                const offset = c - (pct / 100) * c;
                return (
                  <div style={{ position: 'relative', width: 64, height: 64, margin: '0 auto 24px' }}>
                    <svg width="64" height="64" viewBox="0 0 64 64" style={{ transform: 'rotate(-90deg)' }}>
                      <circle cx="32" cy="32" r={r} fill="none" stroke="#eef1f4" strokeWidth="5" />
                      <circle
                        cx="32"
                        cy="32"
                        r={r}
                        fill="none"
                        stroke="#F55600"
                        strokeWidth="5"
                        strokeLinecap="round"
                        strokeDasharray={c}
                        strokeDashoffset={offset}
                        style={{ transition: 'stroke-dashoffset 0.6s ease' }}
                      />
                    </svg>
                    <div
                      style={{
                        position: 'absolute',
                        inset: 0,
                        display: 'grid',
                        placeItems: 'center',
                        fontSize: 14,
                        fontWeight: 800,
                        color: '#15140F',
                      }}
                    >
                      {pct}%
                    </div>
                  </div>
                );
              })()}

              {/* Step list — website steps for URL mode, content steps for the
                  paste/upload (no-website) mode. */}
              <div className="flex flex-col" style={{ gap: 3 }}>
                {(inputMode === 'content' ? CONTENT_STEPS : SCRAPE_STEPS).map((s, i) => {
                  const done = i < scrapeStep;
                  const active = i === scrapeStep;
                  const baseStyle = {
                    display: 'flex',
                    alignItems: 'center',
                    gap: 13,
                    padding: '13px 16px',
                    borderRadius: 14,
                    textAlign: 'left',
                    fontSize: 15,
                  };
                  const activeStyle = active
                    ? { background: '#fff3ee', color: '#15171c', fontWeight: 700 }
                    : done
                    ? { color: '#646a76', fontWeight: 500 }
                    : { color: '#9aa0ab', fontWeight: 500 };
                  return (
                    <div key={i} style={{ ...baseStyle, ...activeStyle }}>
                      <span style={{ width: 18, height: 18, flex: '0 0 auto', display: 'grid', placeItems: 'center', color: done ? '#1f9d57' : active ? '#F55600' : 'transparent' }}>
                        {done ? (
                          <CheckCircle2 className="w-[18px] h-[18px]" />
                        ) : active ? (
                          <span
                            style={{
                              width: 16,
                              height: 16,
                              border: '2px solid #F55600',
                              borderTopColor: 'transparent',
                              borderRadius: '50%',
                              animation: 'newrunspin .8s linear infinite',
                            }}
                          />
                        ) : (
                          <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#e7eaee' }} />
                        )}
                      </span>
                      <span>{s.label}</span>
                    </div>
                  );
                })}
              </div>

              {/* Footer */}
              <div
                className="flex items-center justify-between"
                style={{ marginTop: 22, paddingTop: 18, borderTop: '1px solid #eef1f4' }}
              >
                <span style={{ color: '#15140F', fontSize: 12.5, fontWeight: 600 }} className="truncate max-w-[60%]">
                  {url || 'pipelyt.ai'}
                </span>
                <button
                  type="button"
                  onClick={handleCancelScrape}
                  style={{ background: 'none', border: 'none', color: '#15171c', fontWeight: 700, fontSize: 14, cursor: 'pointer' }}
                >
                  Cancel
                </button>
              </div>
            </div>
            <style>{`@keyframes newrunspin{to{transform:rotate(360deg)}}`}</style>
          </div>
        )}

        {/* ── summary step ─────────────────────────────────────────── */}
        {step === 'summary' && (
          <div className="animate-in fade-in duration-300">
            {/* Outer list no longer scrolls — the scrollbar now lives
                INSIDE the summary card (see the card div below) so it
                stays within the box's rounded border instead of running
                past the bottom edge. */}
            <div className="space-y-3 mb-4">
              {refineMsgs.map((msg) => {
                if (msg.role === 'user' && msg.type === 'url') {
                  // The URL pill now lives inline on the right of the AI
                  // summary header (see below), so the standalone bubble
                  // is suppressed to keep them on one line.
                  return null;
                }
                if (msg.role === 'user' && msg.type === 'instruction') {
                  return (
                    <div key={msg.id} className="flex justify-end">
                      <div className="px-3 py-2 rounded-2xl bg-[#2B2926] text-white text-sm max-w-lg">
                        {msg.content}
                      </div>
                    </div>
                  );
                }
                if (msg.role === 'ai') {
                  const isInitial = msg.type === 'summary';
                  const isLoading = msg.status === 'loading';
                  const isPending = msg.status === 'pending';
                  const isApproved = msg.status === 'approved';
                  const isDeclined = msg.status === 'declined';
                  const isError = msg.status === 'error';

                  return (
                    <div key={msg.id} className="min-w-0">
                      {/* Sparkles avatar removed per request. Scroll lives
                          inside this card with a stable gutter so the thin
                          scrollbar sits cleanly inside the rounded border
                          instead of being clipped by the corner. */}
                      <div
                        className="min-w-0 rounded-2xl border border-[#2B2926]/10 px-4 py-3 bg-white flex flex-col"
                        style={isInitial ? { height: '64vh' } : undefined}
                      >
                        {isInitial && (
                          <div className="shrink-0 flex flex-wrap items-center gap-1.5 pb-2 mb-2 -mx-4 px-4 border-b border-[#2B2926]/10">
                            <FaviconImg src={faviconUrl} url={url} size={15} />
                            <span className="text-[12px] font-black text-[#2B2926] truncate min-w-0 max-w-full">
                              {productName || 'Your Product'}
                            </span>
                            <span className="text-[8.5px] font-bold uppercase tracking-wider text-[#F55600] bg-[#F55600]/10 px-1.5 py-px rounded whitespace-nowrap shrink-0">
                              AI-Generated
                            </span>
                            {/* URL pill — right-aligned on the same line as
                                the product name on wider screens; wraps to
                                its own full-width row on mobile so the
                                product name stays fully visible. */}
                            <span className="ml-auto inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-[#F55600] text-white text-[12px] font-bold max-w-full sm:max-w-[55%]">
                              <span className="truncate">{url}</span>
                            </span>
                            {/*
                              Entity-type toggle button removed per user
                              request — the entity type is already chosen
                              on the URL step (Product / Service / GCC
                              chooser), so showing it again here as a
                              clickable chip is redundant. The underlying
                              `entityType` state + handleToggleEntity
                              function are kept in place in case we
                              re-surface this later (e.g. on the targeting
                              step) — no functional change.
                            */}
                          </div>
                        )}

                        {/* Scroll area — only this scrolls; the header above
                            stays fixed at the top of the card. */}
                        <div
                          className="min-w-0 flex-1 overflow-y-auto"
                          style={isInitial ? { scrollbarWidth: 'thin', scrollbarGutter: 'stable' } : undefined}
                        >
                        {isLoading && (
                          <div className="flex items-center gap-1">
                            <span className="w-1.5 h-1.5 bg-[#F55600] rounded-full animate-bounce" />
                            <span
                              className="w-1.5 h-1.5 bg-[#F55600] rounded-full animate-bounce"
                              style={{ animationDelay: '120ms' }}
                            />
                            <span
                              className="w-1.5 h-1.5 bg-[#F55600] rounded-full animate-bounce"
                              style={{ animationDelay: '240ms' }}
                            />
                          </div>
                        )}

                        {isError && (
                          <p className="text-sm text-[#F55600]">
                            Something went wrong — try again.
                          </p>
                        )}

                        {!isLoading && !isError && isInitial && (
                          <>
                            {/* ── 3-SECTION PRODUCT DESCRIPTION (2026-05-28) ─────
                                Gemini returns a structured product_description
                                block with 3 labelled sections + drill-down arrays.
                                Render them when available; otherwise fall back
                                to the legacy single-textarea editor (kept for
                                back-compat with products created before the
                                3-section prompt rolled out). */}
                            {editableDescription && (
                              (() => {
                                const pd = editableDescription;
                                const hasAny =
                                  (pd.what_the_company_is && pd.what_the_company_is.trim()) ||
                                  (pd.what_they_do && pd.what_they_do.trim()) ||
                                  (pd.who_they_serve && pd.who_they_serve.trim()) ||
                                  (pd.key_capabilities?.length || 0) > 0 ||
                                  (pd.target_industries?.length || 0) > 0;
                                if (!hasAny) return null;


                                const parent = rawAnalysis?.parent_company?.trim?.() || '';
                                const onDescChange = (field, value) =>
                                  setEditableDescription((prev) => ({ ...prev, [field]: value }));
                                return (
                                  <div
                                    className={
                                      editingAll
                                        // EDIT: a clean white "page" (document feel) —
                                        // generous padding, soft border, no boxy inputs.
                                        ? 'mb-4 rounded-2xl border border-[#2B2926]/10 bg-white shadow-sm p-6 md:p-8'
                                        : 'mb-4 p-3 rounded-xl border border-[#2B2926]/15 bg-white'
                                    }
                                  >
                                    {parent && (
                                      <div className="text-[10px] text-[#2B2926]/60 mb-2">
                                        Parent company:{' '}
                                        <span className="font-bold text-[#2B2926]">{parent}</span>
                                      </div>
                                    )}
                                    <DescSection
                                      sectionKey="what_is"
                                      title="Who we are"
                                      body={pd.what_the_company_is}
                                      editing={editingAll}
                                      onChange={onDescChange}
                                    />
                                    <DescSection
                                      sectionKey="what_do"
                                      title="What we do"
                                      body={pd.what_they_do}
                                      editing={editingAll}
                                      onChange={onDescChange}
                                      lists={[
                                        {
                                          label: entityType === 'product' ? 'Features' : 'Services',
                                          field: 'key_capabilities',
                                          items: pd.key_capabilities || [],
                                        },
                                      ]}
                                    />
                                    <DescSection
                                      sectionKey="who_serve"
                                      title="Our focus industries"
                                      body={pd.who_they_serve}
                                      editing={editingAll}
                                      onChange={onDescChange}
                                      lists={[
                                        {
                                          label: 'Industries',
                                          field: 'target_industries',
                                          items: pd.target_industries || [],
                                        },
                                      ]}
                                    />
                                  </div>
                                );
                              })()
                            )}
                            {/*
                              Legacy fallback editor — only shown when the
                              product has NO structured 3-section
                              product_description (older rows, or sites
                              that yielded an empty Gemini structure).
                              Uses the incoming Edit-toggle pattern from
                              origin/add-nexus for cleaner UX: read-only
                              prose by default, click Edit to open a
                              focused textarea, click Done to save.
                            */}
                            {!rawAnalysis?.product_description && (
                              <>
                                <div className="flex items-center justify-between gap-2 mb-3">
                                  <label className="block text-[10px] font-black uppercase tracking-wider text-[#2B2926]/50">
                                    {entityType === 'service'
                                      ? 'Company Description'
                                      : entityType === 'gcc'
                                      ? 'GCC Description'
                                      : 'Product Description'}
                                  </label>
                                  {editingId !== 'summary' && (
                                    <button
                                      type="button"
                                      onClick={() => setEditingId('summary')}
                                      className="inline-flex items-center gap-1 px-2 py-1 rounded-md border border-[#2B2926]/10 text-[#2B2926]/70 text-[11px] font-bold hover:bg-[#F55600]/5 hover:text-[#F55600] hover:border-[#F55600]/30"
                                    >
                                      <Edit3 className="w-3 h-3" />
                                      Edit
                                    </button>
                                  )}
                                </div>
                                {editingId === 'summary' ? (
                                  <div className="relative">
                                    <textarea
                                      ref={(el) => {
                                        summaryTextareaRef.current = el;
                                        if (el) autoResize(el);
                                      }}
                                      className="w-full text-sm text-[#2B2926] bg-[#F55600]/[0.03] border border-[#F55600]/30 rounded-lg px-3 py-2 pb-10 resize-none overflow-hidden focus:outline-none focus:border-[#F55600] leading-relaxed"
                                      value={summaryText}
                                      onChange={(e) => {
                                        setSummaryText(e.target.value);
                                        autoResize(e.target);
                                      }}
                                      spellCheck={false}
                                      rows={1}
                                      autoFocus
                                    />
                                    <button
                                      type="button"
                                      onClick={() => setEditingId(null)}
                                      className="absolute bottom-2 right-2 inline-flex items-center gap-1 px-2 py-1 rounded-md bg-[#10B981] text-white text-[11px] font-bold hover:opacity-90 shadow-sm"
                                    >
                                      <CheckCircle2 className="w-3 h-3" />
                                      Done
                                    </button>
                                  </div>
                                ) : (
                                  <div className="text-sm text-[#2B2926] whitespace-pre-wrap leading-relaxed">
                                    {summaryText}
                                  </div>
                                )}
                              </>
                            )}
                          </>
                        )}

                        {!isLoading && !isError && !isInitial && (
                          // Pending refinements are editable behind an
                          // explicit Edit button (same UX as the initial
                          // summary). Approved/declined messages stay
                          // read-only so the chat history is scannable.
                          isPending ? (
                            <>
                              {editingId !== msg.id && (
                                <div className="flex items-center justify-end mb-2">
                                  <button
                                    type="button"
                                    onClick={() => setEditingId(msg.id)}
                                    className="inline-flex items-center gap-1 px-2 py-1 rounded-md border border-[#2B2926]/10 text-[#2B2926]/70 text-[11px] font-bold hover:bg-[#F55600]/5 hover:text-[#F55600] hover:border-[#F55600]/30"
                                  >
                                    <Edit3 className="w-3 h-3" />
                                    Edit
                                  </button>
                                </div>
                              )}
                              {editingId === msg.id ? (
                                <div className="relative">
                                  <textarea
                                    className="w-full text-sm text-[#2B2926] bg-[#F55600]/[0.03] border border-[#F55600]/30 rounded-lg px-3 py-2 pb-10 resize-none overflow-hidden focus:outline-none focus:border-[#F55600] leading-relaxed"
                                    value={msg.content}
                                    onChange={(e) => {
                                      const next = e.target.value;
                                      setRefineMsgs((prev) =>
                                        prev.map((m) =>
                                          m.id === msg.id ? { ...m, content: next } : m,
                                        ),
                                      );
                                      autoResize(e.target);
                                    }}
                                    spellCheck={false}
                                    rows={1}
                                    autoFocus
                                    ref={(el) => {
                                      if (el) autoResize(el);
                                    }}
                                  />
                                  <button
                                    type="button"
                                    onClick={() => setEditingId(null)}
                                    className="absolute bottom-2 right-2 inline-flex items-center gap-1 px-2 py-1 rounded-md bg-[#10B981] text-white text-[11px] font-bold hover:opacity-90 shadow-sm"
                                  >
                                    <CheckCircle2 className="w-3 h-3" />
                                    Done
                                  </button>
                                </div>
                              ) : (
                                <div className="text-sm text-[#2B2926] whitespace-pre-wrap leading-relaxed">
                                  {msg.content}
                                </div>
                              )}
                            </>
                          ) : (
                            <div className="text-sm text-[#2B2926] whitespace-pre-wrap leading-relaxed">
                              {msg.content}
                            </div>
                          )
                        )}

                        {isPending && (
                          <div className="flex items-center gap-2 mt-3 pt-3 border-t border-[#2B2926]/10">
                            <button
                              type="button"
                              onClick={() => handleApproveRefinement(msg.id, msg.content)}
                              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-[#10B981] text-white text-xs font-bold hover:opacity-90"
                            >
                              <CheckCircle2 className="w-3 h-3" />
                              Apply changes
                            </button>
                            <button
                              type="button"
                              onClick={() => handleDeclineRefinement(msg.id)}
                              className="text-xs font-bold text-[#2B2926]/60 hover:text-[#F55600]"
                            >
                              Discard
                            </button>
                          </div>
                        )}

                        {isApproved && (
                          <div className="inline-flex items-center gap-1 mt-2 text-[11px] font-bold text-[#10B981]">
                            <CheckCircle2 className="w-3 h-3" />
                            Applied
                          </div>
                        )}
                        {isDeclined && (
                          <div className="inline-flex items-center gap-1 mt-2 text-[11px] font-bold text-[#2B2926]/40">
                            Discarded
                          </div>
                        )}
                        </div>
                        {/* Pinned edit-mode footer — sits OUTSIDE the scroll
                            area (shrink-0) so it's always visible at the bottom
                            of the card and clicking Done never scrolls/collapses
                            the container under the cursor. */}
                        {isInitial && editingAll && (
                          <div className="shrink-0 flex items-center justify-end pt-2 mt-1 -mx-4 px-4 border-t border-[#2B2926]/10">
                            <button
                              type="button"
                              onClick={() => setEditingAll(false)}
                              className="inline-flex items-center gap-1.5 text-[13px] font-bold text-white bg-[#2B2926] px-5 py-1.5 rounded-lg shadow-sm hover:bg-[#2B2926]/85"
                            >
                              <CheckCircle2 className="w-3.5 h-3.5" />
                              Done
                            </button>
                          </div>
                        )}
                      </div>
                    </div>
                  );
                }
                return null;
              })}

              {brandColors.length > 0 && (
                <div className="flex items-center gap-2 px-3 py-2 rounded-lg border border-[#2B2926]/10 mt-2">
                  <span className="text-[10px] font-black uppercase tracking-wider text-[#2B2926]/60">
                    Brand colors
                  </span>
                  <div className="flex items-center gap-1">
                    {brandColors.map((hex, i) => (
                      <div
                        key={i}
                        className="w-4 h-4 rounded border border-[#2B2926]/10"
                        style={{ background: hex }}
                        title={hex}
                      />
                    ))}
                  </div>
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>

            {error && <p className="text-xs font-bold text-[#F55600] mb-2">{error}</p>}

            {/* Approve + refine */}
            <div className="space-y-3 pt-3 border-t border-[#2B2926]/10">
              <div className="flex items-center justify-between gap-3 flex-wrap">
                {/* Back — solid black */}
                <button
                  type="button"
                  onClick={() => {
                    setStep('url');
                    setError('');
                  }}
                  className="inline-flex items-center justify-center gap-1.5"
                  style={{
                    background: '#0F1115',
                    color: '#fff',
                    border: '1px solid #0F1115',
                    borderRadius: 10,
                    height: 38,
                    padding: '0 16px',
                    fontSize: 13,
                    fontWeight: 700,
                    cursor: 'pointer',
                    transition: 'background 0.15s',
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = '#1c2128')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = '#0F1115')}
                >
                  ← Back
                </button>

                {/* Regenerate — centered between Back and the action group
                    (flex justify-between makes the middle child center). */}
                <button
                  type="button"
                  onClick={() => handleUrlSubmit()}
                  disabled={step === 'scraping' || isRefining}
                  title="Regenerate the description from the same URL"
                  className="inline-flex items-center justify-center gap-1.5 hover:bg-[#F55600]/5 hover:border-[#F55600]/40 hover:text-[#F55600] disabled:opacity-50 transition-colors"
                  style={{
                    background: '#fff',
                    color: '#15171c',
                    border: '1px solid #b0b4bd',
                    borderRadius: 10,
                    height: 38,
                    padding: '0 16px',
                    fontSize: 13,
                    fontWeight: 700,
                    cursor: 'pointer',
                  }}
                >
                  {step === 'scraping' ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <RefreshCw className="w-3.5 h-3.5" />
                  )}
                  Regenerate
                </button>

                <div className="inline-flex items-center gap-2 flex-wrap">
                  {/* Glassy Edit — opens inline edit mode for the whole
                      description (replaces the in-card pencil). Same 38px
                      height as Back / continue; visible darker border.
                      Hidden while editing (the in-card "Done" button is the
                      exit affordance then) so we don't show Edit + Done both. */}
                  {!editingAll && (
                  <button
                    type="button"
                    onClick={() => {
                      setEditingAll(true);
                      // Bring the description card into view after it
                      // switches to editable fields.
                      const el = summaryTextareaRef.current;
                      if (el) {
                        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                      }
                    }}
                    className="inline-flex items-center justify-center gap-1.5"
                    style={{
                      background: 'rgba(255,255,255,0.6)',
                      color: '#15171c',
                      border: '1px solid #b0b4bd',
                      borderRadius: 10,
                      fontWeight: 700,
                      fontSize: 13,
                      height: 38,
                      padding: '0 16px',
                      cursor: 'pointer',
                      backdropFilter: 'saturate(1.4) blur(10px)',
                      WebkitBackdropFilter: 'saturate(1.4) blur(10px)',
                      transition: 'background 0.15s',
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = '#f4f6f7')}
                    onMouseLeave={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.6)')}
                  >
                    <Edit3 className="w-[14px] h-[14px]" style={{ color: '#F55600' }} />
                    Edit
                  </button>
                  )}

                  <button
                    type="button"
                    onClick={handleApproveSummary}
                    disabled={isRefining || hasPending}
                    title={hasPending ? 'Apply or discard the pending changes first' : ''}
                    className="inline-flex items-center justify-center gap-1.5 disabled:opacity-50"
                    style={{
                      background: '#F55600',
                      color: '#fff',
                      border: '1px solid #F55600',
                      borderRadius: 10,
                      height: 38,
                      padding: '0 16px',
                      fontSize: 13,
                      fontWeight: 700,
                      cursor: isRefining || hasPending ? 'not-allowed' : 'pointer',
                    }}
                  >
                    Continue
                    <ArrowRight className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ── targeting step ──────────────────────────────────────── */}
        {step === 'targeting' && (
          <div className="animate-in fade-in duration-300">
            {/* Brand pill (favicon + product name) removed per request. */}
            <h2 className="text-2xl font-black tracking-tight text-[#2B2926] mb-4">
              Target Audience
            </h2>
            {icpLoading && (
              <div className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-[#F55600]/5 text-xs font-bold text-[#F55600] mb-4">
                <Loader2 className="w-3 h-3 animate-spin" />
                Analysing your product summary…
              </div>
            )}

            <div className={icpLoading ? 'opacity-60 pointer-events-none' : ''}>
              <LocationSelect
                label="Target Locations"
                selected={targetLocations}
                onChange={setTargetLocations}
                placeholder='Search e.g. "United States" or "Texas, United States"'
                badge={<AutofillBadge meta={targetMeta.person_locations} />}
              />
              <MultiSelect
                label="Target Industries"
                options={INDUSTRIES}
                selected={targetIndustries}
                onChange={setTargetIndustries}
                placeholder="Search industries..."
                badge={<AutofillBadge meta={targetMeta.organization_industries} />}
              />
              <RevenueCombo
                label="Company Revenue"
                value={targetRevenue}
                onChange={setTargetRevenue}
                badge={<AutofillBadge meta={targetMeta.revenue_range} />}
              />
              {/* FREE-TEXT (2026-06-09): Apollo's person_titles accepts ANY
                  job title string, so Roles is an open input — the ROLES list
                  is just autocomplete suggestions, not a restriction. Lets
                  users target titles beyond our 60 (e.g. "Chief Revenue
                  Officer", "Head of Growth") for full Apollo parity. */}
              <FreeTextChips
                label="Target Roles"
                suggestions={ROLES}
                selected={targetRoles}
                onChange={setTargetRoles}
                placeholder="Type any job title (e.g. Chief Revenue Officer)…"
                badge={<AutofillBadge meta={targetMeta.person_titles} />}
              />

              {/* Target Seniority + Target Departments rows removed per
                  user request — both are redundant with Target Roles
                  (titles already imply seniority AND department), and
                  sending them to Apollo over-narrowed the match set.
                  Maps to analyze_targeting.py + discovery_apollo.py. */}
              <FreeTextChips
                label="Target Technologies"
                suggestions={ALL_TECHNOLOGIES}
                selected={targetTechnologies}
                onChange={setTargetTechnologies}
                placeholder="Add a technology (e.g. Salesforce)..."
                badge={<AutofillBadge meta={targetMeta.buyer_technologies} />}
              />
            </div>

            {/* Who outbound email is signed by. Required — see RepresentativeCard. */}
            <RepresentativeCard
              user={user}
              repIsMe={repIsMe}
              setRepIsMe={setRepIsMe}
              repName={repName}
              setRepName={setRepName}
              repTitle={repTitle}
              setRepTitle={setRepTitle}
              productName={productName}
              disabled={step === 'launching'}
            />

            {error && <p className="text-xs font-bold text-[#F55600] mb-2">{error}</p>}

            <div className="flex items-center justify-between pt-3 border-t border-[#2B2926]/10">
              <button
                type="button"
                onClick={() => {
                  // Content mode has NO product-description page — Back returns
                  // to the input box (where the user pasted/uploaded). URL mode
                  // goes back to the summary page as before.
                  setStep(inputMode === 'content' ? 'url' : 'summary');
                  setError('');
                }}
                className="inline-flex items-center gap-1.5"
                style={{
                  background: '#0F1115',
                  color: '#fff',
                  border: 'none',
                  borderRadius: 10,
                  padding: '9px 16px',
                  fontSize: 13,
                  fontWeight: 700,
                  cursor: 'pointer',
                  transition: 'background 0.15s',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.background = '#1c2128')}
                onMouseLeave={(e) => (e.currentTarget.style.background = '#0F1115')}
              >
                ← Back
              </button>
              {/* Right group: "Number of leads" stepper inline next to Launch
                  (placement C). Native number input -> type a value OR use the
                  arrows. Default 20, min 1, no max. Drives the QUALIFIED lead
                  target sent to /analyze as lead_count. */}
              <div className="flex items-center gap-3">
                <label
                  className="flex items-center gap-2 text-[11px] font-black uppercase tracking-wider text-[#2B2926]/55"
                  title="How many qualified leads to find this run"
                >
                  Number of leads
                  <input
                    type="number"
                    min={1}
                    step={1}
                    value={leadCount}
                    onChange={(e) =>
                      setLeadCount(
                        e.target.value === ''
                          ? ''
                          : Math.max(1, parseInt(e.target.value, 10) || 1),
                      )
                    }
                    onBlur={() =>
                      setLeadCount((c) => (c === '' || c < 1 ? 20 : c))
                    }
                    disabled={step === 'launching'}
                    className="w-[68px] text-sm font-bold text-[#2B2926] bg-white border border-[#cdd1d9] rounded-lg px-2 py-1.5 focus:outline-none focus:border-[#F55600] disabled:opacity-50"
                  />
                </label>
                <button
                  type="button"
                  onClick={handleLaunch}
                  disabled={
                    step === 'launching' ||
                    !workspaceId ||
                    !summaryText?.trim() ||
                    !(
                      targetLocations?.length ||
                      targetIndustries?.length ||
                      !!targetRevenue ||
                      targetRoles?.length ||
                      targetTechnologies?.length
                    )
                  }
                  className="inline-flex items-center gap-1.5 disabled:opacity-50"
                  style={{
                    background: '#F55600',
                    color: '#fff',
                    border: 'none',
                    borderRadius: 10,
                    padding: '9px 16px',
                    fontSize: 13,
                    fontWeight: 700,
                    cursor: step === 'launching' ? 'not-allowed' : 'pointer',
                  }}
                >
                  {step === 'launching' ? (
                    <>
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      Creating campaign...
                    </>
                  ) : (
                    <>
                      Launch AI Campaign
                      <ArrowRight className="w-3.5 h-3.5" />
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ── launching step ──────────────────────────────────────── */}
        {step === 'launching' && (
          <LaunchStepsTimeline error={error} />
        )}

        {/* ── launched step ───────────────────────────────────────── */}
        {step === 'launched' && (
          <div className="animate-in fade-in duration-300 pt-4 pb-3">
            {/* Compact single-row header so the leads table below gets the
                vertical space (was a large centered icon/title/subtitle
                block that pushed the table down). */}
            {/* /analyze now runs without a wall-clock cap (Lambda Function
                URL — no API Gateway 29s ceiling). Discovery runs to natural
                completion and the response includes all leads inline. */}
            <div className="flex items-center gap-3 mb-4">
              <div className="inline-flex items-center justify-center w-9 h-9 rounded-xl bg-[#10B981]/10 text-[#10B981] shrink-0">
                <CheckCircle2 className="w-5 h-5" />
              </div>
              <div className="min-w-0">
                <h2 className="text-lg font-black tracking-tight text-[#2B2926] leading-tight">
                  Campaign launched
                  {launchResult?.campaign?.product_name && (
                    <span title="Product / company"> · {launchResult.campaign.product_name}</span>
                  )}
                  {launchResult?.campaign?.campaign_number != null && (
                    <span title="Campaign ID (per product)"> #{launchResult.campaign.campaign_number}</span>
                  )}
                </h2>
              </div>
            </div>

            {/* Shortfall is surfaced as a TOAST (see the launch handler's
                setMessage call), not an inline banner — keeps the launched
                view clean and the table high on the page. */}

            {/* Live leads table. Polls /nexus/campaigns/{id}/leads while
                the backend reports status="discovering". Each row click
                fires onLeadClick — the right-panel (Content/Flow/Analytics)
                that another developer is building consumes that callback.
                Until that panel ships, clicking a row is a no-op. */}
            <LeadsTable
              campaignId={launchResult?.campaign?.id}
              authAxios={authAxios}
              apiBase={apiBase}
              // Toast when the background email-content generation completes.
              setMessage={setMessage}
              // Show the discovery funnel (Found → Checking signals → Accepted)
              // + the Start-Outreach gate once Agent #10 finishes scoring.
              enableStartOutreach={true}
              // Fence the table + funnel to ONLY this run's leads (the campaign
              // is reused across runs and never cleared).
              latestRunOnly={true}
              // If /analyze returned leads inline, pass them so the table
              // renders immediately. If it returned NONE yet (discovery
              // still running), pass null — NOT [] — so LeadsTable falls
              // back to its polling path and surfaces leads as they land.
              // ([] is truthy, which would wrongly flip status to "done"
              // and skip polling entirely → permanent "No leads found".)
              initialLeads={
                launchResult?.leads && launchResult.leads.length > 0
                  ? launchResult.leads
                  : null
              }
              onLeadClick={(lead) => {
                // TODO: integrate with the Content/Flow/Analytics
                // right-panel once the parallel-work branch is merged.
                // For now we navigate to GTM Journey filtered by lead.
                if (onNavigate && lead?.lead_sequence_id) {
                  onNavigate('gtm-journey', {
                    leadSequenceId: lead.lead_sequence_id,
                  });
                }
              }}
            />

            {/* 2026-05-29 — Outbound emails preview REMOVED from the
                launched screen per user request. The LeadsTable above
                is the single source of truth for what was enrolled in
                this run. Per-lead email content lives in the Lead
                detail panel (Content tab) on the GTM Journey page. */}

            <div className="flex items-center justify-center gap-3 mt-6">
              <button
                type="button"
                // Go back to the ICP filter step (filters are preserved) so the
                // user can tweak targeting and re-run without starting over.
                onClick={() => setStep('targeting')}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-[#2B2926] text-xs font-bold text-[#2B2926] hover:bg-[#F55600]/5 hover:border-[#1A1816] transition-colors"
              >
                ← Back to filters
              </button>
              <button
                type="button"
                onClick={() => {
                  setStep('url');
                  setUrl('');
                  setSummaryText('');
                  setRefineMsgs([]);
                  setLaunchResult(null);
                  setError('');
                }}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-[#2B2926] text-xs font-bold text-[#2B2926] hover:bg-[#F55600]/5 hover:border-[#1A1816] transition-colors"
              >
                Start another
              </button>
              <button
                type="button"
                onClick={() => onNavigate && onNavigate('gtm-journey')}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[#F55600] text-white text-xs font-black hover:opacity-90"
              >
                View leads
                <ArrowRight className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

// ─────────────────────────────────────────────────────────────────────────────
// CampaignOutboundEmailsPreview — fetches and shows the Gemini-generated
// subject + body for every email queued/sent for this campaign. Polls every
// 8 seconds for up to ~3 minutes so the operator sees emails as they land,
// then stops. Read-only.
// ─────────────────────────────────────────────────────────────────────────────
// LaunchStepsTimeline — shown while /analyze runs after the user clicks
// "Launch AI Run" on the targeting step. Replaces the legacy generic
// "Creating run… ~30s spinner" with a 4-step progress timeline. Steps
// are client-side time-driven (no SSE / polling) so this works without
// any backend change. If /analyze finishes faster than the timeline,
// the parent transitions to the launched screen — no flash.
// ─────────────────────────────────────────────────────────────────────────────
// Steps mirror the ACTUAL backend discovery flow (Option B, 2026-06-09):
//   1. Apollo people-search for ICP matches
//   2. Agent #10 scores each company's buying intent  ← qualify BEFORE reveal
//   3. Reveal verified emails for QUALIFIED leads only (Apollo bulk_match)
//   4. Attach the qualified leads into the campaign list
// Outreach/email personalization is NOT part of this screen — it runs in the
// background AFTER the qualified-leads table is shown, so it's not listed here.
const _LAUNCH_STEPS = [
  { id: 'search',  label: 'Matching leads to your criteria',   icon: '🔍', durationMs:  6000 },
  { id: 'qualify', label: 'Qualifying leads by buying intent',  icon: '🎯', durationMs: 14000 },
  { id: 'reveal',  label: 'Revealing emails for qualified leads', icon: '✉️', durationMs:  8000 },
];

function LaunchStepsTimeline({ error }) {
  const [activeIdx, setActiveIdx] = React.useState(0);

  React.useEffect(() => {
    // Schedule one timer per step boundary.
    const timers = [];
    let cumulative = 0;
    _LAUNCH_STEPS.forEach((step, i) => {
      cumulative += step.durationMs;
      // Move to NEXT step (i+1) after this one's duration elapses.
      // The final step stays "active" until /analyze returns and the
      // parent transitions to 'launched' — no need to mark it done here.
      if (i < _LAUNCH_STEPS.length - 1) {
        timers.push(setTimeout(() => setActiveIdx(i + 1), cumulative));
      }
    });
    return () => timers.forEach(clearTimeout);
  }, []);

  return (
    <div className="animate-in fade-in duration-300 flex flex-col items-center py-12 px-6">
      <div className="w-full max-w-md">
        <h2 className="text-xl font-black tracking-tight text-[#2B2926] mb-8 text-center">
          Finding your leads
        </h2>

        <ol className="space-y-2">
          {_LAUNCH_STEPS.map((step, i) => {
            const isDone = i < activeIdx;
            const isActive = i === activeIdx;
            const isWaiting = i > activeIdx;
            return (
              <li
                key={step.id}
                className={[
                  'flex items-center gap-3 px-4 py-3 rounded-lg border transition-all',
                  isDone
                    ? 'border-[#10B981]/30 bg-[#10B981]/5'
                    : isActive
                    ? 'border-[#F55600]/40 bg-[#F55600]/5 shadow-sm'
                    : 'border-[#2B2926]/10 bg-white',
                ].join(' ')}
              >
                {/* Status icon */}
                <span
                  className={[
                    'shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-[12px] font-bold',
                    isDone
                      ? 'bg-[#10B981] text-white'
                      : isActive
                      ? 'bg-[#F55600] text-white'
                      : 'bg-[#2B2926]/5 text-[#2B2926]/30',
                  ].join(' ')}
                >
                  {isDone ? (
                    <CheckCircle2 className="w-4 h-4" />
                  ) : isActive ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <span>{i + 1}</span>
                  )}
                </span>

                {/* Label with emoji */}
                <span
                  className={[
                    'flex-1 text-sm font-bold',
                    isDone
                      ? 'text-[#2B2926]/60'
                      : isActive
                      ? 'text-[#2B2926]'
                      : 'text-[#2B2926]/40',
                  ].join(' ')}
                >
                  <span className="mr-1.5">{step.icon}</span>
                  {step.label}
                </span>

                {/* Status text */}
                <span
                  className={[
                    'shrink-0 text-[10px] font-bold uppercase tracking-wider',
                    isDone
                      ? 'text-[#10B981]'
                      : isActive
                      ? 'text-[#F55600]'
                      : 'text-[#2B2926]/30',
                  ].join(' ')}
                >
                  {isDone ? 'Done' : isActive ? 'In progress' : 'Waiting'}
                </span>
              </li>
            );
          })}
        </ol>

        {error && (
          <p className="mt-4 text-xs font-bold text-[#F55600] text-center">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}

//
// When sender = Apollo: status comes back as 'queued_to_apollo' until Apollo
// physically sends, after which we have no signal (Apollo's webhook isn't
// wired yet). When sender = Resend: status flips to 'sent_via_resend'.
// ─────────────────────────────────────────────────────────────────────────────
function CampaignOutboundEmailsPreview({ campaignId, authAxios, apiBase }) {
  const [emails, setEmails] = React.useState([]);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState('');
  const [expandedId, setExpandedId] = React.useState(null);
  // pollNonce: bumped by the manual Refresh button to restart the effect
  // (and thus reset attempts to 0 + start a fresh polling budget).
  // autoPolling: drives the "· live" badge in the header — true while we
  // still have a scheduled tick, false once we've stopped (either because
  // every row is terminal or because we exhausted the ~21-min budget).
  const [pollNonce, setPollNonce] = React.useState(0);
  const [autoPolling, setAutoPolling] = React.useState(false);

  React.useEffect(() => {
    if (!campaignId || !authAxios) return undefined;
    // Reset state on campaign change so the preview doesn't briefly
    // render the PREVIOUS campaign's emails before the new poll's first
    // response arrives. Without this reset, opening the wizard for a
    // second launch would show the old campaign's 20+ rows for the few
    // seconds it takes the fresh poll to complete.
    setEmails([]);
    setError('');
    setExpandedId(null);

    let cancelled = false;
    let attempts = 0;

    // Adaptive backoff so we keep waiting long enough for Gemini to
    // finish generating bodies for slow leads (sometimes 5–10 min when
    // the API is busy) without hammering the backend forever.
    //   attempts  0–21   → poll every  8 s   (first ~3 min)
    //   attempts 22–37   → poll every 30 s   (next ~8 min)
    //   attempts 38–47   → poll every 60 s   (next ~10 min)
    //   attempts ≥ 48    → stop (operator can hit Refresh)
    function nextDelay(n) {
      if (n < 22) return 8000;
      if (n < 38) return 30000;
      if (n < 48) return 60000;
      return null;
    }

    setAutoPolling(true);

    async function poll() {
      if (cancelled) return;
      attempts += 1;
      let latestEmails = [];
      try {
        const res = await authAxios.get(
          `${apiBase}/campaigns/${campaignId}/outbound-emails?limit=25`,
        );
        if (cancelled) return;
        latestEmails = res.data?.emails || [];
        setEmails(latestEmails);
        setError('');
      } catch (err) {
        if (cancelled) return;
        setError(err?.response?.data?.detail || err?.message || 'Failed to load queued emails');
      } finally {
        if (cancelled) return;
        setLoading(false);
      }
      // Stop polling once we have ≥1 row AND no rows are still pending
      // (the operator has the snapshot they need). Use `latestEmails`
      // (the fresh response value) NOT the closure-captured `emails`
      // state — the setState above hasn't propagated yet on this tick,
      // so `emails` would be stale and could short-circuit polling
      // before any emails actually showed up.
      const pending = latestEmails.some(
        (e) =>
          (e.display_status === 'queued_to_apollo' && !e.sent_at) ||
          e.display_status === 'pregenerated',
      );
      const haveSome = latestEmails.length > 0;
      if (haveSome && !pending) {
        setAutoPolling(false);
        return;
      }
      const delay = nextDelay(attempts);
      if (delay === null) {
        // Budget exhausted — stop and let the operator hit Refresh.
        setAutoPolling(false);
        return;
      }
      window.setTimeout(poll, delay);
    }
    setLoading(true);
    poll();
    return () => {
      cancelled = true;
      setAutoPolling(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaignId, apiBase, pollNonce]);

  if (!campaignId) return null;

  const statusBadge = (s) => {
    const m = {
      pregenerated: { label: 'Generating · awaiting send', cls: 'bg-[#F55600]/5 text-[#F55600]/80' },
      queued_to_apollo: { label: 'Queued for send', cls: 'bg-[#F55600]/10 text-[#F55600]' },
      sent_via_resend: { label: 'Sent', cls: 'bg-[#10B981]/15 text-[#10B981]' },
      failed_to_queue: { label: 'Failed to queue', cls: 'bg-[#2B2926]/5 text-[#2B2926]/60' },
      failed_send: { label: 'Failed send', cls: 'bg-[#2B2926]/5 text-[#2B2926]/60' },
    };
    return m[s] || { label: s || '—', cls: 'bg-[#2B2926]/5 text-[#2B2926]/60' };
  };

  return (
    <div className="mt-6 border-t border-[#2B2926]/10 pt-6">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-xs uppercase tracking-wider font-black text-[#2B2926]/60">
            Outbound emails
          </h3>
          <p className="text-[11px] text-[#2B2926]/40 mt-0.5">
            AI-generated content per lead. Outbound queue schedules the actual send.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-bold text-[#2B2926]/40">
            {loading
              ? 'Loading…'
              : autoPolling
              ? `${emails.length} email${emails.length === 1 ? '' : 's'} · live`
              : `${emails.length} email${emails.length === 1 ? '' : 's'}`}
          </span>
          {/* Manual Refresh — restarts the poll effect with a fresh
              ~21-min budget. Useful when Gemini is still generating
              bodies after our auto-budget expired, or when the operator
              wants an immediate re-fetch instead of waiting for the
              next adaptive tick. */}
          <button
            type="button"
            onClick={() => setPollNonce((n) => n + 1)}
            className="inline-flex items-center gap-1 px-2 py-1 rounded text-[10px] font-bold text-[#2B2926]/60 hover:text-[#F55600] hover:bg-[#F55600]/5 border border-[#2B2926]/10"
            title="Refresh"
          >
            <RefreshCw size={11} />
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <p className="text-[11px] text-[#F55600] mb-2">{error}</p>
      )}

      {!loading && !error && emails.length === 0 && (
        <p className="text-xs text-[#2B2926]/40 italic">
          No emails queued yet. Discovery may still be running — refresh in a minute or check the GTM Journey.
        </p>
      )}

      {emails.length > 0 && (
        // Spenzo-table-v2 styling — card + dark header, click a row to
        // expand its full body inline (replaces the old accordion cards).
        <div
          className="overflow-x-auto bg-white"
          style={{
            border: '1px solid #E5E7EB',
            borderRadius: 14,
            boxShadow: '0 8px 24px rgba(17,24,39,0.06)',
          }}
        >
          <div className="max-h-96 overflow-y-auto">
            <table className="w-full" style={{ borderCollapse: 'collapse', fontSize: 13.5 }}>
              <thead className="sticky top-0 z-10">
                <tr>
                  {['#', 'EMAIL', 'COMPANY', 'SUBJECT', 'STATUS'].map((h) => (
                    <th
                      key={h}
                      className="text-left whitespace-nowrap"
                      style={{
                        background: '#111111',
                        color: '#CBD5E1',
                        fontSize: 11,
                        fontWeight: 600,
                        letterSpacing: '0.6px',
                        textTransform: 'uppercase',
                        padding: '13px 16px',
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {emails.map((e, i) => {
                  const b = statusBadge(e.display_status);
                  const isOpen = expandedId === e.touchpoint_id;
                  const cellBorder = '1px solid #F1F2F4';
                  return (
                    <React.Fragment key={e.touchpoint_id}>
                      <tr
                        onClick={() => setExpandedId(isOpen ? null : e.touchpoint_id)}
                        className="cursor-pointer"
                        style={{ background: isOpen ? 'rgba(43,41,38,0.05)' : 'transparent', transition: 'background 0.12s' }}
                        onMouseEnter={(ev) => { if (!isOpen) ev.currentTarget.style.background = '#FFF6F2'; }}
                        onMouseLeave={(ev) => { if (!isOpen) ev.currentTarget.style.background = 'transparent'; }}
                      >
                        <td style={{ padding: '13px 16px', borderTop: cellBorder, color: '#9AA0AA', fontVariantNumeric: 'tabular-nums', fontSize: 13 }}>
                          {i + 1}
                        </td>
                        <td
                          style={{ padding: '13px 16px', borderTop: cellBorder, maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                          title={e.lead_email || ''}
                        >
                          <span style={{ color: '#111111', fontWeight: 600 }}>{e.lead_email || '—'}</span>
                        </td>
                        <td
                          style={{ padding: '13px 16px', borderTop: cellBorder, color: '#6B7280', maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                          title={e.company_name || ''}
                        >
                          {e.company_name || '—'}
                        </td>
                        <td
                          style={{ padding: '13px 16px', borderTop: cellBorder, color: '#6B7280', maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                          title={e.subject || ''}
                        >
                          {e.subject || '(no subject)'}
                        </td>
                        <td style={{ padding: '13px 16px', borderTop: cellBorder }}>
                          <span
                            className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold ${b.cls}`}
                            style={{ whiteSpace: 'nowrap' }}
                          >
                            {b.label}
                          </span>
                        </td>
                      </tr>
                      {isOpen && (
                        <tr>
                          <td colSpan={5} style={{ borderTop: cellBorder, background: '#FBFBFA', padding: '12px 16px' }}>
                            <div className="text-[10px] uppercase tracking-wider font-bold text-[#2B2926]/40">
                              Body
                            </div>
                            <pre className="mt-1 text-[12px] text-[#2B2926]/80 whitespace-pre-wrap font-sans leading-relaxed">
                              {e.body || '(empty)'}
                            </pre>
                            {e.error && (
                              <div className="mt-2 text-[11px] text-[#F55600]">
                                Error: {e.error}
                              </div>
                            )}
                            {e.sent_at && (
                              <div className="mt-2 text-[10px] text-[#2B2926]/40">
                                {/* Backend stores TIMESTAMP WITHOUT TIME ZONE
                                    and serializes as naive ISO (no 'Z'); browser
                                    would treat it as local time and shift the
                                    label. Force-append 'Z' so it's parsed as UTC
                                    then toLocaleString renders in the user's
                                    actual zone. */}
                                {new Date(
                                  /[Zz]$|[+-]\d{2}:?\d{2}$/.test(e.sent_at) ? e.sent_at : e.sent_at + 'Z'
                                ).toLocaleString()}
                              </div>
                            )}
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

export default NexusNewCampaign;