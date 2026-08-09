/**
 * JourneyTimeline — center-panel timeline for the selected lead.
 *
 * Renders chronological events ported from legacy timeline view.
 * Event types: email_outreach, followup_email, email_reply,
 * linkedin_message, voice_call, inbound_message, outbound_message.
 *
 * Sort: ascending by occurred_at (earliest first).
 * Skeleton: 3 fake events while loading.
 */
import React, { useState } from 'react';
import {
  ChevronDown,
  ChevronUp,
  FileText,
  Linkedin,
  Mail,
  MailOpen,
  MessageSquare,
  PhoneCall,
  RotateCw,
  Send,
} from 'lucide-react';
import { fmtRelative } from './NexusJourney';

const EVENT_META = {
  email_outreach: {
    icon: Mail,
    label: 'Email Sent',
    chip: 'bg-[#F55600]/10 text-[#F55600]',
    dot: 'bg-[#F55600]',
  },
  followup_email: {
    icon: RotateCw,
    label: (e) => `Follow-up Email${e.step ? ` (Step ${e.step})` : ''}`,
    chip: 'bg-[#F55600]/10 text-[#F55600]',
    dot: 'bg-[#F55600]',
  },
  email_reply: {
    icon: MailOpen,
    label: 'Email Reply',
    chip: 'bg-[#10B981]/15 text-[#10B981]',
    dot: 'bg-[#10B981]',
  },
  linkedin_message: {
    icon: Linkedin,
    // Label respects the new `variant` field on the event payload. The
    // backend tags InMail variants explicitly; legacy rows default to
    // 'dm' (or are missing the field entirely) and read as a DM.
    label: (e) => (e.variant === 'inmail' ? 'LinkedIn InMail' : 'LinkedIn Message'),
    chip: 'bg-[#2B2926]/5 text-[#2B2926]',
    dot: 'bg-[#2B2926]',
  },
  linkedin_inmail: {
    icon: Linkedin,
    label: 'LinkedIn InMail',
    chip: 'bg-[#2B2926]/5 text-[#2B2926]',
    dot: 'bg-[#2B2926]',
  },
  voice_call: {
    icon: PhoneCall,
    label: 'Voice Call',
    chip: 'bg-[#10B981]/10 text-[#10B981]',
    dot: 'bg-[#10B981]',
  },
  inbound_message: {
    icon: MessageSquare,
    label: 'Inbound Message',
    chip: 'bg-[#10B981]/10 text-[#10B981]',
    dot: 'bg-[#10B981]',
  },
  outbound_message: {
    icon: Send,
    label: 'Outbound Message',
    chip: 'bg-[#F55600]/10 text-[#F55600]',
    dot: 'bg-[#F55600]',
  },
};

const STATUS_CHIP = {
  sent: 'bg-[#2B2926]/5 text-[#2B2926]/60',
  queued: 'bg-[#F55600]/10 text-[#F55600]',
  pending: 'bg-[#F55600]/10 text-[#F55600]',
  not_generated: 'bg-[#2B2926]/5 text-[#2B2926]/40',
  failed: 'bg-[#F55600]/15 text-[#F55600]',
  bounced: 'bg-[#F55600]/15 text-[#F55600]',
  replied: 'bg-[#10B981]/15 text-[#10B981]',
  opened: 'bg-[#10B981]/10 text-[#10B981]',
  clicked: 'bg-[#10B981]/15 text-[#10B981]',
  unavailable: 'bg-[#2B2926]/5 text-[#2B2926]/40',
};

const truncate = (s, n = 240) => {
  if (!s) return '';
  const t = String(s).trim();
  return t.length > n ? `${t.slice(0, n)}…` : t;
};

const SkeletonEvent = () => (
  <div className="flex gap-3 animate-pulse">
    <div className="flex flex-col items-center">
      <div className="w-6 h-6 rounded-full bg-[#2B2926]/5" />
      <div className="w-px flex-1 bg-[#2B2926]/5 mt-1" />
    </div>
    <div className="flex-1 pb-4">
      <div className="h-3 bg-[#2B2926]/5 rounded w-1/2" />
      <div className="h-2 bg-[#2B2926]/5 rounded w-1/3 mt-2" />
      <div className="h-12 bg-[#2B2926]/5 rounded w-full mt-2" />
    </div>
  </div>
);

