import XIcon from './icons/XIcon';

// TikTok glyph — same SVG path used in Connections.jsx so the chip/list
// rendering matches the platform-tile rendering. Inlined here (rather than
// imported from a separate file) so a fresh Vite dev server pick-up doesn't
// depend on a brand-new module landing in the HMR graph.
const TikTokIcon = ({ size = 24, className = '', color, style, ...rest }) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 24 24"
    width={size}
    height={size}
    className={className}
    style={style}
    fill={color || 'currentColor'}
    aria-hidden="true"
    {...rest}
  >
    <path d="M12.525.02c1.31-.02 2.61-.01 3.91-.01.08 1.53.63 3.09 1.75 4.17 1.12 1.11 2.7 1.62 4.24 1.79v4.03c-1.44-.06-2.89-.35-4.2-.97-.57-.26-1.1-.59-1.59-1.01-.14 3.39-.12 6.79-.12 10.18.06 2.1-.69 4.31-2.31 5.74-1.61 1.48-3.95 1.89-6.02 1.25-2.07-.63-3.75-2.58-4.14-4.63-.48-2.61.64-5.59 2.92-6.94 1.48-.88 3.3-.96 4.9-.4v4.25c-2.4-.64-5.11.75-5.38 3.23-.21 1.9 1.56 3.82 3.48 3.73 1.48.06 2.87-1 3.19-2.45.1-.38.12-.77.12-1.16-.01-5.1-.01-10.19-.01-15.29-.01-2.5 1.61-4.75 4-5.36z" />
  </svg>
);

// Single source of truth for platform logos across the whole app.
// To add a new platform everywhere, add ONE entry here (id → image in
// /public) — every list, badge, and chip that uses <PlatformLogo /> picks
// it up automatically. No more per-page edits.
//
// Fix — Pinterest was missing here even though the asset lived at
// /public/pinterest-logo.png. Every list that used <PlatformLogo /> for
// a pinterest-published post rendered null → the Published card's
// engagement row for Pinterest showed a blank chip (no logo, just
// hearts/comments). Same story for Reddit whose asset also exists.
export const PLATFORM_LOGOS = {
  linkedin:  '/linkedlin.jpg',
  facebook:  '/facebook.png',
  instagram: '/instagram.jpg',
  youtube:   '/youtube-icon.png',
  pinterest: '/pinterest-logo.png',
  reddit:    '/reddit-icon.png',
};

// Normalise common aliases (e.g. the UI shows "X" but data stores "twitter").
const normalizePlatform = (p) => {
  const k = (p || '').toLowerCase().trim();
  if (k === 'x') return 'twitter';
  return k;
};

// Render a platform's logo. Image-backed platforms use their /public asset;
// X (Twitter) has no image asset so it falls back to the vector mark.
export default function PlatformLogo({ platform, className = 'w-3.5 h-3.5', alt }) {
  const key = normalizePlatform(platform);
  const src = PLATFORM_LOGOS[key];
  if (src) {
    return <img src={src} className={`${className} object-contain`} alt={alt || key} />;
  }
  if (key === 'twitter') {
    return <XIcon className={`${className} text-[#2B2926]`} />;
  }
  if (key === 'tiktok') {
    return <TikTokIcon className={`${className} text-[#2B2926]`} />;
  }
  return null;
}
