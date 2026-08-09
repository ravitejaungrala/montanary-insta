import { Composition } from 'remotion';
import { BrandPromo } from './compositions/BrandPromo';
import {
  RESOLUTIONS, FPS, totalDurationFrames,
  type BrandPromoStoryboard,
} from './storyboard-types';
import { sampleStoryboard } from './sample-storyboard';

/**
 * Composition registry — one <Composition> per template.
 *
 * When the backend's storyboard agent produces a JSON for a different
 * template later (problem-solution, quick-tip, testimonial), we add
 * another <Composition id="..."> entry here pointing at its renderer
 * component.
 *
 * For Phase 1 we ship ONE composition: `brand-promo-9x16`.
 *
 * `defaultProps` makes `remotion studio` instantly playable for local
 * iteration — devs see the sample storyboard until production input
 * arrives via Lambda render.
 */
export const Root: React.FC = () => {
  const aspect = sampleStoryboard.aspect_ratio;
  const { width, height } = RESOLUTIONS[aspect];
  const totalFrames = totalDurationFrames(sampleStoryboard.scenes);

  return (
    <>
      {/* Plain `<Composition>` without the generic — Remotion 4 expects a
          Zod schema there. Type-safety on the props is preserved by the
          `BrandPromo` component itself accepting `{ storyboard: BrandPromoStoryboard }`. */}
      <Composition
        id="brand-promo-9x16"
        component={BrandPromo}
        width={width}
        height={height}
        fps={FPS}
        durationInFrames={totalFrames}
        defaultProps={{ storyboard: sampleStoryboard }}
        // calculateMetadata lets a render run override duration / size
        // based on the actual JSON sent in (different scene counts,
        // different ratio). Without this, the lambda render would be
        // stuck at the defaultProps total of 30s.
        calculateMetadata={({ props }) => {
          const sb = (props as { storyboard: BrandPromoStoryboard }).storyboard;
          const res = RESOLUTIONS[sb.aspect_ratio];
          return {
            durationInFrames: totalDurationFrames(sb.scenes),
            width: res.width,
            height: res.height,
            fps: FPS,
          };
        }}
      />
    </>
  );
};
