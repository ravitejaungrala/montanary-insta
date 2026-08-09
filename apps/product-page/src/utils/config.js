/**
 * Central runtime config for the product-page app.
 *
 * All environment-dependent URLs live here so we never hard-code per-env
 * values in components. Set these via Vite env vars in each Amplify app:
 *
 *   Production Amplify app
 *     VITE_API_URL=https://api.pipelyt.ai
 *     VITE_LANDING_URL=https://pipelyt.ai
 *
 *   Staging Amplify app
 *     VITE_API_URL=https://staging.api.pipelyt.ai
 *     VITE_LANDING_URL=https://staging.pipelyt.ai
 *
 * Local dev reads from `.env` / `.env.local` (both gitignored).
 */

const env = import.meta.env;

const stripTrailingSlash = (url) => (url ? url.replace(/\/+$/, "") : url);

// ---- API (backend Lambda) ----------------------------------------------------
// In dev, fall back to the local uvicorn port. In prod builds we REFUSE to fall
// back silently — an unset VITE_API_URL in production would quietly point the
// app at localhost and break every request, which is the exact kind of failure
// that should be loud.
const FALLBACK_API_URL = env.DEV ? "http://localhost:8000" : "";
export const API_BASE_URL = stripTrailingSlash(env.VITE_API_URL) || FALLBACK_API_URL;

if (env.PROD && !API_BASE_URL) {
  // eslint-disable-next-line no-console
  console.error(
    "[config] VITE_API_URL is not set for this production build. " +
      "All API calls will fail. Set it in the Amplify console env vars."
  );
}

// ---- Landing page (marketing site) ------------------------------------------
// Used for logo/home links inside the product app. Defaults to production so
// staging environments must explicitly opt into the staging landing URL.
export const LANDING_URL =
  stripTrailingSlash(env.VITE_LANDING_URL) || "https://pipelyt.ai";

// Convenience: a pre-trailing-slashed href, since most anchor tags want one.
export const LANDING_HOME_HREF = `${LANDING_URL}/`;

// ---- Enterprise Booking URL -------------------------------------------------
export const ENTERPRISE_BOOKING_URL =
  env.VITE_ENTERPRISE_BOOKING_URL || "https://bookings.cloud.microsoft/book/PipelytMeetings@neuzenai.com/?ismsaljsauthenabled=true";

// ---- Nexus feature flag -----------------------------------------------------
// Mirrors the backend's NEXUS_ENABLED env var (see
// apps/backend/core/config.py). While false (default), the Nexus surface
// is hidden from the sidebar and its placeholder page renders nothing.
// Flip to "true" on a staging Amplify app once Phase 1+ slices land —
// see docs/nexus-migration/PHASE_PLAN.md.
export const NEXUS_ENABLED = false;
