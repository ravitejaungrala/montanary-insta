import { useCurrentFrame, useVideoConfig, spring, interpolate } from 'remotion';
import { SANS } from '../fonts';

/**
 * Vertical bars that climb in sequence.
 *
 * Each bar enters with a spring-based scaleY from 0 to its normalised
 * height. Bars are staggered 4 frames apart so the chart "fills in"
 * over ~1.5s. Last bar can be marked highlighted (rendered in primary
 * colour vs accent) — by default we highlight the tallest bar so the
 * data point speaks for itself.
 *
 * Width grows to fill its container; height is hard-capped at 360px so
 * it never overwhelms a 1080×1920 frame.
 */
export const AnimatedBarChart: React.FC<{
  values: number[];
  labels?: string[];
  color: string;
  accent?: string;
  width?: number;
  height?: number;
}> = ({
  values, labels, color, accent = '#0f172a',
  width = 540, height = 360,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  if (!values || values.length === 0) return null;
  const maxValue = Math.max(...values, 1);
  const barCount = values.length;
  const labelHeight = labels && labels.length === barCount ? 32 : 0;
  const chartHeight = height - labelHeight;
  const gap = 12;
  const barWidth = (width - (barCount - 1) * gap) / barCount;
  // Highlight the tallest bar so the eye lands on the headline value
  // without us having to plumb through a separate `highlightIndex` prop.
  const tallestIdx = values.indexOf(maxValue);

  return (
    <div style={{ width, height, display: 'flex', flexDirection: 'column', alignItems: 'flex-start' }}>
      <div style={{ width, height: chartHeight, display: 'flex', alignItems: 'flex-end', gap }}>
        {values.map((v, i) => {
          const heightFrac = v / maxValue;
          // Stagger each bar's entrance by 4 frames; spring physics
          // gives the rise a subtle overshoot at the top.
          const enterAt = i * 4;
          const grow = spring({
            frame: frame - enterAt,
            fps,
            config: { damping: 14, stiffness: 110 },
            from: 0,
            to: 1,
          });
          const isHighlight = i === tallestIdx;
          return (
            <div
              key={i}
              style={{
                width: barWidth,
                height: chartHeight * heightFrac,
                background: isHighlight ? color : accent,
                opacity: isHighlight ? 1 : 0.35,
                borderRadius: 8,
                transformOrigin: 'bottom',
                transform: `scaleY(${grow})`,
              }}
            />
          );
        })}
      </div>
      {labels && labels.length === barCount && (
        <div style={{ width, display: 'flex', gap, marginTop: 8 }}>
          {labels.map((l, i) => {
            const labelOpacity = interpolate(frame, [i * 4 + 12, i * 4 + 24], [0, 1], {
              extrapolateLeft: 'clamp',
              extrapolateRight: 'clamp',
            });
            return (
              <div
                key={i}
                style={{
                  width: barWidth,
                  fontFamily: SANS,
                  fontSize: 14,
                  fontWeight: 700,
                  color: accent,
                  textAlign: 'center',
                  opacity: labelOpacity,
                  textTransform: 'uppercase',
                  letterSpacing: '0.1em',
                }}
              >
                {l}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
