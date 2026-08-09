import React, { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { ChevronLeft, ChevronRight, Calendar as CalendarIcon } from 'lucide-react';

/**
 * CalendarPicker — branded date picker matching the project's Apollo design:
 *   - Pill-style trigger with brand-orange border + calendar icon
 *   - Popup with month-nav header, day-initials row, 6×7 date grid
 *   - Today highlighted with orange ring; selected with solid orange fill
 *   - Footer with "NOW" link (jumps to today) + "DONE" button
 *
 * Props:
 *   value       — current selected date as ISO string "YYYY-MM-DD" (or empty)
 *   onChange    — (newDate: string) => void
 *   placeholder — text shown when value is empty (default "Select date")
 *   minDate     — optional lower bound (ISO string)
 *   className   — extra classes for the trigger button
 */
const CalendarPicker = ({
  value = '',
  onChange,
  placeholder = 'Select date',
  minDate,
  className = '',
}) => {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef(null);
  const menuRef = useRef(null);
  const [menuRect, setMenuRect] = useState(null);

  // Parse the value as a Date; default to today.
  const parsed = value ? new Date(`${value}T00:00:00`) : new Date();
  const safeParsed = isNaN(parsed.getTime()) ? new Date() : parsed;

  // The month currently being browsed in the popup (state independent of the
  // selected date so user can flip through months without committing).
  const [viewYear, setViewYear] = useState(safeParsed.getFullYear());
  const [viewMonth, setViewMonth] = useState(safeParsed.getMonth());

  // Reset the browsing month every time the picker opens to match the
  // currently-selected date.
  useEffect(() => {
    if (open) {
      const v = value ? new Date(`${value}T00:00:00`) : new Date();
      if (!isNaN(v.getTime())) {
        setViewYear(v.getFullYear());
        setViewMonth(v.getMonth());
      }
    }
  }, [open, value]);

  // Close on outside-click — check both the trigger AND the portal menu.
  useEffect(() => {
    if (!open) return undefined;
    const onDocClick = (e) => {
      const inTrigger = triggerRef.current?.contains(e.target);
      const inMenu = menuRef.current?.contains(e.target);
      if (!inTrigger && !inMenu) setOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  // Position the menu relative to the trigger (portalled so overflow:hidden
  // ancestors can't clip it). Recompute on scroll + resize so it stays glued.
  useEffect(() => {
    if (!open) { setMenuRect(null); return undefined; }
    const reposition = () => {
      const t = triggerRef.current;
      if (!t) return;
      const r = t.getBoundingClientRect();
      const MENU_W = 240;
      const MENU_H = 300;
      // Open below by default; flip up when there's not enough room.
      const spaceBelow = window.innerHeight - r.bottom;
      const openUp = spaceBelow < MENU_H && r.top > MENU_H;
      // Clamp left so menu stays inside the viewport.
      const left = Math.max(
        8,
        Math.min(r.left, window.innerWidth - MENU_W - 8)
      );
      setMenuRect({
        left,
        top: openUp ? null : r.bottom + 6,
        bottom: openUp ? window.innerHeight - r.top + 6 : null,
        width: MENU_W,
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

  // Build the 6×7 grid of dates for the currently-viewed month.
  const grid = (() => {
    const firstDay = new Date(viewYear, viewMonth, 1);
    const firstWeekday = firstDay.getDay(); // 0 = Sunday
    const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
    const daysInPrev = new Date(viewYear, viewMonth, 0).getDate();
    const cells = [];
    // Leading days from previous month
    for (let i = firstWeekday - 1; i >= 0; i--) {
      cells.push({ d: daysInPrev - i, inMonth: false, y: viewYear, m: viewMonth - 1 });
    }
    // Current month
    for (let d = 1; d <= daysInMonth; d++) {
      cells.push({ d, inMonth: true, y: viewYear, m: viewMonth });
    }
    // Trailing days to fill 6 rows × 7 = 42 cells
    let trailing = 1;
    while (cells.length < 42) {
      cells.push({ d: trailing, inMonth: false, y: viewYear, m: viewMonth + 1 });
      trailing++;
    }
    return cells;
  })();

  const today = new Date();
  const todayKey = `${today.getFullYear()}-${today.getMonth()}-${today.getDate()}`;
  const selectedKey = value ? (() => {
    const v = new Date(`${value}T00:00:00`);
    return isNaN(v.getTime()) ? '' : `${v.getFullYear()}-${v.getMonth()}-${v.getDate()}`;
  })() : '';
  const minKey = minDate ? (() => {
    const v = new Date(`${minDate}T00:00:00`);
    return isNaN(v.getTime()) ? null : v;
  })() : null;

  const monthName = new Date(viewYear, viewMonth, 1).toLocaleDateString('en-US', { month: 'long' }).toUpperCase();

  const prevMonth = () => {
    if (viewMonth === 0) {
      setViewMonth(11);
      setViewYear(viewYear - 1);
    } else {
      setViewMonth(viewMonth - 1);
    }
  };
  const nextMonth = () => {
    if (viewMonth === 11) {
      setViewMonth(0);
      setViewYear(viewYear + 1);
    } else {
      setViewMonth(viewMonth + 1);
    }
  };

  const pickCell = (cell) => {
    if (!cell) return;
    const d = new Date(cell.y, cell.m, cell.d);
    if (minKey && d < minKey) return;
    const iso = `${cell.y}-${String(cell.m + 1).padStart(2, '0')}-${String(cell.d).padStart(2, '0')}`;
    onChange?.(iso);
  };

  const jumpToToday = () => {
    setViewYear(today.getFullYear());
    setViewMonth(today.getMonth());
    const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
    onChange?.(iso);
  };

  const displayLabel = value
    ? (() => {
        const v = new Date(`${value}T00:00:00`);
        if (isNaN(v.getTime())) return value;
        return `${String(v.getDate()).padStart(2, '0')}-${String(v.getMonth() + 1).padStart(2, '0')}-${v.getFullYear()}`;
      })()
    : placeholder;

  // Render menu via portal so overflow:hidden ancestors can't clip it.
  const menu = open && menuRect ? (
    <div
      ref={menuRef}
      style={{
        position: 'fixed',
        left: menuRect.left,
        top: menuRect.top ?? undefined,
        bottom: menuRect.bottom ?? undefined,
        width: menuRect.width,
        zIndex: 10000,
      }}
      className="bg-white border-2 border-[#F55600] rounded-2xl shadow-[0_18px_40px_rgba(43,41,38,0.22)] p-3 select-none"
    >
      {/* Month nav header */}
      <div className="flex items-center justify-between mb-2">
        <button
          type="button"
          onClick={prevMonth}
          className="w-6 h-6 rounded-md border border-[#2B2926]/20 bg-white text-[#2B2926] hover:border-[#F55600] hover:text-[#F55600] flex items-center justify-center transition-colors"
          aria-label="Previous month"
        >
          <ChevronLeft className="w-3.5 h-3.5" />
        </button>
        <div className="text-[12px] font-bold text-[#2B2926] tracking-[0.14em] uppercase">
          {monthName} {viewYear}
        </div>
        <button
          type="button"
          onClick={nextMonth}
          className="w-6 h-6 rounded-md border border-[#2B2926]/20 bg-white text-[#2B2926] hover:border-[#F55600] hover:text-[#F55600] flex items-center justify-center transition-colors"
          aria-label="Next month"
        >
          <ChevronRight className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Weekday header strip */}
      <div className="grid grid-cols-7 gap-1 mb-1">
        {['SU', 'MO', 'TU', 'WE', 'TH', 'FR', 'SA'].map((w) => (
          <div key={w} className="text-[9px] font-bold text-[#2B2926]/60 uppercase tracking-[0.08em] text-center py-0.5">{w}</div>
        ))}
      </div>

      {/* Day grid */}
      <div className="grid grid-cols-7 gap-1">
        {grid.map((cell, i) => {
          const key = `${cell.y}-${cell.m}-${cell.d}`;
          const isToday = key === todayKey;
          const isSelected = key === selectedKey;
          const d = new Date(cell.y, cell.m, cell.d);
          const isDisabled = minKey ? d < minKey : false;
          return (
            <button
              key={i}
              type="button"
              onClick={() => !isDisabled && pickCell(cell)}
              disabled={isDisabled}
              className={`h-7 rounded-md text-[11px] font-semibold transition-all
                ${isSelected
                  ? 'bg-[#F55600] text-white shadow-md shadow-[#F55600]/30'
                  : isToday
                  ? 'border-2 border-[#F55600] text-[#F55600] bg-white'
                  : cell.inMonth
                  ? 'text-[#2B2926] hover:bg-[#F55600]/10 hover:text-[#F55600]'
                  : 'text-[#2B2926]/25 hover:bg-[#2B2926]/[0.04]'
                }
                ${isDisabled ? 'opacity-30 cursor-not-allowed' : ''}
              `}
            >
              {cell.d}
            </button>
          );
        })}
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between mt-3 pt-2 border-t border-[#2B2926]/10">
        <button
          type="button"
          onClick={jumpToToday}
          className="text-[10px] font-bold uppercase tracking-[0.14em] text-[#2B2926] hover:text-[#F55600] transition-colors"
        >
          Now
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="px-3 py-1 bg-[#F55600] text-white text-[10px] font-bold uppercase tracking-[0.14em] rounded-md hover:bg-[#e65a2b] transition-colors shadow-md shadow-[#F55600]/25"
        >
          Done
        </button>
      </div>
    </div>
  ) : null;

  return (
    <div className="relative inline-block w-full">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`w-full px-3 py-2 bg-white border-2 border-[#F55600]/30 rounded-xl flex items-center justify-between gap-2 cursor-pointer hover:border-[#F55600] hover:bg-[#F55600]/[0.03] transition-all shadow-sm active:scale-[0.98] ${className}`}
      >
        <div className="flex items-center gap-2 min-w-0">
          <CalendarIcon className="w-3.5 h-3.5 text-[#F55600] shrink-0" />
          <span className={`text-[12px] font-bold truncate ${value ? 'text-[#2B2926]' : 'text-[#2B2926]/45'}`}>
            {displayLabel}
          </span>
        </div>
        <ChevronRight className={`w-3.5 h-3.5 text-[#F55600] shrink-0 transition-transform ${open ? 'rotate-90' : ''}`} />
      </button>
      {typeof document !== 'undefined' && menu ? createPortal(menu, document.body) : null}
    </div>
  );
};

export default CalendarPicker;
