import { useCurrentFrame, useVideoConfig } from 'remotion';
import { SANS } from '../fonts';

/**
 * Horizontal scrolling ticker — the kind of micro-text strip you see
 * on stock dashboards or news lower-thirds. Doubles the input list so
 * the marquee can loop seamlessly at the wraparound point.
 *
 * Used as a tiny editorial accent above headlines / below the logo
 * lockup. Speed is "items per scene" — by default we move two
 * full-list-widths over the scene's duration, which feels lively but
 * not frantic.
 */
export const TickerMarquee: React.FC<{
  items: string[];
  color: string;
  fontSize?: number;
  speed?: number;
}> = ({ items, color, fontSize = 16, speed = 2 }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();

  if (!items || items.length === 0) return null;

  // Duplicate the list to give us a clean loop midpoint.
  const doubled = [...items, ...items];
  const offsetPct = ((frame / Math.max(1, durationInFrames)) * speed * 50) % 50;

  return (
    <div
      style={{
        width: '100%',
        overflow: 'hidden',
        whiteSpace: 'nowrap',
        opacity: 0.65,
      }}
    >
      <div
        style={{
          display: 'inline-flex',
          gap: '36px',
          fontFamily: SANS,
          fontWeight: 700,
          fontSize,
          color,
          letterSpacing: '0.18em',
          textTransform: 'uppercase',
          transform: `translateX(-${offsetPct}%)`,
        }}
      >
        {doubled.map((label, i) => (
          <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 36 }}>
            {label}
            {/* Dot separator between items */}
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: color, opacity: 0.6 }} />
          </span>
        ))}
      </div>
    </div>
  );
};
