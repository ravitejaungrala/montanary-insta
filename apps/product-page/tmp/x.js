import React, { useState, useRef, useEffect } from "react";
import { Zap, FileText, Image as ImageIcon, X, ChevronDown, Building2, Upload, Info, Check, Ratio, Send, Radio, Linkedin, Instagram, Facebook, Clock, CalendarDays, Palette } from "lucide-react";
import XIcon from "../../../components/icons/XIcon";
import { isReadOnly } from "../../../lib/permissions";
import CustomTimePicker from "../../../components/CustomTimePicker";
import AlertModal from "../../../components/ui/AlertModal";
const Chip = ({ icon: Icon, label, value, isOpen, onToggle, children, minWidth, align = "left" }) => /* @__PURE__ */ React.createElement("div", { className: "relative", style: minWidth ? { minWidth } : void 0 }, /* @__PURE__ */ React.createElement(
  "button",
  {
    type: "button",
    onClick: onToggle,
    className: `w-full flex items-center gap-1 sm:gap-2 pl-2 sm:pl-3 pr-1.5 sm:pr-2.5 py-2 rounded-full border-2 text-[11px] font-bold transition-all shadow-sm
        ${isOpen ? "border-[#F55600] bg-white text-[#F55600] shadow-md shadow-orange-100/20" : "border-[#2B2926]/40 bg-white text-[#2B2926] hover:border-[#F55600] hover:text-[#F55600]"}`
  },
  Icon && /* @__PURE__ */ React.createElement(Icon, { className: "w-3.5 h-3.5 shrink-0" }),
  /* @__PURE__ */ React.createElement("span", { className: "text-[#2B2926] uppercase tracking-wider sm:tracking-widest text-[9px] font-semibold shrink-0" }, label),
  /* @__PURE__ */ React.createElement("span", { className: "flex-1 min-w-0 truncate text-[#2B2926] font-semibold" }, value),
  /* @__PURE__ */ React.createElement(ChevronDown, { className: `w-3.5 h-3.5 shrink-0 text-[#2B2926] transition-transform ${isOpen ? "rotate-180" : ""}` })
), isOpen && // On mobile (2-col grid) the dropdown spans BOTH columns so it never
// overflows the card edge. We anchor `right-0` and use a width of
// `calc(200% + 0.5rem)` (chip-width × 2 + the grid gap) which always
// fits the row whether the chip is in column 1 or column 2.
// `sm:` restores the original chip-anchored behaviour on desktop.
/* @__PURE__ */ React.createElement(
  "div",
  {
    className: `absolute z-30 top-full mt-2 sm:top-auto sm:bottom-full sm:mt-0 sm:mb-3 bg-white rounded-2xl border border-[#2B2926]/20 shadow-[0_12px_32px_rgba(43,41,38,0.16)] animate-in fade-in zoom-in-95 duration-150
          right-0 w-[calc(200%+0.5rem)] max-w-[calc(100vw-32px)]
          sm:w-max sm:min-w-[220px] sm:max-w-[calc(100vw-28px)]
          ${align === "right" ? "sm:right-0 sm:left-auto" : "sm:left-0 sm:right-auto"}`
  },
  children
));
const ChipMenuItem = ({ active, icon: Icon, label, sub, onClick }) => /* @__PURE__ */ React.createElement(
  "button",
  {
    type: "button",
    onClick,
    className: `w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors border-l border-b border-b-[#2B2926]/[0.06] last:border-b-0
      ${active ? "bg-[#F55600]/10 border-l-[#F55600]" : "bg-white border-l-transparent hover:bg-[#F55600]/5 hover:border-l-[#F55600]/20"}`
  },
  Icon && /* @__PURE__ */ React.createElement("div", { className: `w-7 h-7 rounded-lg flex items-center justify-center shrink-0 ${active ? "bg-[#F55600] text-white" : "bg-[#2B2926]/[0.06] text-[#67655E]"}` }, /* @__PURE__ */ React.createElement(Icon, { className: "w-3.5 h-3.5" })),
  /* @__PURE__ */ React.createElement("div", { className: "flex-1 min-w-0" }, /* @__PURE__ */ React.createElement("div", { className: `text-[12px] font-semibold truncate ${active ? "text-[#F55600]" : "text-[#2B2926]"}` }, label), sub && /* @__PURE__ */ React.createElement("div", { className: "text-[10px] text-[#67655E] font-normal truncate mt-0.5" }, sub)),
  active && /* @__PURE__ */ React.createElement(Check, { className: "w-3.5 h-3.5 text-[#F55600] shrink-0" })
);
const CHANNEL_OPTIONS = [
  { id: "linkedin", label: "LinkedIn", icon: ({ className }) => /* @__PURE__ */ React.createElement("img", { src: "/linkedlin.jpg", className: `${className} object-contain`, alt: "LinkedIn" }) },
  { id: "twitter", label: "X", icon: XIcon },
  { id: "instagram", label: "Instagram", icon: ({ className }) => /* @__PURE__ */ React.createElement("img", { src: "/instagram.jpg", className: `${className} object-contain`, alt: "Instagram" }) },
  { id: "facebook", label: "Facebook", icon: ({ className }) => /* @__PURE__ */ React.createElement("img", { src: "/facebook.png", className: `${className} object-contain`, alt: "Facebook" }) },
  { id: "youtube", label: "YouTube", icon: ({ className }) => /* @__PURE__ */ React.createElement("img", { src: "/youtube-icon.png", className: `${className} object-contain`, alt: "YouTube" }) },
  { id: "tiktok", label: "TikTok", icon: ({ className }) => /* @__PURE__ */ React.createElement("svg", { className, viewBox: "0 0 24 24", fill: "#010101", "aria-label": "TikTok" }, /* @__PURE__ */ React.createElement("path", { d: "M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.05-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.62-.93-.01 2.92.01 5.84-.02 8.75-.08 1.4-.54 2.79-1.35 3.94-1.31 1.92-3.58 3.17-5.91 3.21-1.43.08-2.86-.31-4.08-1.03-2.02-1.19-3.44-3.37-3.65-5.71-.02-.5-.03-1-.01-1.49.18-1.9 1.12-3.72 2.58-4.96 1.66-1.44 3.98-2.13 6.15-1.72.02 1.48-.04 2.96-.04 4.44-.99-.32-2.15-.23-3.02.37-.63.41-1.11 1.04-1.36 1.75-.21.51-.15 1.08-.14 1.62.24 1.64 1.82 3.02 3.5 2.87 1.12-.01 2.19-.66 2.77-1.61.19-.33.4-.67.41-1.06.1-1.79.06-3.57.07-5.36.01-4.03-.01-8.05.02-12.07z" })) }
];
const platformAllowedForType = (id, postType) => {
  if (postType === "document") return id === "linkedin";
  if (id === "youtube" || id === "tiktok") return postType === "video";
  if (postType === "text") return id !== "instagram";
  return true;
};
const allowedPlatformIds = (postType) => CHANNEL_OPTIONS.filter((p) => platformAllowedForType(p.id, postType)).map((p) => p.id);
const ASPECT_RATIOS = [
  { value: "auto", label: "Auto", sub: "Defaults to 16:9" },
  { value: "16:9", label: "16:9", sub: "Wide \u2014 LinkedIn / X / FB feed (default)" },
  { value: "1:1", label: "1:1", sub: "Square \u2014 Instagram feed" },
  { value: "9:16", label: "9:16", sub: "Story / Reel vertical" },
  { value: "4:5", label: "4:5", sub: "Portrait feed" },
  { value: "5:4", label: "5:4", sub: "Landscape feed" },
  { value: "3:4", label: "3:4", sub: "Book portrait" },
  { value: "4:3", label: "4:3", sub: "Classic landscape" },
  { value: "3:2", label: "3:2", sub: "Photo landscape" },
  { value: "2:3", label: "2:3", sub: "Photo portrait" },
  { value: "21:9", label: "21:9", sub: "Cinematic" }
];
let _styleCatalogCache = [];
const _AUTO_STYLE_FALLBACK = {
  slug: "auto",
  label: "Auto",
  group: "auto",
  emoji: "\u2728",
  when_to_use: "Let AI pick from your brand DNA and industry"
};
const _STYLE_GROUP_LABELS = {
  auto: "",
  physical_product: "Physical Product",
  service_saas: "Service / SaaS / Content",
  creative: "Creative"
};
const CampaignBrief = ({
  campaignBrief,
  setCampaignBrief,
  // Inline rejection message from the brief guard / refiner (HTTP 422).
  // Rendered directly under the textarea instead of via the global toast
  // so the user reads the reason while looking at the field they edit.
  briefError = "",
  setBriefError = () => {
  },
  // Channels + Post Strategy are still in scope for generation but live in
  // Settings (future). We intentionally do NOT render pickers for them here.
  selectedDashboardPlatforms,
  setSelectedDashboardPlatforms,
  // Agent post type from the parent dropdown (text | image | video | document).
  // When 'document', the Channels picker is filtered to LinkedIn only — the
  // other platforms reject PDFs and letting the user pick them here would
  // lead to silent per-platform failures at publish time.
  postType = "image",
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
  setProductReferenceImages = () => {
  },
  // Post type — used to hide the Style chip for text-only campaigns
  // (no image to style).
  dashboardPostType,
  authAxios,
  user,
  navigateToSettingsTab
}) => {
  const logoInputRef = useRef(null);
  const docsInputRef = useRef(null);
  const productInputRef = useRef(null);
  const chipsRowRef = useRef(null);
  const [isUploadingProduct, setIsUploadingProduct] = useState(false);
  const [openChip, setOpenChip] = useState(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showAlert, setShowAlert] = useState(false);
  const [aspectRatioLocal, setAspectRatioLocal] = useState("16:9");
  const aspectRatio = aspectRatioProp ?? aspectRatioLocal;
  const setAspectRatio = setAspectRatioProp ?? setAspectRatioLocal;
  const [imageStyleLocal, setImageStyleLocal] = useState("auto");
  const imageStyle = imageStyleProp ?? imageStyleLocal;
  const setImageStyle = setImageStyleProp ?? setImageStyleLocal;
  const [styleCatalog, setStyleCatalog] = useState(() => _styleCatalogCache);
  useEffect(() => {
    if (styleCatalog?.length) return;
    if (!authAxios) return;
    let cancelled = false;
    authAxios.get("/config/image-styles").then((res) => {
      const list = res?.data?.styles || [];
      if (cancelled || !list.length) return;
      _styleCatalogCache = list;
      setStyleCatalog(list);
    }).catch((err) => {
      console.warn("[CampaignBrief] failed to load image-styles catalog", err?.response?.status || err?.message);
    });
    return () => {
      cancelled = true;
    };
  }, [authAxios]);
  const styleBySlug = React.useMemo(() => {
    const m = {};
    for (const s of styleCatalog || []) m[s.slug] = s;
    return m;
  }, [styleCatalog]);
  const currentStyle = styleBySlug[imageStyle] || styleBySlug.auto || _AUTO_STYLE_FALLBACK;
  const styleDisplayLabel = currentStyle.label;
  const showStyleChip = dashboardPostType !== "text";
  useEffect(() => {
    if (!openChip) return;
    const onDown = (e) => {
      if (chipsRowRef.current && !chipsRowRef.current.contains(e.target)) {
        setOpenChip(null);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [openChip]);
  useEffect(() => {
    if (selectedProduct === "__none__") {
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
    if (e.target) e.target.value = null;
  };
  const removeDoc = (index) => {
    setContextFiles((prev) => prev.filter((_, i) => i !== index));
  };
  const handleProductRefUpload = async (e) => {
    const files = Array.from(e.target.files || []);
    if (!files.length || !authAxios) return;
    const remainingSlots = Math.max(0, 4 - productReferenceImages.length);
    const toUpload = files.slice(0, remainingSlots);
    if (toUpload.length === 0) {
      if (e.target) e.target.value = null;
      return;
    }
    setIsUploadingProduct(true);
    try {
      const uploaded = [];
      for (const file of toUpload) {
        const fd = new FormData();
        fd.append("file", file);
        const res = await authAxios.post("/upload-image", fd, {
          headers: { "Content-Type": "multipart/form-data" }
        });
        if (res?.data?.url) uploaded.push(res.data.url);
      }
      if (uploaded.length) {
        setProductReferenceImages((prev) => [...prev, ...uploaded].slice(0, 4));
      }
    } catch (err) {
      console.error("product ref upload failed", err);
    } finally {
      setIsUploadingProduct(false);
      if (e.target) e.target.value = null;
    }
  };
  const removeProductRef = (index) => {
    setProductReferenceImages((prev) => prev.filter((_, i) => i !== index));
  };
  const isMember = isReadOnly(user);
  const memberAssignedIds = React.useMemo(() => {
    if (!isMember) return [];
    const list = Array.isArray(user?.assigned_dna_product_ids) ? user.assigned_dna_product_ids.filter(Boolean) : [];
    if (list.length > 0) return list;
    return user?.assigned_dna_product_id ? [user.assigned_dna_product_id] : [];
  }, [isMember, user?.assigned_dna_product_ids, user?.assigned_dna_product_id]);
  const dnaProducts = user?.business_dna?.products || {};
  const hasProducts = Object.keys(dnaProducts).length > 0;
  const companyName = user?.business_dna?.company_name || user?.company_name || "Company Profile";
  const productName = (id) => dnaProducts[id]?.product_name || id;
  const NO_DNA_SENTINEL = "__none__";
  const isNoDna = selectedProduct === NO_DNA_SENTINEL;
  const dnaLabel = isMember ? selectedProduct ? productName(selectedProduct) : memberAssignedIds[0] ? productName(memberAssignedIds[0]) : "Assigned Brand" : isNoDna ? "None" : selectedProduct ? dnaProducts[selectedProduct]?.product_name || selectedProduct : companyName;
  React.useEffect(() => {
    if (!isMember || memberAssignedIds.length === 0) return;
    if (!selectedProduct || !memberAssignedIds.includes(selectedProduct)) {
      setSelectedProduct(memberAssignedIds[0]);
    }
  }, [isMember, memberAssignedIds, selectedProduct, setSelectedProduct]);
  const logoLabel = logoPreview ? "From DNA" : "Not set";
  const ratioLabel = ASPECT_RATIOS.find((r) => r.value === aspectRatio)?.label || aspectRatio;
  const plats = selectedDashboardPlatforms || [];
  const channelsLabel = (() => {
    if (plats.length === 0) return "None";
    if (plats.length === CHANNEL_OPTIONS.length) return `All ${CHANNEL_OPTIONS.length}`;
    const head = CHANNEL_OPTIONS.find((c) => c.id === plats[0])?.label || plats[0];
    return plats.length === 1 ? head : `${head} +${plats.length - 1}`;
  })();
  const isSchedulingRestricted = ["free", "starter"].includes(user?.pricing_plan?.toLowerCase());
  return /* @__PURE__ */ React.createElement("div", { className: "space-y-3 animate-in fade-in slide-in-from-bottom-3 duration-700 pb-4 max-w-6xl mx-auto" }, /* @__PURE__ */ React.createElement("div", { className: "text-center pt-1" }, /* @__PURE__ */ React.createElement("h3", { className: "text-xl font-semibold text-[#2B2926] tracking-tight mb-0.5" }, "Create ", /* @__PURE__ */ React.createElement("span", { className: "text-[#F55600]" }, "New Campaign")), /* @__PURE__ */ React.createElement("p", { className: "text-[11px] text-[#2B2926] font-semibold bg-white inline-block px-4 py-1.5 rounded-full border-2 border-[#10B981] shadow-sm" }, "Define strategy and let Pipelyt AI handle the rest.")), /* @__PURE__ */ React.createElement("div", { className: "bg-white rounded-[28px] border-2 border-[#2B2926]/30 shadow-[0_15px_40px_rgba(255,107,53,0.03)] relative overflow-visible" }, /* @__PURE__ */ React.createElement("div", { className: "absolute top-0 right-0 w-64 h-64 bg-white/40 rounded-full blur-3xl -mr-32 -mt-32 pointer-events-none" }), /* @__PURE__ */ React.createElement("div", { className: "relative p-3 sm:p-4 space-y-3" }, /* @__PURE__ */ React.createElement("div", { className: `rounded-[20px] border-2 ${briefError ? "border-[#F55600] ring-4 ring-[#F55600]/10" : "border-[#2B2926]/30 focus-within:border-[#F55600] focus-within:ring-4 focus-within:ring-[#F55600]/5"} transition-all bg-white shadow-sm overflow-hidden` }, /* @__PURE__ */ React.createElement(
    "textarea",
    {
      value: campaignBrief,
      onChange: (e) => {
        setCampaignBrief(e.target.value);
        if (briefError) setBriefError("");
      },
      placeholder: "Describe the campaign you want to create\u2026\n\ne.g. \u201CAnnounce Pipelyt\u2019s new AI content generation.\u201D",
      className: "block w-full h-[130px] bg-white p-4 text-sm text-[#2B2926] font-bold leading-relaxed outline-none resize-none overflow-y-auto placeholder:text-[#2B2926]/50"
    }
  )), briefError && /* @__PURE__ */ React.createElement(
    "div",
    {
      role: "alert",
      className: "flex items-start gap-2.5 rounded-[14px] border-2 border-[#F55600]/30 bg-[#F55600]/5 px-4 py-3 text-[12px] font-bold text-[#F55600] leading-snug"
    },
    /* @__PURE__ */ React.createElement(Info, { className: "w-4 h-4 mt-0.5 shrink-0" }),
    /* @__PURE__ */ React.createElement("span", { className: "text-[#2B2926]/80" }, briefError)
  ), /* @__PURE__ */ React.createElement("div", { ref: chipsRowRef, className: "grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center sm:gap-2.5" }, isMember && memberAssignedIds.length <= 1 ? /* @__PURE__ */ React.createElement("div", { className: "inline-flex items-center gap-2 bg-white border-2 border-[#F55600]/20 text-[#F55600] rounded-full px-3 py-1.5 text-xs font-semibold cursor-not-allowed select-none shadow-sm" }, /* @__PURE__ */ React.createElement(Building2, { className: "w-3.5 h-3.5" }), /* @__PURE__ */ React.createElement("span", { className: "uppercase tracking-wider text-[9px] opacity-70" }, "Brand (locked)"), /* @__PURE__ */ React.createElement("span", null, dnaLabel)) : isMember ? /* @__PURE__ */ React.createElement(
    Chip,
    {
      icon: Building2,
      label: "Assigned Brand",
      value: dnaLabel,
      isOpen: openChip === "dna",
      onToggle: () => setOpenChip(openChip === "dna" ? null : "dna")
    },
    /* @__PURE__ */ React.createElement("div", { className: "max-h-[280px] overflow-y-auto py-1" }, memberAssignedIds.map((id) => /* @__PURE__ */ React.createElement(
      ChipMenuItem,
      {
        key: id,
        active: selectedProduct === id,
        icon: Building2,
        label: productName(id),
        sub: "Assigned by admin",
        onClick: () => {
          setSelectedProduct(id);
          setOpenChip(null);
        }
      }
    )))
  ) : /* @__PURE__ */ React.createElement(
    Chip,
    {
      icon: Building2,
      label: "Business DNA",
      value: dnaLabel,
      isOpen: openChip === "dna",
      onToggle: () => setOpenChip(openChip === "dna" ? null : "dna")
    },
    /* @__PURE__ */ React.createElement("div", { className: "max-h-[280px] overflow-y-auto py-1" }, /* @__PURE__ */ React.createElement(
      ChipMenuItem,
      {
        active: selectedProduct === null || selectedProduct === void 0,
        icon: Building2,
        label: companyName,
        sub: "Company-wide brand DNA",
        onClick: () => {
          setSelectedProduct(null);
          setOpenChip(null);
        }
      }
    ), /* @__PURE__ */ React.createElement(
      ChipMenuItem,
      {
        active: isNoDna,
        icon: X,
        label: "None",
        sub: "No brand context \u2014 write from brief only",
        onClick: () => {
          setSelectedProduct(NO_DNA_SENTINEL);
          setOpenChip(null);
        }
      }
    ), hasProducts && /* @__PURE__ */ React.createElement("div", { className: "px-4 py-1.5 text-[8px] font-semibold text-slate-400 uppercase tracking-widest border-t border-slate-50 mt-1" }, "Products"), Object.entries(dnaProducts).map(([id, prod]) => /* @__PURE__ */ React.createElement(
      ChipMenuItem,
      {
        key: id,
        active: selectedProduct === id,
        icon: Building2,
        label: prod.product_name || id,
        sub: prod.tagline || prod.url || "",
        onClick: () => {
          setSelectedProduct(id);
          setOpenChip(null);
        }
      }
    )))
  ), /* @__PURE__ */ React.createElement(
    Chip,
    {
      icon: ImageIcon,
      label: "Logo",
      value: logoLabel,
      align: "right",
      isOpen: openChip === "logo",
      onToggle: () => setOpenChip(openChip === "logo" ? null : "logo")
    },
    /* @__PURE__ */ React.createElement("div", { className: "p-3 space-y-3 w-full sm:w-[260px]" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-3" }, /* @__PURE__ */ React.createElement("div", { className: "w-12 h-12 rounded-xl bg-slate-50 border-2 border-slate-100 flex items-center justify-center overflow-hidden shrink-0" }, logoPreview ? /* @__PURE__ */ React.createElement(
      "img",
      {
        src: logoPreview,
        alt: "Logo preview",
        className: "w-full h-full object-contain p-1",
        onError: (e) => {
          e.currentTarget.style.display = "none";
        }
      }
    ) : /* @__PURE__ */ React.createElement(ImageIcon, { className: "w-5 h-5 text-slate-300" })), /* @__PURE__ */ React.createElement("div", { className: "flex-1 min-w-0" }, /* @__PURE__ */ React.createElement("div", { className: "text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest" }, logoPreview ? "Logo active" : "No logo set"), /* @__PURE__ */ React.createElement("div", { className: "text-[10px] text-[#2B2926] font-bold truncate" }, logoPreview ? "Used on generated images" : "Sync from Business DNA"))), /* @__PURE__ */ React.createElement(
      "button",
      {
        type: "button",
        onClick: () => logoInputRef.current?.click(),
        className: "w-full flex items-center justify-center gap-2 py-2 rounded-xl bg-[#F55600] text-white text-[10px] font-semibold uppercase tracking-widest hover:bg-[#F55600] transition-all shadow-md shadow-orange-100"
      },
      /* @__PURE__ */ React.createElement(Upload, { className: "w-3.5 h-3.5" }),
      " Upload custom logo"
    ), logoPreview && /* @__PURE__ */ React.createElement(
      "button",
      {
        type: "button",
        onClick: () => {
          setLogoImage(null);
          setLogoPreview(null);
          setOpenChip(null);
        },
        className: "w-full flex items-center justify-center gap-2 py-1.5 text-[10px] font-semibold text-red-500 uppercase tracking-widest hover:bg-red-50 rounded-xl transition-colors"
      },
      /* @__PURE__ */ React.createElement(X, { className: "w-3 h-3" }),
      " Remove logo"
    ), /* @__PURE__ */ React.createElement(
      "input",
      {
        ref: logoInputRef,
        type: "file",
        onChange: handleLogoUpload,
        hidden: true,
        accept: "image/*"
      }
    ))
  ), /* @__PURE__ */ React.createElement(
    Chip,
    {
      icon: Ratio,
      label: "Aspect Ratio",
      value: ratioLabel,
      isOpen: openChip === "ratio",
      onToggle: () => setOpenChip(openChip === "ratio" ? null : "ratio")
    },
    /* @__PURE__ */ React.createElement("div", { className: "max-h-[200px] overflow-y-auto py-1 w-full sm:w-[240px]" }, ASPECT_RATIOS.map((r) => /* @__PURE__ */ React.createElement(
      ChipMenuItem,
      {
        key: r.value,
        active: aspectRatio === r.value,
        icon: Ratio,
        label: r.label,
        sub: r.sub,
        onClick: () => {
          setAspectRatio(r.value);
          setOpenChip(null);
        }
      }
    )))
  ), showStyleChip && /* @__PURE__ */ React.createElement(
    Chip,
    {
      icon: Palette,
      label: "Style",
      value: styleDisplayLabel,
      isOpen: openChip === "style",
      onToggle: () => setOpenChip(openChip === "style" ? null : "style")
    },
    /* @__PURE__ */ React.createElement("div", { className: "max-h-[380px] overflow-y-auto py-1 w-full sm:w-[280px]" }, (() => {
      const groups = {};
      for (const s of styleCatalog || [_AUTO_STYLE_FALLBACK]) {
        const g = s.group || "auto";
        if (!groups[g]) groups[g] = [];
        groups[g].push(s);
      }
      const order = ["auto", "physical_product", "service_saas", "creative"];
      return order.filter((g) => groups[g]).map((g) => /* @__PURE__ */ React.createElement(React.Fragment, { key: g }, _STYLE_GROUP_LABELS[g] && /* @__PURE__ */ React.createElement("div", { className: "px-4 pt-2 pb-1 text-[9px] font-semibold text-slate-400 uppercase tracking-widest" }, _STYLE_GROUP_LABELS[g]), groups[g].map((s) => /* @__PURE__ */ React.createElement(
        ChipMenuItem,
        {
          key: s.slug,
          active: imageStyle === s.slug,
          label: s.label,
          sub: s.when_to_use,
          onClick: () => {
            setImageStyle(s.slug);
            setOpenChip(null);
          }
        }
      ))));
    })())
  ), /* @__PURE__ */ React.createElement(
    Chip,
    {
      icon: Radio,
      label: "Channels",
      value: channelsLabel,
      align: "right",
      isOpen: openChip === "channels",
      onToggle: () => setOpenChip(openChip === "channels" ? null : "channels")
    },
    /* @__PURE__ */ React.createElement("div", { className: "py-1 w-full sm:w-[240px]" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center justify-between px-4 py-2 border-b border-slate-50" }, /* @__PURE__ */ React.createElement("span", { className: "text-[9px] font-semibold text-slate-400 uppercase tracking-widest" }, "Platforms"), /* @__PURE__ */ React.createElement(
      "button",
      {
        type: "button",
        onClick: () => {
          const all = allowedPlatformIds(postType);
          setSelectedDashboardPlatforms(
            (selectedDashboardPlatforms || []).length === all.length ? [] : all
          );
        },
        className: "text-[9px] font-semibold text-[#F55600] uppercase tracking-widest hover:underline"
      },
      (selectedDashboardPlatforms || []).length === allowedPlatformIds(postType).length ? "Clear all" : "Select all"
    )), postType === "document" && /* @__PURE__ */ React.createElement("div", { className: "px-4 py-2 text-[9px] font-bold text-[#F55600] bg-white border-b border-[#F55600]/10" }, "Document posts are LinkedIn-only \u2014 other platforms hidden."), postType === "text" && /* @__PURE__ */ React.createElement("div", { className: "px-4 py-2 text-[9px] font-bold text-purple-700 bg-white border-b border-purple-100" }, "Instagram hidden \u2014 IG Graph API requires media for every post."), /* @__PURE__ */ React.createElement("div", { className: "max-h-[168px] overflow-y-auto custom-scrollbar" }, CHANNEL_OPTIONS.map((p) => {
      const allowed = platformAllowedForType(p.id, postType);
      const isSelected = allowed && (selectedDashboardPlatforms || []).includes(p.id);
      const videoOnly = p.id === "youtube" || p.id === "tiktok";
      return /* @__PURE__ */ React.createElement(
        "button",
        {
          key: p.id,
          type: "button",
          disabled: !allowed,
          onClick: () => {
            if (!allowed) return;
            setSelectedDashboardPlatforms(
              (prev) => (prev || []).includes(p.id) ? prev.filter((x) => x !== p.id) : [...prev || [], p.id]
            );
          },
          className: `w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors border-l-2
                        ${!allowed ? "bg-white border-transparent opacity-40 cursor-not-allowed" : isSelected ? "bg-[#F55600]/10 border-[#F55600]" : "bg-white border-transparent hover:bg-[#F55600]/5 hover:border-[#F55600]/20"}`,
          title: !allowed ? videoOnly ? "Video posts only" : "Not available for this post type" : void 0
        },
        /* @__PURE__ */ React.createElement("div", { className: `w-3.5 h-3.5 rounded border flex items-center justify-center shrink-0 transition-all
                        ${isSelected ? "bg-[#F55600] border-[#F55600] shadow-sm shadow-slate-200" : "bg-white border-slate-200"}` }, isSelected && /* @__PURE__ */ React.createElement(Check, { className: "w-2 h-2 text-white", strokeWidth: 4 })),
        /* @__PURE__ */ React.createElement(p.icon, { className: `w-3.5 h-3.5 shrink-0 ${isSelected ? "text-[#F55600]" : "text-slate-400"}` }),
        /* @__PURE__ */ React.createElement("span", { className: `text-[10px] font-bold flex-1 ${isSelected ? "text-[#F55600]" : "text-slate-600"}` }, p.label),
        !allowed && videoOnly && /* @__PURE__ */ React.createElement("span", { className: "text-[8px] font-bold text-slate-400 uppercase tracking-wide shrink-0" }, "Video only")
      );
    })))
  ), /* @__PURE__ */ React.createElement(
    Chip,
    {
      icon: Send,
      label: "Strategy",
      value: postStrategy === "schedule" ? "Scheduled" : "Instant",
      isOpen: openChip === "strategy",
      onToggle: () => setOpenChip(openChip === "strategy" ? null : "strategy"),
      align: "right"
    },
    /* @__PURE__ */ React.createElement("div", { className: "w-full sm:w-[280px]" }, /* @__PURE__ */ React.createElement(
      ChipMenuItem,
      {
        active: postStrategy !== "schedule",
        icon: Send,
        label: "Instant",
        sub: "Publish immediately",
        onClick: () => {
          setPostStrategy("publish");
        }
      }
    ), /* @__PURE__ */ React.createElement(
      ChipMenuItem,
      {
        active: postStrategy === "schedule",
        icon: Clock,
        label: isSchedulingRestricted ? "Schedule (Pro)" : "Schedule",
        sub: isSchedulingRestricted ? "Upgrade to Growth" : "Pick a future date and time",
        onClick: () => {
          if (isSchedulingRestricted) {
            setShowAlert(true);
            return;
          }
          setPostStrategy("schedule");
        }
      }
    ), postStrategy === "schedule" && !isSchedulingRestricted && /* @__PURE__ */ React.createElement("div", { className: "border-t border-slate-50 p-3 space-y-3 bg-white animate-in fade-in slide-in-from-top-1 duration-200" }, /* @__PURE__ */ React.createElement("div", { className: "space-y-1" }, /* @__PURE__ */ React.createElement("label", { className: "text-[10px] font-bold text-[#2B2926] uppercase tracking-[0.14em]" }, "Plan Duration"), /* @__PURE__ */ React.createElement("div", { className: "relative" }, /* @__PURE__ */ React.createElement(
      "input",
      {
        type: "number",
        min: "1",
        max: "30",
        value: scheduledDays === void 0 || scheduledDays === null ? "" : scheduledDays,
        onChange: (e) => {
          const v = e.target.value;
          if (v === "") {
            setScheduledDays("");
          } else {
            const n = parseInt(v, 10);
            if (!isNaN(n)) setScheduledDays(n);
          }
        },
        onBlur: (e) => {
          const n = parseInt(e.target.value, 10);
          if (!n || isNaN(n) || n < 1) setScheduledDays(1);
        },
        onFocus: (e) => e.target.select(),
        className: "w-full pl-3 pr-24 py-2 bg-white border-2 border-[#F55600]/30 rounded-xl text-[12px] font-bold text-[#2B2926] outline-none focus:border-[#F55600] focus:ring-2 focus:ring-[#F55600]/15 transition-all shadow-sm [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:m-0 [&::-webkit-inner-spin-button]:m-0"
      }
    ), /* @__PURE__ */ React.createElement("span", { className: "absolute right-3 top-1/2 -translate-y-1/2 text-[10px] font-bold text-[#2B2926]/70 uppercase tracking-[0.12em]" }, (scheduledDays || 1) > 1 ? "Day campaign" : "Single day"))), (scheduledDays || 1) <= 1 && /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("div", { className: "space-y-1" }, /* @__PURE__ */ React.createElement("label", { className: "text-[10px] font-bold text-[#2B2926] uppercase tracking-[0.14em]" }, "Date"), /* @__PURE__ */ React.createElement("div", { className: "relative" }, /* @__PURE__ */ React.createElement(CalendarDays, { className: "absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#F55600] pointer-events-none" }), /* @__PURE__ */ React.createElement(
      "input",
      {
        type: "date",
        value: scheduledDate || "",
        onChange: (e) => setScheduledDate(e.target.value),
        className: "w-full pl-9 pr-3 py-2.5 bg-white border-2 border-[#F55600]/30 rounded-xl text-[12px] font-bold text-[#2B2926] outline-none focus:border-[#F55600] focus:ring-2 focus:ring-[#F55600]/15 transition-all shadow-sm hover:border-[#F55600]/50 cursor-pointer"
      }
    ))), /* @__PURE__ */ React.createElement("div", { className: "space-y-1" }, /* @__PURE__ */ React.createElement("label", { className: "text-[10px] font-bold text-[#2B2926] uppercase tracking-[0.14em]" }, "Time"), /* @__PURE__ */ React.createElement(
      CustomTimePicker,
      {
        selectedTime: scheduledTime,
        onTimeChange: setScheduledTime,
        selectedTimezone,
        onTimezoneChange: setSelectedTimezone
      }
    )))))
  )))), /* @__PURE__ */ React.createElement("div", { className: "bg-white rounded-[24px] border border-[#2B2926]/25 shadow-sm relative overflow-hidden" }, /* @__PURE__ */ React.createElement("div", { className: "p-3 sm:p-4" }, /* @__PURE__ */ React.createElement(
    "button",
    {
      type: "button",
      onClick: () => setShowAdvanced((v) => !v),
      className: "w-full flex items-center gap-2 text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.14em] hover:text-[#F55600] transition-colors"
    },
    /* @__PURE__ */ React.createElement(ChevronDown, { className: `w-3.5 h-3.5 transition-transform ${showAdvanced ? "rotate-180" : ""}` }),
    "Advanced \xB7 Reference documents ",
    contextFiles.length > 0 && `(${contextFiles.length})`
  ), showAdvanced && /* @__PURE__ */ React.createElement(React.Fragment, null, /* @__PURE__ */ React.createElement("div", { className: "mt-3 rounded-2xl border-2 border-[#2B2926]/40 bg-white p-3 space-y-2 animate-in fade-in slide-in-from-top-1 duration-200 shadow-sm" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center justify-between" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-2" }, /* @__PURE__ */ React.createElement(FileText, { className: "w-4 h-4 text-[#F55600]" }), /* @__PURE__ */ React.createElement("span", { className: "text-[10px] font-semibold text-slate-600 uppercase tracking-widest" }, "Strategy Context")), /* @__PURE__ */ React.createElement(
    "button",
    {
      type: "button",
      onClick: () => docsInputRef.current?.click(),
      className: "text-[9px] font-semibold text-white bg-[#2B2926] hover:bg-[#F55600] uppercase tracking-widest px-3 py-1.5 rounded-lg border-2 border-[#2B2926] hover:border-[#F55600] transition-all shadow-sm"
    },
    "Upload docs"
  ), /* @__PURE__ */ React.createElement(
    "input",
    {
      ref: docsInputRef,
      type: "file",
      onChange: handleDocsUpload,
      hidden: true,
      multiple: true,
      accept: ".pdf,.csv,.docx,.txt"
    }
  )), contextFiles.length > 0 ? /* @__PURE__ */ React.createElement("div", { className: "space-y-1.5 max-h-[140px] overflow-y-auto pr-1" }, contextFiles.map((file, idx) => /* @__PURE__ */ React.createElement(
    "div",
    {
      key: idx,
      className: "flex items-center justify-between bg-white px-3 py-1.5 rounded-xl border border-slate-200"
    },
    /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-2 truncate flex-1" }, /* @__PURE__ */ React.createElement(FileText, { className: "w-3 h-3 text-slate-400 shrink-0" }), /* @__PURE__ */ React.createElement("span", { className: "text-[10px] font-bold text-slate-600 truncate" }, file.name)),
    /* @__PURE__ */ React.createElement(
      "button",
      {
        type: "button",
        onClick: () => removeDoc(idx),
        className: "text-slate-300 hover:text-red-500 transition-colors",
        "aria-label": `Remove ${file.name}`
      },
      /* @__PURE__ */ React.createElement(X, { className: "w-3 h-3" })
    )
  ))) : /* @__PURE__ */ React.createElement("p", { className: "text-[10px] font-medium text-slate-400" }, "Attach PDF, CSV, DOCX, or TXT to give the agent deeper context on this campaign.")), /* @__PURE__ */ React.createElement("div", { className: "mt-3 rounded-2xl border-2 border-[#2B2926]/40 bg-white p-3 space-y-2 shadow-sm" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center justify-between" }, /* @__PURE__ */ React.createElement("div", { className: "flex items-center gap-2" }, /* @__PURE__ */ React.createElement(ImageIcon, { className: "w-4 h-4 text-[#F55600]" }), /* @__PURE__ */ React.createElement("span", { className: "text-[10px] font-semibold text-slate-600 uppercase tracking-widest" }, "Product Reference Photos ", productReferenceImages.length > 0 && `(${productReferenceImages.length}/4)`)), /* @__PURE__ */ React.createElement(
    "button",
    {
      type: "button",
      onClick: () => productInputRef.current?.click(),
      disabled: isUploadingProduct || productReferenceImages.length >= 4,
      className: "text-[9px] font-semibold text-white bg-[#2B2926] hover:bg-[#F55600] uppercase tracking-widest px-3 py-1.5 rounded-lg border-2 border-[#2B2926] hover:border-[#F55600] transition-all shadow-sm disabled:opacity-40 disabled:cursor-not-allowed"
    },
    isUploadingProduct ? "Uploading\u2026" : "Upload photos"
  ), /* @__PURE__ */ React.createElement(
    "input",
    {
      ref: productInputRef,
      type: "file",
      onChange: handleProductRefUpload,
      hidden: true,
      multiple: true,
      accept: "image/png,image/jpeg,image/webp"
    }
  )), productReferenceImages.length > 0 ? /* @__PURE__ */ React.createElement("div", { className: "grid grid-cols-4 gap-2" }, productReferenceImages.map((url, idx) => /* @__PURE__ */ React.createElement("div", { key: idx, className: "relative aspect-square rounded-lg overflow-hidden border border-slate-200 bg-slate-50 group" }, /* @__PURE__ */ React.createElement("img", { src: url, alt: `Product reference ${idx + 1}`, className: "w-full h-full object-cover" }), /* @__PURE__ */ React.createElement(
    "button",
    {
      type: "button",
      onClick: () => removeProductRef(idx),
      className: "absolute top-1 right-1 w-5 h-5 rounded-full bg-white/95 text-slate-700 hover:bg-red-500 hover:text-white shadow flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity",
      "aria-label": `Remove product photo ${idx + 1}`
    },
    /* @__PURE__ */ React.createElement(X, { className: "w-3 h-3" })
  )))) : /* @__PURE__ */ React.createElement("p", { className: "text-[10px] font-medium text-slate-400" }, "Up to 4 photos of the actual product/service so generated slides feature it accurately (PNG, JPG, WEBP)."))))), !isMember && /* @__PURE__ */ React.createElement(
    "button",
    {
      type: "button",
      onClick: handleGenerateContent,
      className: "w-full bg-[#2B2926] hover:bg-[#F55600] text-white py-4 rounded-3xl font-semibold text-xs uppercase tracking-[0.2em] flex items-center justify-center gap-3 shadow-xl shadow-black/25 transition-colors"
    },
    /* @__PURE__ */ React.createElement(Zap, { className: "w-4 h-4 fill-white" }),
    /* @__PURE__ */ React.createElement("span", null, "Generate Content with AI")
  ), /* @__PURE__ */ React.createElement("div", { className: "flex items-center justify-center gap-2 text-[12px] text-[#2B2926] font-semibold" }, /* @__PURE__ */ React.createElement(Info, { className: "w-3 h-3 text-[#F55600]" }), /* @__PURE__ */ React.createElement("span", null, "Pipelyt AI can make mistakes \u2014 always review generated content.")), /* @__PURE__ */ React.createElement(
    AlertModal,
    {
      isOpen: showAlert,
      onClose: () => setShowAlert(false),
      title: "Pipelyt",
      message: "Upgrade to growth plan to access",
      actionText: "Upgrade to Growth",
      onAction: () => {
        if (navigateToSettingsTab) {
          navigateToSettingsTab("billing");
        } else {
          window.location.hash = "#settings";
        }
      }
    }
  ));
};
export default CampaignBrief;
