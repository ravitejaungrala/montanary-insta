import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  FiUsers, FiPlus, FiRefreshCw, FiX, FiEyeOff, FiTrash2,
  FiCheckCircle, FiAlertCircle, FiEdit2, FiMail, FiTag, FiSend, FiClock, FiCopy,
  FiBriefcase, FiMapPin, FiHash, FiChevronDown, FiLink, FiLinkedin, FiFacebook,
  FiInstagram,
} from 'react-icons/fi';
import { Country, State, City } from 'country-state-city';
import XIcon from '../components/icons/XIcon';
import ConfirmModal from '../components/ConfirmModal';
import BrandSelect from '../components/BrandSelect';
import { grantableRoles, isMasterAdmin, ROLE_LABELS, roleLabel } from '../lib/permissions';

// ConnectionPlatformIcon — small platform indicator next to each
// pre-assignable connection in the Invite modal so admins can tell
// e.g. "LinkedIn — Z-Ninth" apart from "X — Z-Ninth" instead of seeing
// four identical "Z-Ninth" rows.
const ConnectionPlatformIcon = ({ platform }) => {
  const p = (platform || '').toLowerCase();
  const cls = 'w-3.5 h-3.5 shrink-0';
  if (p === 'linkedin')  return <FiLinkedin className={cls} style={{ color: '#0A66C2' }} />;
  if (p === 'facebook')  return <FiFacebook className={cls} style={{ color: '#1877F2' }} />;
  if (p === 'instagram') return <FiInstagram className={cls} style={{ color: '#E1306C' }} />;
  if (p === 'twitter' || p === 'x') return <XIcon className={cls} />;
  return <FiLink className={cls} style={{ color: '#2B2926' }} />;
};

