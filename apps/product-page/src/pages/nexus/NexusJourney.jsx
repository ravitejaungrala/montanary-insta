/**
 * NexusJourney — port of legacy `apps/nexus-legacy/client/src/components/GTMJourney.jsx`.
 *
 * Three-column workspace:
 *   ┌─────────────┬──────────────────────┬─────────────┐
 *   │ Lead List   │ Timeline             │ Right pane  │
 *   │ + search    │ (selected lead's     │ (Outreach   │
 *   │ + view tabs │  full chronological  │  Flow OR    │
 *   │ + product   │  journey)            │  Demo Brief)│
 *   │   filter    │                      │             │
 *   └─────────────┴──────────────────────┴─────────────┘
 *
 * State shape, variable names, and API call sequence match legacy 1:1
 * so behavior is identical. Only the palette is changed to PIPELYT's
 * mandatory colors (#F55600, #10B981, black, white) per CLAUDE.md.
 *
 * Children (in same folder):
 *   - JourneyTimeline.jsx   (F2 — timeline event renderers)
 *   - OutreachFlowPanel.jsx (F3 — right-pane outreach flow diagram)
 *   - DemoBriefingPanel.jsx (F4 — right-pane demo briefing)
 */
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';
import {
  AlertCircle,
  Briefcase,
  Building2,
  Check,
  ChevronLeft,
  Copy,
  Eye,
  EyeOff,
  Inbox,
  Linkedin,
  Loader2,
  Mail,
  Mailbox,
  Phone,
  RefreshCw,
  Reply,
  Search,
  Shield,
  Target,
  Upload,
  Users,
  X,
  Zap,
} from 'lucide-react';
import JourneyTimeline from './JourneyTimeline';
import OutreachFlowPanel from './OutreachFlowPanel';
import DemoBriefingPanel from './DemoBriefingPanel';
import FilterSidebar from './FilterSidebar';
import useCreditBalance from './useCreditBalance';
import CreditsBadge from './CreditsBadge';
import BuyCreditsModal from './BuyCreditsModal';
import CreditWarningPopup from './CreditWarningPopup';
import { canManageBilling, isReadOnly } from '../../lib/permissions';

// ── Utility helpers (ported from legacy) ───────────────────────────────────

// Backend serializes naive datetimes (TIMESTAMP WITHOUT TIME ZONE
// columns) as ISO strings WITHOUT a 'Z' suffix or offset, e.g.
// "2026-05-24T12:07:35". JavaScript's `new Date()` interprets such
// strings as LOCAL time, which is wrong — these are stored as UTC on
// the server. For an operator in IST (UTC+5:30) a touchpoint created
// 5 minutes ago renders as "5h ago" without this normalization.
// Strings that already carry a Z or +HH:MM offset pass through unchanged.
const _normaliseIsoToUtc = (iso) => {
  if (typeof iso !== 'string') return iso;
  if (/[Zz]$|[+-]\d{2}:?\d{2}$/.test(iso)) return iso;
  return iso + 'Z';
};

export const fmtRelative = (iso) => {
  if (!iso) return '—';
  try {
    const then = new Date(_normaliseIsoToUtc(iso)).getTime();
    const diff = Math.max(0, Date.now() - then);
    const s = Math.floor(diff / 1000);
    if (s < 30) return 'just now';
    const m = Math.floor(diff / 60000);
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    const d = Math.floor(h / 24);
    if (d < 30) return `${d}d ago`;
    // Past the 30-day relative window we render an absolute date. Use
    // the strict DD/MM/YYYY formatter so the timeline matches the rest
    // of the app (OutreachFlowPanel etc.) instead of falling back to
    // `toLocaleDateString()` whose output varies per browser locale.
    return fmtDateShort(iso);
  } catch {
    return '—';
  }
};

