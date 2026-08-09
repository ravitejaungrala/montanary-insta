/**
 * NexusIntegrations — connect external APIs (Apollo, Hunter, Resend, Gmail, etc.).
 *
 * Legacy label: "Integrations". Each tile shows connection status, key
 * configuration, and a "Connect"/"Configure" button.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertCircle,
  CheckCircle2,
  ExternalLink,
  Loader2,
  Plug,
  RefreshCw,
  Settings as Cog,
} from 'lucide-react';

// Each integration: id, label, description, config_keys (env vars), docs link.
const INTEGRATIONS = [
  {
    id: 'hunter',
    label: 'Hunter.io',
    description: 'Email finder + verifier',
    keys: ['HUNTER_API_KEY'],
    docs: 'https://hunter.io/api-documentation',
  },
  {
    id: 'resend',
    label: 'Resend',
    description: 'Transactional email sending + inbound webhooks',
    keys: ['RESEND_API_KEY', 'RESEND_WEBHOOK_SECRET'],
    docs: 'https://resend.com/docs',
  },
  {
    id: 'gmail',
    label: 'Gmail',
    description: 'OAuth2 mailbox polling for inbound replies',
    keys: ['(OAuth flow)'],
    docs: 'https://developers.google.com/gmail/api',
  },
  {
    id: 'twilio',
    label: 'Twilio',
    description: 'Outbound voice calls + SMS',
    keys: ['TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN', 'TWILIO_FROM_NUMBER'],
    docs: 'https://www.twilio.com/docs',
  },
  {
    id: 'deepgram',
    label: 'Deepgram',
    description: 'Voice-call transcription',
    keys: ['DEEPGRAM_API_KEY'],
    docs: 'https://developers.deepgram.com',
  },
  {
    id: 'calendly',
    label: 'Calendly',
    description: 'Demo booking webhooks',
    keys: ['CALENDLY_SIGNING_KEY'],
    docs: 'https://developer.calendly.com',
  },
  {
    id: 'ms_bookings',
    label: 'Microsoft Bookings',
    description: 'MS-side demo booking integration',
    keys: ['MS_BOOKINGS_TENANT_ID', 'MS_BOOKINGS_CLIENT_ID', 'MS_BOOKINGS_CLIENT_SECRET'],
    docs: 'https://learn.microsoft.com/en-us/graph/bookings-concept-overview',
  },
  {
    id: 'github',
    label: 'GitHub',
    description: 'Public-signal discovery (repo activity)',
    keys: ['GITHUB_TOKEN'],
    docs: 'https://docs.github.com/en/rest',
  },
  {
    id: 'reddit',
    label: 'Reddit',
    description: 'Public-signal discovery (pain-point detection)',
    keys: ['REDDIT_CLIENT_ID', 'REDDIT_CLIENT_SECRET'],
    docs: 'https://www.reddit.com/dev/api',
  },
  {
    id: 'nvidia',
    label: 'Nvidia NIM',
    description: 'Hosted Qwen / Llama LLM inference',
    keys: ['NVIDIA_NIM_API_KEY'],
    docs: 'https://build.nvidia.com',
  },
];

const NexusIntegrations = ({ authAxios, apiBase, user, setMessage }) => {
  const [statuses, setStatuses] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const loadStatuses = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await authAxios
        .get(`${apiBase}/system/integrations`)
        .catch(() => ({ data: { statuses: {} } }));
      setStatuses(res.data?.statuses || {});
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  }, [authAxios, apiBase]);

  useEffect(() => {
    loadStatuses();
  }, [loadStatuses]);

  return (
    <div className="bg-white">
      <div className="px-5 py-4 border-b border-[#2B2926]/10 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-black text-[#2B2926] tracking-tight">Integrations</h1>
          <p className="text-xs text-[#2B2926]/60 mt-0.5">
            Connect external APIs that power discovery, enrichment, and outreach.
          </p>
        </div>
        <button
          type="button"
          onClick={loadStatuses}
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

      <div className="p-5 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {INTEGRATIONS.map((it) => {
          const status = statuses[it.id] || {};
          const connected = !!status.connected;
          return (
            <div
              key={it.id}
              className={[
                'border rounded-lg p-3 transition-all',
                connected
                  ? 'border-[#10B981]/30 bg-[#10B981]/5'
                  : 'border-[#2B2926]/10 hover:bg-[#F55600]/5',
              ].join(' ')}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <div
                    className={[
                      'w-8 h-8 rounded-lg flex items-center justify-center shrink-0',
                      connected ? 'bg-[#10B981]/15 text-[#10B981]' : 'bg-[#F55600]/10 text-[#F55600]',
                    ].join(' ')}
                  >
                    <Plug className="w-4 h-4" />
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-bold text-[#2B2926] truncate">{it.label}</div>
                    <div className="text-[10px] text-[#2B2926]/60 truncate">{it.description}</div>
                  </div>
                </div>
                {connected && <CheckCircle2 className="w-4 h-4 text-[#10B981] shrink-0" />}
              </div>

              <div className="mt-3 pt-3 border-t border-[#2B2926]/10">
                <div className="text-[9px] uppercase tracking-wider text-[#2B2926]/40 font-bold mb-1">
                  Configure via env vars
                </div>
                <div className="space-y-1">
                  {it.keys.map((k) => (
                    <div
                      key={k}
                      className="text-[10px] font-mono text-[#2B2926]/70 bg-[#2B2926]/[0.02] rounded px-1.5 py-0.5"
                    >
                      {k}
                    </div>
                  ))}
                </div>
                <a
                  href={it.docs}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-2 inline-flex items-center gap-1 text-[10px] font-bold text-[#F55600] hover:opacity-70"
                >
                  Docs
                  <ExternalLink className="w-2.5 h-2.5" />
                </a>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default NexusIntegrations;