// Module-level cache so the Team table survives navigations. Without this
// every re-mount (sidebar click → Analytics → Team) showed the big orange
// spinner for 300–800 ms even though the data hadn't changed. Now the
// cached rows render instantly and a silent background refetch updates
// them if anything changed server-side. Invalidated by any mutation
// below (invite, disable, enable, remove) so stale reads are bounded.
//
// Hydrated from localStorage on module load so even a fresh browser
// session paints the page from disk instead of staring at the
// "Retrieving Team Roster…" spinner for several seconds.
const _TEAM_LS_KEY = 'pipelyt_team_cache_v1';
const _TEAM_MAX_AGE_MS = 60 * 60 * 1000;
let _teamCache = (() => {
  try {
    const raw = localStorage.getItem(_TEAM_LS_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    if (typeof parsed.fetchedAt !== 'number') return null;
    if (Date.now() - parsed.fetchedAt > _TEAM_MAX_AGE_MS) return null;
    return parsed.data || null;
  } catch { return null; }
})();
const _persistTeamCache = (data) => {
  try {
    localStorage.setItem(_TEAM_LS_KEY, JSON.stringify({ data, fetchedAt: Date.now() }));
  } catch { /* quota — ignore */ }
};

/**
 * Team Members — invite-flow version.
 *
 * Admin sends an email invite; the invitee clicks through to set their own
 * password + name via /accept-invite. The table here shows both accepted
 * members and pending invites, discriminated by row.kind ('member' | 'invite').
 */
const Team = ({ authAxios, user }) => {
  // Seed state from the module cache so the table is instantly visible
  // on re-mount. Only show the initial spinner when there's truly nothing
  // to display (first-ever load this session).
  const [data, setData] = useState(_teamCache);
  const [loading, setLoading] = useState(_teamCache === null);
  const [error, setError] = useState(null);
  const [addOpen, setAddOpen] = useState(false);
  const [flash, setFlash] = useState(null); // { type: 'ok'|'err', message }
  // Invite-sent success popup — shown center-screen after a successful
  // invite so the admin gets an unmissable confirmation. The inline
  // banner at top of the page is still shown afterwards as a soft echo.
  const [inviteSuccess, setInviteSuccess] = useState(null); // { email, emailSent, acceptUrl }
  const [confirmModal, setConfirmModal] = useState({ isOpen: false, title: '', message: '', onConfirm: () => {} });

  const confirmAction = (title, message, action) => {
    setConfirmModal({
      isOpen: true,
      title,
      message,
      onConfirm: () => {
        action();
        setConfirmModal(prev => ({ ...prev, isOpen: false }));
      }
    });
  };

  // Auto-dismiss flash messages after 3.5s. The inline banner under the
  // page header is still rendered, but the bottom-right toast also reads
  // off this same state, so a single setFlash() drives both notifiers and
  // they fade out together.
  useEffect(() => {
    if (!flash) return;
    const t = setTimeout(() => setFlash(null), 3500);
    return () => clearTimeout(t);
  }, [flash]);

  const load = useCallback(async () => {
    // Only show the full-page "Retrieving Team Roster…" spinner when
    // we have zero data to render. With cached rows in memory or
    // localStorage, the page paints instantly and the network refetch
    // runs silently in the background — no spinner flash on every
    // navigation back to Members.
    if (!_teamCache) setLoading(true);
    setError(null);
    try {
      const res = await authAxios.get('/team/members');
      _teamCache = res.data;
      _persistTeamCache(res.data);
      setData(res.data);
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || 'Failed to load team');
    } finally {
      setLoading(false);
    }
  }, [authAxios]);

  useEffect(() => { load(); }, [load]);

  const rows = data?.members || [];
  const seats = data?.seats || { used: 0, cap: 0 };
  const brands = data?.available_dna_products || [];
  const seatCapDisplay = seats.cap >= 999999 ? '∞' : seats.cap;
  const seatsFull = seats.cap < 999999 && seats.used >= seats.cap;
  const usedSeats = Math.max(seats.used || 0, rows.length);

  return (
    <div className="bg-white min-h-[90vh] px-2 md:px-4 lg:px-6 pt-4 pb-8 space-y-4 animate-in fade-in duration-500">
      <div className="flex flex-col xl:flex-row xl:items-center justify-between gap-2 pb-2 border-b-2 border-slate-200">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-3xl font-black tracking-tight">
              <span className="text-[#F55600]">Members</span>
            </h1>
          </div>
          {/* Refresh — sits in the title row on mobile; the desktop copy
              lives in the actions group below (hidden xl:flex). */}
          <button
            onClick={load}
            disabled={loading}
            className="xl:hidden shrink-0 flex items-center px-3 py-2 bg-white border-2 border-slate-300 rounded-xl text-xs font-black hover:border-[#F55600]/30 text-[#2B2926] transition-all"
          >
            <FiRefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
        <div className="flex items-center gap-3">
          <div className="bg-white border border-[#2B2926]/20 rounded-xl px-4 py-2 flex items-center gap-2 shadow-sm">
            <FiUsers className="text-[#F55600]" size={16} />
            <span className="text-[10px] font-bold text-[#2B2926] uppercase tracking-[0.14em] leading-none">Seats</span>
            <span className="text-sm font-bold text-[#2B2926]">
              {usedSeats} / {seatCapDisplay}
            </span>
          </div>
          <button
            onClick={() => setAddOpen(true)}
            disabled={seatsFull || brands.length === 0}
            className="flex items-center gap-2 bg-[#F55600] text-white px-4 py-2 rounded-xl text-xs font-black uppercase tracking-widest hover:bg-[#F55600] shadow-md disabled:opacity-40 disabled:cursor-not-allowed transition-all active:scale-95"
          >
            Invite Member
          </button>
          <button
            onClick={load}
            disabled={loading}
            className="hidden xl:flex items-center gap-2 px-3 py-2 bg-white border-2 border-slate-300 rounded-xl text-xs font-black hover:border-[#F55600]/30 text-[#2B2926] transition-all"
          >
            <FiRefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {flash && (
        <div className={`text-xs rounded-xl px-4 py-3 flex items-center gap-2 ${flash.type === 'ok' ? 'bg-emerald-50 border border-emerald-200 text-[#10B981]' : 'bg-red-50 border border-red-200 text-red-700'}`}>
          {flash.type === 'ok' ? <FiCheckCircle /> : <FiAlertCircle />}
          <span>{flash.message}</span>
          <button onClick={() => setFlash(null)} className="ml-auto text-slate-600 hover:text-[#2B2926]"><FiX size={12} /></button>
        </div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 text-xs rounded-xl px-4 py-3 flex items-center gap-2">
          <FiAlertCircle /> {error}
        </div>
      )}

      {brands.length === 0 && !loading && (
        <div className="bg-amber-50 border border-amber-200 text-amber-700 text-xs rounded-xl px-4 py-3">
          Add at least one product in <strong>Settings → Business DNA</strong> before inviting team members.
        </div>
      )}

      <div className="bg-white rounded-2xl border border-[#2B2926]/15 shadow-sm overflow-hidden">
        <div className="max-h-[60vh] overflow-y-auto relative">
          <table className="w-full text-left border-separate border-spacing-0">
            <thead>
              <tr className="bg-[#2B2926]/[0.03] text-[#2B2926] text-[11px] uppercase font-bold tracking-[0.14em]">
                <th className="sticky top-0 bg-slate-50 px-6 py-4 border-b border-slate-100">Email</th>
                <th className="sticky top-0 bg-slate-50 px-4 py-4 border-b border-slate-100">Name</th>
                <th className="sticky top-0 bg-slate-50 px-4 py-4 border-b border-slate-100">Role</th>
                <th className="sticky top-0 bg-slate-50 px-4 py-4 border-b border-slate-100">Assigned Brand</th>
                <th className="sticky top-0 bg-slate-50 px-4 py-4 border-b border-slate-100 text-center">Status</th>
                <th className="sticky top-0 bg-slate-50 px-4 py-4 border-b border-slate-100 text-left">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {loading && rows.length === 0 && (
                <tr>
                  <td colSpan="6" className="text-center py-20">
                    <div className="flex flex-col items-center gap-4">
                      <div className="relative">
                        <div className="w-12 h-12 rounded-full border-4 border-slate-100 border-t-[#F55600] animate-spin" />
                        <FiUsers className="absolute inset-0 m-auto text-[#F55600]/40" size={20} />
                      </div>
                      <p className="text-[10px] font-black text-slate-400 uppercase tracking-[0.2em] animate-pulse">
                        Retrieving Team Roster...
                      </p>
                    </div>
                  </td>
                </tr>
              )}
              {!loading && rows.length === 0 && (
                <tr><td colSpan="6" className="px-8 py-16 text-center text-slate-600 italic font-bold">
                  No members or pending invites yet. Click "Invite Member" to send the first one.
                </td></tr>
              )}
              {rows.map((r) => (
                r.kind === 'invite' ? (
                  <InviteRow key={`inv-${r.id}`} invite={r} authAxios={authAxios}
                    onChanged={load} onFlash={setFlash}
                    confirmAction={confirmAction} />
                ) : (
                  <MemberRow key={`mem-${r.id}`} member={r} brands={brands}
                    authAxios={authAxios} onChanged={load} onFlash={setFlash}
                    confirmAction={confirmAction} user={user} />
                )
              ))}
            </tbody>
          </table>
          
          {/* Background Refresh Indicator (A-15 style) */}
          {loading && rows.length > 0 && (
            <div className="absolute top-0 left-0 right-0 h-1 bg-orange-100 overflow-hidden z-20">
              <div className="h-full bg-[#F55600] animate-progress-fast" />
            </div>
          )}
        </div>
      </div>

      {addOpen && (
        <InviteMemberModal
          brands={brands}
          authAxios={authAxios}
          user={user}
          onClose={() => setAddOpen(false)}
          onCreated={(emailSent, acceptUrl, email) => {
            setAddOpen(false);
            setInviteSuccess({ email, emailSent, acceptUrl });
            setFlash({
              type: 'ok',
              message: emailSent
                ? 'Invite sent. The member will receive an email with the accept link.'
                : `Invite created but email could not be sent. Copy this link to share manually: ${acceptUrl}`,
            });
            load();
          }}
        />
      )}

      {inviteSuccess && (
        <InviteSuccessPopup
          email={inviteSuccess.email}
          emailSent={inviteSuccess.emailSent}
          acceptUrl={inviteSuccess.acceptUrl}
          onClose={() => setInviteSuccess(null)}
        />
      )}

      <ConfirmModal
        isOpen={confirmModal.isOpen}
        title={confirmModal.title}
        message={confirmModal.message}
        onConfirm={confirmModal.onConfirm}
        onCancel={() => setConfirmModal((prev) => ({ ...prev, isOpen: false }))}
      />

      {/* Bottom-right toast — fires on every flash event (invite sent,
          resent, revoked, member disabled/enabled/deleted) so the admin
          gets a peripheral confirmation even after the inline banner has
          scrolled out of view. Auto-dismisses 3.5s after the flash is set
          (see the useEffect above). */}
      {flash && (
        <div className="fixed bottom-6 right-6 z-[200] pointer-events-none">
          <div
            className={`pointer-events-auto flex items-center gap-3 px-4 py-3 rounded-2xl shadow-2xl border-2 max-w-[360px] min-w-[260px] animate-in slide-in-from-right-4 fade-in duration-300 ${
              flash.type === 'ok'
                ? 'bg-white border-[#10B981]/40'
                : 'bg-white border-red-300'
            }`}
          >
            <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 ${
              flash.type === 'ok' ? 'bg-[#10B981]/10' : 'bg-red-50'
            }`}>
              {flash.type === 'ok'
                ? <FiCheckCircle className="w-5 h-5 text-[#10B981]" />
                : <FiAlertCircle className="w-5 h-5 text-red-600" />}
            </div>
            <div className="flex-1 min-w-0">
              <div className={`text-[10px] font-black uppercase tracking-widest ${
                flash.type === 'ok' ? 'text-[#10B981]' : 'text-red-700'
              }`}>
                {flash.type === 'ok' ? 'Success' : 'Error'}
              </div>
              <div className="text-[12px] font-bold text-[#2B2926] mt-0.5 break-words">{flash.message}</div>
            </div>
            <button
              onClick={() => setFlash(null)}
              className="text-[#2B2926]/40 hover:text-[#2B2926]/80 shrink-0"
              aria-label="Dismiss"
            >
              <FiX size={14} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

// InviteSuccessPopup — center-screen confirmation shown immediately after a
// successful /team/invites POST. Auto-dismisses after 3.5s but the admin can
// also close it manually. When the email send failed we fall back to showing
// the accept-link with a Copy button so the admin can share it manually.
const InviteSuccessPopup = ({ email, emailSent, acceptUrl, onClose }) => {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!emailSent) return; // keep open if there's a fallback link to copy
    const t = setTimeout(onClose, 3500);
    return () => clearTimeout(t);
  }, [emailSent, onClose]);

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(acceptUrl || '');
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard blocked — user can still see the URL */ }
  };

  return (
    <div className="fixed inset-0 z-[110] flex items-center justify-center p-4 pointer-events-none">
      <div className="absolute inset-0 pointer-events-auto" onClick={onClose} />
      <div
        role="dialog"
        aria-modal="true"
        className="relative bg-white w-full max-w-sm rounded-3xl shadow-2xl border-2 border-[#10B981]/40 overflow-hidden animate-in zoom-in-95 fade-in duration-300"
      >
        <div className="p-6 flex flex-col items-center text-center">
          <div className="w-14 h-14 rounded-full bg-[#10B981]/10 border-2 border-[#10B981]/30 flex items-center justify-center mb-3 animate-in zoom-in-50 duration-500">
            <FiCheckCircle className="w-8 h-8 text-[#10B981]" />
          </div>
          <h4 className="font-black text-[#2B2926] text-base">
            {emailSent ? 'Invite Sent!' : 'Invite Created'}
          </h4>
          <p className="text-[12px] text-[#2B2926]/65 mt-1 max-w-[280px]">
            {emailSent
              ? <>The accept link was emailed to <span className="font-bold text-[#2B2926]">{email}</span>.</>
              : <>Email delivery failed — copy the link below and share it with <span className="font-bold text-[#2B2926]">{email}</span> manually.</>
            }
          </p>
          {!emailSent && acceptUrl && (
            <div className="mt-4 w-full">
              <div className="text-[9px] font-black uppercase tracking-widest text-[#2B2926]/60 mb-1 text-left">Accept Link</div>
              <div className="flex items-center gap-2 bg-[#2B2926]/5 border border-[#2B2926]/10 rounded-xl px-2 py-2">
                <span className="flex-1 text-[10px] text-[#2B2926]/70 truncate text-left" title={acceptUrl}>{acceptUrl}</span>
                <button
                  type="button"
                  onClick={copyLink}
                  className="text-[10px] font-black uppercase tracking-widest px-3 py-1.5 rounded-lg bg-[#F55600] text-white hover:opacity-90 shrink-0"
                >
                  {copied ? 'Copied' : 'Copy'}
                </button>
              </div>
            </div>
          )}
        </div>
        <div className="px-6 pb-5 flex justify-center">
          <button
            type="button"
            onClick={onClose}
            className="text-[10px] font-black uppercase tracking-widest px-5 py-2 rounded-xl bg-[#F55600] text-white hover:opacity-90 shadow-md"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
};

const MemberRow = ({ member, brands, authAxios, onChanged, onFlash, confirmAction, user }) => {
  const [busy, setBusy] = useState(false);
  const [editBrand, setEditBrand] = useState(false);
  const [editFull, setEditFull] = useState(false);
  // Franchisees have role='master_admin' (they own their workspace), so the
  // standard grantable-role check rejects them even for their brand owner.
  // Allow Master to manage franchisees despite the role gap — backend
  // enforces the same allowance via franchise_of_id match.
  const isFranchise = member.kind === 'franchise' || member.user_type === 'franchise';
  // A viewer may only manage members BELOW their role: Master → Admin/User,
  // Admin → User only. Hide all actions on members they can't manage (the
  // backend enforces the same rule — this keeps the UI honest).
  const canManage = isFranchise || grantableRoles(user).includes(member.role);
  const [brandIds, setBrandIds] = useState(
    Array.isArray(member.assigned_dna_product_ids) && member.assigned_dna_product_ids.length > 0
      ? member.assigned_dna_product_ids
      : (member.assigned_dna_product_id ? [member.assigned_dna_product_id] : [])
  );
  const toggleEditBrand = (pid) => {
    setBrandIds((prev) => prev.includes(pid) ? prev.filter(x => x !== pid) : [...prev, pid]);
  };

  const call = async (fn, okMsg) => {
    setBusy(true);
    try {
      await fn();
      if (okMsg) onFlash({ type: 'ok', message: okMsg });
      await onChanged();
    } catch (err) {
      onFlash({ type: 'err', message: err?.response?.data?.detail || err?.message || 'Action failed' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <React.Fragment>
      <tr className="hover:bg-slate-50/50 transition-colors">
        <td className="px-6 py-4 text-[13px] font-semibold text-[#2B2926]">
          <div className="flex items-center gap-2 flex-wrap">
            <span>{member.email}</span>
            {(member.kind === 'franchise' || member.user_type === 'franchise') && (
              <span className="inline-flex items-center text-[9px] font-black uppercase tracking-[0.12em] bg-[#F55600]/10 text-[#F55600] border border-[#F55600]/30 rounded-full px-2 py-0.5">
                Franchise
              </span>
            )}
          </div>
        </td>
        <td className="px-4 py-4 text-[13px] font-semibold text-[#2B2926]">
          {member.full_name || <span className="text-slate-400 italic font-normal">—</span>}
        </td>
        <td className="px-4 py-4 text-xs">
          <span className="inline-flex items-center text-[10px] font-bold uppercase tracking-[0.1em] bg-[#2B2926]/[0.04] border border-[#2B2926]/15 text-[#2B2926] rounded-full px-2.5 py-0.5">
            {roleLabel(member.role)}
          </span>
        </td>
        <td className="px-4 py-4 text-xs">
          {editBrand ? (
            <div className="flex flex-col gap-1.5">
              <div className="px-3 py-1 text-[8px] font-black uppercase tracking-widest text-[#2B2926] border-b border-slate-50 mb-1">Assign To</div>
              <div className="flex flex-wrap gap-1 max-w-[280px]">
                {brands.map((b) => {
                  const checked = brandIds.includes(b.product_id);
                  return (
                    <label
                      key={b.product_id}
                      className={`flex items-center gap-1 text-[10px] px-2 py-1 rounded border cursor-pointer ${checked ? 'bg-orange-50 border-orange-200 text-[#F55600] font-black' : 'bg-slate-50 border-slate-100 text-slate-600'}`}
                    >
                      <input type="checkbox" checked={checked}
                        onChange={() => toggleEditBrand(b.product_id)}
                        className="accent-[#F55600]" />
                      {b.product_name}
                    </label>
                  );
                })}
              </div>
              <div className="flex items-center gap-2">
                <button disabled={busy || brandIds.length === 0}
                  onClick={() => call(
                    () => authAxios.put(`/team/members/${member.id}`, { assigned_dna_product_ids: brandIds }),
                    'Brands reassigned',
                  ).then(() => setEditBrand(false))}
                  className="text-[10px] font-black uppercase text-white bg-[#F55600] px-2.5 py-1 rounded-md disabled:opacity-40">
                  Save
                </button>
                <button
                  onClick={() => {
                    setBrandIds(member.assigned_dna_product_ids || (member.assigned_dna_product_id ? [member.assigned_dna_product_id] : []));
                    setEditBrand(false);
                  }}
                  className="text-[10px] font-black uppercase text-slate-600 hover:text-[#2B2926]">
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap gap-1">
              {(member.assigned_brands && member.assigned_brands.length > 0 ? member.assigned_brands : [{ id: null, name: member.assigned_brand_name || 'Unassigned' }]).map((b, i) => (
                <span key={b.id || i} className="inline-flex items-center gap-1 bg-white border border-[#2B2926]/25 text-[#F55600] rounded-full px-2.5 py-0.5 font-bold text-[10px]">
                  <FiTag size={9} /> {b.name}
                </span>
              ))}
            </div>
          )}
        </td>
        <td className="px-4 py-4 text-center">
          {member.disabled ? (
            <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-[0.1em] bg-[#2B2926] text-white rounded-full px-2 py-0.5">
              <FiEyeOff size={10} /> Disabled
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-[0.1em] bg-white border border-[#065F46]/55 text-[#065F46] rounded-full px-2.5 py-0.5">
              <FiCheckCircle size={10} /> Active
            </span>
          )}
        </td>
        <td className="px-4 py-4 text-left">
          {!canManage ? (
            <span className="text-slate-300 text-xs">—</span>
          ) : (
          <div className="inline-flex items-center gap-2">
            {!editBrand && (
              <>
                <button onClick={() => setEditFull(true)} disabled={busy}
                  className="p-1.5 rounded-lg hover:bg-orange-50 text-slate-600 hover:text-[#F55600] transition-colors"
                  title="Edit profile (name, company, location)">
                  <FiEdit2 size={14} />
                </button>
                <button onClick={() => setEditBrand(true)} disabled={busy}
                  className="p-1.5 rounded-lg hover:bg-orange-50 text-slate-600 hover:text-[#F55600] transition-colors"
                  title="Reassign brand">
                  <FiTag size={14} />
                </button>
              </>
            )}
            {member.disabled ? (
              <button disabled={busy}
                onClick={() => call(
                  () => authAxios.put(`/team/members/${member.id}`, { disabled: false }),
                  isFranchise ? 'Franchisee re-enabled' : 'Member re-enabled',
                )}
                className="text-[10px] font-bold uppercase tracking-[0.1em] text-emerald-700 bg-emerald-50 px-3 py-1.5 rounded-md hover:bg-emerald-100">
                Enable
              </button>
            ) : (
              <button disabled={busy}
                onClick={() => {
                  confirmAction(
                    isFranchise ? "Disable Franchisee?" : "Disable Member?",
                    isFranchise
                      ? `Disable ${member.email}? They won't be able to log in or access your brand DNA. Their franchise link stays intact — Enable restores access.`
                      : `Are you sure you want to disable ${member.email}? They won't be able to log in, but their content will be preserved.`,
                    () => call(
                      () => authAxios.delete(`/team/members/${member.id}`),
                      isFranchise ? 'Franchisee disabled' : 'Member disabled',
                    )
                  );
                }}
                className="text-[10px] font-bold uppercase tracking-[0.1em] text-white bg-[#2B2926] px-3 py-1.5 rounded-md hover:bg-[#1A1816] transition-colors"
                title="Soft-disable (blocks login, preserves content)">
                Disable
              </button>
            )}
            <button disabled={busy}
              onClick={() => {
                confirmAction(
                  isFranchise ? "Remove from Franchise Network?" : "Remove Member Permanently?",
                  isFranchise
                    ? `Remove ${member.email} from your franchise network? Their brand DNA access is revoked and they leave the roster — but they KEEP their own account, subscription, and content (they operate independently after this).`
                    : `This will PERMANENTLY delete the account for ${member.email}. Any social accounts delegated to them are returned to your pool, but their posts, drafts, and schedules stay with you for historical records. This action cannot be undone.`,
                  () => call(
                    () => authAxios.post(`/team/members/${member.id}/remove`),
                    isFranchise ? 'Franchisee removed from network' : 'Member removed permanently',
                  )
                );
              }}
              className="p-1.5 rounded-lg hover:bg-red-50 text-slate-600 hover:text-red-500 transition-colors"
              title={isFranchise ? "Remove from franchise network (they keep their standalone account)" : "Remove permanently (hard delete)"}>
              <FiTrash2 size={14} />
            </button>
          </div>
          )}
        </td>
      </tr>
      {editFull && (
        <tr className="bg-slate-50/30">
          <EditMemberModal
            member={member}
            authAxios={authAxios}
            user={user}
            onClose={() => setEditFull(false)}
            onSaved={() => { setEditFull(false); onChanged(); }}
          />
        </tr>
      )}
    </React.Fragment>
  );
};

