import React from 'react';
import { CircleDollarSign } from 'lucide-react';

/**
 * "Credits: N" chip. Amber when low, red "Buy Credits" when zero. Clicking it
 * opens the Buy-Credits modal. Renders nothing until a balance is loaded.
 */
export default function CreditsBadge({ balance, onBuy }) {
  if (!balance) return null;

  const { available, unlimited, out_of_credits, low_credits } = balance;

  if (unlimited) {
    return (
      <span
        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-emerald-200 bg-emerald-50 text-emerald-700 text-xs font-bold"
        title="Your plan includes unlimited credits"
      >
        <CircleDollarSign className="w-3.5 h-3.5" strokeWidth={2.5} />
        Unlimited Credits
      </span>
    );
  }

  let cls = 'border-[#2B2926]/20 text-[#2B2926] bg-white/70';
  if (out_of_credits) cls = 'border-red-300 text-red-700 bg-red-50';
  else if (low_credits) cls = 'border-amber-300 text-amber-700 bg-amber-50';

  return (
    <button
      type="button"
      onClick={onBuy}
      title={
        out_of_credits
          ? 'You are out of credits — click to buy more'
          : `${available} credits left${low_credits ? ' (running low)' : ''} — click to buy more`
      }
      className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-bold transition-colors hover:opacity-90 ${cls}`}
    >
      <CircleDollarSign className="w-3.5 h-3.5" />
      {out_of_credits ? 'Buy Credits' : `Credits: ${available}`}
    </button>
  );
}
