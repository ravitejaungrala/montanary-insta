# Pipelyt Video Renderer

Remotion-based motion-graphics video generator for Pipelyt.

Phase 1 ships ONE template: `brand_promo` (5 scenes, 30 seconds, 9:16 vertical, pure motion graphics — zero AI image generation, zero voiceover, zero music). Later phases add background music, voiceover, and additional templates.

## How it fits

```
Pipelyt backend (FastAPI on Lambda)
   │
   ├── storyboard agent (Gemini 2.5 Pro)  →  emits JSON matching ./src/storyboard-types.ts
   │
   └── Remotion Lambda  ←  renders that JSON into MP4
```

## Local development

### Prerequisites
- Node.js 18+
- An AWS IAM user with Lambda + S3 + IAM permissions (only needed for the deploy commands; not for local preview)

### First-time setup

```bash
cd apps/video-renderer
npm install
cp .env.example .env
# edit .env and paste your AWS keys + region
```

### Live preview (no AWS needed)

```bash
npm run preview
```

Opens Remotion Studio at `http://localhost:3000`. The `Brand Promo 9:16` composition appears with the sample storyboard playing in real time. Edit anything in `src/` and the preview hot-reloads.

### Build a local MP4 (no AWS)

```bash
npx remotion render brand_promo_9_16 out/test.mp4
```

Renders the sample storyboard to `out/test.mp4` using your local Chromium. Useful for quick smoke tests before deploying.

## Deploying to AWS Lambda (one-time per region)

```bash
# 1. Deploy the rendering Lambda function (creates the function in your AWS account)
npm run lambda:deploy

# 2. Push the bundled composition to S3 so the Lambda can find it
npm run lambda:site
```

Both commands print outputs you'll need to add to the backend's environment:
- `REMOTION_FUNCTION_NAME` — from step 1
- `REMOTION_SERVE_URL` — from step 2

The Pipelyt backend's `services/video/remotion_dispatcher.py` reads these at runtime and calls the Lambda via `@remotion/lambda`'s `renderMediaOnLambda` helper.

## Project layout

```
src/
├─ index.ts                  # Remotion entrypoint (registers Root)
├─ Root.tsx                  # Composition registry — one <Composition> per template
├─ storyboard-types.ts       # TS contract shared with the backend agents
├─ sample-storyboard.ts      # Hardcoded demo data for local preview
├─ compositions/
│  └─ BrandPromo.tsx         # 5-scene Brand Promo composition
└─ components/
   ├─ AnimatedBackground.tsx # Brand-coloured drift behind every scene
   ├─ BrandLogoReveal.tsx    # Scene 1
   ├─ KineticHeadline.tsx    # Scene 2
   ├─ FeatureCallout.tsx     # Scenes 3 + 4
   └─ CTACard.tsx            # Scene 5
```

## Adding a new template (future)

1. Add the new template's discriminant + scene types to `storyboard-types.ts`
2. Create a new composition file under `src/compositions/`
3. Register the composition in `Root.tsx`
4. Update the backend storyboard agent to emit the new template's JSON shape
5. Re-run `npm run lambda:site` to push the updated bundle

No backend redeploy needed for composition-only changes — the bundle lives on S3.
