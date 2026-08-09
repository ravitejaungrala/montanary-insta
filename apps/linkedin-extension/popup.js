// Pipelyt GTM LinkedIn connector — reads the customer's already-logged-in LinkedIn
// cookies (in THEIR browser, on THEIR IP) and posts them to the Pipelyt backend,
// which stores them encrypted. No password is ever handled. The human already
// solved any 2FA/CAPTCHA by logging in normally — so the cloud never has to.

const $ = (id) => document.getElementById(id);
// Extension uses the connect-token auth path (cross-origin, no app session cookie).
const SESSION_PATH = "/nexus/linkedin-agent/session/token";

// Restore saved settings (API URL + token persist; email is per-account).
chrome.storage.local.get(["apiBase", "token", "email"], (s) => {
  if (s.apiBase) $("apiBase").value = s.apiBase;
  if (s.token) $("token").value = s.token;
  if (s.email) $("email").value = s.email;
});

function setStatus(msg, cls) {
  const el = $("status");
  el.textContent = msg;
  el.className = "status " + (cls || "muted");
}

$("connect").addEventListener("click", async () => {
  const apiBase = $("apiBase").value.trim().replace(/\/+$/, "");
  const token = $("token").value.trim();
  const email = $("email").value.trim().toLowerCase();

  if (!apiBase || !token) return setStatus("Open Settings and enter the API URL + connect token.", "err");
  if (!email) return setStatus("Enter your LinkedIn email.", "err");

  // Persist settings for next time.
  chrome.storage.local.set({ apiBase, token, email });

  $("connect").disabled = true;
  setStatus("Reading your LinkedIn session…", "muted");

  try {
    // Read every linkedin.com cookie from THIS browser. The backend normalizes
    // them and requires `li_at` (the auth cookie) — so this only works if the
    // user is actually logged in.
    const cookies = await chrome.cookies.getAll({ domain: "linkedin.com" });
    if (!cookies.some((c) => c.name === "li_at")) {
      $("connect").disabled = false;
      return setStatus("Not logged in. Open linkedin.com, sign in, then try again.", "err");
    }

    setStatus("Connecting to Pipelyt…", "muted");
    const res = await fetch(apiBase + SESSION_PATH, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + token },
      body: JSON.stringify({ linkedin_email: email, cookies }),
    });

    if (res.ok) {
      setStatus("✅ Connected! You can close this and return to Pipelyt.", "ok");
    } else {
      const body = await res.text();
      setStatus(`Failed (${res.status}): ${body.slice(0, 160)}`, "err");
    }
  } catch (e) {
    setStatus("Error: " + (e && e.message ? e.message : String(e)), "err");
  } finally {
    $("connect").disabled = false;
  }
});
