import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  PhoneCall,
  PhoneOff,
  ArrowLeft,
  Loader2,
  AlertCircle,
  FileText,
  Search,
  X,
  Mic,
} from 'lucide-react';

/**
 * NexusVoice
 *
 * List of outbound Twilio voice calls + per-call detail view. From the list
 * users can initiate a new call (lead picker typeahead + script textarea).
 *
 * Palette: PIPELYT mandatory four — #F55600, #10B981, black, white.
 */

const OUTCOME_BADGE = {
  interested:         'text-[#10B981] bg-[#10B981]/10',
  callback_requested: 'text-[#F55600] bg-[#F55600]/10',
  not_interested:     'text-[#2B2926]/40 bg-[#2B2926]/5',
  no_answer:          'text-[#2B2926]/40 bg-[#2B2926]/5',
};

function relativeTime(iso) {
  if (!iso) return '—';
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return '—';
  const diff = Date.now() - t;
  const sec = Math.round(diff / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  return `${day}d ago`;
}

function absoluteTime(iso) {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

function fmtDuration(sec) {
  if (!sec && sec !== 0) return '—';
  const s = Math.max(0, Math.floor(Number(sec) || 0));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}:${String(r).padStart(2, '0')}`;
}

function prettyOutcome(o) {
  if (!o) return 'unknown';
  return String(o).replace(/_/g, ' ');
}

const NexusVoice = ({ authAxios, setMessage = () => {} }) => {
  const [mode, setMode] = useState('list'); // 'list' | 'detail'
  const [activeId, setActiveId] = useState(null);

  const [calls, setCalls] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notConfigured, setNotConfigured] = useState(false);
  const [showInitiate, setShowInitiate] = useState(false);

  const fetchCalls = useCallback(async () => {
    setError('');
    setLoading(true);
    setNotConfigured(false);
    try {
      const res = await authAxios.get('/nexus/voice/calls?limit=50&offset=0');
      const data = Array.isArray(res.data) ? res.data : (res.data?.calls || []);
      setCalls(data);
    } catch (err) {
      if (err?.response?.status === 404) {
        setNotConfigured(true);
        setCalls([]);
      } else {
        setError(err?.response?.data?.detail || err.message || 'Failed to load calls');
      }
    } finally {
      setLoading(false);
    }
  }, [authAxios]);

  useEffect(() => { fetchCalls(); }, [fetchCalls]);

  if (mode === 'detail' && activeId) {
    return (
      <CallDetail
        authAxios={authAxios}
        callId={activeId}
        onBack={() => { setMode('list'); setActiveId(null); }}
      />
    );
  }

  return (
    <div className="p-8 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6 gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-black text-[#2B2926] flex items-center gap-2">
            <PhoneCall className="w-6 h-6 text-[#F55600]" />
            Voice Calls
          </h1>
          <p className="text-sm text-[#2B2926]/60 mt-1">
            Outbound Twilio calls + transcripts + outcomes.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setShowInitiate(true)}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold bg-[#F55600] text-white hover:bg-[#F55600]/90"
        >
          <PhoneCall className="w-4 h-4" />
          New Call
        </button>
      </div>

      {/* States */}
      {loading && (
        <div className="bg-white border border-[#2B2926]/10 rounded-2xl p-12 flex items-center justify-center">
          <Loader2 className="w-5 h-5 animate-spin text-[#F55600]" />
          <span className="ml-3 text-sm text-[#2B2926]/60">Loading calls…</span>
        </div>
      )}

      {!loading && notConfigured && (
        <div className="bg-white border border-[#2B2926]/10 rounded-2xl p-12 text-center">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-[#F55600]/5 mb-4">
            <PhoneOff className="w-7 h-7 text-[#F55600]" />
          </div>
          <h2 className="text-xl font-black text-[#2B2926] mb-2">Voice not configured</h2>
          <p className="text-sm text-[#2B2926]/60 max-w-md mx-auto">
            The voice endpoint isn't available in this workspace yet. Once Twilio
            credentials are connected, calls and transcripts will appear here.
          </p>
        </div>
      )}

      {!loading && error && (
        <div className="bg-white border border-[#F55600]/30 rounded-2xl p-6 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-[#F55600] mt-0.5" />
          <div className="flex-1">
            <p className="text-sm font-bold text-[#2B2926]">Couldn't load calls</p>
            <p className="text-xs text-[#2B2926]/60 mt-1">{error}</p>
            <button
              type="button"
              onClick={fetchCalls}
              className="mt-3 text-xs font-bold text-[#F55600] hover:underline"
            >
              Retry
            </button>
          </div>
        </div>
      )}

      {!loading && !error && !notConfigured && calls.length === 0 && (
        <div className="bg-white border border-[#2B2926]/10 rounded-2xl p-12 text-center">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-[#F55600]/5 mb-4">
            <PhoneCall className="w-7 h-7 text-[#F55600]" />
          </div>
          <h2 className="text-xl font-black text-[#2B2926] mb-2">No voice calls yet</h2>
          <p className="text-sm text-[#2B2926]/60 max-w-md mx-auto">
            No voice calls yet. Initiate one from a lead's detail view.
          </p>
          <button
            type="button"
            onClick={() => setShowInitiate(true)}
            className="mt-5 inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold bg-[#F55600] text-white hover:bg-[#F55600]/90"
          >
            <PhoneCall className="w-4 h-4" />
            Start a call
          </button>
        </div>
      )}

      {!loading && !error && calls.length > 0 && (
        <div className="bg-white border border-[#2B2926]/10 rounded-2xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-[#2B2926]/[0.02] text-left">
              <tr className="text-[11px] uppercase tracking-wide text-[#2B2926]/40">
                <th className="px-4 py-3 font-bold">Lead</th>
                <th className="px-4 py-3 font-bold">Started</th>
                <th className="px-4 py-3 font-bold">Duration</th>
                <th className="px-4 py-3 font-bold">Outcome</th>
                <th className="px-4 py-3 font-bold">Transcript</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((c) => {
                const badge = OUTCOME_BADGE[c.outcome] || 'text-[#2B2926]/40 bg-[#2B2926]/5';
                const hasTranscript = !!(c.transcript && String(c.transcript).trim().length > 0);
                return (
                  <tr
                    key={c.id}
                    onClick={() => { setActiveId(c.id); setMode('detail'); }}
                    className="border-t border-[#2B2926]/5 hover:bg-[#F55600]/[0.02] cursor-pointer"
                  >
                    <td className="px-4 py-3 font-bold text-[#2B2926]">
                      {c.lead_email || c.lead?.email || c.lead_id?.slice?.(0, 8) || '—'}
                    </td>
                    <td className="px-4 py-3 text-[#2B2926]/70">
                      <div title={absoluteTime(c.started_at)}>{relativeTime(c.started_at)}</div>
                    </td>
                    <td className="px-4 py-3 text-[#2B2926]/70">{fmtDuration(c.duration_sec)}</td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-bold ${badge}`}>
                        {prettyOutcome(c.outcome)}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      {hasTranscript ? (
                        <span className="inline-flex items-center gap-1 text-[11px] font-bold text-[#10B981]">
                          <FileText className="w-3.5 h-3.5" />
                          available
                        </span>
                      ) : (
                        <span className="text-[11px] text-[#2B2926]/40">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {showInitiate && (
        <InitiateModal
          authAxios={authAxios}
          setMessage={setMessage}
          onClose={() => setShowInitiate(false)}
          onInitiated={() => { setShowInitiate(false); fetchCalls(); }}
        />
      )}
    </div>
  );
};

// ─────────────────────────────────────────────────────────────────────────────
// Call detail
// ─────────────────────────────────────────────────────────────────────────────

function CallDetail({ authAxios, callId, onBack }) {
  const [call, setCall] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError('');
      try {
        const res = await authAxios.get(`/nexus/voice/${callId}`);
        if (!cancelled) setCall(res.data);
      } catch (err) {
        if (!cancelled) {
          setError(err?.response?.data?.detail || err.message || 'Failed to load call');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [authAxios, callId]);

  return (
    <div className="p-8 max-w-4xl mx-auto">
      <button
        type="button"
        onClick={onBack}
        className="inline-flex items-center gap-2 text-xs font-bold text-[#2B2926]/60 hover:text-[#2B2926] mb-4"
      >
        <ArrowLeft className="w-4 h-4" />
        Back to calls
      </button>

      {loading && (
        <div className="bg-white border border-[#2B2926]/10 rounded-2xl p-12 flex items-center justify-center">
          <Loader2 className="w-5 h-5 animate-spin text-[#F55600]" />
          <span className="ml-3 text-sm text-[#2B2926]/60">Loading call…</span>
        </div>
      )}

      {!loading && error && (
        <div className="bg-white border border-[#F55600]/30 rounded-2xl p-6 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-[#F55600] mt-0.5" />
          <div className="flex-1">
            <p className="text-sm font-bold text-[#2B2926]">Couldn't load call</p>
            <p className="text-xs text-[#2B2926]/60 mt-1">{error}</p>
          </div>
        </div>
      )}

      {!loading && !error && call && (
        <>
          {/* Top summary */}
          <div className="bg-white border border-[#2B2926]/10 rounded-2xl p-6 mb-4">
            <div className="flex items-start justify-between gap-4 flex-wrap">
              <div>
                <h2 className="text-xl font-black text-[#2B2926] flex items-center gap-2">
                  <PhoneCall className="w-5 h-5 text-[#F55600]" />
                  {call.lead_email || call.lead?.email || `Lead ${String(call.lead_id || '').slice(0, 8)}`}
                </h2>
                <p className="text-xs text-[#2B2926]/40 mt-1">
                  call_sid <span className="font-mono">{call.twilio_call_sid || '—'}</span>
                </p>
              </div>
              <div className="flex items-center gap-3 flex-wrap">
                {call.outcome && (
                  <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-bold ${OUTCOME_BADGE[call.outcome] || 'text-[#2B2926]/40 bg-[#2B2926]/5'}`}>
                    {prettyOutcome(call.outcome)}
                  </span>
                )}
                <span className="text-xs text-[#2B2926]/60">
                  Duration <span className="font-bold text-[#2B2926]">{fmtDuration(call.duration_sec)}</span>
                </span>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-4 text-xs">
              <div>
                <p className="text-[#2B2926]/40 font-bold uppercase tracking-wide text-[10px] mb-1">Started</p>
                <p className="text-[#2B2926]/70">{absoluteTime(call.started_at)}</p>
              </div>
              <div>
                <p className="text-[#2B2926]/40 font-bold uppercase tracking-wide text-[10px] mb-1">Ended</p>
                <p className="text-[#2B2926]/70">{absoluteTime(call.ended_at)}</p>
              </div>
            </div>

            {call.error && (
              <div className="mt-4 px-3 py-2 rounded-lg bg-[#F55600]/10 text-[#F55600] text-xs font-bold">
                Error: {call.error}
              </div>
            )}
          </div>

          {/* Recording */}
          {call.recording_url && (
            <div className="bg-white border border-[#2B2926]/10 rounded-2xl p-6 mb-4">
              <h3 className="text-sm font-bold text-[#2B2926] mb-3 flex items-center gap-2">
                <Mic className="w-4 h-4 text-[#F55600]" />
                Recording
              </h3>
              {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
              <audio controls src={call.recording_url} className="w-full" />
            </div>
          )}

          {/* Transcript */}
          <div className="bg-white border border-[#2B2926]/10 rounded-2xl p-6">
            <h3 className="text-sm font-bold text-[#2B2926] mb-3 flex items-center gap-2">
              <FileText className="w-4 h-4 text-[#F55600]" />
              Transcript
            </h3>
            {call.transcript && String(call.transcript).trim().length > 0 ? (
              <pre className="whitespace-pre-wrap text-[#2B2926]/80 text-sm leading-relaxed font-sans">
                {call.transcript}
              </pre>
            ) : (
              <p className="text-xs text-[#2B2926]/40">No transcript available for this call.</p>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Initiate-call modal
// ─────────────────────────────────────────────────────────────────────────────

function InitiateModal({ authAxios, setMessage, onClose, onInitiated }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [selectedLead, setSelectedLead] = useState(null);
  const [script, setScript] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState('');
  const searchTimer = useRef(null);

  // Typeahead search /nexus/leads with a 250ms debounce so we don't hammer
  // the backend on every keystroke.
  useEffect(() => {
    if (selectedLead) return; // pause search while a lead is locked in
    clearTimeout(searchTimer.current);
    if (!query || query.trim().length < 2) {
      setResults([]);
      return;
    }
    searchTimer.current = setTimeout(async () => {
      setSearching(true);
      try {
        const res = await authAxios.get(`/nexus/leads?search=${encodeURIComponent(query.trim())}&limit=10`);
        const data = Array.isArray(res.data) ? res.data : (res.data?.leads || []);
        setResults(data);
      } catch {
        setResults([]);
      } finally {
        setSearching(false);
      }
    }, 250);
    return () => clearTimeout(searchTimer.current);
  }, [query, authAxios, selectedLead]);

  async function handleSubmit(e) {
    e?.preventDefault?.();
    setFormError('');
    if (!selectedLead) return setFormError('Pick a lead first');
    if (!script.trim()) return setFormError('Add a script the bot should follow');
    setSubmitting(true);
    try {
      await authAxios.post('/nexus/voice/initiate', {
        lead_id: selectedLead.id,
        script: script.trim(),
      });
      setMessage('Calling — check back in a few minutes');
      onInitiated();
    } catch (err) {
      setFormError(err?.response?.data?.detail || err.message || 'Failed to initiate call');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-[#2B2926]/40"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl border border-[#2B2926]/10 w-full max-w-lg p-6 relative"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          onClick={onClose}
          className="absolute top-4 right-4 p-1.5 rounded-lg hover:bg-[#2B2926]/5 text-[#2B2926]/60"
        >
          <X className="w-4 h-4" />
        </button>

        <h2 className="text-xl font-black text-[#2B2926] mb-1 flex items-center gap-2">
          <PhoneCall className="w-5 h-5 text-[#F55600]" />
          Initiate call
        </h2>
        <p className="text-sm text-[#2B2926]/60 mb-5">
          Pick a lead and a script — the bot dials and follows your prompt.
        </p>

        {formError && (
          <div className="mb-4 px-3 py-2 rounded-lg bg-[#F55600]/10 text-[#F55600] text-xs font-bold">
            {formError}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Lead picker */}
          <div>
            <span className="block text-xs font-bold text-[#2B2926]/70 mb-1">Lead</span>
            {selectedLead ? (
              <div className="flex items-center justify-between gap-2 px-3 py-2 rounded-lg border border-[#F55600]/30 bg-[#F55600]/5">
                <div className="text-sm text-[#2B2926] truncate">
                  <span className="font-bold">{selectedLead.name || selectedLead.email || selectedLead.id}</span>
                  {selectedLead.email && (
                    <span className="text-[#2B2926]/60 ml-2">{selectedLead.email}</span>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => { setSelectedLead(null); setQuery(''); setResults([]); }}
                  className="text-xs font-bold text-[#F55600] hover:underline"
                >
                  Change
                </button>
              </div>
            ) : (
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#2B2926]/40" />
                <input
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search leads by name, email, or company…"
                  className="w-full pl-9 pr-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600]"
                />
                {(searching || results.length > 0) && (
                  <div className="absolute z-10 top-full left-0 right-0 mt-1 max-h-56 overflow-auto bg-white border border-[#2B2926]/10 rounded-lg shadow-sm">
                    {searching && (
                      <div className="px-3 py-2 text-xs text-[#2B2926]/40 flex items-center gap-2">
                        <Loader2 className="w-3 h-3 animate-spin" />
                        Searching…
                      </div>
                    )}
                    {!searching && results.length === 0 && query.trim().length >= 2 && (
                      <div className="px-3 py-2 text-xs text-[#2B2926]/40">No leads matched.</div>
                    )}
                    {!searching && results.map((l) => (
                      <button
                        key={l.id}
                        type="button"
                        onClick={() => { setSelectedLead(l); setResults([]); }}
                        className="block w-full text-left px-3 py-2 text-sm hover:bg-[#F55600]/5"
                      >
                        <div className="font-bold text-[#2B2926]">{l.name || l.email || l.id}</div>
                        {l.email && <div className="text-xs text-[#2B2926]/60">{l.email}</div>}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Script */}
          <label className="block">
            <span className="block text-xs font-bold text-[#2B2926]/70 mb-1">Script</span>
            <textarea
              value={script}
              onChange={(e) => setScript(e.target.value)}
              rows={6}
              placeholder="Hi {{lead.name}}, this is a quick call about…"
              className="w-full px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600] resize-y"
            />
            <span className="block mt-1 text-[11px] text-[#2B2926]/40">
              The bot reads this aloud and improvises follow-ups. Keep it short and natural.
            </span>
          </label>

          <div className="flex items-center justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="px-4 py-2 rounded-lg text-sm font-bold text-[#2B2926]/60 hover:bg-[#2B2926]/5 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold bg-[#F55600] text-white hover:bg-[#F55600]/90 disabled:opacity-50"
            >
              {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
              <PhoneCall className="w-4 h-4" />
              Initiate Call
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default NexusVoice;
