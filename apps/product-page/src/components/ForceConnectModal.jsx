import React from 'react';
import { X, ShieldCheck } from 'lucide-react';
import Connections from '../pages/Connections';

const ForceConnectModal = ({ show, onClose, ...connectionsProps }) => {
  if (!show) return null;

  const { connections, user } = connectionsProps;
  const totalConnected = Object.values(connections || {}).reduce((acc, curr) => acc + (curr?.length || 0), 0);
  // H2 fix — same billing-cycle suffix strip as Connections.jsx so an
  // Agency Yearly customer isn't shown a "1 account" fake cap.
  const plan = ((user?.pricing_plan || '') + '')
    .toLowerCase()
    .replace(/_yearly$|_monthly$/, '') || 'free';
  const limits = { free: 1, starter: 3, growth: 5, agency: Infinity, enterprise: Infinity };
  const limit = limits[plan] || 1;
  const isUnlimited = limit === Infinity;

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center p-4 md:p-6 animate-in fade-in duration-300">
      {/* H3 fix — backdrop no longer closes the modal on click. The whole
          point of "Force Connect" is that the user must actually connect
          at least one channel before publishing. Accidental clicks
          outside used to dismiss it silently. The X button is retained
          as the explicit escape hatch (App.jsx re-opens the modal on
          next tab change if totalConnected is still 0). */}
      <div className="absolute inset-0 bg-slate-900/20 backdrop-blur-md" />
      <div className="bg-white rounded-[32px] w-full max-w-5xl max-h-[95vh] shadow-2xl overflow-hidden border border-slate-100 flex flex-col relative animate-in zoom-in-95 duration-300">
        
        {/* Header Overlay for Modal */}
        <div className="absolute top-6 right-8 z-[210] flex items-center gap-4">
          <div className="bg-white px-2.5 py-1 rounded-xl border border-slate-200 flex items-center gap-2.5 shadow-sm scale-90">
            <span className="text-[8px] font-black text-slate-400 uppercase tracking-tighter leading-none">Accounts</span>
            <div className="flex items-baseline gap-0.5">
              <span className="text-sm font-black text-[#2B2926] leading-none">{totalConnected}</span>
              <span className="text-[9px] font-bold text-slate-400 leading-none">/ {isUnlimited ? 'Unlimited' : limit}</span>
            </div>
          </div>
          <button 
            onClick={onClose}
            className="p-2.5 bg-white/10 hover:bg-white/20 rounded-2xl text-slate-400 hover:text-slate-600 transition-all border border-slate-100 shadow-sm group"
          >
            <X className="w-5 h-5 group-hover:rotate-90 transition-transform duration-300" />
          </button>
        </div>

        {/* Scrollable Content Container */}
        <div className="flex-1 overflow-y-auto custom-scrollbar pt-8 pb-4">
          <div className="mb-4 px-4 md:px-12 text-center">
            <div className="inline-flex items-center gap-2 px-3 py-1 bg-orange-50 rounded-full mb-2">
              <ShieldCheck className="w-3.5 h-3.5 text-[#F55600]" />
              <span className="text-[9px] font-black uppercase tracking-widest text-[#F55600]">Secure Connection Required</span>
            </div>
            <h2 className="text-2xl font-black text-[#2B2926] tracking-tight">Connect Your First Channel</h2>
            <p className="text-slate-500 text-xs font-medium mt-1">Link at least one social page to start scheduling with AI.</p>
          </div>
          
          {/* Reuse the existing Connections component - hiding its internal header to save space */}
          <Connections {...connectionsProps} hideHeader={true} />
        </div>
        
        {/* Footer Accent */}
        <div className="h-2 bg-gradient-to-r from-[#F55600] to-[#ffb800] w-full shrink-0" />
      </div>
    </div>
  );
};

export default ForceConnectModal;