export const fmtDate = (iso) => {
  if (!iso) return '—';
  try {
    return new Date(_normaliseIsoToUtc(iso)).toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
};

// Short absolute date in YYYY-MM-DD form — matches the lead-table "CREATED
// DATE" column so every date in the workspace reads the same way. Used in
// the lead header ("Last contacted") and under stage labels in the
// OutreachFlowPanel. Manually slice the ISO date so the output is
// locale-independent (toLocaleDateString varies per browser).
export const fmtDateShort = (iso) => {
  if (!iso) return '—';
  try {
    const d = new Date(_normaliseIsoToUtc(iso));
    if (Number.isNaN(d.getTime())) return '—';
    const dd = String(d.getDate()).padStart(2, '0');
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const yyyy = d.getFullYear();
    return `${yyyy}-${mm}-${dd}`;
  } catch {
    return '—';
  }
};

const STATUS_PILL = {
  new: 'bg-[#F55600]/10 text-[#F55600]',
  // 'queued' = enrolled in a sequence, waiting for the next tick to fire
  // the first email. Distinct from 'new' (just discovered, not yet enrolled).
  // Visually softer than 'new' so the operator's eye is drawn to the
  // actionable end (replied / demo_scheduled) rather than in-flight rows.
  queued: 'bg-[#F55600]/5 text-[#F55600]/80',
  contacted: 'bg-[#2B2926]/5 text-[#2B2926]',
  replied: 'bg-[#10B981]/10 text-[#10B981]',
  demo_scheduled: 'bg-[#10B981]/15 text-[#10B981]',
  bounced: 'bg-[#2B2926]/5 text-[#2B2926]',
  unsubscribed: 'bg-[#2B2926]/5 text-[#2B2926]',
};

const dominantChannel = (counts = {}) => {
  const e = counts.email || 0;
  const l = counts.linkedin || 0;
  const v = counts.voice || 0;
  if (e >= l && e >= v) return 'email';
  if (l >= v) return 'linkedin';
  return 'voice';
};

const leadInitials = (lead) => {
  const n = (lead.name || '').trim();
  if (n) {
    const parts = n.split(/\s+/);
    return ((parts[0] || '').charAt(0) + (parts[1] || '').charAt(0)).toUpperCase();
  }
  return (lead.email || '?').charAt(0).toUpperCase();
};

// ── Lead card (left list) ──────────────────────────────────────────────────

const LeadCard = ({ lead, selected, onClick }) => {
  const dom = dominantChannel(lead.channel_attempts);
  const status = lead.status || 'new';
  const isHidden = (lead.priority_state || 'active') === 'hidden';
  return (
    <button
      type="button"
      onClick={onClick}
      className={[
        'w-full text-left px-3 py-2.5 rounded-lg border transition-all',
        selected
          ? 'bg-[#F55600]/5 border-[#F55600] shadow-sm'
          : isHidden
          ? 'bg-[#2B2926]/[0.02] border-[#2B2926]/10 hover:bg-[#F55600]/5 opacity-70'
          : 'bg-white border-[#2B2926]/10 hover:bg-[#F55600]/5',
      ].join(' ')}
    >
      <div className="flex items-start gap-2.5">
        <div
          className={[
            'w-8 h-8 shrink-0 rounded-lg flex items-center justify-center text-[10px] font-bold',
            isHidden
              ? 'bg-[#2B2926]/10 text-[#2B2926]'
              : status === 'replied' || status === 'demo_scheduled'
              ? 'bg-[#10B981]/15 text-[#10B981]'
              : 'bg-[#F55600]/10 text-[#F55600]',
          ].join(' ')}
        >
          {leadInitials(lead)}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-bold text-[#2B2926] truncate inline-flex items-center gap-1.5 min-w-0">
              <span
                className="truncate"
                style={lead.source === 'referral' ? { color: '#DC2626' } : undefined}
                title={lead.source === 'referral' ? 'Referral lead — sourced from another lead’s reply' : undefined}
              >
                {lead.name || lead.email || 'Unknown'}
              </span>
              {isHidden && (
                <span className="shrink-0 text-[9px] font-bold uppercase tracking-wider text-[#2B2926] italic">
                  (Hidden)
                </span>
              )}
            </span>
            <span
              className={[
                'shrink-0 inline-block w-1.5 h-1.5 rounded-full',
                dom === 'email' ? 'bg-[#F55600]' : dom === 'linkedin' ? 'bg-[#2B2926]' : 'bg-[#10B981]',
              ].join(' ')}
              title={`Last channel: ${dom}`}
            />
          </div>
          <div className="text-[10px] text-[#2B2926] truncate mt-0.5">
            {lead.job_title ? `${lead.job_title} · ` : ''}
            {lead.company || lead.company_domain || '—'}
          </div>
          <div className="flex items-center justify-between gap-2 mt-1.5">
            <div className="flex items-center gap-1 min-w-0">
              <span
                className={[
                  'inline-flex px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider shrink-0',
                  STATUS_PILL[status] || 'bg-[#2B2926]/5 text-[#2B2926]',
                ].join(' ')}
              >
                {status.replace('_', ' ')}
              </span>
              {lead.product_name && (
                <span
                  className={[
                    'inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider truncate',
                    // Product type tints: orange for products, black-tinted for services.
                    lead.product_entity_type === 'service'
                      ? 'bg-[#2B2926]/[0.04] text-[#2B2926] border border-[#2B2926]/10'
                      : 'bg-[#F55600]/8 text-[#F55600] border border-[#F55600]/20',
                  ].join(' ')}
                  title={`Campaign product: ${lead.product_name}`}
                >
                  {lead.product_name}
                </span>
              )}
            </div>
            <span className="text-[10px] text-[#2B2926] shrink-0">
              {fmtRelative(lead.last_attempt_at || lead.enrolled_at)}
            </span>
          </div>
        </div>
      </div>
    </button>
  );
};

// ── Lead row (table) ────────────────────────────────────────────────────────
// Helpers shared with the new table layout. Kept inside this file so the
// component stays a single drop-in unit.

const splitName = (lead) => {
  const n = (lead?.name || '').trim();
  if (!n) return { first: lead?.email || '—', last: '' };
  const parts = n.split(/\s+/);
  if (parts.length === 1) return { first: parts[0], last: '' };
  return { first: parts[0], last: parts.slice(1).join(' ') };
};

const linkedinHandle = (url) => {
  if (!url) return '';
  const m = String(url).match(/linkedin\.com\/(?:in|pub|company)\/([^/?#]+)/i);
  return m ? m[1] : url;
};

// One row in the spreadsheet-style lead table. Click anywhere on the
// row (except the LinkedIn link) to open the lead's detail overlay.
// Match-score pill — single green colour regardless of score.
// Per request the pill always uses pure green `#008000` for the
// foreground, with a soft tint of the same green at 14% alpha for
// the background. (Original 3-tier ramp #047857 / #10B981 / #34D399
// removed — every row now reads identically.)
const _matchTier = () => ({ fg: '#008000', bg: 'rgba(0,128,0,0.14)' });

const LeadRow = ({ lead, index, selected, onSelect }) => {
  // Click handler is built fresh each LeadRow render, BUT LeadRow only
  // renders when its memoised props actually change (see LeadRowMemo
  // below). The parent passes a stable `onSelect` so its identity
  // doesn't bust memo, and the closure here just glues this row's id
  // to that stable callback.
  const onClick = () => onSelect && onSelect(lead._id, lead.campaign_id);
  const { first, last } = splitName(lead);
  const name = `${first || ''} ${last || ''}`.trim() || lead.email || 'Unknown';
  const isHidden = (lead.priority_state || 'active') === 'hidden';
  const ch = lead.channel_attempts || {};
  const emails = ch.email || 0;
  const linkedinTouches = ch.linkedin || 0;
  // All captured numbers (scenario 9). `phone` is the primary click-to-dial;
  // any extras are surfaced as a subtle "+N" badge next to it.
  const phones = Array.isArray(lead.phones) ? lead.phones.filter(Boolean) : [];
  const phone = lead.phone || lead.phone_number || lead.mobile || phones[0] || '';
  const extraPhoneCount = Math.max(0, (phones.length || (phone ? 1 : 0)) - 1);
  // Click the "+N" badge to open a popover listing every number; it STAYS open
  // until a click lands outside it (or on the badge again). Rendered in a
  // PORTAL with fixed positioning so the table's scroll/overflow container
  // can't clip it (an absolutely-positioned popover inside the cell was
  // invisible — clipped by the horizontal-scroll wrapper).
  const [phonesOpen, setPhonesOpen] = useState(false);
  const [popPos, setPopPos] = useState(null); // { top, right } in viewport coords
  const phonesRef = useRef(null);              // the badge/anchor
  const popRef = useRef(null);                 // the portal popover
  useEffect(() => {
    if (!phonesOpen) return undefined;
    const onDocMouseDown = (e) => {
      const inAnchor = phonesRef.current && phonesRef.current.contains(e.target);
      const inPop = popRef.current && popRef.current.contains(e.target);
      if (!inAnchor && !inPop) setPhonesOpen(false);
    };
    // Repositioning a fixed popover while the page scrolls is fiddly; just
    // close it on scroll/resize (standard popover behaviour).
    const onScrollOrResize = () => setPhonesOpen(false);
    document.addEventListener('mousedown', onDocMouseDown);
    window.addEventListener('resize', onScrollOrResize);
    window.addEventListener('scroll', onScrollOrResize, true);
    return () => {
      document.removeEventListener('mousedown', onDocMouseDown);
      window.removeEventListener('resize', onScrollOrResize);
      window.removeEventListener('scroll', onScrollOrResize, true);
    };
  }, [phonesOpen]);
  const togglePhones = (e) => {
    e.stopPropagation();
    if (phonesOpen) { setPhonesOpen(false); return; }
    const r = e.currentTarget.getBoundingClientRect();
    // Anchor the popover's TOP-RIGHT just under the badge; using `right`
    // avoids needing to know the popover's width up front.
    setPopPos({ top: r.bottom + 6, right: Math.max(8, window.innerWidth - r.right) });
    setPhonesOpen(true);
  };
  const tier = _matchTier(lead.icp_score);
  const cellBorder = '1px solid #E3E5E8';

  return (
    <tr
      className=""
      style={{
        // Very-light grey for both selected + hover instead of the
        // previous orange tint (removed per single-#F55600 spec) was
        // popping out too strongly when the user moused over the
        // table. Neutral greys read as a soft "current row" cue
        // without competing with the orange brand accents elsewhere.
        background: selected ? '#F4F2EE' : 'transparent',
        opacity: isHidden ? 0.7 : 1,
        transition: 'background 0.12s',
      }}
      onMouseEnter={(e) => {
        if (!selected) e.currentTarget.style.background = '#F5F5F6';
      }}
      onMouseLeave={(e) => {
        if (!selected) e.currentTarget.style.background = 'transparent';
      }}
    >
      {/* # */}
      <td style={{ padding: '13px 10px', borderTop: cellBorder, color: '#67655E', fontVariantNumeric: 'tabular-nums', fontSize: 13, fontWeight: 500 }}>
        {index + 1}
      </td>
      {/* Campaign (product) pill */}
      <td style={{ padding: '13px 10px', borderTop: cellBorder }}>
        {lead.product_name ? (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              padding: '3px 10px',
              borderRadius: 6,
              fontSize: 10.5,
              fontWeight: 700,
              letterSpacing: '0.4px',
              background: 'rgba(43,41,38,0.05)',
              color: '#2B2926',
              border: '1px solid rgba(43,41,38,0.15)',
              backdropFilter: 'blur(6px)',
              WebkitBackdropFilter: 'blur(6px)',
              textTransform: 'uppercase',
              whiteSpace: 'nowrap',
            }}
            title={`Campaign product: ${lead.product_name}`}
          >
            {lead.product_name}
          </span>
        ) : (
          <span style={{ color: '#A6A39B' }}>—</span>
        )}
      </td>
      {/* Campaign ID — per-product campaign number (restarts at 1 per product). */}
      <td
        style={{ padding: '13px 10px', borderTop: cellBorder, color: '#2B2926', fontSize: 13, whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}
      >
        {lead.campaign_number != null ? lead.campaign_number : '—'}
      </td>
      {/* Created date — the campaign's creation date as YYYY-MM-DD (e.g. 2026-06-09). */}
      <td
        style={{ padding: '13px 10px', borderTop: cellBorder, color: '#2B2926', fontSize: 13, whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}
        title={(lead.campaign_created_at || '').slice(0, 10)}
      >
        {(() => {
          const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(lead.campaign_created_at || '');
          return m ? `${m[1]}-${m[2]}-${m[3]}` : '—';
        })()}
      </td>
      {/* Match pill — blank (em-dash) for BYO leads uploaded without a
          "Match Score" column (icp_score is null); scored leads get the pill. */}
      <td style={{ padding: '13px 10px', borderTop: cellBorder }}>
        {lead.icp_score === null || lead.icp_score === undefined ? (
          <span style={{ color: '#9AA0AA' }}>—</span>
        ) : (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              minWidth: 44,
              padding: '4px 12px',
              borderRadius: 999,
              fontSize: 12.5,
              fontWeight: 700,
              fontVariantNumeric: 'tabular-nums',
              background: tier.bg,
              color: tier.fg,
            }}
          >
            {Number(lead.icp_score)}
          </span>
        )}
      </td>
      {/* Name link — hyperlink styled, opens the lead detail overlay */}
      <td style={{ padding: '13px 10px', borderTop: cellBorder, overflow: 'hidden' }}>
        <a
          onClick={onClick}
          className="nx-name-link truncate"
          style={{
            color: lead.source === 'referral' ? '#DC2626' : '#2B2926',
            fontWeight: 600,
            fontSize: 13.5,
            display: 'inline-block',
            maxWidth: '100%',
            verticalAlign: 'bottom',
            cursor: 'pointer',
            textDecoration: 'none',
            borderBottom: '1px solid transparent',
          }}
          title={lead.source === 'referral' ? `${name} — referral lead` : name}
        >
          {name}
          {isHidden && (
            <span style={{ marginLeft: 6, fontSize: 9, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: '#A6A39B', fontStyle: 'italic' }}>
              (Hidden)
            </span>
          )}
        </a>
      </td>
      {/* Title */}
      <td
        style={{ padding: '13px 10px', borderTop: cellBorder, color: '#2B2926', fontSize: 13.5, whiteSpace: 'nowrap' }}
        title={lead.job_title || ''}
      >
        {lead.job_title || '—'}
      </td>
      {/* Company */}
      <td
        style={{ padding: '13px 10px', borderTop: cellBorder, color: '#2B2926', fontSize: 13.5, whiteSpace: 'nowrap' }}
        title={lead.company || lead.company_domain || ''}
      >
        {lead.company || lead.company_domain || '—'}
      </td>
      {/* LinkedIn — blue icon only */}
      <td style={{ padding: '13px 10px', borderTop: cellBorder }}>
        {lead.linkedin_url ? (
          <a
            href={lead.linkedin_url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="inline-grid place-items-center hover:bg-[#0A66C2]/10"
            style={{ width: 26, height: 26, borderRadius: 6, color: '#0A66C2' }}
            title="View LinkedIn"
          >
            <Linkedin className="w-4 h-4" />
          </a>
        ) : (
          <span style={{ color: '#A6A39B' }}>—</span>
        )}
      </td>
      {/* Email */}
      <td
        style={{ padding: '13px 10px', borderTop: cellBorder, color: '#2B2926', fontSize: 13, whiteSpace: 'nowrap' }}
        title={lead.email || ''}
      >
        {lead.email || '—'}
      </td>
      {/* Touches — hyperlinked email + linkedin counts */}
      <td style={{ padding: '13px 10px', borderTop: cellBorder }}>
        <span className="inline-flex items-center" style={{ gap: 16, color: '#2B2926', fontVariantNumeric: 'tabular-nums' }}>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onSelect && onSelect(lead._id, lead.campaign_id, 'email'); }}
            className="nx-touch-link inline-flex items-center"
            style={{ gap: 6, fontSize: 12.5, color: '#0078D4', background: 'none', border: 0, padding: 0, cursor: 'pointer' }}
            title={`Open email outreach (${emails} touch${emails === 1 ? '' : 'es'})`}
          >
            {/* Single mailbox mark — a green envelope with a check badge
                (matches the connected-mailbox icon used elsewhere) instead
                of two overlapping provider logos. */}
            <svg
              width="17"
              height="16"
              viewBox="0 0 24 22"
              aria-hidden="true"
              style={{ flexShrink: 0 }}
            >
              {/* envelope */}
              <rect x="3" y="5" width="18" height="14" rx="2.5" fill="#ffffff" stroke="#2F7DE1" strokeWidth="1.6" />
              <path d="M4.5 7.5 L12 13 L19.5 7.5" fill="none" stroke="#2F7DE1" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              {/* blue check badge, top-left */}
              <circle cx="6" cy="6" r="5" fill="#2563EB" stroke="#ffffff" strokeWidth="1.4" />
              <path d="M3.8 6.1 L5.3 7.6 L8.2 4.4" fill="none" stroke="#ffffff" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            {emails}
          </button>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onSelect && onSelect(lead._id, lead.campaign_id, 'linkedin'); }}
            className="nx-touch-link inline-flex items-center"
            style={{ gap: 6, fontSize: 12.5, color: '#0A66C2', background: 'none', border: 0, padding: 0, cursor: 'pointer' }}
            title={`Open LinkedIn outreach (${linkedinTouches} touch${linkedinTouches === 1 ? '' : 'es'})`}
          >
            <Linkedin className="w-3 h-3" style={{ color: '#0A66C2' }} />
            {linkedinTouches}
          </button>
        </span>
      </td>
      {/* Location — joined city/state/country from the backend
          (journey.py builds it from person_city/state/country). Empty
          when Apollo didn't return any of those for the lead. */}
      <td
        style={{ padding: '13px 10px', borderTop: cellBorder, color: '#2B2926', fontSize: 13, whiteSpace: 'normal', lineHeight: 1.35 }}
        title={lead.location || ''}
      >
        {lead.location || '—'}
      </td>
      {/* Contact — LAST column. Primary phone (click-to-dial) + a subtle "+N"
          badge when the lead has more numbers on file, else — */}
      <td style={{ padding: '13px 10px', borderTop: cellBorder }}>
        {phone ? (
          <span ref={phonesRef} className="inline-flex items-center" style={{ position: 'relative' }}>
            <a
              href={`tel:${phone}`}
              onClick={(e) => e.stopPropagation()}
              className="inline-flex items-center gap-1.5 hover:underline"
              style={{ color: '#10B981', fontVariantNumeric: 'tabular-nums' }}
              title={`Call ${name}`}
            >
              <Phone className="w-[15px] h-[15px] shrink-0" />
              {phone}
            </a>
            {extraPhoneCount > 0 && (
              <>
                <span
                  role="button"
                  tabIndex={0}
                  onClick={togglePhones}
                  title="Show all numbers"
                  style={{
                    marginLeft: 6,
                    padding: '1px 6px',
                    borderRadius: 999,
                    fontSize: 11,
                    fontWeight: 600,
                    background: phonesOpen ? '#10B981' : '#ECFDF5',
                    color: phonesOpen ? '#FFFFFF' : '#10B981',
                    fontVariantNumeric: 'tabular-nums',
                    cursor: 'pointer',
                  }}
                >
                  +{extraPhoneCount}
                </span>
                {phonesOpen && popPos && createPortal(
                  <div
                    ref={popRef}
                    onClick={(e) => e.stopPropagation()}
                    style={{
                      position: 'fixed',
                      top: popPos.top,
                      right: popPos.right,
                      zIndex: 9999,
                      minWidth: 190,
                      background: '#FFFFFF',
                      border: '1px solid #E3E5E8',
                      borderRadius: 8,
                      boxShadow: '0 8px 24px rgba(0,0,0,0.16)',
                      padding: 8,
                      textAlign: 'left',
                    }}
                  >
                    <div style={{ fontSize: 11, fontWeight: 600, color: '#6B7280', padding: '2px 6px 6px' }}>
                      Phone numbers ({phones.length})
                    </div>
                    {phones.map((p, i) => (
                      <a
                        key={`${p}-${i}`}
                        href={`tel:${p}`}
                        onClick={(e) => e.stopPropagation()}
                        className="flex items-center gap-1.5 hover:underline"
                        style={{
                          color: '#10B981',
                          fontSize: 13,
                          padding: '5px 6px',
                          fontVariantNumeric: 'tabular-nums',
                          textDecoration: 'none',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        <Phone className="w-[13px] h-[13px] shrink-0" />
                        {p}
                      </a>
                    ))}
                  </div>,
                  document.body,
                )}
              </>
            )}
          </span>
        ) : (
          <span style={{ color: '#A6A39B' }}>—</span>
        )}
      </td>
    </tr>
  );
};

// React.memo wrapper for LeadRow. With 500+ rows in the GTM table,
// every parent state tick (filter hover, search keystroke, etc.) was
// re-rendering all 500 rows. The custom comparator below skips a row
// when neither its `lead._id`, its row index, nor its selected state
// changed — and we pass a stable id-based `onSelect` from the parent
// so the function-prop identity doesn't bust the memo every render.
const LeadRowMemo = React.memo(
  LeadRow,
  (prev, next) =>
    prev.lead === next.lead &&
    prev.index === next.index &&
    prev.selected === next.selected &&
    prev.onSelect === next.onSelect,
);

// Horizontal pill row used by the Products/Services filter bar.
// Extracted out of the parent so React's render path is straightforward
// and selectedId changes don't recompute the whole journey component.
const FilterRow = ({ label, items, accent, selectedId, onSelect }) => (
  <div className="flex items-center gap-2 overflow-x-auto py-1">
    <span className="shrink-0 text-[9px] uppercase tracking-wider text-[#2B2926] font-bold pr-1">
      {label}
    </span>
    <button
      type="button"
      onClick={() => onSelect('')}
      className={[
        'shrink-0 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-bold transition-all',
        selectedId === ''
          ? `${accent} text-white`
          : 'bg-white text-[#2B2926] border border-[#2B2926]/10 hover:bg-[#F55600]/5',
      ].join(' ')}
    >
      All
    </button>
    {items.map((p) => {
      const pid = String(p.id || p._id);
      const active = pid === selectedId;
      return (
        <button
          key={pid}
          type="button"
          onClick={() => onSelect(pid)}
          className={[
            'shrink-0 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-bold transition-all',
            active
              ? `${accent} text-white`
              : 'bg-white text-[#2B2926] border border-[#2B2926]/10 hover:bg-[#F55600]/5',
          ].join(' ')}
          title={p.name}
        >
          <Briefcase className="w-3 h-3" />
          {p.name || 'Unnamed'}
        </button>
      );
    })}
  </div>
);

const SkeletonCard = () => (
  <div className="px-3 py-2.5 rounded-lg border border-[#2B2926]/10 bg-white animate-pulse">
    <div className="flex items-start gap-2.5">
      <div className="w-8 h-8 rounded-lg bg-[#2B2926]/5" />
      <div className="flex-1">
        <div className="h-3 bg-[#2B2926]/5 rounded w-3/4" />
        <div className="h-2 bg-[#2B2926]/5 rounded w-1/2 mt-2" />
        <div className="h-2 bg-[#2B2926]/5 rounded w-1/3 mt-2" />
      </div>
    </div>
  </div>
);

// ── Lead header (above timeline) ───────────────────────────────────────────

const LeadHeader = ({ lead, timeline, linkedinTouches: linkedinTouchesProp }) => {
  if (!lead) return null;
  const status = lead.status || 'new';
  const hidden = (lead.priority_state || 'active') === 'hidden';
  const verified =
    lead.email_verify_status === 'verified' || lead.email_verified === true;
  const likely = lead.email_verify_status === 'likely to engage';
  const positive = status === 'replied' || status === 'demo_scheduled';
  // Compute stat tiles once — reused by the top-right rail.
  // Match the TOUCHES column exactly so the header and the table row never
  // disagree:
  //   • Skip the synthetic placeholder events the detail API injects for a
  //     lead with no real email yet (status 'unavailable', or an empty
  //     'queued' projection with no subject/body) — those aren't real touches.
  //   • Count DISTINCT LinkedIn variants (DM + InMail, max 2), like the
  //     table's LinkedIn query, so duplicate draft rows don't inflate it.
  let emailCount = 0;
  const _liVariants = new Set();
  // "Last contacted" = the most recent REAL outbound send or reply, derived
  // from the timeline (the stored last_contacted_at is sometimes not stamped).
  let lastContactedAt = null;
  (timeline || []).forEach((t) => {
    const type = (t.type || '').toLowerCase();
    const st = (t.status || '').toLowerCase();
    if ((st === 'sent' || st === 'replied') && t.occurred_at) {
      if (!lastContactedAt || new Date(t.occurred_at) > new Date(lastContactedAt)) {
        lastContactedAt = t.occurred_at;
      }
    }
    const isPlaceholder =
      st === 'unavailable' ||
      ((st === 'queued' || st === 'projected') && !t.subject && !t.body);
    if (isPlaceholder) return;
    if (type === 'email_outreach' || type === 'followup_email' || type === 'outbound_message') {
      // 'draft' = generated but NOT sent — shown in the Content tab as a
      // Draft, but it is not a real touch, so it must not inflate the count.
      // 'outbound_message' = the agent's auto-reply we SENT — a real email touch.
      if (st !== 'draft') emailCount += 1;
    }
    if (type === 'linkedin_message' || type === 'linkedin_inmail') {
      // Only count ACTUALLY-SENT LinkedIn touches (t.sent = LinkedIn URN
      // present), consistent with the email count. Generated drafts are
      // not real touches and must not inflate the stat (was showing 2).
      if (t.sent) _liVariants.add(t.variant === 'inmail' ? 'inmail' : 'dm');
    }
  });
  // Prefer the backend's true sent-count (each sent action = 1 touch); the
  // timeline only carries the latest DM + InMail snapshot, so counting it here
  // caps at 2. Fall back to the snapshot count for pre-deploy payloads.
  const linkedinCount =
    typeof linkedinTouchesProp === 'number' ? linkedinTouchesProp : _liVariants.size;
  const statTiles = [
    { icon: Mail,     value: emailCount,    label: 'Emails',    accent: '#F55600' },
    { icon: Linkedin, value: linkedinCount, label: 'InMail/DM', accent: '#0A66C2' },
  ];

  return (
    <div className="bg-white border-b border-[#2B2926]/15">
      <div className="px-4 py-2.5 sm:px-5 sm:py-4">
        {/* Row 1 — identity (eyebrow + name + job title) on the left,
            Emails / InMail tiles on the right. Chips moved to row 2
            below so the name has room to breathe. */}
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-2 sm:gap-4">
          <div className="min-w-0 flex-1">
            {/* Eyebrow row: last contacted + verified + archived */}
            <div className="flex items-center gap-2 flex-wrap mb-1.5">
              {lastContactedAt && (
                <span className="text-[11px] font-bold uppercase tracking-[0.1em] text-[#4A4742]">
                  Last contacted ·{' '}
                  <span className="text-[#0F1115] normal-case font-bold">
                    {fmtDateShort(lastContactedAt)}
                  </span>
                </span>
              )}
              {(verified || likely) && (
                <>
                  <span className="w-1 h-1 rounded-full bg-[#2B2926]/25" />
                  <span
                    className={[
                      'inline-flex items-center gap-1 text-[9px] font-semibold uppercase tracking-[0.14em]',
                      verified ? 'text-[#10B981]' : 'text-[#F55600]',
                    ].join(' ')}
                  >
                    <Shield className="w-2.5 h-2.5" />
                    {verified ? 'Verified' : 'Likely'}
                  </span>
                </>
              )}
              {hidden && (
                <>
                  <span className="w-1 h-1 rounded-full bg-[#2B2926]/25" />
                  <span className="inline-flex items-center gap-1 text-[9px] font-semibold uppercase tracking-[0.14em] text-[#2B2926]">
                    <EyeOff className="w-2.5 h-2.5" />
                    Archived
                  </span>
                </>
              )}
            </div>

            <h2
              className="text-[16px] sm:text-[19px] font-semibold text-[#2B2926] tracking-tight truncate leading-tight"
              style={lead.source === 'referral' ? { color: '#DC2626' } : undefined}
              title={lead.source === 'referral' ? 'Referral lead — sourced from another lead’s reply' : undefined}
            >
              {lead.name || lead.email || 'Unknown lead'}
            </h2>

            {lead.job_title && (
              <div className="text-[13px] text-[#67655E] font-normal truncate leading-snug mt-0.5" title={lead.job_title}>
                {lead.job_title}
              </div>
            )}
          </div>

          {/* Stat tiles parked top-right so they don't crowd the chips row. */}
          <div className="flex items-stretch gap-2 shrink-0">
            {statTiles.map((t, i) => (
              <div
                key={i}
                className="flex flex-col items-center justify-center min-w-[62px] px-3 py-2 rounded-xl bg-white border border-[#2B2926]/12 shadow-[0_1px_2px_rgba(43,41,38,0.05)]"
              >
                <div className="flex items-center gap-1.5">
                  <t.icon className="w-3 h-3" style={{ color: t.accent }} />
                  <span
                    className="text-[16px] font-bold tabular-nums leading-none"
                    style={{ color: t.accent }}
                  >
                    {t.value}
                  </span>
                </div>
                <span className="text-[8.5px] font-bold uppercase tracking-[0.13em] text-[#67655E] mt-1.5 whitespace-nowrap">
                  {t.label}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Row 2 — contact chips (company / email / LinkedIn) on a clean
            dedicated row below the name. Gives each chip room to render
            instead of being squashed between the name and the stat rail. */}
        {(lead.company || lead.email || lead.linkedin_url) && (
          <div className="flex flex-wrap items-center gap-1.5 mt-2">
            {lead.company && (
              <a
                href={lead.company_domain
                  ? `https://${String(lead.company_domain).replace(/^https?:\/\//, '')}`
                  : `https://www.google.com/search?q=${encodeURIComponent(lead.company)}`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-[#2B2926]/[0.04] border border-[#2B2926]/15 text-[11px] font-semibold text-[#2B2926] max-w-full hover:bg-[#2B2926]/[0.08] hover:border-[#F55600]/50 transition-colors cursor-pointer"
                title={lead.company_domain ? `Open ${lead.company_domain}` : `Search "${lead.company}" on Google`}
              >
                <Building2 className="w-2.5 h-2.5 text-[#2B2926] shrink-0" />
                <span className="truncate">{lead.company}</span>
                {lead.company_domain && (
                  <span className="text-[#67655E] font-normal truncate">
                    / {lead.company_domain}
                  </span>
                )}
              </a>
            )}
            {lead.email && (
              <a
                href={`mailto:${lead.email}`}
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-[#2B2926]/[0.04] border border-[#2B2926]/15 text-[11px] font-semibold text-[#2B2926] max-w-full hover:bg-[#2B2926]/[0.08] hover:border-[#F55600]/50 transition-colors cursor-pointer"
                title={`Email ${lead.email}`}
              >
                <Mail className="w-2.5 h-2.5 text-[#2B2926] shrink-0" />
                <span className="truncate">{lead.email}</span>
              </a>
            )}
            {lead.linkedin_url && (
              <a
                href={lead.linkedin_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-[#F55600] text-white text-[11px] font-semibold hover:bg-[#e63e00] transition-colors"
                title={lead.linkedin_url}
              >
                <Linkedin className="w-2.5 h-2.5 shrink-0" />
                LinkedIn
              </a>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

// ── Right-pane sub-panels (2026-05-27 redesign) ────────────────────────────
//
// Both panels read directly from the same `timeline` array the existing
// JourneyTimeline component renders — so they stay in sync without needing
// new backend endpoints. The timeline event shape is the same as before:
//   { type, status, subject, body, sent_at, opened_at, clicked_at, ... }

const _copyToClipboard = (text) => {
  try {
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text);
    }
  } catch {
    // Non-fatal — older browsers without Clipboard API just no-op.
  }
};

// Strip HTML tags + decode common entities so the preview reads as plain
// text. We never trust raw HTML from the timeline (it's drafted by the
// AI service, but rendering it would still let an injected style sheet
// scroll-jack the right pane).
const _stripHtml = (raw) => {
  const s = String(raw || '');
  if (!s) return '';
  const stripped = s
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<br\s*\/?\s*>/gi, '\n')
    .replace(/<\/p>/gi, '\n\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  // Drop the standalone signature-dash line ("--") and trim trailing spaces
  // per line so signatures render tight and clean, not airy.
  return stripped
    .split('\n')
    .filter((line) => line.trim() !== '--')
    .map((line) => line.replace(/[ \t]+$/, ''))
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
};

// Strip "Re:" / "Fwd:" prefixes from a subject for display — the thread view
// already makes the reply relationship obvious, so the prefix is just noise.
const _cleanSubject = (subj) =>
  String(subj || '')
    .replace(/(^|\s)(re|fwd?)\s*:\s*/gi, '$1')
    .replace(/\s{2,}/g, ' ')
    .trim();

// Parse an ISO timestamp to epoch millis for chronological sorting. Returns
// null on missing/invalid input (unsent drafts) so they can sink to the end.
const _tsOf = (v) => {
  if (!v) return null;
  const ms = Date.parse(v);
  return Number.isNaN(ms) ? null : ms;
};

const GeneratedContentItem = ({ icon: Icon, title, sentAt, status, subject, body, html, channel, kind = 'outbound', meta = '' }) => {
  const plain = _stripHtml(body);
  const cleanSubject = _cleanSubject(subject);
  // `html` is the actual branded email (same template + brand the lead's
  // email uses), rendered server-side. When present, default to the visual
  // Preview; the operator can flip to plain Text.
  const hasHtml = !!(html && String(html).trim());
  const [view, setView] = useState(hasHtml ? 'preview' : 'text');
  const [copied, setCopied] = useState(false);
  // Auto-grow the preview iframe to the email's full height so the whole
  // message shows at once (no awkward inner scrollbar / cut-off header).
  const [frameH, setFrameH] = useState(560);
  // One clear state per card so it's never ambiguous whether the message
  // actually went out vs. is just an AI draft:
  //   Sent · <when>  (green)  — really sent
  //   Draft          (muted)  — generated, not sent yet
  //   (no chip)               — nothing generated yet (placeholder)
  const _st = (status || '').toLowerCase();
  // Conversation cards (inbound reply from the lead / AI-agent auto-response)
  // are rendered with their own accent + state wording so they read as a
  // dialogue, distinct from the outbound cadence sends.
  const isInbound = kind === 'inbound';
  const isAgent = kind === 'agent';
  const isReplyKind = isInbound || isAgent;
  const isSent = !!sentAt || _st === 'sent';
  // A calendar RSVP outcome (accepted / declined) — a one-line signal, no body.
  const isSystem = kind === 'system';
  const _sysNeg = _st === 'declined' || _st === 'cancelled';
  // A connection request that went out WITHOUT the personalized note (e.g. the
  // account was out of free notes): the connection is a real touch, but the note
  // text below was never delivered — label it honestly, not "Sent".
  const isConnNoNote = _st === 'connection_no_note';
  const stateText = isSystem
    ? (_sysNeg ? 'Declined' : _st === 'tentative' ? 'Tentative' : 'Accepted')
    : isInbound
    ? (meta ? `Received · ${meta}` : 'Received')
    : isAgent
    ? 'Sent'
    : isSent
    ? 'Sent'
    : isConnNoNote
    ? 'Connection sent · note not delivered'
    : (plain ? 'Draft' : '');
  // Conversation cards (lead reply + our response) share ONE accent so the
  // exchange reads as a single thread, visually distinct from the outbound
  // cadence sends (orange) and LinkedIn (dark).
  // Icons match the other email cards (orange); LinkedIn keeps its dark tint.
  const squareCls = isSystem
    ? (_sysNeg ? 'bg-[#EF4444]/10 text-[#EF4444]' : 'bg-[#10B981]/10 text-[#10B981]')
    : channel === 'linkedin'
    ? 'bg-[#2B2926]/5 text-[#2B2926]'
    : 'bg-[#F55600]/10 text-[#F55600]';
  // Reply "Received" and response "Sent" share one color, the same green the
  // other Sent emails use.
  const stateCls = isSystem
    ? (_sysNeg ? 'text-[#EF4444]' : 'text-[#10B981]')
    : isReplyKind
    ? 'text-[#10B981]'
    : isSent
    ? 'text-[#10B981]'
    : isConnNoNote
    ? 'text-[#B45309]'
    : 'text-[#2B2926]/45';
  // All cards share the same white background + plain border as the Initial /
  // Follow-up sends (no blue tint, no left-accent line). Reply/response cards
  // stay distinguishable only by their blue icon + label.
  const cardCls = 'border-[#2B2926]/10 bg-white';
  return (
    <div className={`border rounded-xl p-3 ${cardCls}`}>
      <div className="flex items-start justify-between gap-2 mb-1.5">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className={[
              'w-7 h-7 rounded-lg flex items-center justify-center shrink-0',
              squareCls,
            ].join(' ')}
          >
            <Icon className="w-3.5 h-3.5" />
          </span>
          <div className="min-w-0">
            <div className="text-[13px] font-semibold text-[#2B2926] truncate">{title}</div>
            {stateText && (
              <div className={['text-[11px] mt-0.5 font-semibold', stateCls].join(' ')}>
                {stateText}
              </div>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {plain && (
            <button
              type="button"
              onClick={() => {
                _copyToClipboard(cleanSubject ? `Subject: ${cleanSubject}\n\n${plain}` : plain);
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              }}
              className={[
                'inline-flex items-center gap-1 text-[11px] font-bold px-2.5 py-1 rounded-md transition-colors text-white',
                copied
                  ? 'bg-[#10B981]'
                  : 'bg-[#0F1115] hover:bg-[#2B2926]',
              ].join(' ')}
            >
              {copied ? <Check className="w-3 h-3" strokeWidth={3} /> : <Copy className="w-3 h-3" />}
              {copied ? 'Copied' : 'Copy'}
            </button>
          )}
        </div>
      </div>
      {cleanSubject && (
        <div className="text-[13px] font-semibold text-[#2B2926] mb-2">
          Subject: {cleanSubject}
        </div>
      )}
      {!isSystem && (hasHtml && view === 'preview' ? (
        <div
          className="w-full rounded-lg border border-[#2B2926]/10 bg-[#f1f1f0] flex justify-center"
          style={{ padding: '12px 0' }}
        >
          <iframe
            title="Email preview"
            srcDoc={html}
            sandbox="allow-same-origin"
            scrolling="no"
            onLoad={(e) => {
              try {
                const doc =
                  e.target.contentDocument || e.target.contentWindow?.document;
                if (doc && doc.body) {
                  const h = Math.min(
                    Math.max(doc.body.scrollHeight + 16, 200),
                    2400,
                  );
                  setFrameH(h);
                }
              } catch {
                /* cross-origin — keep default height */
              }
            }}
            className="bg-white rounded shadow-sm"
            style={{ width: 600, maxWidth: '100%', height: frameH, border: 'none' }}
          />
        </div>
      ) : (
        <div className="text-[13px] text-[#2B2926] whitespace-pre-wrap leading-[1.65]">
          {plain || <span className="text-[#2B2926] italic">No content yet.</span>}
        </div>
      ))}
    </div>
  );
};

const GeneratedContentPanel = ({ timeline, loading, channelFilter = null, sequences = [], leadName = '' }) => {
  // Pull content-bearing events out of the timeline. We use the event
  // `type` to pick an icon + channel tint so the user can scan at a
  // glance which channel each piece of content belongs to.
  // 2026-05-29 — `channelFilter` ('email' | 'linkedin' | null) lets
  // the Content tab's sub-tabs surface only one channel at a time.
  // Email step → label, matching the Flow sequence exactly (step is the
  // backend's nexus_touchpoints.step: 0=Initial, 1=FU1, 2=FU2, 3=Closing).
  const EMAIL_STEP_LABELS = ['Initial Email', 'Follow-up 1', 'Follow-up 2', 'Closing Email'];
  // Once the cadence has ended (reply/demo/unsubscribe/hard-fail) no further
  // follow-up will ever send, so an unsent DRAFT is stale noise — hide it, the
  // same way OutreachFlowPanel drops its pending nodes on a stopped sequence.
  // Only suppress when EVERY sequence is stopped, so a still-active sequence
  // (incl. OOO, which stays 'active') keeps its scheduled draft visible.
  const _SEQ_STOPPED = ['replied', 'halted', 'stopped', 'unsubscribed', 'failed', 'dead'];
  const seqEnded =
    (sequences || []).length > 0 &&
    (sequences || []).every((s) => _SEQ_STOPPED.includes((s?.status || '').toLowerCase()));
  const items = useMemo(() => {
    const out = [];
    (timeline || []).forEach((t, i) => {
      const type = (t.type || '').toLowerCase();
      if (type === 'email_outreach' || type === 'followup_email') {
        if (channelFilter && channelFilter !== 'email') return;
        // A still-unsent draft once the sequence has ended will never send —
        // don't surface it as an actionable card.
        if (seqEnded && (t.status || '').toLowerCase() === 'draft') return;
        // `step` drives both the label and the order so Content mirrors the
        // Flow sequence (Initial → Follow-up 1 → Follow-up 2 → Closing).
        const step = Number.isFinite(t.step) ? t.step : (type === 'email_outreach' ? 0 : null);
        out.push({
          key: `e-${i}`,
          icon: Mail,
          channel: 'email',
          kind: 'outbound',
          _order: step != null ? step : 99,
          // Only a SENT send takes a chronological slot. An unsent draft's
          // occurred_at is its generation time (set at enrollment), which
          // predates every real send/reply — so we leave it null, which sinks
          // the draft to the bottom (the "next message" slot) via the sort
          // below instead of floating it to the top.
          _ts: (t.status || '').toLowerCase() === 'sent'
            ? _tsOf(t.occurred_at || t.sent_at || t.created_at)
            : null,
          title:
            (step != null && EMAIL_STEP_LABELS[step])
              ? EMAIL_STEP_LABELS[step]
              : (type === 'followup_email' ? 'Email follow-up' : 'Email'),
          sentAt: t.sent_at || t.created_at,
          status: t.status,
          subject: t.subject,
          body: t.body || t.body_snapshot || t.preview,
          html: t.html || '',
        });
      } else if (type === 'inbound_message' || type === 'email_reply') {
        // The lead's own reply (incoming). `inbound_message` comes from the
        // parsed inbound thread; `email_reply` from an Outreach.reply_text
        // mirror. Both render as an incoming conversation card.
        if (channelFilter && channelFilter !== 'email') return;
        out.push({
          key: `r-${i}`,
          icon: Inbox,
          channel: 'email',
          kind: 'inbound',
          _order: 50,
          _ts: _tsOf(t.occurred_at || t.sent_at || t.created_at),
          title: leadName ? `Reply from ${leadName}` : 'Reply received',
          meta: '',
          sentAt: t.occurred_at || t.sent_at || t.created_at,
          status: t.status,
          subject: t.subject,
          body: t.body || t.reply_text,
        });
      } else if (type === 'outbound_message') {
        // Our response back to the lead's reply (sent from the mailbox).
        if (channelFilter && channelFilter !== 'email') return;
        out.push({
          key: `ar-${i}`,
          icon: Reply,
          channel: 'email',
          kind: 'agent',
          _order: 51,
          _ts: _tsOf(t.occurred_at || t.sent_at || t.created_at),
          title: leadName ? `Reply to ${leadName}` : 'Reply sent',
          meta: '',
          sentAt: t.occurred_at || t.sent_at || t.created_at,
          status: 'sent',
          subject: t.subject,
          body: t.body,
        });
      } else if (type === 'linkedin_message' || type === 'linkedin_inmail') {
        if (channelFilter && channelFilter !== 'linkedin') return;
        // LinkedIn order: message (DM) first, then InMail — same as Flow.
        const isInmail = t.variant === 'inmail' || type === 'linkedin_inmail';
        out.push({
          key: `l-${i}`,
          icon: Linkedin,
          channel: 'linkedin',
          kind: 'outbound',
          _order: isInmail ? 1 : 0,
          // Same rule as email: only a real touch (sent DM, or a no-note
          // connection that still went out) is placed chronologically; an
          // unsent InMail/DM draft stays null so it sinks below the sends.
          _ts: ['sent', 'connection_no_note'].includes((t.status || '').toLowerCase())
            ? _tsOf(t.occurred_at || t.sent_at || t.created_at)
            : null,
          title: isInmail ? 'LinkedIn InMail' : 'LinkedIn message',
          sentAt: t.sent_at || t.created_at,
          status: t.status,
          subject: t.subject,
          body: t.body,
        });
      } else if (type === 'calendar_response') {
        // A prospect's RSVP to the demo invite (accepted / declined) — a clean
        // one-line signal, not a reply.
        if (channelFilter && channelFilter !== 'email') return;
        const st = (t.status || '').toLowerCase();
        out.push({
          key: `cr-${i}`,
          icon: st === 'declined' || st === 'cancelled' ? X : Check,
          channel: 'email',
          kind: 'system',
          _order: 52,
          _ts: _tsOf(t.occurred_at || t.sent_at || t.created_at),
          title: t.subject || 'Calendar response',
          status: st,
          sentAt: t.occurred_at,
        });
      }
    });
    // Option A — chronological within each channel so the lead's reply and the
    // AI agent's response sit between the sends where they actually happened.
    // Email group stays before LinkedIn (each sub-tab shows one channel anyway).
    // Unsent drafts (no timestamp) sink to the end, then fall back to cadence
    // order (step, DM-before-InMail).
    const channelRank = (c) => (c === 'email' ? 0 : 1);
    out.sort((a, b) => {
      const cr = channelRank(a.channel) - channelRank(b.channel);
      if (cr) return cr;
      const at = a._ts, bt = b._ts;
      if (at != null && bt != null && at !== bt) return at - bt;
      if (at == null && bt != null) return 1;
      if (at != null && bt == null) return -1;
      return (a._order ?? 99) - (b._order ?? 99);
    });
    return out;
  }, [timeline, channelFilter, leadName, seqEnded]);

  if (loading) {
    return (
      <div className="p-4 space-y-3">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="border border-[#2B2926]/10 rounded-xl p-3 bg-white animate-pulse"
          >
            <div className="h-3 w-1/2 bg-[#2B2926]/5 rounded mb-2" />
            <div className="h-2 w-full bg-[#2B2926]/5 rounded mb-1" />
            <div className="h-2 w-5/6 bg-[#2B2926]/5 rounded mb-1" />
            <div className="h-2 w-3/5 bg-[#2B2926]/5 rounded" />
          </div>
        ))}
      </div>
    );
  }

  if (items.length === 0) {
    const emptyMsg =
      channelFilter === 'linkedin'
        ? 'No LinkedIn content yet. Drafts will appear here once outreach begins.'
        : channelFilter === 'email'
        ? 'No email content yet. Messages will appear here once outreach begins.'
        : 'No content yet. Emails and LinkedIn drafts will appear here once outreach begins.';
    return (
      <div className="p-8 text-center">
        <Mailbox className="w-8 h-8 mx-auto text-[#2B2926] mb-2" />
        <p className="text-xs text-[#2B2926]">{emptyMsg}</p>
      </div>
    );
  }

  return (
    <div className="p-4 space-y-3">
      {items.map((item) => (
        <GeneratedContentItem
          key={item.key}
          icon={item.icon}
          title={item.title}
          sentAt={item.sentAt}
          status={item.status}
          subject={item.subject}
          body={item.body}
          html={item.html}
          channel={item.channel}
          kind={item.kind}
          meta={item.meta}
        />
      ))}
    </div>
  );
};

const StatTile = ({ label, value, hint, accent }) => (
  <div className="rounded-xl border border-[#2B2926]/10 px-2.5 py-2 bg-white">
    <div className="text-[9px] uppercase tracking-wider text-[#2B2926] font-bold truncate">
      {label}
    </div>
    <div
      className={[
        'text-lg font-black mt-0.5',
        accent === 'green'
          ? 'text-[#10B981]'
          : accent === 'orange'
          ? 'text-[#F55600]'
          : 'text-[#2B2926]',
      ].join(' ')}
    >
      {value}
    </div>
    {hint && <div className="text-[10px] text-[#2B2926] mt-0.5">{hint}</div>}
  </div>
);

// One metric cell inside the Engagement Summary grid: leading icon + label on
// the left, bold value on the right, inside a soft bordered pill. Two cells per
// row keep the label and value close together (no big empty gap).
const SummaryRow = ({ icon: Icon, label, children }) => (
  <div className="flex items-center justify-between gap-3 px-3 py-2.5 rounded-xl bg-[#2B2926]/[0.025] border border-[#2B2926]/[0.08]">
    <div className="flex items-center gap-2 min-w-0">
      <Icon className="w-3.5 h-3.5 text-[#67655E] shrink-0" />
      <span className="text-[12px] text-[#2B2926] font-semibold truncate">{label}</span>
    </div>
    <div className="text-[13px] font-bold text-[#2B2926] tabular-nums shrink-0">{children}</div>
  </div>
);

const LeadAnalyticsPanel = ({ lead, timeline, onJumpToBrief }) => {
  // Derive event counts from the timeline. We count types not states —
  // an opened email and a sent email are separate timeline rows.
  const stats = useMemo(() => {
    const tl = timeline || [];
    let emails = 0;
    let voice = 0;
    let opens = 0;
    let clicks = 0;
    let replies = 0;
    let inbound = 0;
    let lastTouch = null;
    const liVariants = new Set();
    tl.forEach((t) => {
      const type = (t.type || '').toLowerCase();
      const status = (t.status || '').toLowerCase();
      // Match the header + table TOUCHES exactly: skip synthetic placeholders,
      // count only SENT emails (a 'draft' is the upcoming message, not a real
      // touch), and count DISTINCT LinkedIn variants (DM + InMail, max 2).
      const isPlaceholder =
        status === 'unavailable' ||
        ((status === 'queued' || status === 'projected') && !t.subject && !t.body);
      if (isPlaceholder) return;
      if (type === 'email_outreach' || type === 'followup_email' || type === 'outbound_message') {
        // outbound_message = the agent's auto-reply we SENT — a real email touch.
        if (status !== 'draft') emails += 1;
      }
      if (type === 'linkedin_message' || type === 'linkedin_inmail') {
        // Count only ACTUALLY-SENT LinkedIn touches (t.sent = LinkedIn URN
        // present). Generated drafts are not real touchpoints.
        if (t.sent) liVariants.add(t.variant === 'inmail' ? 'inmail' : 'dm');
      }
      if (type === 'voice_call') voice += 1;
      if (type === 'email_reply' || type === 'inbound_message') {
        replies += 1;
        inbound += 1;
      }
      if (status === 'opened' || t.opened_at) opens += 1;
      if (status === 'clicked' || t.clicked_at) clicks += 1;
      if (status !== 'draft') {
        // Timeline events carry the timestamp as `occurred_at`; sent_at/
        // created_at are the legacy fallbacks.
        const ts = t.occurred_at || t.sent_at || t.created_at;
        if (ts && (!lastTouch || ts > lastTouch)) lastTouch = ts;
      }
    });
    const linkedin = liVariants.size;
    return { emails, linkedin, voice, opens, clicks, replies, inbound, lastTouch };
  }, [timeline]);

  const totalSent = stats.emails + stats.linkedin + stats.voice;
  const openRate = stats.emails > 0 ? Math.round((stats.opens / stats.emails) * 100) : null;
  const replyRate = totalSent > 0 ? Math.round((stats.replies / totalSent) * 100) : null;
  const isDemoScheduled = lead?.status === 'demo_scheduled';

  // Resolve a "last activity" timestamp via a fallback chain so the row
  // never shows "—" when there's any signal at all. Timeline-derived
  // value wins (most accurate), then the lead's denormalised columns.
  const lastActivityIso =
    stats.lastTouch ||
    lead?.last_attempt_at ||
    lead?.last_contacted_at ||
    null;
  const enrolledIso = lead?.enrolled_at || lead?.created_at || null;
  const demoBookedIso = lead?.demo_booked_at || null;
  const demoScheduledIso = lead?.demo_scheduled_at || null;

  return (
    <div className="p-4 space-y-4">
      {isDemoScheduled && onJumpToBrief && (
        <button
          type="button"
          onClick={onJumpToBrief}
          className="w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-[#10B981] text-white text-xs font-bold hover:opacity-90 transition-opacity"
          title="Open the Demo Agent tab"
        >
          <Briefcase className="w-3.5 h-3.5" />
          Open in Demo Agent
        </button>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        <StatTile label="Emails sent" value={stats.emails} accent="orange" />
        <StatTile label="LinkedIn touches" value={stats.linkedin} />
        <StatTile label="Clicks" value={stats.clicks} />
        <StatTile label="Voice calls" value={stats.voice} />
        <StatTile label="Replies" value={stats.replies} accent="green" />
        <StatTile
          label="Open"
          value={stats.opens}
          hint={openRate != null ? `${openRate}% open rate` : null}
        />
      </div>
      <div className="rounded-2xl border border-[#2B2926]/10 bg-white shadow-sm overflow-hidden">
        <div className="flex items-center gap-2.5 px-4 py-3 border-b border-[#2B2926]/[0.07] bg-[#2B2926]/[0.015]">
          <div className="w-6 h-6 rounded-lg bg-[#F55600]/10 flex items-center justify-center shrink-0">
            <Zap className="w-3.5 h-3.5 text-[#F55600]" />
          </div>
          <span className="text-[11px] uppercase tracking-[0.12em] text-[#2B2926] font-bold">
            Engagement Summary
          </span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 p-3">
          <SummaryRow icon={Target} label="Total touchpoints">{totalSent}</SummaryRow>
          <SummaryRow icon={RefreshCw} label="Reply rate">
            {replyRate != null ? `${replyRate}%` : '—'}
          </SummaryRow>
          <SummaryRow icon={Mail} label="Last activity">
            {lastActivityIso ? fmtDateShort(lastActivityIso) : '—'}
          </SummaryRow>
          <SummaryRow icon={Users} label="Enrolled">
            {enrolledIso ? fmtDateShort(enrolledIso) : '—'}
          </SummaryRow>
          {isDemoScheduled && (
            <>
              <SummaryRow icon={Briefcase} label="Demo booked">
                {demoBookedIso ? fmtDateShort(demoBookedIso) : '—'}
              </SummaryRow>
              <SummaryRow icon={Briefcase} label="Demo scheduled for">
                <span className="text-[#10B981]">
                  {demoScheduledIso ? fmtDateShort(demoScheduledIso) : '—'}
                </span>
              </SummaryRow>
            </>
          )}
          <SummaryRow icon={Shield} label="Lead status">
            <span className="text-[13px] font-bold capitalize text-[#047857]">
              {(lead?.status || 'new').replace('_', ' ')}
            </span>
          </SummaryRow>
        </div>
      </div>
    </div>
  );
};


// Lead-table columns — single source of truth for header labels + the keys
// the resizable-column widths (colW) are stored under. Order MUST match the
// order the body cells are rendered in LeadRow.
const LEAD_COLUMNS = [
  { key: 'sno', label: 'S.NO' },
  { key: 'campaign', label: 'CAMPAIGN' },
  { key: 'campaignId', label: 'CAMPAIGN ID' },
  { key: 'created', label: 'CREATED DATE' },
  { key: 'match', label: 'MATCH SCORE' },
  { key: 'name', label: 'NAME' },
  { key: 'title', label: 'TITLE' },
  { key: 'company', label: 'COMPANY' },
  { key: 'linkedin', label: 'LINKEDIN' },
  { key: 'email', label: 'EMAIL' },
  { key: 'touches', label: 'TOUCHES' },
  { key: 'location', label: 'LOCATION' },
  { key: 'contact', label: 'CONTACT' },
];

// ── Main component ─────────────────────────────────────────────────────────

// GTM Journey loads 25 rows at a time with infinite scroll (was a single
// 500-row load that hung the tab). Scrolling near the bottom appends the
// next page; client-side search/filter runs over the loaded set.
const PAGE_SIZE = 25;

const NexusJourney = ({ authAxios, apiBase, user, setMessage, onNavigate, onBack, activateNonce }) => {
  // Credit balance: drives the header badge, the Buy-Credits modal, and the
  // low-credit popup.
  const { balance: creditBalance, refresh: refreshCredits } = useCreditBalance(authAxios, apiBase);
  const [buyCreditsOpen, setBuyCreditsOpen] = useState(false);
  // Buying credits is Master-Admin-only; everyone sees the balance badge.
  const openBuyCredits = () => {
    if (canManageBilling(user)) setBuyCreditsOpen(true);
    else if (setMessage) setMessage('Only the Master Admin can buy credits.');
  };
  // State (names mirror legacy)
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [summary, setSummary] = useState({
    active: 0,
    hidden: 0,
    total: 0,
    eligible_auto_hide: 0,
  });
  // 2026-05-23: hidden tab removed in favor of a single merged "all" view.
  // Hidden leads now appear in the same list with a "(Hidden)" tag next to
  // their name. The backend's view=all is what makes this possible.
  const [view, setView] = useState('all');
  const [leads, setLeads] = useState([]);
  // Infinite-scroll pagination (25/page). hasMore drives the scroll-prefetch,
  // pageOffsetRef tracks the next offset, loadingMoreRef guards double-fires.
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const pageOffsetRef = useRef(0);
  const loadingMoreRef = useRef(false);
  const [selectedLeadId, setSelectedLeadId] = useState('');
  // Campaign the selected row is attributed to → scopes the detail so a
  // multi-campaign lead's timeline doesn't mix products.
  const [selectedCampaignId, setSelectedCampaignId] = useState(null);
  const [selectedDetail, setSelectedDetail] = useState(null);
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  const [search, setSearch] = useState('');
  const [products, setProducts] = useState([]);
  const [selectedProductId, setSelectedProductId] = useState('');
  const [demoData, setDemoData] = useState({ booking: null, briefing: null });
  const [demoLoading, setDemoLoading] = useState(false);
  const [rightTab, setRightTab] = useState('content');
  // 2026-05-29 — Content tab now has Email / LinkedIn sub-tabs (mirrors
  // the channel selector inside the Flow tab). State lives here so it
  // persists across lead selections.
  const [contentChannel, setContentChannel] = useState('email');
  // Dynamic filter system (added 2026-05-27 with the lead-workspace redesign).
  // `filterSchema` is fetched from /journey/filters/schema and dictates which
  // filter groups the sidebar renders — the frontend never hard-codes the
  // list. `selectedFilters` is keyed by the schema's group `id` so the
  // sidebar's onChange callback can update one group at a time without
  // touching the others.
  const [filterSchema, setFilterSchema] = useState({ total: 0, filters: [] });
  const [filterSchemaLoading, setFilterSchemaLoading] = useState(true);
  const [selectedFilters, setSelectedFilters] = useState({});
  const [totalCount, setTotalCount] = useState(null);
  // Sort key for the new lead list. Defaults to backend's view-specific
  // ranking when null. Activity_desc = most recently touched first.
  const [sortKey, setSortKey] = useState(null);
  // Resizable columns — user can drag the right edge of ANY header to widen
  // or narrow that column. Widths drive (via CSS vars on the table) the
  // matching body cells, which truncate with an ellipsis when narrower than
  // their content. Keys + order MUST match LEAD_COLUMNS below.
  const [colW, setColW] = useState({
    sno: 56, campaign: 110, campaignId: 96, created: 116, match: 72,
    name: 180, title: 200, company: 180, linkedin: 72, email: 220,
    touches: 104, location: 200, contact: 84,
  });
  const startColResize = (key) => (e) => {
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX;
    const startW = colW[key];
    const onMove = (ev) => {
      const next = Math.max(48, Math.min(560, startW + (ev.clientX - startX)));
      setColW((prev) => (prev[key] === next ? prev : { ...prev, [key]: next }));
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };
  // Per-column sizing CSS — body cells truncate to their width; headers keep
  // width but never truncate (so labels like "CAMPAIGN ID" stay fully visible).
  const colSizingCss = LEAD_COLUMNS.map((c, i) => {
    const n = i + 1;
    const v = 'var(--cw-' + c.key + ')';
    return '.nx-lead-table td:nth-child(' + n + '){width:' + v + ';min-width:' + v + ';max-width:' + v + ';overflow:hidden;text-overflow:ellipsis;}'
      + '.nx-lead-table th:nth-child(' + n + '){width:' + v + ';min-width:' + v + ';}';
  }).join('');
  // Filter sidebar — auto-expands on hover, auto-collapses to the 52px
  // icon rail when the mouse leaves. The pin button overrides this:
  // when pinned, the panel stays open regardless of hover.
  //   filtersOpen = filtersPinned || filtersHovered
  const [filtersPinned, setFiltersPinned] = useState(false);
  // Filter sidebar opens ONLY via explicit click on the rail icon — the
  // previous hover-auto-expand was firing every time the user grazed the
  // 52px rail with their cursor, which caused the entire NexusJourney to
  // re-render and felt like the page was slowing down. Now `filtersOpen`
  // just mirrors `filtersPinned` (toggled by the open icon / close X
  // button).
  const filtersOpen = filtersPinned;

  // Mobile breakpoint (< md / 768px). On phones the 3-pane workspace can't
  // sit side-by-side, so the filter rail floats as an overlay (instead of
  // squeezing the table) and the lead-detail pane goes full-width.
  const [isMobile, setIsMobile] = useState(
    typeof window !== 'undefined' ? window.innerWidth < 768 : false,
  );
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const mq = window.matchMedia('(max-width: 767px)');
    const onChange = (e) => setIsMobile(e.matches);
    setIsMobile(mq.matches);
    // addEventListener is the modern API; fall back to addListener for Safari < 14.
    if (mq.addEventListener) mq.addEventListener('change', onChange);
    else mq.addListener(onChange);
    return () => {
      if (mq.removeEventListener) mq.removeEventListener('change', onChange);
      else mq.removeListener(onChange);
    };
  }, []);

  const pollTimerRef = useRef(null);
  const pollCountRef = useRef(0);
  // Detail-view auto-refresh: when the selected lead's timeline is still
  // missing the LinkedIn draft (or has a pre-generated email touchpoint
  // that hasn't flipped to sent yet), keep refetching the detail every
  // ~10s so the user doesn't have to manually refresh.
  const detailPollTimerRef = useRef(null);
  const detailPollCountRef = useRef(0);
  // List-status auto-refresh: while any visible lead in the left list
  // is still in a non-settled state (queued / new), re-fetch the list
  // every ~15s so the status pill flips from "queued" to "contacted"
  // without the operator clicking Refresh. Stops once every lead has
  // settled or the cap is hit.
  const listPollTimerRef = useRef(null);
  const listPollCountRef = useRef(0);

  const journeyBase = `${apiBase}/journey`;
  const productsBase = `${apiBase}/products`;

  // ── Loaders ──────────────────────────────────────────────────────────────

  const loadProducts = useCallback(async () => {
    try {
      const res = await authAxios.get(productsBase);
      const list = res.data?.products || res.data || [];
      const arr = Array.isArray(list) ? list : [];

      // Normalize each row so the render code can rely on top-level
      // `entity_type` and `lead_count` regardless of which backend
      // version answered:
      //   - New backend: returns both fields directly.
      //   - Old backend (pre-deploy): returns neither; we derive
      //     entity_type from icp.entity_type and leave lead_count as
      //     undefined so the zero-lead filter below short-circuits.
      const normalised = arr.map((p) => {
        const icpEntity =
          (p && p.icp && typeof p.icp === 'object' && p.icp.entity_type) ||
          null;
        const et = (p.entity_type || icpEntity || 'product')
          .toString()
          .trim()
          .toLowerCase();
        return {
          ...p,
          entity_type: et === 'service' ? 'service' : 'product',
          lead_count:
            typeof p.lead_count === 'number' ? p.lead_count : undefined,
        };
      });

      // Only apply the zero-lead filter when the backend actually told us
      // lead counts. Old deployments don't return that field — in that
      // case we show every product so the filter row still renders,
      // matching the legacy UX while we wait for the new backend to ship.
      const backendKnowsLeadCount = normalised.some(
        (p) => typeof p.lead_count === 'number',
      );
      const visible = backendKnowsLeadCount
        ? normalised.filter((p) => (p.lead_count || 0) > 0)
        : normalised;

      // Collapse legacy duplicates: when several products share the same
      // source_url (the wizard re-ran /analyze before the upsert fix
      // shipped), render ONE pill for that URL. The canonical id is the
      // product with the most leads (ties broken by id desc — newest
      // wins). lead_count becomes the sum across siblings so the user
      // sees total reach for that URL. The backend leads endpoint is
      // sibling-aware, so clicking the pill returns all union'd leads.
      const groupKey = (p) =>
        (p.source_url || '').toString().trim().toLowerCase().replace(/\/+$/, '') ||
        `__name__::${(p.name || '').trim().toLowerCase()}` ||
        `__id__::${p.id}`;

      const groups = new Map();
      for (const p of visible) {
        const k = groupKey(p);
        const g = groups.get(k);
        if (!g) {
          groups.set(k, { canonical: p, total: p.lead_count || 0 });
          continue;
        }
        g.total += p.lead_count || 0;
        const pLeads = p.lead_count || 0;
        const cLeads = g.canonical.lead_count || 0;
        if (pLeads > cLeads || (pLeads === cLeads && p.id > g.canonical.id)) {
          g.canonical = p;
        }
      }
      const collapsed = Array.from(groups.values()).map((g) => ({
        ...g.canonical,
        lead_count: g.total,
      }));
      setProducts(collapsed);
    } catch (err) {
      // Non-fatal — product filter just won't render.
      // eslint-disable-next-line no-console
      console.warn('[journey] loadProducts failed', err?.response?.data || err.message);
    }
  }, [authAxios, productsBase]);

  // Fetch the dynamic filter schema. Cheap-ish (single workspace-scoped
  // query + facet count) so we re-fetch whenever the bucket view flips —
  // counts shift between active/hidden/all. Failures are non-fatal; the
  // sidebar falls back to "no filters available" rather than breaking
  // the whole page.
  const loadFilterSchema = useCallback(
    async (nextView = view) => {
      setFilterSchemaLoading(true);
      try {
        const res = await authAxios.get(`${journeyBase}/filters/schema`, {
          params: { view: nextView },
        });
        // Belt-and-suspenders: also drop any filter groups the product
        // team has decided not to surface, regardless of what the
        // backend returns. Currently just `email_verified` — the
        // backend's schema endpoint has been updated to omit it too,
        // but this guard means the sidebar reflects the decision the
        // moment the frontend deploys, without waiting for a backend
        // restart on the deployed env.
        const HIDDEN_FILTER_IDS = new Set(['email_verified']);
        const raw = res.data || { total: 0, filters: [] };
        const cleaned = {
          ...raw,
          filters: (raw.filters || []).filter(
            (g) => !HIDDEN_FILTER_IDS.has(g.id),
          ),
        };
        setFilterSchema(cleaned);
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn(
          '[journey] loadFilterSchema failed',
          err?.response?.data || err.message,
        );
        setFilterSchema({ total: 0, filters: [] });
      } finally {
        setFilterSchemaLoading(false);
      }
    },
    [authAxios, journeyBase, view],
  );

  // Build the axios params object for /journey/leads given the current
  // search + sidebar filter state. axios v1's default serializer uses
  // URLSearchParams which emits arrays as `?key=a,b` — FastAPI's
  // `List[str] = Query(None)` does NOT parse that and silently drops the
  // values, so we serialize arrays manually as repeated keys
  // (`?status=a&status=b`) via the leadsParamsSerializer below.
  //
  // `email_verified` is the only non-array param coming out of the sidebar:
  // its schema type is "boolean" and the backend signature is Optional[bool],
  // so we collapse the [true] / [false] array down to a single value here.
  const buildLeadsParams = useCallback(
    (nextView, offset = 0) => {
      const params = { view: nextView, limit: PAGE_SIZE, offset };
      // Send `q` to the backend so search spans ALL leads (not just the
      // 25/page loaded client-side). The backend now matches name, email,
      // company, title, campaign (product_name), campaign id (campaign_number)
      // and location with normalised partial matching (spaces/punctuation
      // stripped). `filteredLeads` mirrors the same normalisation client-side.
      if (search && search.trim()) params.q = search.trim();
      if (sortKey) params.sort = sortKey;
      Object.entries(selectedFilters || {}).forEach(([id, arr]) => {
        if (!Array.isArray(arr) || arr.length === 0) return;
        if (id === 'email_verified') {
          // Sidebar enforces single-select on boolean groups (see
          // FilterSidebar's FilterGroup), so arr is always length 1 here.
          params.email_verified = arr[0];
        } else {
          params[id] = arr;
        }
      });
      return params;
    },
    [sortKey, selectedFilters, search],
  );

  // axios serializer that emits each array element as its own `key=value`
  // pair so FastAPI's `List[...] = Query(None)` actually receives them.
  // Without this, multi-select sidebar filters look like they "work" in
  // the UI but the backend never sees them and returns the unfiltered list.
  const leadsParamsSerializer = useCallback(
    (params) => {
      const parts = [];
      Object.entries(params || {}).forEach(([k, v]) => {
        if (v == null) return;
        if (Array.isArray(v)) {
          v.forEach((vv) => {
            if (vv == null) return;
            parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(vv)}`);
          });
        } else {
          parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
        }
      });
      return parts.join('&');
    },
    [],
  );

  const loadList = useCallback(
    async (nextView = view, { append = false, offset = 0, silent = false } = {}) => {
      if (append) {
        if (loadingMoreRef.current) return;  // a page is already in flight
        loadingMoreRef.current = true;
        setLoadingMore(true);
      } else if (!silent) {
        setLoading(true);
      }
      setError('');
      try {
        // Fresh load: summary + first page in parallel. Append: only the
        // next page of leads (offset-paginated). Route through /journey/leads
        // so sidebar filters (sent as product_ids etc.) apply.
        const leadsP = authAxios.get(`${journeyBase}/leads`, {
          params: buildLeadsParams(nextView, offset),
          paramsSerializer: leadsParamsSerializer,
        });
        const summaryP = append
          ? Promise.resolve(null)
          : authAxios.get(`${journeyBase}/summary`);
        const [leadsRes, summaryRes] = await Promise.all([leadsP, summaryP]);

        if (summaryRes) {
          setSummary(
            summaryRes.data?.summary || {
              active: 0,
              hidden: 0,
              total: 0,
              eligible_auto_hide: 0,
            },
          );
        }
        const newLeads = leadsRes.data?.leads || [];
        // Append to the loaded set on scroll; replace on a fresh load.
        setLeads((prev) => (append ? [...prev, ...newLeads] : newLeads));
        if (typeof leadsRes.data?.total === 'number') {
          setTotalCount(leadsRes.data.total);
        } else if (!append) {
          setTotalCount(newLeads.length);
        }
        // A full page back → there may be more. A short page → end reached.
        setHasMore(newLeads.length === PAGE_SIZE);
        pageOffsetRef.current = offset + newLeads.length;

        // Selection bookkeeping only matters on a fresh load (not append).
        if (!append) {
          if (
            selectedLeadId &&
            !newLeads.find((l) => String(l._id) === String(selectedLeadId))
          ) {
            setSelectedLeadId('');
            setSelectedDetail(null);
          } else if (newLeads.length === 0) {
            setSelectedLeadId('');
            setSelectedDetail(null);
          }
        }
      } catch (err) {
        const msg = err?.response?.data?.detail || err?.response?.data?.error || err.message;
        setError(msg);
      } finally {
        if (append) {
          loadingMoreRef.current = false;
          setLoadingMore(false);
        } else {
          setLoading(false);
        }
      }
    },
    [
      authAxios,
      journeyBase,
      selectedLeadId,
      view,
      buildLeadsParams,
      leadsParamsSerializer,
    ],
  );

  // Infinite scroll — prefetch the next 25-row page when the user scrolls
  // near the bottom of the leads list.
  const onLeadsScroll = useCallback(
    (e) => {
      if (loadingMoreRef.current || !hasMore) return;
      const el = e.currentTarget;
      const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      const prefetchMargin = Math.max(600, el.clientHeight * 1.5);
      if (distanceFromBottom < prefetchMargin) {
        loadList(view, { append: true, offset: pageOffsetRef.current, silent: true });
      }
    },
    [hasMore, view, loadList],
  );

  const loadDetail = useCallback(
    async (leadId, campaignId) => {
      if (!leadId) return;
      setDetailLoading(true);
      try {
        // Scope the detail to the row's campaign so a lead in multiple
        // campaigns (same person, different products) shows ONLY that
        // campaign's timeline — no product mixing. Omitted → all campaigns.
        const res = await authAxios.get(`${journeyBase}/leads/${leadId}/detail`, {
          params: campaignId ? { campaign_id: campaignId } : {},
        });
        setSelectedDetail(res.data);
        // If demo_scheduled, also fetch demo data.
        if (res.data?.lead?.status === 'demo_scheduled') {
          loadDemoData(leadId);
        } else {
          setDemoData({ booking: null, briefing: null });
        }
      } catch (err) {
        const msg = err?.response?.data?.detail || err?.response?.data?.error || err.message;
        setError(msg);
      } finally {
        setDetailLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [authAxios, journeyBase],
  );

  const loadDemoData = useCallback(
    async (leadId) => {
      if (!leadId) return;
      setDemoLoading(true);
      try {
        const res = await authAxios.get(`${journeyBase}/leads/${leadId}/demo`);
        setDemoData(res.data || { booking: null, briefing: null });
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn('[journey] loadDemoData failed', err?.response?.data || err.message);
        setDemoData({ booking: null, briefing: null });
      } finally {
        setDemoLoading(false);
      }
    },
    [authAxios, journeyBase],
  );

  // ── Stable callbacks for the memoised FilterSidebar ───────────────────────
  // These three handlers are recreated on every render by default,
  // which defeats React.memo on FilterSidebar and made every state
  // tick in this big component re-render the entire sidebar (N groups
  // × M options each + useMemos per row). Wrapping them in
  // useCallback with the setters (which are stable) as the only deps
  // keeps the identity stable forever.
  const onSidebarChange = useCallback((groupId, values) => {
    setSelectedFilters((prev) => ({ ...prev, [groupId]: values }));
  }, []);
  const onSidebarClear = useCallback(() => {
    setSelectedFilters({});
    setSearch('');
  }, []);
  const onSidebarTogglePin = useCallback(() => {
    setFiltersPinned((v) => !v);
  }, []);

  // Stable per-row click handler — receives the lead id and updates
  // selection. Identity is frozen forever (setSelectedLeadId is
  // stable), so the React.memo'd LeadRow doesn't bust its memoisation
  // on every parent render. Saves ~500 re-renders per parent tick on
  // the GTM table.
  // When a TOUCHES icon is clicked we open the lead AND jump straight to the
  // matching Content sub-tab (Email / LinkedIn). This ref tells the
  // "reset rightTab" effect below to leave our explicit choice alone.
  const explicitChannelRef = useRef(false);
  const handleLeadRowClick = useCallback((id, campaignId, channel) => {
    setSelectedLeadId(String(id));
    setSelectedCampaignId(campaignId ?? null);
    if (channel) {
      explicitChannelRef.current = true;
      setRightTab('content');
      setContentChannel(channel);
    } else {
      explicitChannelRef.current = false;
    }
  }, []);

  // ── Effects ──────────────────────────────────────────────────────────────

  // On mount: products + filter schema + initial list.
  useEffect(() => {
    loadProducts();
    loadFilterSchema('all');
    loadList('all');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-fetch the filter schema when the bucket view flips. The facet
  // counts reflect the post-view set, so they need to refresh.
  useEffect(() => {
    loadFilterSchema(view);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  // Debounced reload on sidebar filter / sort / SEARCH changes. Search now
  // round-trips to the backend (debounced) so it spans ALL leads across every
  // searchable column, not just the 25/page currently loaded client-side.
  useEffect(() => {
    const t = setTimeout(() => {
      loadList(view);
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedFilters, sortKey, search]);

  // When selectedLeadId changes, fetch detail (scoped to the row's campaign).
  useEffect(() => {
    if (selectedLeadId) loadDetail(selectedLeadId, selectedCampaignId);
  }, [selectedLeadId, selectedCampaignId, loadDetail]);

  // Reset the detail-poll counter ONLY when the user selects a
  // different lead. Previously the reset lived inside the polling
  // effect itself, which meant every successful poll (which mutates
  // selectedDetail and re-runs the effect) wiped the counter back to
  // 0 — and the MAX_POLLS cap never tripped. With the reset isolated
  // here, the cap actually works: 12 × 10s ≈ 2 minutes and then we
  // stop, instead of polling forever on every selected lead.
  useEffect(() => {
    detailPollCountRef.current = 0;
  }, [selectedLeadId]);

  // Detail auto-refresh — DISABLED.
  //
  // Previously this fired every 10s after a lead was selected, while
  // the timeline still had unsettled events. User reported the page
  // was reloading every 10 seconds and feeling stuck — this poll was
  // the cause. The Refresh button in the header pulls the latest
  // state on demand; the operator can use that when waiting for a
  // LinkedIn draft to finish generating.
  useEffect(() => {
    // Always clean up any in-flight poll when the selection changes.
    if (detailPollTimerRef.current) {
      clearTimeout(detailPollTimerRef.current);
      detailPollTimerRef.current = null;
    }

    // No-op — no auto-poll.
    return () => {
      if (detailPollTimerRef.current) {
        clearTimeout(detailPollTimerRef.current);
        detailPollTimerRef.current = null;
      }
    };
  }, [selectedLeadId, selectedDetail, loadDetail]);

  // When view/product changes, reload list.
  useEffect(() => {
    loadList(view);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, selectedProductId]);

  // Reload when this tab is (re)activated by the parent NexusLayout.
  // The layout increments `activateNonce` every time the user navigates
  // INTO this section from a different one. Without this, the keep-alive
  // mount preserves the previous leads array — so a user who just uploaded
  // new manual leads via the wizard would see a stale list until they hit
  // Refresh. We skip the very first mount (handled by the initial-load
  // effect) and only refetch on subsequent re-entries.
  const _firstNonceMount = useRef(true);
  useEffect(() => {
    if (_firstNonceMount.current) {
      _firstNonceMount.current = false;
      return;
    }
    loadList(view);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activateNonce]);

  // Auto-poll while the leads list is empty. Previously this fired every
  // 5 seconds (12 polls in 60s = ~24 DB queries/minute on an empty page),
  // which hammered the backend during the discovery window. Switched to
  // exponential backoff via setTimeout — 15s, 30s, 60s — capped at 3 polls
  // over ~2 minutes. A populated list short-circuits and clears the timer.
  // The user still has the manual Refresh button for anything beyond that.
  useEffect(() => {
    if (leads.length > 0) {
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
      pollCountRef.current = 0;
      return undefined;
    }
    const BACKOFF_MS = [15000, 30000, 60000];
    let cancelled = false;

    const schedule = () => {
      if (cancelled) return;
      const i = pollCountRef.current;
      if (i >= BACKOFF_MS.length) return; // give up after the final delay
      pollTimerRef.current = setTimeout(() => {
        if (cancelled) return;
        pollCountRef.current = i + 1;
        loadList(view);
        schedule();
      }, BACKOFF_MS[i]);
    };
    schedule();

    return () => {
      cancelled = true;
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
      // (List-status poll is owned by a separate effect below — no
      // cleanup needed here.)
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leads.length, view]);

  // List-status auto-refresh — DISABLED.
  //
  // Previously this refetched the entire leads list every 15s while any
  // visible row was still in `queued`/`new` status. With 500+ leads
  // that meant a heavy `/journey/leads` call + `setLeads(newArray)` +
  // a full re-render of the table every 15 seconds in perpetuity (the
  // `!hasPending` short-circuit reset the cap every time pending
  // toggled to false, so the MAX_POLLS guard never settled).
  //
  // User reported the page was "reloading every 10 seconds" — this
  // poll combined with the detail-poll was the cause. The page now
  // sticks to whatever the user fetched manually. The Refresh button
  // in the header is still available for the operator to pull the
  // latest state on demand.
  useEffect(() => {
    if (listPollTimerRef.current) {
      clearTimeout(listPollTimerRef.current);
      listPollTimerRef.current = null;
    }
    return undefined;
  }, []);

  // ── Derived ──────────────────────────────────────────────────────────────

  const filteredLeads = useMemo(() => {
    // Normalised partial match — strip everything but [a-z0-9] so "JPMorgan
    // Chase" matches "jpmorganchase" / "j.p. morgan", etc. MUST mirror the
    // backend's `_norm`/`_fuzzy_contains` + field set (journey.py) so this
    // client-side pass never drops a row the backend already matched.
    const norm = (v) => String(v ?? '').toLowerCase().replace(/[^a-z0-9]/g, '');
    // Approximate substring match (min edit distance of `pattern` vs any
    // substring of `text`) so typos like "hyera" still hit "hyderabad".
    const fuzzyContains = (pattern, text, maxDist) => {
      const m = pattern.length, n = text.length;
      if (m === 0) return true;
      if (n === 0) return m <= maxDist;
      let prev = new Array(n + 1).fill(0);
      for (let i = 1; i <= m; i++) {
        const cur = new Array(n + 1).fill(0);
        cur[0] = i;
        const pc = pattern[i - 1];
        for (let j = 1; j <= n; j++) {
          const cost = pc === text[j - 1] ? 0 : 1;
          cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
        }
        prev = cur;
      }
      return Math.min(...prev) <= maxDist;
    };
    const q = norm(search);
    if (!q) return leads;
    const maxDist = q.length < 4 ? 0 : (q.length <= 6 ? 1 : 2);
    // A purely-numeric query is a Campaign ID lookup — match campaign_number
    // EXACTLY, not as a substring of emails/names/etc. (typing "1" must not
    // surface a lead whose email merely contains a "1"). Mirrors journey.py.
    const qIsNumeric = /^[0-9]+$/.test(q);
    return leads.filter((l) => {
      if (qIsNumeric) return norm(l.campaign_number) === q;
      const norms = [
        l.name,
        l.first_name,
        l.last_name,
        l.email,
        l.company,
        l.company_domain,
        l.job_title,          // TITLE
        l.product_name,       // CAMPAIGN
        l.campaign_number,    // CAMPAIGN ID
        l.location,           // LOCATION
      ].map(norm).filter((v) => v !== '');
      if (norms.some((v) => v.includes(q))) return true;       // sharp substring
      if (maxDist) return norms.some((v) => fuzzyContains(q, v, maxDist)); // typo-tolerant
      return false;
    });
  }, [leads, search]);

  // Split visible products into Products row + Services row. Memoised so
  // the render path doesn't recompute on every keystroke in the search
  // box. `entity_type` is normalised inside loadProducts so legacy
  // backends (no top-level entity_type) still fall into Products.
  const productList = useMemo(
    () => products.filter((p) => p.entity_type !== 'service'),
    [products],
  );
  const serviceList = useMemo(
    () => products.filter((p) => p.entity_type === 'service'),
    [products],
  );

  // Reset rightTab when lead changes — unless the user opened the lead by
  // clicking a TOUCHES icon, in which case their Content/Email|LinkedIn
  // choice takes precedence.
  useEffect(() => {
    if (explicitChannelRef.current) {
      explicitChannelRef.current = false;
      return;
    }
    if (selectedDetail?.lead?.status === 'demo_scheduled') {
      setRightTab('flow');
    }
  }, [selectedLeadId, selectedDetail?.lead?.status]);

  // ── Actions ──────────────────────────────────────────────────────────────

  const onHide = async () => {
    if (!selectedLeadId) return;
    setWorking(true);
    try {
      await authAxios.post(`${journeyBase}/leads/${selectedLeadId}/hide`, {
        reason: 'manual_hide',
      });
      await loadList(view);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setWorking(false);
    }
  };

  const onUnhide = async () => {
    if (!selectedLeadId) return;
    setWorking(true);
    try {
      await authAxios.post(`${journeyBase}/leads/${selectedLeadId}/unhide`);
      await loadList(view);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setWorking(false);
    }
  };

  const onAutoHideStale = async () => {
    setWorking(true);
    try {
      await authAxios.post(`${journeyBase}/actions/auto-hide-stale`, {
        min_attempts: 3,
      });
      await loadList(view);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setWorking(false);
    }
  };

  // Refresh = reconcile + reload. Besides re-fetching the table, it catches up
  // any qualified lead that hasn't been started yet: the backend enrolls them
  // and kicks draft generation; the sequencer then sends paced by mailbox.
  const onRefresh = async () => {
    setWorking(true);
    try {
      const res = await authAxios.post(`${journeyBase}/reconcile-outreach`);
      const n = res?.data?.enrolled || 0;
      if (n > 0 && setMessage) {
        setMessage(`Started outreach for ${n} lead${n === 1 ? '' : 's'}.`);
      }
    } catch (err) {
      // Best-effort: still refresh the view even if reconcile failed.
      setError(err?.response?.data?.detail || err.message);
    } finally {
      await loadList(view);
      refreshCredits();
      setWorking(false);
    }
  };

  const onRegenerateBriefing = async () => {
    if (!selectedLeadId) return;
    setDemoLoading(true);
    try {
      await authAxios.post(`${journeyBase}/leads/${selectedLeadId}/demo/regenerate`);
      setTimeout(() => loadDemoData(selectedLeadId), 3000);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
      setDemoLoading(false);
    }
  };

  // ── Render ───────────────────────────────────────────────────────────────

  const selectedLead = selectedDetail?.lead;
  const timeline = selectedDetail?.timeline || [];
  const isDemoScheduled = selectedLead?.status === 'demo_scheduled';

  return (
    <div className="bg-white h-full flex flex-col min-h-0">
      {/* Header
          Rebuilt as title + chip-style stats + a single Refresh CTA.
          Dropped the "stale ≥3 attempts" inline metric and the
          "Auto-hide stale (N)" button — they were noisy power-user
          features that confused the operator about whether something
          was wrong. The underlying endpoint is still wired if we want
          to bring it back as an explicit action later (e.g. inside a
          settings dialog). */}
      <div className="px-5 py-2 border-b border-[#2B2926]/10 flex items-center justify-between gap-3 flex-wrap shrink-0">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          {/* Back — returns to the previous tab (e.g. the Campaign launched
              page reached via "View leads"). Only shown when the layout
              passed an onBack (i.e. there's a previous section). */}
          {onBack && (
            <button
              type="button"
              onClick={onBack}
              className="inline-flex items-center gap-1.5 text-xs font-bold text-white shrink-0"
              style={{ background: '#0F1115', borderRadius: 8, padding: '6px 12px' }}
              onMouseEnter={(e) => (e.currentTarget.style.background = '#1c2128')}
              onMouseLeave={(e) => (e.currentTarget.style.background = '#0F1115')}
            >
              <ChevronLeft className="w-3.5 h-3.5" />
              Back
            </button>
          )}
          {/* "Lead Journey" — normal weight per request (was font-black) */}
          <h1 className="text-base font-medium text-[#2B2926] whitespace-nowrap">
            Lead Journey
          </h1>
          {/* Active + Archived stat pills. "· N total" string was
              dropped per request — same total appears in the result-
              count line below the search bar, so showing it twice was
              redundant. Pills upgraded to fuller rounded-full chips
              with stronger contrast: Active uses the brand orange,
              Archived uses a neutral slate so it reads as the muted
              of the two without disappearing. */}
          <div className="flex items-center gap-2">
            <span
              className="inline-flex items-center gap-1.5 pl-2 pr-2.5 py-1 rounded-full bg-[#F55600]/10 text-[#F55600] text-[11px] font-semibold border border-[#F55600]/25"
              title="Leads currently visible in the active list"
            >
              <span className="font-bold tabular-nums">{summary.active}</span>
              <span>Active</span>
            </span>
            {/* Archived pill only shows when there ARE archived leads — hiding
                the "0 Archived" chip avoids confusion. */}
            {(summary.hidden || 0) > 0 && (
              <span
                className="inline-flex items-center gap-1.5 pl-2 pr-2.5 py-1 rounded-full bg-[#2B2926]/[0.04] text-[#2B2926] text-[11px] font-semibold border border-[#2B2926]/15"
                title="Leads archived from the active list (still in DB)"
              >
                <span className="font-bold tabular-nums">{summary.hidden}</span>
                <span>Archived</span>
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {onNavigate && !isReadOnly(user) && (
            <button
              type="button"
              onClick={() => onNavigate('upload-leads')}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#F55600] text-white text-xs font-bold hover:opacity-90 shadow-sm"
              title="Upload your own lead list (CSV / Excel)"
            >
              <Upload className="w-3.5 h-3.5" />
              Upload Leads
            </button>
          )}
          <CreditsBadge balance={creditBalance} onBuy={openBuyCredits} />
          <button
            type="button"
            onClick={onRefresh}
            disabled={loading || working}
            title="Refresh the table and start outreach for any qualified leads that haven't begun yet"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#2B2926]/30 text-xs font-medium text-[#2B2926] bg-white/70 backdrop-blur hover:bg-white hover:border-[#2B2926]/50 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={['w-3.5 h-3.5', (loading || working) ? 'animate-spin' : ''].join(' ')} />
            Refresh
          </button>
        </div>
      </div>

      <BuyCreditsModal
        open={buyCreditsOpen}
        onClose={() => {
          setBuyCreditsOpen(false);
          refreshCredits();
        }}
        authAxios={authAxios}
      />
      <CreditWarningPopup balance={creditBalance} onBuy={openBuyCredits} />

      {/* Error banner */}
      {error && (
        <div className="mx-5 mt-3 px-3 py-2 rounded-lg bg-[#F55600]/10 border border-[#F55600]/30 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-xs text-[#F55600]">
            <AlertCircle className="w-3.5 h-3.5" />
            {error}
          </div>
          <button
            type="button"
            onClick={() => setError('')}
            className="text-[#F55600] hover:opacity-70"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* Product / Service pill row moved into the CENTER column of the
          3-column workspace below so the new filter sidebar gets a clean
          left rail. (See the in-column FilterRow blocks lower down.) */}

      {/* 3-column lead workspace (2026-05-27 redesign).
          LEFT  — dynamic filter sidebar (driven by /journey/filters/schema)
          CENTER — sticky omnibox search + lead list (selectable cards)
          RIGHT  — selected lead's outreach details across tabs:
                   Content | Outreach Flow | Analytics
                   (and Brief when status=demo_scheduled).

          The selected-lead header that used to sit in the center now lives
          atop the right pane so the center can be a pure leads workspace.

          Uses `flex-1 min-h-0` so each column scrolls independently inside
          the page slot rather than the page itself scrolling. */}
      <div
        className="relative grid gap-0 border-t border-[#2B2926]/10 flex-1 min-h-0"
        // 2-column grid (filter + center list) — the lead detail pane is
        // no longer a grid column. When a lead is clicked it slides in as
        // an absolute-positioned overlay anchored to the right edge of
        // this container, sitting on top of the search bar + list. The
        // Back button on the overlay dismisses it. `relative` here gives
        // the overlay its positioning context. `minmax(0, 1fr)` keeps the
        // center column from blowing past its track on long lead strings.
        style={{ gridTemplateColumns: 'auto minmax(0, 1fr)' }}
      >
        {/* ── Left: dynamic filter sidebar ─────────────────────────────────
            Auto-expands on hover, auto-collapses to a 52px icon rail when
            the mouse leaves. The pin button keeps it open permanently. */}
        <div
          // Mouse enter/leave handlers removed — the sidebar no longer
          // auto-opens on hover. See `filtersOpen` declaration above.
          className="border-r border-[#2B2926]/10 flex flex-col min-h-0 overflow-hidden bg-white relative"
          style={{
            // On mobile, an expanded sidebar floats over the list as an
            // absolute overlay so it never squeezes the table to a sliver;
            // collapsed it stays a 52px in-flow rail. On desktop it always
            // pushes the grid track as before.
            width: isMobile && filtersOpen ? 'min(280px, 85vw)' : filtersOpen ? 280 : 52,
            transition: 'width 180ms ease',
            ...(isMobile && filtersOpen
              ? {
                  position: 'absolute',
                  top: 0,
                  bottom: 0,
                  left: 0,
                  zIndex: 30,
                  boxShadow: '0 8px 30px rgba(0,0,0,0.18)',
                }
              : {}),
          }}
        >
          {/* Callbacks are stable (defined via useCallback below the
              render scope) so the React.memo'd FilterSidebar only
              re-renders when schema / selected / loading actually
              change — not on every state tick in this big component. */}
          <FilterSidebar
            schema={filterSchema}
            loading={filterSchemaLoading}
            resultCount={totalCount}
            selected={selectedFilters}
            onChange={onSidebarChange}
            onClear={onSidebarClear}
            collapsed={!filtersOpen}
            pinned={filtersPinned}
            onTogglePin={onSidebarTogglePin}
          />
        </div>

        {/* ── Center: search + lead list ───────────────────────────────── */}
        <div className="flex flex-col min-h-0 bg-white">
          {/* Sticky omnibox + sort dropdown + result count */}
          <div className="px-4 py-3 border-b border-[#2B2926]/10 shrink-0 bg-white">
            <div className="flex items-center gap-2">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#2B2926]" />
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search campaign, ID, name, title, company, location…"
                  className="w-full pl-9 pr-9 py-2 rounded-lg border border-[#2B2926]/30 text-sm focus:outline-none focus:border-[#F55600] focus:ring-1 focus:ring-[#F55600]/30"
                />
                {search && (
                  <button
                    type="button"
                    onClick={() => setSearch('')}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-[#2B2926] hover:text-[#2B2926]"
                  >
                    <X className="w-4 h-4" />
                  </button>
                )}
              </div>
              {/* Sort dropdown removed from the UI. The `sortKey` state is
                  kept around so the backend's `sort` query param contract
                  is untouched — if a future surface wants to re-expose it,
                  just render the <select> again. */}
            </div>
            <div className="flex items-center justify-between mt-2">
              {/* Result-count line removed per request — the lead total
                  already shows in the Active/Archived pills at the top
                  of the page. We keep a "Loading leads…" indicator only
                  while the list is in-flight so the user sees feedback. */}
              <div className="text-[11px] text-[#2B2926]">
                {loading ? 'Loading leads…' : ''}
              </div>
              {/* Active filter chips summary */}
              {Object.values(selectedFilters).flat().length > 0 && (
                <button
                  type="button"
                  onClick={() => setSelectedFilters({})}
                  className="text-[10px] font-bold text-[#F55600] hover:underline inline-flex items-center gap-1"
                >
                  <X className="w-3 h-3" />
                  Clear filters
                </button>
              )}
            </div>
          </div>

          {/* Products / Services pill row removed 2026-05-27 — product
              filtering is now driven entirely by the "Product" group in
              the left FilterSidebar (sent as `product_ids`). Avoids two
              competing filter UIs and a silent endpoint switch that was
              bypassing the sidebar filters. */}

          {/* Lead table — scrollable in both axes; sticky header keeps
              the column labels visible while the body scrolls. Each row
              is a click target that opens the detail overlay. */}
          <div
            className="flex-1 overflow-auto"
            style={{ scrollbarGutter: 'stable', scrollbarWidth: 'thin' }}
            onScroll={onLeadsScroll}
          >
            {loading ? (
              <div className="p-3 space-y-1.5">
                <SkeletonCard />
                <SkeletonCard />
                <SkeletonCard />
                <SkeletonCard />
                <SkeletonCard />
                <SkeletonCard />
              </div>
            ) : filteredLeads.length === 0 ? (
              <div className="text-center py-16 px-4">
                <Inbox className="w-10 h-10 mx-auto text-[#2B2926] mb-3" />
                <p className="text-sm text-[#2B2926] font-bold mb-1">
                  No leads match
                </p>
                <p className="text-xs text-[#2B2926]">
                  {search || Object.values(selectedFilters).flat().length > 0
                    ? 'Try clearing search or filters.'
                    : 'Launch a campaign to populate this workspace.'}
                </p>
              </div>
            ) : (
              <>
              <style>{`
                .nx-lead-table, .nx-lead-table th, .nx-lead-table td, .nx-lead-table a, .nx-lead-table span, .nx-lead-table button{font-family:"ABC Diatype","ABC Diatype Mono",system-ui,-apple-system,sans-serif !important;}
                .nx-name-link:hover{color:#F55600 !important;border-bottom-color:#F55600 !important;}
                .nx-touch-link:hover{text-decoration:none !important;}
                ${colSizingCss}
                .nx-col-grip{position:absolute;top:0;right:-1px;height:100%;width:10px;cursor:col-resize;z-index:2;}
                .nx-col-grip::after{content:'';position:absolute;top:25%;right:4px;height:50%;width:2px;background:rgba(203,213,225,0.4);border-radius:2px;}
                .nx-col-grip:hover::after{background:#F55600;}
              `}</style>
              <table className="nx-lead-table w-full border-collapse text-left" style={{ fontSize: '13.5px', fontFamily: '"ABC Diatype", system-ui, -apple-system, sans-serif', ...Object.fromEntries(LEAD_COLUMNS.map((c) => [`--cw-${c.key}`, colW[c.key] + 'px'])) }}>
                <thead className="sticky top-0 z-10">
                  <tr>
                    {LEAD_COLUMNS.map((col) => (
                      <th
                        key={col.key}
                        className="text-left whitespace-nowrap"
                        style={{
                          // Per request (June 2026): deep near-black bg
                          // with cool light-slate text. Replaces the
                          // previous warm `#3F3C39 / #FFFFFF` pair to
                          // sharpen the table-header contrast and match
                          // the requested spec exactly.
                          background: '#111111',
                          color: '#CBD5E1',
                          fontSize: '11px',
                          fontWeight: 600,
                          letterSpacing: '0.6px',
                          textTransform: 'uppercase',
                          padding: '13px 10px',
                          position: 'relative',
                        }}
                      >
                        {col.label}
                        <span
                          className="nx-col-grip"
                          onMouseDown={startColResize(col.key)}
                          onClick={(e) => e.stopPropagation()}
                          title="Drag to resize column"
                        />
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {/* `LeadRowMemo` skips a re-render unless the row's
                      lead object, its index, its selected state, or
                      its onClick identity change. We build the
                      onClick lazily via the stable `handleLeadRowClick`
                      callback below — same callback for every row,
                      so memo sees a stable reference. */}
                  {filteredLeads.map((lead, idx) => (
                    <LeadRowMemo
                      key={lead.row_key || lead._id}
                      lead={lead}
                      index={idx}
                      selected={
                        String(lead._id) === String(selectedLeadId) &&
                        String(lead.campaign_id ?? '') === String(selectedCampaignId ?? '')
                      }
                      onSelect={handleLeadRowClick}
                    />
                  ))}
                </tbody>
              </table>
              </>
            )}
          </div>
        </div>

        {/* ── Right: outreach details (selected lead) ────────────────────
            Rendered only when a lead is actively selected (2026-05-28).
            Previously the pane stayed visible with a "Select a lead…"
            empty state; the new layout keeps the workspace focused on
            filters + results until the user explicitly drills into a lead.
            The Back button at the top clears the selection and returns
            the layout to its 2-column initial state. */}
        {selectedLeadId && (
          <div
            className="absolute top-0 right-0 bottom-0 z-20 border-l border-[#2B2926]/10 flex flex-col bg-white min-h-0 shadow-2xl"
            // Overlay stretches from the right edge all the way to the
            // right edge of the filter sidebar (which is 280px when open,
            // 52px when collapsed) — fully covering the search bar +
            // lead list while keeping the filter rail visible.
            style={{
              // Full-width on mobile (covers the rail too); on desktop it
              // stops at the filter rail's right edge.
              left: isMobile ? 0 : filtersOpen ? 280 : 52,
              transition: 'left 180ms ease',
            }}
          >
            {/* Back-to-list bar — clears selection, which also collapses
                the grid back to filter + list. Lives above the LeadHeader
                so all of the existing hide/unhide / tab UI stays intact. */}
            <div className="px-3 py-2 border-b border-[#2B2926]/10 flex items-center justify-between gap-2 shrink-0 bg-white">
              <button
                type="button"
                onClick={() => {
                  setSelectedLeadId('');
                  setSelectedDetail(null);
                }}
                className="inline-flex items-center gap-1"
                style={{
                  background: '#0F1115',
                  color: '#fff',
                  border: 'none',
                  borderRadius: 7,
                  padding: '4px 9px',
                  fontSize: 10.5,
                  fontWeight: 700,
                  cursor: 'pointer',
                  transition: 'background 0.15s',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.background = '#1c2128')}
                onMouseLeave={(e) => (e.currentTarget.style.background = '#0F1115')}
                title="Back to lead list"
              >
                <ChevronLeft className="w-3 h-3" />
                Back to list
              </button>
              {detailLoading && (
                <Loader2 className="w-3.5 h-3.5 text-[#2B2926] animate-spin" />
              )}
            </div>
            {selectedLead ? (
              <>
                <LeadHeader
                  lead={selectedLead}
                  timeline={timeline}
                  linkedinTouches={selectedDetail?.linkedin_touches}
                />
                {/* Tab strip — 3 fixed tabs. The standalone "Generated
                    Content" tab was merged into the Timeline view (now
                    labelled "Content") on 2026-05-28 since the timeline
                    already renders full email + LinkedIn bodies inline. */}
                <div className="px-3 pt-2 border-b border-[#2B2926]/15 flex items-stretch gap-1 shrink-0">
                  {[
                    { id: 'content',   label: 'Content'   },
                    { id: 'flow',      label: 'Flow'      },
                    { id: 'analytics', label: 'Analytics' },
                  ].map((tab) => (
                    <button
                      key={tab.id}
                      type="button"
                      onClick={() => setRightTab(tab.id)}
                      title={tab.label}
                      className={[
                        'flex-1 basis-0 min-w-0 px-3 py-2 rounded-t-lg text-[12px] font-semibold border-b-2 transition-colors truncate',
                        rightTab === tab.id
                          ? 'border-[#F55600] text-[#F55600]'
                          : 'border-transparent text-[#67655E] hover:text-[#2B2926]',
                      ].join(' ')}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>
                <div className="flex-1 overflow-auto">
                  {rightTab === 'content' && (
                    <div className="px-4 py-3">
                      {/* Email / LinkedIn sub-tabs — same shape as the
                          Flow tab's channel selector. Each sub-tab shows
                          ONLY the generated content for that channel. */}
                      <div className="flex items-center gap-2 mb-4">
                        <button
                          type="button"
                          onClick={() => setContentChannel('email')}
                          className={[
                            'flex-1 inline-flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg text-[13px] font-semibold transition-colors',
                            contentChannel === 'email'
                              ? 'bg-[#F55600] text-white hover:bg-[#e63e00]'
                              : 'bg-white text-[#2B2926] border border-[#2B2926]/20 hover:border-[#2B2926]/40',
                          ].join(' ')}
                        >
                          <Mail className="w-3.5 h-3.5" />
                          Email
                        </button>
                        <button
                          type="button"
                          onClick={() => setContentChannel('linkedin')}
                          className={[
                            'flex-1 inline-flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg text-[13px] font-semibold transition-colors',
                            contentChannel === 'linkedin'
                              ? 'bg-[#2B2926] text-white hover:bg-[#1f1d1b]'
                              : 'bg-white text-[#2B2926] border border-[#2B2926]/20 hover:border-[#2B2926]/40',
                          ].join(' ')}
                        >
                          <Linkedin className="w-3.5 h-3.5" />
                          LinkedIn
                        </button>
                      </div>
                      <GeneratedContentPanel
                        timeline={timeline}
                        loading={detailLoading}
                        channelFilter={contentChannel}
                        sequences={selectedDetail?.sequences || []}
                        leadName={
                          (selectedLead?.name || '').split(' ')[0] ||
                          selectedLead?.name ||
                          selectedLead?.email ||
                          ''
                        }
                      />
                    </div>
                  )}
                  {rightTab === 'flow' && (
                    <OutreachFlowPanel
                      lead={selectedLead}
                      timeline={timeline}
                      sequences={selectedDetail?.sequences || []}
                      campaigns={selectedDetail?.campaigns || []}
                      authAxios={authAxios}
                      apiBase={apiBase}
                    />
                  )}
                  {rightTab === 'analytics' && (
                    <LeadAnalyticsPanel
                      lead={selectedLead}
                      timeline={timeline}
                      onJumpToBrief={
                        // Jump to the dedicated Demo Agent tab in the parent
                        // NexusLayout. Falls back to no-op if the layout
                        // didn't pass the navigator (e.g. running standalone).
                        isDemoScheduled && onNavigate
                          ? () => onNavigate('your-bookings')
                          : undefined
                      }
                    />
                  )}
                </div>
              </>
            ) : (
              // Selection set but detail still in-flight — show a soft
              // loading state instead of an empty box.
              <div className="flex-1 flex items-center justify-center p-8">
                <Loader2 className="w-6 h-6 text-[#2B2926]/30 animate-spin" />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default NexusJourney;