const InviteRow = ({ invite, authAxios, onChanged, onFlash, confirmAction }) => {
  const [busy, setBusy] = useState(false);
  const call = async (fn, okMsg) => {
    setBusy(true);
    try {
      const res = await fn();
      if (okMsg) onFlash({ type: 'ok', message: okMsg });
      await onChanged();
      return res;
    } catch (err) {
      onFlash({ type: 'err', message: err?.response?.data?.detail || err?.message || 'Action failed' });
    } finally {
      setBusy(false);
    }
  };
  return (
    <tr className="hover:bg-orange-50/30 transition-colors">
      <td className="px-6 py-4 text-xs font-bold text-[#2B2926]">{invite.email}</td>
      <td className="px-4 py-4 text-xs">
        {invite.full_name ? (
          <span className="font-black text-[#2B2926]">
            {invite.full_name}
          </span>
        ) : (
          <span className="text-slate-400 italic font-normal">Not signed up yet</span>
        )}
      </td>
      <td className="px-4 py-4 text-xs">
        <span className="inline-flex items-center text-[10px] font-bold uppercase tracking-[0.1em] bg-[#2B2926]/[0.04] border border-[#2B2926]/15 text-[#2B2926] rounded-full px-2.5 py-0.5">
          {roleLabel(invite.role)}
        </span>
      </td>
      <td className="px-4 py-4 text-xs">
        <div className="flex flex-wrap gap-1">
          {(invite.assigned_brands && invite.assigned_brands.length > 0
            ? invite.assigned_brands
            : [{ id: null, name: invite.assigned_brand_name || 'Unassigned' }]
          ).map((b, i) => (
            <span key={b.id || i} className="inline-flex items-center gap-1 bg-white border border-[#2B2926]/25 text-[#F55600] rounded-full px-2.5 py-0.5 font-bold text-[10px]">
              <FiTag size={9} /> {b.name}
            </span>
          ))}
        </div>
      </td>
      <td className="px-4 py-4 text-center">
        <span className="inline-flex items-center gap-1 text-[10px] font-black uppercase bg-amber-50 text-amber-600 rounded-full px-2 py-0.5">
          <FiClock size={10} /> Pending
        </span>
      </td>
      <td className="px-4 py-4 text-left">
        <div className="inline-flex items-center gap-2">
          <button disabled={busy}
            onClick={async () => {
              const res = await call(
                () => authAxios.post(`/team/invites/${invite.id}/resend`),
                'Invite resent',
              );
              if (res?.data?.accept_url) {
                try { await navigator.clipboard.writeText(res.data.accept_url); } catch { }
              }
            }}
            className="text-[10px] font-black uppercase flex items-center gap-1 text-[#F55600] bg-orange-50 px-2.5 py-1 rounded-md hover:bg-orange-100"
            title="Resend email + rotate link">
            <FiSend size={11} /> Resend
          </button>
          <button disabled={busy}
            onClick={() => {
              confirmAction(
                "Revoke Invite?",
                `Are you sure you want to revoke the invite for ${invite.email}? The existing link will stop working immediately.`,
                () => call(
                  () => authAxios.delete(`/team/invites/${invite.id}`),
                  'Invite revoked',
                )
              );
            }}
            className="p-1.5 rounded-lg hover:bg-red-50 text-slate-500 hover:text-red-500 transition-colors"
            title="Revoke invite">
            <FiTrash2 size={14} />
          </button>
        </div>
      </td>
    </tr>
  );
};

