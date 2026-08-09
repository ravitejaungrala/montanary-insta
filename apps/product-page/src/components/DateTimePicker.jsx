import React, { useState, useRef, useEffect, useLayoutEffect } from 'react';
import { createPortal } from 'react-dom';
import { Calendar as CalendarIcon, ChevronLeft, ChevronRight } from 'lucide-react';

/**
 * DateTimePicker — a compact, brand-themed replacement for the native
 * <input type="datetime-local">. The native picker's calendar popup is
 * rendered by the browser and can't be restyled, so this component renders
 * its own small calendar + time grid in the Pipelyt palette (#F55600 /
 * black / white). Calendar and time sit side by side.
 *
 * Props:
 *   value     — string in datetime-local format "YYYY-MM-DDTHH:mm"
 *   onChange  — (nextValue) => void  (same "YYYY-MM-DDTHH:mm" string shape)
 */
const WEEKDAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];
const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

const pad = (n) => String(n).padStart(2, '0');

const parseValue = (v) => {
  if (!v || typeof v !== 'string') return null;
  // Time part is optional — supports both "YYYY-MM-DD" (date mode) and
  // "YYYY-MM-DDTHH:mm" (datetime mode).
  const m = v.match(/^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/);
  if (!m) return null;
  return { y: +m[1], m: +m[2] - 1, d: +m[3], h: +(m[4] || 0), min: +(m[5] || 0) };
};

const format = (y, m, d, h, min, dateOnly) =>
  dateOnly
    ? `${y}-${pad(m + 1)}-${pad(d)}`
    : `${y}-${pad(m + 1)}-${pad(d)}T${pad(h)}:${pad(min)}`;

/**
 * @param {'datetime'|'date'} mode — 'date' hides the time columns and the
 *   value / onChange use the "YYYY-MM-DD" shape instead of "YYYY-MM-DDTHH:mm".
 */
