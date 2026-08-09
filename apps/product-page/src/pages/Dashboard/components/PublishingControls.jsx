import React from 'react';
import { Calendar, Clock, CheckCircle2, RefreshCw, Send, FileEdit } from 'lucide-react';
import CustomTimePicker from '../../../components/CustomTimePicker';
import XIcon from '../../../components/icons/XIcon';

// Dynamic platform → logo image. Add a platform here and its logo shows up
// everywhere this component renders a channel — no per-platform JSX branches.
const PLATFORM_IMG = {
  linkedin:  '/linkedlin.jpg',
  facebook:  '/facebook.png',
  instagram: '/instagram.jpg',
  youtube:   '/youtube-icon.png',
};
const PlatformLogo = ({ platform, className = 'w-3.5 h-3.5' }) => {
  if (PLATFORM_IMG[platform]) {
    return <img src={PLATFORM_IMG[platform]} alt={platform} className={`${className} object-contain rounded-sm`} />;
  }
  if (platform === 'tiktok') {
    return (
      <svg className={className} viewBox="0 0 24 24" fill="#010101" aria-label="TikTok">
        <path d="M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.08-.14 1.62.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z" />
      </svg>
    );
  }
  return <XIcon className={className} />;
};

const PublishingControls = ({
  dashboardStep,
  activeActionTab,
  setActiveActionTab,
  scheduledDate,
  setScheduledDate,
  scheduledTime,
  setScheduledTime,
  selectedTimezone,
  setSelectedTimezone,
  selectedDashboardPlatforms,
  connections,
  hasImageSelected,
  toggleTarget,
  selectedTargets,
  handlePublishNow,
  handleSchedule,
  handleSaveDraft,
  isPublishing,
  isScheduling
}) => {
  if (dashboardStep !== 'publish') return null;

  return (
    <div className="bg-white rounded-2xl px-4 py-3 border-2 border-slate-300 shadow-lg border-t-4 border-t-[#F55600] animate-in fade-in slide-in-from-bottom-2 duration-500">
      <div className="flex items-center gap-2 mb-2">
        <Calendar className="w-4 h-4 text-[#F55600]" />
        <h4 className="text-sm font-bold text-[#2B2926] tracking-tight">Post Placement</h4>
      </div>

      <div className="flex bg-slate-50 gap-1 p-1 rounded-lg mb-2 border border-slate-200">
        <button 
          onClick={() => setActiveActionTab('publish')}
          className={`flex-1 px-3 py-2 text-[9px] font-semibold rounded-md transition-all uppercase tracking-widest ${
            activeActionTab === 'publish' 
              ? 'bg-white text-[#F55600] shadow-md border border-orange-100' 
              : 'text-slate-500 hover:text-[#2B2926]'
          }`}
        >
          Publish
        </button>
        <button 
          onClick={() => setActiveActionTab('schedule')}
          className={`flex-1 px-3 py-2 text-[9px] font-semibold rounded-md transition-all uppercase tracking-widest ${
            activeActionTab === 'schedule' 
              ? 'bg-white text-[#F55600] shadow-md border border-orange-100' 
              : 'text-slate-500 hover:text-[#2B2926]'
          }`}
        >
          Schedule
        </button>
        <button 
          onClick={() => setActiveActionTab('draft')}
          className={`flex-1 px-3 py-2 text-[9px] font-semibold rounded-md transition-all uppercase tracking-widest ${
            activeActionTab === 'draft' 
              ? 'bg-white text-[#F55600] shadow-md border border-orange-100' 
              : 'text-slate-500 hover:text-[#2B2926]'
          }`}
        >
          Draft
        </button>
      </div>

      {activeActionTab === 'schedule' && (
        <div className="space-y-3 mb-5 p-3 bg-orange-50/60 rounded-xl border-2 border-orange-100/70 animate-in fade-in zoom-in-95 duration-300">
          <div className="flex items-center gap-2">
             <Clock className="w-3.5 h-3.5 text-[#F55600]" />
             <span className="text-[9px] font-semibold text-[#2B2926] uppercase tracking-widest">Schedule</span>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <label className="text-[8px] font-semibold text-[#2B2926] uppercase ml-1">Date</label>
              <input 
                type="date" 
                value={scheduledDate}
                onChange={(e) => setScheduledDate(e.target.value)}
                className="w-full px-2.5 py-1.5 bg-white border-2 border-slate-200 rounded-lg text-xs font-bold text-[#2B2926] outline-none focus:ring-2 focus:ring-orange-100 focus:border-[#F55600] transition-all"
              />
            </div>
            <div className="space-y-1 flex-1">
              <label className="text-[8px] font-semibold text-[#2B2926] uppercase ml-1">Time & Zone</label>
              <CustomTimePicker 
                selectedTime={scheduledTime}
                onTimeChange={setScheduledTime}
                timezone={selectedTimezone}
              />
            </div>
          </div>
          <p className="text-[8px] text-slate-600 font-bold italic">Common time for all platforms in your timezone.</p>
        </div>
      )}

      <div className="flex flex-col gap-2 w-full mt-3">
        <div className="flex items-center gap-2">
           <CheckCircle2 className="w-3.5 h-3.5 text-[#F55600]" />
           <span className="text-[9px] font-semibold text-[#2B2926] uppercase tracking-widest">Target Accounts</span>
        </div>
        
        <div className="flex flex-wrap gap-1.5 min-h-[40px] items-center">
          {selectedDashboardPlatforms.flatMap(platform => {
            const platformAccounts = connections[platform] || [];
            if (platform === 'instagram' && !hasImageSelected) return [];

            return platformAccounts.map(acc => {
              const isSelected = selectedTargets[platform]?.includes(acc.id);
              return (
                <div
                  key={acc.id}
                  onClick={() => toggleTarget(platform, acc.id)}
                  className={`group relative flex items-center gap-1.5 pl-1.5 pr-3 py-1.5 rounded-full border-2 transition-all cursor-pointer bg-white ${
                    isSelected 
                      ? 'border-[#F55600] bg-orange-50 shadow-md ring-2 ring-orange-100' 
                      : 'border-slate-100 hover:border-orange-200'
                  }`}
                  title={acc.name}
                >
                  <div className="shrink-0 text-[#2B2926]">
                    <PlatformLogo platform={platform} className="w-3.5 h-3.5" />
                  </div>

                  <div className="flex items-center min-w-0">
                    <span className={`text-[10px] font-semibold uppercase tracking-tight truncate ${isSelected ? 'text-[#F55600]' : 'text-[#2B2926]'}`}>
                      {acc.name}
                    </span>
                  </div>
                  
                  {/* Platform Badge */}
                  <div className="absolute -bottom-0.5 -right-0.5 w-3.5 h-3.5 rounded-full flex items-center justify-center border-2 border-white bg-white shadow-sm z-10 overflow-hidden">
                    <PlatformLogo platform={platform} className="w-2.5 h-2.5" />
                  </div>

                  {isSelected && (
                    <div className="absolute -top-1 -right-1 bg-[#F55600] rounded-full p-0.5 border border-white shadow-sm z-20">
                      <CheckCircle2 className="w-1.5 h-1.5 text-white" />
                    </div>
                  )}
                </div>
              );
            });
          })}
        </div>
      </div>

      <div className="flex flex-col gap-1.5 w-full mt-2">
        {activeActionTab === 'publish' ? (
          <button 
            onClick={handlePublishNow}
            disabled={isPublishing || selectedDashboardPlatforms.length === 0}
            className="w-full bg-gradient-to-r from-[#F55600] to-[#F55600] text-white py-2 rounded-lg font-semibold text-[10px] flex items-center justify-center gap-2 shadow-lg shadow-orange-200/50 hover:shadow-xl transition-all disabled:opacity-60 disabled:hover:shadow-lg uppercase tracking-widest"
          >
            {isPublishing ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}
            {isPublishing ? 'Publishing...' : 'Publish Now'}
          </button>
        ) : activeActionTab === 'schedule' ? (
          <button 
            onClick={handleSchedule}
            disabled={isScheduling || selectedDashboardPlatforms.length === 0}
            className="w-full bg-slate-800 text-white py-2 rounded-lg font-semibold text-[10px] flex items-center justify-center gap-2 shadow-lg shadow-slate-200/50 hover:shadow-xl transition-all disabled:opacity-60 disabled:hover:shadow-lg uppercase tracking-widest"
          >
            {isScheduling ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Calendar className="w-3 h-3" />}
            {isScheduling ? 'Scheduling...' : 'Schedule Post'}
          </button>
        ) : (
          <button 
            onClick={handleSaveDraft}
            className="w-full py-2 border-2 border-slate-300 text-[#2B2926] text-[9px] font-semibold rounded-lg hover:border-slate-400 hover:bg-slate-50 transition-all flex items-center justify-center gap-2 uppercase tracking-widest"
          >
            <FileEdit className="w-3 h-3" />
            Save as Draft
          </button>
        )}
      </div>
    </div>
  );
};

export default PublishingControls;