const InviteMemberModal = ({ brands, authAxios, user, onClose, onCreated }) => {
  // Roles this inviter may grant (Master Admin → Admin|User, Admin → User only).
  const grantable = grantableRoles(user);
  // `assigned_dna_product_ids` is the list of selected brand ids (multi-select).
  // We default to the first available brand so the member is never saved with
  // zero brands (backend rejects empty). Admin can add/remove with checkboxes.
  //
  // Profile fields (company + country/state/city/pin) are all OPTIONAL — they
  // exist so the admin can later filter analytics by region/employer without
  // waiting for the member to self-populate.
  const [form, setForm] = useState({
    email: '',
    invited_name: '',
    invite_type: 'team',  // 'team' | 'franchise'
    role: grantable[grantable.length - 1] || 'user',  // default least-privileged
    assigned_dna_product_ids: brands[0] ? [brands[0].product_id] : [],
    member_company_name: '',
    country: '',        // ISO-2
    country_name: '',   // display snapshot
    state: '',          // ISO-2 subdivision
    state_name: '',     // display snapshot
    city: '',
    pin_code: '',
  });
  const isFranchise = form.invite_type === 'franchise';
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [expandLocation, setExpandLocation] = useState(false);
  // Cascading country/state/city lists live inside the shared <ProfileFields>
  // component below — keeps this modal focused on invite-specific state.

  // Pre-assign-connections — optional. Fetch admin's /connections once so the
  // admin can tick which accounts this new member should inherit on accept.
  const [connByPlatform, setConnByPlatform] = useState(null);
  const [expandConns, setExpandConns] = useState(false);
  const [selectedConnIds, setSelectedConnIds] = useState([]);

  useEffect(() => {
    (async () => {
      try {
        const r = await authAxios.get('/connections');
        setConnByPlatform(r.data || {});
      } catch { }
    })();
  }, [authAxios]);

  const toggleConn = (dbId) => {
    setSelectedConnIds(prev =>
      prev.includes(dbId) ? prev.filter(x => x !== dbId) : [...prev, dbId]
    );
  };

  const toggleBrand = (pid) => {
    setForm((f) => {
      const has = f.assigned_dna_product_ids.includes(pid);
      return {
        ...f,
        assigned_dna_product_ids: has
          ? f.assigned_dna_product_ids.filter((x) => x !== pid)
          : [...f.assigned_dna_product_ids, pid],
      };
    });
  };

  const submit = async (e) => {
    e.preventDefault();
    if (form.assigned_dna_product_ids.length === 0) {
      setErr('Select at least one brand.');
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const _trimOrNull = (v) => (v && String(v).trim()) || null;
      const payload = {
        email: form.email,
        invite_type: form.invite_type,
        // Franchise invites always grant master_admin (backend enforces this);
        // team invites use the selected grantable role.
        role: form.invite_type === 'franchise' ? 'master_admin' : form.role,
        invited_name: _trimOrNull(form.invited_name),
        assigned_dna_product_ids: form.assigned_dna_product_ids,
        member_company_name: _trimOrNull(form.member_company_name),
        country: _trimOrNull(form.country),
        country_name: _trimOrNull(form.country_name),
        state: _trimOrNull(form.state),
        state_name: _trimOrNull(form.state_name),
        city: _trimOrNull(form.city),
        pin_code: _trimOrNull(form.pin_code),
      };
      // Franchisees connect their OWN socials — never pre-assign Master's.
      if (form.invite_type !== 'franchise' && selectedConnIds.length > 0) {
        payload.connection_ids = selectedConnIds;
      }
      const res = await authAxios.post('/team/invites', payload);
      onCreated(!!res?.data?.email_sent, res?.data?.accept_url, form.email);
    } catch (error) {
      setErr(error?.response?.data?.detail || error?.message || 'Failed to create invite');
    } finally {
      setBusy(false);
    }
  };

  const allConnAccounts = connByPlatform
    ? Object.entries(connByPlatform).flatMap(([plat, accs]) =>
      (accs || []).filter(a => !a.assigned_to_user_id).map(a => ({ ...a, _platform: plat }))
    )
    : [];

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 pointer-events-none">
      {/* Transparent click-catcher — closes the modal when clicking outside
          the card. Was a tinted/blurred backdrop; user asked for the
          darkening to go away so the Team page behind stays fully visible. */}
      <div className="absolute inset-0 pointer-events-auto" onClick={onClose} />
      <form
        onSubmit={submit}
        style={{ pointerEvents: 'auto' }}
        className="bg-white w-full max-w-md max-h-[90vh] rounded-3xl shadow-2xl relative z-10 animate-in zoom-in-95 fade-in duration-300 border-2 border-orange-200 flex flex-col"
      >
        <div className="flex items-center justify-between p-4 border-b-2 border-[#2B2926]/15 flex-shrink-0 bg-white">
          <h4 className="font-black text-[#2B2926] text-base">
            {isFranchise ? 'Invite Franchisee' : 'Invite Team Member'}
          </h4>
          <button type="button" onClick={onClose} className="p-2 hover:bg-[#2B2926]/5 rounded-xl text-[#2B2926]">
            <FiX size={18} />
          </button>
        </div>
        {/* Invite type toggle — franchisee brings own plan/subscription vs
            team member inheriting Master's plan. */}
        <div className="px-5 pt-4 pb-2">
          <div className="grid grid-cols-2 gap-2 rounded-2xl bg-slate-100 p-1">
            <button
              type="button"
              onClick={() => setForm(f => ({ ...f, invite_type: 'team' }))}
              className={`text-xs font-black uppercase tracking-widest py-2 rounded-xl transition-all ${
                !isFranchise ? 'bg-white text-[#F55600] shadow-sm' : 'text-slate-500'
              }`}
            >
              Team member
            </button>
            <button
              type="button"
              onClick={() => setForm(f => ({ ...f, invite_type: 'franchise' }))}
              className={`text-xs font-black uppercase tracking-widest py-2 rounded-xl transition-all ${
                isFranchise ? 'bg-white text-[#F55600] shadow-sm' : 'text-slate-500'
              }`}
            >
              Franchisee
            </button>
          </div>
          <p className="text-[10px] text-slate-500 mt-2 leading-relaxed">
            {isFranchise
              ? 'Franchisees operate under your brand but pay their own subscription and connect their own social accounts. You will not be charged for them.'
              : 'Team members inherit your plan and post from your assigned social accounts. They count against your team seat quota.'}
          </p>
        </div>
        {/* Sticky error banner — was rendered at the bottom of the scroll
            area which meant on a filled-in form it hid below the fold and
            users saw "nothing happens" on submit. Now it sits right under
            the header so server-side 400s are always visible. */}
        {err && (
          <div className="mx-5 mt-3 text-[11px] text-red-700 bg-red-50 border-2 border-red-200 rounded-xl px-3 py-2 flex items-start gap-2">
            <FiAlertCircle className="shrink-0 mt-0.5" />
            <span className="break-words">{err}</span>
          </div>
        )}
        <div className="p-5 space-y-3 overflow-y-auto flex-1">
          <Field label="Email" icon={<FiMail />} required>
            <input
              type="email" required
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
              className="w-full text-sm rounded-xl border-2 border-[#2B2926]/20 px-3 py-2 text-[#2B2926] focus:outline-none focus:border-[#F55600]"
              placeholder="member@company.com"
              autoFocus
            />
            <p className="text-[10px] text-[#2B2926]/70 mt-1">They'll receive an email with a single-use link.</p>
          </Field>
          <Field label="Name (for your reference)" required>
            <input
              type="text"
              value={form.invited_name}
              onChange={(e) => setForm({ ...form, invited_name: e.target.value })}
              className="w-full text-sm rounded-xl border-2 border-[#2B2926]/20 px-3 py-2 text-[#2B2926] focus:outline-none focus:border-[#F55600]"
              placeholder="e.g. Alice"
            />
          </Field>
          {!isFranchise && (
            <Field label="Role" required>
              <select
                value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value })}
                disabled={grantable.length <= 1}
                className="w-full text-sm rounded-xl border-2 border-[#2B2926]/20 px-3 py-2 text-[#2B2926] focus:outline-none focus:border-[#F55600] disabled:opacity-60"
              >
                {grantable.map((r) => (
                  <option key={r} value={r}>{ROLE_LABELS[r]}</option>
                ))}
              </select>
              <p className="text-[10px] text-[#2B2926]/70 mt-1">
                {form.role === 'admin'
                  ? 'Admin — can run campaigns, post, connect, and add Users.'
                  : 'User — view-only access.'}
              </p>
            </Field>
          )}
          <Field label="Assigned Brands" icon={<FiTag />} required>
            <div className="space-y-1 max-h-[140px] overflow-y-auto border-2 border-[#2B2926]/20 rounded-xl p-2">
              {brands.length === 0 ? (
                <p className="text-[11px] text-[#2B2926]/60 italic p-2">No brands available.</p>
              ) : brands.map((b) => {
                const checked = form.assigned_dna_product_ids.includes(b.product_id);
                return (
                  <label
                    key={b.product_id}
                    className={`flex items-center gap-2 text-xs px-2 py-1.5 rounded-lg cursor-pointer border-2 transition ${checked ? 'bg-[#F55600]/10 border-[#F55600] text-[#2B2926]' : 'bg-white border-[#2B2926]/15 text-[#2B2926] hover:bg-[#10B981]/10 hover:border-[#10B981]'}`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleBrand(b.product_id)}
                      className="accent-[#F55600]"
                    />
                    <span className="font-bold truncate text-[11px]">{b.product_name}</span>
                  </label>
                );
              })}
            </div>
          </Field>

          {/* External company + cascading location. Shared component is
              reused by the Edit modal so the two stay in lock-step. The
              header here uses a chevron toggle (no "(optional)" suffix,
              no badge text) so the row aligns vertically with the
              Connections section header below. */}
          <div className="border-t border-[#2B2926]/10 pt-2">
            <button
              type="button"
              onClick={() => setExpandLocation(v => !v)}
              className="flex items-center justify-between w-full text-[10px] font-black uppercase tracking-widest text-[#2B2926] hover:text-[#F55600] mb-2"
            >
              <span className="flex items-center gap-1.5"><FiMapPin className="shrink-0" /> Company &amp; Location</span>
              <FiChevronDown className={`shrink-0 transition-transform ${expandLocation ? 'rotate-180' : ''}`} />
            </button>
            {expandLocation && (
              <div className="space-y-2">
                <ProfileFields form={form} setForm={setForm} />
              </div>
            )}
          </div>

          {/* Optional: pre-assign admin-owned connections. Hidden for
              franchisees — they connect their OWN socials, never inherit
              the Master's. Header mirrors Company & Location for alignment. */}
          <div className={`border-t border-[#2B2926]/10 pt-2 ${isFranchise ? 'hidden' : ''}`}>
            <button
              type="button"
              onClick={() => setExpandConns(v => !v)}
              className="flex items-center justify-between w-full text-[10px] font-black uppercase tracking-widest text-[#2B2926] hover:text-[#F55600]"
            >
              <span className="flex items-center gap-1.5"><FiLink className="shrink-0" /> Connections</span>
              <FiChevronDown className={`shrink-0 transition-transform ${expandConns ? 'rotate-180' : ''}`} />
            </button>
            {expandConns && (
              <div className="mt-2 space-y-1 max-h-[140px] overflow-y-auto pr-1">
                {allConnAccounts.length === 0 ? (
                  <p className="text-[11px] text-[#2B2926]/50 italic p-2">No connections available.</p>
                ) : allConnAccounts.map((acc) => {
                  const plat = (acc._platform || '').toLowerCase();
                  const platLabel = plat === 'twitter' ? 'X' : plat.charAt(0).toUpperCase() + plat.slice(1);
                  return (
                    <label key={acc.db_id}
                      className={`flex items-center gap-2 text-xs px-2 py-1.5 rounded-lg cursor-pointer border-2 transition ${selectedConnIds.includes(acc.db_id) ? 'bg-[#F55600]/10 border-[#F55600] text-[#2B2926]' : 'bg-white border-[#2B2926]/15 text-[#2B2926] hover:bg-[#10B981]/10 hover:border-[#10B981]'}`}
                    >
                      <input
                        type="checkbox"
                        checked={selectedConnIds.includes(acc.db_id)}
                        onChange={() => toggleConn(acc.db_id)}
                        className="accent-[#F55600]"
                      />
                      {/* Fixed-width icon slot + fixed-width platform
                          label so the "·" separator and the account
                          name line up at the SAME x position on every
                          row, regardless of icon width or label length
                          (LinkedIn vs X vs Instagram). */}
                      <span className="w-4 flex items-center justify-center shrink-0">
                        <ConnectionPlatformIcon platform={plat} />
                      </span>
                      <span className="font-bold text-[11px] shrink-0 text-[#2B2926] w-[68px]">{platLabel}</span>
                      <span className="text-[#2B2926]/40 shrink-0">·</span>
                      <span className="font-medium truncate text-[11px] text-[#2B2926]">{acc.name}</span>
                    </label>
                  );
                })}
              </div>
            )}
          </div>

          {err && (
            <div className="text-[10px] text-red-600 bg-red-50 border border-red-100 rounded-xl px-3 py-1 flex items-center gap-2">
              <FiAlertCircle /> {err}
            </div>
          )}
        </div>
        <div className="flex items-center justify-end gap-2 p-4 border-t border-[#2B2926]/10 bg-white">
          <button type="button" onClick={onClose}
            className="text-[10px] font-black uppercase tracking-widest px-4 py-2 text-[#2B2926] hover:text-[#F55600]">
            Cancel
          </button>
          <button type="submit" disabled={busy}
            className="text-[11px] font-black uppercase tracking-widest px-5 py-2 rounded-xl bg-[#F55600] text-white hover:bg-[#2B2926] disabled:opacity-40 shadow-md transition-colors">
            {busy ? 'Sending…' : 'Send Invite'}
          </button>
        </div>
        </form>
    </div>
  );
};

