import React, { useState, useEffect, useCallback, useMemo, useRef, useLayoutEffect } from 'react';
import { createPortal } from 'react-dom';
import { ChevronDown } from 'lucide-react';
import { isReadOnly } from '../lib/permissions';

/**
 * Shared cascading "Brand Filter" dropdown used by both the Analytics page
 * and the Dashboard page. Same cascade rules as the existing Analytics
 * implementation, extracted so both pages stay in lockstep:
 *
 *   Companies (multi)  →  narrows available Brands
 *   Brands    (multi)  →  narrows available Members
 *   Members   (multi)  →  narrows everything downstream
 *   Country → State → City → Pin — cascading location
 *
 * Clearing all filters resolves to "admin only" (the sentinel adminUserId
 * is sent via onChange so the hosting page can send
 * `?member_user_ids=<adminId>`).
 *
 * Props:
 *   user       — current user. Admins only (members get nothing rendered).
 *   authAxios  — authenticated axios instance.
 *   value      — { companies, brandIds, memberIds, country, state, city, pin_code }
 *   onChange   — called with ({ sel, selectedMemberIds, hasAnyFilter })
 *                `selectedMemberIds` is the resolved csv-ready list the
 *                parent passes to the backend. `sel` is the raw picker
 *                state so parent can persist if they want.
 */
const EMPTY_SEL = Object.freeze({
  companies: [], brandIds: [], memberIds: [],
  country: '', state: '', city: '', pin_code: '',
});

/**
 * Branded select replacement for the native <select>.
 * Native option dropdowns inherit the OS-level highlight color (blue on most
 * systems) and cannot be styled with Tailwind. This component renders a
 * custom popover so the selected/highlighted option uses #F55600 (brand
 * orange) instead.
 *
 * Props:
 *   value     — currently selected value (string)
 *   onChange  — (newValue) => void
 *   options   — [{ value, label }, ...] (the "All" entry is added automatically)
 *   placeholder — label shown when nothing selected (default: 'All')
 */
