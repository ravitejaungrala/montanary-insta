import React, { useState, useMemo, useEffect, useRef } from 'react';
import { isReadOnly } from '../../lib/permissions';
import {
  LayoutDashboard,
  Zap,
  Calendar as CalendarIcon,
  Route,
  PlusCircle,
  Plus,
  Plug,
  TrendingUp,
} from 'lucide-react';
import { NEXUS_ENABLED, API_BASE_URL } from '../../utils/config';

import NexusDashboard from './NexusDashboard';
import NexusAutomations from './NexusAutomations';
import NexusBookings from './NexusBookings';
import NexusJourney from './NexusJourney';
import NexusNewCampaign from './NexusNewCampaign';
import NexusUploadLeads from './NexusUploadLeads';
import NexusConnectors from './NexusConnectors';
import NexusPerformanceAgent from './NexusPerformanceAgent';

/**
 * NEXUS sub-nav sections — labels follow LEGACY NEXUS naming exactly so
 * users/migrations recognize them. Sections are grouped:
 *
 *   GTM Journey group  (legacy primary nav)
 *     - GTM Journey, Dashboard, Products, Runs, All Leads,
 *       Tracker Engine, LinkedIn Outreach
 *   Operations group
 *     - Your Bookings, Automation, Inbound Inbox, Sequences, Voice
 *   Insights group
 *     - Analytics, Performance, Reports
 *   Configuration group
 *     - Integrations, Team, Billing, Settings
 *   Extras
 *     - New Campaign wizard, Onboarding
 *     (Campaigns kept under operations for back-compat)
 *
 * Palette: PIPELYT's mandatory 4 colors only (#F55600, #10B981, black, white).
 */
// Further-trimmed tab list. In addition to the previous removals
// (Onboarding, Settings, Billing, Team, Leads, Voice, Campaigns,
// Integrations, Tracker Engine, standalone "New Campaign") we've now
// also removed: Products, Runs, All Leads, LinkedIn Outreach,
// Sequences, Performance, Reports, Analytics. Reports + Analytics
// were merged into the Dashboard — its filter bar + download button
// now cover both surfaces. The New Campaign wizard stays hidden-but-
// renderable so the "+ New Run" CTA still routes to it.
const NEXUS_SECTIONS = [
  // GTM Journey group
  { id: 'gtm-journey',        label: 'GTM Journey',        icon: Route,           Component: NexusJourney,         group: 'gtm' },
  { id: 'dashboard',          label: 'Dashboard',          icon: LayoutDashboard, Component: NexusDashboard,       group: 'gtm' },

  // Connectors — connect your own sending mailbox (Outlook / Gmail).
  { id: 'connectors',         label: 'Connectors',         icon: Plug,            Component: NexusConnectors,      group: 'gtm' },

  // Operations
  { id: 'your-bookings',      label: 'Demo Agent',         icon: CalendarIcon,    Component: NexusBookings,        group: 'ops' },
  // Performance Agent — re-added 2026-07-11 as a fresh build (backend
  // /nexus/performance/generate now actually exists; the old tab was pulled
  // because it always 404'd). See /implementation.md.
  { id: 'performance',        label: 'Performance',        icon: TrendingUp,      Component: NexusPerformanceAgent, group: 'ops' },
  // Automation tab hidden temporarily (kept registered so the route still
  // resolves if anyone deep-links to it). Re-add to the visible tab list
  // once the recharts width=0 warning + cadence editor polish are done.
  { id: 'automation',         label: 'Automation',         icon: Zap,             Component: NexusAutomations,     group: 'ops', hiddenInTabs: true },

  // Hidden — kept registered so + New Run CTA still navigates here.
  { id: 'new-campaign',       label: 'New Campaign',       icon: PlusCircle,      Component: NexusNewCampaign,     group: 'extra', hiddenInTabs: true },
  // Hidden — BYO-leads wizard. Reachable from the "Upload Leads" button
  // in the GTM Journey header (handler calls onNavigate('upload-leads')).
  { id: 'upload-leads',       label: 'Upload Leads',       icon: PlusCircle,      Component: NexusUploadLeads,     group: 'extra', hiddenInTabs: true },
];

// One-time auto-provision guard — keyed by user.id so a single browser
// shared across two accounts (rare but possible) only runs the bootstrap
// once per user. Held outside the component so React StrictMode's double-
// invoke doesn't fire it twice on dev.
const _autoProvisionedFor = new Set();

