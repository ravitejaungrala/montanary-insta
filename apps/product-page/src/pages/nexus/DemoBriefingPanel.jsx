/**
 * DemoBriefingPanel — right-pane briefing for leads with status='demo_scheduled'.
 *
 * Port of legacy DemoBriefingPanelContent. Shows:
 *   - Booking card (date/time + status badge)
 *   - Briefing sections (About Person, About Company, Talking Points,
 *     Questions to Ask, Likely Objections)
 *   - Regenerate button (POSTs /journey/leads/{id}/demo/regenerate)
 *
 * Briefing states: loading / no_briefing / generating / failed / ready.
 */
import React, { useState } from 'react';
import {
  AlertCircle,
  Building2,
  Calendar,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  HelpCircle,
  Loader2,
  RefreshCw,
  Shield,
  Target,
  User as UserIcon,
  X,
} from 'lucide-react';
import { fmtDate, fmtRelative } from './NexusJourney';

const BOOKING_STATUS = {
  pending_rep_confirm: { label: 'Awaiting Confirmation', cls: 'bg-[#F55600]/10 text-[#F55600]' },
  scheduled: { label: 'Scheduled', cls: 'bg-[#2B2926]/5 text-[#2B2926]/60' },
  confirmed: { label: 'Confirmed', cls: 'bg-[#10B981]/15 text-[#10B981]' },
  completed: { label: 'Completed', cls: 'bg-[#10B981]/15 text-[#10B981]' },
  cancelled: { label: 'Cancelled', cls: 'bg-[#2B2926]/5 text-[#2B2926]/40' },
  no_show: { label: 'No Show', cls: 'bg-[#2B2926]/5 text-[#2B2926]/40' },
};

const Section = ({ icon: Icon, title, children, defaultOpen = true }) => {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border border-[#2B2926]/10 rounded-lg overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-2 px-3 py-2 bg-white hover:bg-[#F55600]/5 transition-all"
      >
        <span className="inline-flex items-center gap-1.5 text-[11px] font-bold text-[#2B2926]">
          {Icon && <Icon className="w-3.5 h-3.5 text-[#F55600]" />}
          {title}
        </span>
        {open ? (
          <ChevronUp className="w-3.5 h-3.5 text-[#2B2926]/40" />
        ) : (
          <ChevronDown className="w-3.5 h-3.5 text-[#2B2926]/40" />
        )}
      </button>
      {open && <div className="px-3 py-2.5 bg-white border-t border-[#2B2926]/5">{children}</div>}
    </div>
  );
};

const renderList = (items) => {
  if (!items || !Array.isArray(items) || items.length === 0) {
    return <p className="text-[11px] text-[#2B2926]/40 italic">None recorded.</p>;
  }
  return (
    <ul className="space-y-1.5 text-[11px] text-[#2B2926]/70 leading-relaxed list-disc list-inside">
      {items.map((it, i) => (
        <li key={i}>{typeof it === 'string' ? it : it?.text || JSON.stringify(it)}</li>
      ))}
    </ul>
  );
};

const renderObjections = (items) => {
  if (!items || !Array.isArray(items) || items.length === 0) {
    return <p className="text-[11px] text-[#2B2926]/40 italic">None recorded.</p>;
  }
  return (
    <div className="space-y-2.5">
      {items.map((ob, i) => (
        <div key={i} className="text-[11px]">
          <div className="font-bold text-[#2B2926]">{ob?.objection || ob?.q || `Objection ${i + 1}`}</div>
          {(ob?.response || ob?.a) && (
            <div className="text-[#2B2926]/70 mt-1 leading-relaxed">{ob.response || ob.a}</div>
          )}
        </div>
      ))}
    </div>
  );
};