export const BrandSelect = ({ value, onChange, options, placeholder = 'All' }) => {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const triggerRef = useRef(null);
  const menuRef = useRef(null);
  // Position state for the portal-rendered menu. The Brand Filter popover
  // is an `overflow-y-auto` container, so an `absolute` menu rendered
  // inside it gets clipped — most visibly for the bottom rows (City,
  // Pin/ZIP) where the menu was cut off entirely. Rendering the menu in a
  // Portal at document.body lets it float above the popover unclipped.
  const [menuRect, setMenuRect] = useState(null);

  // Close on outside click — check the trigger AND the portal menu since
  // the menu now lives outside `wrapRef`.
  useEffect(() => {
    if (!open) return undefined;
    const onDocClick = (e) => {
      const inTrigger = wrapRef.current?.contains(e.target);
      const inMenu = menuRef.current?.contains(e.target);
      if (!inTrigger && !inMenu) setOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  // Recompute menu position (viewport-relative) on open + scroll/resize so
  // it stays glued to the trigger. Flips upward when there isn't room
  // below — keeps the menu fully visible on the tall filter popover.
  useLayoutEffect(() => {
    if (!open) { setMenuRect(null); return undefined; }
    const reposition = () => {
      const t = triggerRef.current;
      if (!t) return;
      const r = t.getBoundingClientRect();
      const spaceBelow = window.innerHeight - r.bottom;
      const openUp = spaceBelow < 210 && r.top > 210;
      setMenuRect({
        left: r.left,
        width: r.width,
        top: openUp ? null : r.bottom + 4,
        bottom: openUp ? window.innerHeight - r.top + 4 : null,
      });
    };
    reposition();
    window.addEventListener('scroll', reposition, true);
    window.addEventListener('resize', reposition);
    return () => {
      window.removeEventListener('scroll', reposition, true);
      window.removeEventListener('resize', reposition);
    };
  }, [open]);

  const selected = options.find((o) => o.value === value);
  const displayLabel = selected ? selected.label : placeholder;

  const menu = open && menuRect ? (
    <div
      ref={menuRef}
      style={{
        position: 'fixed',
        left: menuRect.left,
        width: menuRect.width,
        top: menuRect.top ?? undefined,
        bottom: menuRect.bottom ?? undefined,
        zIndex: 10000,
      }}
      className="bg-white border-2 border-[#F55600]/20 rounded-lg shadow-2xl max-h-48 overflow-y-auto py-1"
    >
      {/* "All" entry — clears the selection */}
      <button
        type="button"
        onClick={() => { onChange(''); setOpen(false); }}
        className={`w-full text-left text-[11px] px-2 py-1.5 transition-colors ${
          value === ''
            ? 'bg-[#F55600] text-white font-bold'
            : 'text-[#2B2926] hover:bg-[#2B2926]/[0.05]'
        }`}
      >
        All
      </button>
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => { onChange(opt.value); setOpen(false); }}
          className={`w-full text-left text-[11px] px-2 py-1.5 transition-colors ${
            value === opt.value
              ? 'bg-[#F55600] text-white font-bold'
              : 'text-[#2B2926] hover:bg-[#2B2926]/[0.05]'
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  ) : null;

  return (
    <div ref={wrapRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full rounded-[12px] border focus:outline-none flex items-center justify-between gap-1.5 transition-all"
        style={{
          padding: '8px 11px',
          background: '#fffaf6',
          borderColor: 'rgba(43,36,64,0.1)',
          color: '#2b2440',
          fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"',
          fontSize: '13px',
          fontWeight: 600,
        }}
        onMouseEnter={(e) => { e.currentTarget.style.borderColor = '#F55600'; }}
        onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'rgba(43,36,64,0.1)'; }}
        onFocus={(e) => { e.currentTarget.style.borderColor = '#F55600'; e.currentTarget.style.boxShadow = '0 0 0 3px rgba(255,106,44,0.2)'; }}
        onBlur={(e) => { e.currentTarget.style.borderColor = 'rgba(43,36,64,0.1)'; e.currentTarget.style.boxShadow = 'none'; }}
      >
        <span className="truncate">{displayLabel}</span>
        <ChevronDown size={16} strokeWidth={2.4} className="text-[#F55600] shrink-0" />
      </button>
      {typeof document !== 'undefined' && menu
        ? createPortal(menu, document.body)
        : null}
    </div>
  );
};

const BrandFilter = ({ user, authAxios, value, onChange }) => {
  const isMember = isReadOnly(user);
  const sel = value || EMPTY_SEL;

  // Seed from localStorage so the Brand Filter dropdown shows companies
  // / brands instantly on every mount instead of flashing "No companies
  // set on team members yet" until /team/filter-options returns. The
  // same key is populated by App.jsx's app-boot prefetch and refreshed
  // every time the Analytics page or this dropdown fetches.
  const [filterOptions, setFilterOptions] = useState(() => {
    try {
      const raw = localStorage.getItem('pipelyt_filter_options_cache_v1');
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed?.data && typeof parsed.fetchedAt === 'number'
            && Date.now() - parsed.fetchedAt < 60 * 60 * 1000) {
          return parsed.data;
        }
      }
    } catch { /* corrupted — ignore */ }
    return {
      members: [], brands: [], companies: [],
      countries: [], states: [], cities: [], pin_codes: [],
    };
  });
  const [open, setOpen] = useState(false);
  // The popover is rendered through a Portal to document.body so it is
  // positioned against the real viewport — product pages sit inside
  // transformed (framer-motion) ancestors, which make a plain
  // `position: fixed` resolve against the ancestor instead of the screen
  // and drop the popover in the wrong place.
  const triggerRef = useRef(null);
  const [menuRect, setMenuRect] = useState(null);
  useLayoutEffect(() => {
    if (!open) { setMenuRect(null); return undefined; }
    const reposition = () => {
      const t = triggerRef.current;
      if (!t) return;
      const r = t.getBoundingClientRect();
      const mobile = window.innerWidth < 640;
      setMenuRect(mobile
        ? { mobile: true, top: r.bottom + 6 }
        : { mobile: false, top: r.bottom + 6, right: Math.max(8, window.innerWidth - r.right) });
    };
    reposition();
    window.addEventListener('scroll', reposition, true);
    window.addEventListener('resize', reposition);
    return () => {
      window.removeEventListener('scroll', reposition, true);
      window.removeEventListener('resize', reposition);
    };
  }, [open]);
  // Brand & Team Details and Regional Filters both default OPEN —
  // matches the new layout where every filter is visible at a glance
  // (single-select dropdowns instead of long checkbox lists).
  const [brandSectionOpen, setBrandSectionOpen] = useState(true);
  const [advancedOpen, setAdvancedOpen] = useState(true);
  // Auto-open the Advanced section when the dropdown opens AND there are
  // already advanced filters set, so the user immediately sees what's active.
  useEffect(() => {
    if (open) {
      const hasAdvanced = sel.memberIds.length > 0
        || !!sel.country || !!sel.state || !!sel.city || !!sel.pin_code;
      if (hasAdvanced) setAdvancedOpen(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Fetch on mount + every dropdown open so newly-invited members / new
  // brands show up without a full page reload. Persists to localStorage
  // so the next mount can hydrate from disk instantly (instead of
  // showing the empty "No companies" state during the network call).
  const refresh = useCallback(async () => {
    if (isMember || !authAxios) return;
    try {
      const res = await authAxios.get('/team/filter-options');
      setFilterOptions(res.data || {});
      try {
        localStorage.setItem(
          'pipelyt_filter_options_cache_v1',
          JSON.stringify({ data: res.data, fetchedAt: Date.now() }),
        );
      } catch { /* quota — ignore */ }
    } catch (e) {
      /* plan without teams → hide silently */
    }
  }, [authAxios, isMember]);

  const mountedRef = React.useRef(false);
  useEffect(() => {
    if (mountedRef.current) return;
    mountedRef.current = true;
    refresh();
  }, [refresh]);

  const allMembers = filterOptions.members || [];
  const allBrands = filterOptions.brands || [];

  const membersAfterCompany = useMemo(() => (
    sel.companies.length === 0
      ? allMembers
      : allMembers.filter((m) => m.company && sel.companies.includes(m.company))
  ), [allMembers, sel.companies]);

  const availableBrands = useMemo(() => {
    if (sel.companies.length === 0) return allBrands;
    const used = new Set(membersAfterCompany.flatMap((m) => m.brands || []));
    return allBrands.filter((b) => used.has(b.id));
  }, [allBrands, membersAfterCompany, sel.companies.length]);

  const membersAfterBrand = useMemo(() => (
    sel.brandIds.length === 0
      ? membersAfterCompany
      : membersAfterCompany.filter((m) => (m.brands || []).some((b) => sel.brandIds.includes(b)))
  ), [membersAfterCompany, sel.brandIds]);

  const availableMembers = membersAfterBrand;

  const availableCountries = useMemo(() => {
    const seen = new Map();
    membersAfterBrand.forEach((m) => {
      if (m.country && !seen.has(m.country)) seen.set(m.country, m.country_name || m.country);
    });
    return Array.from(seen, ([code, name]) => ({ code, name })).sort((a, b) => a.name.localeCompare(b.name));
  }, [membersAfterBrand]);

  const availableStates = useMemo(() => {
    const seen = new Map();
    membersAfterBrand
      .filter((m) => !sel.country || m.country === sel.country)
      .forEach((m) => {
        if (m.state && !seen.has(m.state)) seen.set(m.state, m.state_name || m.state);
      });
    return Array.from(seen, ([code, name]) => ({ code, name })).sort((a, b) => a.name.localeCompare(b.name));
  }, [membersAfterBrand, sel.country]);

  const availableCities = useMemo(() => {
    const seen = new Set();
    membersAfterBrand
      .filter((m) => (!sel.country || m.country === sel.country) && (!sel.state || m.state === sel.state))
      .forEach((m) => { if (m.city) seen.add(m.city); });
    return Array.from(seen).sort();
  }, [membersAfterBrand, sel.country, sel.state]);

  const availablePins = useMemo(() => {
    const seen = new Set();
    membersAfterBrand
      .filter((m) =>
        (!sel.country || m.country === sel.country) &&
        (!sel.state || m.state === sel.state) &&
        (!sel.city || m.city === sel.city)
      )
      .forEach((m) => { if (m.pin_code) seen.add(m.pin_code); });
    return Array.from(seen).sort();
  }, [membersAfterBrand, sel.country, sel.state, sel.city]);

  const hasAnyFilter = (
    sel.companies.length +
    sel.brandIds.length +
    sel.memberIds.length +
    (sel.country ? 1 : 0) + (sel.state ? 1 : 0) +
    (sel.city ? 1 : 0) + (sel.pin_code ? 1 : 0)
  ) > 0;

  // Push state up via onChange. "Admin-only" sentinel behaviour: when the
  // filter is fully cleared we resolve memberIds to [adminUserId] so the
  // parent-page fetch sends `member_user_ids=<admin>` (backend shows only
  // the admin's own data). Matches the Analytics page semantic.
  const adminUserId = user?.id || user?.user_id || null;
  const adminCompany = (user?.company_name || '').trim();

  // When the admin's own company / brand / location matches a filter, the
  // admin's own posts must be included in the result. Without this, an admin
  // selecting their own company "NeuzenAI" was excluded from their own
  // dashboard because the team-roster `availableMembers` doesn't list the
  // admin themselves.
  const adminBrandIds = (allBrands || []).map(b => b.id); // admin owns every brand
  const adminMatchesFilter = (s) => {
    if (!adminUserId) return false;
    // Company filter — admin's own company is in the selected set
    if (s.companies.length > 0 && adminCompany && s.companies.includes(adminCompany)) {
      return true;
    }
    // Brand filter — admin owns all brands; if any brand is selected, admin is included
    if (s.brandIds.length > 0 && s.brandIds.some(id => adminBrandIds.includes(id))) {
      return true;
    }
    // Location filter — admin's own location matches
    if (s.country && user?.country === s.country) return true;
    if (s.state   && user?.state   === s.state)   return true;
    if (s.city    && user?.city    === s.city)    return true;
    if (s.pin_code && user?.pin_code === s.pin_code) return true;
    return false;
  };

  const resolveMemberIds = useCallback((s) => {
    if (s.memberIds.length > 0) return s.memberIds;

    if (s.companies.length + s.brandIds.length > 0) {
      const ids = availableMembers.map((m) => m.id);
      // Admin's own company/brand is in the filter → include admin too.
      if (adminUserId && adminMatchesFilter(s) && !ids.includes(adminUserId)) {
        ids.push(adminUserId);
      }
      return ids;
    }

    const locationOnly = (s.country || s.state || s.city || s.pin_code);
    if (locationOnly) {
      const ids = availableMembers.map((m) => m.id);
      if (adminUserId && adminMatchesFilter(s) && !ids.includes(adminUserId)) {
        ids.push(adminUserId);
      }
      return ids;
    }

    // No filter selected ("All") → return empty so the parent omits
    // `member_user_ids` and the backend defaults to the full team scope
    // (admin + all members combined). Previously returned [adminUserId]
    // which narrowed "All" to just the admin's data.
    return [];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [availableMembers, adminUserId, adminCompany, allBrands, user?.country, user?.state, user?.city, user?.pin_code]);

  const emit = useCallback((nextSel) => {
    onChange?.({
      sel: nextSel,
      selectedMemberIds: resolveMemberIds(nextSel),
      hasAnyFilter: (
        nextSel.companies.length +
        nextSel.brandIds.length +
        nextSel.memberIds.length +
        (nextSel.country ? 1 : 0) + (nextSel.state ? 1 : 0) +
        (nextSel.city ? 1 : 0) + (nextSel.pin_code ? 1 : 0)
      ) > 0,
    });
  }, [onChange, resolveMemberIds]);

  if (isMember) return null;

  // Always render for admins — helpful "empty state" messages inside the
  // panel explain what's empty rather than the whole button disappearing.
  const labelText = !hasAnyFilter
    ? 'All'
    : [
        sel.brandIds.length && `${sel.brandIds.length} brand${sel.brandIds.length > 1 ? 's' : ''}`,
        sel.companies.length && `${sel.companies.length} co.`,
        sel.memberIds.length && `${sel.memberIds.length} mem.`,
        (sel.country || sel.state || sel.city || sel.pin_code) && 'loc',
      ].filter(Boolean).join(' · ');

  // PIPELYT BRAND PALETTE — only these hex codes may appear in this component:
  //   #F55600 (brand orange), #10B981 (success green), black, white.
  // Greys are expressed as black/white with opacity. Do not introduce slate-*,
  // orange-100/200, or other Tailwind palette shades here.
  return (
    <div className="relative w-full sm:w-auto">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => {
          const next = !open;
          setOpen(next);
          if (next) refresh();
        }}
        className="h-10 flex sm:inline-flex w-full sm:w-auto items-center justify-center sm:justify-start gap-2.5 bg-white border border-[#2B2926]/30 rounded-full px-4 focus:outline-none whitespace-nowrap transition-all hover:border-[#2B2926]/50 hover:shadow-[0_4px_12px_rgba(46,43,41,0.08)]"
      >
        {/* Apollo-style refinement: 12px semibold uppercase label with
            calmer tracking. */}
        <span
          className="uppercase text-[#2B2926]"
          style={{ fontSize: '12px', fontWeight: 600, letterSpacing: '0.08em' }}
        >
          BRAND FILTER
        </span>
        {/* Active brand value — orange, 13px semibold (was 800/black —
            chunky), thinner chevron at 12px stroke-2. */}
        <span
          className="ml-1 inline-flex items-center gap-1"
          style={{ color: '#F55600', fontSize: '13px', fontWeight: 600 }}
        >
          {labelText}
          <ChevronDown size={12} strokeWidth={2} className="text-[#F55600]" />
        </span>
      </button>
      {open && menuRect && createPortal((
        <>
          <div className="fixed inset-0 z-[1900]" onClick={() => setOpen(false)} />
          {/* Portal-rendered to document.body: on mobile a viewport-centred
              card, on desktop anchored just below the Brand Filter button.
              Positioning is measured (getBoundingClientRect) so transformed
              page ancestors can't throw it off-screen. */}
          <div
            style={menuRect.mobile
              ? { position: 'fixed', left: '50%', top: menuRect.top, transform: 'translateX(-50%)', width: 'calc(100vw - 24px)', maxWidth: 336, maxHeight: '78vh', zIndex: 1901, fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }
              : { position: 'fixed', top: menuRect.top, right: menuRect.right, width: 336, maxHeight: 'min(74vh, 540px)', zIndex: 1901, fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
            className="bg-white rounded-[20px] shadow-[0_28px_70px_rgba(43,36,64,0.22)] border border-white/85 overflow-y-auto p-3.5"
          >
            {/* SECTION 1 — Brand & Team Details (collapsible). Two-column
                grid of single-select BrandSelect dropdowns: Companies +
                Business DNA (top row), Team Members (full-width row).
                "All" is the default; picking a value narrows the chain. */}
            <div className="pb-2">
              <button
                type="button"
                onClick={() => setBrandSectionOpen((v) => !v)}
                className="w-full flex items-center justify-between gap-2.5 py-1.5 pb-3.5"
              >
                <span
                  className="uppercase tracking-[0.1em]"
                  style={{ color: '#F55600', fontSize: '12px', fontWeight: 700 }}
                >
                  Brand &amp; Team Details
                </span>
                <span
                  className={`inline-flex items-center justify-center w-[22px] h-[22px] rounded-[7px] border transition-transform ${brandSectionOpen ? 'rotate-0' : 'rotate-180'}`}
                  style={{ borderColor: 'rgba(255,106,44,0.2)', color: '#F55600', padding: '3px' }}
                >
                  <ChevronDown size={11} strokeWidth={2.4} className="rotate-180" />
                </span>
              </button>
              {brandSectionOpen && (
                <div className="flex flex-col gap-3.5">
                  <div className="grid grid-cols-2 gap-3">
                    <div className="flex flex-col gap-1.5 min-w-0">
                      <span
                        className="uppercase tracking-[0.06em]"
                        style={{ color: '#2b2440', fontSize: '11px', fontWeight: 700, fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
                      >
                        Companies
                      </span>
                      <BrandSelect
                        value={sel.companies[0] || ''}
                        onChange={(v) => emit({
                          ...sel,
                          companies: v ? [v] : [],
                          brandIds: [], memberIds: [],
                          country: '', state: '', city: '', pin_code: '',
                        })}
                        options={(filterOptions.companies || []).map((c) => ({ value: c, label: c }))}
                        placeholder="All"
                      />
                    </div>
                    <div className="flex flex-col gap-1.5 min-w-0">
                      <span
                        className="uppercase tracking-[0.06em]"
                        style={{ color: '#2b2440', fontSize: '11px', fontWeight: 700, fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
                      >
                        Business DNA
                      </span>
                      <BrandSelect
                        value={sel.brandIds[0] || ''}
                        onChange={(v) => emit({
                          ...sel,
                          brandIds: v ? [v] : [],
                          memberIds: [], country: '', state: '', city: '', pin_code: '',
                        })}
                        options={availableBrands.map((b) => ({ value: b.id, label: b.name }))}
                        placeholder="All"
                      />
                    </div>
                  </div>
                  <div className="flex flex-col gap-1.5 min-w-0">
                    <span
                      className="uppercase tracking-[0.06em]"
                      style={{ color: '#2b2440', fontSize: '11px', fontWeight: 700, fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
                    >
                      Team Members
                    </span>
                    <BrandSelect
                      value={sel.memberIds[0] || ''}
                      onChange={(v) => emit({
                        ...sel,
                        memberIds: v ? [v] : [],
                      })}
                      options={availableMembers.map((m) => ({ value: m.id, label: m.full_name || m.email }))}
                      placeholder="All"
                    />
                  </div>
                </div>
              )}
            </div>

            {/* SECTION 2 — Regional Filters (collapsible). 2x2 grid of
                cascading location selects: Country → State → City → Pin /
                ZIP. Each level only shows values that exist in the upstream
                selection. */}
            <div className="mt-2 pt-1">
              <button
                type="button"
                onClick={() => setAdvancedOpen((v) => !v)}
                className="w-full flex items-center justify-between gap-2.5 py-1.5 pb-3.5"
              >
                <span
                  className="uppercase tracking-[0.1em]"
                  style={{ color: '#F55600', fontSize: '12px', fontWeight: 700 }}
                >
                  Regional Filters
                </span>
                <span
                  className={`inline-flex items-center justify-center w-[22px] h-[22px] rounded-[7px] border transition-transform ${advancedOpen ? 'rotate-0' : 'rotate-180'}`}
                  style={{ borderColor: 'rgba(255,106,44,0.2)', color: '#F55600', padding: '3px' }}
                >
                  <ChevronDown size={11} strokeWidth={2.4} className="rotate-180" />
                </span>
              </button>
              {advancedOpen && (
                <div className="flex flex-col gap-3.5">
                  <div className="grid grid-cols-2 gap-3">
                    <div className="flex flex-col gap-1.5 min-w-0">
                      <span
                        className="uppercase tracking-[0.06em]"
                        style={{ color: '#2b2440', fontSize: '11px', fontWeight: 700, fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
                      >
                        Country
                      </span>
                      <BrandSelect
                        value={sel.country}
                        onChange={(v) => emit({ ...sel, country: v, state: '', city: '', pin_code: '' })}
                        options={availableCountries.map((c) => ({ value: c.code, label: c.name }))}
                        placeholder="All"
                      />
                    </div>
                    <div className="flex flex-col gap-1.5 min-w-0">
                      <span
                        className="uppercase tracking-[0.06em]"
                        style={{ color: '#2b2440', fontSize: '11px', fontWeight: 700, fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
                      >
                        State
                      </span>
                      <BrandSelect
                        value={sel.state}
                        onChange={(v) => emit({ ...sel, state: v, city: '', pin_code: '' })}
                        options={availableStates.map((s) => ({ value: s.code, label: s.name }))}
                        placeholder="All"
                      />
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="flex flex-col gap-1.5 min-w-0">
                      <span
                        className="uppercase tracking-[0.06em]"
                        style={{ color: '#2b2440', fontSize: '11px', fontWeight: 700, fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
                      >
                        City
                      </span>
                      <BrandSelect
                        value={sel.city}
                        onChange={(v) => emit({ ...sel, city: v, pin_code: '' })}
                        options={availableCities.map((c) => ({ value: c, label: c }))}
                        placeholder="All"
                      />
                    </div>
                    <div className="flex flex-col gap-1.5 min-w-0">
                      <span
                        className="uppercase tracking-[0.06em]"
                        style={{ color: '#2b2440', fontSize: '11px', fontWeight: 700, fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"' }}
                      >
                        Pin / ZIP
                      </span>
                      <BrandSelect
                        value={sel.pin_code}
                        onChange={(v) => emit({ ...sel, pin_code: v })}
                        options={availablePins.map((p) => ({ value: p, label: p }))}
                        placeholder="All"
                      />
                    </div>
                  </div>
                </div>
              )}
            </div>

            {hasAnyFilter && (
              <button
                type="button"
                onClick={() => emit({ ...EMPTY_SEL })}
                className="w-full mt-3 py-2 uppercase tracking-[0.1em] text-[#2B2926]/55 hover:text-[#F55600] hover:bg-[#F55600]/5 rounded-lg transition-colors"
                style={{ fontSize: '11px', fontWeight: 700 }}
              >
                Clear all filters
              </button>
            )}
          </div>
        </>
      ), document.body)}
    </div>
  );
};

export { EMPTY_SEL };
export default BrandFilter;
