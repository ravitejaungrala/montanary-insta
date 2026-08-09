import { useCurrentFrame, useVideoConfig, interpolate } from 'remotion';
import { OSWALD } from '../fonts';

/**
 * Counter that rolls 0 → `value` over the entire scene.
 *
 * Apple-style number ticker. Used for stat callouts ("60% faster",
 * "$12k saved"). The value lands at ~80% of the scene then holds for
 * the final 20% so the eye can read it.
 *
 * Pure CSS — no canvas, no SVG — so it composes with shadow filters,
 * text-stroke, gradients without surprises.
 */
export const AnimatedNumber: React.FC<{
  value: number;
  prefix?: string;
  suffix?: string;
  color?: string;
  fontSize?: number;
  durationFrames?: number;
}> = ({
  value, prefix = '', suffix = '',
  color = '#ff4500', fontSize = 280, durationFrames,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const targetFrame = durationFrames ?? Math.round((durationInFrames || fps * 6) * 0.8);

  // Ease-out interpolation — fast at the start, slow as it lands. Makes
  // the value feel "settling" rather than just linearly counting.
  const progress = interpolate(frame, [0, targetFrame], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const eased = 1 - Math.pow(1 - progress, 3);
  const display = Math.round(value * eased);

  return (
    <div
      style={{
        fontFamily: OSWALD,
        fontWeight: 700,
        fontSize,
        color,
        letterSpacing: '-0.02em',
        lineHeight: 1,
        // Tabular numbers — without this the width jitters as digits
        // change shape (1 vs 8). With it, the counter feels mechanical
        // and stable.
        fontVariantNumeric: 'tabular-nums',
        display: 'flex',
        alignItems: 'baseline',
        gap: 8,
      }}
    >
      {prefix && <span style={{ fontSize: fontSize * 0.55 }}>{prefix}</span>}
      <span>{display}</span>
      {suffix && <span style={{ fontSize: fontSize * 0.55 }}>{suffix}</span>}
    </div>
  );
};
