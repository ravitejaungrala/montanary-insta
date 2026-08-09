/**
 * NexusBookingsCalendar — full-calendar view used inside NexusBookings.jsx.
 *
 * Legacy parity: month/week/day/year toggles, mini-calendar sidebar with
 * "TODAY" widget, booking pills rendered on date cells. Click a date to
 * filter the right pane; click a booking to open detail (modal or panel).
 *
 * Receives `bookings` array (already loaded by parent) so this stays a
 * pure-presentational component. Date logic is local.
 */
import React, { useEffect, useMemo, useState } from 'react';

// Shared mobile-breakpoint hook (< md / 768px). Used so the month-grid
// day popover can render as a fixed, centered sheet on phones (otherwise
// the horizontal-scroll wrapper clips it and it can't be scrolled into view).
function useIsMobile() {
  const [isMobile, setIsMobile] = useState(
    typeof window !== 'undefined' ? window.innerWidth < 768 : false,
  );
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const mq = window.matchMedia('(max-width: 767px)');
    const onChange = (e) => setIsMobile(e.matches);
    setIsMobile(mq.matches);
    if (mq.addEventListener) mq.addEventListener('change', onChange);
    else mq.addListener(onChange);
    return () => {
      if (mq.removeEventListener) mq.removeEventListener('change', onChange);
      else mq.removeListener(onChange);
    };
  }, []);
  return isMobile;
}
import {
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Clock,
  ExternalLink,
  Search,
  User as UserIcon,
} from 'lucide-react';

const VIEWS = [
  { id: 'day',   label: 'Day' },
  { id: 'week',  label: 'Week' },
  { id: 'month', label: 'Month' },
  { id: 'year',  label: 'Year' },
];

const DAY_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const DAY_LABELS_SHORT = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];

function sameDay(a, b) {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}
function startOfWeek(d) {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  x.setDate(x.getDate() - x.getDay());
  return x;
}
function startOfMonth(d) {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}
function endOfMonth(d) {
  return new Date(d.getFullYear(), d.getMonth() + 1, 0);
}
function addMonths(d, n) {
  return new Date(d.getFullYear(), d.getMonth() + n, 1);
}
function weekNumber(d) {
  const onejan = new Date(d.getFullYear(), 0, 1);
  return Math.ceil(((d - onejan) / 86400000 + onejan.getDay() + 1) / 7);
}
function fmtTime(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleTimeString(undefined, {
      hour: 'numeric',
      minute: '2-digit',
    });
  } catch {
    return '';
  }
}
function bookingDate(b) {
  return new Date(b.scheduled_at || b.start_time || b.created_at);
}

// ── Mini-calendar (left sidebar) ─────────────────────────────────────────────

