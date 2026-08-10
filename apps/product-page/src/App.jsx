import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import axios from 'axios';
import { Lock, Zap, Mail, Eye, ArrowRight, EyeOff, Menu, X, RefreshCw, CheckCircle2, PlusCircle } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import './styles/mobile.css';
import { Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';

// Components
import Sidebar from './components/Sidebar';
import { isReadOnly } from './lib/permissions';
import AccountSelectionModal from './components/AccountSelectionModal';
import ForceConnectModal from './components/ForceConnectModal';
import RepostModal from './components/RepostModal';
// Pages
import Dashboard from './pages/Dashboard';
import DashboardStats, { clearDashboardCache } from './pages/DashboardStats';
// Per-platform live progress popup — same one used by Agent Post so
// Manual Post publish gets consistent UX.
import PublishingProgressModal from './components/PublishingProgressModal';
import Connections from './pages/Connections';
import Posts from './pages/Posts';
import Analytics from './pages/Analytics';
import Calendar from './pages/Calendar';
import Published from './pages/Published';
import Reputation from './pages/Reputation';

import SignInCard from './components/ui/SignInCard';
import SignUpCard from './components/ui/SignUpCard';
import Drafts from './pages/Drafts';
import Scheduled from './pages/Scheduled';
import Profile from './pages/Profile';
import Team from './pages/Team';
import AcceptInvite from './pages/AcceptInvite';
import Onboarding from './pages/Onboarding';
import CanvaCallback from './pages/CanvaCallback';
import CampaignOptionSelection from './pages/CampaignOptionSelection';
import UserGuide from './pages/UserGuide';
import NexusLayout from './pages/nexus/NexusLayout';

import { API_BASE_URL, NEXUS_ENABLED } from './utils/config';
// L1 + H3 — shared helpers so App.jsx and Posts.jsx stay in lock-step:
//   resolveMediaUrl → picks base64 vs http URL vs null (was duplicated)
//   formatApiError  → normalises FastAPI array-of-objects into a string
import { resolveMediaUrl, formatApiError } from './utils/postContent';
import { useNotification } from './context/NotificationContext';

/**
 * KeepAliveTab — module-level so its identity is STABLE across all App
 * re-renders. Each instance manages its own "has been active" flag
 * internally; no shared state from the parent is needed.
 *
 * WHY MODULE-LEVEL MATTERS: the old implementation defined this component
 * inside a `useMemo` that depended on `visitedTabs`. Every time a new tab
 * was first visited, `visitedTabs` changed → a NEW function reference was
 * returned by useMemo → React saw a different component type → unmounted
 * and remounted EVERY KeepAlive'd tab, defeating the entire cache.
 *
 * With a stable identity, React always sees the same type. Tabs that have
 * been active once render in a hidden <div> instead of being destroyed.
 */
const KeepAliveTab = ({ active, children }) => {
  // Start alive immediately if the tab is already active (e.g. the  initial
  // tab on first load), otherwise wait until first activation.
  const [alive, setAlive] = React.useState(active);
  React.useEffect(() => {
    if (active && !alive) setAlive(true);
  }, [active, alive]);
  if (!alive) return null;
  return (
    <div
      style={{ display: active ? 'flex' : 'none', flex: 1, flexDirection: 'column', height: '100%', width: '100%', overflow: 'auto' }}
      className={active ? 'animate-in fade-in duration-300' : ''}
    >
      {children}
    </div>
  );
};

/**
 * Toast — module-level so its component identity is STABLE across
 * `App` re-renders. Previously this lived inside `App` as a closure,
 * which meant every state change (token, user, analytics, etc.)
 * regenerated the component definition. React then unmounted the old
 * Toast and mounted a new one, restarting the 3-second auto-dismiss
 * timer from scratch. After login, App re-renders 5–10 times in quick
 * succession, so the timer never fired and the toast appeared to hang
 * around for a minute or more before finally clearing.
 *
 * With Toast hoisted out, mount happens once when `msg` arrives, the
 * setTimeout fires after exactly 3s, and the toast disappears cleanly.
 */
const Toast = ({ msg, onClear }) => {
  useEffect(() => {
    const timer = setTimeout(onClear, 3000);
    return () => clearTimeout(timer);
  }, [msg, onClear]);
  return (
    <motion.div
      initial={{ opacity: 0, y: 50, scale: 0.9 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 20, scale: 0.9 }}
      className="fixed bottom-8 right-8 z-[100] flex items-center gap-3 px-6 py-4 bg-white border border-slate-100 rounded-2xl shadow-2xl shadow-orange-200/50 min-w-[300px]"
    >
      <div className="w-10 h-10 rounded-xl bg-orange-50 flex items-center justify-center text-[#F55600]">
        <Zap className="w-5 h-5 fill-current" />
      </div>
      <div>
        <p className="text-[10px] font-black uppercase tracking-widest text-[#F55600]">Notification</p>
        <p className="text-sm font-bold text-[#2B2926]">{msg}</p>
      </div>
    </motion.div>
  );
};

const App = () => {
  const [token, setToken] = useState(localStorage.getItem('token') || '');
  const [user, setUser] = useState(null);
  const [activeTab, setActiveTab] = useState(localStorage.getItem('activeTab') || 'dashboard');
  // Publish progress popup for Manual Post publishes. Same shape as
  // Dashboard.jsx uses: { open, platforms[], results }.
  const [manualPublishProgress, setManualPublishProgress] = useState({
    open: false,
    platforms: [],
    results: null,
  });
  const [settingsInitialTab, setSettingsInitialTab] = useState('account');
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [message, setMessage] = useState('');
  // Stable identity for the toast onClear callback so passing it down
  // doesn't trigger Toast's auto-dismiss useEffect to re-run (which
  // would reset the 3-second timer). Inline `() => setMessage('')`
  // creates a fresh function each render and would defeat the fix.
  const clearMessage = useRef(() => setMessage('')).current;
  // Inline error for the brief textarea. Rejections from the pre-flight
  // brief guard / refiner (HTTP 422) land here instead of the global toast
  // so the user sees the reason directly under the field they need to fix.
  const [briefError, setBriefError] = useState('');
  const [showAccountModal, setShowAccountModal] = useState(false);
  const [showForceConnectModal, setShowForceConnectModal] = useState(false);

  const navigateToSettingsTab = (tabId) => {
    setSettingsInitialTab(tabId);
    setActiveTab('settings');
  };
  const [pendingAccounts, setPendingAccounts] = useState({ platform: '', accounts: [] });
  const [connections, setConnections] = useState({ linkedin: [], facebook: [], instagram: [], twitter: [], reddit: [], youtube: [], tiktok: [] });
  const [selectedTargets, setSelectedTargets] = useState({});
  const [postContent, setPostContent] = useState('');
  // YouTube-Mode form fields for Manual Post → Video Mode. Kept at App
  // level so both handlePublish (here) and the Posts.jsx form (below) can
  // read/write the same state. Posts.jsx falls back to local state if
  // these props aren't wired — but they always are below.
  const [youtubeTitle, setYoutubeTitle] = useState('');
  const [youtubeTags, setYoutubeTags] = useState('');
  const [youtubeCategory, setYoutubeCategory] = useState('22');
  const [youtubePrivacy, setYoutubePrivacy] = useState('public');
  // TikTok form state — caption uses the main `postContent` field, so we
  // only track the TikTok-specific privacy enum (PUBLIC_TO_EVERYONE etc).
  // SELF_ONLY is the safe default for sandbox testing (the only privacy
  // level the sandbox accepts unconditionally).
  const [tiktokPrivacy, setTiktokPrivacy] = useState('SELF_ONLY');
  const [postType, setPostType] = useState(null);
  const [loading, setLoading] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);
  const [analytics, setAnalytics] = useState(() => {
    try {
      const cached = localStorage.getItem('pipelyt_analytics_cache');
      return cached ? JSON.parse(cached) : null;
    } catch (e) {
      return null;
    }
  });
  const [showIds, setShowIds] = useState(false);
  const [showAllAccounts, setShowAllAccounts] = useState(false);
  // Seed all three lists from localStorage so Scheduled/Published/Drafts
  // pages render instantly on a full-page reload — no spinner on first visit.
  const [drafts, setDrafts] = useState(() => {
    try { const r = localStorage.getItem('pipelyt_drafts_cache_v1'); return r ? JSON.parse(r) : []; } catch { return []; }
  });
  const [publishedPosts, setPublishedPosts] = useState(() => {
    try { const r = localStorage.getItem('pipelyt_published_cache_v1'); return r ? JSON.parse(r) : []; } catch { return []; }
  });
  const [scheduledPosts, setScheduledPosts] = useState(() => {
    try { const r = localStorage.getItem('pipelyt_scheduled_cache_v1'); return r ? JSON.parse(r) : []; } catch { return []; }
  });
  const [calendarPosts, setCalendarPosts] = useState([]);
  
  // Dashboard Stats Cache
  const [dashboardStats, setDashboardStats] = useState(() => {
    try {
      const cached = localStorage.getItem('pipelyt_dashboard_stats');
      return cached ? JSON.parse(cached) : { scheduled: 0, posted: 0, drafts: 0, connections: 0 };
    } catch (e) {
      return { scheduled: 0, posted: 0, drafts: 0, connections: 0 };
    }
  });
  const [dashboardAnalytics, setDashboardAnalytics] = useState(() => {
    try {
      const cached = localStorage.getItem('pipelyt_dashboard_analytics');
      return cached ? JSON.parse(cached) : null;
    } catch (e) {
      return null;
    }
  });

  // Dashboard AI Generation State
  const [dashboardStep, setDashboardStep] = useState('brief');
  const [campaignBrief, setCampaignBrief] = useState('');
  // Aspect ratio for image generation. Lives here so the API call has
  // access to it; CampaignBrief reads/writes via props. Default 16:9
  // (wide — best feed real estate for LinkedIn / X / Facebook).
  const [aspectRatio, setAspectRatio] = useState('16:9');
  // User-selected image style. "auto" = current pipeline behavior
  // (backend does nothing new). Any other slug triggers STYLE LOCK
  // on the Art Director prompt + industry playbook suppression.
  const [imageStyle, setImageStyle] = useState('auto');
  // Agent post type — drives whether the visualist runs (image) or the user
  // picks a copy variant + optionally attaches their own media (text/video/
  // document). Default 'image' preserves historical behaviour.
  const [dashboardPostType, setDashboardPostType] = useState('image');
  const [isGenerating, setIsGenerating] = useState(false);
  const [generatedData, setGeneratedData] = useState(null);
  const [selectedDashboardPlatforms, setSelectedDashboardPlatforms] = useState(['instagram']);
  const [reputationPosts, setReputationPosts] = useState([]);
  // KeepAliveTab is now module-level. visitedTabs state removed —
  // each KeepAliveTab instance manages its own liveness internally.
  const [activeReviewPlatform, setActiveReviewPlatform] = useState('instagram');
  const [selectedVariants, setSelectedVariants] = useState({});

  // Sync dashboardStats with fetched arrays & persist
  useEffect(() => {
    const newStats = {
      ...dashboardStats,
      scheduled: scheduledPosts.length,
      posted: publishedPosts.length,
      drafts: drafts.length
    };
    
    // Only update if something changed to avoid infinite loops if dashboardStats
    // is a dependency elsewhere (though it shouldn't be for this effect)
    if (newStats.scheduled !== dashboardStats.scheduled || 
        newStats.posted !== dashboardStats.posted || 
        newStats.drafts !== dashboardStats.drafts) {
      setDashboardStats(newStats);
      try {
        localStorage.setItem('pipelyt_dashboard_stats', JSON.stringify(newStats));
      } catch (e) {}
    }
  }, [scheduledPosts, publishedPosts, drafts]);
    const [selectedVisual, setSelectedVisual] = useState(0);
   const [editingPlatform, setEditingPlatform] = useState('default');
   
   // Session Data Tracking
   const [loadedStatus, setLoadedStatus] = useState({
     // If we seeded from localStorage, treat these as already loaded so
     // the child pages don't immediately re-fetch on first render.
     scheduled: !!(() => { try { return localStorage.getItem('pipelyt_scheduled_cache_v1'); } catch { return null; } })(),
     published: !!(() => { try { return localStorage.getItem('pipelyt_published_cache_v1'); } catch { return null; } })(),
     drafts:    !!(() => { try { return localStorage.getItem('pipelyt_drafts_cache_v1');    } catch { return null; } })(),
     analytics: false,
     connections: false,
     profile: false,
     usage: false
   });
   // Context & Branding State
   const [contextFiles, setContextFiles] = useState([]);
   const [logoImage, setLogoImage] = useState(null);
    const [logoPreview, setLogoPreview] = useState(null);
    const [selectedProduct, setSelectedProduct] = useState(null);
    // Product reference images — user-uploaded S3 URLs forwarded to
    // gpt-image-2 as extra references so generated visuals feature the
    // actual product. Plain string array; uploads happen via /upload-image.
    const [productReferenceImages, setProductReferenceImages] = useState([]);
   
  const [isRegistering, setIsRegistering] = useState(false);
  const [loginEmail, setLoginEmail] = useState('');
  const [loginPassword, setLoginPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  
  // OTP State
  const [loginStep, setLoginStep] = useState(1);
  const [signUpStep, setSignUpStep] = useState(0);
  const [otpCode, setOtpCode] = useState('');

  // Forgot-password flow state. forgotStep: 0 = inactive, 1 = email entry,
  // 2 = OTP entry, 3 = new-password entry. Shares loginEmail with the login
  // form so the user's typed email carries through if they click "Forgot?".
  const [forgotStep, setForgotStep] = useState(0);
  const [forgotOtpCode, setForgotOtpCode] = useState('');
  const [forgotNewPassword, setForgotNewPassword] = useState('');

  const [imagePreview, setImagePreview] = useState(null);
  const [base64Image, setBase64Image] = useState(null);
  // mediaType tracks what the user uploaded. Drives platform-availability
  // gating (document posts → LinkedIn only) and publisher routing on the
  // backend. Values: 'image' | 'video' | 'document' | null.
  const [mediaType, setMediaType] = useState(null);
  const [mediaUploadProgress, setMediaUploadProgress] = useState(0);
  // Measured client-side from <video>.duration. Drives the Twitter hide-rule
  // in the account selector (X free tier refuses >2 min video).
  const [videoDurationSec, setVideoDurationSec] = useState(0);
  const fileInputRef = useRef(null);

  // Repost Modal State
  const [showRepostModal, setShowRepostModal] = useState(false);
  const [repostPost, setRepostPost] = useState(null);

  const isSubscriptionExpired = useMemo(() => {
    return false;
  }, [user]);

  const totalConnected = useMemo(() => {
    return Object.values(connections).flat().length;
  }, [connections]);

  const navigate = useNavigate();
  const location = useLocation();

  // Force Connection modal for users with 0 accounts.
  // Only shown to users who bought Pipelyt (or Both) — GTM-only users
  // don't need social channel connections; their onboarding is different.
  //
  // H3 fix — re-open the modal on ANY tab change while the user still
  // has 0 connections. Previously the effect only fired once, so the X
  // button (or the now-removed backdrop click) was a permanent escape
  // hatch — user could dismiss and use the app with 0 accounts. Now:
  // dismiss it → try to navigate → modal re-opens. It becomes a soft
  // gate that keeps nudging without being locked-behind-a-dialog rude.
  useEffect(() => {
    if (token && user && user.onboarded && loadedStatus.connections) {
      const surface = user.product_selection || 'both';
      const needsPipelytConnections = surface === 'pipelyt' || surface === 'both';
      if (needsPipelytConnections && totalConnected === 0) {
        setShowForceConnectModal(true);
      }
    }
  }, [token, user, loadedStatus.connections, totalConnected, activeTab]);

  // Subscription lock logic
  useEffect(() => {
    if (isSubscriptionExpired && activeTab !== 'settings') {
      setActiveTab('settings');
      setSettingsInitialTab('billing');
      setMessage('Your subscription has expired. Please renew to continue using Pipelyt 💳');
    }
  }, [isSubscriptionExpired, activeTab]);

  // Product-surface landing route — send GTM-only users to /nexus and
  // Pipelyt-only users to /dashboard. Runs whenever user.product_selection
  // resolves, so a user who lands on 'dashboard' (the default) but only
  // purchased GTM gets bounced to the GTM landing page. Team members
  // inherit their master's selection (backend fills product_selection at
  // profile-read time). Users on 'both' can stay wherever they land.
  const _routedForProductSelectionRef = useRef(false);
  useEffect(() => {
    if (!user) return;
    if (_routedForProductSelectionRef.current) return;
    // Force dashboard redirect if they land on nexus tab
    if (activeTab === 'nexus') {
      setActiveTab('dashboard');
    }
    _routedForProductSelectionRef.current = true;
  }, [user?.product_selection, activeTab]);

  const authAxios = useMemo(() => {
    const instance = axios.create({
      baseURL: API_BASE_URL,
      headers: { Authorization: `Bearer ${token}` }
    });

    instance.interceptors.response.use(
      (response) => response,
      (error) => {
        if (error.response?.status === 401) {
          handleLogout();
        }
        return Promise.reject(error);
      }
    );

    return instance;
  }, [token]);

  const fetchProfile = async () => {
    if (!token) return;
    try {
      const response = await authAxios.get('/profile');
      setUser(response.data);
      setLoadedStatus(prev => ({ ...prev, profile: true }));
    } catch (err) {}
  };

  const fetchConnections = async () => {
    if (!token) return;
    try {
      const response = await authAxios.get('/connections');
      setConnections(response.data);
      setLoadedStatus(prev => ({ ...prev, connections: true }));
    } catch (err) {}
  };

  useEffect(() => {
    try {
      localStorage.setItem('activeTab', activeTab);
    } catch (e) {}
    // Only fetch analytics automatically if we haven't loaded it this session
    if ((activeTab === 'analytics' || activeTab === 'campaign-performance') && token && !loadedStatus.analytics) {
      fetchAnalytics();
    }
  }, [activeTab, token, loadedStatus.analytics]);

  // True while the URL still contains ?status=success — i.e. the window between
  // Stripe redirecting back and our confirm-session handler finishing + stripping
  // the query string with navigate('/'). Used to suppress routing-guard bounces
  // that would otherwise flash the user through /onboarding step 1 for ~500 ms
  // while user.onboarded is still false on the client.
  const isPaymentConfirming =
    new URLSearchParams(location.search).get('status') === 'success';

  // Routing Guard
  useEffect(() => {
    // Hold all navigation decisions while the payment-return confirmation is
    // in flight. The dedicated confirm-session effect below will navigate to
    // '/' once it flips user.onboarded = true.
    if (isPaymentConfirming) return;

    // Public routes that don't require auth — skip all redirects for them.
    const PUBLIC_PATHS = new Set(['/', '/signup', '/accept-invite']);
    const isPublic = PUBLIC_PATHS.has(location.pathname);

    if (!token) {
      if (!isPublic) {
        navigate('/');
      }
    } else if (user) {
      // Authed: still allow /accept-invite (edge case: user pastes invite link
      // while already logged in — AcceptInvite handles that on its own).
      if (location.pathname === '/accept-invite') return;
      if (!user.onboarded && location.pathname !== '/onboarding') {
        navigate('/onboarding');
      } else if (user.onboarded && (location.pathname === '/signin' || location.pathname === '/signup' || location.pathname === '/onboarding')) {
        navigate('/');
      }
    }
  }, [token, user, location.pathname, location.search, navigate, isPaymentConfirming]);

  // Guard: team members cannot land on admin-only tabs (calendar + team).
  // The sidebar already hides these, but tabs can be deep-linked via
  // localStorage or manual set — force them back to the dashboard.
  useEffect(() => {
    if (isReadOnly(user) && (activeTab === 'calendar' || activeTab === 'team')) {
      setActiveTab('dashboard');
    }
  }, [user?.role, activeTab]);

  useEffect(() => {
    if (token) {
      fetchProfile();
    }
  }, [token]);

  useEffect(() => {
    if (token && user) {
      // ── LAZY ANALYTICS (2026-06-26) ─────────────────────────────────
      // We used to prefetch ~10-15 endpoints here (analytics summary x4
      // platforms, analytics/posts, reputation, team members + per-
      // company analytics cascade, filter-options) to "pre-warm caches"
      // so tabs would paint instantly. Problem: every login fired all
      // of them in parallel, eating Lambda concurrency that the
      // /generate-content call needs. A carousel render that should
      // start instantly was queued behind 5+ slow analytics queries.
      //
      // New rule: only prefetch lightweight, frequently-needed data on
      // boot. Heavy analytics fetches happen LAZILY when the user
      // actually opens the Analytics / Campaign Performance tab — see
      // the activeTab-gated fetchAnalytics() above and Analytics.jsx
      // which fetches on its own mount.
      //
      // Kept on boot:
      //   - scheduled/published/drafts background refresh (Dashboard
      //     counts depend on these and they're cheap DB queries)
      // Removed on boot (now lazy / on-tab-open):
      //   - /analytics/summary (all variants)
      //   - /analytics/posts
      //   - /reputation/posts
      //   - /team/members, /team/filter-options
      //   - per-company trends cascade
      fetchScheduled(true);
      fetchPublished(true);
      fetchDrafts(true);
    }
  }, [token, user]);

  useEffect(() => {
    if (token) {
      fetchConnections();
    }
    const handleMessage = (event) => {
      // Log EVERY message received so we can trace OAuth flow even when
      // events are coming from unexpected origins or with unexpected types.
      console.log('[oauth:message-received]', {
        origin: event.origin,
        expected_origin: window.location.origin,
        origin_matches: event.origin === window.location.origin,
        data_type: event.data?.type,
        data_keys: event.data ? Object.keys(event.data) : [],
        timestamp: new Date().toISOString(),
      });

      if (event.data.type === 'AUTH_SUCCESS') {
        console.log('[oauth:AUTH_SUCCESS]', {
          platform: event.data.platform,
          fb_accounts_count: (event.data.fb_accounts || []).length,
          ig_accounts_count: (event.data.ig_accounts || []).length,
          accounts_count: (event.data.accounts || []).length,
        });
        oauthSucceededRef.current = true;
        const platform = event.data.platform;
        const accounts = platform === 'instagram' ? event.data.ig_accounts :
                         (platform === 'twitter' ? event.data.accounts : event.data.fb_accounts || event.data.accounts);
        setPendingAccounts({ platform, accounts });
        setShowAccountModal(true);
      }
      if (event.data.type === 'AUTH_ERROR') {
        console.error('[oauth:AUTH_ERROR]', {
          platform: event.data.platform,
          error: event.data.error,
        });
        oauthSucceededRef.current = true;
        const platform = event.data.platform || 'provider';
        const errMsg = event.data.error || 'Unknown OAuth error';
        setMessage(`${platform.charAt(0).toUpperCase() + platform.slice(1)} OAuth failed: ${errMsg} ⚠️`);
      }
      if (event.data.type === 'CANVA_FINISHED') {
        const { designId } = event.data;
        setMessage('Canva design saved! 🎨 Fetching image...');
        
        // Call backend to export the image
        console.log("CANVA_FINISHED: Fetching image for designId:", designId);
        authAxios.post('/auth/canva/export', { design_id: designId })
          .then(res => {
            console.log("CANVA_EXPORT Response:", res.data);
            if (res.data.url) {
              setImagePreview(res.data.url);
              setPostType('image');
              setSelectedVisual('uploaded');
              setMessage('Image ready! 🖼️');
            } else if (res.data.error) {
              setMessage(`Export error: ${res.data.error} ❌`);
              console.error("Canva Export Error:", res.data.error, res.data.details);
            }
          })
          .catch(err => {
            console.error("Export request failed:", err);
            setMessage("Failed to fetch image from Canva ❌");
          });
      }
    };
    window.addEventListener('message', handleMessage);
    // Handle Canva Return Navigation (correlation_jwt on root)
    const urlParams = new URLSearchParams(window.location.search);
    const designId = urlParams.get('design_id') || urlParams.get('correlation_jwt');
    const isStarting = urlParams.get('edit_url'); // If edit_url is present, we are just starting!
    
    if (designId && !isStarting && window.opener && window.location.pathname !== '/canva-callback') {
      console.log("ROOT_HANDLER: Detected Canva return! Sending CANVA_FINISHED for designId:", designId);
      window.opener.postMessage({ type: 'CANVA_FINISHED', designId }, "*");
      window.close();
    }

    return () => window.removeEventListener('message', handleMessage);
  }, [token]);

  // Handle Stripe Success/Cancel return.
  //
  // The webhook at POST /payment/webhook is the primary mechanism for marking
  // a user as paid+onboarded, but it's async, can be late, and is unreachable
  // during local dev. So we also confirm synchronously here via
  // POST /payment/confirm-session — the backend re-queries Stripe (trusted)
  // and only flips user.onboarded=true when Stripe itself reports paid.
  //
  // Guarded with a ref so this effect doesn't double-fire if React re-renders
  // before `navigate('/')` strips the query string.
  const confirmingSessionRef = useRef(false);
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    if (params.get('status') !== 'success') return;
    if (confirmingSessionRef.current) return;
    confirmingSessionRef.current = true;

    const sessionId = params.get('session_id');

    (async () => {
      setMessage('Payment successful! Synchronizing your account... 🚀');
      try {
        if (sessionId && token) {
          const resp = await authAxios.post('/payment/confirm-session', { session_id: sessionId });
          if (resp.data?.status === 'paid') {
            setMessage('Subscription activated ✅');
          } else if (resp.data?.status === 'pending') {
            // Stripe hasn't finalized yet — webhook will catch up; keep a
            // friendly message and continue. fetchProfile below will retry.
            setMessage('Payment received, finalizing subscription... ⏳');
          }
        }
      } catch (err) {
        console.error('confirm-session failed', err);
        setMessage('Payment confirmation failed — please contact support if charged ⚠️');
      } finally {
        // Re-fetch profile either way — webhook may have already flipped onboarded.
        await fetchProfile();
        navigate('/', { replace: true });
        confirmingSessionRef.current = false;
      }
    })();
  }, [location.search, navigate, token]);

  // Credit-purchase return — Stripe redirects back with ?status=credits_success.
  // confirm-session is kind-aware and grants the credits idempotently (the
  // webhook may already have). The GTM Journey badge refreshes on its own mount.
  const confirmingCreditsRef = useRef(false);
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    if (params.get('status') !== 'credits_success') return;
    if (confirmingCreditsRef.current) return;
    confirmingCreditsRef.current = true;

    const sessionId = params.get('session_id');
    (async () => {
      setMessage('Adding your credits...');
      try {
        if (sessionId && token) {
          await authAxios.post('/payment/confirm-session', { session_id: sessionId });
        }
        setMessage('Credits added to your account.');
      } catch (err) {
        console.error('credits confirm-session failed', err);
        setMessage('Payment could not be confirmed. If you were charged, contact support.');
      } finally {
        navigate('/', { replace: true });
        confirmingCreditsRef.current = false;
      }
    })();
  }, [location.search, navigate, token]);

  const onRequestOTP = async (purpose = 'login', e) => {
    if (e) e.preventDefault();
    const targetEmail = (purpose === 'change_password' && user?.email) ? user.email : loginEmail;
    
    if (!targetEmail) { 
      setMessage('Please provide an email 🚩'); 
      return; 
    }
    
    setLoading(true);
    try {
      await axios.post(`${API_BASE_URL}/auth/request-otp`, { 
        email: targetEmail, 
        purpose 
      });
      setMessage(`OTP sent to ${targetEmail} ✅`);
      if (purpose === 'login') setLoginStep(2);
      else if (purpose === 'register') setSignUpStep(1);
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Failed to send OTP ❌');
    } finally { setLoading(false); }
  };

  const onVerifyOTP = async (purpose = 'login', e) => {
    if (e) e.preventDefault();
    if (!otpCode) { setMessage('Please enter the verification code 🚩'); return; }
    setLoading(true);
    try {
      const response = await axios.post(`${API_BASE_URL}/auth/verify-otp`, { 
        email: loginEmail, 
        code: otpCode,
        purpose 
      });
      
      if (response.data.access_token) {
        // Login success (or user already existed during signup)
        const newToken = response.data.access_token;
        localStorage.setItem('token', newToken);
        setToken(newToken);
        await fetchProfile();
        setMessage('Authenticated! ✅');
      } else if (response.data.is_new_user) {
        // Verification success for registration - automatically create account
        setMessage('Code verified! Finalizing registration... ⏳');
        await handleSignUp();
      }
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Invalid OTP ❌');
    } finally { setLoading(false); }
  };

  // ========================== FORGOT PASSWORD FLOW ==========================
  // Three-step unauthenticated recovery:
  //   1. onRequestForgotOtp → POST /auth/request-otp purpose='reset_password'
  //   2. onVerifyForgotOtp  → POST /auth/verify-otp  purpose='reset_password'
  //   3. onResetPassword    → POST /auth/reset-password → auto-login
  // Uses its own step/otp/password state to avoid colliding with the
  // signup/login OTP inputs.

  // M1 fix — normalize client-side email so 'Alice@Example.com' hits the
  // backend as 'alice@example.com'. Backend also normalizes (H1/H2), so this
  // is belt-and-suspenders; benefit here is that the "Reset code sent to
  // …" toast shows the normalized address the user should look for.
  const _normEmail = (raw) => (raw || '').trim().toLowerCase();

  const onRequestForgotOtp = async (e) => {
    if (e) e.preventDefault();
    const email = _normEmail(loginEmail);
    if (!email) { setMessage('Please provide an email 🚩'); return; }
    setLoading(true);
    try {
      await axios.post(`${API_BASE_URL}/auth/request-otp`, {
        email,
        purpose: 'reset_password',
      });
      setMessage(`Reset code sent to ${email} ✅`);
      setForgotStep(2);
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Failed to send reset code ❌');
    } finally { setLoading(false); }
  };

  const onVerifyForgotOtp = async (e) => {
    if (e) e.preventDefault();
    if (!forgotOtpCode) { setMessage('Enter the 6-digit code 🚩'); return; }
    setLoading(true);
    try {
      // verify-otp with reset_password purpose only validates the code;
      // it does NOT mark is_used (backend carve-out, see auth.py verify_otp).
      // /auth/reset-password is the final consumer and marks the row used
      // atomically with the password update.
      await axios.post(`${API_BASE_URL}/auth/verify-otp`, {
        email: _normEmail(loginEmail),
        code: forgotOtpCode,
        purpose: 'reset_password',
      });
      setMessage('Code verified — set a new password ✅');
      setForgotStep(3);
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Invalid or expired code ❌');
    } finally { setLoading(false); }
  };

  const onResetPassword = async (e) => {
    if (e) e.preventDefault();
    // L1 note — this 6-char minimum mirrors the backend check at
    // routers/auth.py:_MIN_PASSWORD_LEN. Kept as a client-side fast-fail
    // (no round-trip) + HTML5 minLength={6} on the input is the 3rd layer.
    // If the backend threshold ever changes, update ALL three sites.
    if (!forgotNewPassword || forgotNewPassword.length < 6) {
      setMessage('New password must be at least 6 characters 🚩');
      return;
    }
    setLoading(true);
    try {
      const resp = await axios.post(`${API_BASE_URL}/auth/reset-password`, {
        email: _normEmail(loginEmail),
        otp_code: forgotOtpCode,
        new_password: forgotNewPassword,
      });
      // Backend issues a fresh JWT on successful reset; use it to log in
      // immediately so the user lands on the correct route (onboarding or
      // dashboard depending on user.onboarded).
      const newToken = resp.data?.access_token;
      if (newToken) {
        localStorage.setItem('token', newToken);
        setToken(newToken);
        await fetchProfile();
        setMessage('Password reset — you are logged in ✅');
        navigate('/');
      } else {
        // Endpoint returned 200 without a token — fall back to manual login.
        setMessage('Password reset — please sign in with your new password ✅');
        navigate('/');
      }
      // Reset the forgot-flow state so the card returns to login mode cleanly.
      setForgotStep(0);
      setForgotOtpCode('');
      setForgotNewPassword('');
      setLoginPassword('');
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Password reset failed ❌');
      // L2 fix — clear the (possibly wrong / expired) OTP field so the user
      // can request a fresh code without deleting the old digits first.
      setForgotOtpCode('');
    } finally { setLoading(false); }
  };

  const handleLogin = async (e) => {
    e.preventDefault();
    // M1 fix — normalize email client-side (strip whitespace + lowercase)
    // so the login attempt matches the row stored by /auth/register (which
    // also normalizes). Belt-and-suspenders with the backend's H1 fix.
    const email = _normEmail(loginEmail);
    if (!email || !loginPassword) {
      setMessage('Please enter both email and password 🚩');
      return;
    }
    setLoading(true);
    try {
      const formData = new FormData();
      formData.append('username', email);
      formData.append('password', loginPassword);

      const response = await axios.post(`${API_BASE_URL}/auth/login`, formData);
      const newToken = response.data.access_token;

      localStorage.setItem('token', newToken);
      setToken(newToken);

      await fetchProfile();
      setMessage('Welcome back! ✅');
      navigate('/');
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Invalid email or password ❌');
    } finally {
      setLoading(false);
    }
  };

  const handleSignUp = async (e) => {
    if (e) e.preventDefault();
    setLoading(true);
    try {
      // Create user after OTP is verified and password is provided in state
      await axios.post(`${API_BASE_URL}/auth/register`, { 
        email: loginEmail, 
        password: loginPassword 
      });
      
      // Auto-login after registration
      const formData = new FormData();
      formData.append('username', loginEmail);
      formData.append('password', loginPassword);
      const response = await axios.post(`${API_BASE_URL}/auth/login`, formData);
      const newToken = response.data.access_token;
      localStorage.setItem('token', newToken);
      setToken(newToken);
      
      await fetchProfile();
      setMessage('Account created successfully! ✅');
      navigate('/onboarding');
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Registration failed ❌');
    } finally { setLoading(false); }
  };

  const handleRefresh = () => {
    fetchProfile();
    fetchConnections();
    if (activeTab === 'analytics' || activeTab === 'campaign-performance') fetchAnalytics();
    setMessage('Refreshing data... 🔄');
  };

  const handleLogout = () => {
    // 1. Clear ALL browser storage to ensure no auth/credentials linger.
    //    localStorage holds the JWT + cached analytics/state.
    //    sessionStorage isn't used today but we wipe it defensively so any
    //    future feature or third-party lib that drops user data there is
    //    cleaned up on logout.
    try { localStorage.clear(); } catch (e) { /* private mode */ }
    try { sessionStorage.clear(); } catch (e) { /* private mode */ }

    // H1 fix — wipe the module-level dashboard cache so the next user
    // logging in on the same tab can't briefly see the previous user's
    // KPI numbers before their own data arrives.
    try { clearDashboardCache(); } catch (e) { /* defensive */ }

    // 2. Clear Auth State
    setToken('');
    setUser(null);
    setActiveTab('dashboard');
    
    // 3. Clear Analytics & Stats State
    setAnalytics(null);
    setDashboardStats({ scheduled: 0, posted: 0, drafts: 0, connections: 0 });
    setDashboardAnalytics(null);
    setReputationPosts([]);
    setAnalytics(null);
    setDrafts([]);
    setPublishedPosts([]);
    setScheduledPosts([]);
    setCalendarPosts([]);

    // 4. Clear Campaign & AI state
    setCampaignBrief('');
    setGeneratedData(null);
    setDashboardStep('brief');
    setSelectedVariants({});
    setSelectedTargets({});
    setContextFiles([]);
    setLogoImage(null);
    setLogoPreview(null);
    setSelectedProduct(null);
    setImagePreview(null);
    setBase64Image(null);
    setPostContent('');
    setSelectedVisual(0);
    // 5. Clear Connections
    setConnections({ linkedin: [], facebook: [], instagram: [], twitter: [], reddit: [], youtube: [], tiktok: [] });
    setLoadedStatus({
      scheduled: false,
      published: false,
      drafts: false,
      analytics: false,
      connections: false,
      profile: false,
      usage: false
    });

    setMessage('Logged out successfully! 👋');
    navigate('/');
  };

  // Toast is defined at the module level above (outside the App
  // closure). Keeping it module-level ensures component identity stays
  // stable across App re-renders so the 3-second auto-dismiss timer
  // doesn't keep restarting whenever the parent's state updates.

  // Tracks the oauth popup we opened most recently, so we can detect when the
  // user closes it before the AUTH_SUCCESS message fires (or before it ever
  // opens at all, in case of a popup blocker).
  const oauthPopupRef = useRef(null);
  const oauthPopupTimerRef = useRef(null);
  const oauthSucceededRef = useRef(false);

  const handleConnect = (platform) => {
    const width = 600;
    const height = 700;
    const left = window.screen.width / 2 - width / 2;
    const top = window.screen.height / 2 - height / 2;
    const providerPath = platform === 'instagram' ? 'facebook' : platform;
    const url = `${API_BASE_URL}/auth/${providerPath}?source=${platform}&token=${token}`;
    const features = `width=${width},height=${height},top=${top},left=${left}`;

    // Debug log — gives us full OAuth trace in browser console
    console.log('[oauth:init]', {
      platform,
      providerPath,
      API_BASE_URL,
      current_origin: window.location.origin,
      token_present: !!token,
      url,
      timestamp: new Date().toISOString(),
    });

    oauthSucceededRef.current = false;
    const popup = window.open(url, `Connect ${platform}`, features);

    if (!popup || popup.closed || typeof popup.closed === 'undefined') {
      console.warn('[oauth:init] popup BLOCKED by browser');
      setMessage(`Popup blocked. Allow popups for this site to connect ${platform}. 🚫`);
      return;
    }
    console.log('[oauth:init] popup OPENED successfully');

    // Best-effort focus; some browsers silently reject this for cross-origin.
    try { popup.focus(); } catch (e) {}

    oauthPopupRef.current = popup;
    if (oauthPopupTimerRef.current) clearInterval(oauthPopupTimerRef.current);
    const openedAt = Date.now();
    oauthPopupTimerRef.current = setInterval(() => {
      if (!oauthPopupRef.current || oauthPopupRef.current.closed) {
        clearInterval(oauthPopupTimerRef.current);
        oauthPopupTimerRef.current = null;
        oauthPopupRef.current = null;
        const elapsed = ((Date.now() - openedAt) / 1000).toFixed(1);
        console.log(`[oauth:popup-closed] after ${elapsed}s, oauthSucceededRef.current=${oauthSucceededRef.current}`);
        if (!oauthSucceededRef.current) {
          console.warn(`[oauth:cancel] popup closed without AUTH_SUCCESS or AUTH_ERROR for ${platform}`);
          setMessage(`${platform.charAt(0).toUpperCase() + platform.slice(1)} connection cancelled or failed. ⚠️`);
        }
      }
    }, 500);
  };

  const handleDisconnectAccount = async (platform, accountId) => {
    // Optimistic UI: remove the row from the parent `connections` state
    // BEFORE waiting for the DELETE roundtrip. Without this the channels
    // grid tile (which reads `connections[platform]`) stays on
    // "MANAGE 1 ACCOUNT" until fetchConnections finishes — which can be
    // 10+ seconds after the click on a busy page (browser queues the
    // GET /connections behind 6 in-flight analytics requests). The user
    // sees the manage modal go to "0 connected" but the underlying tile
    // stays stale, which looks like the delete didn't work.
    //
    // We snapshot the previous state so we can roll back if the DELETE
    // fails on the server.
    let prevConnections = null;
    setConnections(prev => {
      prevConnections = prev;
      const list = prev?.[platform] || [];
      return {
        ...prev,
        [platform]: list.filter(a => String(a.id) !== String(accountId)),
      };
    });

    try {
      await authAxios.delete(`/disconnect/${platform}/${accountId}`);
      // Sync with the server in the background. Don't await — the UI is
      // already correct from the optimistic update above, this is just to
      // pick up anything we couldn't predict locally (e.g. baseline rows
      // being deleted with the account).
      fetchConnections();
      setMessage(`${platform.charAt(0).toUpperCase() + platform.slice(1)} channel successfully removed ✅`);
    } catch (err) {
      console.error(err);
      // Roll back the optimistic update so the UI matches reality.
      if (prevConnections) setConnections(prevConnections);
      setMessage(`Failed to remove ${platform} channel: ${err.response?.data?.detail || 'Unknown error'} ❌`);
    }
  };

  const handleResetConnection = async (platform) => {
    const p_accounts = connections[platform] || [];
    try {
      // Disconnect all existing ones
      await Promise.all(p_accounts.map(acc => authAxios.delete(`/disconnect/${platform}/${acc.id}`)));
      fetchConnections();
      handleConnect(platform);
    } catch (err) {
      console.error(err);
      handleConnect(platform); // Try to connect anyway
    }
  };

  const handleDisconnectCanva = async () => {
    try {
      await authAxios.post('/auth/canva/disconnect');
      fetchProfile(); // Refresh user state to clear canva_access_token
      setMessage('Canva disconnected! 🎨');
    } catch (err) { console.error(err); }
  };

  // Upload the picked file DIRECTLY to S3 via a presigned PUT URL. This
  // sidesteps Lambda entirely for the bytes — critical for:
  //   • Videos up to 1 GB (would blow past API Gateway's 10 MB body cap and
  //     Lambda's 6 MB sync payload + 1024 MB memory limits).
  //   • Documents / PDFs.
  //   • Large images too (unified path, one less code branch to maintain).
  // Small images (<5 MB) still use the same path — the base64 fallback path
  // from the old /upload-image endpoint is gone because the presign flow is
  // strictly more reliable (no Lambda IAM surprises, no base64 bloat).
  const handleMediaChange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    // Per TC-MPOST-010 — enforce that the picked file matches the current
    // campaign mode. Previously an image dropped into Video Mode (or a
    // random PDF into Image Mode) was silently uploaded with no toast, and
    // the user only saw a failure at publish time. Fail fast at pick time
    // with a specific message so they know to re-select the right file.
    //
    // Rules mirror what each backend publisher accepts:
    //   postType='image'    → image/* only
    //   postType='video'    → video/* only
    //   postType='document' → application/pdf only (LinkedIn carousel is PDF)
    //   postType='text'     → this handler shouldn't fire at all, but if the
    //                        user swapped mode with a file still picked, we
    //                        silently allow the reset flow.
    const mimeExpectations = {
      image:    { prefix: 'image/',           label: 'an image (PNG, JPG, WebP, SVG)' },
      video:    { prefix: 'video/',           label: 'a video (MP4, MOV, WebM)' },
      document: { exact:  'application/pdf',  label: 'a PDF document' },
    };
    const expect = mimeExpectations[postType];
    if (expect) {
      const ok = expect.exact
        ? file.type === expect.exact
        : file.type.startsWith(expect.prefix);
      if (!ok) {
        const modeLabel = String(postType).charAt(0).toUpperCase() + String(postType).slice(1);
        setMessage(`${modeLabel} Mode expects ${expect.label}. You picked a ${file.type || 'file with unknown type'} — pick a different file.`);
        try { e.target.value = ''; } catch (_e) {}
        return;
      }
    }

    // Hard cap 1 GB client-side; server also enforces.
    const MAX_BYTES = 1024 * 1024 * 1024;
    if (file.size > MAX_BYTES) {
      setMessage(`File too large. Max 1 GB. This file is ${(file.size / 1024 / 1024).toFixed(0)} MB.`);
      // Reset the input so a re-pick of the same file re-fires onChange.
      try { e.target.value = ''; } catch (_e) {}
      return;
    }

    // Instant local preview while we upload.
    const localPreview = URL.createObjectURL(file);
    setImagePreview(localPreview);
    setBase64Image(null);
    setMediaUploadProgress(0);
    setVideoDurationSec(0);

    // For videos, probe the duration via an invisible <video> element so we
    // can gate Twitter (free tier caps video posts at 2 minutes). Waits up
    // to ~3 s for metadata to load; if it fails we just don't gate.
    if (file.type.startsWith('video/')) {
      try {
        const v = document.createElement('video');
        v.preload = 'metadata';
        v.src = localPreview;
        await new Promise((resolve) => {
          let done = false;
          const finish = () => { if (done) return; done = true; resolve(); };
          v.onloadedmetadata = () => {
            if (isFinite(v.duration) && v.duration > 0) setVideoDurationSec(v.duration);
            finish();
          };
          v.onerror = finish;
          setTimeout(finish, 3000);
        });
        // Auto-deselect any already-picked Twitter accounts if the video is
        // too long for X free tier — avoids the user hitting Publish with a
        // phantom-selected but hidden target.
        if (isFinite(v.duration) && v.duration > 120) {
          setSelectedTargets((prev) => {
            if (!prev?.twitter?.length) return prev;
            const next = { ...prev };
            delete next.twitter;
            return next;
          });
        }
      } catch (_e) { /* non-fatal */ }
    }

    try {
      // 1. Ask backend for a presigned PUT URL.
      const presignRes = await authAxios.post('/upload-media/presign', {
        filename: file.name,
        content_type: file.type,
        size_bytes: file.size,
      });
      const { upload_url, public_url, media_type: mt } = presignRes.data;

      // 2. PUT the bytes straight to S3 — no Authorization header, just
      // Content-Type (must match what we signed for). Use axios's native
      // upload progress so we can render a progress bar in the UI.
      await axios.put(upload_url, file, {
        headers: { 'Content-Type': file.type },
        onUploadProgress: (ev) => {
          if (ev.total) setMediaUploadProgress(Math.round((ev.loaded / ev.total) * 100));
        },
        // Don't let global axios interceptors inject our JWT — S3 would
        // 403 on an unexpected Authorization header.
        transformRequest: [(data) => data],
      });

      // 3. Swap preview to the real S3 URL + remember media type for
      // publisher routing.
      setImagePreview(public_url);
      setMediaType(mt);
      setMediaUploadProgress(100);
    } catch (err) {
      console.error('[upload-media] direct-to-S3 upload failed:', err);
      setMediaUploadProgress(0);
      // Surface enough detail to the user that they don't think it silently
      // worked. Common causes: presign expired, file type rejected server-
      // side, S3 CORS not configured on the bucket.
      const uploadMsg = formatApiError(err?.response?.data?.detail, err?.message || 'unknown error');
      setMessage(`Upload failed: ${uploadMsg}`);
      setImagePreview(null);
    }
  };

  // Back-compat alias — many child components still import `handleImageChange`
  // by that name. Route them to the new unified handler.
  const handleImageChange = handleMediaChange;

  const handlePublish = async () => {
    setLoading(true);
    try {
      // L1 — shared resolver used by both publish + schedule paths.
      const finalImage = resolveMediaUrl(imagePreview, base64Image);
      const isYoutubePost = !!(selectedTargets?.youtube?.length);
      const isTiktokPost = !!(selectedTargets?.tiktok?.length);
      const isPinterestPost = !!(selectedTargets?.pinterest?.length);

      // Guard: at least one target must be selected. UI disables the button
      // when this is empty, but a stale-state edge case or keyboard shortcut
      // could still fire the handler; defensive check keeps the toast helpful.
      const hasAnyTarget = Object.values(selectedTargets || {}).some(a => a?.length);
      if (!hasAnyTarget) {
        setMessage('Select at least one account before publishing. 🎯');
        setLoading(false);
        return;
      }

      // Guard: image-required platforms (Pinterest, Instagram, YouTube, TikTok)
      // must have a finished image/video before publish. Earlier we silently
      // sent image_url=null and the backend short-circuited per platform —
      // the user just saw "Published" but nothing went out. Block here and
      // tell them what's wrong.
      const needsMedia = isPinterestPost || isYoutubePost || isTiktokPost
        || !!selectedTargets?.instagram?.length;
      if (needsMedia && !finalImage) {
        const stillUploading = imagePreview && !imagePreview.startsWith('http');
        setMessage(stillUploading
          ? 'Image is still uploading — wait for the preview to load, then click Publish. ⏳'
          : 'This post needs an image. Upload one before publishing. 🖼️');
        setLoading(false);
        return;
      }

      // H4 fix — YouTube requires a video title. Was only enforced server-
      // side; now users fail fast with a clear message rather than seeing
      // the backend's validation error (which after H3 renders cleanly but
      // still requires a round-trip).
      if (isYoutubePost && !(youtubeTitle || '').trim()) {
        setMessage('YouTube video needs a title. Add one and try again. 🎬');
        setLoading(false);
        return;
      }

      // Open the per-platform progress popup so the user sees each target
      // resolve from spinner → ✅ / ❌ as the /post call completes.
      const publishPlatforms = Object.entries(selectedTargets || {})
        .filter(([, accts]) => Array.isArray(accts) && accts.length > 0)
        .map(([p]) => p);
      if (publishPlatforms.length > 0) {
        setManualPublishProgress({ open: true, platforms: publishPlatforms, results: null });
      }
      // Resolve the Campaign-Brief DNA picker into the canonical value the
      // backend stores on PublishedPost.dna_product_id. This is what makes
      // the dashboard's Business-DNA filter able to isolate posts later.
      //   - '<product_id>' → Product DNA branch (e.g. 'prod_spenzo_ai')
      //   - '__none__'     → user picked "None" → store NULL (no DNA tag)
      //   - null / unset   → Company DNA branch → store '__company__' sentinel
      const dnaProductId =
        selectedProduct === '__none__' ? null
        : selectedProduct ? selectedProduct
        : '__company__';
      const payload = {
        content: postContent,
        image_url: finalImage,
        media_type: mediaType,
        targets: selectedTargets,
        dna_product_id: dnaProductId,
      };
      if (isYoutubePost) {
        // YouTube-Mode form fields. Backend reads these only when targets.youtube
        // is non-empty; all other branches ignore them.
        payload.youtube_title = (youtubeTitle || '').trim();
        payload.youtube_description = postContent; // caption box is the description in YT-mode
        payload.youtube_tags = (youtubeTags || '')
          .split(',')
          .map(t => t.trim())
          .filter(Boolean);
        payload.youtube_category_id = youtubeCategory || '22';
        payload.youtube_privacy = youtubePrivacy || 'public';
      }
      if (isTiktokPost) {
        // TikTok-Mode form fields. Caption falls back to the main composer
        // text (`content`) on the backend, so we only forward privacy here.
        // Backend reads these only when targets.tiktok is non-empty.
        payload.tiktok_caption = postContent;
        payload.tiktok_privacy = tiktokPrivacy || 'SELF_ONLY';
      }
      const res = await authAxios.post('/post', payload);

      // Update the per-platform progress popup with the resolved results.
      // Modal derives success / failure / mixed per platform and auto-
      // closes ~4s after every row reaches a terminal state.
      setManualPublishProgress(prev => ({ ...prev, results: res?.data?.results || {} }));

      // Fix — the endpoint returns 200 even when SOME (or ALL) platforms
      // failed at their platform-specific publish step. The response body
      // is `{ results: { linkedin: [{name, status}], facebook: [...] } }`
      // where `status` is either "Success ✅" or an error string. Previously
      // this line was `setMessage('Published! ✅')` unconditionally, so a
      // user whose LinkedIn publish failed still saw a success toast and
      // navigated away thinking their post had gone live.
      //
      // Now we count success vs failure across all (platform, account)
      // entries and pick the appropriate toast:
      //   all success   → "Published to N platforms ✅"
      //   partial fail  → "Published to N of M. Failed: [list]"
      //   total failure → "Publish failed for: [list]"
      const results = res?.data?.results || {};
      const flat = Object.entries(results).flatMap(([platform, entries]) =>
        (Array.isArray(entries) ? entries : []).map(entry => ({
          platform,
          name: entry?.name || entry?.account_id || '',
          status: String(entry?.status ?? ''),
        }))
      );
      const isSuccessStatus = (s) => s.toLowerCase().startsWith('success');
      const successes = flat.filter(x => isSuccessStatus(x.status));
      const failures = flat.filter(x => !isSuccessStatus(x.status));
      const failedLabel = failures
        .map(x => x.platform.charAt(0).toUpperCase() + x.platform.slice(1))
        .filter((v, i, arr) => arr.indexOf(v) === i)
        .join(', ');

      if (flat.length === 0) {
        // No per-platform breakdown returned at all — endpoint responded
        // 200 but the shape is empty. Treat as success (matches old
        // behaviour) with a hedged message.
        setMessage('Published! ✅');
      } else if (failures.length === 0) {
        setMessage(`Published to ${successes.length} account${successes.length === 1 ? '' : 's'} ✅`);
      } else if (successes.length === 0) {
        setMessage(`❌ Publish failed for: ${failedLabel}`);
      } else {
        setMessage(`Published to ${successes.length}/${flat.length}. Failed: ${failedLabel} ⚠️`);
      }
      setPostContent(''); setImagePreview(null); setBase64Image(null); setMediaType(null); setSelectedTargets({});
      if (isYoutubePost) {
        // Reset the YouTube form fields so the next Manual Post starts clean.
        setYoutubeTitle('');
        setYoutubeTags('');
        setYoutubeCategory('22');
        setYoutubePrivacy('public');
      }
      if (isTiktokPost) {
        setTiktokPrivacy('SELF_ONLY');
      }

      // Proactively refresh the published list and profile (for stats)
      fetchPublished(true);
      fetchProfile();
    } catch (err) {
      console.error("Publish failed", err);
      // H3 fix — FastAPI validation errors arrive as
      //   detail: [{ loc, msg, type }, ...]  (array of objects)
      // The previous `${detail}` interpolation rendered that as
      // "[object Object]". formatApiError() normalises any shape (string,
      // array, or object) into a readable single-line message.
      const detail = err?.response?.data?.detail;
      const msg = formatApiError(detail, 'Failed to publish');
      setMessage(`${msg} ❌`);
      // Terminate all pending rows in the progress popup as failed so it
      // doesn't sit forever showing spinners after a top-level error.
      setManualPublishProgress(prev => ({
        ...prev,
        results: Object.fromEntries(
          prev.platforms.map(p => [p, [{ status: `Failed ❌: ${msg}` }]])
        ),
      }));
    }
    finally { setLoading(false); }
  };

  const fetchAnalytics = async () => {
    try {
      // Prefetch with the same defaults the Analytics page uses on first
      // mount (time_period=7d, platform=all, no narrowing) so the
      // filter-keyed cache hit on the page is a guaranteed win.
      const response = await authAxios.get('/analytics/summary?time_period=7d&platform=all');
      setAnalytics(response.data);
      try {
        localStorage.setItem('pipelyt_analytics_cache', JSON.stringify(response.data));
      } catch (e) {
        if (e.name === 'QuotaExceededError' || e.name === 'NS_ERROR_DOM_QUOTA_REACHED') {
          console.warn("Analytics cache storage full. Pruning secondary caches.");
          localStorage.removeItem('pipelyt_dashboard_analytics');
          try {
            localStorage.setItem('pipelyt_analytics_cache', JSON.stringify(response.data));
          } catch (retryError) {
            // Silently fail if still full
          }
        }
      }
      // Seed the Analytics page's filter-keyed cache for the all-platforms
      // view. Cache key shape MUST match Analytics.jsx's `fetchAnalytics`
      // exactly: [mode, timePeriod, platform, member_ids, brand_ids,
      // country, state, city, pin, custom_dates].join('|') = 9 pipes for
      // the default (no filters) case.
      // IMPORTANT: key must be 'pipelyt_analytics_summary_cache_v4' —
      // Analytics.jsx's IIFE hydrates from that key, not v1.
      const lsKey = 'pipelyt_analytics_summary_cache_v4';
      const seedCache = (extra) => {
        try {
          const existing = JSON.parse(localStorage.getItem(lsKey) || '{}');
          Object.assign(existing, extra);
          localStorage.setItem(lsKey, JSON.stringify(existing));
        } catch { /* best-effort */ }
      };
      seedCache({
        'main|7d|all|||||||': { data: response.data, fetchedAt: Date.now() },
        'post-performance-only|7d|all|||||||': { data: response.data, fetchedAt: Date.now() },
      });
      // Pre-warm per-platform variants so picking LinkedIn / X / Facebook
      // / Instagram from the platform dropdown is also a cache hit. Fired
      // in parallel — backend can handle 4 concurrent calls fine.
      (async () => {
        const platforms = ['linkedin', 'facebook', 'instagram', 'twitter'];
        await Promise.all(platforms.map(async (plat) => {
          const cacheKey = `main|7d|${plat}|||||||`;
          try {
            const r = await authAxios.get(`/analytics/summary?time_period=7d&platform=${plat}`);
            seedCache({ [cacheKey]: { data: r.data, fetchedAt: Date.now() } });
          } catch { /* harmless */ }
        }));
      })();
    } catch (err) { console.error(err); }
    finally {
      setLoadedStatus(prev => ({ ...prev, analytics: true }));
    }
  };

  const fetchScheduled = async (force = false, params = "") => {
    if (!token || (loadedStatus.scheduled && !force && !params)) return;
    try {
      const res = await authAxios.get(`/scheduled${params}`);
      setScheduledPosts(res.data);
      if (!params) {
        setLoadedStatus(prev => ({ ...prev, scheduled: true }));
        try { localStorage.setItem('pipelyt_scheduled_cache_v1', JSON.stringify(res.data)); } catch {}
      }
    } catch (err) { console.error(err); }
  };

  const fetchPublished = async (force = false, params = "") => {
    const defaultSort = params ? "" : "?sort=desc";
    if (!token || (loadedStatus.published && !force && !params)) return;
    try {
      const res = await authAxios.get(`/posts/published${defaultSort}${params ? (defaultSort ? params : '?' + params.substring(1)) : ''}`);
      setPublishedPosts(res.data);
      if (!params) {
        setLoadedStatus(prev => ({ ...prev, published: true }));
        try { localStorage.setItem('pipelyt_published_cache_v1', JSON.stringify(res.data)); } catch {}
      }
    } catch (err) { console.error(err); }
  };

  const fetchDrafts = async (force = false, params = "") => {
    if (!token || (loadedStatus.drafts && !force && !params)) return;
    try {
      const res = await authAxios.get(`/drafts${params}`);
      setDrafts(res.data);
      if (!params) {
        setLoadedStatus(prev => ({ ...prev, drafts: true }));
        try { localStorage.setItem('pipelyt_drafts_cache_v1', JSON.stringify(res.data)); } catch {}
      }
    } catch (err) { console.error(err); }
  };

  // Sync Dashboard Logo with Business DNA
  useEffect(() => {
    if (user?.business_dna?.logo_url && !logoPreview && !logoImage) {
      setLogoPreview(user.business_dna.logo_url);
    }
  }, [user, logoPreview, logoImage]);

  const handleGenerateContent = async () => {
    if (!campaignBrief.trim()) { setMessage('Please enter a campaign brief first 🚩'); return; }
    if (selectedDashboardPlatforms.length === 0) { setMessage('Please select at least one platform 🚩'); return; }
    setBriefError('');
    // TRACE: t0 = click. From here we measure click→request_sent→response→render.
    const _trace_t0 = performance.now();
    const _trace_iso0 = new Date().toISOString();
    console.log(`%c[TRACE] click ts=${_trace_iso0} post_type=${dashboardPostType || 'image'}`, 'color:#F55600;font-weight:bold');
    setIsGenerating(true);
    try {
      const formData = new FormData();
      formData.append('campaign_brief', campaignBrief);
      formData.append('platforms', JSON.stringify(selectedDashboardPlatforms));
      // post_type drives whether the backend runs the visualist (image) or
      // skips it (text / video / document).
      formData.append('post_type', dashboardPostType || 'image');

      if (logoImage) {
        formData.append('logo_image', logoImage);
      } else if (logoPreview && typeof logoPreview === 'string' && logoPreview.startsWith('http')) {
        formData.append('logo_url', logoPreview);
      }

      if (contextFiles && contextFiles.length > 0) {
        contextFiles.forEach(file => {
          formData.append('context_files', file);
        });
      }

      // Product reference photos (already uploaded to S3 — we forward URLs).
      // Backend caps to 4 and attaches to every gpt-image-2 slide render.
      if (Array.isArray(productReferenceImages) && productReferenceImages.length > 0) {
        formData.append('product_image_urls', JSON.stringify(productReferenceImages));
      }

      // Image style — only sent when user picked something other than
      // Auto. Skipping the field entirely for Auto keeps the backend
      // pipeline byte-for-byte unchanged from before this feature.
      if (imageStyle && imageStyle !== 'auto') {
        formData.append('image_style', imageStyle);
      }

      // TRACE: t1 = request sent.
      const _trace_t1 = performance.now();
      console.log(`%c[TRACE] request_sent ts=${new Date().toISOString()} click→send=${((_trace_t1 - _trace_t0)/1000).toFixed(2)}s`, 'color:#F55600');
      const response = await authAxios.post('/generate-content', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        params: { product_name: selectedProduct, aspect_ratio: aspectRatio }
      });
      // TRACE: t2 = response received (backend done + network roundtrip back).
      const _trace_t2 = performance.now();
      console.log(`%c[TRACE] response_received ts=${new Date().toISOString()} send→response=${((_trace_t2 - _trace_t1)/1000).toFixed(2)}s click→response=${((_trace_t2 - _trace_t0)/1000).toFixed(2)}s`, 'color:#F55600;font-weight:bold');

      setGeneratedData(response.data);
      setTimeout(() => {
        setIsGenerating(false); setDashboardStep('review');
        const variants = {}; const bestMatch = response.data.recommendation?.best_variant || 'brand'; const initialTargets = { ...selectedTargets };
        selectedDashboardPlatforms.forEach(p => {
          variants[p] = bestMatch;
          if (connections[p]?.length > 0) initialTargets[p] = connections[p].map(acc => acc.id);
        });
        setSelectedVariants(variants); setSelectedTargets(initialTargets);
        // TRACE: t3 = review screen mounted. Includes the artificial
        // 2000ms setTimeout below (intentional UX feel delay — flag it).
        const _trace_t3 = performance.now();
        console.log(`%c[TRACE] render_review_step ts=${new Date().toISOString()} response→render=${((_trace_t3 - _trace_t2)/1000).toFixed(2)}s (includes ~2.0s artificial UX delay) click→render=${((_trace_t3 - _trace_t0)/1000).toFixed(2)}s`, 'color:#F55600;font-weight:bold');
        console.log(`%c[TRACE] SUMMARY click→done = ${((_trace_t3 - _trace_t0)/1000).toFixed(2)}s  (backend round-trip = ${((_trace_t2 - _trace_t1)/1000).toFixed(2)}s, UX delay = 2.00s)`, 'background:#F55600;color:white;padding:2px 6px;border-radius:3px;font-weight:bold');
      }, 2000);
    } catch (err) {
      setIsGenerating(false);
      const detail = err.response?.data?.detail;
      // 422 = brief guard / refiner rejection. Surface inline under the
      // textarea, not as a transient toast — the user needs to read the
      // reason and edit the field directly.
      if (err.response?.status === 422 && typeof detail === 'string') {
        setBriefError(detail);
      } else {
        setMessage(detail || 'AI Generation failed ❌');
      }
    }
  };

  const handleRegenerateVisuals = async () => {
    if (!campaignBrief.trim() || !generatedData) return;
    setIsGenerating(true);
    try {
      const formData = new FormData();
      formData.append('campaign_brief', campaignBrief);
      formData.append('linkedin_content', JSON.stringify(generatedData.content?.linkedin || {}));
      formData.append('platforms', JSON.stringify(selectedDashboardPlatforms));
      // Regenerate — force the Art Director + gpt-image-2 Magic Pipeline
      // by supplying the full per-variant content dict. Without this the
      // backend fell through to the legacy v4 (Gemini) fallback, which is
      // NOT what users expect on Regenerate. Now regeneration always runs
      // through the same magic pipeline as the initial generation, producing
      // the 2 STANDARD_VARIANTS (viral_reach + follower_growth).
      if (generatedData?.content) {
        formData.append('content_variants_json', JSON.stringify(generatedData.content));
      }

      if (logoImage) formData.append('logo_image', logoImage);
      else if (logoPreview?.startsWith('http')) formData.append('logo_url', logoPreview);

      // Preserve the style the user picked at brief time so Regenerate
      // produces on-style variants (not a fresh Auto pass).
      if (imageStyle && imageStyle !== 'auto') {
        formData.append('image_style', imageStyle);
      }

      // product_name flows to _active_dna() on the backend — without it,
      // regeneration falls back to company DNA and loses the product-scoped
      // logo/color/context the user picked in the campaign builder.
      // /generate-visuals accepts it as a query param (same contract as
      // /generate-content).
      const productQuery = selectedProduct && selectedProduct !== '__none__'
        ? `?product_name=${encodeURIComponent(selectedProduct)}`
        : '';

      const response = await authAxios.post(`/generate-visuals${productQuery}`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });

      setGeneratedData(prev => ({ ...prev, visuals: response.data.visuals }));
      setTimeout(() => setIsGenerating(false), 1500);
    } catch (err) { setIsGenerating(false); setMessage('Visual regeneration failed ❌'); }
  };

  const handleCustomPrompt = async (prompt) => {
    if (!prompt.trim()) return;
    setIsGenerating(true);
    try {
      const formData = new FormData();
      formData.append('user_prompt', prompt);

      if (logoImage) formData.append('logo_image', logoImage);
      else if (logoPreview?.startsWith('http')) formData.append('logo_url', logoPreview);

      // Same product_name pass-through as regenerate — so the custom prompt
      // pipeline can pull the correct DNA logo/color for the selected product.
      const productQuery = selectedProduct && selectedProduct !== '__none__'
        ? `?product_name=${encodeURIComponent(selectedProduct)}`
        : '';

      const response = await authAxios.post(`/generate-custom-image${productQuery}`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      
      // Add as the 4th visual or replace the 3rd? Let's append if possible, 
      // but VisualGallery currently shows [0,1,2]. Let's replace the selected one or just append to visuals.
      setGeneratedData(prev => {
        const newVisuals = [...(prev.visuals || [])];
        if (newVisuals.length >= 3) newVisuals[2] = response.data; // Replace 3rd one
        else newVisuals.push(response.data);
        return { ...prev, visuals: newVisuals };
      });
      
      setTimeout(() => setIsGenerating(false), 1500);
    } catch (err) { setIsGenerating(false); setMessage('Custom generation failed ❌'); }
  };

  const toggleTarget = (platform, accountId) => {
    setSelectedTargets(prev => {
      const targets = prev[platform] || [];
      return { ...prev, [platform]: targets.includes(accountId) ? targets.filter(id => id !== accountId) : [...targets, accountId] };
    });
  };

  const loadDraft = (draft, platform = 'default') => {
    let displayContent = draft.content;
    if (draft.content && draft.content.startsWith('{')) {
      try {
        const json = JSON.parse(draft.content);
        displayContent = json[platform] || json.default || Object.values(json)[0] || "";
      } catch (e) {}
    }
    setPostContent(displayContent); setEditingPlatform(platform); setSelectedTargets(draft.targets || {}); setImagePreview(draft.image_url); setPostType(draft.image_url ? 'image' : 'text'); setActiveTab('posts');
  };

  const handleRepost = (post) => {
    setRepostPost(post);
    setShowRepostModal(true);
    setMessage('Opening repost assistant... ✨');
  };

  const resetEditor = () => {
    setPostContent(""); setEditingPlatform('default'); setSelectedTargets({}); setImagePreview(null); setBase64Image(null); setPostType(null); setActiveTab('posts');
  };

  if (window.location.pathname.includes('/canva-callback')) {
    return <CanvaCallback />;
  }

  return (
    <>
      <Routes>
        <Route path="/" element={
          token ? (
            user && !user.onboarded ? <Navigate to="/onboarding" /> : (
              user && user.onboarded ? (
                <div className="flex flex-col lg:flex-row h-screen bg-[#060b13] text-white overflow-hidden">
                  <header className="lg:hidden h-16 bg-[#0b1320] border-b border-slate-800 px-5 flex items-center justify-between shrink-0 z-40">
                    <div className="flex items-center gap-3">
                      <img src="/montanary-elevators.png" alt="Montanary Elevators" className="w-8 h-8 object-contain rounded-lg" />
                      <div className="flex flex-col leading-none">
                        <span className="text-base font-bold text-white tracking-tight">Montanary Elevators</span>
                        <span className="text-[8px] font-black tracking-[0.14em] mt-0.5 uppercase">
                          <span className="text-[#F55600]">All Channels</span>
                          <span className="text-slate-500"> · </span>
                          <span className="text-[#10B981]">One Intelligence</span>
                        </span>
                      </div>
                    </div>
                    <button 
                      onClick={() => setIsSidebarOpen(true)}
                      className="p-2.5 text-slate-400 hover:text-white hover:bg-slate-800 transition-all active:scale-95 bg-[#0d1726]/80 rounded-xl relative z-[60] border border-slate-800"
                      aria-label="Open Sidebar"
                    >
                      <Menu className="w-6 h-6" />
                    </button>
                  </header>

                  <Sidebar 
                    activeTab={activeTab} setActiveTab={setActiveTab} handleRefresh={handleRefresh} 
                    handleLogout={handleLogout} isOpen={isSidebarOpen} onClose={() => setIsSidebarOpen(false)} user={user}
                  />
                  
                  <main className={`flex-1 overflow-auto relative bg-[#060b13] ${activeTab === 'nexus' ? 'p-0' : ['create', 'agent-post', 'connections', 'analytics', 'reputation', 'settings', 'calendar', 'scheduled', 'published', 'drafts', 'team', 'dashboard'].includes(activeTab) ? 'p-1 md:p-2 lg:p-4' : 'p-4 md:p-6 lg:p-8'}`}>

                    {!['create', 'guide', 'agent-post', 'posts', 'dashboard', 'analytics', 'campaign-performance', 'reputation', 'drafts', 'settings', 'published', 'scheduled', 'calendar', 'connections', 'team', 'nexus'].includes(activeTab) && (
                      <header className={`${['create', 'agent-post', 'connections', 'analytics', 'reputation', 'nexus'].includes(activeTab) ? 'mb-1' : 'mb-8'} hidden lg:flex items-center justify-between`}>
                        <h2 className="text-2xl font-bold capitalize tracking-tight text-[#2B2926]">{activeTab.replace('-', ' ')}</h2>
                      </header>
                    )}

                    <KeepAliveTab active={activeTab === 'dashboard'}>
                      <DashboardStats 
                        authAxios={authAxios} 
                        connections={connections} 
                        setActiveTab={setActiveTab} 
                        activeTab={activeTab} 
                        stats={dashboardStats}
                        setStats={setDashboardStats}
                        analytics={dashboardAnalytics}
                        setAnalytics={setDashboardAnalytics}
                        user={user}
                        fetchScheduled={fetchScheduled}
                        fetchPublished={fetchPublished}
                        fetchDrafts={fetchDrafts}
                        fetchAnalytics={fetchAnalytics}
                        loadedStatus={loadedStatus}
                      />
                    </KeepAliveTab>
                    {activeTab === 'agent-post' && (
                      <Dashboard
                        campaignBrief={campaignBrief} setCampaignBrief={setCampaignBrief} dashboardStep={dashboardStep} setDashboardStep={setDashboardStep}
                        dashboardPostType={dashboardPostType} setDashboardPostType={setDashboardPostType}
                        briefError={briefError} setBriefError={setBriefError}
                        aspectRatio={aspectRatio} setAspectRatio={setAspectRatio} imageStyle={imageStyle} setImageStyle={setImageStyle}
                        isGenerating={isGenerating} setIsGenerating={setIsGenerating} handleGenerateContent={handleGenerateContent}
                        handleRegenerateVisuals={handleRegenerateVisuals} handleCustomPrompt={handleCustomPrompt}
                        generatedData={generatedData} setGeneratedData={setGeneratedData} selectedDashboardPlatforms={selectedDashboardPlatforms} 
                        setSelectedDashboardPlatforms={setSelectedDashboardPlatforms} activeReviewPlatform={activeReviewPlatform} 
                        setActiveReviewPlatform={setActiveReviewPlatform} selectedVariants={selectedVariants} setSelectedVariants={setSelectedVariants}
                        selectedVisual={selectedVisual} setSelectedVisual={setSelectedVisual} authAxios={authAxios} connections={connections}
                        selectedTargets={selectedTargets} toggleTarget={toggleTarget}
                        uploadedImageUrl={imagePreview} setUploadedImageUrl={setImagePreview}
                        handleImageChange={handleImageChange} mediaType={mediaType} setMediaType={setMediaType}
                        mediaUploadProgress={mediaUploadProgress} videoDurationSec={videoDurationSec}
                        contextFiles={contextFiles} setContextFiles={setContextFiles}
                        logoImage={logoImage} setLogoImage={setLogoImage}
                        logoPreview={logoPreview} setLogoPreview={setLogoPreview}
                        selectedProduct={selectedProduct} setSelectedProduct={setSelectedProduct}
                        productReferenceImages={productReferenceImages}
                        setProductReferenceImages={setProductReferenceImages}
                        user={user}
                        setMessage={setMessage}
                        setActiveTab={setActiveTab}
                        navigateToSettingsTab={navigateToSettingsTab}
                        fetchPublished={fetchPublished}
                        fetchScheduled={fetchScheduled}
                      />
                    )}
                    {activeTab === 'create' && <CampaignOptionSelection onSelect={setActiveTab} />}
                    {activeTab === 'connections' && (
                      <Connections
                        connections={connections}
                        handleConnect={handleConnect}
                        handleDisconnectAccount={handleDisconnectAccount}
                        handleResetConnection={handleResetConnection}
                        handleDisconnectCanva={handleDisconnectCanva}
                        user={user}
                        showIds={showIds}
                        setShowIds={setShowIds}
                        showAllAccounts={showAllAccounts}
                        setShowAllAccounts={setShowAllAccounts}
                        authAxios={authAxios}
                      />
                    )}
                    {activeTab === 'posts' && (
                      <Posts
                        postType={postType} setPostType={setPostType} setActiveTab={setActiveTab}
                        connections={connections} toggleTarget={toggleTarget}
                        selectedTargets={selectedTargets} setSelectedTargets={setSelectedTargets}
                        handleImageChange={handleImageChange} imagePreview={imagePreview}
                        setImagePreview={setImagePreview} base64Image={base64Image} setBase64Image={setBase64Image}
                        mediaType={mediaType} setMediaType={setMediaType}
                        mediaUploadProgress={mediaUploadProgress} videoDurationSec={videoDurationSec}
                        fileInputRef={fileInputRef} postContent={postContent} setPostContent={setPostContent}
                        handlePublish={handlePublish} editingPlatform={editingPlatform}
                        setEditingPlatform={setEditingPlatform} resetEditor={resetEditor}
                        loading={loading} message={message} authAxios={authAxios} user={user}
                        fetchPublished={fetchPublished}
                        fetchScheduled={fetchScheduled}
                        fetchDrafts={fetchDrafts}
                        youtubeTitle={youtubeTitle} setYoutubeTitle={setYoutubeTitle}
                        youtubeTags={youtubeTags} setYoutubeTags={setYoutubeTags}
                        youtubeCategory={youtubeCategory} setYoutubeCategory={setYoutubeCategory}
                        youtubePrivacy={youtubePrivacy} setYoutubePrivacy={setYoutubePrivacy}
                        tiktokPrivacy={tiktokPrivacy} setTiktokPrivacy={setTiktokPrivacy}
                      />
                    )}
                    <KeepAliveTab active={activeTab === 'calendar'}>
                      <Calendar authAxios={authAxios} posts={calendarPosts} setPosts={setCalendarPosts} user={user} />
                    </KeepAliveTab>
                    <KeepAliveTab active={activeTab === 'scheduled'}>
                      <Scheduled 
                        authAxios={authAxios} 
                        connections={connections} 
                        user={user} 
                        posts={scheduledPosts} 
                        setPosts={setScheduledPosts} 
                        fetchScheduled={fetchScheduled}
                        loadedStatus={loadedStatus}
                      />
                    </KeepAliveTab>
                    <KeepAliveTab active={activeTab === 'published'}>
                      <Published 
                        authAxios={authAxios} 
                        user={user} 
                        connections={connections} 
                        posts={publishedPosts} 
                        setPosts={setPublishedPosts} 
                        fetchPublished={fetchPublished}
                        loadedStatus={loadedStatus}
                        onRepost={handleRepost}
                      />
                    </KeepAliveTab>
                    <KeepAliveTab active={activeTab === 'drafts'}>
                      <Drafts
                        authAxios={authAxios}
                        setActiveTab={setActiveTab}
                        loadDraft={loadDraft}
                        resetEditor={resetEditor}
                        drafts={drafts}
                        setDrafts={setDrafts}
                        fetchDrafts={fetchDrafts}
                        loadedStatus={loadedStatus}
                        user={user}
                      />
                    </KeepAliveTab>
                    {/* Analytics + Campaign Performance: kept alive between tab switches
                        so they never remount/re-fetch. KeepAliveTab mounts on first visit
                        and hides (display:none) instead of unmounting on every switch.
                        Both instances use different `mode` → separate cacheKeys → no fight. */}
                    <KeepAliveTab active={activeTab === 'analytics'}>
                      <Analytics authAxios={authAxios} initialData={analytics} user={user} fetchAnalytics={fetchAnalytics} loadedStatus={loadedStatus} />
                    </KeepAliveTab>
                    <KeepAliveTab active={activeTab === 'campaign-performance'}>
                      <Analytics authAxios={authAxios} initialData={analytics} user={user} fetchAnalytics={fetchAnalytics} loadedStatus={loadedStatus} mode="post-performance-only" />
                    </KeepAliveTab>
                    {/* Reputation: KeepAlive + pass pre-fetched posts so the list
                        paints instantly from App-level prefetch, no spinner. */}
                    <KeepAliveTab active={activeTab === 'reputation'}>
                      <Reputation authAxios={authAxios} user={user} initialPosts={reputationPosts} setInitialPosts={setReputationPosts} />
                    </KeepAliveTab>
                    <KeepAliveTab active={activeTab === 'team' && !isReadOnly(user) && (user?.pricing_plan === 'agency' || user?.pricing_plan === 'enterprise')}>
                      <Team authAxios={authAxios} user={user} />
                    </KeepAliveTab>
                    {activeTab === 'guide' && <UserGuide />}
                    {activeTab === 'settings' && <Profile authAxios={authAxios} setMessage={setMessage} setUser={setUser} user={user} initialTab={settingsInitialTab} />}
                    {/* NEXUS surface — multi-section sales-engagement app, gated by VITE_NEXUS_ENABLED. */}
                    {NEXUS_ENABLED && activeTab === 'nexus' && (
                      <NexusLayout authAxios={authAxios} user={user} setMessage={setMessage} />
                    )}
                  </main>
                </div>
              ) : null
            )
          ) : (
            <SignInCard
              onSignIn={handleLogin}
              email={loginEmail} setEmail={setLoginEmail}
              password={loginPassword} setPassword={setLoginPassword}
              isLoading={loading} showPassword={showPassword} setShowPassword={setShowPassword}
              loginStep={loginStep} setLoginStep={setLoginStep}
              otpCode={otpCode} setOtpCode={setOtpCode}
              onRequestOTP={(e) => onRequestOTP('login', e)}
              onVerifyOTP={(e) => onVerifyOTP('login', e)}
              forgotStep={forgotStep} setForgotStep={setForgotStep}
              forgotOtpCode={forgotOtpCode} setForgotOtpCode={setForgotOtpCode}
              forgotNewPassword={forgotNewPassword} setForgotNewPassword={setForgotNewPassword}
              onRequestForgotOtp={onRequestForgotOtp}
              onVerifyForgotOtp={onVerifyForgotOtp}
              onResetPassword={onResetPassword}
            />
          )
        } />
        <Route path="/signup" element={
          <SignUpCard
            onSignUp={handleSignUp}
            email={loginEmail} setEmail={setLoginEmail}
            password={loginPassword} setPassword={setLoginPassword}
            isLoading={loading} showPassword={showPassword} setShowPassword={setShowPassword}
            signUpStep={signUpStep} setSignUpStep={setSignUpStep}
            otpCode={otpCode} setOtpCode={setOtpCode}
            onRequestOTP={(e) => onRequestOTP('register', e)}
            onVerifyOTP={(e) => onVerifyOTP('register', e)}
          />
        } />
        {/* Public team-invite accept page (no auth required). On success,
            AcceptInvite stashes the token and navigates to "/". The App's
            existing protected-route logic picks up the new token and renders
            the dashboard with activeTab='connections'. */}
        <Route path="/accept-invite" element={
          <AcceptInvite setToken={setToken} setActiveTab={setActiveTab} />
        } />
        <Route path="/onboarding" element={
          token ? (
            <Onboarding 
              user={user} setUser={setUser} authAxios={authAxios}
              connections={connections} handleConnect={handleConnect}
              handleDisconnectAccount={handleDisconnectAccount} 
              onComplete={() => {
                fetchProfile();
                navigate('/');
                setActiveTab('dashboard');
              }}
              handleLogout={handleLogout}
            />
          ) : <Navigate to="/" />
        } />
        <Route path="/*" element={
          // While the Stripe return is being confirmed server-side, show
          // a dedicated "activating" screen so the user doesn't momentarily
          // flash through /onboarding step 1 before user.onboarded flips.
          isPaymentConfirming ? (
            <div className="min-h-screen flex items-center justify-center bg-white">
              <div className="text-center">
                <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-[#F55600] text-white shadow-xl shadow-orange-200 mb-4">
                  <RefreshCw className="w-6 h-6 animate-spin" />
                </div>
                <p className="text-sm font-black uppercase tracking-[0.2em] text-[#F55600] mb-1.5">Activating subscription</p>
                <p className="text-[#2B2926] font-bold text-xs">Syncing your plan with Stripe — one moment.</p>
              </div>
            </div>
          ) :
          (token && user && user.onboarded) ? (
            <div className="flex flex-col lg:flex-row h-screen bg-[#060b13] text-white overflow-hidden">
              <header className="lg:hidden h-16 bg-[#0b1320] border-b border-slate-800 px-5 flex items-center justify-between shrink-0 z-40">
                <div className="flex items-center gap-3">
                  <img src="/montanary-elevators.png" alt="Montanary Elevators" className="w-8 h-8 object-contain rounded-lg" />
                  <span className="text-base font-bold text-white tracking-tight">Montanary Elevators</span>
                </div>
                <button 
                  onClick={() => setIsSidebarOpen(true)}
                  className="p-2.5 text-slate-400 hover:text-white hover:bg-slate-800 transition-all active:scale-95 bg-[#0d1726]/80 rounded-xl relative z-[60] border border-slate-800"
                  aria-label="Open Sidebar"
                >
                  <Menu className="w-6 h-6" />
                </button>
              </header>

              <Sidebar 
                activeTab={activeTab} setActiveTab={setActiveTab} handleRefresh={handleRefresh} 
                handleLogout={handleLogout} isOpen={isSidebarOpen} onClose={() => setIsSidebarOpen(false)} user={user}
                isExpired={isSubscriptionExpired}
              />
              
              <main className={`flex-1 overflow-auto relative bg-[#060b13] ${activeTab === 'nexus' ? 'p-0' : ['create', 'agent-post', 'connections', 'analytics', 'reputation', 'settings', 'calendar', 'scheduled', 'published', 'drafts', 'dashboard'].includes(activeTab) ? 'p-1 md:p-2 lg:p-4' : 'p-4 md:p-6 lg:p-8'}`}>

                {!['create', 'guide', 'agent-post', 'posts', 'dashboard', 'analytics', 'reputation', 'drafts', 'settings', 'published', 'scheduled', 'calendar', 'connections', 'nexus'].includes(activeTab) && (
                  <header className={`${['agent-post', 'connections', 'analytics', 'reputation', 'nexus'].includes(activeTab) ? 'mb-1' : 'mb-8'} hidden lg:flex items-center justify-between`}>
                    <h2 className="text-2xl font-bold capitalize tracking-tight text-[#2B2926]">{activeTab.replace('-', ' ')}</h2>
                  </header>
                )}

                <KeepAliveTab active={activeTab === 'dashboard'}>
                  <DashboardStats 
                    authAxios={authAxios} 
                    connections={connections} 
                    setActiveTab={setActiveTab} 
                    activeTab={activeTab} 
                    stats={dashboardStats}
                    setStats={setDashboardStats}
                    analytics={dashboardAnalytics}
                    setAnalytics={setDashboardAnalytics}
                  />
                </KeepAliveTab>
                {['create', 'CREATE'].includes(activeTab) && (
                  <div className="min-h-screen">
                    <CampaignOptionSelection onSelect={(tab) => setActiveTab(tab)} />
                  </div>
                )}
                {activeTab === 'agent-post' && (
                  <Dashboard
                    campaignBrief={campaignBrief} setCampaignBrief={setCampaignBrief} dashboardStep={dashboardStep} setDashboardStep={setDashboardStep}
                    dashboardPostType={dashboardPostType} setDashboardPostType={setDashboardPostType}
                    briefError={briefError} setBriefError={setBriefError}
                    aspectRatio={aspectRatio} setAspectRatio={setAspectRatio} imageStyle={imageStyle} setImageStyle={setImageStyle}
                    isGenerating={isGenerating} setIsGenerating={setIsGenerating} handleGenerateContent={handleGenerateContent}
                    handleRegenerateVisuals={handleRegenerateVisuals} handleCustomPrompt={handleCustomPrompt}
                    generatedData={generatedData} setGeneratedData={setGeneratedData} selectedDashboardPlatforms={selectedDashboardPlatforms} 
                    setSelectedDashboardPlatforms={setSelectedDashboardPlatforms} activeReviewPlatform={activeReviewPlatform} 
                    setActiveReviewPlatform={setActiveReviewPlatform} selectedVariants={selectedVariants} setSelectedVariants={setSelectedVariants}
                    selectedVisual={selectedVisual} setSelectedVisual={setSelectedVisual} authAxios={authAxios} connections={connections}
                    selectedTargets={selectedTargets} toggleTarget={toggleTarget}
                    uploadedImageUrl={imagePreview} setUploadedImageUrl={setImagePreview}
                    contextFiles={contextFiles} setContextFiles={setContextFiles}
                    logoImage={logoImage} setLogoImage={setLogoImage}
                    logoPreview={logoPreview} setLogoPreview={setLogoPreview}
                    user={user}
                    setActiveTab={setActiveTab}
                    setMessage={setMessage}
                    fetchPublished={fetchPublished}
                    fetchScheduled={fetchScheduled}
                  />
                )}
                {activeTab === 'connections' && (
                  <Connections
                    connections={connections}
                    handleConnect={handleConnect}
                    handleDisconnectAccount={handleDisconnectAccount}
                    handleResetConnection={handleResetConnection}
                    handleDisconnectCanva={handleDisconnectCanva}
                    user={user}
                    showIds={showIds}
                    setShowIds={setShowIds}
                    showAllAccounts={showAllAccounts}
                    setShowAllAccounts={setShowAllAccounts}
                    authAxios={authAxios}
                    // M1 — refresh connections in-place after assign instead
                    // of full page reload.
                    onConnectionsChange={fetchConnections}
                  />
                )}
                {activeTab === 'posts' && (
                  <Posts
                    postType={postType} setPostType={setPostType} setActiveTab={setActiveTab}
                    connections={connections} toggleTarget={toggleTarget} selectedTargets={selectedTargets}
                    handleImageChange={handleImageChange} imagePreview={imagePreview}
                    setImagePreview={setImagePreview} base64Image={base64Image} setBase64Image={setBase64Image}
                    mediaType={mediaType} setMediaType={setMediaType}
                    mediaUploadProgress={mediaUploadProgress} videoDurationSec={videoDurationSec}
                    fileInputRef={fileInputRef} postContent={postContent} setPostContent={setPostContent}
                    handlePublish={handlePublish} editingPlatform={editingPlatform}
                    setEditingPlatform={setEditingPlatform} resetEditor={resetEditor}
                    loading={loading} message={message} authAxios={authAxios} user={user}
                    fetchPublished={fetchPublished}
                    fetchScheduled={fetchScheduled}
                    fetchDrafts={fetchDrafts}
                  />
                )}
                
                <KeepAliveTab tabId="calendar" active={activeTab === 'calendar'}>
                  <Calendar authAxios={authAxios} posts={calendarPosts} setPosts={setCalendarPosts} user={user} />
                </KeepAliveTab>
                
                <KeepAliveTab tabId="scheduled" active={activeTab === 'scheduled'}>
                  <Scheduled 
                    authAxios={authAxios} 
                    connections={connections} 
                    user={user} 
                    posts={scheduledPosts} 
                    setPosts={setScheduledPosts} 
                    fetchScheduled={fetchScheduled}
                    loadedStatus={loadedStatus}
                  />
                </KeepAliveTab>

                <KeepAliveTab tabId="published" active={activeTab === 'published'}>
                  <Published 
                    authAxios={authAxios} 
                    user={user} 
                    connections={connections} 
                    posts={publishedPosts} 
                    setPosts={setPublishedPosts} 
                    fetchPublished={fetchPublished}
                    loadedStatus={loadedStatus}
                    onRepost={handleRepost}
                  />
                </KeepAliveTab>

                <KeepAliveTab tabId="drafts" active={activeTab === 'drafts'}>
                  <Drafts 
                    authAxios={authAxios} 
                    setActiveTab={setActiveTab} 
                    loadDraft={loadDraft} 
                    resetEditor={resetEditor} 
                    drafts={drafts} 
                    setDrafts={setDrafts} 
                    fetchDrafts={fetchDrafts}
                    loadedStatus={loadedStatus}
                  />
                </KeepAliveTab>

                {/* DUPLICATE REMOVED: Analytics was rendered both as a
                    conditional (`{activeTab === 'analytics' && <Analytics />}`)
                    earlier in this main, AND inside a KeepAliveTab here.
                    Two parallel instances fought each other — applying a
                    Brand Filter on the visible one was overwritten ~5s later
                    by the duplicate that re-fetched without the filter.
                    Now Analytics renders once via the conditional only. */}

                <KeepAliveTab active={activeTab === 'analytics'}>
                  <Analytics authAxios={authAxios} initialData={analytics} user={user} fetchAnalytics={fetchAnalytics} loadedStatus={loadedStatus} />
                </KeepAliveTab>
                <KeepAliveTab active={activeTab === 'campaign-performance'}>
                  <Analytics authAxios={authAxios} initialData={analytics} user={user} fetchAnalytics={fetchAnalytics} loadedStatus={loadedStatus} mode="post-performance-only" />
                </KeepAliveTab>
                <KeepAliveTab active={activeTab === 'reputation'}>
                   <Reputation authAxios={authAxios} user={user} initialPosts={reputationPosts} setInitialPosts={setReputationPosts} />
                </KeepAliveTab>

                <KeepAliveTab active={activeTab === 'team' && !isReadOnly(user) && (user?.pricing_plan === 'agency' || user?.pricing_plan === 'enterprise')}>
                  <Team authAxios={authAxios} user={user} />
                </KeepAliveTab>
                {activeTab === 'guide' && <UserGuide />}
                {activeTab === 'settings' && <Profile authAxios={authAxios} setMessage={setMessage} setUser={setUser} user={user} />}
                {NEXUS_ENABLED && activeTab === 'nexus' && (
                  <NexusLayout authAxios={authAxios} user={user} setMessage={setMessage} />
                )}
              </main>
            </div>
          ) : <Navigate to="/" />
        } />
      </Routes>

      {/*
        AccountSelectionModal lives at the top level — OUTSIDE the route switch
        so it appears whether the user is on `/`, `/onboarding`, or any other
        authenticated route. Previously this was duplicated inside each route
        and missing from `/onboarding`, which caused the social-connect popup
        on step 3 to flash closed with no visible follow-up modal.
      */}
      <AccountSelectionModal
        show={showAccountModal}
        onClose={() => setShowAccountModal(false)}
        platform={pendingAccounts.platform}
        accounts={pendingAccounts.accounts}
        isConnecting={isConnecting}
        onSelect={async (platform, accountIds) => {
          console.log('[AccountSelection] Selection made:', { platform, accountIds });
          
          if (!pendingAccounts.accounts || !accountIds.length) {
            console.warn('[AccountSelection] Empty selection or missing pending accounts');
            setShowAccountModal(false);
            return;
          }

          // Use String coercion to ensure IDs match correctly regardless of type (BigInt vs String)
          const selectedList = pendingAccounts.accounts.filter(a => 
            accountIds.some(id => String(id) === String(a.id))
          );
          
          console.log('[AccountSelection] Filtered selected list:', selectedList);

          if (selectedList.length === 0) {
            console.error('[AccountSelection] No matching accounts found in pending list');
            setMessage('Error: Account selection mismatch ⚠️');
            setShowAccountModal(false);
            return;
          }

          try {
            setIsConnecting(true);
            await authAxios.post('/connect-accounts', { platform, accounts: selectedList });
            setShowAccountModal(false);
            fetchConnections();
            setMessage(`${platform.charAt(0).toUpperCase() + platform.slice(1)} accounts connected! ✅`);
          } catch (err) {
            console.error('[AccountSelection] API Error:', err);
            setMessage('Failed to connect accounts ❌');
          } finally {
            setIsConnecting(false);
          }
        }}
      />

      <AnimatePresence>
        {message && <Toast msg={message} onClear={clearMessage} />}
      </AnimatePresence>

      <ForceConnectModal
        show={showForceConnectModal}
        onClose={() => setShowForceConnectModal(false)}
        connections={connections}
        handleConnect={handleConnect}
        handleDisconnectAccount={handleDisconnectAccount}
        handleResetConnection={handleResetConnection}
        handleDisconnectCanva={handleDisconnectCanva}
        user={user}
        showIds={showIds}
        setShowIds={setShowIds}
        showAllAccounts={showAllAccounts}
        setShowAllAccounts={setShowAllAccounts}
        authAxios={authAxios}
      />

      {/* Per-platform publish progress popup for Manual Post flow — same
          component the Agent Post (Dashboard.jsx) flow uses. */}
      <PublishingProgressModal
        open={manualPublishProgress.open}
        platforms={manualPublishProgress.platforms}
        results={manualPublishProgress.results}
        onClose={() => setManualPublishProgress({ open: false, platforms: [], results: null })}
      />

      <RepostModal
        isOpen={showRepostModal} 
        onClose={() => setShowRepostModal(false)}
        post={repostPost}
        authAxios={authAxios}
        user={user}
        setMessage={setMessage}
        fetchPublished={fetchPublished}
        fetchScheduled={fetchScheduled}
      />
    </>
  );
};

export default App;
