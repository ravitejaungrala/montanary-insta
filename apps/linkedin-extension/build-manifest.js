// Generates manifest.json from the URLs in your .env files, so the extension's
// host_permissions (API) and externally_connectable (web app) always match your
// environment — no hand-editing. Re-run after changing .env (local ↔ prod):
//
//   node build-manifest.js
//
// Reads:
//   VITE_API_URL  from apps/product-page/.env   → host_permissions (backend API)
//   FRONTEND_URL  from apps/backend/.env         → externally_connectable (web app)
const fs = require("fs");
const path = require("path");

function readEnv(file, key) {
  try {
    for (const line of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
      const t = line.trim();
      if (!t || t.startsWith("#")) continue;          // skip blanks + comments
      const i = t.indexOf("=");
      if (i === -1) continue;
      if (t.slice(0, i).trim() === key) {
        return t.slice(i + 1).trim().replace(/^["']|["']$/g, "");
      }
    }
  } catch (e) {
    /* file missing — handled below */
  }
  return null;
}

// "http://localhost:8000" → "http://localhost:8000/*"  (origin + /*)
function originPattern(url) {
  if (!url) return null;
  try {
    const u = new URL(url);
    return `${u.protocol}//${u.host}/*`; // host includes :port when present
  } catch {
    return null;
  }
}

const here = __dirname;
const LINKEDIN = "https://*.linkedin.com/";

// PROD targets (used with `--prod`) — customers only ever hit production.
// externally_connectable covers the apex + any subdomain (app.pipelyt.ai etc.);
// host_permissions includes the prod Lambda API plus a *.pipelyt.ai wildcard so
// the extension can reach the API whether it's the Lambda URL or a custom domain.
const PROD = {
  appPatterns: ["https://pipelyt.ai/*", "https://*.pipelyt.ai/*"],
  apiPatterns: [
    "https://vvghzrx6ss7b3w5jjaa5nmpguy0kvwrq.lambda-url.us-east-1.on.aws/*",
    "https://*.pipelyt.ai/*",
  ],
};

const useProd = process.argv.includes("--prod");
let apiPatterns, appPatterns;
if (useProd) {
  apiPatterns = PROD.apiPatterns;
  appPatterns = PROD.appPatterns;
} else {
  const apiUrl = readEnv(path.join(here, "..", "product-page", ".env"), "VITE_API_URL");
  const appUrl = readEnv(path.join(here, "..", "backend", ".env"), "FRONTEND_URL");
  apiPatterns = [originPattern(apiUrl)];
  appPatterns = [originPattern(appUrl)];
  if (!apiPatterns[0] || !appPatterns[0]) {
    console.error("ERROR: could not read VITE_API_URL (product-page/.env) and/or FRONTEND_URL (backend/.env).");
    console.error("  VITE_API_URL =", apiUrl, "| FRONTEND_URL =", appUrl);
    process.exit(1);
  }
}

const manifest = {
  manifest_version: 3,
  name: "Pipelyt GTM — LinkedIn Connector",
  version: "1.0.0",
  description:
    "Securely connect your LinkedIn account to Pipelyt GTM for outreach automation. Captures your existing logged-in session — your password is never shared.",
  permissions: ["cookies", "storage"],
  host_permissions: [LINKEDIN, ...apiPatterns],
  background: { service_worker: "background.js" },
  externally_connectable: { matches: appPatterns },
  icons: { 16: "icon16.png", 48: "icon48.png", 128: "icon128.png" },
  action: {
    default_popup: "popup.html",
    default_title: "Connect LinkedIn to Pipelyt GTM",
    default_icon: { 16: "icon16.png", 48: "icon48.png", 128: "icon128.png" },
  },
};

// Embed the stable public key (from gen-key.js) so the extension ID is fixed
// across machines/reloads. Optional — without it the ID is random per dev load.
const keyPath = path.join(here, "manifest-key.txt");
if (fs.existsSync(keyPath)) {
  manifest.key = fs.readFileSync(keyPath, "utf8").trim();
}

fs.writeFileSync(path.join(here, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
console.log(`✅ manifest.json generated (${useProd ? "PROD" : "LOCAL from .env"}):`);
console.log("   host_permissions (API)      →", apiPatterns.join(", "));
console.log("   externally_connectable (app) →", appPatterns.join(", "));
console.log("   stable key embedded          →", fs.existsSync(keyPath) ? "yes" : "no (run gen-key.js)");
