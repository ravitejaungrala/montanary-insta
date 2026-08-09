// Generates a stable signing key for the extension so its ID NEVER changes
// (same ID on every machine + every reload, instead of a random per-load dev ID).
// Run ONCE:  node gen-key.js
//
// Outputs:
//   key.pem           — PRIVATE key. KEEP SECRET (gitignored). Back it up safely.
//   manifest-key.txt  — PUBLIC key (base64). build-manifest.js puts it in manifest "key".
// Prints the resulting extension ID — paste it into EXTENSION_ID in the frontend.
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const here = __dirname;
const pemPath = path.join(here, "key.pem");
if (fs.existsSync(pemPath)) {
  console.error("key.pem already exists — refusing to overwrite (that would change your ID). Delete it first if you really mean to.");
  process.exit(1);
}

const { publicKey, privateKey } = crypto.generateKeyPairSync("rsa", {
  modulusLength: 2048,
  publicKeyEncoding: { type: "spki", format: "der" },
  privateKeyEncoding: { type: "pkcs8", format: "pem" },
});

const pubB64 = Buffer.from(publicKey).toString("base64");

// Chrome extension ID = first 128 bits of SHA256(pubkey DER), hex digits 0-f mapped to a-p.
const hashHex = crypto.createHash("sha256").update(publicKey).digest("hex");
const extId = hashHex
  .slice(0, 32)
  .split("")
  .map((c) => String.fromCharCode(97 + parseInt(c, 16)))
  .join("");

fs.writeFileSync(pemPath, privateKey);
fs.writeFileSync(path.join(here, "manifest-key.txt"), pubB64 + "\n");

console.log("✅ Stable extension key generated.");
console.log("   Extension ID:", extId);
console.log("   → paste this into EXTENSION_ID in apps/product-page/src/pages/nexus/NexusLinkedInAccounts.jsx");
console.log("   key.pem written (PRIVATE — keep safe, it's gitignored).");
console.log("   Run `node build-manifest.js` (or npm run build) to embed the public key.");
