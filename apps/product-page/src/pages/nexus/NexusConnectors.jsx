import React, { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { Plug, Loader2, Mail, Plus, ChevronLeft, ChevronDown, Trash2, Check, Calendar, ExternalLink, MoreHorizontal, Pencil, Copy, X } from 'lucide-react';
import { isReadOnly } from '../../lib/permissions';
import { LinkedInConnectModal, LinkedInIcon } from './NexusLinkedInAccounts';

// Brand logos (inline SVG) for the Connect dropdown + per-mailbox marker.
const OutlookIcon = ({ className }) => (
  <svg className={className} viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <path fill="#1066b5" d="M24 22l-1 .9V35h18.4c.9 0 1.6-.7 1.6-1.6V22H24z" />
    <path fill="#28a8ea" d="M24 22h20v-7.4c0-.9-.7-1.6-1.6-1.6H24v9z" />
    <path fill="#0078d4" d="M24 13H8.6C7.7 13 7 13.7 7 14.6v18.8c0 .9.7 1.6 1.6 1.6H24V13z" />
    <path fill="#fff" d="M14.5 19.5c-2.8 0-4.5 2.2-4.5 4.9s1.7 4.6 4.4 4.6 4.5-2.1 4.5-4.8-1.6-4.7-4.4-4.7zm-.1 7.5c-1.5 0-2.4-1.3-2.4-2.8s.9-2.8 2.4-2.8 2.4 1.3 2.4 2.7c0 1.6-.9 2.9-2.4 2.9z" />
  </svg>
);
const GmailIcon = ({ className }) => (
  <svg className={className} viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <path fill="#4caf50" d="M45 16.2l-5 2.75l-5 4.75L35 40h7c1.657 0 3-1.343 3-3V16.2z" />
    <path fill="#1e88e5" d="M3 16.2l3.614 1.71L13 23.7V40H6c-1.657 0-3-1.343-3-3V16.2z" />
    <polygon fill="#e53935" points="35,11.2 24,19.45 13,11.2 12,17 13,23.7 24,31.95 35,23.7 36,17" />
    <path fill="#c62828" d="M3 12.298V16.2l10 7.5V11.2L9.876 8.859C9.132 8.301 8.228 8 7.298 8C4.924 8 3 9.924 3 12.298z" />
    <path fill="#fbc02d" d="M45 12.298V16.2l-10 7.5V11.2l3.124-2.341C38.868 8.301 39.772 8 40.702 8C43.076 8 45 9.924 45 12.298z" />
  </svg>
);

const ENTITY_LABEL = { product: 'Product', service: 'Service', gcc: 'GCC' };

const _domain = (url) => {
  try {
    return new URL(url.includes('://') ? url : `https://${url}`).hostname.replace(/^www\./, '');
  } catch {
    return '';
  }
};

// Allowed demo-booking providers (host suffixes). Keep in sync with the
// backend list in nexus/routers/connectors.py (_BOOKING_HOSTS).
const BOOKING_HOSTS = [
  // Microsoft Bookings
  'outlook.office.com', 'outlook.office365.com', 'bookings.cloud.microsoft', 'bookings.microsoft.com',
  // Google (Calendar appointment schedules)
  'calendar.google.com', 'calendar.app.google',
  // Calendly
  'calendly.com',
];
const BOOKING_MSG = 'Please enter a valid Microsoft Bookings, Google, or Calendly link.';

// True when `url` is a well-formed http(s) link on an allowed booking host.
const isValidBookingUrl = (url) => {
  const v = (url || '').trim();
  if (!v) return false;
  let parsed;
  try {
    parsed = new URL(v);
  } catch {
    return false;
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return false;
  const host = parsed.hostname.toLowerCase().replace(/^www\./, '');
  return BOOKING_HOSTS.some((h) => host === h || host.endsWith('.' + h));
};

// Short provider label shown on the demo-link pill.
const bookingProvider = (url) => {
  let host = '';
  try {
    host = new URL(url).hostname.toLowerCase().replace(/^www\./, '');
  } catch {
    return 'Demo link';
  }
  if (host.includes('calendly')) return 'Calendly';
  if (host.includes('google')) return 'Google';
  if (host.includes('office') || host.includes('bookings')) return 'Bookings';
  return 'Demo link';
};

// Real logo from the brand's website (Clearbit → favicon → letter avatar).
const BrandLogo = ({ name, url }) => {
  const domain = _domain(url);
  const [step, setStep] = useState(0);
  const initial = (name || '?').trim().charAt(0).toUpperCase() || '?';
  const sources = domain
    ? [
        `https://logo.clearbit.com/${domain}`,
        `https://www.google.com/s2/favicons?domain=${domain}&sz=128`,
      ]
    : [];
  if (step < sources.length) {
    return (
      <img
        src={sources[step]}
        onError={() => setStep((s) => s + 1)}
        alt=""
        className="w-8 h-8 rounded-md object-contain bg-white border border-black/5"
      />
    );
  }
  return (
    <div className="w-8 h-8 rounded-md bg-[#F55600]/10 text-[#F55600] text-sm font-extrabold flex items-center justify-center">
      {initial}
    </div>
  );
};

/**
 * NEXUS Connectors — sender cards by website + entity type.
 * Add a card (Product/Service/GCC + URL), then connect mailboxes to it.
 * Campaigns match by their product's URL → card → its mailboxes.
 */
const NexusConnectors = ({ authAxios, apiBase, user, setMessage, activateNonce, onBack }) => {
  const connectorsBase = `${apiBase}/connectors`;

  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState(''); // `${provider}:${brandId}`
  const [accounts, setAccounts] = useState([]);
  const [brands, setBrands] = useState([]);
  const [outlookAvailable, setOutlookAvailable] = useState(false);
  const [gmailAvailable, setGmailAvailable] = useState(false);

  // Which brand's email-overflow popover is open (brand id), or null.
  const [openEmailOverflow, setOpenEmailOverflow] = useState(null);

  // Per-brand demo-link editing: unsaved input drafts + the brand id saving now.
  const [bookingDrafts, setBookingDrafts] = useState({}); // { [brandId]: string }
  const [savingBooking, setSavingBooking] = useState(null); // brandId being saved
  const [editingBooking, setEditingBooking] = useState(null); // brandId in add/edit input mode
  const [openBookingMenu, setOpenBookingMenu] = useState(null); // brandId whose ⋯ menu is open
  const [bookingMenuPos, setBookingMenuPos] = useState(null); // {top,left} for the portal menu

  // Add-card form
  const [showAdd, setShowAdd] = useState(false);
  const [addType, setAddType] = useState('product');
  const [addName, setAddName] = useState('');
  const [addUrl, setAddUrl] = useState('');
  const [adding, setAdding] = useState(false);

  // LinkedIn outreach accounts (connected per brand, like mailboxes)
  const linkedinBase = `${apiBase}/linkedin-agent`;
  const [liAccounts, setLiAccounts] = useState([]);
  const [liModal, setLiModal] = useState(null);  // { brandId, brandName } when connecting
  const loadLinkedIn = useCallback(async () => {
    try {
      const res = await authAxios.get(`${linkedinBase}/accounts`);
      setLiAccounts(res.data?.accounts || []);
    } catch {
      setLiAccounts([]);  // 404 = feature flag off; just show no LinkedIn accounts
    }
  }, [authAxios, linkedinBase]);
  const liForBrand = useCallback(
    (brandId) => liAccounts.filter(
      (a) => a.session_status !== 'disconnected' && (a.brand_ids || []).map(String).includes(String(brandId)),
    ),
    [liAccounts],
  );
  const disconnectLinkedIn = useCallback(async (accountId) => {
    try { await authAxios.post(`${linkedinBase}/${accountId}/disconnect`); loadLinkedIn(); }
    catch { setMessage?.('Could not disconnect LinkedIn account.'); }
  }, [authAxios, linkedinBase, loadLinkedIn, setMessage]);

  const loadStatus = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authAxios.get(`${connectorsBase}/status`);
      setAccounts(res.data?.accounts || []);
      setBrands(res.data?.brands || []);
      setOutlookAvailable(!!res.data?.outlook_available);
      setGmailAvailable(!!res.data?.gmail_available);
    } catch (err) {
      const msg = err?.response?.data?.detail || err.message;
      setMessage?.(`Couldn't load connectors: ${msg}`);
    } finally {
      setLoading(false);
    }
  }, [authAxios, connectorsBase, setMessage]);

  // Detect the OAuth return param once on mount, toast it, strip it.
  const _returnHandled = useRef(false);
  useEffect(() => {
    if (_returnHandled.current) return;
    _returnHandled.current = true;
    const params = new URLSearchParams(window.location.search);
    const provider = params.get('nexus_connector');
    if (provider === 'outlook' || provider === 'gmail') {
      const label = provider === 'gmail' ? 'Gmail' : 'Outlook';
      const status = params.get('status');
      const email = params.get('email');
      if (status === 'connected') {
        setMessage?.(`${label} mailbox connected${email ? `: ${email}` : ''}.`);
      } else if (status === 'mapped') {
        const prod = params.get('product') || 'another card';
        setMessage?.(`That mailbox is already connected to ${prod} — disconnect it there first.`);
      } else if (status === 'conflict') {
        setMessage?.('That mailbox is already connected to another workspace.');
      } else if (status === 'expired') {
        setMessage?.('The connection link expired. Please try again.');
      } else if (status === 'error') {
        setMessage?.(`${label} connection failed. Please try again.`);
      }
      ['nexus_connector', 'status', 'email', 'product'].forEach((k) => params.delete(k));
      const clean = window.location.pathname + (params.toString() ? `?${params}` : '');
      window.history.replaceState({}, '', clean);
    }
  }, [setMessage]);

  useEffect(() => {
    loadStatus();
    loadLinkedIn();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activateNonce]);

  const handleAddBrand = useCallback(async () => {
    const url = addUrl.trim();
    if (!url) {
      setMessage?.('Enter a website URL.');
      return;
    }
    setAdding(true);
    try {
      await authAxios.post(`${connectorsBase}/brands`, {
        entity_type: addType,
        url,
        name: addName.trim() || undefined,
      });
      setAddUrl('');
      setAddName('');
      setShowAdd(false);
      loadStatus();
    } catch (err) {
      const msg = err?.response?.data?.detail || err.message;
      setMessage?.(`Couldn't add: ${msg}`);
    } finally {
      setAdding(false);
    }
  }, [addUrl, addName, addType, authAxios, connectorsBase, setMessage, loadStatus]);

  const cancelAdd = useCallback(() => {
    setShowAdd(false);
    setAddUrl('');
    setAddName('');
  }, []);

  const handleConnect = useCallback(
    async (provider, brandId) => {
      const label = provider === 'gmail' ? 'Gmail' : 'Outlook';
      setConnecting(`${provider}:${brandId || ''}`);
      try {
        const res = await authAxios.get(`${connectorsBase}/${provider}/authorize`, {
          params: brandId ? { brand_id: brandId } : {},
        });
        const url = res.data?.auth_url;
        if (url) {
          window.location.href = url;
        } else {
          setMessage?.(`Could not start ${label} connection.`);
          setConnecting('');
        }
      } catch (err) {
        const msg = err?.response?.data?.detail || err.message;
        setMessage?.(`${label} connect failed: ${msg}`);
        setConnecting('');
      }
    },
    [authAxios, connectorsBase, setMessage],
  );

  const handleDisconnect = useCallback(
    async (id) => {
      try {
        await authAxios.post(`${connectorsBase}/${id}/disconnect`);
        setMessage?.('Mailbox disconnected.');
        loadStatus();
      } catch (err) {
        const msg = err?.response?.data?.detail || err.message;
        setMessage?.(`Disconnect failed: ${msg}`);
      }
    },
    [authAxios, connectorsBase, setMessage, loadStatus],
  );

  const handleMap = useCallback(
    async (accountId, brandId) => {
      try {
        await authAxios.post(`${connectorsBase}/${accountId}/map`, {
          brand_id: brandId || null,
        });
        loadStatus();
      } catch (err) {
        const msg = err?.response?.data?.detail || err.message;
        setMessage?.(`Couldn't assign mailbox: ${msg}`);
      }
    },
    [authAxios, connectorsBase, setMessage, loadStatus],
  );

  // Save (or clear) a brand's demo booking link. Validates BEFORE the API call
  // and shows a popup (setMessage) on an invalid URL; the backend re-validates.
  // Returns true on success so the caller can close the editor.
  const handleSaveBooking = useCallback(
    async (brandId, rawValue) => {
      let url = (rawValue || '').trim();
      // Be forgiving: a pasted bare domain gets an https:// scheme.
      if (url && !/^https?:\/\//i.test(url)) url = `https://${url}`;
      if (url && !isValidBookingUrl(url)) {
        setMessage?.(BOOKING_MSG);
        return false;
      }
      setSavingBooking(brandId);
      try {
        await authAxios.post(`${connectorsBase}/brands/${brandId}/booking-link`, { booking_url: url });
        setBookingDrafts((d) => {
          const next = { ...d };
          delete next[brandId];
          return next;
        });
        setMessage?.(url ? 'Demo link saved.' : 'Demo link removed.');
        loadStatus();
        return true;
      } catch (err) {
        const msg = err?.response?.data?.detail || err.message;
        setMessage?.(msg);
        return false;
      } finally {
        setSavingBooking(null);
      }
    },
    [authAxios, connectorsBase, setMessage, loadStatus],
  );

  // Close the add/edit input for a brand, discarding its unsaved draft.
  const cancelBookingEdit = useCallback((brandId) => {
    setBookingDrafts((d) => {
      const next = { ...d };
      delete next[brandId];
      return next;
    });
    setEditingBooking(null);
  }, []);

  // The ⋯ menu renders in a portal (fixed position) so the table's
  // overflow-x-auto wrapper can't clip it. Close it on scroll/resize so it
  // never drifts away from its button.
  useEffect(() => {
    if (openBookingMenu == null) return undefined;
    const close = () => setOpenBookingMenu(null);
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    return () => {
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  }, [openBookingMenu]);

  const connectedAccounts = accounts.filter((a) => a.connected);
  const mailboxesFor = (brandId) => connectedAccounts.filter((a) => a.brand_id === brandId);
  const unassigned = connectedAccounts.filter((a) => !a.brand_id);
  const noConnectorConfigured = !outlookAvailable && !gmailAvailable;

  const MailboxRow = ({ a, showMapMenu = false }) => (
    <div className="flex items-center justify-between gap-2 bg-black/[0.02] rounded-lg px-3 py-2">
      <div className="flex items-center gap-2 min-w-0">
        {a.provider === 'gmail' ? <GmailIcon className="w-4 h-4 shrink-0" /> : <OutlookIcon className="w-4 h-4 shrink-0" />}
        <span className="text-sm text-black/80 truncate">{a.email_address}</span>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {showMapMenu && (
          <select
            value={a.brand_id || ''}
            onChange={(e) => handleMap(a.id, e.target.value)}
            className="text-xs rounded-md border border-amber-300 text-amber-700 bg-white px-2 py-1"
          >
            <option value="">Assign to…</option>
            {brands.map((b) => (
              <option key={b.id} value={b.id}>{b.name}</option>
            ))}
          </select>
        )}
        {!isReadOnly(user) && (
          <button
            type="button"
            onClick={() => handleDisconnect(a.id)}
            className="text-[11px] font-bold text-black/45 hover:text-red-600"
          >
            Disconnect
          </button>
        )}
      </div>
    </div>
  );

  const ConnectMenu = ({ brandId, brandName }) => {
    if (isReadOnly(user)) return null;  // read-only User cannot connect mailboxes
    const busy = connecting === `outlook:${brandId}` || connecting === `gmail:${brandId}`;
    const [open, setOpen] = useState(false);
    const [pos, setPos] = useState(null);
    const btnRef = useRef(null);
    const menuRef = useRef(null);
    // LinkedIn is always offered (no per-tenant OAuth app needed), so the button
    // stays enabled even when no email connector is configured.
    const disabled = !!connecting;
    // Custom dropdown (replaces a native <select>) so the option hover is a
    // neutral grey instead of the browser's default blue highlight.
    const options = [
      ...(outlookAvailable ? [{ value: 'outlook', label: 'Outlook', Icon: OutlookIcon }] : []),
      ...(gmailAvailable ? [{ value: 'gmail', label: 'Gmail', Icon: GmailIcon }] : []),
      { value: 'linkedin', label: 'LinkedIn', Icon: LinkedInIcon },
    ];
    // Position the menu via a portal so it's never clipped by the scrollable
    // table; flip it above the button when there isn't room below.
    const place = () => {
      const r = btnRef.current?.getBoundingClientRect();
      if (!r) return;
      const width = Math.max(r.width, 160);
      const estH = 8 + options.length * 34;
      const spaceBelow = window.innerHeight - r.bottom;
      const openUp = spaceBelow < estH + 8 && r.top > estH;
      setPos({
        left: r.right - width,
        top: openUp ? r.top - estH - 6 : r.bottom + 6,
        width,
      });
    };
    useEffect(() => {
      if (!open) return undefined;
      place();
      const onDoc = (e) => {
        if (btnRef.current?.contains(e.target) || menuRef.current?.contains(e.target)) return;
        setOpen(false);
      };
      const reposition = () => place();
      document.addEventListener('mousedown', onDoc);
      window.addEventListener('scroll', reposition, true);
      window.addEventListener('resize', reposition);
      return () => {
        document.removeEventListener('mousedown', onDoc);
        window.removeEventListener('scroll', reposition, true);
        window.removeEventListener('resize', reposition);
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open]);
    return (
      <div className="flex items-center gap-2">
        {busy && <Loader2 className="w-3.5 h-3.5 animate-spin text-black/40" />}
        <button
          ref={btnRef}
          type="button"
          disabled={disabled}
          onClick={() => setOpen((o) => !o)}
          className="inline-flex items-center justify-between gap-2 text-xs font-bold rounded-lg border border-black/15 px-2.5 py-1.5 bg-white text-black/80 hover:border-[#ff4500] disabled:opacity-50 cursor-pointer min-w-[104px]"
        >
          <span>Connect</span>
          <ChevronDown className={`w-3.5 h-3.5 transition-transform ${open ? 'rotate-180' : ''}`} />
        </button>
        {open && options.length > 0 && pos && createPortal(
          <div
            ref={menuRef}
            style={{ position: 'fixed', top: pos.top, left: pos.left, width: pos.width, zIndex: 9999 }}
            className="bg-white border border-black/10 rounded-lg shadow-xl py-1"
          >
            {options.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => {
                  setOpen(false);
                  if (opt.value === 'linkedin') setLiModal({ brandId, brandName });
                  else handleConnect(opt.value, brandId);
                }}
                className="w-full flex items-center gap-2 px-3 py-1.5 text-xs font-semibold text-[#2B2926] hover:bg-[#2B2926]/[0.06] text-left transition-colors"
              >
                <opt.Icon className="w-4 h-4 shrink-0" />
                {opt.label}
              </button>
            ))}
          </div>,
          document.body,
        )}
      </div>
    );
  };

  return (
    <div className="p-3 sm:p-4 md:p-5 w-full">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
        <div className="flex items-center gap-3">
          {/* Back — returns to the previously-opened tab in its preserved
              state (wired by NexusLayout). Only shown when there's somewhere
              to go back to. */}
          {onBack && (
            <button
              type="button"
              onClick={onBack}
              className="inline-flex items-center gap-1.5 text-xs font-bold text-white shrink-0"
              style={{ background: '#0F1115', borderRadius: 8, padding: '6px 12px' }}
              onMouseEnter={(e) => (e.currentTarget.style.background = '#1c2128')}
              onMouseLeave={(e) => (e.currentTarget.style.background = '#0F1115')}
            >
              <ChevronLeft className="w-3.5 h-3.5" />
              Back
            </button>
          )}
          <Plug className="w-5 h-5 text-[#ff4500]" />
          <h1 className="text-xl font-bold text-black">Connectors</h1>
        </div>
        {!isReadOnly(user) && (
          <button
            type="button"
            onClick={() => setShowAdd((v) => !v)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#F55600] text-white text-xs font-bold hover:bg-[#F55600]/90 whitespace-nowrap shrink-0"
          >
            Add product / company
          </button>
        )}
      </div>

      {/* Add-card form */}
      {showAdd && (
        <div className="border border-black/10 rounded-xl p-4 mb-4 flex flex-wrap items-center gap-2">
          <select
            value={addType}
            onChange={(e) => setAddType(e.target.value)}
            className="text-sm rounded-lg border border-black/15 px-3 py-2 bg-white"
          >
            <option value="product">Product</option>
            <option value="service">Service company</option>
            <option value="gcc">GCC</option>
          </select>
          <input
            type="text"
            value={addName}
            onChange={(e) => setAddName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleAddBrand()}
            placeholder="Name (e.g. Spenzo AI)"
            className="w-[180px] text-sm rounded-lg border border-black/15 px-3 py-2"
          />
          <input
            type="text"
            value={addUrl}
            onChange={(e) => setAddUrl(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleAddBrand()}
            placeholder="Website URL (e.g. spenzo.ai)"
            className="flex-1 min-w-[200px] text-sm rounded-lg border border-black/15 px-3 py-2"
          />
          <button
            type="button"
            onClick={handleAddBrand}
            disabled={adding}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[#ff4500] text-white text-sm font-bold hover:bg-[#ff4500]/90 disabled:opacity-50"
          >
            {adding && <Loader2 className="w-3.5 h-3.5 animate-spin" />} Add
          </button>
          <button
            type="button"
            onClick={cancelAdd}
            className="px-4 py-2 rounded-lg border border-black/15 text-sm font-bold text-black/60 hover:bg-black/[0.03]"
          >
            Cancel
          </button>
        </div>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-black/40 text-sm py-10">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading connectors…
        </div>
      ) : (
        <div className="space-y-4">
          {noConnectorConfigured && (
            <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
              No mailbox connector is configured on the server yet (admin must
              set the Outlook/Gmail app credentials).
            </div>
          )}

          {brands.length === 0 && (
            <div className="text-sm text-black/50 border border-dashed border-black/15 rounded-xl p-6 text-center">
              Nothing here yet. Click <b>Add product / company</b> to add one by
              its website, then connect mailboxes to it.
            </div>
          )}

          {/* TABLE layout — one row per sender brand. Logo · Brand · Type · Domain · Mailboxes · Actions */}
          {brands.length > 0 && (
            <div className="border border-[#2B2926]/15 rounded-2xl overflow-hidden bg-white shadow-sm">
              <div className="overflow-x-auto">
                <table className="w-full border-collapse" style={{ minWidth: 900 }}>
                  <thead>
                    <tr className="bg-[#2B2926]/[0.03] border-b border-[#2B2926]/15">
                      <th className="text-left px-3 py-2.5 text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.14em] min-w-[230px]">Brand</th>
                      <th className="text-left px-3 py-2.5 text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.14em]">Type</th>
                      <th className="text-left px-3 py-2.5 text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.14em] min-w-[220px]">Domain</th>
                      <th className="text-left px-3 py-2.5 text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.14em]">Mailboxes</th>
                      <th className="text-left px-3 py-2.5 text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.14em] w-[280px]">Email</th>
                      <th className="text-left px-3 py-2.5 text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.14em] w-[220px]">LinkedIn</th>
                      <th className="text-left px-3 py-2.5 text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.14em] w-[250px]">Booking link</th>
                      {!isReadOnly(user) && (
                        <th className="text-left px-3 py-2.5 text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.14em] w-[120px]">Connect</th>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {brands.map((b, idx) => {
                      const list = mailboxesFor(b.id);
                      const et = b.entity_type || 'product';
                      const chipCls = et === 'service'
                        ? 'text-[#047857] border border-[#10B981]/45'
                        : et === 'gcc'
                        ? 'text-[#2B2926] border border-[#2B2926]/35'
                        : 'text-[#F55600] border border-[#F55600]/45';
                      return (
                        <tr
                          key={b.id}
                          className={`${idx !== brands.length - 1 ? 'border-b border-[#2B2926]/8' : ''} hover:bg-[#2B2926]/[0.015] transition-colors align-top`}
                        >
                          {/* Brand cell — logo + name stacked */}
                          <td className="px-3 py-2">
                            <div className="flex items-center gap-2.5 min-w-0">
                              <BrandLogo name={b.name} url={b.url} />
                              <div className="text-[13px] font-bold text-[#2B2926] truncate max-w-[180px]">{b.name}</div>
                            </div>
                          </td>
                          {/* Type chip */}
                          <td className="px-3 py-2">
                            <span className={`inline-flex items-center justify-center min-w-[78px] text-[11px] font-bold uppercase tracking-[0.1em] px-2 py-0.5 rounded-md ${chipCls}`}>
                              {ENTITY_LABEL[et] || 'Product'}
                            </span>
                          </td>
                          {/* Domain */}
                          <td className="px-3 py-2">
                            <div className="text-[12px] text-[#2B2926] font-medium truncate max-w-[260px]">{_domain(b.url) || b.url || '—'}</div>
                          </td>
                          {/* Mailboxes — COUNT only (header already says MAILBOXES) */}
                          <td className="px-3 py-2">
                            <div className="text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.12em] whitespace-nowrap">
                              {list.length}
                            </div>
                          </td>
                          {/* Email — first connected address inline + "+N" badge for overflow.
                              Clicking "+N" opens a popover showing all addresses with Disconnect actions. */}
                          <td className="px-3 py-2 w-[280px] max-w-[280px]">
                            {list.length === 0 ? (
                              <span className="text-[11px] text-[#2B2926]/30">—</span>
                            ) : (
                              <div className="inline-flex items-center gap-2 max-w-full relative">
                                {/* First email pill */}
                                <div className="inline-flex items-center gap-1.5 bg-[#2B2926]/[0.04] rounded-md px-2 py-1 min-w-0 max-w-[170px]">
                                  {list[0].provider === 'gmail' ? <GmailIcon className="w-[18px] h-[18px] shrink-0" /> : <OutlookIcon className="w-[18px] h-[18px] shrink-0" />}
                                  <span className="text-[11px] text-[#2B2926] truncate">{list[0].email_address}</span>
                                </div>
                                {list.length === 1 ? (
                                  /* Single email — show disconnect button beside (hidden for read-only Users) */
                                  !isReadOnly(user) && (
                                  <button
                                    type="button"
                                    onClick={() => handleDisconnect(list[0].id)}
                                    title="Disconnect mailbox"
                                    className="inline-flex items-center justify-center w-7 h-7 rounded-md text-[#2B2926]/45 hover:text-white hover:bg-[#DC2626] border border-[#2B2926]/15 hover:border-[#DC2626] transition-all shrink-0"
                                  >
                                    <Trash2 className="w-3.5 h-3.5" strokeWidth={2.2} />
                                  </button>
                                  )
                                ) : (
                                  /* Multiple emails — show "+N" badge that opens popover */
                                  <>
                                    <button
                                      type="button"
                                      onClick={() => setOpenEmailOverflow(openEmailOverflow === b.id ? null : b.id)}
                                      title={`Show all ${list.length} mailboxes`}
                                      className="inline-flex items-center justify-center min-w-[36px] h-7 px-2 rounded-md text-[11px] font-bold text-[#F55600] bg-[#F55600]/10 border border-[#F55600]/30 hover:bg-[#F55600] hover:text-white hover:border-[#F55600] transition-all shrink-0"
                                    >
                                      +{list.length - 1}
                                    </button>
                                    {openEmailOverflow === b.id && (
                                      <>
                                        {/* backdrop to close on click-outside */}
                                        <div
                                          onClick={() => setOpenEmailOverflow(null)}
                                          className="fixed inset-0 z-30"
                                        />
                                        {/* popover */}
                                        <div className="absolute z-40 top-full left-0 mt-2 bg-white rounded-xl border border-[#2B2926]/20 shadow-[0_12px_32px_rgba(43,41,38,0.16)] p-2 min-w-[260px] max-w-[320px] animate-in fade-in zoom-in-95 duration-150">
                                          <div className="text-[10px] font-bold text-[#2B2926]/55 uppercase tracking-[0.14em] px-2 py-1.5">
                                            {list.length} Connected Mailboxes
                                          </div>
                                          <div className="space-y-1">
                                            {list.map((a) => (
                                              <div key={a.id} className="flex items-center justify-between gap-2 bg-[#2B2926]/[0.03] rounded-md px-2 py-1.5">
                                                <div className="flex items-center gap-1.5 min-w-0">
                                                  {a.provider === 'gmail' ? <GmailIcon className="w-3 h-3 shrink-0" /> : <OutlookIcon className="w-3 h-3 shrink-0" />}
                                                  <span className="text-[11px] text-[#2B2926] truncate">{a.email_address}</span>
                                                </div>
                                                {!isReadOnly(user) && (
                                                <button
                                                  type="button"
                                                  onClick={() => { handleDisconnect(a.id); setOpenEmailOverflow(null); }}
                                                  title="Disconnect mailbox"
                                                  className="inline-flex items-center justify-center w-6 h-6 rounded text-[#2B2926]/45 hover:text-white hover:bg-[#DC2626] border border-[#2B2926]/15 hover:border-[#DC2626] transition-all shrink-0"
                                                >
                                                  <Trash2 className="w-3 h-3" strokeWidth={2.2} />
                                                </button>
                                                )}
                                              </div>
                                            ))}
                                          </div>
                                        </div>
                                      </>
                                    )}
                                  </>
                                )}
                              </div>
                            )}
                          </td>
                          {/* LinkedIn — connected account(s) for this brand */}
                          <td className="px-3 py-2 w-[220px] max-w-[220px]">
                            {liForBrand(b.id).length === 0 ? (
                              <span className="text-[11px] text-[#2B2926]/30">—</span>
                            ) : (
                              <div className="flex flex-col items-start gap-1.5">
                                {liForBrand(b.id).map((a) => (
                                  <div key={a.id} className="inline-flex items-center gap-1.5 bg-[#0A66C2]/[0.08] rounded-md px-2 py-1 max-w-full">
                                    <LinkedInIcon className="w-[16px] h-[16px] shrink-0" />
                                    <span className="text-[11px] text-[#2B2926] truncate">{a.linkedin_email}</span>
                                    {!isReadOnly(user) && (
                                      <button
                                        type="button"
                                        onClick={() => disconnectLinkedIn(a.id)}
                                        title="Disconnect LinkedIn"
                                        className="inline-flex items-center justify-center w-6 h-6 rounded text-[#2B2926]/45 hover:text-white hover:bg-[#DC2626] border border-[#2B2926]/15 hover:border-[#DC2626] transition-all shrink-0"
                                      >
                                        <Trash2 className="w-3.5 h-3.5" strokeWidth={2.2} />
                                      </button>
                                    )}
                                  </div>
                                ))}
                              </div>
                            )}
                          </td>
                          {/* Demo Link — per-brand booking URL (Option B: pill + ⋯ menu) */}
                          <td className="px-3 py-2 w-[250px] max-w-[250px]">
                            {(() => {
                              const hasLink = !!b.booking_url;
                              const isEditing = editingBooking === b.id;
                              const draftVal = (b.id in bookingDrafts) ? bookingDrafts[b.id] : (b.booking_url || '');

                              // Read-only Users: show the link (or a dash), no editing.
                              if (isReadOnly(user)) {
                                return hasLink ? (
                                  <a
                                    href={b.booking_url}
                                    target="_blank"
                                    rel="noreferrer"
                                    title={b.booking_url}
                                    className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-full text-[11px] font-medium bg-[#047857]/[0.10] text-[#047857] max-w-[160px]"
                                  >
                                    <Calendar className="w-3.5 h-3.5 shrink-0" />
                                    <span className="truncate">{bookingProvider(b.booking_url)}</span>
                                    <ExternalLink className="w-3 h-3 shrink-0" />
                                  </a>
                                ) : (
                                  <span className="text-[11px] text-[#2B2926]/30">—</span>
                                );
                              }

                              // Add / edit input: field + save + cancel.
                              if (isEditing) {
                                const submit = async () => {
                                  const ok = await handleSaveBooking(b.id, draftVal);
                                  if (ok) setEditingBooking(null);
                                };
                                return (
                                  <div className="flex items-center gap-1.5">
                                    <input
                                      type="url"
                                      autoFocus
                                      value={draftVal}
                                      onChange={(e) => setBookingDrafts((d) => ({ ...d, [b.id]: e.target.value }))}
                                      onKeyDown={(e) => {
                                        if (e.key === 'Enter') submit();
                                        if (e.key === 'Escape') cancelBookingEdit(b.id);
                                      }}
                                      placeholder="Enter booking link"
                                      className="w-[160px] text-[11px] px-2 py-1.5 rounded-md border border-[#2B2926]/15 focus:border-[#F55600] outline-none"
                                    />
                                    <button
                                      type="button"
                                      onClick={submit}
                                      disabled={savingBooking === b.id}
                                      title="Save"
                                      className="inline-flex items-center justify-center w-7 h-7 rounded-md text-white bg-[#F55600] hover:bg-[#d94b00] disabled:opacity-50 transition-colors shrink-0"
                                    >
                                      {savingBooking === b.id ? (
                                        <Loader2 className="w-3.5 h-3.5 animate-spin" strokeWidth={2.4} />
                                      ) : (
                                        <Check className="w-3.5 h-3.5" strokeWidth={2.4} />
                                      )}
                                    </button>
                                    <button
                                      type="button"
                                      onClick={() => cancelBookingEdit(b.id)}
                                      title="Cancel"
                                      className="inline-flex items-center justify-center w-7 h-7 rounded-md text-[#2B2926]/45 hover:bg-[#2B2926]/[0.06] border border-[#2B2926]/15 transition-all shrink-0"
                                    >
                                      <X className="w-3.5 h-3.5" strokeWidth={2.4} />
                                    </button>
                                  </div>
                                );
                              }

                              // Empty: dashed "Add link" button.
                              if (!hasLink) {
                                return (
                                  <button
                                    type="button"
                                    onClick={() => {
                                      setBookingDrafts((d) => ({ ...d, [b.id]: '' }));
                                      setEditingBooking(b.id);
                                    }}
                                    className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md text-[11px] font-medium text-[#2B2926]/70 border border-dashed border-[#2B2926]/25 hover:border-[#F55600] hover:text-[#F55600] transition-all"
                                  >
                                    <Plus className="w-3.5 h-3.5" /> Add link
                                  </button>
                                );
                              }

                              // Has link: provider pill + ⋯ menu (Edit / Copy / Remove).
                              const menuOpen = openBookingMenu === b.id;
                              return (
                                <div className="inline-flex items-center gap-1.5 relative">
                                  <a
                                    href={b.booking_url}
                                    target="_blank"
                                    rel="noreferrer"
                                    title={b.booking_url}
                                    className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-full text-[11px] font-medium bg-[#047857]/[0.10] text-[#047857] hover:bg-[#047857]/[0.16] transition-colors max-w-[150px]"
                                  >
                                    <Calendar className="w-3.5 h-3.5 shrink-0" />
                                    <span className="truncate">{bookingProvider(b.booking_url)}</span>
                                    <ExternalLink className="w-3 h-3 shrink-0" />
                                  </a>
                                  <button
                                    type="button"
                                    onClick={(e) => {
                                      if (menuOpen) { setOpenBookingMenu(null); return; }
                                      const r = e.currentTarget.getBoundingClientRect();
                                      setBookingMenuPos({ top: r.bottom + 6, left: r.right - 160 });
                                      setOpenBookingMenu(b.id);
                                    }}
                                    title="More actions"
                                    className="inline-flex items-center justify-center w-7 h-7 rounded-md text-[#2B2926]/50 hover:bg-[#2B2926]/[0.06] border border-[#2B2926]/15 transition-all shrink-0"
                                  >
                                    <MoreHorizontal className="w-4 h-4" />
                                  </button>
                                  {menuOpen && bookingMenuPos && createPortal(
                                    <>
                                      <div onClick={() => setOpenBookingMenu(null)} className="fixed inset-0 z-[9998]" />
                                      <div
                                        style={{ position: 'fixed', top: bookingMenuPos.top, left: bookingMenuPos.left, zIndex: 9999 }}
                                        className="w-[160px] bg-white rounded-xl border border-[#2B2926]/20 shadow-[0_12px_32px_rgba(43,41,38,0.16)] p-1 animate-in fade-in zoom-in-95 duration-150"
                                      >
                                        <button
                                          type="button"
                                          onClick={() => {
                                            setBookingDrafts((d) => ({ ...d, [b.id]: b.booking_url || '' }));
                                            setEditingBooking(b.id);
                                            setOpenBookingMenu(null);
                                          }}
                                          className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-[12px] text-[#2B2926] hover:bg-[#2B2926]/[0.05] transition-colors"
                                        >
                                          <Pencil className="w-3.5 h-3.5 text-[#2B2926]/60" /> Edit link
                                        </button>
                                        <button
                                          type="button"
                                          onClick={() => {
                                            navigator.clipboard?.writeText(b.booking_url || '');
                                            setMessage?.('Link copied.');
                                            setOpenBookingMenu(null);
                                          }}
                                          className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-[12px] text-[#2B2926] hover:bg-[#2B2926]/[0.05] transition-colors"
                                        >
                                          <Copy className="w-3.5 h-3.5 text-[#2B2926]/60" /> Copy
                                        </button>
                                        <button
                                          type="button"
                                          onClick={() => {
                                            handleSaveBooking(b.id, '');
                                            setOpenBookingMenu(null);
                                          }}
                                          className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-[12px] text-[#DC2626] hover:bg-[#DC2626]/[0.06] transition-colors"
                                        >
                                          <Trash2 className="w-3.5 h-3.5" /> Remove
                                        </button>
                                      </div>
                                    </>,
                                    document.body,
                                  )}
                                </div>
                              );
                            })()}
                          </td>
                          {/* Connect action — hidden entirely for read-only Users */}
                          {!isReadOnly(user) && (
                            <td className="px-3 py-2 text-left">
                              <div className="inline-flex">
                                <ConnectMenu brandId={b.id} brandName={b.name} />
                              </div>
                            </td>
                          )}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Unassigned mailboxes (connected but not on a card) */}
          {unassigned.length > 0 && (
            <div className="border border-amber-200 bg-amber-50/40 rounded-xl p-5">
              <div className="font-bold text-black flex items-center gap-2">
                <Mail className="w-4 h-4 text-amber-600" />
                Unassigned mailboxes
              </div>
              <p className="text-xs text-black/50 mt-1">
                These mailboxes aren’t on a card yet, so they won’t send. Assign
                each to a sender below.
              </p>
              <div className="mt-3 space-y-1.5">
                {unassigned.map((a) => (
                  <MailboxRow key={a.id} a={a} showMapMenu />
                ))}
              </div>
            </div>
          )}

        </div>
      )}
      {liModal && (
        <LinkedInConnectModal
          base={`${apiBase}/linkedin-agent`}
          authAxios={authAxios}
          lockedBrandId={liModal.brandId}
          brandName={liModal.brandName}
          setMessage={setMessage}
          onClose={() => setLiModal(null)}
          onDone={() => { setLiModal(null); loadLinkedIn(); }}
        />
      )}
    </div>
  );
};

export default NexusConnectors;
