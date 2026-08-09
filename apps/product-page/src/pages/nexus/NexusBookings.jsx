import React, { useEffect, useState, useCallback, useMemo } from 'react';
import BrandSelect from '../../components/BrandSelect';
import MeetingAgendaPanel from './MeetingAgendaPanel';
import { isReadOnly } from '../../lib/permissions';
import {
  Calendar as CalendarIcon,
  CalendarDays,
  ChevronLeft,
  Loader2,
  AlertCircle,
  List,
  Clock,
  User,
  Mail,
  X,
  FileText,
  Sparkles,
  RefreshCw,
  Building2,
  Lightbulb,
  Target,
  Zap,
  MessageSquare,
  Shield,
  HelpCircle,
} from 'lucide-react';
import NexusBookingsCalendar from './NexusBookingsCalendar';

/**
 * NexusBookings
 *
 * Demo bookings list (MS Bookings / Calendly) grouped by relative day, with a
 * collapsible MS Bookings setup panel at the top.
 *
 * Palette: PIPELYT mandatory four — #F55600, #10B981, black, white.
 */

const STATUS_BADGE = {
  scheduled: 'text-[#F55600] bg-[#F55600]/10',
  completed: 'text-[#10B981] bg-[#10B981]/10',
  cancelled: 'text-[#2B2926] bg-[#2B2926]/5',
  no_show:   'text-[#2B2926] bg-[#2B2926]/5',
};

const SOURCE_LABEL = {
  ms_bookings: 'MS Bookings',
  calendly:    'Calendly',
};

const TABS = [
  { id: 'upcoming', label: 'Upcoming' },
  { id: 'past',     label: 'Past' },
  { id: 'all',      label: 'All' },
];

// ─────────────────────────────────────────────────────────────────────────────
// Date helpers
// ─────────────────────────────────────────────────────────────────────────────

function startOfDay(d) {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return x;
}

function daysBetween(a, b) {
  const ms = startOfDay(a).getTime() - startOfDay(b).getTime();
  return Math.round(ms / (24 * 60 * 60 * 1000));
}

function dayGroupLabel(iso) {
  if (!iso) return 'No date';
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return 'No date';
  const today = new Date();
  const diff = daysBetween(d, today);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Tomorrow';
  if (diff === -1) return 'Yesterday';
  if (diff > 1 && diff <= 7) return 'Next Week';
  if (diff < -1 && diff >= -7) return 'Last Week';
  if (diff > 7 && diff <= 30) return 'This Month';
  if (diff < -7 && diff >= -30) return 'Earlier This Month';
  if (diff > 30) {
    try {
      return d.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
    } catch { return 'Later'; }
  }
  try {
    return d.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
  } catch { return 'Earlier'; }
}

function fmtDateTime(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString(undefined, {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    });
  } catch { return iso; }
}

// ─────────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────────

