import React, { useState } from 'react';
import { LinkedInCloudConnect } from './NexusLinkedInCloudConnect';

// Published Chrome extension ID — fill in after publishing (shown on
// chrome://extensions). Required for the one-click "Connect via extension"
// auto-handoff; the manual copy-token + paste-cookies paths work without it.
const EXTENSION_ID = 'amfmjdibkilkdlkpkbimjfbaahimafji';

// Chrome Web Store listing — fill in after publishing (it'll resolve to the
// stable ID above). Used by the "Install extension" link in the Connect modal.
const EXTENSION_STORE_URL = `https://chromewebstore.google.com/detail/${EXTENSION_ID}`;

/**
 * Brand-locked "Connect LinkedIn" modal — used per brand row on the Connectors
 * page, mirroring the email mailbox connect. Connects a LinkedIn account and
 * assigns it to THIS one brand (lockedBrandId). Backend: /nexus/linkedin-agent/*.
 *
 * NOTE: the actual sign-in is performed by the GTM LinkedIn worker (headless
 * login + cookie capture). Until that worker is deployed, a submitted account
 * stays in 'pending'/'challenge'. The UI is fully wired for when it's live.
 */
export function LinkedInConnectModal({ base, authAxios, lockedBrandId, brandName, setMessage, onClose, onDone }) {
  // Two ways to connect a LinkedIn session captured in the user's OWN browser:
  //   'extension' = one-click via the Pipelyt GTM extension (recommended)
  //   'paste'     = paste exported LinkedIn cookies (no extension)
  const [mode, setMode] = useState('extension');
  const [email, setEmail] = useState('');
  const [cookiesText, setCookiesText] = useState('');
  const [busy, setBusy] = useState(false);

  // ── Session capture (recommended) — posts cookies to /session, active instantly.
  const connectSession = async () => {
    if (!email.trim()) { setMessage?.('LinkedIn email is required.'); return; }
    let cookies;
    try {
      cookies = JSON.parse(cookiesText);
    } catch {
      setMessage?.('Cookies must be valid JSON — paste the exported cookie array.');
      return;
    }
    if (!Array.isArray(cookies) || cookies.length === 0) {
      setMessage?.('Paste a non-empty JSON array of LinkedIn cookies.');
      return;
    }
    setBusy(true);
    try {
      const res = await authAxios.post(`${base}/session`, {
        linkedin_email: email.trim(),
        cookies,
        account_type: 'personal',
        brand_ids: lockedBrandId != null ? [Number(lockedBrandId)] : [],
      });
      if (res.data?.ok) { setMessage?.('LinkedIn connected.'); onDone(); return; }
      setMessage?.('Could not connect LinkedIn.');
      setBusy(false);
    } catch (e) {
      // 400 from the backend includes the reason (e.g. missing li_at cookie).
      setMessage?.(e?.response?.data?.detail || 'Could not connect LinkedIn.');
      setBusy(false);
    }
  };

  // One-click: issue a connect token and hand it to the extension directly (no
  // copy-paste). Falls back to a clear message if the extension isn't installed.
  const connectViaExtension = async () => {
    if (!email.trim()) { setMessage?.('LinkedIn email is required.'); return; }
    const rt = (typeof window !== 'undefined' && window.chrome) ? window.chrome.runtime : null;
    if (!rt?.sendMessage) { setMessage?.('Install the Pipelyt GTM extension, then try again.'); return; }
    setBusy(true);
    try {
      const tr = await authAxios.post(`${base}/connect-token`, {
        brand_id: lockedBrandId != null ? Number(lockedBrandId) : null,
      });
      const token = tr.data?.token;
      if (!token) { setMessage?.('Could not generate a connect token.'); setBusy(false); return; }
      const apiBase = (authAxios?.defaults?.baseURL || window.location.origin).replace(/\/+$/, '');
      rt.sendMessage(EXTENSION_ID, { type: 'pipelyt_connect', apiBase, token, email: email.trim() }, (resp) => {
        if (rt.lastError) { setMessage?.('Could not reach the extension — is it installed?'); setBusy(false); return; }
        if (resp?.ok) { setMessage?.('LinkedIn connected.'); onDone(); }
        else { setMessage?.(resp?.error || `Failed (${resp?.status || '?'})`); setBusy(false); }
      });
    } catch (e) {
      setMessage?.(e?.response?.data?.detail || 'Could not connect via extension.');
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[10000] bg-black/40 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl w-full max-w-md p-6" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-base font-semibold text-[#2B2926] mb-4 inline-flex items-center gap-2">
          <LinkedInIcon className="w-5 h-5" /> Connect LinkedIn
        </h3>

        {/* Two ways to connect */}
        <div className="flex gap-1 mb-4 bg-[#2B2926]/5 p-1 rounded-xl">
          <button onClick={() => setMode('extension')}
            className={`flex-1 text-[12px] font-semibold py-2 rounded-lg transition ${mode === 'extension' ? 'bg-white shadow-sm text-[#2B2926]' : 'text-[#585450]'}`}>
            Browser extension
          </button>
          <button onClick={() => setMode('paste')}
            className={`flex-1 text-[12px] font-semibold py-2 rounded-lg transition ${mode === 'paste' ? 'bg-white shadow-sm text-[#2B2926]' : 'text-[#585450]'}`}>
            Paste cookies
          </button>
          <button onClick={() => setMode('cloud')}
            className={`flex-1 text-[12px] font-semibold py-2 rounded-lg transition ${mode === 'cloud' ? 'bg-white shadow-sm text-[#2B2926]' : 'text-[#585450]'}`}>
            Secure browser
          </button>
        </div>

        {/* Shared — the LinkedIn account being connected (cloud mode has its own input) */}
        {mode !== 'cloud' && (
          <>
            <label className="block text-[13px] font-semibold text-[#2B2926] mb-1.5">LinkedIn email</label>
            <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com"
              className="w-full border border-[#2B2926]/15 rounded-lg px-3 py-2 text-sm mb-4 focus:outline-none focus:border-[#0A66C2]" />
          </>
        )}

        {mode === 'cloud' ? (
          <LinkedInCloudConnect base={base} authAxios={authAxios} lockedBrandId={lockedBrandId} setMessage={setMessage} onDone={onDone} />
        ) : mode === 'extension' ? (
          <>
            <ol className="space-y-2.5 mb-3">
              <li className="flex gap-2.5 items-start">
                <span className="flex-none w-5 h-5 rounded-full bg-[#0A66C2]/10 text-[#0A66C2] text-[11px] font-bold flex items-center justify-center mt-px">1</span>
                <span className="text-[12.5px] text-[#585450] leading-snug">Make sure you’re signed in to <span className="font-semibold text-[#2B2926]">linkedin.com</span> in this browser.</span>
              </li>
              <li className="flex gap-2.5 items-start">
                <span className="flex-none w-5 h-5 rounded-full bg-[#0A66C2]/10 text-[#0A66C2] text-[11px] font-bold flex items-center justify-center mt-px">2</span>
                <span className="text-[12.5px] text-[#585450] leading-snug">Enter your <span className="font-semibold text-[#2B2926]">LinkedIn email</span> above.</span>
              </li>
              <li className="flex gap-2.5 items-start">
                <span className="flex-none w-5 h-5 rounded-full bg-[#0A66C2]/10 text-[#0A66C2] text-[11px] font-bold flex items-center justify-center mt-px">3</span>
                <span className="text-[12.5px] text-[#585450] leading-snug">Click <span className="font-semibold text-[#2B2926]">Connect via extension</span>.</span>
              </li>
            </ol>

            <details className="mb-3 border border-[#2B2926]/12 rounded-lg bg-[#FAFAF9]">
              <summary className="cursor-pointer select-none text-[12px] font-semibold text-[#0A66C2] px-3 py-2">View full step-by-step guide</summary>
              <ol className="list-decimal pl-7 pr-3 pb-3 pt-1 space-y-1.5 text-[12px] text-[#585450] leading-snug">
                <li><a href={EXTENSION_STORE_URL} target="_blank" rel="noreferrer" className="font-semibold text-[#0A66C2] hover:underline">Install the Pipelyt GTM extension →</a> from the Chrome Web Store, then click <b>Add to Chrome</b> → <b>Add extension</b>.</li>
                <li>Open a new tab and go to <b>linkedin.com</b>. Make sure you’re signed in (you can see your feed).</li>
                <li>Come back to this Pipelyt tab.</li>
                <li>Type your <b>LinkedIn email</b> in the box above.</li>
                <li>Click <b>Connect via extension</b> below — done. The extension securely links your LinkedIn session; nothing to copy.</li>
              </ol>
            </details>

            <button disabled={busy} onClick={connectViaExtension}
              className="w-full px-3 py-2.5 rounded-lg bg-[#0A66C2] hover:bg-[#095196] text-white text-sm font-semibold transition disabled:opacity-60">
              {busy ? 'Connecting…' : 'Connect via extension'}
            </button>
          </>
        ) : (
          <>
            <ol className="space-y-2.5 mb-3">
              <li className="flex gap-2.5 items-start">
                <span className="flex-none w-5 h-5 rounded-full bg-[#0A66C2]/10 text-[#0A66C2] text-[11px] font-bold flex items-center justify-center mt-px">1</span>
                <span className="text-[12.5px] text-[#585450] leading-snug">Make sure you’re signed in to <span className="font-semibold text-[#2B2926]">linkedin.com</span>.</span>
              </li>
              <li className="flex gap-2.5 items-start">
                <span className="flex-none w-5 h-5 rounded-full bg-[#0A66C2]/10 text-[#0A66C2] text-[11px] font-bold flex items-center justify-center mt-px">2</span>
                <span className="text-[12.5px] text-[#585450] leading-snug">Open <span className="font-semibold text-[#2B2926]">Cookie-Editor</span> → <span className="font-semibold text-[#2B2926]">Export</span> → <span className="font-semibold text-[#2B2926]">Export as JSON</span> (copies your cookies).</span>
              </li>
              <li className="flex gap-2.5 items-start">
                <span className="flex-none w-5 h-5 rounded-full bg-[#0A66C2]/10 text-[#0A66C2] text-[11px] font-bold flex items-center justify-center mt-px">3</span>
                <span className="text-[12.5px] text-[#585450] leading-snug">Enter your email above, paste below, then click <span className="font-semibold text-[#2B2926]">Connect</span>.</span>
              </li>
            </ol>

            <details className="mb-3 border border-[#2B2926]/12 rounded-lg bg-[#FAFAF9]">
              <summary className="cursor-pointer select-none text-[12px] font-semibold text-[#0A66C2] px-3 py-2">View full step-by-step guide</summary>
              <ol className="list-decimal pl-7 pr-3 pb-3 pt-1 space-y-1.5 text-[12px] text-[#585450] leading-snug">
                <li>Install the free <b>Cookie-Editor</b> extension: open the Chrome Web Store, search <b>Cookie-Editor</b>, click <b>Add to Chrome</b> → <b>Add extension</b>.</li>
                <li>Open a new tab and go to <b>linkedin.com</b>. Make sure you’re signed in.</li>
                <li>Click the <b>puzzle-piece icon</b> (Extensions) at the top-right of Chrome, then click <b>Cookie-Editor</b>.</li>
                <li>In the Cookie-Editor popup, click the <b>Export</b> icon at the bottom → choose <b>Export as JSON</b>. Your cookies are now copied.</li>
                <li>Come back to this Pipelyt tab and type your <b>LinkedIn email</b> above.</li>
                <li>Click inside the box below and press <b>Ctrl + V</b> (Mac: <b>Cmd + V</b>) to paste.</li>
                <li>Click <b>Connect</b>.</li>
              </ol>
            </details>

            <textarea value={cookiesText} onChange={(e) => setCookiesText(e.target.value)}
              placeholder='[{"name":"li_at","value":"…","domain":".linkedin.com"}, …]'
              rows={4} className="w-full border border-[#2B2926]/15 rounded-lg px-3 py-2 text-xs font-mono mb-3 focus:outline-none focus:border-[#0A66C2]" />
            <button disabled={busy} onClick={connectSession}
              className="w-full px-3 py-2.5 rounded-lg bg-[#0A66C2] hover:bg-[#095196] text-white text-sm font-semibold transition disabled:opacity-60">
              {busy ? 'Connecting…' : 'Connect'}
            </button>
          </>
        )}

        <div className="flex justify-end mt-4">
          <button onClick={onClose} className="px-3 py-2 rounded-lg border border-[#2B2926]/15 text-sm text-[#585450] hover:bg-[#2B2926]/5">Close</button>
        </div>
      </div>
    </div>
  );
}

export function LinkedInIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="#0A66C2" xmlns="http://www.w3.org/2000/svg">
      <path d="M20.45 20.45h-3.56v-5.57c0-1.33-.02-3.04-1.85-3.04-1.85 0-2.13 1.45-2.13 2.94v5.67H9.35V9h3.42v1.56h.05c.48-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 5.46v6.28zM5.34 7.43a2.07 2.07 0 1 1 0-4.14 2.07 2.07 0 0 1 0 4.14zM7.12 20.45H3.55V9h3.57v11.45zM22.22 0H1.77C.79 0 0 .77 0 1.73v20.54C0 23.23.79 24 1.77 24h20.45c.98 0 1.78-.77 1.78-1.73V1.73C24 .77 23.2 0 22.22 0z" />
    </svg>
  );
}

export default LinkedInConnectModal;
