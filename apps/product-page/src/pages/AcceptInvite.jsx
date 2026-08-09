import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Lock, Eye, EyeOff, ArrowRight, AlertCircle, Building2, Tag, Mail } from 'lucide-react';
import { API_BASE_URL } from '../utils/config';

/**
 * Accept-Invite landing page.
 *
 * URL: /accept-invite?token=<opaque-token-from-email>
 *
 * Flow:
 *  1. On mount, GET /team/invites/{token} to fetch the invite metadata
 *     (inviter name, company, brand, email). If the invite is expired or
 *     used, we show an error state and prompt the user to contact their
 *     admin.
 *  2. User enters full name + password, submits.
 *  3. POST /team/invites/{token}/accept → returns a JWT access_token.
 *  4. We stash the token in localStorage and navigate to `/?tab=connections`
 *     so App.jsx re-hydrates the authenticated state and lands them in
 *     the Connections page (per product spec — members go connect their
 *     own socials first).
 */
const AcceptInvite = ({ setToken, setActiveTab }) => {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token');
  const navigate = useNavigate();

  const [loading, setLoading] = useState(true);
  const [invite, setInvite] = useState(null);
  const [error, setError] = useState(null);
  const [fullName, setFullName] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!token) {
      setError('No invite token provided.');
      setLoading(false);
      return;
    }
    (async () => {
      try {
        const res = await axios.get(`${API_BASE_URL}/team/invites/${token}`);
        setInvite(res.data);
      } catch (err) {
        setError(err?.response?.data?.detail || err?.message || 'Invalid invite link.');
      } finally {
        setLoading(false);
      }
    })();
  }, [token]);

  const submit = async (e) => {
    e.preventDefault();
    if (password.length < 6) {
      setError('Password must be at least 6 characters.');
      return;
    }
    if (password !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const res = await axios.post(`${API_BASE_URL}/team/invites/${token}/accept`, {
        password,
        full_name: fullName || null,
      });
      const access = res?.data?.access_token;
      if (!access) throw new Error('No access token returned.');

      localStorage.setItem('token', access);
      if (setToken) setToken(access);

      // Franchisee: has NOT chosen a plan or paid yet. Route them into
      // onboarding step 4 (plan picker → Stripe checkout) instead of the
      // default connections page.
      if (res?.data?.needs_plan_selection) {
        navigate('/onboarding?step=4&kind=franchise');
      } else {
        localStorage.setItem('activeTab', 'connections');
        if (setActiveTab) setActiveTab('connections');
        navigate('/');
      }
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || 'Failed to accept invite.');
    } finally {
      setSubmitting(false);
    }
  };

  const isFranchise = invite?.invite_type === 'franchise';

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-orange-50/40 via-white to-slate-50">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-[#F55600]" />
      </div>
    );
  }

  if (error && !invite) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-orange-50/40 via-white to-slate-50 p-6">
        <div className="max-w-md w-full bg-white rounded-3xl border-2 border-red-200 shadow-lg p-8 text-center">
          <div className="w-14 h-14 rounded-full bg-red-50 flex items-center justify-center mx-auto mb-4">
            <AlertCircle className="text-red-500 w-7 h-7" />
          </div>
          <h1 className="text-xl font-black text-[#2B2926] mb-2">Invite unavailable</h1>
          <p className="text-sm text-slate-600 mb-6">{error}</p>
          <button
            onClick={() => navigate('/')}
            className="text-xs font-black uppercase tracking-widest text-[#F55600] hover:underline"
          >
            Go to sign in
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-orange-50/40 via-white to-slate-50 p-6">
      <div className="max-w-md w-full bg-white rounded-3xl border-2 border-orange-200 shadow-[0_40px_80px_-20px_rgba(255,107,53,0.25)] overflow-hidden">
        <div className="bg-gradient-to-br from-[#F55600] to-[#FF5722] p-6 text-white">
          <div className="flex items-center gap-3 mb-4">
            <div className="w-10 h-10 rounded-xl bg-white/20 backdrop-blur flex items-center justify-center font-black">
              P
            </div>
            <span className="font-black text-lg">Pipelyt</span>
          </div>
          <h1 className="text-xl font-black leading-tight">
            {isFranchise ? "You've been invited to franchise" : "You're invited to join"}
          </h1>
          <p className="text-sm opacity-90 mt-1">
            {invite?.inviter_name ? `${invite.inviter_name} from ` : ''}
            <strong>{invite?.company_name || 'Pipelyt'}</strong>
          </p>
          {isFranchise && (
            <p className="text-xs opacity-90 mt-2 leading-relaxed">
              You'll operate under this brand but bring your own plan and
              manage your own subscription. Next step: pick a plan.
            </p>
          )}
        </div>

        <form onSubmit={submit} className="p-6 space-y-4">
          <div className="grid grid-cols-1 gap-3 -mt-2">
            <InfoRow icon={<Mail size={14} />} label="Invited email">{invite?.email}</InfoRow>
            {invite?.brand_name && (
              <InfoRow icon={<Tag size={14} />} label="Assigned brand">{invite.brand_name}</InfoRow>
            )}
          </div>

          <div className="border-t border-slate-100 pt-4 space-y-3">
            <Labeled label="Your name (optional)">
              <input
                type="text"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                className="w-full text-sm rounded-xl border-2 border-slate-200 px-3 py-2 focus:outline-none focus:border-[#F55600]/50"
                placeholder="Jane Doe"
              />
            </Labeled>
            <Labeled label="Set a password" icon={<Lock size={12} />}>
              <div className="relative">
                <input
                  type={showPw ? 'text' : 'password'}
                  required minLength={6}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full text-sm rounded-xl border-2 border-slate-200 px-3 py-2 pr-10 focus:outline-none focus:border-[#F55600]/50"
                  placeholder="At least 6 characters"
                />
                <button type="button"
                  onClick={() => setShowPw(!showPw)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 p-1"
                >
                  {showPw ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </Labeled>
            <Labeled label="Confirm password">
              <input
                type={showPw ? 'text' : 'password'}
                required minLength={6}
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="w-full text-sm rounded-xl border-2 border-slate-200 px-3 py-2 focus:outline-none focus:border-[#F55600]/50"
              />
            </Labeled>
          </div>

          {error && (
            <div className="text-xs text-red-600 bg-red-50 border border-red-100 rounded-xl px-3 py-2 flex items-center gap-2">
              <AlertCircle size={14} /> {error}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="w-full bg-[#F55600] text-white font-black uppercase tracking-widest text-xs py-3.5 rounded-xl hover:bg-[#F55600] shadow-md disabled:opacity-40 transition-all flex items-center justify-center gap-2"
          >
            {submitting
              ? 'Creating your account…'
              : isFranchise
                ? (<>Continue to Plan Selection <ArrowRight size={14} /></>)
                : (<>Accept & Continue <ArrowRight size={14} /></>)}
          </button>

          <p className="text-[10px] text-slate-400 text-center">
            By accepting you agree to Pipelyt's terms.
            {isFranchise
              ? ' You will pick your plan and manage your own subscription in the next step.'
              : " Your admin manages the plan and brand DNA."}
          </p>
        </form>
      </div>
    </div>
  );
};

const Labeled = ({ label, icon, children }) => (
  <label className="block">
    <div className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-1 flex items-center gap-1">
      {icon}{label}
    </div>
    {children}
  </label>
);

const InfoRow = ({ icon, label, children }) => (
  <div className="flex items-center gap-2 text-xs text-slate-600">
    <span className="p-1.5 rounded-lg bg-orange-50 text-[#F55600]">{icon}</span>
    <span className="font-bold text-slate-400 uppercase tracking-widest text-[9px]">{label}:</span>
    <span className="font-black text-[#2B2926] truncate">{children}</span>
  </div>
);

export default AcceptInvite;