const MiniCalendar = ({ anchorDate, setAnchorDate, bookings, onPickDay }) => {
  const monthStart = startOfMonth(anchorDate);
  const monthEnd = endOfMonth(anchorDate);
  const gridStart = startOfWeek(monthStart);
  const totalCells = Math.ceil((daysBetween(monthEnd, gridStart) + 1) / 7) * 7;

  const cells = useMemo(() => {
    const out = [];
    for (let i = 0; i < totalCells; i++) {
      const d = new Date(gridStart);
      d.setDate(gridStart.getDate() + i);
      out.push(d);
    }
    return out;
  }, [gridStart, totalCells]);

  const bookingDays = useMemo(() => {
    const set = new Set();
    for (const b of bookings) {
      const d = bookingDate(b);
      set.add(d.toDateString());
    }
    return set;
  }, [bookings]);

  const today = new Date();

  return (
    // White-card mini calendar — matches the rest of the app's
    // light surface treatment. Today gets an orange filled circle
    // (was blue), days with bookings get a tiny orange dot beneath.
    <div
      className="select-none"
      style={{
        background: '#FFFFFF',
        color: '#2B2926',
        border: '1px solid #EAEAE6',
        borderRadius: 14,
        padding: 12,
        boxShadow: '0 2px 4px rgba(20,16,12,0.04), 0 10px 28px rgba(20,16,12,0.04)',
      }}
    >
      <div className="flex items-center justify-between mb-2">
        <div className="text-[12px] ">
          {anchorDate.toLocaleDateString(undefined, { month: 'long' })}{' '}
          <span style={{ color: '#F55600' }}>{anchorDate.getFullYear()}</span>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setAnchorDate(addMonths(anchorDate, -1))}
            className="w-5 h-5 inline-flex items-center justify-center rounded hover:bg-[#FFEEE6]"
            style={{ color: '#2B2926' }}
          >
            <ChevronLeft className="w-3 h-3" />
          </button>
          <button
            type="button"
            onClick={() => setAnchorDate(addMonths(anchorDate, 1))}
            className="w-5 h-5 inline-flex items-center justify-center rounded hover:bg-[#FFEEE6]"
            style={{ color: '#2B2926' }}
          >
            <ChevronRight className="w-3 h-3" />
          </button>
        </div>
      </div>

      {/* Header row */}
      <div
        className="grid grid-cols-8 text-[8px]  uppercase mb-1"
        style={{ color: '#2B2926' }}
      >
        <div className="text-center">CW</div>
        {DAY_LABELS_SHORT.map((d, i) => (
          <div key={i} className="text-center">
            {d}
          </div>
        ))}
      </div>

      {/* Date grid w/ week-number col */}
      <div className="grid grid-cols-8 gap-y-1 text-[10px]">
        {Array.from({ length: Math.ceil(cells.length / 7) }).map((_, weekIdx) => {
          const weekStart = cells[weekIdx * 7];
          return (
            <React.Fragment key={weekIdx}>
              <div className="text-center" style={{ color: '#2B2926' }}>
                {weekNumber(weekStart)}
              </div>
              {cells.slice(weekIdx * 7, weekIdx * 7 + 7).map((d) => {
                const isCurMonth = d.getMonth() === anchorDate.getMonth();
                const isToday = sameDay(d, today);
                const hasBooking = bookingDays.has(d.toDateString());
                const baseStyle = {
                  width: 22,
                  height: 22,
                  margin: '0 auto',
                  borderRadius: '50%',
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 10,
                  fontWeight: isToday || hasBooking ? 700 : 500,
                  border: 'none',
                  background: 'transparent',
                  cursor: 'pointer',
                  transition: 'background 0.12s, color 0.12s',
                };
                const stateStyle = isToday
                  ? { background: '#F55600', color: '#fff' }
                  : isCurMonth
                  ? hasBooking
                    ? { color: '#2B2926' }
                    : { color: '#2B2926' }
                  : { color: '#2B2926' };
                return (
                  <button
                    key={d.toISOString()}
                    type="button"
                    onClick={() => onPickDay && onPickDay(d)}
                    style={{ ...baseStyle, ...stateStyle }}
                    onMouseEnter={(e) => {
                      if (!isToday) e.currentTarget.style.background = '#FFEEE6';
                    }}
                    onMouseLeave={(e) => {
                      if (!isToday) e.currentTarget.style.background = 'transparent';
                    }}
                  >
                    {d.getDate()}
                  </button>
                );
              })}
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
};

function daysBetween(a, b) {
  return Math.round((a.getTime() - b.getTime()) / 86400000);
}

// ── Month grid (main pane) ───────────────────────────────────────────────────

const MonthGrid = ({ anchorDate, bookings, onPickBooking }) => {
  // Which day's bookings popover is open (keyed by toDateString()).
  const [popoverKey, setPopoverKey] = useState(null);
  const isMobile = useIsMobile();
  const monthStart = startOfMonth(anchorDate);
  const monthEnd = endOfMonth(anchorDate);
  const gridStart = startOfWeek(monthStart);
  const totalCells = Math.ceil((daysBetween(monthEnd, gridStart) + 1) / 7) * 7;
  const cells = useMemo(() => {
    const out = [];
    for (let i = 0; i < totalCells; i++) {
      const d = new Date(gridStart);
      d.setDate(gridStart.getDate() + i);
      out.push(d);
    }
    return out;
  }, [gridStart, totalCells]);

  const bookingsByDay = useMemo(() => {
    const map = new Map();
    for (const b of bookings) {
      const k = bookingDate(b).toDateString();
      if (!map.has(k)) map.set(k, []);
      map.get(k).push(b);
    }
    return map;
  }, [bookings]);

  const today = new Date();

  // Match the social-calendar UI: rounded outer card, sticky-style
  // header row, soft 1px dividers, today highlighted with an orange
  // border + filled date pill, booking pills as mint capsules with
  // a small bullet and the attendee name truncated.
  const monthLabelShort = (d) =>
    d.toLocaleDateString(undefined, { month: 'short' }).toUpperCase();
  return (
    <div
      className="flex flex-col"
      style={{
        background: '#FFFFFF',
        border: '1px solid #D4D2CC',
        borderRadius: 18,
        boxShadow: '0 2px 4px rgba(20,16,12,0.04), 0 10px 28px rgba(20,16,12,0.05)',
        // overflow visible (not hidden) so day popovers can extend past
        // the card edge instead of being clipped.
        overflow: 'visible',
      }}
    >
      {/* Day-of-week header row */}
      <div
        className="grid grid-cols-7"
        style={{
          borderBottom: '1px solid #D4D2CC',
          fontSize: 11,
          color: '#2B2926',
          // Bumped from 400 → 600 so the SUN/MON/TUE strip reads as a
          // proper crisp header instead of dull thin text. Matches the
          // weight Apollo uses on day-of-week labels.
          fontWeight: 600,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
        }}
      >
        {DAY_LABELS.map((d) => (
          <div key={d} className="px-2 py-2 text-center">
            {d}
          </div>
        ))}
      </div>

      {/* Grid cells — taller rows to use the available vertical space
          below the calendar without stretching edge-to-edge. */}
      <div
        className="grid grid-cols-7"
        style={{
          gridAutoRows: '88px',
        }}
      >
        {cells.map((d, cellIdx) => {
          const isCurMonth = d.getMonth() === anchorDate.getMonth();
          const isToday = sameDay(d, today);
          const dayBookings = bookingsByDay.get(d.toDateString()) || [];
          // Bottom-row days: open the popover UPWARD so it isn't clipped below
          // the calendar grid (the "showing under the table" issue).
          const _totalRows = Math.ceil(cells.length / 7);
          const _openUp = Math.floor(cellIdx / 7) >= _totalRows - 2;
          return (
            <div
              key={d.toISOString()}
              style={{
                borderRight: '1px solid #E3E1DB',
                borderBottom: '1px solid #E3E1DB',
                padding: 6,
                // overflow visible so an open bookings popover isn't
                // clipped by the cell; z-index lifts the active cell
                // above its neighbours while its popover is open.
                overflow: 'visible',
                background: isCurMonth ? '#FFFFFF' : '#FAFAF8',
                boxShadow: isToday ? 'inset 0 0 0 2px #F55600' : 'none',
                borderRadius: isToday ? 10 : 0,
                position: 'relative',
                zIndex: popoverKey === d.toDateString() ? 50 : 'auto',
              }}
            >
              {/* Date label — top-left, "DD MMM" style like the social calendar */}
              <div className="flex items-baseline gap-1.5" style={{ marginBottom: 4 }}>
                <span
                  style={{
                    fontSize: isToday ? 13 : 12.5,
                    fontWeight: isToday ? 800 : 700,
                    color: isToday ? '#F55600' : isCurMonth ? '#2B2926' : '#2B2926',
                    fontFamily: '"ABC Diatype", "ABC Diatype", system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"',
                  }}
                >
                  {d.getDate()}
                </span>
                <span
                  style={{
                    fontSize: 9.5,
                    fontWeight: 500,
                    letterSpacing: '0.05em',
                    color: isToday ? '#F55600' : isCurMonth ? '#2B2926' : '#2B2926',
                  }}
                >
                  {monthLabelShort(d)}
                </span>
              </div>
              {/* Booking summary — a single "N demo(s)" pill regardless of
                  how many bookings the day has, so cells never overflow.
                  Clicking opens a popover listing each booking; each row
                  in the popover routes to onPickBooking. */}
              {dayBookings.length > 0 && (
                // Show the first booking as a full time + name pill. If
                // there are more, a "+N" pill sits beneath it. Either
                // pill opens the popover listing every booking that day.
                <div className="space-y-1">
                  {(() => {
                    const first = dayBookings[0];
                    const firstName =
                      first.attendee_name ||
                      first.attendee_email?.split('@')[0] ||
                      'Booking';
                    const open = (e) => {
                      e.stopPropagation();
                      setPopoverKey((k) =>
                        k === d.toDateString() ? null : d.toDateString(),
                      );
                    };
                    return (
                      <>
                        <button
                          type="button"
                          onClick={open}
                          className="w-full inline-flex items-center gap-1.5 truncate"
                          style={{
                            padding: '4px 8px',
                            borderRadius: 8,
                            background: 'rgba(16,185,129,0.12)',
                            color: '#0F7C53',
                            fontSize: 10.5,
                            fontWeight: 500,
                            textAlign: 'left',
                            border: 'none',
                            cursor: 'pointer',
                            transition: 'background 0.12s',
                          }}
                          onMouseEnter={(e) =>
                            (e.currentTarget.style.background = 'rgba(16,185,129,0.22)')
                          }
                          onMouseLeave={(e) =>
                            (e.currentTarget.style.background = 'rgba(16,185,129,0.12)')
                          }
                        >
                          <span className="truncate">
                            {fmtTime(first.scheduled_at || first.start_time)} {firstName}
                          </span>
                        </button>
                        {dayBookings.length > 1 && (
                          <div className="flex justify-center">
                            <button
                              type="button"
                              onClick={open}
                              className="inline-flex items-center justify-center"
                              style={{
                                padding: '2px 10px',
                                borderRadius: 8,
                                background: '#F3F3F1',
                                color: '#2B2926',
                                fontSize: 10,
                                fontWeight: 500,
                                border: 'none',
                                cursor: 'pointer',
                                width: 'fit-content',
                                transition: 'background 0.12s',
                              }}
                              onMouseEnter={(e) =>
                                (e.currentTarget.style.background = '#E3E1DB')
                              }
                              onMouseLeave={(e) =>
                                (e.currentTarget.style.background = '#F3F3F1')
                              }
                            >
                              +{dayBookings.length - 1}
                            </button>
                          </div>
                        )}
                      </>
                    );
                  })()}
                </div>
              )}

              {/* Popover — anchored bottom-left of the cell, lists each
                  booking for this day with time + attendee. */}
              {popoverKey === d.toDateString() && dayBookings.length > 0 && (
                <>
                  <div
                    onClick={(e) => {
                      e.stopPropagation();
                      setPopoverKey(null);
                    }}
                    style={{
                      position: 'fixed',
                      inset: 0,
                      zIndex: 60,
                      // Dim the screen on mobile so the centered sheet reads
                      // as a modal; transparent on desktop (anchored popover).
                      background: isMobile ? 'rgba(15,17,21,0.35)' : 'transparent',
                    }}
                  />
                  <div
                    onClick={(e) => e.stopPropagation()}
                    style={
                      isMobile
                        ? {
                            // Fixed, centered sheet — escapes the horizontal
                            // scroll wrapper that was clipping the popover.
                            position: 'fixed',
                            left: '50%',
                            top: '50%',
                            transform: 'translate(-50%, -50%)',
                            zIndex: 70,
                            width: 'min(320px, 88vw)',
                            maxHeight: '70vh',
                            overflowY: 'auto',
                            background: '#FFFFFF',
                            border: '1px solid #EAEAE6',
                            borderRadius: 14,
                            boxShadow: '0 20px 48px rgba(20,16,12,0.28)',
                            padding: 10,
                          }
                        : {
                            position: 'absolute',
                            // Open upward for bottom-row days so the list isn't
                            // clipped below the calendar grid.
                            ...(_openUp
                              ? { bottom: 'calc(100% - 4px)' }
                              : { top: 'calc(100% - 4px)' }),
                            left: 4,
                            zIndex: 70,
                            minWidth: 220,
                            maxWidth: 280,
                            maxHeight: 260,
                            overflowY: 'auto',
                            background: '#FFFFFF',
                            border: '1px solid #EAEAE6',
                            borderRadius: 12,
                            boxShadow: '0 12px 28px rgba(20,16,12,0.16)',
                            padding: 8,
                          }
                    }
                  >
                    <div
                      style={{
                        fontSize: 10,
                        fontWeight: 500,
                        textTransform: 'uppercase',
                        letterSpacing: '0.08em',
                        color: '#2B2926',
                        padding: '2px 6px 6px',
                      }}
                    >
                      {d.toLocaleDateString(undefined, {
                        weekday: 'short',
                        month: 'short',
                        day: 'numeric',
                      })}{' '}
                      · {dayBookings.length} demo
                      {dayBookings.length === 1 ? '' : 's'}
                    </div>
                    <div className="space-y-1">
                      {dayBookings.map((b) => (
                        <button
                          key={b.id || b._id}
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setPopoverKey(null);
                            onPickBooking && onPickBooking(b);
                          }}
                          className="w-full inline-flex items-center gap-2 truncate"
                          style={{
                            padding: '6px 8px',
                            borderRadius: 8,
                            background: 'rgba(16,185,129,0.08)',
                            color: '#2B2926',
                            fontSize: 11.5,
                            fontWeight: 600,
                            textAlign: 'left',
                            border: 'none',
                            cursor: 'pointer',
                          }}
                          onMouseEnter={(e) =>
                            (e.currentTarget.style.background =
                              'rgba(16,185,129,0.18)')
                          }
                          onMouseLeave={(e) =>
                            (e.currentTarget.style.background =
                              'rgba(16,185,129,0.08)')
                          }
                        >
                          <span
                            style={{
                              width: 5,
                              height: 5,
                              borderRadius: '50%',
                              background: '#10B981',
                              flexShrink: 0,
                            }}
                          />
                          <span style={{ color: '#0F7C53', fontWeight: 500 }}>
                            {fmtTime(b.scheduled_at || b.start_time)}
                          </span>
                          <span className="truncate">
                            {b.attendee_name ||
                              b.attendee_email?.split('@')[0] ||
                              'Booking'}
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

// ── Time-grid helpers (Week + Day views) ─────────────────────────────────────
// Render a vertical hour ruler (DAY_START..DAY_END) with events positioned
// absolutely by their start time + duration — the MS-Teams-style layout the
// user asked for, instead of a stacked line-by-line list.
const DAY_START_HOUR = 8;   // 8 AM
const DAY_END_HOUR = 20;    // 8 PM
const HOUR_H = 52;          // px per hour row
const HOURS = Array.from(
  { length: DAY_END_HOUR - DAY_START_HOUR + 1 },
  (_, i) => DAY_START_HOUR + i,
);
function fmtHourLabel(h) {
  const ampm = h < 12 ? 'AM' : 'PM';
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12} ${ampm}`;
}
function bookingEndMs(b) {
  if (b.end_time) return new Date(b.end_time).getTime();
  return bookingDate(b).getTime() + 30 * 60000; // default 30-min slot
}
// → { top, height } in px for an event block within a day column.
function eventBox(b) {
  const start = bookingDate(b);
  const startMins = start.getHours() * 60 + start.getMinutes();
  const durMins = Math.max(24, (bookingEndMs(b) - start.getTime()) / 60000);
  const top = ((startMins - DAY_START_HOUR * 60) / 60) * HOUR_H;
  const height = (durMins / 60) * HOUR_H;
  return { top: Math.max(0, top), height };
}
const GRID_HEIGHT = (DAY_END_HOUR - DAY_START_HOUR) * HOUR_H;

const EventBlock = ({ booking, onPick }) => {
  const { top, height } = eventBox(booking);
  return (
    <button
      type="button"
      onClick={() => onPick && onPick(booking)}
      className="absolute text-left overflow-hidden flex flex-col justify-center"
      style={{
        top,
        // Min height of 46px so both the time line and the attendee
        // name are always fully visible (a 30-min slot would otherwise
        // clip the name).
        height: Math.max(46, height - 2),
        left: 3,
        right: 3,
        background: 'rgba(16,185,129,0.16)',
        borderLeft: '3px solid #0F9F63',
        borderRadius: 7,
        padding: '4px 8px',
        cursor: 'pointer',
        transition: 'background 0.12s',
      }}
      onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(16,185,129,0.28)')}
      onMouseLeave={(e) => (e.currentTarget.style.background = 'rgba(16,185,129,0.16)')}
    >
      <div style={{ fontSize: 11, fontWeight: 500, color: '#0B6E47', lineHeight: 1.25 }}>
        {fmtTime(booking.scheduled_at || booking.start_time)}
      </div>
      <div
        className="truncate"
        style={{ fontSize: 11.5, color: '#0F1115', fontWeight: 500, lineHeight: 1.3 }}
      >
        {booking.attendee_name || booking.attendee_email || 'Booking'}
      </div>
    </button>
  );
};

// Shared hour ruler column.
const HourRuler = () => (
  <div style={{ width: 52, flexShrink: 0 }}>
    {/* spacer matching the day-header height */}
    <div style={{ height: 40, borderBottom: '1px solid #E3E1DB' }} />
    {HOURS.slice(0, -1).map((h) => (
      <div
        key={h}
        style={{
          height: HOUR_H,
          position: 'relative',
          borderRight: '1px solid #E3E1DB',
        }}
      >
        <span
          style={{
            position: 'absolute',
            top: 4,
            right: 8,
            fontSize: 10.5,
            fontWeight: 500,
            color: '#2B2926',
          }}
        >
          {fmtHourLabel(h)}
        </span>
      </div>
    ))}
  </div>
);

// ── Week grid (time-grid) ─────────────────────────────────────────────────────

const WeekGrid = ({ anchorDate, bookings, onPickBooking }) => {
  const wkStart = startOfWeek(anchorDate);
  const days = Array.from({ length: 7 }).map((_, i) => {
    const d = new Date(wkStart);
    d.setDate(wkStart.getDate() + i);
    return d;
  });
  const bookingsByDay = useMemo(() => {
    const map = new Map();
    for (const b of bookings) {
      const k = bookingDate(b).toDateString();
      if (!map.has(k)) map.set(k, []);
      map.get(k).push(b);
    }
    return map;
  }, [bookings]);

  const today = new Date();
  return (
    <div className="flex-1 overflow-auto">
      <div className="flex" style={{ minWidth: 640 }}>
        <HourRuler />
        <div className="flex-1 grid grid-cols-7">
          {days.map((d) => {
            const isToday = sameDay(d, today);
            const dayBookings = bookingsByDay.get(d.toDateString()) || [];
            return (
              <div key={d.toISOString()} style={{ borderRight: '1px solid #E3E1DB' }}>
                {/* Day header */}
                <div
                  style={{
                    height: 40,
                    borderBottom: '1px solid #E3E1DB',
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  <span style={{ fontSize: 9, fontWeight: 500, letterSpacing: '0.06em', textTransform: 'uppercase', color: '#2B2926' }}>
                    {DAY_LABELS[d.getDay()]}
                  </span>
                  <span
                    style={{
                      fontSize: 14,
                      fontWeight: 500,
                      color: isToday ? '#F55600' : '#2B2926',
                      lineHeight: 1.1,
                    }}
                  >
                    {d.getDate()}
                  </span>
                </div>
                {/* Hour rows + positioned events */}
                <div style={{ position: 'relative', height: GRID_HEIGHT }}>
                  {HOURS.slice(0, -1).map((h) => (
                    <div
                      key={h}
                      style={{ height: HOUR_H, borderBottom: '1px solid #F1EFEA' }}
                    />
                  ))}
                  {dayBookings.map((b) => (
                    <EventBlock key={b.id || b._id} booking={b} onPick={onPickBooking} />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};

// ── Day grid (time-grid) ───────────────────────────────────────────────────────

const DayGrid = ({ anchorDate, bookings, onPickBooking }) => {
  const dayBookings = useMemo(
    () =>
      bookings
        .filter((b) => sameDay(bookingDate(b), anchorDate))
        .sort((a, b) => bookingDate(a).getTime() - bookingDate(b).getTime()),
    [bookings, anchorDate],
  );
  const isToday = sameDay(anchorDate, new Date());

  return (
    <div className="flex-1 overflow-auto">
      <div className="flex">
        <HourRuler />
        <div className="flex-1" style={{ borderRight: '1px solid #E3E1DB' }}>
          {/* Day header */}
          <div
            style={{
              height: 40,
              borderBottom: '1px solid #E3E1DB',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '0 14px',
            }}
          >
            <span style={{ fontSize: 10, fontWeight: 500, letterSpacing: '0.06em', textTransform: 'uppercase', color: '#2B2926' }}>
              {DAY_LABELS[anchorDate.getDay()]}
            </span>
            <span
              style={{
                fontSize: 16,
                fontWeight: 500,
                color: isToday ? '#F55600' : '#2B2926',
              }}
            >
              {anchorDate.toLocaleDateString(undefined, { month: 'long', day: 'numeric' })}
            </span>
          </div>
          {/* Hour rows + positioned events */}
          <div style={{ position: 'relative', height: GRID_HEIGHT }}>
            {HOURS.slice(0, -1).map((h) => (
              <div key={h} style={{ height: HOUR_H, borderBottom: '1px solid #F1EFEA' }} />
            ))}
            {dayBookings.map((b) => (
              <EventBlock key={b.id || b._id} booking={b} onPick={onPickBooking} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

// ── Year grid (12 mini-months) ───────────────────────────────────────────────

const YearGrid = ({ anchorDate, bookings, onPickMonth }) => {
  const year = anchorDate.getFullYear();
  const months = Array.from({ length: 12 }).map((_, i) => new Date(year, i, 1));

  const bookingDays = useMemo(() => {
    const set = new Set();
    for (const b of bookings) {
      const d = bookingDate(b);
      if (d.getFullYear() === year) set.add(d.toDateString());
    }
    return set;
  }, [bookings, year]);

  const today = new Date();
  return (
    <div className="flex-1 p-4 grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
      {months.map((m) => {
        const mStart = startOfMonth(m);
        const mEnd = endOfMonth(m);
        const gridStart = startOfWeek(mStart);
        const totalCells = Math.ceil((daysBetween(mEnd, gridStart) + 1) / 7) * 7;
        const cells = [];
        for (let i = 0; i < totalCells; i++) {
          const d = new Date(gridStart);
          d.setDate(gridStart.getDate() + i);
          cells.push(d);
        }
        return (
          <button
            key={m.toISOString()}
            type="button"
            onClick={() => onPickMonth && onPickMonth(m)}
            className="text-left border border-[#2B2926]/10 rounded-lg p-2 hover:bg-[#F55600]/5"
          >
            <div className="text-xs  text-[#2B2926] mb-1">
              {m.toLocaleDateString(undefined, { month: 'long' })}
            </div>
            <div className="grid grid-cols-7 gap-y-0.5 text-[9px]">
              {DAY_LABELS_SHORT.map((d, i) => (
                <div key={i} className="text-center text-[#2B2926]">
                  {d}
                </div>
              ))}
              {cells.map((d) => {
                const isCur = d.getMonth() === m.getMonth();
                const isToday = sameDay(d, today);
                const hasBooking = bookingDays.has(d.toDateString());
                return (
                  <div
                    key={d.toISOString()}
                    className={[
                      'text-center h-3.5 leading-3.5',
                      isToday
                        ? 'bg-blue-500 text-white  rounded-full'
                        : isCur
                        ? hasBooking
                          ? 'text-[#10B981] '
                          : 'text-[#2B2926]'
                        : 'text-[#2B2926]',
                    ].join(' ')}
                  >
                    {d.getDate()}
                  </div>
                );
              })}
            </div>
          </button>
        );
      })}
    </div>
  );
};

// ── Main calendar wrapper ────────────────────────────────────────────────────

const NexusBookingsCalendar = ({ bookings = [], onPickBooking }) => {
  const [anchorDate, setAnchorDate] = useState(new Date());
  const [view, setView] = useState('month');
  const [search, setSearch] = useState('');

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return bookings;
    return bookings.filter((b) =>
      [b.attendee_name, b.attendee_email, b.rep_name, b.rep_email]
        .filter(Boolean)
        .some((v) => String(v).toLowerCase().includes(q)),
    );
  }, [bookings, search]);

  const todayBookings = useMemo(
    () => filtered.filter((b) => sameDay(bookingDate(b), new Date())),
    [filtered],
  );

  const goToday = () => setAnchorDate(new Date());
  const goPrev = () => {
    if (view === 'month') setAnchorDate(addMonths(anchorDate, -1));
    else if (view === 'week') {
      const d = new Date(anchorDate);
      d.setDate(d.getDate() - 7);
      setAnchorDate(d);
    } else if (view === 'day') {
      const d = new Date(anchorDate);
      d.setDate(d.getDate() - 1);
      setAnchorDate(d);
    } else if (view === 'year') {
      setAnchorDate(new Date(anchorDate.getFullYear() - 1, 0, 1));
    }
  };
  const goNext = () => {
    if (view === 'month') setAnchorDate(addMonths(anchorDate, 1));
    else if (view === 'week') {
      const d = new Date(anchorDate);
      d.setDate(d.getDate() + 7);
      setAnchorDate(d);
    } else if (view === 'day') {
      const d = new Date(anchorDate);
      d.setDate(d.getDate() + 1);
      setAnchorDate(d);
    } else if (view === 'year') {
      setAnchorDate(new Date(anchorDate.getFullYear() + 1, 0, 1));
    }
  };

  return (
    <div
      className="grid grid-cols-1 md:grid-cols-[280px_1fr]"
      style={{ minHeight: 600 }}
    >
      {/* Left sidebar — mini-calendar + today widget. On mobile it stacks
          above the main calendar grid (border switches from right to bottom). */}
      <div className="border-b md:border-b-0 md:border-r border-[#2B2926]/10 p-3 space-y-3">
        <MiniCalendar
          anchorDate={anchorDate}
          setAnchorDate={setAnchorDate}
          bookings={filtered}
          onPickDay={(d) => {
            setAnchorDate(d);
            setView('day');
          }}
        />
        <div
          className="rounded-lg p-3"
          style={{ background: '#FFFFFF', border: '1px solid #EAEAE6' }}
        >
          <div
            className="text-[10px] uppercase tracking-wider  mb-1.5"
            style={{ color: '#2B2926' }}
          >
            Today {new Date().toLocaleDateString(undefined, {
              month: 'numeric', day: 'numeric', year: '2-digit',
            })}
          </div>
          {todayBookings.length === 0 ? (
            <div className="text-[11px]" style={{ color: '#2B2926' }}>
              No bookings today
            </div>
          ) : (
            <div className="space-y-2">
              {todayBookings.map((b) => (
                <div
                  key={b.id || b._id}
                  className="rounded-lg p-2"
                  style={{ background: '#F8F8F7', border: '1px solid #EAEAE6' }}
                >
                  <button
                    type="button"
                    onClick={() => onPickBooking && onPickBooking(b)}
                    className="w-full text-left text-[11px] hover:opacity-80"
                  >
                    <div className="" style={{ color: '#0F7C53' }}>
                      {fmtTime(b.scheduled_at || b.start_time)}
                    </div>
                    <div className="truncate" style={{ color: '#2B2926' }}>
                      {b.attendee_name || b.attendee_email}
                    </div>
                  </button>
                  {/* Join button — orange bg, white text. Opens the
                      meeting link when present, otherwise falls back to
                      the booking detail. */}
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      const link =
                        b.join_url || b.meeting_url || b.online_meeting_url || b.location;
                      if (link && /^https?:\/\//.test(link)) {
                        window.open(link, '_blank', 'noopener,noreferrer');
                      } else {
                        onPickBooking && onPickBooking(b);
                      }
                    }}
                    className="mt-1.5 inline-flex items-center gap-1"
                    style={{
                      background: '#F55600',
                      color: '#fff',
                      border: 'none',
                      borderRadius: 7,
                      padding: '4px 12px',
                      fontSize: 10.5,
                      fontWeight: 500,
                      cursor: 'pointer',
                      transition: 'opacity 0.12s',
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.opacity = '0.9')}
                    onMouseLeave={(e) => (e.currentTarget.style.opacity = '1')}
                  >
                    Join
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Right pane — header + grid */}
      <div className="flex flex-col min-w-0">
        <div className="px-4 py-3 border-b border-[#2B2926]/10 flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={goPrev}
              className="w-7 h-7 inline-flex items-center justify-center rounded-lg border border-[#2B2926]/10 text-[#2B2926] hover:bg-[#F55600]/5"
            >
              <ChevronLeft className="w-3.5 h-3.5" />
            </button>
            <button
              type="button"
              onClick={goToday}
              className="px-3 py-1.5 rounded-lg border border-[#2B2926]/10 text-xs  text-[#2B2926] hover:bg-[#F55600]/5"
            >
              Today
            </button>
            <button
              type="button"
              onClick={goNext}
              className="w-7 h-7 inline-flex items-center justify-center rounded-lg border border-[#2B2926]/10 text-[#2B2926] hover:bg-[#F55600]/5"
            >
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
            <div className="text-sm  text-[#2B2926] ml-2">
              {anchorDate.toLocaleDateString(undefined, { month: 'long' })}{' '}
              <span className="text-[#F55600]">{anchorDate.getFullYear()}</span>
            </div>
          </div>

          {/* Right-side controls — on mobile the calendar card has
              `overflow-hidden` which clips anything wider than its content
              box, so we stack the View toggle and Search on separate rows
              and let the Search input stretch the full inner width.
              From `sm:` up everything sits on a single line as before. */}
          <div className="flex flex-col sm:flex-row sm:items-center gap-2 w-full sm:w-auto">
            <div className="inline-flex items-center rounded-lg border border-[#2B2926]/10 overflow-hidden self-start">
              {VIEWS.map((v) => (
                <button
                  key={v.id}
                  type="button"
                  onClick={() => setView(v.id)}
                  className={[
                    'px-3 py-1.5 text-[11px]  transition-all',
                    view === v.id
                      ? 'bg-[#F55600] text-white'
                      : 'text-[#2B2926] hover:bg-[#F55600]/5',
                  ].join(' ')}
                >
                  {v.label}
                </button>
              ))}
            </div>
            <div className="relative w-full sm:w-44">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[#2B2926]" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search"
                className="w-full pl-7 pr-3 py-1.5 rounded-lg border border-[#2B2926]/10 text-xs focus:outline-none focus:border-[#F55600] box-border"
              />
            </div>
          </div>
        </div>

        {/* Active view */}
        {view === 'month' && (
          // Horizontal scroll on mobile so the 7-column grid keeps a
          // legible width (min 560px) instead of cramming into ~360px —
          // user scrolls right to see Fri/Sat. Desktop keeps overflow
          // visible so the day popovers can extend past the card edge.
          <div className="overflow-x-auto md:overflow-x-visible">
            <div style={{ minWidth: 560 }}>
              <MonthGrid anchorDate={anchorDate} bookings={filtered} onPickBooking={onPickBooking} />
            </div>
          </div>
        )}
        {view === 'week' && (
          <WeekGrid anchorDate={anchorDate} bookings={filtered} onPickBooking={onPickBooking} />
        )}
        {view === 'day' && (
          <DayGrid anchorDate={anchorDate} bookings={filtered} onPickBooking={onPickBooking} />
        )}
        {view === 'year' && (
          <YearGrid
            anchorDate={anchorDate}
            bookings={filtered}
            onPickMonth={(m) => {
              setAnchorDate(m);
              setView('month');
            }}
          />
        )}
      </div>
    </div>
  );
};

export default NexusBookingsCalendar;
