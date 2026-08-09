import React, { useState } from 'react';
import { createPortal } from 'react-dom';
import { Zap, Search, Eye, X, Target, TrendingUp, HelpCircle, Building2, Globe, ListChecks, ShieldAlert, ExternalLink, ChevronDown, CalendarDays, Hash, Sparkles, Newspaper } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

// Coerce any AI-emitted value to a string for safe rendering.
const toText = (val) => {
  if (val == null) return '';
  if (typeof val === 'string') return val;
  if (typeof val === 'object') {
    const brand = val.brand_name || val.company_name || val.product_name || '';
    const desc = val.value_proposition || val.analysis || val.description || '';
    if (brand || desc) return `${brand} ${desc}`.trim();
    const firstVal = Object.values(val).find(v => typeof v === 'string');
    return firstVal || JSON.stringify(val);
  }
  return String(val);
};

// Render a paragraph that may contain [src:N] markers from the grounded
// researcher. Each marker becomes a small superscript pill that scrolls
// to the matching entry in the Sources panel.
const RichText = ({ text, sources = [] }) => {
  const str = toText(text);
  if (!str) return <span className="text-[#2B2926]">Awaiting AI insights...</span>;

  // Split on [src:1], [src:2,4], etc. — keep delimiters as captures.
  const parts = str.split(/(\[src:\s*[\d,\s]+\])/g);
  if (parts.length === 1) return <>{str}</>;

  return (
    <>
      {parts.map((part, i) => {
        const match = /^\[src:\s*([\d,\s]+)\]$/.exec(part);
        if (!match) return <React.Fragment key={i}>{part}</React.Fragment>;
        const ids = match[1].split(',').map(s => parseInt(s.trim(), 10)).filter(Boolean);
        return (
          <span key={i} className="inline-flex items-center gap-0.5 align-super">
            {ids.map((id, j) => {
              const src = sources.find(s => Number(s.id) === id);
              const onClick = () => {
                const el = document.getElementById(`src-${id}`);
                if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
              };
              return (
                <button
                  key={`${i}-${j}`}
                  onClick={onClick}
                  title={src ? `${src.title || src.url}${src.published ? ` (${src.published})` : ''}` : `Source ${id}`}
                  className="text-[9px] font-semibold text-[#F55600] bg-[#F55600]/10 hover:bg-[#F55600]/20 px-1.5 py-0.5 rounded-full transition-colors"
                >
                  {id}
                </button>
              );
            })}
          </span>
        );
      })}
    </>
  );
};

// Compact label chip with three tones matching the researcher's
// grounding_confidence outcome.
const GroundingBadge = ({ confidence }) => {
  if (!confidence) return null;
  const c = String(confidence).toLowerCase();
  const tone =
    c === 'grounded' ? 'bg-[#10B981]/10 text-[#10B981] border-[#10B981]/30'
    : c === 'partial' ? 'bg-[#F55600]/10 text-[#F55600] border-[#F55600]/30'
    : 'bg-[#2B2926]/5 text-[#2B2926]/60 border-[#2B2926]/10';
  return (
    <span className={`text-[9px] font-semibold uppercase tracking-[0.18em] px-2.5 py-1 rounded-full border ${tone}`}>
      {c}
    </span>
  );
};

// Domain extractor for the Source cards.
const hostnameOf = (url) => {
  try { return new URL(url).hostname.replace(/^www\./, ''); }
  catch { return url; }
};

// ─────────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────────

