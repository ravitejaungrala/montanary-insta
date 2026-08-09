import { useCurrentFrame, interpolate } from 'remotion';

/**
 * Text with a chromatic-aberration RGB split + horizontal shake during a
 * defined window — the cheap CSS trick that makes "problem" text feel
 * panicked / glitchy without WebGL or video filters.
 *
 * The same string is rendered 3 times with red / green / blue overlays
 * offset by a few px in opposing directions; during the glitch window
 * the offset jitters, after which everything settles to perfect
 * registration so the reader can read the actual word.
 */
export const GlitchText: React.FC<{
  text: string;
  fontFamily: string;
  fontSize: number;
  fontWeight?: number | string;
  color: string;
  /** Frame at which the glitch SHAKE begins. */
  glitchStart?: number;
  /** Number of frames the glitch lasts. After this everything settles. */
  glitchDuration?: number;
  /** Max horizontal jitter in px during the glitch. */
  jitterPx?: number;
  style?: React.CSSProperties;
}> = ({
  text, fontFamily, fontSize, fontWeight = 700, color,
  glitchStart = 0, glitchDuration = 8, jitterPx = 6,
  style,
}) => {
  const frame = useCurrentFrame();

  // Outside the glitch window: zero offset, full opacity for the main
  // colour layer, hidden RGB layers so it reads cleanly.
  const inWindow = frame >= glitchStart && frame < glitchStart + glitchDuration;
  // Pseudo-random jitter using sine + frame parity so it looks chaotic
  // but is deterministic for Lambda render reproducibility.
  const jitter = inWindow
    ? jitterPx * Math.sin((frame - glitchStart) * 1.7) * (((frame % 2) === 0) ? 1 : -1)
    : 0;
  const splitOpacity = inWindow ? 0.65 : 0;

  const baseStyle: React.CSSProperties = {
    fontFamily, fontSize, fontWeight,
    lineHeight: 1.35,
    letterSpacing: '-0.005em',
    ...style,
  };

  return (
    <div style={{ position: 'relative', display: 'inline-block', transform: `translateX(${jitter}px)` }}>
      {/* Red channel — slight left offset */}
      <div
        style={{
          ...baseStyle,
          color: '#ff3b30',
          position: 'absolute',
          inset: 0,
          transform: 'translate(-3px, 0)',
          opacity: splitOpacity,
          mixBlendMode: 'screen',
          pointerEvents: 'none',
        }}
      >
        {text}
      </div>
      {/* Cyan channel — slight right offset */}
      <div
        style={{
          ...baseStyle,
          color: '#00e5ff',
          position: 'absolute',
          inset: 0,
          transform: 'translate(3px, 0)',
          opacity: splitOpacity,
          mixBlendMode: 'screen',
          pointerEvents: 'none',
        }}
      >
        {text}
      </div>
      {/* Main legible layer */}
      <div style={{ ...baseStyle, color, position: 'relative' }}>{text}</div>
    </div>
  );
};
