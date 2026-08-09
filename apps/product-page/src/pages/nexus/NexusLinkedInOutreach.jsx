/**
 * NexusLinkedInOutreach — LinkedIn DM queue + status board.
 *
 * Legacy label: "LinkedIn Outreach" (under GTM Journey).
 * Backend: /nexus/linkedin/* + nexus_linkedin_messages table.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  Copy,
  ExternalLink,
  Linkedin,
  Loader2,
  RefreshCw,
} from 'lucide-react';

const STATUS_PILL = {
  not_generated: 'bg-[#2B2926]/5 text-[#2B2926]/40',
  generated: 'bg-[#F55600]/10 text-[#F55600]',
  pending: 'bg-[#F55600]/10 text-[#F55600]',
  sent: 'bg-[#10B981]/15 text-[#10B981]',
  replied: 'bg-[#10B981]/15 text-[#10B981]',
  failed: 'bg-[#F55600]/15 text-[#F55600]',
};

const NexusLinkedInOutreach = ({ authAxios, apiBase, user, setMessage }) => {
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState('all');

  const loadMessages = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await authAxios.get(`${apiBase}/linkedin`);
      setMessages(res.data?.messages || res.data || []);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  }, [authAxios, apiBase]);

  useEffect(() => {
    loadMessages();
  }, [loadMessages]);

  const filtered = useMemo(() => {
    if (filter === 'all') return messages;
    return messages.filter((m) => (m.intent || m.status) === filter);
  }, [messages, filter]);

  const copyToClipboard = async (text) => {
    try {
      await navigator.clipboard.writeText(text || '');
      if (setMessage) setMessage({ type: 'success', text: 'Copied to clipboard' });
    } catch {
      /* clipboard blocked */
    }
  };

  return (
    <div className="bg-white">
      <div className="px-5 py-4 border-b border-[#2B2926]/10 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-black text-[#2B2926] tracking-tight">LinkedIn Outreach</h1>
          <p className="text-xs text-[#2B2926]/60 mt-0.5">
            DMs generated for your leads — copy + paste into LinkedIn to send.
          </p>
        </div>
        <button
          type="button"
          onClick={loadMessages}
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

      <div className="px-5 py-3 border-b border-[#2B2926]/10 flex items-center gap-1 flex-wrap">
        {['all', 'generated', 'pending', 'sent', 'replied', 'failed'].map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => setFilter(f)}
            className={[
              'px-2.5 py-1 rounded-lg text-[10px] font-bold transition-all',
              filter === f
                ? 'bg-[#F55600] text-white'
                : 'bg-white text-[#2B2926]/60 border border-[#2B2926]/10 hover:bg-[#F55600]/5',
            ].join(' ')}
          >
            {f === 'all' ? 'All' : f.replace('_', ' ')}
          </button>
        ))}
      </div>

      <div className="p-5">
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-[#2B2926]/60">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading messages…
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-12">
            <Linkedin className="w-10 h-10 mx-auto text-[#2B2926]/15 mb-2" />
            <p className="text-xs text-[#2B2926]/60">No LinkedIn messages yet.</p>
          </div>
        ) : (
          <div className="space-y-3 max-w-4xl">
            {filtered.map((m) => {
              const st = m.intent || m.status || 'not_generated';
              return (
                <div key={m.id || m._id} className="border border-[#2B2926]/10 rounded-lg p-3">
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <Linkedin className="w-3.5 h-3.5 text-[#2B2926] shrink-0" />
                      <span className="text-xs font-bold text-[#2B2926] truncate">
                        Lead #{m.lead_id || m._id}
                      </span>
                      <span
                        className={[
                          'inline-flex px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider',
                          STATUS_PILL[st] || 'bg-[#2B2926]/5 text-[#2B2926]/60',
                        ].join(' ')}
                      >
                        {st.replace('_', ' ')}
                      </span>
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      <button
                        type="button"
                        onClick={() => copyToClipboard(m.body)}
                        className="inline-flex items-center gap-1 px-2 py-1 rounded border border-[#2B2926]/10 text-[10px] font-bold text-[#2B2926] hover:bg-[#F55600]/5"
                      >
                        <Copy className="w-3 h-3" />
                        Copy
                      </button>
                      {m.linkedin_message_urn && (
                        <a
                          href={`https://www.linkedin.com/messaging/thread/${m.linkedin_message_urn}/`}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 px-2 py-1 rounded border border-[#2B2926]/10 text-[10px] font-bold text-[#2B2926] hover:bg-[#F55600]/5"
                        >
                          <ExternalLink className="w-3 h-3" />
                          Open
                        </a>
                      )}
                    </div>
                  </div>
                  {m.body && (
                    <div className="text-[11px] text-[#2B2926]/70 leading-relaxed whitespace-pre-wrap bg-[#2B2926]/[0.02] rounded p-2 border border-[#2B2926]/5">
                      {m.body}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

export default NexusLinkedInOutreach;
