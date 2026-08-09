import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate } from 'remotion';
import type { BrandTokens } from '../storyboard-types';

/**
 * v2 background.
 *
 * Three layers stacked back-to-front:
 *   1. Solid base colour (white by default)
 *   2. Two slowly-rotating tinted blobs (kept from v1)
 *   3. NEW — a faint diagonal grid at 4% opacity gives editorial texture
 *      that competes with the foreground only when you stare for it
 *
 * Background runs UNINTERRUPTED across all 5 scenes — Remotion's
 * <Series> resets per-scene component state but a parent <AbsoluteFill>
 * sibling stays continuous, so the gradient drift carries through cuts.
 */
export const AnimatedBackground: React.FC<{ brand: BrandTokens }> = ({ brand }) => {
  const frame = useCurrentFrame();
  const { width, height, durationInFrames } = useVideoConfig();

  const bg = brand.background_color || '#ffffff';
  const accent = brand.primary_color;
  const secondary = brand.secondary_color || '#0f172a';

  // Slow, full-video rotation so motion never feels jittery.
  const rotation = interpolate(frame, [0, durationInFrames], [0, 360], {
    extrapolateRight: 'clamp',
  });
  const drift = interpolate(frame, [0, durationInFrames], [0, height * 0.08], {
    extrapolateRight: 'clamp',
  });

  // Subtle diagonal grid built from a CSS gradient — no images, no SVG.
  // 4% opacity means it reads as "texture" not "graphic".
  const gridStyle: React.CSSProperties = {
    backgroundImage:
      `linear-gradient(45deg, ${secondary}0a 1px, transparent 1px), ` +
      `linear-gradient(-45deg, ${secondary}0a 1px, transparent 1px)`,
    backgroundSize: '40px 40px',
  };

  return (
    <AbsoluteFill style={{ backgroundColor: bg, overflow: 'hidden' }}>
      {/* Texture grid */}
      <AbsoluteFill style={{ ...gridStyle, opacity: 0.6 }} />

      {/* Big tinted blob top-left, anchored off-canvas so we just see the curve */}
      <div
        style={{
          position: 'absolute',
          left: -width * 0.3,
          top: -height * 0.15,
          width: width * 1.1,
          height: width * 1.1,
          borderRadius: '50%',
          background: accent,
          opacity: 0.09,
          filter: 'blur(60px)',
          transform: `rotate(${rotation}deg)`,
        }}
      />
      {/* Smaller darker blob bottom-right for visual balance */}
      <div
        style={{
          position: 'absolute',
          right: -width * 0.25,
          bottom: -height * 0.2 + drift,
          width: width * 0.9,
          height: width * 0.9,
          borderRadius: '50%',
          background: secondary,
          opacity: 0.06,
          filter: 'blur(80px)',
          transform: `rotate(${-rotation}deg)`,
        }}
      />
    </AbsoluteFill>
  );
};
