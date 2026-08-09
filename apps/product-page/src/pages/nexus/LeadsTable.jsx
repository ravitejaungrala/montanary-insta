/**
 * LeadsTable — table of leads for a campaign.
 *
 * Used on TWO surfaces:
 *   1. New Run "launched" step (NexusNewCampaign.jsx)
 *   2. GTM Journey page (NexusJourney.jsx) — eventual integration
 *
 * Fetches GET /nexus/campaigns/{id}/leads on a polling interval while
 * the backend reports status="discovering". Stops polling once status
 * flips to "done".
 *
 * Columns (10):
 *   # · Fit Score · First Name · Last Name · Title · Company Name
 *   · Company URL · LinkedIn
 *
 * Each row is clickable — fires `onLeadClick(lead)` so the parent can
 * open the Content/Flow/Analytics right-panel that another developer
 * is building.
 */
import React, { useEffect, useRef, useState } from 'react';
import { Linkedin, Phone, RefreshCw, Check, Loader2 } from 'lucide-react';

// Match-score pill tiers (mirrors spenzo-table-v2-final.html):
//   ≥ 90 → dark green   ≥ 80 → green   else → light green
function matchTier(f) {
  const n = Number(f) || 0;
  if (n >= 90) return { fg: '#047857', bg: 'rgba(4,120,87,0.14)' };
  if (n >= 80) return { fg: '#10B981', bg: 'rgba(16,185,129,0.14)' };
  return { fg: '#34D399', bg: 'rgba(52,211,153,0.18)' };
}

// Discovery funnel tabs. "accepted" is the default view — these are the
// leads the signal agent picked and that are entering outreach.
// 2026-06-04 — 'Not picked' (rejected) leads are no longer shown: only the
// accepted set flows to the campaign, so the funnel surfaces Accepted +
// the transient Checking-signals state only.
const FUNNEL_TABS = [
  { key: 'accepted', label: 'Accepted' },
  { key: 'scoring', label: 'Checking signals' },
];


