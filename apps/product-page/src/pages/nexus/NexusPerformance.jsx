/**
 * NexusPerformance — AI-generated periodic insights.
 *
 * Legacy label: "Performance".
 * Backend: /nexus/performance/* (nexus_performance_insights table).
 * Distinct from Analytics (raw metrics) and Reports (digest emails).
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  RefreshCw,
  Sparkles,
  TrendingUp,
} from 'lucide-react';

const STATUS_PILL = {
  running: 'bg-[#F55600]/10 text-[#F55600]',
  ready: 'bg-[#10B981]/15 text-[#10B981]',
  failed: 'bg-[#F55600]/15 text-[#F55600]',
};

const fmtDate = (iso) => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString();
  } catch {
    return iso;
  }
};

const NexusPerformance = ({ authAxios, apiBase, user, setMessage }) => {
  const [insights, setInsights] = useState([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState(null);

  const loadInsights = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await authAxios.get(`${apiBase}/performance`);
      setInsights(res.data?.insights || res.data || []);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  }, [authAxios, apiBase]);

  useEffect(() => {
    loadInsights();
  }, [loadInsights]);

  const generateNew = async () => {
    setGenerating(true);
    setError('');
    try {
      await authAxios.post(`${apiBase}/performance/generate`);
      await loadInsights();
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className="bg-white">
      <div className="px-5 py-4 border-b border-[#2B2926]/10 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-black text-[#2B2926] tracking-tight">Performance</h1>
          <p className="text-xs text-[#2B2926]/60 mt-0.5">
            AI-generated insights into what's working and what isn't.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={generateNew}
            disabled={generating}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#F55600] text-white text-xs font-bold hover:opacity-90 disabled:opacity-50"
          >
            {generating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
            Generate insights
          </button>
          <button
            type="button"
            onClick={loadInsights}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#2B2926]/10 text-xs font-bold text-[#2B2926] hover:bg-[#F55600]/5 disabled:opacity-50"
          >
            <RefreshCw className={['w-3.5 h-3.5', loading ? 'animate-spin' : ''].join(' ')} />
            Refresh
          </button>
        </div>
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
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading insights…
          </div>
        ) : insights.length === 0 ? (
          <div className="text-center py-12">
            <TrendingUp className="w-10 h-10 mx-auto text-[#2B2926]/15 mb-2" />
            <p className="text-xs text-[#2B2926]/60">
              No insights yet. Click <span className="font-bold text-[#F55600]">Generate insights</span> to create your first one.
            </p>
          </div>
        ) : (
          insights.map((it) => {
            const isExp = expanded === (it.id || it._id);
            return (
              <div key={it.id || it._id} className="border border-[#2B2926]/10 rounded-lg overflow-hidden">
                <button
                  type="button"
                  onClick={() => setExpanded(isExp ? null : it.id || it._id)}
                  className="w-full flex items-center justify-between gap-3 px-3 py-2.5 hover:bg-[#F55600]/5 transition-all text-left"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <Sparkles className="w-3.5 h-3.5 text-[#F55600] shrink-0" />
                    <span className="text-xs font-bold text-[#2B2926] truncate">
                      {fmtDate(it.period_start)} — {fmtDate(it.period_end)}
                    </span>
                    <span
                      className={[
                        'inline-flex px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider',
                        STATUS_PILL[it.status] || 'bg-[#2B2926]/5 text-[#2B2926]/60',
                      ].join(' ')}
                    >
                      {it.status || 'unknown'}
                    </span>
                  </div>
                  {it.status === 'ready' && <CheckCircle2 className="w-3.5 h-3.5 text-[#10B981] shrink-0" />}
                </button>
                {isExp && it.summary && (
                  <div className="px-3 py-3 border-t border-[#2B2926]/10 bg-[#2B2926]/[0.02] text-[11px] text-[#2B2926]/70 leading-relaxed whitespace-pre-wrap">
                    {it.summary}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};

export default NexusPerformance;