// In-flight bootstrap promise — keyed by user.id. Lets the axios interceptor
// share a single workspace-create call across multiple concurrent requests
// that all 422 with the same "No default workspace selected" error.
const _bootstrapInFlight = new Map();
// Axios response interceptors we've already attached, keyed by user.id, so
// we don't double-wrap the same instance.
const _interceptorAttached = new Set();

// Shared bootstrap routine — used by BOTH the initial useEffect and the
// retry interceptor below. Reads /nexus/me first; if a workspace already
// exists, returns immediately without writes (safe for existing users).
async function _ensureWorkspace({ authAxios, apiBase, user }) {
  if (!user?.id) return null;
  if (_bootstrapInFlight.has(user.id)) return _bootstrapInFlight.get(user.id);
  const promise = (async () => {
    const me = await authAxios.get(`${apiBase}/me`);
    if (me?.data?.default_workspace_id) {
      // Existing user — nothing to do.
      return me.data.default_workspace_id;
    }
    const handle = (user.email || '').split('@')[0] || 'My';
    const wsName = handle.charAt(0).toUpperCase() + handle.slice(1) + "'s Workspace";
    const created = await authAxios.post(`${apiBase}/workspaces`, { name: wsName });
    const newId = created?.data?.id;
    if (!newId) return null;
    // Belt-and-braces — backend's create_workspace already promotes when
    // the user had no default, so this PATCH is a no-op for new users.
    try {
      await authAxios.patch(`${apiBase}/me/settings`, { default_workspace_id: newId });
    } catch (_e) { /* already bound by create handler — ignore */ }
    return newId;
  })().finally(() => _bootstrapInFlight.delete(user.id));
  _bootstrapInFlight.set(user.id, promise);
  return promise;
}

