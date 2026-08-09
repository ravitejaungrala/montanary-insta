import { AbsoluteFill, useCurrentFrame, useVideoConfig, spring, interpolate } from 'remotion';
import type { BrandTokens, CTACardScene } from '../storyboard-types';
import { OSWALD } from '../fonts';
import { ShapeUnderline } from './ShapeUnderline';
import { ParticleBurst } from './ParticleBurst';

/**
 * Scene 5 v2 — CTA with chevron flank, typewriter URL, particle burst.
 *
 * Choreography:
 *   0–14f   headline lifts up + fades in
 *   14–28f  pill spring-lands with a particle burst behind it
 *   28+     pill keeps a gentle pulse so the closing seconds breathe
 *   30–60f  URL types in character-by-character with a blinking cursor
 *
 * The chevrons (›  ‹) on either side of the pill breathe in and out,
 * giving the eye an arrow-into-the-button without literally drawing
 * cursors.
 */
export const CTACard: React.FC<{
  scene: CTACardScene;
  brand: BrandTokens;
}> = ({ scene, brand }) => {
  const frame = useCurrentFrame();
  const { fps, width } = useVideoConfig();

  const headlineOpacity = interpolate(frame, [0, 14], [0, 1], { extrapolateRight: 'clamp' });
  const headlineOffset = interpolate(frame, [0, 14], [30, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  const pillScale = spring({
    frame: frame - 10,
    fps,
    config: { damping: 11, stiffness: 130 },
    from: 0.3,
    to: 1,
  });
  const pillOpacity = interpolate(frame, [10, 24], [0, 1], { extrapolateRight: 'clamp' });

  // Heartbeat scale (1.0 → 1.025 → 1.0) starting after the pill lands.
  const pulse = frame > 28 ? 1 + 0.025 * Math.sin((frame - 28) / 8) : 1;

  // Chevron breathing — sine wave that translates the arrows in/out.
  const chevronShift = Math.sin(frame / 7) * 6;
  const chevronOpacity = interpolate(frame, [16, 28], [0, 0.6], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Typewriter for the subline (URL). Reveal one character per frame
  // starting at frame 30. Cursor blinks every 12 frames after typing.
  const sub = scene.subline || '';
  const typeStart = 30;
  const typedCount = Math.max(0, Math.min(sub.length, frame - typeStart));
  const typed = sub.slice(0, typedCount);
  const typingDone = frame >= typeStart + sub.length;
  const cursorOn = typingDone ? Math.floor((frame - typeStart - sub.length) / 12) % 2 === 0 : true;
  const sublineOpacity = interpolate(frame, [26, 38], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill
      style={{
        justifyContent: 'center',
        alignItems: 'center',
        flexDirection: 'column',
        padding: '0 80px',
      }}
    >
      {/* Particle burst around the pill at landing */}
      <ParticleBurst
        color={brand.primary_color}
        startFrame={18}
        durationFrames={20}
        radius={width * 0.35}
        size={10}
        count={20}
      />

      {/* Headline */}
      <div
        style={{
          fontFamily: OSWALD,
          fontWeight: 700,
          fontSize: width * 0.075,
          color: brand.text_color || '#0f172a',
          letterSpacing: '-0.01em',
          textAlign: 'center',
          textTransform: 'uppercase',
          opacity: headlineOpacity,
          transform: `translateY(${headlineOffset}px)`,
          marginBottom: 16,
          lineHeight: 1.05,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
        }}
      >
        {scene.headline}
        <div style={{ marginTop: 8 }}>
          <ShapeUnderline
            width={Math.min(width * 0.5, 480)}
            color={brand.primary_color}
            startFrame={6}
            drawFrames={20}
            thickness={Math.max(8, width * 0.008)}
          />
        </div>
      </div>

      {/* Pill flanked by chevrons */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 24,
          marginTop: 32,
        }}
      >
        <div
          style={{
            fontFamily: OSWALD,
            fontWeight: 700,
            fontSize: width * 0.065,
            color: brand.primary_color,
            opacity: chevronOpacity,
            transform: `translateX(${-chevronShift}px)`,
          }}
        >
          ››
        </div>

        <div
          style={{
            backgroundColor: brand.primary_color,
            padding: '32px 80px',
            borderRadius: 999,
            opacity: pillOpacity,
            transform: `scale(${pillScale * pulse})`,
            fontFamily: OSWALD,
            fontWeight: 700,
            fontSize: width * 0.055,
            letterSpacing: '0.04em',
            color: '#ffffff',
            textTransform: 'uppercase',
            boxShadow: `0 16px 40px ${brand.primary_color}55`,
          }}
        >
          Get Started
        </div>

        <div
          style={{
            fontFamily: OSWALD,
            fontWeight: 700,
            fontSize: width * 0.065,
            color: brand.primary_color,
            opacity: chevronOpacity,
            transform: `translateX(${chevronShift}px)`,
          }}
        >
          ‹‹
        </div>
      </div>

      {/* URL typewriter */}
      {sub && (
        <div
          style={{
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
            fontWeight: 700,
            fontSize: width * 0.034,
            color: brand.text_color || '#0f172a',
            opacity: sublineOpacity * 0.78,
            marginTop: 48,
            letterSpacing: '0.04em',
            display: 'flex',
            alignItems: 'baseline',
            gap: 2,
          }}
        >
          {typed}
          <span
            style={{
              display: 'inline-block',
              width: 2,
              height: '1em',
              background: brand.primary_color,
              opacity: cursorOn ? 1 : 0,
              transform: 'translateY(0.15em)',
            }}
          />
        </div>
      )}
    </AbsoluteFill>
  );
};
