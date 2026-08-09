import React, { useEffect, useState } from 'react';
import { AlertTriangle, X } from 'lucide-react';

/**
 * One-time-per-day low-credit popup. Fires on app load when the balance is low
 * or zero. Guarded via localStorage so it doesn't nag on every navigation.
 * `onBuy` opens the Buy-Credits modal.
 */
export default function CreditWarningPopup({ balance, onBuy }) {
  const [show, setShow] = useState(false);

  useEffect(() => {
    if (!balance || balance.unlimited) return;
    if (!balance.low_credits && !balance.out_of_credits) return;
    // Show once per day per balance state.
    const today = new Date().toISOString().slice(0, 10);
    const key = `nexus_credit_warn_${today}_${balance.out_of_credits ? 'zero' : 'low'}`;
    try {
      if (localStorage.getItem(key)) return;
      localStorage.setItem(key, '1');
    } catch (_e) {
      /* localStorage unavailable — still show once this session */
    }
    setShow(true);
  }, [balance]);

  if (!show || !balance) return null;

  const zero = balance.out_of_credits;
  return (
    <div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-sm rounded-2xl bg-white p-5 shadow-2xl">
        <div className="flex items-start gap-3">
          <div className={`mt-0.5 rounded-full p-2 ${zero ? 'bg-red-50 text-red-600' : 'bg-amber-50 text-amber-600'}`}>
            <AlertTriangle className="w-5 h-5" />
          </div>
          <div className="flex-1">
            <h3 className="text-sm font-black text-[#2B2926]">
              {zero ? 'You are out of credits' : 'You are low on credits'}
            </h3>
            <p className="mt-1 text-xs text-black/60">
              {zero
                ? 'Campaigns can’t reveal new leads until you buy more credits.'
                : `Only ${balance.available} credit${balance.available === 1 ? '' : 's'} left. Buy more so your campaigns don’t stop mid-run.`}
            </p>
          </div>
          <button type="button" onClick={() => setShow(false)} className="text-black/30 hover:text-black">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => setShow(false)}
            className="rounded-lg px-3 py-1.5 text-xs font-semibold text-black/50 hover:bg-black/5"
          >
            Later
          </button>
          <button
            type="button"
            onClick={() => {
              setShow(false);
              onBuy && onBuy();
            }}
            className="rounded-lg bg-[#F55600] px-3 py-1.5 text-xs font-bold text-white hover:opacity-90"
          >
            Buy Credits
          </button>
        </div>
      </div>
    </div>
  );
}
