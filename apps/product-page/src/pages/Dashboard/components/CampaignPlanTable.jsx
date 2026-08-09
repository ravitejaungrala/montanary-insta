import React from 'react';
import { Calendar, Clock, MapPin, CheckCircle2, ArrowLeft, Zap, Linkedin, Instagram, Facebook, Edit3, Search, Sparkles, PartyPopper, X } from 'lucide-react';
import XIcon from '../../../components/icons/XIcon';
import RadialClockPicker from '../../../components/CustomTimePicker';
import CalendarPicker from '../../../components/CalendarPicker';

const CampaignPlanTable = ({ plan, setPlan, setDashboardStep, handleGenerateContent }) => {
  const [editingId, setEditingId] = React.useState(null);

  const getPlatformIcon = (platform) => {
    switch ((platform || '').toLowerCase()) {
      case 'linkedin': return <img src="/linkedlin.jpg" className="w-3.5 h-3.5 object-contain" alt="LinkedIn" />;
      case 'twitter': case 'x': return <XIcon className="text-[#2B2926]" size={14} />;
      case 'instagram': return <img src="/instagram.jpg" className="w-3.5 h-3.5 object-contain" alt="Instagram" />;
      case 'facebook': return <img src="/facebook.png" className="w-3.5 h-3.5 object-contain" alt="Facebook" />;
      default: return <MapPin className="text-gray-400" size={14} />;
    }
  };

  const handleUpdateSlot = (index, field, value) => {
    const newPlan = [...plan];
    newPlan[index] = { ...newPlan[index], [field]: value };
    setPlan(newPlan);
  };

  // Re-derive day-of-week when the user edits the date so the "DAY" cell
  // doesn't go stale (e.g. user changes a Wed slot to Thu).
  const handleUpdateDate = (index, newDate) => {
    const newPlan = [...plan];
    let day = newPlan[index].day || '';
    try {
      const dt = new Date(newDate + 'T00:00:00');
      if (!Number.isNaN(dt.getTime())) {
        day = dt.toLocaleDateString('en-US', { weekday: 'long' }).toUpperCase();
      }
    } catch {}
    newPlan[index] = { ...newPlan[index], date: newDate, day };
    setPlan(newPlan);
  };

  // Festival + research counts for the header chip strip
  const festivalCount = plan.filter(s => s.is_festival).length;
  const researchCount = plan.filter(s => s.needs_research).length;
  const staticCount   = plan.length - researchCount;

  return (
    <div className="space-y-4 animate-in fade-in slide-in-from-bottom-4 duration-700">
      {/* Header Area — compact single-line title row, counters inline at right */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-2 -mt-1">
        <div className="flex items-baseline gap-3">
          <h2 className="text-base font-bold text-[#2B2926] tracking-tight">Campaign Strategy Plan</h2>
          <span className="text-[10px] text-[#2B2926]/55 font-semibold hidden md:inline">· Review and refine your AI posting schedule</span>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div className="px-2 py-1 flex items-center gap-1.5">
            <CheckCircle2 className="w-3.5 h-3.5 text-[#F55600]" />
            <span className="text-[12px] font-bold text-[#F55600] uppercase tracking-[0.14em]">{plan.length} Slots</span>
          </div>
          {researchCount > 0 && (
            <div
              className="px-3 py-1.5 bg-blue-50 text-blue-700 rounded-lg border-2 border-blue-200 flex items-center gap-2 shadow-sm"
              title="Posts that need fresh web research — will be generated on the scheduled date so they catch the latest news/trends."
            >
              <Search className="w-3.5 h-3.5" />
              <span className="text-[9px] font-semibold uppercase tracking-widest">{researchCount} Research</span>
            </div>
          )}
          {staticCount > 0 && (
            <div
              className="px-2 py-1 flex items-center"
              title="Static product/service posts — pre-generated at approval so you can review before they fire."
            >
              <span className="text-[12px] font-bold text-[#065F46] uppercase tracking-[0.14em]">{staticCount} Static</span>
            </div>
          )}
          {festivalCount > 0 && (
            <div
              className="px-3 py-1.5 bg-pink-50 text-pink-700 rounded-lg border-2 border-pink-200 flex items-center gap-2 shadow-sm"
              title="Auto-added festival slots in the planning window."
            >
              <PartyPopper className="w-3.5 h-3.5" />
              <span className="text-[9px] font-semibold uppercase tracking-widest">{festivalCount} Festival</span>
            </div>
          )}
          {/* Close — exits the plan back to the campaign brief */}
          <button
            type="button"
            onClick={() => setDashboardStep('brief')}
            title="Close — back to campaign brief"
            className="ml-1 w-8 h-8 rounded-lg flex items-center justify-center text-[#2B2926]/50 hover:text-[#F55600] hover:bg-[#F55600]/10 border border-[#2B2926]/15 transition-colors"
          >
            <X className="w-4 h-4" strokeWidth={2.5} />
          </button>
        </div>
      </div>

      {/* Plan Table */}
      <div className="bg-white rounded-2xl border-2 border-slate-350 shadow-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-slate-50/60 border-b-2 border-slate-300">
                <th className="px-4 py-3 text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.14em]">Day</th>
                <th className="px-4 py-3 text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.14em]">Date</th>
                <th className="px-4 py-3 text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.14em]">Platform</th>
                <th className="px-4 py-3 text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.14em] text-center">Type</th>
                <th className="px-4 py-3 text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.14em]">Topic & Title</th>
                <th className="px-4 py-3 text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.14em]">Theme</th>
                <th
                  className="px-4 py-3 text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.14em] text-center"
                  title="Research = generated on the scheduled date (catches latest news). Static = pre-generated at approval so you can review."
                >
                  Generation
                </th>
                <th className="px-4 py-3 text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.14em] text-right">Time</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {plan.map((slot, idx) => {
                const isFestival = !!slot.is_festival;
                const needsResearch = !!slot.needs_research;
                const rowTone = isFestival
                  ? 'bg-pink-50/40 hover:bg-pink-50/70 border-l-4 border-l-pink-400'
                  : 'hover:bg-orange-50/20 border-l-4 border-l-transparent';
                return (
                <tr key={idx} className={`transition-colors group ${rowTone}`}>
                  <td className="px-4 py-3">
                    <span className="text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.14em]">
                      {slot.day || '—'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="w-[150px]">
                      <CalendarPicker
                        value={slot.date || ''}
                        onChange={(d) => handleUpdateDate(idx, d)}
                        placeholder="Pick date"
                      />
                    </div>
                    {isFestival && slot.festival_name && (
                      <div className="text-[9px] font-semibold text-pink-700 mt-1 flex items-center gap-1">
                        <PartyPopper size={10} />
                        {slot.festival_name}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1.5 px-2 py-1 bg-slate-100 rounded-lg border-2 border-slate-300 w-fit">
                      {getPlatformIcon(slot.channel)}
                      <span className="text-[9px] font-bold text-[#2B2926] uppercase tracking-[0.1em]">{((slot.channel || '').toLowerCase() === 'twitter') ? 'X' : slot.channel}</span>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-center">
                    <span className="px-2.5 py-1 bg-[#2B2926] text-white rounded-lg text-[9px] font-bold uppercase tracking-[0.14em]">
                      {slot.content_type || 'Post'}
                    </span>
                  </td>
                  <td className="px-4 py-3 min-w-[220px]">
                    <div className="relative group/edit">
                      <input
                        type="text"
                        value={slot.topic || ''}
                        onChange={(e) => handleUpdateSlot(idx, 'topic', e.target.value)}
                        className="w-full bg-transparent border-none text-xs font-semibold text-[#2B2926] outline-none focus:ring-2 focus:ring-orange-100 rounded px-1 transition-all"
                      />
                      <Edit3 className="absolute -left-5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[#F55600] group-hover/edit:scale-110 group-hover/edit:text-[#2B2926] transition-all" strokeWidth={2.4} />
                    </div>
                    {slot.cta && (
                      <p className="text-[10px] text-slate-600 font-bold px-1 mt-1 italic">{slot.cta}</p>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <input
                      type="text"
                      value={slot.theme || ''}
                      onChange={(e) => handleUpdateSlot(idx, 'theme', e.target.value)}
                      className="text-[10px] font-semibold text-[#2B2926] bg-slate-100 px-2 py-1 rounded-md border border-slate-200 outline-none focus:ring-2 focus:ring-orange-100 max-w-[120px]"
                    />
                  </td>
                  <td className="px-4 py-3 text-center">
                    <button
                      type="button"
                      onClick={() => handleUpdateSlot(idx, 'needs_research', !needsResearch)}
                      title={
                        needsResearch
                          ? (slot.research_reason || 'Will be generated on the scheduled date with fresh web research.')
                          : 'Pre-generates at approval so you can review before fire date.'
                      }
                      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[9px] font-semibold uppercase tracking-widest border-2 transition-colors ${
                        needsResearch
                          ? 'bg-blue-50 text-blue-700 border-blue-200 hover:bg-blue-100'
                          : 'bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100'
                      }`}
                    >
                      {needsResearch && <Search size={10} />}
                      {needsResearch ? 'Research' : 'Static'}
                    </button>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="inline-block w-[160px] ml-auto">
                      <RadialClockPicker
                        selectedTime={slot.time || '12:00'}
                        onTimeChange={(t) => handleUpdateSlot(idx, 'time', t)}
                      />
                    </div>
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Footer Actions */}
        <div className="bg-slate-50/50 p-6 flex items-center justify-between border-t border-slate-100">
           <p className="text-[10px] text-slate-600 font-semibold uppercase tracking-widest">
             <span className="text-[#F55600]">Tip:</span> Click any cell to edit — Date, Time, Topic, Theme, and Research/Static toggle.
           </p>
           
           <button 
             onClick={handleGenerateContent}
             className="px-8 py-4 bg-[#F55600] hover:bg-[#e65a2b] text-white rounded-2xl font-semibold text-xs flex items-center gap-3 transition-all shadow-xl shadow-orange-100 group"
           >
             <Zap className="w-4 h-4 fill-white animate-pulse" />
             Approve & Generate Campaign
           </button>
        </div>
      </div>
    </div>
  );
};

export default CampaignPlanTable;
