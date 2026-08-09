  /**
 * OutreachFlowPanel — right-pane stage diagram (port of legacy
 * OutreachFlowPanelContent).
 *
 * Two channel tabs (email / linkedin). Each renders a vertical sequence
 * of stage nodes that flip to "done" based on what events exist in the
 * timeline. Footer shows aggregate counts (emails / replies / calls).
 *
 * Stage diagram is BUILT CLIENT-SIDE from the timeline array — no
 * extra backend call.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Check,
  ChevronDown,
  Circle,
  Copy,
  Flag,
  Linkedin,
  Mail,
  MailOpen,
  MessageSquare,
  PhoneCall,
  RotateCw,
  Send,
  Sparkles,
  StickyNote,
  Target,
  UserCheck,
  UserPlus,
} from 'lucide-react';
import { fmtDateShort } from './NexusJourney';

// Resolve a node's accent hex from its tailwind `dotClass` so the horizontal
// stepper can colour the circle, connector and icon consistently.
const accentHex = (dotClass = '') =>
  dotClass.includes('F55600') ? '#F55600'
  : dotClass.includes('10B981') ? '#10B981'
  : '#0F1115';

const FLOW_FONT = 'Inter, system-ui, -apple-system, sans-serif';

// Plain-language label for a classified reply intent — used both for the
// Reply Received subtitle and the "Sequence ended" reason (Shape 2, see
// flow_tab_dynamic_plan.md §6). Falls back to the raw code so an intent
// added later never renders blank.
const INTENT_LABELS = {
  OUT_OF_OFFICE: 'Out of office',
  NOT_NOW: 'Not right now',
  QUESTION: 'Asked a question',
  QUESTION_PRICE: 'Asked about pricing',
  INTERESTED: 'Interested',
  NOT_INTERESTED: 'Not interested',
  UNSUBSCRIBE: 'Unsubscribed',
  DEMO_SCHEDULED: 'Demo booked',
};
const humanizeIntent = (intent) => INTENT_LABELS[(intent || '').toUpperCase()] || intent || '';

// LinkedIn `stop_reason` codes (gtm_linkedin_events.sequence_stopped) in plain
// language. Distinct from INTENT_LABELS: several reasons have no intent behind
// them at all (we ran out of InMail credit, a human took the thread over).
const LI_STOP_LABELS = {
  replied: 'They replied',
  not_interested: 'Not interested',
  unsubscribe: 'Asked us to stop',
  demo_scheduled: 'Demo booked',
  conversation_cap: 'Handed to a human',
  human_takeover: 'A colleague took over',
  no_inmail_credit: 'No InMail credit left',
  unreachable_no_connect_no_inmail: 'Unreachable on LinkedIn',
  backfill_unresolved: 'Paused for review',
};
const humanizeStop = (reason) => {
  const key = (reason || '').toLowerCase();
  if (LI_STOP_LABELS[key]) return LI_STOP_LABELS[key];
  // Compound reasons carry a prefix, e.g. "max_deferrals:job_failed:send_message"
  // and "handoff_ungroundable" — surface something meaningful rather than raw.
  if (key.startsWith('max_deferrals')) return 'Kept failing — stopped';
  if (key.startsWith('handoff_')) return 'Handed to a human';
  return reason || '';
};

// Label for a planned touch, by its semantic role.
const LI_TOUCH_LABELS = {
  opener: 'First Message',
  followup: 'Follow-up',
  close: 'Closing Message',
  answer: 'Our Reply',
  request: 'Connection Request',
  check: 'Acceptance Check',
};

// Small rounded status/marker chip — used throughout the Flow timeline so
// "Done", "Replied", "Rewritten" etc. read as distinct pills rather than
// a run of same-size colored text.
const Badge = ({ color, bg, icon: Icon, title, children }) => (
  <span
    className="inline-flex items-center gap-1 text-[11px] font-bold rounded-full px-2 py-0.5 leading-none whitespace-nowrap"
    style={{ color, background: bg }}
    title={title}
  >
    {Icon && <Icon className="w-2.5 h-2.5" />}
    {children}
  </span>
);

// Real mailbox replies glue the new text directly onto the quoted trailing
// thread with no blank line ("...thanks. On Thu, Jul 16, 2026, 3:35 PM Jane
// Doe <jane@x.com> wrote: ..."). Cut the quoted portion so previews show
// only what the sender actually typed, not the reply-chain noise.
const QUOTE_PATTERNS = [
  /\bOn\s+\w+,?\s+\w+\s+\d{1,2},?\s+\d{4},?\s+\d{1,2}:\d{2}\s*(?:AM|PM)?\s+.+?wrote:/i,
  /-{2,}\s*Original Message\s*-{2,}/i,
  /^From:\s.+$/im,
];
const stripQuotedReply = (text) => {
  if (!text) return '';
  let cutAt = text.length;
  QUOTE_PATTERNS.forEach((re) => {
    const m = text.match(re);
    if (m && typeof m.index === 'number' && m.index < cutAt && m.index > 0) cutAt = m.index;
  });
  return text.slice(0, cutAt).trim();
};
const snippet = (text, max = 220) => {
  const clean = stripQuotedReply(text);
  return clean.length > max ? `${clean.slice(0, max).trim()}…` : clean;
};

const OutreachFlowPanel = ({
  lead,
  timeline = [],
  sequences = [],
  campaigns = [],
  authAxios,
  apiBase,
}) => {
  const [channelTab, setChannelTab] = useState('email');
  // Which reply node's "View Conversation" popover is open (click only, no
  // hover). Index into the rendered `stages` array; null when closed.
  const [openConversation, setOpenConversation] = useState(null);
  // One ref per node's popover, keyed by stage index, so opening one can be
  // scrolled into view automatically.
  const conversationRefs = useRef({});
  useEffect(() => {
    if (openConversation == null) return;
    const el = conversationRefs.current[openConversation];
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
  }, [openConversation]);

  // ── Lead identity (computed early — many effects depend on it) ──────────
  const leadId = lead?._id || lead?.id || null;

  // ── LinkedIn draft state (lazy-fetched on tab switch) ───────────────────
  // We only fetch when the user opens the LinkedIn tab so we don't burn an
  // HTTP call for every lead the user scrolls past. The sequencer tick
  // generates drafts every ~minute, so the very first selection on a
  // freshly-discovered lead may show 'no draft yet' until the next tick.
  const [linkedinDraft, setLinkedinDraft] = useState(null);
  // Sister state for the LinkedIn InMail variant — populated from the
  // same /linkedin/lead/:id fetch, filtered by variant='inmail'. The
  // sequencer generates one InMail alongside every DM (additive — DM
  // path untouched), so this card sits below the DM card whenever the
  // InMail draft exists.
  const [linkedinInmailDraft, setLinkedinInmailDraft] = useState(null);
  // Real per-lead flow state from /linkedin/lead/:id/flow — drives the
  // branch-aware LinkedIn stage diagram (connection → note → inmail →
  // acceptance → Messages|InMail branch). Null until fetched / when the
  // lead isn't enrolled in a LinkedIn sequence.
  const [linkedinFlow, setLinkedinFlow] = useState(null);
  const [linkedinLoading, setLinkedinLoading] = useState(false);
  const [linkedinError, setLinkedinError] = useState('');
  const [copied, setCopied] = useState(false);
  // Separate copied-flag for the InMail copy button so each card's
  // "Copied" feedback is independent.
  const [inmailCopied, setInmailCopied] = useState(false);

  // Multi-campaign leads can be enrolled in several sequences (e.g. one
  // for each product they fit). The flow panel renders one campaign's
  // sequence at a time; default to the most recently updated enrollment
  // and let the operator switch via the selector below.
  // Reset keyed on lead._id so switching leads always re-picks the
  // most-recent campaign for the new lead.
  const [activeCampaignId, setActiveCampaignId] = useState(null);
  useEffect(() => {
    if (!sequences || sequences.length === 0) {
      setActiveCampaignId(null);
      return;
    }
    // sequences are already sorted DESC by updated_at server-side.
    const firstWithCampaign = sequences.find((s) => s?.campaign_id != null);
    setActiveCampaignId(firstWithCampaign?.campaign_id ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leadId, sequences]);

  const activeSequence = useMemo(() => {
    if (!activeCampaignId) return sequences?.[0] || null;
    return (
      sequences.find((s) => s?.campaign_id === activeCampaignId) ||
      sequences?.[0] ||
      null
    );
  }, [sequences, activeCampaignId]);

  useEffect(() => {
    // Reset draft state whenever the selected lead changes.
    setLinkedinDraft(null);
    setLinkedinInmailDraft(null);
    setLinkedinFlow(null);
    setLinkedinError('');
    setCopied(false);
    setInmailCopied(false);
    setOpenConversation(null);
  }, [leadId]);

  useEffect(() => {
    // Bail with no cleanup when we're not actively fetching. Removing
    // `linkedinDraft` from the dep array means the effect only runs on
    // tab/lead/auth changes, not when we set the draft (which would
    // otherwise re-fire the effect and skip the cleanup wire-up).
    if (channelTab !== 'linkedin') return undefined;
    if (!leadId || !authAxios || !apiBase) return undefined;

    let cancelled = false;
    setLinkedinLoading(true);
    setLinkedinError('');
    (async () => {
      try {
        // Fetch drafts + flow-state together. The flow call is best-effort
        // (its own catch) so a missing/500 flow endpoint never blanks the
        // draft cards.
        const [res, flowRes] = await Promise.all([
          authAxios.get(`${apiBase}/linkedin/lead/${leadId}`),
          authAxios
            .get(`${apiBase}/linkedin/lead/${leadId}/flow`)
            .catch(() => ({ data: null })),
        ]);
        if (cancelled) return;
        const list = Array.isArray(res.data) ? res.data : [];
        // Latest outbound DM draft. Legacy rows pre-date the variant
        // column and read as variant === undefined / null — treat
        // those as DM too so existing behaviour is preserved.
        const reversed = [...list].reverse();
        const latestDm = reversed.find(
          (m) => m?.direction === 'outbound'
            && (!m?.variant || m.variant === 'dm'),
        );
        const latestInmail = reversed.find(
          (m) => m?.direction === 'outbound' && m?.variant === 'inmail',
        );
        setLinkedinDraft(latestDm || { _empty: true });
        setLinkedinInmailDraft(latestInmail || { _empty: true });
        setLinkedinFlow(flowRes?.data || null);
      } catch (err) {
        if (cancelled) return;
        setLinkedinError(
          err?.response?.data?.detail || err?.message || 'Failed to load LinkedIn draft',
        );
        setLinkedinDraft({ _empty: true });
        setLinkedinInmailDraft({ _empty: true });
      } finally {
        if (!cancelled) setLinkedinLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [channelTab, leadId, authAxios, apiBase]);

  // Generic copy helper — used by both the DM and InMail cards.
  // Falls back to a hidden-textarea + execCommand trick on browsers
  // that don't expose the async clipboard API.
  const _copyText = async (text, setFlag) => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setFlag(true);
      setTimeout(() => setFlag(false), 1500);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand('copy');
        setFlag(true);
        setTimeout(() => setFlag(false), 1500);
      } catch {
        /* swallow */
      }
      document.body.removeChild(ta);
    }
  };

  const handleCopy = () => _copyText(linkedinDraft?.body || '', setCopied);

  // InMail copy puts the subject on the first line so the operator can
  // paste it straight into LinkedIn Sales Navigator's compose window,
  // which has separate Subject + Body fields — but the single-block
  // form is also what most users want as a starting point.
  const handleCopyInmail = () => {
    const subj = (linkedinInmailDraft?.subject || '').trim();
    const body = (linkedinInmailDraft?.body || '').trim();
    if (!subj && !body) return;
    const combined = subj ? `Subject: ${subj}\n\n${body}` : body;
    _copyText(combined, setInmailCopied);
  };

  // Categorize timeline events.
  const buckets = useMemo(() => {
    const sentInitialEmails = timeline.filter(
      (t) =>
        t.type === 'email_outreach' &&
        (t.status === 'sent' || t.status === 'opened' || t.status === 'clicked' || t.status === 'replied'),
    );
    const followUps = timeline.filter((t) => t.type === 'followup_email');
    const calls = timeline.filter((t) => t.type === 'voice_call');
    // Lead-originated replies come through two paths: `email_reply` (from
    // nexus_outreach.reply_text) and `inbound_message` (parsed inbound thread,
    // e.g. MS-Graph mailbox syncs). Count both; `outbound_message` is OUR
    // response and must never be counted as a reply.
    const replies = timeline.filter(
      (t) => t.type === 'email_reply' || t.type === 'inbound_message',
    );
    const allEmailsSent = [...sentInitialEmails, ...followUps];
    return {
      sentInitialEmails, followUps, calls, replies, allEmailsSent,
    };
  }, [timeline]);

  // Real (SENT) touch counts — mirror the header badge logic exactly so the
  // footer stats never disagree with the "Emails / InMail-DM" tiles up top.
  // A 'draft'/placeholder is generated-but-not-sent and must NOT be counted
  // (that's why the old `allEmailsSent.length` showed 3 instead of 2).
  const sentCounts = useMemo(() => {
    let emails = 0;
    const liVariants = new Set();
    (timeline || []).forEach((t) => {
      const type = (t.type || '').toLowerCase();
      const st = (t.status || '').toLowerCase();
      const placeholder =
        st === 'unavailable' ||
        ((st === 'queued' || st === 'projected') && !t.subject && !t.body);
      if (placeholder) return;
      if (type === 'email_outreach' || type === 'followup_email' || type === 'outbound_message') {
        // outbound_message = the agent's auto-reply we SENT — a real email touch.
        if (st !== 'draft') emails += 1;
      } else if (type === 'linkedin_message' || type === 'linkedin_inmail') {
        // Count only SENT LinkedIn touches (t.sent = URN present); drafts don't count.
        if (t.sent) liVariants.add(t.variant === 'inmail' || type === 'linkedin_inmail' ? 'inmail' : 'dm');
      }
    });
    return {
      emails,
      dm: liVariants.has('dm') ? 1 : 0,
      inmail: liVariants.has('inmail') ? 1 : 0,
    };
  }, [timeline]);

  const status = lead?.status || 'new';
  // A booked demo sets the SEQUENCE halt_reason to 'demo_scheduled' but leaves
  // lead.status as 'replied' — so keying only on lead.status left the Demo
  // Booked node stuck on "Pending" for genuinely booked demos. Trust
  // halt_reason too (the same reliable signal isNegativeStop uses below).
  const isDemo =
    status === 'demo_scheduled' ||
    (activeSequence?.halt_reason || '').toLowerCase() === 'demo_scheduled';

  const lastOf = (arr) => (arr.length ? arr[arr.length - 1] : null);

  // ── Email channel stages ────────────────────────────────────────────────
  // Stages are derived from the BACKEND's sequence config
  // (`activeSequence.sequence_steps`), not a hardcoded constant. Each
  // step in the sequence becomes one stage node; we look up the
  // matching touchpoint by `(campaign_id === activeCampaignId, step ===
  // step.order)` so multi-campaign leads don't bleed events into the
  // wrong campaign's flow. Falls back to the legacy 4-step labels when
  // `sequence_steps` is missing (older backend or sequence without a
  // definition row).
  const sequenceSteps =
    activeSequence?.sequence_steps && activeSequence.sequence_steps.length > 0
      ? activeSequence.sequence_steps
      : [
          { order: 0, label: 'Initial Email', channel: 'email' },
          { order: 1, label: 'Follow-up 1', channel: 'email' },
          { order: 2, label: 'Follow-up 2', channel: 'email' },
          { order: 3, label: 'Closing Email', channel: 'email' },
        ];

  // Find the email events for THIS campaign only. The buckets above
  // include all timeline events across all campaigns; here we scope.
  const emailEventsForCampaign = useMemo(() => {
    if (!activeCampaignId) {
      return [...buckets.sentInitialEmails, ...buckets.followUps];
    }
    return timeline.filter(
      (t) =>
        t.campaign_id === activeCampaignId &&
        (t.type === 'email_outreach' || t.type === 'followup_email'),
    );
  }, [timeline, activeCampaignId, buckets.sentInitialEmails, buckets.followUps]);

  // A reply / unsubscribe / demo (or hard failure) ENDS the cadence — the
  // sequencer only runs 'active' sequences, so any other state means the
  // remaining steps will never send. Render those as "Stopped" instead of a
  // misleading "Scheduled". OOO leaves the sequence 'active', so its follow-ups
  // correctly keep showing as Scheduled.
  const _seqStatus = (activeSequence?.status || '').toLowerCase();
  const seqStopped = ['replied', 'halted', 'stopped', 'unsubscribed', 'failed', 'dead'].includes(_seqStatus);
  const nextActionAt = activeSequence?.next_action_at || null;

  // ── Dynamic conversation timeline (email_flow_plan_2.md) ────────────────
  // Instead of enumerating the 4 sequence slots and squeezing every reply
  // into one aggregate "Reply Received" node, walk the REAL chronological
  // events for this campaign — every sent step, every reply, every one of
  // our acks — and render exactly what happened, in true time order. The
  // node count is driven entirely by the conversation, not a fixed shape.

  // `sequence_steps` is now used only as a label/metadata lookup (name,
  // whether it's the final step, its configured delay) — it no longer
  // determines how many nodes render.
  const stepMetaByOrder = useMemo(() => {
    const map = {};
    sequenceSteps.forEach((step, idx) => {
      map[step.order] = {
        label: step.label,
        isFinal: sequenceSteps.length >= 3 && idx === sequenceSteps.length - 1,
        delayDays: Number(step?.delay_days) || 0,
      };
    });
    return map;
  }, [sequenceSteps]);

  // One real (non-draft) event per step actually sent for this campaign.
  const sentStepEvents = useMemo(() => {
    const byStep = new Map();
    emailEventsForCampaign
      .filter((e) => (e.status || '').toLowerCase() !== 'draft')
      .forEach((e) => {
        const stepOrder = e.step ?? 0;
        const existing = byStep.get(stepOrder);
        if (!existing || new Date(e.occurred_at || 0) > new Date(existing.occurred_at || 0)) {
          byStep.set(stepOrder, e);
        }
      });
    return [...byStep.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([stepOrder, event]) => ({ stepOrder, event }));
  }, [emailEventsForCampaign]);

  // The single upcoming step's draft — there's only ever one "next" email;
  // content further out isn't generated until it's actually due.
  const pendingDraftEvent = useMemo(() => {
    const cur = activeSequence?.current_step ?? 0;
    return (
      emailEventsForCampaign.find(
        (e) => (e.step ?? 0) === cur && (e.status || '').toLowerCase() === 'draft',
      ) || null
    );
  }, [emailEventsForCampaign, activeSequence]);

  // Replies + our acks are NOT scoped to the active campaign — a lead's
  // inbound thread isn't split per campaign in the data model, matching the
  // existing hasReply/buckets.replies behaviour this replaces.
  const replyEvents = buckets.replies;
  const ackEvents = useMemo(
    () => timeline.filter((t) => t.type === 'outbound_message'),
    [timeline],
  );

  // Merge sent steps + replies + acks into one chronological walk. The
  // pending draft is handled separately below since it has no real
  // occurred_at yet (nothing sent) and always belongs at the very end.
  const conversationWalk = useMemo(() => {
    const items = [
      ...sentStepEvents.map((s) => ({ kind: 'sent', stepOrder: s.stepOrder, event: s.event, time: s.event.occurred_at })),
      ...replyEvents.map((event) => ({ kind: 'reply', event, time: event.occurred_at })),
      ...ackEvents.map((event) => ({ kind: 'ack', event, time: event.occurred_at })),
    ];
    items.sort((a, b) => new Date(a.time || 0) - new Date(b.time || 0));
    return items;
  }, [sentStepEvents, replyEvents, ackEvents]);

  // Walk the merged timeline and build one render node per sent step / per
  // reply, in true order. Each sent node tracks whether a later reply
  // answered it (§3d); each reply node picks up the ack that answered it
  // and the referral (if any) named in that exact message.
  const conversationNodes = useMemo(() => {
    const nodes = [];
    let lastSentNode = null;
    let lastReplyNode = null;
    conversationWalk.forEach((item) => {
      if (item.kind === 'sent') {
        const meta = stepMetaByOrder[item.stepOrder] || { label: `Step ${item.stepOrder}`, isFinal: false };
        const node = {
          kind: 'sent',
          stepOrder: item.stepOrder,
          icon: item.stepOrder === 0 ? Mail : meta.isFinal ? MailOpen : RotateCw,
          label: meta.label,
          done: true,
          occurredAt: item.time,
          dotClass: 'bg-[#F55600]',
          repliedIndicator: null,
        };
        nodes.push(node);
        lastSentNode = node;
      } else if (item.kind === 'reply') {
        const node = {
          kind: 'reply',
          icon: MailOpen,
          label: 'Reply Received',
          done: true,
          occurredAt: item.time,
          dotClass: 'bg-[#10B981]',
          subtitle: humanizeIntent(item.event.intent),
          bodySnippet: snippet(item.event.body),
          referral: item.event.referral || null,
          // The actual instant ack we sent back, if any — shown as its own
          // nested message, not just a "we replied" note (user request).
          ack: null,
        };
        nodes.push(node);
        lastReplyNode = node;
        if (lastSentNode) lastSentNode.repliedIndicator = 'replied';
      } else if (item.kind === 'ack' && lastReplyNode) {
        lastReplyNode.ack = {
          occurredAt: item.time,
          bodySnippet: snippet(item.event.body),
        };
      }
    });
    // "No reply yet" applies only to the single most recent send, and only
    // if nothing answered it — older un-replied sends stay silent (§3d).
    const lastSent = [...nodes].reverse().find((n) => n.kind === 'sent');
    if (lastSent && !lastSent.repliedIndicator) lastSent.repliedIndicator = 'no_reply_yet';
    return nodes;
  }, [conversationWalk, stepMetaByOrder]);

  // The reply (if any) that triggered the currently-pending draft's
  // rewrite — makes the "Rewritten" marker traceable to a specific message
  // instead of relying on chronological adjacency (§3c).
  const regenTriggerReply = useMemo(() => {
    const mid = pendingDraftEvent?.regenerated_from_message_id;
    if (!mid) return null;
    return replyEvents.find((r) => r.message_id === mid) || null;
  }, [pendingDraftEvent, replyEvents]);

  // Every not-yet-sent step, shown by default (email_flow_plan_3.md) —
  // not just the current one. Dates come from the sequence's fixed
  // cadence (`step_schedule`, computed once at enrollment and only ever
  // moved by an explicit OOO return date — see sequencer.py), so
  // Follow-up 1/2/Closing all show their real scheduled dates up front
  // instead of only appearing once they become "current." Only the
  // CURRENT step carries real draft content (Rewritten marker/tooltip) —
  // steps further out are pure placeholders, since their content isn't
  // generated until they're actually due.
  const pendingNodes = useMemo(() => {
    if (!pendingDraftEvent) return [];
    const cur = activeSequence?.current_step ?? 0;
    const stepSchedule = activeSequence?.step_schedule || null;
    // Chained fallback for rows enrolled before step_schedule existed (or
    // missing it for any other reason) — every not-yet-sent step still
    // gets a real date, projected forward from the last known date using
    // each step's own delay_days, same math the fixed schedule itself uses
    // (compute_step_schedule), just computed client-side. The CURRENT
    // step anchors on next_action_at first (reflects any real postponement
    // — e.g. a mailbox daily cap); steps after it chain from there.
    let runningFallback = nextActionAt
      ? new Date(nextActionAt)
      : (conversationWalk.length
          ? new Date(conversationWalk[conversationWalk.length - 1].time)
          : (lead?.created_at ? new Date(lead.created_at) : null));
    return sequenceSteps
      .filter((step) => step.order >= cur)
      .map((step) => {
        const isCurrent = step.order === cur;
        const meta = stepMetaByOrder[step.order] || { label: `Step ${step.order}`, isFinal: false, delayDays: 0 };
        let projectedAt = stepSchedule ? stepSchedule[String(step.order)] || null : null;
        if (!projectedAt) {
          if (!isCurrent && runningFallback && !Number.isNaN(runningFallback.getTime())) {
            runningFallback = new Date(runningFallback);
            runningFallback.setDate(runningFallback.getDate() + meta.delayDays);
          }
          if (runningFallback && !Number.isNaN(runningFallback.getTime())) {
            projectedAt = runningFallback.toISOString();
          }
        }
        return {
          kind: 'pending',
          icon: step.order === 0 ? Mail : meta.isFinal ? MailOpen : RotateCw,
          label: meta.label,
          done: false,
          projectedAt,
          dotClass: 'bg-[#F55600]',
          regenerated: isCurrent && !!pendingDraftEvent.regenerated,
          regenTrigger: isCurrent && regenTriggerReply ? humanizeIntent(regenTriggerReply.intent) : null,
        };
      });
  }, [pendingDraftEvent, activeSequence, sequenceSteps, stepMetaByOrder, nextActionAt, conversationWalk, lead, regenTriggerReply]);

  // ── Scenario context for the adaptive tail (flow_tab_dynamic_plan.md §6) ──
  // The reply that actually drove the sequence's current state — used for
  // the "Sequence ended" reason if the sequence stopped. Falls back to the
  // sequence's own halt_reason when no reply intent is available.
  const latestReply = lastOf(buckets.replies);
  const replyIntent = latestReply?.intent || null;
  const stopReason =
    (replyIntent && humanizeIntent(replyIntent)) ||
    (activeSequence?.halt_reason === 'unsubscribed' ? 'Unsubscribed' : null) ||
    (activeSequence?.halt_reason === 'demo_scheduled' ? 'Demo booked' : null) ||
    'Sequence ended';

  // Shape 2 (Stop): once the sequence has stopped, collapse whatever would
  // have come next into ONE terminal node — but only if the cadence didn't
  // already run to completion naturally (final step sent, nothing pending).
  const finalStepSent = conversationNodes.some(
    (n) => n.kind === 'sent' && stepMetaByOrder[n.stepOrder]?.isFinal,
  );
  const cadenceExhausted = finalStepSent && !pendingDraftEvent;
  const showTerminalNode = seqStopped && !cadenceExhausted;

  const displayStepStages = [
    ...conversationNodes,
    ...(showTerminalNode
      ? [{
          icon: Circle,
          label: 'Sequence ended',
          done: false,
          stopped: true,
          terminal: true,
          stopReason,
          occurredAt: null,
          projectedAt: null,
          dotClass: 'bg-[#8C8881]',
        }]
      : !seqStopped
      ? pendingNodes
      : []),
  ];

  // A NEGATIVE stop (NOT_INTERESTED/UNSUBSCRIBE-style — a genuine dead end)
  // vs. DEMO_SCHEDULED (also "stopped", but a POSITIVE outcome — the
  // relationship is still active, a call or the demo itself may still be
  // coming). Only the negative case should hide never-going-to-happen
  // future steps; a scheduled demo keeps them visible as still-relevant.
  // halt_reason is the reliable signal here (set directly by the intent
  // handler), not lead.status, which a demo-booking flow may set separately.
  const isNegativeStop = seqStopped && (activeSequence?.halt_reason || '').toLowerCase() !== 'demo_scheduled';
  const callsHappened = buckets.calls.length > 0;

  const emailStages = [
    {
      icon: Target,
      label: 'Lead Discovered',
      count: 1,
      done: true,
      occurredAt: lead?.created_at,
      dotClass: 'bg-[#2B2926]',
    },
    ...displayStepStages,
    // Voice Calls: hidden on a negative stop UNLESS a call already
    // genuinely happened — real history is never hidden, only the
    // never-going-to-happen placeholder.
    ...(!isNegativeStop || callsHappened
      ? [{
          icon: PhoneCall,
          label: 'Voice Calls',
          count: buckets.calls.length,
          done: callsHappened,
          occurredAt: lastOf(buckets.calls)?.occurred_at,
          dotClass: 'bg-[#10B981]',
        }]
      : []),
    // Reply Received is no longer a single aggregate node — every reply
    // already has its own node inside displayStepStages, positioned right
    // after the email it answered (email_flow_plan_2.md §3b).
    // Demo Booked: hidden entirely on a negative stop — isDemo is always
    // false here by definition (a demo-scheduled halt is excluded from
    // isNegativeStop above), so this node can only ever read "Pending"
    // for a lead that has already said no.
    ...(!isNegativeStop
      ? [{
          icon: Sparkles,
          label: 'Demo Booked',
          count: 1,
          done: isDemo,
          occurredAt: isDemo ? (lead?.demo_booked_at || lead?.last_contacted_at) : null,
          dotClass: 'bg-[#10B981]',
        }]
      : []),
  ];

  // ── LinkedIn channel stages — branch-aware ──────────────────────────────
  // Built from the REAL flow state (/linkedin/lead/:id/flow), so the diagram
  // mirrors the backend state machine:
  //   Connection Request → Note → InMail → Connection Accepted →
  //        ├─ accepted     → Messages branch (shown alone)
  //        └─ not accepted → InMail follow-up branch
  //   (while still awaiting acceptance, BOTH branches show as pending so the
  //    fork is visible; once accepted we show ONLY the message branch.)
  // Hybrid mode has no connection step — it's an InMail-only sequence.
  // When the lead isn't enrolled / flow hasn't loaded, every step renders
  // pending so the operator still sees the planned path.
  const flow = linkedinFlow && linkedinFlow.enrolled ? linkedinFlow : null;
  const connStatus = flow?.connection_status || 'none';
  const connSentAt = flow?.connection_sent_at || null;
  const connAccepted = connStatus === 'accepted' || !!flow?.connection_accepted_at;
  const liBranch = flow?.current_branch || null;      // pending_acceptance|accepted|inmail
  const wentInmail = liBranch === 'inmail' || connStatus === 'declined';
  const isHybrid = flow?.workflow_mode === 'hybrid';
  const firstInmailDone = flow?.inmail_status === 'sent' || !!flow?.inmail_sent_at;
  const liMsgDone = !!flow?.message_sent_at;
  // LinkedIn reply status is LinkedIn-only — do NOT fold in email replies
  // (email_reply / inbound_message) here. An email reply
  // must never mark the LinkedIn "Reply Received" step done.
  const liReplied = flow?.reply_status === 'replied';

  // Forward date chain: a DONE step anchors the running date on its real
  // timestamp; a PENDING action step projects `gapDays` past the running date
  // so it shows a (~) scheduled date like the email flow. LinkedIn cadence:
  // connection + note + first InMail land together (day 0), the acceptance
  // check is ~2d later, messages ~2d after that, InMail follow-ups ~3d.
  // Outcome steps (the acceptance fork + Reply) pass gapDays=null → "Not yet".
  let liRunning = lead?.created_at || null;
  const liProject = (gapDays) => {
    if (!liRunning) return null;
    try {
      const d = new Date(liRunning);
      d.setDate(d.getDate() + gapDays);
      if (Number.isNaN(d.getTime())) return null;
      liRunning = d.toISOString();
      return liRunning;
    } catch {
      return null;
    }
  };
  const linkedInStages = [];
  const pushLi = (stage, gapDays) => {
    if (stage.done) {
      if (stage.occurredAt) liRunning = stage.occurredAt;
      linkedInStages.push(stage);
    } else {
      linkedInStages.push({
        ...stage,
        projectedAt: gapDays == null ? null : liProject(gapDays),
      });
    }
  };

  pushLi({ icon: Target, label: 'Lead Discovered', count: 1, done: true,
    occurredAt: lead?.created_at, dotClass: 'bg-[#2B2926]' }, 0);
  if (!isHybrid) {
    pushLi({ icon: UserPlus, label: 'Connection Request', count: 1, done: !!connSentAt,
      occurredAt: connSentAt, dotClass: 'bg-[#F55600]' }, 0);
    pushLi({ icon: StickyNote, label: 'Note', count: 1, done: !!flow?.note_attached,
      occurredAt: connSentAt, dotClass: 'bg-[#F55600]' }, 0);
  }
  pushLi({ icon: Send, label: 'InMail', count: 1, done: firstInmailDone,
    occurredAt: flow?.inmail_sent_at, dotClass: 'bg-[#F55600]' }, 0);
  if (!isHybrid) {
    pushLi({ icon: UserCheck, label: 'Connection Accepted', count: 1, done: connAccepted,
      occurredAt: flow?.connection_accepted_at, dotClass: 'bg-[#10B981]' }, 2);
    // ── Branch ──
    if (connAccepted) {
      // Accepted → messaging branch only.
      pushLi({ icon: MessageSquare, label: 'Message', count: 1, done: liMsgDone,
        occurredAt: flow?.message_sent_at, dotClass: 'bg-[#F55600]' }, 2);
    } else if (wentInmail) {
      // Not accepted (declined / acceptance timed out) → InMail follow-ups.
      pushLi({ icon: Send, label: 'InMail Follow-up', count: 1, done: firstInmailDone,
        occurredAt: flow?.inmail_sent_at, dotClass: 'bg-[#F55600]' }, 3);
    } else {
      // Still awaiting acceptance — show the fork; branch date is unknown so
      // leave both as "Not yet" (gapDays=null).
      pushLi({ icon: MessageSquare, label: 'Message (if accepted)', count: 1, done: false,
        occurredAt: null, dotClass: 'bg-[#F55600]' }, null);
      pushLi({ icon: Send, label: 'InMail (if not)', count: 1, done: false,
        occurredAt: null, dotClass: 'bg-[#F55600]' }, null);
    }
  }
  pushLi({ icon: MailOpen, label: 'Reply Received', count: liReplied ? 1 : 0, done: liReplied,
    occurredAt: flow?.last_reply_at,
    // What they said, in plain language, right at the pivot point where the
    // fixed front half gives way to whatever happens next.
    subtitle: liReplied ? (humanizeIntent(flow?.reply_intent) || null) : null,
    dotClass: 'bg-[#10B981]' }, null);

  // ── Tail: what will ACTUALLY happen next for this lead ────────────────────
  // The backend plan is per-lead and rewritten on every reply, so the tail is
  // rendered from it rather than from a fixed diagram. Three shapes:
  //   ended      → ONE terminal node (not three repeated "Stopped" pills — the
  //                point is that nothing more happens, so it reads as one ending)
  //   conversing → the queued reply, marked as such
  //   continuing → the remaining touches with their real projected dates
  const liSeqStatus = flow?.sequence_status || null;
  const liEnded = liSeqStatus === 'stopped' || liSeqStatus === 'completed';
  const liPlan = Array.isArray(flow?.plan) ? flow.plan : [];

  if (liEnded) {
    const reason =
      humanizeStop(flow?.stop_reason) ||
      humanizeIntent(flow?.reply_intent) ||
      (liSeqStatus === 'completed' ? 'No reply' : 'Sequence ended');
    linkedInStages.push({
      icon: Flag,
      label: liSeqStatus === 'completed' ? 'Sequence Complete' : 'Sequence Ended',
      count: 1,
      done: true,
      occurredAt: flow?.last_reply_at || null,
      subtitle: flow?.flagged_reason ? `${reason} — needs a human` : reason,
      dotClass: liSeqStatus === 'completed' ? 'bg-[#10B981]' : 'bg-[#9CA3AF]',
    });
  } else {
    liPlan.forEach((t) => {
      const isReply = !!t.is_reply;
      const label = isReply
        ? 'Our Reply'
        : (LI_TOUCH_LABELS[t.role] || (t.kind === 'inmail' ? 'InMail' : 'Message'));
      pushLi({
        icon: isReply ? MessageSquare : (t.kind === 'inmail' ? Send : MessageSquare),
        label,
        count: 1,
        done: false,
        occurredAt: null,
        subtitle: isReply ? 'answering them' : (t.content_stale ? 'rewritten' : null),
        dotClass: isReply ? 'bg-[#10B981]' : 'bg-[#F55600]',
      }, t.delay_days ?? 0);
    });
  }

  const stages = channelTab === 'email' ? emailStages : linkedInStages;

  // Progress counts for the channel tabs + the sequence header bar.
  const emailDone = emailStages.filter((s) => s.done).length;
  const liDone = linkedInStages.filter((s) => s.done).length;
  const curDone = stages.filter((s) => s.done).length;
  const curTotal = stages.length;
  const curPct = curTotal ? Math.round((curDone / curTotal) * 100) : 0;

  // Build the campaign selector options. Only show when the lead is in
  // 2+ campaigns — otherwise the dropdown is noise.
  const campaignOptions = useMemo(() => {
    const ids = new Set();
    const opts = [];
    for (const s of sequences || []) {
      const cid = s?.campaign_id;
      if (cid == null || ids.has(cid)) continue;
      ids.add(cid);
      const c = (campaigns || []).find((cc) => cc?._id === cid);
      opts.push({ id: cid, name: c?.name || `Campaign ${cid}` });
    }
    return opts;
  }, [sequences, campaigns]);

  return (
    <div className="px-4 pb-4">
      {/* Sticky header — the Outreach Flow label + Email/LinkedIn channel
          tabs stay pinned while the stage list below scrolls. */}
      <div className="sticky top-0 z-10 bg-white pt-4 pb-1 -mx-4 px-4 border-b border-[#2B2926]/[0.06]">
      {/* Campaign selector — only renders when the lead has 2+ campaigns,
          so the operator can switch the flow between them. Single-campaign
          leads show nothing here. */}
      {campaignOptions.length > 1 && (
        <div className="flex items-center justify-end mb-3">
          <select
            value={activeCampaignId ?? ''}
            onChange={(e) => setActiveCampaignId(Number(e.target.value))}
            className="text-[10px] font-bold text-[#2B2926]/70 bg-white border border-[#2B2926]/10 rounded px-1.5 py-0.5 hover:border-[#F55600]/30 focus:outline-none focus:border-[#F55600]"
            title="Switch the flow to a different campaign's sequence"
          >
            {campaignOptions.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Channel tabs — button-style toggle (active = filled) */}
      <div className="flex items-center gap-2 mb-4" style={{ fontFamily: FLOW_FONT }}>
        {[
          { key: 'email', icon: Mail, label: 'Email', activeBg: '#F55600' },
          { key: 'linkedin', icon: Linkedin, label: 'LinkedIn', activeBg: '#F55600' },
        ].map((tab) => {
          const active = channelTab === tab.key;
          return (
            <button
              key={tab.key}
              type="button"
              onClick={() => setChannelTab(tab.key)}
              className="flex-1 inline-flex items-center justify-center gap-2 px-3 py-2.5 rounded-xl text-[13px] font-semibold transition-all"
              style={
                active
                  ? { background: tab.activeBg, color: '#fff' }
                  : { background: '#fff', color: '#67655E', border: '1px solid rgba(43,41,38,0.15)' }
              }
            >
              <tab.icon className="w-4 h-4" />
              {tab.label}
            </button>
          );
        })}
      </div>
      </div>

      {/* Sequence card — horizontal timeline. Each node is one flex column
          that grows to help fill the available width (bigger for a reply
          card, which carries more content) and draws its OWN half of the
          connector line: the left 50% of its own box (connecting back to
          the previous node) and the right 50% (connecting forward). Since
          each half is sized relative to that column's own width, the two
          halves from adjacent columns always meet exactly at the shared
          column boundary — correct regardless of how wide either column
          ends up, with no JS pixel measurement needed. The circle paints
          on top (z-10) to hide the segment passing underneath it. */}
      <div
        className="mt-4 rounded-xl border border-[#2B2926]/10 bg-white px-4 py-4 isolate"
        style={{ fontFamily: FLOW_FONT }}
      >
        <div className="overflow-x-auto pb-1">
          <div className="flex items-start" style={{ minWidth: '100%' }}>
            {stages.map((s, i) => {
              const hex = accentHex(s.dotClass);
              const isOrange = hex === '#F55600';
              const isCard = s.kind === 'reply';
              const isFirst = i === 0;
              const isLastNode = i === stages.length - 1;
              const leftDone = i > 0 && stages[i - 1].done;
              const dateText = s.done
                ? fmtDateShort(s.occurredAt)
                : s.terminal
                ? s.stopReason
                : s.stopped
                ? 'Stopped'
                : s.projectedAt
                ? fmtDateShort(s.projectedAt)
                : 'Not yet';
              return (
                <div
                  key={`${i}-${s.kind || 'x'}-${s.label}`}
                  className="relative flex flex-col items-center px-1"
                  style={{ flex: '1 1 140px', maxWidth: 200, minWidth: 110 }}
                >
                  {!isFirst && (
                    <div
                      className="absolute"
                      style={
                        leftDone
                          ? { left: 0, width: '50%', top: 21, height: 2, background: '#F55600' }
                          : { left: 0, width: '50%', top: 21, height: 0, borderTop: '2px dashed rgba(43,41,38,0.16)' }
                      }
                    />
                  )}
                  {!isLastNode && (
                    <div
                      className="absolute"
                      style={
                        s.done
                          ? { right: 0, width: '50%', top: 21, height: 2, background: '#F55600' }
                          : { right: 0, width: '50%', top: 21, height: 0, borderTop: '2px dashed rgba(43,41,38,0.16)' }
                      }
                    />
                  )}
                  {/* node circle */}
                  <div
                    className="relative w-11 h-11 rounded-full flex items-center justify-center shrink-0"
                    style={
                      s.done
                        ? { background: hex, boxShadow: '0 1px 4px rgba(15,17,21,0.12)' }
                        : { background: '#fff', border: `1.5px solid ${isOrange ? 'rgba(245,86,0,0.4)' : 'rgba(43,41,38,0.16)'}` }
                    }
                  >
                    <s.icon
                      className="w-[19px] h-[19px]"
                      style={{ color: s.done ? '#fff' : isOrange ? 'rgba(245,86,0,0.7)' : '#A6A39B' }}
                    />
                  </div>

                  {/* content: same plain block for every node — label,
                      date, status pill — with reply-only extras (intent,
                      snippet, our ack, referral) stacked below. No card/
                      box around replies; they read like every other node,
                      just with more underneath. */}
                  <div className="relative mt-2.5 flex flex-col items-center gap-1.5 text-center w-full">
                    <div className="text-[14px] font-bold leading-tight" style={{ color: '#0F1115' }}>
                      {s.label}
                    </div>
                    <div
                      className={`text-[12px] tabular-nums ${dateText === 'Not yet' ? 'italic font-medium' : 'font-semibold'}`}
                      style={{ color: dateText === 'Not yet' || s.stopped ? '#A8A5A0' : '#67655E' }}
                    >
                      {dateText}
                    </div>
                    <div className="flex flex-wrap items-center justify-center gap-1">
                      {s.done ? (
                        <Badge color="#047857" bg="rgba(16,185,129,0.12)" icon={Check}>Done</Badge>
                      ) : s.terminal ? (
                        <Badge color="#8C8881" bg="rgba(140,136,129,0.12)">Ended</Badge>
                      ) : s.stopped ? (
                        <Badge color="#8C8881" bg="rgba(140,136,129,0.12)">Stopped</Badge>
                      ) : s.projectedAt ? (
                        <Badge color="#F55600" bg="rgba(245,86,0,0.1)">Scheduled</Badge>
                      ) : (
                        <Badge color="#1F2937" bg="rgba(31,41,55,0.06)">Pending</Badge>
                      )}
                      {/* "Did this email get a reply?" — explicit per sent
                          email, not just one aggregate node (§3d). */}
                      {s.kind === 'sent' && s.repliedIndicator === 'replied' && (
                        <Badge color="#10B981" bg="rgba(16,185,129,0.1)">→ Replied</Badge>
                      )}
                      {s.kind === 'sent' && s.repliedIndicator === 'no_reply_yet' && (
                        <Badge color="#A8A5A0" bg="rgba(168,165,160,0.14)">No reply yet</Badge>
                      )}
                      {/* Rewritten marker — this step's content was
                          rewritten in response to a reply, not the original
                          enrollment-time draft; traceable to the exact
                          reply that triggered it (§3c). */}
                      {s.regenerated && (
                        <Badge
                          color="#0A66C2"
                          bg="rgba(10,102,194,0.08)"
                          icon={RotateCw}
                          title={s.regenTrigger ? `Rewritten after: ${s.regenTrigger}` : ''}
                        >
                          Rewritten
                        </Badge>
                      )}
                      {/* Reply intent — what the lead's reply was classified
                          as. */}
                      {s.subtitle && (
                        <Badge color="#F55600" bg="rgba(245,86,0,0.1)">{s.subtitle}</Badge>
                      )}
                    </div>
                    {/* Referral branch — the reply named a different
                        contact; a new lead was created for them. Stays
                        visible inline (compact, high-value at a glance) —
                        only the long message text moves behind the
                        conversation toggle below. */}
                    {s.referral && (
                      <Badge color="#0A66C2" bg="rgba(10,102,194,0.08)" icon={UserPlus} title={s.referral.email || ''}>
                        New lead: {s.referral.name}
                      </Badge>
                    )}
                    {/* The reply text + our ack are the "bulky" content —
                        kept out of the always-visible node so every node
                        stays a uniform, compact height. Click-only (no
                        hover); opening one auto-scrolls it into view since
                        it can render below the fold. */}
                    {isCard && (s.bodySnippet || s.ack) && (
                      <div className="relative">
                        <button
                          type="button"
                          onClick={() => setOpenConversation(openConversation === i ? null : i)}
                          className="inline-flex items-center gap-1 text-[11.5px] font-bold"
                          style={{ color: '#0A66C2' }}
                        >
                          View Conversation
                          <ChevronDown
                            className="w-3 h-3 transition-transform"
                            style={{ transform: openConversation === i ? 'rotate(180deg)' : 'none' }}
                          />
                        </button>
                        <div
                          ref={(el) => { conversationRefs.current[i] = el; }}
                          className={`absolute left-1/2 -translate-x-1/2 top-full mt-2 z-20 w-[320px] max-w-[80vw] rounded-lg border bg-white shadow-lg px-3.5 py-3 text-left ${
                            openConversation === i ? 'block' : 'hidden'
                          }`}
                          style={{ borderColor: 'rgba(43,41,38,0.12)' }}
                        >
                          {s.bodySnippet && (
                            <p className="text-[12.5px] italic leading-relaxed" style={{ color: '#4A4844' }}>
                              &ldquo;{s.bodySnippet}&rdquo;
                            </p>
                          )}
                          {s.ack && (
                            <div
                              className="mt-2.5 rounded-md px-3 py-2"
                              style={{ background: 'rgba(10,102,194,0.06)', borderLeft: '2.5px solid #0A66C2' }}
                            >
                              <div className="flex items-center justify-between gap-2">
                                <span className="text-[11.5px] font-bold" style={{ color: '#0A66C2' }}>↪ We replied</span>
                                <span className="text-[11px] font-semibold tabular-nums shrink-0" style={{ color: '#67655E' }}>
                                  {fmtDateShort(s.ack.occurredAt)}
                                </span>
                              </div>
                              {s.ack.bodySnippet && (
                                <p className="mt-1 text-[12px] italic leading-relaxed" style={{ color: '#4A4844' }}>
                                  &ldquo;{s.ack.bodySnippet}&rdquo;
                                </p>
                              )}
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* 2026-05-29 — LinkedIn draft + InMail draft cards moved to the
          Content tab (left sibling of this panel). Flow tab now shows
          ONLY the flow/process stages — no message previews. Cards
          stay rendered here behind `false` so the existing fetching
          + linkedinDraft / linkedinInmailDraft state plumbing keeps
          working (other components elsewhere may read them). */}
      {false && channelTab === 'linkedin' && (
        <div className="mt-4">
          {linkedinLoading && (
            <div className="px-3 py-3 rounded-lg border border-[#2B2926]/10 bg-[#2B2926]/5 text-[11px] text-[#2B2926]/60">
              Loading LinkedIn draft…
            </div>
          )}
          {!linkedinLoading && linkedinDraft && !linkedinDraft._empty && (
            <div className="px-3 py-3 rounded-lg border border-[#2B2926]/10 bg-white">
              <div className="flex items-center justify-between gap-2 mb-2">
                <span className="text-[9px] uppercase tracking-wider text-[#2B2926]/40 font-bold">
                  LinkedIn DM · draft
                </span>
                <button
                  type="button"
                  onClick={handleCopy}
                  className={[
                    'inline-flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-bold transition-all',
                    copied
                      ? 'bg-[#10B981] text-white'
                      : 'bg-[#2B2926] text-white hover:opacity-90',
                  ].join(' ')}
                  title="Copy DM body to clipboard"
                >
                  {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
                  {copied ? 'Copied' : 'Copy'}
                </button>
              </div>
              <p className="text-[11px] text-[#2B2926] whitespace-pre-wrap leading-relaxed">
                {linkedinDraft.body}
              </p>
              {linkedinDraft.sent_at && (
                <div className="mt-2 text-[10px] text-[#2B2926]/40">
                  Marked sent {fmtDateShort(linkedinDraft.sent_at)}
                </div>
              )}
            </div>
          )}
          {!linkedinLoading && linkedinDraft && linkedinDraft._empty && !linkedinError && (
            <div className="px-3 py-3 rounded-lg border border-dashed border-[#2B2926]/10 bg-white text-[11px] text-[#2B2926]/60">
              No LinkedIn draft yet. One will be generated within ~1 minute.
            </div>
          )}

          {/* LinkedIn InMail card — separate variant generated by the
              sequencer alongside the DM. Only rendered when an InMail
              row exists for this lead; absent for legacy DM-only leads
              (or future leads where InMail generation failed and only
              the DM made it through). Subject + body shown together,
              copy button stages them as "Subject: ...\n\n<body>" so the
              operator can paste straight into Sales Navigator. */}
          {!linkedinLoading && linkedinInmailDraft && !linkedinInmailDraft._empty && (
            <div className="mt-3 px-3 py-3 rounded-lg border border-[#2B2926]/10 bg-white">
              <div className="flex items-center justify-between gap-2 mb-2">
                <span className="text-[9px] uppercase tracking-wider text-[#2B2926]/40 font-bold">
                  LinkedIn InMail · draft
                </span>
                <button
                  type="button"
                  onClick={handleCopyInmail}
                  className={[
                    'inline-flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-bold transition-all',
                    inmailCopied
                      ? 'bg-[#10B981] text-white'
                      : 'bg-[#2B2926] text-white hover:opacity-90',
                  ].join(' ')}
                  title="Copy InMail subject + body to clipboard"
                >
                  {inmailCopied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
                  {inmailCopied ? 'Copied' : 'Copy'}
                </button>
              </div>
              {linkedinInmailDraft.subject && (
                <div className="mb-2 text-[11px] font-bold text-[#2B2926]">
                  {linkedinInmailDraft.subject}
                </div>
              )}
              <p className="text-[11px] text-[#2B2926] whitespace-pre-wrap leading-relaxed">
                {linkedinInmailDraft.body}
              </p>
              {linkedinInmailDraft.sent_at && (
                <div className="mt-2 text-[10px] text-[#2B2926]/40">
                  Marked sent {fmtDateShort(linkedinInmailDraft.sent_at)}
                </div>
              )}
            </div>
          )}

          {!linkedinLoading && linkedinError && (
            <div className="px-3 py-3 rounded-lg border border-[#F55600]/30 bg-[#F55600]/5 text-[11px] text-[#F55600]">
              {linkedinError}
            </div>
          )}
        </div>
      )}

      {/* Footer stats — three compact metric cards. The set switches with the
          active channel tab so Email shows email metrics and LinkedIn shows
          its own (Messages / InMails). Counts are SENT touches only. */}
      <div className="mt-4 grid grid-cols-3 gap-2.5" style={{ fontFamily: FLOW_FONT }}>
        {(channelTab === 'linkedin'
          ? [
              { label: 'Messages', value: sentCounts.dm,          color: '#F55600' },
              { label: 'InMails',  value: sentCounts.inmail,      color: '#0A66C2' },
              // LinkedIn-only reply count — email replies must not leak in here.
              { label: 'Replies',  value: liReplied ? 1 : 0,      color: '#10B981' },
            ]
          : [
              { label: 'Emails',  value: sentCounts.emails,      color: '#F55600' },
              { label: 'Replies', value: buckets.replies.length, color: '#10B981' },
              { label: 'Calls',   value: buckets.calls.length,   color: '#0F1115' },
            ]
        ).map((m) => (
          <div
            key={m.label}
            className="rounded-xl border border-[#2B2926]/10 bg-white px-3 py-3 text-center shadow-[0_1px_2px_rgba(43,41,38,0.04)]"
          >
            <div className="text-[20px] font-bold leading-none tabular-nums" style={{ color: m.color }}>
              {m.value}
            </div>
            <div className="text-[10px] font-bold uppercase tracking-[0.1em] mt-1.5" style={{ color: '#3A3F47' }}>
              {m.label}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default OutreachFlowPanel;
