import {
  AbsoluteFill, Img, useCurrentFrame, useVideoConfig,
  interpolate, spring, continueRender, delayRender,
} from 'remotion';
import { useEffect, useState } from 'react';
import type { BrandTokens, PainAvatarScene } from '../storyboard-types';
import { OSWALD, SANS } from '../fonts';

/**
 * Beat 2 — Pain Avatar.
 *
 * Full-bleed photo of a person struggling with the problem. Heavy red
 * scrim sets the mood (frustration, urgency). The character's "quote"
 * overlays in the lower 40% with a dark gradient under it for legibility.
 *
 * Subtle Ken-Burns zoom on the photo so it doesn't feel static during
 * the 8-second hold — slow zoom-in (1.0 → 1.06) works for photos with
 * a face in centre frame.
 *
 * If `avatar_url` is missing or fails to load, falls back to a styled
 * red gradient backdrop so the video still ships even when image gen
 * is unavailable.
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

export const PainAvatar: React.FC<{
  scene: PainAvatarScene;
  brand: BrandTokens;
}> = ({ scene, brand }) => {
  const frame = useCurrentFrame();
  const { fps, width, durationInFrames } = useVideoConfig();
  const txt = '#ffffff';

  const imgState = useImageLoadable(scene.avatar_url);

  // Slow Ken-Burns zoom-in across the entire scene
  const zoom = interpolate(frame, [0, durationInFrames], [1.0, 1.06], { extrapolateRight: 'clamp' });

  // Label fade-in
  const labelOpacity = interpolate(frame, [6, 18], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const labelOffset = interpolate(frame, [6, 18], [-30, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });

  // Quote — line-by-line reveal so the eye can read at a comfortable pace
  const lines = splitIntoLines(scene.quote, 6);
  const lineEntrance = (i: number) => {
    const start = 24 + i * 12;
    return {
      opacity: interpolate(frame, [start, start + 14], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }),
      offset: interpolate(frame, [start, start + 14], [22, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }),
    };
  };

  return (
    <AbsoluteFill style={{ background: '#1a0a0a', overflow: 'hidden' }}>
      {/* Photo background — full-bleed with Ken-Burns zoom */}
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
            transformOrigin: 'center 40%',
          }}
        />
      ) : (
        // Fallback gradient — radial dark-red so the framing still feels
        // intentional even with no real photo.
        <AbsoluteFill
          style={{
            background:
              'radial-gradient(circle at 50% 35%, #7f1d1d 0%, #450a0a 60%, #1f0606 100%)',
          }}
        />
      )}

      {/* Red mood scrim — heavier than v3, sets unmistakable "problem" mood */}
      <AbsoluteFill style={{ background: '#ef4444', opacity: 0.18, mixBlendMode: 'multiply' }} />

      {/* Bottom gradient for text legibility */}
      <AbsoluteFill
        style={{
          background:
            'linear-gradient(to top, rgba(0,0,0,0.85) 0%, rgba(0,0,0,0.55) 30%, rgba(0,0,0,0) 65%)',
        }}
      />

      {/* Content — anchored at bottom 40% of frame */}
      <AbsoluteFill
        style={{
          flexDirection: 'column',
          justifyContent: 'flex-end',
          padding: '0 64px 12% 64px',
          alignItems: 'flex-start',
        }}
      >
        <div
          style={{
            fontFamily: OSWALD,
            fontWeight: 700,
            fontSize: width * 0.026,
            color: '#fca5a5',
            letterSpacing: '0.22em',
            textTransform: 'uppercase',
            marginBottom: 18,
            display: 'inline-flex',
            alignItems: 'center',
            gap: 12,
            opacity: labelOpacity,
            transform: `translateX(${labelOffset}px)`,
          }}
        >
          <span style={{ fontSize: width * 0.034 }}>✕</span>
          {scene.label || 'The Problem'}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {lines.map((line, i) => {
            const { opacity, offset } = lineEntrance(i);
            return (
              <div
                key={i}
                style={{
                  fontFamily: OSWALD,
                  fontWeight: 700,
                  fontSize: width * 0.06,
                  color: txt,
                  letterSpacing: '-0.01em',
                  lineHeight: 1.1,
                  textTransform: 'uppercase',
                  opacity,
                  transform: `translateY(${offset}px)`,
                  textShadow: '0 4px 20px rgba(0,0,0,0.5)',
                }}
              >
                {line}
              </div>
            );
          })}
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

/* Greedy line-breaker so the quote stacks into lines of <= maxWords each.
   Keeps phrasing intuitive (no random mid-clause breaks) but ensures the
   text fits the lower-third overlay area. */
function splitIntoLines(text: string, maxWords: number): string[] {
  const words = text.split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  for (let i = 0; i < words.length; i += maxWords) {
    lines.push(words.slice(i, i + maxWords).join(' '));
  }
  return lines;
}
