/**
 * NexusBilling — workspace plan + usage view (legacy label: "Billing").
 *
 * Backend: /nexus/billing/{usage,plan,upgrade}. Note: PIPELYT owns the
 * actual upgrade flow (/payment/*) — this page mostly reads usage and
 * redirects users to PIPELYT for changes.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertCircle,
  ArrowUpCircle,
  CheckCircle2,
  CreditCard,
  Loader2,
  RefreshCw,
} from 'lucide-react';

const UsageBar = ({ label, current, limit }) => {
  const isUnlimited = limit === -1 || limit === Infinity;
  const pct = isUnlimited ? 0 : Math.min(100, Math.round(((current || 0) / Math.max(1, limit)) * 100));
  const nearLimit = pct >= 80;
  return (
    <div>
      <div className="flex items-center justify-between text-[11px] mb-1">
        <span className="font-bold text-[#2B2926]">{label}</span>
        <span className={nearLimit ? 'text-[#F55600] font-bold' : 'text-[#2B2926]/60'}>
          {(current || 0).toLocaleString()} {isUnlimited ? '· unlimited' : `/ ${(limit || 0).toLocaleString()}`}
        </span>
      </div>
      {!isUnlimited && (
        <div className="h-1.5 rounded-full bg-[#2B2926]/5 overflow-hidden">
          <div
            className={['h-full rounded-full', nearLimit ? 'bg-[#F55600]' : 'bg-[#10B981]'].join(' ')}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
};

const NexusBilling = ({ authAxios, apiBase, user, setMessage }) => {
  const [usage, setUsage] = useState({});
  const [plan, setPlan] = useState('starter');
  const [limits, setLimits] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await authAxios.get(`${apiBase}/billing/usage`);
      const d = res.data || {};
      setPlan(d.plan || 'starter');
      setUsage(d.usage || {});
      setLimits(d.limits || {});
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  }, [authAxios, apiBase]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const handleUpgrade = () => {
    window.location.href = '/payment';
  };

  return (
    <div className="bg-white">
      <div className="px-5 py-4 border-b border-[#2B2926]/10 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-black text-[#2B2926] tracking-tight">Billing</h1>
          <p className="text-xs text-[#2B2926]/60 mt-0.5">
            Plan + monthly usage. Upgrade via PIPELYT billing.
          </p>
        </div>
        <button
          type="button"
          onClick={loadAll}
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

      <div className="p-5 max-w-3xl space-y-5">
        {/* Current plan */}
        <div className="border border-[#2B2926]/10 rounded-lg p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-[10px] uppercase tracking-wider text-[#2B2926]/40 font-bold">Current plan</div>
              <div className="text-2xl font-black text-[#2B2926] mt-1 inline-flex items-center gap-2">
                {plan.charAt(0).toUpperCase() + plan.slice(1)}
                {plan !== 'free' && <CheckCircle2 className="w-5 h-5 text-[#10B981]" />}
              </div>
            </div>
            <button
              type="button"
              onClick={handleUpgrade}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#F55600] text-white text-xs font-bold hover:opacity-90"
            >
              <ArrowUpCircle className="w-3.5 h-3.5" />
              Manage / upgrade
            </button>
          </div>
        </div>

        {/* Usage */}
        <div className="border border-[#2B2926]/10 rounded-lg p-4">
          <h3 className="text-xs font-bold text-[#2B2926] mb-3 uppercase tracking-wider inline-flex items-center gap-1.5">
            <CreditCard className="w-3.5 h-3.5 text-[#F55600]" />
            This billing period
          </h3>
          {loading ? (
            <div className="flex items-center gap-2 text-xs text-[#2B2926]/60">
              <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading…
            </div>
          ) : (
            <div className="space-y-3">
              <UsageBar
                label="Campaigns"
                current={usage.campaigns_active}
                limit={limits.campaigns}
              />
              <UsageBar
                label="Leads this month"
                current={usage.leads_this_month}
                limit={limits.leads_per_month}
              />
              <UsageBar
                label="Emails this month"
                current={usage.emails_this_month}
                limit={limits.emails_per_month}
              />
              <UsageBar
                label="LLM tokens this month"
                current={usage.tokens_this_month}
                limit={limits.tokens_per_month}
              />
              <UsageBar
                label="Team members"
                current={usage.team_members}
                limit={limits.team_members}
              />
              <UsageBar
                label="Mailboxes"
                current={usage.mailboxes}
                limit={limits.mailboxes}
              />
            </div>
          )}
        </div>

        <p className="text-[10px] text-[#2B2926]/40 text-center">
          NEXUS billing is managed by PIPELYT. Click <span className="font-bold">Manage / upgrade</span> above to change plans.
        </p>
      </div>
    </div>
  );
};

export default NexusBilling;
