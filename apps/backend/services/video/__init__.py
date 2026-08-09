"""Video-generation pipeline (Phase 1 — silent motion graphics).

Three pieces:
    storyboard_agent.py      — Gemini 2.5 Pro produces a BrandPromoStoryboard JSON
    remotion_dispatcher.py   — invokes the Remotion Lambda to render the JSON
    (router.video uses both end-to-end)

Phase 2 will add `music_picker.py`, Phase 3 `voiceover_agent.py`.
"""
