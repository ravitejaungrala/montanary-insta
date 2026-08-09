import React from 'react';
import { Construction } from 'lucide-react';

/**
 * Shared placeholder for Nexus pages that haven't been fleshed out yet.
 * Lets the sub-nav navigate cleanly between sections while individual
 * page agents fill in the real implementations.
 *
 * Each real page imports this only as a fallback for empty states.
 */
const NexusPlaceholder = ({ title, hint }) => (
  <div className="p-8 max-w-4xl mx-auto">
    <div className="bg-white border border-[#2B2926]/10 rounded-2xl p-12 text-center">
      <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-[#F55600]/5 mb-4">
        <Construction className="w-7 h-7 text-[#F55600]" />
      </div>
      <h2 className="text-2xl font-black text-[#2B2926] mb-2">{title}</h2>
      {hint && <p className="text-sm text-[#2B2926]/60 max-w-md mx-auto">{hint}</p>}
    </div>
  </div>
);

export default NexusPlaceholder;