const NexusBookings = ({ authAxios, user, setMessage = () => {}, onBack }) => {
  const [tab, setTab] = useState('upcoming');
  // Top-level mode: 'list' (legacy grouped list) or 'calendar' (month/week/day grid).
  const [mode, setMode] = useState('calendar');

  const [bookings, setBookings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  // Selected booking shown in the detail modal. NULL = closed. Stored locally
  // (NOT passed to App's setMessage) because the top-level Toast component
  // expects a string and crashes — taking out the whole React tree, hence
  // the legacy blank-screen-on-click report — if handed an object.
  const [selected, setSelected] = useState(null);

  // ── Slice filter state (mirrors Dashboard's filter pattern) ──────────────
  // entityFilter:    'all' | 'product' | 'service'
  // selectedProductId: null (all of that type) | number (specific target)
  // The product list is fetched from /nexus/analytics/per-product so the
  // dropdown shows every product/service in the workspace, even ones
  // without any bookings yet (lets the operator notice "no demos
  // booked for X yet" rather than just hiding empties).
  const [entityFilter, setEntityFilter] = useState('all');
  const [selectedProductId, setSelectedProductId] = useState(null);
  const [products, setProducts] = useState([]);

  // Reset specific-product selection when switching entity type.
  useEffect(() => {
    setSelectedProductId(null);
  }, [entityFilter]);

  // Fetch the full product/service list once — used to populate the
  // picker. Cheap (single GET); doesn't depend on the booking filter.
  useEffect(() => {
    if (!authAxios) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const res = await authAxios.get(
          '/nexus/analytics/per-product?entity_type=all&period=30d',
        );
        if (cancelled) return;
        const list = Array.isArray(res?.data?.products) ? res.data.products : [];
        setProducts(list);
      } catch {
        if (!cancelled) setProducts([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authAxios]);

  // Bookings fetch — re-runs whenever the slice filter changes. Backend
  // accepts optional product_id + entity_type query params; when neither
  // is sent the server returns every booking for the workspace (its
  // pre-change behaviour).
  const fetchBookings = useCallback(async () => {
    setError('');
    setLoading(true);
    try {
      const qs = new URLSearchParams();
      qs.set('limit', '50');
      if (entityFilter !== 'all') qs.set('entity_type', entityFilter);
      if (selectedProductId != null) qs.set('product_id', String(selectedProductId));
      const res = await authAxios.get(`/nexus/bookings?${qs.toString()}`);
      const data = Array.isArray(res.data) ? res.data : (res.data?.bookings || []);
      setBookings(data);
    } catch (err) {
      if (err?.response?.status === 404) {
        setBookings([]);
      } else {
        setError(err?.response?.data?.detail || err.message || 'Failed to load bookings');
      }
    } finally {
      setLoading(false);
    }
  }, [authAxios, entityFilter, selectedProductId]);

  useEffect(() => {
    fetchBookings();
  }, [fetchBookings]);

  // ── Specific-product dropdown options, filtered by current entityFilter
  const productOptions = useMemo(() => {
    if (entityFilter === 'all') return [];
    return (products || [])
      .filter((p) => (p.entity_type || 'product') === entityFilter)
      .map((p) => ({ id: p.product_id, name: p.name || `Product ${p.product_id}` }));
  }, [products, entityFilter]);

  const isFiltered = entityFilter !== 'all' || selectedProductId != null;

  // ── Filter by tab ──────────────────────────────────────────────────────────
  const filtered = useMemo(() => {
    const now = Date.now();
    let list = bookings.filter((b) => !!b);
    if (tab === 'upcoming') {
      list = list.filter((b) => {
        const t = b.scheduled_at ? new Date(b.scheduled_at).getTime() : 0;
        return t >= now;
      });
      list.sort((a, b) => new Date(a.scheduled_at) - new Date(b.scheduled_at));
    } else if (tab === 'past') {
      list = list.filter((b) => {
        const t = b.scheduled_at ? new Date(b.scheduled_at).getTime() : 0;
        return t < now;
      });
      list.sort((a, b) => new Date(b.scheduled_at) - new Date(a.scheduled_at));
    } else {
      list.sort((a, b) => new Date(b.scheduled_at) - new Date(a.scheduled_at));
    }
    return list;
  }, [bookings, tab]);

  // ── Group by day-label ─────────────────────────────────────────────────────
  const grouped = useMemo(() => {
    const groups = [];
    const indexByLabel = {};
    filtered.forEach((b) => {
      const label = dayGroupLabel(b.scheduled_at);
      if (!(label in indexByLabel)) {
        indexByLabel[label] = groups.length;
        groups.push({ label, items: [] });
      }
      groups[indexByLabel[label]].items.push(b);
    });
    return groups;
  }, [filtered]);

  // Full-width wrapper (not max-w-5xl + mx-auto) so the header sits at
  // a fixed left margin regardless of which view is active. The old
  // wrapper centered a 1024px-wide box in the viewport — empty states
  // and the calendar grid changed page height, which toggled the
  // browser scrollbar and shifted the centered wrapper by the
  // scrollbar's width. Effect: the title visibly jumped on tab switch.
  // `scrollbar-gutter: stable` further insures against scrollbar
  // toggles for browsers that respect it.
  return (
    <div className="px-5 py-3 w-full" style={{ scrollbarGutter: 'stable' }}>
      {/* Compact top strip — title on the left, mode toggle + entity filter
          inline on the right. Replaces the previous stacked layout
          (header → filter card → mode toggle row) which burned ~200px of
          vertical space before the calendar even started. List-mode sub-
          tabs sit in the second strip when active. */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex flex-col sm:flex-row sm:items-center gap-x-2 min-w-0">
          {onBack && (
            <button
              type="button"
              onClick={onBack}
              className="inline-flex items-center gap-1.5 text-xs  text-white shrink-0 mb-1 sm:mb-0 sm:mr-2 self-start"
              style={{ background: '#0F1115', borderRadius: 8, padding: '6px 12px' }}
              onMouseEnter={(e) => (e.currentTarget.style.background = '#1c2128')}
              onMouseLeave={(e) => (e.currentTarget.style.background = '#0F1115')}
            >
              <ChevronLeft className="w-3.5 h-3.5" />
              Back
            </button>
          )}
          <h1 className="text-lg  text-[#2B2926] flex items-center gap-1.5 shrink-0">
            <CalendarIcon className="w-4 h-4 text-[#F55600]" />
            Demo Agent
          </h1>
          {/* Subtitle removed per request. */}
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {/* Calendar / List toggle */}
          <div className="inline-flex items-center rounded-lg border border-[#2B2926]/10 overflow-hidden">
            <button
              type="button"
              onClick={() => setMode('calendar')}
              className={[
                'inline-flex items-center gap-1.5 px-2.5 py-1 text-[11px]  transition-all',
                mode === 'calendar'
                  ? 'bg-[#F55600] text-white'
                  : 'text-[#2B2926] hover:bg-[#F55600]/5',
              ].join(' ')}
            >
              <CalendarDays className="w-3.5 h-3.5" />
              Calendar
            </button>
            <button
              type="button"
              onClick={() => setMode('list')}
              className={[
                'inline-flex items-center gap-1.5 px-2.5 py-1 text-[11px]  transition-all',
                mode === 'list'
                  ? 'bg-[#F55600] text-white'
                  : 'text-[#2B2926] hover:bg-[#F55600]/5',
              ].join(' ')}
            >
              <List className="w-3.5 h-3.5" />
              List
            </button>
          </div>

          {/* Entity filter — pills (no outer card, no FILTER label —
              save vertical space). */}
          <div className="inline-flex items-center gap-1 bg-white rounded-full border border-[#2B2926]/10 p-0.5">
            {[
              { id: 'all',     label: 'All' },
              { id: 'product', label: 'Products' },
              { id: 'service', label: 'Services' },
              // 2026-05-29 — GCC entity type added (matches Dashboard
              // + GTM Journey filter behavior). entity_type='gcc' is
              // already supported by perProduct.filter at line 193.
              { id: 'gcc',     label: 'GCC' },
            ].map((opt) => {
              const active = entityFilter === opt.id;
              return (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => setEntityFilter(opt.id)}
                  className={[
                    'px-2.5 py-1 rounded-full text-[11px]  transition-all',
                    active
                      ? 'bg-[#F55600] text-white'
                      : 'text-[#2B2926] hover:text-[#2B2926]',
                  ].join(' ')}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>

          {entityFilter !== 'all' && (() => {
            const allLabel = `All ${entityFilter === 'service' ? 'services' : entityFilter === 'gcc' ? 'GCC' : 'products'}`;
            return (
            <BrandSelect
              value={selectedProductId != null ? String(selectedProductId) : ''}
              onChange={(v) => setSelectedProductId(v ? Number(v) : null)}
              placeholder={allLabel}
              options={[
                { value: '', label: allLabel },
                ...productOptions.map((p) => ({ value: String(p.id), label: p.name })),
              ]}
            />
            );
          })()}

        </div>
      </div>

      {/* List-mode sub-tabs — only when in list mode */}
      {mode === 'list' && (
        <div className="flex items-center gap-1.5 mt-2">
          {TABS.map((t) => {
            const active = t.id === tab;
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => setTab(t.id)}
                className={[
                  'px-2.5 py-1 text-[11px]  rounded-lg transition',
                  active
                    ? 'bg-[#F55600] text-white'
                    : 'text-[#2B2926] border border-[#2B2926]/10 hover:bg-[#F55600]/5',
                ].join(' ')}
              >
                {t.label}
              </button>
            );
          })}
        </div>
      )}
      <div className="mt-3" />


      {/* Calendar mode short-circuits the loading / list states below */}
      {mode === 'calendar' && !loading && !error && (
        <div className="bg-white border border-[#2B2926]/10 rounded-2xl overflow-hidden">
          <NexusBookingsCalendar
            bookings={bookings}
            onPickBooking={(b) => setSelected(b)}
          />
        </div>
      )}

      <BookingDetailModal
        booking={selected}
        onClose={() => setSelected(null)}
        authAxios={authAxios}
        user={user}
      />

      {/* States — list mode */}
      {mode === 'list' && loading && (
        <div className="bg-white border border-[#2B2926]/10 rounded-2xl p-12 flex items-center justify-center">
          <Loader2 className="w-5 h-5 animate-spin text-[#F55600]" />
          <span className="ml-3 text-sm text-[#2B2926]">Loading bookings…</span>
        </div>
      )}

      {mode === 'list' && !loading && error && (
        <div className="bg-white border border-[#F55600]/30 rounded-2xl p-6 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-[#F55600] mt-0.5" />
          <div className="flex-1">
            <p className="text-sm  text-[#2B2926]">Couldn't load bookings</p>
            <p className="text-xs text-[#2B2926] mt-1">{error}</p>
            <button
              type="button"
              onClick={fetchBookings}
              className="mt-3 text-xs  text-[#F55600] hover:underline"
            >
              Retry
            </button>
          </div>
        </div>
      )}

      {mode === 'list' && !loading && !error && filtered.length === 0 && (
        <div className="bg-white border border-[#2B2926]/10 rounded-2xl p-12 text-center">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-[#F55600]/5 mb-4">
            <CalendarIcon className="w-7 h-7 text-[#F55600]" />
          </div>
          <h2 className="text-xl  text-[#2B2926] mb-2">
            {tab === 'upcoming' ? 'No upcoming demos' : tab === 'past' ? 'No past demos' : 'No demos yet'}
          </h2>
          <p className="text-sm text-[#2B2926] max-w-md mx-auto">
            No demos scheduled yet.
          </p>
        </div>
      )}

      {mode === 'list' && !loading && !error && grouped.length > 0 && (
        <div className="space-y-6">
          {grouped.map((g) => (
            <div key={g.label}>
              <h3 className="text-xs uppercase tracking-wide  text-[#2B2926] mb-2">
                {g.label}
              </h3>
              <div className="space-y-2">
                {g.items.map((b) => (
                  <BookingCard key={b.id} booking={b} onClick={() => setSelected(b)} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

// ─────────────────────────────────────────────────────────────────────────────
// Briefing markdown -> structured sections parser.
//
// Splits the briefing body on `^## ` headings (case-insensitive, multiline)
// and returns an array of { title, body, bullets } where `bullets` holds the
// `- ` items lifted out of the section and `body` is the remaining non-bullet
// prose. Bold markers (**text**) are converted to <strong> at render time.
// ─────────────────────────────────────────────────────────────────────────────

function renderInlineBold(line, keyPrefix) {
  // Strip stray inline `#` markers Gemini sometimes leaves mid-line, then
  // split on **bold** while preserving the matched groups.
  const cleaned = String(line || '').replace(/^#{1,6}\s+/, '');
  const parts = cleaned.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (/^\*\*[^*]+\*\*$/.test(part)) {
      return <strong key={`${keyPrefix}-b-${i}`}>{part.slice(2, -2)}</strong>;
    }
    return <React.Fragment key={`${keyPrefix}-t-${i}`}>{part}</React.Fragment>;
  });
}

// Per-section visual metadata. Keys are tested as case-insensitive substring
// matches against the section title so "How Spenzo Solves BlackRock's Pain"
// still picks up the Pain icon even with interpolation.
const SECTION_META = [
  { match: 'about the person',    Icon: User,           tint: 'from-[#F55600]/10 to-[#F55600]/0' },
  { match: 'about the company',   Icon: Building2,      tint: 'from-[#F55600]/10 to-[#F55600]/0' },
  { match: 'why this product',    Icon: Lightbulb,      tint: 'from-[#F55600]/15 to-[#F55600]/0' },
  { match: 'pain',                Icon: Target,         tint: 'from-[#F55600]/15 to-[#F55600]/0' },
  { match: 'quick-win',           Icon: Zap,            tint: 'from-[#10B981]/15 to-[#10B981]/0' },
  { match: 'use case',            Icon: Zap,            tint: 'from-[#10B981]/15 to-[#10B981]/0' },
  { match: 'talking',             Icon: MessageSquare,  tint: 'from-[#F55600]/10 to-[#F55600]/0' },
  { match: 'objection',           Icon: Shield,         tint: 'from-black/5 to-transparent' },
  { match: 'question',            Icon: HelpCircle,     tint: 'from-[#10B981]/10 to-[#10B981]/0' },
];

function sectionMetaFor(title) {
  const t = (title || '').toLowerCase();
  for (const m of SECTION_META) {
    if (t.includes(m.match)) return m;
  }
  return { Icon: FileText, tint: 'from-black/5 to-transparent' };
}

// Group consecutive body lines into "blocks". A block is either:
//   - { kind: 'sub', label: 'Pain Point', text: '...' }   from `### Pain Point` or `**Pain Point:** text`
//   - { kind: 'p',   text: 'plain paragraph' }
// This lets the renderer style sub-labels without leaking `###` characters.
function partitionBodyBlocks(rawLines) {
  const blocks = [];
  let para = [];
  const flushPara = () => {
    if (para.length === 0) return;
    const text = para.join(' ').trim();
    if (text) blocks.push({ kind: 'p', text });
    para = [];
  };
  for (const raw of rawLines) {
    const line = raw.replace(/\s+$/, '');
    if (!line.trim()) {
      flushPara();
      continue;
    }
    const h3 = line.match(/^#{3,}\s+(.+)$/);
    if (h3) {
      flushPara();
      // `### Pain Point: details here` → label + text, else label-only.
      const rest = h3[1].trim();
      const colonIdx = rest.indexOf(':');
      if (colonIdx > 0 && colonIdx < rest.length - 1) {
        blocks.push({
          kind: 'sub',
          label: rest.slice(0, colonIdx).trim(),
          text: rest.slice(colonIdx + 1).trim(),
        });
      } else {
        blocks.push({ kind: 'sub', label: rest, text: '' });
      }
      continue;
    }
    // `**Pain Point:** details` — promote to sub-label too.
    const labelLine = line.match(/^\*\*([^*]+):\*\*\s*(.*)$/);
    if (labelLine) {
      flushPara();
      blocks.push({
        kind: 'sub',
        label: labelLine[1].trim(),
        text: labelLine[2].trim(),
      });
      continue;
    }
    para.push(line);
  }
  flushPara();
  return blocks;
}

function parseBriefingSections(md) {
  if (!md || typeof md !== 'string') return [];
  // Strip any top-level `# Title` Gemini might leave at the very top.
  const trimmed = md.replace(/^#\s+.*$/m, '').trim();
  const parts = trimmed.split(/^##\s+/im);
  const sections = [];
  for (const part of parts) {
    if (!part || !part.trim()) continue;
    const lines = part.split(/\r?\n/);
    const title = (lines.shift() || '').trim();
    if (!title) continue;
    const bullets = [];
    const bodyRaw = [];
    for (const raw of lines) {
      const line = raw.replace(/\s+$/, '');
      const bulletMatch = line.match(/^\s*[-*]\s+(.+)$/);
      if (bulletMatch) {
        bullets.push(bulletMatch[1].trim());
      } else {
        bodyRaw.push(line);
      }
    }
    const blocks = partitionBodyBlocks(bodyRaw);
    // Skip sections that came back completely empty so we don't render
    // a hollow card.
    if (blocks.length === 0 && bullets.length === 0) continue;
    sections.push({ title, blocks, bullets });
  }
  return sections;
}

// ─────────────────────────────────────────────────────────────────────────────
// Booking detail modal — shown when a booking is clicked anywhere on the page.
// Local state so the top-level Toast component is never asked to render an
// object (the source of the historical blank-screen crash).
// ─────────────────────────────────────────────────────────────────────────────

function BookingDetailModal({ booking, onClose, authAxios, user }) {
  const [briefing, setBriefing] = useState(null);
  const [briefingLoading, setBriefingLoading] = useState(false);
  const [briefingError, setBriefingError] = useState('');
  const [generating, setGenerating] = useState(false);
  const [refinePrompt, setRefinePrompt] = useState('');
  const [refining, setRefining] = useState(false);

  useEffect(() => {
    if (!booking?.id || !authAxios) {
      setBriefing(null);
      setBriefingError('');
      return;
    }
    let cancelled = false;
    setBriefingLoading(true);
    setBriefingError('');
    authAxios
      .get(`/nexus/bookings/${booking.id}/briefing`)
      .then((res) => {
        if (!cancelled) setBriefing(res.data || null);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err?.response?.status === 404) {
          setBriefing({ status: 'no_briefing', briefing_md: '' });
        } else {
          setBriefingError(err?.response?.data?.detail || err.message || 'Failed to load briefing');
        }
      })
      .finally(() => {
        if (!cancelled) setBriefingLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [booking?.id, authAxios]);

  const regenerate = useCallback(async () => {
    if (!booking?.id || !authAxios) return;
    setGenerating(true);
    setBriefingError('');
    try {
      const res = await authAxios.post(`/nexus/bookings/${booking.id}/briefing`);
      setBriefing((prev) => ({
        ...(prev || {}),
        status: res.data?.status || 'ready',
        briefing_md: res.data?.briefing_md || '',
        booking_id: booking.id,
        // Backend echoes the freshly-resolved product on regenerate so
        // the chip in the briefing header updates in place. Without
        // this the chip would keep the stale product_name from the
        // initial GET even when the body regenerated correctly.
        product_name: res.data?.product_name ?? prev?.product_name,
        product_value_proposition:
          res.data?.product_value_proposition ?? prev?.product_value_proposition,
      }));
    } catch (err) {
      setBriefingError(err?.response?.data?.detail || err.message || 'Failed to generate briefing');
    } finally {
      setGenerating(false);
    }
  }, [authAxios, booking?.id]);

  const refine = useCallback(async () => {
    const instruction = refinePrompt.trim();
    if (!booking?.id || !authAxios || !instruction) return;
    setRefining(true);
    setBriefingError('');
    try {
      const res = await authAxios.post(
        `/nexus/bookings/${booking.id}/briefing/refine`,
        { instruction },
      );
      setBriefing((prev) => ({
        ...(prev || {}),
        status: res.data?.status || 'ready',
        briefing_md: res.data?.briefing_md || '',
        booking_id: booking.id,
      }));
      setRefinePrompt('');
    } catch (err) {
      setBriefingError(err?.response?.data?.detail || err.message || 'Failed to refine briefing');
    } finally {
      setRefining(false);
    }
  }, [authAxios, booking?.id, refinePrompt]);

  if (!booking) return null;
  const statusClass = STATUS_BADGE[booking.status] || 'text-[#2B2926] bg-[#2B2926]/5';
  const sourceLabel = SOURCE_LABEL[booking.source] || booking.source || 'unknown';
  const hasBriefing = briefing?.status === 'ready' && (briefing?.briefing_md || '').trim();

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[#2B2926]/50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl border border-[#2B2926]/10 max-w-2xl w-full max-h-[90vh] flex flex-col shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 px-5 py-4 border-b border-[#2B2926]/10">
          <div className="min-w-0">
            <h2 className="text-lg  text-[#2B2926] truncate">
              {booking.attendee_name || booking.attendee_email || 'Unnamed attendee'}
            </h2>
            <div className="flex items-center gap-2 mt-1 flex-wrap">
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px]  border border-[#2B2926]/10 text-[#2B2926]">
                {sourceLabel}
              </span>
              <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px]  ${statusClass}`}>
                {booking.status || 'unknown'}
              </span>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 w-8 h-8 inline-flex items-center justify-center rounded-lg border border-[#2B2926]/10 hover:bg-[#F55600]/5"
            aria-label="Close"
          >
            <X className="w-4 h-4 text-[#2B2926]" />
          </button>
        </div>

        <div className="px-5 py-4 space-y-3 text-sm overflow-y-auto flex-1">
          {booking.attendee_email && (
            <Row icon={<Mail className="w-4 h-4 text-[#2B2926]" />} label="Email" value={booking.attendee_email} />
          )}
          <Row
            icon={<CalendarIcon className="w-4 h-4 text-[#2B2926]" />}
            label="Scheduled"
            value={fmtDateTime(booking.scheduled_at)}
          />
          {booking.end_time && (
            <Row
              icon={<Clock className="w-4 h-4 text-[#2B2926]" />}
              label="Ends"
              value={fmtDateTime(booking.end_time)}
            />
          )}
          {booking.duration_min && (
            <Row
              icon={<Clock className="w-4 h-4 text-[#2B2926]" />}
              label="Duration"
              value={`${booking.duration_min} min`}
            />
          )}
          {booking.lead_id && (
            <Row
              icon={<User className="w-4 h-4 text-[#2B2926]" />}
              label="Lead"
              value={<span className="font-mono">{String(booking.lead_id)}</span>}
            />
          )}
          {/* Pre-call briefing — auto-generated by the sync, regen on demand */}
          <div className="pt-3 mt-3 border-t border-[#2B2926]/10">
            <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
              <div className="inline-flex items-center gap-2 min-w-0">
                <div className="inline-flex items-center gap-1.5 text-[11px] uppercase tracking-wider  text-[#2B2926] shrink-0">
                  <FileText className="w-3.5 h-3.5 text-[#F55600]" />
                  Pre-call briefing
                </div>
                {briefing?.product_name && (
                  <span
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-[#F55600]/10 text-[#F55600] text-[10px]  border border-[#F55600]/20 min-w-0"
                    title={briefing.product_value_proposition || briefing.product_name}
                  >
                    <Lightbulb className="w-3 h-3 shrink-0" />
                    <span className="truncate max-w-[180px]">{briefing.product_name}</span>
                  </span>
                )}
              </div>
              {(hasBriefing || briefing?.status === 'no_briefing') && !isReadOnly(user) && (
                <button
                  type="button"
                  onClick={regenerate}
                  disabled={generating}
                  className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg border border-[#2B2926]/10 text-[11px]  text-[#2B2926] hover:bg-[#F55600]/5 disabled:opacity-50"
                >
                  {generating ? (
                    <Loader2 className="w-3 h-3 animate-spin" />
                  ) : hasBriefing ? (
                    <RefreshCw className="w-3 h-3" />
                  ) : (
                    <Sparkles className="w-3 h-3 text-[#F55600]" />
                  )}
                  {hasBriefing ? 'Regenerate' : generating ? 'Generating…' : 'Generate'}
                </button>
              )}
            </div>

            {briefingLoading && (
              <div className="text-xs text-[#2B2926] inline-flex items-center gap-1.5">
                <Loader2 className="w-3 h-3 animate-spin" />
                Loading briefing…
              </div>
            )}
            {briefingError && (
              <div className="text-xs text-[#F55600]">{briefingError}</div>
            )}
            {!briefingLoading && !briefingError && hasBriefing && (
              <div className="space-y-3">
                {parseBriefingSections(briefing.briefing_md).map((section, idx) => {
                  const meta = sectionMetaFor(section.title);
                  const SectionIcon = meta.Icon;
                  return (
                    <div
                      key={`briefing-section-${idx}`}
                      className="group relative border border-[#2B2926]/10 rounded-2xl bg-white overflow-hidden hover:border-[#F55600]/30 transition-colors"
                    >
                      {/* gradient header strip — pure Tailwind, palette-safe */}
                      <div className={`absolute inset-x-0 top-0 h-12 bg-gradient-to-b ${meta.tint} pointer-events-none`} />
                      <div className="relative px-4 pt-3.5 pb-3">
                        <div className="flex items-center gap-2.5 mb-2.5">
                          <div className="w-8 h-8 rounded-lg bg-white border border-[#2B2926]/10 inline-flex items-center justify-center shrink-0 shadow-sm">
                            <SectionIcon className="w-4 h-4 text-[#F55600]" />
                          </div>
                          <h4 className=" text-[#2B2926] text-[14px] leading-tight tracking-tight">
                            {section.title}
                          </h4>
                        </div>

                        {section.blocks.length > 0 && (
                          <div className="space-y-2">
                            {section.blocks.map((blk, bi) =>
                              blk.kind === 'sub' ? (
                                <div
                                  key={`s${idx}-blk-${bi}`}
                                  className="rounded-lg border border-[#2B2926]/5 bg-[#2B2926]/[0.02] px-3 py-2"
                                >
                                  <div className="text-[10px] uppercase tracking-wider  text-[#F55600] mb-0.5">
                                    {blk.label}
                                  </div>
                                  {blk.text && (
                                    <div className="text-[13px] leading-relaxed text-[#2B2926]">
                                      {renderInlineBold(blk.text, `s${idx}-st-${bi}`)}
                                    </div>
                                  )}
                                </div>
                              ) : (
                                <p
                                  key={`s${idx}-blk-${bi}`}
                                  className="text-[13px] leading-relaxed text-[#2B2926]"
                                >
                                  {renderInlineBold(blk.text, `s${idx}-pt-${bi}`)}
                                </p>
                              ),
                            )}
                          </div>
                        )}

                        {section.bullets.length > 0 && (
                          <ul className="mt-3 space-y-1.5 list-none pl-0">
                            {section.bullets.map((bullet, bi) => (
                              <li
                                key={`s${idx}-bul-${bi}`}
                                className="flex items-start gap-2.5 text-[13px] leading-relaxed text-[#2B2926]"
                              >
                                <span className="mt-[7px] inline-block w-1.5 h-1.5 rounded-full bg-[#F55600] shrink-0" />
                                <span className="min-w-0 flex-1">
                                  {renderInlineBold(bullet, `s${idx}-bt-${bi}`)}
                                </span>
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
            {!briefingLoading && !briefingError && !hasBriefing && briefing?.status === 'no_briefing' && (
              <div className="text-xs text-[#2B2926] italic">
                No briefing yet. Click <span className="">Generate</span> to draft one now —
                the background sync will also auto-generate within ~5 minutes of a new booking.
              </div>
            )}

            {hasBriefing && !isReadOnly(user) && (
              <div className="mt-3 pt-3 border-t border-[#2B2926]/5">
                <label className="block text-[10px] uppercase tracking-wider  text-[#2B2926] mb-1.5">
                  Refine — tell the model what to change
                </label>
                <div className="flex items-start gap-2">
                  <textarea
                    value={refinePrompt}
                    onChange={(e) => setRefinePrompt(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                        e.preventDefault();
                        refine();
                      }
                    }}
                    placeholder="e.g. make this more detailed, add a competitor comparison, shorten the objections section"
                    rows={2}
                    className="flex-1 text-[12px] border border-[#2B2926]/10 rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-[#F55600] resize-none"
                    disabled={refining}
                  />
                  <button
                    type="button"
                    onClick={refine}
                    disabled={refining || !refinePrompt.trim()}
                    className="shrink-0 inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-[#F55600] text-white text-[11px]  hover:bg-[#F55600]/90 disabled:opacity-50"
                  >
                    {refining ? (
                      <Loader2 className="w-3 h-3 animate-spin" />
                    ) : (
                      <Sparkles className="w-3 h-3" />
                    )}
                    {refining ? 'Refining…' : 'Refine'}
                  </button>
                </div>
                <p className="mt-1 text-[10px] text-[#2B2926]">
                  Ctrl/Cmd+Enter to submit. The model rewrites the briefing in place — prior
                  versions are kept in the booking's refine history (last 10).
                </p>
              </div>
            )}
          </div>

          <MeetingAgendaPanel booking={booking} authAxios={authAxios} />
        </div>
      </div>
    </div>
  );
}

function Row({ icon, label, value }) {
  return (
    <div className="flex items-start gap-2">
      <span className="mt-0.5">{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="text-[10px] uppercase tracking-wider  text-[#2B2926]">{label}</div>
        <div className="text-[#2B2926] break-words">{value}</div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Booking card
// ─────────────────────────────────────────────────────────────────────────────

function BookingCard({ booking, onClick }) {
  const statusClass = STATUS_BADGE[booking.status] || 'text-[#2B2926] bg-[#2B2926]/5';
  const sourceLabel = SOURCE_LABEL[booking.source] || booking.source || 'unknown';

  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full text-left bg-white border border-[#2B2926]/10 rounded-xl p-4 hover:border-[#F55600]/30 transition"
    >
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <User className="w-4 h-4 text-[#2B2926] shrink-0" />
            <span className=" text-[#2B2926] text-sm truncate">
              {booking.attendee_name || booking.attendee_email || 'Unnamed attendee'}
            </span>
            {booking.attendee_email && booking.attendee_name && (
              <span className="inline-flex items-center gap-1 text-xs text-[#2B2926]">
                <Mail className="w-3 h-3" />
                {booking.attendee_email}
              </span>
            )}
          </div>

          <div className="flex items-center gap-3 mt-2 text-xs text-[#2B2926] flex-wrap">
            <span className="inline-flex items-center gap-1">
              <CalendarIcon className="w-3.5 h-3.5" />
              {fmtDateTime(booking.scheduled_at)}
            </span>
            {booking.duration_min ? (
              <span className="inline-flex items-center gap-1">
                <Clock className="w-3.5 h-3.5" />
                {booking.duration_min} min
              </span>
            ) : null}
            {booking.lead_id && (
              <span className="inline-flex items-center gap-1 text-[#2B2926]">
                lead <span className="font-mono">{String(booking.lead_id).slice(0, 8)}</span>
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px]  border border-[#2B2926]/10 text-[#2B2926]">
            {sourceLabel}
          </span>
          <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px]  ${statusClass}`}>
            {booking.status || 'unknown'}
          </span>
        </div>
      </div>
    </button>
  );
}

export default NexusBookings;
