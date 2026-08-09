/*
 * MeetingAgendaPanel — prospect-facing meeting agenda for a booked demo.
 *
 * Phase 1: the agenda is auto-generated + stored on every booking and shown
 * here for the rep to VALIDATE. It is NOT yet embedded in the prospect's invite
 * (that's Phase 2) — hence the explicit "not yet sent to prospects" note.
 *
 * Self-contained: fetches GET /nexus/bookings/:id/agenda, renders the markdown,
 * and offers Copy + Regenerate (POST …/agenda/regenerate).
 */
import React, { useCallback, useEffect, useState } from 'react';
import { CalendarClock, Copy, Check, RefreshCw, EyeOff } from 'lucide-react';

// Minimal renderer for the agenda's markdown shape (bold purpose line, ##
// headings, - bullets, paragraphs). No external markdown dep.
function renderInline(line, k) {
  const parts = String(line || '').split(/(\*\*[^*]+\*\*)/g);
  return parts.map((p, i) =>
    /^\*\*[^*]+\*\*$/.test(p) ? (
      <strong key={`${k}-b-${i}`}>{p.slice(2, -2)}</strong>
    ) : (
      <React.Fragment key={`${k}-t-${i}`}>{p}</React.Fragment>
    ),
  );
}

function AgendaBody({ md }) {
  const lines = String(md || '').split('\n');
  const out = [];
  let bullets = [];
  const flush = (key) => {
    if (bullets.length) {
      out.push(
        <ul key={`ul-${key}`} className="mt-1.5 mb-2 space-y-1.5 list-none pl-0">
          {bullets.map((b, bi) => (
            <li key={`li-${key}-${bi}`} className="flex items-start gap-2.5 text-[13px] leading-relaxed text-[#2B2926]">
              <span className="mt-[7px] inline-block w-1.5 h-1.5 rounded-full bg-[#10B981] shrink-0" />
              <span className="min-w-0 flex-1">{renderInline(b, `li-${key}-${bi}`)}</span>
            </li>
          ))}
        </ul>,
      );
      bullets = [];
    }
  };
  lines.forEach((raw, i) => {
    const line = raw.trim();
    if (!line) {
      flush(i);
      return;
    }
    if (line.startsWith('## ')) {
      flush(i);
      out.push(
        <h4 key={`h-${i}`} className="text-[12px] font-black uppercase tracking-wide text-[#2B2926] mt-3 mb-1">
          {line.slice(3).trim()}
        </h4>,
      );
    } else if (line.startsWith('- ') || line.startsWith('* ')) {
      bullets.push(line.slice(2).trim());
    } else {
      flush(i);
      out.push(
        <p key={`p-${i}`} className="text-[13px] leading-relaxed text-[#2B2926] mb-1">
          {renderInline(line, `p-${i}`)}
        </p>,
      );
    }
  });
  flush('end');
  return <div>{out}</div>;
}

export default function MeetingAgendaPanel({ booking, authAxios }) {
  const [agenda, setAgenda] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!booking?.id || !authAxios) {
      setAgenda(null);
      setError('');
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError('');
    authAxios
      .get(`/nexus/bookings/${booking.id}/agenda`)
      .then((res) => {
        if (!cancelled) setAgenda(res.data || null);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err?.response?.status === 404) setAgenda({ status: 'no_agenda', agenda_markdown: '' });
        else setError(err?.response?.data?.detail || err.message || 'Failed to load agenda');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [booking?.id, authAxios]);

  const regenerate = useCallback(async () => {
    if (!booking?.id || !authAxios) return;
    setBusy(true);
    setError('');
    try {
      const res = await authAxios.post(`/nexus/bookings/${booking.id}/agenda/regenerate`);
      setAgenda((prev) => ({
        ...(prev || {}),
        status: 'ready',
        agenda_markdown: res.data?.agenda_markdown || '',
        model: res.data?.model ?? prev?.model,
      }));
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to generate agenda');
    } finally {
      setBusy(false);
    }
  }, [authAxios, booking?.id]);

  const md = (agenda?.agenda_markdown || '').trim();
  const hasAgenda = agenda?.status === 'ready' && md;

  const copy = useCallback(() => {
    if (!md) return;
    navigator.clipboard?.writeText(md);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [md]);

  return (
    <div className="mt-4 border border-[#2B2926]/10 rounded-xl p-3.5 bg-white">
      <div className="flex items-center justify-between gap-2 mb-1">
        <div className="flex items-center gap-2 min-w-0">
          <span className="w-7 h-7 rounded-lg bg-[#10B981]/10 text-[#10B981] grid place-items-center shrink-0">
            <CalendarClock className="w-3.5 h-3.5" />
          </span>
          <span className="text-[13px] font-black text-[#2B2926]">Meeting Agenda</span>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {md && (
            <button
              type="button"
              onClick={copy}
              className="inline-flex items-center gap-1 text-[11px] font-bold px-2 py-1 rounded-md border border-[#2B2926]/15 text-[#2B2926] hover:border-[#2B2926]/35"
            >
              {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
              {copied ? 'Copied' : 'Copy'}
            </button>
          )}
          <button
            type="button"
            onClick={regenerate}
            disabled={busy}
            className="inline-flex items-center gap-1 text-[11px] font-bold px-2 py-1 rounded-md text-white bg-[#0F1115] hover:bg-[#2B2926] disabled:opacity-50"
          >
            <RefreshCw className={`w-3 h-3 ${busy ? 'animate-spin' : ''}`} />
            {busy ? 'Generating…' : hasAgenda ? 'Regenerate' : 'Generate'}
          </button>
        </div>
      </div>

      {/* Phase-1 disclosure: the prospect does NOT see this yet. */}
      <div className="flex items-center gap-1.5 text-[10.5px] text-[#2B2926]/50 mb-2">
        <EyeOff className="w-3 h-3 shrink-0" />
        Prospect-shareable draft — not yet added to the calendar invite.
      </div>

      {loading && <p className="text-[11px] text-[#2B2926]/50">Loading agenda…</p>}
      {error && <p className="text-[11px] text-[#F55600] font-semibold">{error}</p>}
      {!loading && !error && hasAgenda && <AgendaBody md={md} />}
      {!loading && !error && !hasAgenda && agenda?.status === 'no_agenda' && (
        <p className="text-[12px] text-[#2B2926]/70 italic">
          No agenda yet — it auto-generates on booking, or click Generate to draft one now.
        </p>
      )}
    </div>
  );
}
