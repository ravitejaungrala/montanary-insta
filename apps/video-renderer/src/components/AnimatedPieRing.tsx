import { useCurrentFrame, useVideoConfig, interpolate } from 'remotion';
import { OSWALD } from '../fonts';

/**
 * Donut ring that fills to a target percentage, with the percentage
 * counting up in the centre.
 *
 * Pure SVG circle with stroke-dasharray driving the fill — same trick
 * as the line graph but on a closed circle. The trailing arc stays
 * visible at low opacity so the user understands the full 0-100% scale.
 */
export const AnimatedPieRing: React.FC<{
  percent: number;
  color: string;
  size?: number;
  thickness?: number;
}> = ({ percent, color, size = 260, thickness = 22 }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const clamped = Math.max(0, Math.min(100, percent));
  const radius = size / 2 - thickness / 2;
  const cx = size / 2;
  const cy = size / 2;
  const circumference = 2 * Math.PI * radius;

  const fillEnd = Math.round((durationInFrames || fps * 6) * 0.7);
  const fillProgress = interpolate(frame, [0, fillEnd], [0, clamped / 100], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const eased = 1 - Math.pow(1 - fillProgress / (clamped / 100 || 1), 2);
  const fraction = (clamped / 100) * eased;

  const display = Math.round(clamped * eased);

  return (
    <div style={{ position: 'relative', width: size, height: size }}>
      <svg
        width={size}
        height={size}
        // Rotate so 0% starts at 12 o'clock, not 3 o'clock.
        style={{ transform: 'rotate(-90deg)' }}
      >
        {/* Background ring — barely-there guide showing the full 100%. */}
        <circle
          cx={cx}
          cy={cy}
          r={radius}
          stroke={color}
          strokeWidth={thickness}
          fill="none"
          opacity={0.12}
        />
        {/* Filled arc */}
        <circle
          cx={cx}
          cy={cy}
          r={radius}
          stroke={color}
          strokeWidth={thickness}
          strokeLinecap="round"
          fill="none"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - fraction)}
        />
      </svg>
      <div
        style={{
          position: 'absolute',
          inset: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontFamily: OSWALD,
          fontWeight: 700,
          fontSize: size * 0.32,
          color,
          letterSpacing: '-0.02em',
          fontVariantNumeric: 'tabular-nums',
          lineHeight: 1,
        }}
      >
        {display}%
      </div>
    </div>
  );
};
