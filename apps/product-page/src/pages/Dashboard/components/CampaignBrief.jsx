import React, { useState, useRef, useEffect } from 'react';
import { Zap, FileText, Image as ImageIcon, X, ChevronDown, Building2, Upload, Info, Check, Ratio, Send, Radio, Linkedin, Instagram, Facebook, Clock, CalendarDays, Palette } from 'lucide-react';
import XIcon from '../../../components/icons/XIcon';
import { isReadOnly } from '../../../lib/permissions';
import CustomTimePicker from '../../../components/CustomTimePicker';
import AlertModal from '../../../components/ui/AlertModal';

/**
 * Reusable chip with a click-to-open popover menu. Used for Business DNA and
 * Aspect Ratio selectors. One-chip-open-at-a-time is enforced by the parent.
 */
const Chip = ({ icon: Icon, label, value, isOpen, onToggle, children, minWidth, align = 'left' }) => (
  <div className="relative" style={minWidth ? { minWidth } : undefined}>
    <button
      type="button"
      onClick={onToggle}
      // Tight padding + gap on the smallest mobile widths (360 / 375 / 390)
      // so the icon + label + value + chevron all fit inside ~160px per
      // grid cell without the chevron kissing the chip border. Desktop
      // (`sm:`) restores the original looser spacing.
      className={`w-full flex items-center gap-1 sm:gap-2 pl-2 sm:pl-3 pr-1.5 sm:pr-2.5 py-2 rounded-full border-2 text-[11px] font-bold transition-all shadow-sm
        ${isOpen
          ? 'border-[#F55600] bg-white text-[#F55600] shadow-md shadow-orange-100/20'
          : 'border-[#2B2926]/40 bg-white text-[#2B2926] hover:border-[#F55600] hover:text-[#F55600]'}`}
    >
      {Icon && <Icon className="w-3.5 h-3.5 shrink-0" />}
      {/* `tracking-wider` on mobile keeps the uppercase label readable but
          ~10% narrower than `tracking-widest`. Restored at `sm:`. */}
      <span className="text-[#2B2926] uppercase tracking-wider sm:tracking-widest text-[9px] font-semibold shrink-0">{label}</span>
      <span className="flex-1 min-w-0 truncate text-[#2B2926] font-semibold">{value}</span>
      <ChevronDown className={`w-3.5 h-3.5 shrink-0 text-[#2B2926] transition-transform ${isOpen ? 'rotate-180' : ''}`} />
    </button>
    {isOpen && (
      // On mobile (2-col grid) the dropdown spans BOTH columns so it never
      // overflows the card edge. We anchor `right-0` and use a width of
      // `calc(200% + 0.5rem)` (chip-width × 2 + the grid gap) which always
      // fits the row whether the chip is in column 1 or column 2.
      // `sm:` restores the original chip-anchored behaviour on desktop.
      <div
        className={`absolute z-30 top-full mt-2 sm:top-auto sm:bottom-full sm:mt-0 sm:mb-3 bg-white rounded-2xl border border-[#2B2926]/20 shadow-[0_12px_32px_rgba(43,41,38,0.16)] animate-in fade-in zoom-in-95 duration-150
          right-0 w-[calc(200%+0.5rem)] max-w-[calc(100vw-32px)]
          sm:w-max sm:min-w-[220px] sm:max-w-[calc(100vw-28px)]
          ${align === 'right' ? 'sm:right-0 sm:left-auto' : 'sm:left-0 sm:right-auto'}`}
      >
        {children}
      </div>
    )}
  </div>
);

const ChipMenuItem = ({ active, icon: Icon, label, sub, onClick }) => (
  <button
    type="button"
    onClick={onClick}
    className={`w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors border-l border-b border-b-[#2B2926]/[0.06] last:border-b-0
      ${active
        ? 'bg-[#F55600]/10 border-l-[#F55600]'
        : 'bg-white border-l-transparent hover:bg-[#F55600]/5 hover:border-l-[#F55600]/20'}`}
  >
    {Icon && (
      <div className={`w-7 h-7 rounded-lg flex items-center justify-center shrink-0 ${active ? 'bg-[#F55600] text-white' : 'bg-[#2B2926]/[0.06] text-[#67655E]'}`}>
        <Icon className="w-3.5 h-3.5" />
      </div>
    )}
    <div className="flex-1 min-w-0">
      <div className={`text-[12px] font-semibold truncate ${active ? 'text-[#F55600]' : 'text-[#2B2926]'}`}>{label}</div>
      {sub && <div className="text-[10px] text-[#67655E] font-normal truncate mt-0.5">{sub}</div>}
    </div>
    {active && <Check className="w-3.5 h-3.5 text-[#F55600] shrink-0" />}
  </button>
);

// Channels shown in the Channels chip dropdown. Add a platform here and it
// automatically flows through the picker, "Select all", and the post-type
// filtering below — no other edits needed.
const CHANNEL_OPTIONS = [
  { id: 'instagram', label: 'Instagram', icon: ({ className }) => <img src="/instagram.jpg" className={`${className} object-contain`} alt="Instagram" /> },
];

// Which platforms accept a given post type. Centralised so the rules live
// in one place and every list (picker, "select all", counts) derives from
// it dynamically.
//   - document (PDF carousel) → LinkedIn only
//   - YouTube → video only (it has no text/image post format)
//   - text → excludes Instagram (IG Graph API requires media)
const platformAllowedForType = (id, postType) => {
  if (postType === 'document') return id === 'linkedin';
  if (id === 'youtube' || id === 'tiktok') return postType === 'video';
  if (postType === 'text') return id !== 'instagram';
  return true;
};