const NexusLayout = ({ authAxios, user, setMessage }) => {
  // Persist the last-selected section across reloads. If a previously-saved
  // section id is no longer in NEXUS_SECTIONS (e.g. localStorage carries
  // 'reports' or 'analytics' from a build before those tabs were merged
  // into Dashboard), fall back to the dashboard so the layout never tries
  // to render a missing tab.
  const initial = (() => {
    try {
      const saved = localStorage.getItem('nexus_section');
      if (saved && NEXUS_SECTIONS.some((s) => s.id === saved)) return saved;
      return 'gtm-journey';
    } catch {
      return 'gtm-journey';
    }
  })();
  const [section, setSection] = useState(initial);

  // Per-section "becomes active" nonce. Increments every time the user
  // switches INTO that section from a different one. Children that need
  // to refetch on re-entry (e.g. NexusJourney's lead list after a manual
  // upload populates new rows) read the nonce as a useEffect dep so they
  // pick up fresh data instead of showing stale state from the keep-alive.
  const [activeNonces, setActiveNonces] = useState({});
  const prevSectionRef = useRef(initial);
  // When true, the NEXT section switch SKIPS the nonce bump so the destination
  // tab renders in its PRESERVED (kept-alive) state instead of being re-keyed
  // and reset. Set by the "Back" button so e.g. returning to the New-Campaign
  // tab lands on the Campaign-launched leads screen the user came from via
  // "View leads", not a fresh blank wizard.
  const preserveNextRef = useRef(false);
  useEffect(() => {
    if (prevSectionRef.current !== section) {
      prevSectionRef.current = section;
      if (preserveNextRef.current) {
        preserveNextRef.current = false;
      } else {
        setActiveNonces((prev) => ({ ...prev, [section]: (prev[section] || 0) + 1 }));
      }
    }
  }, [section]);

  // Tabs are LAZY-mounted (mounted on first visit, kept alive afterwards).
  // Once a tab has been visited it stays in the DOM (display:none when not
  // active) — so subsequent switches are instant and child state is kept.
  const [visited, setVisited] = useState(() => new Set([initial]));

  // Remembers the section the user was on before the current one, so child
  // pages (e.g. GTM Journey reached via "View leads") can offer a Back
  // button that returns to where the user came from.
  const [prevSection, setPrevSection] = useState(null);

  const handleSelect = (id) => {
    setSection((cur) => {
      if (id !== cur) setPrevSection(cur);
      return id;
    });
    setVisited((prev) => {
      if (prev.has(id)) return prev;
      const next = new Set(prev);
      next.add(id);
      return next;
    });
    try {
      localStorage.setItem('nexus_section', id);
    } catch {
      /* private mode */
    }
  };

  const handleNewRun = () => handleSelect('new-campaign');

  // History-style Back used by child pages' top "Back" button. Returns to the
  // previously-opened tab WITHOUT re-keying it, so its kept-alive state is
  // preserved — e.g. Back from GTM Journey lands on the exact Campaign-launched
  // leads page the user reached via "View leads", not a reset New Run wizard.
  const handleBack = () => {
    if (!prevSection || prevSection === section) return;
    preserveNextRef.current = true;
    handleSelect(prevSection);
  };

  // ── New-user auto-provision (frontend only, backend untouched) ──────────
  //
  // First time a brand-new user opens any Nexus tab, the backend's first
  // API call 422s with "No default workspace selected" (see deps.py:117).
  // We catch THAT case by calling GET /nexus/me; if it returns
  // default_workspace_id === null we:
  //   1. POST /nexus/workspaces      → creates a fresh workspace owned
  //                                      by THIS user (backend enforces
  //                                      owner_id = user.id, so it cannot
  //                                      see anyone else's data).
  //   2. PATCH /nexus/me/settings    → binds it as the user's default.
  //
  // SAFETY:
  //   • We READ /nexus/me first and BAIL if a workspace is already set.
  //     Existing users (your first email) hit this branch and exit before
  //     any POST/PATCH ever fires. Their data cannot be touched.
  //   • Module-level `_autoProvisionedFor` guards against duplicate runs.
  //   • Any error is swallowed silently — the existing "No default
  //     workspace selected" banner remains as a fallback so the user can
  //     still call the endpoints manually.
  useEffect(() => {
    if (!NEXUS_ENABLED) return;
    if (!authAxios || !user?.id) return;
    const apiBase = `${API_BASE_URL}/nexus`;

    // ── Eager bootstrap (handles the common path: navigate to GTM Journey
    //    → no race because no other call has started yet). ────────────────
    if (!_autoProvisionedFor.has(user.id)) {
      _autoProvisionedFor.add(user.id);
      _ensureWorkspace({ authAxios, apiBase, user }).catch(() => {
        // If it fails the interceptor below will retry on-demand the next
        // time any Nexus call 422s with the workspace error.
      });
    }

    // ── Reactive interceptor (handles the race: user submits a form
    //    before the eager bootstrap finishes, /analyze 422s). On that
    //    specific error, we silently ensure the workspace exists then
    //    replay the original request — once. Non-workspace errors flow
    //    through unchanged. ────────────────────────────────────────────
    if (_interceptorAttached.has(user.id)) return;
    _interceptorAttached.add(user.id);

    const isWorkspaceError = (detail) =>
      typeof detail === 'string' && detail.toLowerCase().includes('default workspace');

    const interceptorId = authAxios.interceptors.response.use(
      (response) => response,
      async (error) => {
        const cfg = error?.config;
        const status = error?.response?.status;
        const detail = error?.response?.data?.detail;
        if (
          !cfg ||
          cfg.__autoProvisionRetried ||
          status !== 422 ||
          !isWorkspaceError(detail)
        ) {
          return Promise.reject(error);
        }
        cfg.__autoProvisionRetried = true;
        try {
          await _ensureWorkspace({ authAxios, apiBase, user });
        } catch (_e) {
          return Promise.reject(error);
        }
        // Replay the original request now that the workspace exists.
        return authAxios.request(cfg);
      },
    );

    return () => {
      authAxios.interceptors.response.eject(interceptorId);
      _interceptorAttached.delete(user.id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id]);

  if (!NEXUS_ENABLED) {
    return (
      <div className="flex-1 flex items-center justify-center p-8 bg-white">
        <p className="text-sm text-[#2B2926]/60">Nexus is not enabled in this environment.</p>
      </div>
    );
  }

  return (
    <div data-nexus="true" className="flex-1 flex flex-col h-full bg-white">
      {/* Top sub-nav — Soft Aurora style (rounded pills, soft shadow). */}
      <div
        className="bg-white sticky top-0 z-10"
        style={{ borderBottom: '1px solid #E5E7EB', fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
      >
        <div className="flex items-center gap-2 px-4 py-[6px] overflow-x-auto">
          {/* Nexus brand block (lightning icon + label) intentionally removed
              per user request — the navbar starts directly with the
              + New Run pill so there's no visual noise on the left. */}

          {/* + New Run — active (gradient orange) when the user is on the
              new-campaign route, normal tab style otherwise so it matches
              the other sub-nav pills. Hidden for read-only Users. */}
          {!isReadOnly(user) && (
            <>
              <button
                type="button"
                onClick={handleNewRun}
                className="shrink-0 inline-flex items-center gap-1.5 px-[10px] py-[5px] rounded-[9px] text-[12px] font-semibold transition-all hover:-translate-y-[1px]"
                style={
                  section === 'new-campaign'
                    ? {
                        color: '#fff',
                        background: '#F55600',
                        boxShadow: '0 2px 6px rgba(245,86,0,0.18)',
                        border: '1px solid transparent',
                      }
                    : {
                        color: '#2B2926',
                        background: '#fff',
                        border: '1px solid #E5E7EB',
                      }
                }
              >
                New Campaign
              </button>

              <div className="w-px h-[18px] mx-1 shrink-0" style={{ background: '#E5E7EB' }} />
            </>
          )}

          {NEXUS_SECTIONS.filter((s) => !s.hiddenInTabs).map(({ id, label, icon: Icon }) => {
            const active = id === section;
            return (
              <button
                key={id}
                type="button"
                onClick={() => handleSelect(id)}
                className="shrink-0 inline-flex items-center gap-1.5 px-[10px] py-[5px] rounded-[9px] text-[12px] font-semibold transition-all hover:-translate-y-[1px]"
                style={
                  active
                    ? {
                        color: '#fff',
                        background: '#F55600',
                        boxShadow: '0 2px 6px rgba(245,86,0,0.18)',
                      }
                    : {
                        color: '#2B2926',
                        background: '#fff',
                        border: '1px solid #E5E7EB',
                      }
                }
              >
                <Icon className="w-[12px] h-[12px]" />
                {label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Active section — keep-alive: mount on first visit, hide-on-switch.
          Container is a bounded flex column (overflow-hidden + min-h-0) so
          each active tab can be a flex child that fills the slot exactly.
          Tab wrapper style is picked per-tab:
            - gtm-journey: non-scrolling wrapper, lets the inner 3-column
              grid manage its own scrollbars (lead list scrolls inside the
              column, not at page level).
            - all others: wrapper scrolls vertically so legacy tabs that
              expect document-style scroll still work.
          Inactive tabs use `display: none` to stay mounted but invisible. */}
      <div className="flex-1 flex flex-col overflow-hidden min-h-0">
        {NEXUS_SECTIONS.filter((s) => visited.has(s.id)).map((s) => {
          const Comp = s.Component;
          const active = s.id === section;
          const wantsInternalScroll = s.id === 'gtm-journey';
          const activeStyle = wantsInternalScroll
            ? { display: 'flex', flex: '1 1 0', minHeight: 0, flexDirection: 'column', overflow: 'hidden' }
            : { display: 'flex', flex: '1 1 0', minHeight: 0, flexDirection: 'column', overflowY: 'auto' };
          // 2026-05-29 — Force REMOUNT of the New Campaign tab on every
          // re-entry per user request ("after run user has visited to
          // GTM Journey and came back to New Run, it should be again
          // new rather than showing previous"). The nonce bumps each
          // time the section becomes active, so using it as the React
          // `key` for the New Campaign component throws away the cached
          // state and renders a fresh wizard. Other tabs keep their
          // state across navigations (stable key = s.id).
          const compKey = s.id === 'new-campaign'
            ? `new-campaign-${activeNonces['new-campaign'] || 0}`
            : s.id;
          return (
            <div
              key={s.id}
              style={active ? activeStyle : { display: 'none' }}
              data-nexus-tab={s.id}
            >
              <Comp
                key={compKey}
                authAxios={authAxios}
                user={user}
                setMessage={setMessage}
                apiBase={`${API_BASE_URL}/nexus`}
                onNavigate={handleSelect}
                onBack={
                  // History-style Back: returns to the previously-opened tab
                  // in its PRESERVED state (see handleBack). Available on every
                  // page once the user has navigated at least once.
                  prevSection ? handleBack : undefined
                }
                activateNonce={activeNonces[s.id] || 0}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default NexusLayout;