const Field = ({ label, icon, children, required }) => (
  <label className="block">
    <div className="flex items-center gap-1.5 text-[10px] font-black uppercase tracking-widest mb-1 text-[#2B2926]">
      {icon && <span className="text-[#F55600] flex items-center">{icon}</span>}
      <span>{label}{required && <span className="text-[#F55600] ml-0.5">*</span>}</span>
    </div>
    {children}
  </label>
);

// ---------------------------------------------------------------------------
// ProfileFields — shared company + cascading location controls used by both
// the Invite modal (at creation) and the Edit modal (post-acceptance). All
// fields are optional; `form` is the parent's form state object and `setForm`
// its setter. The caller is responsible for submitting values upstream —
// this component just drives the UI.
// ---------------------------------------------------------------------------
const ProfileFields = ({ form, setForm }) => {
  const countries = useMemo(() => Country.getAllCountries(), []);
  const states = useMemo(
    () => (form.country ? State.getStatesOfCountry(form.country) : []),
    [form.country]
  );
  const cities = useMemo(
    () => (form.country && form.state
      ? City.getCitiesOfState(form.country, form.state)
      : []),
    [form.country, form.state]
  );

  // Shared text-input classes — solid black/20 border, full-orange
  // focus, dark typed text. (Country/State/City use BrandSelect, which
  // brings its own styling, so only Company Name + Pin/ZIP need this.)
  const fieldCls = "w-full text-sm rounded-xl border-2 border-[#2B2926]/20 px-3 py-2 text-[#2B2926] focus:outline-none focus:border-[#F55600] transition-colors";

  return (
    <div className="space-y-2.5">
      <Field label="Company Name" icon={<FiBriefcase />}>
        <input
          type="text"
          value={form.member_company_name || ''}
          onChange={(e) => setForm({ ...form, member_company_name: e.target.value })}
          className={fieldCls}
          placeholder="e.g. Acme Inc — their employer"
        />
      </Field>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
        {/* Country / State / City use BrandSelect (div-based dropdown)
            instead of native <select>. Native selects open a giant
            unstyled list that the browser can shove to the top of the
            page; BrandSelect opens a fixed-height (260px) scrollable
            menu anchored directly below the trigger via a Portal. */}
        <Field label="Country" icon={<FiMapPin />}>
          <BrandSelect
            size="md"
            value={form.country || ''}
            onChange={(iso) => {
              const name = countries.find(c => c.isoCode === iso)?.name || '';
              setForm({
                ...form,
                country: iso, country_name: name,
                state: '', state_name: '', city: '',
              });
            }}
            options={[
              // Empty option = "no country chosen" (the whole Company &
              // Location section is optional). Labelled plainly so it
              // isn't mistaken for a stray dash.
              { value: '', label: 'Select country' },
              ...countries.map(c => ({ value: c.isoCode, label: c.name })),
            ]}
            placeholder="Select country"
          />
        </Field>

        <Field label="State / Region" icon={<FiMapPin />}>
          <BrandSelect
            size="md"
            value={form.state || ''}
            onChange={(iso) => {
              const name = states.find(s => s.isoCode === iso)?.name || '';
              setForm({ ...form, state: iso, state_name: name, city: '' });
            }}
            options={[
              { value: '', label: form.country ? (states.length ? 'Select state' : 'No states available') : 'Pick country first' },
              ...states.map(s => ({ value: s.isoCode, label: s.name })),
            ]}
            placeholder={form.country ? (states.length ? 'Select state' : 'No states available') : 'Pick country first'}
          />
        </Field>

        <Field label="City" icon={<FiMapPin />}>
          <BrandSelect
            size="md"
            value={form.city || ''}
            onChange={(v) => setForm({ ...form, city: v })}
            options={[
              { value: '', label: form.state ? (cities.length ? 'Select city' : 'No cities available') : 'Pick state first' },
              ...cities.map(c => ({ value: c.name, label: c.name })),
            ]}
            placeholder={form.state ? (cities.length ? 'Select city' : 'No cities available') : 'Pick state first'}
          />
        </Field>

        <Field label="Pin / ZIP" icon={<FiHash />}>
          <input
            type="text"
            value={form.pin_code || ''}
            onChange={(e) => setForm({ ...form, pin_code: e.target.value })}
            className={fieldCls}
            placeholder="e.g. 560001"
          />
        </Field>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// EditMemberModal — edits an accepted member's full profile. Email is locked
// (changing it would invalidate login), brand is edited inline in the table,
// so this modal focuses on name + company + location + pin. Submits the
// minimal diff to PUT /team/members/{id} — only fields actually changed are
// sent, which lets the backend leave untouched fields alone.
// ---------------------------------------------------------------------------
const EditMemberModal = ({ member, authAxios, user, onClose, onSaved }) => {
  // Role editing is MASTER-ADMIN-ONLY (matches the backend: changing a role
  // requires master_admin). An Admin manages Users but cannot change anyone's
  // role, so the dropdown is hidden for them — the role was set by the Master.
  const grantable = grantableRoles(user);
  const roleOptions = Array.from(new Set([...(member.role ? [member.role] : []), ...grantable]));
  const canEditRole = isMasterAdmin(user);
  const [form, setForm] = useState({
    full_name: member.full_name || '',
    role: member.role || 'user',
    member_company_name: member.member_company_name || '',
    country: member.country || '',
    country_name: member.country_name || '',
    state: member.state || '',
    state_name: member.state_name || '',
    city: member.city || '',
    pin_code: member.pin_code || '',
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      // Send every field in the form — empty strings become NULL on the
      // backend (admin explicitly cleared them). Simpler than diffing.
      const payload = {
        full_name: form.full_name?.trim() ?? '',
        member_company_name: form.member_company_name?.trim() ?? '',
        country: form.country?.trim() ?? '',
        country_name: form.country_name?.trim() ?? '',
        state: form.state?.trim() ?? '',
        state_name: form.state_name?.trim() ?? '',
        city: form.city?.trim() ?? '',
        pin_code: form.pin_code?.trim() ?? '',
      };
      // Only send role when the editor can change it AND it actually changed —
      // keeps the request a no-op for role on profile-only edits.
      if (canEditRole && form.role && form.role !== member.role) {
        payload.role = form.role;
      }
      await authAxios.put(`/team/members/${member.id}`, payload);
      onSaved();
    } catch (error) {
      setErr(error?.response?.data?.detail || error?.message || 'Failed to save');
    } finally {
      setBusy(false);
    }
  };

  return (
    <td colSpan="6" className="p-0">
      <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 pointer-events-none">
        {/* Transparent click-catcher. No tint/blur — the Team page behind
            stays fully visible while the edit card floats on top. */}
        <div className="absolute inset-0 pointer-events-auto" onClick={onClose} />
        <form
          style={{ pointerEvents: 'auto' }}
          onSubmit={submit}
          className="bg-white w-full max-w-md max-h-[90vh] rounded-3xl shadow-2xl relative z-10 animate-in zoom-in-95 fade-in duration-300 border-2 border-orange-200 flex flex-col"
        >
          <div className="flex items-center justify-between p-4 border-b-2 border-slate-200 flex-shrink-0 bg-white">
            <h4 className="font-black text-[#2B2926] text-base">Edit Member Profile</h4>
            <button type="button" onClick={onClose} className="p-2 hover:bg-slate-100 rounded-xl text-slate-400">
              <FiX size={18} />
            </button>
          </div>
          <div className="p-5 space-y-3 overflow-y-auto flex-1">
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">
              Email: <span className="text-[#2B2926] normal-case tracking-normal">{member.email}</span>
              <span className="ml-2 text-slate-300">(locked)</span>
            </div>

            <Field label="Full Name">
              <input
                type="text"
                value={form.full_name}
                onChange={(e) => setForm({ ...form, full_name: e.target.value })}
                className="w-full text-sm rounded-xl border-2 border-slate-200 px-3 py-2 focus:outline-none focus:border-[#F55600]/50"
                placeholder="e.g. Alice Johnson"
              />
            </Field>

            {canEditRole && (
              <Field label="User Role">
                <select
                  value={form.role}
                  onChange={(e) => setForm({ ...form, role: e.target.value })}
                  className="w-full text-sm rounded-xl border-2 border-slate-200 px-3 py-2 focus:outline-none focus:border-[#F55600]/50 bg-white"
                >
                  {roleOptions.map((r) => (
                    <option key={r} value={r}>{ROLE_LABELS[r] || r}</option>
                  ))}
                </select>
              </Field>
            )}

            <ProfileFields form={form} setForm={setForm} />

            {err && (
              <div className="text-[10px] text-red-600 bg-red-50 border border-red-100 rounded-xl px-3 py-1 flex items-center gap-2">
                <FiAlertCircle /> {err}
              </div>
            )}
          </div>
          <div className="flex items-center justify-end gap-2 p-4 border-t border-slate-100 bg-slate-50/50">
            <button type="button" onClick={onClose}
              className="text-[10px] font-black uppercase tracking-widest px-4 py-2 text-slate-500 hover:text-[#2B2926]">
              Cancel
            </button>
            <button type="submit" disabled={busy}
              className="text-[10px] font-black uppercase tracking-widest px-5 py-2 rounded-xl bg-[#F55600] text-white hover:bg-[#F55600] disabled:opacity-40 shadow-md">
              {busy ? 'Saving…' : 'Save Changes'}
            </button>
          </div>
        </form>
      </div>
    </td>
  );
};

export default Team;
