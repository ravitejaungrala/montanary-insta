/**
 * LocationSelect — hierarchical location picker for the NewCampaign
 * targeting step. Lets the user target at three granularities in ONE
 * searchable dropdown:
 *
 *   • Country  →  "United States"
 *   • State    →  "California, United States"
 *   • City     →  "San Francisco, California, United States"
 *
 * The emitted value is always a comma-joined "City, State, Country" string
 * (most-specific-first), which is exactly the shape Apollo's `person_locations`
 * filter accepts (see discovery_apollo._normalise_locations). Carrying the
 * full path in the value also disambiguates duplicate city names (there are
 * dozens of "Springfield"s) — two cities never collapse to the same chip.
 *
 * Data source: the `country-state-city` package (already a dependency, also
 * used by Team.jsx). City search builds a lowercased index once (cached in a
 * ref) and only kicks in at 2+ chars so the 148k-row scan never runs on a
 * single keystroke.
 *
 * Mirrors the MultiSelect chip/input styling in NexusNewCampaign.jsx so the
 * targeting page stays visually consistent.
 */
import React, { useMemo, useRef, useState, useEffect } from 'react';
import { X } from 'lucide-react';
import { Country, State, City } from 'country-state-city';

const MAX_COUNTRIES = 8;
const MAX_STATES = 12;
const MAX_CITIES = 40;

export default function LocationSelect({
  label,
  selected,
  onChange,
  placeholder = 'Search a country, state, or city…',
  badge = null,
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  // Lowercased city index, built lazily the first time the user types 2+ chars.
  const cityIndexRef = useRef(null);

  // Close on outside click.
  useEffect(() => {
    function handleClick(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const selectedLower = useMemo(
    () => new Set((selected || []).map((s) => s.toLowerCase())),
    [selected],
  );

  // Build the merged country/state/city option list for the current query.
  const options = useMemo(() => {
    const q = query.trim().toLowerCase();
    const out = [];
    const pushed = new Set();

    const add = (value, optLabel, tier) => {
      const low = value.toLowerCase();
      if (selectedLower.has(low) || pushed.has(low)) return;
      pushed.add(low);
      out.push({ value, label: optLabel, tier });
    };

    // ── Countries ──────────────────────────────────────────────────────
    const countries = Country.getAllCountries();
    const matchedCountries = (
      q ? countries.filter((c) => c.name.toLowerCase().includes(q)) : countries
    ).slice(0, q ? MAX_COUNTRIES : 12);
    matchedCountries.forEach((c) =>
      add(c.name, `${c.flag ? c.flag + ' ' : ''}${c.name}`, 'Country'),
    );

    // ── States (only once the user is searching) ────────────────────────
    if (q.length >= 1) {
      const states = State.getAllStates();
      let n = 0;
      for (let i = 0; i < states.length && n < MAX_STATES; i += 1) {
        const s = states[i];
        if (!s.name.toLowerCase().includes(q)) continue;
        const country = Country.getCountryByCode(s.countryCode);
        if (!country) continue;
        const value = `${s.name}, ${country.name}`;
        if (!selectedLower.has(value.toLowerCase()) && !pushed.has(value.toLowerCase())) {
          add(value, value, 'State');
          n += 1;
        }
      }
    }

    // ── Cities (2+ chars; heavy scan, so gated + capped) ─────────────────
    if (q.length >= 2) {
      if (!cityIndexRef.current) {
        cityIndexRef.current = City.getAllCities().map((ci) => ({
          name: ci.name,
          lname: ci.name.toLowerCase(),
          countryCode: ci.countryCode,
          stateCode: ci.stateCode,
        }));
      }
      const idx = cityIndexRef.current;
      let n = 0;
      for (let i = 0; i < idx.length && n < MAX_CITIES; i += 1) {
        const ci = idx[i];
        if (!ci.lname.includes(q)) continue;
        const country = Country.getCountryByCode(ci.countryCode);
        const state = State.getStateByCodeAndCountry(ci.stateCode, ci.countryCode);
        const parts = [ci.name, state?.name, country?.name].filter(Boolean);
        const value = parts.join(', ');
        if (!selectedLower.has(value.toLowerCase()) && !pushed.has(value.toLowerCase())) {
          add(value, value, 'City');
          n += 1;
        }
      }
    }

    return out;
  }, [query, selectedLower]);

  function addValue(value) {
    const v = (value || '').trim();
    if (!v) return;
    if (selectedLower.has(v.toLowerCase())) {
      setQuery('');
      return;
    }
    onChange([...selected, v]);
    setQuery('');
  }

  // LIST-ONLY: locations must be picked from the country/state/city database
  // (it's comprehensive — 148k cities). Custom free-text locations are NOT
  // allowed; only the Technologies field accepts arbitrary custom values.

  return (
    <div className="mb-2.5" ref={wrapRef}>
      <div className="flex items-center justify-between mb-1">
        <label className="block text-[11px] font-black uppercase tracking-wider text-[#2B2926]/60">
          {label}
        </label>
        {badge}
      </div>
      <div
        className="min-h-[40px] bg-white px-3 py-1.5 transition-all"
        style={{
          border: `1px solid ${open ? '#ff4d0d' : '#cdd1d9'}`,
          borderRadius: 12,
          boxShadow: '0 1px 2px rgba(15,17,21,0.04)',
        }}
      >
        <div className="flex flex-wrap items-center gap-2">
          {selected.map((s) => (
            <span
              key={s}
              className="inline-flex items-center gap-1.5 rounded-md text-[12.5px] font-semibold"
              style={{ background: '#f1f2f4', color: '#1c2128', padding: '4px 9px' }}
            >
              {s}
              <button
                type="button"
                className="rounded"
                style={{ color: '#ff4d0d', lineHeight: 1, fontWeight: 700 }}
                onClick={() => onChange(selected.filter((x) => x !== s))}
                aria-label={`Remove ${s}`}
              >
                <X className="w-3 h-3" />
              </button>
            </span>
          ))}
          <input
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                // List-only: add the top DB suggestion; never raw custom text.
                addValue(options[0]?.value || '');
              } else if (e.key === 'Backspace' && !query && selected.length) {
                onChange(selected.slice(0, -1));
              }
            }}
            placeholder={selected.length === 0 ? placeholder : 'Add another location…'}
            className="flex-1 min-w-[160px] bg-transparent text-sm text-[#2B2926] placeholder:text-[#2B2926]/40 focus:outline-none"
          />
        </div>
        {open && (
          <div className="mt-2 -mx-2 -mb-1.5 border-t border-[#2B2926]/10 max-h-60 overflow-y-auto bg-white">
            {options.map((o) => (
              <div
                key={`${o.tier}:${o.value}`}
                className="px-3 py-2 text-sm text-[#2B2926] hover:bg-[#F55600]/5 cursor-pointer flex items-center justify-between gap-2"
                onMouseDown={(e) => {
                  e.preventDefault();
                  addValue(o.value);
                }}
              >
                <span className="truncate">{o.label}</span>
                <span className="text-[10px] font-bold uppercase tracking-wide text-[#2B2926]/30 shrink-0">
                  {o.tier}
                </span>
              </div>
            ))}
            {/* No "+ Add custom" row — locations are list-only (pick from the
                country/state/city database). Custom entry is Technologies-only. */}
            {options.length === 0 && query.trim().length === 1 && (
              <div className="px-3 py-2 text-xs text-[#2B2926]/40">
                Keep typing to search cities…
              </div>
            )}
            {options.length === 0 && query.trim().length >= 2 && (
              <div className="px-3 py-2 text-xs text-[#2B2926]/40">
                No match — pick a country, state, or city.
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
