// Auto-handoff path: the Pipelyt GTM web app sends the connect token directly to
// this extension via chrome.runtime.sendMessage(extensionId, …). No copy-paste. The
// extension reads the customer's LinkedIn cookies (their browser, their IP) and
// posts them to the backend — same endpoint the popup uses.

const SESSION_PATH = "/nexus/linkedin-agent/session/token";

async function captureAndSend({ apiBase, token, email }) {
  if (!apiBase || !token || !email) {
    return { ok: false, error: "Missing apiBase, token, or email." };
  }
  const cookies = await chrome.cookies.getAll({ domain: "linkedin.com" });
  if (!cookies.some((c) => c.name === "li_at")) {
    return { ok: false, error: "Not logged into LinkedIn in this browser." };
  }
  const res = await fetch(apiBase.replace(/\/+$/, "") + SESSION_PATH, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + token },
    body: JSON.stringify({ linkedin_email: email, cookies }),
  });
  if (res.ok) return { ok: true };
  return { ok: false, status: res.status, error: (await res.text()).slice(0, 200) };
}

// Messages from the Pipelyt GTM web app (origin allowed via manifest externally_connectable).
chrome.runtime.onMessageExternal.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.type !== "pipelyt_connect") return;
  captureAndSend(msg).then(sendResponse).catch((e) => sendResponse({ ok: false, error: String(e) }));
  return true; // keep the channel open for the async response
});

// Messages from our own popup (manual fallback) reuse the same capture logic.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.type !== "pipelyt_connect") return;
  captureAndSend(msg).then(sendResponse).catch((e) => sendResponse({ ok: false, error: String(e) }));
  return true;
});
