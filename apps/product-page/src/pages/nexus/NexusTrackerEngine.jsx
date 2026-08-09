/**
 * NexusTrackerEngine — open / click / bounce engagement stats.
 *
 * Legacy label: "Tracker Engine" (under GTM Journey).
 * Backend: /nexus/tracker/* + tracking-pixel events on nexus_touchpoints.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertCircle,
  Eye,
  Loader2,
  MailCheck,
  MailOpen,
  MailX,
  MousePointerClick,
  RefreshCw,
} from 'lucide-react';

const StatCard = ({ icon: Icon, label, value, accent }) => (
  <div className="border border-[#2B2926]/10 rounded-lg p-4">
    <div className="flex items-center justify-between">
      <div>
        <div className="text-[10px] uppercase tracking-wider text-[#2B2926]/40 font-bold">{label}</div>
        <div className="text-2xl font-black text-[#2B2926] mt-1">{value}</div>
      </div>
      <div
        className={[
          'w-9 h-9 rounded-lg flex items-center justify-center',
          accent === 'green'
            ? 'bg-[#10B981]/10 text-[#10B981]'
            : accent === 'red'
            ? 'bg-[#F55600]/15 text-[#F55600]'
            : 'bg-[#F55600]/10 text-[#F55600]',
        ].join(' ')}
      >
        <Icon className="w-4 h-4" />
      </div>
    </div>
  </div>
);

const NexusTrackerEngine = ({ authAxios, apiBase, user, setMessage }) => {
  const [stats, setStats] = useState({
    emails_sent: 0,
    opens: 0,
    clicks: 0,
    bounces: 0,
    open_rate: 0,
    click_rate: 0,
    bounce_rate: 0,
  });
  const [recentEvents, setRecentEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const loadStats = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      // Try the legacy tracker endpoint; fall back to derived analytics.
      const res = await authAxios.get(`${apiBase}/tracker/stats`).catch(() =>
        authAxios.get(`${apiBase}/analytics/summary`),
      );
      const d = res.data || {};
      setStats({
        emails_sent: d.emails_sent || d.sent || 0,
        opens: d.opens || 0,
        clicks: d.clicks || 0,
        bounces: d.bounces || 0,
        open_rate: d.open_rate || 0,
        click_rate: d.click_rate || 0,
        bounce_rate: d.bounce_rate || 0,
      });
      setRecentEvents(d.recent_events || []);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  }, [authAxios, apiBase]);

  useEffect(() => {
    loadStats();
  }, [loadStats]);

  const formatPct = (v) => `${Math.round((v || 0) * 1000) / 10}%`;

  return (
    <div className="bg-white">
      <div className="px-5 py-4 border-b border-[#2B2926]/10 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-black text-[#2B2926] tracking-tight">Tracker Engine</h1>
          <p className="text-xs text-[#2B2926]/60 mt-0.5">
            Email opens, link clicks, bounces — workspace-wide.
          </p>
        </div>
        <button
          type="button"
          onClick={loadStats}
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

      <div className="p-5 space-y-4">
        {/* KPIs */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard icon={MailCheck} label="Emails Sent" value={stats.emails_sent} />
          <StatCard icon={MailOpen} label="Opens" value={`${stats.opens} (${formatPct(stats.open_rate)})`} accent="green" />
          <StatCard
            icon={MousePointerClick}
            label="Clicks"
            value={`${stats.clicks} (${formatPct(stats.click_rate)})`}
            accent="green"
          />
          <StatCard icon={MailX} label="Bounces" value={`${stats.bounces} (${formatPct(stats.bounce_rate)})`} accent="red" />
        </div>

        {/* Recent events */}
        <div className="border border-[#2B2926]/10 rounded-lg overflow-hidden">
          <div className="px-3 py-2 border-b border-[#2B2926]/10 inline-flex items-center gap-1.5 text-xs font-bold text-[#2B2926]">
            <Activity className="w-3.5 h-3.5 text-[#F55600]" />
            Recent tracking events
          </div>
          {loading ? (
            <div className="p-4 flex items-center gap-2 text-xs text-[#2B2926]/60">
              <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading…
            </div>
          ) : recentEvents.length === 0 ? (
            <div className="p-6 text-center text-xs text-[#2B2926]/40">
              <Eye className="w-6 h-6 mx-auto mb-1 text-[#2B2926]/15" />
              No events yet. Sent emails will appear here once they're opened or clicked.
            </div>
          ) : (
            <div className="divide-y divide-black/5">
              {recentEvents.slice(0, 20).map((ev, i) => (
                <div key={i} className="px-3 py-2 text-xs text-[#2B2926]/70 flex items-center gap-2">
                  <span className="font-bold text-[#2B2926]">{ev.type}</span>
                  <span className="text-[#2B2926]/60">{ev.lead_email}</span>
                  <span className="ml-auto text-[10px] text-[#2B2926]/40">{ev.occurred_at}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default NexusTrackerEngine;
