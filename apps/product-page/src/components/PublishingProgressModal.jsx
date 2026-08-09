import React from 'react';
import { CheckCircle2, XCircle, Loader2, Send, X } from 'lucide-react';
import PlatformLogo from './PlatformLogo';

// Per-platform publish status modal.
//
// Backend `/post` returns everything at once (no streaming), so the modal
// has two states:
//   • `results === null` → all platforms show a spinner ("Publishing…")
//   • `results` populated → each platform shows ✅ Success or ❌ Failure
//
// The parent (Dashboard.jsx handlePublishNow / App.jsx handlePublish) is
// responsible for opening the modal BEFORE the fetch and setting results
// AFTER the fetch completes.
//
// If you want streaming per-platform updates later (SSE / WebSocket), the
// props stay the same — you'd just push partial `results` incrementally.
export default function PublishingProgressModal({
  open,
  platforms = [],           // array of platform slugs the publish attempt is targeting
  results = null,           // { linkedin: [{status: 'Success ✅'|'...'}, ...], ... } or null while pending
  onClose,
  autoCloseSeconds = 4,     // auto-close N seconds after ALL platforms resolved (0 to disable)
}) {
  // Auto-close timer once results arrive.
  React.useEffect(() => {
    if (!open || !results || !autoCloseSeconds) return;
    const t = setTimeout(() => onClose?.(), autoCloseSeconds * 1000);
    return () => clearTimeout(t);
  }, [open, results, autoCloseSeconds, onClose]);

  if (!open) return null;

  // Normalise: derive per-platform outcome.
  //   'pending' → still publishing (or results not yet available)
  //   'success' → at least one account in this platform succeeded
  //   'failure' → all accounts on this platform failed
  //   'mixed'   → some accounts succeeded, others failed
  const outcomeFor = (platform) => {
    if (!results) return 'pending';
    const entries = Array.isArray(results?.[platform]) ? results[platform] : [];
    if (entries.length === 0) return 'pending';
    const succ = entries.filter(e => String(e?.status || '').toLowerCase().startsWith('success')).length;
    if (succ === entries.length) return 'success';
    if (succ === 0) return 'failure';
    return 'mixed';
  };

  const allResolved = results && platforms.every(p => outcomeFor(p) !== 'pending');

  const rowIcon = (outcome) => {
    if (outcome === 'success') return <CheckCircle2 className="w-5 h-5 text-emerald-500" />;
    if (outcome === 'failure') return <XCircle className="w-5 h-5 text-red-500" />;
    if (outcome === 'mixed')   return <XCircle className="w-5 h-5 text-amber-500" />;
    return <Loader2 className="w-5 h-5 text-[#F55600] animate-spin" />;
  };

  const rowLabel = (outcome, platform) => {
    if (outcome === 'success') return 'Done';
    if (outcome === 'failure') return 'Failed';
    if (outcome === 'mixed')   return 'Partial';
    return 'Publishing…';
  };

  const platformPretty = (p) => {
    if (p === 'twitter') return 'X (Twitter)';
    if (p === 'tiktok')  return 'TikTok';
    return p.charAt(0).toUpperCase() + p.slice(1);
  };

  return (
    <div className="fixed inset-0 z-[220] flex items-center justify-center p-4 animate-in fade-in duration-200">
      {/* Backdrop — non-dismissible until all platforms resolved to prevent
          accidental clicks mid-publish. */}
      <div
        className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm z-0"
        onClick={allResolved ? onClose : undefined}
      />
      <div className="relative z-10 bg-white rounded-3xl w-full max-w-md shadow-2xl overflow-hidden border border-slate-200 animate-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="p-5 border-b border-slate-100 bg-slate-50/60 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-[#F55600] text-white flex items-center justify-center shadow-sm">
              <Send className="w-4 h-4" />
            </div>
            <div>
              <h3 className="text-sm font-black text-[#2B2926] tracking-tight">
                {allResolved ? 'Publish complete' : 'Publishing…'}
              </h3>
              <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500 mt-0.5">
                {platforms.length} platform{platforms.length === 1 ? '' : 's'}
              </p>
            </div>
          </div>
          {allResolved && (
            <button
              type="button"
              onClick={onClose}
              className="p-2 rounded-xl text-slate-400 hover:text-[#2B2926] hover:bg-slate-100 transition-colors"
              aria-label="Close"
            >
              <X className="w-5 h-5" />
            </button>
          )}
        </div>

        {/* Platform rows */}
        <div className="p-4 space-y-2 max-h-[60vh] overflow-y-auto">
          {platforms.length === 0 ? (
            <p className="text-sm text-slate-500 text-center py-6">No platforms selected.</p>
          ) : (
            platforms.map((p) => {
              const outcome = outcomeFor(p);
              return (
                <div
                  key={p}
                  className={`flex items-center justify-between gap-3 px-4 py-3 rounded-2xl border transition-colors ${
                    outcome === 'pending'
                      ? 'border-orange-200 bg-orange-50/50'
                      : outcome === 'success'
                        ? 'border-emerald-200 bg-emerald-50/50'
                        : outcome === 'failure'
                          ? 'border-red-200 bg-red-50/50'
                          : 'border-amber-200 bg-amber-50/50'
                  }`}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="w-8 h-8 rounded-xl bg-white ring-1 ring-slate-200 flex items-center justify-center shrink-0">
                      <PlatformLogo platform={p} className="w-5 h-5" />
                    </div>
                    <div className="min-w-0">
                      <p className="text-xs font-black text-[#2B2926] uppercase tracking-widest truncate">
                        {platformPretty(p)}
                      </p>
                      <p className="text-[10px] font-semibold text-slate-500 mt-0.5">
                        {rowLabel(outcome, p)}
                      </p>
                    </div>
                  </div>
                  <div className="shrink-0">{rowIcon(outcome)}</div>
                </div>
              );
            })
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-slate-100 bg-slate-50/60 flex items-center justify-between">
          <p className="text-[10px] text-slate-500 font-semibold">
            {allResolved
              ? (autoCloseSeconds > 0 ? `Closes automatically in a moment…` : 'You can close this now.')
              : 'Please keep this window open until all platforms finish.'}
          </p>
          {allResolved && (
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-xl bg-[#2B2926] text-white text-[10px] font-black uppercase tracking-widest hover:bg-[#F55600] transition-colors"
            >
              Done
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
