import React, { useState, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Plus,
  Layers,
  ArrowLeft,
  Trash2,
  Save,
  RefreshCw,
  AlertCircle,
  Clock,
  ChevronRight,
  Target,
  X,
  GripVertical,
} from 'lucide-react';

/* --------------------------------------------------------------------------
 * NexusSequences
 * --------------------------------------------------------------------------
 * Modes:
 *   - list   : card list of sequences
 *   - create : new sequence form (name + campaign + initial steps)
 *   - edit   : full editor for sequence name + steps array
 * -------------------------------------------------------------------------- */

const PLACEHOLDER_VARS = [
  '{first_name}',
  '{company}',
  '{role}',
  '{value_prop}',
  '{sender_name}',
];

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

const buildEmptyStep = (idx) => ({
  step: idx + 1,
  delay_days: idx === 0 ? 0 : 3,
  subject_template: '',
  body_template: '',
});

/* -------------------------------------------------------------------------- */

const NexusSequences = ({ authAxios, user, setMessage, apiBase }) => {
  const [mode, setMode] = useState({ name: 'list' });

  // List
  const [sequences, setSequences] = useState([]);
  const [loadingList, setLoadingList] = useState(true);
  const [listError, setListError] = useState(null);

  // Campaigns (lookup)
  const [campaigns, setCampaigns] = useState([]);
  const [loadingCampaigns, setLoadingCampaigns] = useState(false);

  // Detail/edit
  const [editing, setEditing] = useState(null); // {id, name, campaign_id, steps[]}
  const [editingLoading, setEditingLoading] = useState(false);
  const [editError, setEditError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(null);

  const notify = useCallback(
    (msg, kind = 'info') => {
      if (setMessage) setMessage({ text: msg, type: kind });
    },
    [setMessage]
  );

  /* ----------------------------- DATA ----------------------------- */
  const fetchSequences = useCallback(async () => {
    setLoadingList(true);
    setListError(null);
    try {
      const res = await authAxios.get('/nexus/sequences');
      const list = Array.isArray(res.data) ? res.data : res.data?.items || [];
      setSequences(list);
    } catch (e) {
      setListError(
        e?.response?.data?.detail || e?.message || 'Failed to load sequences'
      );
    } finally {
      setLoadingList(false);
    }
  }, [authAxios]);

  const fetchCampaigns = useCallback(async () => {
    setLoadingCampaigns(true);
    try {
      const res = await authAxios.get('/nexus/campaigns');
      const list = Array.isArray(res.data) ? res.data : res.data?.items || [];
      setCampaigns(list);
    } catch (e) {
      // non-fatal — we still let the user open the editor
    } finally {
      setLoadingCampaigns(false);
    }
  }, [authAxios]);

  useEffect(() => {
    fetchSequences();
    fetchCampaigns();
  }, [fetchSequences, fetchCampaigns]);

  const campaignName = (id) => {
    const c = campaigns.find((x) => x.id === id);
    return c?.name || 'Unattached';
  };

  /* ----------------------------- LOAD ONE ----------------------------- */
  const openEdit = async (id) => {
    setMode({ name: 'edit', id });
    setEditingLoading(true);
    setEditError(null);
    setEditing(null);
    try {
      const res = await authAxios.get(`/nexus/sequences/${id}`);
      const data = res.data || {};
      const steps = (data.steps || []).map((s, i) => ({
        step: s.step ?? i + 1,
        delay_days:
          typeof s.delay_days === 'number'
            ? s.delay_days
            : parseInt(s.delay_days, 10) || 0,
        subject_template: s.subject_template || '',
        body_template: s.body_template || '',
      }));
      setEditing({
        id: data.id || id,
        name: data.name || '',
        campaign_id: data.campaign_id || '',
        steps: steps.length ? steps : [buildEmptyStep(0)],
        updated_at: data.updated_at || data.created_at || null,
      });
    } catch (e) {
      setEditError(
        e?.response?.data?.detail || e?.message || 'Failed to load sequence'
      );
    } finally {
      setEditingLoading(false);
    }
  };

  const openCreate = () => {
    setEditing({
      id: null,
      name: '',
      campaign_id: '',
      steps: [buildEmptyStep(0)],
      updated_at: null,
    });
    setMode({ name: 'create' });
  };

  /* ----------------------------- STEP MUTATORS ----------------------------- */
  const updateStep = (idx, patch) => {
    setEditing((cur) => {
      if (!cur) return cur;
      const next = [...cur.steps];
      next[idx] = { ...next[idx], ...patch };
      return { ...cur, steps: next };
    });
  };

  const addStep = () => {
    setEditing((cur) => {
      if (!cur) return cur;
      const next = [...cur.steps, buildEmptyStep(cur.steps.length)];
      return { ...cur, steps: next };
    });
  };

  const removeStep = (idx) => {
    setEditing((cur) => {
      if (!cur) return cur;
      if (cur.steps.length <= 1) return cur;
      const next = cur.steps
        .filter((_, i) => i !== idx)
        .map((s, i) => ({ ...s, step: i + 1 }));
      return { ...cur, steps: next };
    });
  };

  /* ----------------------------- SAVE ----------------------------- */
  const validate = () => {
    if (!editing) return 'No sequence loaded';
    if (!editing.name.trim()) return 'Sequence name is required';
    if (!editing.steps.length) return 'At least one step is required';
    for (const s of editing.steps) {
      if (!s.body_template?.trim()) {
        return `Step ${s.step}: body cannot be empty`;
      }
      if (Number.isNaN(parseInt(s.delay_days, 10))) {
        return `Step ${s.step}: delay must be a number`;
      }
    }
    return null;
  };

  const saveSequence = async () => {
    const err = validate();
    if (err) {
      notify(err, 'error');
      return;
    }
    setSaving(true);
    const payload = {
      name: editing.name.trim(),
      campaign_id: editing.campaign_id || null,
      steps: editing.steps.map((s, i) => ({
        step: i + 1,
        delay_days: parseInt(s.delay_days, 10) || 0,
        subject_template: s.subject_template || '',
        body_template: s.body_template || '',
      })),
    };
    try {
      if (mode.name === 'create' || !editing.id) {
        const res = await authAxios.post('/nexus/sequences', payload);
        notify('Sequence created', 'success');
        await fetchSequences();
        const newId = res?.data?.id;
        if (newId) {
          openEdit(newId);
        } else {
          setMode({ name: 'list' });
        }
      } else {
        const res = await authAxios.patch(
          `/nexus/sequences/${editing.id}`,
          payload
        );
        notify('Sequence saved', 'success');
        setSequences((list) =>
          list.map((s) =>
            s.id === editing.id ? { ...s, ...(res.data || payload) } : s
          )
        );
        setEditing((cur) => (cur ? { ...cur, ...payload } : cur));
      }
    } catch (e) {
      notify(
        e?.response?.data?.detail || 'Failed to save sequence',
        'error'
      );
    } finally {
      setSaving(false);
    }
  };

  const deleteSequence = async (id) => {
    try {
      await authAxios.delete(`/nexus/sequences/${id}`);
      setSequences((list) => list.filter((s) => s.id !== id));
      setConfirmDelete(null);
      notify('Sequence deleted', 'success');
      if (mode.name === 'edit' && mode.id === id) {
        setMode({ name: 'list' });
      }
    } catch (e) {
      notify(
        e?.response?.data?.detail || 'Failed to delete sequence',
        'error'
      );
    }
  };

  /* ----------------------------- RENDER: LIST ----------------------------- */
  const renderList = () => (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-3xl font-black text-[#2B2926] tracking-tight">
            Sequences
          </h1>
          <p className="text-sm text-[#2B2926]/60 mt-1">
            Multi-step cadences that go out to leads in a campaign.
          </p>
        </div>
        <button
          onClick={openCreate}
          className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-[#F55600] text-white font-bold text-sm shadow-lg shadow-[#F55600]/20 hover:bg-[#F55600]/90 active:scale-95 transition-all"
        >
          <Plus size={18} />
          New Sequence
        </button>
      </div>

      {loadingList && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-32" />
          ))}
        </div>
      )}

      {!loadingList && listError && (
        <div className="bg-white border-2 border-[#F55600]/30 rounded-2xl p-6 flex items-start gap-3">
          <AlertCircle className="text-[#F55600] mt-0.5" size={20} />
          <div className="flex-1">
            <p className="font-bold text-[#2B2926]">Couldn't load sequences</p>
            <p className="text-sm text-[#2B2926]/60 mt-1">{listError}</p>
          </div>
          <button
            onClick={fetchSequences}
            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-[#F55600] text-white text-xs font-bold hover:bg-[#F55600]/90"
          >
            <RefreshCw size={14} /> Retry
          </button>
        </div>
      )}

      {!loadingList && !listError && sequences.length === 0 && (
        <div className="bg-white border border-[#2B2926]/10 rounded-3xl p-12 flex flex-col items-center text-center">
          <div className="w-16 h-16 rounded-2xl bg-[#F55600]/10 flex items-center justify-center mb-4">
            <Layers className="text-[#F55600]" size={28} />
          </div>
          <h3 className="text-xl font-black text-[#2B2926] mb-2">
            No sequences yet
          </h3>
          <p className="text-sm text-[#2B2926]/60 max-w-md mb-6">
            Sequences are reusable cadence templates. Build one and attach it
            to any campaign.
          </p>
          <button
            onClick={openCreate}
            className="inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-[#F55600] text-white font-bold text-sm shadow-lg shadow-[#F55600]/20 hover:bg-[#F55600]/90 active:scale-95 transition-all"
          >
            <Plus size={18} />
            Create your first sequence
          </button>
        </div>
      )}

      {!loadingList && !listError && sequences.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {sequences.map((s) => {
            const stepCount = (s.steps || []).length;
            const updated = s.updated_at || s.created_at;
            return (
              <motion.button
                key={s.id}
                whileHover={{ y: -2 }}
                onClick={() => openEdit(s.id)}
                className="text-left bg-white border border-[#2B2926]/10 rounded-2xl p-5 hover:border-[#F55600]/40 hover:shadow-lg transition-all group"
              >
                <div className="flex items-start justify-between mb-3">
                  <div className="w-10 h-10 rounded-xl bg-[#F55600]/10 flex items-center justify-center">
                    <Layers className="text-[#F55600]" size={18} />
                  </div>
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold uppercase tracking-wider bg-[#2B2926]/5 text-[#2B2926]/60">
                    {stepCount} {stepCount === 1 ? 'step' : 'steps'}
                  </span>
                </div>
                <h3 className="text-lg font-black text-[#2B2926] mb-1 line-clamp-2">
                  {s.name || 'Untitled sequence'}
                </h3>
                <p className="text-xs text-[#2B2926]/50 mb-4 inline-flex items-center gap-1.5">
                  <Target size={12} />
                  {campaignName(s.campaign_id)}
                </p>
                <div className="flex items-center justify-between text-xs text-[#2B2926]/40">
                  <span className="inline-flex items-center gap-1">
                    <Clock size={12} />
                    {updated ? `Updated ${relativeTime(updated)}` : '—'}
                  </span>
                  <ChevronRight
                    size={16}
                    className="text-[#2B2926]/30 group-hover:text-[#F55600] transition-colors"
                  />
                </div>
              </motion.button>
            );
          })}
        </div>
      )}
    </div>
  );

  /* ----------------------------- RENDER: EDIT/CREATE ----------------------------- */
  const renderEditor = () => {
    if (editingLoading) {
      return (
        <div className="max-w-4xl mx-auto space-y-4">
          <Skeleton className="h-16" />
          <Skeleton className="h-40" />
          <Skeleton className="h-40" />
        </div>
      );
    }
    if (editError) {
      return (
        <div className="max-w-4xl mx-auto bg-white border-2 border-[#F55600]/30 rounded-2xl p-6 flex items-start gap-3">
          <AlertCircle className="text-[#F55600] mt-0.5" size={20} />
          <div className="flex-1">
            <p className="font-bold text-[#2B2926]">Couldn't load sequence</p>
            <p className="text-sm text-[#2B2926]/60 mt-1">{editError}</p>
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
    if (!editing) return null;

    const isCreate = mode.name === 'create' || !editing.id;

    return (
      <div className="max-w-4xl mx-auto space-y-6">
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
              <h1 className="text-2xl md:text-3xl font-black text-[#2B2926] tracking-tight">
                {isCreate ? 'New sequence' : 'Edit sequence'}
              </h1>
              <p className="text-sm text-[#2B2926]/60">
                {editing.updated_at
                  ? `Last updated ${relativeTime(editing.updated_at)}`
                  : 'Drafting changes locally — click Save to persist.'}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {!isCreate && (
              <button
                onClick={() => setConfirmDelete(editing.id)}
                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl bg-[#F55600]/10 text-[#F55600] text-sm font-bold hover:bg-[#F55600]/20 transition-colors"
              >
                <Trash2 size={14} /> Delete
              </button>
            )}
            <button
              onClick={saveSequence}
              disabled={saving}
              className="inline-flex items-center gap-2 px-5 py-2 rounded-xl bg-[#F55600] text-white font-bold text-sm shadow-lg shadow-[#F55600]/20 hover:bg-[#F55600]/90 active:scale-95 transition-all disabled:opacity-50"
            >
              {saving ? (
                <>
                  <RefreshCw size={16} className="animate-spin" />
                  Saving…
                </>
              ) : (
                <>
                  <Save size={16} />
                  Save
                </>
              )}
            </button>
          </div>
        </div>

        {/* Meta */}
        <SectionCard>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            <div>
              <label className="block text-xs font-bold uppercase tracking-widest text-[#2B2926]/60 mb-2">
                Sequence name
              </label>
              <input
                value={editing.name}
                onChange={(e) =>
                  setEditing((cur) => ({ ...cur, name: e.target.value }))
                }
                placeholder="3-touch outbound for Heads of Ops"
                className="w-full px-4 py-3 rounded-xl border-2 border-[#2B2926]/10 bg-white text-[#2B2926] placeholder:text-[#2B2926]/30 outline-none focus:border-[#F55600] transition-colors"
              />
            </div>
            <div>
              <label className="block text-xs font-bold uppercase tracking-widest text-[#2B2926]/60 mb-2">
                Attached campaign
              </label>
              <select
                value={editing.campaign_id || ''}
                onChange={(e) =>
                  setEditing((cur) => ({
                    ...cur,
                    campaign_id: e.target.value || '',
                  }))
                }
                disabled={loadingCampaigns}
                className="w-full px-4 py-3 rounded-xl border-2 border-[#2B2926]/10 bg-white text-[#2B2926] outline-none focus:border-[#F55600] transition-colors disabled:opacity-50"
              >
                <option value="">— None —</option>
                {campaigns.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </SectionCard>

        {/* Steps */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-bold uppercase tracking-widest text-[#2B2926]/60">
              Steps ({editing.steps.length})
            </h2>
            <div className="flex flex-wrap gap-1.5">
              {PLACEHOLDER_VARS.map((v) => (
                <span
                  key={v}
                  className="px-2 py-1 rounded-md bg-[#2B2926]/5 text-[#2B2926]/60 text-[11px] font-mono"
                >
                  {v}
                </span>
              ))}
            </div>
          </div>

          {editing.steps.map((s, idx) => (
            <SectionCard key={idx}>
              <div className="flex items-start gap-3 mb-4">
                <div className="flex items-center gap-2">
                  <GripVertical size={16} className="text-[#2B2926]/20" />
                  <div className="w-9 h-9 rounded-xl bg-[#F55600]/10 text-[#F55600] font-black flex items-center justify-center">
                    {idx + 1}
                  </div>
                </div>
                <div className="flex-1">
                  <p className="text-sm font-bold text-[#2B2926]">
                    Step {idx + 1}
                  </p>
                  <p className="text-xs text-[#2B2926]/50">
                    {idx === 0
                      ? 'First touch — sent immediately after lead is added.'
                      : `Follow-up — sent ${
                          parseInt(s.delay_days, 10) || 0
                        } day(s) after previous step.`}
                  </p>
                </div>
                {editing.steps.length > 1 && (
                  <button
                    onClick={() => removeStep(idx)}
                    className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-bold text-[#2B2926]/50 hover:bg-[#F55600]/10 hover:text-[#F55600] transition-colors"
                  >
                    <X size={12} />
                    Remove
                  </button>
                )}
              </div>

              <div className="grid grid-cols-1 md:grid-cols-[140px_1fr] gap-4 mb-4">
                <div>
                  <label className="block text-[11px] font-bold uppercase tracking-widest text-[#2B2926]/50 mb-1.5">
                    Delay (days)
                  </label>
                  <input
                    type="number"
                    min={0}
                    value={s.delay_days}
                    onChange={(e) =>
                      updateStep(idx, {
                        delay_days:
                          e.target.value === ''
                            ? 0
                            : parseInt(e.target.value, 10) || 0,
                      })
                    }
                    className="w-full px-3 py-2.5 rounded-xl border-2 border-[#2B2926]/10 bg-white text-[#2B2926] outline-none focus:border-[#F55600] transition-colors"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-bold uppercase tracking-widest text-[#2B2926]/50 mb-1.5">
                    Subject
                  </label>
                  <input
                    value={s.subject_template}
                    onChange={(e) =>
                      updateStep(idx, { subject_template: e.target.value })
                    }
                    placeholder={
                      idx === 0
                        ? '{first_name}, quick idea for {company}'
                        : 're: previous note'
                    }
                    className="w-full px-3 py-2.5 rounded-xl border-2 border-[#2B2926]/10 bg-white text-[#2B2926] placeholder:text-[#2B2926]/30 outline-none focus:border-[#F55600] transition-colors"
                  />
                </div>
              </div>

              <div>
                <label className="block text-[11px] font-bold uppercase tracking-widest text-[#2B2926]/50 mb-1.5">
                  Body
                </label>
                <textarea
                  value={s.body_template}
                  onChange={(e) =>
                    updateStep(idx, { body_template: e.target.value })
                  }
                  rows={6}
                  placeholder={`Hi {first_name},\n\nNoticed {company} is scaling fast — figured {value_prop} might land for someone in your seat.\n\n— {sender_name}`}
                  className="w-full px-3 py-2.5 rounded-xl border-2 border-[#2B2926]/10 bg-white text-[#2B2926] placeholder:text-[#2B2926]/30 outline-none focus:border-[#F55600] transition-colors font-mono text-sm leading-relaxed resize-y"
                />
                <p className="text-[#2B2926]/40 text-xs mt-1.5">
                  Use placeholders:{' '}
                  <span className="font-mono">
                    {PLACEHOLDER_VARS.join(' ')}
                  </span>
                </p>
              </div>
            </SectionCard>
          ))}

          <button
            onClick={addStep}
            className="w-full inline-flex items-center justify-center gap-2 px-4 py-3 rounded-2xl border-2 border-dashed border-[#F55600]/30 text-[#F55600] text-sm font-bold hover:bg-[#F55600]/5 hover:border-[#F55600] transition-all"
          >
            <Plus size={16} />
            Add Step
          </button>
        </div>

        {/* Bottom save bar (sticky-ish) */}
        <div className="flex items-center justify-end gap-3 pt-2">
          <button
            onClick={() => setMode({ name: 'list' })}
            className="px-5 py-2.5 rounded-xl text-sm font-bold text-[#2B2926]/70 hover:bg-[#2B2926]/5 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={saveSequence}
            disabled={saving}
            className="inline-flex items-center gap-2 px-6 py-2.5 rounded-xl bg-[#F55600] text-white font-bold text-sm shadow-lg shadow-[#F55600]/20 hover:bg-[#F55600]/90 active:scale-95 transition-all disabled:opacity-50"
          >
            {saving ? (
              <>
                <RefreshCw size={16} className="animate-spin" />
                Saving…
              </>
            ) : isCreate ? (
              'Create sequence'
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
              Delete sequence?
            </h3>
            <p className="text-sm text-[#2B2926]/60 mb-6">
              This will remove the sequence and stop any in-flight steps.
              This action can't be undone.
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => setConfirmDelete(null)}
                className="flex-1 px-4 py-2.5 rounded-xl text-sm font-bold text-[#2B2926]/70 bg-[#2B2926]/5 hover:bg-[#2B2926]/10 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => deleteSequence(confirmDelete)}
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

  /* ----------------------------- TOP-LEVEL ----------------------------- */
  return (
    <div className="p-6 md:p-8 max-w-7xl mx-auto">
      {mode.name === 'list' && renderList()}
      {(mode.name === 'edit' || mode.name === 'create') && renderEditor()}
      {renderConfirmDelete()}
    </div>
  );
};

export default NexusSequences;