export default function LeadsTable({
  campaignId,
  authAxios,
  apiBase,
  onLeadClick = () => {},
  // Fired after the operator successfully hits "Start Outreach" (receives
  // the endpoint's { enrolled, ... } payload) so the parent wizard can
  // advance if it wants. Default noop.
  onOutreachStarted = () => {},
  // Opt-in: show the campaign-level "Start Outreach" CTA. Only the New-Run
  // launch surface sets this true; the GTM Journey surface leaves it off
  // (its leads are already live, nothing to launch). Defaults off so the
  // button never appears where it shouldn't.
  enableStartOutreach = false,
  // When the parent already has a list of leads (e.g. GTM Journey
  // surface that has its own data source), pass them in directly and
  // we skip the polling. Default null = self-fetch.
  initialLeads = null,
  // Override the auto-poll interval (ms). Default 2000.
  pollIntervalMs = 2000,
  // When true, ask the backend for ONLY the leads found in the campaign's
  // latest run (run-fenced), not the whole accumulated history. The New-Run
  // launch surface sets this; the GTM Journey / history surface leaves it
  // false so it still shows every lead on the campaign.
  latestRunOnly = false,
  // Toast callback (parent's setMessage). Used to show ONE "emails generated"
  // message when the background email-content generation completes. Default noop.
  setMessage = () => {},
}) {
  // Are any seeded leads still being scored by the signal agent? If so we
  // must keep polling even though initialLeads was provided — otherwise the
  // funnel would freeze on "Checking" and never advance to Accepted.
  const seededScoring = (initialLeads || []).some(
    (l) => (l.intent_stage || 'accepted') === 'scoring',
  );

  function seedFunnel(rows) {
    const f = { found: rows.length, scoring: 0, accepted: 0, rejected: 0 };
    rows.forEach((l) => {
      f[(l.intent_stage || 'accepted')] = (f[(l.intent_stage || 'accepted')] || 0) + 1;
    });
    return f;
  }

  const [leads, setLeads] = useState(initialLeads || []);
  const [status, setStatus] = useState(
    initialLeads && !seededScoring ? 'done' : 'discovering',
  );
  const [total, setTotal] = useState(initialLeads ? initialLeads.length : 0);
  const [error, setError] = useState('');
  // Discovery funnel counts + active tab. Default to the Accepted view.
  const [funnel, setFunnel] = useState(
    initialLeads ? seedFunnel(initialLeads) : { found: 0, scoring: 0, accepted: 0, rejected: 0 },
  );
  const [activeStage, setActiveStage] = useState('accepted');
  // Outreach now AUTO-STARTS once Agent #10 finishes scoring (no operator
  // approval). This flag, mirrored from the /leads response, just drives the
  // passive "Outreach started" status pill.
  const [outreachApproved, setOutreachApproved] = useState(false);
  // Once the user clicks a tab we stop auto-focusing for them.
  const userPickedTab = useRef(false);
  // Guards against overlapping score-pending requests (each runs ~1 min).
  const scoringInFlight = useRef(false);
  // Fire the "emails generated" toast only once when drafts_ready flips true.
  const draftsToastShown = useRef(false);
  // Manual-refresh nonce — bumped by the Refresh button. Re-runs the
  // poll effect with a fresh attempt budget.
  const [pollNonce, setPollNonce] = useState(0);
  // Used to compute the "· live" indicator next to the leads count.
  const [autoPolling, setAutoPolling] = useState(false);

  // Polling effect. Lifts the same adaptive-cadence pattern as
  // CampaignOutboundEmailsPreview: 2 s for the first ~3 min (fast),
  // then 5 s for the next ~5 min (slower), then stop after ~21 min.
  // The user can hit Refresh to restart polling at any time.
  useEffect(() => {
    if (!campaignId) return undefined;
    // Skip polling only for a STATIC seeded list (no leads still scoring).
    // When seeded leads are mid-scoring we poll so the funnel advances.
    if (initialLeads && !seededScoring) return undefined;

    let cancelled = false;
    let attempts = 0;
    setAutoPolling(true);
    setError('');

    function nextDelay(n) {
      if (n < 90) return pollIntervalMs;     // first ~3 min: 2 s cadence
      if (n < 150) return pollIntervalMs * 5; // next ~5 min: 10 s
      if (n < 200) return pollIntervalMs * 15; // next ~17 min: 30 s
      return null;                             // stop polling
    }

    async function poll() {
      if (cancelled) return;
      attempts += 1;
      try {
        const res = await authAxios.get(
          `${apiBase}/campaigns/${campaignId}/leads?limit=100${
            latestRunOnly ? '&latest_run=true' : ''
          }`,
        );
        if (cancelled) return;
        setLeads(res.data?.leads || []);
        setTotal(res.data?.total || 0);
        if (res.data?.funnel) setFunnel(res.data.funnel);
        setOutreachApproved(!!res.data?.outreach_approved);

        // Background EMAIL-content generation finished → show ONE short toast.
        const draftsReady = !!res.data?.drafts_ready;
        if (
          draftsReady &&
          res.data?.outreach_approved &&
          !draftsToastShown.current
        ) {
          draftsToastShown.current = true;
          const n = res.data?.drafts_count || res.data?.funnel?.accepted || 0;
          setMessage(
            n
              ? `Email content generated for ${n} lead${n === 1 ? '' : 's'} — ready to review.`
              : 'Email content generated — ready to review.',
          );
        }

        // Client-driven scoring: while leads are still being checked, kick a
        // synchronous score batch on the backend (works even on serverless,
        // where background tasks freeze). Fire-and-forget + a guard so we
        // never overlap; the regular poll keeps showing live updates and
        // fires the next batch when this one finishes.
        const scoringCount = res.data?.funnel?.scoring || 0;
        if (scoringCount > 0 && !scoringInFlight.current) {
          scoringInFlight.current = true;
          authAxios
            .post(`${apiBase}/campaigns/${campaignId}/score-pending`)
            .catch(() => {})
            .finally(() => { scoringInFlight.current = false; });
        }

        const next = res.data?.status || 'done';
        setStatus(next);
        setError('');
        // Status flipped to "done" → stop polling — UNLESS outreach has just
        // been approved and the background email-content generation hasn't
        // finished yet. In that case keep polling (no UI change) just long
        // enough to catch drafts_ready and fire the completion toast above.
        if (next === 'done' && !(res.data?.outreach_approved && !draftsReady)) {
          setAutoPolling(false);
          return;
        }
      } catch (err) {
        if (cancelled) return;
        setError(
          err?.response?.data?.detail ||
            err?.message ||
            'Failed to fetch leads',
        );
      }

      const delay = nextDelay(attempts);
      if (delay === null) {
        setAutoPolling(false);
        return;
      }
      window.setTimeout(poll, delay);
    }
    poll();

    return () => {
      cancelled = true;
      setAutoPolling(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaignId, pollNonce, initialLeads]);

  // Auto-focus the most relevant tab until the user manually picks one:
  // prefer Accepted if any exist, else Checking (fresh launch), else Not picked.
  // Without this, a just-launched campaign (0 accepted, N scoring) lands on an
  // empty Accepted panel even though Checking has the leads.
  useEffect(() => {
    if (userPickedTab.current) return;
    // Never auto-focus 'rejected' — not-picked leads are not shown. Fall back
    // to the Accepted (empty-state) view when there's nothing accepted/scoring.
    if (funnel.accepted > 0) setActiveStage('accepted');
    else if (funnel.scoring > 0) setActiveStage('scoring');
    else setActiveStage('accepted');
  }, [funnel.accepted, funnel.scoring, funnel.rejected]);

  // 2026-06-09 — funnel UI removed per request. Show ONE qualified-leads
  // table: every surfaced lead EXCEPT the ones the intent agent held back
  // (rejected). No Found / Checking signals / Accepted split.
  // 2026-06-11 — EXCEPTION: duplicate markers (Agent #10 approved the
  // person, but they're already in an earlier campaign of this product)
  // are stored as rejected so they can never enter outreach, but they DO
  // render here with an "Already in campaign" badge instead of vanishing.
  const isDuplicate = (l) => (l.intent && l.intent.drop_reason) === 'duplicate';
  // "10 Jun 2026" from the marker's ISO timestamp; '' when absent.
  const dupDate = (l) => {
    const raw = l.intent && l.intent.dup_date;
    if (!raw) return '';
    const d = new Date(raw);
    return Number.isNaN(d.getTime())
      ? ''
      : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
  };
  const visibleLeads = leads.filter(
    (l) => (l.intent_stage || 'accepted') !== 'rejected' || isDuplicate(l),
  );
  // The "N qualified" count must reflect actionable leads only — duplicates
  // are informational rows, not deliveries.
  const qualifiedCount = visibleLeads.filter((l) => !isDuplicate(l)).length;
  const duplicateCount = visibleLeads.length - qualifiedCount;

  // 2026-06-09 — show NOTHING but a loader until discovery + scoring finish,
  // then render the final qualified-leads table with scores. (Email
  // generation continues in the backend afterward — the UI doesn't wait.)
  const isWorking = status === 'discovering' || (funnel.scoring || 0) > 0;

  if (!campaignId && !initialLeads) return null;

  return (
    <div className="mt-6">
      {isWorking ? (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <Loader2 className="w-6 h-6 animate-spin text-[#F55600] mb-3" />
          <p className="text-sm font-semibold text-black/70">
            {status === 'discovering'
              ? 'Finding your leads…'
              : 'Qualifying & scoring leads…'}
          </p>
          <p className="text-xs text-black/40 mt-1">
            The qualified leads and their match scores will appear here once
            this finishes.
          </p>
        </div>
      ) : (
      <>
      {/* Header bar: lead count + live indicator + refresh.
          Stacks on mobile so "N leads · live" + Refresh drop to their own
          row instead of overlapping the title/subtitle. */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-3">
        <div className="min-w-0">
          <h3 className="text-xs uppercase tracking-wider font-black text-black/60">
            Leads
          </h3>
          <p className="text-[11px] text-black/40 mt-0.5">
            Click any lead to open content, flow, and analytics.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {/* Outreach auto-starts when Agent #10 finishes scoring — no
              approval click. This is just a passive status pill. */}
          {outreachApproved && enableStartOutreach && (
            <span
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-bold"
              style={{ color: '#065F46', background: '#ffffff', border: '1px solid #065F46' }}
              title="Outreach has started for the accepted leads"
            >
              <Check size={12} /> Outreach started
            </span>
          )}
          <span className="text-[10px] font-bold text-black/40">
            {/* Count the QUALIFIED rows actually rendered, not the raw
                fetched total. Duplicate markers are listed but counted
                separately — they're informational, not deliveries. */}
            {`${qualifiedCount} qualified${
              duplicateCount > 0 ? ` · ${duplicateCount} already in campaign` : ''
            }${autoPolling ? ' · live' : ''}`}
          </span>
          <button
            type="button"
            onClick={() => setPollNonce((n) => n + 1)}
            className="inline-flex items-center gap-1 px-2 py-1 rounded text-[10px] font-bold text-black/60 hover:text-[#F55600] hover:bg-[#F55600]/5 border border-black/10"
            title="Refresh"
          >
            <RefreshCw size={11} />
            Refresh
          </button>
        </div>
      </div>

      {/* Funnel tabs (Found / Checking signals / Accepted) removed per
          request — we show a single qualified-leads table instead. */}

      {error && (
        <p className="text-[11px] text-[#F55600] mb-2">{error}</p>
      )}

      {visibleLeads.length === 0 && (
        <div className="border border-dashed border-black/10 rounded-xl px-4 py-8 text-center">
          <p className="text-sm text-black/50">
            {status === 'discovering'
              ? 'Discovery is running — qualified leads will appear here.'
              : 'No qualified leads for this campaign yet.'}
          </p>
        </div>
      )}

      {visibleLeads.length > 0 && (
        // Spenzo leads table — dark header, green match pill, name link,
        // blue LinkedIn icon, product pill, touches (mail + linkedin
        // counts), and a green contact/call icon (or — when no phone).
        <div
          className="overflow-x-auto bg-white"
          style={{
            border: '1px solid #E5E7EB',
            borderRadius: 14,
            boxShadow: '0 8px 24px rgba(17,24,39,0.06)',
          }}
        >
          <table className="w-full" style={{ borderCollapse: 'collapse', fontSize: 13.5 }}>
            <thead>
              <tr>
                {['S.NO', 'MATCH', 'NAME', 'TITLE', 'COMPANY', 'LINKEDIN', 'EMAIL', 'CONTACT', 'LOCATION'].map((h) => (
                  <th
                    key={h}
                    style={{
                      textAlign: 'left',
                      fontSize: 11,
                      fontWeight: 600,
                      letterSpacing: '0.6px',
                      textTransform: 'uppercase',
                      color: '#CBD5E1',
                      padding: '13px 16px',
                      whiteSpace: 'nowrap',
                      background: '#111111',
                    }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visibleLeads.map((lead, idx) => {
                const name = `${lead.first_name || ''} ${lead.last_name || ''}`.trim() || lead.email || 'Unknown';
                const tier = matchTier(lead.fit_score);
                // All captured numbers (scenario 9). `phone` is the primary shown
                // as click-to-dial; any extras are surfaced as a subtle "+N" badge.
                const phones = Array.isArray(lead.phones) ? lead.phones.filter(Boolean) : [];
                const phone = lead.phone || lead.phone_number || lead.mobile || phones[0] || '';
                const extraPhoneCount = Math.max(0, (phones.length || (phone ? 1 : 0)) - 1);
                return (
                  <tr
                    key={lead.lead_id || lead.global_lead_id || idx}
                    onClick={() => onLeadClick(lead)}
                    className="cursor-pointer"
                    style={{ transition: 'background 0.12s' }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = '#FFF6F2')}
                    onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                  >
                    {/* # */}
                    <td style={{ padding: '13px 16px', borderTop: '1px solid #F1F2F4', color: '#9AA0AA', fontVariantNumeric: 'tabular-nums', width: 40 }}>
                      {idx + 1}
                    </td>
                    {/* Match pill */}
                    <td style={{ padding: '13px 16px', borderTop: '1px solid #F1F2F4' }}>
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
                        {/* Duplicate markers show Agent #10's re-vet score;
                            legacy markers without one show a dash instead of
                            the misleading firmographic 100. */}
                        {isDuplicate(lead) && !(lead.intent && lead.intent.score)
                          ? '—'
                          : (lead.fit_score ?? 0)}
                      </span>
                    </td>
                    {/* Name link (+ "Already in campaign" badge for duplicate
                        markers — shown but never part of outreach) */}
                    <td style={{ padding: '13px 16px', borderTop: '1px solid #F1F2F4' }}>
                      <span
                        className="hover:text-[#FF4500]"
                        style={{ color: '#111111', fontWeight: 600, borderBottom: '1px solid transparent' }}
                      >
                        {name}
                      </span>
                      {isDuplicate(lead) && (
                        <span
                          title={`This person was already contacted${
                            lead.intent && lead.intent.dup_campaign
                              ? ` in "${lead.intent.dup_campaign}"`
                              : ' in an earlier campaign for this product'
                          }${dupDate(lead) ? ` on ${dupDate(lead)}` : ''} — no new outreach will be sent.`}
                          style={{
                            marginLeft: 8,
                            padding: '2px 8px',
                            borderRadius: 999,
                            fontSize: 10.5,
                            fontWeight: 700,
                            color: '#92400E',
                            background: 'rgba(245,158,11,0.14)',
                            border: '1px solid rgba(245,158,11,0.35)',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {(lead.intent && lead.intent.dup_campaign
                            ? `Already in ${lead.intent.dup_campaign}`
                            : 'Already in campaign')
                            + (dupDate(lead) ? ` · ${dupDate(lead)}` : '')}
                        </span>
                      )}
                    </td>
                    {/* Title */}
                    <td
                      style={{ padding: '13px 16px', borderTop: '1px solid #F1F2F4', color: '#6B7280', maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                      title={lead.title}
                    >
                      {lead.title || '—'}
                    </td>
                    {/* Company */}
                    <td style={{ padding: '13px 16px', borderTop: '1px solid #F1F2F4', color: '#6B7280' }}>
                      {lead.company_name || '—'}
                    </td>
                    {/* LinkedIn icon only (brand blue) */}
                    <td style={{ padding: '13px 16px', borderTop: '1px solid #F1F2F4' }}>
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
                        <span style={{ color: '#9AA0AA' }}>—</span>
                      )}
                    </td>
                    {/* Email */}
                    <td style={{ padding: '13px 16px', borderTop: '1px solid #F1F2F4', color: '#6B7280', fontVariantNumeric: 'tabular-nums' }}>
                      {lead.email || '—'}
                    </td>
                    {/* Contact — primary phone (click-to-dial) + a subtle "+N"
                        badge when the lead has more numbers on file, else — */}
                    <td style={{ padding: '13px 16px', borderTop: '1px solid #F1F2F4' }}>
                      {phone ? (
                        <span className="inline-flex items-center">
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
                            <span
                              title={`All numbers:\n${phones.join('\n')}`}
                              style={{
                                marginLeft: 6,
                                padding: '1px 6px',
                                borderRadius: 999,
                                fontSize: 11,
                                fontWeight: 600,
                                background: '#ECFDF5',
                                color: '#10B981',
                                fontVariantNumeric: 'tabular-nums',
                                cursor: 'default',
                              }}
                            >
                              +{extraPhoneCount}
                            </span>
                          )}
                        </span>
                      ) : (
                        <span style={{ color: '#9AA0AA' }}>—</span>
                      )}
                    </td>
                    {/* Location — joined city/state/country from nexus_global_leads.
                        "—" when Apollo didn't return one. */}
                    <td
                      style={{ padding: '13px 16px', borderTop: '1px solid #F1F2F4', color: '#6B7280', minWidth: 200, whiteSpace: 'normal', lineHeight: 1.35 }}
                      title={lead.location || ''}
                    >
                      {lead.location || '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      </>
      )}
    </div>
  );
}
