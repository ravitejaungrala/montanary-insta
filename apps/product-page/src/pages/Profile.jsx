import React, { useState, useEffect, useRef } from 'react';
import { useNotification } from '../context/NotificationContext';
import { isReadOnly, canManageBilling, canChangeProductSelection } from '../lib/permissions';
import { motion, AnimatePresence } from 'framer-motion';
import {
  User, Building2, Shield, MapPin, Globe, Camera,
  Save, Loader2, Key, Loader, CheckCircle2, CreditCard,
  ChevronRight, Info, AlertCircle, Fingerprint, RefreshCcw, X,
  Palette, Type, Sparkles, MessageSquare, Quote, Mail, Phone, ExternalLink, Plus, Zap, Rocket,
  FileText, Trash, Infinity as InfinityIcon
} from 'lucide-react';

import { timezones, formatInTimezone } from '../utils/timezones';
import { ENTERPRISE_BOOKING_URL } from '../utils/config';

const Profile = ({ authAxios, setMessage, setUser, user, initialTab = 'account' }) => {
  // Members cannot land on admin-only tabs. Force them to 'account'.
  // Billing is stricter than DNA — only the master_admin (plan owner)
  // manages billing; team admins share the master's plan so they get
  // redirected away from the billing tab too.
  if (isReadOnly(user) && (initialTab === 'company' || initialTab === 'billing')) {
    initialTab = 'account';
  }
  if (!canManageBilling(user) && initialTab === 'billing') {
    initialTab = 'account';
  }
  const [profile, setProfile] = useState(user || null);
  const [loading, setLoading] = useState(!user);
  const [updating, setUpdating] = useState(false);
  const [activeTab, setActiveTab] = useState(initialTab);
  const [billingCycle, setBillingCycle] = useState('monthly');
  const [activeDnaEntity, setActiveDnaEntity] = useState('company');
  // `current` was a relic from an earlier design — the change-password flow
  // uses OTP verification, not old-password. Keeping just the two fields we
  // actually render + submit.
  const [passwordData, setPasswordData] = useState({ new: '', confirm: '' });
  // 30-second cooldown for the Change-Password OTP Resend button — same
  // pattern as the signup / forgot-password OTP flows.
  const [changePwResendCooldown, setChangePwResendCooldown] = useState(0);
  const [productUrls, setProductUrls] = useState(['']);
  const fileInputRef = useRef(null);
  const logoInputRef = useRef(null);
  const { toast, confirm } = useNotification();

  // Product-selection tile state — mirrors user.product_selection, saves
  // via PUT /profile/product-selection. Team members see the tile locked
  // (canChangeProductSelection returns false — they inherit their master).
  const [productSelection, setProductSelection] = useState(user?.product_selection || 'both');
  const [savingProductSel, setSavingProductSel] = useState(false);
  useEffect(() => {
    // Keep the local state in sync when the user prop refreshes (e.g. after
    // a login refresh reloads the profile). Prevents stale-state flicker.
    setProductSelection(user?.product_selection || 'both');
  }, [user?.product_selection]);

  // Business categories — fetched once from /config/business-categories.
  // Drives the DNA "Business Category" dropdown that routes Auto Style.
  const [businessCategories, setBusinessCategories] = useState([]);
  useEffect(() => {
    if (!authAxios) return;
    let cancelled = false;
    authAxios.get('/config/business-categories').then((res) => {
      if (!cancelled) setBusinessCategories(res?.data?.categories || []);
    }).catch(() => {
      // Non-fatal — dropdown just won't render if fetch fails; DNA save still works.
    });
    return () => { cancelled = true; };
  }, [authAxios]);

  const handleSaveProductSelection = async (choice) => {
    if (choice === productSelection) return; // no-op click on already-selected
    if (!canChangeProductSelection(user)) return; // team members can't change
    setSavingProductSel(true);
    try {
      const { data } = await authAxios.put('/profile/product-selection', {
        product_selection: choice,
      });
      setProductSelection(choice);
      setUser?.(data);
      setMessage?.('Product selection updated ✅');
    } catch (e) {
      setMessage?.('Failed to update product selection ❌');
    } finally {
      setSavingProductSel(false);
    }
  };

  const handleImageUpload = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    // Same guard as the Business DNA logo upload — `accept="image/*"` on
    // the input is only a browser hint; users can still pick a PDF via
    // "All files". Without this check a PDF's base64 gets sent to
    // /profile/image and stored as a broken profile picture.
    if (!file.type.startsWith('image/')) {
      toast.error('Please choose an image file (PNG, JPG, SVG, etc.) — that file is not an image.');
      e.target.value = '';
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      toast.error('Profile image too large. Max size is 5 MB.');
      e.target.value = '';
      return;
    }

    setUpdating(true);
    try {
      const reader = new FileReader();
      reader.onloadend = async () => {
        const base64String = reader.result;
        const res = await authAxios.put('/profile/image', { profile_picture_url: base64String });
        setProfile(res.data);
        if (setUser) setUser(res.data);
        toast.success('Profile image updated! ✅');
      };
      reader.readAsDataURL(file);
    } catch (err) {
      toast.error('Failed to upload image ❌');
    } finally {
      setUpdating(false);
    }
  };

  useEffect(() => {
    fetchProfile();
  }, []);

  const fetchProfile = async () => {
    try {
      const res = await authAxios.get('/profile');
      setProfile(res.data);
      // Initialize product URLs from existing DNA or single field
      const existingProds = Object.values(res.data.business_dna?.products || {}).map(p => p.url).filter(Boolean);
      if (existingProds.length > 0) {
        setProductUrls(existingProds);
      } else {
        setProductUrls(['']);
      }
    } catch (err) {
      toast.error('Failed to load profile ❌');
    } finally {
      setLoading(false);
    }
  };

  // Helper to update deeply nested DNA state
  const handleRemoveProduct = async (id) => {
    const prodName = profile.business_dna?.products?.[id]?.product_name || id;
    const ok = await confirm({
      title: "Remove Brand DNA?",
      message: `Are you sure you want to remove ${prodName}? This DNA will be permanently deleted across all systems.`,
      confirmText: "Delete Now"
    });
    if (!ok) return;

    try {
      setUpdating(true);
      const newDna = { ...profile.business_dna };
      if (newDna.products) {
        delete newDna.products[id];
      }

      const prodUrl = profile.business_dna?.products?.[id]?.url;
      const newUrls = productUrls.filter(u => u !== prodUrl);
      if (newUrls.length === 0) newUrls.push('');
      setProductUrls(newUrls);

      const res = await authAxios.put('/profile', { business_dna: newDna, product_url: newUrls[0] || '' });
      setProfile(res.data);
      if (activeDnaEntity === id) setActiveDnaEntity('company');
      toast.success('Product removed successfully 🗑️');
    } catch (err) {
      // M1 fix — was showing a generic "Failed to remove product ❌" that
      // hid the real reason (team-member still assigned). Backend returns
      // 409 with a structured detail:
      //   { message: "...", conflicts: { <product_id>: ["email1", ...] } }
      // Show which team members block the removal so the admin knows who
      // to reassign first.
      const detail = err?.response?.data?.detail;
      if (err?.response?.status === 409 && detail?.conflicts) {
        const emails = Object.values(detail.conflicts).flat();
        const emailList = emails.slice(0, 3).join(', ') + (emails.length > 3 ? ` +${emails.length - 3} more` : '');
        toast.error(`${detail.message || 'Cannot remove — brand is assigned to team members.'} Reassign: ${emailList}`);
      } else {
        toast.error(detail?.message || detail || 'Failed to remove product ❌');
      }
    } finally {
      setUpdating(false);
    }
  };

  const updateDNA = (key, value) => {
    setProfile(prev => {
      if (activeDnaEntity === 'company') {
        return {
          ...prev,
          business_dna: { ...prev.business_dna, [key]: value }
        };
      } else {
        const prodId = activeDnaEntity;
        return {
          ...prev,
          business_dna: {
            ...prev.business_dna,
            products: {
              ...(prev.business_dna?.products || {}),
              [prodId]: { ...(prev.business_dna?.products?.[prodId] || {}), [key]: value }
            }
          }
        };
      }
    });
  };

  // M2 fix — was always writing to the top-level company `contact_details`,
  // ignoring the currently-active product tab. Now branches on
  // `activeDnaEntity` the same way `updateDNA` and `updateColor` do so
  // edits made while viewing a product's DNA persist against THAT product.
  const updateContact = (key, value) => {
    setProfile(prev => {
      if (activeDnaEntity === 'company') {
        return {
          ...prev,
          business_dna: {
            ...prev.business_dna,
            contact_details: { ...(prev.business_dna?.contact_details || {}), [key]: value }
          }
        };
      }
      const prodId = activeDnaEntity;
      return {
        ...prev,
        business_dna: {
          ...prev.business_dna,
          products: {
            ...(prev.business_dna?.products || {}),
            [prodId]: {
              ...(prev.business_dna?.products?.[prodId] || {}),
              contact_details: {
                ...(prev.business_dna?.products?.[prodId]?.contact_details || {}),
                [key]: value,
              },
            },
          },
        },
      };
    });
  };

  const updateColor = (key, value) => {
    setProfile(prev => {
      if (activeDnaEntity === 'company') {
        return {
          ...prev,
          business_dna: {
            ...prev.business_dna,
            colors: { ...(prev.business_dna?.colors || {}), [key]: value }
          }
        };
      } else {
        const prodId = activeDnaEntity;
        const prodDna = prev.business_dna?.products?.[prodId] || {};
        return {
          ...prev,
          business_dna: {
            ...prev.business_dna,
            products: {
              ...(prev.business_dna?.products || {}),
              [prodId]: {
                ...prodDna,
                colors: { ...(prodDna.colors || {}), [key]: value }
              }
            }
          }
        };
      }
    });
  };

  const removeTag = (key, idx) => {
    const activeDna = activeDnaEntity === 'company'
      ? profile.business_dna
      : profile.business_dna.products[activeDnaEntity];

    const newTags = [...(activeDna[key] || [])];
    newTags.splice(idx, 1);
    updateDNA(key, newTags);
  };

  const addTag = (key, val) => {
    if (!val) return;
    const activeDna = activeDnaEntity === 'company'
      ? profile.business_dna
      : profile.business_dna.products[activeDnaEntity];

    updateDNA(key, [...(activeDna[key] || []), val]);
  };

  const handleUploadDocument = async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    setUpdating(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('entity_id', activeDnaEntity);

      // Simple status toast
      toast.info(`Uploading ${file.name}... 📄`);
      const res = await authAxios.post('/profile/dna-document', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });

      // Update local state with the new document
      const newDoc = res.data.doc;
      setProfile(prev => {
        const newDna = { ...prev.business_dna };
        if (activeDnaEntity === 'company') {
          newDna.documents = [...(newDna.documents || []), newDoc];
        } else {
          newDna.products[activeDnaEntity].documents = [...(newDna.products[activeDnaEntity].documents || []), newDoc];
        }
        return { ...prev, business_dna: newDna };
      });

      toast.success('Document added to DNA! ✅');
    } catch (err) {
      toast.error('Failed to upload document ❌');
    } finally {
      setUpdating(false);
      e.target.value = null; // reset input
    }
  };

  const handleDeleteDocument = async (docId) => {
    const ok = await confirm({
      title: "Delete Document?",
      message: "Are you sure you want to remove this document from your local DNA? This cannot be undone.",
      confirmText: "Delete Permanently"
    });
    if (!ok) return;

    setUpdating(true);
    try {
      await authAxios.delete(`/profile/dna-document/${activeDnaEntity}/${docId}`);

      setProfile(prev => {
        const newDna = { ...prev.business_dna };
        if (activeDnaEntity === 'company') {
          newDna.documents = newDna.documents.filter(d => d.id !== docId);
        } else {
          newDna.products[activeDnaEntity].documents = newDna.products[activeDnaEntity].documents.filter(d => d.id !== docId);
        }
        return { ...prev, business_dna: newDna };
      });

      toast.success('Document removed 💡');
    } catch (err) {
      toast.error('Failed to delete ❌');
    } finally {
      setUpdating(false);
    }
  };

  const handleUpdateProfile = async (e) => {
    if (e) e.preventDefault();
    setUpdating(true);

    try {
      // Normalise the user's current Product URL inputs into a clean array —
      // trimmed, non-empty, de-duplicated. This is the authoritative "target"
      // list; anything in business_dna.products whose url is NOT in here will
      // be removed from the DNA on save.
      const targetUrls = Array.from(
        new Set(productUrls.map(u => (u || '').trim()).filter(Boolean))
      );

      // 1. Initial profile save — persist basic fields first so name/tz/etc
      //    stick even if a downstream DNA extraction fails.
      const initialProfile = {
        ...profile,
        product_url: targetUrls[0] || '',
      };
      const res = await authAxios.put('/profile', initialProfile);
      const updatedUser = res.data;
      setProfile(updatedUser);
      if (setUser) setUser(updatedUser);
      setMessage('Settings saved ✅');

      // 2. Compute the DNA diff against target product URLs.
      let dnaChanged = false;
      let finalDNA = { ...(updatedUser.business_dna || {}) };
      const existingProducts = { ...(finalDNA.products || {}) };

      // 2a. Remove entries whose URL is no longer in the target list.
      //     Matches your spec: "if user remove url and click save, it also
      //     removes url dna from business_dna and database".
      const removedIds = [];
      for (const [prodId, prodDna] of Object.entries(existingProducts)) {
        const prodUrl = (prodDna?.url || '').trim();
        if (prodUrl && !targetUrls.includes(prodUrl)) {
          delete existingProducts[prodId];
          removedIds.push(prodId);
          dnaChanged = true;
        }
      }
      if (removedIds.length > 0) {
        setMessage(`Removed product DNA: ${removedIds.join(', ')} 🗑️`);
        // Reset active DNA tab if it pointed at a now-deleted entity.
        if (removedIds.includes(activeDnaEntity)) {
          setActiveDnaEntity('company');
        }
      }

      // 2b. Auto-extract company DNA when business_url or company name changed
      //     (or when DNA is empty/errored). Re-extraction keeps the current
      //     products dict — we swap it back in after.
      const businessUrlChanged = profile.business_url && profile.business_url !== user?.business_url;
      const companyNameChanged = profile.company_name && profile.company_name !== user?.company_name;
      const dnaNeedsFix =
        !profile.business_dna ||
        profile.business_dna.error ||
        profile.business_dna.company_name === profile.business_url;

      // H2 fix — collect URLs whose extraction failed so we can surface
      // them in a single follow-up toast at the end instead of silently
      // swallowing errors and telling the user "Settings saved ✅" while
      // the DNA didn't actually update. Distinguish company from products.
      let companyDnaFailed = false;
      const failedProductUrls = [];

      if (businessUrlChanged || companyNameChanged || dnaNeedsFix) {
        try {
          setMessage('Extracting company DNA... 🧬');
          // H3 fix — 60s client-side timeout so the Save handler can't
          // hang forever on a slow-to-scrape website. Backend typically
          // finishes in 10-30s; anything past 60s should fail fast.
          const dnaRes = await authAxios.post(
            '/profile/extract-dna',
            {
              url: profile.business_url,
              is_product: false,
            },
            { timeout: 60000 }
          );
          // Merge: keep the (possibly-trimmed) products dict; overwrite
          // company-level fields with the freshly extracted ones.
          finalDNA = { ...finalDNA, ...dnaRes.data, url: profile.business_url };
          dnaChanged = true;
        } catch (err) {
          console.error('Company DNA extraction failed', err);
          companyDnaFailed = true;
        }
      }

      // 2c. Extract DNA for NEW product URLs only (skip URLs that already
      //     have a matching entry). Matches your spec: "if URL company and
      //     product URLs added, it automatically updated business DNA".
      for (const url of targetUrls) {
        const alreadyExtracted = Object.values(existingProducts).some(
          p => (p?.url || '').trim() === url
        );
        if (alreadyExtracted) continue;
        try {
          setMessage(`Extracting product DNA: ${url}... 🚀`);
          // H3 fix — same 60s cap for per-product extractions.
          const dnaRes = await authAxios.post(
            '/profile/extract-dna',
            {
              url,
              is_product: true,
            },
            { timeout: 60000 }
          );
          const prodId =
            dnaRes.data.product_name || `product_${Date.now()}`;
          existingProducts[prodId] = { ...dnaRes.data, url };
          dnaChanged = true;
        } catch (err) {
          console.error(`Product DNA extraction failed for ${url}`, err);
          // H2 fix — remember failed URLs to report at the end.
          failedProductUrls.push(url);
        }
      }

      // H2 fix — surface any extraction failures as a toast so the user
      // knows their Save partially succeeded (basic profile saved but DNA
      // couldn't be pulled). Timeout errors get a distinct message so the
      // user knows to retry vs check the URL.
      if (companyDnaFailed || failedProductUrls.length > 0) {
        const bits = [];
        if (companyDnaFailed) bits.push('company website');
        if (failedProductUrls.length > 0) {
          const list = failedProductUrls.slice(0, 2).join(', ')
            + (failedProductUrls.length > 2 ? ` +${failedProductUrls.length - 2} more` : '');
          bits.push(list);
        }
        toast.error(
          `Saved, but couldn't extract DNA for: ${bits.join(' and ')}. Check the URL(s) and retry, or edit manually below.`
        );
      }

      // Attach the (diff-applied) products dict back onto the DNA.
      finalDNA = { ...finalDNA, products: existingProducts };

      // 3. Persist the final DNA if anything changed (add OR remove).
      if (dnaChanged) {
        const finalProfile = { ...updatedUser, business_dna: finalDNA };
        const finalRes = await authAxios.put('/profile', finalProfile);
        setProfile(finalRes.data);
        if (setUser) setUser(finalRes.data);
        setMessage('Profile & DNA synchronized ⚡');
      }
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Update failed ❌');
    } finally {
      setUpdating(false);
    }
  };

  const [usage, setUsage] = useState(null);

  // Fetch usage on first mount (was previously only fetched when the Billing
  // tab opened — which meant the Account tab's Product URLs section, which
  // gates on `usage.limits.brands > 1`, was hidden until the user visited
  // Billing first). Now every tab has access to usage/plan limits.
  useEffect(() => {
    fetchUsage();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fetchUsage = async () => {
    try {
      const res = await authAxios.get('/usage');
      setUsage(res.data);
    } catch (err) {
      console.error("Failed to fetch usage", err);
    }
  };

  const handleManageSubscription = async () => {
    setUpdating(true);
    try {
      const res = await authAxios.post('/payment/create-portal-session');
      window.location.href = res.data.url;
    } catch (err) {
      setMessage("Failed to open billing portal ❌");
    } finally {
      setUpdating(false);
    }
  };

  // Click handler for the three pricing cards on the Billing tab.
  //   • Enterprise tile → open a "Contact Sales" mailto (enterprise pricing
  //     is negotiated, not self-serve).
  //   • Currently on a paid plan → route through the Stripe Customer Portal
  //     so Stripe handles upgrade/downgrade proration safely.
  //   • Free / no subscription → start a fresh checkout session with the
  //     correct {tier}_{cycle} price ID.
  const handlePlanAction = async (plan) => {
    const tier = (plan?.name || '').toLowerCase();
    if (!tier) return;

    if (tier === 'enterprise') {
      window.open(ENTERPRISE_BOOKING_URL, "_blank");
      return;
    }

    const currentTier = (profile?.pricing_plan || 'free').toLowerCase();
    // "Has a real Stripe subscription?" — a user can have pricing_plan set
    // (e.g. enterprise via manual admin flag) without ever going through
    // Stripe checkout, in which case there's no Stripe customer or
    // subscription to manage. Routing those users to the portal opens
    // an empty page with nothing to do. Use subscription_end_date as the
    // signal because it's only populated when a real Stripe subscription
    // wrote it via checkout.session.completed.
    const hasRealStripeSubscription = !!(profile?.subscription_end_date);

    setUpdating(true);
    try {
      if (hasRealStripeSubscription) {
        // Existing Stripe subscribers switch plans via the Stripe Customer
        // Portal — that flow handles proration, cancels the old line,
        // and keeps billing state consistent.
        setMessage('Opening billing portal to change plan... 🔁');
        const res = await authAxios.post('/payment/create-portal-session');
        window.location.href = res.data.url;
      } else {
        // Fresh checkout for users without an active plan.
        const planKey = `${tier}_${billingCycle}`; // e.g. "pro_monthly"
        setMessage(`Starting ${plan.name} ${billingCycle} checkout... 💳`);
        const res = await authAxios.post('/payment/create-checkout-session', { plan: planKey });
        if (res.data?.url) {
          window.location.href = res.data.url;
        } else {
          setMessage('Failed to start checkout ❌');
        }
      }
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Plan change failed ❌');
    } finally {
      setUpdating(false);
    }
  };

  const [otpCode, setOtpCode] = useState('');
  const [otpSent, setOtpSent] = useState(false);

  const handleAddProductFromUrl = async (url) => {
    if (!url) return;
    setUpdating(true);
    try {
      setMessage("Analyzing Product DNA... 🧬");
      const res = await authAxios.post('/profile/extract-dna', { url, is_product: true });
      const prodId = res.data.product_name || `product_${Date.now()}`;
      const newProfile = {
        ...profile,
        business_dna: {
          ...(profile.business_dna || {}),
          products: {
            ...(profile.business_dna?.products || {}),
            [prodId]: res.data
          }
        }
      };
      await authAxios.put('/profile', newProfile);
      setProfile(newProfile);

      // Update productUrls state to keep it in sync with the new DNA
      const newUrls = [...productUrls];
      if (!newUrls.includes(url)) {
        // Find first empty or add new
        const emptyIdx = newUrls.indexOf('');
        if (emptyIdx !== -1) newUrls[emptyIdx] = url;
        else newUrls.push(url);
        setProductUrls(newUrls);
      }

      setActiveDnaEntity(prodId);
      setActiveTab('company');
      setMessage(`New product "${res.data.product_name || 'DNA'}" added! ⚡`);
    } catch (err) {
      setMessage("Failed to extract product DNA ❌");
    } finally {
      setUpdating(false);
    }
  };

  // Tick the Change-Password resend cooldown down to 0 once per second.
  useEffect(() => {
    if (changePwResendCooldown <= 0) return;
    const t = setTimeout(() => setChangePwResendCooldown(changePwResendCooldown - 1), 1000);
    return () => clearTimeout(t);
  }, [changePwResendCooldown]);

  // Derived: is the new-password form shaped correctly to request an OTP?
  // Disables the "Request Verification" button until the user has typed a
  // password of at least 6 chars AND confirmed it matches, so we never ship
  // an OTP email for a form that's going to fail validation later anyway.
  const newPw = passwordData.new || '';
  const confirmPw = passwordData.confirm || '';
  const changePwFormReady =
    newPw.length >= 6 && newPw === confirmPw;

  const handleRequestOTP = async (e) => {
    if (e && e.preventDefault) e.preventDefault();
    // Short-circuit before hitting the API — avoids wasting an OTP email
    // on a form that is definitely going to fail submit validation. Backend
    // also enforces length ≥ 6 via the change-password endpoint itself.
    if (newPw.length < 6) {
      setMessage('New password must be at least 6 characters 🚩');
      return;
    }
    if (newPw !== confirmPw) {
      setMessage('Passwords do not match 🚩');
      return;
    }
    if (changePwResendCooldown > 0) return;

    setUpdating(true);
    try {
      await authAxios.post('/auth/request-otp', {
        email: user?.email,
        purpose: 'change_password',
      });
      setOtpSent(true);
      // Clear any stale code from a previous round (CP-6 in the audit): if
      // the user typed 123456, clicked Resend, and a NEW code arrives, don't
      // leave the old one in the box.
      setOtpCode('');
      setChangePwResendCooldown(30);
      setMessage('Verification code sent to your email 📩');
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Failed to send code ❌');
    } finally {
      setUpdating(false);
    }
  };

  const handleChangePassword = async (e) => {
    e.preventDefault();
    if (newPw.length < 6) {
      setMessage('New password must be at least 6 characters ❌');
      return;
    }
    if (newPw !== confirmPw) {
      setMessage('Passwords do not match ❌');
      return;
    }
    if (!otpSent) {
      setMessage('Please request a verification code first 🚩');
      return;
    }
    if (!otpCode) {
      setMessage('Please enter the verification code 🚩');
      return;
    }

    setUpdating(true);
    try {
      await authAxios.post('/auth/change-password', {
        new_password: newPw,
        otp_code: otpCode,
      });
      setPasswordData({ new: '', confirm: '' });
      setOtpCode('');
      setOtpSent(false);
      setChangePwResendCooldown(0);
      setMessage('Password changed successfully ✅');
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Failed to change password ❌');
    } finally {
      setUpdating(false);
    }
  };

  if (loading || !profile) {
    return (
      <div className="flex flex-col items-center justify-center h-[60vh] gap-4">
        <Loader2 className="w-12 h-12 animate-spin text-[#F55600]" />
        <Loader2 className="w-12 h-12 animate-spin text-[#2B2926]" />
        <p className="text-[#2B2926] font-medium">Loading settings...</p>
      </div>
    );
  }

  const TabButton = ({ id, label, icon: Icon }) => (
    <button
      onClick={() => setActiveTab(id)}
      className={`w-full flex items-center justify-between px-3 py-3 rounded-xl transition-all duration-300 ${activeTab === id
        ? 'bg-[#2B2926] text-white shadow-sm'
        : 'text-[#2B2926] hover:bg-slate-100 hover:text-[#2B2926]'
        }`}
    >
      <div className="flex items-center gap-3">
        <div className={`p-1.5 rounded-lg transition-all duration-300 ${activeTab === id ? 'bg-white/10 text-white' : 'bg-slate-100 text-[#2B2926] group-hover:text-[#2B2926]'}`}>
          <Icon className="w-4 h-4" />
        </div>
        <span className={`text-[11px] tracking-tight font-semibold uppercase transition-colors ${activeTab === id ? 'text-white' : 'text-[#2B2926] group-hover:text-[#2B2926]'}`}>{label}</span>
      </div>
      <ChevronRight className={`w-3.5 h-3.5 transition-transform ${activeTab === id ? 'translate-x-0 opacity-100' : '-translate-x-2 opacity-0'}`} />
    </button>
  );

  return (
    <div className="max-w-[1800px] pb-20 px-6 mx-auto">
      {/*
        The big top profile strip (avatar + name + email + timezone pill) was
        removed intentionally in favour of an inline avatar + meta block inside
        the Account Details card (see the "Personal Information" header below).
        This keeps the page single-column at the top and puts identity editing
        in one place.
      */}

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-start">
        {/* Navigation Sidebar */}
        <div className="lg:col-span-3 space-y-3">
          <div className="bg-white p-4 rounded-[32px] border border-[#2B2926]/30 shadow-sm space-y-2 lg:sticky lg:top-2">
            <p className="px-4 py-2 text-[10px] font-semibold text-[#2B2926] uppercase tracking-[0.2em]">Menu</p>
            <TabButton id="account" label="Account Details" icon={User} />
            {/* Members don't manage brand DNA — only Account + Password. */}
            {!isReadOnly(user) && (
              <TabButton id="company" label="Business DNA" icon={Fingerprint} />
            )}
            <TabButton id="security" label="Change Password" icon={Key} />
            {/* Billing is master-admin only — team admins share the master's
                plan, so they don't need a billing tab even though they can
                otherwise write. */}
            {canManageBilling(user) && (
              <TabButton id="billing" label="Billing & Plan" icon={CreditCard} />
            )}
          </div>
        </div>

        {/* Content Area */}
        <div className="lg:col-span-9">
          <AnimatePresence mode="wait">
            {activeTab === 'account' && (
              <motion.div
                key="account"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="space-y-4"
              >
                {/* Product Selection tile — master-admins only. Team members
                    inherit their master's selection and see nothing here. */}
                {canChangeProductSelection(user) && (
                  <div className="bg-white p-6 rounded-[24px] border border-[#2B2926]/30 shadow-sm">
                    <div className="flex items-center justify-between mb-4">
                      <div>
                        <h4 className="text-lg font-semibold text-[#2B2926] tracking-tight leading-none mb-1">Product Selection</h4>
                        <p className="text-xs text-slate-500">Choose which surface(s) you want to use. Switch anytime — no re-onboarding required.</p>
                      </div>
                      {savingProductSel && <Loader2 className="w-4 h-4 animate-spin text-[#F55600]" />}
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                      {[
                        // Display labels match the onboarding step 4 wording so
                        // users see the same product names everywhere. Backend
                        // enum values ('pipelyt' | 'gtm' | 'both') unchanged.
                        { key: 'pipelyt', title: 'Content Generation',        tagline: 'AI Content Creation' },
                        { key: 'gtm',     title: 'GTM',                      tagline: 'Lead Gen & Outreach' },
                        { key: 'both',    title: 'Content Generation + GTM', tagline: 'Full Suite' },
                      ].map((opt) => {
                        const isSelected = productSelection === opt.key;
                        return (
                          <button
                            key={opt.key}
                            type="button"
                            onClick={() => handleSaveProductSelection(opt.key)}
                            disabled={savingProductSel}
                            className={`text-left p-4 rounded-2xl border-2 transition-all disabled:opacity-60 ${
                              isSelected
                                ? 'border-[#F55600] bg-[#F55600]/5'
                                : 'border-slate-200 bg-white hover:border-slate-400'
                            }`}
                          >
                            <div className="flex items-center justify-between mb-1">
                              <h5 className="text-sm font-black text-[#2B2926] tracking-tight">{opt.title}</h5>
                              {isSelected && <CheckCircle2 className="w-4 h-4 text-[#F55600]" />}
                            </div>
                            <p className="text-[10px] font-bold uppercase tracking-wider text-slate-500">{opt.tagline}</p>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}
                <div className="bg-white p-6 rounded-[24px] border border-[#2B2926]/30 shadow-sm relative overflow-hidden">
                  {/* Account Details header — avatar moved in from the (now
                      removed) top profile strip, with inline email + tz chip
                      + Verified badge so the identity summary lives right
                      next to the fields that edit it. */}
                  <div className="flex items-center gap-4 mb-5 pb-4 border-b border-[#2B2926]/10">
                    <div
                      className="relative group/avatar cursor-pointer shrink-0"
                      onClick={() => fileInputRef.current.click()}
                      title="Upload profile picture"
                    >
                      <div className="relative w-14 h-14 rounded-xl bg-slate-50 flex items-center justify-center overflow-hidden border-2 border-white shadow-md transition-all duration-300 group-hover/avatar:scale-105">
                        {profile.profile_picture_url ? (
                          <img src={profile.profile_picture_url} alt="Profile" className="w-full h-full object-cover" />
                        ) : (
                          <User className="w-7 h-7 text-[#F55600]" />
                        )}
                      </div>
                      <div className="absolute -bottom-1 -right-1 p-1 bg-[#F55600] text-white rounded-lg shadow-lg border border-white z-10">
                        <Camera className="w-3 h-3" />
                      </div>
                      <input
                        type="file"
                        ref={fileInputRef}
                        onChange={handleImageUpload}
                        className="hidden"
                        accept="image/*"
                      />
                    </div>

                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <h4 className="text-lg font-semibold text-[#2B2926] tracking-tight leading-none">Personal Information</h4>
                        {profile.onboarded && (
                          <div className="flex items-center gap-1 px-2 py-0.5 bg-[#10B981] text-white text-[8px] font-semibold rounded-full uppercase tracking-widest shadow-sm">
                            <CheckCircle2 className="w-2.5 h-2.5" /> Verified
                          </div>
                        )}
                      </div>
                      <p className="text-[12px] text-[#2B2926] font-bold uppercase tracking-[0.14em] mt-1.5">
                        Manage your core account details
                      </p>
                      <div className="flex items-center gap-3 mt-2 flex-wrap">
                        <span className="text-[11px] text-[#2B2926] font-bold truncate">{profile.email}</span>
                      </div>
                    </div>
                  </div>

                  <form onSubmit={handleUpdateProfile} className="space-y-4 relative z-10">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div className="space-y-1.5">
                        <label className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest pl-1">Full Name</label>
                        <input
                          type="text"
                          value={profile.full_name || ''}
                          onChange={e => setProfile({ ...profile, full_name: e.target.value })}
                          placeholder="Your official name"
                          className="w-full bg-white border border-[#2B2926]/30 focus:border-[#F55600] focus:ring-4 focus:ring-orange-500/5 focus:bg-white text-[#2B2926] h-9 px-3 rounded-xl transition-all outline-none font-semibold text-sm shadow-sm"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest pl-1">Username</label>
                        <input
                          type="text"
                          value={profile.username || ''}
                          onChange={e => setProfile({ ...profile, username: e.target.value })}
                          placeholder="@username"
                          className="w-full bg-white border border-[#2B2926]/30 focus:border-[#F55600] focus:ring-4 focus:ring-orange-500/5 focus:bg-white text-[#2B2926] h-9 px-3 rounded-xl transition-all outline-none font-semibold text-sm shadow-sm"
                        />
                      </div>
                    </div>

                    <div className="space-y-1.5">
                      <label className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest pl-1">Primary Timezone</label>
                      <div className="relative">
                        <MapPin className="absolute left-3.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[#2B2926]" />
                        <select
                          value={profile.timezone || 'UTC'}
                          onChange={e => setProfile({ ...profile, timezone: e.target.value })}
                          className="w-full bg-white border border-[#2B2926]/30 focus:border-[#F55600] focus:ring-4 focus:ring-orange-500/5 focus:bg-white text-[#2B2926] h-9 pl-10 pr-3 rounded-xl transition-all outline-none appearance-none font-bold text-sm cursor-pointer shadow-sm"
                        >
                          {timezones.map(tz => (
                            <option key={tz.value} value={tz.value}>{tz.label}</option>
                          ))}
                        </select>
                      </div>
                    </div>

                    {/* Company Name / Business URL — members see only a
                        read-only Company Name (inherited from admin) and no
                        Business URL field. Admins keep the full editable row. */}
                    {isReadOnly(user) ? (
                      <div className="space-y-3">
                        <div className="space-y-1.5">
                          <label className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest pl-1">Company Name</label>
                          <input
                            type="text"
                            value={profile.company_name || ''}
                            readOnly disabled
                            className="w-full bg-slate-100 border border-[#2B2926]/30 text-[#2B2926] h-9 px-3 rounded-xl font-semibold text-sm shadow-sm cursor-not-allowed"
                          />
                          <p className="text-[9px] text-[#2B2926] font-medium italic pl-1">Inherited from your admin's organisation.</p>
                        </div>
                        {(() => {
                          // Show the member's admin-assigned brands. Prefer the
                          // authoritative list; fall back to the single legacy field.
                          const assignedIds = Array.isArray(profile.assigned_dna_product_ids)
                            ? profile.assigned_dna_product_ids.filter(Boolean)
                            : (profile.assigned_dna_product_id ? [profile.assigned_dna_product_id] : []);
                          if (assignedIds.length === 0) return null;
                          return (
                            <div className="space-y-1.5">
                              <label className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest pl-1">
                                Assigned Brand{assignedIds.length === 1 ? '' : 's'}
                              </label>
                              <div className="flex flex-wrap gap-1.5">
                                {assignedIds.map((id) => (
                                  <span
                                    key={id}
                                    className="inline-flex items-center gap-1.5 bg-orange-50 border border-orange-200 text-[#F55600] rounded-full px-2.5 py-1 font-semibold text-[11px]"
                                  >
                                    <Fingerprint className="w-3 h-3" />
                                    {id}
                                  </span>
                                ))}
                              </div>
                              <p className="text-[9px] text-[#2B2926] font-medium italic pl-1">
                                You can post under {assignedIds.length === 1 ? 'this brand only' : 'any of these brands'}. Your admin controls this list.
                              </p>
                            </div>
                          );
                        })()}
                      </div>
                    ) : (
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-1.5">
                          <label className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest pl-1">Company Name</label>
                          <input
                            type="text"
                            value={profile.company_name || ''}
                            onChange={e => setProfile({ ...profile, company_name: e.target.value })}
                            placeholder="e.g. Pipelyt"
                            className="w-full bg-white border border-[#2B2926]/30 focus:border-[#F55600] focus:ring-4 focus:ring-orange-500/5 focus:bg-white text-[#2B2926] h-9 px-3 rounded-xl transition-all outline-none font-semibold text-sm shadow-sm"
                          />
                        </div>
                        <div className="space-y-1.5">
                          <label className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest pl-1">Business URL</label>
                          <input
                            type="text"
                            value={profile.business_url || ''}
                            onChange={e => setProfile({ ...profile, business_url: e.target.value })}
                            placeholder="pipelyt.ai"
                            className="w-full bg-white border border-[#2B2926]/30 focus:border-[#F55600] focus:ring-4 focus:ring-orange-500/5 focus:bg-white text-[#2B2926] h-9 px-3 rounded-xl transition-all outline-none font-semibold text-sm shadow-sm"
                          />
                        </div>
                      </div>
                    )}

                    {!isReadOnly(user) && (usage?.limits?.brands || 1) > 1 && (
                      <div className="space-y-3 pt-2">
                      <label className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-widest pl-1">Product URLs (Automatically Extracts DNA)</label>
                      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">
                        {productUrls.map((url, idx) => (
                          <div key={idx} className="relative group/prod-input">
                            <input
                              type="text"
                              value={url}
                              onChange={e => {
                                const newUrls = [...productUrls];
                                newUrls[idx] = e.target.value;
                                setProductUrls(newUrls);
                              }}
                              placeholder="e.g. spenzo.io/pro"
                              className="w-full bg-white border border-[#2B2926]/30 focus:border-[#F55600] focus:ring-4 focus:ring-orange-500/5 focus:bg-white text-[#2B2926] h-9 pl-3 pr-10 rounded-xl transition-all outline-none font-semibold text-[11px] shadow-sm"
                            />
                            <div className="absolute right-1 top-1/2 -translate-y-1/2 flex items-center gap-0.5">
                              {productUrls.length > 1 && (
                                <button
                                  type="button"
                                  onClick={() => {
                                    const newUrls = [...productUrls];
                                    const removedUrl = newUrls[idx];
                                    newUrls.splice(idx, 1);
                                    setProductUrls(newUrls);

                                    // Also remove from stored DNA object so it doesn't reappear on reload
                                    if (profile.business_dna?.products) {
                                      const updatedProducts = { ...profile.business_dna.products };
                                      const prodIdToDelete = Object.keys(updatedProducts).find(
                                        id => updatedProducts[id].url === removedUrl
                                      );
                                      if (prodIdToDelete) {
                                        delete updatedProducts[prodIdToDelete];
                                        setProfile({
                                          ...profile,
                                          business_dna: { ...profile.business_dna, products: updatedProducts }
                                        });
                                      }
                                    }
                                  }}
                                  className="w-6 h-6 bg-slate-100 text-[#2B2926] rounded-lg flex items-center justify-center hover:bg-red-50 hover:text-red-500 transition-all"
                                >
                                  <X className="w-2.5 h-2.5" />
                                </button>
                              )}
                            </div>
                          </div>
                        ))}
                        {/* Standalone "+" tile — lives as its own grid cell so it
                            visually sits OUTSIDE the input boxes, not crammed
                            inside the last input. Matches the input height + radius
                            with a dashed orange border so it reads as a real
                            "add another" action. */}
                        {productUrls.length < ((usage?.limits?.brands || 1) - 1) && (
                          <button
                            type="button"
                            onClick={() => setProductUrls([...productUrls, ''])}
                            className="h-9 border-2 border-dashed border-[#F55600]/40 text-[#F55600] rounded-xl flex items-center justify-center gap-2 font-bold text-[11px] uppercase tracking-[0.12em] hover:bg-[#F55600]/[0.04] hover:border-[#F55600] transition-all shadow-sm"
                          >
                            Add Product URL
                          </button>
                        )}
                      </div>
                      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mt-2">
                        <p className="text-[10.5px] text-[#2B2926] font-semibold uppercase tracking-widest opacity-80 pl-1">
                          Click <span className="text-[#F55600]">+</span> to add more products. All DNA will be extracted when you save.
                        </p>
                        {usage?.limits?.brands && (
                          <span className="self-start sm:self-auto shrink-0 whitespace-nowrap inline-flex items-center gap-1 text-[9px] font-semibold uppercase tracking-widest text-white bg-[#2B2926] px-2.5 py-1 rounded-md border border-[#2B2926]">
                            {usage.limits.brands > 9000 ? (
                              <>
                                {productUrls.length + 1} DNAs Used /
                                <InfinityIcon className="w-4 h-4 text-white" strokeWidth={3} />
                                <span>DNA</span>
                              </>
                            ) : (
                              `${productUrls.length + 1} / ${usage.limits.brands} DNAs Used`
                            )}
                          </span>
                        )}
                      </div>
                      </div>
                    )}

                    <div className="pt-4 mt-2">
                      <button
                        type="submit"
                        disabled={updating}
                        className={`w-full bg-[#F55600] text-white font-semibold text-[13px] uppercase tracking-[0.18em] h-11 rounded-2xl shadow-[0_6px_20px_rgba(245,86,0,0.40)] drop-shadow-[0_1px_2px_rgba(0,0,0,0.15)] flex items-center justify-center gap-2.5 transition-all duration-300 ${updating ? 'opacity-90 cursor-not-allowed' : 'hover:shadow-[0_8px_24px_rgba(245,86,0,0.50)] hover:scale-[1.01] active:scale-[0.98]'}`}
                      >
                        {updating ? <Loader2 className="w-4 h-4 animate-spin text-white" /> : <Save className="w-4 h-4" />}
                        {updating ? 'Saving Changes...' : 'Save Account Changes'}
                      </button>
                    </div>
                  </form>
                </div>
              </motion.div>
            )}

            {activeTab === 'company' && (
              <motion.div
                key="company"
                initial={{ opacity: 0, x: 10 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -10 }}
                className="space-y-4"
              >
                <div className="bg-white p-3 rounded-2xl border border-[#2B2926]/30 shadow-sm relative overflow-hidden">
                  <div className="absolute top-0 right-0 w-24 h-24 bg-orange-50/50 rounded-bl-full -mr-12 -mt-12" />

                  <div className="flex items-center gap-2 mb-1.5">
                    <div className="p-1.5 bg-orange-50 rounded-lg border border-orange-100/50">
                      <Fingerprint className="w-3.5 h-3.5 text-[#F55600]" />
                    </div>
                    <div>
                      <h4 className="text-[15px] font-semibold text-[#2B2926] leading-none">Business DNA Library</h4>
                      <p className="text-[12px] text-[#2B2926] font-bold uppercase tracking-[0.14em] mt-1.5">Manage brand assets & DNA</p>
                    </div>
                  </div>

                  {/* Entity Selector Chips */}
                  <div className="flex items-center gap-6 overflow-x-auto pb-2 pt-1 scrollbar-hide no-scrollbar -mx-1 px-1">
                    <button
                      onClick={() => setActiveDnaEntity('company')}
                      className={`flex items-center gap-2 px-5 py-2 rounded-full whitespace-nowrap transition-all duration-500 border-2 ${activeDnaEntity === 'company'
                        ? 'bg-[#F55600] text-white border-[#F55600] shadow-[0_5px_15px_-5px_rgba(255,107,53,0.3)] scale-105'
                        : 'bg-white text-[#2B2926] border-[#2B2926] hover:bg-[#F55600]/5 hover:border-[#F55600] shadow-sm'
                        }`}
                    >
                      <Building2 className={`w-3.5 h-3.5 ${activeDnaEntity === 'company' ? 'text-white' : 'text-[#F55600]'}`} />
                      <span className="text-[11px] font-bold uppercase tracking-[0.14em]">Company Profile</span>
                    </button>

                    {Object.entries(profile.business_dna?.products || {}).map(([id, prod]) => (
                      <div key={id} className="relative group/prod mr-1">
                        <button
                          onClick={() => setActiveDnaEntity(id)}
                          className={`flex items-center gap-2.5 px-6 py-2.5 rounded-full whitespace-nowrap transition-all duration-500 border-2 ${activeDnaEntity === id
                            ? 'bg-[#2B2926] text-white border-[#2B2926] shadow-xl scale-105'
                            : 'bg-white text-[#2B2926] border-[#2B2926] hover:bg-[#F55600]/5 hover:border-[#F55600] shadow-sm'
                            }`}
                        >
                          <Sparkles className={`w-4 h-4 ${activeDnaEntity === id ? 'text-[#F55600]' : 'text-[#F55600]'}`} />
                          <span className="text-[10px] font-semibold uppercase tracking-widest">{prod.product_name || id}</span>
                        </button>

                        <button
                          onClick={(e) => { e.stopPropagation(); handleRemoveProduct(id); }}
                          className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-red-500 text-white rounded-full flex items-center justify-center opacity-0 group-hover/prod:opacity-100 transition-all hover:bg-red-600 shadow-md transform hover:scale-110 z-10 border-2 border-white"
                        >
                          <X className="w-3 h-3" />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>

                {/* DNA Grid Map */}
                {(activeDnaEntity === 'company' ? profile.business_dna : profile.business_dna?.products?.[activeDnaEntity]) && (
                  <motion.div
                    key={activeDnaEntity}
                    initial={{ opacity: 0, scale: 0.98 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 1.02 }}
                    className="grid grid-cols-1 md:grid-cols-12 gap-4"
                  >
                    {/* Identity card */}
                    <div className="md:col-span-6 bg-white p-4 rounded-xl border border-[#2B2926]/30 shadow-sm flex flex-col justify-center group/dna-card overflow-hidden relative">
                      <div className="absolute top-0 right-0 w-32 h-32 bg-orange-50/30 rounded-bl-full -mr-8 -mt-8 group-hover:scale-110 transition-transform duration-500" />
                      <div className="relative z-10 w-full">
                        <div className="flex items-center gap-2 text-[#2B2926] mb-3">
                          <Building2 className="w-4 h-4 text-[#F55600]" />
                          <span className="text-[10px] font-semibold uppercase tracking-widest text-[#2B2926]">
                            {activeDnaEntity === 'company' ? 'Company Name' : 'Product Name'}
                          </span>
                        </div>
                        <input
                          type="text"
                          value={(activeDnaEntity === 'company' ? profile.business_dna.company_name : profile.business_dna.products[activeDnaEntity].product_name) || ''}
                          onChange={e => updateDNA(activeDnaEntity === 'company' ? 'company_name' : 'product_name', e.target.value)}
                          className="w-full bg-transparent text-2xl font-semibold text-[#2B2926] tracking-tight outline-none focus:text-[#F55600] transition-colors p-0"
                        />
                        <div className="flex items-center gap-2 text-[#2B2926] text-xs font-bold mt-2">
                          <Globe className="w-4 h-4 text-[#F55600]" />
                          <input
                            type="text"
                            value={(activeDnaEntity === 'company' ? profile.business_url : profile.business_dna.products[activeDnaEntity].url) || ''}
                            onChange={e => {
                              if (activeDnaEntity === 'company') {
                                setProfile({ ...profile, business_url: e.target.value });
                              } else {
                                updateDNA('url', e.target.value);
                              }
                            }}
                            className="bg-transparent outline-none focus:text-[#2B2926] transition-colors w-full p-0"
                          />
                        </div>
                      </div>
                    </div>

                    {/* Logo Card */}
                    <div className="md:col-span-2 bg-white p-4 rounded-xl border border-[#2B2926]/30 shadow-sm flex flex-col items-center justify-center group relative overflow-hidden">
                      <input
                        type="file"
                        ref={logoInputRef}
                        className="hidden"
                        accept="image/*"
                        onChange={async (e) => {
                          const file = e.target.files[0];
                          if (!file) return;
                          // Per TC-DNA-006 — reject non-image files with a
                          // toast. The `accept="image/*"` above is only a
                          // browser hint; users can still pick a PDF via
                          // "All files" in the picker. Without this guard
                          // the PDF's base64 got stored as logo_url and
                          // rendered as a broken <img>.
                          if (!file.type.startsWith('image/')) {
                            toast.error('Please choose an image file (PNG, JPG, SVG, etc.) — that file is not an image.');
                            e.target.value = '';
                            return;
                          }
                          // Also cap at 5 MB — matches the guard we use on
                          // the onboarding logo upload. Prevents oversized
                          // base64 blobs from bloating business_dna JSON.
                          if (file.size > 5 * 1024 * 1024) {
                            toast.error('Logo too large. Max size is 5 MB.');
                            e.target.value = '';
                            return;
                          }
                          const reader = new FileReader();
                          reader.onloadend = () => updateDNA('logo_url', reader.result);
                          reader.readAsDataURL(file);
                        }}
                      />
                      <div className="absolute inset-0 bg-gradient-to-br from-orange-50/10 to-transparent pointer-events-none" />
                      <div className="flex items-center justify-between gap-2 text-[#2B2926] mb-6 w-full">
                        <div className="flex items-center gap-2">
                          <Camera className="w-4 h-4 text-[#F55600]" />
                          <span className="text-[10px] font-semibold uppercase tracking-widest text-[#2B2926]">Brand Mark</span>
                        </div>
                      </div>
                      <div
                        onClick={() => logoInputRef.current?.click()}
                        className="relative w-20 h-20 bg-slate-50 rounded-2xl p-3 flex items-center justify-center shadow-inner border border-[#2B2926]/30 group-hover:scale-105 transition-transform duration-500 cursor-pointer"
                      >
                        {(activeDnaEntity === 'company' ? profile.business_dna.logo_url : profile.business_dna.products[activeDnaEntity].logo_url) ? (
                          <>
                            <img src={activeDnaEntity === 'company' ? profile.business_dna.logo_url : profile.business_dna.products[activeDnaEntity].logo_url} alt="Logo" className="max-w-full max-h-full object-contain filter drop-shadow-sm" />
                            <div className="absolute -right-2 -top-2 w-7 h-7 bg-white rounded-full shadow-lg border border-[#2B2926]/30 flex items-center justify-center text-[#F55600] group-hover:scale-110 transition-transform">
                              <Camera className="w-3.5 h-3.5" />
                            </div>
                          </>
                        ) : (
                          <div className="flex flex-col items-center gap-2">
                            <Camera className="w-8 h-8 text-slate-200" />
                            <span className="text-[8px] font-semibold text-[#2B2926] uppercase">Upload</span>
                          </div>
                        )}
                      </div>
                    </div>

                    {/* Colors */}
                    <div className="md:col-span-4 bg-white p-4 rounded-xl border border-[#2B2926]/30 shadow-sm relative overflow-hidden">
                      <div className="absolute top-0 left-0 w-16 h-16 bg-slate-50 rounded-br-full -ml-10 -mt-10" />
                      <div className="flex items-center gap-2 text-[#2B2926] mb-4 relative z-10">
                        <Palette className="w-3.5 h-3.5 text-[#F55600]" />
                        <span className="text-[12px] font-bold uppercase tracking-[0.14em] text-[#2B2926]">Brand Palette</span>
                      </div>
                      <div className="flex items-center justify-between px-2">
                        {Object.entries((activeDnaEntity === 'company' ? profile.business_dna.colors : profile.business_dna.products[activeDnaEntity].colors) || {}).map(([key, color]) => (
                          <div key={key} className="flex flex-col items-center gap-2">
                            <div className="relative group">
                              <div className="w-10 h-10 rounded-full border-4 border-white shadow-lg transition-all group-hover:scale-110 cursor-pointer" style={{ background: color }} />
                              <input
                                type="color"
                                value={color.startsWith('#') ? color : '#000000'}
                                onChange={e => updateColor(key, e.target.value)}
                                className="absolute inset-0 opacity-0 cursor-pointer"
                              />
                            </div>
                            <input
                              type="text"
                              value={color}
                              onChange={e => updateColor(key, e.target.value)}
                              className="text-[9px] font-semibold text-[#2B2926] uppercase tracking-tighter w-16 text-center bg-transparent outline-none focus:text-[#F55600]"
                            />
                          </div>
                        ))}
                      </div>
                    </div>

                    {/* Tagline */}
                    <div className="md:col-span-8 bg-[#F55600] p-4 rounded-xl shadow-lg shadow-orange-100/30 flex flex-col justify-center relative overflow-hidden">
                      <Quote className="absolute -right-6 -bottom-6 w-24 h-24 text-white/10 rotate-12" />
                      <div className="flex items-center gap-2 text-white mb-2 relative z-10">
                        <Quote className="w-4 h-4" />
                        <span className="text-[12px] font-black uppercase tracking-[0.16em] text-white drop-shadow-[0_1px_2px_rgba(0,0,0,0.25)]">Tagline</span>
                      </div>
                      <textarea
                        value={(activeDnaEntity === 'company' ? profile.business_dna.tagline : profile.business_dna.products[activeDnaEntity].tagline) || ''}
                        onChange={e => updateDNA('tagline', e.target.value)}
                        rows={1}
                        className="w-full bg-transparent text-xl font-black text-white italic leading-tight outline-none resize-none overflow-hidden [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none] placeholder:text-white/55 border-none p-0 focus:ring-0 drop-shadow-[0_1px_3px_rgba(0,0,0,0.18)]"
                        placeholder="Unlocking Digital Potential..."
                      />
                    </div>

                    {/* Typography */}
                    <div className="md:col-span-4 bg-white p-4 rounded-xl border-2 border-[#2B2926]/15 shadow-sm relative group overflow-hidden">
                      <div className="flex items-center gap-2 mb-3">
                        <Type className="w-3.5 h-3.5 text-[#F55600]" />
                        <span className="text-[12px] font-bold uppercase tracking-[0.14em] text-[#2B2926]">Typography</span>
                      </div>
                      <div className="flex flex-col items-center justify-center py-1">
                        <span className="text-5xl font-semibold text-[#2B2926] mb-1">Aa</span>
                        <input
                          type="text"
                          value={((activeDnaEntity === 'company' ? profile.business_dna.fonts : profile.business_dna.products[activeDnaEntity].fonts) || []).join(', ')}
                          onChange={e => updateDNA('fonts', e.target.value.split(',').map(s => s.trim()))}
                          className="text-[#2B2926] font-bold text-[10px] bg-transparent outline-none text-center w-full border-none p-0 focus:ring-0"
                        />
                      </div>
                    </div>

                    {/* Business Category — drives which image-generation
                        playbook + Auto Style group is used downstream.
                        Options come from GET /config/business-categories
                        (11 canonical: 10 industries + personal). Extracted
                        automatically at DNA setup; user can override any
                        time. Empty falls back to the SaaS/service prompt. */}
                    <div className="md:col-span-12 bg-white p-4 rounded-xl border-2 border-[#2B2926]/15 shadow-sm">
                      <div className="flex items-center gap-2 mb-3">
                        <Sparkles className="w-3.5 h-3.5 text-[#F55600]" />
                        <span className="text-[12px] font-bold uppercase tracking-[0.14em] text-[#2B2926]">Business Category</span>
                        <span className="text-[9px] font-semibold text-[#2B2926]/40 uppercase tracking-widest">— drives image style</span>
                      </div>
                      {(() => {
                        const currentCat = ((activeDnaEntity === 'company' ? profile.business_dna.category : profile.business_dna.products[activeDnaEntity].category) || '').trim().toLowerCase();
                        // Legacy values we still recognise but no longer expose in the dropdown.
                        const LEGACY = { saas_product: 'software_product', hardware_service: 'human_services' };
                        const isCustom = currentCat.startsWith('custom:');
                        const knownKeys = new Set(businessCategories.map(c => c.key));
                        // Show legacy value verbatim so users can see what's saved before overriding.
                        const showLegacy = currentCat && !knownKeys.has(currentCat) && !isCustom;
                        return (
                          <select
                            value={isCustom ? '__custom__' : (currentCat || '')}
                            onChange={e => {
                              const v = e.target.value;
                              if (v === '__custom__') return;
                              updateDNA('category', v);
                            }}
                            className="w-full bg-white border-2 border-[#2B2926]/20 focus:border-[#F55600] focus:ring-4 focus:ring-orange-500/5 text-[#2B2926] h-10 px-3 rounded-xl transition-all outline-none font-semibold text-sm shadow-sm"
                          >
                            <option value="">— Select category —</option>
                            {businessCategories.map(c => (
                              <option key={c.key} value={c.key}>{c.label}</option>
                            ))}
                            {showLegacy && (
                              <option value={currentCat}>
                                {currentCat} (legacy — will migrate to {LEGACY[currentCat] || 'personal'})
                              </option>
                            )}
                            {isCustom && (
                              <option value="__custom__" disabled>
                                Custom: {currentCat.replace(/^custom:/, '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
                              </option>
                            )}
                          </select>
                        );
                      })()}
                      <p className="text-[10px] text-[#2B2926]/60 font-medium mt-2 leading-relaxed">
                        Pipelyt uses this to pick the right image-generation style for your posts.
                        Physical products get product-first photography instead of dashboards.
                      </p>
                    </div>

                    {/* Tags loop — section header, tag chips and the Add
                        input all use solid black text now (were faded
                        slate-500/600/700). Chips: orange-tinted with
                        solid black text, brand-orange × on every variant
                        — palette-compliant (no blue/slate). */}
                    {[
                      { key: 'brand_values', label: 'Brand DNA Values', icon: Sparkles },
                      { key: 'brand_aesthetic', label: 'Brand Aesthetic', icon: Palette },
                      { key: 'brand_tone', label: 'Tone of Voice', icon: MessageSquare }
                    ].map(({ key, label, icon: Icon }) => {
                      // Tone of Voice chips use brand-green; Brand DNA Values
                      // and Brand Aesthetic use brand-orange. A light tinted
                      // fill + strong coloured border keeps the colour identity
                      // while bold BLACK text stays crisply readable — solid
                      // orange/green fills made the text hard to read.
                      // Per-category chip styling:
                      // - Tone of Voice → soft mint fill, deep-green text, mint border
                      // - Brand Aesthetic → solid warm-dark fill, white text (per user spec)
                      // - Brand DNA Values → white fill, orange text, light-grey hairline border
                      const isTone = key === 'brand_tone';
                      const isAesthetic = key === 'brand_aesthetic';
                      let chipCls, textCls, xCls;
                      if (isTone) {
                        chipCls = 'bg-[#10B981]/12 border-[#10B981] hover:bg-[#10B981]/20';
                        textCls = 'text-[#065F46]';
                        xCls = 'text-[#10B981]';
                      } else if (isAesthetic) {
                        chipCls = 'bg-[#2B2926] border-[#2B2926] hover:bg-[#1A1816] hover:border-[#1A1816]';
                        textCls = 'text-white';
                        xCls = 'text-white/85';
                      } else { // brand_values
                        chipCls = 'bg-white border-[#2B2926]/25 hover:border-[#F55600]/55 hover:bg-[#F55600]/5';
                        textCls = 'text-[#F55600]';
                        xCls = 'text-[#F55600]';
                      }
                      return (
                      <div key={key} className="md:col-span-4 bg-white p-4 rounded-xl border-2 border-[#2B2926]/10 shadow-sm flex flex-col">
                        {/* Section header */}
                        <div className="flex items-center gap-2 mb-3 pb-2.5 border-b border-[#2B2926]/8">
                          <div className="w-6 h-6 rounded-lg bg-[#F55600]/10 flex items-center justify-center shrink-0">
                            <Icon className="w-3.5 h-3.5 text-[#F55600]" />
                          </div>
                          <span className="text-[12px] font-bold uppercase tracking-[0.14em] text-[#2B2926]">{label}</span>
                          <span className="ml-auto text-[9px] font-semibold text-[#2B2926]/30 tabular-nums">
                            {((activeDnaEntity === 'company' ? profile.business_dna[key] : profile.business_dna.products[activeDnaEntity][key]) || []).length}
                          </span>
                        </div>

                        {/* Chips — content-start + items-start prevents rows
                            from stretching to fill empty vertical space which
                            was causing each chip to become a tall box. */}
                        <div className="flex flex-wrap gap-1.5 mb-3 content-start items-start min-h-[60px]">
                          {((activeDnaEntity === 'company' ? profile.business_dna[key] : profile.business_dna.products[activeDnaEntity][key]) || []).map((val, idx) => (
                            <button
                              key={idx}
                              onClick={() => removeTag(key, idx)}
                              className={`inline-flex items-center gap-1 shrink-0 px-2.5 py-1 rounded-full text-[10px] font-bold border-2 active:scale-95 transition-all ${textCls} ${chipCls}`}
                            >
                              <span className="leading-none">{val}</span>
                              <span className={`${xCls} font-semibold text-[12px] leading-none -mt-px`}>×</span>
                            </button>
                          ))}
                          {((activeDnaEntity === 'company' ? profile.business_dna[key] : profile.business_dna.products[activeDnaEntity][key]) || []).length === 0 && (
                            <p className="text-[10px] text-[#2B2926]/25 font-medium italic">No items yet — type below and press Enter</p>
                          )}
                        </div>

                        {/* Add input */}
                        <input
                          type="text"
                          placeholder="Add and press Enter…"
                          onKeyDown={e => {
                            if (e.key === 'Enter' && e.target.value.trim()) {
                              addTag(key, e.target.value.trim());
                              e.target.value = '';
                            }
                          }}
                          className="mt-auto w-full bg-white border-2 border-[#2B2926]/25 h-9 px-3 rounded-lg text-[11px] font-bold text-[#2B2926] placeholder:text-[#2B2926]/55 outline-none focus:border-[#F55600] transition-colors"
                        />
                      </div>
                      );
                    })}

                    {/* Master Value Proposition (Overview) */}
                    <div className="md:col-span-12 bg-white p-7 rounded-[28px] border border-[#2B2926]/30 shadow-sm relative group mt-2 overflow-hidden">
                      <div className="flex items-center gap-2 text-[#2B2926] mb-4 relative z-10">
                        <Info className="w-4 h-4 text-[#F55600]" />
                        <span className="text-[12px] font-bold uppercase tracking-[0.14em] text-[#2B2926]">Master Value Proposition</span>
                      </div>
                      <textarea
                        value={(activeDnaEntity === 'company' ? profile.business_dna?.overview : profile.business_dna?.products?.[activeDnaEntity]?.overview) || ''}
                        onChange={e => updateDNA('overview', e.target.value)}
                        rows={12}
                        className="w-full bg-transparent text-[#2B2926] font-medium text-[15px] leading-relaxed relative z-10 outline-none resize-none border-none p-0 focus:ring-0"
                        placeholder="Define your core value proposition here..."
                      />
                    </div>

                    {/* Knowledge Base Documents Section */}
                    <div className="md:col-span-12 bg-white p-6 rounded-[24px] border border-[#2B2926]/30 shadow-sm relative group mt-3">
                      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-5">
                        <div className="flex items-center gap-2.5">
                          <div className="p-2 bg-orange-50 rounded-lg border border-orange-100">
                            <FileText className="w-4 h-4 text-[#F55600]" />
                          </div>
                          <div>
                            <h4 className="text-[13px] font-semibold text-[#2B2926] tracking-tight leading-none">Knowledge Base Documents</h4>
                            <p className="text-[9px] text-[#2B2926] font-semibold uppercase tracking-tight mt-1">Train AI with custom context</p>
                          </div>
                        </div>
                        <label className="self-start sm:self-auto shrink-0 cursor-pointer bg-[#2B2926] text-white px-4 py-2 rounded-xl text-[9px] font-semibold uppercase tracking-widest hover:bg-[#F55600] transition-all flex items-center gap-2 shadow-lg shadow-slate-900/10 active:scale-95">
                          Add Document (PDF/DOC)
                          <input type="file" className="hidden" accept=".pdf,.docx,.csv,.txt" onChange={handleUploadDocument} />
                        </label>
                      </div>

                      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3.5">
                        {((activeDnaEntity === 'company' ? profile.business_dna.documents : profile.business_dna.products[activeDnaEntity].documents) || []).map((doc) => (
                          <div
                            key={doc.id}
                            role="button"
                            tabIndex={0}
                            title={`Download ${doc.name}`}
                            onClick={() => { if (doc.url) window.open(doc.url, '_blank', 'noopener,noreferrer'); }}
                            onKeyDown={(e) => { if ((e.key === 'Enter' || e.key === ' ') && doc.url) { e.preventDefault(); window.open(doc.url, '_blank', 'noopener,noreferrer'); } }}
                            className="bg-slate-50/50 p-4 rounded-2xl border border-[#2B2926]/30 flex items-center justify-between group/doc hover:border-orange-200 hover:bg-white transition-all shadow-sm cursor-pointer"
                          >
                            <div className="flex items-center gap-3 overflow-hidden">
                              <div className="w-10 h-10 bg-white rounded-xl flex items-center justify-center text-[#F55600] shrink-0 shadow-sm border border-[#2B2926]/10">
                                <FileText className="w-5 h-5" />
                              </div>
                              <div className="overflow-hidden">
                                <p className="text-[11px] font-semibold text-[#2B2926] truncate">{doc.name}</p>
                                <p className="text-[9px] font-bold text-[#2B2926]">{(doc.size / 1024).toFixed(1)} KB</p>
                              </div>
                            </div>
                            <button
                              onClick={(e) => { e.stopPropagation(); handleDeleteDocument(doc.id); }}
                              className="p-2 text-[#2B2926] hover:text-red-500 hover:bg-red-50 rounded-lg transition-all opacity-0 group-hover/doc:opacity-100"
                            >
                              <Trash className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        ))}
                        {(!((activeDnaEntity === 'company' ? profile.business_dna.documents : profile.business_dna.products[activeDnaEntity].documents) || [])?.length) && (
                          <div className="md:col-span-2 lg:col-span-3 py-3 flex flex-col items-center justify-center border-2 border-dashed border-[#2B2926]/25 rounded-2xl bg-white">
                            <div className="flex items-center gap-3">
                              <FileText className="w-5 h-5 text-[#F55600]" />
                              <p className="text-[11px] font-semibold uppercase tracking-widest text-[#2B2926]">No documents yet</p>
                            </div>
                          </div>
                        )}
                      </div>
                    </div>

                    {/* Dedicated Save Button at Bottom */}
                    <div className="md:col-span-12 pt-6">
                      <button
                        onClick={handleUpdateProfile}
                        disabled={updating}
                        className="w-full bg-[#F55600] text-white font-semibold text-[13px] uppercase tracking-[0.18em] h-11 rounded-2xl shadow-[0_6px_20px_rgba(245,86,0,0.40)] flex items-center justify-center gap-3 hover:shadow-[0_8px_24px_rgba(245,86,0,0.50)] hover:scale-[1.01] active:scale-[0.98] transition-all duration-300 group drop-shadow-[0_1px_2px_rgba(0,0,0,0.15)]"
                      >
                        {updating ? (
                          <Loader2 className="w-5 h-5 animate-spin" />
                        ) : (
                          <div className="flex items-center gap-3">
                            <Save className="w-5 h-5 group-hover:rotate-12 transition-transform" />
                            SAVE BUSINESS DNA
                          </div>
                        )}
                      </button>
                    </div>
                  </motion.div>
                )}
              </motion.div>
            )}

            {activeTab === 'security' && (
              <motion.div
                key="security"
                initial={{ opacity: 0, scale: 0.98 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 1.02 }}
              >
                <div className="bg-white p-8 md:p-10 rounded-[40px] border-2 border-orange-200 shadow-[0_20px_60px_rgba(245,86,0,0.05)] relative overflow-hidden">
                  <div className="absolute top-0 right-0 w-80 h-80 bg-orange-50/40 rounded-full blur-3xl -mr-40 -mt-40" />
                  <div className="flex items-center gap-5 mb-10 relative z-10">
                    <div className="p-4 bg-orange-50 rounded-[24px] shadow-sm border border-orange-100">
                      <Shield className="w-6 h-6 text-[#F55600]" />
                    </div>
                    <div>
                      <h4 className="text-[26px] font-bold text-[#2B2926] tracking-tight leading-tight">Security Protocol</h4>
                      <p className="text-[12px] text-[#2B2926] font-bold uppercase tracking-[0.14em] mt-1.5">Update your master authentication credentials</p>
                    </div>
                  </div>

                  <form onSubmit={handleChangePassword} className="relative z-10 space-y-8">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                      <div className="space-y-2.5">
                        <label className="text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.14em] pl-1 block">New Password</label>
                        <div className="relative group/key">
                          <Key className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-[#2B2926] group-focus-within/key:text-[#F55600] transition-colors" />
                          <input
                            type="password"
                            placeholder="••••••••"
                            value={passwordData.new}
                            onChange={e => setPasswordData({ ...passwordData, new: e.target.value })}
                            className="w-full bg-white border border-[#2B2926]/30 focus:border-[#F55600] focus:ring-4 focus:ring-orange-500/5 focus:bg-white text-[#2B2926] h-12 pl-12 pr-4 rounded-xl transition-all outline-none font-semibold text-sm"
                          />
                        </div>
                      </div>
                      <div className="space-y-2.5">
                        <label className="text-[11px] font-bold text-[#2B2926] uppercase tracking-[0.14em] pl-1 block">Confirm New Password</label>
                        <div className="relative group/key">
                          <CheckCircle2 className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-[#2B2926] group-focus-within/key:text-[#F55600] transition-colors" />
                          <input
                            type="password"
                            placeholder="••••••••"
                            value={passwordData.confirm}
                            onChange={e => setPasswordData({ ...passwordData, confirm: e.target.value })}
                            className="w-full bg-white border border-[#2B2926]/30 focus:border-[#F55600] focus:ring-4 focus:ring-orange-500/5 focus:bg-white text-[#2B2926] h-12 pl-12 pr-4 rounded-xl transition-all outline-none font-semibold text-sm"
                          />
                        </div>
                      </div>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pt-6 border-t border-[#2B2926]/10">
                      <div className="flex flex-col justify-end">
                        {!otpSent ? (
                          <>
                            <button
                              type="button"
                              onClick={handleRequestOTP}
                              disabled={updating || !changePwFormReady}
                              title={changePwFormReady ? '' : 'Enter a new password (min 6 chars) and matching confirmation first'}
                              className="w-full bg-[#2B2926] text-white font-bold text-[12px] uppercase tracking-[0.14em] h-12 rounded-xl flex items-center justify-center gap-3 hover:bg-[#F55600] hover:shadow-[0_4px_12px_rgba(245,86,0,0.25)] transition-all active:scale-95 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-[#2B2926] disabled:hover:shadow-none"
                            >
                              {updating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Mail className="w-4 h-4" />}
                              Request Verification
                            </button>
                            {!changePwFormReady && (newPw || confirmPw) && (
                              <p className="text-[10px] text-[#2B2926] font-bold mt-2 pl-1">
                                {newPw.length < 6
                                  ? 'Password must be at least 6 characters.'
                                  : 'Passwords must match.'}
                              </p>
                            )}
                          </>
                        ) : (
                          <div className="space-y-2.5">
                            <label className="text-[11px] font-bold text-[#F55600] uppercase tracking-[0.14em] pl-1">Verification Code</label>
                            <div className="relative">
                              <Shield className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-[#F55600]" />
                              <input
                                type="text"
                                maxLength={6}
                                placeholder="Enter Code"
                                value={otpCode}
                                onChange={e => setOtpCode(e.target.value.replace(/\D/g, ''))}
                                className="w-full bg-[#F55600]/[0.06] border-2 border-[#F55600] focus:border-[#F55600] text-[#F55600] h-12 pl-12 pr-24 rounded-xl transition-all outline-none font-bold text-sm tracking-[0.3em] text-center"
                              />
                              <button
                                type="button"
                                onClick={handleRequestOTP}
                                disabled={changePwResendCooldown > 0 || updating}
                                className="absolute right-2 top-1/2 -translate-y-1/2 px-2 py-1.5 text-[11px] font-bold text-[#F55600] uppercase hover:underline disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:no-underline"
                              >
                                {changePwResendCooldown > 0 ? `Resend (${changePwResendCooldown}s)` : 'Resend'}
                              </button>
                            </div>
                          </div>
                        )}
                      </div>

                      <div className="flex flex-col justify-end">
                        <button
                          type="submit"
                          disabled={updating || (otpSent && !otpCode)}
                          className="w-full bg-[#F55600] text-white font-bold text-[12px] uppercase tracking-[0.14em] h-12 rounded-xl flex items-center justify-center gap-3 shadow-[0_4px_12px_rgba(245,86,0,0.25)] hover:shadow-[0_6px_18px_rgba(245,86,0,0.35)] hover:-translate-y-0.5 active:translate-y-0 active:scale-95 transition-all disabled:opacity-40 disabled:pointer-events-none"
                        >
                          {updating ? <Loader2 className="w-5 h-5 animate-spin" /> : <Save className="w-5 h-5" />}
                          Update Password
                        </button>
                      </div>
                    </div>
                  </form>
                </div>
              </motion.div>
            )}

            {activeTab === 'billing' && canManageBilling(user) && (
              <motion.div
                key="billing"
                initial={{ opacity: 0, scale: 0.98 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 1.02 }}
                className="space-y-4"
              >
                {/* Downgrade warning — only shown when on a team-enabled plan. */}
                <div className="bg-white p-5 sm:p-6 rounded-[24px] border border-[#2B2926]/30 shadow-sm flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                  <div className="flex items-center gap-4">
                    <div className="p-3 bg-[#2B2926]/[0.05] rounded-xl shrink-0">
                      <CreditCard className="w-5 h-5 text-[#2B2926]" />
                    </div>
                    <div className="min-w-0">
                      <h4 className="text-lg sm:text-xl font-semibold text-[#2B2926] tracking-tighter leading-none">Subscription & Billing</h4>
                      <p className="text-[11px] text-[#67655E] font-semibold uppercase tracking-[0.14em] mt-1.5">Plans & Usage</p>
                    </div>
                  </div>
                  <div className="flex flex-col items-stretch sm:items-end gap-1.5">
                    <div className="flex items-center gap-1 bg-[#2B2926]/5 p-1 rounded-xl border border-[#2B2926]/20 w-full sm:w-auto max-w-full overflow-hidden">
                      <button
                        onClick={() => setBillingCycle('monthly')}
                        className={`flex-1 sm:flex-none px-4 sm:px-5 py-2 text-[10px] font-semibold uppercase tracking-widest transition-all rounded-lg ${billingCycle === 'monthly'
                          ? 'bg-[#2B2926] text-white shadow-sm'
                          : 'text-[#2B2926] hover:text-[#F55600]'
                          }`}
                      >
                        Monthly
                      </button>
                      <button
                        onClick={() => setBillingCycle('yearly')}
                        className={`flex-1 sm:flex-none flex items-center justify-center px-4 sm:px-5 py-2 text-[10px] font-semibold uppercase tracking-widest transition-all rounded-lg ${billingCycle === 'yearly'
                          ? 'bg-[#2B2926] text-white shadow-sm'
                          : 'text-[#2B2926] hover:text-[#F55600]'
                          }`}
                      >
                        Yearly
                        <span className="ml-1.5 px-1.5 py-0.5 bg-[#10B981] text-white rounded-md text-[9px] font-semibold antialiased shadow-sm">-20%</span>
                      </button>
                    </div>
                    {/* The toggle above only changes the *displayed* price on
                        the pricing cards below. Switching the billing cycle
                        on an active subscription is handled by Stripe's
                        Customer Portal (see Manage Subscription). */}
                    {['starter', 'growth', 'agency', 'enterprise'].includes((profile.pricing_plan || '').toLowerCase()) && (
                      <p className="text-[9px] text-[#2B2926] font-semibold uppercase tracking-[0.14em] sm:pr-1">
                        Switch cycle via Stripe Portal below
                      </p>
                    )}
                  </div>
                </div>

                {/*
                  Active Plan + Monthly Quota cards — now read the user's real
                  plan / subscription_end_date / /usage response instead of
                  the earlier hard-coded "Enterprise / 5/14/2026 / 35 / Unlimited"
                  placeholders.
                */}
                {(() => {
                  const planName = profile.pricing_plan || 'free';
                  const planLabel = planName.charAt(0).toUpperCase() + planName.slice(1);
                  const endIso = profile.subscription_end_date;
                  const endLabel = endIso
                    ? new Date(endIso).toLocaleDateString(undefined, {
                        year: 'numeric', month: 'short', day: 'numeric',
                      })
                    : '—';
                  // When the user has clicked Cancel in the Stripe portal,
                  // Stripe keeps the subscription active until period end —
                  // surface that explicitly so they don't think the cancel
                  // didn't take effect when they still see the plan badge.
                  const cancelScheduled = !!profile.subscription_cancel_at_period_end;
                  const used = typeof usage?.usage?.posts === 'number' ? usage.usage.posts : 0;
                  const quota = usage?.limits?.posts;  // number, 0, or undefined (unlimited)
                  const isUnlimited = !quota || quota <= 0 || quota > 9000;
                  const pct = isUnlimited
                    ? 0
                    : Math.min(100, Math.round((used / quota) * 100));
                  const quotaLabel = isUnlimited
                    ? `${used} / Unlimited`
                    : `${used} / ${quota}`;
                  return (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div className="bg-white p-4 rounded-xl border border-[#2B2926]/30 shadow-sm relative overflow-hidden h-28 flex flex-col justify-center">
                        <div className="flex items-center justify-between mb-2">
                          <span className="text-[11px] font-semibold text-[#F55600] uppercase tracking-widest">Active Plan</span>
                          <div className={`px-2 py-0.5 text-white text-[8px] font-semibold rounded-md uppercase ${cancelScheduled ? 'bg-amber-500' : 'bg-green-500'}`}>
                            {cancelScheduled ? 'Cancelling' : 'Active'}
                          </div>
                        </div>
                        <h3 className="text-2xl font-semibold text-[#2B2926] tracking-tighter">{planLabel}</h3>
                        <p className={`text-[11px] font-semibold uppercase mt-1 tracking-[0.12em] ${cancelScheduled ? 'text-amber-600' : 'text-[#2B2926]'}`}>
                          {cancelScheduled ? `Cancels on: ${endLabel}` : `Next Billing: ${endLabel}`}
                        </p>
                      </div>

                      <div className="bg-[#2B2926] p-4 rounded-xl shadow-lg relative overflow-hidden h-28 flex flex-col justify-center">
                        <div className="flex items-center justify-between mb-1.5">
                          <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-white/85">Monthly Quota</span>
                          <span className="text-[10px] font-semibold text-white px-2 py-0.5 bg-white/10 rounded-md shadow-inner">{quotaLabel}</span>
                        </div>
                        <div className="w-full h-1.5 bg-white/10 rounded-full overflow-hidden mb-2">
                          <div
                            className="h-full bg-[#F55600] shadow-[0_0_12px_rgba(245,86,0,0.6)] transition-all duration-500"
                            style={{ width: isUnlimited ? '4%' : `${Math.max(pct, 2)}%` }}
                          />
                        </div>
                        <p className="text-[11px] text-white/85 font-semibold uppercase tracking-[0.12em]">
                          {isUnlimited
                            ? 'Unlimited posts available until next cycle'
                            : `${Math.max(0, (quota || 0) - used)} posts remaining this cycle`}
                        </p>
                      </div>
                    </div>
                  );
                })()}

                {/* Modern Light-Grey Pricing Grid.
                    `active` is now derived from profile.pricing_plan so the
                    "Current Plan" marker tracks the actual subscription
                    instead of being hard-coded to Enterprise. */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6 pt-6 px-2 pb-8">
                   {[
                    {
                      name: 'Starter',
                      price: billingCycle === 'monthly' ? '49' : '39',
                      desc: 'Ideal for solopreneurs',
                      icon: <Zap className="w-5 h-5 text-orange-500" />,
                      features: ['2 brand DNAs', '3 connected channels', '30 AI posts / mo', '60 AI images / mo', 'Manual + basic scheduling', 'Email support']
                    },
                    {
                      name: 'Growth',
                      price: billingCycle === 'monthly' ? '99' : '79',
                      desc: 'Ideal for power users & teams',
                      icon: <Sparkles className="w-5 h-5 text-orange-600" />,
                      features: ['5 brand DNAs', '5 connected channels', '60 AI posts / mo', '120 AI images / mo', 'Full scheduling + Canva import', 'Cross-platform analytics', 'Priority support'],
                      popular: true
                    },
                    {
                      name: 'Agency',
                      price: billingCycle === 'monthly' ? '249' : '199',
                      desc: 'Ideal for scaling agencies',
                      icon: <Building2 className="w-5 h-5 text-orange-700" />,
                      features: ['10 brand DNAs', 'Unlimited channels', '120 AI posts / mo', '240 AI images / mo', 'Approval workflows + team roles', 'Video generation (beta)', 'Dedicated CSM']
                    },
                    {
                      name: 'Enterprise',
                      price: '0',
                      desc: 'Ideal for organizations',
                      icon: <Rocket className="w-5 h-5 text-[#2B2926]" />,
                      features: ['Unlimited brand DNAs', 'SSO + SOC 2 + custom DPAs', 'Custom image + video volumes', 'Lead-gen automation (Phase 2)', 'Private model routing', 'Dedicated solutions engineer'],
                      isCustom: true
                    }
                  ].map((plan) => {
                    const currentPlan = (profile.pricing_plan || 'free').toLowerCase();
                    const planNameLower = plan.name.toLowerCase();
                    
                    // Logic to determine if this item is the current plan, an upgrade, or a downgrade
                    const planOrder = ['starter', 'growth', 'agency', 'enterprise'];
                    const currentIdx = planOrder.indexOf(currentPlan);
                    const targetIdx = planOrder.indexOf(planNameLower);
                    
                    const isCurrent = planNameLower === currentPlan;
                    const isUpgrade = targetIdx > currentIdx;
                    const isDowngrade = targetIdx < currentIdx;

                    const handlePlanClick = () => {
                      if (isDowngrade) {
                        window.alert('You are downgrading your plan. This change will apply after your current plan cycle ends.');
                      }
                      handlePlanAction(plan);
                    };

                    return (
                    <motion.div
                      key={plan.name}
                      whileHover={{ scale: 1.02, transition: { duration: 0.2 } }}
                      className={`relative flex flex-col h-full bg-white rounded-[24px] p-6 border transition-all duration-300 shadow-[0_10px_30px_rgba(43,41,38,0.06)] ${isCurrent ? 'border-2 border-green-500 ring-4 ring-green-500/5' : 'border-[#2B2926]/30 hover:border-[#2B2926]/50'}`}
                    >
                      {plan.popular && (
                        <div className="absolute top-3 right-3 bg-[#F55600] text-white text-[9px] font-semibold px-2.5 py-1 rounded-full uppercase tracking-[0.14em]">
                          Popular
                        </div>
                      )}
                      
                      <div className="mb-4">
                        <div className="flex items-center gap-2 mb-0.5">
                          <h4 className="text-xl font-semibold text-[#2B2926] tracking-tight">{plan.name}</h4>
                          {isCurrent && (
                            <span className="px-2 py-0.5 bg-[#10B981] text-white text-[10px] font-semibold rounded-md uppercase tracking-[0.14em]">Active</span>
                          )}
                        </div>
                        <p className="text-[13px] text-[#2B2926] font-medium leading-relaxed">{plan.desc}</p>
                      </div>

                      <div className="mb-5">
                          {plan.isCustom ? (
                            <span className="text-3xl font-semibold text-[#2B2926] tracking-tighter">Let's talk</span>
                          ) : (
                            <>
                              <span className="text-4xl font-semibold text-[#2B2926] tracking-tighter">${plan.price}</span>
                              <span className="text-sm text-[#2B2926] font-medium">/month</span>
                            </>
                          )}
                        {billingCycle === 'yearly' && (
                          <p className="text-[10px] text-[#2B2926] font-semibold uppercase tracking-[0.14em] mt-1">
                            Billed annually
                          </p>
                        )}
                      </div>

                      <div className="space-y-3 mb-6 flex-1">
                        <p className="text-[10px] font-semibold text-[#2B2926] uppercase tracking-[0.14em] mb-3">
                          {plan.name === 'Starter' ? 'Starter features:' : plan.name === 'Growth' ? 'Everything in Starter and:' : plan.name === 'Agency' ? 'Everything in Growth and:' : 'Everything in Agency and:'}
                        </p>
                        {plan.features.map((feature, i) => (
                          <div key={i} className="flex items-center gap-3 group/feature">
                            <CheckCircle2 className="w-4 h-4 text-[#F55600] shrink-0" strokeWidth={2.6} />
                            <span className="text-[13px] font-medium text-[#2B2926] leading-snug tracking-tight">{feature}</span>
                          </div>
                        ))}
                      </div>

                      <button
                        type="button"
                        onClick={isCurrent ? undefined : handlePlanClick}
                        disabled={isCurrent || updating}
                        aria-disabled={isCurrent}
                        className={`w-full h-11 rounded-xl font-semibold text-[12px] uppercase tracking-[0.14em] transition-colors duration-200 shadow-sm active:scale-[0.98] disabled:opacity-100 ${
                          isCurrent
                            // Non-clickable badge style — flat green pill, no hover, no active scale.
                            ? 'bg-[#0E9F6E] text-white font-bold shadow-[0_4px_12px_rgba(14,159,110,0.30)] cursor-not-allowed pointer-events-none'
                            : isDowngrade
                              ? 'bg-[#2B2926] text-white hover:bg-[#2B2926]/80 disabled:cursor-not-allowed'
                              : 'bg-[#F55600] text-white hover:bg-[#2B2926] disabled:cursor-not-allowed'
                        }`}
                      >
                        {isCurrent ? 'Current Plan' : (plan.name === 'Enterprise' ? 'Schedule a Call' : (isDowngrade ? `Switch to ${plan.name}` : `Upgrade to ${plan.name}`))}
                      </button>

                      {/* Cancel Plan — visible on every active PAID card so
                          all subscribers (Starter / Growth / Agency / Enterprise)
                          have parity. Routes through the Stripe Customer Portal
                          which handles the cancel-at-period-end flow. The
                          webhook flips subscription_cancel_at_period_end on the
                          user, and the Active Plan card above flips its label
                          from "Next Billing" to "Cancels on …".
                          Hidden only on Free (nothing to cancel). Enterprise
                          users without a Stripe subscription (manually flagged
                          accounts) still get the button — the portal endpoint
                          auto-creates a Stripe customer and shows an empty
                          portal in that edge case. */}
                      {isCurrent && plan.name !== 'Free' && (() => {
                        const hasStripeSub = !!(profile?.subscription_end_date);
                        if (hasStripeSub) {
                          return (
                            <button
                              type="button"
                              onClick={handleManageSubscription}
                              disabled={updating}
                              className="mt-2 w-full h-11 rounded-lg font-bold text-[11px] uppercase tracking-[0.14em] text-[#F55600] border-2 border-[#F55600] shadow-[0_2px_8px_rgba(245,86,0,0.18)] hover:bg-[#F55600]/[0.08] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                              {updating ? 'Opening portal…' : 'Cancel Plan'}
                            </button>
                          );
                        }
                        // Manually-flagged users (no Stripe subscription) get
                        // a Contact Support button — only an admin can revoke
                        // their plan. Mailto opens their email client with
                        // pre-filled body so support can action quickly.
                        const supportSubject = encodeURIComponent(`Cancel ${plan.name} plan — ${profile?.email || ''}`);
                        const supportBody = encodeURIComponent(
                          `Hi Pipelyt support,\n\nI'd like to cancel my ${plan.name} plan.\n\nAccount email: ${profile?.email || ''}\n`
                        );
                        return (
                          <a
                            href={`mailto:contact@neuzenai.com?subject=${supportSubject}&body=${supportBody}`}
                            className="mt-2 w-full h-11 rounded-lg font-bold text-[11px] uppercase tracking-[0.14em] text-[#F55600] border-2 border-[#F55600] shadow-[0_2px_8px_rgba(245,86,0,0.18)] hover:bg-[#F55600]/[0.08] transition-colors flex items-center justify-center"
                            title="No Stripe subscription on file — contact support to cancel"
                          >
                            Contact Support to Cancel
                          </a>
                        );
                      })()}
                    </motion.div>
                    );
                  })}
                </div>

                {/* Removed the Secure Stripe Billing footer + the
                    "Manage Subscription & Direct Billing" link. The portal
                    is now reachable from the Cancel Plan / Switch buttons
                    on each plan card directly, so the duplicate footer
                    just added visual noise. */}
              </motion.div>
            )}
          </AnimatePresence>
    </div>
  </div>
</div>
  );
};

/**
 * Downgrade-aware warning banner — shown in the Billing tab when the admin
 * has active team members. Fetches current seat usage on mount.
 *
 * Non-intrusive: it doesn't block the downgrade (that happens in Stripe's
 * portal and is outside our control). It informs the admin that moving
 * below Agency will lock every active member out until they re-upgrade.
 */

export default Profile;
