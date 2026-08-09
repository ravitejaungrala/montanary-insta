/**
 * NexusOnboarding — port of legacy Onboarding.jsx.
 *
 * 4-step wizard for first-time NEXUS users:
 *   1. Workspace setup (name)
 *   2. Product analysis (URL → AI extracts summary + ICP)
 *   3. First campaign (name + targeting)
 *   4. Connect mailbox (Gmail OAuth or Resend domain)
 *
 * State names match legacy. Backend calls are stubbed to existing
 * endpoints where they exist; gaps return safe defaults.
 */
import React, { useState } from 'react';
import {
  Briefcase,
  CheckCircle2,
  ChevronRight,
  Loader2,
  Mail,
  Sparkles,
  Target,
} from 'lucide-react';

const STEPS = [
  { id: 1, label: 'Workspace',   icon: Briefcase, key: 'workspace' },
  { id: 2, label: 'Product',     icon: Sparkles,  key: 'product' },
  { id: 3, label: 'Campaign',    icon: Target,    key: 'campaign' },
  { id: 4, label: 'Mailbox',     icon: Mail,      key: 'mailbox' },
];

const NexusOnboarding = ({ authAxios, apiBase, user, setMessage }) => {
  const [currentStep, setCurrentStep] = useState(1);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const [workspaceName, setWorkspaceName] = useState('');
  const [productUrl, setProductUrl] = useState('');
  const [productSummary, setProductSummary] = useState(null);
  const [campaignName, setCampaignName] = useState('');
  const [mailboxProvider, setMailboxProvider] = useState('resend');
  const [completed, setCompleted] = useState({});

  const handleNext = async () => {
    setError('');
    setWorking(true);
    try {
      if (currentStep === 1) {
        await authAxios.post(`${apiBase}/workspaces`, { name: workspaceName });
        setCompleted((c) => ({ ...c, workspace: true }));
      } else if (currentStep === 2) {
        const res = await authAxios.post(`${apiBase}/analyze`, { url: productUrl });
        setProductSummary(res.data || null);
        setCompleted((c) => ({ ...c, product: true }));
      } else if (currentStep === 3) {
        await authAxios.post(`${apiBase}/campaigns`, { name: campaignName });
        setCompleted((c) => ({ ...c, campaign: true }));
      } else if (currentStep === 4) {
        // Mailbox setup — legacy redirects to OAuth flow; placeholder here.
        setCompleted((c) => ({ ...c, mailbox: true }));
      }
      setCurrentStep((s) => Math.min(STEPS.length, s + 1));
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setWorking(false);
    }
  };

  const renderStep = () => {
    if (currentStep === 1) {
      return (
        <>
          <h2 className="text-lg font-black text-[#2B2926]">Name your workspace</h2>
          <p className="text-xs text-[#2B2926]/60 mt-1">
            One workspace per company. You can rename it later.
          </p>
          <input
            type="text"
            value={workspaceName}
            onChange={(e) => setWorkspaceName(e.target.value)}
            placeholder="e.g., Acme Sales"
            className="w-full mt-4 px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600]"
          />
        </>
      );
    }
    if (currentStep === 2) {
      return (
        <>
          <h2 className="text-lg font-black text-[#2B2926]">Add your product</h2>
          <p className="text-xs text-[#2B2926]/60 mt-1">
            Paste your product URL. We'll extract the summary + ideal customer profile.
          </p>
          <input
            type="text"
            value={productUrl}
            onChange={(e) => setProductUrl(e.target.value)}
            placeholder="https://yourproduct.com"
            className="w-full mt-4 px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600]"
          />
          {productSummary && (
            <div className="mt-3 p-3 rounded-lg bg-[#10B981]/10 border border-[#10B981]/30 text-xs text-[#10B981]">
              ✓ Product summary extracted.
            </div>
          )}
        </>
      );
    }
    if (currentStep === 3) {
      return (
        <>
          <h2 className="text-lg font-black text-[#2B2926]">Name your first campaign</h2>
          <p className="text-xs text-[#2B2926]/60 mt-1">
            We'll use your product summary + ICP to find and contact leads.
          </p>
          <input
            type="text"
            value={campaignName}
            onChange={(e) => setCampaignName(e.target.value)}
            placeholder="e.g., Q2 Outbound — US SaaS founders"
            className="w-full mt-4 px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600]"
          />
        </>
      );
    }
    if (currentStep === 4) {
      return (
        <>
          <h2 className="text-lg font-black text-[#2B2926]">Connect a mailbox</h2>
          <p className="text-xs text-[#2B2926]/60 mt-1">
            We send through Resend by default (verified domain). You can switch to
            Gmail later for higher deliverability on smaller volumes.
          </p>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <label
              className={[
                'p-3 rounded-lg border cursor-pointer transition-all',
                mailboxProvider === 'resend'
                  ? 'border-[#F55600] bg-[#F55600]/5'
                  : 'border-[#2B2926]/10 hover:bg-[#F55600]/5',
              ].join(' ')}
            >
              <input
                type="radio"
                name="mailbox"
                value="resend"
                checked={mailboxProvider === 'resend'}
                onChange={() => setMailboxProvider('resend')}
                className="sr-only"
              />
              <div className="text-sm font-bold text-[#2B2926]">Resend</div>
              <div className="text-[10px] text-[#2B2926]/60 mt-1">Verified domain · high volume</div>
            </label>
            <label
              className={[
                'p-3 rounded-lg border cursor-pointer transition-all',
                mailboxProvider === 'gmail'
                  ? 'border-[#F55600] bg-[#F55600]/5'
                  : 'border-[#2B2926]/10 hover:bg-[#F55600]/5',
              ].join(' ')}
            >
              <input
                type="radio"
                name="mailbox"
                value="gmail"
                checked={mailboxProvider === 'gmail'}
                onChange={() => setMailboxProvider('gmail')}
                className="sr-only"
              />
              <div className="text-sm font-bold text-[#2B2926]">Gmail</div>
              <div className="text-[10px] text-[#2B2926]/60 mt-1">OAuth · 1:1 personal sender</div>
            </label>
          </div>
        </>
      );
    }
    return null;
  };

  const isDone = currentStep > STEPS.length;

  return (
    <div className="bg-white">
      <div className="px-5 py-4 border-b border-[#2B2926]/10">
        <h1 className="text-lg font-black text-[#2B2926] tracking-tight">Get started with Nexus</h1>
        <p className="text-xs text-[#2B2926]/60 mt-0.5">
          Four quick steps to your first campaign.
        </p>
      </div>

      {/* Step indicator */}
      <div className="px-5 py-4 border-b border-[#2B2926]/10">
        <div className="flex items-center gap-2">
          {STEPS.map((s, i) => {
            const active = currentStep === s.id;
            const done = completed[s.key];
            return (
              <React.Fragment key={s.id}>
                <div
                  className={[
                    'inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-bold transition-all',
                    active
                      ? 'bg-[#F55600] text-white'
                      : done
                      ? 'bg-[#10B981]/15 text-[#10B981]'
                      : 'bg-[#2B2926]/5 text-[#2B2926]/40',
                  ].join(' ')}
                >
                  {done ? <CheckCircle2 className="w-3 h-3" /> : <s.icon className="w-3 h-3" />}
                  {s.label}
                </div>
                {i < STEPS.length - 1 && (
                  <ChevronRight className="w-3 h-3 text-[#2B2926]/20" />
                )}
              </React.Fragment>
            );
          })}
        </div>
      </div>

      {error && (
        <div className="mx-5 mt-3 px-3 py-2 rounded-lg bg-[#F55600]/10 border border-[#F55600]/30 text-xs text-[#F55600]">
          {error}
        </div>
      )}

      <div className="px-5 py-6 max-w-xl">
        {isDone ? (
          <div className="text-center py-12">
            <CheckCircle2 className="w-12 h-12 mx-auto text-[#10B981] mb-3" />
            <h2 className="text-lg font-black text-[#2B2926]">You're all set!</h2>
            <p className="text-xs text-[#2B2926]/60 mt-1">
              Head over to the Dashboard to see your first leads land.
            </p>
          </div>
        ) : (
          <>
            {renderStep()}
            <div className="mt-6 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={handleNext}
                disabled={working}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[#F55600] text-white text-xs font-bold hover:opacity-90 disabled:opacity-50"
              >
                {working ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
                {currentStep === STEPS.length ? 'Finish' : 'Next'}
                <ChevronRight className="w-3.5 h-3.5" />
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
};

export default NexusOnboarding;