const TimelineEvent = ({ event, isLast }) => {
  const [expanded, setExpanded] = useState(false);
  const meta = EVENT_META[event.type] || EVENT_META.email_outreach;
  const Icon = meta.icon;
  const label = typeof meta.label === 'function' ? meta.label(event) : meta.label;
  const status = event.status || '';
  const body = event.body || '';
  const reply = event.reply_text || '';
  const transcript = Array.isArray(event.transcript) ? event.transcript : [];
  const hasExpandable =
    (body && body.length > 240) ||
    (reply && reply.length > 240) ||
    transcript.length > 0;

  return (
    <div className="flex gap-3">
      {/* Rail */}
      <div className="flex flex-col items-center">
        <div
          className={[
            'w-7 h-7 rounded-full flex items-center justify-center shrink-0',
            meta.dot,
            'text-white',
          ].join(' ')}
        >
          <Icon className="w-3.5 h-3.5" />
        </div>
        {!isLast && <div className="w-px flex-1 bg-[#2B2926]/10 mt-1" />}
      </div>

      {/* Body */}
      <div className="flex-1 pb-5 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-bold text-[#2B2926]">{label}</span>
          {status && (
            <span
              className={[
                'inline-flex px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider',
                STATUS_CHIP[status] || 'bg-[#2B2926]/5 text-[#2B2926]/60',
              ].join(' ')}
            >
              {String(status).replace('_', ' ')}
            </span>
          )}
          {event.campaign_name && (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-bold bg-[#2B2926]/5 text-[#2B2926]/60">
              <FileText className="w-2.5 h-2.5" />
              {event.campaign_name}
            </span>
          )}
        </div>
        <div className="text-[10px] text-[#2B2926]/40 mt-0.5">
          {fmtRelative(event.occurred_at)}
        </div>

        {event.subject && (
          <div className="mt-1.5 text-xs font-bold text-[#2B2926]/80 truncate">
            {event.subject}
          </div>
        )}

        {/* Body content */}
        {(body || reply || transcript.length > 0) && (
          <div className="mt-2 text-[11px] text-[#2B2926]/70 leading-relaxed whitespace-pre-wrap bg-[#2B2926]/[0.02] rounded p-2 border border-[#2B2926]/5">
            {expanded ? (
              <>
                {body && <div>{body}</div>}
                {reply && (
                  <div className="mt-2 pt-2 border-t border-[#2B2926]/10">
                    <div className="text-[10px] font-bold text-[#10B981] mb-1">Reply</div>
                    {reply}
                  </div>
                )}
                {transcript.length > 0 && (
                  <div className="mt-2 pt-2 border-t border-[#2B2926]/10 space-y-1">
                    {transcript.map((line, i) => (
                      <div key={i} className="text-[11px]">
                        <span
                          className={[
                            'font-bold',
                            line.role === 'rep' ? 'text-[#F55600]' : 'text-[#2B2926]/60',
                          ].join(' ')}
                        >
                          {line.role || 'speaker'}:
                        </span>{' '}
                        {line.text}
                      </div>
                    ))}
                  </div>
                )}
              </>
            ) : (
              truncate(body || reply || '', 240) || (
                <span className="text-[#2B2926]/40 italic">
                  {status === 'unavailable'
                    ? 'No data available'
                    : status === 'queued'
                    ? 'Pending dispatch'
                    : 'No content'}
                </span>
              )
            )}
          </div>
        )}

        {hasExpandable && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="mt-1.5 inline-flex items-center gap-1 text-[10px] font-bold text-[#F55600] hover:opacity-70"
          >
            {expanded ? (
              <>
                <ChevronUp className="w-3 h-3" /> Collapse
              </>
            ) : (
              <>
                <ChevronDown className="w-3 h-3" /> Expand
              </>
            )}
          </button>
        )}
      </div>
    </div>
  );
};

const JourneyTimeline = ({ timeline, loading }) => {
  if (loading) {
    return (
      <div className="space-y-4">
        <SkeletonEvent />
        <SkeletonEvent />
        <SkeletonEvent />
      </div>
    );
  }
  if (!timeline || timeline.length === 0) {
    return (
      <div className="text-center py-16 px-6">
        <FileText className="w-10 h-10 mx-auto text-[#2B2926]/15 mb-2" />
        <p className="text-sm text-[#2B2926]/60">No journey events recorded yet.</p>
      </div>
    );
  }
  // Belt-and-suspenders chronological sort: oldest at the top, newest
  // at the bottom. Backend already returns events sorted ascending by
  // occurred_at, but if any event arrives with a missing/zero timestamp
  // (e.g. a freshly generated LinkedIn draft where sent_at was set to
  // CURRENT_TIMESTAMP by the DB but the row was inserted out-of-order)
  // we re-sort client-side so the visual order always matches the order
  // events actually happened.
  const _ts = (e) => {
    const v = e && e.occurred_at ? new Date(e.occurred_at).getTime() : 0;
    return Number.isFinite(v) ? v : 0;
  };
  // Drop email events that have no usable body. Legacy nexus_outreach
  // rows sometimes have a subject ("re: ai") but empty email_html,
  // producing ghost rows in the timeline that the operator can't
  // expand into anything useful. They also tend to come from
  // cross-campaign attribution (lead enrolled in product A's campaign
  // gets a stub row from product B), so removing them cleans up the
  // visual without hiding real outreach activity — the right-panel
  // flow already shows the canonical per-campaign stages.
  // Non-email event types (linkedin_message, voice_call,
  // email_reply, inbound_message, demo_booked) keep rendering even
  // with empty body because their UI conveys meaning via type/status
  // alone (e.g. "LinkedIn Message · NOT GENERATED" is informative).
  const _isEmptyEmailEvent = (e) => {
    if (!e) return true;
    if (e.type !== 'email_outreach' && e.type !== 'followup_email') return false;
    const body = (e.body || '').trim();
    return body.length === 0;
  };
  const sortedTimeline = [...timeline]
    .filter((e) => !_isEmptyEmailEvent(e))
    .sort((a, b) => _ts(a) - _ts(b));
  return (
    <div className="space-y-0">
      {sortedTimeline.map((event, i) => (
        <TimelineEvent
          key={`${event.type}-${event.occurred_at}-${i}`}
          event={event}
          isLast={i === sortedTimeline.length - 1}
        />
      ))}
    </div>
  );
};

export default JourneyTimeline;
