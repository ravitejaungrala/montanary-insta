import {
  AbsoluteFill, Img, useCurrentFrame, useVideoConfig,
  interpolate, spring, continueRender, delayRender,
} from 'remotion';
import { useEffect, useState } from 'react';
import type { BrandTokens, SolutionAvatarScene } from '../storyboard-types';
import { OSWALD, SANS } from '../fonts';
import { ParticleBurst } from './ParticleBurst';

/**
 * Beat 3 — Solution Avatar.
 *
 * Different person from beat 2. The expert / confident user persona who
 * explains the product. Brand-coloured scrim instead of red. White flash
 * at scene start to mark the transition from problem → solution.
 *
 * Quote text in the upper-mid area (so the face stays visible in the
 * 9:16 frame). After the quote lands, an optional `proof` line slides
 * in below in the brand colour as a final emphasis beat.
 */
const useImageLoadable = (url: string | undefined): 'loading' | 'ok' | 'fail' => {
  const [state, setState] = useState<'loading' | 'ok' | 'fail'>(url ? 'loading' : 'fail');
  useEffect(() => {
    if (!url) { setState('fail'); return; }
    const handle = delayRender(`avatar img ${url}`);
    const img = new Image();
    img.onload = () => { setState('ok'); continueRender(handle); };
    img.onerror = () => { setState('fail'); continueRender(handle); };
    img.src = url;
  }, [url]);
  return state;
};

export const SolutionAvatar: React.FC<{
  scene: SolutionAvatarScene;
  brand: BrandTokens;
}> = ({ scene, brand }) => {
  const frame = useCurrentFrame();
  const { fps, width, durationInFrames } = useVideoConfig();
  const accent = brand.primary_color;
  const txt = '#ffffff';

  const imgState = useImageLoadable(scene.avatar_url);

  // Opening flash — lands at frames 0-2, then fades over 6 frames
  const flashOpacity = interpolate(frame, [0, 2, 8], [1, 1, 0], { extrapolateRight: 'clamp' });

  // Slow zoom-out (1.08 → 1.0) — counter-intuitive but gives the sense
  // of "stepping back to take in the answer"
  const zoom = interpolate(frame, [0, durationInFrames], [1.08, 1.0], { extrapolateRight: 'clamp' });

  // Label
  const labelOpacity = interpolate(frame, [10, 22], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const labelOffset = interpolate(frame, [10, 22], [30, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });

  // Quote line entrance
  const lines = splitIntoLines(scene.quote, 6);
  const lineEntrance = (i: number) => {
    const start = 28 + i * 14;
    return {
      opacity: interpolate(frame, [start, start + 16], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }),
      scale: spring({
        frame: frame - start, fps,
        config: { damping: 14, stiffness: 130 },
        from: 0.92, to: 1,
      }),
    };
  };

  // Proof beat — slides in late
  const proofStart = 28 + lines.length * 14 + 20;
  const proofOpacity = interpolate(frame, [proofStart, proofStart + 16], [0, 1], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });
  const proofOffset = interpolate(frame, [proofStart, proofStart + 16], [24, 0], {
    extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill style={{ overflow: 'hidden', background: '#0f172a' }}>
      {/* Photo background */}
      {imgState === 'ok' ? (
        <Img
          src={scene.avatar_url!}
          style={{
            position: 'absolute',
            inset: 0,
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            transform: `scale(${zoom})`,
            transformOrigin: 'center 35%',
          }}
        />
      ) : (
        <AbsoluteFill
          style={{
            background:
              `radial-gradient(circle at 50% 30%, ${accent}cc 0%, ${accent}66 50%, #0f172a 100%)`,
          }}
        />
      )}

      {/* Brand-colour mood scrim */}
      <AbsoluteFill style={{ background: accent, opacity: 0.12, mixBlendMode: 'multiply' }} />

      {/* Top gradient for upper text legibility */}
      <AbsoluteFill
        style={{
          background:
            'linear-gradient(to bottom, rgba(0,0,0,0.65) 0%, rgba(0,0,0,0.25) 35%, rgba(0,0,0,0) 60%)',
        }}
      />

      {/* Content — anchored at top 50% of frame */}
      <AbsoluteFill
        style={{
          flexDirection: 'column',
          justifyContent: 'flex-start',
          padding: '14% 64px 0 64px',
          alignItems: 'flex-start',
        }}
      >
        <div
          style={{
            fontFamily: OSWALD,
            fontWeight: 700,
            fontSize: width * 0.026,
            color: accent,
            letterSpacing: '0.22em',
            textTransform: 'uppercase',
            marginBottom: 20,
            display: 'inline-flex',
            alignItems: 'center',
            gap: 12,
            opacity: labelOpacity,
            transform: `translateX(${labelOffset}px)`,
            textShadow: '0 2px 8px rgba(0,0,0,0.4)',
          }}
        >
          <span style={{ fontSize: width * 0.036 }}>✓</span>
          {scene.label || 'The Solution'}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {lines.map((line, i) => {
            const { opacity, scale } = lineEntrance(i);
            return (
              <div
                key={i}
                style={{
                  fontFamily: OSWALD,
                  fontWeight: 700,
                  fontSize: width * 0.062,
                  color: txt,
                  letterSpacing: '-0.01em',
                  lineHeight: 1.1,
                  textTransform: 'uppercase',
                  opacity,
                  transform: `scale(${scale})`,
                  transformOrigin: 'left center',
                  textShadow: '0 4px 24px rgba(0,0,0,0.5)',
                }}
              >
                {line}
              </div>
            );
          })}
        </div>

        {scene.proof && (
          <div
            style={{
              marginTop: 28,
              padding: '10px 18px',
              background: accent,
              borderRadius: 8,
              fontFamily: OSWALD,
              fontWeight: 700,
              fontSize: width * 0.034,
              color: '#ffffff',
              letterSpacing: '0.04em',
              textTransform: 'uppercase',
              opacity: proofOpacity,
              transform: `translateY(${proofOffset}px)`,
              boxShadow: `0 12px 28px ${accent}55`,
            }}
          >
            {scene.proof}
          </div>
        )}
      </AbsoluteFill>

      {/* Particle burst on the flash */}
      <ParticleBurst
        color={accent}
        startFrame={2}
        durationFrames={28}
        radius={width * 0.4}
        size={12}
        count={28}
      />

      {/* Opening white flash — covers the cut from beat 2 */}
      <AbsoluteFill style={{ background: '#ffffff', opacity: flashOpacity, pointerEvents: 'none' }} />
    </AbsoluteFill>
  );
};

function splitIntoLines(text: string, maxWords: number): string[] {
  const words = text.split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  for (let i = 0; i < words.length; i += maxWords) {
    lines.push(words.slice(i, i + maxWords).join(' '));
  }
  return lines;
}
