import React from 'react';
import { Plus, Trash2, ExternalLink, Search, Rocket, CheckCircle2, Cpu, RefreshCw, Facebook, Linkedin, Instagram, ChevronLeft, Type, Image as ImageIcon, Video, FileText } from 'lucide-react';
import XIcon from '../components/icons/XIcon';
import { motion, AnimatePresence } from 'framer-motion';
import LoadingModal from '../components/LoadingModal';
// Per-platform live progress popup — shows a status per selected platform
// as the /post call publishes them (spinner → ✅ / ❌ / ⚠️).
import PublishingProgressModal from '../components/PublishingProgressModal';
// Was: 14 raw `alert(...)` calls throughout this file for success / error /
// validation feedback. Browser-native alerts block the whole page, don't
// match the app's design language, and are jarring on mobile. Switched
// to the shared toast context — same one Posts.jsx already uses.
import { useNotification } from '../context/NotificationContext';
// Normalises FastAPI validation errors (array-of-objects with loc/msg/type)
// into a readable one-liner. Without this the toast rendered "[object
// Object]" whenever the backend rejected the plan request with a 422.
import { formatApiError } from '../utils/postContent';

// Modular Dashboard Components
import StepIndicator from './Dashboard/components/StepIndicator';
import CampaignBrief from './Dashboard/components/CampaignBrief';
import StrategicBlueprint from './Dashboard/components/StrategicBlueprint';
import ContentVariants from './Dashboard/components/ContentVariants';
import VisualGallery, { flattenVisuals } from './Dashboard/components/VisualGallery';
import CarouselSwiper from '../components/CarouselSwiper';
import VideoGenerator from './Dashboard/components/VideoGenerator';
import ReviewSidebar from './Dashboard/components/ReviewSidebar';
import PublishingControls from './Dashboard/components/PublishingControls';
import ReviewActions from './Dashboard/components/ReviewActions';
import CampaignPlanTable from './Dashboard/components/CampaignPlanTable';
import CampaignReviewQueue from './Dashboard/components/CampaignReviewQueue';

