import React, { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { isReadOnly } from '../lib/permissions';
import { Plus, Linkedin, Instagram, Facebook, Users, X, RefreshCw, Trash2, AlertTriangle, Youtube, UserPlus, Send, CheckCircle2, Clock } from 'lucide-react';
import XIcon from '../components/icons/XIcon';
import { useNotification } from '../context/NotificationContext';
// AlertModal is used at the bottom of this file for the "Remove account?"
// confirmation. Without this import, production builds throw
//   ReferenceError: AlertModal is not defined
// the moment a user opens the Connections tab.
import AlertModal from '../components/ui/AlertModal';

// Portal-based Assign dropdown — escapes parent overflow:auto/hidden so the
// menu renders ABOVE everything (the manage modal's scroll container was
// clipping it). Computes fixed coords from the trigger button's rect.
const AssignDropdown = ({ acc, isOpen, onToggle, onClose, teamMembers, handleAssign }) => {
  const btnRef = useRef(null);
  const [coords, setCoords] = useState({ top: 0, left: 0 });

  useEffect(() => {
    if (!isOpen || !btnRef.current) return;
    const compute = () => {
      const r = btnRef.current.getBoundingClientRect();
      const menuWidth = 220;
      const margin = 6;
      // Anchor menu's right edge to button's right edge so it opens leftward
      // when the button sits near the right of the viewport.
      const left = Math.max(8, r.right - menuWidth);
      const top = r.bottom + margin;
      setCoords({ top, left });
    };
    compute();
    const handler = () => compute();
    window.addEventListener('scroll', handler, true);
    window.addEventListener('resize', handler);
    return () => {
      window.removeEventListener('scroll', handler, true);
      window.removeEventListener('resize', handler);
    };
  }, [isOpen]);

  return (
    <div className="relative">
      <button
        ref={btnRef}
        onClick={onToggle}
        className={`p-1.5 rounded-lg border transition-all flex items-center gap-2 group/assign ${acc.assigned_to ? 'bg-orange-50 border-orange-200 text-[#F55600]' : 'bg-white border-[#2B2926]/30 text-[#F55600] hover:border-orange-200'}`}
        title="Assign to team member"
      >
        <UserPlus className="w-3 h-3" />
        {acc.assigned_to && (
          <span className="text-[9px] font-semibold uppercase tracking-tighter">
            {acc.assigned_to.full_name || acc.assigned_to.email?.split('@')[0]}
          </span>
        )}
      </button>

      {isOpen && createPortal(
        <>
          <div
            className="fixed inset-0"
            style={{ zIndex: 9998 }}
            onClick={onClose}
          />
          <div
            className="fixed bg-white rounded-xl shadow-2xl border border-[#2B2926]/30 py-1.5 animate-in fade-in zoom-in-95 duration-150"
            style={{ top: coords.top, left: coords.left, width: 220, zIndex: 9999 }}
          >
            <div className="px-3 py-1 text-[8px] font-semibold uppercase tracking-widest text-[#2B2926] border-b border-slate-50 mb-1">Assign To</div>
            <button
              onClick={() => { handleAssign(acc.db_id, null); onClose(); }}
              className={`w-full text-left px-3 py-2 text-[10px] font-bold hover:bg-slate-50 ${!acc.assigned_to_user_id ? 'text-[#F55600] bg-orange-50/50' : 'text-[#2B2926]'}`}
            >
              Myself (Unassigned)
            </button>
            {teamMembers.map(m => (
              <button
                key={m.id}
                onClick={() => { handleAssign(acc.db_id, m.id); onClose(); }}
                className={`w-full text-left px-3 py-2 text-[10px] font-bold hover:bg-orange-50 flex items-center justify-between ${acc.assigned_to_user_id === m.id ? 'text-[#F55600] bg-orange-50' : 'text-[#2B2926]'}`}
              >
                <span>{m.full_name || m.email}</span>
              </button>
            ))}
          </div>
        </>,
        document.body
      )}
    </div>
  );
};

const Connections = ({
  connections,
  handleConnect,
  handleDisconnectAccount,
  handleResetConnection,
  handleDisconnectCanva,
  user,
  showIds, setShowIds,
  authAxios,
  hideHeader = false,
  // M1 fix — callback the parent wires to fetchConnections() so we can
  // refresh state after an assign/reassign without a full page reload.
  // Optional so existing call sites (Onboarding, ForceConnectModal) don't
  // break; they default to a no-op that falls back to the old behavior.
  onConnectionsChange = null,
}) => {
  const [manageModal, setManageModal] = React.useState({ isOpen: false, platform: '', accounts: [] });
  // Disconnect-confirmation modal — shape: { isOpen, platform, acc }.
  // Was previously USED in JSX (the <AlertModal> at the bottom of this
  // file) but never declared as state, which caused
  //   ReferenceError: confirmModal is not defined
  // the moment any user opened the Connections tab in any environment
  // where the bundle managed to survive minification (Amplify staging
  // crashed first — production was still serving an older bundle that
  // didn't contain the AlertModal block, so the bug looked
  // environment-specific until the force-push).
  const [confirmModal, setConfirmModal] = React.useState({ isOpen: false, platform: '', acc: null });

  // Team-members feature:
  //   isMember  — members see only accounts assigned to them, and the pill
  //               carries a "Request Reconnect" button instead of disconnect.
  //   isAdmin   — admins can assign accounts to team members via a dropdown,
  //               and see a banner with any pending reconnect requests.
  const isMember = isReadOnly(user);
  const isAdmin = user && !isReadOnly(user);
  const [teamMembers, setTeamMembers] = React.useState([]);
  const [pendingRequests, setPendingRequests] = React.useState([]);
  const [assignOpenFor, setAssignOpenFor] = React.useState(null); // db_id of the open dropdown
  const { toast, confirm, prompt } = useNotification();
  const [flash, setFlash] = useState(null);



  // Admin-only: fetch team member list (for the Assign dropdown) + pending
  // reconnect requests (for the top banner).
  React.useEffect(() => {
    if (!authAxios || !isAdmin) return;
    (async () => {
      try {
        const t = await authAxios.get('/team/members');
        // list returns combined members + invites; we only want accepted members
        setTeamMembers(((t.data?.members) || []).filter(m => m.kind === 'member' && !m.disabled));
      } catch { /* silently ignore — user may be on a plan without teams */ }
      try {
        const r = await authAxios.get('/connections/reconnect-requests');
        setPendingRequests(r.data || []);
      } catch (err) {
        // H1 fix — was calling undefined `setMessage`, which threw
        // ReferenceError and crashed the whole Connections page for any
        // admin whose reconnect-requests fetch failed (network, 5xx).
        // The component already uses `toast` from useNotification.
        console.error(err);
        toast.error(`Failed to fetch reconnect requests: ${err.response?.data?.detail || 'Unknown error'}`);
      }
    })();
  }, [authAxios, isAdmin]);

  const refreshPending = async () => {
    if (!authAxios || !isAdmin) return;
    try {
      const r = await authAxios.get('/connections/reconnect-requests');
      setPendingRequests(r.data || []);
    } catch { }
  };

  const handleAssign = async (accDbId, targetUserId) => {
    try {
      await authAxios.put(`/connections/${accDbId}/assign`, { user_id: targetUserId });
      toast.success(targetUserId ? 'Assigned to team member.' : 'Unassigned.');
      setAssignOpenFor(null);
      // M1 fix — was `window.location.reload()`, which nuked the whole
      // SPA (loses conversation context, refetches profile + dashboard +
      // connections + analytics + everything). Now we ask the parent to
      // just re-run fetchConnections(); the assigned_to field lives on
      // the account row itself, so a single refetch is sufficient.
      if (typeof onConnectionsChange === 'function') {
        onConnectionsChange();
      } else {
        // Fallback for callers that didn't wire the callback yet (e.g.
        // ForceConnectModal, Onboarding step 3). A full reload is
        // strictly worse but preserves the old behavior instead of
        // silently leaving stale data on screen.
        window.location.reload();
      }
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Assignment failed');
    }
  };

  const handleRequestReconnect = async (accDbId, platform) => {
    const reason = await prompt({
      title: 'Request Reconnect',
      message: `Ask your admin to reconnect your ${platform} account. Add an optional note:`,
      placeholder: "Why is it broken? (optional)",
      confirmText: 'Send Request'
    });
    if (reason === null) return; // cancelled
    try {
      await authAxios.post(`/connections/${accDbId}/reconnect-request`, { reason: reason || null });
      toast.success('Reconnect request sent to your admin.');
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Could not send request');
    }
  };
  const totalConnected = Object.values(connections || {}).reduce((acc, curr) => acc + (curr?.length || 0), 0);
  // H2 fix — Stripe stores billing cycle in the same column (e.g.
  // 'agency_yearly', 'growth_monthly'). Without stripping the suffix, the
  // lookup below missed 'agency_yearly' and fell through to `|| 1` — so a
  // paying Agency Yearly customer saw "N / 1 accounts" instead of "N / ∞".
  const plan = ((user?.pricing_plan || '') + '')
    .toLowerCase()
    .replace(/_yearly$|_monthly$/, '') || 'free';
  const limits = { free: 1, starter: 3, growth: 5, agency: Infinity, enterprise: Infinity };
  const limit = limits[plan] || 1;
  const isUnlimited = limit === Infinity;

  const platformMeta = {
    facebook: {
      name: 'Facebook Page',
      color: '#054ada',
      bg: 'bg-blue-50',
      light: '#e7f3ff',
      icon: ({ className }) => <img src="/facebook.png" className={className} alt="Facebook" />,
      type: 'social'
    },
    instagram: {
      name: 'Instagram Business',
      color: '#b71d53',
      bg: 'bg-rose-50',
      light: '#fff0f3',
      icon: ({ className }) => <img src="/instagram.jpg" className={className} alt="Instagram" />,
      type: 'social'
    },
    linkedin: {
      name: 'LinkedIn',
      color: '#005582',
      bg: 'bg-sky-50',
      light: '#f0f7ff',
      icon: ({ className }) => <img src="/linkedlin.jpg" className={className} alt="LinkedIn" />,
      type: 'social'
    },
    twitter: {
      name: 'X',
      color: '#2B2926',
      bg: 'bg-slate-100',
      light: '#f8fafc',
      icon: ({ className }) => <XIcon className={className} />,
      type: 'social'
    },
    reddit: {
      name: 'Reddit',
      color: '#F55600',
      bg: 'bg-orange-50',
      light: '#fff5f0',
      icon: ({ className }) => <img src="/reddit-icon.png" className={className} alt="Reddit" />,
      // Reddit OAuth/publish is not production-ready yet — shown as Coming Soon
      // until the integration ships. Was previously 'social' with a special-case
      // click-block hack; that hack is now removed.
      type: 'soon'
    },
    tiktok: {
      name: 'TikTok',
      color: '#2B2926',
      bg: 'bg-slate-50',
      light: '#f1f5f9',
      icon: ({ className }) => (
        <svg className={className} viewBox="0 0 24 24" fill="currentColor">
          <path d="M12.525.02c1.31-.02 2.61-.01 3.91-.01.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.06-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.59-1.01-.14 3.39-.12 6.79-.12 10.18.06 2.1-.69 4.31-2.31 5.74-1.61 1.48-3.95 1.89-6.02 1.25-2.07-.63-3.75-2.58-4.14-4.63-.48-2.61.64-5.59 2.92-6.94 1.48-.88 3.3-.96 4.9-.4v4.25c-2.4-.64-5.11.75-5.38 3.23-.21 1.9 1.56 3.82 3.48 3.73 1.48.06 2.87-1 3.19-2.45.1-.38.12-.77.12-1.16-.01-5.1-.01-10.19-.01-15.29-.01-2.5 1.61-4.75 4-5.36z" />
        </svg>
      ),
      type: 'social'
    },
    youtube: {
      name: 'YouTube',
      color: '#c40000',
      bg: 'bg-red-50',
      light: '#fff2f2',
      icon: ({ className }) => <img src="/youtube-icon.png" className={className} alt="YouTube" />,
      type: 'social'
    },
    pinterest: {
      name: 'Pinterest',
      color: '#a8001a',
      bg: 'bg-red-50',
      light: '#fff1f1',
      icon: ({ className }) => <img src="/pinterest-logo.png" className={className} alt="Pinterest" />,
      type: 'social'
    },
    google: {
      name: 'Google Business',
      color: '#174ea6',
      bg: 'bg-blue-50',
      light: '#f1f7ff',
      icon: ({ className }) => <img src="/googlebussiness.png" className={className} alt="Google Business" />,
      type: 'soon'
    },
    canva: {
      name: 'Canva',
      color: '#005fa3',
      bg: 'bg-cyan-50',
      light: '#e0fbff',
      icon: ({ className }) => <img src="/canava-icon.png" className={className} alt="Canva" />,
      type: 'integration'
    }
  };

  const activePlatforms = ['instagram', 'canva'];
  const soonPlatforms = [];

  return (
    <div className={`bg-white ${(typeof hideHeader !== 'undefined' && hideHeader) ? 'py-0' : 'min-h-screen pt-2 pb-8'} px-4 relative font-['Lato',sans-serif]`}>
      <div className="max-w-[1600px] mx-auto relative cursor-default">
        {!(typeof hideHeader !== 'undefined' && hideHeader) && (
          <div className="relative flex flex-col items-center text-center mb-8 pt-2">
            {/* Usage badge — on mobile it sits in normal flow above the
                lock icon (the absolute top-right placement overlapped the
                centred icon on narrow screens); pinned top-right from sm up. */}
            <div className="flex items-center gap-3 z-10 mb-3 sm:absolute sm:top-0 sm:right-0 sm:mb-0">
              <div className="bg-white px-3 py-1.5 rounded-xl border border-[#2B2926]/30 flex items-center gap-3 shadow-sm">
                <span className="text-[9px] font-semibold text-[#2B2926] uppercase tracking-widest leading-none">Accounts</span>
                <div className="flex items-baseline gap-1">
                  <span className="text-base font-semibold text-[#2B2926] leading-none">{totalConnected}</span>
                  {isUnlimited ? (
                    <span className="text-[#2B2926] leading-none flex items-center gap-0.5">
                      <span className="text-[10px] font-bold">/</span>
                      <span className="text-[15px] font-semibold leading-none relative -top-px">∞</span>
                    </span>
                  ) : (
                    <span className="text-[10px] font-bold text-[#2B2926] leading-none">/ {limit}</span>
                  )}
                </div>
              </div>
            </div>

            <div className="w-10 h-10 bg-emerald-50 rounded-xl flex items-center justify-center mb-4 border-2 border-emerald-100 shadow-sm">
              <div className="text-[#10B981]">
                <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="11" width="18" height="11" rx="2" ry="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" /><path d="M12 15v3" />
                </svg>
              </div>
            </div>

            <h1 className="font-['Lato',sans-serif] font-semibold tracking-tight mb-2 lg:whitespace-nowrap" style={{ color: '#575757', fontSize: '24px' }}>
              Securely Connect Your Social Media to Schedule and Publish Content!
            </h1>
            <p className="text-[#2B2926] font-['Lato',sans-serif] font-bold text-[11px] md:text-xs">
              Manage your brand identity seamlessly with <span className="text-[#F55600] font-black text-base md:text-lg tracking-tight inline-block px-1 align-baseline">Pipelyt</span>
            </p>
          </div>
        )}

        {/* Admin pending-reconnect-requests banner */}
        {isAdmin && pendingRequests.length > 0 && (
          <div className="mb-8 bg-amber-50 border-2 border-amber-200 rounded-3xl p-6 shadow-sm max-w-[2100px] mx-auto animate-in fade-in slide-in-from-top-4 duration-500">
            <div className="flex items-center gap-3 mb-4">
              <div className="p-2 bg-amber-100 rounded-xl">
                <Clock className="w-5 h-5 text-amber-600" />
              </div>
              <h4 className="text-base font-semibold text-amber-900">
                {pendingRequests.length} Pending Reconnect Request{pendingRequests.length === 1 ? '' : 's'}
              </h4>
              <button
                onClick={refreshPending}
                className="ml-auto flex items-center gap-2 text-[10px] font-semibold uppercase tracking-widest text-amber-700 hover:text-amber-900 bg-white px-3 py-1.5 rounded-xl border border-amber-200 shadow-sm transition-all active:scale-95"
              >
                <RefreshCw className="w-3 h-3" /> Refresh
              </button>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {pendingRequests.map(r => (
                <div key={r.id} className="flex items-center justify-between gap-4 text-xs bg-white/80 rounded-2xl px-4 py-3 border border-amber-100/50 shadow-sm hover:shadow-md transition-all">
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="w-8 h-8 rounded-full bg-amber-100 flex items-center justify-center text-amber-600 font-bold shrink-0">
                      {(r.requester?.full_name || r.requester?.email)?.[0].toUpperCase()}
                    </div>
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="font-semibold text-[#2B2926] truncate">{r.requester?.full_name || r.requester?.email}</span>
                        <span className="px-1.5 py-0.5 bg-slate-100 text-[#2B2926] text-[8px] font-semibold uppercase rounded tracking-tighter shrink-0">{r.platform}</span>
                      </div>
                      <p className="text-[10px] text-[#2B2926] truncate">{r.account_name}</p>
                      {r.reason && (
                        <div className="mt-1 text-[10px] text-amber-600 font-medium bg-amber-50/50 px-2 py-0.5 rounded italic truncate">
                          "{r.reason}"
                        </div>
                      )}
                    </div>
                  </div>
                  <button
                    onClick={() => handleConnect(r.platform)}
                    className="flex items-center gap-2 text-[9px] font-semibold uppercase tracking-widest bg-[#2B2926] text-white px-4 py-2 rounded-xl hover:bg-slate-900 shadow-lg shadow-black/10 transition-all active:scale-95 shrink-0 border border-[#2B2926]"
                  >
                    <RefreshCw className="w-3 h-3" /> Reconnect
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {flash && (
          <div className={`mb-8 max-w-6xl mx-auto rounded-2xl px-5 py-4 flex items-center gap-3 text-sm animate-in zoom-in-95 duration-300 shadow-lg border-2 ${flash.type === 'ok' ? 'bg-emerald-50 border-emerald-100 text-[#10B981]' : 'bg-red-50 border-red-100 text-red-700'}`}>
            <div className={`p-1.5 rounded-lg ${flash.type === 'ok' ? 'bg-emerald-100' : 'bg-red-100'}`}>
              {flash.type === 'ok' ? <CheckCircle2 className="w-5 h-5 text-[#10B981]" /> : <AlertTriangle className="w-5 h-5" />}
            </div>
            <span className="font-bold">{flash.message}</span>
            <button onClick={() => setFlash(null)} className="ml-auto p-1 hover:bg-[#2B2926]/5 rounded-lg transition-all"><X className="w-4 h-4" /></button>
          </div>
        )}

        {/* Connections Grid - Compact & Responsive */}
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 max-w-7xl mx-auto">
          {[...activePlatforms, ...soonPlatforms].map((key) => {
            const platform = key; // alias for consistency with user logic
            const meta = platformMeta[key];
            const accs = key === 'canva'
              ? (user?.canva_access_token ? [{ id: 'canva', name: 'Design Integration' }] : [])
              : (connections[key] || []);
            const isSoon = soonPlatforms.includes(key);
            const isConnected = accs.length > 0;

            return (
              <div key={key} className="flex flex-col group h-full transition-all duration-500">
                {/* Card Top Block - Glassmorphism & Tinted Background */}
                <div
                  className={`relative flex flex-col items-center justify-center p-3 rounded-t-xl border border-slate-800 flex-1 overflow-hidden transition-all duration-300 group-hover:shadow-2xl bg-white`}
                >
                  {/* Glass Border Overlay */}
                  <div className="absolute inset-0 border border-white/40 rounded-t-xl pointer-events-none" />

                  <div
                    className="w-14 h-14 rounded-xl flex items-center justify-center mb-2.5 transition-all duration-300 group-hover:scale-110 shrink-0"
                    style={{ color: meta.color }}
                  >
                    {/* Fixed 40×40 inner box so every icon (bitmap or SVG)
                        ends up the same visual size regardless of intrinsic
                        padding/aspect ratio inside its source asset. */}
                    <div className="w-10 h-10 flex items-center justify-center">
                      <meta.icon className="max-w-full max-h-full object-contain" />
                    </div>
                  </div>
                  <h3 className="font-['Lato',sans-serif] font-semibold tracking-tight text-center uppercase" style={{ color: '#575757', fontSize: '14px' }}>{meta.name}</h3>

                  {/* Connected Accounts Display */}
                  {isConnected && !isSoon && (
                    <div className="mt-2.5 flex flex-col items-center w-full animate-in fade-in slide-in-from-bottom-1">
                      <div
                        className="flex -space-x-1.5 mb-1.5 cursor-pointer hover:scale-105 transition-transform relative"
                        onClick={() => setManageModal({ isOpen: true, platform: key, accounts: accs })}
                      >
                        {accs.slice(0, 4).map((acc, i) => (
                          <div key={acc.id} className="relative">
                            <div className={`w-6 h-6 rounded-full border-2 border-white bg-slate-100 overflow-hidden shadow-sm relative ${acc.assigned_to ? 'ring-2 ring-orange-200' : ''}`} title={acc.name}>
                              {acc.profile_picture_url ? (
                                <img
                                  src={acc.profile_picture_url}
                                  className="w-full h-full object-cover"
                                  alt=""
                                  referrerPolicy="no-referrer"
                                  onError={(e) => {
                                    // Broken / expired profile picture URL
                                    // (common with Facebook CDN tokens). Hide
                                    // the <img> so the initial-letter fallback
                                    // underneath becomes visible.
                                    e.currentTarget.style.display = 'none';
                                  }}
                                />
                              ) : null}
                              <div className="absolute inset-0 -z-0 flex items-center justify-center text-[8px] font-bold text-[#2B2926] uppercase bg-slate-100">
                                {acc.name?.[0] || '?'}
                              </div>
                            </div>
                            {/* Tiny Team Badge on Avatar */}
                            {isAdmin && acc.assigned_to && (
                              <div className="absolute -top-1 -right-1 w-2.5 h-2.5 bg-[#F55600] rounded-full border border-white shadow-sm flex items-center justify-center" title={`Assigned to ${acc.assigned_to.full_name}`}>
                                <UserPlus className="w-1.5 h-1.5 text-white" />
                              </div>
                            )}
                          </div>
                        ))}
                        {accs.length > 4 && (
                          <div className="w-6 h-6 rounded-full border-2 border-white bg-[#F55600] flex items-center justify-center text-[7px] font-bold text-white shadow-sm ring-2 ring-transparent group-hover:ring-orange-100 transition-all">
                            +{accs.length - 4}
                          </div>
                        )}
                      </div>

                      <button
                        onClick={() => setManageModal({ isOpen: true, platform: key, accounts: accs })}
                        className="text-[9px] font-['Lato',sans-serif] font-semibold text-[#F55600] uppercase tracking-widest hover:underline flex items-center gap-1"
                      >
                        Manage {accs.length} account{accs.length !== 1 ? 's' : ''}
                      </button>
                    </div>
                  )}

                  {!isConnected && !isSoon && (
                    <div className="mt-2 text-center">
                      <p className="text-[10px] font-['Lato',sans-serif] text-[#2B2926] font-semibold uppercase tracking-wider">Available Now</p>
                    </div>
                  )}

                  {isSoon && (
                    <div
                      className="mt-2.5 px-3 py-1 rounded-full shadow-sm animate-pulse flex items-center gap-1.5 bg-white/20 border border-white/30"
                    >
                      <div className="w-1.5 h-1.5 rounded-full bg-current" style={{ color: meta.color }}></div>
                      <span className="text-[8px] font-['Lato',sans-serif] font-semibold uppercase tracking-widest leading-none text-[#2B2926]">Coming Soon</span>
                    </div>
                  )}
                </div>

                {/* Card Bottom Button - Full width colored for consistency */}
                {!isMember && (
                <button
                  onClick={() => !isSoon && handleConnect(key)}
                  disabled={isSoon}
                  className={`w-full py-2.5 rounded-b-xl text-[10px] font-semibold uppercase tracking-widest transition-all duration-300 relative overflow-hidden text-white shadow-lg shadow-black/5 border border-t-0 border-slate-800 ${isSoon
                    ? 'opacity-80'
                    : 'hover:brightness-110 active:scale-[0.98]'
                    }`}
                  style={{
                    backgroundColor: meta.color,
                  }}
                >
                  <span className="relative z-10">{isSoon ? 'Stay Tuned' : (isConnected ? 'Add More' : 'Connect Now')}</span>
                  <div className="absolute inset-0 bg-white/10 opacity-0 group-hover:opacity-100 transition-opacity" />
                </button>
                )}
              </div>
            );
          })}

        </div>

        {/* Footer Info */}
        <div className={(typeof hideHeader !== 'undefined' && hideHeader) ? "mt-8 text-center" : "mt-16 text-center"}>
          <p className="text-xs font-['Lato',sans-serif] text-[#2B2926] font-semibold flex items-center justify-center gap-2">
            <span className="w-1.5 h-1.5 bg-[#10B981] rounded-full"></span>
            Your credentials are encrypted and stored securely. We never share your data.
          </p>
        </div>
      </div>

      {/* Account Management Modal (Pop-up) */}
      {manageModal.isOpen && (
        // M2 fix — raised z-[100] → z-[220] so this modal renders ABOVE
        // the ForceConnectModal (z-[200]) when a user with a stale
        // dangling account opens the manage popup from inside the force
        // modal. Previously it rendered behind, effectively invisible.
        <div className="fixed inset-0 z-[220] flex items-center justify-center p-4 animate-in fade-in duration-200">
          {/* Backdrop — kept as `absolute inset-0` but WITH a bg so it
              actually renders as a dim, and z-0 so the content div below
              can outrank it with an explicit z-index. Previously the
              backdrop had NO position/z sibling to compete with, and
              because absolute-positioned elements paint above normal-flow
              siblings, clicks on the "Add Another Account" button (and any
              other non-z-indexed interactive element inside the content
              div) were intercepted by this invisible backdrop and just
              closed the modal without ever firing the button's onClick.
              Fixed by adding `relative z-10` to the content div so it
              sits above the backdrop for both paint AND hit-testing. */}
          <div className="absolute inset-0 z-0" onClick={() => setManageModal({ isOpen: false, platform: null, accounts: [] })} />
          <div className="relative z-10 bg-white rounded-3xl w-full max-w-md shadow-2xl overflow-hidden border border-[#2B2926]/30" onClick={e => e.stopPropagation()}>
            <div className="p-6 border-b border-slate-50 flex items-center justify-between bg-slate-50/50">
              <div className="flex items-center gap-3">
                <div
                  className="w-10 h-10 rounded-xl flex items-center justify-center"
                  style={{ color: platformMeta[manageModal.platform]?.color }}
                >
                  {React.createElement(platformMeta[manageModal.platform]?.icon, { className: "w-6 h-6" })}
                </div>
                <div>
                  <h2 className="text-sm font-semibold text-[#2B2926]">Manage {platformMeta[manageModal.platform]?.name}</h2>
                  <p className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest">{manageModal.accounts.length} Accounts Connected</p>
                </div>
              </div>
              <button
                onClick={() => setManageModal({ ...manageModal, isOpen: false })}
                className="p-2 hover:bg-white rounded-xl text-[#2B2926] hover:text-[#2B2926] transition-all border border-transparent hover:border-[#2B2926]/30"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="p-4 max-h-[60vh] overflow-y-auto space-y-1.5 custom-scrollbar">
              {manageModal.accounts.map((acc) => (
                <div key={acc.id} className="flex items-center justify-between p-1.5 bg-white rounded-xl border border-slate-50 hover:border-orange-200 transition-all shadow-sm group">
                  <div className="flex items-center gap-2.5">
                    <div className="relative w-8 h-8 rounded-full bg-slate-200 border-2 border-white overflow-hidden shadow-sm shrink-0">
                      {acc.profile_picture_url ? (
                        <img
                          src={acc.profile_picture_url}
                          className="w-full h-full object-cover relative z-10"
                          alt=""
                          referrerPolicy="no-referrer"
                          onError={(e) => { e.currentTarget.style.display = 'none'; }}
                        />
                      ) : null}
                      <div className="absolute inset-0 flex items-center justify-center text-[10px] font-bold text-[#2B2926] uppercase bg-slate-200">
                        {acc.name?.[0] || '?'}
                      </div>
                    </div>
                    <div className="min-w-0">
                      <p className="text-[11px] font-semibold text-[#2B2926] truncate max-w-[200px]">{acc.name}</p>
                      {showIds && <p className="text-[8px] font-mono text-[#2B2926] truncate opacity-0 group-hover:opacity-100 transition-opacity">ID: {acc.id.slice(0, 10)}...</p>}
                    </div>
                  </div>
                  <div className="flex gap-1.5 items-center">
                    {/* Admin Team Assignment Button */}
                    {isAdmin && teamMembers.length > 0 && (
                      <AssignDropdown
                        acc={acc}
                        isOpen={assignOpenFor === acc.db_id}
                        onToggle={() => setAssignOpenFor(assignOpenFor === acc.db_id ? null : acc.db_id)}
                        onClose={() => setAssignOpenFor(null)}
                        teamMembers={teamMembers}
                        handleAssign={handleAssign}
                      />
                    )}

                    {/* Member Request Reconnect or Admin Refresh */}
                    {isMember && acc.owner_kind === 'admin' ? (
                      <button
                        onClick={() => handleRequestReconnect(acc.db_id, manageModal.platform)}
                        className="p-1.5 bg-amber-50 text-amber-600 hover:bg-amber-100 rounded-lg border border-amber-200 shadow-sm transition-all"
                        title="Request reconnect from your admin"
                      >
                        <RefreshCw className="w-3 h-3" strokeWidth={3} />
                      </button>
                    ) : (
                      (manageModal.platform === 'facebook' || manageModal.platform === 'instagram') && !isMember && (
                        <button
                          onClick={() => handleResetConnection(manageModal.platform)}
                          className="p-1.5 bg-white text-[#F55600] hover:text-orange-600 rounded-lg border border-[#2B2926]/30 shadow-sm transition-all"
                          title="Refresh Token"
                        >
                          <RefreshCw className="w-3 h-3" />
                        </button>
                      )
                    )}
                    <button
                      onClick={() => {
                        if (manageModal.platform === 'canva') handleDisconnectCanva();
                        else setConfirmModal({ isOpen: true, platform: manageModal.platform, acc });
                      }}
                      className="pointer-events-auto relative z-[60] p-2.5 bg-[#F55600]/5 text-[#F55600] hover:bg-[#F55600]/10 border border-[#F55600]/20 rounded-xl shadow-sm transition-all hover:scale-110 active:scale-95 group/del cursor-pointer"
                      title="Remove Account"
                    >
                      <Trash2 className="w-4 h-4 pointer-events-none group-hover/del:rotate-6 transition-transform" />
                    </button>

                  </div>
                </div>
              ))}
            </div>

            <div className="p-4 bg-white border-t border-slate-50">
              <button
                onClick={() => {
                  // Fire the OAuth popup FIRST while the click is still a
                  // trusted user gesture. Closing the modal FIRST (which
                  // was the old order) caused React to begin unmounting
                  // the modal DOM in the same event tick, and Chrome/
                  // Safari then flag the subsequent window.open() as
                  // non-user-initiated → popup blocked. Same-tick
                  // reordering with a captured `platform` avoids the
                  // stale-state race.
                  const platform = manageModal.platform;
                  handleConnect(platform);
                  setManageModal({ ...manageModal, isOpen: false });
                }}
                className="w-full py-2.5 bg-white border-2 border-[#F55600] rounded-xl text-[11px] font-semibold text-[#2B2926] hover:bg-orange-50 transition-all flex items-center justify-center gap-2 shadow-sm"
              >
                <Plus className="w-4 h-4 text-[#F55600]" />
                Add Another Account
              </button>
            </div>
          </div>
        </div>
      )}

      <AlertModal
        isOpen={confirmModal.isOpen}
        onClose={() => setConfirmModal({ ...confirmModal, isOpen: false })}
        title="Pipelyt"
        message={`Remove ${confirmModal.platform} account "${confirmModal.acc?.name}"?`}
        actionText="Remove"
        icon={AlertTriangle}
        onAction={() => {
          if (confirmModal.platform && confirmModal.acc) {
            handleDisconnectAccount(confirmModal.platform, confirmModal.acc.id);
            setConfirmModal({ ...confirmModal, isOpen: false });
            // Refresh manage modal if open
            if (manageModal.isOpen) {
              setManageModal(prev => ({
                ...prev,
                accounts: prev.accounts.filter(a => a.id !== confirmModal.acc.id)
              }));
            }
          }
        }}
      />
    </div>
  );
};

export default Connections;
