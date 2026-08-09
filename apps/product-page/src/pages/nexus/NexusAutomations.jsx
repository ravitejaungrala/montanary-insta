import React, { useEffect, useState, useCallback, useMemo } from 'react';
import {
  Zap,
  Plus,
  Pencil,
  Trash2,
  PlayCircle,
  PauseCircle,
  Loader2,
  AlertCircle,
  Calendar as CalendarIcon,
  ArrowLeft,
} from 'lucide-react';

/**
 * NexusAutomations
 *
 * List / create / edit recurring lead-discovery + sequence-enrollment runs.
 * Talks to /nexus/automations and /nexus/campaigns. Manual-trigger button
 * (POST /nexus/scheduler/automation/tick) is hidden unless the current user
 * is flagged as admin — the endpoint is guarded by X-Scheduler-Secret and
 * non-admins would just get a 403.
 *
 * Palette: PIPELYT mandatory four — #F55600, #10B981, black, white.
 */

// Common IANA timezones — keep the list short and hand-picked so users can
// scan quickly. Anything more exotic, they'll have to switch in Profile.
const TIMEZONES = [
  'UTC',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'America/Sao_Paulo',
  'Europe/London',
  'Europe/Paris',
  'Europe/Berlin',
  'Europe/Madrid',
  'Asia/Dubai',
  'Asia/Kolkata',
  'Asia/Singapore',
  'Asia/Tokyo',
  'Asia/Shanghai',
  'Australia/Sydney',
];

const STATUS_BADGE = {
  active:    'text-[#10B981] bg-[#10B981]/10',
  paused:    'text-[#2B2926]/60 bg-[#2B2926]/5',
  completed: 'text-[#2B2926]/60 bg-[#2B2926]/5',
  error:     'text-[#F55600] bg-[#F55600]/10',
};

function relativeTime(iso) {
  if (!iso) return '—';
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return '—';
  const diff = t - Date.now();
  const abs = Math.abs(diff);
  const sec = Math.round(abs / 1000);
  const min = Math.round(sec / 60);
  const hr  = Math.round(min / 60);
  const day = Math.round(hr  / 24);
  let label;
  if (sec < 60)     label = `${sec}s`;
  else if (min < 60) label = `${min}m`;
  else if (hr  < 24) label = `${hr}h`;
  else               label = `${day}d`;
  return diff < 0 ? `${label} ago` : `in ${label}`;
}

