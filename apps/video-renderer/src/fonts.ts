/**
 * Centralised font loading.
 *
 * Importing this module from any composition once is enough — the load
 * promise resolves before Remotion measures the first frame, so every
 * <Text> tag using `OSWALD` will render at its proper weight without a
 * fallback flash.
 *
 * Brand Promo v2 uses Oswald 800 for headlines (condensed, bold,
 * keynote-grade) and falls back to system-ui for body copy.
 */
import { loadFont as loadOswald } from '@remotion/google-fonts/Oswald';

const oswald = loadOswald('normal', { weights: ['600', '700'] });

/** Use this in inline styles: fontFamily: OSWALD */
export const OSWALD = oswald.fontFamily;

/** Body / sans fallback — system stack for cross-platform consistency. */
export const SANS = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
