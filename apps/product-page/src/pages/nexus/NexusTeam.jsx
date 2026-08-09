/**
 * NexusTeam — port of legacy apps/nexus-legacy/client/src/pages/Team.jsx.
 *
 * Workspace members + invites. State names match legacy.
 *
 * Backend: GET/POST/PATCH/DELETE /nexus/team/* (router stub: team.py).
 */
import React, { useCallback, useEffect, useState } from 'react';
import { AlertCircle, Loader2, Mail, Plus, Shield, Trash2, UserX } from 'lucide-react';
import { ROLE_LABELS, grantableRoles, roleLabel } from '../../lib/permissions';

const NexusTeam = ({ authAxios, apiBase, user, setMessage }) => {
  const [members, setMembers] = useState([]);
  const [invites, setInvites] = useState([]);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [inviteEmail, setInviteEmail] = useState('');
  // Roles this user may grant (Master Admin → admin|user, Admin → user only).
  const grantable = grantableRoles(user);
  const [inviteRole, setInviteRole] = useState(grantable[grantable.length - 1] || 'user');

  const teamBase = `${apiBase}/team`;

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await authAxios.get(`${teamBase}/members`);
      setMembers(res.data?.members || []);
      // Invites endpoint not yet present; leave empty.
      setInvites([]);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  }, [authAxios, teamBase]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const sendInvite = async () => {
    if (!inviteEmail) return;
    setWorking(true);
    try {
      await authAxios.post(`${teamBase}/invite`, {
        email: inviteEmail,
        role: inviteRole,
      });
      setInviteEmail('');
      await loadAll();
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setWorking(false);
    }
  };

  const removeMember = async (memberId) => {
    setWorking(true);
    try {
      await authAxios.delete(`${teamBase}/members/${memberId}`);
      await loadAll();
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setWorking(false);
    }
  };

  return (
    <div className="bg-white">
      <div className="px-5 py-4 border-b border-[#2B2926]/10">
        <h1 className="text-lg font-black text-[#2B2926] tracking-tight">Team</h1>
        <p className="text-xs text-[#2B2926]/60 mt-0.5">
          Manage workspace members and invitations.
        </p>
      </div>

      {error && (
        <div className="mx-5 mt-3 px-3 py-2 rounded-lg bg-[#F55600]/10 border border-[#F55600]/30 flex items-center gap-2 text-xs text-[#F55600]">
          <AlertCircle className="w-3.5 h-3.5" />
          {error}
        </div>
      )}

      {/* Invite form */}
      <div className="px-5 py-4 border-b border-[#2B2926]/10">
        <h3 className="text-xs font-bold text-[#2B2926] mb-2 uppercase tracking-wider">
          Invite a new member
        </h3>
        <div className="flex items-end gap-2 max-w-xl">
          <div className="flex-1">
            <input
              type="email"
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              placeholder="teammate@company.com"
              className="w-full px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600]"
            />
          </div>
          <select
            value={inviteRole}
            onChange={(e) => setInviteRole(e.target.value)}
            disabled={grantable.length === 0}
            className="px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600] disabled:opacity-50"
          >
            {grantable.map((r) => (
              <option key={r} value={r}>{ROLE_LABELS[r]}</option>
            ))}
          </select>
          <button
            type="button"
            onClick={sendInvite}
            disabled={working || !inviteEmail}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[#F55600] text-white text-xs font-bold hover:opacity-90 disabled:opacity-50"
          >
            {working ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
            Send invite
          </button>
        </div>
      </div>

      {/* Members */}
      <div className="px-5 py-4">
        <h3 className="text-xs font-bold text-[#2B2926] mb-3 uppercase tracking-wider">
          Members ({members.length})
        </h3>
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-[#2B2926]/60">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading…
          </div>
        ) : members.length === 0 ? (
          <div className="text-xs text-[#2B2926]/40 italic">No members yet.</div>
        ) : (
          <div className="space-y-2 max-w-2xl">
            {members.map((m) => (
              <div
                key={m._id}
                className="flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg border border-[#2B2926]/10"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <div className="w-8 h-8 rounded-lg bg-[#F55600]/10 text-[#F55600] flex items-center justify-center text-xs font-bold">
                    {(m.email || '?').charAt(0).toUpperCase()}
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-bold text-[#2B2926] truncate">{m.email}</div>
                    <div className="text-[10px] text-[#2B2926]/60 inline-flex items-center gap-1">
                      <Shield className="w-2.5 h-2.5" />
                      {roleLabel(m.role)}
                    </div>
                  </div>
                </div>
                {grantable.includes(m.role) && (
                  <button
                    type="button"
                    onClick={() => removeMember(m._id)}
                    disabled={working}
                    className="inline-flex items-center gap-1 px-2 py-1 rounded text-[10px] font-bold text-[#2B2926]/60 hover:text-[#F55600] hover:bg-[#F55600]/5"
                  >
                    <UserX className="w-3 h-3" />
                    Remove
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Pending invites */}
      {invites.length > 0 && (
        <div className="px-5 py-4 border-t border-[#2B2926]/10">
          <h3 className="text-xs font-bold text-[#2B2926] mb-3 uppercase tracking-wider">
            Pending invites ({invites.length})
          </h3>
          <div className="space-y-2 max-w-2xl">
            {invites.map((inv) => (
              <div
                key={inv._id}
                className="flex items-center justify-between gap-3 px-3 py-2.5 rounded-lg border border-[#2B2926]/10 bg-[#2B2926]/[0.02]"
              >
                <div className="inline-flex items-center gap-2 text-xs text-[#2B2926]">
                  <Mail className="w-3.5 h-3.5 text-[#F55600]" />
                  {inv.email_lower}
                  <span className="text-[10px] text-[#2B2926]/40">· {roleLabel(inv.role)}</span>
                </div>
                <button
                  type="button"
                  disabled
                  className="text-[10px] font-bold text-[#2B2926]/40"
                >
                  <Trash2 className="w-3 h-3 inline" /> Revoke
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

export default NexusTeam;
