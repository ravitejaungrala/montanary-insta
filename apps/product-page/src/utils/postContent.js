// Shared helpers for rendering per-platform post content and timestamps.
// Previously these were copy-pasted across Drafts / Published / Scheduled /
// Calendar — four identical implementations that drifted. Centralising here
// means a bug in tz parsing or JSON extraction is fixed in one place.

/**
 * Extract the content for a specific platform from a post's content field.
 *
 * Posts can store content as either a plain string (same text for every
 * platform) or a stringified JSON map keyed by platform
 * (e.g. {"linkedin": "...", "twitter": "...", "default": "..."}).
 *
 * Falls back gracefully: platform-specific → default → first value → raw.
 */
export function getContentForPlatform(rawContent, platform = 'default') {
  if (!rawContent) return '';
  const text = typeof rawContent === 'string' ? rawContent : String(rawContent);
  if (!text.trim().startsWith('{')) return text;
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === 'object') {
      return (
        parsed[platform] ??
        parsed.default ??
        Object.values(parsed)[0] ??
        text
      );
    }
  } catch {
    // Malformed JSON — fall back to the raw string.
  }
  return text;
}

/**
 * Parse an ISO-8601 datetime string into a Date, tolerant of any variant
 * the backend might emit: 'Z' suffix, '+05:30' offset, or naive (no tz,
 * assumed UTC).
 *
 * Returns null on invalid input so callers can render a safe placeholder
 * instead of crashing.
 */
export function parseIsoDatetime(iso) {
  if (!iso) return null;
  const s = String(iso);
  // Native Date parses Z-suffixed and offset forms; a naive local-looking
  // string ("2026-04-17T12:34:56") is interpreted as local by Date — but our
  // backend always emits UTC, so we normalise by appending Z when there's no
  // explicit timezone marker.
  const hasTz = /[zZ]|[+-]\d{2}:?\d{2}$/.test(s);
  const normalised = hasTz ? s : s + 'Z';
  const d = new Date(normalised);
  return isNaN(d.getTime()) ? null : d;
}

/**
 * Convert a backend ISO string into the value format an <input type="datetime-local">
 * expects: 'YYYY-MM-DDTHH:MM' in the given IANA timezone (defaults to UTC).
 *
 * Replaces the brittle `iso.split('Z')[0]` pattern that broke the moment the
 * backend emitted an offset-suffixed string.
 */
export function isoToDatetimeLocalInput(iso, timezone) {
  const d = parseIsoDatetime(iso);
  if (!d) return '';
  try {
    // Build a YYYY-MM-DDTHH:MM string in the target tz using Intl.
    const tz = timezone || 'UTC';
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: tz,
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false,
    }).formatToParts(d).reduce((acc, p) => {
      acc[p.type] = p.value;
      return acc;
    }, {});
    return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
  } catch {
    // Fallback to UTC if the tz is unknown.
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
  }
}

/**
 * Convert a value from <input type="datetime-local"> (which is timezone-naive
 * wall-clock time) back into an ISO-8601 UTC string, interpreting the wall
 * clock as being IN the supplied timezone.
 *
 * Example: user types "2026-05-01T09:00" with timezone="Asia/Kolkata" →
 * returns "2026-05-01T03:30:00.000Z".
 *
 * This replaces `new Date(datetimeLocalValue).toISOString()` which silently
 * assumes the browser's local timezone, producing wrong times for users whose
 * browser clock doesn't match their chosen app timezone.
 */
export function datetimeLocalInputToIso(value, timezone) {
  if (!value) return null;
  const tz = timezone || 'UTC';

  // Interpret the naive wall-clock value as being in `tz` by iteratively
  // correcting the offset. Intl gives us the offset for any Date in any tz,
  // but we don't know the correct instant yet — one iteration is enough
  // except near DST transitions where two iterations stabilise the result.
  let guess = new Date(value + 'Z'); // first guess: treat as UTC
  for (let i = 0; i < 2; i++) {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: tz,
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    }).formatToParts(guess).reduce((acc, p) => { acc[p.type] = p.value; return acc; }, {});
    // Intl "24" hour hack: en-US returns "24" for midnight-end; normalise.
    if (parts.hour === '24') parts.hour = '00';
    const inTz = `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}Z`;
    const drift = new Date(inTz).getTime() - new Date(value + ':00Z').getTime();
    guess = new Date(guess.getTime() - drift);
  }
  return guess.toISOString();
}


// L1 fix — was duplicated in App.jsx (handlePublish) and Posts.jsx
// (handleSchedule). Both branches must stay in lock-step or the publish
// path silently drops the local upload while the schedule path keeps it,
// so extracting to one function prevents future drift.
//
// Rule: prefer base64Image (local upload the backend will decode + push to
// S3 at fire time) → then imagePreview if it's a fully-qualified http(s)
// URL (already-hosted from Canva or a previous S3 upload) → else null.
// A blob: URL from URL.createObjectURL is NOT valid to send server-side.
export function resolveMediaUrl(imagePreview, base64Image) {
  if (base64Image) return base64Image;
  if (imagePreview && (imagePreview.startsWith('http://') || imagePreview.startsWith('https://'))) {
    return imagePreview;
  }
  return null;
}


// H3 fix — FastAPI validation errors come back as
//   { detail: [ { loc: ['body','field'], msg: '...', type: '...' }, ... ] }
// Simple string interpolation renders arrays containing objects as
// "[object Object]". This normaliser turns any detail shape into a
// human-readable string so the user sees the real reason.
export function formatApiError(detail, fallback = 'Something went wrong') {
  if (!detail) return fallback;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map(d => (typeof d === 'string' ? d : (d?.msg || d?.detail || null)))
      .filter(Boolean);
    return msgs.length ? msgs.join('; ') : fallback;
  }
  if (typeof detail === 'object') {
    return detail.msg || detail.message || fallback;
  }
  return String(detail) || fallback;
}
