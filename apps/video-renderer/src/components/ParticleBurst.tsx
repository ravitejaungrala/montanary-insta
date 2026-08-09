import { useCurrentFrame, useVideoConfig, interpolate } from 'remotion';

/**
 * One-shot radial particle burst centred on its container.
 *
 * Spawns N small dots that fly outward in a circle pattern, fading +
 * shrinking as they go. Used to punctuate scene starts (logo reveal,
 * CTA pill landing). All particles are pure-CSS divs — cheap to render,
 * no canvas / WebGL.
 *
 * Default 24 particles is a good "burst that reads" — fewer feels
 * stingy, more becomes mush.
 */
export const ParticleBurst: React.FC<{
  color: string;
  count?: number;
  startFrame?: number;
  /** How many frames the burst takes from spawn to fully gone. */
  durationFrames?: number;
  /** Final radius the particles reach in px. */
  radius?: number;
  /** Particle size in px. */
  size?: number;
}> = ({
  color, count = 24, startFrame = 0,
  durationFrames = 24, radius = 200, size = 10,
}) => {
  const frame = useCurrentFrame() - startFrame;

  // Hide entirely outside the active window — saves Remotion the
  // per-frame DOM diff cost on long scenes that only burst once.
  if (frame < 0 || frame > durationFrames) return null;

  const progress = interpolate(frame, [0, durationFrames], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const opacity = interpolate(progress, [0, 0.2, 1], [0, 1, 0]);
  const scale = interpolate(progress, [0, 1], [1, 0.4]);
  const r = radius * progress;

  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        pointerEvents: 'none',
      }}
    >
      <div style={{ position: 'relative', width: 0, height: 0 }}>
        {Array.from({ length: count }).map((_, i) => {
          // Even radial distribution — golden-angle would feel more
          // organic but the eye reads "burst" better with an even ring.
          const angle = (i / count) * Math.PI * 2;
          const x = Math.cos(angle) * r;
          const y = Math.sin(angle) * r;
          return (
            <div
              key={i}
              style={{
                position: 'absolute',
                width: size * scale,
                height: size * scale,
                borderRadius: '50%',
                background: color,
                opacity,
                transform: `translate(${x - (size * scale) / 2}px, ${y - (size * scale) / 2}px)`,
              }}
            />
          );
        })}
      </div>
    </div>
  );
};
