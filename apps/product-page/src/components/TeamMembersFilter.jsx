import React, { useEffect, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { isReadOnly } from '../lib/permissions';

/**
 * Admin-only dropdown that lets the admin narrow a page's data to a subset
 * of team members. Renders nothing for members or admins on plans that
 * don't include the teams feature (the /team/members call will 403 and
 * we hide the control).
 *
 * Props:
 *   user          — current user object (to detect member role)
 *   authAxios     — authenticated axios instance
 *   value         — array of selected user ids (empty = "All")
 *   onChange      — (ids[]) => void
 *   label         — override the "TEAM" prefix text (optional)
 */
const TeamMembersFilter = ({ user, authAxios, value = [], onChange, label = 'Company' }) => {
  const isMember = isReadOnly(user);
  const [members, setMembers] = useState([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (isMember || !authAxios) return;
    (async () => {
      try {
        const res = await authAxios.get('/team/members');
        setMembers(
          ((res.data?.members) || [])
            .filter((m) => m.kind === 'member' && !m.disabled)
            .map((m) => ({ id: m.id, email: m.email, full_name: m.full_name }))
        );
      } catch {
        /* plan without teams feature → silently hide */
      }
    })();
  }, [authAxios, isMember]);

  if (isMember || members.length === 0) return null;

  const selectedMember = value.length === 1 ? members.find((m) => m.id === value[0]) : null;
  const labelText =
    value.length === 0
      ? 'All'
      : value.length === 1
        ? selectedMember?.full_name || selectedMember?.email || '1 selected'
        : `${value.length} selected`;

  const toggle = (id) => {
    if (value.includes(id)) onChange(value.filter((x) => x !== id));
    else onChange([...value, id]);
    setOpen(false);
  };

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="h-11 flex items-center justify-between gap-3 bg-white border-2 border-orange-200 rounded-xl px-4 text-xs font-black text-[#2B2926] focus:outline-none hover:border-[#F55600]/50 transition-all whitespace-nowrap"
      >
        <div className="flex items-center gap-2">
          <span className="uppercase tracking-wider text-[9px] text-slate-400 shrink-0">{label}</span>
          <span className="truncate max-w-[120px]">{labelText}</span>
        </div>
        <ChevronDown size={14} className="text-[#F55600] shrink-0" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 mt-1 z-50 bg-white rounded-xl shadow-2xl border-2 border-orange-100 py-2 min-w-[260px] max-h-[320px] overflow-y-auto">
            <label className="flex items-center gap-2 px-3 py-2 hover:bg-orange-50 cursor-pointer text-xs">
              <input
                type="checkbox"
                checked={value.length === 0}
                onChange={() => {
                  onChange([]);
                  setOpen(false);
                }}
                className="accent-[#F55600]"
              />
              <span className="font-black uppercase tracking-widest text-[10px] text-[#F55600]">
                All (admin + every member)
              </span>
            </label>
            <div className="h-px bg-slate-100 my-1" />
            {members.map((m) => (
              <label
                key={m.id}
                className="flex items-center gap-2 px-3 py-2 hover:bg-orange-50 cursor-pointer text-xs"
              >
                <input
                  type="checkbox"
                  checked={value.includes(m.id)}
                  onChange={() => toggle(m.id)}
                  className="accent-[#F55600]"
                />
                <div className="min-w-0">
                  <div className="font-bold text-[#2B2926] truncate">
                    {m.full_name || m.email}
                  </div>
                  {m.full_name && (
                    <div className="text-[10px] text-slate-400 truncate">{m.email}</div>
                  )}
                </div>
              </label>
            ))}
          </div>
        </>
      )}
    </div>
  );
};

export default TeamMembersFilter;