const DemoBriefingPanel = ({ booking, briefing, loading, onRegenerate }) => {
  const status = booking?.status;
  const statusMeta = BOOKING_STATUS[status] || {
    label: status || 'Unknown',
    cls: 'bg-[#2B2926]/5 text-[#2B2926]/60',
  };
  const briefStatus = briefing?.status;

  if (loading) {
    return (
      <div className="p-4 flex items-center justify-center min-h-[300px]">
        <div className="text-center">
          <Loader2 className="w-5 h-5 mx-auto text-[#F55600] animate-spin" />
          <p className="text-xs text-[#2B2926]/60 mt-2">Loading briefing…</p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-4 space-y-3">
      <div className="text-[10px] uppercase tracking-wider text-[#2B2926]/40 font-bold">
        Demo Briefing
      </div>

      {/* Booking card */}
      {booking ? (
        <div className="border border-[#2B2926]/10 rounded-lg p-3 bg-white">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="inline-flex items-center gap-1.5 text-[11px] font-bold text-[#2B2926]">
                <Calendar className="w-3.5 h-3.5 text-[#F55600]" />
                {fmtDate(booking.scheduled_at)}
              </div>
              {booking.attendee_name && (
                <div className="text-[10px] text-[#2B2926]/60 mt-1">
                  Attendee: {booking.attendee_name}
                </div>
              )}
              {booking.rep_name && (
                <div className="text-[10px] text-[#2B2926]/60">
                  Rep: {booking.rep_name}
                </div>
              )}
            </div>
            <span
              className={[
                'shrink-0 inline-flex px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider',
                statusMeta.cls,
              ].join(' ')}
            >
              {statusMeta.label}
            </span>
          </div>
        </div>
      ) : (
        <div className="border border-[#2B2926]/10 rounded-lg p-4 text-center text-[11px] text-[#2B2926]/40">
          No booking recorded yet.
        </div>
      )}

      {/* Briefing states */}
      {!booking ? null : !briefing ? (
        <div className="border border-[#2B2926]/10 rounded-lg p-4 text-center text-[11px] text-[#2B2926]/60">
          Briefing will be generated once the demo is confirmed.
        </div>
      ) : briefStatus === 'generating' ? (
        <div className="border border-[#F55600]/30 rounded-lg p-4 text-center bg-[#F55600]/5">
          <Loader2 className="w-4 h-4 mx-auto text-[#F55600] animate-spin" />
          <p className="text-[11px] text-[#F55600] font-bold mt-2">Generating briefing…</p>
          <p className="text-[10px] text-[#2B2926]/60 mt-1">
            This usually takes 30–60 seconds.
          </p>
        </div>
      ) : briefStatus === 'failed' ? (
        <div className="border border-[#F55600]/30 rounded-lg p-4 text-center bg-[#F55600]/5">
          <AlertCircle className="w-4 h-4 mx-auto text-[#F55600]" />
          <p className="text-[11px] text-[#F55600] font-bold mt-2">Generation failed.</p>
          <button
            type="button"
            onClick={onRegenerate}
            className="mt-2 inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-[#F55600] text-white text-[10px] font-bold hover:opacity-90"
          >
            <RefreshCw className="w-3 h-3" />
            Regenerate
          </button>
        </div>
      ) : (
        <>
          {briefing.about_person && (
            <Section icon={UserIcon} title="About the Person">
              <p className="text-[11px] text-[#2B2926]/70 leading-relaxed whitespace-pre-wrap">
                {briefing.about_person}
              </p>
            </Section>
          )}
          {briefing.about_company && (
            <Section icon={Building2} title="About the Company">
              <p className="text-[11px] text-[#2B2926]/70 leading-relaxed whitespace-pre-wrap">
                {briefing.about_company}
              </p>
            </Section>
          )}
          {briefing.why_this_product && (
            <Section icon={Target} title="Why This Product">
              <p className="text-[11px] text-[#2B2926]/70 leading-relaxed whitespace-pre-wrap">
                {briefing.why_this_product}
              </p>
            </Section>
          )}
          <Section icon={CheckCircle2} title="Talking Points">
            {renderList(briefing.talking_points)}
          </Section>
          <Section icon={HelpCircle} title="Questions to Ask">
            {renderList(briefing.questions_to_ask)}
          </Section>
          <Section icon={Shield} title="Likely Objections" defaultOpen={false}>
            {renderObjections(briefing.likely_objections)}
          </Section>

          <button
            type="button"
            onClick={onRegenerate}
            className="w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg border border-[#2B2926]/10 text-[11px] font-bold text-[#2B2926] hover:bg-[#F55600]/5"
          >
            <RefreshCw className="w-3 h-3" />
            Regenerate Briefing
          </button>
          {briefing.regenerated_at && (
            <div className="text-[10px] text-[#2B2926]/40 text-center">
              Last regenerated: {fmtRelative(briefing.regenerated_at)}
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default DemoBriefingPanel;
