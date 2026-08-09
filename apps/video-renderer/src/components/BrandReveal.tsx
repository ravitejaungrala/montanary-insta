import {
  AbsoluteFill, Img, useCurrentFrame, useVideoConfig,
  spring, interpolate, continueRender, delayRender,
} from 'remotion';
import { useEffect, useState } from 'react';
import type { BrandTokens } from '../storyboard-types';
import { OSWALD, SANS } from '../fonts';

/**
 * Beat 3 — Brand Reveal (2s).
 *
 * Subtle, fast brand stamp AFTER attention has been earned. NOT the
 * big logo title card from v2 — this is a horizontal lockup that
 * slides in from the side, holds for ~1.2s, then wipes out.
 *
 * Choreography (60 frames):
 *   0-12f    logo + name slide in from left as a unit
 *   12-22f   tagline fades in below
 *   22-60f   sustains
 */
const LetterMarkFallback: React.FC<{ brand: BrandTokens; size: number }> = ({ brand, size }) => {
  const letter = (brand.company_name || 'P').trim().charAt(0).toUpperCase();
  return (
    <div
      style={{
        width: size, height: size,
        borderRadius: 18,
        background: brand.primary_color,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        boxShadow: `0 12px 28px ${brand.primary_color}55`,
        fontFamily: OSWALD,
        fontWeight: 700,
        color: '#ffffff',
        fontSize: size * 0.6,
      }}
    >
      {letter}
    </div>
  );
};

const useLogoLoadable = (url: string | undefined): 'loading' | 'ok' | 'fail' => {
  const [state, setState] = useState<'loading' | 'ok' | 'fail'>('loading');
  useEffect(() => {
    if (!url) { setState('fail'); return; }
    const handle = delayRender(`probing logo ${url}`);
    const img = new Image();
    img.onload = () => { setState('ok'); continueRender(handle); };
    img.onerror = () => { setState('fail'); continueRender(handle); };
    img.src = url;
  }, [url]);
  return state;
};

export const BrandReveal: React.FC<{ brand: BrandTokens }> = ({ brand }) => {
  const frame = useCurrentFrame();
  const { fps, width } = useVideoConfig();

  const slide = spring({
    frame, fps,
    config: { damping: 14, stiffness: 130 },
    from: -100, to: 0,
  });
  const opacity = interpolate(frame, [0, 8], [0, 1], { extrapolateRight: 'clamp' });
  const taglineOpacity = interpolate(frame, [12, 22], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });

  const logoSize = width * 0.18;
  const logoState = useLogoLoadable(brand.logo_url);

  return (
    <AbsoluteFill
      style={{
        justifyContent: 'center',
        alignItems: 'center',
        flexDirection: 'column',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 24,
          opacity,
          transform: `translateX(${slide}px)`,
        }}
      >
        <div
          style={{
            width: logoSize,
            height: logoSize,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            filter: 'drop-shadow(0 10px 24px rgba(15,23,42,0.18))',
          }}
        >
          {logoState === 'ok'
            ? <Img src={brand.logo_url} style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
            : <LetterMarkFallback brand={brand} size={logoSize} />}
        </div>
        <div
          style={{
            fontFamily: OSWALD,
            fontWeight: 700,
            fontSize: width * 0.075,
            color: brand.text_color || '#0f172a',
            letterSpacing: '-0.01em',
            textTransform: 'uppercase',
            lineHeight: 1,
          }}
        >
          {brand.company_name}
        </div>
      </div>

      {brand.tagline && (
        <div
          style={{
            fontFamily: SANS,
            fontWeight: 700,
            fontSize: width * 0.024,
            color: brand.primary_color,
            letterSpacing: '0.06em',
            textTransform: 'uppercase',
            marginTop: 24,
            opacity: taglineOpacity,
            textAlign: 'center',
            padding: '0 64px',
            maxWidth: width * 0.8,
          }}
        >
          {brand.tagline}
        </div>
      )}
    </AbsoluteFill>
  );
};
