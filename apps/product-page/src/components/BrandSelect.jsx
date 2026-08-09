import React, { useState, useRef, useEffect, useLayoutEffect } from 'react';
import { createPortal } from 'react-dom';
import { ChevronDown } from 'lucide-react';

/**
 * BrandSelect — drop-in replacement for native <select> that uses brand
 * orange (#F55600) for the selected/hovered option highlight instead of
 * the OS-default blue (which Chrome won't let CSS override on native
 * <option> elements).
 *
 * Props:
 *   value      — current selected option value
 *   onChange   — (value) => void  (matches native <select>'s onChange shape
 *                except we pass the value directly, not the event)
 *   options    — [{ value, label }] OR [string, string, ...] (strings are
 *                used for both value and label)
 *   className  — optional extra classes for the trigger button
 *   title      — tooltip on the trigger
 *   placeholder — text when value === '' (default "Select…")
 */
const BrandSelect = ({
  value = '',
  onChange,
  options = [],
  className = '',
  title,
  placeholder = 'Select…',
  // Size variants — "sm" is the original compact style used inline on
  // chart cards; "md" matches the BrandFilter popover (full-width,
  // larger touch target, slightly bigger text). Pages that opt into
  // size="md" should also pass `className="w-full"` to fill their
  // container width.
  size = 'sm',
}) => {
  const [open, setOpen] = useState(false);
  const [hoverIdx, setHoverIdx] = useState(-1);
  const wrapRef = useRef(null);
  const triggerRef = useRef(null);
  const menuRef = useRef(null);
  // Position state for the portal-rendered menu — recomputed every time
  // the dropdown opens so the menu always pins to the trigger's
  // viewport-relative coordinates (works through `overflow:auto`
  // ancestors that would otherwise clip the menu).
  const [menuRect, setMenuRect] = useState(null);

  // Normalize options to [{value, label}] regardless of input shape.
  const normalized = (options || []).map((o) =>
    typeof o === 'string' ? { value: o, label: o } : o
  );

  const selected = normalized.find((o) => o.value === value);
  const display = selected ? selected.label : placeholder;

  // Close on outside click — must check both the trigger AND the
  // portal-rendered menu since the menu now lives outside `wrapRef`.
  useEffect(() => {
    if (!open) return undefined;
    const onDocClick = (e) => {
      const inTrigger = wrapRef.current?.contains(e.target);
      const inMenu = menuRef.current?.contains(e.target);
      if (!inTrigger && !inMenu) {
        setOpen(false);
        setHoverIdx(-1);
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  // Compute menu position (relative to viewport) every time it opens
  // and on scroll/resize so it stays glued to the trigger.
  useLayoutEffect(() => {
    if (!open) { setMenuRect(null); return undefined; }
    const reposition = () => {
      const t = triggerRef.current;
      if (!t) return;
      const r = t.getBoundingClientRect();

      // Find the nearest ancestor that clips its overflow (a scrollable
      // modal body, popover, etc.). The menu must stay inside *that* box,
      // not just the viewport — otherwise it spills past the modal's
      // action buttons. Falls back to the viewport when none is found.
      let clipTop = 8;
      let clipBottom = window.innerHeight - 8;
      let node = t.parentElement;
      while (node && node !== document.body) {
        const cs = window.getComputedStyle(node);
        const clips = ['auto', 'scroll', 'hidden'].includes(cs.overflowY)
          || cs.position === 'fixed';
        if (clips) {
          const cr = node.getBoundingClientRect();
          clipTop = Math.max(clipTop, cr.top + 4);
          clipBottom = Math.min(clipBottom, cr.bottom - 4);
          break;
        }
        node = node.parentElement;
      }

      const spaceBelow = clipBottom - r.bottom;
      const spaceAbove = r.top - clipTop;
      // Open downward by default; flip up when there isn't comfortable
      // room below and the side above has more space — keeps the menu
      // inside its modal / popover instead of overflowing it.
      const openUp = spaceBelow < 220 && spaceAbove > spaceBelow;
      // Cap the menu to the room actually available on the chosen side
      // so it never spills out — the menu scrolls internally instead.
      const room = openUp ? spaceAbove : spaceBelow;
      const maxHeight = Math.max(120, Math.min(240, room - 8));
      // Effective menu width — sm trigger may be narrower than the
      // menu's min-width (160px), so use the larger so the clamp below
      // doesn't think the menu fits when it actually overflows the trigger.
      const minMenuWidth = isMd ? r.width : Math.max(r.width, 160);
      // Right-edge guard — keep the menu inside the viewport even when the
      // trigger sits near the right edge of the screen. Use clientWidth
      // (excludes the vertical scrollbar) so the menu never spills under /
      // past the scrollbar off-screen.
      const vw = document.documentElement.clientWidth;
      const maxLeft = vw - minMenuWidth - 8;
      const clampedLeft = Math.min(Math.max(8, r.left), Math.max(8, maxLeft));
      setMenuRect({
        left: clampedLeft,
        width: minMenuWidth,
        top: openUp ? null : r.bottom + 4,
        bottom: openUp ? window.innerHeight - r.top + 4 : null,
        maxHeight,
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

  // Keyboard nav: Esc closes, Up/Down move hover, Enter selects.
  const onKey = (e) => {
    if (!open) {
      if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown') {
        e.preventDefault();
        setOpen(true);
      }
      return;
    }
    if (e.key === 'Escape') {
      setOpen(false);
      setHoverIdx(-1);
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setHoverIdx((i) => Math.min(normalized.length - 1, i + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHoverIdx((i) => Math.max(0, i - 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (hoverIdx >= 0 && normalized[hoverIdx]) {
        onChange?.(normalized[hoverIdx].value);
        setOpen(false);
        setHoverIdx(-1);
      }
    }
  };

  // Size-driven base classes. `md` is wider, taller, and uses larger text
  // — matches the BrandFilter popover layout. `sm` keeps the original
  // compact look for inline chart-card dropdowns.
  const isMd = size === 'md';
  // `md` matches the BrandFilter popover field style: peach-cream background
  // (#fffaf6), subtle border that turns orange on hover/focus, slightly
  // taller touch target, Plus Jakarta Sans body. `sm` keeps the original
  // compact white look for inline chart-card filters.
  const triggerCls = isMd
    ? 'rounded-[12px] border outline-none flex items-center justify-between gap-1.5 w-full transition-all'
    : 'text-[11px] font-semibold border border-[#2B2926]/20 bg-white rounded-lg px-2.5 py-1.5 outline-none hover:border-[#2B2926]/40 focus:border-[#F55600]/60 transition-colors max-w-[160px] truncate flex items-center gap-1.5';
  const mdStyle = isMd
    ? {
        padding: '8px 11px',
        background: '#fffaf6',
        borderColor: 'rgba(43,36,64,0.1)',
        color: '#2b2440',
        fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"',
        fontSize: '13px',
        fontWeight: 600,
      }
    : { color: '#2B2926' };
  const itemCls = isMd
    ? 'text-[12px] font-semibold px-3 py-2 cursor-pointer transition-colors truncate'
    : 'text-[11px] font-semibold px-3 py-1.5 cursor-pointer transition-colors truncate';
  const wrapperCls = isMd ? 'relative w-full' : 'relative inline-block';

  // Menu rendered into a Portal at document.body so it floats above
  // any `overflow: auto` ancestor (the Brand Filter popover) — fixes
  // the bug where opening Business DNA on the Analytics filter
  // partially hid options behind the Regional Filters section.
  const menu = open && menuRect ? (
    <div
      ref={menuRef}
      style={{
        position: 'fixed',
        left: menuRect.left,
        width: menuRect.width,
        top: menuRect.top ?? undefined,
        bottom: menuRect.bottom ?? undefined,
        maxHeight: menuRect.maxHeight,
        zIndex: 10000,
      }}
      className={isMd
        ? 'bg-white border border-[#2B2926]/20 rounded-xl shadow-[0_12px_32px_rgba(43,41,38,0.16)] overflow-y-auto'
        : 'bg-white border border-[#2B2926]/20 rounded-lg shadow-[0_10px_28px_rgba(43,41,38,0.14)] overflow-y-auto min-w-[160px]'}
    >
      {normalized.map((opt, i) => {
        const isSelected = opt.value === value;
        const isHovered = i === hoverIdx;
        let bg = 'bg-white';
        let fg = 'text-[#2B2926]';
        if (isSelected) {
          // Selected option = solid brand orange.
          bg = 'bg-[#F55600]';
          fg = 'text-white';
        } else if (isHovered) {
          // Hover (un-selected) = neutral light grey, no brand colour.
          // Only the SELECTED option uses brand orange.
          bg = 'bg-[#2B2926]/[0.05]';
          fg = 'text-[#2B2926]';
        }
        return (
          <div
            key={`${opt.value}-${i}`}
            onMouseEnter={() => setHoverIdx(i)}
            onMouseLeave={() => setHoverIdx(-1)}
            onClick={() => {
              onChange?.(opt.value);
              setOpen(false);
              setHoverIdx(-1);
            }}
            className={`${itemCls} ${bg} ${fg}`}
          >
            {opt.label}
          </div>
        );
      })}
    </div>
  ) : null;

  return (
    <div ref={wrapRef} className={wrapperCls} title={title}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        onKeyDown={onKey}
        className={`${triggerCls} ${className}`}
        style={mdStyle}
        onMouseEnter={isMd ? (e) => { e.currentTarget.style.borderColor = '#F55600'; } : undefined}
        onMouseLeave={isMd ? (e) => { e.currentTarget.style.borderColor = 'rgba(43,36,64,0.1)'; } : undefined}
      >
        <span className="truncate">{display}</span>
        <ChevronDown
          size={isMd ? 16 : 12}
          strokeWidth={isMd ? 2.4 : 2}
          className={`flex-shrink-0 transition-transform ${open ? 'rotate-180' : ''}`}
          style={{ color: '#F55600' }}
        />
      </button>
      {typeof document !== 'undefined' && menu
        ? createPortal(menu, document.body)
        : null}
    </div>
  );
};

export default BrandSelect;
