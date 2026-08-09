import React, { useEffect, useRef, useState } from 'react';
import { Sparkles, Film, RefreshCw, AlertCircle, CheckCircle2, Cpu } from 'lucide-react';

/**
 * VideoGenerator — Phase 1 motion-graphics video flow.
 *
 * Sits inside the Agent Post review screen ABOVE the manual video-upload
 * card. Users can either:
 *   (a) click "Generate Video from Brief" here and get an AI-built MP4, OR
 *   (b) upload their own footage in the existing card below.
 *
 * On a successful generation we call `setUploadedImageUrl(mp4_url)` and
 * `setMediaType('video')` so the existing publish pipeline treats the
 * AI video exactly like a manually uploaded one — no separate publish
 * code path needed.
 *
 * The poll interval is 2.5s — Lambda renders typically finish in 30-60s
 * so this gives a fast-enough feel without hammering the API.
 */
const POLL_INTERVAL_MS = 2500;
const STAGE_LABELS = {
  pending: 'Queueing',
  rendering: 'Storyboarding & rendering',
  ready: 'Ready',
  failed: 'Failed',
};

export default function VideoGenerator({
  authAxios,
  campaignBrief,
  uploadedImageUrl,
  setUploadedImageUrl,
  setMediaType,
  selectedProduct,
}) {
  const [jobId, setJobId] = useState(null);
  const [status, setStatus] = useState(null);   // 'pending' | 'rendering' | 'ready' | 'failed'
  const [storyboard, setStoryboard] = useState(null);
  const [errorMsg, setErrorMsg] = useState(null);
  const [elapsedSec, setElapsedSec] = useState(0);

  const pollTimer = useRef(null);
  const elapsedTimer = useRef(null);

  // Cleanup any timers on unmount so we don't leak intervals if the user
  // navigates away mid-generation.
  useEffect(() => () => {
    if (pollTimer.current) clearTimeout(pollTimer.current);
    if (elapsedTimer.current) clearInterval(elapsedTimer.current);
  }, []);

  const isRunning = status === 'pending' || status === 'rendering';
  const briefShort = (campaignBrief || '').trim();

  const startGeneration = async () => {
    if (!briefShort) {
      setErrorMsg('Add a campaign brief above before generating a video.');
      return;
    }
    setErrorMsg(null);
    setStoryboard(null);
    setStatus('pending');
    setElapsedSec(0);
    elapsedTimer.current = setInterval(() => setElapsedSec(s => s + 1), 1000);

    try {
      const res = await authAxios.post('/video/generate', {
        campaign_brief: briefShort,
        aspect_ratio: '9:16',
        template: 'brand_promo',
        duration_sec: 30,
        dna_product_id: selectedProduct || null,
      });
      const id = res?.data?.id;
      if (!id) throw new Error('Backend did not return a job id');
      setJobId(id);
      pollOnce(id);
    } catch (err) {
      setStatus('failed');
      setErrorMsg(err?.response?.data?.detail || err?.message || 'Failed to start generation');
      stopElapsed();
    }
  };

  const pollOnce = (id) => {
    pollTimer.current = setTimeout(async () => {
      try {
        const res = await authAxios.get(`/video/${id}/status`);
        const next = res?.data;
        if (!next) throw new Error('Empty status response');

        setStatus(next.status);
        if (next.storyboard_json) setStoryboard(next.storyboard_json);

        if (next.status === 'ready' && next.video_url) {
          // Hand the MP4 over to the existing publish pipeline — same
          // hooks the manual upload uses, so Publish / Schedule / Save
          // Draft all work without further changes.
          setUploadedImageUrl(next.video_url);
          if (typeof setMediaType === 'function') setMediaType('video');
          stopElapsed();
          return;
        }
        if (next.status === 'failed') {
          setErrorMsg(next.error_message || 'Render failed (no detail)');
          stopElapsed();
          return;
        }
        // Still pending / rendering — keep polling.
        pollOnce(id);
      } catch (err) {
        setStatus('failed');
        setErrorMsg(err?.response?.data?.detail || err?.message || 'Polling failed');
        stopElapsed();
      }
    }, POLL_INTERVAL_MS);
  };

  const stopElapsed = () => {
    if (elapsedTimer.current) {
      clearInterval(elapsedTimer.current);
      elapsedTimer.current = null;
    }
  };

  const reset = () => {
    if (pollTimer.current) clearTimeout(pollTimer.current);
    stopElapsed();
    setJobId(null);
    setStatus(null);
    setStoryboard(null);
    setErrorMsg(null);
    setElapsedSec(0);
    setUploadedImageUrl(null);
    if (typeof setMediaType === 'function') setMediaType(null);
  };

  // Compact intro state — no job in flight, no MP4 yet.
  if (!isRunning && status !== 'ready' && !uploadedImageUrl) {
    return (
      <div className="bg-gradient-to-br from-orange-50 to-white rounded-2xl p-5 border-2 border-[#F55600]/30 shadow-sm mb-3">
        <div className="flex items-start gap-4">
          <div className="w-12 h-12 rounded-2xl bg-[#F55600] text-white flex items-center justify-center shrink-0 shadow-md">
            <Film className="w-6 h-6" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-black text-[#2B2926] mb-1">Generate a 30-second video</div>
            <div className="text-[11px] font-bold text-slate-600 leading-relaxed">
              Pipelyt's AI will turn your campaign brief into a 9:16 motion-graphics video — logo reveal, headline, two feature callouts, and a CTA.
              Renders in about 60 seconds.
            </div>
            {errorMsg && (
              <div className="mt-2 text-[11px] font-bold text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-1.5 flex items-center gap-1.5">
                <AlertCircle className="w-3 h-3" /> {errorMsg}
              </div>
            )}
            <button
              type="button"
              onClick={startGeneration}
              disabled={!briefShort}
              className="mt-3 inline-flex items-center gap-2 bg-[#F55600] disabled:bg-slate-300 disabled:cursor-not-allowed text-white px-4 py-2.5 rounded-xl text-[11px] font-black uppercase tracking-widest hover:bg-[#ff5e1f] active:scale-[0.98] transition-all shadow-lg shadow-orange-100"
            >
              <Sparkles className="w-3.5 h-3.5" />
              Generate Video from Brief
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Running state — pending or rendering.
  if (isRunning) {
    return (
      <div className="bg-white rounded-2xl p-5 border-2 border-[#F55600]/30 shadow-sm mb-3">
        <div className="flex items-center gap-4 mb-3">
          <div className="w-12 h-12 rounded-2xl bg-[#F55600]/10 text-[#F55600] flex items-center justify-center shrink-0 animate-pulse">
            <Cpu className="w-6 h-6" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-black text-[#2B2926]">{STAGE_LABELS[status] || 'Working'}…</div>
            <div className="text-[11px] font-bold text-slate-500">
              {elapsedSec}s elapsed · usually finishes in 60-90s
            </div>
          </div>
          <div className="text-[10px] font-black text-slate-400 uppercase tracking-widest">Job #{jobId}</div>
        </div>

        {/* Stage strip — the three steps the worker walks through. */}
        <div className="grid grid-cols-3 gap-2 mt-3">
          <Stage label="Brief refined"     done={status === 'rendering' || status === 'ready'} active={status === 'pending'} />
          <Stage label="Storyboard built"  done={!!storyboard}                                  active={status === 'rendering' && !storyboard} />
          <Stage label="Rendering on Lambda" done={status === 'ready'}                          active={status === 'rendering' && !!storyboard} />
        </div>

        {storyboard?.scenes && (
          <div className="mt-3 text-[10px] font-bold text-slate-500 uppercase tracking-widest">
            {storyboard.scenes.length} scenes · {storyboard.aspect_ratio} · {storyboard.brand?.company_name || ''}
          </div>
        )}
      </div>
    );
  }

  // Ready or failed — handled by the parent video-upload card via
  // uploadedImageUrl. We just show a small "regenerate" affordance if a
  // video is loaded, so the user can rerun the agent.
  if (status === 'ready' && uploadedImageUrl) {
    return (
      <div className="bg-emerald-50 rounded-2xl p-3 border border-emerald-200 shadow-sm mb-3 flex items-center gap-3">
        <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
        <div className="flex-1 text-[11px] font-black text-emerald-700 uppercase tracking-widest">
          AI video ready · loaded into upload card below
        </div>
        <button
          type="button"
          onClick={() => { reset(); startGeneration(); }}
          className="inline-flex items-center gap-1.5 text-[10px] font-black uppercase tracking-widest text-[#F55600] hover:underline"
        >
          <RefreshCw className="w-3 h-3" /> Regenerate
        </button>
      </div>
    );
  }

  if (status === 'failed') {
    return (
      <div className="bg-red-50 rounded-2xl p-3 border border-red-200 shadow-sm mb-3 flex items-start gap-2">
        <AlertCircle className="w-4 h-4 text-red-600 shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <div className="text-[11px] font-black text-red-700">Generation failed</div>
          <div className="text-[10px] font-bold text-red-500 break-words">{errorMsg}</div>
          <button
            type="button"
            onClick={() => { reset(); startGeneration(); }}
            className="mt-1.5 text-[10px] font-black uppercase tracking-widest text-[#F55600] hover:underline inline-flex items-center gap-1"
          >
            <RefreshCw className="w-3 h-3" /> Try again
          </button>
        </div>
      </div>
    );
  }

  return null;
}

/* Compact stage indicator pill used inside the running-state strip. */
function Stage({ label, done, active }) {
  const tone = done
    ? 'bg-emerald-50 border-emerald-200 text-emerald-700'
    : active
      ? 'bg-orange-50 border-orange-200 text-[#F55600] animate-pulse'
      : 'bg-slate-50 border-slate-100 text-slate-400';
  return (
    <div className={`text-[10px] font-black uppercase tracking-widest px-2 py-2 rounded-xl border ${tone} text-center leading-tight`}>
      {label}
    </div>
  );
}
