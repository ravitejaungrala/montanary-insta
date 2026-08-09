import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { CheckCircle2, ArrowRight, ArrowLeft, Linkedin, Instagram, Facebook, Users, Building2, Globe, Zap, Loader2, Sparkles, Palette, Type, Quote, Camera, RefreshCcw, MessageSquare, Info, Rocket, ChevronLeft, Upload } from 'lucide-react';
import Connections_UI from './Connections';

import { timezones, getDefaultTimezone, getFriendlyTimezoneLabel, getCurrentTimeInTimezone } from '../utils/timezones';
import { LANDING_HOME_HREF, ENTERPRISE_BOOKING_URL } from '../utils/config';

const Onboarding = ({ user, setUser, authAxios, connections, handleConnect, handleDisconnectAccount, onComplete, handleLogout }) => {
  // Deep-link support:
  //   /onboarding?step=4&kind=franchise → franchisee jumps to plan picker
  //   /onboarding?status=cancel         → user cancelled Stripe Checkout —
  //     land them back on step 5 so they don't have to re-enter name, DNA,
  //     product selection, etc. that were already saved before checkout.
  //     (Backend /payment/create-checkout-session sets cancel_url to this
  //     path; profile fields were persisted in handleFinish BEFORE the
  //     Stripe session was created, so all state is still in the DB.)
  const _readInitialStep = () => {
    try {
      const params = new URLSearchParams(window.location.search);
      // Stripe-cancel returns the user to the plan step so they can pick
      // again without redoing every prior step.
      if (params.get('status') === 'cancel') return 5;
      const requested = parseInt(params.get('step') || '', 10);
      const kind = params.get('kind') || '';
      const isFranchise = kind === 'franchise' || !!user?.franchise_of_id;
      if (requested >= 1 && requested <= 5 && isFranchise) return requested;
    } catch {}
    return 1;
  };
  const [step, setStep] = useState(_readInitialStep);
  const isFranchiseeOnboarding = !!user?.franchise_of_id;
  // Step 4 = product selection (pipelyt / gtm / both). Persist locally so
  // if the user goes Back to change it, we don't lose their choice.
  const [productSelection, setProductSelection] = useState(user?.product_selection || 'both');
  // L2 fix — keep the local card selection in sync when the `user` prop
  // refreshes AFTER mount (e.g. profile reload). Without this, if
  // user.product_selection arrives after the first render, we render
  // stale 'both' instead of the actual choice.
  React.useEffect(() => {
    if (user?.product_selection && user.product_selection !== productSelection) {
      setProductSelection(user.product_selection);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.product_selection]);
  const [savingProductSelection, setSavingProductSelection] = useState(false);
  const [loading, setLoading] = useState(false);
  // H3 fix — surface async errors (Stripe / DNA extract / profile save) as
  // an inline red banner instead of silent console.error. Cleared on the
  // next successful action or step change.
  const [error, setError] = useState('');
  // M1 fix — inline error for logo upload (replaces browser alert()).
  const [logoError, setLogoError] = useState('');
  // Clear the top-level error whenever the user moves between steps —
  // stale errors from step 5 shouldn't linger on step 4 back-navigation.
  const _clearError = () => setError('');
  const [billingCycle, setBillingCycle] = useState('monthly'); // 'monthly' or 'yearly'
  const logoInputRef = React.useRef(null);
  const [couponCode, setCouponCode] = useState('');
  const [isValidatingCoupon, setIsValidatingCoupon] = useState(false);
  // Holds the validated promo code + discount once Stripe confirms it.
  // Shape: { code: 'SAVE20', discount: 20 }  (discount is either % or $ off)
  const [appliedCoupon, setAppliedCoupon] = useState(null);
  const [couponError, setCouponError] = useState('');
  const [formData, setFormData] = useState({
    full_name: user?.full_name || '',
    company_name: user?.company_name || '',
    company_description: user?.company_description || '',
    product_details: user?.product_details || '',
    timezone: user?.timezone || getDefaultTimezone(),
    business_url: user?.business_url || '',
    business_dna: user?.business_dna || null
  });
  const [currentClock, setCurrentClock] = useState('');
  // Tracks whether the logo image URL (from backend DNA extraction) failed to
  // load. When true we render the Upload placeholder instead of a broken <img>,
  // even if business_dna.logo_url is present. Resets when the URL changes.
  const [logoLoadFailed, setLogoLoadFailed] = useState(false);

  React.useEffect(() => {
    setLogoLoadFailed(false);
  }, [user?.business_dna?.logo_url, formData?.business_dna?.logo_url]);

  React.useEffect(() => {
    const tick = () => {
      setCurrentClock(getCurrentTimeInTimezone(formData.timezone));
    };
    tick();
    const timer = setInterval(tick, 1000 * 30); // update every 30s
    return () => clearInterval(timer);
  }, [formData.timezone]);

  const totalSteps = 5;

  const handleNext = async () => {
    _clearError();
    if (step < totalSteps) {
      setStep(step + 1);
    }
  };

  const handleBack = () => {
    _clearError();
    // Franchisees jump straight to step 4 (product selection) and cannot
    // step back into brand-DNA / company setup — those belong to the
    // Master whose brand they're franchising. They CAN move between
    // step 4 (product selection) and step 5 (plan picker) freely.
    const minStep = isFranchiseeOnboarding ? 4 : 1;
    if (step > minStep) setStep(step - 1);
  };

  // Persist product_selection to the backend + advance to the plan step.
  // Called by the "Continue" button on step 4.
  const handleSaveProductSelection = async (choice) => {
    try {
      setSavingProductSelection(true);
      const { data } = await authAxios.put('/profile/product-selection', {
        product_selection: choice,
      });
      setUser?.(data);
    } catch (e) {
      // Non-fatal — advance anyway. The value will be saved next time the
      // user hits Continue OR later from the Settings page.
      console.warn('[onboarding] product_selection save failed:', e);
    } finally {
      setSavingProductSelection(false);
      setStep(5);
    }
  };

  // Helper to update deeply nested DNA state
  const updateDNA = (key, value) => {
    setFormData(prev => ({
      ...prev,
      business_dna: { ...prev.business_dna, [key]: value }
    }));
  };

  const updateDNAProp = (key, value) => {
    setFormData(prev => ({
      ...prev,
      [key]: value
    }));
  };

  const updateColor = (key, value) => {
    setFormData(prev => ({
      ...prev,
      business_dna: {
        ...prev.business_dna,
        colors: { ...prev.business_dna.colors, [key]: value }
      }
    }));
  };

  const removeTag = (key, idx) => {
    const newTags = [...formData.business_dna[key]];
    newTags.splice(idx, 1);
    updateDNA(key, newTags);
  };

  const addTag = (key, val) => {
    if (!val) return;
    updateDNA(key, [...(formData.business_dna[key] || []), val]);
  };

  // Handle manual brand-mark upload: validate type/size, read as data URL, and
  // overwrite the auto-extracted logo_url. Resets the load-failed flag so the
  // <img> retries with the new source.
  const handleLogoUpload = (e) => {
    const file = e.target?.files?.[0];
    if (!file) return;
    // M1 fix — inline validation errors instead of browser alert() dialogs.
    // Cleared on success or when the user picks a different file.
    if (!file.type.startsWith('image/')) {
      setLogoError('Please choose an image file (PNG, JPG, SVG, etc.)');
      e.target.value = '';
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      setLogoError('Logo too large. Max size is 5 MB.');
      e.target.value = '';
      return;
    }
    setLogoError('');
    const reader = new FileReader();
    reader.onload = (ev) => {
      updateDNA('logo_url', ev.target.result);
      setLogoLoadFailed(false);
    };
    reader.readAsDataURL(file);
    e.target.value = '';
  };

  const handleValidateCoupon = async () => {
    const code = couponCode.trim();
    if (!code) return;
    setIsValidatingCoupon(true);
    setCouponError('');
    try {
      const res = await authAxios.post('/payment/verify-coupon', { code });
      if (res.data?.valid) {
        setAppliedCoupon({ code, discount: res.data.discount });
      } else {
        setAppliedCoupon(null);
        setCouponError('Invalid or expired code');
      }
    } catch (err) {
      setAppliedCoupon(null);
      setCouponError('Could not verify code — try again');
    } finally {
      setIsValidatingCoupon(false);
    }
  };

  const handleRemoveCoupon = () => {
    setAppliedCoupon(null);
    setCouponCode('');
    setCouponError('');
  };

  const handleFinish = async (plan = 'free') => {
    setLoading(true);
    setError('');
    try {
      // 1. Save all profile/dna data and force 'enterprise' pricing plan (free, unlimited)
      await authAxios.put('/profile', { ...formData, pricing_plan: 'enterprise' });

      // 2. Directly complete the onboarding without any payment session
      const completeRes = await authAxios.post('/onboarding/complete');
      setUser(completeRes.data);
      onComplete();
    } catch (err) {
      console.error(err);
      setError(err?.response?.data?.detail || 'Could not complete onboarding. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleExtractDNA = async () => {
    if (!formData.business_url) return;
    setLoading(true);
    setError('');
    try {
      // L1 fix — 60s client-side timeout so the button spinner can't spin
      // forever if the backend hangs on a slow website. Server-side
      // Jina/Microlink calls typically finish in 10-30s.
      const res = await authAxios.post(
        '/profile/extract-dna',
        { url: formData.business_url },
        { timeout: 60000 }
      );
      setFormData(prev => ({
        ...prev,
        company_name: res.data.company_name || prev.company_name,
        company_description: res.data.overview || prev.company_description,
        business_dna: res.data
      }));
      // Stay on step 2 to allow preview/edit
    } catch (err) {
      // H3 fix — surface the error inline. Timeout vs 4xx/5xx get distinct
      // messages so the user knows whether to retry the same URL or try
      // a different one.
      console.error(err);
      if (err?.code === 'ECONNABORTED') {
        setError('DNA extraction timed out. The website may be slow — try a different URL or try again.');
      } else {
        setError(err?.response?.data?.detail || 'Could not extract DNA from that URL. Please check the address and try again.');
      }
    } finally {
      setLoading(false);
    }
  };

  const stepVariants = {
    initial: { opacity: 0, x: 20 },
    animate: { opacity: 1, x: 0 },
    exit: { opacity: 0, x: -20 }
  };

  return (
    <div className="min-h-screen bg-white flex flex-col items-center justify-start pt-2 px-4 pb-4 md:p-6 relative">


      <div className={`w-full transition-all duration-700 ${[2, 3, 4].includes(step) ? 'max-w-6xl' : (step === 5 ? 'max-w-7xl' : 'max-w-2xl')}`}>
        {/* Logo/Brand */}
        <div className="flex flex-col items-center mb-4">

          <a href={LANDING_HOME_HREF} className="block">

            <div className="w-11 h-11 bg-[#F55600] rounded-xl flex items-center justify-center text-white font-black text-2xl shadow-xl shadow-orange-200 mb-2 cursor-pointer">

              P
            </div>
          </a>
          <h2 className="text-lg font-black text-[#2B2926] tracking-tight">Configure Your Workspace</h2>
          {/* H1 fix — progress dots now show all 5 steps (was hard-coded to
              3 back when onboarding had 3 steps, then 4, now 5). */}
          <div className="mt-2 flex gap-2">
            {[1, 2, 3, 4, 5].map(i => (
              <div
                key={i}
                className={`h-1.5 rounded-full transition-all duration-700 ${step >= i ? 'w-10 bg-[#F55600]' : 'w-5 bg-slate-200'}`}
              />
            ))}
          </div>
          {/* H3 fix — global error banner. Rendered here so it's visible
              regardless of which step raised it. Auto-clears on step nav. */}
          {error && (
            <div className="mt-3 w-full max-w-2xl bg-red-50 border border-red-200 rounded-xl px-4 py-3 flex items-start gap-3">
              <div className="w-5 h-5 rounded-full bg-red-500 text-white flex items-center justify-center flex-shrink-0 text-xs font-black mt-0.5">!</div>
              <div className="flex-1 text-xs font-bold text-red-700 leading-relaxed">{error}</div>
              <button
                type="button"
                onClick={_clearError}
                className="text-red-500 hover:text-red-700 text-lg leading-none flex-shrink-0"
                aria-label="Dismiss error"
              >
                ×
              </button>
            </div>
          )}
        </div>

        <AnimatePresence mode="wait">
          {step === 1 && (
            <motion.div
              key="step1" variants={stepVariants} initial="initial" animate="animate" exit="exit"
              className="bg-white p-6 rounded-[24px] border border-[#F55600]/20 shadow-[0_20px_60px_rgba(0,0,0,0.05)]"
            >


              <div className="flex items-center gap-5 mb-6">

                <div className="w-12 h-12 bg-orange-50 rounded-2xl flex items-center justify-center text-[#F55600]">
                  <Users className="w-6 h-6" />
                </div>
                <div>
                  <h3 className="text-xl font-black text-[#2B2926] tracking-tight leading-none mb-1.5">Personal Identity</h3>
                  <p className="text-slate-400 font-bold text-xs uppercase tracking-wider">Profile basics</p>
                </div>
              </div>


              <div className="space-y-4">

                <div className="space-y-2">
                  <label className="text-[10px] font-black text-slate-600 uppercase tracking-[0.15em] pl-1">Full Name</label>


                  <input
                    type="text"
                    value={formData.full_name}
                    onChange={e => setFormData({ ...formData, full_name: e.target.value })}
                    placeholder="E.g. Sam"
                    className="w-full bg-slate-50 border-2 border-transparent focus:border-orange-100 focus:bg-white text-[#2B2926] h-11 px-5 rounded-xl transition-all outline-none text-base font-bold placeholder:text-slate-300"
                  />
                </div>



                <div className="space-y-2">
                  <label className="text-[10px] font-black text-slate-600 uppercase tracking-[0.15em] pl-1">Timezone Preference</label>


                  <div className="relative">
                    <Globe className="absolute left-6 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-300 pointer-events-none" />
                    <select
                      value={formData.timezone}
                      onChange={e => setFormData({ ...formData, timezone: e.target.value })}
                      className="w-full bg-slate-50 border-2 border-transparent focus:border-orange-100 focus:bg-white text-[#2B2926] h-11 pl-12 pr-28 rounded-xl transition-all outline-none text-sm font-bold appearance-none cursor-pointer truncate"

                    >
                      {/* Ensure detected timezone is an option */}
                      {!timezones.find(tz => tz.value === formData.timezone) && (
                        <option value={formData.timezone}>{getFriendlyTimezoneLabel(formData.timezone)} (Detected)</option>
                      )}
                      {timezones.map(tz => (
                        <option key={tz.value} value={tz.value}>{tz.label}</option>
                      ))}
                    </select>

                    {currentClock && (
                      <div className="absolute right-3 top-1/2 -translate-y-1/2 flex items-center gap-1.5 bg-orange-50 px-2 py-1 rounded-lg border border-orange-100 pointer-events-none">
                        <Zap className="w-3 h-3 text-[#F55600]" />
                        <span className="text-[9px] font-black text-[#F55600] uppercase tracking-wider whitespace-nowrap">{currentClock}</span>
                      </div>
                    )}
                  </div>
                </div>

              </div>

              <div className="mt-8 flex justify-end">
                <button
                  onClick={handleNext}
                  disabled={!formData.full_name}
                  className="px-8 h-12 bg-slate-900 text-white font-black text-[10px] uppercase tracking-[0.2em] rounded-xl shadow-xl hover:bg-[#F55600] transition-all flex items-center justify-center gap-3 disabled:opacity-50"
                >
                  Continue <ArrowRight className="w-4 h-4" />
                </button>

              </div>

            </motion.div>
          )}

          {step === 2 && (
            <motion.div
              key="step2" variants={stepVariants} initial="initial" animate="animate" exit="exit"
              className="bg-white p-6 rounded-[24px] border border-[#F55600]/20 shadow-[0_20px_60px_rgba(0,0,0,0.05)] w-full max-w-5xl mx-auto"
            >



              {!formData.business_dna ? (
                <>
                  <div className="flex items-center gap-5 mb-6">

                    <div className="w-12 h-12 bg-orange-50 rounded-2xl flex items-center justify-center text-[#F55600]">
                      <Building2 className="w-6 h-6" />
                    </div>
                    <div>
                      <h3 className="text-xl font-black text-[#2B2926] tracking-tight leading-none mb-1.5">Business DNA</h3>
                      <p className="text-slate-500 font-bold text-xs uppercase tracking-wider">Brand assets</p>
                    </div>
                  </div>



                  <div className="space-y-6">
                    <div className="space-y-2">
                      <label className="text-[10px] font-black text-slate-600 uppercase tracking-[0.15em] pl-1">Business Website</label>


                      <div className="relative">
                        <Globe className="absolute left-6 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-300" />
                        <input
                          type="text"
                          value={formData.business_url}
                          onChange={e => setFormData({ ...formData, business_url: e.target.value })}
                          placeholder="https://yourcompany.com"
                          className="w-full bg-slate-50 border-2 border-transparent focus:border-[#F55600] focus:bg-white text-[#2B2926] h-12 pl-14 pr-4 rounded-xl transition-all outline-none text-base font-bold"

                        />
                      </div>
                    </div>


                    <p className="text-[11px] text-slate-500 font-medium italic px-2 leading-relaxed">
                      Our AI will visit your website to extract your professional logo, brand colors, and mission to personalize your content strategy.
                    </p>
                  </div>


                  <div className="mt-8 flex justify-between items-center">
                    <button onClick={handleBack} className="text-slate-600 font-black uppercase tracking-widest text-[9px] flex items-center gap-2 hover:text-[#F55600] transition-colors">

                      <ArrowLeft className="w-3.5 h-3.5" /> Back
                    </button>
                    <button
                      onClick={handleExtractDNA}
                      disabled={loading || !formData.business_url}
                      className={`px-8 h-12 font-black text-[10px] uppercase tracking-[0.15em] rounded-xl shadow-xl transition-all flex items-center justify-center gap-3 disabled:opacity-50 ${!formData.business_url ? 'bg-orange-100 text-orange-400 shadow-none' : 'bg-[#F55600] text-white shadow-orange-100 hover:bg-[#e65a2b]'}`}
                    >
                      {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
                      Fetch DNA
                    </button>


                  </div>

                </>
              ) : (
                <div className="space-y-6">
                  <div className="flex items-center justify-between mb-4">

                    <div className="flex items-center gap-5">
                      <div className="w-11 h-11 bg-green-50 rounded-[18px] flex items-center justify-center text-green-500 shadow-sm border border-green-100">
                        <CheckCircle2 className="w-5 h-5" />
                      </div>
                      <div>
                        <h3 className="text-xl font-black text-[#2B2926] tracking-tight">Identity Verified</h3>
                        <p className="text-slate-400 font-bold text-xs">Review and refine your business personality below.</p>
                      </div>
                    </div>

                    <button
                      onClick={() => setFormData({ ...formData, business_dna: null })}
                      className="text-[10px] font-black uppercase tracking-widest text-[#2B2926] hover:text-red-500 transition-all bg-white px-5 py-2.5 rounded-xl border-2 border-slate-100 hover:border-red-100 hover:shadow-sm"
                    >
                      Search Again
                    </button>

                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-6 gap-3">
                    {/* Identity Card */}
                    <div className="md:col-span-4 bg-slate-50/50 p-3.5 rounded-xl border border-slate-100 flex flex-col justify-center">
                      <div className="flex items-center gap-2 text-slate-600 mb-1.5">
                        <Building2 className="w-3 h-3 text-[#F55600]" />
                        <span className="text-[8px] font-black uppercase tracking-widest">Company Name</span>
                      </div>
                      <input
                        type="text"
                        value={formData.company_name || ''}
                        onChange={e => updateDNAProp('company_name', e.target.value)}
                        className="w-full bg-transparent text-2xl font-black text-[#2B2926] tracking-tight outline-none focus:text-[#F55600] transition-colors p-0"
                      />
                      <div className="flex items-center gap-2 text-slate-400 text-xs font-bold mt-2">
                        <Globe className="w-3 h-3 text-[#F55600]" />
                        <input
                          type="text"
                          value={formData.business_url || ''}
                          onChange={e => updateDNAProp('business_url', e.target.value)}
                          className="bg-transparent outline-none focus:text-[#2B2926] transition-colors w-full p-0"
                        />
                      </div>
                    </div>

                    {/* Logo Card */}
                    <div className="md:col-span-2 bg-slate-50/50 p-3.5 rounded-xl border border-slate-100 flex flex-col items-center justify-center relative">
                      {/* Hidden file input wired to logoInputRef. Triggered by
                          either clicking the logo preview tile OR the explicit
                          'Upload' button below. */}
                      <input
                        ref={logoInputRef}
                        type="file"
                        accept="image/*"
                        onChange={handleLogoUpload}
                        className="hidden"
                      />
                      <div className="flex items-center justify-between gap-2 text-slate-400 mb-3 w-full">
                        <div className="flex items-center gap-1.5">
                          <Camera className="w-3 h-3 text-[#F55600]" />
                          <span className="text-[8px] font-black uppercase tracking-widest text-slate-600">Brand Mark</span>
                        </div>
                        <button
                          type="button"
                          onClick={() => logoInputRef.current?.click()}
                          className="text-[8px] font-black uppercase tracking-widest text-[#F55600] hover:text-orange-600 transition-colors flex items-center gap-1"
                        >
                          <Upload className="w-3 h-3" />
                          {formData.business_dna.logo_url ? 'Change' : 'Upload'}
                        </button>
                      </div>
                      <div
                        onClick={() => logoInputRef.current?.click()}
                        title="Click to upload your own brand logo"
                        className="relative w-14 h-14 bg-white rounded-xl p-2.5 flex items-center justify-center hover:scale-105 transition-transform cursor-pointer shadow-sm border border-slate-100 group"
                      >
                        {formData.business_dna.logo_url && !logoLoadFailed ? (
                          <>
                            <img
                              src={formData.business_dna.logo_url}
                              alt="Logo"
                              className="max-w-full max-h-full object-contain"
                              onError={() => setLogoLoadFailed(true)}
                              referrerPolicy="no-referrer"
                            />
                            <div className="absolute -right-2 -top-2 w-6 h-6 bg-[#F55600] rounded-full shadow-lg border-2 border-white flex items-center justify-center text-white opacity-0 group-hover:opacity-100 transition-opacity">
                              <Camera className="w-3 h-3" />
                            </div>
                          </>
                        ) : (
                          <Upload className="w-6 h-6 text-slate-300" />
                        )}
                      </div>
                      {/* M1 fix — inline logo-upload error (replaces browser
                          alert() dialogs). Shows below the preview tile so it
                          stays visually anchored to the file input. */}
                      {logoError && (
                        <p className="mt-2 text-[9px] font-bold text-red-500 text-center leading-tight px-1">
                          {logoError}
                        </p>
                      )}
                    </div>

                    {/* Tagline Card */}
                    <div className="md:col-span-6 bg-[#F55600] p-3.5 rounded-[20px] shadow-lg shadow-orange-100 flex flex-col justify-center relative overflow-hidden">
                      <Quote className="absolute -right-4 -bottom-4 w-20 h-20 text-white/10 rotate-12" />
                      <div className="flex items-center gap-2 text-white mb-1.5 relative z-10">
                        <Quote className="w-3.5 h-3.5" />
                        <span className="text-[9px] font-black uppercase tracking-widest text-white">Brand Narrative</span>
                      </div>
                      <textarea
                        value={formData.business_dna.tagline || ''}
                        onChange={e => updateDNA('tagline', e.target.value)}
                        rows={1}
                        placeholder="Unlocking Digital Potential..."
                        className="w-full bg-transparent text-lg font-black text-white italic leading-tight outline-none resize-none border-none p-0 focus:ring-0 placeholder:text-white/40"
                      />
                    </div>


                    {/* Palette */}
                    <div className="md:col-span-3 bg-white p-5 rounded-[24px] border border-slate-100 shadow-sm">
                      <div className="flex items-center gap-2 text-slate-400 mb-4">

                        <Palette className="w-3.5 h-3.5 text-[#F55600]" />
                        <span className="text-[9px] font-black uppercase tracking-widest text-slate-600">Brand Palette</span>

                      </div>
                      <div className="flex items-center justify-between">
                        {Object.entries(formData.business_dna.colors || {}).map(([key, color]) => (
                          <div key={key} className="flex flex-col items-center gap-2">
                            <div className="relative">
                              <div className="w-10 h-10 rounded-full border-2 border-white shadow-md active:scale-95 transition-transform" style={{ background: color }} />
                              <input type="color" value={color?.startsWith('#') ? color : '#000000'}
                                onChange={e => updateColor(key, e.target.value)}
                                className="absolute inset-0 opacity-0 cursor-pointer" />
                            </div>
                            <span className="text-[8px] font-black text-slate-400 uppercase">{color}</span>
                          </div>
                        ))}
                      </div>
                    </div>

                    <div className="md:col-span-3 bg-white p-5 rounded-[24px] border border-slate-100 shadow-sm">
                      <div className="flex items-center gap-2 text-slate-400 mb-3">

                        <Type className="w-3.5 h-3.5 text-[#F55600]" />
                        <span className="text-[9px] font-black uppercase tracking-widest text-slate-600">Typography</span>

                      </div>
                      <div className="flex items-center gap-4">
                        <span className="text-4xl font-black text-[#2B2926]">Aa</span>
                        <input value={(formData.business_dna.fonts || []).join(', ')}
                          onChange={e => updateDNA('fonts', e.target.value.split(',').map(s => s.trim()))}
                          className="bg-slate-50 px-4 py-2 rounded-xl text-xs font-bold text-[#F55600] outline-none w-full border border-slate-100 focus:border-orange-200"
                          placeholder="Inter, Roboto" />
                      </div>
                    </div>

                    {[
                      { key: 'brand_values', label: 'Brand DNA Values', icon: Sparkles, color: 'slate' },
                      { key: 'brand_aesthetic', label: 'Brand Aesthetic', icon: Palette, color: 'orange' },
                      { key: 'brand_tone', label: 'Tone of Voice', icon: MessageSquare, color: 'blue' }
                    ].map(({ key, label, icon: Icon, color }) => (
                      <div key={key} className="md:col-span-2 bg-white p-5 rounded-[24px] border border-slate-100 shadow-sm flex flex-col">
                        <div className="flex items-center gap-2 text-slate-400 mb-3">

                          <Icon className="w-3.5 h-3.5 text-[#F55600]" />
                          <span className="text-[9px] font-black uppercase tracking-widest text-slate-600">{label}</span>

                        </div>
                        <div className="flex flex-wrap gap-2 mb-4 flex-1">
                          {(formData.business_dna[key] || []).map((val, idx) => (
                            <button key={idx} onClick={() => removeTag(key, idx)}
                              className={`px-2 py-1 rounded-lg text-[9px] font-bold border transition-all ${color === 'orange' ? 'bg-orange-50 text-[#F55600] border-orange-100' :
                                color === 'blue' ? 'bg-blue-50 text-blue-600 border-blue-100' : 'bg-slate-50 text-slate-600 border-slate-100'
                                }`}
                            >
                              {val} ×
                            </button>
                          ))}
                        </div>
                        <input type="text" placeholder="Add..."
                          onKeyDown={e => { if (e.key === 'Enter') { addTag(key, e.target.value); e.target.value = ''; } }}
                          className="w-full bg-slate-50 border-none h-8 px-3 rounded-lg text-[9px] font-bold outline-none focus:ring-1 focus:ring-[#F55600] border border-slate-100" />
                      </div>
                    ))}

                    <div className="md:col-span-6 bg-white p-5 rounded-[24px] border border-slate-100 shadow-sm">
                      <div className="flex items-center gap-2 text-slate-400 mb-1">

                        <Info className="w-3.5 h-3.5 text-[#F55600]" />
                        <span className="text-[9px] font-black uppercase tracking-widest text-slate-600">Business Mission</span>

                      </div>
                      <textarea
                        value={formData.business_dna.overview || ''}
                        onChange={e => updateDNA('overview', e.target.value)}
                        rows={3}
                        className="w-full bg-transparent text-slate-600 text-sm font-medium leading-relaxed outline-none resize-none border-none p-0 focus:ring-0"
                      />
                    </div>
                  </div>

                  <div className="mt-5 flex justify-end">

                    <button
                      onClick={handleNext}
                      className="px-8 h-12 bg-[#F55600] text-white font-black text-[10px] uppercase tracking-[0.15em] rounded-xl shadow-lg shadow-orange-100 hover:bg-[#e65a2b] transition-all flex items-center justify-center gap-3"
                    >
                      Confirm Identity <ArrowRight className="w-4 h-4" />
                    </button>

                  </div>


                </div>
              )}
            </motion.div>
          )}
          {step === 3 && (
            <motion.div
              key="step3" variants={stepVariants} initial="initial" animate="animate" exit="exit"
              className="bg-white p-4 pt-3 rounded-[24px] border border-[#F55600]/20 shadow-[0_20px_60px_rgba(0,0,0,0.05)]"
            >

              <div className="flex items-center gap-5 mb-2">
                <div className="w-12 h-12 bg-blue-50 rounded-2xl flex items-center justify-center text-blue-500">
                  <Globe className="w-6 h-6" />
                </div>
                <div>
                  <h3 className="text-lg font-black text-[#2B2926] tracking-tight leading-none mb-1.5">Social Networks</h3>
                  <p className="text-slate-400 font-bold text-xs uppercase tracking-wider">Connect accounts</p>
                </div>
              </div>

              <div className="mt-2 scale-90 -mx-4">
                <Connections_UI
                  connections={connections}
                  handleConnect={handleConnect}
                  handleDisconnectAccount={handleDisconnectAccount}
                  handleResetConnection={() => {}}
                  handleDisconnectCanva={() => {}}
                  user={user}
                  authAxios={authAxios}
                  hideHeader={true}
                  showIds={false}
                  setShowIds={() => {}}
                />
              </div>

              <div className="mt-2 flex justify-between items-center">
                <button onClick={handleBack} className="text-slate-600 font-black uppercase tracking-widest text-[9px] flex items-center gap-2 hover:text-[#2B2926] transition-colors">
                  <ArrowLeft className="w-3.5 h-3.5" /> Back
                </button>
                {/* L3 fix — removed the redundant "Skip" button. Clicking
                    Continue without any connection has the same effect as
                    skipping (both call handleNext), and having two buttons
                    do the same thing confused users. Connections are still
                    optional on this step. */}
                <button onClick={handleNext} className="px-8 h-11 bg-slate-900 text-white font-black text-[10px] uppercase tracking-[0.15em] rounded-xl shadow-xl hover:bg-[#F55600] transition-all flex items-center justify-center gap-3">
                  Continue
                </button>
              </div>
            </motion.div>
          )}
          {step === 4 && (
            <motion.div
              key="step4-product-selection" variants={stepVariants} initial="initial" animate="animate" exit="exit"
              className="bg-white p-4 pt-3 rounded-[24px] border border-[#F55600]/20 shadow-[0_20px_60px_rgba(0,0,0,0.05)]"
            >
              <div className="flex items-center gap-5 mb-3">
                <div className="w-12 h-12 bg-orange-50 rounded-2xl flex items-center justify-center text-[#F55600]">
                  <Sparkles className="w-6 h-6" />
                </div>
                <div>
                  <h3 className="text-lg font-black text-[#2B2926] tracking-tight leading-none mb-1.5">Choose Your Product</h3>
                  <p className="text-slate-400 font-bold text-xs uppercase tracking-wider">You can change this anytime in Settings</p>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-2">
                {[
                  {
                    // Backend value stays 'pipelyt' — only the display label changes
                    // to "Content Generation" per product feedback (users understand
                    // what it does, not the brand name of the surface).
                    key: 'pipelyt',
                    title: 'Content Generation',
                    tagline: 'AI Content Creation Suite',
                    icon: Sparkles,
                    bullets: [
                      'Text post generation',
                      'Image post generation',
                      'Video post generation',
                      'Carousel post generation',
                      'Bulk scheduling',
                      'Auto-commenting',
                      'Analytics dashboard',
                      'Reputation management',
                    ],
                  },
                  {
                    key: 'gtm',
                    title: 'GTM',
                    tagline: 'Lead Generation & Outreach',
                    icon: Rocket,
                    bullets: [
                      'Lead sourcing & enrichment',
                      'Multi-step email sequences',
                      'LinkedIn outreach automation',
                      'Meeting booking automation',
                      'Reply tracking & inbox',
                      'Sequence performance analytics',
                    ],
                  },
                  {
                    key: 'both',
                    title: 'Content Generation + GTM',
                    tagline: 'Full Growth Suite (Recommended)',
                    icon: Zap,
                    bullets: [
                      'Text / Image / Video / Carousel post generation',
                      'Bulk scheduling & auto-commenting',
                      'Analytics & reputation management',
                      'Lead sourcing & enrichment',
                      'Multi-step email + LinkedIn sequences',
                      'Meeting booking & reply tracking',
                      'Unified workflow across both surfaces',
                    ],
                  },
                ].map((opt) => {
                  const Icon = opt.icon;
                  const isSelected = productSelection === opt.key;
                  return (
                    <button
                      key={opt.key}
                      type="button"
                      onClick={() => setProductSelection(opt.key)}
                      className={`text-left p-4 rounded-2xl border-2 transition-all ${
                        isSelected
                          ? 'border-[#F55600] bg-[#F55600]/5 shadow-[0_10px_30px_rgba(245,86,0,0.15)]'
                          : 'border-slate-200 bg-white hover:border-slate-400'
                      }`}
                    >
                      <div className="flex items-center justify-between mb-2">
                        <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${
                          isSelected ? 'bg-[#F55600] text-white' : 'bg-slate-100 text-slate-500'
                        }`}>
                          <Icon className="w-5 h-5" />
                        </div>
                        {isSelected && (
                          <CheckCircle2 className="w-5 h-5 text-[#F55600]" />
                        )}
                      </div>
                      <h4 className="text-sm font-black text-[#2B2926] tracking-tight mb-1">{opt.title}</h4>
                      <p className="text-[10px] font-bold uppercase tracking-wider text-slate-500 mb-3">{opt.tagline}</p>
                      <ul className="space-y-1">
                        {opt.bullets.map((b, i) => (
                          <li key={i} className="text-xs text-slate-600 flex items-start gap-2">
                            <span className={`mt-0.5 w-1 h-1 rounded-full flex-shrink-0 ${
                              isSelected ? 'bg-[#F55600]' : 'bg-slate-400'
                            }`} />
                            <span>{b}</span>
                          </li>
                        ))}
                      </ul>
                    </button>
                  );
                })}
              </div>

              <div className="mt-4 flex justify-between items-center">
                {isFranchiseeOnboarding ? (
                  <span />
                ) : (
                  <button onClick={handleBack} className="text-slate-600 font-black uppercase tracking-widest text-[9px] flex items-center gap-2 hover:text-[#2B2926] transition-colors">
                    <ArrowLeft className="w-3.5 h-3.5" /> Back
                  </button>
                )}
                <button
                  onClick={() => handleSaveProductSelection(productSelection)}
                  disabled={savingProductSelection}
                  className="px-8 h-11 bg-slate-900 text-white font-black text-[10px] uppercase tracking-[0.15em] rounded-xl shadow-xl hover:bg-[#F55600] transition-all flex items-center justify-center gap-3 disabled:opacity-50"
                >
                  {savingProductSelection ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <>Continue <ArrowRight className="w-3.5 h-3.5" /></>}
                </button>
              </div>
            </motion.div>
          )}
          {step === 5 && (
            <motion.div
              key="step5" variants={stepVariants} initial="initial" animate="animate" exit="exit"
              className="bg-white p-4 pt-2 rounded-[24px] border border-[#F55600]/20 shadow-[0_30px_80px_rgba(0,0,0,0.05)]"
            >

              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-5">
                  <div className="w-12 h-12 bg-orange-50 rounded-2xl flex items-center justify-center text-[#F55600]">
                    <Zap className="w-6 h-6" />
                  </div>
                  <div>
                    <h3 className="text-lg font-black text-[#2B2926] tracking-tight leading-none mb-1.5">Select Plan</h3>
                    <p className="text-slate-400 font-bold text-xs uppercase tracking-wider">Choose your growth tier</p>
                  </div>
                </div>

                <div className="flex items-center gap-4 bg-slate-50 p-1 rounded-xl border border-slate-100">
                  <button
                    onClick={() => setBillingCycle('monthly')}
                    className={`px-5 py-1.5 rounded-lg text-[9px] font-black uppercase tracking-widest transition-all ${billingCycle === 'monthly' ? 'bg-white shadow-sm text-[#2B2926]' : 'text-slate-400 hover:text-slate-600'}`}
                  >
                    Monthly
                  </button>
                  <button
                    onClick={() => setBillingCycle('yearly')}
                    className={`px-5 py-1.5 rounded-lg text-[9px] font-black uppercase tracking-widest transition-all ${billingCycle === 'yearly' ? 'bg-white shadow-sm text-[#2B2926]' : 'text-slate-400 hover:text-slate-600'}`}
                  >
                    Yearly <span className="ml-1 text-emerald-500"> -20%</span>
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4 items-stretch pt-4">

                {[
                  {
                    id: 'starter',
                    name: 'Starter',
                    monthlyPrice: 49,
                    annualMonthlyPrice: 39,
                    annualTotal: 468,
                    desc: 'IDEAL FOR SOLOPRENEURS',
                    iconBg: 'bg-orange-50',
                    iconColor: 'text-[#F55600]',
                    features: ['2 brand DNAs', '3 channels', '30 AI posts / mo', '60 AI images / mo', 'Email support']
                  },
                  {
                    id: 'growth',
                    name: 'Growth',
                    monthlyPrice: 99,
                    annualMonthlyPrice: 79,
                    annualTotal: 948,
                    desc: 'IDEAL FOR TEAMS',
                    badge: 'MOST POPULAR',
                    badgeColor: 'bg-[#F55600]',
                    iconBg: 'bg-red-50',
                    iconColor: 'text-red-500',
                    highlight: true,
                    features: ['5 brand DNAs', '5 channels', '60 AI posts / mo', '120 AI images / mo', 'Priority support']
                  },
                  {
                    id: 'agency',
                    name: 'Agency',
                    monthlyPrice: 249,
                    annualMonthlyPrice: 199,
                    annualTotal: 2388,
                    desc: 'IDEAL FOR AGENCIES',
                    badge: 'AGENCY',
                    badgeColor: 'bg-slate-900',
                    iconBg: 'bg-blue-50',
                    iconColor: 'text-blue-600',
                    features: ['10 brand DNAs', 'Unlimited channels', '120 AI posts / mo', '240 AI images', 'Dedicated CSM']
                  },
                  {
                    id: 'enterprise',
                    name: 'Enterprise',
                    monthlyPrice: 0,
                    annualMonthlyPrice: 0,
                    annualTotal: 0,
                    isCustom: true,
                    desc: 'IDEAL FOR ORGANIZATIONS',
                    badge: 'ENTERPRISE',
                    badgeColor: 'bg-slate-900',
                    iconBg: 'bg-orange-50',
                    iconColor: 'text-orange-600',
                    features: ['Unlimited DNAs', 'SSO + SOC 2', 'Custom volumes', 'Private model routing', 'Solutions engineer']
                  },
                ].map(plan => {
                  const isYearly = billingCycle === 'yearly';
                  const displayPrice = isYearly ? plan.annualMonthlyPrice : plan.monthlyPrice;

                  return (
                    <div
                      key={plan.id}
                      className={`relative flex flex-col p-6 rounded-[24px] border transition-all ${plan.highlight ? 'border-[#F55600] shadow-xl shadow-orange-100 ring-4 ring-orange-50' : 'border-slate-100 bg-slate-50/30'}`}
                    >
                      {plan.badge && (
                        <div className={`absolute -top-3 left-1/2 -translate-x-1/2 ${plan.badgeColor || 'bg-slate-800'} text-white text-[8px] font-black px-4 py-1 rounded-full uppercase tracking-widest whitespace-nowrap z-10 shadow-lg`}>
                          {plan.badge}
                        </div>
                      )}

                      <div className="mb-4">
                        <h4 className="text-lg font-black text-[#2B2926] tracking-tight leading-none mb-1.5">{plan.name}</h4>
                        <p className="text-[9px] font-black text-slate-500 uppercase tracking-widest">{plan.desc}</p>
                      </div>

                      <div className="mb-6">
                        {plan.isCustom ? (
                          <div className="h-10 flex items-center">
                            <span className="text-xl font-black text-[#2B2926]">Let's talk</span>
                          </div>
                        ) : (
                          <div className="flex items-baseline gap-1">
                            <span className="text-3xl font-black text-[#2B2926] tracking-tighter">${displayPrice}</span>
                            <span className="text-xs font-bold text-slate-400">/mo</span>
                          </div>
                        )}
                        {isYearly && !plan.isCustom && (
                          <p className="text-[11px] font-bold text-emerald-500 mt-1">Billed ${plan.annualTotal}/yr</p>
                        )}
                      </div>

                      <ul className="space-y-3 mb-10 flex-1">
                        {plan.features.map((f, i) => (
                          <li key={i} className="flex items-start gap-3 group">
                            <div className={`w-4 h-4 rounded-full ${plan.highlight ? 'bg-orange-50 text-[#F55600]' : 'bg-slate-100 text-slate-400'} flex items-center justify-center shrink-0 mt-0.5`}>
                              <CheckCircle2 className="w-2.5 h-2.5" />
                            </div>
                            <span className="text-[11px] font-bold text-[#2B2926] leading-tight group-hover:text-[#2B2926] transition-colors">{f}</span>
                          </li>
                        ))}
                      </ul>

                      <button
                        onClick={() => handleFinish(
                          plan.id === 'enterprise'
                            ? 'enterprise'
                            : `${plan.id}_${billingCycle}`
                        )}
                        disabled={loading}
                        className={`w-full h-12 rounded-xl text-[11px] font-black uppercase tracking-widest transition-all flex items-center justify-center gap-2 ${plan.highlight ? 'bg-[#F55600] text-white shadow-xl shadow-orange-100 hover:bg-[#e65a2b]' : 'bg-slate-900 text-white hover:bg-slate-800'}`}
                      >
                        {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : (plan.isCustom ? 'Schedule a Call' : 'Select Plan')}
                        {!loading && <ArrowRight className="w-4 h-4" />}
                      </button>
                    </div>
                  );
                })}
              </div>

              <div className="mt-4 flex justify-start px-2">
                <button
                  onClick={handleBack}
                  className="px-6 py-2 bg-slate-100 text-slate-600 font-black uppercase tracking-widest text-[9px] rounded-full border border-slate-200 hover:bg-[#F55600] hover:text-white hover:border-[#F55600] transition-all flex items-center gap-2 shadow-sm"
                >
                  <ArrowLeft className="w-3.5 h-3.5" /> Back
                </button>
              </div>

            </motion.div>
          )}
        </AnimatePresence>

        <div className="mt-3 md:mt-8 flex justify-center pb-2">
          <button
            onClick={handleLogout}
            className="px-6 py-2.5 bg-white text-[#2B2926] hover:text-[#F55600] text-[10px] font-black uppercase tracking-[0.2em] transition-all flex items-center gap-2 rounded-xl border-2 border-[#F55600]/30 hover:border-[#F55600] hover:shadow-lg hover:shadow-orange-50"
          >
            <ChevronLeft className="w-3.5 h-3.5" /> Back to Sign In
          </button>
        </div>
      </div>
    </div >
  );
};

export default Onboarding;
