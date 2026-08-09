import { useCallback, useEffect, useState } from 'react';

/**
 * Reads the funding user's credit balance from GET /nexus/credits/balance.
 * Returns { balance, loading, refresh }. `balance` is null until first load.
 */
export default function useCreditBalance(authAxios, apiBase) {
  const [balance, setBalance] = useState(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!authAxios || !apiBase) return;
    setLoading(true);
    try {
      const res = await authAxios.get(`${apiBase}/credits/balance`);
      setBalance(res.data || null);
    } catch (_e) {
      // Non-fatal: leave the last-known balance in place.
    } finally {
      setLoading(false);
    }
  }, [authAxios, apiBase]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { balance, loading, refresh };
}