// The set of platform ids available for a post type (drives "Select all").
const allowedPlatformIds = (postType) =>
  CHANNEL_OPTIONS.filter((p) => platformAllowedForType(p.id, postType)).map((p) => p.id);

// Aspect ratio options. 'Auto' maps to 1:1 per product decision for this pass;
// the backend wiring to actually render in these ratios is a follow-up turn.
const ASPECT_RATIOS = [
  { value: 'auto',  label: 'Auto',  sub: 'Defaults to 16:9' },
  { value: '16:9',  label: '16:9',  sub: 'Wide — LinkedIn / X / FB feed (default)' },
  { value: '1:1',   label: '1:1',   sub: 'Square — Instagram feed' },
  { value: '9:16',  label: '9:16',  sub: 'Story / Reel vertical' },
  { value: '4:5',   label: '4:5',   sub: 'Portrait feed' },
  { value: '5:4',   label: '5:4',   sub: 'Landscape feed' },
  { value: '3:4',   label: '3:4',   sub: 'Book portrait' },
  { value: '4:3',   label: '4:3',   sub: 'Classic landscape' },
  { value: '3:2',   label: '3:2',   sub: 'Photo landscape' },
  { value: '2:3',   label: '2:3',   sub: 'Photo portrait' },
  { value: '21:9',  label: '21:9',  sub: 'Cinematic' },
];

// Module-level cache for the /config/image-styles catalog so switching
// between campaigns / steps doesn't re-fetch. Populated on first
// Dashboard mount, then reused.
let _styleCatalogCache = [];

// Fallback used only if /config/image-styles fails on first load (dev
// machine offline, etc). Just the Auto default — the picker will still
// render, and the user can pick it or type anything.
const _AUTO_STYLE_FALLBACK = {
  slug: 'auto',
  label: 'Auto',
  group: 'auto',
  emoji: '✨',
  when_to_use: 'Let AI pick from your brand DNA and industry',
};

// Human-readable section titles for the grouped picker.
const _STYLE_GROUP_LABELS = {
  auto: '',
  physical_product: 'Physical Product',
  software_product: 'Software Product',
  consulting_services: 'Consulting & IT Services',
  religious: 'Religious / Devotional',
  travel_immigration: 'Travel & Immigration',
  education: 'Education / EdTech',
  creative: 'Creative',
};