const Dashboard = ({
  campaignBrief, setCampaignBrief,
  dashboardStep, setDashboardStep,
  dashboardPostType = 'image', setDashboardPostType = () => {},
  briefError = '', setBriefError = () => {},
  isGenerating, setIsGenerating, handleGenerateContent,
  handleRegenerateVisuals,
  handleCustomPrompt,
  generatedData, setGeneratedData,
  selectedDashboardPlatforms, setSelectedDashboardPlatforms,
  activeReviewPlatform, setActiveReviewPlatform,
  selectedVariants, setSelectedVariants,
  selectedTargets,
  toggleTarget,
  uploadedImageUrl,
  setUploadedImageUrl,
  selectedVisual,
  setSelectedVisual,
  // Media upload pipeline (shared with manual Posts page) — reused here for
  // the video / document agent flows. Aliased to avoid colliding with the
  // local handleImageChange used by the legacy image-upload card.
  handleImageChange: handleMediaUpload,
  mediaType, setMediaType,
  mediaUploadProgress,
  videoDurationSec,
  authAxios,
  connections,
  contextFiles, setContextFiles,
  logoImage, setLogoImage,
  logoPreview, setLogoPreview,
  selectedProduct, setSelectedProduct,
  productReferenceImages = [], setProductReferenceImages = () => {},
  // Aspect ratio for image generation. Owned by App.jsx so the API call
  // can read the user's choice; forwarded to CampaignBrief's chip.
  aspectRatio, setAspectRatio,
  imageStyle, setImageStyle,
  user,
  // Toast/message setter from App.jsx. Default to a no-op so the Dashboard
  // still renders if the parent forgot to wire it — previously this line
  // referenced an undeclared `setMessage` identifier and threw a
  // ReferenceError inside the "generation complete" effect.
  setMessage = () => {},
  setActiveTab,
  navigateToSettingsTab,
  fetchPublished,
  fetchScheduled
}) => {
  const { toast } = useNotification();
  const [stats, setStats] = React.useState({ scheduled: 0, posted: 0, drafts: 0 });
  const [showGenerationModal, setShowGenerationModal] = React.useState(false);
  // Publish progress popup state (per-platform status while /post runs).
  const [publishProgress, setPublishProgress] = React.useState({
    open: false,
    platforms: [],
    results: null,
  });

  // Sync modal with generation state
  React.useEffect(() => {
    setShowGenerationModal(isGenerating);
  }, [isGenerating]);

  // Handle completion notification
  React.useEffect(() => {
    if (!isGenerating && generatedData) {
      // If modal was closed manually (set to false while isGenerating was still true earlier), 
      // or just finished, we can notify if it's hidden.
      if (!showGenerationModal && setMessage) {
        setMessage("Generation Complete! 🚀 AI has finished crafting your strategy.");
      }
    }
  }, [isGenerating, generatedData]);

  const fetchStats = async () => {
    try {
      const [s, p, d] = await Promise.all([
        authAxios.get('/scheduled'),
        authAxios.get('/posts'),
        authAxios.get('/drafts')
      ]);
      setStats({
        scheduled: s.data?.length || 0,
        posted: p.data?.length || 0,
        drafts: d.data?.length || 0
      });
    } catch (e) {
      console.error("Failed to fetch stats", e);
    }
  };

  React.useEffect(() => {
    fetchStats();
  }, [dashboardStep]);

  const [previewPlatform, setPreviewPlatform] = React.useState('linkedin');
// ... rest of state ...

  const [editingVariant, setEditingVariant] = React.useState(null); // 'a' or 'b'
  const [tempText, setTempText] = React.useState("");

  const [scheduledDate, setScheduledDate] = React.useState("");
  const [scheduledTime, setScheduledTime] = React.useState("12:00");
  const [selectedTimezone, setSelectedTimezone] = React.useState(Intl.DateTimeFormat().resolvedOptions().timeZone);
  const [isScheduling, setIsScheduling] = React.useState(false);
  const [activeActionTab, setActiveActionTab] = React.useState('publish'); // 'publish', 'schedule', 'draft'
  const [postStrategy, setPostStrategy] = React.useState('publish'); // 'publish' or 'schedule'
  const [scheduledDays, setScheduledDays] = React.useState(7);
  const [campaignPlan, setCampaignPlan] = React.useState([]);

  const [uploadedBase64, setUploadedBase64] = React.useState(null);
  const [isUploadingImage, setIsUploadingImage] = React.useState(false);
  const fileInputRef = React.useRef(null);

  // "Back to Brief" confirmation modal — warns the user that discarding will
  // drop the currently generated content/visuals, and offers a Save as Draft
  // escape hatch so they don't lose work.
  const [showBackConfirm, setShowBackConfirm] = React.useState(false);
  const [isSavingDraftBack, setIsSavingDraftBack] = React.useState(false);

  const discardAndGoBack = () => {
    try { setGeneratedData && setGeneratedData(null); } catch (_) {}
    try { setSelectedVisual && setSelectedVisual(null); } catch (_) {}
    setShowBackConfirm(false);
    setDashboardStep('brief');
  };

  const saveDraftAndGoBack = async () => {
    try {
      setIsSavingDraftBack(true);
      await handleSaveDraft();
    } finally {
      setIsSavingDraftBack(false);
      setShowBackConfirm(false);
      setDashboardStep('brief');
    }
  };

  const handleImageChange = (e) => {
    const file = e.target.files[0];
    if (file) {
      setUploadedImageUrl(URL.createObjectURL(file));
      const reader = new FileReader();
      reader.onloadend = () => setUploadedBase64(reader.result);
      reader.readAsDataURL(file);
    }
  };

  const hasImageSelected = selectedVisual !== 'none' && (
    selectedVisual === 'uploaded' ? !!uploadedImageUrl :
    (typeof selectedVisual === 'number' && !!flattenVisuals(generatedData)[selectedVisual]?.url)
  );

  const handleSaveDraft = async () => {
    try {
      // 1. Prepare multi-platform content JSON
      const contentMap = { default: campaignBrief };
      selectedDashboardPlatforms.forEach(platform => {
        const variant = selectedVariants[platform] || 'viral_reach';
        const pContent = generatedData?.content?.[platform]?.[variant];
        if (pContent) contentMap[platform] = pContent;
      });
      
      // Set specific default if possible
      const currentVariant = selectedVariants[previewPlatform] || 'viral_reach';
      const currentContent = generatedData?.content?.[previewPlatform]?.[currentVariant];
      if (currentContent) contentMap.default = currentContent;
      
      const contentJson = JSON.stringify(contentMap);

      // 2. Resolve image + media_type. For carousel/document posts we must
      // pass media_type='document' so the Drafts page renders a PDF preview
      // and so /post selects the LinkedIn document publisher at publish time.
      let imageUrl = null;
      let mediaType = null;
      let thumbnailUrl = null;
      let allSlideUrls = null;  // JSON-stringified at send; populated when a carousel is picked
      if (selectedVisual === 'uploaded') {
        imageUrl = uploadedImageUrl;
        // Sniff uploaded URL for document extensions.
        if (uploadedImageUrl && /\.(pdf|docx?|pptx?)(\?|$)/i.test(uploadedImageUrl)) {
          mediaType = 'document';
        }
      } else if (typeof selectedVisual === 'number') {
        const flat = flattenVisuals(generatedData);
        const sel = flat[selectedVisual];
        if (sel) {
          imageUrl   = sel.url;
          mediaType  = sel.media_type
            || (/\.(pdf|docx?|pptx?)(\?|$)/i.test(sel.url || '') ? 'document' : null);
          // Capture slide-1 PNG when the picked variant IS a carousel.
          // Without this the normal-pick path was saving carousels
          // with thumbnail_url=NULL — Drafts then showed "Preview
          // unavailable" instead of the slide cover.
          if (mediaType === 'document') {
            thumbnailUrl = sel.thumbnail_url
              || (Array.isArray(sel.slides) && sel.slides[0]?.png_s3_url)
              || null;
          }
          if (mediaType === 'document' && Array.isArray(sel.slides)) {
            allSlideUrls = sel.slides.map(s => s?.png_s3_url).filter(Boolean);
          }
        }
      }
      // Fallback: if we picked nothing but the campaign generated a carousel,
      // use that.
      if (!imageUrl && Array.isArray(generatedData?.visuals)) {
        const docVisual = generatedData.visuals.find(
          v => v?.media_type === 'document' || v?.pipeline === 'carousel-gpt-image-2'
        );
        if (docVisual) {
          imageUrl     = docVisual.url;
          mediaType    = 'document';
          // Slide-1 PNG so the Drafts grid can render a fast, reliable <img>.
          thumbnailUrl = docVisual.thumbnail_url
            || (Array.isArray(docVisual.slides) && docVisual.slides[0]?.png_s3_url)
            || null;
          if (Array.isArray(docVisual.slides)) {
            allSlideUrls = docVisual.slides.map(s => s?.png_s3_url).filter(Boolean);
          }
        }
      }

      await authAxios.post('/drafts', {
        content: contentJson,
        image_url: imageUrl,
        thumbnail_url: thumbnailUrl,
        slide_thumbnail_urls: allSlideUrls && allSlideUrls.length ? JSON.stringify(allSlideUrls) : null,
        media_type: mediaType,
        targets: selectedTargets
      });
      toast.success("Draft saved! 📝");
    } catch (err) {
      console.error(err);
      toast.error("Failed to save draft");
    }
  };

  const handleGenerateContentFiltered = async () => {
    if (postStrategy === 'schedule') {
      // Pre-flight validation — was letting the request fire with empty
      // brief / 0 days / no platforms, then surfacing the resulting 422
      // as an unhelpful "[object Object]" browser dialog. Now every
      // missing input gets a friendly toast BEFORE any network call.
      if (!campaignBrief.trim()) {
        toast.error('Please enter a campaign brief first 🚩');
        return;
      }
      const _days = parseInt(scheduledDays, 10);
      if (!Number.isFinite(_days) || _days <= 0) {
        toast.error('Plan duration must be at least 1 day.');
        return;
      }
      if (_days > 30) {
        toast.error('Plan duration is capped at 30 days.');
        return;
      }
      if (!Array.isArray(selectedDashboardPlatforms) || selectedDashboardPlatforms.length === 0) {
        toast.error('Please select at least one channel 🚩');
        return;
      }

      setIsGenerating(true);
      try {
        setCampaignPlan([]); // Clear old context
        const formData = new FormData();
        formData.append('brief', campaignBrief);
        formData.append('platforms', JSON.stringify(selectedDashboardPlatforms));
        formData.append('days', String(_days));
        // Lock content_type globally — planner emits this on every slot
        // instead of mixing Text/Image per slot on its own.
        formData.append('post_type', dashboardPostType);
        // Propagate the picked style to bulk-schedule too so every
        // generated slot honours the user's visual choice. Skipped for
        // Auto so /generate-plan behaves exactly as before.
        if (imageStyle && imageStyle !== 'auto') {
          formData.append('image_style', imageStyle);
        }

        if (contextFiles && contextFiles.length > 0) {
          contextFiles.forEach(file => {
            formData.append('context_files', file);
          });
        }

        const response = await authAxios.post('/generate-plan', formData, {
          headers: { 'Content-Type': 'multipart/form-data' },
          params: { product_name: selectedProduct }
        });

        // Robust delay for UX feel
        setTimeout(() => {
          setCampaignPlan(response.data.plan || []);
          setDashboardStep('plan');
          setIsGenerating(false);
          setShowGenerationModal(false); // Explicitly close modal
        }, 1500);

      } catch (err) {
        setIsGenerating(false);
        console.error("Plan Generation Error:", err);
        // formatApiError handles: string (as-is), array-of-objects (FastAPI
        // 422), and plain objects. Previously the raw `detail` (often an
        // array) fell through to toast.error which stringified to
        // "[object Object]".
        toast.error(
          formatApiError(err.response?.data?.detail, 'Failed to generate posting plan. Please try again.')
        );
      }
    } else {
      handleGenerateContent();
    }
  };

  const togglePublishPlatform = (p) => {
    setSelectedDashboardPlatforms(prev => prev.includes(p) ? prev.filter(i => i !== p) : [...prev, p]);
  };

  const [isPublishing, setIsPublishing] = React.useState(false);

  const handlePublishNow = async () => {
    setIsPublishing(true);
    // Only publish to platforms that have at least one account selected
    let platformsToPublish = selectedDashboardPlatforms.filter(p => (selectedTargets[p] && selectedTargets[p].length > 0));
    
    if (platformsToPublish.length === 0) {
      toast.error("Please select at least one account to publish to.");
      setIsPublishing(false);
      return;
    }

    // Instagram requires media on every post (Meta Graph API rejects
    // text-only). Strip IG from the publish set when there's genuinely no
    // media attached — i.e.:
    //   - image post with no visualist selection AND no upload
    //   - text post (no media ever)
    // Do NOT strip for video/document posts that already have a user-
    // uploaded URL — those ARE media and IG can post them as Reels.
    const hasAnyMedia =
      hasImageSelected ||
      (dashboardPostType === 'video' && !!uploadedImageUrl) ||
      (dashboardPostType === 'document' && !!uploadedImageUrl);
    if (!hasAnyMedia) {
      platformsToPublish = platformsToPublish.filter(p => p !== 'instagram');
    }

    try {
      // 1. Resolve image / media — for non-image agent post types the
      //    user-uploaded media URL is authoritative (VisualGallery doesn't
      //    render). For image posts, read from the visual-gallery selection
      //    as before.
      let imageUrl = null;
      if (dashboardPostType === 'video' || dashboardPostType === 'document') {
        imageUrl = uploadedImageUrl;
      } else if (dashboardPostType === 'text') {
        imageUrl = null;
      } else if (selectedVisual === 'uploaded') {
        imageUrl = uploadedImageUrl;
      } else if (typeof selectedVisual === 'number') {
        const flat = flattenVisuals(generatedData);
        if (flat[selectedVisual]) imageUrl = flat[selectedVisual].url;
      }

      // 2. Consolidate targets and content map
      const allTargets = {};
      const contentMap = {};
      
      for (const platform of platformsToPublish) {
        const selectedForPlatform = selectedTargets[platform] || [];
        if (selectedForPlatform.length > 0) {
          allTargets[platform] = selectedForPlatform;
          const variantKey = selectedVariants[platform] || 'viral_reach';
          contentMap[platform] = generatedData?.content?.[platform]?.[variantKey] || '';
        }
      }

      if (Object.keys(allTargets).length === 0) {
        toast.error("No accounts selected for publishing.");
        setIsPublishing(false);
        return;
      }

      // Open the per-platform progress popup BEFORE firing the /post call.
      // Each row starts in the "publishing…" spinner state; results update
      // once the backend responds with its `results` map.
      setPublishProgress({
        open: true,
        platforms: Object.keys(allTargets),
        results: null,
      });

      // 3. Single POST call. When the user is in a non-image agent flow
      // (video / document) uploadedImageUrl already holds the S3 URL of
      // their media; forward the media_type so the publisher routes to
      // the right platform API.
      const effectiveMediaType = dashboardPostType === 'image' ? null : dashboardPostType;
      // Same Campaign-Brief DNA resolver as App.jsx handlePublish + this
      // file's handleSchedule. This path was previously missing it — every
      // post published via the Campaign Brief "Publish Now" button was
      // landing with dna_product_id=NULL, which is why the Brand Filter
      // couldn't isolate them by Spenzo / Zyntegrate / etc.
      const dnaProductId =
        selectedProduct === '__none__' ? null
        : selectedProduct ? selectedProduct
        : '__company__';
      const res = await authAxios.post('/post', {
        content: JSON.stringify(contentMap),
        image_url: imageUrl,
        media_type: effectiveMediaType,
        targets: allTargets,
        dna_product_id: dnaProductId,
      });

      const resData = res.data;
      const messages = Object.entries(resData.results || {}).flatMap(([plat, accArr]) =>
        accArr.map(acc => `${plat.toUpperCase()} (${acc.name}):\n${acc.status}`)
      );

      // Update the progress popup with the resolved per-platform results.
      // The modal auto-derives 'success' / 'failure' / 'mixed' per platform
      // from the array of per-account statuses and auto-closes ~4s after
      // all rows reach a terminal state.
      setPublishProgress(prev => ({ ...prev, results: resData.results || {} }));

      // Publish results as a toast — was a big multi-line alert() that
      // required the user to click OK. For failure-mixed cases the toast
      // still communicates the outcome without blocking the page. If any
      // per-platform failure was in the message, use an error toast; else
      // success.
      if (messages.length > 0) {
        const anyFail = messages.some(m => /(fail|error|not authorized)/i.test(m));
        (anyFail ? toast.error : toast.success)("Publish Results: " + messages.join(' | '));
      } else {
        toast.success("Publishing complete!");
      }
      if (fetchPublished) fetchPublished(true);
      setDashboardStep('brief');
    } catch (err) {
      console.error("Publish Error:", err);
      toast.error("Error publishing: " + (err.response?.data?.detail || err.message));
      // Mark every platform as failed in the progress popup so it doesn't
      // sit forever on the pending spinner state after a top-level error.
      setPublishProgress(prev => ({
        ...prev,
        results: Object.fromEntries(
          prev.platforms.map(p => [p, [{ status: 'Failed ❌: ' + (err?.response?.data?.detail || err?.message || 'unknown') }]])
        ),
      }));
    } finally {
      setIsPublishing(false);
    }
  };

  const handleSchedule = async () => {
    if (!scheduledDate || !scheduledTime) {
      toast.error("Please select both date and time to schedule");
      return;
    }
    
    setIsScheduling(true);
    try {
      // 1. Prepare multi-platform targets
      const targetsMap = {};
      selectedDashboardPlatforms.forEach(p => {
        const platformConnections = connections?.[p] || [];
        if (platformConnections.length > 0) {
          const selectedForPlatform = selectedTargets[p] || [];
          targetsMap[p] = selectedForPlatform.length > 0 ? selectedForPlatform : platformConnections.map(c => c.account_id || c.id);
        }
      });
      
      if (Object.keys(targetsMap).length === 0) {
        toast.error("Please select at least one target account.");
        setIsScheduling(false);
        return;
      }

      // 2. Resolve content
      const contentMap = { default: campaignBrief };
      selectedDashboardPlatforms.forEach(platform => {
        const variant = selectedVariants[platform] || 'viral_reach';
        const pContent = generatedData?.content?.[platform]?.[variant];
        if (pContent) contentMap[platform] = pContent;
      });
      const contentJson = JSON.stringify(contentMap);

      // 3. Resolve image / media (same rule as handlePublishNow — non-image
      //    types use the user-uploaded S3 URL directly, text has no media).
      // Also resolve carousel thumbnail (slide-1 PNG + full slide list) —
      // same logic handleSaveDraft uses. Without this, scheduling a carousel
      // stored thumbnail_url=NULL and Scheduled.jsx fell back to the ugly
      // generic "PDF DOCUMENT CAROUSEL" tile even though Drafts/Published
      // showed the actual slide.
      let imageUrl = null;
      let thumbnailUrl = null;
      let allSlideUrls = null;
      if (dashboardPostType === 'video' || dashboardPostType === 'document') {
        imageUrl = uploadedImageUrl;
      } else if (dashboardPostType === 'text') {
        imageUrl = null;
      } else if (selectedVisual === 'uploaded') {
        imageUrl = uploadedImageUrl;
      } else if (typeof selectedVisual === 'number') {
        const flat = flattenVisuals(generatedData);
        const sel = flat[selectedVisual];
        if (sel) {
          imageUrl = sel.url;
          if (sel.media_type === 'document' || /\.(pdf|docx?|pptx?)(\?|$)/i.test(sel.url || '')) {
            thumbnailUrl = sel.thumbnail_url
              || (Array.isArray(sel.slides) && sel.slides[0]?.png_s3_url)
              || null;
            if (Array.isArray(sel.slides)) {
              allSlideUrls = sel.slides.map(s => s?.png_s3_url).filter(Boolean);
            }
          }
        }
      }
      // Carousel fallback — user didn't explicitly pick a variant but the
      // campaign generated a document. Same lookup handleSaveDraft does.
      if (!thumbnailUrl && Array.isArray(generatedData?.visuals)) {
        const docVisual = generatedData.visuals.find(
          v => v?.media_type === 'document' || v?.pipeline === 'carousel-gpt-image-2'
        );
        if (docVisual) {
          if (!imageUrl) imageUrl = docVisual.url;
          thumbnailUrl = docVisual.thumbnail_url
            || (Array.isArray(docVisual.slides) && docVisual.slides[0]?.png_s3_url)
            || null;
          if (Array.isArray(docVisual.slides)) {
            allSlideUrls = docVisual.slides.map(s => s?.png_s3_url).filter(Boolean);
          }
        }
      }

      const scheduled_for = new Date(`${scheduledDate}T${scheduledTime}`).toISOString();
      const timezone = selectedTimezone;

      // Mirror the resolver in App.jsx handlePublish — the Campaign-Brief
      // DNA picker rides along on the scheduled row so when the scheduler
      // later promotes this into a PublishedPost the dna_product_id is
      // carried forward (backend already does that propagation).
      const dnaProductId =
        selectedProduct === '__none__' ? null
        : selectedProduct ? selectedProduct
        : '__company__';

      await authAxios.post('/schedule', {
        content: contentJson,
        image_url: imageUrl,
        media_type: dashboardPostType === 'image' ? null : dashboardPostType,
        thumbnail_url: thumbnailUrl,
        slide_thumbnail_urls: allSlideUrls && allSlideUrls.length ? JSON.stringify(allSlideUrls) : null,
        targets: targetsMap,
        scheduled_for,
        timezone,
        dna_product_id: dnaProductId,
      });
      
      toast.success("Post scheduled successfully! 🚀");
      if (fetchScheduled) fetchScheduled(true);
      setDashboardStep('brief');
      setActiveActionTab('publish');
    } catch (err) {
      console.error(err);
      toast.error("Failed to schedule post");
    } finally {
      setIsScheduling(false);
    }
  };

  const handleApprovePlan = async () => {
    setIsGenerating(true);
    try {
      // 1. Resolve targets — same fallback rule as the single-post schedule
      // flow at handleSchedulePost: if the user didn't explicitly pick an
      // account picker on this platform, default to ALL connected accounts.
      // The strategy/plan flow doesn't show the account picker step, so
      // requiring an explicit pick here would always fail for users who
      // just have "1 LinkedIn connected, schedule it".
      const targetsMap = {};
      selectedDashboardPlatforms.forEach(p => {
        const platformConnections = connections?.[p] || [];
        if (platformConnections.length === 0) return; // platform has no connected account at all
        const selectedForPlatform = selectedTargets[p] || [];
        targetsMap[p] = selectedForPlatform.length > 0
          ? selectedForPlatform
          : platformConnections.map(c => c.account_id || c.id);
      });

      if (Object.keys(targetsMap).length === 0) {
        // None of the selected platforms have any connected account.
        // Give the user a clear next step instead of a generic error.
        const missing = selectedDashboardPlatforms
          .filter(p => !(connections?.[p] || []).length)
          .map(p => p.charAt(0).toUpperCase() + p.slice(1))
          .join(', ');
        toast.error(
          `No connected account on: ${missing || 'the selected platforms'}. Connect a social account first, then try again.`
        );
        setIsGenerating(false);
        return;
      }

      // 2. Start campaign
      await authAxios.post('/start-campaign', {
        brief: campaignBrief,
        plan: campaignPlan,
        targets: targetsMap
      });

      // BOTH static and research slots enter the review queue now. Static
      // rows are pre-generated for caption/image review; research rows show
      // their topic + scheduled time and require the user's approval before
      // they're handed to the publisher. Nothing auto-schedules.
      //
      // IMPORTANT: this step is `schedule_review`, NOT `review`. The
      // single-post (instant) flow uses `dashboardStep='review'` for its
      // legacy review screen (StrategicBlueprint + variant tabs). Reusing
      // that step name here would steer the instant flow into the strategy
      // card grid, which has the wrong shape (per-slot cards instead of
      // per-platform variant tabs).
      setDashboardStep('schedule_review');
    } catch (err) {
      console.error("Campaign Start Error:", err);
      toast.error("Failed to start campaign. Please try again.");
    } finally {
      setIsGenerating(false);
    }
  };

  const handleImageUpload = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      setIsUploadingImage(true);
      const formData = new FormData();
      formData.append('file', file);
      const res = await authAxios.post('/upload-image', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      if (res.data.url) {
        setUploadedImageUrl(res.data.url);
        setSelectedVisual('uploaded');
      }
    } catch (err) {
      console.error("Upload failed", err);
      toast.error("Failed to upload image. Please check backend config.");
    } finally {
      setIsUploadingImage(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const startEditing = (variant, currentText) => {
    setEditingVariant(variant);
    setTempText(currentText);
  };

  const saveEdit = () => {
    if (!editingVariant) return;
    const newData = { ...generatedData };
    if (!newData.content[activeReviewPlatform]) newData.content[activeReviewPlatform] = {};
    newData.content[activeReviewPlatform][editingVariant] = tempText;
    setGeneratedData(newData);
    setEditingVariant(null);
  };
  const previewAccount = (connections[previewPlatform] && connections[previewPlatform].length > 0)
    ? connections[previewPlatform][0]
    : { name: 'Admin', profile_picture_url: null };

  // Shared props for PublishingControls — it is rendered in two spots:
  // inline under "Ready to Publish" on mobile, and in the sidebar on
  // desktop. Keeping the props in one object avoids drift between them.
  const publishingControlsProps = {
    dashboardStep,
    activeActionTab,
    setActiveActionTab,
    scheduledDate,
    setScheduledDate,
    scheduledTime,
    setScheduledTime,
    selectedTimezone,
    setSelectedTimezone,
    selectedDashboardPlatforms,
    connections,
    hasImageSelected: selectedVisual !== 'none',
    selectedTargets,
    toggleTarget,
    handlePublishNow,
    isPublishing,
    handleSchedule,
    isScheduling,
    handleSaveDraft,
  };

  return (
    <div className="min-h-screen bg-white py-4 px-4 overflow-hidden">
      <div className="max-w-[1600px] mx-auto flex flex-col h-full">
        <button
          onClick={() => {
            // On the Strategy Plan step, go back to the brief instead of all
            // the way out — lets the user tweak the brief without re-picking
            // the post type.
            if (dashboardStep === 'plan') {
              setDashboardStep('brief');
            } else {
              setActiveTab('create');
            }
          }}
          className="mb-4 self-start w-fit inline-flex items-center gap-2 bg-[#2B2926] hover:bg-[#F55600] text-white rounded-lg px-3.5 py-2 font-semibold text-[10px] uppercase tracking-widest shadow-sm transition-colors"
        >
          <ChevronLeft className="w-3.5 h-3.5" />
          {dashboardStep === 'plan' ? 'Back to Campaign Brief' : 'Back to Selection'}
        </button>
        <StepIndicator dashboardStep={dashboardStep} />

      {dashboardStep === 'brief' ? (
        <>
          {/* Post-type selector - Optimized for standard desktop */}
          <div className="max-w-5xl mx-auto mb-4 px-2">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-[11px] font-semibold text-[#2B2926] uppercase tracking-widest">Select Post Type</span>
              {dashboardPostType === 'document' && (
                <span className="text-[10px] font-semibold text-white bg-[#10B981] border border-[#10B981] rounded-full px-2.5 py-0.5 shadow-sm">LinkedIn only</span>
              )}
            </div>
            <div className="grid grid-cols-2 md:flex md:flex-row items-center justify-center gap-2">
              {[
                { id: 'text', label: 'Text', desc: 'Copy-only', icon: Type },
                { id: 'image', label: 'Image', desc: 'AI Visuals', icon: ImageIcon },
                { id: 'video', label: 'Video', desc: 'Video upload', icon: Video },
                { id: 'document', label: 'Document', desc: 'PDF Carousel', icon: FileText },
              ].map(opt => {
                const isActive = dashboardPostType === opt.id;
                const Icon = opt.icon;
                return (
                  <button
                    key={opt.id}
                    type="button"
                    onClick={() => {
                      setDashboardPostType(opt.id);
                      if (opt.id === 'document') {
                        setSelectedDashboardPlatforms(['linkedin']);
                      } else if (opt.id === 'text') {
                        setSelectedDashboardPlatforms(prev => (prev || []).filter(p => p !== 'instagram'));
                      }
                    }}
                    className={`flex-1 w-full md:min-w-[140px] lg:min-w-[155px] flex items-center gap-2.5 p-2.5 rounded-[18px] border-2 transition-all duration-300 relative group
                      ${isActive
                        ? 'border-[#F55600] bg-[#F55600] text-white shadow-lg shadow-orange-200 scale-[1.02]'
                        : 'border-[#F55600] bg-white text-[#F55600] hover:scale-[1.01]'
                      }`}
                  >
                    <div className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 transition-colors
                      ${isActive ? 'bg-white/10 text-white' : 'bg-white text-[#F55600] border-2 border-[#F55600]/20'}`}>
                      <Icon className="w-4 h-4" />
                    </div>
                    <div className="text-left">
                      <div className={`text-[12px] font-semibold tracking-tight leading-none mb-1 ${isActive ? 'text-white' : 'text-[#F55600]'}`}>{opt.label}</div>
                      <div className={`text-[9px] font-semibold uppercase tracking-widest leading-none ${isActive ? 'text-white' : 'text-[#2B2926] opacity-100'}`}>{opt.desc}</div>
                    </div>
                    {isActive && (
                      <div className="absolute top-1.5 right-1.5">
                        <div className="w-3.5 h-3.5 bg-white rounded-full flex items-center justify-center">
                          <CheckCircle2 className="w-2.5 h-2.5 text-[#F55600]" />
                        </div>
                      </div>
                    )}
                  </button>
                );
              })}
            </div>
          </div>

        <CampaignBrief
          campaignBrief={campaignBrief}
          setCampaignBrief={setCampaignBrief}
          briefError={briefError}
          setBriefError={setBriefError}
          selectedDashboardPlatforms={selectedDashboardPlatforms}
          setSelectedDashboardPlatforms={setSelectedDashboardPlatforms}
          postType={dashboardPostType}
          postStrategy={postStrategy}
          setPostStrategy={setPostStrategy}
          scheduledDate={scheduledDate}
          setScheduledDate={setScheduledDate}
          scheduledTime={scheduledTime}
          setScheduledTime={setScheduledTime}
          selectedTimezone={selectedTimezone}
          setSelectedTimezone={setSelectedTimezone}
          scheduledDays={scheduledDays}
          setScheduledDays={setScheduledDays}
          handleGenerateContent={handleGenerateContentFiltered}
          contextFiles={contextFiles}
          setContextFiles={setContextFiles}
          logoPreview={logoPreview}
          setLogoPreview={setLogoPreview}
          setLogoImage={setLogoImage}
          selectedProduct={selectedProduct}
          setSelectedProduct={setSelectedProduct}
          aspectRatio={aspectRatio}
          setAspectRatio={setAspectRatio}
          imageStyle={imageStyle}
          setImageStyle={setImageStyle}
          dashboardPostType={dashboardPostType}
          productReferenceImages={productReferenceImages}
          setProductReferenceImages={setProductReferenceImages}
          authAxios={authAxios}
          user={user}
          navigateToSettingsTab={navigateToSettingsTab}
        />
        </>
      ) : dashboardStep === 'plan' ? (
        <CampaignPlanTable
          plan={campaignPlan}
          setPlan={setCampaignPlan}
          setDashboardStep={setDashboardStep}
          handleGenerateContent={handleApprovePlan}
        />
      ) : dashboardStep === 'schedule_review' ? (
        // Strategy/schedule flow ONLY. The instant single-post flow uses
        // dashboardStep='review' for the legacy StrategicBlueprint screen
        // below — keep them separated so the right UI shows for each.
        <CampaignReviewQueue
          authAxios={authAxios}
          setDashboardStep={setDashboardStep}
          onAllApproved={() => {
            // Auto-bounce back to the brief screen once every static post
            // has been approved (or rejected). The user can return to the
            // Scheduled tab to see everything booked.
            setDashboardStep('brief');
          }}
        />
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 animate-in fade-in slide-in-from-right-4 duration-700">
          <div className="lg:col-span-2 space-y-2">
            <div className="flex items-center justify-between p-2.5 bg-white rounded-2xl border-2 border-slate-350 shadow-sm">
              <button 
                onClick={() => setShowBackConfirm(true)}
                className="flex items-center gap-2 px-3 py-2 bg-slate-50 hover:bg-[#F55600] text-[#2B2926] hover:text-white font-bold text-[9px] transition-all rounded-xl border border-slate-200 hover:border-[#F55600] shadow-sm group uppercase tracking-widest"
              >
                <div className="w-3.5 h-3.5 rounded-md bg-slate-100 group-hover:bg-white flex items-center justify-center border border-slate-200 group-hover:border-white transition-all text-[#2B2926] group-hover:text-[#F55600]">
                  <Plus className="w-2.5 h-2.5 rotate-45" strokeWidth={3} />
                </div>
                Back to Brief
              </button>
              <div className="bg-white text-[#2B2926] text-[8px] font-semibold px-3 py-1 rounded-full uppercase tracking-widest border border-[#2B2926]/20">Reviewing AI Output</div>
            </div>

            <StrategicBlueprint
              researchReport={generatedData?.research_report}
              culturalCalendar={generatedData?.cultural_calendar}
            />

            <div className="bg-white rounded-2xl px-5 py-4 border-2 border-slate-350 shadow-lg relative overflow-hidden">
              <div className="absolute top-0 right-0 w-64 h-64 bg-gradient-to-br from-[#2B2926]/[0.03] to-transparent rounded-full blur-3xl -z-0"></div>
              <div className="flex flex-wrap items-center justify-between gap-2 mb-4 relative z-10">
                <div className="flex items-center gap-3 min-w-0">
                  <div className="w-10 h-10 rounded-2xl bg-[#2B2926]/[0.04] flex items-center justify-center text-[#2B2926] border border-[#2B2926]/15 shrink-0">
                    <Cpu className="w-5 h-5" />
                  </div>
                  <div className="flex items-end gap-2 flex-wrap">
                    <h3 className="text-lg sm:text-xl font-semibold text-[#2B2926] tracking-tight">AI Content</h3>
                    <span className="text-[10px] font-bold text-[#065F46] border border-[#065F46]/55 bg-white px-2.5 py-0.5 rounded-full uppercase tracking-[0.14em] mb-0.5 whitespace-nowrap">Generator</span>
                  </div>
                </div>
                <div className="flex w-full sm:w-auto gap-2 shrink-0">
                  <button
                    onClick={handleGenerateContent}
                    className="flex-1 sm:flex-none justify-center px-3 sm:px-4 py-2 bg-[#2B2926]/5 text-[#2B2926] rounded-xl text-[9px] font-semibold border-2 border-[#2B2926]/15 hover:bg-[#F55600] hover:text-white hover:border-[#F55600] transition-all flex items-center gap-1.5 uppercase tracking-widest shadow-sm whitespace-nowrap"
                  >
                    <RefreshCw className="w-3 h-3" /> Re-Analyze
                  </button>
                  <div className="flex-1 sm:flex-none flex items-center justify-center px-3 sm:px-4 py-2 bg-white text-[#2B2926] rounded-xl text-[9px] font-semibold border border-[#2B2926]/20 uppercase tracking-widest whitespace-nowrap">
                    <span className="sm:hidden">3 Variants</span>
                    <span className="hidden sm:inline">3 Strategic Variants</span>
                  </div>
                </div>
              </div>

              <div className="flex flex-wrap gap-1 mb-3 p-1 bg-[#2B2926]/[0.04] rounded-xl border border-[#2B2926]/15 w-fit max-w-full relative z-10">
                {selectedDashboardPlatforms.map(p => {
                  const isActive = activeReviewPlatform === p;
                  const iconNode = p === 'linkedin' ? <img src="/linkedlin.jpg" className={`w-3.5 h-3.5 object-contain transition-transform duration-300 ${isActive ? 'scale-110' : 'opacity-60 grayscale group-hover/tab:grayscale-0 group-hover/tab:opacity-100'}`} alt="LinkedIn" /> : p === 'twitter' ? <XIcon className={`w-3.5 h-3.5 transition-transform duration-300 ${isActive ? 'scale-110' : 'opacity-60 grayscale group-hover/tab:grayscale-0 group-hover/tab:opacity-100'}`} /> : p === 'instagram' ? <img src="/instagram.jpg" className={`w-3.5 h-3.5 object-contain transition-transform duration-300 ${isActive ? 'scale-110' : 'opacity-60 grayscale group-hover/tab:grayscale-0 group-hover/tab:opacity-100'}`} alt="Instagram" /> : <img src="/facebook.png" className={`w-3.5 h-3.5 object-contain transition-transform duration-300 ${isActive ? 'scale-110' : 'opacity-60 grayscale group-hover/tab:grayscale-0 group-hover/tab:opacity-100'}`} alt="Facebook" />;

                  // Premium branded color schemes
                  const brandColors = {
                    linkedin: { bg: 'bg-[#0077b5]', text: 'text-[#0077b5]' },
                    twitter: { bg: 'bg-[#2B2926]', text: 'text-[#2B2926]' },
                    instagram: { bg: 'bg-gradient-to-tr from-[#f09433] via-[#e6683c] to-[#bc1888]', text: 'text-[#E4405F]' },
                    facebook: { bg: 'bg-[#1877F2]', text: 'text-[#1877F2]' }
                  };

                  const { bg, text } = brandColors[p] || brandColors.linkedin;

                  return (
                    <div key={p} className="relative group/tab shrink-0">
                      <button
                        onClick={() => setActiveReviewPlatform(p)}
                        className={`px-2.5 sm:px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] transition-colors duration-300 relative flex items-center gap-1.5 sm:gap-2 rounded-lg whitespace-nowrap z-10
                          ${isActive ? 'text-white' : 'text-[#67655E] hover:text-[#2B2926]'}`}>
                        {iconNode}
                        {p === 'twitter' ? 'X' : p}
                      </button>

                      {isActive && (
                        <motion.div
                          layoutId="activeTab"
                          className={`absolute inset-0 ${bg} rounded-lg z-0`}
                          transition={{ type: "spring", bounce: 0.25, duration: 0.5 }}
                        />
                      )}

                      {selectedDashboardPlatforms.length > 1 && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelectedDashboardPlatforms(prev => prev.filter(i => i !== p));
                            if (activeReviewPlatform === p) setActiveReviewPlatform(selectedDashboardPlatforms.find(i => i !== p));
                          }}
                          className={`absolute -top-1.5 -right-1.5 w-4 h-4 bg-white text-[#67655E] rounded-full flex items-center justify-center opacity-0 group-hover/tab:opacity-100 transition-all hover:bg-[#F55600] hover:text-white shadow-sm border border-[#2B2926]/20 z-20`}
                        >
                          <Plus className="w-2.5 h-2.5 rotate-45" />
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>

              <ContentVariants 
                generatedData={generatedData}
                activeReviewPlatform={activeReviewPlatform}
                selectedVariants={selectedVariants}
                setSelectedVariants={setSelectedVariants}
                editingVariant={editingVariant}
                setEditingVariant={setEditingVariant}
                tempText={tempText}
                setTempText={setTempText}
                handleSaveEdit={saveEdit}
                handleGenerateContent={handleGenerateContent}
              />

              <ReviewActions
                setDashboardStep={setDashboardStep}
                handleGenerateContent={handleGenerateContent}
              />
            </div>

            {/* Mobile only: surface the Post Placement panel right under
                the "Ready to Publish" button. On desktop (lg+) it stays in
                the right-hand sidebar instead. */}
            {dashboardStep === 'publish' && (
              <div className="lg:hidden">
                <PublishingControls {...publishingControlsProps} />
              </div>
            )}

            {/* Swap the AI VisualGallery for a plain upload card when the
                user picked Text / Video / Document — those types either
                need no media at all (text) or the user is providing it
                (video/document). The shared handleMediaUpload does a
                presigned direct-to-S3 PUT so uploadedImageUrl ends up as
                an https URL that downstream publish logic just passes
                through. */}
            {dashboardPostType === 'image' ? (
              <VisualGallery
                generatedData={generatedData}
                selectedVisual={selectedVisual}
                setSelectedVisual={setSelectedVisual}
                uploadedImageUrl={uploadedImageUrl}
                fileInputRef={fileInputRef}
                handleImageUpload={handleImageUpload}
                isUploadingImage={isUploadingImage}
                authAxios={authAxios}
                onRegenerate={handleRegenerateVisuals}
                onCustomPrompt={handleCustomPrompt}
                user={user}
              />
            ) : dashboardPostType === 'text' ? (
              // Text posts don't need a media picker at all. Render a compact
              // info card so the review step still feels complete.
              <div className="bg-white rounded-2xl p-5 border-2 border-orange-100 shadow-sm text-center">
                <div className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest mb-1">Text-only post</div>
                <div className="text-sm font-bold text-[#2B2926]">No media will be attached. Pick a variant on the right and hit Publish.</div>
              </div>
            ) : (
              // Video / Document — reuse the same presigned media upload
              // shared with the manual Posts flow. For Video specifically
              // we ALSO render the AI generator card above the upload box
              // so the user can choose to either generate or upload.
              <>
                {dashboardPostType === 'video' && (
                  <VideoGenerator
                    authAxios={authAxios}
                    campaignBrief={campaignBrief}
                    uploadedImageUrl={uploadedImageUrl}
                    setUploadedImageUrl={setUploadedImageUrl}
                    setMediaType={setMediaType}
                    selectedProduct={selectedProduct}
                  />
                )}
              {(() => {
                // Carousel pipeline already produced a PDF for document
                // posts: surface the inline preview here so the user can
                // review what was generated. Falls back to the legacy
                // "upload your own file" widget for video posts or when
                // the carousel pipeline is disabled / failed.
                const generatedDoc = dashboardPostType === 'document'
                  ? (generatedData?.visuals || []).find(
                      v => v?.media_type === 'document'
                        || v?.pipeline === 'carousel-gpt-image-2'
                    )
                  : null;
                if (generatedDoc) {
                  return (
                    <div className="bg-white rounded-2xl p-5 border-2 border-[#F55600]/20 shadow-sm">
                      <div className="flex items-center justify-between mb-3">
                        <div>
                          <div className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest">
                            AI-generated carousel
                            <span className="ml-2 text-blue-700 bg-blue-50 border border-blue-100 rounded-full px-2 py-0.5 text-[8px]">
                              LinkedIn only
                            </span>
                          </div>
                          <div className="text-[9px] font-bold text-slate-600 mt-0.5">
                            {(generatedDoc.pdf_title || 'Carousel')} · {generatedDoc.slide_count || (generatedDoc.slides?.length ?? 0)} slides · Ready to publish
                          </div>
                        </div>
                        <a
                          href={generatedDoc.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-[9px] font-semibold text-[#F55600] hover:text-[#e65a2b] uppercase tracking-widest"
                        >
                          Open in tab
                        </a>
                      </div>
                      <div className="rounded-2xl overflow-hidden border border-slate-100 bg-slate-50 h-[420px]">
                        <CarouselSwiper
                          slides={generatedDoc.slides || []}
                          variant="preview"
                          enableKeyboard={false}
                        />
                      </div>
                      {Array.isArray(generatedDoc.slides) && generatedDoc.slides.length > 0 && (
                        <div className="mt-3 grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-2">
                          {generatedDoc.slides.map((s, i) => (
                            <div key={i} className="aspect-square rounded-lg overflow-hidden border border-slate-200 bg-slate-50 relative">
                              {s.png_s3_url ? (
                                <img
                                  src={s.png_s3_url}
                                  alt={`Slide ${s.slide_no || i + 1}`}
                                  className="w-full h-full object-cover"
                                />
                              ) : (
                                <div className="w-full h-full flex items-center justify-center text-[9px] text-slate-400">
                                  Slide {s.slide_no || i + 1}
                                </div>
                              )}
                              <div className="absolute bottom-1 left-1 bg-[#2B2926]/80 text-white text-[8px] font-semibold px-1.5 py-0.5 rounded">
                                {s.role || `Slide ${s.slide_no || i + 1}`}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                }
                return (
                  <div className="bg-white rounded-2xl p-5 border-2 border-[#F55600]/20 shadow-sm">
                    <div className="flex items-center justify-between mb-3">
                      <div>
                        <div className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest">
                          {dashboardPostType === 'video' ? 'Video upload' : 'Document upload'}
                          {dashboardPostType === 'document' && (
                            <span className="ml-2 text-blue-700 bg-blue-50 border border-blue-100 rounded-full px-2 py-0.5 text-[8px]">LinkedIn only</span>
                          )}
                        </div>
                        <div className="text-[9px] font-bold text-slate-600 mt-0.5">
                          {dashboardPostType === 'video'
                            ? 'Up to 1 GB. Videos longer than 2 min will hide the X account at publish.'
                            : 'PDF / DOCX / PPTX. Max 100 MB. Rendered as a swipeable carousel on LinkedIn.'}
                        </div>
                      </div>
                      {uploadedImageUrl && (
                        <button
                          type="button"
                          onClick={() => { setUploadedImageUrl(null); if (setMediaType) setMediaType(null); }}
                          className="text-[9px] font-semibold text-red-500 hover:text-red-700 uppercase tracking-widest"
                        >
                          Remove
                        </button>
                      )}
                    </div>

                    {!uploadedImageUrl ? (
                      <label className="block border-2 border-dashed border-[#F55600]/30 rounded-2xl p-8 text-center cursor-pointer hover:border-[#F55600] hover:bg-[#F55600]/5 transition-all">
                        <input
                          type="file"
                          className="hidden"
                          accept={
                            dashboardPostType === 'video'
                              ? 'video/*'
                              : 'application/pdf,.pdf,.doc,.docx,.ppt,.pptx'
                          }
                          onChange={(e) => handleMediaUpload && handleMediaUpload(e)}
                        />
                        <div className="text-sm font-semibold text-[#2B2926]">
                          Click to upload {dashboardPostType === 'video' ? 'a video' : 'a document'}
                        </div>
                        <div className="text-[10px] font-bold text-slate-600 mt-1 uppercase tracking-widest">
                          Direct to S3 — no lambda roundtrip
                        </div>
                      </label>
                    ) : (
                      <div className="rounded-2xl overflow-hidden border border-slate-100 bg-slate-50">
                        {dashboardPostType === 'video' ? (
                          <video src={uploadedImageUrl} controls className="w-full max-h-[320px] bg-[#2B2926]" />
                        ) : /\.pdf(\?|$)/i.test(uploadedImageUrl) ? (
                          <iframe
                            src={`${uploadedImageUrl}#toolbar=0&navpanes=0&view=Fit`}
                            title="Uploaded document preview"
                            className="w-full h-[320px] bg-white"
                          />
                        ) : (
                          <div className="flex items-center gap-3 p-4">
                            <div className="w-10 h-10 rounded-lg bg-white flex items-center justify-center text-[#F55600] font-semibold border border-[#F55600]/30 shadow-sm">PDF</div>
                            <div className="flex-1 min-w-0">
                              <div className="text-xs font-semibold text-[#2B2926] truncate">{uploadedImageUrl.split('/').pop().split('?')[0]}</div>
                              <div className="text-[10px] font-bold text-slate-600 uppercase tracking-widest">Document uploaded</div>
                            </div>
                          </div>
                        )}
                      </div>
                    )}

                    {mediaUploadProgress > 0 && mediaUploadProgress < 100 && (
                      <div className="w-full bg-slate-100 rounded-full h-1.5 mt-3 overflow-hidden">
                        <div className="h-full bg-[#F55600] transition-all duration-300" style={{ width: `${mediaUploadProgress}%` }} />
                      </div>
                    )}
                  </div>
                );
              })()}
              </>
            )}
          </div>

          {/* Right Sidebar */}
          <div className="space-y-3">
            <ReviewSidebar 
              selectedDashboardPlatforms={selectedDashboardPlatforms}
              previewPlatform={previewPlatform}
              setPreviewPlatform={setPreviewPlatform}
              previewAccount={previewAccount}
              generatedData={generatedData}
              selectedVariants={selectedVariants}
              selectedVisual={selectedVisual}
              uploadedImageUrl={uploadedImageUrl}
            />

            {/* Desktop only — on mobile this panel is rendered inline
                under "Ready to Publish" (see above). */}
            {dashboardStep === 'publish' && (
              <div className="hidden lg:block">
                <PublishingControls {...publishingControlsProps} />
              </div>
            )}
          </div>
        </div>
      )}
      <LoadingModal
        show={showGenerationModal}
        onClose={() => setShowGenerationModal(false)}
      />

      {/* Per-platform publish progress popup — spinner per platform until
          /post resolves, then swaps to ✅ / ❌ per platform (auto-closes). */}
      <PublishingProgressModal
        open={publishProgress.open}
        platforms={publishProgress.platforms}
        results={publishProgress.results}
        onClose={() => setPublishProgress({ open: false, platforms: [], results: null })}
      />

      {/* Back-to-Brief confirmation — protects the user from accidentally
          nuking a generated post. Three outcomes: discard, cancel, or save as
          draft before going back. */}
      {showBackConfirm && (
        <div className="fixed inset-0 z-[99999] flex items-center justify-center p-4">
          <div
            className="absolute inset-0 bg-slate-900/50 backdrop-blur-sm"
            onClick={() => !isSavingDraftBack && setShowBackConfirm(false)}
          />
          <div className="relative bg-white rounded-3xl shadow-[0_30px_90px_rgba(0,0,0,0.25)] border border-slate-100 w-full max-w-md p-6 animate-in fade-in zoom-in-95 duration-200">
            <div className="flex items-start gap-3 mb-4">
              <div className="w-10 h-10 rounded-2xl bg-orange-50 border border-orange-100 flex items-center justify-center shrink-0">
                <Plus className="w-5 h-5 rotate-45 text-[#F55600]" />
              </div>
              <div className="flex-1">
                <h3 className="text-sm font-semibold text-[#2B2926] tracking-tight">
                  Discard this generated post?
                </h3>
                <p className="text-[11px] text-slate-500 mt-1 leading-relaxed">
                  Going back to the brief will remove the content and visuals generated for this campaign. You can save them as a draft first if you want to keep them.
                </p>
              </div>
            </div>

            <div className="flex flex-col gap-2 mt-5">
              <button
                onClick={discardAndGoBack}
                disabled={isSavingDraftBack}
                className="w-full py-3 bg-[#F55600] hover:bg-[#e65a2b] text-white text-[10px] font-semibold uppercase tracking-widest rounded-xl shadow-md transition-all disabled:opacity-50 active:scale-[0.98]"
              >
                Confirm &amp; Discard
              </button>
              <button
                onClick={saveDraftAndGoBack}
                disabled={isSavingDraftBack}
                className="w-full py-3 bg-white hover:bg-slate-50 text-[#2B2926] text-[10px] font-semibold uppercase tracking-widest rounded-xl border border-slate-200 transition-all disabled:opacity-50 active:scale-[0.98]"
              >
                {isSavingDraftBack ? 'Saving…' : 'Save as Draft'}
              </button>
              <button
                onClick={() => setShowBackConfirm(false)}
                disabled={isSavingDraftBack}
                className="w-full py-2 text-[10px] font-semibold text-slate-400 hover:text-slate-600 uppercase tracking-widest transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
      </div>
    </div>
  );
};

export default Dashboard;