const DateTimePicker = ({ value, onChange, mode = 'datetime' }) => {
  const dateOnly = mode === 'date';
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const triggerRef = useRef(null);
  const menuRef = useRef(null);
  const hourListRef = useRef(null);
  const minListRef = useRef(null);
  const [rect, setRect] = useState(null);

  const parsed = parseValue(value);
  const now = new Date();

  const [viewY, setViewY] = useState(parsed ? parsed.y : now.getFullYear());
  const [viewM, setViewM] = useState(parsed ? parsed.m : now.getMonth());

  useEffect(() => {
    if (open && parsed) { setViewY(parsed.y); setViewM(parsed.m); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => {
      if (!wrapRef.current?.contains(e.target) && !menuRef.current?.contains(e.target)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  useLayoutEffect(() => {
    if (!open) { setRect(null); return undefined; }
    const reposition = () => {
      const t = triggerRef.current;
      if (!t) return;
      const r = t.getBoundingClientRect();
      const menuH = 280;
      const spaceBelow = window.innerHeight - r.bottom;
      const openUp = spaceBelow < menuH && r.top > spaceBelow;
      setRect({
        left: Math.max(8, Math.min(r.left, window.innerWidth - 388)),
        top: openUp ? null : r.bottom + 6,
        bottom: openUp ? window.innerHeight - r.top + 6 : null,
      });
    };
    reposition();
    // Capture-phase scroll catches page / ancestor scrolling so the fixed
    // menu follows the trigger. But it ALSO fires when the user scrolls the
    // hour/minute lists inside the menu — which would call setRect, re-run the
    // auto-center effect, and snap their scroll back. Skip those internal
    // scrolls so the time lists scroll freely.
    const onScroll = (e) => {
      if (menuRef.current && menuRef.current.contains(e.target)) return;
      reposition();
    };
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', reposition);
    return () => {
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', reposition);
    };
  }, [open]);

  const sel = parsed || {
    y: now.getFullYear(), m: now.getMonth(), d: now.getDate(),
    h: now.getHours(), min: now.getMinutes(),
  };

  useEffect(() => {
    if (!open || !rect) return;
    const id = requestAnimationFrame(() => {
      [[hourListRef, sel.h], [minListRef, sel.min]].forEach(([ref, val]) => {
        const c = ref.current;
        const item = c && c.children ? c.children[val] : null;
        if (c && item) c.scrollTop = item.offsetTop - c.clientHeight / 2 + item.clientHeight / 2;
      });
    });
    return () => cancelAnimationFrame(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, rect]);

  const emit = (next) => onChange?.(format(next.y, next.m, next.d, next.h, next.min, dateOnly));
  const pickDay = (d) => emit({ ...sel, y: viewY, m: viewM, d });
  const pickHour = (h) => emit({ ...sel, h });
  const pickMin = (min) => emit({ ...sel, min });

  const prevMonth = () => setViewM((m) => {
    if (m === 0) { setViewY((y) => y - 1); return 11; }
    return m - 1;
  });
  const nextMonth = () => setViewM((m) => {
    if (m === 11) { setViewY((y) => y + 1); return 0; }
    return m + 1;
  });

  const firstDow = new Date(viewY, viewM, 1).getDay();
  const daysInMonth = new Date(viewY, viewM + 1, 0).getDate();
  const cells = [];
  for (let i = 0; i < firstDow; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);

  const isSelectedDay = (d) =>
    parsed && parsed.y === viewY && parsed.m === viewM && parsed.d === d;
  const isToday = (d) =>
    now.getFullYear() === viewY && now.getMonth() === viewM && now.getDate() === d;

  const displayText = parsed
    ? (dateOnly
        ? `${pad(parsed.d)}-${pad(parsed.m + 1)}-${parsed.y}`
        : `${pad(parsed.d)}-${pad(parsed.m + 1)}-${parsed.y}   ${pad(parsed.h)}:${pad(parsed.min)}`)
    : (dateOnly ? 'Select date' : 'Select date & time');

  const menu = open && rect ? (
    <div
      ref={menuRef}
      style={{
        position: 'fixed',
        left: rect.left,
        top: rect.top ?? undefined,
        bottom: rect.bottom ?? undefined,
        width: dateOnly ? 270 : 380,
        zIndex: 10000,
      }}
      className="bg-white rounded-xl border-2 border-[#F55600] shadow-2xl p-2.5"
    >
      <div className="flex gap-2.5">
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between mb-1.5">
            <button
              type="button" onClick={prevMonth}
              className="w-6 h-6 rounded-md border-2 border-[#2B2926]/15 flex items-center justify-center text-[#2B2926] hover:border-[#F55600] hover:text-[#F55600] transition-colors"
            >
              <ChevronLeft className="w-3.5 h-3.5" />
            </button>
            <span className="text-[11px] font-black text-[#2B2926] uppercase tracking-wide">
              {MONTHS[viewM]} {viewY}
            </span>
            <button
              type="button" onClick={nextMonth}
              className="w-6 h-6 rounded-md border-2 border-[#2B2926]/15 flex items-center justify-center text-[#2B2926] hover:border-[#F55600] hover:text-[#F55600] transition-colors"
            >
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
          </div>

          <div className="grid grid-cols-7">
            {WEEKDAYS.map((w) => (
              <div key={w} className="text-center text-[8px] font-black text-[#2B2926]/45 uppercase py-0.5">{w}</div>
            ))}
          </div>

          <div className="grid grid-cols-7 gap-0.5">
            {cells.map((d, i) => d === null ? <div key={i} /> : (
              <button
                key={i}
                type="button"
                onClick={() => pickDay(d)}
                className={`h-6 rounded-md text-[10px] font-bold transition-colors ${
                  isSelectedDay(d)
                    ? 'bg-[#F55600] text-white'
                    : isToday(d)
                      ? 'text-[#F55600] border border-[#F55600]/50 hover:bg-[#F55600]/10'
                      : 'text-[#2B2926] hover:bg-[#F55600]/10'
                }`}
              >
                {d}
              </button>
            ))}
          </div>
        </div>

        {!dateOnly && (
        <div className="w-[132px] shrink-0 border-l-2 border-[#2B2926]/10 pl-2.5">
          <div className="text-[8px] font-black text-[#2B2926] uppercase tracking-widest mb-1">Time</div>
          <div className="flex gap-1.5">
            <div className="flex-1">
              <div className="text-[7px] font-black text-[#2B2926]/45 uppercase mb-0.5 text-center">Hour</div>
              <div ref={hourListRef} className="h-[148px] overflow-y-auto rounded-md border-2 border-[#2B2926]/15">
                {Array.from({ length: 24 }, (_, h) => h).map((h) => (
                  <button
                    key={h} type="button" onClick={() => pickHour(h)}
                    className={`w-full text-[11px] font-bold py-1 transition-colors ${
                      sel.h === h ? 'bg-[#F55600] text-white' : 'text-[#2B2926] hover:bg-[#F55600]/10'
                    }`}
                  >
                    {pad(h)}
                  </button>
                ))}
              </div>
            </div>
            <div className="flex-1">
              <div className="text-[7px] font-black text-[#2B2926]/45 uppercase mb-0.5 text-center">Minute</div>
              <div ref={minListRef} className="h-[148px] overflow-y-auto rounded-md border-2 border-[#2B2926]/15">
                {Array.from({ length: 60 }, (_, m) => m).map((m) => (
                  <button
                    key={m} type="button" onClick={() => pickMin(m)}
                    className={`w-full text-[11px] font-bold py-1 transition-colors ${
                      sel.min === m ? 'bg-[#F55600] text-white' : 'text-[#2B2926] hover:bg-[#F55600]/10'
                    }`}
                  >
                    {pad(m)}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
        )}
      </div>

      <div className="flex items-center justify-between mt-2 pt-1.5 border-t-2 border-[#2B2926]/10">
        <button
          type="button"
          onClick={() => {
            const n = new Date();
            emit({ y: n.getFullYear(), m: n.getMonth(), d: n.getDate(), h: n.getHours(), min: n.getMinutes() });
            setViewY(n.getFullYear());
            setViewM(n.getMonth());
          }}
          className="text-[9px] font-black uppercase tracking-widest text-[#2B2926] hover:text-[#F55600] transition-colors"
        >
          Now
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="text-[9px] font-black uppercase tracking-widest bg-[#F55600] text-white px-3.5 py-1 rounded-md hover:bg-[#2B2926] transition-colors"
        >
          Done
        </button>
      </div>
    </div>
  ) : null;

  return (
    <div ref={wrapRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full bg-white border-2 border-[#F55600]/30 rounded-xl p-3 text-sm font-bold text-[#F55600] flex items-center justify-between gap-2 hover:border-[#F55600] focus:outline-none focus:border-[#F55600] transition-all shadow-sm"
      >
        <span>{displayText}</span>
        <CalendarIcon className="w-4 h-4 text-[#F55600] shrink-0" />
      </button>
      {typeof document !== 'undefined' && menu ? createPortal(menu, document.body) : null}
    </div>
  );
};

export default DateTimePicker;
