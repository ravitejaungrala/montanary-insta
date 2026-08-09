/*
 * RepresentativeCard — who outbound email is signed by.
 *
 * Without a representative every email closes "<Product> Team", which reads as
 * bulk mail and leaves the prospect nobody to reply to. Both campaign-creation
 * paths (New Campaign and Upload Leads) send the same cadence, so both capture
 * this — hence one shared component rather than two copies.
 *
 * The value is saved on the PRODUCT (product.icp['brand'] → rep_name/rep_title),
 * so it applies to every campaign for that product.
 */
import React from 'react';

// Common sender titles. A convenience list only — the input is free text and
// whatever the user types is stored verbatim, so this stays short rather than
// exhaustive, and nothing is enumerated server-side.
export const REP_ROLE_PRESETS = [
  'Founder',
  'Co-founder',
  'CEO',
  'Account Executive',
  'Sales Lead',
  'Growth Lead',
  'Business Development',
  'Partnerships',
  'Customer Success',
  'Solutions Engineer',
];

export default function RepresentativeCard({
  user,
  repIsMe,
  setRepIsMe,
  repName,
  setRepName,
  repTitle,
  setRepTitle,
  productName = '',
  disabled = false,
}) {
  return (
    <div className="mb-4 rounded-xl border border-[#2B2926]/10 bg-white p-4">
      <div className="flex items-baseline justify-between gap-2 mb-1">
        <h3 className="text-[13px] font-black text-[#2B2926]">Representative name</h3>
        <span className="text-[10.5px] text-[#2B2926]/45">
          Signs every email for this product
        </span>
      </div>
      <p className="text-[11.5px] text-[#2B2926]/60 mb-3 leading-relaxed">
        Emails close with this person instead of “{productName || 'Product'} Team” —
        prospects reply to people, not teams.
      </p>

      <div className="flex items-center gap-4 mb-3">
        {[
          { id: true, label: user?.full_name ? `You — ${user.full_name}` : 'You' },
          { id: false, label: 'Someone else' },
        ].map((opt) => (
          <label
            key={String(opt.id)}
            className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-[#2B2926] cursor-pointer"
          >
            <input
              type="radio"
              name="rep-who"
              checked={repIsMe === opt.id}
              onChange={() => {
                setRepIsMe(opt.id);
                // "You" restores the logged-in name; "Someone else" clears both
                // so nobody ships a half-edited identity.
                setRepName(opt.id ? user?.full_name || '' : '');
                setRepTitle('');
              }}
              className="accent-[#F55600]"
            />
            {opt.label}
          </label>
        ))}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="block text-[10.5px] font-bold uppercase tracking-wide text-[#2B2926]/50 mb-1">
            Name
          </label>
          <input
            type="text"
            value={repName}
            onChange={(e) => setRepName(e.target.value)}
            placeholder="e.g. Nitesh Reddy"
            disabled={disabled}
            className="w-full text-[13px] px-2.5 py-1.5 rounded-lg border border-[#cdd1d9] focus:outline-none focus:border-[#F55600] disabled:opacity-50"
          />
        </div>
        <div>
          <label className="block text-[10.5px] font-bold uppercase tracking-wide text-[#2B2926]/50 mb-1">
            Role
          </label>
          {/* datalist = click a common title or type anything. */}
          <input
            type="text"
            list="rep-role-presets"
            value={repTitle}
            onChange={(e) => setRepTitle(e.target.value)}
            placeholder="Pick one or type your own"
            disabled={disabled}
            className="w-full text-[13px] px-2.5 py-1.5 rounded-lg border border-[#cdd1d9] focus:outline-none focus:border-[#F55600] disabled:opacity-50"
          />
          <datalist id="rep-role-presets">
            {REP_ROLE_PRESETS.map((r) => (
              <option key={r} value={r} />
            ))}
          </datalist>
        </div>
      </div>

      {repName.trim() && repTitle.trim() && (
        <p className="mt-3 text-[11px] text-[#2B2926]/55">
          Signs off as <span className="font-bold text-[#2B2926]">{repName.trim()}</span>
          <span className="text-[#2B2926]/70"> · {repTitle.trim()}</span>
        </p>
      )}
    </div>
  );
}
