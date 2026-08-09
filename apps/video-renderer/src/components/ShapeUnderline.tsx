import { useCurrentFrame, interpolate } from 'remotion';

/**
 * Brand-coloured underline that draws left-to-right beneath any text.
 *
 * Used to mark the emphasis word in a kinetic headline, or to underline
 * a number/title in a feature scene. SVG path with stroke-dashoffset
 * for the draw animation; the curve is intentionally subtle (just a
 * gentle dip) so it reads as marker / brush, not graphic.
 */
export const ShapeUnderline: React.FC<{
  width: number;
  color: string;
  /** Frame at which the draw animation should START. Default 0. */
  startFrame?: number;
  /** How long the draw takes in frames. Default 18 frames (~0.6s). */
  drawFrames?: number;
  /** Stroke thickness in px. Scales with font size — pass roughly 1/12 of font px. */
  thickness?: number;
}> = ({ width, color, startFrame = 0, drawFrames = 18, thickness = 8 }) => {
  const frame = useCurrentFrame();

  // Build a gentle hand-drawn-ish path: start low-left, dip, end mid-right.
  // The slight curve gives motion energy; a flat line would feel sterile.
  const h = thickness * 2.5;
  const path = `M 0 ${h * 0.6} Q ${width * 0.4} ${h * 1.0}, ${width * 0.6} ${h * 0.7} T ${width} ${h * 0.5}`;
  // Length approximation — for Bezier curves we slightly over-estimate
  // so the dashoffset never exposes a residual gap at the end.
  const pathLen = width * 1.05;

  const draw = interpolate(
    frame,
    [startFrame, startFrame + drawFrames],
    [0, 1],
    { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }
  );

  return (
    <svg
      width={width}
      height={h * 1.5}
      viewBox={`0 0 ${width} ${h * 1.5}`}
      style={{ display: 'block', overflow: 'visible' }}
    >
      <path
        d={path}
        stroke={color}
        strokeWidth={thickness}
        strokeLinecap="round"
        fill="none"
        strokeDasharray={pathLen}
        strokeDashoffset={pathLen * (1 - draw)}
      />
    </svg>
  );
};
