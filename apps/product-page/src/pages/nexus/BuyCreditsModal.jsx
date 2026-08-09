import React, { useState } from 'react';
import { X, Coins, Check, ChevronDown } from 'lucide-react';

// Credit packs (defined in code; must match backend CREDIT_PACKS).
const PACKS = [
  { credits: 500, price: 10 },
  { credits: 1000, price: 25 },
  { credits: 2500, price: 50 },
  { credits: 5000, price: 100 },
  { credits: 10000, price: 180 },
];

/**
 * Buy-Credits modal: a styled dropdown of credit packs → POST
 * /payment/buy-credits → redirect to Stripe Checkout. `authAxios` is the app's
 * axios instance (baseURL = API root, so the /payment path resolves correctly).
 */
export default function BuyCreditsModal({ open, onClose, authAxios }) {
  const [selected, setSelected] = useState(PACKS[1].credits); // default 1000
  const [menuOpen, setMenuOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  if (!open) return null;

  const pack = PACKS.find((p) => p.credits === Number(selected)) || PACKS[0];

  const buy = async () => {
    setBusy(true);
    setError('');
    try {
      const res = await authAxios.post('/payment/buy-credits', { credits: pack.credits });
      if (res?.data?.url) {
        window.location.href = res.data.url; // → Stripe Checkout
      } else {
        setError('Could not start checkout. Please try again.');
        setBusy(false);
      }
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Checkout failed.');
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-black text-[#2B2926] flex items-center gap-2">
            <Coins className="w-5 h-5 text-[#F55600]" /> Buy Credits
          </h3>
          <button type="button" onClick={onClose} className="text-black/40 hover:text-black">
            <X className="w-5 h-5" />
          </button>
        </div>

        {error && (
          <div className="mt-3 rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-xs font-semibold text-red-700">
            {error}
          </div>
        )}

        <label className="mt-5 block text-[11px] font-bold uppercase tracking-wide text-black/40">
          Choose a pack
        </label>

        {/* Custom dropdown */}
        <div className="relative mt-1.5">
          <button
            type="button"
            onClick={() => setMenuOpen((v) => !v)}
            className={`flex w-full items-center justify-between rounded-xl border px-4 py-3 text-left transition-colors ${
              menuOpen ? 'border-[#F55600] ring-1 ring-[#F55600]/30' : 'border-black/15 hover:border-black/30'
            }`}
          >
            <span className="flex items-baseline gap-2">
              <span className="text-base font-black text-[#2B2926]">
                {pack.credits.toLocaleString()}
              </span>
              <span className="text-xs font-semibold text-black/40">credits</span>
            </span>
            <span className="flex items-center gap-2">
              <span className="text-base font-black text-[#2B2926]">${pack.price}</span>
              <ChevronDown
                className={`h-4 w-4 text-black/40 transition-transform ${menuOpen ? 'rotate-180' : ''}`}
              />
            </span>
          </button>

          {menuOpen && (
            <div className="absolute left-0 right-0 z-10 mt-2 overflow-hidden rounded-xl border border-black/10 bg-white shadow-xl">
              {PACKS.map((p) => {
                const active = p.credits === Number(selected);
                return (
                  <button
                    key={p.credits}
                    type="button"
                    onClick={() => {
                      setSelected(p.credits);
                      setMenuOpen(false);
                    }}
                    className={`flex w-full items-center justify-between px-4 py-2.5 text-left transition-colors ${
                      active ? 'bg-[#F55600]/10' : 'hover:bg-black/5'
                    }`}
                  >
                    <span className="flex items-baseline gap-2">
                      <span className="text-sm font-bold text-[#2B2926]">
                        {p.credits.toLocaleString()}
                      </span>
                      <span className="text-[11px] font-semibold text-black/40">credits</span>
                    </span>
                    <span className="flex items-center gap-2">
                      <span className="text-sm font-bold text-[#2B2926]">${p.price}</span>
                      {active && <Check className="h-3.5 w-3.5 text-[#F55600]" />}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <button
          type="button"
          disabled={busy}
          onClick={buy}
          className="mt-5 w-full rounded-xl bg-[#F55600] py-3 text-sm font-bold text-white hover:opacity-90 disabled:opacity-50"
        >
          {busy ? 'Redirecting…' : `Buy ${pack.credits.toLocaleString()} credits · $${pack.price}`}
        </button>

        <p className="mt-3 flex items-center justify-center gap-1 text-[10px] text-black/40">
          <Check className="w-3 h-3" /> Secure one-time payment via Stripe · credits added instantly
        </p>
      </div>
    </div>
  );
}
