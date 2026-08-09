import { useCurrentFrame, useVideoConfig, interpolate } from 'remotion';

/**
 * Line graph that draws stroke-by-stroke across the canvas.
 *
 * Built with one SVG `<path>` and an animated `stroke-dashoffset` so the
 * line "writes itself" left to right. After the line lands, dot markers
 * pop in at each data point with a tiny stagger.
 *
 * Designed for the Stripe / Linear "growth chart" feel — minimalist,
 * brand-coloured stroke, no fill.
 */
export const AnimatedLineGraph: React.FC<{
  values: number[];
  color: string;
  width?: number;
  height?: number;
}> = ({ values, color, width = 540, height = 240 }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  if (!values || values.length < 2) return null;

  const padding = 16;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const range = max - min || 1;

  // Convert values → SVG points spread across (padding..width-padding)
  // and (height-padding..padding) so larger values render higher on screen.
  const points = values.map((v, i) => {
    const x = padding + (i * (width - padding * 2)) / (values.length - 1);
    const y = height - padding - ((v - min) / range) * (height - padding * 2);
    return [x, y] as const;
  });
  const path = points.map((p, i) => (i === 0 ? `M ${p[0]} ${p[1]}` : `L ${p[0]} ${p[1]}`)).join(' ');

  // Approximate the path length so we can drive stroke-dashoffset
  // without measuring at runtime. Sum of segment lengths.
  let pathLen = 0;
  for (let i = 1; i < points.length; i++) {
    const dx = points[i][0] - points[i - 1][0];
    const dy = points[i][1] - points[i - 1][1];
    pathLen += Math.sqrt(dx * dx + dy * dy);
  }

  // Animate over first 75% of scene; dots appear at 60-90%.
  const drawEnd = Math.round((durationInFrames || fps * 6) * 0.75);
  const drawProgress = interpolate(frame, [0, drawEnd], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      style={{ display: 'block' }}
    >
      <path
        d={path}
        stroke={color}
        strokeWidth={6}
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
        strokeDasharray={pathLen}
        strokeDashoffset={pathLen * (1 - drawProgress)}
      />
      {points.map(([x, y], i) => {
        // Dots fade + scale in once the line has reached them.
        const dotStart = Math.round((drawEnd * (i + 1)) / points.length);
        const dotOpacity = interpolate(frame, [dotStart, dotStart + 6], [0, 1], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
        });
        const dotScale = interpolate(frame, [dotStart, dotStart + 8], [0, 1], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
        });
        return (
          <circle
            key={i}
            cx={x}
            cy={y}
            r={6 * dotScale}
            fill={color}
            opacity={dotOpacity}
          />
        );
      })}
    </svg>
  );
};
