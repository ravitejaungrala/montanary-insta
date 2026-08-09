import React from 'react';
import { Rocket, RefreshCw, Sparkles } from 'lucide-react';

const ReviewActions = ({ setDashboardStep, handleGenerateContent }) => {
  return (
    <div className="mt-6 flex gap-3">
      <button
        onClick={() => setDashboardStep('publish')}
        className="flex-1 bg-gradient-to-r from-[#F55600] to-[#F55600] hover:shadow-xl text-white px-6 py-3 rounded-lg font-semibold text-[10px] shadow-lg shadow-orange-200/50 active:scale-95 transition-all flex items-center justify-center gap-2.5 group uppercase tracking-widest">
        <Rocket className="w-4 h-4 transition-transform group-hover:scale-110" /> 
        Ready to Publish
      </button>
      <button
        onClick={handleGenerateContent}
        className="flex-1 bg-[#2B2926] text-white border-2 border-[#2B2926] px-6 py-3 rounded-lg font-semibold text-[10px] hover:bg-[#F55600] hover:border-[#F55600] transition-all flex items-center justify-center gap-2.5 uppercase tracking-widest"
      >
        <RefreshCw className="w-4 h-4" /> Regenerate
      </button>
    </div>
  );
};

export default ReviewActions;