const CampaignBrief = ({
  campaignBrief,
  setCampaignBrief,
  // Inline rejection message from the brief guard / refiner (HTTP 422).
  // Rendered directly under the textarea instead of via the global toast
  // so the user reads the reason while looking at the field they edit.
  briefError = '',
  setBriefError = () => {},
  // Channels + Post Strategy are still in scope for generation but live in
  // Settings (future). We intentionally do NOT render pickers for them here.
  selectedDashboardPlatforms,
  setSelectedDashboardPlatforms,
  // Agent post type from the parent dropdown (text | image | video | document).
  // When 'document', the Channels picker is filtered to LinkedIn only — the
  // other platforms reject PDFs and letting the user pick them here would
  // lead to silent per-platform failures at publish time.
  postType = 'image',
  postStrategy,
  setPostStrategy,
  scheduledDate,
  setScheduledDate,
  scheduledTime,
  setScheduledTime,
  selectedTimezone,
  setSelectedTimezone,
  scheduledDays,
  setScheduledDays,
  handleGenerateContent,
  contextFiles,
  setContextFiles,
  logoPreview,
  setLogoPreview,
  setLogoImage,
  selectedProduct,
  setSelectedProduct,
  // Aspect ratio state owned by the parent eventually; local default for now.
  aspectRatio: aspectRatioProp,
  setAspectRatio: setAspectRatioProp,
  // User-selected image style ("auto" | style slug). Owned by parent
  // so it survives step navigation. Defaults to "auto" — current
  // pipeline behavior, no prompt changes.
  imageStyle: imageStyleProp,
  setImageStyle: setImageStyleProp,
  // Optional product reference images. Each entry is a public S3 URL
  // (uploaded via /upload-image). Sent to /generate-content so gpt-image-2
  // uses the actual product as a reference in every slide.
  productReferenceImages = [],
  setProductReferenceImages = () => {},
  // Post type — used to hide the Style chip for text-only campaigns
  // (no image to style).
  dashboardPostType,
  authAxios,
  user,
  navigateToSettingsTab,
}) => {
  const logoInputRef = useRef(null);
  const docsInputRef = useRef(null);
  const productInputRef = useRef(null);
  const chipsRowRef = useRef(null);
  const [isUploadingProduct, setIsUploadingProduct] = useState(false);

  // Which chip dropdown is currently open (only one at a time). Values:
  // 'dna' | 'logo' | 'ratio' | null.
  const [openChip, setOpenChip] = useState(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showAlert, setShowAlert] = useState(false);

  // Local aspect-ratio state if the parent hasn't taken over yet. Default 16:9
  // (wide — best feed real estate for LinkedIn / X / Facebook). User can pick a
  // different value from the dropdown if they want something else.
  const [aspectRatioLocal, setAspectRatioLocal] = useState('16:9');
  const aspectRatio = aspectRatioProp ?? aspectRatioLocal;
  const setAspectRatio = setAspectRatioProp ?? setAspectRatioLocal;

  // Image style — same lift pattern as aspect ratio. Default "auto"
  // = current pipeline (no prompt changes). Any other slug triggers
  // STYLE LOCK on the backend Art Director prompt.
  const [imageStyleLocal, setImageStyleLocal] = useState('auto');
  const imageStyle = imageStyleProp ?? imageStyleLocal;
  const setImageStyle = setImageStyleProp ?? setImageStyleLocal;

  // Fetch the 15-style catalog from the backend on mount. Cached in
  // module state so switching campaigns / navigating between steps
  // doesn't re-fetch. Falls back to a minimal built-in list if the
  // network call fails (dev machine offline, etc).
  const [styleCatalog, setStyleCatalog] = useState(() => _styleCatalogCache);
  useEffect(() => {
    if (styleCatalog?.length) return;
    if (!authAxios) return;
    let cancelled = false;
    authAxios.get('/config/image-styles').then((res) => {
      const list = res?.data?.styles || [];
      if (cancelled || !list.length) return;
      _styleCatalogCache = list;
      setStyleCatalog(list);
    }).catch((err) => {
      // Non-fatal — leave the fallback in place. Log once for triage.
      // eslint-disable-next-line no-console
      console.warn('[CampaignBrief] failed to load image-styles catalog', err?.response?.status || err?.message);
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authAxios]);

  // Convenience map + resolved display info for the currently-picked style.
  const styleBySlug = React.useMemo(() => {
    const m = {};
    for (const s of (styleCatalog || [])) m[s.slug] = s;
    return m;
  }, [styleCatalog]);
  const currentStyle = styleBySlug[imageStyle] || styleBySlug.auto || _AUTO_STYLE_FALLBACK;
  const styleDisplayLabel = currentStyle.label;

  // Hide the Style chip entirely for text posts (no image to style).
  const showStyleChip = dashboardPostType !== 'text';

  // Close open chip when the user clicks outside any chip.
  useEffect(() => {
    if (!openChip) return;
    const onDown = (e) => {
      if (chipsRowRef.current && !chipsRowRef.current.contains(e.target)) {
        setOpenChip(null);
      }
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [openChip]);

  // Auto-sync logo preview to the selected Business DNA's logo. The user can
  // still override via the Logo chip's "Upload custom…" action; uploading
  // clears this effect's authority until they switch products again.
  useEffect(() => {
    // "None" (admin sentinel) → no brand context, no logo. Clear both so the
    // image generator doesn't accidentally stamp a leftover brand mark on a
    // brand-agnostic post.
    if (selectedProduct === '__none__') {
      setLogoPreview(null);
      setLogoImage(null);
    } else if (selectedProduct && user?.business_dna?.products?.[selectedProduct]) {
      const prodLogo = user.business_dna.products[selectedProduct].logo_url;
      setLogoPreview(prodLogo || null);
      setLogoImage(null);
    } else if (!selectedProduct && user?.business_dna?.logo_url) {
      setLogoPreview(user.business_dna.logo_url);
      setLogoImage(null);
    }
  }, [selectedProduct, user?.business_dna, setLogoPreview, setLogoImage]);

  const handleLogoUpload = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    setLogoImage(file);
    const reader = new FileReader();
    reader.onloadend = () => setLogoPreview(reader.result);
    reader.readAsDataURL(file);
    setOpenChip(null);
  };

  const handleDocsUpload = (e) => {
    const files = Array.from(e.target.files);
    setContextFiles((prev) => [...prev, ...files]);
    // reset so the same file can be re-picked
    if (e.target) e.target.value = null;
  };

  const removeDoc = (index) => {
    setContextFiles((prev) => prev.filter((_, i) => i !== index));
  };

  // Upload product reference photos to S3 immediately and store the URLs.
  // We upload up front (rather than batching at Generate time) so the
  // brief screen shows tile previews. Backend caps to 4; we mirror that
  // in the UI to discourage uploading more.
  const handleProductRefUpload = async (e) => {
    const files = Array.from(e.target.files || []);
    if (!files.length || !authAxios) return;
    const remainingSlots = Math.max(0, 4 - productReferenceImages.length);
    const toUpload = files.slice(0, remainingSlots);
    if (toUpload.length === 0) {
      // user already at the cap
      if (e.target) e.target.value = null;
      return;
    }
    setIsUploadingProduct(true);
    try {
      const uploaded = [];
      for (const file of toUpload) {
        const fd = new FormData();
        fd.append('file', file);
        const res = await authAxios.post('/upload-image', fd, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
        if (res?.data?.url) uploaded.push(res.data.url);
      }
      if (uploaded.length) {
        setProductReferenceImages((prev) => [...prev, ...uploaded].slice(0, 4));
      }
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error('product ref upload failed', err);
    } finally {
      setIsUploadingProduct(false);
      if (e.target) e.target.value = null;
    }
  };

  const removeProductRef = (index) => {
    setProductReferenceImages((prev) => prev.filter((_, i) => i !== index));
  };

  // ---------- Derived labels for chip values ----------
  // Members have admin-assigned brand(s). We pull the full list from
  // user.assigned_dna_product_ids (authoritative) and fall back to the
  // legacy single column. If the member has ONE brand → render a locked
  // chip. If they have MULTIPLE → render a clickable dropdown limited to
  // their assigned set.
  const isMember = isReadOnly(user);
  const memberAssignedIds = React.useMemo(() => {
    if (!isMember) return [];
    const list = Array.isArray(user?.assigned_dna_product_ids)
      ? user.assigned_dna_product_ids.filter(Boolean)
      : [];
    if (list.length > 0) return list;
    return user?.assigned_dna_product_id ? [user.assigned_dna_product_id] : [];
  }, [isMember, user?.assigned_dna_product_ids, user?.assigned_dna_product_id]);

  const dnaProducts = user?.business_dna?.products || {};
  const hasProducts = Object.keys(dnaProducts).length > 0;
  const companyName =
    user?.business_dna?.company_name || user?.company_name || 'Company Profile';

  // Resolve the human name for a product id — works for admin (reads from
  // their own business_dna.products) and gracefully falls back to the raw id
  // for members (they don't have admin's DNA products on their user object
  // yet; backend only exposes the assigned list of ids today).
  const productName = (id) => dnaProducts[id]?.product_name || id;

  // Sentinel for "no brand context at all" — admin-only. The backend
  // recognises this string and runs the agents with an empty user_context.
  const NO_DNA_SENTINEL = '__none__';
  const isNoDna = selectedProduct === NO_DNA_SENTINEL;

  const dnaLabel = isMember
    ? (selectedProduct ? productName(selectedProduct) : (memberAssignedIds[0] ? productName(memberAssignedIds[0]) : 'Assigned Brand'))
    : isNoDna
      ? 'None'
      : selectedProduct
        ? (dnaProducts[selectedProduct]?.product_name || selectedProduct)
        : companyName;

  // Keep `selectedProduct` in sync with the member's primary brand on mount,
  // and clamp any stray value that isn't in their assigned set.
  React.useEffect(() => {
    if (!isMember || memberAssignedIds.length === 0) return;
    if (!selectedProduct || !memberAssignedIds.includes(selectedProduct)) {
      setSelectedProduct(memberAssignedIds[0]);
    }
  }, [isMember, memberAssignedIds, selectedProduct, setSelectedProduct]);

  const logoLabel = logoPreview ? 'From DNA' : 'Not set';
  const ratioLabel =
    ASPECT_RATIOS.find((r) => r.value === aspectRatio)?.label || aspectRatio;

  // Channels chip summary — short string shown inside the chip, not the full
  // platform list. "All 4" / "LinkedIn +2" / "None" etc.
  const plats = selectedDashboardPlatforms || [];
  const channelsLabel = (() => {
    if (plats.length === 0) return 'None';
    if (plats.length === CHANNEL_OPTIONS.length) return `All ${CHANNEL_OPTIONS.length}`;
    const head = CHANNEL_OPTIONS.find((c) => c.id === plats[0])?.label || plats[0];
    return plats.length === 1 ? head : `${head} +${plats.length - 1}`;
  })();

  const isSchedulingRestricted = ['free', 'starter'].includes(user?.pricing_plan?.toLowerCase());

  return (
    <div className="space-y-3 animate-in fade-in slide-in-from-bottom-3 duration-700 pb-4 max-w-6xl mx-auto">
      {/* Header */}
      <div className="text-center pt-1">
        <h3 className="text-xl font-semibold text-[#2B2926] tracking-tight mb-0.5">
          Create <span className="text-[#F55600]">New Campaign</span>
        </h3>
        <p className="text-[11px] text-[#2B2926] font-semibold bg-white inline-block px-4 py-1.5 rounded-full border-2 border-[#10B981] shadow-sm">
          Define strategy and let Pipelyt AI handle the rest.
        </p>
      </div>

      {/* Main Card */}
      <div className="bg-white rounded-[28px] border-2 border-[#2B2926]/30 shadow-[0_15px_40px_rgba(255,107,53,0.03)] relative overflow-visible">
        {/* Subtle background accent */}
        <div className="absolute top-0 right-0 w-64 h-64 bg-white/40 rounded-full blur-3xl -mr-32 -mt-32 pointer-events-none" />

        {/* Card padding scales: p-4 (16px) on small phones gives chips +16px
            of width each — chevron stops kissing the card edge on 360-414px
            devices. Restored to p-6 from `sm:` up. */}
        <div className="relative p-3 sm:p-4 space-y-3">
          {/* Brief textarea
              Wrapper owns the border + rounded corners + overflow-hidden, so
              the textarea's scrollbar is clipped to the rounded shape and
              stays neatly inside the box. The textarea itself is a fixed-
              height scroll window; longer content scrolls inside the box. */}
          <div className={`rounded-[20px] border-2 ${briefError ? 'border-[#F55600] ring-4 ring-[#F55600]/10' : 'border-[#2B2926]/30 focus-within:border-[#F55600] focus-within:ring-4 focus-within:ring-[#F55600]/5'} transition-all bg-white shadow-sm overflow-hidden`}>
            <textarea
              value={campaignBrief}
              onChange={(e) => {
                setCampaignBrief(e.target.value);
                // Clear any prior rejection the moment the user starts editing
                // — keeping it pinned after they change the text would be stale.
                if (briefError) setBriefError('');
              }}
              placeholder="Describe the campaign you want to create…&#10;&#10;e.g. “Announce Pipelyt’s new AI content generation.”"
              className="block w-full h-[130px] bg-white p-4 text-sm text-[#2B2926] font-bold leading-relaxed outline-none resize-none overflow-y-auto placeholder:text-[#2B2926]/50"
            />
          </div>

          {briefError && (
            <div
              role="alert"
              className="flex items-start gap-2.5 rounded-[14px] border-2 border-[#F55600]/30 bg-[#F55600]/5 px-4 py-3 text-[12px] font-bold text-[#F55600] leading-snug"
            >
              <Info className="w-4 h-4 mt-0.5 shrink-0" />
              <span className="text-[#2B2926]/80">{briefError}</span>
            </div>
          )}

          {/* Chip row — Business DNA / Logo / Aspect Ratio. Two per row on
              mobile (grid), a single flowing row from `sm:` up. */}
          <div ref={chipsRowRef} className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center sm:gap-2.5">
            {/* Business DNA —
                • Member with 1 brand  → locked chip (no dropdown)
                • Member with 2+ brands → clickable chip that opens a picker
                                          limited to their assigned set
                • Admin                 → full selectable dropdown w/ all
                                          brands + "Company-wide" option */}
            {isMember && memberAssignedIds.length <= 1 ? (
              <div className="inline-flex items-center gap-2 bg-white border-2 border-[#F55600]/20 text-[#F55600] rounded-full px-3 py-1.5 text-xs font-semibold cursor-not-allowed select-none shadow-sm">
                <Building2 className="w-3.5 h-3.5" />
                <span className="uppercase tracking-wider text-[9px] opacity-70">Brand (locked)</span>
                <span>{dnaLabel}</span>
              </div>
            ) : isMember ? (
              <Chip
                icon={Building2}
                label="Assigned Brand"
                value={dnaLabel}
                isOpen={openChip === 'dna'}
                onToggle={() => setOpenChip(openChip === 'dna' ? null : 'dna')}
              >
                <div className="max-h-[280px] overflow-y-auto py-1">
                  {memberAssignedIds.map((id) => (
                    <ChipMenuItem
                      key={id}
                      active={selectedProduct === id}
                      icon={Building2}
                      label={productName(id)}
                      sub="Assigned by admin"
                      onClick={() => { setSelectedProduct(id); setOpenChip(null); }}
                    />
                  ))}
                </div>
              </Chip>
            ) : (
              <Chip
                icon={Building2}
                label="Business DNA"
                value={dnaLabel}
                isOpen={openChip === 'dna'}
                onToggle={() => setOpenChip(openChip === 'dna' ? null : 'dna')}
              >
                <div className="max-h-[280px] overflow-y-auto py-1">
                  <ChipMenuItem
                    active={selectedProduct === null || selectedProduct === undefined}
                    icon={Building2}
                    label={companyName}
                    sub="Company-wide brand DNA"
                    onClick={() => { setSelectedProduct(null); setOpenChip(null); }}
                  />
                  <ChipMenuItem
                    active={isNoDna}
                    icon={X}
                    label="None"
                    sub="No brand context — write from brief only"
                    onClick={() => { setSelectedProduct(NO_DNA_SENTINEL); setOpenChip(null); }}
                  />
                  {hasProducts && (
                    <div className="px-4 py-1.5 text-[8px] font-semibold text-slate-400 uppercase tracking-widest border-t border-slate-50 mt-1">
                      Products
                    </div>
                  )}
                  {Object.entries(dnaProducts).map(([id, prod]) => (
                    <ChipMenuItem
                      key={id}
                      active={selectedProduct === id}
                      icon={Building2}
                      label={prod.product_name || id}
                      sub={prod.tagline || prod.url || ''}
                      onClick={() => { setSelectedProduct(id); setOpenChip(null); }}
                    />
                  ))}
                </div>
              </Chip>
            )}

            {/* Logo */}
            <Chip
              icon={ImageIcon}
              label="Logo"
              value={logoLabel}
              align="right"
              isOpen={openChip === 'logo'}
              onToggle={() => setOpenChip(openChip === 'logo' ? null : 'logo')}
            >
              <div className="p-3 space-y-3 w-full sm:w-[260px]">
                <div className="flex items-center gap-3">
                  <div className="w-12 h-12 rounded-xl bg-slate-50 border-2 border-slate-100 flex items-center justify-center overflow-hidden shrink-0">
                    {logoPreview ? (
                      <img
                        src={logoPreview}
                        alt="Logo preview"
                        className="w-full h-full object-contain p-1"
                        onError={(e) => { e.currentTarget.style.display = 'none'; }}
                      />
                    ) : (
                      <ImageIcon className="w-5 h-5 text-slate-300" />
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest">
                      {logoPreview ? 'Logo active' : 'No logo set'}
                    </div>
                    <div className="text-[10px] text-[#2B2926] font-bold truncate">
                      {logoPreview ? 'Used on generated images' : 'Sync from Business DNA'}
                    </div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => logoInputRef.current?.click()}
                  className="w-full flex items-center justify-center gap-2 py-2 rounded-xl bg-[#F55600] text-white text-[10px] font-semibold uppercase tracking-widest hover:bg-[#F55600] transition-all shadow-md shadow-orange-100"
                >
                  <Upload className="w-3.5 h-3.5" /> Upload custom logo
                </button>
                {logoPreview && (
                  <button
                    type="button"
                    onClick={() => { setLogoImage(null); setLogoPreview(null); setOpenChip(null); }}
                    className="w-full flex items-center justify-center gap-2 py-1.5 text-[10px] font-semibold text-red-500 uppercase tracking-widest hover:bg-red-50 rounded-xl transition-colors"
                  >
                    <X className="w-3 h-3" /> Remove logo
                  </button>
                )}
                <input
                  ref={logoInputRef}
                  type="file"
                  onChange={handleLogoUpload}
                  hidden
                  accept="image/*"
                />
              </div>
            </Chip>

            {/* Aspect Ratio */}
            <Chip
              icon={Ratio}
              label="Aspect Ratio"
              value={ratioLabel}
              isOpen={openChip === 'ratio'}
              onToggle={() => setOpenChip(openChip === 'ratio' ? null : 'ratio')}
            >
              <div className="max-h-[200px] overflow-y-auto py-1 w-full sm:w-[240px]">
                {ASPECT_RATIOS.map((r) => (
                  <ChipMenuItem
                    key={r.value}
                    active={aspectRatio === r.value}
                    icon={Ratio}
                    label={r.label}
                    sub={r.sub}
                    onClick={() => { setAspectRatio(r.value); setOpenChip(null); }}
                  />
                ))}
              </div>
            </Chip>

            {/* Image Style — hidden for text posts (nothing to style).
                Auto default keeps the pipeline byte-for-byte identical to
                today. Any explicit pick triggers STYLE LOCK on the
                backend Art Director + industry playbook suppression. */}
            {showStyleChip && (
              <Chip
                icon={Palette}
                label="Style"
                value={styleDisplayLabel}
                isOpen={openChip === 'style'}
                onToggle={() => setOpenChip(openChip === 'style' ? null : 'style')}
              >
                <div className="max-h-[380px] overflow-y-auto py-1 w-full sm:w-[280px]">
                  {(() => {
                    // Group the catalog by section so the picker reads
                    // as three tidy lists (Physical Product / Service /
                    // Creative) with Auto sitting at the top by itself.
                    const groups = {};
                    for (const s of (styleCatalog || [_AUTO_STYLE_FALLBACK])) {
                      const g = s.group || 'auto';
                      if (!groups[g]) groups[g] = [];
                      groups[g].push(s);
                    }
                    const order = ['auto', 'physical_product', 'software_product', 'consulting_services', 'religious', 'travel_immigration', 'education', 'creative'];
                    return order.filter(g => groups[g]).map((g) => (
                      <React.Fragment key={g}>
                        {_STYLE_GROUP_LABELS[g] && (
                          <div className="px-4 pt-2 pb-1 text-[9px] font-semibold text-slate-400 uppercase tracking-widest">
                            {_STYLE_GROUP_LABELS[g]}
                          </div>
                        )}
                        {groups[g].map((s) => (
                          <ChipMenuItem
                            key={s.slug}
                            active={imageStyle === s.slug}
                            /* No icons AND no emojis on the label — the
                               dropdown reads as a clean text list. */
                            label={s.label}
                            sub={s.when_to_use}
                            onClick={() => { setImageStyle(s.slug); setOpenChip(null); }}
                          />
                        ))}
                      </React.Fragment>
                    ));
                  })()}
                </div>
              </Chip>
            )}

            {/* Channels — multi-select. Menu stays open across checkbox toggles
                so the user can pick several platforms in one interaction.
                Filtered to LinkedIn-only when postType === 'document'. */}
            <Chip
              icon={Radio}
              label="Channels"
              value={channelsLabel}
              align="right"
              isOpen={openChip === 'channels'}
              onToggle={() => setOpenChip(openChip === 'channels' ? null : 'channels')}
            >
              <div className="py-1 w-full sm:w-[240px]">
                <div className="flex items-center justify-between px-4 py-2 border-b border-slate-50">
                  <span className="text-[9px] font-semibold text-slate-400 uppercase tracking-widest">Platforms</span>
                  <button
                    type="button"
                    onClick={() => {
                      // "Select all" set is derived from the centralised
                      // post-type rules so it stays correct as platforms change.
                      const all = allowedPlatformIds(postType);
                      setSelectedDashboardPlatforms(
                        (selectedDashboardPlatforms || []).length === all.length ? [] : all
                      );
                    }}
                    className="text-[9px] font-semibold text-[#F55600] uppercase tracking-widest hover:underline"
                  >
                    {(selectedDashboardPlatforms || []).length === allowedPlatformIds(postType).length ? 'Clear all' : 'Select all'}
                  </button>
                </div>
                {postType === 'document' && (
                  <div className="px-4 py-2 text-[9px] font-bold text-[#F55600] bg-white border-b border-[#F55600]/10">
                    Document posts are LinkedIn-only — other platforms hidden.
                  </div>
                )}
                {postType === 'text' && (
                  <div className="px-4 py-2 text-[9px] font-bold text-purple-700 bg-white border-b border-purple-100">
                    Instagram hidden — IG Graph API requires media for every post.
                  </div>
                )}
                <div className="max-h-[168px] overflow-y-auto custom-scrollbar">
                {CHANNEL_OPTIONS.map((p) => {
                  const allowed = platformAllowedForType(p.id, postType);
                  const isSelected = allowed && (selectedDashboardPlatforms || []).includes(p.id);
                  const videoOnly = (p.id === 'youtube' || p.id === 'tiktok');
                  return (
                    <button
                      key={p.id}
                      type="button"
                      disabled={!allowed}
                      onClick={() => {
                        if (!allowed) return;
                        setSelectedDashboardPlatforms((prev) =>
                          (prev || []).includes(p.id)
                            ? prev.filter((x) => x !== p.id)
                            : [...(prev || []), p.id]
                        );
                      }}
                      className={`w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors border-l-2
                        ${!allowed
                          ? 'bg-white border-transparent opacity-40 cursor-not-allowed'
                          : isSelected
                          ? 'bg-[#F55600]/10 border-[#F55600]'
                          : 'bg-white border-transparent hover:bg-[#F55600]/5 hover:border-[#F55600]/20'}`}
                      title={!allowed ? (videoOnly ? 'Video posts only' : 'Not available for this post type') : undefined}
                    >
                      {/* Checkbox */}
                      <div className={`w-3.5 h-3.5 rounded border flex items-center justify-center shrink-0 transition-all
                        ${isSelected ? 'bg-[#F55600] border-[#F55600] shadow-sm shadow-slate-200' : 'bg-white border-slate-200'}`}>
                        {isSelected && <Check className="w-2 h-2 text-white" strokeWidth={4} />}
                      </div>
                      <p.icon className={`w-3.5 h-3.5 shrink-0 ${isSelected ? 'text-[#F55600]' : 'text-slate-400'}`} />
                      <span className={`text-[10px] font-bold flex-1 ${isSelected ? 'text-[#F55600]' : 'text-slate-600'}`}>
                        {p.label}
                      </span>
                      {!allowed && videoOnly && (
                        <span className="text-[8px] font-bold text-slate-400 uppercase tracking-wide shrink-0">Video only</span>
                      )}
                    </button>
                  );
                })}
                </div>
              </div>
            </Chip>

            {/* Post Strategy — Instant vs Schedule. When Schedule is picked
                the dropdown expands to show Date / Time / Duration inputs so
                the user can finish configuring without leaving the chip. */}
            <Chip
              icon={Send}
              label="Strategy"
              value={postStrategy === 'schedule' ? 'Scheduled' : 'Instant'}
              isOpen={openChip === 'strategy'}
              onToggle={() => setOpenChip(openChip === 'strategy' ? null : 'strategy')}
              align="right"
            >
              <div className="w-full sm:w-[280px]">
                <ChipMenuItem
                  active={postStrategy !== 'schedule'}
                  icon={Send}
                  label="Instant"
                  sub="Publish immediately"
                  onClick={() => { setPostStrategy('publish'); }}
                />
                <ChipMenuItem
                  active={postStrategy === 'schedule'}
                  icon={Clock}
                  label={isSchedulingRestricted ? "Schedule (Pro)" : "Schedule"}
                  sub={isSchedulingRestricted ? "Upgrade to Growth" : "Pick a future date and time"}
                  onClick={() => {
                    if (isSchedulingRestricted) {
                      setShowAlert(true);
                      return;
                    }
                    setPostStrategy('schedule');
                  }}
                />
                {postStrategy === 'schedule' && !isSchedulingRestricted && (
                  <div className="border-t border-slate-50 p-3 space-y-3 bg-white animate-in fade-in slide-in-from-top-1 duration-200">
                    <div className="space-y-1">
                      <label className="text-[10px] font-bold text-[#2B2926] uppercase tracking-[0.14em]">
                        Plan Duration
                      </label>
                      <div className="relative">
                        <input
                          type="number"
                          min="1"
                          max="30"
                          value={scheduledDays === undefined || scheduledDays === null ? '' : scheduledDays}
                          onChange={(e) => {
                            // Allow empty string so user can backspace and re-type.
                            // Don't coerce to 1 here — that defeats backspace.
                            const v = e.target.value;
                            if (v === '') {
                              setScheduledDays('');
                            } else {
                              const n = parseInt(v, 10);
                              if (!isNaN(n)) setScheduledDays(n);
                            }
                          }}
                          onBlur={(e) => {
                            // On blur, fall back to 1 if the user left it empty.
                            const n = parseInt(e.target.value, 10);
                            if (!n || isNaN(n) || n < 1) setScheduledDays(1);
                          }}
                          onFocus={(e) => e.target.select()}
                          className="w-full pl-3 pr-24 py-2 bg-white border-2 border-[#F55600]/30 rounded-xl text-[12px] font-bold text-[#2B2926] outline-none focus:border-[#F55600] focus:ring-2 focus:ring-[#F55600]/15 transition-all shadow-sm [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:m-0 [&::-webkit-inner-spin-button]:m-0"
                        />
                        <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[10px] font-bold text-[#2B2926]/70 uppercase tracking-[0.12em]">
                          {(scheduledDays || 1) > 1 ? 'Day campaign' : 'Single day'}
                        </span>
                      </div>
                    </div>

                    {(scheduledDays || 1) <= 1 && (
                      <>
                        <div className="space-y-1">
                          <label className="text-[10px] font-bold text-[#2B2926] uppercase tracking-[0.14em]">
                            Date
                          </label>
                          <div className="relative">
                            <CalendarDays className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#F55600] pointer-events-none" />
                            <input
                              type="date"
                              value={scheduledDate || ''}
                              onChange={(e) => setScheduledDate(e.target.value)}
                              className="w-full pl-9 pr-3 py-2.5 bg-white border-2 border-[#F55600]/30 rounded-xl text-[12px] font-bold text-[#2B2926] outline-none focus:border-[#F55600] focus:ring-2 focus:ring-[#F55600]/15 transition-all shadow-sm hover:border-[#F55600]/50 cursor-pointer"
                            />
                          </div>
                        </div>
                        <div className="space-y-1">
                          <label className="text-[10px] font-bold text-[#2B2926] uppercase tracking-[0.14em]">
                            Time
                          </label>
                          <CustomTimePicker
                            selectedTime={scheduledTime}
                            onTimeChange={setScheduledTime}
                            selectedTimezone={selectedTimezone}
                            onTimezoneChange={setSelectedTimezone}
                          />
                        </div>
                      </>
                    )}
                  </div>
                )}
              </div>
            </Chip>
          </div>
        </div>
      </div>

      {/* SEPARATE CARD — Advanced · Reference documents lives in its own
          card below the brief, so it reads as a distinct add-on section
          rather than buried under the chip row. */}
      <div className="bg-white rounded-[24px] border border-[#2B2926]/25 shadow-sm relative overflow-hidden">
        <div className="p-3 sm:p-4">
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            className="w-full flex items-center gap-2 text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.14em] hover:text-[#F55600] transition-colors"
          >
            <ChevronDown className={`w-3.5 h-3.5 transition-transform ${showAdvanced ? 'rotate-180' : ''}`} />
            Advanced · Reference documents {contextFiles.length > 0 && `(${contextFiles.length})`}
          </button>

          {showAdvanced && (
            <>
              <div className="mt-3 rounded-2xl border-2 border-[#2B2926]/40 bg-white p-3 space-y-2 animate-in fade-in slide-in-from-top-1 duration-200 shadow-sm">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <FileText className="w-4 h-4 text-[#F55600]" />
                    <span className="text-[10px] font-semibold text-slate-600 uppercase tracking-widest">
                      Strategy Context
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => docsInputRef.current?.click()}
                    className="text-[9px] font-semibold text-white bg-[#2B2926] hover:bg-[#F55600] uppercase tracking-widest px-3 py-1.5 rounded-lg border-2 border-[#2B2926] hover:border-[#F55600] transition-all shadow-sm"
                  >
                    Upload docs
                  </button>
                  <input
                    ref={docsInputRef}
                    type="file"
                    onChange={handleDocsUpload}
                    hidden
                    multiple
                    accept=".pdf,.csv,.docx,.txt"
                  />
                </div>

                {contextFiles.length > 0 ? (
                  <div className="space-y-1.5 max-h-[140px] overflow-y-auto pr-1">
                    {contextFiles.map((file, idx) => (
                      <div
                        key={idx}
                        className="flex items-center justify-between bg-white px-3 py-1.5 rounded-xl border border-slate-200"
                      >
                        <div className="flex items-center gap-2 truncate flex-1">
                          <FileText className="w-3 h-3 text-slate-400 shrink-0" />
                          <span className="text-[10px] font-bold text-slate-600 truncate">{file.name}</span>
                        </div>
                        <button
                          type="button"
                          onClick={() => removeDoc(idx)}
                          className="text-slate-300 hover:text-red-500 transition-colors"
                          aria-label={`Remove ${file.name}`}
                        >
                          <X className="w-3 h-3" />
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-[10px] font-medium text-slate-400">
                    Attach PDF, CSV, DOCX, or TXT to give the agent deeper context on this campaign.
                  </p>
                )}
              </div>

              {/* Product reference images — passed to gpt-image-2 as
                  references so generated visuals feature the actual
                  product instead of generic stock-style imagery. */}
              <div className="mt-3 rounded-2xl border-2 border-[#2B2926]/40 bg-white p-3 space-y-2 shadow-sm">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <ImageIcon className="w-4 h-4 text-[#F55600]" />
                    <span className="text-[10px] font-semibold text-slate-600 uppercase tracking-widest">
                      Product Reference Photos {productReferenceImages.length > 0 && `(${productReferenceImages.length}/4)`}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => productInputRef.current?.click()}
                    disabled={isUploadingProduct || productReferenceImages.length >= 4}
                    className="text-[9px] font-semibold text-white bg-[#2B2926] hover:bg-[#F55600] uppercase tracking-widest px-3 py-1.5 rounded-lg border-2 border-[#2B2926] hover:border-[#F55600] transition-all shadow-sm disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {isUploadingProduct ? 'Uploading…' : 'Upload photos'}
                  </button>
                  <input
                    ref={productInputRef}
                    type="file"
                    onChange={handleProductRefUpload}
                    hidden
                    multiple
                    accept="image/png,image/jpeg,image/webp"
                  />
                </div>

                {productReferenceImages.length > 0 ? (
                  <div className="grid grid-cols-4 gap-2">
                    {productReferenceImages.map((url, idx) => (
                      <div key={idx} className="relative aspect-square rounded-lg overflow-hidden border border-slate-200 bg-slate-50 group">
                        <img src={url} alt={`Product reference ${idx + 1}`} className="w-full h-full object-cover" />
                        <button
                          type="button"
                          onClick={() => removeProductRef(idx)}
                          className="absolute top-1 right-1 w-5 h-5 rounded-full bg-white/95 text-slate-700 hover:bg-red-500 hover:text-white shadow flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                          aria-label={`Remove product photo ${idx + 1}`}
                        >
                          <X className="w-3 h-3" />
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-[10px] font-medium text-slate-400">
                    Up to 4 photos of the actual product/service so generated slides feature it accurately (PNG, JPG, WEBP).
                  </p>
                )}
              </div>
            </>
            )}
        </div>
      </div>

      {/* Generate CTA — solid black, white text; on hover it turns brand
          orange (#F55600), text stays white. No shimmer sweep or scale.
          Hidden for read-only Users (content generation is a write action). */}
      {!isMember && (
        <button
          type="button"
          onClick={handleGenerateContent}
          className="w-full bg-[#2B2926] hover:bg-[#F55600] text-white py-4 rounded-3xl font-semibold text-xs uppercase tracking-[0.2em] flex items-center justify-center gap-3 shadow-xl shadow-black/25 transition-colors"
        >
          <Zap className="w-4 h-4 fill-white" />
          <span>Generate Content with AI</span>
        </button>
      )}

      {/* Footer helper */}
      <div className="flex items-center justify-center gap-2 text-[12px] text-[#2B2926] font-semibold">
        <Info className="w-3 h-3 text-[#F55600]" />
        <span>Pipelyt AI can make mistakes — always review generated content.</span>
      </div>

      <AlertModal
        isOpen={showAlert}
        onClose={() => setShowAlert(false)}
        title="Pipelyt"
        message="Upgrade to growth plan to access"
        actionText="Upgrade to Growth"
        onAction={() => {
          if (navigateToSettingsTab) {
            navigateToSettingsTab('billing');
          } else {
            window.location.hash = '#settings';
          }
        }}
      />
    </div>
  );
};

export default CampaignBrief;
