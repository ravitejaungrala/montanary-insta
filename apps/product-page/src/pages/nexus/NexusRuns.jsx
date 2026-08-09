/**
 * NexusRuns — list of campaign / discovery runs.
 *
 * Legacy parity: "Runs" tab in GTM Journey section.
 * Each "run" = one campaign execution (campaigns table). Shows status,
 * progress, lead counts, last run time.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  Loader2,
  PlayCircle,
  RefreshCw,
  Target,
} from 'lucide-react';

const STATUS_PILL = {
  draft: 'bg-[#2B2926]/5 text-[#2B2926]/60',
  analyzing: 'bg-[#F55600]/10 text-[#F55600]',
  generating_leads: 'bg-[#F55600]/10 text-[#F55600]',
  personalizing: 'bg-[#F55600]/10 text-[#F55600]',
  ready: 'bg-[#10B981]/15 text-[#10B981]',
  completed: 'bg-[#10B981]/15 text-[#10B981]',
  canceled: 'bg-[#2B2926]/5 text-[#2B2926]/40',
};

const fmtRel = (iso) => {
  if (!iso) return '—';
  try {
    const d = Math.max(0, Date.now() - new Date(iso).getTime());
    const m = Math.floor(d / 60000);
    if (m < 1) return 'just now';
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
  } catch {
    return '—';
  }
};

const NexusRuns = ({ authAxios, apiBase, user, setMessage }) => {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const loadRuns = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authAxios.get(`${apiBase}/campaigns`);
      setRuns(res.data?.campaigns || res.data || []);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  }, [authAxios, apiBase]);

  useEffect(() => {
    loadRuns();
  }, [loadRuns]);

  return (
    <div className="bg-white">
      <div className="px-5 py-4 border-b border-[#2B2926]/10 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-black text-[#2B2926] tracking-tight">Runs</h1>
          <p className="text-xs text-[#2B2926]/60 mt-0.5">
            Campaign executions — analyze, generate, personalize, send.
          </p>
        </div>
        <button
          type="button"
          onClick={loadRuns}
          disabled={loading}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#2B2926]/10 text-xs font-bold text-[#2B2926] hover:bg-[#F55600]/5 disabled:opacity-50"
        >
          <RefreshCw className={['w-3.5 h-3.5', loading ? 'animate-spin' : ''].join(' ')} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="mx-5 mt-3 px-3 py-2 rounded-lg bg-[#F55600]/10 border border-[#F55600]/30 inline-flex items-center gap-2 text-xs text-[#F55600]">
          <AlertCircle className="w-3.5 h-3.5" />
          {error}
        </div>
      )}

      <div className="p-5 space-y-3">
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-[#2B2926]/60">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading runs…
          </div>
        ) : runs.length === 0 ? (
          <div className="text-center py-12">
            <PlayCircle className="w-10 h-10 mx-auto text-[#2B2926]/15 mb-2" />
            <p className="text-xs text-[#2B2926]/60">No runs yet.</p>
          </div>
        ) : (
          runs.map((r) => {
            const status = r.status || 'draft';
            return (
              <div
                key={r.id || r._id}
                className="border border-[#2B2926]/10 rounded-lg p-3 hover:bg-[#F55600]/5 transition-all"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-bold text-[#2B2926] truncate">{r.name || 'Unnamed run'}</span>
                      <span
                        className={[
                          'inline-flex px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider',
                          STATUS_PILL[status] || 'bg-[#2B2926]/5 text-[#2B2926]/60',
                        ].join(' ')}
                      >
                        {status.replace('_', ' ')}
                      </span>
                    </div>
                    <div className="text-[10px] text-[#2B2926]/60 mt-1 inline-flex items-center gap-3">
                      <span className="inline-flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {fmtRel(r.updated_at || r.created_at)}
                      </span>
                      {r.icp?.industries && (
                        <span className="inline-flex items-center gap-1">
                          <Target className="w-3 h-3" />
                          {Array.isArray(r.icp.industries) ? r.icp.industries.slice(0, 2).join(', ') : ''}
                        </span>
                      )}
                    </div>
                  </div>
                  {(status === 'completed' || status === 'ready') ? (
                    <CheckCircle2 className="w-4 h-4 text-[#10B981]" />
                  ) : (
                    <PlayCircle className="w-4 h-4 text-[#F55600]" />
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};

export default NexusRuns;
