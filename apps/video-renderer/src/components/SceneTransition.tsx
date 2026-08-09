import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate } from 'remotion';

/**
 * Per-scene transition wipe.
 *
 * Renders the scene's content as a child while:
 *   1. A horizontal brand-coloured bar sweeps across (0–8 frames) at the
 *      start of the scene — covers the cut, then peels back to reveal.
 *   2. After the bar leaves, the content fades up from 0 to 1 over a
 *      short window so nothing pops abruptly.
 *
 * The result: every cut feels like an Apple keynote section break, not
 * a slide deck.
 *
 * Skip the wipe on the very first scene (no previous scene to mask).
 */
export const SceneTransition: React.FC<{
  color: string;
  /** Set true on the first scene of the video to skip the entrance wipe. */
  skipEntry?: boolean;
  children: React.ReactNode;
}> = ({ color, skipEntry = false, children }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();

  // Entry wipe: brand bar slides in 0→8 covering full width, then leaves
  // from frame 8→16. Total: ~0.55s. Content fades in 14→24.
  const entryDuration = 16;
  const entryFadeStart = 14;
  const entryFadeEnd = 24;

  // Bar position: -1 = fully off-screen left, 0 = covering full screen,
  // +1 = fully off-screen right.
  const barTranslate = skipEntry
    ? 1
    : interpolate(frame, [0, 8, entryDuration], [-1, 0, 1], {
        extrapolateLeft: 'clamp',
        extrapolateRight: 'clamp',
      });

  const contentOpacity = skipEntry
    ? 1
    : interpolate(frame, [entryFadeStart, entryFadeEnd], [0, 1], {
        extrapolateLeft: 'clamp',
        extrapolateRight: 'clamp',
      });

  return (
    <AbsoluteFill>
      <div style={{ opacity: contentOpacity, width: '100%', height: '100%' }}>
        {children}
      </div>

      {/* Wipe bar — sits above the content but below any AbsoluteFill
          overlays the scene component itself stacks on top. */}
      {!skipEntry && frame < entryDuration && (
        <AbsoluteFill
          style={{
            background: color,
            // Slight skew gives the wipe a bit of motion energy — pure
            // verticals feel mechanical. `skewX` belongs inside the
            // transform shorthand, not as its own property.
            transform: `translateX(${barTranslate * 100}%) skewX(-6deg)`,
          }}
        />
      )}
    </AbsoluteFill>
  );
};
