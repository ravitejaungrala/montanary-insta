/**
 * FilterSidebar — dynamic filter panel for the GTM Journey workspace.
 *
 * Apollo-style design:
 *   • Each filter group renders as a single collapsible row: left icon
 *     + label + right chevron.
 *   • When expanded, the row reveals a "Search [label]…" input with a
 *     scrollable matching list underneath (NOT a chip grid). Click an
 *     option to toggle. Selected items show up as removable chips
 *     above the input.
 *   • Footer: "Clear filters" link + "More Filters" button.
 *
 * The top Total / Net New / Saved tab strip was removed per request.
 *
 * Props:
 *   schema:           { total, filters: [{ id, label, type, options }] }
 *   loading:          bool   — render skeletons while the schema loads
 *   resultCount:      number — leads after filters applied (from leads API)
 *   selected:         { [filterId]: (string|number|boolean)[] }
 *   onChange:         (filterId, valueArray) => void
 *   onClear:          () => void          — clear every filter
 *   collapsed:        bool   — render the icon-only rail
 *   pinned:           bool   — pin/unpin button state
 *   onTogglePin:      () => void
 */
import React, { useMemo, useState } from 'react';
import {
  ChevronDown,
  X,
  Filter,
  Search,
  SlidersHorizontal,
  Pin,
  PinOff,
  ClipboardList,
  Building2,
  MapPin,
  Users,
  Briefcase,
  Layers,
  Hash,
  Sparkles,
  Mail,
  CheckCircle2,
  Flag,
  Megaphone,
  Tag,
  Check,
} from 'lucide-react';

// Group-id → icon. Falls back to Filter for anything unrecognized.
const GROUP_ICONS = {
  status: CheckCircle2,
  priority: Flag,
  campaign: Megaphone,
  company: Building2,
  account_location: MapPin,
  location: MapPin,
  geo: MapPin,
  geography: MapPin,
  geographies: MapPin,
  employees: Users,
  company_size: Users,
  company_sizes: Users,
  industry: Briefcase,
  industries: Briefcase,
  market_segment: Layers,
  market_segments: Layers,
  sic: Hash,
  naics: Hash,
  ai: Sparkles,
  ai_filters: Sparkles,
  lists: ClipboardList,
  lookalikes: Building2,
  product: Tag,
  email_verified: Mail,
  title: Briefcase,
  job_title: Briefcase,
  job_titles: Briefcase,
  engagement: Sparkles,
};

const iconFor = (id) => GROUP_ICONS[String(id).toLowerCase()] || Filter;

const SkeletonRow = () => (
  <div className="px-3 py-3 border-b border-[#2B2926]/5 animate-pulse">
    <div className="h-4 bg-[#2B2926]/5 rounded" />
  </div>
);

