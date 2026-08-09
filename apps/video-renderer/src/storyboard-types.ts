/**
 * Storyboard JSON contract — v4 character-driven reel.
 *
 *   1. logo_intro       (0–2s)   brand mark reveal — establishes who's
 *                                speaking before the story starts
 *   2. pain_avatar      (2–10s)  AI-generated photo of a person struggling
 *                                with the problem. Quote text overlays.
 *   3. solution_avatar  (10–22s) AI-generated photo of a different person
 *                                (expert / confident user) explaining the
 *                                product. Solution copy overlays.
 *   4. cta_card         (22–30s) action close
 *
 * Total: 30 seconds.
 *
 * Scene-level photo URLs are filled in by the backend's image-generation
 * step BEFORE the storyboard reaches Remotion. The frontend never has to
 * know how the photos were produced — it just renders whatever URL the
 * field carries.
 */

export type BrandTokens = {
  primary_color: string;
  secondary_color?: string;
  background_color?: string;
  text_color?: string;
  logo_url: string;
  company_name: string;
  tagline?: string;
};

export type AspectRatio = '9:16' | '1:1' | '16:9';

export const RESOLUTIONS: Record<AspectRatio, { width: number; height: number }> = {
  '9:16': { width: 1080, height: 1920 },
  '1:1': { width: 1080, height: 1080 },
  '16:9': { width: 1920, height: 1080 },
};

export const FPS = 30;

type SceneBase = { duration_sec: number };

// ─── BEAT 1 ─── Logo intro: 2-second brand stamp at the start.
export type LogoIntroScene = SceneBase & {
  type: 'logo_intro';
};

// ─── BEAT 2 ─── Pain avatar: a person struggling with the problem.
export type PainAvatarScene = SceneBase & {
  type: 'pain_avatar';
  /**
   * Image-prompt seed describing the avatar. The backend image agent
   * uses this to call Gemini 2.5 Flash Image. NEVER rendered to video.
   * Example: "Mid-30s marketing manager at a cluttered desk, frustrated
   * expression, holding head, dim office light, 9:16 portrait."
   */
  avatar_prompt: string;
  /** Backend fills this in after generation — the public S3 URL. */
  avatar_url?: string;
  /**
   * The "quote" text that overlays the photo. Speaks AS the struggling
   * character (1st person voice if possible).
   */
  quote: string;
  /** Optional 2-3 word label shown above the quote, e.g. "THE PROBLEM". */
  label?: string;
};

// ─── BEAT 3 ─── Solution avatar: confident expert explaining the product.
export type SolutionAvatarScene = SceneBase & {
  type: 'solution_avatar';
  /** Image-prompt seed for the solution-side avatar. */
  avatar_prompt: string;
  /** Backend-filled URL after generation. */
  avatar_url?: string;
  /** Solution copy — speaks AS the expert / brand voice. */
  quote: string;
  label?: string;
  /**
   * Optional one-line proof statement that lands AFTER the main quote
   * (separate visual beat — typically a stat or capability in caps).
   */
  proof?: string;
};

// ─── BEAT 4 ─── CTA: closing action frame.
export type CTACardScene = SceneBase & {
  type: 'cta_card';
  headline: string;
  subline?: string;
};

export type Scene =
  | LogoIntroScene
  | PainAvatarScene
  | SolutionAvatarScene
  | CTACardScene;

export type ReelPromoStoryboard = {
  template: 'reel_promo';
  aspect_ratio: AspectRatio;
  brand: BrandTokens;
  scenes: Scene[];
};

/** Back-compat alias. */
export type BrandPromoStoryboard = ReelPromoStoryboard;

export const totalDurationFrames = (scenes: Scene[]): number =>
  scenes.reduce((acc, s) => acc + Math.round(s.duration_sec * FPS), 0);
