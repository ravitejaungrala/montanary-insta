// Kept for backward compatibility — the active Nexus surface is now
// NexusLayout.jsx (the multi-section shell). This file remains so any
// legacy imports of `NexusIndex` continue to resolve.

import React from 'react';
import { Zap } from 'lucide-react';
import { NEXUS_ENABLED } from '../../utils/config';

/**
 * NEXUS landing placeholder.
 *
 * Phase-0 stub for the NEXUS port. Not wired into App.jsx routes or the
 * sidebar yet — that happens in Phase 8 once the backend slices are in
 * place (see docs/nexus-migration/PHASE_PLAN.md).
 *
 * Renders nothing useful if the feature flag is off, so importing this
 * module in production builds is safe.
 *
 * Palette: only the four colors permitted by docs/BRAND_COLORS.md
 * (`#F55600`, `#10B981`, black, white — greys via black/white opacity).
 */
const NexusIndex = () => {
  if (!NEXUS_ENABLED) {
    return null;
  }

  return (
    <div className="min-h-full w-full flex items-center justify-center p-8 bg-white">
      <div className="max-w-2xl w-full text-center">
        <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-[#F55600] mb-6">
          <Zap className="w-8 h-8 text-white" />
        </div>
        <h1 className="text-4xl font-black tracking-tight text-[#2B2926] mb-3">
          Nexus
        </h1>
        <p className="text-base text-[#2B2926]/60 max-w-md mx-auto mb-8">
          AI-powered B2B sales engagement is being integrated into Pipelyt.
          This surface will light up as features land slice-by-slice.
        </p>
        <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full border border-[#2B2926]/10 bg-white">
          <span className="w-2 h-2 rounded-full bg-[#10B981]" />
          <span className="text-xs font-bold uppercase tracking-widest text-[#2B2926]/60">
            Migration in progress
          </span>
        </div>
      </div>
    </div>
  );
};

export default NexusIndex;
