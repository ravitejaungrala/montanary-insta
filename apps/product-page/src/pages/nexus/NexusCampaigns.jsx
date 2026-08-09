import React, { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { isReadOnly } from '../../lib/permissions';
import {
  Plus,
  Target,
  ArrowLeft,
  Edit3,
  Trash2,
  Pause,
  Play,
  X,
  ChevronRight,
  Users,
  Send,
  TrendingUp,
  Calendar,
  AlertCircle,
  RefreshCw,
  Briefcase,
  Layers,
} from 'lucide-react';

/* --------------------------------------------------------------------------
 * NexusCampaigns
 * --------------------------------------------------------------------------
 * Modes:
 *   - list     : table of all campaigns + create button
 *   - create   : multi-section form (name, product, ICP)
 *   - detail   : single campaign with stats + attached sequences + leads
 *   - edit     : inline edit of a campaign's name/status/ICP
 * -------------------------------------------------------------------------- */

const STATUS_OPTIONS = ['active', 'paused', 'completed'];

const relativeTime = (iso) => {
  if (!iso) return '';
  try {
    const then = new Date(iso).getTime();
    const now = Date.now();
    const diff = Math.max(0, now - then) / 1000;
    if (diff < 60) return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`;
    return new Date(iso).toLocaleDateString();
  } catch {
    return '';
  }
};

const StatusBadge = ({ status }) => {
  const s = (status || '').toLowerCase();
  if (s === 'active') {
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold uppercase tracking-wider bg-[#10B981]/10 text-[#10B981]">
        <span className="w-1.5 h-1.5 rounded-full bg-[#10B981]" />
        Active
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold uppercase tracking-wider bg-[#2B2926]/5 text-[#2B2926]/60">
      <span className="w-1.5 h-1.5 rounded-full bg-[#2B2926]/40" />
      {s || 'draft'}
    </span>
  );
};

const ChipInput = ({ label, values, onChange, placeholder }) => {
  const [input, setInput] = useState('');

  const addChip = (raw) => {
    const v = (raw || '').trim().replace(/,$/, '').trim();
    if (!v) return;
    if (values.includes(v)) return;
    onChange([...values, v]);
    setInput('');
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      addChip(input);
    } else if (e.key === 'Backspace' && !input && values.length) {
      onChange(values.slice(0, -1));
    }
  };

  return (
    <div>
      <label className="block text-xs font-bold uppercase tracking-widest text-[#2B2926]/60 mb-2">
        {label}
      </label>
      <div className="min-h-[48px] w-full px-3 py-2 rounded-xl border-2 border-[#2B2926]/10 bg-white flex flex-wrap gap-2 items-center focus-within:border-[#F55600] transition-colors">
        {values.map((v) => (
          <span
            key={v}
            className="inline-flex items-center gap-1 pl-3 pr-1 py-1 rounded-full bg-[#F55600]/10 text-[#F55600] text-xs font-semibold"
          >
            {v}
            <button
              type="button"
              onClick={() => onChange(values.filter((x) => x !== v))}
              className="p-0.5 rounded-full hover:bg-[#F55600]/20 transition-colors"
              aria-label={`Remove ${v}`}
            >
              <X size={12} />
            </button>
          </span>
        ))}
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={() => input && addChip(input)}
          placeholder={values.length ? '' : placeholder}
          className="flex-1 min-w-[120px] px-1 py-1 outline-none text-sm bg-transparent text-[#2B2926] placeholder:text-[#2B2926]/30"
        />
      </div>
      <p className="text-[11px] text-[#2B2926]/40 mt-1.5">
        Press Enter or comma to add. Backspace to remove the last chip.
      </p>
    </div>
  );
};

const Skeleton = ({ className = '' }) => (
  <div className={`bg-[#2B2926]/5 animate-pulse rounded-lg ${className}`} />
);

const SectionCard = ({ children, className = '' }) => (
  <div
    className={`bg-white border border-[#2B2926]/10 rounded-2xl p-6 ${className}`}
  >
    {children}
  </div>
);

/* -------------------------------------------------------------------------- */

const NexusCampaigns = ({ authAxios, user, setMessage, apiBase }) => {
  const [mode, setMode] = useState({ name: 'list' });

  // List state
  const [campaigns, setCampaigns] = useState([]);
  const [loadingList, setLoadingList] = useState(true);
  const [listError, setListError] = useState(null);

  // Create state
  const [products, setProducts] = useState([]);
  const [loadingProducts, setLoadingProducts] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({
    name: '',
    product_id: '',
    industries: [],
    roles: [],
    company_sizes: [],
    pain_points: [],
  });

  // Detail/edit state
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState(null);
  const [stats, setStats] = useState(null);
  const [attachedSequences, setAttachedSequences] = useState([]);
  const [recentLeads, setRecentLeads] = useState([]);
  const [confirmDelete, setConfirmDelete] = useState(null);
  const [savingDetail, setSavingDetail] = useState(false);

  const notify = useCallback(
    (msg, kind = 'info') => {
      if (setMessage) setMessage({ text: msg, type: kind });
    },
    [setMessage]
  );

  /* ----------------------------- LIST ----------------------------- */
  const fetchCampaigns = useCallback(async () => {
    setLoadingList(true);
    setListError(null);
    try {
      const res = await authAxios.get('/nexus/campaigns');
      const list = Array.isArray(res.data) ? res.data : res.data?.items || [];
      setCampaigns(list);
    } catch (e) {
      const msg =
        e?.response?.data?.detail ||
        e?.message ||
        'Failed to load campaigns';
      setListError(msg);
    } finally {
      setLoadingList(false);
    }
  }, [authAxios]);

  useEffect(() => {
    fetchCampaigns();
  }, [fetchCampaigns]);

  /* ----------------------------- CREATE ----------------------------- */
  const fetchProducts = useCallback(async () => {
    setLoadingProducts(true);
    try {
      const res = await authAxios.get('/nexus/products');
      const list = Array.isArray(res.data) ? res.data : res.data?.items || [];
      setProducts(list);
    } catch (e) {
      notify(
        e?.response?.data?.detail || 'Failed to load products',
        'error'
      );
    } finally {
      setLoadingProducts(false);
    }
  }, [authAxios, notify]);

  const openCreate = () => {
    setForm({
      name: '',
      product_id: '',
      industries: [],
      roles: [],
      company_sizes: [],
      pain_points: [],
    });
    fetchProducts();
    setMode({ name: 'create' });
  };

  const submitCreate = async () => {
    if (!form.name.trim()) {
      notify('Campaign name is required', 'error');
      return;
    }
    if (!form.product_id) {
      notify('Pick a product for this campaign', 'error');
      return;
    }
    setCreating(true);
    try {
      const payload = {
        name: form.name.trim(),
        product_id: form.product_id,
        icp: {
          industries: form.industries,
          roles: form.roles,
          company_sizes: form.company_sizes,
          pain_points: form.pain_points,
        },
      };
      const res = await authAxios.post('/nexus/campaigns', payload);
      notify('Campaign created', 'success');
      await fetchCampaigns();
      const id = res?.data?.id;
      if (id) {
        openDetail(id);
      } else {
        setMode({ name: 'list' });
      }
    } catch (e) {
      notify(
        e?.response?.data?.detail || 'Failed to create campaign',
        'error'
      );
    } finally {
      setCreating(false);
    }
  };

  /* ----------------------------- DETAIL ----------------------------- */
  const openDetail = async (id) => {
    setMode({ name: 'detail', id });
    setDetailLoading(true);
    setDetailError(null);
    setDetail(null);
    setStats(null);
    setAttachedSequences([]);
    setRecentLeads([]);

    try {
      const [cRes, sRes, seqRes, lRes] = await Promise.allSettled([
        authAxios.get(`/nexus/campaigns/${id}`),
        authAxios.get(`/nexus/analytics/summary`, {
          params: { campaign_id: id },
        }),
        authAxios.get(`/nexus/sequences`, { params: { campaign_id: id } }),
        authAxios.get(`/nexus/leads`, {
          params: { campaign_id: id, limit: 10 },
        }),
      ]);

      if (cRes.status === 'fulfilled') {
        setDetail(cRes.value.data);
      } else {
        setDetailError(
          cRes.reason?.response?.data?.detail ||
            cRes.reason?.message ||
            'Failed to load campaign'
        );
      }

      if (sRes.status === 'fulfilled') {
        setStats(sRes.value.data || {});
      }
      if (seqRes.status === 'fulfilled') {
        const list = Array.isArray(seqRes.value.data)
          ? seqRes.value.data
          : seqRes.value.data?.items || [];
        setAttachedSequences(list);
      }
      if (lRes.status === 'fulfilled') {
        const list = Array.isArray(lRes.value.data)
          ? lRes.value.data
          : lRes.value.data?.items || [];
        setRecentLeads(list);
      }
    } finally {
      setDetailLoading(false);
    }
  };

  const patchCampaign = async (id, patch) => {
    setSavingDetail(true);
    try {
      const res = await authAxios.patch(`/nexus/campaigns/${id}`, patch);
      setDetail((d) => ({ ...(d || {}), ...(res.data || patch) }));
      setCampaigns((list) =>
        list.map((c) => (c.id === id ? { ...c, ...(res.data || patch) } : c))
      );
      notify('Campaign updated', 'success');
    } catch (e) {
      notify(
        e?.response?.data?.detail || 'Failed to update campaign',
        'error'
      );
    } finally {
      setSavingDetail(false);
    }
  };

  const togglePause = (c) => {
    const next = c.status === 'active' ? 'paused' : 'active';
    patchCampaign(c.id, { status: next });
  };

  const deleteCampaign = async (id) => {
    try {
      await authAxios.delete(`/nexus/campaigns/${id}`);
      setCampaigns((list) => list.filter((c) => c.id !== id));
      setConfirmDelete(null);
      notify('Campaign deleted', 'success');
      if (mode.name === 'detail' && mode.id === id) {
        setMode({ name: 'list' });
      }
    } catch (e) {
      notify(
        e?.response?.data?.detail || 'Failed to delete campaign',
        'error'
      );
    }
  };

  /* ----------------------------- HELPERS ----------------------------- */
  const productName = (campaign) => {
    if (campaign?.product?.name) return campaign.product.name;
    const p = products.find((x) => x.id === campaign?.product_id);
    if (p?.name) return p.name;
    return campaign?.product_name || 'Untitled product';
  };

  /* ----------------------------- RENDER: LIST ----------------------------- */
  const renderList = () => (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-3xl font-black text-[#2B2926] tracking-tight">
            Campaigns
          </h1>
          <p className="text-sm text-[#2B2926]/60 mt-1">
            Outbound campaigns powered by Nexus.
          </p>
        </div>
        {!isReadOnly(user) && (
          <button
            onClick={openCreate}
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-[#F55600] text-white font-bold text-sm shadow-lg shadow-[#F55600]/20 hover:bg-[#F55600]/90 active:scale-95 transition-all"
          >
            <Plus size={18} />
            New Campaign
          </button>
        )}
      </div>

      {loadingList && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <Skeleton key={i} className="h-40" />
          ))}
        </div>
      )}

      {!loadingList && listError && (
        <div className="bg-white border-2 border-[#F55600]/30 rounded-2xl p-6 flex items-start gap-3">
          <AlertCircle className="text-[#F55600] mt-0.5" size={20} />
          <div className="flex-1">
            <p className="font-bold text-[#2B2926]">Couldn't load campaigns</p>
            <p className="text-sm text-[#2B2926]/60 mt-1">{listError}</p>
          </div>
          <button
            onClick={fetchCampaigns}
            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-[#F55600] text-white text-xs font-bold hover:bg-[#F55600]/90"
          >
            <RefreshCw size={14} /> Retry
          </button>
        </div>
      )}

      {!loadingList && !listError && campaigns.length === 0 && (
        <div className="bg-white border border-[#2B2926]/10 rounded-3xl p-12 flex flex-col items-center text-center">
          <div className="w-16 h-16 rounded-2xl bg-[#F55600]/10 flex items-center justify-center mb-4">
            <Target className="text-[#F55600]" size={28} />
          </div>
          <h3 className="text-xl font-black text-[#2B2926] mb-2">
            No campaigns yet
          </h3>
          <p className="text-sm text-[#2B2926]/60 max-w-md mb-6">
            Campaigns let you target a specific ICP with a curated cadence.
            Start by spinning up your first one.
          </p>
          {!isReadOnly(user) && (
            <button
              onClick={openCreate}
              className="inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-[#F55600] text-white font-bold text-sm shadow-lg shadow-[#F55600]/20 hover:bg-[#F55600]/90 active:scale-95 transition-all"
            >
              <Plus size={18} />
              Create your first campaign
            </button>
          )}
        </div>
      )}

      {!loadingList && !listError && campaigns.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {campaigns.map((c) => (
            <motion.button
              key={c.id}
              whileHover={{ y: -2 }}
              onClick={() => openDetail(c.id)}
              className="text-left bg-white border border-[#2B2926]/10 rounded-2xl p-5 hover:border-[#F55600]/40 hover:shadow-lg transition-all group"
            >
              <div className="flex items-start justify-between mb-4">
                <div className="w-10 h-10 rounded-xl bg-[#F55600]/10 flex items-center justify-center">
                  <Target className="text-[#F55600]" size={18} />
                </div>
                <StatusBadge status={c.status} />
              </div>
              <h3 className="text-lg font-black text-[#2B2926] mb-1 line-clamp-2">
                {c.name || 'Untitled campaign'}
              </h3>
              <p className="text-xs text-[#2B2926]/50 mb-4 inline-flex items-center gap-1.5">
                <Briefcase size={12} />
                {productName(c)}
              </p>
              <div className="flex items-center justify-between text-xs text-[#2B2926]/40">
                <span className="inline-flex items-center gap-1">
                  <Calendar size={12} />
                  {relativeTime(c.created_at)}
                </span>
                <ChevronRight
                  size={16}
                  className="text-[#2B2926]/30 group-hover:text-[#F55600] transition-colors"
                />
              </div>
            </motion.button>
          ))}
        </div>
      )}
    </div>
  );

  /* ----------------------------- RENDER: CREATE ----------------------------- */
  const renderCreate = () => {
    const productsEmpty = !loadingProducts && products.length === 0;
    return (
      <div className="max-w-3xl mx-auto space-y-6">
        <div className="flex items-center gap-3">
          <button
            onClick={() => setMode({ name: 'list' })}
            className="p-2 rounded-xl hover:bg-[#2B2926]/5 text-[#2B2926]/60 hover:text-[#2B2926] transition-colors"
            aria-label="Back"
          >
            <ArrowLeft size={20} />
          </button>
          <div>
            <h1 className="text-2xl font-black text-[#2B2926]">New Campaign</h1>
            <p className="text-sm text-[#2B2926]/60">
              Define who you're targeting and what you're pitching.
            </p>
          </div>
        </div>

        <SectionCard>
          <h2 className="text-sm font-bold uppercase tracking-widest text-[#2B2926]/60 mb-4">
            Basics
          </h2>
          <div className="space-y-5">
            <div>
              <label className="block text-xs font-bold uppercase tracking-widest text-[#2B2926]/60 mb-2">
                Campaign name
              </label>
              <input
                value={form.name}
                onChange={(e) =>
                  setForm((f) => ({ ...f, name: e.target.value }))
                }
                placeholder="Q3 fintech ops outbound"
                className="w-full px-4 py-3 rounded-xl border-2 border-[#2B2926]/10 bg-white text-[#2B2926] placeholder:text-[#2B2926]/30 outline-none focus:border-[#F55600] transition-colors"
              />
            </div>

            <div>
              <label className="block text-xs font-bold uppercase tracking-widest text-[#2B2926]/60 mb-2">
                Product
              </label>
              {loadingProducts ? (
                <Skeleton className="h-12" />
              ) : productsEmpty ? (
                <div className="px-4 py-3 rounded-xl border-2 border-dashed border-[#2B2926]/15 bg-[#2B2926]/[0.02] text-sm">
                  <span className="text-[#2B2926]/60">No products yet. </span>
                  <a
                    href="/nexus/products"
                    className="text-[#F55600] font-bold hover:underline"
                  >
                    Create a product first
                  </a>
                </div>
              ) : (
                <select
                  value={form.product_id}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, product_id: e.target.value }))
                  }
                  className="w-full px-4 py-3 rounded-xl border-2 border-[#2B2926]/10 bg-white text-[#2B2926] outline-none focus:border-[#F55600] transition-colors"
                >
                  <option value="">Select a product…</option>
                  {products.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              )}
            </div>
          </div>
        </SectionCard>

        <SectionCard>
          <h2 className="text-sm font-bold uppercase tracking-widest text-[#2B2926]/60 mb-4">
            Ideal Customer Profile
          </h2>
          <div className="space-y-5">
            <ChipInput
              label="Industries"
              values={form.industries}
              onChange={(v) => setForm((f) => ({ ...f, industries: v }))}
              placeholder="e.g. Fintech, Logistics, SaaS"
            />
            <ChipInput
              label="Roles"
              values={form.roles}
              onChange={(v) => setForm((f) => ({ ...f, roles: v }))}
              placeholder="e.g. Head of Ops, VP Marketing"
            />
            <ChipInput
              label="Company sizes"
              values={form.company_sizes}
              onChange={(v) => setForm((f) => ({ ...f, company_sizes: v }))}
              placeholder="e.g. 11-50, 51-200"
            />
            <ChipInput
              label="Pain points"
              values={form.pain_points}
              onChange={(v) => setForm((f) => ({ ...f, pain_points: v }))}
              placeholder="e.g. Manual reporting, Slow onboarding"
            />
          </div>
        </SectionCard>

        <div className="flex items-center justify-end gap-3">
          <button
            onClick={() => setMode({ name: 'list' })}
            className="px-5 py-2.5 rounded-xl text-sm font-bold text-[#2B2926]/70 hover:bg-[#2B2926]/5 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={submitCreate}
            disabled={creating}
            className="inline-flex items-center gap-2 px-6 py-2.5 rounded-xl bg-[#F55600] text-white font-bold text-sm shadow-lg shadow-[#F55600]/20 hover:bg-[#F55600]/90 active:scale-95 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {creating ? (
              <>
                <RefreshCw size={16} className="animate-spin" />
                Creating…
              </>
            ) : (
              <>
                <Plus size={16} />
                Create campaign
              </>
            )}
          </button>
        </div>
      </div>
    );
  };

  /* ----------------------------- RENDER: DETAIL ----------------------------- */
  const renderStat = (label, value, Icon) => (
    <div className="bg-white border border-[#2B2926]/10 rounded-2xl p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] font-bold uppercase tracking-widest text-[#2B2926]/50">
          {label}
        </span>
        <Icon size={14} className="text-[#2B2926]/30" />
      </div>
      <div className="text-2xl font-black text-[#2B2926]">
        {value === undefined || value === null ? '—' : value}
      </div>
    </div>
  );

  const renderDetail = () => {
    if (detailLoading && !detail) {
      return (
        <div className="space-y-6">
          <Skeleton className="h-20" />
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-24" />
            ))}
          </div>
          <Skeleton className="h-40" />
        </div>
      );
    }
    if (detailError) {
      return (
        <div className="bg-white border-2 border-[#F55600]/30 rounded-2xl p-6 flex items-start gap-3">
          <AlertCircle className="text-[#F55600] mt-0.5" size={20} />
          <div className="flex-1">
            <p className="font-bold text-[#2B2926]">Couldn't load campaign</p>
            <p className="text-sm text-[#2B2926]/60 mt-1">{detailError}</p>
          </div>
          <button
            onClick={() => setMode({ name: 'list' })}
            className="px-3 py-2 rounded-lg bg-[#2B2926]/5 text-[#2B2926] text-xs font-bold hover:bg-[#2B2926]/10"
          >
            Back
          </button>
        </div>
      );
    }
    if (!detail) return null;

    const icp = detail.icp || {};

    const fmtPct = (v) => {
      if (v === undefined || v === null) return '—';
      const n = typeof v === 'number' ? v : parseFloat(v);
      if (Number.isNaN(n)) return '—';
      const pct = n > 1 ? n : n * 100;
      return `${pct.toFixed(1)}%`;
    };

    return (
      <div className="space-y-6">
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-4">
          <div className="flex items-start gap-3">
            <button
              onClick={() => setMode({ name: 'list' })}
              className="p-2 rounded-xl hover:bg-[#2B2926]/5 text-[#2B2926]/60 hover:text-[#2B2926] transition-colors mt-1"
              aria-label="Back"
            >
              <ArrowLeft size={20} />
            </button>
            <div>
              <div className="flex items-center gap-3 mb-1">
                <h1 className="text-2xl md:text-3xl font-black text-[#2B2926] tracking-tight">
                  {detail.name}
                </h1>
                <StatusBadge status={detail.status} />
              </div>
              <p className="text-sm text-[#2B2926]/60 inline-flex items-center gap-1.5">
                <Briefcase size={14} />
                {productName(detail)}
                <span className="text-[#2B2926]/30 mx-1">•</span>
                <Calendar size={14} />
                Created {relativeTime(detail.created_at)}
              </p>
            </div>
          </div>

          {!isReadOnly(user) && (
          <div className="flex items-center gap-2">
            <button
              onClick={() => togglePause(detail)}
              disabled={savingDetail}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl border border-[#2B2926]/10 bg-white text-sm font-bold text-[#2B2926] hover:bg-[#2B2926]/5 transition-colors disabled:opacity-50"
            >
              {detail.status === 'active' ? (
                <>
                  <Pause size={14} /> Pause
                </>
              ) : (
                <>
                  <Play size={14} /> Resume
                </>
              )}
            </button>
            <button
              onClick={() => setMode({ name: 'edit', id: detail.id })}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl border border-[#2B2926]/10 bg-white text-sm font-bold text-[#2B2926] hover:bg-[#2B2926]/5 transition-colors"
            >
              <Edit3 size={14} /> Edit
            </button>
            <button
              onClick={() => setConfirmDelete(detail.id)}
              className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl bg-[#F55600]/10 text-[#F55600] text-sm font-bold hover:bg-[#F55600]/20 transition-colors"
            >
              <Trash2 size={14} /> Delete
            </button>
          </div>
          )}
        </div>

        {/* Stats */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {renderStat('Sent', stats?.sent ?? 0, Send)}
          {renderStat('Replies', stats?.replies ?? 0, Users)}
          {renderStat(
            'Intent signal rate',
            fmtPct(stats?.intent_signal_rate),
            TrendingUp
          )}
          {renderStat('Demo bookings', stats?.demo_bookings ?? 0, Calendar)}
        </div>

        {/* ICP */}
        <SectionCard>
          <h2 className="text-sm font-bold uppercase tracking-widest text-[#2B2926]/60 mb-4">
            Target ICP
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            {['industries', 'roles', 'company_sizes', 'pain_points'].map(
              (key) => (
                <div key={key}>
                  <p className="text-[11px] font-bold uppercase tracking-widest text-[#2B2926]/40 mb-2">
                    {key.replace('_', ' ')}
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {(icp[key] || []).length === 0 ? (
                      <span className="text-sm text-[#2B2926]/40">—</span>
                    ) : (
                      (icp[key] || []).map((v) => (
                        <span
                          key={v}
                          className="px-2.5 py-1 rounded-full bg-[#2B2926]/5 text-[#2B2926] text-xs font-semibold"
                        >
                          {v}
                        </span>
                      ))
                    )}
                  </div>
                </div>
              )
            )}
          </div>
        </SectionCard>

        {/* Sequences */}
        <SectionCard>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-bold uppercase tracking-widest text-[#2B2926]/60 inline-flex items-center gap-2">
              <Layers size={14} />
              Attached sequences
            </h2>
            <a
              href="/nexus/sequences"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#F55600] text-white text-xs font-bold hover:bg-[#F55600]/90"
            >
              <Plus size={12} /> New Sequence
            </a>
          </div>
          {attachedSequences.length === 0 ? (
            <p className="text-sm text-[#2B2926]/50">
              No sequences attached to this campaign yet.
            </p>
          ) : (
            <div className="space-y-2">
              {attachedSequences.map((s) => (
                <div
                  key={s.id}
                  className="flex items-center justify-between p-3 rounded-xl border border-[#2B2926]/10 hover:border-[#F55600]/30 transition-colors"
                >
                  <div>
                    <p className="font-bold text-[#2B2926]">{s.name}</p>
                    <p className="text-xs text-[#2B2926]/50">
                      {(s.steps || []).length} steps
                    </p>
                  </div>
                  <ChevronRight size={16} className="text-[#2B2926]/30" />
                </div>
              ))}
            </div>
          )}
        </SectionCard>

        {/* Leads */}
        <SectionCard>
          <h2 className="text-sm font-bold uppercase tracking-widest text-[#2B2926]/60 mb-4 inline-flex items-center gap-2">
            <Users size={14} />
            Recent leads
          </h2>
          {recentLeads.length === 0 ? (
            <p className="text-sm text-[#2B2926]/50">No leads yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-widest text-[#2B2926]/40 border-b border-[#2B2926]/10">
                    <th className="py-2 pr-4 font-bold">Name</th>
                    <th className="py-2 pr-4 font-bold">Company</th>
                    <th className="py-2 pr-4 font-bold">Role</th>
                    <th className="py-2 pr-4 font-bold">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {recentLeads.map((l) => (
                    <tr
                      key={l.id}
                      className="border-b border-[#2B2926]/5 last:border-0"
                    >
                      <td className="py-2.5 pr-4 text-[#2B2926] font-medium">
                        {l.first_name || l.name || '—'}{' '}
                        {l.last_name || ''}
                      </td>
                      <td className="py-2.5 pr-4 text-[#2B2926]/70">
                        {l.company || '—'}
                      </td>
                      <td className="py-2.5 pr-4 text-[#2B2926]/70">
                        {l.role || l.title || '—'}
                      </td>
                      <td className="py-2.5 pr-4">
                        <StatusBadge status={l.status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </SectionCard>
      </div>
    );
  };

  /* ----------------------------- RENDER: EDIT ----------------------------- */
  const [editDraft, setEditDraft] = useState(null);
  useEffect(() => {
    if (mode.name === 'edit' && detail) {
      setEditDraft({
        name: detail.name || '',
        status: detail.status || 'active',
        industries: detail.icp?.industries || [],
        roles: detail.icp?.roles || [],
        company_sizes: detail.icp?.company_sizes || [],
        pain_points: detail.icp?.pain_points || [],
      });
    }
  }, [mode, detail]);

  const submitEdit = async () => {
    if (!editDraft) return;
    await patchCampaign(detail.id, {
      name: editDraft.name.trim(),
      status: editDraft.status,
      icp: {
        industries: editDraft.industries,
        roles: editDraft.roles,
        company_sizes: editDraft.company_sizes,
        pain_points: editDraft.pain_points,
      },
    });
    setMode({ name: 'detail', id: detail.id });
  };

  const renderEdit = () => {
    if (!editDraft || !detail) {
      return <Skeleton className="h-64" />;
    }
    return (
      <div className="max-w-3xl mx-auto space-y-6">
        <div className="flex items-center gap-3">
          <button
            onClick={() => setMode({ name: 'detail', id: detail.id })}
            className="p-2 rounded-xl hover:bg-[#2B2926]/5 text-[#2B2926]/60 hover:text-[#2B2926] transition-colors"
            aria-label="Back"
          >
            <ArrowLeft size={20} />
          </button>
          <div>
            <h1 className="text-2xl font-black text-[#2B2926]">Edit campaign</h1>
            <p className="text-sm text-[#2B2926]/60">{detail.name}</p>
          </div>
        </div>

        <SectionCard>
          <div className="space-y-5">
            <div>
              <label className="block text-xs font-bold uppercase tracking-widest text-[#2B2926]/60 mb-2">
                Name
              </label>
              <input
                value={editDraft.name}
                onChange={(e) =>
                  setEditDraft((d) => ({ ...d, name: e.target.value }))
                }
                className="w-full px-4 py-3 rounded-xl border-2 border-[#2B2926]/10 bg-white text-[#2B2926] outline-none focus:border-[#F55600] transition-colors"
              />
            </div>
            <div>
              <label className="block text-xs font-bold uppercase tracking-widest text-[#2B2926]/60 mb-2">
                Status
              </label>
              <div className="flex gap-2">
                {STATUS_OPTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => setEditDraft((d) => ({ ...d, status: s }))}
                    className={`px-4 py-2 rounded-xl text-sm font-bold capitalize transition-colors ${
                      editDraft.status === s
                        ? 'bg-[#F55600] text-white'
                        : 'bg-[#2B2926]/5 text-[#2B2926]/60 hover:bg-[#2B2926]/10'
                    }`}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </SectionCard>

        <SectionCard>
          <h2 className="text-sm font-bold uppercase tracking-widest text-[#2B2926]/60 mb-4">
            Ideal Customer Profile
          </h2>
          <div className="space-y-5">
            <ChipInput
              label="Industries"
              values={editDraft.industries}
              onChange={(v) =>
                setEditDraft((d) => ({ ...d, industries: v }))
              }
              placeholder="Add industry"
            />
            <ChipInput
              label="Roles"
              values={editDraft.roles}
              onChange={(v) => setEditDraft((d) => ({ ...d, roles: v }))}
              placeholder="Add role"
            />
            <ChipInput
              label="Company sizes"
              values={editDraft.company_sizes}
              onChange={(v) =>
                setEditDraft((d) => ({ ...d, company_sizes: v }))
              }
              placeholder="Add size"
            />
            <ChipInput
              label="Pain points"
              values={editDraft.pain_points}
              onChange={(v) =>
                setEditDraft((d) => ({ ...d, pain_points: v }))
              }
              placeholder="Add pain point"
            />
          </div>
        </SectionCard>

        <div className="flex items-center justify-end gap-3">
          <button
            onClick={() => setMode({ name: 'detail', id: detail.id })}
            className="px-5 py-2.5 rounded-xl text-sm font-bold text-[#2B2926]/70 hover:bg-[#2B2926]/5 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={submitEdit}
            disabled={savingDetail}
            className="inline-flex items-center gap-2 px-6 py-2.5 rounded-xl bg-[#F55600] text-white font-bold text-sm shadow-lg shadow-[#F55600]/20 hover:bg-[#F55600]/90 active:scale-95 transition-all disabled:opacity-50"
          >
            {savingDetail ? (
              <>
                <RefreshCw size={16} className="animate-spin" />
                Saving…
              </>
            ) : (
              'Save changes'
            )}
          </button>
        </div>
      </div>
    );
  };

  /* ----------------------------- DELETE MODAL ----------------------------- */
  const renderConfirmDelete = () => (
    <AnimatePresence>
      {confirmDelete && (
        <div className="fixed inset-0 z-[1000] flex items-center justify-center p-4">
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setConfirmDelete(null)}
            className="absolute inset-0 bg-[#2B2926]/30"
          />
          <motion.div
            initial={{ scale: 0.95, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.95, opacity: 0 }}
            className="relative bg-white rounded-2xl p-6 w-full max-w-sm shadow-2xl border border-[#2B2926]/10"
          >
            <div className="w-12 h-12 rounded-2xl bg-[#F55600]/10 flex items-center justify-center mb-4">
              <Trash2 className="text-[#F55600]" size={22} />
            </div>
            <h3 className="text-xl font-black text-[#2B2926] mb-2">
              Delete campaign?
            </h3>
            <p className="text-sm text-[#2B2926]/60 mb-6">
              This will permanently remove the campaign and detach its
              sequences. This action can't be undone.
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => setConfirmDelete(null)}
                className="flex-1 px-4 py-2.5 rounded-xl text-sm font-bold text-[#2B2926]/70 bg-[#2B2926]/5 hover:bg-[#2B2926]/10 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => deleteCampaign(confirmDelete)}
                className="flex-1 px-4 py-2.5 rounded-xl text-sm font-bold text-white bg-[#F55600] hover:bg-[#F55600]/90 transition-colors"
              >
                Delete
              </button>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );

  /* ----------------------------- TOP-LEVEL RENDER ----------------------------- */
  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto">
      {mode.name === 'list' && renderList()}
      {mode.name === 'create' && renderCreate()}
      {mode.name === 'detail' && renderDetail()}
      {mode.name === 'edit' && renderEdit()}
      {renderConfirmDelete()}
    </div>
  );
};

export default NexusCampaigns;