function absoluteTime(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

const NexusAutomations = ({ authAxios, setMessage = () => {}, user }) => {
  const [mode, setMode] = useState('list'); // 'list' | 'create' | 'edit'
  const [editing, setEditing] = useState(null);

  const [automations, setAutomations] = useState([]);
  const [campaigns, setCampaigns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [triggering, setTriggering] = useState(false);

  const isAdmin = !!(user && (user.is_admin || user.role === 'admin' || user.is_superuser));

  const fetchAutomations = useCallback(async () => {
    setError('');
    setLoading(true);
    try {
      const res = await authAxios.get('/nexus/automations');
      const data = Array.isArray(res.data) ? res.data : (res.data?.automations || []);
      setAutomations(data);
    } catch (err) {
      if (err?.response?.status === 404) {
        setAutomations([]);
      } else {
        setError(err?.response?.data?.detail || err.message || 'Failed to load automations');
      }
    } finally {
      setLoading(false);
    }
  }, [authAxios]);

  const fetchCampaigns = useCallback(async () => {
    try {
      const res = await authAxios.get('/nexus/campaigns');
      const data = Array.isArray(res.data) ? res.data : (res.data?.campaigns || []);
      setCampaigns(data);
    } catch {
      // Campaigns dropdown is best-effort — if it fails, user can still
      // see the list, they just can't create new automations until
      // /nexus/campaigns is available.
      setCampaigns([]);
    }
  }, [authAxios]);

  useEffect(() => {
    fetchAutomations();
    fetchCampaigns();
  }, [fetchAutomations, fetchCampaigns]);

  const campaignsById = useMemo(() => {
    const m = {};
    campaigns.forEach((c) => { m[c.id] = c; });
    return m;
  }, [campaigns]);

  async function handleDelete(a) {
    if (!window.confirm(`Delete automation "${a.name}"? This cannot be undone.`)) return;
    try {
      await authAxios.delete(`/nexus/automations/${a.id}`);
      setMessage('Automation deleted');
      fetchAutomations();
    } catch (err) {
      setMessage(`Delete failed: ${err?.response?.data?.detail || err.message}`);
    }
  }

  async function handleTogglePause(a) {
    const nextStatus = a.status === 'active' ? 'paused' : 'active';
    try {
      await authAxios.patch(`/nexus/automations/${a.id}`, { status: nextStatus });
      setMessage(nextStatus === 'paused' ? 'Automation paused' : 'Automation resumed');
      fetchAutomations();
    } catch (err) {
      setMessage(`Failed: ${err?.response?.data?.detail || err.message}`);
    }
  }

  async function handleTriggerTick() {
    setTriggering(true);
    try {
      await authAxios.post('/nexus/scheduler/automation/tick');
      setMessage('Scheduler tick triggered');
      fetchAutomations();
    } catch (err) {
      setMessage(`Trigger failed: ${err?.response?.data?.detail || err.message}`);
    } finally {
      setTriggering(false);
    }
  }

  if (mode === 'create' || mode === 'edit') {
    return (
      <AutomationForm
        authAxios={authAxios}
        setMessage={setMessage}
        campaigns={campaigns}
        initial={mode === 'edit' ? editing : null}
        onCancel={() => { setMode('list'); setEditing(null); }}
        onSaved={() => { setMode('list'); setEditing(null); fetchAutomations(); }}
      />
    );
  }

  // Top-level tab: 'schedule' (existing automations table) or 'voice' (Voice & Cadence settings).
  const [topTab, setTopTab] = React.useState('schedule');

  return (
    <div className="p-8 max-w-6xl mx-auto">
      {/* Header — legacy: "Automation Manager" */}
      <div className="flex items-center justify-between mb-4 gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-black text-[#2B2926] flex items-center gap-2">
            <Zap className="w-6 h-6 text-[#F55600]" />
            Automation Manager
          </h1>
          <p className="text-sm text-[#2B2926]/60 mt-1">
            Tell the engine when to generate leads, what voice to use, and how to follow up.
            Everything you set here runs without you having to touch it again.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {isAdmin && (
            <button
              type="button"
              onClick={handleTriggerTick}
              disabled={triggering}
              className="inline-flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-bold border border-[#2B2926]/10 text-[#2B2926]/60 hover:bg-[#2B2926]/5 disabled:opacity-50"
            >
              {triggering ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <PlayCircle className="w-3.5 h-3.5" />}
              Trigger tick
            </button>
          )}
          <button
            type="button"
            onClick={() => { setEditing(null); setMode('create'); }}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold bg-[#F55600] text-white hover:bg-[#F55600]/90"
          >
            <Plus className="w-4 h-4" />
            New Automation
          </button>
        </div>
      </div>

      {/* Top tabs: Schedule | Voice & Cadence */}
      <div className="inline-flex items-center rounded-full border border-[#2B2926]/10 overflow-hidden mb-5 bg-white">
        <button
          type="button"
          onClick={() => setTopTab('schedule')}
          className={[
            'inline-flex items-center gap-2 px-4 py-1.5 text-xs font-bold transition-all',
            topTab === 'schedule'
              ? 'bg-[#2B2926] text-white'
              : 'text-[#2B2926]/60 hover:bg-[#F55600]/5',
          ].join(' ')}
        >
          Schedule
          <span
            className={[
              'inline-flex items-center justify-center w-5 h-5 rounded-full text-[10px]',
              topTab === 'schedule' ? 'bg-[#F55600] text-white' : 'bg-[#F55600] text-white',
            ].join(' ')}
          >
            {automations?.length || 0}
          </span>
        </button>
        <button
          type="button"
          onClick={() => setTopTab('voice')}
          className={[
            'inline-flex items-center gap-2 px-4 py-1.5 text-xs font-bold transition-all',
            topTab === 'voice'
              ? 'bg-[#2B2926] text-white'
              : 'text-[#2B2926]/60 hover:bg-[#F55600]/5',
          ].join(' ')}
        >
          Voice & Cadence
        </button>
      </div>

      {/* Schedule new automation CTA — only on schedule tab */}
      {topTab === 'schedule' && (
        <div className="flex justify-end mb-3">
          <button
            type="button"
            onClick={() => { setEditing(null); setMode('create'); }}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-full text-xs font-bold bg-[#F55600] text-white hover:opacity-90 shadow-sm"
          >
            <Plus className="w-3.5 h-3.5" />
            Schedule new automation
          </button>
        </div>
      )}

      {/* Voice & Cadence tab body */}
      {topTab === 'voice' && (
        <div className="bg-white border border-[#2B2926]/10 rounded-2xl p-6 space-y-4">
          <h2 className="text-base font-bold text-[#2B2926]">Voice & Cadence</h2>
          <p className="text-sm text-[#2B2926]/60">
            Voice tone, follow-up cadence, and engine-level outreach settings.
            Backed by <code className="bg-[#2B2926]/[0.05] px-1 rounded">nexus_settings</code> →
            workspace config. Operational logic is being wired by the team.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
            <div className="border border-[#2B2926]/10 rounded-lg p-4">
              <div className="text-[10px] uppercase tracking-wider text-[#2B2926]/40 font-bold">Voice tone</div>
              <select
                disabled
                className="mt-2 w-full px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm disabled:bg-[#2B2926]/[0.02]"
              >
                <option>Founder-mode (high-touch)</option>
                <option>Consultative</option>
                <option>Direct + concise</option>
              </select>
            </div>
            <div className="border border-[#2B2926]/10 rounded-lg p-4">
              <div className="text-[10px] uppercase tracking-wider text-[#2B2926]/40 font-bold">
                Default follow-up cadence
              </div>
              <select
                disabled
                className="mt-2 w-full px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm disabled:bg-[#2B2926]/[0.02]"
              >
                <option>3-step (Day 0, +2, +5)</option>
                <option>5-step (Day 0, +2, +4, +7, +14)</option>
                <option>Aggressive (Day 0, +1, +2, +3, +5)</option>
              </select>
            </div>
            <div className="border border-[#2B2926]/10 rounded-lg p-4 md:col-span-2">
              <div className="text-[10px] uppercase tracking-wider text-[#2B2926]/40 font-bold">
                Auto-pause when reply detected
              </div>
              <label className="mt-2 flex items-center gap-2 cursor-not-allowed opacity-60">
                <input type="checkbox" disabled className="accent-[#F55600]" defaultChecked />
                <span className="text-xs text-[#2B2926]">Pause sequence once a lead replies (recommended)</span>
              </label>
            </div>
          </div>
        </div>
      )}

      {/* States — only on schedule tab */}
      {topTab === 'schedule' && loading && (
        <div className="bg-white border border-[#2B2926]/10 rounded-2xl p-12 flex items-center justify-center">
          <Loader2 className="w-5 h-5 animate-spin text-[#F55600]" />
          <span className="ml-3 text-sm text-[#2B2926]/60">Loading automations…</span>
        </div>
      )}

      {topTab === 'schedule' && !loading && error && (
        <div className="bg-white border border-[#F55600]/30 rounded-2xl p-6 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-[#F55600] mt-0.5" />
          <div className="flex-1">
            <p className="text-sm font-bold text-[#2B2926]">Couldn't load automations</p>
            <p className="text-xs text-[#2B2926]/60 mt-1">{error}</p>
            <button
              type="button"
              onClick={fetchAutomations}
              className="mt-3 text-xs font-bold text-[#F55600] hover:underline"
            >
              Retry
            </button>
          </div>
        </div>
      )}

      {topTab === 'schedule' && !loading && !error && automations.length === 0 && (
        <div className="bg-white border border-[#2B2926]/10 rounded-2xl p-12 text-center">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-[#F55600]/5 mb-4">
            <Zap className="w-7 h-7 text-[#F55600]" />
          </div>
          <h2 className="text-xl font-black text-[#2B2926] mb-2">No automations yet</h2>
          <p className="text-sm text-[#2B2926]/60 max-w-md mx-auto">
            Schedule recurring lead discovery + sequence enrollment runs.
          </p>
          <button
            type="button"
            onClick={() => { setEditing(null); setMode('create'); }}
            className="mt-5 inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold bg-[#F55600] text-white hover:bg-[#F55600]/90"
          >
            <Plus className="w-4 h-4" />
            New Automation
          </button>
        </div>
      )}

      {topTab === 'schedule' && !loading && !error && automations.length > 0 && (
        <div className="bg-white border border-[#2B2926]/10 rounded-2xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-[#2B2926]/[0.02] text-left">
              <tr className="text-[11px] uppercase tracking-wide text-[#2B2926]/40">
                <th className="px-4 py-3 font-bold">Name</th>
                <th className="px-4 py-3 font-bold">Campaign</th>
                <th className="px-4 py-3 font-bold">Schedule</th>
                <th className="px-4 py-3 font-bold">Next run</th>
                <th className="px-4 py-3 font-bold">Last run</th>
                <th className="px-4 py-3 font-bold">Status</th>
                <th className="px-4 py-3 font-bold">Progress</th>
                <th className="px-4 py-3 font-bold text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {automations.map((a) => {
                const camp = campaignsById[a.campaign_id];
                const badgeClass = STATUS_BADGE[a.status] || 'text-[#2B2926]/60 bg-[#2B2926]/5';
                return (
                  <tr key={a.id} className="border-t border-[#2B2926]/5 hover:bg-[#F55600]/[0.02]">
                    <td className="px-4 py-3 font-bold text-[#2B2926]">{a.name || '—'}</td>
                    <td className="px-4 py-3 text-[#2B2926]/70">
                      {/* `slice(0, 8)` was a Mongo-era trick to show a
                          short ObjectId fragment. In Postgres campaign_id
                          is a BigInteger; .slice on a number throws. Just
                          show the numeric id when the friendly name is
                          missing. */}
                      {camp?.name || (a.campaign_id != null ? `#${a.campaign_id}` : '—')}
                    </td>
                    <td className="px-4 py-3">
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-bold border border-[#2B2926]/10 text-[#2B2926]/70">
                        {a.schedule_type === 'daily' ? 'Daily' : 'One-time'}
                      </span>
                      <span className="ml-2 text-[11px] text-[#2B2926]/40">{a.tz || 'UTC'}</span>
                    </td>
                    <td className="px-4 py-3 text-[#2B2926]/70">
                      <div title={absoluteTime(a.next_run_at)}>{relativeTime(a.next_run_at)}</div>
                    </td>
                    <td className="px-4 py-3 text-[#2B2926]/70">
                      <div title={absoluteTime(a.last_run_at)}>{relativeTime(a.last_run_at)}</div>
                    </td>
                    <td className="px-4 py-3">
                      {/* Status chips block — legacy: "Target reached", product name, campaign # */}
                      <div className="flex items-center gap-1 flex-wrap">
                        <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-bold ${badgeClass}`}>
                          {a.status || 'unknown'}
                        </span>
                        {(a.leads_generated ?? 0) >= (a.target_leads ?? 0) && (a.target_leads ?? 0) > 0 && (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-bold bg-[#10B981]/10 text-[#10B981]">
                            Target reached
                          </span>
                        )}
                        {camp?.product_name && (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-bold bg-[#2B2926]/5 text-[#2B2926]/60">
                            {camp.product_name}
                          </span>
                        )}
                        {camp?.name && (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-bold bg-[#2B2926]/5 text-[#2B2926]/60">
                            {camp.name.length > 18 ? camp.name.slice(0, 16) + '…' : camp.name}
                          </span>
                        )}
                      </div>
                      {a.last_run_status && (
                        <div
                          className={[
                            'mt-1 text-[10px] uppercase font-bold tracking-wider',
                            a.last_run_status === 'ok'
                              ? 'text-[#10B981]'
                              : a.last_run_status === 'failed'
                              ? 'text-[#F55600]'
                              : 'text-[#2B2926]/40',
                          ].join(' ')}
                        >
                          {a.last_run_status === 'ok' ? 'Completed' : a.last_run_status}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {/* Progress bar — leads_generated / target_leads */}
                      <div>
                        <div className="text-[11px] font-bold text-[#2B2926]">
                          {(a.leads_generated ?? 0)}
                          <span className="text-[#2B2926]/40 font-normal"> / {a.target_leads ?? '—'}</span>
                        </div>
                        {(a.target_leads ?? 0) > 0 && (
                          <div className="h-1.5 mt-1 rounded-full bg-[#2B2926]/5 overflow-hidden w-32">
                            <div
                              className="h-full rounded-full bg-[#10B981]"
                              style={{
                                width: `${Math.min(100, Math.round(((a.leads_generated ?? 0) / a.target_leads) * 100))}%`,
                              }}
                            />
                          </div>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-1">
                        {a.campaign_id && (
                          <a
                            href={`#campaign-${a.campaign_id}`}
                            title="View Run"
                            className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-bold text-[#F55600] hover:bg-[#F55600]/5"
                          >
                            View Run →
                          </a>
                        )}
                        <button
                          type="button"
                          onClick={() => handleTogglePause(a)}
                          title={a.status === 'active' ? 'Pause' : 'Resume'}
                          className="p-2 rounded-lg hover:bg-[#2B2926]/5 text-[#2B2926]/60"
                        >
                          {a.status === 'active'
                            ? <PauseCircle className="w-4 h-4" />
                            : <PlayCircle className="w-4 h-4" />}
                        </button>
                        <button
                          type="button"
                          onClick={() => { setEditing(a); setMode('edit'); }}
                          title="Edit"
                          className="p-2 rounded-lg hover:bg-[#2B2926]/5 text-[#2B2926]/60"
                        >
                          <Pencil className="w-4 h-4" />
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDelete(a)}
                          title="Delete"
                          className="p-2 rounded-lg hover:bg-[#F55600]/5 text-[#F55600]"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

// ─────────────────────────────────────────────────────────────────────────────
// Create / Edit form
// ─────────────────────────────────────────────────────────────────────────────

function AutomationForm({ authAxios, setMessage, campaigns, initial, onCancel, onSaved }) {
  const editMode = !!initial;

  const browserTz = (() => {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; }
    catch { return 'UTC'; }
  })();

  const [name, setName] = useState(initial?.name || '');
  const [campaignId, setCampaignId] = useState(initial?.campaign_id || '');
  const [scheduleType, setScheduleType] = useState(initial?.schedule_type || 'daily');
  const [tz, setTz] = useState(initial?.tz || browserTz);
  const [targetLeads, setTargetLeads] = useState(initial?.target_leads ?? 100);
  const [nextRunAt, setNextRunAt] = useState(() => {
    if (!initial?.next_run_at) return '';
    try {
      // datetime-local needs YYYY-MM-DDTHH:MM in *local* time.
      const d = new Date(initial.next_run_at);
      const pad = (n) => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    } catch { return ''; }
  });

  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');

  // Surface the timezone list with the user's browser TZ pinned at top if
  // not in the default set — fewer surprises.
  const tzList = useMemo(() => {
    const list = [...TIMEZONES];
    if (browserTz && !list.includes(browserTz)) list.unshift(browserTz);
    return list;
  }, [browserTz]);

  async function handleSave(e) {
    e?.preventDefault?.();
    setFormError('');
    if (!name.trim()) return setFormError('Name is required');
    if (!campaignId) return setFormError('Pick a campaign');
    if (!targetLeads || targetLeads < 1) return setFormError('Target leads must be at least 1');

    const body = {
      name: name.trim(),
      campaign_id: campaignId,
      schedule_type: scheduleType,
      tz,
      target_leads: Number(targetLeads),
    };
    if (nextRunAt) {
      // Send as ISO so backend doesn't have to guess the user's TZ from
      // the bare local-string.
      try {
        body.next_run_at = new Date(nextRunAt).toISOString();
      } catch { /* leave unset */ }
    }

    setSaving(true);
    try {
      if (editMode) {
        await authAxios.patch(`/nexus/automations/${initial.id}`, body);
        setMessage('Automation updated');
      } else {
        await authAxios.post('/nexus/automations', body);
        setMessage('Automation created');
      }
      onSaved();
    } catch (err) {
      setFormError(err?.response?.data?.detail || err.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="p-8 max-w-2xl mx-auto">
      <button
        type="button"
        onClick={onCancel}
        className="inline-flex items-center gap-2 text-xs font-bold text-[#2B2926]/60 hover:text-[#2B2926] mb-4"
      >
        <ArrowLeft className="w-4 h-4" />
        Back to automations
      </button>

      <div className="bg-white border border-[#2B2926]/10 rounded-2xl p-6">
        <h2 className="text-xl font-black text-[#2B2926] mb-1">
          {editMode ? 'Edit Automation' : 'New Automation'}
        </h2>
        <p className="text-sm text-[#2B2926]/60 mb-6">
          {editMode
            ? 'Adjust schedule, target, or campaign for this automation.'
            : 'Schedule a recurring run that pulls leads and enrolls them into your cadence.'}
        </p>

        {formError && (
          <div className="mb-4 px-3 py-2 rounded-lg bg-[#F55600]/10 text-[#F55600] text-xs font-bold">
            {formError}
          </div>
        )}

        <form onSubmit={handleSave} className="space-y-5">
          {/* Name */}
          <label className="block">
            <span className="block text-xs font-bold text-[#2B2926]/70 mb-1">Name</span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Daily SaaS prospects"
              className="w-full px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600]"
            />
          </label>

          {/* Campaign */}
          <label className="block">
            <span className="block text-xs font-bold text-[#2B2926]/70 mb-1">Campaign</span>
            <select
              value={campaignId}
              onChange={(e) => setCampaignId(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm bg-white focus:outline-none focus:border-[#F55600]"
            >
              <option value="">Select a campaign…</option>
              {campaigns.map((c) => (
                <option key={c.id} value={c.id}>{c.name || c.id}</option>
              ))}
            </select>
            {campaigns.length === 0 && (
              <span className="block mt-1 text-[11px] text-[#2B2926]/40">
                No campaigns found — create one in the Campaigns tab first.
              </span>
            )}
          </label>

          {/* Schedule type */}
          <div>
            <span className="block text-xs font-bold text-[#2B2926]/70 mb-2">Schedule</span>
            <div className="flex items-center gap-3">
              {['daily', 'one_time'].map((opt) => (
                <label
                  key={opt}
                  className={[
                    'flex-1 cursor-pointer px-4 py-3 rounded-lg border text-sm font-bold text-center transition',
                    scheduleType === opt
                      ? 'border-[#F55600] bg-[#F55600]/5 text-[#2B2926]'
                      : 'border-[#2B2926]/10 text-[#2B2926]/60 hover:bg-[#2B2926]/[0.02]',
                  ].join(' ')}
                >
                  <input
                    type="radio"
                    name="schedule_type"
                    value={opt}
                    checked={scheduleType === opt}
                    onChange={() => setScheduleType(opt)}
                    className="sr-only"
                  />
                  {opt === 'daily' ? 'Daily' : 'One-time'}
                </label>
              ))}
            </div>
          </div>

          {/* Timezone */}
          <label className="block">
            <span className="block text-xs font-bold text-[#2B2926]/70 mb-1">Timezone</span>
            <select
              value={tz}
              onChange={(e) => setTz(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm bg-white focus:outline-none focus:border-[#F55600]"
            >
              {tzList.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </label>

          {/* Target leads */}
          <label className="block">
            <span className="block text-xs font-bold text-[#2B2926]/70 mb-1">Target leads</span>
            <input
              type="number"
              min="1"
              max="10000"
              value={targetLeads}
              onChange={(e) => setTargetLeads(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600]"
            />
            <span className="block mt-1 text-[11px] text-[#2B2926]/40">
              How many leads each run should aim to find.
            </span>
          </label>

          {/* Next run at */}
          <label className="block">
            <span className="block text-xs font-bold text-[#2B2926]/70 mb-1">
              Next run at <span className="text-[#2B2926]/40 font-normal">(optional)</span>
            </span>
            <input
              type="datetime-local"
              value={nextRunAt}
              onChange={(e) => setNextRunAt(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600]"
            />
            <span className="block mt-1 text-[11px] text-[#2B2926]/40">
              Leave blank to let the scheduler compute the next run from the schedule type + timezone.
            </span>
          </label>

          {/* Actions */}
          <div className="flex items-center justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onCancel}
              disabled={saving}
              className="px-4 py-2 rounded-lg text-sm font-bold text-[#2B2926]/60 hover:bg-[#2B2926]/5 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold bg-[#F55600] text-white hover:bg-[#F55600]/90 disabled:opacity-50"
            >
              {saving && <Loader2 className="w-4 h-4 animate-spin" />}
              <CalendarIcon className="w-4 h-4" />
              {editMode ? 'Save changes' : 'Create automation'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default NexusAutomations;