const StrategicBlueprint = ({ researchReport, culturalCalendar }) => {
  const [showModal, setShowModal] = useState(false);
  const [showQueries, setShowQueries] = useState(false);

  if (!researchReport) return null;

  // Cultural calendar — pre-fetched once per day. May be null on first
  // request before the cache is warm, or if the lookup failed.
  const cal = culturalCalendar && !culturalCalendar.error ? culturalCalendar : null;
  const calHasEntries = cal && (
    (cal.india?.today?.length || 0) +
    (cal.india?.tomorrow?.length || 0) +
    (cal.usa?.today?.length || 0) +
    (cal.usa?.tomorrow?.length || 0)
  ) > 0;

  // Prefer the LLM-emitted sources[] (richer: id/url/title/published/publisher).
  // Fall back to Gemini's raw grounding metadata when the model didn't echo them.
  const grounding = researchReport._grounding || {};
  const llmSources = Array.isArray(researchReport.sources) ? researchReport.sources : [];
  const metaSources = Array.isArray(grounding.sources) ? grounding.sources : [];
  const sources = llmSources.length > 0
    ? llmSources
    : metaSources.map((s, i) => ({ id: i + 1, url: s.uri, title: s.title }));
  const webQueries = Array.isArray(grounding.queries) ? grounding.queries : [];
  const searchEntryHtml = grounding.search_entry_point_html || null;
  const isGrounded = sources.length > 0 || webQueries.length > 0;

  const angles = Array.isArray(researchReport.angles_to_test) ? researchReport.angles_to_test : [];
  const doNotClaim = Array.isArray(researchReport.do_not_claim) ? researchReport.do_not_claim : [];
  const festivalAlerts = Array.isArray(researchReport.festival_alerts) ? researchReport.festival_alerts : [];
  const trendingTopics = Array.isArray(researchReport.trending_topics) ? researchReport.trending_topics : [];
  const trendingHashtags = Array.isArray(researchReport.trending_hashtags) ? researchReport.trending_hashtags : [];
  const trendingKeywords = Array.isArray(researchReport.trending_keywords) ? researchReport.trending_keywords : [];
  const competitorNews = Array.isArray(researchReport.competitor_news) ? researchReport.competitor_news : [];

  const ModalContent = (
    <AnimatePresence>
      {showModal && (
        <div className="fixed inset-0 z-[9999] flex items-center justify-center p-2 sm:p-4 md:p-6 overflow-hidden">
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setShowModal(false)}
            className="absolute inset-0"
          />

          {/* Modal Card */}
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 30 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 30 }}
            className="relative bg-white rounded-2xl sm:rounded-[20px] md:rounded-[24px] w-full max-w-5xl max-h-[92vh] sm:max-h-[90vh] overflow-hidden shadow-2xl flex flex-col border border-[#2B2926]/30"
          >
            {/* Modal Header */}
            <div className="px-4 sm:px-5 md:px-6 py-3.5 sm:py-4 md:py-5 border-b border-slate-50 flex items-start sm:items-center justify-between gap-3 bg-slate-50/50">
              <div className="flex items-center gap-2.5 sm:gap-3 md:gap-4 min-w-0 flex-1">
                <div className="w-9 h-9 sm:w-10 sm:h-10 md:w-11 md:h-11 rounded-xl sm:rounded-xl md:rounded-2xl bg-[#F55600] flex items-center justify-center text-white shadow-lg shadow-slate-200 flex-shrink-0">
                  <Zap className="w-4 h-4 sm:w-5 sm:h-5 fill-white" />
                </div>
                <div>
                  <div className="flex flex-wrap items-center gap-1.5 sm:gap-2.5 min-w-0">
                    <h2 className="text-[15px] sm:text-base md:text-xl font-semibold text-[#2B2926] tracking-tight leading-tight">AI Deep-Research Blueprint</h2>
                    <GroundingBadge confidence={researchReport.grounding_confidence} />
                    {isGrounded && (
                      <span className="text-[9px] font-semibold uppercase tracking-[0.18em] px-2.5 py-1 rounded-full border bg-[#F55600]/10 text-[#F55600] border-[#F55600]/30 flex items-center gap-1">
                        <Globe className="w-3 h-3" /> Web Grounded
                      </span>
                    )}
                  </div>
                  <p className="text-[9px] sm:text-[10px] text-[#2B2926] font-bold uppercase tracking-[0.14em] sm:tracking-[0.2em] mt-1 leading-tight">Strategic Marketing Intelligence</p>
                </div>
              </div>
              <button
                onClick={() => setShowModal(false)}
                className="p-2 sm:p-2.5 md:p-3 bg-white hover:bg-slate-100 text-[#2B2926] hover:text-[#2B2926] rounded-xl sm:rounded-2xl border border-[#2B2926]/30 transition-all shadow-sm flex-shrink-0"
              >
                <X className="w-4 h-4 sm:w-5 sm:h-5" />
              </button>
            </div>

            {/* Modal Body */}
            <div className="flex-1 overflow-y-auto p-3 sm:p-4 md:p-5 space-y-3 sm:space-y-3 md:space-y-3.5 bg-white">

              {/* Row 0: Festival Alert banner — fires only when a major
                  nation-wide festival/holiday is today/tomorrow and the
                  user did not mention it. Pinned at the top so the user
                  sees "today is X — I forgot" before anything else. */}
              {festivalAlerts.length > 0 && (
                <motion.div
                  initial={{ opacity: 0, y: -10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="bg-[#F55600]/5 p-3.5 sm:p-4 md:p-4 rounded-2xl border-2 border-[#F55600]/30 shadow-sm"
                >
                  <div className="flex items-start gap-4 mb-3">
                    <div className="w-12 h-12 rounded-2xl bg-[#F55600] flex-shrink-0 flex items-center justify-center text-white shadow-lg">
                      <CalendarDays className="w-4 h-4 md:w-5 md:h-5" />
                    </div>
                    <div className="flex-1">
                      <h4 className="text-[12px] font-semibold text-[#F55600] uppercase tracking-widest mb-1">
                        Festival Alert · You may have forgotten
                      </h4>
                      <p className="text-[13px] font-bold text-[#2B2926]/80 leading-snug">
                        {festivalAlerts.length === 1
                          ? "Your brief didn't mention this — consider posting a festival variant alongside your campaign."
                          : `${festivalAlerts.length} major festivals are happening — your brief didn't mention them. Consider posting festival variants alongside your campaign.`}
                      </p>
                    </div>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5 ml-0 sm:ml-16">
                    {festivalAlerts.map((alert, i) => (
                      <div
                        key={i}
                        className="bg-white p-3.5 rounded-2xl border border-[#F55600]/20"
                      >
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-[14px] font-semibold text-[#F55600]">{alert.festival_name}</span>
                          <span className="text-[9px] font-semibold uppercase tracking-widest px-2 py-0.5 rounded-full bg-[#F55600]/10 text-[#F55600]">
                            {alert.country?.toUpperCase()}
                          </span>
                          <span className="text-[9px] font-semibold uppercase tracking-widest text-[#2B2926]/40">
                            {alert.when}{alert.date ? ` · ${alert.date}` : ''}
                          </span>
                        </div>
                        {alert.suggested_angle && (
                          <p className="text-[11px] font-bold text-[#2B2926]/70 leading-snug">
                            {alert.suggested_angle}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                </motion.div>
              )}

              {/* Row 1: Brand & Product Positioning */}
              <motion.div
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.05 }}
                className="bg-white p-3 sm:p-4 md:p-4 rounded-2xl border border-[#2B2926]/30 shadow-sm flex items-start gap-3 sm:gap-3.5 md:gap-4 hover:shadow-md transition-shadow"
              >
                <div className="w-9 h-9 sm:w-10 sm:h-10 md:w-10 md:h-10 rounded-xl bg-[#2B2926] flex-shrink-0 flex items-center justify-center text-white shadow-[0_2px_6px_rgba(43,41,38,0.2)]">
                  <Building2 className="w-4 h-4 md:w-5 md:h-5" />
                </div>
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-2">
                    <h4 className="text-[11px] font-bold text-[#F55600] uppercase tracking-[0.14em]">Brand &amp; Product Positioning</h4>
                    <div className="h-px flex-1 bg-slate-50"></div>
                  </div>
                  <p className="text-[13px] text-[#2B2926] leading-relaxed font-bold">
                    <RichText text={researchReport.company_product_analysis} sources={sources} />
                  </p>
                </div>
              </motion.div>

              {/* Row 2: Target Audience */}
              <motion.div
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.1 }}
                className="bg-white p-3 sm:p-4 md:p-4 rounded-2xl border border-[#2B2926]/30 shadow-sm flex items-start gap-3 sm:gap-3.5 md:gap-4 hover:shadow-md transition-shadow"
              >
                <div className="w-9 h-9 sm:w-10 sm:h-10 md:w-10 md:h-10 rounded-xl bg-[#2B2926] flex-shrink-0 flex items-center justify-center text-white shadow-[0_2px_6px_rgba(43,41,38,0.2)]">
                  <Target className="w-4 h-4 md:w-5 md:h-5" />
                </div>
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-2">
                    <h4 className="text-[11px] font-bold text-[#F55600] uppercase tracking-[0.14em]">Target Audience Definition</h4>
                    <div className="h-px flex-1 bg-slate-50"></div>
                  </div>
                  <p className="text-[13px] text-[#2B2926] leading-relaxed font-bold">
                    <RichText text={researchReport.target_audience} sources={sources} />
                  </p>
                </div>
              </motion.div>

              {/* Row 3: Trending Context */}
              <motion.div
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.15 }}
                className="bg-white p-3 sm:p-4 md:p-4 rounded-2xl border border-[#2B2926]/30 shadow-sm flex items-start gap-3 sm:gap-3.5 md:gap-4 hover:shadow-md transition-shadow"
              >
                <div className="w-9 h-9 sm:w-10 sm:h-10 md:w-10 md:h-10 rounded-xl bg-[#2B2926] flex-shrink-0 flex items-center justify-center text-white shadow-[0_2px_6px_rgba(43,41,38,0.2)]">
                  <TrendingUp className="w-4 h-4 md:w-5 md:h-5" />
                </div>
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-2">
                    <h4 className="text-[11px] font-bold text-[#F55600] uppercase tracking-[0.14em]">Trending Market Context</h4>
                    <div className="h-px flex-1 bg-slate-50"></div>
                  </div>
                  <p className="text-[13px] text-[#2B2926] leading-relaxed font-bold">
                    <RichText text={researchReport.trending_context} sources={sources} />
                  </p>
                </div>
              </motion.div>

              {/* Row 4: Problem Solving */}
              <motion.div
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.2 }}
                className="bg-white p-3 sm:p-4 md:p-4 rounded-2xl border border-[#2B2926]/30 shadow-sm flex items-start gap-3 sm:gap-3.5 md:gap-4 hover:shadow-md transition-shadow"
              >
                <div className="w-9 h-9 sm:w-10 sm:h-10 md:w-10 md:h-10 rounded-xl bg-[#10B981] flex-shrink-0 flex items-center justify-center text-white shadow-[0_2px_6px_rgba(16,185,129,0.25)]">
                  <HelpCircle className="w-4 h-4 md:w-5 md:h-5" />
                </div>
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-2">
                    <h4 className="text-[11px] font-bold text-[#F55600] uppercase tracking-[0.14em]">Problem-Solving Opportunities</h4>
                    <div className="h-px flex-1 bg-slate-50"></div>
                  </div>
                  <p className="text-[13px] text-[#2B2926] leading-relaxed font-bold">
                    <RichText text={researchReport.problem_solving_opportunity} sources={sources} />
                  </p>
                </div>
              </motion.div>

              {/* Row 5: Strategic Angles to Test */}
              {angles.length > 0 && (
                <motion.div
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.25 }}
                  className="bg-white p-3 sm:p-4 md:p-4 rounded-2xl border border-[#2B2926]/30 shadow-sm flex items-start gap-3 sm:gap-3.5 md:gap-4 hover:shadow-md transition-shadow"
                >
                  <div className="w-9 h-9 sm:w-10 sm:h-10 md:w-10 md:h-10 rounded-xl bg-[#2B2926] flex-shrink-0 flex items-center justify-center text-white shadow-[0_2px_6px_rgba(43,41,38,0.2)]">
                    <ListChecks className="w-4 h-4 md:w-5 md:h-5" />
                  </div>
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-3">
                      <h4 className="text-[11px] font-bold text-[#F55600] uppercase tracking-[0.14em]">Strategic Angles to Test</h4>
                      <div className="h-px flex-1 bg-slate-50"></div>
                    </div>
                    <ol className="space-y-2.5">
                      {angles.map((angle, i) => (
                        <li key={i} className="flex items-start gap-3">
                          <span className="flex-shrink-0 w-6 h-6 rounded-full bg-[#F55600] text-white text-[10px] font-semibold flex items-center justify-center">
                            {i + 1}
                          </span>
                          <p className="text-[13px] text-[#2B2926] leading-relaxed font-bold flex-1">
                            <RichText text={angle} sources={sources} />
                          </p>
                        </li>
                      ))}
                    </ol>
                  </div>
                </motion.div>
              )}

              {/* Row 5a: Trending Topics (broader industry topics, always-on aux) */}
              {trendingTopics.length > 0 && (
                <motion.div
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.26 }}
                  className="bg-white p-3 sm:p-4 md:p-4 rounded-2xl border border-[#2B2926]/30 shadow-sm flex items-start gap-3 sm:gap-3.5 md:gap-4 hover:shadow-md transition-shadow"
                >
                  <div className="w-9 h-9 sm:w-10 sm:h-10 md:w-10 md:h-10 rounded-xl bg-[#2B2926] flex-shrink-0 flex items-center justify-center text-white shadow-[0_2px_6px_rgba(43,41,38,0.2)]">
                    <TrendingUp className="w-4 h-4 md:w-5 md:h-5" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-3">
                      <h4 className="text-[11px] font-bold text-[#F55600] uppercase tracking-[0.14em]">
                        Trending Topics <span className="text-[#2B2926]/40">· {trendingTopics.length}</span>
                      </h4>
                      <span className="text-[9px] font-semibold uppercase tracking-widest text-[#2B2926]/40">Industry-Wide · Always Researched</span>
                      <div className="h-px flex-1 bg-slate-50"></div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {trendingTopics.map((t, i) => (
                        <span
                          key={i}
                          className="text-[12px] font-bold px-3 py-1.5 rounded-full bg-[#F55600]/5 text-[#2B2926]/80 border border-[#F55600]/20"
                        >
                          {t}
                        </span>
                      ))}
                    </div>
                  </div>
                </motion.div>
              )}

              {/* Row 5b: Trending Hashtags */}
              {trendingHashtags.length > 0 && (
                <motion.div
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.27 }}
                  className="bg-white p-3 sm:p-4 md:p-4 rounded-2xl border border-[#2B2926]/30 shadow-sm flex items-start gap-3 sm:gap-3.5 md:gap-4 hover:shadow-md transition-shadow"
                >
                  <div className="w-9 h-9 sm:w-10 sm:h-10 md:w-10 md:h-10 rounded-xl bg-[#2B2926] flex-shrink-0 flex items-center justify-center text-white shadow-[0_2px_6px_rgba(43,41,38,0.2)]">
                    <Hash className="w-4 h-4 md:w-5 md:h-5" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-3">
                      <h4 className="text-[11px] font-bold text-[#F55600] uppercase tracking-[0.14em]">
                        Trending Hashtags <span className="text-[#2B2926]/40">· {trendingHashtags.length}</span>
                      </h4>
                      <div className="h-px flex-1 bg-slate-50"></div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {trendingHashtags.map((tag, i) => (
                        <span
                          key={i}
                          className="text-[12px] font-semibold px-3 py-1.5 rounded-full bg-[#F55600]/10 text-[#F55600] border border-[#F55600]/20"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  </div>
                </motion.div>
              )}

              {/* Row 5c: Trending Keywords */}
              {trendingKeywords.length > 0 && (
                <motion.div
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.28 }}
                  className="bg-white p-3 sm:p-4 md:p-4 rounded-2xl border border-[#2B2926]/30 shadow-sm flex items-start gap-3 sm:gap-3.5 md:gap-4 hover:shadow-md transition-shadow"
                >
                  <div className="w-9 h-9 sm:w-10 sm:h-10 md:w-10 md:h-10 rounded-xl bg-[#2B2926] flex-shrink-0 flex items-center justify-center text-white shadow-[0_2px_6px_rgba(43,41,38,0.2)]">
                    <Sparkles className="w-4 h-4 md:w-5 md:h-5" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-3">
                      <h4 className="text-[11px] font-bold text-[#F55600] uppercase tracking-[0.14em]">
                        Trending Keywords <span className="text-[#2B2926]/40">· {trendingKeywords.length}</span>
                      </h4>
                      <div className="h-px flex-1 bg-slate-50"></div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {trendingKeywords.map((kw, i) => (
                        <span
                          key={i}
                          className="text-[12px] font-bold px-3 py-1.5 rounded-full bg-[#2B2926]/5 text-[#2B2926]/80 border border-[#2B2926]/10"
                        >
                          {kw}
                        </span>
                      ))}
                    </div>
                  </div>
                </motion.div>
              )}

              {/* Row 5d: Competitor News */}
              {competitorNews.length > 0 && (
                <motion.div
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.29 }}
                  className="bg-white p-3 sm:p-4 md:p-4 rounded-2xl border border-[#2B2926]/30 shadow-sm flex items-start gap-3 sm:gap-3.5 md:gap-4 hover:shadow-md transition-shadow"
                >
                  <div className="w-9 h-9 sm:w-10 sm:h-10 md:w-10 md:h-10 rounded-xl bg-[#2B2926] flex-shrink-0 flex items-center justify-center text-white shadow-[0_2px_6px_rgba(43,41,38,0.2)]">
                    <Newspaper className="w-4 h-4 md:w-5 md:h-5" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-3">
                      <h4 className="text-[11px] font-bold text-[#F55600] uppercase tracking-[0.14em]">
                        Competitor News <span className="text-[#2B2926]/40">· {competitorNews.length}</span>
                      </h4>
                      <div className="h-px flex-1 bg-slate-50"></div>
                    </div>
                    <ul className="space-y-2">
                      {competitorNews.map((item, i) => {
                        const src = item.src ? sources.find(s => Number(s.id) === Number(item.src)) : null;
                        return (
                          <li key={i} className="flex items-start gap-3">
                            <span className="flex-shrink-0 text-[10px] font-semibold uppercase tracking-widest text-[#F55600] mt-1">
                              {item.competitor || '?'}
                            </span>
                            <div className="flex-1 min-w-0">
                              <p className="text-[12px] text-[#2B2926] leading-snug font-bold">
                                {item.headline}
                                {item.src && (
                                  <button
                                    onClick={() => {
                                      const el = document.getElementById(`src-${item.src}`);
                                      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                    }}
                                    className="ml-1 text-[9px] font-semibold text-[#F55600] bg-[#F55600]/10 hover:bg-[#F55600]/20 px-1.5 py-0.5 rounded-full align-super"
                                  >
                                    {item.src}
                                  </button>
                                )}
                              </p>
                              {item.published && (
                                <p className="text-[10px] font-bold text-[#2B2926]/40 mt-0.5">{item.published}{src?.publisher ? ` · ${src.publisher}` : ''}</p>
                              )}
                            </div>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                </motion.div>
              )}

              {/* Row 6: Do Not Claim */}
              {doNotClaim.length > 0 && (
                <motion.div
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.3 }}
                  className="bg-white p-3 sm:p-4 md:p-4 rounded-2xl border border-[#2B2926]/10 shadow-sm flex items-start gap-3 sm:gap-3.5 md:gap-4 hover:shadow-md transition-shadow"
                >
                  <div className="w-9 h-9 sm:w-10 sm:h-10 md:w-10 md:h-10 rounded-xl bg-[#2B2926] flex-shrink-0 flex items-center justify-center text-white shadow-[0_2px_6px_rgba(43,41,38,0.2)]">
                    <ShieldAlert className="w-4 h-4 md:w-5 md:h-5" />
                  </div>
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-3">
                      <h4 className="text-[11px] font-bold text-[#F55600] uppercase tracking-[0.14em]">Do Not Claim</h4>
                      <div className="h-px flex-1 bg-slate-50"></div>
                    </div>
                    <ul className="space-y-1.5">
                      {doNotClaim.map((item, i) => (
                        <li key={i} className="flex items-start gap-2 text-[12px] text-[#2B2926]/70 font-bold">
                          <span className="text-[#2B2926]/30 mt-1">·</span>
                          <RichText text={item} sources={sources} />
                        </li>
                      ))}
                    </ul>
                  </div>
                </motion.div>
              )}

              {/* Row 6b: Cultural Calendar (today + tomorrow, IN + US) */}
              {calHasEntries && (
                <motion.div
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.32 }}
                  className="bg-white p-3 sm:p-4 md:p-4 rounded-2xl border border-[#2B2926]/30 shadow-sm flex items-start gap-3 sm:gap-3.5 md:gap-4 hover:shadow-md transition-shadow"
                >
                  <div className="w-9 h-9 sm:w-10 sm:h-10 md:w-10 md:h-10 rounded-xl bg-[#2B2926] flex-shrink-0 flex items-center justify-center text-white shadow-[0_2px_6px_rgba(43,41,38,0.2)]">
                    <CalendarDays className="w-4 h-4 md:w-5 md:h-5" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-3">
                      <h4 className="text-[11px] font-bold text-[#F55600] uppercase tracking-[0.14em]">Cultural Calendar</h4>
                      <span className="text-[9px] font-semibold uppercase tracking-widest text-[#2B2926]/40">Today + Tomorrow · India &amp; USA</span>
                      <div className="h-px flex-1 bg-slate-50"></div>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      {/* Today */}
                      <div>
                        <div className="text-[10px] font-semibold uppercase tracking-widest text-[#2B2926]/60 mb-2">
                          Today · {cal.today_date}
                        </div>
                        <div className="space-y-2">
                          <div>
                            <div className="text-[9px] font-semibold uppercase tracking-widest text-[#F55600] mb-1">India</div>
                            {(cal.india?.today?.length || 0) === 0 ? (
                              <p className="text-[11px] font-bold text-[#2B2926]/40">No notable observance</p>
                            ) : (
                              <ul className="space-y-1">
                                {cal.india.today.map((e, i) => (
                                  <li key={i} className="text-[12px] font-bold text-[#2B2926] leading-snug">
                                    <span className="text-[#F55600]">{e.name}</span>
                                    {e.type && <span className="text-[#2B2926]/40"> · {e.type}</span>}
                                    {e.note && <div className="text-[11px] font-medium text-[#2B2926]/60 mt-0.5">{e.note}</div>}
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>
                          <div>
                            <div className="text-[9px] font-semibold uppercase tracking-widest text-[#F55600] mb-1">USA</div>
                            {(cal.usa?.today?.length || 0) === 0 ? (
                              <p className="text-[11px] font-bold text-[#2B2926]/40">No notable observance</p>
                            ) : (
                              <ul className="space-y-1">
                                {cal.usa.today.map((e, i) => (
                                  <li key={i} className="text-[12px] font-bold text-[#2B2926] leading-snug">
                                    <span className="text-[#F55600]">{e.name}</span>
                                    {e.type && <span className="text-[#2B2926]/40"> · {e.type}</span>}
                                    {e.note && <div className="text-[11px] font-medium text-[#2B2926]/60 mt-0.5">{e.note}</div>}
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>
                        </div>
                      </div>
                      {/* Tomorrow */}
                      <div>
                        <div className="text-[10px] font-semibold uppercase tracking-widest text-[#2B2926]/60 mb-2">
                          Tomorrow · {cal.tomorrow_date}
                        </div>
                        <div className="space-y-2">
                          <div>
                            <div className="text-[9px] font-semibold uppercase tracking-widest text-[#F55600] mb-1">India</div>
                            {(cal.india?.tomorrow?.length || 0) === 0 ? (
                              <p className="text-[11px] font-bold text-[#2B2926]/40">No notable observance</p>
                            ) : (
                              <ul className="space-y-1">
                                {cal.india.tomorrow.map((e, i) => (
                                  <li key={i} className="text-[12px] font-bold text-[#2B2926] leading-snug">
                                    <span className="text-[#F55600]">{e.name}</span>
                                    {e.type && <span className="text-[#2B2926]/40"> · {e.type}</span>}
                                    {e.note && <div className="text-[11px] font-medium text-[#2B2926]/60 mt-0.5">{e.note}</div>}
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>
                          <div>
                            <div className="text-[9px] font-semibold uppercase tracking-widest text-[#F55600] mb-1">USA</div>
                            {(cal.usa?.tomorrow?.length || 0) === 0 ? (
                              <p className="text-[11px] font-bold text-[#2B2926]/40">No notable observance</p>
                            ) : (
                              <ul className="space-y-1">
                                {cal.usa.tomorrow.map((e, i) => (
                                  <li key={i} className="text-[12px] font-bold text-[#2B2926] leading-snug">
                                    <span className="text-[#F55600]">{e.name}</span>
                                    {e.type && <span className="text-[#2B2926]/40"> · {e.type}</span>}
                                    {e.note && <div className="text-[11px] font-medium text-[#2B2926]/60 mt-0.5">{e.note}</div>}
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </motion.div>
              )}

              {/* Row 7: Sources */}
              {sources.length > 0 && (
                <motion.div
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.35 }}
                  className="bg-white p-3 sm:p-4 md:p-4 rounded-2xl border border-[#2B2926]/30 shadow-sm flex items-start gap-3 sm:gap-3.5 md:gap-4 hover:shadow-md transition-shadow"
                >
                  <div className="w-9 h-9 sm:w-10 sm:h-10 md:w-10 md:h-10 rounded-xl bg-[#2B2926] flex-shrink-0 flex items-center justify-center text-white shadow-[0_2px_6px_rgba(43,41,38,0.2)]">
                    <Globe className="w-4 h-4 md:w-5 md:h-5" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-3">
                      <h4 className="text-[11px] font-bold text-[#F55600] uppercase tracking-[0.14em]">
                        Sources <span className="text-[#2B2926]/40">· {sources.length}</span>
                      </h4>
                      <div className="h-px flex-1 bg-slate-50"></div>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                      {sources.map((s) => (
                        <a
                          key={s.id}
                          id={`src-${s.id}`}
                          href={s.url}
                          target="_blank"
                          rel="noreferrer noopener"
                          className="group flex items-start gap-3 p-3 rounded-2xl border border-[#2B2926]/30 hover:border-[#F55600]/30 hover:bg-[#F55600]/[0.02] transition-all"
                        >
                          <span className="flex-shrink-0 w-7 h-7 rounded-full bg-[#F55600]/10 text-[#F55600] text-[10px] font-semibold flex items-center justify-center">
                            {s.id}
                          </span>
                          <div className="flex-1 min-w-0">
                            <p className="text-[12px] font-semibold text-[#2B2926] leading-snug truncate group-hover:text-[#F55600] transition-colors">
                              {s.title || s.url}
                            </p>
                            <div className="flex items-center gap-1.5 text-[10px] font-bold text-[#2B2926]/40 mt-0.5">
                              <span className="truncate">{s.publisher || hostnameOf(s.url)}</span>
                              {s.published && s.published !== 'unknown' && (
                                <>
                                  <span>·</span>
                                  <span>{s.published}</span>
                                </>
                              )}
                            </div>
                          </div>
                          <ExternalLink className="w-3.5 h-3.5 text-[#2B2926] group-hover:text-[#F55600] flex-shrink-0 mt-1 transition-colors" />
                        </a>
                      ))}
                    </div>
                  </div>
                </motion.div>
              )}

              {/* Row 8: Web Searches Run (collapsed by default) */}
              {webQueries.length > 0 && (
                <motion.div
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.4 }}
                  className="bg-white p-3 sm:p-4 md:p-4 rounded-2xl border border-[#2B2926]/30 shadow-sm"
                >
                  <button
                    onClick={() => setShowQueries(v => !v)}
                    className="w-full flex items-center gap-3 text-left"
                  >
                    <div className="w-10 h-10 rounded-2xl bg-white border-2 border-[#2B2926]/10 flex-shrink-0 flex items-center justify-center text-[#2B2926]/60">
                      <Search className="w-5 h-5" />
                    </div>
                    <div className="flex-1">
                      <h4 className="text-[11px] font-bold text-[#F55600] uppercase tracking-[0.14em]">
                        Web Searches Run <span className="text-[#2B2926]/40">· {webQueries.length}</span>
                      </h4>
                      <p className="text-[10px] text-[#2B2926]/40 font-bold mt-0.5">Queries Gemini executed to ground this report</p>
                    </div>
                    <ChevronDown className={`w-4 h-4 text-[#2B2926]/40 transition-transform ${showQueries ? 'rotate-180' : ''}`} />
                  </button>
                  {showQueries && (
                    <ul className="mt-4 space-y-1.5 pl-13">
                      {webQueries.map((q, i) => (
                        <li key={i} className="flex items-start gap-2 text-[12px] text-[#2B2926]/70 font-bold">
                          <Search className="w-3 h-3 mt-1 text-[#2B2926]/30 flex-shrink-0" />
                          <span className="font-mono text-[11px] bg-[#2B2926]/5 px-2 py-0.5 rounded">{q}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </motion.div>
              )}

              {/* Row 9: Google search entry point (compliance — required widget when displaying grounded answers) */}
              {searchEntryHtml && (
                <motion.div
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.45 }}
                  className="bg-white p-4 rounded-[20px] border border-[#2B2926]/30"
                  // Google requires this HTML snippet to render verbatim next
                  // to grounded answers. dangerouslySetInnerHTML is intentional.
                  dangerouslySetInnerHTML={{ __html: searchEntryHtml }}
                />
              )}

            </div>

            {/* Modal Footer */}
            <div className="p-3.5 sm:p-4 md:p-5 bg-slate-50/50 border-t border-slate-50 flex justify-end">
              <button
                onClick={() => setShowModal(false)}
                className="w-full sm:w-auto px-5 sm:px-6 md:px-7 py-2 sm:py-2 md:py-2.5 bg-slate-900 text-white rounded-xl text-[10px] sm:text-[10px] md:text-[11px] font-bold uppercase tracking-[0.14em] hover:bg-[#F55600] transition-all shadow-md shadow-slate-200"
              >
                Close Insights
              </button>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );

  return (
    <>
      <motion.div
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        className="px-2 py-2.5 bg-white rounded-2xl border-2 border-slate-300 shadow-sm flex items-center justify-between group overflow-hidden"
      >
        <div className="flex items-center gap-4">
          <div className="w-7 h-7 md:w-9 md:h-9 rounded-xl bg-[#2B2926]/[0.04] flex items-center justify-center text-[#2B2926] border border-[#2B2926]/15">
            <Search className="w-3.5 h-3.5 md:w-4 md:h-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-[10px] md:text-[11px] font-semibold text-[#2B2926] uppercase tracking-widest">Industry Research Blueprint</h3>
              {isGrounded && (
                <span className="text-[8px] font-semibold uppercase tracking-widest px-1.5 py-0.5 rounded-full bg-[#F55600]/10 text-[#F55600] flex items-center gap-1">
                  <Globe className="w-2.5 h-2.5" /> Web
                </span>
              )}
            </div>
            <p className="text-[8px] md:text-[9px] text-[#2B2926] font-semibold uppercase tracking-tight">
              {isGrounded
                ? `${sources.length} source${sources.length === 1 ? '' : 's'} · ${webQueries.length} quer${webQueries.length === 1 ? 'y' : 'ies'}`
                : 'AI analysis complete · Strategy ready for review'}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowModal(true)}
            className="flex items-center gap-2 px-3 md:px-4 py-1.5 bg-slate-900 text-white rounded-xl text-[9px] md:text-[10px] font-semibold uppercase tracking-widest hover:bg-[#F55600] transition-all shadow-lg shadow-slate-200 hover:shadow-orange-100 group/btn"
          >
            <Eye className="w-3 md:w-3.5 h-3 md:h-3.5 group-hover:scale-110 transition-transform" />
            Read Report
          </button>
        </div>
      </motion.div>

      {createPortal(ModalContent, document.body)}
    </>
  );
};

export default StrategicBlueprint;
