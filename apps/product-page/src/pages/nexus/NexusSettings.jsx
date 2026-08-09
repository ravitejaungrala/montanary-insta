/**
 * NexusSettings — port of legacy apps/nexus-legacy/client/src/pages/Settings.jsx.
 *
 * State names + section structure match legacy 1:1 so future Mongo
 * payloads land on the right keys. Operational logic is stubbed —
 * devs fill in axios calls + persistence as backend matures.
 *
 * Sections (legacy):
 *   - Profile (name, email, timezone, daily_report_time)
 *   - Workspace (workspace name, plan, founder_mode)
 *   - Notifications (email / in_app / slack)
 *   - Sending (mailboxes, daily_send_limit per mailbox)
 *   - Integrations (Apollo, Hunter, Resend, Gmail, Calendly, MS Bookings)
 *   - Danger zone (delete workspace)
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  Bell,
  Building2,
  Mail,
  Plug,
  Save,
  User as UserIcon,
  Loader2,
} from 'lucide-react';

const SECTIONS = [
  { id: 'profile',       label: 'Profile',       icon: UserIcon },
  { id: 'workspace',     label: 'Workspace',     icon: Building2 },
  { id: 'notifications', label: 'Notifications', icon: Bell },
  { id: 'sending',       label: 'Sending',       icon: Mail },
  { id: 'integrations',  label: 'Integrations',  icon: Plug },
  { id: 'danger',        label: 'Danger Zone',   icon: AlertTriangle },
];

const NexusSettings = ({ authAxios, apiBase, user, setMessage }) => {
  // State names mirror legacy Settings.jsx
  const [activeSection, setActiveSection] = useState('profile');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [profile, setProfile] = useState({
    timezone: 'UTC',
    daily_report_time: '08:00',
    daily_pipeline_time: '07:00',
    auto_reply_enabled: false,
    founder_mode: false,
  });
  const [workspaceSettings, setWorkspaceSettings] = useState({});
  const [notifications, setNotifications] = useState({
    email: true,
    in_app: true,
    slack: false,
  });

  const settingsBase = `${apiBase}/settings`;

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [meRes, wsRes, notifRes] = await Promise.all([
        authAxios.get(`${settingsBase}/me`),
        authAxios.get(`${settingsBase}/workspace`),
        authAxios.get(`${settingsBase}/notifications`),
      ]);
      setProfile((p) => ({ ...p, ...(meRes.data || {}) }));
      setWorkspaceSettings(wsRes.data?.settings || {});
      setNotifications((n) => ({ ...n, ...(notifRes.data || {}) }));
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  }, [authAxios, settingsBase]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const saveProfile = async () => {
    setSaving(true);
    try {
      await authAxios.patch(`${settingsBase}/me`, profile);
      if (setMessage) setMessage({ type: 'success', text: 'Settings saved' });
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setSaving(false);
    }
  };

  const saveNotifications = async () => {
    setSaving(true);
    try {
      await authAxios.patch(`${settingsBase}/notifications`, notifications);
      if (setMessage) setMessage({ type: 'success', text: 'Notifications saved' });
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setSaving(false);
    }
  };

  const renderProfile = () => (
    <div className="space-y-4 max-w-xl">
      <div>
        <label className="block text-xs font-bold text-[#2B2926] mb-1">Timezone</label>
        <input
          type="text"
          value={profile.timezone || ''}
          onChange={(e) => setProfile({ ...profile, timezone: e.target.value })}
          className="w-full px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600]"
          placeholder="e.g., America/New_York"
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-bold text-[#2B2926] mb-1">Daily Report Time</label>
          <input
            type="time"
            value={profile.daily_report_time || '08:00'}
            onChange={(e) => setProfile({ ...profile, daily_report_time: e.target.value })}
            className="w-full px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600]"
          />
        </div>
        <div>
          <label className="block text-xs font-bold text-[#2B2926] mb-1">Daily Pipeline Time</label>
          <input
            type="time"
            value={profile.daily_pipeline_time || '07:00'}
            onChange={(e) => setProfile({ ...profile, daily_pipeline_time: e.target.value })}
            className="w-full px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600]"
          />
        </div>
      </div>
      <label className="flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={profile.auto_reply_enabled || false}
          onChange={(e) => setProfile({ ...profile, auto_reply_enabled: e.target.checked })}
          className="accent-[#F55600]"
        />
        <span className="text-xs text-[#2B2926]">Auto-reply to common questions</span>
      </label>
      <label className="flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={profile.founder_mode || false}
          onChange={(e) => setProfile({ ...profile, founder_mode: e.target.checked })}
          className="accent-[#F55600]"
        />
        <span className="text-xs text-[#2B2926]">Founder mode (high-touch outreach)</span>
      </label>
      <button
        type="button"
        onClick={saveProfile}
        disabled={saving}
        className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[#F55600] text-white text-xs font-bold hover:opacity-90 disabled:opacity-50"
      >
        {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
        Save changes
      </button>
    </div>
  );

  const renderNotifications = () => (
    <div className="space-y-3 max-w-xl">
      {Object.entries({
        email: 'Email notifications',
        in_app: 'In-app notifications',
        slack: 'Slack notifications',
      }).map(([key, label]) => (
        <label key={key} className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={!!notifications[key]}
            onChange={(e) => setNotifications({ ...notifications, [key]: e.target.checked })}
            className="accent-[#F55600]"
          />
          <span className="text-xs text-[#2B2926]">{label}</span>
        </label>
      ))}
      <button
        type="button"
        onClick={saveNotifications}
        disabled={saving}
        className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[#F55600] text-white text-xs font-bold hover:opacity-90 disabled:opacity-50"
      >
        {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
        Save notifications
      </button>
    </div>
  );

  const renderPlaceholder = (title) => (
    <div className="max-w-xl">
      <p className="text-xs text-[#2B2926]/60">
        {title} configuration coming soon. Backend endpoints are stubbed; the
        UI here will wire up once the team makes them operational.
      </p>
    </div>
  );

  return (
    <div className="bg-white">
      <div className="px-5 py-4 border-b border-[#2B2926]/10">
        <h1 className="text-lg font-black text-[#2B2926] tracking-tight">Settings</h1>
        <p className="text-xs text-[#2B2926]/60 mt-0.5">
          Workspace + personal preferences.
        </p>
      </div>

      {error && (
        <div className="mx-5 mt-3 px-3 py-2 rounded-lg bg-[#F55600]/10 border border-[#F55600]/30 text-xs text-[#F55600]">
          {error}
        </div>
      )}

      <div className="grid" style={{ gridTemplateColumns: '220px 1fr' }}>
        {/* Section nav */}
        <div className="border-r border-[#2B2926]/10 p-3 space-y-1">
          {SECTIONS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => setActiveSection(id)}
              className={[
                'w-full text-left inline-flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-bold transition-all',
                activeSection === id
                  ? 'bg-[#F55600]/10 text-[#F55600]'
                  : 'text-[#2B2926]/60 hover:bg-[#F55600]/5 hover:text-[#2B2926]',
              ].join(' ')}
            >
              <Icon className="w-3.5 h-3.5" />
              {label}
            </button>
          ))}
        </div>

        {/* Body */}
        <div className="p-5">
          {loading ? (
            <div className="flex items-center gap-2 text-xs text-[#2B2926]/60">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              Loading settings…
            </div>
          ) : activeSection === 'profile' ? (
            renderProfile()
          ) : activeSection === 'notifications' ? (
            renderNotifications()
          ) : activeSection === 'workspace' ? (
            renderPlaceholder('Workspace')
          ) : activeSection === 'sending' ? (
            renderPlaceholder('Sending mailboxes')
          ) : activeSection === 'integrations' ? (
            renderPlaceholder('Integrations (Hunter, Gmail, etc.)')
          ) : (
            <div className="max-w-xl">
              <div className="border border-[#F55600]/30 bg-[#F55600]/5 rounded-lg p-4">
                <h3 className="text-sm font-bold text-[#F55600] inline-flex items-center gap-1.5">
                  <AlertTriangle className="w-4 h-4" />
                  Delete workspace
                </h3>
                <p className="text-xs text-[#2B2926]/60 mt-2">
                  Permanently deletes all leads, campaigns, sequences, and inbox
                  data for this workspace. PIPELYT user account stays intact.
                </p>
                <button
                  type="button"
                  disabled
                  className="mt-3 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#F55600] text-white text-xs font-bold opacity-50 cursor-not-allowed"
                  title="Wire to DELETE /nexus/workspaces/{id} when ready"
                >
                  Delete workspace
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default NexusSettings;
