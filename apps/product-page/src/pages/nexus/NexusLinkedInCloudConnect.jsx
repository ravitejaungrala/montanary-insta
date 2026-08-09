import React, { useEffect, useRef, useState } from 'react';

/**
 * Cloud login (Phase 2) — the user logs into LinkedIn inside a browser WE host
 * (Fargate + noVNC), so the session is born in our environment and the full
 * profile is captured. We never see the password.
 *
 * Flow:
 *   POST {base}/connect/cloud/start  → { session_id }
 *   poll GET {base}/connect/cloud/{session_id} until:
 *     - viewer_url appears  → show the live browser in an iframe
 *     - status === 'saved'  → success
 *     - status in failed/expired → error
 *
 * Props mirror LinkedInConnectModal: { base, authAxios, lockedBrandId, setMessage, onDone }.
 */
export function LinkedInCloudConnect({ base, authAxios, lockedBrandId, setMessage, onDone }) {
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState(null);
  const [status, setStatus] = useState(null);
  const [viewerUrl, setViewerUrl] = useState(null);
  const pollRef = useRef(null);

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };
  useEffect(() => stopPolling, []); // cleanup on unmount

  const start = async () => {
    if (!email.trim()) { setMessage?.('LinkedIn email is required.'); return; }
    setBusy(true);
    setStatus('starting');
    setViewerUrl(null);
    try {
      const res = await authAxios.post(`${base}/connect/cloud/start`, {
        linkedin_email: email.trim(),
        brand_id: lockedBrandId ?? null,
      });
      const sid = res.data?.session_id;
      setSessionId(sid);
      setStatus(res.data?.status || 'awaiting_login');
      // Poll for the viewer URL + completion.
      pollRef.current = setInterval(async () => {
        try {
          const s = await authAxios.get(`${base}/connect/cloud/${sid}`);
          const st = s.data?.status;
          setStatus(st);
          if (s.data?.viewer_url) setViewerUrl(s.data.viewer_url);
          if (st === 'saved') {
            stopPolling();
            setMessage?.('LinkedIn connected — session captured.');
            onDone?.();
          } else if (st === 'failed' || st === 'expired') {
            stopPolling();
            setMessage?.(s.data?.detail || 'Reconnect LinkedIn — cloud login did not complete.');
          }
        } catch {
          /* transient — keep polling */
        }
      }, 2000);
    } catch (e) {
      const msg = e?.response?.data?.detail || 'Could not start cloud login.';
      setMessage?.(msg);
      setStatus(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      {!sessionId && (
        <>
          <label className="block text-sm font-semibold text-[#2B2926]">LinkedIn email</label>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com"
            className="w-full rounded-lg border border-[#2B2926]/15 px-3 py-2 text-sm"
          />
          <p className="text-[12px] text-[#2B2926]/60">
            A secure browser opens below. Log into LinkedIn there (password + any 2FA).
            We never see your password — only the resulting session is saved.
          </p>
          <button
            type="button"
            disabled={busy}
            onClick={start}
            className="rounded-lg bg-[#F55600] px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
          >
            {busy ? 'Starting…' : 'Connect via secure browser'}
          </button>
        </>
      )}

      {sessionId && (
        <div className="space-y-2">
          <div className="text-[12px] font-semibold text-[#2B2926]">
            Status: <span className="text-[#F55600]">{status || '…'}</span>
          </div>
          {status === 'saved' ? (
            <div className="rounded-lg border border-[#1F7A45]/30 bg-[#EAF5EE] px-3 py-4 text-sm text-[#1F7A45] font-semibold">
              ✅ Connected — LinkedIn session captured.
            </div>
          ) : viewerUrl ? (
            <div className="space-y-2 rounded-lg border border-[#2B2926]/12 bg-[#F5F3F1] px-3 py-4 text-center">
              <a
                href={viewerUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-block rounded-lg bg-[#F55600] px-4 py-2 text-sm font-semibold text-white"
              >
                Open secure browser to log in ↗
              </a>
              <p className="text-[12px] text-[#2B2926]/60">
                Opens the login window in a new tab. Log into LinkedIn there (password + any 2FA) —
                this box updates automatically when you’re done.
              </p>
            </div>
          ) : (
            <div className="rounded-lg border border-[#2B2926]/12 bg-[#F5F3F1] px-3 py-6 text-center text-sm text-[#2B2926]/60">
              Preparing your secure browser… this can take ~30–60 seconds.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default LinkedInCloudConnect;
