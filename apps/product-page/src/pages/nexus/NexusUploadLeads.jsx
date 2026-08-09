/**
 * NexusUploadLeads — "Bring-Your-Own-Leads" campaign creator.
 *
 * Mirrors the shape of NexusNewCampaign but instead of Apollo-discovering
 * leads we accept a user-supplied CSV / TSV / XLSX / pasted spreadsheet.
 * Validation is all-or-nothing: the entire upload is rejected if ANY row
 * is missing one of the 4 mandatory fields (Name, Role, Company, Email)
 * or has an invalid email.
 *
 * Steps:
 *   1. leads     — upload + parse + per-row validation + preview table
 *   2. url       — product/service URL + Product/Service toggle
 *   3. scraping  — animated progress while backend scrapes
 *   4. summary   — review/edit AI-generated description (Edit button gates
 *                  the textarea, same pattern as NexusNewCampaign)
 *   5. kb        — paste optional knowledge base text
 *   6. launching — POST /nexus/analyze/from-leads (skips Apollo discovery)
 *   7. launched  — success screen, jumps to GTM Journey
 *
 * Important: the existing /nexus/analyze (New Run) and Apollo discovery
 * code paths are untouched. This wizard talks to a NEW endpoint
 * /nexus/analyze/from-leads that the backend ships alongside /analyze.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useNotification } from '../../context/NotificationContext';
import { isReadOnly } from '../../lib/permissions';
import RepresentativeCard from './RepresentativeCard';
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  Edit3,
  FileSpreadsheet,
  FileText,
  Globe,
  Info,
  Loader2,
  Plus,
  Send,
  Sparkles,
  Trash2,
  Upload,
  Users,
  X,
} from 'lucide-react';

// ── Favicon helpers ─────────────────────────────────────────────────────────
// Mirrors the same helpers in NexusNewCampaign so the URL pill shows the
// site's favicon as the user types — gives the wizard a "we recognised
// your site" cue without an extra fetch.
function _normaliseUrl(raw) {
  const u = (raw || '').trim();
  if (!u) return '';
  return /^https?:\/\//i.test(u) ? u : `https://${u}`;
}

function getDuckDuckGoFavicon(rawUrl) {
  try {
    const u = _normaliseUrl(rawUrl);
    if (!u) return '';
    const { hostname } = new URL(u);
    if (!hostname.includes('.')) return '';
    return `https://icons.duckduckgo.com/ip3/${hostname.replace(/^www\./, '')}.ico`;
  } catch {
    return '';
  }
}

function getDirectFavicon(rawUrl) {
  try {
    const u = _normaliseUrl(rawUrl);
    if (!u) return '';
    const { origin } = new URL(u);
    return `${origin}/favicon.ico`;
  } catch {
    return '';
  }
}

function FaviconImg({ src, url, size = 22, className = '' }) {
  if (!src) {
    return (
      <span className={className} style={{ width: size, height: size }}>
        <Globe className="w-full h-full text-[#2B2926]/40" />
      </span>
    );
  }
  return (
    <img
      src={src}
      width={size}
      height={size}
      className={className}
      alt=""
      onError={(e) => {
        // DuckDuckGo returns a placeholder for unknown domains — fall back
        // to the site's own /favicon.ico, then hide on second failure.
        const direct = getDirectFavicon(url);
        if (direct && e.target.src !== direct) {
          e.target.src = direct;
        } else {
          e.target.style.visibility = 'hidden';
        }
      }}
    />
  );
}

// ── CSV / TSV parser ────────────────────────────────────────────────────────
// Minimal RFC-4180-aware parser. Handles quoted fields with commas inside
// and escaped double-quotes ("""). Returns Array<Array<string>> — caller
// decides which row is the header. Auto-detects delimiter: tab if more
// tabs than commas in the first non-empty row, otherwise comma.
function parseDelimited(text) {
  const src = String(text || '');
  if (!src.trim()) return [];
  // Delimiter detection — peek at the first non-blank line.
  const firstLine = src.split(/\r?\n/).find((l) => l.trim()) || '';
  const tabs = (firstLine.match(/\t/g) || []).length;
  const commas = (firstLine.match(/,/g) || []).length;
  const delim = tabs > commas ? '\t' : ',';

  const rows = [];
  let field = '';
  let row = [];
  let inQuotes = false;
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (inQuotes) {
      if (c === '"') {
        if (src[i + 1] === '"') {
          field += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        field += c;
      }
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === delim) {
      row.push(field);
      field = '';
    } else if (c === '\n' || c === '\r') {
      // Swallow \r\n as one line break.
      if (c === '\r' && src[i + 1] === '\n') i += 1;
      row.push(field);
      rows.push(row);
      row = [];
      field = '';
    } else {
      field += c;
    }
  }
  // Final cell + row.
  if (field.length > 0 || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  // Drop fully-empty rows (trailing newlines in pasted text).
  return rows.filter((r) => r.some((cell) => String(cell || '').trim().length > 0));
}

// Map a header cell to one of our canonical field keys. Returns null when
// the header doesn't match any known synonym so the column gets ignored.
function detectField(headerCell) {
  const h = String(headerCell || '')
    .trim()
    .toLowerCase()
    .replace(/[_\-\s]+/g, ' ');
  if (!h) return null;
  if (h === 'name' || h === 'full name' || h === 'lead name' || h === 'contact name') return 'name';
  if (h === 'first name') return 'first_name';
  if (h === 'last name' || h === 'surname') return 'last_name';
  if (h === 'role' || h === 'title' || h === 'job title' || h === 'position' || h === 'designation')
    return 'role';
  if (h === 'company' || h === 'company name' || h === 'organization' || h === 'organisation')
    return 'company';
  if (h === 'email' || h === 'email id' || h === 'email address' || h === 'work email' || h === 'e-mail')
    return 'email';
  if (
    h === 'linkedin' ||
    h === 'linkedin url' ||
    h === 'linkedin profile' ||
    h === 'profile url' ||
    h === 'linkedin link'
  )
    return 'linkedin_url';
  if (
    h === 'match score' ||
    h === 'match' ||
    h === 'score' ||
    h === 'fit score' ||
    h === 'icp score'
  )
    return 'match_score';
  if (h === 'location' || h === 'city' || h === 'region' || h === 'geo')
    return 'location';
  if (
    h === 'phone' ||
    h === 'contact' ||
    h === 'mobile' ||
    h === 'phone number' ||
    h === 'contact number' ||
    h === 'mobile number'
  )
    return 'phone';
  return null;
}

// RFC-5322-light email check — same shape the backend validates.
const EMAIL_RE = /^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$/;

// Convert the 2D rows + detected column map into our typed lead objects.
// `columnMap`: { [colIndex]: 'name' | 'role' | 'company' | 'email' | 'linkedin_url' | 'first_name' | 'last_name' }
function rowsToLeads(rows, columnMap) {
  const out = [];
  for (const r of rows) {
    const obj = {};
    Object.entries(columnMap).forEach(([idxStr, key]) => {
      const idx = Number(idxStr);
      const raw = idx < r.length ? r[idx] : '';
      obj[key] = String(raw || '').trim();
    });
    // If the source only had first_name + last_name, combine into name.
    if (!obj.name && (obj.first_name || obj.last_name)) {
      obj.name = [obj.first_name, obj.last_name].filter(Boolean).join(' ').trim();
    }
    delete obj.first_name;
    delete obj.last_name;
    out.push(obj);
  }
  return out;
}

// Per-row validation. Returns { valid: bool, errors: { [field]: string } }
function validateLead(lead) {
  const errors = {};
  if (!lead.name) errors.name = 'Required';
  if (!lead.role) errors.role = 'Required';
  if (!lead.company) errors.company = 'Required';
  if (!lead.email) {
    errors.email = 'Required';
  } else if (!EMAIL_RE.test(lead.email)) {
    errors.email = 'Invalid email';
  }
  return { valid: Object.keys(errors).length === 0, errors };
}

// SheetJS is loaded from a CDN at runtime (only when the user actually
// picks an Excel file) instead of being imported as an npm dependency.
// This keeps it out of the Vite dependency graph entirely — so the dev
// server / build never tries to resolve a bare "xlsx" specifier (which
// fails when the package isn't installed) and the library doesn't bloat
// the initial bundle. The script is injected once and cached on window.
let _xlsxLoader = null;
function loadSheetJS() {
  if (typeof window !== 'undefined' && window.XLSX) {
    return Promise.resolve(window.XLSX);
  }
  if (_xlsxLoader) return _xlsxLoader;
  _xlsxLoader = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = 'https://cdn.sheetjs.com/xlsx-0.20.3/package/dist/xlsx.full.min.js';
    script.async = true;
    script.onload = () => {
      if (window.XLSX) resolve(window.XLSX);
      else reject(new Error('SheetJS failed to initialise.'));
    };
    script.onerror = () =>
      reject(new Error('Could not load the Excel parser. Check your connection or use CSV/TSV.'));
    document.head.appendChild(script);
  });
  return _xlsxLoader;
}

async function parseExcelFile(file) {
  const XLSX = await loadSheetJS();
  const buf = await file.arrayBuffer();
  const wb = XLSX.read(buf, { type: 'array' });
  const sheetName = wb.SheetNames[0];
  if (!sheetName) return [];
  const ws = wb.Sheets[sheetName];
  // header: 1 → returns a plain 2D array, matching parseDelimited's shape.
  const arr = XLSX.utils.sheet_to_json(ws, { header: 1, defval: '' });
  return arr.filter((r) => Array.isArray(r) && r.some((cell) => String(cell || '').trim().length > 0));
}

// ── Scraping animation steps — same shape New Run uses ────────────────────
const SCRAPE_STEPS = [
  { emoji: '🌐', label: 'Visiting your website…' },
  { emoji: '📖', label: 'Reading your pages…' },
  { emoji: '🔍', label: 'Extracting key details…' },
  { emoji: '✍️', label: 'Writing your summary…' },
];

// ── Product-description render pieces ─────────────────────────────────────
// Read-only replicas of the New Campaign wizard's DescSection / DescBullets
// (NexusNewCampaign.jsx) so the "Here's what we found" summary can render
// Gemini's structured 3-section product_description with identical styling.
// Upload Leads never edits the structured object (summaryText stays the
// launch source of truth), so these are display-only.
function DescBullets({ items }) {
  const clean = (items || []).filter((x) => x && x.trim());
  if (clean.length === 0) return null;
  return (
    <ul className="list-disc pl-5 mt-1.5 space-y-0.5">
      {clean.map((c, i) => (
        <li key={`${c}-${i}`} className="text-[12px] text-[#2B2926]/85 leading-relaxed">
          {c}
        </li>
      ))}
    </ul>
  );
}

function DescSection({ title, body, lists }) {
  const allListsEmpty = (lists || []).every((l) => !l.items || l.items.length === 0);
  const bodyEmpty = !body || !body.trim();
  if (bodyEmpty && allListsEmpty) return null;
  return (
    <div className="mb-3 last:mb-0">
      <div className="text-[10px] font-black uppercase tracking-wider text-[#2B2926] mb-1">
        {title}
      </div>
      {body && <p className="text-[13px] text-[#2B2926]/85 leading-relaxed">{body}</p>}
      {(lists || []).map((lst) => (
        <DescBullets key={lst.label} items={lst.items} />
      ))}
    </div>
  );
}

// ── Main component ──────────────────────────────────────────────────────────
const NexusUploadLeads = ({ authAxios, apiBase, user, setMessage, onNavigate }) => {
  // Wizard step machine.
  const [step, setStep] = useState('leads'); // leads | url | scraping | summary | kb | launching | launched
  const [error, setError] = useState('');

  // App-wide toast (same one the login flow uses) so the "Emails sent for
  // all leads" notification feels native to the rest of the product.
  const { toast } = useNotification();

  // ── Leads state ───────────────────────────────────────────────────────────
  // `parsedLeads` holds the per-row objects + per-row validation; the rest
  // of the wizard never reads `rawText` after this step so the original
  // CSV string is dropped from memory as soon as it's parsed.
  const [parsedLeads, setParsedLeads] = useState([]); // [{ name, role, company, email, linkedin_url, _v }]
  const [parseFileName, setParseFileName] = useState('');
  const [parseError, setParseError] = useState('');
  const [pasteText, setPasteText] = useState('');
  const fileInputRef = useRef(null);

  // ── URL + entity state ────────────────────────────────────────────────────
  const [url, setUrl] = useState('');
  const [entityType, setEntityType] = useState('product');
  // ── Representative (2026-07-30) ──────────────────────────────────────────
  // Who outbound email is signed by. Same capture as the New Campaign wizard —
  // BYO-lead runs send the identical cadence, so without it uploaded leads get
  // "<Product> Team" too. Defaults to the logged-in user.
  const [repIsMe, setRepIsMe] = useState(true);
  const [repName, setRepName] = useState('');
  const [repTitle, setRepTitle] = useState('');

  useEffect(() => {
    if (repIsMe && !repName && user?.full_name) setRepName(user.full_name);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.full_name, repIsMe]);
  // Combined-composer UI state (mirrors New Campaign's smart box): the inline
  // Product/Service/GCC dropdown and a ref for autofocus/auto-grow of the
  // textarea. No "+" knowledge menu — Upload Leads' product step is just
  // URL/details + entity dropdown + send.
  const [entityMenuOpen, setEntityMenuOpen] = useState(false);
  const urlInputRef = useRef(null);

  // ── Scrape / summary state ────────────────────────────────────────────────
  const [productName, setProductName] = useState('');
  const [summaryText, setSummaryText] = useState('');
  // Structured 3-section product_description from Gemini (raw_analysis).
  // Display-only — `summaryText` stays the launch source of truth. Null when
  // the scrape returned no structured block (older/empty responses) → we fall
  // back to the flat summaryText editor.
  const [productDescription, setProductDescription] = useState(null);
  const [scrapeStep, setScrapeStep] = useState(0);
  // One thing-at-a-time edit gate: null = everything read-only, 'summary' =
  // the initial AI summary, otherwise the id of a pending refinement
  // message. Matches the New Run wizard's Edit-button UX so both flows
  // behave identically when the user wants to tweak a draft by hand.
  const [editingId, setEditingId] = useState(null);
  const summaryTextareaRef = useRef(null);

  // Rough line-count estimate so a freshly-mounted textarea opens at
  // approximately the right size. Without this, the textarea mounts at
  // rows={1} (~36px) for the brief window before `_resizeTextarea` runs,
  // collapsing the message card and visually shifting everything below
  // it up by hundreds of pixels for a single frame.
  function _estimateRows(text, charsPerLine = 80) {
    const s = String(text || '');
    if (!s) return 1;
    let rows = 0;
    for (const line of s.split('\n')) {
      rows += Math.max(1, Math.ceil(line.length / charsPerLine));
    }
    return Math.max(1, rows);
  }

  // Resize a textarea to fit its content without scrolling the page.
  // Setting `style.height = 'auto'` collapses the textarea momentarily,
  // which causes any scrollable ancestor (and the window) to recompute
  // scrollTop — that's what was jumping the page up when Edit was
  // clicked. We snapshot every scroll position before the mutation and
  // restore them after, matching the autoResize helper New Run uses.
  function _resizeTextarea(el) {
    if (!el) return;
    const ancestors = [];
    for (let n = el.parentElement; n; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (/(auto|scroll|overlay)/.test(s.overflowY + s.overflow)) ancestors.push(n);
    }
    const snapshots = ancestors.map((n) => n.scrollTop);
    const winY = window.scrollY;
    const winX = window.scrollX;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
    ancestors.forEach((n, i) => {
      if (n.scrollTop !== snapshots[i]) n.scrollTop = snapshots[i];
    });
    if (window.scrollY !== winY || window.scrollX !== winX) {
      window.scrollTo(winX, winY);
    }
  }

  // ── Refine chat state — mirrors New Run's /refine-summary/preview flow ──
  // Each user instruction sends a POST and gets back a refined summary that
  // the user can Apply (replaces summaryText) or Discard. Approved/declined
  // messages collapse to read-only history; pending ones are still editable.
  const [refineMsgs, setRefineMsgs] = useState([]);
  const [refineInput, setRefineInput] = useState('');
  const [isRefining, setIsRefining] = useState(false);
  const msgIdRef = useRef(0);

  // ── KB state ──────────────────────────────────────────────────────────────
  // `knowledgeBase` is the optional pasted text. `kbFiles` holds raw File
  // objects in memory until Launch — exactly the New Run pattern. After
  // /analyze/from-leads returns we POST these to /nexus/kb/upload in the
  // background so extraction + Pinecone indexing happens async (the user
  // doesn't wait for it).
  const [knowledgeBase, setKnowledgeBase] = useState('');
  const [kbFiles, setKbFiles] = useState([]); // [{ filename, file, status }]
  const [isKbDragOver, setIsKbDragOver] = useState(false);
  const kbInputRef = useRef(null);

  // ── Workspace + launch state ──────────────────────────────────────────────
  const [workspaceId, setWorkspaceId] = useState(null);
  const [launchResult, setLaunchResult] = useState(null);
  const scrapeTimerRef = useRef(null);
  // Ref + flag for the "campaign emails done" toast. We track the
  // campaign_id we're polling for (so a second upload doesn't double-fire
  // the toast for the previous campaign), and an alreadyNotified flag so
  // the toast fires exactly once when pending_email flips to 0.
  const sendProgressTimerRef = useRef(null);
  const notifiedCampaignIdRef = useRef(null);

  // ── Effects ───────────────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await authAxios.get('/nexus/me');
        if (cancelled) return;
        const data = res.data || {};
        setWorkspaceId(
          data.default_workspace_id ||
            (Array.isArray(data.workspaces) ? data.workspaces[0]?.id : null) ||
            null,
        );
      } catch {
        /* leave workspaceId null — launch will fail loudly */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authAxios]);

  useEffect(() => {
    if (step !== 'scraping') {
      clearInterval(scrapeTimerRef.current);
      return undefined;
    }
    setScrapeStep(0);
    scrapeTimerRef.current = setInterval(() => {
      setScrapeStep((prev) => {
        if (prev >= SCRAPE_STEPS.length - 1) {
          clearInterval(scrapeTimerRef.current);
          return prev;
        }
        return prev + 1;
      });
    }, 900);
    return () => clearInterval(scrapeTimerRef.current);
  }, [step]);

  // (Auto-fit happens via the textarea's ref callback now — _resizeTextarea
  // preserves scroll positions on every ancestor + the window so entering
  // edit mode never bumps the page.)

  // ── Campaign send-progress poller ─────────────────────────────────────────
  // After a successful launch, poll the backend every 20s to see when the
  // sequencer has finished sending the initial email to every uploaded
  // lead. When it does, fire a one-shot toast via the same notification
  // system the login flow uses. Stops polling when:
  //   • all_done = true (success path → toast fires)
  //   • 30 polls (~10 min) elapse without completion (safety cap)
  //   • component unmounts / new launchResult arrives
  //
  // The toast fires REGARDLESS of which tab the user is on — the wizard
  // stays mounted in the background under NexusLayout's keep-alive, so
  // the poll keeps running while the user is browsing the Lead Journey.
  useEffect(() => {
    const cid = launchResult?.campaign?.id;
    if (!cid || notifiedCampaignIdRef.current === cid) {
      return undefined;
    }
    let cancelled = false;
    let attempts = 0;
    const MAX_ATTEMPTS = 30; // 30 × 20s ≈ 10 min coverage
    const tick = async () => {
      if (cancelled) return;
      attempts += 1;
      try {
        const res = await authAxios.get(
          `${apiBase}/journey/campaign-send-progress`,
          { params: { campaign_id: cid } },
        );
        const data = res.data || {};
        if (data.all_done && notifiedCampaignIdRef.current !== cid) {
          notifiedCampaignIdRef.current = cid;
          const productLabel =
            productName || launchResult?.campaign?.name || 'your campaign';
          toast.success(
            `Emails sent for all ${data.total || parsedLeads.length} lead${
              (data.total || parsedLeads.length) === 1 ? '' : 's'
            } in ${productLabel}.`,
          );
          if (sendProgressTimerRef.current) {
            clearTimeout(sendProgressTimerRef.current);
            sendProgressTimerRef.current = null;
          }
          return;
        }
      } catch {
        /* transient — try again on the next interval */
      }
      if (!cancelled && attempts < MAX_ATTEMPTS) {
        sendProgressTimerRef.current = setTimeout(tick, 20_000);
      }
    };
    // First check fires after a short delay so the sequencer has a chance
    // to run at least one tick before we report "0 emails sent yet".
    sendProgressTimerRef.current = setTimeout(tick, 15_000);
    return () => {
      cancelled = true;
      if (sendProgressTimerRef.current) {
        clearTimeout(sendProgressTimerRef.current);
        sendProgressTimerRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [launchResult?.campaign?.id]);

  // ── Derived ───────────────────────────────────────────────────────────────
  const leadsAllValid = useMemo(
    () => parsedLeads.length > 0 && parsedLeads.every((l) => l._v?.valid),
    [parsedLeads],
  );
  const invalidCount = useMemo(
    () => parsedLeads.filter((l) => !l._v?.valid).length,
    [parsedLeads],
  );
  // Optional columns are shown ONLY when the uploaded file actually carried
  // that data (at least one row has a value) — so the preview reflects the
  // user's file instead of always showing empty columns.
  const optionalCols = useMemo(() => {
    const hasVal = (key) =>
      parsedLeads.some((l) => {
        const v = l[key];
        return v !== undefined && v !== null && String(v).trim() !== '';
      });
    return [
      { key: 'linkedin_url', label: 'LinkedIn' },
      { key: 'phone', label: 'Contact' },
      { key: 'location', label: 'Location' },
      { key: 'match_score', label: 'Match Score' },
    ].filter((c) => hasVal(c.key));
  }, [parsedLeads]);
  // Render one optional cell for a lead, styled per column type.
  const renderOptionalCell = (l, key) => {
    if (key === 'linkedin_url') {
      return l.linkedin_url ? (
        <a
          href={l.linkedin_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-[#F55600] hover:underline truncate"
          title={l.linkedin_url}
        >
          {l.linkedin_url}
        </a>
      ) : (
        <span className="text-[#2B2926]/30">—</span>
      );
    }
    const v = l[key];
    const show = v !== undefined && v !== null && String(v).trim() !== '';
    return show ? String(v) : <span className="text-[#2B2926]/30">—</span>;
  };

  // ── Parsers ───────────────────────────────────────────────────────────────
  const ingestRows = (rows, sourceName) => {
    setParseError('');
    if (!rows || rows.length === 0) {
      setParseError('The file is empty.');
      setParsedLeads([]);
      return;
    }
    if (rows.length < 2) {
      setParseError('Need at least a header row + 1 data row.');
      setParsedLeads([]);
      return;
    }
    // Detect column map from the header.
    const header = rows[0] || [];
    const columnMap = {};
    header.forEach((cell, idx) => {
      const field = detectField(cell);
      if (field && !Object.values(columnMap).includes(field)) {
        columnMap[idx] = field;
      }
    });
    const mappedFields = new Set(Object.values(columnMap));
    // first_name + last_name combine into "name" downstream, so accept
    // either a single "name" column OR the pair.
    const haveName = mappedFields.has('name') || (mappedFields.has('first_name') && mappedFields.has('last_name'));
    const missingHeaders = [];
    if (!haveName) missingHeaders.push('Name');
    if (!mappedFields.has('role')) missingHeaders.push('Role');
    if (!mappedFields.has('company')) missingHeaders.push('Company');
    if (!mappedFields.has('email')) missingHeaders.push('Email');
    if (missingHeaders.length > 0) {
      setParseError(
        `Missing required column${missingHeaders.length > 1 ? 's' : ''}: ${missingHeaders.join(', ')}. ` +
          `Accepted header names: Name (or First/Last), Role/Title, Company, Email.`,
      );
      setParsedLeads([]);
      return;
    }

    const dataRows = rows.slice(1);
    const leads = rowsToLeads(dataRows, columnMap).map((l) => ({
      ...l,
      _v: validateLead(l),
    }));
    if (leads.length === 0) {
      setParseError('No data rows found.');
      setParsedLeads([]);
      return;
    }
    setParsedLeads(leads);
    setParseFileName(sourceName || 'Pasted from sheet');
  };

  const handleFile = async (file) => {
    setParseError('');
    if (!file) return;
    const name = file.name || 'upload';
    const lower = name.toLowerCase();
    try {
      if (lower.endsWith('.xlsx') || lower.endsWith('.xls')) {
        const rows = await parseExcelFile(file);
        ingestRows(rows, name);
      } else {
        // CSV / TSV / any text-shaped file.
        const text = await file.text();
        const rows = parseDelimited(text);
        ingestRows(rows, name);
      }
    } catch (err) {
      setParseError(`Could not read the file: ${err?.message || err}`);
      setParsedLeads([]);
    }
  };

  const handlePasteIngest = () => {
    const rows = parseDelimited(pasteText);
    ingestRows(rows, 'Pasted from sheet');
  };

  // ── URL / scrape / summary ────────────────────────────────────────────────
  const handleUrlSubmit = async () => {
    setError('');
    const u = (url || '').trim();
    if (!u) {
      setError('Enter your product or service URL.');
      return;
    }
    setStep('scraping');
    try {
      // Reuse the existing /nexus/scrape-preview endpoint — same one the
      // New Run wizard uses. Returns a Gemini-generated summary without
      // creating any DB rows. Final creation happens at Launch via
      // /nexus/analyze/from-leads.
      const res = await authAxios.post(`${apiBase}/scrape-preview`, {
        product_url: _normaliseUrl(u),
        entity_type: entityType,
      });
      const data = res.data || {};
      setProductName(data.product_name || '');
      setSummaryText(data.summary_text || '');
      // Capture Gemini's structured 3-section product_description for display
      // on the summary step. summaryText remains the launch value.
      const _pd = data.raw_analysis?.product_description || null;
      if (_pd) {
        setProductDescription({
          what_the_company_is: _pd.what_the_company_is || '',
          what_they_do: _pd.what_they_do || '',
          who_they_serve: _pd.who_they_serve || '',
          key_capabilities: Array.isArray(_pd.key_capabilities) ? [..._pd.key_capabilities] : [],
          target_industries: Array.isArray(_pd.target_industries) ? [..._pd.target_industries] : [],
        });
      } else {
        setProductDescription(null);
      }
      // If Gemini auto-detected entity_type and the user hasn't already
      // overridden it, accept that hint.
      if (data.entity_type && (entityType === 'product' || !entityType)) {
        setEntityType(data.entity_type === 'service' ? 'service' : 'product');
      }
      setStep('summary');
    } catch (err) {
      setError(
        err?.response?.data?.detail ||
          err?.message ||
          'We could not read that URL. Try again or proceed without it.',
      );
      // Fall back to letting the user enter the description manually.
      setStep('summary');
    }
  };

  // Reset the entire wizard back to the Leads step + clear every form
  // field. Called when the user navigates away after a successful launch
  // so they don't come back to a stale "Outreach launched" screen — the
  // parent NexusLayout keeps this tab mounted (lazy mount + display:none)
  // which is what was preserving the prior step state.
  const resetWizard = () => {
    setStep('leads');
    setError('');
    setParsedLeads([]);
    setParseFileName('');
    setPasteText('');
    setParseError('');
    setUrl('');
    setEntityType('product');
    setEntityMenuOpen(false);
    setProductName('');
    setSummaryText('');
    setProductDescription(null);
    setScrapeStep(0);
    setEditingId(null);
    setKnowledgeBase('');
    setKbFiles([]);
    setIsKbDragOver(false);
    setRefineMsgs([]);
    setRefineInput('');
    setIsRefining(false);
    setLaunchResult(null);
    msgIdRef.current = 0;
    // Allow the next campaign to fire its own send-complete toast.
    notifiedCampaignIdRef.current = null;
    if (sendProgressTimerRef.current) {
      clearTimeout(sendProgressTimerRef.current);
      sendProgressTimerRef.current = null;
    }
  };

  // ── KB file handlers ──────────────────────────────────────────────────────
  // Only accept PDF / DOCX / PPTX. Files go into memory as `{ filename,
  // file, status: 'pending' }` and stay there until Launch.
  const _KB_ACCEPT = /\.(pdf|docx|pptx)$/i;
  const handleKbFiles = (files) => {
    if (!files || files.length === 0) return;
    const next = [];
    for (const f of files) {
      if (!_KB_ACCEPT.test(f.name || '')) continue;
      next.push({ filename: f.name, file: f, status: 'pending' });
    }
    if (next.length === 0) return;
    setKbFiles((prev) => [...prev, ...next]);
  };
  const handleKbInputChange = (e) => {
    handleKbFiles(e.target.files);
    e.target.value = '';
  };
  const handleKbDragOver = (e) => {
    e.preventDefault();
    setIsKbDragOver(true);
  };
  const handleKbDragLeave = () => setIsKbDragOver(false);
  const handleKbDrop = (e) => {
    e.preventDefault();
    setIsKbDragOver(false);
    handleKbFiles(e.dataTransfer?.files);
  };

  // ── Refine chat handlers ──────────────────────────────────────────────────
  // Sends the current summary + the user's free-text instruction to
  // /refine-summary/preview and threads the response as an AI message the
  // user can Apply or Discard. Same endpoint New Run uses, so the AI
  // behaviour and refinement quality match exactly.
  async function handleRefineSubmit(e) {
    e?.preventDefault?.();
    const instruction = refineInput.trim();
    if (!instruction || isRefining) return;
    const userMsgId = ++msgIdRef.current;
    const aiMsgId = ++msgIdRef.current;
    setRefineMsgs((prev) => [
      ...prev,
      { id: userMsgId, role: 'user', type: 'instruction', content: instruction },
      { id: aiMsgId, role: 'ai', type: 'refined-summary', content: '', status: 'loading' },
    ]);
    setRefineInput('');
    setError('');
    setIsRefining(true);
    try {
      const res = await authAxios.post(`${apiBase}/refine-summary/preview`, {
        current_summary: summaryText,
        instruction,
      });
      const refined = res.data?.refined_summary || '';
      setRefineMsgs((prev) =>
        prev.map((m) => (m.id === aiMsgId ? { ...m, content: refined, status: 'pending' } : m)),
      );
    } catch (err) {
      setRefineMsgs((prev) =>
        prev.map((m) => (m.id === aiMsgId ? { ...m, status: 'error' } : m)),
      );
      setError(err?.response?.data?.detail || err?.message || 'Refinement failed — try again.');
    } finally {
      setIsRefining(false);
    }
  }

  function handleApproveRefinement(id, content) {
    setSummaryText(content);
    setRefineMsgs((prev) =>
      prev.map((m) => (m.id === id ? { ...m, status: 'approved' } : m)),
    );
  }

  function handleDeclineRefinement(id) {
    setRefineMsgs((prev) =>
      prev.map((m) => (m.id === id ? { ...m, status: 'declined' } : m)),
    );
  }

  // ── Launch ────────────────────────────────────────────────────────────────
  const handleLaunch = async () => {
    setError('');
    if (!workspaceId) {
      setError('Workspace not loaded yet — please wait a moment and try again.');
      return;
    }
    if (!leadsAllValid || parsedLeads.length === 0) {
      setError('Lead list has validation errors. Go back to the Leads step and fix them.');
      return;
    }
    // Same hard gate as the New Campaign wizard: without a representative every
    // email signs "<Product> Team". Uploaded leads get the identical cadence,
    // so they must not be exempt from it.
    if (!repName.trim() || !repTitle.trim()) {
      setError(
        'Add the representative name and role — outbound email is signed by a real person, not the product team.',
      );
      return;
    }
    setStep('launching');
    setLaunchResult(null);
    try {
      // Pydantic's HttpUrl rejects bare hostnames — normalise to
      // include the scheme before sending. Mirrors New Run behaviour.
      const payload = {
        workspace_id: workspaceId,
        url: _normaliseUrl(url),
        entity_type: entityType,
        product_description: (summaryText || '').trim() || null,
        knowledge_base: (knowledgeBase || '').trim() || null,
        // Signs every outbound email for this product (product.icp['brand']).
        rep_name: repName.trim(),
        rep_title: repTitle.trim(),
        leads: parsedLeads.map((l) => ({
          name: l.name,
          role: l.role,
          company: l.company,
          email: l.email,
          linkedin_url: l.linkedin_url || null,
          // Optional columns — sent only when present. Match score is coerced
          // to a number (blank/non-numeric → null so the Journey shows blank).
          match_score:
            l.match_score !== undefined &&
            String(l.match_score).trim() !== '' &&
            !Number.isNaN(Number(l.match_score))
              ? Number(l.match_score)
              : null,
          location: (l.location || '').trim() || null,
          phone: (l.phone || '').trim() || null,
        })),
      };
      const res = await authAxios.post(`${apiBase}/analyze/from-leads`, payload);
      setLaunchResult(res.data || {});
      setStep('launched');
      setMessage &&
        setMessage(
          `Campaign launched · ${res.data?.discovery?.leads_attached || 0} lead${
            (res.data?.discovery?.leads_attached || 0) === 1 ? '' : 's'
          } uploaded`,
        );

      // ── Fire-and-forget: upload KB files to /nexus/kb/upload ────────
      // /analyze/from-leads just returned the new product_id. Files were
      // held in memory until now; ship them to the background indexer.
      // We don't await this — the user is already moving to the launched
      // view. Indexing happens async and surfaces via the global toast.
      const newProductId = res.data?.product?.id || res.data?.product_id;
      const filesToUpload = kbFiles.filter((kb) => kb.file && kb.status === 'pending');
      if (newProductId && filesToUpload.length > 0) {
        const form = new FormData();
        form.append('product_id', String(newProductId));
        filesToUpload.forEach((kb) => form.append('files', kb.file, kb.filename));
        authAxios
          .post(`${apiBase}/kb/upload`, form, {
            headers: { 'Content-Type': 'multipart/form-data' },
          })
          .catch(() => {
            /* indexing is best-effort; failures show up in product KB tab */
          });
      }
    } catch (err) {
      const detail = err?.response?.data?.detail;
      // Three possible shapes the backend may return on error:
      //   1. Our custom { message, errors: [{ row, reason }] } from
      //      /analyze/from-leads — per-row lead validation failures.
      //   2. FastAPI's default pydantic 422 — an ARRAY of objects with
      //      { type, loc, msg, input, ctx }. Trying to render an array
      //      of objects as a React child throws "Objects are not valid
      //      as a React child" (which is what the user just hit).
      //   3. A plain string detail or an unexpected error.
      // Flatten everything down to a single string before setError.
      let msg;
      if (detail && typeof detail === 'object' && !Array.isArray(detail) && Array.isArray(detail.errors)) {
        // Shape #1: our structured per-row error.
        const head = detail.message || 'Validation failed.';
        const rows = detail.errors
          .slice(0, 3)
          .map((e) => `row ${e.row} (${e.field || '?'}): ${e.reason || ''}`)
          .join('; ');
        msg = `${head} — ${rows}${detail.errors.length > 3 ? `; +${detail.errors.length - 3} more` : ''}`;
      } else if (Array.isArray(detail)) {
        // Shape #2: pydantic's auto 422 — list of {loc, msg, type, ...}.
        msg = detail
          .slice(0, 3)
          .map((e) => {
            const where = Array.isArray(e?.loc) ? e.loc.join('.') : (e?.loc || '');
            return `${where}: ${e?.msg || e?.type || 'invalid'}`;
          })
          .join('; ') + (detail.length > 3 ? `; +${detail.length - 3} more` : '');
      } else if (typeof detail === 'string') {
        msg = detail;
      } else if (detail) {
        msg = JSON.stringify(detail);
      } else {
        msg = err?.message || 'Launch failed.';
      }
      setError(msg);
      setStep('summary');
    }
  };

  // ── Render ────────────────────────────────────────────────────────────────
  // Read-only User: the entire upload-leads wizard is a write flow, so show a
  // view-only notice instead of the campaign creator.
  if (isReadOnly(user)) {
    return (
      <div className="h-full overflow-y-auto bg-white">
        <div className="max-w-4xl mx-auto px-8 py-20 text-center">
          <p className="text-base font-bold text-[#2B2926]">View-only access</p>
          <p className="text-sm text-[#2B2926]/60 mt-2">
            Uploading leads and launching campaigns is available to Admins and the Master Admin.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto bg-white">
      {/* Compact white header — clean and minimal. */}
      <div className="border-b border-[#2B2926]/10 bg-white">
        <div className="max-w-4xl mx-auto px-8 py-4 flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-3 min-w-0">
            <button
              type="button"
              onClick={() => {
                resetWizard();
                if (onNavigate) onNavigate('gtm-journey');
              }}
              className="inline-flex items-center gap-1 px-2 py-1 rounded-md border border-[#2B2926]/10 text-[11px] font-bold text-[#2B2926]/70 hover:bg-[#F55600]/5 hover:text-[#F55600] hover:border-[#F55600]/30"
              title="Back to GTM Journey"
            >
              <ChevronLeft className="w-3.5 h-3.5" />
              Back
            </button>
            <h1 className="text-lg font-black text-[#2B2926] tracking-tight">
              Upload Your Leads
            </h1>
            <span className="text-[10px] font-bold uppercase tracking-wider text-[#F55600] bg-[#F55600]/10 px-2 py-0.5 rounded">
              BYO
            </span>
          </div>
          <StepBreadcrumbs step={step} />
        </div>
      </div>

      <div className="max-w-4xl mx-auto px-8 py-8">

        {/* Global error banner */}
        {error && (
          <div className="mb-4 px-3 py-2 rounded-lg bg-[#F55600]/10 border border-[#F55600]/30 text-xs text-[#F55600] flex items-start justify-between gap-3">
            <span>{error}</span>
            <button onClick={() => setError('')} className="shrink-0">
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {/* ───────── Step 1: Leads upload ───────── */}
        {step === 'leads' && (
          <div className="space-y-6">
            <div className="rounded-3xl border border-[#2B2926]/[0.08] bg-white p-8 shadow-sm">
              <div className="flex items-start gap-3 mb-5">
                <div className="w-11 h-11 rounded-xl bg-[#F55600] flex items-center justify-center shadow-sm shadow-[#F55600]/30 shrink-0">
                  <FileSpreadsheet className="w-5 h-5 text-white" />
                </div>
                <div className="flex-1">
                  <h2 className="text-lg font-bold text-[#2B2926] tracking-tight flex items-center gap-1.5">
                    Upload your lead list
                    {/* Hover-tooltip: keeps the header clean while still exposing
                        the file-format / required-column requirements on demand. */}
                    <span className="group relative inline-flex">
                      <Info className="w-4 h-4 text-[#2B2926]/35 hover:text-[#F55600] cursor-help transition-colors" />
                      <span
                        role="tooltip"
                        className="pointer-events-none absolute left-1/2 top-full z-20 mt-2 w-64 -translate-x-1/2 rounded-lg bg-[#2B2926] px-3 py-2 text-[12px] font-normal leading-relaxed text-white opacity-0 shadow-lg transition-opacity duration-150 group-hover:opacity-100"
                      >
                        CSV or Excel (.xlsx) with{' '}
                        <span className="font-semibold">Name, Role, Company, Email</span> (required) and{' '}
                        <span className="font-semibold">LinkedIn</span> (optional). Rows missing a
                        required field are rejected.
                      </span>
                    </span>
                  </h2>
                </div>
              </div>

              {/* Upload file — single full-width drop zone (paste-from-sheet
                  variant removed per UX feedback; file upload covers
                  CSV/TSV/XLSX which is all most users actually use). */}
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="group w-full flex flex-col items-center justify-center px-6 py-12 rounded-2xl border-[1.5px] border-dashed border-[#2B2926]/15 bg-[#F55600]/[0.035] hover:border-[#F55600] hover:bg-[#F55600]/[0.07] transition-all"
              >
                <div className="w-[60px] h-[60px] rounded-2xl bg-white border border-[#2B2926]/10 flex items-center justify-center shadow-sm group-hover:-translate-y-0.5 group-hover:shadow-md transition-all mb-4">
                  <Upload className="w-6 h-6 text-[#F55600]" />
                </div>
                <div className="text-[16px] font-bold text-[#2B2926]">
                  Drop your file here, or <span className="text-[#F55600]">browse</span>
                </div>
                <div className="text-[13px] text-[#2B2926]/55 mt-1">
                  Drag a file anywhere onto this area to upload
                </div>
                <div className="flex items-center justify-center gap-2 mt-4 flex-wrap">
                  {['.CSV', '.XLSX'].map((c) => (
                    <span
                      key={c}
                      className="text-[11.5px] font-semibold text-[#2B2926] bg-white border border-[#2B2926]/12 px-2.5 py-1 rounded-md shadow-[0_1px_2px_rgba(43,41,38,0.04)]"
                      style={{ fontFamily: '"JetBrains Mono", ui-monospace, monospace' }}
                    >
                      {c}
                    </span>
                  ))}
                </div>
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv,.txt,.xlsx,.xls,text/csv"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) handleFile(f);
                  e.target.value = '';
                }}
              />

              {/* Requirement note */}
              <div className="mt-4 flex items-start gap-2.5 px-4 py-3 rounded-xl bg-[#FAFAFB] border border-[#2B2926]/10 text-[12.5px] text-[#2B2926]/60 leading-relaxed">
                <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-[#2B2926]/40" />
                <span>
                  <b className="text-[#2B2926] font-bold">Heads up:</b> the entire file is rejected if any row is missing a required field, so check your columns before uploading.
                </span>
              </div>

              {parseError && (
                <div className="mt-5 px-4 py-3 rounded-lg bg-[#F55600]/8 border border-[#F55600]/25 text-sm text-[#F55600] font-medium flex items-start gap-2">
                  <span className="text-base leading-none">⚠</span>
                  <span>{parseError}</span>
                </div>
              )}
            </div>

            {/* Preview — premium card with refined table styling */}
            {parsedLeads.length > 0 && (
              <div className="rounded-3xl border border-[#2B2926]/[0.08] bg-white shadow-sm overflow-hidden">
                <div className="px-6 py-4 border-b border-[#2B2926]/10 flex items-center justify-between gap-3 bg-gradient-to-br from-black/[0.02] to-transparent">
                  <div className="flex items-center gap-3 min-w-0">
                    <div
                      className={[
                        'w-8 h-8 rounded-lg flex items-center justify-center shrink-0',
                        leadsAllValid
                          ? 'bg-[#10B981]/10 text-[#10B981]'
                          : 'bg-[#F55600]/10 text-[#F55600]',
                      ].join(' ')}
                    >
                      {leadsAllValid ? <CheckCircle2 className="w-4 h-4" /> : <X className="w-4 h-4" />}
                    </div>
                    <div className="min-w-0">
                      <div className="text-sm font-black text-[#2B2926] truncate">
                        {parseFileName}
                      </div>
                      <div className="text-[11px] text-[#2B2926]/60 font-medium">
                        <span
                          className={leadsAllValid ? 'text-[#10B981]' : 'text-[#F55600]'}
                        >
                          {parsedLeads.length - invalidCount} of {parsedLeads.length} valid
                        </span>
                      </div>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      setParsedLeads([]);
                      setParseFileName('');
                      setPasteText('');
                    }}
                    className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md border border-[#2B2926]/10 text-[11px] font-bold text-[#2B2926]/60 hover:bg-[#F55600]/5 hover:text-[#F55600] hover:border-[#F55600]/30"
                  >
                    <Trash2 className="w-3 h-3" />
                    Clear
                  </button>
                </div>
                <div className="max-h-96 overflow-auto">
                  <table className="w-full text-left">
                    <thead className="sticky top-0 z-10 bg-white">
                      <tr className="border-b border-[#2B2926]/10">
                        {['#', 'Name', 'Role', 'Company', 'Email', ...optionalCols.map((c) => c.label)].map((h) => (
                          <th
                            key={h}
                            className="px-3 py-3 text-[9px] font-black uppercase tracking-[0.15em] text-[#2B2926]/40"
                          >
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {parsedLeads.map((l, idx) => {
                        const v = l._v || { valid: true, errors: {} };
                        const cellCls = (field) =>
                          [
                            'px-3 py-2.5 text-[12px] max-w-[200px] truncate',
                            v.errors[field]
                              ? 'bg-[#F55600]/8 text-[#F55600] font-bold'
                              : 'text-[#2B2926]/80',
                          ].join(' ');
                        return (
                          <tr
                            key={idx}
                            className={[
                              'border-b border-[#2B2926]/5 last:border-b-0',
                              idx % 2 === 1 ? 'bg-[#2B2926]/[0.015]' : '',
                            ].join(' ')}
                          >
                            <td className="px-3 py-2.5 text-[10px] text-[#2B2926]/40 tabular-nums font-bold">
                              {String(idx + 1).padStart(2, '0')}
                            </td>
                            <td className={cellCls('name')} title={v.errors.name || l.name}>
                              <span className="font-bold text-[#2B2926]">{l.name || '—'}</span>
                            </td>
                            <td className={cellCls('role')} title={v.errors.role || l.role}>
                              {l.role || '—'}
                            </td>
                            <td className={cellCls('company')} title={v.errors.company || l.company}>
                              {l.company || '—'}
                            </td>
                            <td className={cellCls('email')} title={v.errors.email || l.email}>
                              {l.email || '—'}
                            </td>
                            {optionalCols.map((c) => (
                              <td
                                key={c.key}
                                className="px-3 py-2.5 text-[12px] max-w-[200px] truncate text-[#2B2926]/70"
                                title={typeof l[c.key] === 'string' ? l[c.key] : ''}
                              >
                                {renderOptionalCell(l, c.key)}
                              </td>
                            ))}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                {!leadsAllValid && (
                  <div className="px-6 py-3 border-t border-[#F55600]/20 bg-[#F55600]/5 text-xs text-[#F55600] font-bold flex items-center gap-2">
                    <span className="text-base leading-none">⚠</span>
                    {invalidCount} row{invalidCount === 1 ? '' : 's'} missing required fields
                    or with invalid emails — fix the file and re-upload to continue.
                  </div>
                )}
              </div>
            )}

            <div className="flex items-center justify-end gap-3">
              <button
                type="button"
                onClick={() => setStep('url')}
                disabled={!leadsAllValid}
                className="inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-[#F55600] text-white text-sm font-black disabled:opacity-40 disabled:cursor-not-allowed hover:opacity-95 shadow-lg shadow-[#F55600]/25 transition-all"
              >
                Continue
                <ArrowRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}

        {/* ───────── Step 2: URL + entity ───────── */}
        {step === 'url' && (
          <div className="animate-in fade-in duration-300">
            <div className="text-center mb-6">
              <h2 className="text-3xl font-black tracking-tight text-[#2B2926]">
                {entityType === 'service'
                  ? 'What service do you offer?'
                  : entityType === 'gcc'
                  ? 'What does your GCC firm offer?'
                  : "What's your product?"}
              </h2>
            </div>

            {/* One combined composer — a website URL or product details, an
                inline Product / Service / GCC dropdown, and a send button.
                Mirrors the New Campaign wizard's smart box (minus the "+"
                knowledge attach — this step is URL/details + entity only). */}
            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleUrlSubmit();
              }}
            >
              <div
                className="relative mx-auto"
                style={{
                  maxWidth: 600,
                  margin: '0 auto 12px',
                  background: '#fff',
                  borderRadius: 24,
                  boxShadow: '0 0 0 1.5px #F55600, 0 2px 10px rgba(18,20,24,0.06)',
                  padding: 6,
                }}
              >
                <textarea
                  ref={urlInputRef}
                  rows={1}
                  style={{
                    width: '100%',
                    boxSizing: 'border-box',
                    border: 'none',
                    outline: 'none',
                    background: 'transparent',
                    fontSize: 16,
                    color: '#15171c',
                    padding: '16px 14px 8px',
                    resize: 'none',
                    lineHeight: 1.5,
                    maxHeight: 220,
                    overflowY: 'auto',
                    fontFamily: 'inherit',
                  }}
                  placeholder="Website URL or product details…"
                  value={url}
                  onChange={(e) => {
                    setUrl(e.target.value);
                    setError('');
                    // Auto-grow up to maxHeight, then scroll for long pastes.
                    e.target.style.height = 'auto';
                    e.target.style.height = Math.min(e.target.scrollHeight, 220) + 'px';
                  }}
                  onKeyDown={(e) => {
                    // Enter submits; Shift+Enter = newline.
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      handleUrlSubmit();
                    }
                  }}
                  autoFocus
                />
                <div className="flex items-center gap-2" style={{ padding: '4px 6px 6px' }}>
                  {/* Product / Service / GCC dropdown */}
                  <div style={{ position: 'relative' }}>
                    <button
                      type="button"
                      onClick={() => setEntityMenuOpen((v) => !v)}
                      className="inline-flex items-center gap-2"
                      style={{
                        height: 40,
                        padding: '0 14px',
                        borderRadius: 12,
                        border: '1.5px solid rgba(43,41,38,0.30)',
                        background: '#fff',
                        fontWeight: 700,
                        fontSize: 13.5,
                        cursor: 'pointer',
                        color: '#2B2926',
                        boxShadow: '0 2px 6px rgba(43,41,38,0.06)',
                        transition: 'all 0.2s ease',
                      }}
                    >
                      <span style={{ color: '#F55600', display: 'grid', placeItems: 'center', width: 16, height: 16 }}>
                        {entityType === 'service' ? (
                          <Edit3 className="w-4 h-4" />
                        ) : (
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" width="16" height="16">
                            <path d="M21 8 12 3 3 8v8l9 5 9-5z" />
                            <path d="m3 8 9 5 9-5M12 13v8" />
                          </svg>
                        )}
                      </span>
                      {entityType === 'service' ? 'Service' : entityType === 'gcc' ? 'GCC' : 'Product'}
                      <ChevronDown className="w-3.5 h-3.5" style={{ color: '#9aa0ab' }} />
                    </button>
                    {entityMenuOpen && (
                      <>
                        <div
                          onClick={() => setEntityMenuOpen(false)}
                          style={{ position: 'fixed', inset: 0, zIndex: 90 }}
                        />
                        <div
                          style={{
                            position: 'absolute',
                            top: 'calc(100% + 6px)',
                            left: 0,
                            zIndex: 100,
                            background: '#fff',
                            border: '1px solid #e7eaee',
                            borderRadius: 14,
                            boxShadow: '0 12px 28px rgba(18,20,24,0.14)',
                            padding: 4,
                            minWidth: 160,
                          }}
                        >
                          {[
                            { v: 'product', label: 'Product' },
                            { v: 'service', label: 'Service' },
                            { v: 'gcc', label: 'GCC' },
                          ].map((opt) => {
                            const sel = entityType === opt.v;
                            return (
                              <button
                                key={opt.v}
                                type="button"
                                onClick={() => {
                                  setEntityType(opt.v);
                                  setEntityMenuOpen(false);
                                }}
                                className="flex items-center gap-2 w-full text-left"
                                style={{
                                  padding: '8px 12px',
                                  borderRadius: 10,
                                  fontSize: 13.5,
                                  fontWeight: 600,
                                  background: sel ? '#fff3ee' : '#fff',
                                  color: sel ? '#F55600' : '#15171c',
                                  border: 'none',
                                  cursor: 'pointer',
                                }}
                                onMouseEnter={(e) => {
                                  if (!sel) e.currentTarget.style.background = '#f4f6f7';
                                }}
                                onMouseLeave={(e) => {
                                  if (!sel) e.currentTarget.style.background = '#fff';
                                }}
                              >
                                {opt.label}
                              </button>
                            );
                          })}
                        </div>
                      </>
                    )}
                  </div>

                  <span style={{ flex: 1 }} />

                  {/* Send */}
                  <button
                    type="submit"
                    disabled={!url.trim()}
                    style={{
                      width: 38,
                      height: 38,
                      borderRadius: 14,
                      background: '#F55600',
                      color: '#fff',
                      border: 'none',
                      display: 'grid',
                      placeItems: 'center',
                      cursor: url.trim() ? 'pointer' : 'not-allowed',
                      opacity: url.trim() ? 1 : 0.4,
                      flex: '0 0 auto',
                    }}
                    aria-label="Continue"
                  >
                    <ArrowRight className="w-[17px] h-[17px]" />
                  </button>
                </div>
              </div>
            </form>

            <div className="mt-6 max-w-[600px] mx-auto flex items-center justify-start">
              <button
                type="button"
                onClick={() => setStep('leads')}
                className="inline-flex items-center gap-1 text-sm font-bold text-[#2B2926]/60 hover:text-[#F55600]"
              >
                <ChevronLeft className="w-3.5 h-3.5" />
                Back
              </button>
            </div>
          </div>
        )}

        {/* ───────── Step 3: Scraping ─────────
            Mirrors the New Run scraping step exactly: pulsing emoji
            badge + a tall list of checkpoints that flip from idle dot →
            spinner → green check. */}
        {step === 'scraping' && (
          <div className="animate-in fade-in duration-300 flex flex-col items-center py-10">
            <div className="relative w-24 h-24 mb-6">
              <div className="absolute inset-0 rounded-full bg-[#F55600]/10 animate-pulse" />
              <div className="absolute inset-0 flex items-center justify-center text-4xl">
                {SCRAPE_STEPS[scrapeStep].emoji}
              </div>
            </div>
            <div className="w-full max-w-md space-y-2">
              {SCRAPE_STEPS.map((s, i) => {
                const done = i < scrapeStep;
                const active = i === scrapeStep;
                return (
                  <div
                    key={i}
                    className={[
                      'flex items-center gap-3 px-3 py-2 rounded-lg transition-all',
                      done ? 'opacity-50' : '',
                      active ? 'bg-[#F55600]/5' : '',
                    ].join(' ')}
                  >
                    <div className="w-5 h-5 shrink-0 flex items-center justify-center">
                      {done ? (
                        <CheckCircle2 className="w-4 h-4 text-[#10B981]" />
                      ) : active ? (
                        <Loader2 className="w-4 h-4 text-[#F55600] animate-spin" />
                      ) : (
                        <div className="w-2 h-2 rounded-full bg-[#2B2926]/20" />
                      )}
                    </div>
                    <span
                      className={[
                        'text-sm',
                        active ? 'font-bold text-[#2B2926]' : 'text-[#2B2926]/60',
                      ].join(' ')}
                    >
                      {s.label}
                    </span>
                  </div>
                );
              })}
            </div>
            <p className="mt-6 text-xs text-[#2B2926]/40 truncate max-w-full">{url}</p>
          </div>
        )}

        {/* ───────── Step 4: Summary ───────── */}
        {step === 'summary' && (
          <div className="animate-in fade-in duration-300">
            <div className="text-center mb-6">
              <h2 className="text-3xl font-black tracking-tight text-[#2B2926]">
                {entityType === 'service' ? 'Here\'s what we read' : 'Here\'s what we found'}
              </h2>
            </div>

            <div className="max-w-2xl mx-auto rounded-2xl border border-[#2B2926]/10 bg-white p-5 shadow-sm">
              <div className="flex items-center gap-2 mb-3 flex-wrap">
                <FaviconImg src={getDuckDuckGoFavicon(url)} url={url} size={20} className="shrink-0" />
                <h3 className="text-sm font-black text-[#2B2926]">
                  {productName || (entityType === 'service' ? 'Your service' : 'Your product')}
                </h3>
              </div>

              {/* When Gemini returned a structured product_description we
                  render New Campaign's 3-section layout (Who we are / What we
                  do / Our focus industries) read-only. Otherwise fall back to
                  the flat summaryText editor. `summaryText` stays the launch
                  value in both cases. */}
              {(() => {
                const pd = productDescription;
                const hasStructured =
                  pd &&
                  ((pd.what_the_company_is && pd.what_the_company_is.trim()) ||
                    (pd.what_they_do && pd.what_they_do.trim()) ||
                    (pd.who_they_serve && pd.who_they_serve.trim()) ||
                    (pd.key_capabilities?.length || 0) > 0 ||
                    (pd.target_industries?.length || 0) > 0);
                if (hasStructured) {
                  return (
                    <div>
                      <DescSection title="Who we are" body={pd.what_the_company_is} />
                      <DescSection
                        title="What we do"
                        body={pd.what_they_do}
                        lists={[
                          {
                            label: entityType === 'product' ? 'Features' : 'Services',
                            items: pd.key_capabilities || [],
                          },
                        ]}
                      />
                      <DescSection
                        title="Our focus industries"
                        body={pd.who_they_serve}
                        lists={[{ label: 'Industries', items: pd.target_industries || [] }]}
                      />
                    </div>
                  );
                }
                return (
                  <>
                    <div className="flex items-center justify-between mb-2">
                      <label className="text-[10px] font-black uppercase tracking-wider text-[#2B2926]/50">
                        {entityType === 'service'
                          ? 'Company Description'
                          : entityType === 'gcc'
                          ? 'GCC Description'
                          : 'Product Description'}
                      </label>
                      {editingId !== 'summary' && (
                        <button
                          type="button"
                          onClick={() => setEditingId('summary')}
                          className="inline-flex items-center gap-1 px-2 py-1 rounded-md border border-[#2B2926]/10 text-[#2B2926]/70 text-[11px] font-bold hover:bg-[#F55600]/5 hover:text-[#F55600] hover:border-[#F55600]/30"
                        >
                          <Edit3 className="w-3 h-3" />
                          Edit
                        </button>
                      )}
                    </div>

                    {editingId === 'summary' ? (
                      <div className="relative">
                        <textarea
                          ref={(el) => {
                            summaryTextareaRef.current = el;
                            if (el) {
                              _resizeTextarea(el);
                              // preventScroll keeps the page where it was when
                              // the user clicked Edit — default focus behaviour
                              // would scroll the textarea into view and bump
                              // the whole layout up.
                              el.focus({ preventScroll: true });
                            }
                          }}
                          value={summaryText}
                          onChange={(e) => {
                            setSummaryText(e.target.value);
                            _resizeTextarea(e.target);
                          }}
                          className="w-full text-sm text-[#2B2926] bg-[#F55600]/[0.03] border border-[#F55600]/30 rounded-lg px-3 py-2 pb-10 resize-none overflow-hidden focus:outline-none focus:border-[#F55600] leading-relaxed"
                          spellCheck={false}
                          rows={_estimateRows(summaryText)}
                        />
                        <button
                          type="button"
                          onClick={() => setEditingId(null)}
                          className="absolute bottom-2 right-2 inline-flex items-center gap-1 px-2 py-1 rounded-md bg-[#10B981] text-white text-[11px] font-bold hover:opacity-90 shadow-sm"
                        >
                          <CheckCircle2 className="w-3 h-3" />
                          Done
                        </button>
                      </div>
                    ) : (
                      <div className="text-sm text-[#2B2926] whitespace-pre-wrap leading-relaxed">
                        {summaryText || (
                          <span className="text-[#2B2926]/40 italic">
                            No description yet — click Edit to add one, or skip to use just the URL.
                          </span>
                        )}
                      </div>
                    )}
                  </>
                );
              })()}
            </div>

            {/* Refine chat thread — each instruction is rendered as a
                right-aligned user bubble followed by the AI's response
                (loading / pending / approved / declined). Pending replies
                get Apply / Discard buttons inline. */}
            {refineMsgs.length > 0 && (
              <div className="max-w-2xl mx-auto mt-4 space-y-3">
                {refineMsgs.map((msg) => {
                  if (msg.role === 'user') {
                    return (
                      <div key={msg.id} className="flex justify-end">
                        <div className="px-3 py-2 rounded-2xl bg-[#2B2926] text-white text-sm max-w-lg">
                          {msg.content}
                        </div>
                      </div>
                    );
                  }
                  const isLoading = msg.status === 'loading';
                  const isPending = msg.status === 'pending';
                  const isApproved = msg.status === 'approved';
                  const isDeclined = msg.status === 'declined';
                  const isErrored = msg.status === 'error';
                  return (
                    <div key={msg.id} className="flex items-start gap-2">
                      <div className="w-7 h-7 rounded-full bg-[#F55600] flex items-center justify-center shrink-0">
                        <Sparkles className="w-3.5 h-3.5 text-white" />
                      </div>
                      <div className="flex-1 min-w-0 rounded-2xl border border-[#2B2926]/10 px-4 py-3 bg-white">
                        {isLoading && (
                          <div className="flex items-center gap-1">
                            <span className="w-1.5 h-1.5 bg-[#F55600] rounded-full animate-bounce" />
                            <span className="w-1.5 h-1.5 bg-[#F55600] rounded-full animate-bounce" style={{ animationDelay: '120ms' }} />
                            <span className="w-1.5 h-1.5 bg-[#F55600] rounded-full animate-bounce" style={{ animationDelay: '240ms' }} />
                          </div>
                        )}
                        {isErrored && (
                          <p className="text-sm text-[#F55600]">Something went wrong — try again.</p>
                        )}
                        {!isLoading && !isErrored && (
                          isPending ? (
                            <>
                              {/* Header row stays mounted in both modes so
                                  the textarea below it doesn't jump up by
                                  ~30px when Edit is clicked. The button is
                                  swapped with an invisible spacer during
                                  edit so its slot is preserved. */}
                              <div className="flex items-center justify-end mb-2 min-h-[26px]">
                                {editingId !== msg.id ? (
                                  <button
                                    type="button"
                                    onClick={() => setEditingId(msg.id)}
                                    className="inline-flex items-center gap-1 px-2 py-1 rounded-md border border-[#2B2926]/10 text-[#2B2926]/70 text-[11px] font-bold hover:bg-[#F55600]/5 hover:text-[#F55600] hover:border-[#F55600]/30"
                                  >
                                    <Edit3 className="w-3 h-3" />
                                    Edit
                                  </button>
                                ) : (
                                  <span className="invisible inline-flex items-center gap-1 px-2 py-1 text-[11px] font-bold">
                                    <Edit3 className="w-3 h-3" />
                                    Edit
                                  </span>
                                )}
                              </div>
                              {editingId === msg.id ? (
                                <div className="relative">
                                  <textarea
                                    className="w-full text-sm text-[#2B2926] bg-[#F55600]/[0.03] border border-[#F55600]/30 rounded-lg px-3 py-2 pb-10 resize-none overflow-hidden focus:outline-none focus:border-[#F55600] leading-relaxed"
                                    value={msg.content}
                                    onChange={(e) => {
                                      const next = e.target.value;
                                      setRefineMsgs((prev) =>
                                        prev.map((m) => (m.id === msg.id ? { ...m, content: next } : m)),
                                      );
                                      _resizeTextarea(e.target);
                                    }}
                                    spellCheck={false}
                                    rows={_estimateRows(msg.content)}
                                    ref={(el) => {
                                      if (el) {
                                        _resizeTextarea(el);
                                        el.focus({ preventScroll: true });
                                      }
                                    }}
                                  />
                                  <button
                                    type="button"
                                    onClick={() => setEditingId(null)}
                                    className="absolute bottom-2 right-2 inline-flex items-center gap-1 px-2 py-1 rounded-md bg-[#10B981] text-white text-[11px] font-bold hover:opacity-90 shadow-sm"
                                  >
                                    <CheckCircle2 className="w-3 h-3" />
                                    Done
                                  </button>
                                </div>
                              ) : (
                                <div className="text-sm text-[#2B2926] whitespace-pre-wrap leading-relaxed">
                                  {msg.content}
                                </div>
                              )}
                            </>
                          ) : (
                            <div className="text-sm text-[#2B2926] whitespace-pre-wrap leading-relaxed">
                              {msg.content}
                            </div>
                          )
                        )}
                        {isPending && (
                          <div className="flex items-center gap-2 mt-3 pt-3 border-t border-[#2B2926]/10">
                            <button
                              type="button"
                              onClick={() => handleApproveRefinement(msg.id, msg.content)}
                              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-[#10B981] text-white text-xs font-bold hover:opacity-90"
                            >
                              <CheckCircle2 className="w-3 h-3" />
                              Apply changes
                            </button>
                            <button
                              type="button"
                              onClick={() => handleDeclineRefinement(msg.id)}
                              className="text-xs font-bold text-[#2B2926]/60 hover:text-[#F55600]"
                            >
                              Discard
                            </button>
                          </div>
                        )}
                        {isApproved && (
                          <div className="inline-flex items-center gap-1 mt-2 text-[11px] font-bold text-[#10B981]">
                            <CheckCircle2 className="w-3 h-3" />
                            Applied
                          </div>
                        )}
                        {isDeclined && (
                          <div className="inline-flex items-center gap-1 mt-2 text-[11px] font-bold text-[#2B2926]/40">
                            Discarded
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {/* Refine input — same pill style as the URL input but smaller. */}
            <div className="max-w-2xl mx-auto mt-4">
              <form onSubmit={handleRefineSubmit}>
                <div className="flex items-center gap-2 border border-[#2B2926]/10 rounded-xl pl-3 pr-1 py-1 focus-within:border-[#F55600] bg-white shadow-sm">
                  <Sparkles className="w-4 h-4 text-[#F55600] shrink-0" />
                  <input
                    type="text"
                    className="flex-1 text-sm text-[#2B2926] placeholder:text-[#2B2926]/40 bg-transparent focus:outline-none py-2"
                    placeholder="Ask me to change anything — tone, length, focus…"
                    value={refineInput}
                    onChange={(e) => setRefineInput(e.target.value)}
                    disabled={isRefining}
                    autoComplete="off"
                  />
                  <button
                    type="submit"
                    disabled={isRefining || !refineInput.trim()}
                    className="inline-flex items-center justify-center w-8 h-8 rounded-lg bg-[#F55600] text-white hover:opacity-90 disabled:opacity-40 transition-opacity"
                    aria-label="Refine"
                  >
                    {isRefining ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Send className="w-3.5 h-3.5" />
                    )}
                  </button>
                </div>
              </form>
            </div>

            {/* Who outbound email is signed by. Required — uploaded leads get
                the same cadence, so they are not exempt from having a sender. */}
            <div className="max-w-2xl mx-auto mt-6">
              <RepresentativeCard
                user={user}
                repIsMe={repIsMe}
                setRepIsMe={setRepIsMe}
                repName={repName}
                setRepName={setRepName}
                repTitle={repTitle}
                setRepTitle={setRepTitle}
                productName={productName}
                disabled={step === 'launching'}
              />
            </div>

            <div className="max-w-2xl mx-auto mt-6 flex items-center justify-between">
              <button
                type="button"
                onClick={() => setStep('url')}
                className="inline-flex items-center gap-1 text-sm font-bold text-[#2B2926]/60 hover:text-[#F55600]"
              >
                <ChevronLeft className="w-3.5 h-3.5" />
                Back
              </button>
              <button
                type="button"
                onClick={handleLaunch}
                disabled={!leadsAllValid}
                className="inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-[#F55600] text-white text-sm font-black disabled:opacity-40 disabled:cursor-not-allowed hover:opacity-95 shadow-lg shadow-[#F55600]/25 transition-all"
              >
                Launch outreach
                <ArrowRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}

        {/* ───────── Step 5: Launching ───────── */}
        {step === 'launching' && (
          <div className="animate-in fade-in duration-300 flex flex-col items-center py-10">
            <div className="relative w-24 h-24 mb-6">
              <div className="absolute inset-0 rounded-full bg-[#F55600]/10 animate-pulse" />
              <div className="absolute inset-0 flex items-center justify-center text-4xl">🚀</div>
            </div>
            <h2 className="text-3xl font-black tracking-tight text-[#2B2926] mb-2">
              Launching your campaign…
            </h2>
            <p className="text-sm text-[#2B2926]/60 text-center max-w-md">
              Creating your product, attaching {parsedLeads.length} lead
              {parsedLeads.length === 1 ? '' : 's'}, and queuing email + LinkedIn
              touchpoints.
            </p>
            <Loader2 className="w-5 h-5 text-[#F55600] animate-spin mt-6" />
          </div>
        )}

        {/* ───────── Step 7: Launched ───────── */}
        {step === 'launched' && launchResult && (
          <div className="animate-in fade-in duration-300">
            {/* Success hero — compact so the recap table gets the
                visual focus. Just a small check + a one-word title. */}
            <div className="flex items-center justify-center gap-2 py-4">
              <div className="w-7 h-7 rounded-full bg-[#10B981]/10 flex items-center justify-center">
                <CheckCircle2 className="w-4 h-4 text-[#10B981]" />
              </div>
              <h2 className="text-lg font-black tracking-tight text-[#2B2926]">
                Outreach launched
              </h2>
            </div>

            {/* Recap table — shows every uploaded lead so the operator
                can confirm the right people made it in. Data comes from
                the in-memory `parsedLeads` (already validated client-side
                and accepted by the backend). */}
            {parsedLeads.length > 0 && (
              <div className="rounded-3xl border border-[#2B2926]/[0.08] bg-white shadow-sm overflow-hidden mt-2">
                <div className="px-6 py-4 border-b border-[#2B2926]/10 flex items-center justify-between gap-3 bg-gradient-to-br from-[#10B981]/[0.04] to-transparent">
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="w-8 h-8 rounded-lg bg-[#10B981]/10 flex items-center justify-center shrink-0">
                      <Users className="w-4 h-4 text-[#10B981]" />
                    </div>
                    <div className="min-w-0">
                      <div className="text-sm font-black text-[#2B2926]">
                        Leads added to campaign
                      </div>
                      <div className="text-[11px] text-[#2B2926]/60 font-medium">
                        <span className="text-[#10B981]">{parsedLeads.length} enrolled</span>{' '}
                        · {launchResult.campaign?.name || 'Your campaign'}
                      </div>
                    </div>
                  </div>
                </div>
                <div className="max-h-[420px] overflow-auto">
                  <table className="w-full text-left">
                    <thead className="sticky top-0 z-10 bg-white">
                      <tr className="border-b border-[#2B2926]/10">
                        {['#', 'Name', 'Role', 'Company', 'Email', ...optionalCols.map((c) => c.label)].map((h) => (
                          <th
                            key={h}
                            className="px-3 py-3 text-[9px] font-black uppercase tracking-[0.15em] text-[#2B2926]/40"
                          >
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {parsedLeads.map((l, idx) => (
                        <tr
                          key={idx}
                          className={[
                            'border-b border-[#2B2926]/5 last:border-b-0',
                            idx % 2 === 1 ? 'bg-[#2B2926]/[0.015]' : '',
                          ].join(' ')}
                        >
                          <td className="px-3 py-2.5 text-[10px] text-[#2B2926]/40 tabular-nums font-bold">
                            {String(idx + 1).padStart(2, '0')}
                          </td>
                          <td className="px-3 py-2.5 text-[12px] max-w-[200px] truncate font-bold text-[#2B2926]" title={l.name}>
                            {l.name || '—'}
                          </td>
                          <td className="px-3 py-2.5 text-[12px] max-w-[200px] truncate text-[#2B2926]/80" title={l.role}>
                            {l.role || '—'}
                          </td>
                          <td className="px-3 py-2.5 text-[12px] max-w-[200px] truncate text-[#2B2926]/80" title={l.company}>
                            {l.company || '—'}
                          </td>
                          <td className="px-3 py-2.5 text-[12px] max-w-[220px] truncate text-[#2B2926]/70" title={l.email}>
                            {l.email || '—'}
                          </td>
                          {optionalCols.map((c) => (
                            <td
                              key={c.key}
                              className="px-3 py-2.5 text-[12px] max-w-[200px] truncate text-[#2B2926]/70"
                              title={typeof l[c.key] === 'string' ? l[c.key] : ''}
                            >
                              {renderOptionalCell(l, c.key)}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <div className="flex items-center justify-center mt-6">
              <button
                type="button"
                onClick={() => {
                resetWizard();
                if (onNavigate) onNavigate('gtm-journey');
              }}
                className="inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-[#F55600] text-white text-sm font-black hover:opacity-95 shadow-lg shadow-[#F55600]/25 transition-all"
              >
                <Users className="w-4 h-4" />
                View leads in Lead Journey
                <ArrowRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

// ── Step progress indicator (numbered nodes connected by a track) ─────────
const StepBreadcrumbs = ({ step }) => {
  const all = [
    { id: 'leads', label: 'Leads' },
    { id: 'url', label: 'Product' },
    { id: 'summary', label: 'Description' },
  ];
  // Roll non-user-visible states up to their nearest visible step so the
  // progress dot lands on the right node regardless of internal phase.
  const visible =
    step === 'scraping'
      ? 'url'
      : step === 'launching' || step === 'launched'
      ? 'summary'
      : step;
  const activeIdx = all.findIndex((s) => s.id === visible);

  return (
    <div className="hidden md:flex items-center gap-2">
      {all.map((s, i) => {
        const done = i < activeIdx;
        const active = i === activeIdx;
        return (
          <React.Fragment key={s.id}>
            <div className="flex items-center gap-1.5">
              <div
                className={[
                  'w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-black border-2',
                  done
                    ? 'bg-[#F55600] border-[#F55600] text-white'
                    : active
                    ? 'bg-white border-[#F55600] text-[#F55600] ring-2 ring-[#F55600]/20'
                    : 'bg-white border-[#2B2926]/15 text-[#2B2926]/40',
                ].join(' ')}
              >
                {done ? <CheckCircle2 className="w-3 h-3" /> : i + 1}
              </div>
              <span
                className={[
                  'text-[10px] font-black uppercase tracking-wider',
                  done
                    ? 'text-[#2B2926]/60'
                    : active
                    ? 'text-[#2B2926]'
                    : 'text-[#2B2926]/30',
                ].join(' ')}
              >
                {s.label}
              </span>
            </div>
            {i < all.length - 1 && (
              <div
                className={[
                  'w-6 h-px',
                  done ? 'bg-[#F55600]' : 'bg-[#2B2926]/10',
                ].join(' ')}
              />
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
};

export default NexusUploadLeads;