// One Apollo-style row. Collapsed by default — click to expand and
// reveal a searchable dropdown of options.
const FilterRow = ({ group, selectedValues, onChange }) => {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const Icon = iconFor(group.id);

  const selectedSet = useMemo(
    () => new Set((selectedValues || []).map((v) => String(v))),
    [selectedValues],
  );
  const isBoolean = group.type === 'boolean';

  // Options matched against the search query — case-insensitive prefix
  // OR substring match on the label / value.
  const matchedOptions = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return group.options;
    return group.options.filter((o) => {
      const label = String(o.label != null ? o.label : o.value).toLowerCase();
      return label.includes(q);
    });
  }, [group.options, query]);

  // The selected options (always shown at the top of the list as
  // removable chips) — derived from the live schema so we always have
  // the latest label and count.
  const selectedOptions = useMemo(
    () =>
      group.options.filter((o) => selectedSet.has(String(o.value))),
    [group.options, selectedSet],
  );

  const toggle = (value) => {
    const key = String(value);
    const opts = group.options;
    const coerce = (k) => {
      const match = opts.find((o) => String(o.value) === k);
      return match ? match.value : k;
    };

    if (isBoolean) {
      if (selectedSet.has(key)) onChange(group.id, []);
      else onChange(group.id, [coerce(key)]);
      return;
    }

    const next = new Set(selectedSet);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onChange(group.id, Array.from(next).map(coerce));
  };

  const removeOne = (value) => toggle(value);

  const activeCount = selectedSet.size;
  const hasActive = activeCount > 0;

  return (
    <div className="border-b border-[#2B2926]/5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={[
          'w-full flex items-center justify-between gap-2 px-3 py-3 transition-colors',
          hasActive
            ? 'bg-[#F55600]/[0.06] text-[#F55600]'
            : 'bg-white text-[#2B2926] hover:bg-[#2B2926]/[0.02]',
        ].join(' ')}
      >
        <span className="inline-flex items-center gap-2 min-w-0">
          <Icon
            className={[
              'w-4 h-4 shrink-0',
              hasActive ? 'text-[#F55600]' : 'text-[#2B2926]',
            ].join(' ')}
          />
          <span
            className={[
              'text-[13px] font-semibold truncate',
              hasActive ? 'text-[#F55600]' : 'text-[#2B2926]',
            ].join(' ')}
          >
            {group.label}
          </span>
          {hasActive && (
            <span className="inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full bg-[#F55600] text-white text-[9px] font-black">
              {activeCount}
            </span>
          )}
        </span>
        <ChevronDown
          className={[
            'w-4 h-4 shrink-0 text-[#2B2926] transition-transform',
            open ? 'rotate-180' : '',
          ].join(' ')}
        />
      </button>

      {open && (
        // Expanded panel: chips of currently-selected values → search
        // input → scrollable list of options that match the query.
        // `box-border` + explicit `w-full` on every full-width child so
        // padding never pushes content past the panel edge.
        <div className="px-3 pb-3 pt-0 bg-white">
          {/* Selected chips */}
          {selectedOptions.length > 0 && (
            <div className="flex flex-wrap gap-1 mb-2 mt-1">
              {selectedOptions.map((opt) => {
                const label =
                  opt.label != null ? String(opt.label) : String(opt.value);
                return (
                  <span
                    key={String(opt.value)}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-[#F55600]/10 text-[#F55600] text-[11px] font-bold border border-[#F55600]/30 min-w-0 max-w-full"
                    title={label}
                  >
                    <span className="truncate min-w-0">{label}</span>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        removeOne(opt.value);
                      }}
                      className="shrink-0 -mr-1 p-0.5 rounded hover:bg-[#F55600]/15"
                      title="Remove"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </span>
                );
              })}
            </div>
          )}

          {/* Search input */}
          <div className="relative w-full">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[#2B2926] pointer-events-none" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={`Search ${group.label.toLowerCase()}…`}
              className="block w-full box-border pl-7 pr-3 py-1.5 rounded-md border border-[#2B2926]/15 bg-white text-[12px] text-[#2B2926] placeholder:text-[#2B2926] focus:outline-none focus:border-[#F55600] transition-colors"
            />
          </div>

          {/* Matching options list. `scrollbar-gutter: stable` reserves
              the scrollbar width so the rows don't shift when scroll
              appears, and we set explicit box-sizing on the container
              so the rows are bounded by panel width minus border. */}
          {matchedOptions.length === 0 ? (
            <div className="mt-2 px-2 py-3 text-[11px] text-[#2B2926] text-center">
              No matches
            </div>
          ) : (
            // Cap the list at ~5 visible rows so any group with more
            // options scrolls INSIDE this box instead of pushing the
            // next filter row down the panel. `scrollbar-gutter: stable`
            // reserves the scrollbar lane so rows don't shift when the
            // list overflows.
            <div
              className="mt-2 max-h-[180px] overflow-y-auto overflow-x-hidden rounded-md border border-[#2B2926]/10 box-border"
              style={{ scrollbarWidth: 'thin', scrollbarGutter: 'stable' }}
            >
              {matchedOptions.map((opt) => {
                const key = String(opt.value);
                const label = opt.label != null ? String(opt.label) : key;
                const isSelected = selectedSet.has(key);
                return (
                  <button
                    key={key}
                    type="button"
                    onClick={() => toggle(opt.value)}
                    className={[
                      // `gap-2` between label-block and count + explicit
                      // pl-2 pr-2.5 keeps the count off the right edge.
                      'w-full box-border flex items-center justify-between gap-2 pl-2 pr-2.5 py-1.5 text-left text-[12px] transition-colors border-b border-[#2B2926]/[0.04] last:border-b-0',
                      isSelected
                        ? 'bg-[#F55600]/[0.06] text-[#F55600]'
                        : 'bg-white text-[#2B2926] hover:bg-[#2B2926]/[0.03]',
                    ].join(' ')}
                    title={`${label} (${opt.count})`}
                  >
                    {/* Label block — checkbox + truncating label.
                        `flex-1 min-w-0` makes the label shrink with
                        ellipsis so the count never gets pushed off. */}
                    <span className="inline-flex items-center gap-1.5 min-w-0 flex-1">
                      <span
                        className={[
                          'inline-flex items-center justify-center w-3.5 h-3.5 rounded border shrink-0',
                          isSelected
                            ? 'bg-[#F55600] border-[#F55600] text-white'
                            : 'bg-white border-[#2B2926]/25',
                        ].join(' ')}
                      >
                        {isSelected && <Check className="w-2.5 h-2.5" strokeWidth={3.5} />}
                      </span>
                      <span className="truncate font-semibold min-w-0">{label}</span>
                    </span>
                    <span
                      className={[
                        'shrink-0 text-[10px] font-bold tabular-nums',
                        isSelected ? 'text-[#F55600]/80' : 'text-[#2B2926]',
                      ].join(' ')}
                    >
                      {opt.count}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

const FilterSidebar = ({
  schema,
  loading,
  resultCount,
  selected,
  onChange,
  onClear,
  collapsed = false,
  pinned = false,
  onTogglePin,
}) => {
  const filters = schema?.filters || [];

  const activeTotal = useMemo(
    () =>
      Object.values(selected || {}).reduce(
        (n, arr) => n + (Array.isArray(arr) ? arr.length : 0),
        0,
      ),
    [selected],
  );

  // ── Collapsed icon-rail ────────────────────────────────────────────
  if (collapsed) {
    return (
      <button
        type="button"
        onClick={onTogglePin}
        title="Open filters"
        className="flex flex-col items-center min-h-0 h-full w-full bg-white py-3 gap-3 hover:bg-[#F55600]/5 transition-colors cursor-pointer"
      >
        <div className="relative" title="Filters">
          <SlidersHorizontal className="w-4 h-4 text-[#2B2926]" />
          {activeTotal > 0 && (
            <span className="absolute -top-1.5 -right-2 inline-flex items-center justify-center min-w-[14px] h-[14px] px-1 rounded-full bg-[#F55600] text-white text-[8px] font-black">
              {activeTotal}
            </span>
          )}
        </div>
        <div
          className="text-[9px] font-bold text-[#2B2926] -rotate-90 whitespace-nowrap mt-3"
          style={{ letterSpacing: '0.15em' }}
        >
          FILTERS
        </div>
      </button>
    );
  }

  // ── Expanded panel ─────────────────────────────────────────────────
  // Fill the parent container width — the NexusJourney wrapper sets the
  // outer column to 280px and uses `overflow-hidden`, so a hardcoded
  // 320px was getting its right edge clipped (the "More Filters" button
  // showed up as "More Filt…"). Letting the panel inherit width keeps
  // everything inside the visible column at every breakpoint.
  return (
    <div className="flex flex-col min-h-0 h-full w-full max-w-full bg-white box-border">
      {/* Slim header — Filters label + result count + pin toggle */}
      <div className="px-3 pt-3 pb-2 shrink-0">
        <div className="flex items-center justify-between gap-2">
          <div className="inline-flex items-center gap-1.5 text-[13px] font-bold text-[#2B2926]">
            <SlidersHorizontal className="w-4 h-4" />
            Filters
            {activeTotal > 0 && (
              <span className="text-[11px] font-bold text-[#F55600]">
                · {activeTotal} active
              </span>
            )}
          </div>
          {/* Close (X) button — explicitly closes the sidebar. Used
              in place of the pin toggle now that hover-to-open is gone:
              the sidebar opens on click of the rail icon, and closes
              here. We still expose the underlying onTogglePin so the
              same toggle handler is reused. */}
          {onTogglePin && (
            <button
              type="button"
              onClick={onTogglePin}
              title="Close filters"
              className="p-1 rounded hover:bg-[#F55600]/10 transition-colors text-[#2B2926]"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>
        <div className="text-[11px] text-[#2B2926] mt-0.5">
          {loading
            ? 'Loading filters…'
            : resultCount != null
            ? `${resultCount.toLocaleString()} result${resultCount === 1 ? '' : 's'}`
            : `${(schema?.total ?? 0).toLocaleString()} total leads`}
        </div>
      </div>

      {/* Filter rows — scrollable list */}
      <div
        className="flex-1 overflow-y-auto overflow-x-hidden border-t border-[#2B2926]/5"
        style={{ scrollbarGutter: 'stable', scrollbarWidth: 'thin' }}
      >
        {loading && filters.length === 0 ? (
          <>
            <SkeletonRow />
            <SkeletonRow />
            <SkeletonRow />
            <SkeletonRow />
            <SkeletonRow />
          </>
        ) : filters.length === 0 ? (
          <div className="px-3 py-8 text-center">
            <Filter className="w-7 h-7 mx-auto text-[#2B2926] mb-2" />
            <p className="text-[11px] text-[#2B2926]">
              No filters available yet — launch a campaign to populate
              filterable data.
            </p>
          </div>
        ) : (
          filters.map((g) => (
            <FilterRow
              key={g.id}
              group={g}
              selectedValues={(selected || {})[g.id] || []}
              onChange={onChange}
            />
          ))
        )}
      </div>

      {/* Footer — Clear filters only. (The previous "More Filters"
          button was removed per request — sidebar already exposes
          every backend-schema-driven group, so a secondary "more"
          affordance didn't add anything.) */}
      <div className="shrink-0 border-t border-[#2B2926]/10 px-3 py-2.5 flex items-center bg-white box-border">
        <button
          type="button"
          onClick={onClear}
          disabled={activeTotal === 0}
          className={[
            'text-[12px] font-semibold transition-colors',
            activeTotal === 0
              ? 'text-[#2B2926] cursor-not-allowed'
              : 'text-[#2B2926] hover:text-[#F55600]',
          ].join(' ')}
        >
          Clear filters
        </button>
      </div>
    </div>
  );
};

// React.memo so the sidebar (which renders N filter rows × M
// options each, plus useMemos per row) doesn't re-render on every
// state tick in NexusJourney. With stable callback props from the
// parent, the sidebar only re-renders when schema / selected /
// loading / resultCount actually change.
export default React.memo(FilterSidebar);
