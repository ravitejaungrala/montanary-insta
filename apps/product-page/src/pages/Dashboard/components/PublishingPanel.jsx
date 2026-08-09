import React from 'react';
import { Clock, Send, Zap, Sparkles } from 'lucide-react';

const PublishingPanel = ({ postStrategy, handleSchedulePost, handlePublishPost, publishing, activeVisual }) => {
  return (
    <div className="bg-gradient-to-r from-orange-50/40 to-slate-50/30 rounded-[28px] p-6 border-2 border-orange-100/60 flex flex-col md:flex-row items-center justify-between gap-6">
      <div className="flex items-center gap-4">
        <div className="w-12 h-12 rounded-2xl bg-white flex items-center justify-center text-[#F55600] shadow-lg border-2 border-orange-100 group">
          {postStrategy === 'schedule' ? (
            <Clock className="w-6 h-6 group-hover:rotate-12 transition-transform duration-500" />
          ) : (
            <Send className="w-6 h-6 group-hover:-rotate-12 transition-transform duration-500" />
          )}
        </div>
        <div>
          <h3 className="text-lg font-black text-[#2B2926] tracking-tight">
            {postStrategy === 'schedule' ? 'Ready to Schedule?' : 'Ready to Launch?'}
          </h3>
          <p className="text-[9px] font-bold text-slate-500 mt-0.5 uppercase tracking-widest flex items-center gap-2">
            <Sparkles className="w-3 h-3 text-orange-400" />
            Strategy selected: {postStrategy}
          </p>
        </div>
      </div>

      <div className="flex items-center gap-3 w-full md:w-auto">
        {postStrategy === 'schedule' ? (
          <button
            onClick={handleSchedulePost}
            disabled={publishing}
            className="flex-1 md:flex-none bg-white hover:bg-slate-50 text-[#2B2926] px-8 py-3 rounded-[18px] font-black text-[10px] uppercase tracking-widest transition-all border-2 border-slate-200 shadow-sm active:scale-95 disabled:opacity-50">
            {publishing ? 'Scheduling...' : 'Confirm Schedule'}
          </button>
        ) : (
          <button
            onClick={handlePublishPost}
            disabled={publishing}
            className="flex-1 md:flex-none bg-white hover:bg-slate-50 text-[#2B2926] px-8 py-3 rounded-[18px] font-black text-[10px] uppercase tracking-widest transition-all border-2 border-slate-200 shadow-sm active:scale-95 disabled:opacity-50">
            {publishing ? 'Publishing...' : 'Save as Draft'}
          </button>
        )}
        <button
          onClick={handlePublishPost}
          disabled={publishing}
          className="flex-1 md:flex-none bg-gradient-to-r from-[#F55600] to-[#F55600] hover:shadow-xl text-white px-10 py-3 rounded-[18px] font-black text-[10px] uppercase tracking-widest flex items-center justify-center gap-2.5 transition-all shadow-lg shadow-orange-200/50 group relative overflow-hidden disabled:opacity-50 active:scale-95">
          <div className="absolute inset-0 bg-white/10 translate-y-full group-hover:translate-y-0 transition-transform duration-500"></div>
          <Zap className="w-4 h-4 fill-white relative z-10" />
          <span className="relative z-10 text-[10px]">{publishing ? 'Processing...' : (postStrategy === 'schedule' ? 'Finalize & Schedule' : 'Direct Dispatch')}</span>
        </button>
      </div>
    </div>
  );
};

export default PublishingPanel;
