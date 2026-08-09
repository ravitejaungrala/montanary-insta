import {
  AbsoluteFill, Img, useCurrentFrame, useVideoConfig,
  spring, interpolate, continueRender, delayRender,
} from 'remotion';
import { useEffect, useState } from 'react';
import type { BrandTokens } from '../storyboard-types';
import { OSWALD, SANS } from '../fonts';
import { ParticleBurst } from './ParticleBurst';

/**
 * Beat 1 — Logo Intro (2 seconds).
 *
 * Quick, confident brand stamp. Logo scales in with a spring overshoot,
 * brand name lands, tagline fades in. Single particle burst on landing.
 *
 * This is intentionally short — by the user's spec, the brand should
 * be on screen 0–2s but doesn't need to dominate. Beats 2 and 3 carry
 * the actual story.
 */
const LetterMarkFallback: React.FC<{ brand: BrandTokens; size: number }> = ({ brand, size }) => {
  const letter = (brand.company_name || 'P').trim().charAt(0).toUpperCase();
  return (
    <div
      style={{
        width: size, height: size,
        borderRadius: 24,
        background: brand.primary_color,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        boxShadow: `0 12px 32px ${brand.primary_color}55`,
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

export const LogoIntro: React.FC<{ brand: BrandTokens }> = ({ brand }) => {
  const frame = useCurrentFrame();
  const { fps, width } = useVideoConfig();

  const logoScale = spring({
    frame, fps,
    config: { damping: 11, stiffness: 160 },
    from: 0.5, to: 1,
  });
  const logoOpacity = interpolate(frame, [0, 6], [0, 1], { extrapolateRight: 'clamp' });

  const nameOpacity = interpolate(frame, [10, 20], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const nameOffset = interpolate(frame, [10, 20], [30, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });

  const taglineOpacity = interpolate(frame, [20, 32], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });

  const logoSize = width * 0.32;
  const logoState = useLogoLoadable(brand.logo_url);

  return (
    <AbsoluteFill
      style={{
        justifyContent: 'center',
        alignItems: 'center',
        flexDirection: 'column',
        background: brand.background_color || '#ffffff',
      }}
    >
      <ParticleBurst
        color={brand.primary_color}
        startFrame={4}
        durationFrames={20}
        radius={width * 0.3}
        size={10}
        count={24}
      />

      <div
        style={{
          width: logoSize,
          height: logoSize,
          transform: `scale(${logoScale})`,
          opacity: logoOpacity,
          filter: 'drop-shadow(0 14px 36px rgba(15,23,42,0.18))',
          marginBottom: 40,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
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
          fontSize: width * 0.085,
          color: brand.text_color || '#0f172a',
          letterSpacing: '-0.01em',
          textTransform: 'uppercase',
          opacity: nameOpacity,
          transform: `translateY(${nameOffset}px)`,
          textAlign: 'center',
          padding: '0 64px',
          lineHeight: 1,
        }}
      >
        {brand.company_name}
      </div>

      {brand.tagline && (
        <div
          style={{
            fontFamily: SANS,
            fontWeight: 700,
            fontSize: width * 0.026,
            color: brand.primary_color,
            letterSpacing: '0.06em',
            textAlign: 'center',
            marginTop: 16,
            opacity: taglineOpacity,
            padding: '0 64px',
            maxWidth: width * 0.85,
          }}
        >
          {brand.tagline}
        </div>
      )}
    </AbsoluteFill>
  );
};
