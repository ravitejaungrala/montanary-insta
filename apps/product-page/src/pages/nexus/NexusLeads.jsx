import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Search,
  Sparkles,
  Plus,
  Trash2,
  Eye,
  X,
  Loader2,
  RefreshCw,
  ChevronLeft,
  ChevronRight,
  AlertCircle,
  Inbox,
  Globe,
  Linkedin,
} from 'lucide-react';

const STATUS_STYLES = {
  new: 'bg-[#F55600]/10 text-[#F55600]',
  contacted: 'bg-[#2B2926]/5 text-[#2B2926]/60',
  replied: 'bg-[#10B981]/10 text-[#10B981]',
  unsubscribed: 'bg-[#2B2926]/5 text-[#2B2926]/40',
  bounced: 'bg-[#2B2926]/5 text-[#2B2926]/40',
};

// Backend stores lead `source` using the original provider names. We keep
// `value` aligned with the DB while showing neutral labels in the UI so we
// don't expose third-party vendor names to end users.
const SOURCES = [
  { value: 'Hunter', label: 'Hunter' },
  { value: 'Apollo', label: 'Database' },
  { value: 'LinkedIn', label: 'LinkedIn' },
  { value: 'Reddit', label: 'Reddit' },
  { value: 'GitHub', label: 'GitHub' },
  { value: 'Website', label: 'Website' },
];

const timeAgo = (iso) => {
  if (!iso) return '—';
  try {
    const then = new Date(iso).getTime();
    const diff = Math.max(0, Date.now() - then);
    const m = Math.floor(diff / 60000);
    if (m < 1) return 'just now';
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    const d = Math.floor(h / 24);
    if (d < 30) return `${d}d ago`;
    return new Date(iso).toLocaleDateString();
  } catch {
    return '—';
  }
};

const initials = (lead) => {
  const fn = lead?.global_lead?.first_name || '';
  const ln = lead?.global_lead?.last_name || '';
  const a = fn.charAt(0) || lead?.global_lead?.email?.charAt(0) || '?';
  const b = ln.charAt(0) || '';
  return (a + b).toUpperCase();
};

const fullName = (lead) => {
  const fn = lead?.global_lead?.first_name || '';
  const ln = lead?.global_lead?.last_name || '';
  const n = `${fn} ${ln}`.trim();
  return n || lead?.global_lead?.email || 'Unknown lead';
};

const scoreColor = (s) => {
  if (s == null) return 'bg-[#2B2926]/5 text-[#2B2926]/40';
  if (s >= 70) return 'bg-[#10B981]/10 text-[#10B981]';
  if (s >= 40) return 'bg-[#F55600]/10 text-[#F55600]';
  return 'bg-[#2B2926]/5 text-[#2B2926]/40';
};

const NexusLeads = ({ authAxios, user, setMessage, apiBase }) => {
  const [campaigns, setCampaigns] = useState([]);
  const [campaignsLoading, setCampaignsLoading] = useState(true);
  const [campaignsError, setCampaignsError] = useState(null);

  const [campaignId, setCampaignId] = useState('all');
  const [minScore, setMinScore] = useState(0);
  const [sourceFilter, setSourceFilter] = useState('all');
  const [search, setSearch] = useState('');

  const [leads, setLeads] = useState([]);
  const [leadsLoading, setLeadsLoading] = useState(true);
  const [leadsError, setLeadsError] = useState(null);

  const [limit] = useState(50);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);

  // Autonomous discovery modal state
  const [autoOpen, setAutoOpen] = useState(false);
  const [autoRunning, setAutoRunning] = useState(false);
  const [autoSourceCounts, setAutoSourceCounts] = useState({});
  const [autoLeadsFound, setAutoLeadsFound] = useState(0);
  const [autoTotal, setAutoTotal] = useState(0);
  const [autoLog, setAutoLog] = useState([]);
  const [autoError, setAutoError] = useState(null);
  const [autoTargetCount, setAutoTargetCount] = useState(100);
  const autoAbortRef = useRef(null);
  const pollTimerRef = useRef(null);

  // Quick add modal
  const [quickOpen, setQuickOpen] = useState(false);
  const [quickSubmitting, setQuickSubmitting] = useState(false);
  const [quickForm, setQuickForm] = useState({
    campaign_id: '',
    email: '',
    first_name: '',
    last_name: '',
    role: '',
    company_name: '',
    company_domain: '',
  });

  // Lead detail drawer
  const [detail, setDetail] = useState(null);

  const fetchCampaigns = useCallback(async () => {
    setCampaignsLoading(true);
    setCampaignsError(null);
    try {
      const res = await authAxios.get('/nexus/campaigns');
      const list = Array.isArray(res.data) ? res.data : res.data?.campaigns || [];
      setCampaigns(list);
      if (list.length > 0 && !quickForm.campaign_id) {
        setQuickForm((f) => ({ ...f, campaign_id: list[0].id }));
      }
    } catch (e) {
      setCampaignsError(e?.response?.data?.detail || e.message || 'Failed to load campaigns');
    } finally {
      setCampaignsLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authAxios]);

  const fetchLeads = useCallback(async () => {
    setLeadsLoading(true);
    setLeadsError(null);
    try {
      const params = { limit, offset };
      if (campaignId && campaignId !== 'all') params.campaign_id = campaignId;
      if (minScore > 0) params.min_score = minScore;
      const res = await authAxios.get('/nexus/leads', { params });
      const list = Array.isArray(res.data) ? res.data : res.data?.leads || [];
      setLeads(list);
      setHasMore(list.length === limit);
    } catch (e) {
      setLeadsError(e?.response?.data?.detail || e.message || 'Failed to load leads');
    } finally {
      setLeadsLoading(false);
    }
  }, [authAxios, campaignId, minScore, limit, offset]);

  useEffect(() => {
    fetchCampaigns();
  }, [fetchCampaigns]);

  useEffect(() => {
    fetchLeads();
  }, [fetchLeads]);

  // Reset offset when filters change
  useEffect(() => {
    setOffset(0);
  }, [campaignId, minScore, sourceFilter]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (autoAbortRef.current) {
        try { autoAbortRef.current.abort(); } catch (_) { /* noop */ }
      }
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
      }
    };
  }, []);

  const filteredLeads = leads.filter((l) => {
    if (sourceFilter !== 'all') {
      const signals = l?.signals || {};
      const src = (signals.source || signals.discovered_via || '').toString().toLowerCase();
      if (!src.includes(sourceFilter.toLowerCase())) return false;
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      const hay = [
        l?.global_lead?.email,
        l?.global_lead?.first_name,
        l?.global_lead?.last_name,
        l?.global_lead?.role,
        l?.global_lead?.company_name,
        l?.global_lead?.company_domain,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  const startAutonomous = async () => {
    if (!campaignId || campaignId === 'all') {
      setMessage && setMessage('Pick a specific campaign before running autonomous discovery.');
      return;
    }
    setAutoOpen(true);
    setAutoRunning(true);
    setAutoSourceCounts({});
    setAutoLeadsFound(0);
    setAutoTotal(0);
    setAutoLog([]);
    setAutoError(null);

    const body = { campaign_id: campaignId, target_count: autoTargetCount };
    const controller = new AbortController();
    autoAbortRef.current = controller;

    // Try fetch + ReadableStream for SSE-style consumption. If parsing fails or
    // the endpoint isn't streaming, fall back to polling /nexus/leads.
    let usedStream = false;
    try {
      // Build URL from authAxios baseURL when available, otherwise apiBase.
      const base = (authAxios?.defaults?.baseURL || apiBase || '').replace(/\/$/, '');
      const url = `${base}/nexus/leads/autonomous`;
      const headers = { 'Content-Type': 'application/json', Accept: 'text/event-stream' };
      const authHeader = authAxios?.defaults?.headers?.common?.Authorization
        || authAxios?.defaults?.headers?.Authorization;
      if (authHeader) headers.Authorization = authHeader;

      const res = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!res.ok || !res.body) {
        throw new Error(`HTTP ${res.status}`);
      }

      usedStream = true;
      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';

      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE frames are separated by blank lines; tolerate raw JSON-per-line too
        const frames = buffer.split(/\n\n|\r\n\r\n/);
        buffer = frames.pop() || '';
        for (const frame of frames) {
          const lines = frame.split(/\r?\n/);
          for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed) continue;
            const payload = trimmed.startsWith('data:')
              ? trimmed.slice(5).trim()
              : trimmed;
            if (!payload || payload === '[DONE]') continue;
            try {
              const evt = JSON.parse(payload);
              handleStreamEvent(evt);
            } catch {
              // Non-JSON heartbeat or comment — ignore
            }
          }
        }
      }
    } catch (e) {
      if (e.name === 'AbortError') {
        // user closed modal
      } else if (!usedStream) {
        // Fall back to polling
        setAutoLog((l) => [...l, 'Streaming unavailable — polling lead list every 3s']);
        startPolling();
        return;
      } else {
        setAutoError(e.message || 'Stream error');
      }
    } finally {
      if (usedStream) {
        setAutoRunning(false);
        // Refresh table now that the run is complete
        fetchLeads();
      }
    }
  };

  const handleStreamEvent = (evt) => {
    if (!evt || typeof evt !== 'object') return;
    if (evt.type === 'progress') {
      const src = evt.source || 'unknown';
      const count = typeof evt.count === 'number' ? evt.count : 0;
      setAutoSourceCounts((prev) => ({ ...prev, [src]: count }));
      setAutoLog((l) => [...l, `${src}: ${count} found`]);
    } else if (evt.type === 'lead') {
      setAutoLeadsFound((n) => n + 1);
    } else if (evt.type === 'done') {
      setAutoTotal(evt.total || 0);
      setAutoLog((l) => [...l, `Done — ${evt.total || 0} total leads`]);
    } else if (evt.type === 'error') {
      setAutoError(evt.message || 'Unknown error');
    }
  };

  const startPolling = () => {
    const startTs = Date.now();
    const seen = new Set(leads.map((l) => l.id));
    pollTimerRef.current = setInterval(async () => {
      try {
        const params = { campaign_id: campaignId, limit: 100, offset: 0 };
        const res = await authAxios.get('/nexus/leads', { params });
        const list = Array.isArray(res.data) ? res.data : res.data?.leads || [];
        let added = 0;
        list.forEach((l) => {
          if (!seen.has(l.id)) {
            seen.add(l.id);
            added += 1;
          }
        });
        if (added > 0) {
          setAutoLeadsFound((n) => n + added);
          setAutoLog((log) => [...log, `+${added} new leads`]);
        }
        // Stop after 2 minutes of polling
        if (Date.now() - startTs > 120000) {
          clearInterval(pollTimerRef.current);
          pollTimerRef.current = null;
          setAutoRunning(false);
          fetchLeads();
        }
      } catch (e) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
        setAutoError(e?.response?.data?.detail || e.message || 'Polling failed');
        setAutoRunning(false);
      }
    }, 3000);
  };

  const closeAutoModal = () => {
    if (autoAbortRef.current) {
      try { autoAbortRef.current.abort(); } catch (_) { /* noop */ }
    }
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    setAutoOpen(false);
    setAutoRunning(false);
    fetchLeads();
  };

  const deleteLead = async (lead) => {
    if (!window.confirm(`Delete lead ${fullName(lead)}?`)) return;
    try {
      await authAxios.delete(`/nexus/leads/${lead.id}`);
      setLeads((curr) => curr.filter((l) => l.id !== lead.id));
      setMessage && setMessage('Lead deleted');
    } catch (e) {
      setMessage && setMessage(e?.response?.data?.detail || 'Failed to delete lead');
    }
  };

  const openDetail = async (lead) => {
    setDetail({ loading: true, lead });
    try {
      const res = await authAxios.get(`/nexus/leads/${lead.id}`);
      setDetail({ loading: false, lead: res.data || lead });
    } catch (e) {
      setDetail({ loading: false, lead, error: e?.response?.data?.detail || e.message });
    }
  };

  const submitQuickAdd = async (e) => {
    e.preventDefault();
    if (!quickForm.campaign_id) {
      setMessage && setMessage('Pick a campaign first');
      return;
    }
    if (!quickForm.email) return;
    setQuickSubmitting(true);
    try {
      await authAxios.post('/nexus/leads', {
        campaign_id: quickForm.campaign_id,
        leads: [
          {
            email: quickForm.email,
            first_name: quickForm.first_name,
            last_name: quickForm.last_name,
            role: quickForm.role,
            company_name: quickForm.company_name,
            company_domain: quickForm.company_domain,
          },
        ],
      });
      setQuickOpen(false);
      setQuickForm((f) => ({
        ...f,
        email: '',
        first_name: '',
        last_name: '',
        role: '',
        company_name: '',
        company_domain: '',
      }));
      fetchLeads();
      setMessage && setMessage('Lead added');
    } catch (err) {
      setMessage && setMessage(err?.response?.data?.detail || 'Failed to add lead');
    } finally {
      setQuickSubmitting(false);
    }
  };

  return (
    <div className="p-6 max-w-[1400px] mx-auto">
      {/* Header */}
      <div className="flex items-start justify-between flex-wrap gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-semibold text-[#2B2926]">Leads</h1>
          <p className="text-sm text-[#2B2926]/60 mt-1">
            {user?.full_name ? `${user.full_name.split(' ')[0]}'s pipeline` : 'Your pipeline'}
            {' · '}
            {filteredLeads.length} shown
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setQuickOpen(true)}
            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm text-[#2B2926] hover:bg-[#2B2926]/5 transition-colors"
          >
            <Plus className="w-4 h-4" />
            Quick Add
          </button>
          <button
            type="button"
            disabled={autoRunning}
            onClick={startAutonomous}
            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-[#F55600] text-white text-sm hover:bg-[#F55600]/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {autoRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
            Autonomous Discover
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mb-6 p-4 rounded-xl border border-[#2B2926]/10 bg-white">
        <div>
          <label className="block text-xs text-[#2B2926]/60 mb-1">Campaign</label>
          <select
            value={campaignId}
            onChange={(e) => setCampaignId(e.target.value)}
            disabled={campaignsLoading}
            className="w-full px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600] bg-white"
          >
            <option value="all">All campaigns</option>
            {campaigns.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name || c.title || `Campaign ${c.id}`}
              </option>
            ))}
          </select>
          {campaignsError && (
            <p className="text-xs text-[#F55600] mt-1">{campaignsError}</p>
          )}
        </div>

        <div>
          <label className="block text-xs text-[#2B2926]/60 mb-1">
            Min ICP score: <span className="text-[#2B2926]">{minScore}</span>
          </label>
          <input
            type="range"
            min="0"
            max="100"
            step="5"
            value={minScore}
            onChange={(e) => setMinScore(Number(e.target.value))}
            className="w-full accent-[#F55600]"
          />
        </div>

        <div>
          <label className="block text-xs text-[#2B2926]/60 mb-1">Source</label>
          <select
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value)}
            className="w-full px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600] bg-white"
          >
            <option value="all">All sources</option>
            {SOURCES.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs text-[#2B2926]/60 mb-1">Search</label>
          <div className="relative">
            <Search className="w-4 h-4 text-[#2B2926]/40 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Name, company, email…"
              className="w-full pl-9 pr-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600]"
            />
          </div>
        </div>
      </div>

      {/* Table */}
      <div className="rounded-xl border border-[#2B2926]/10 bg-white overflow-hidden">
        {leadsLoading ? (
          <div className="flex items-center justify-center py-16 text-[#2B2926]/60">
            <Loader2 className="w-5 h-5 animate-spin mr-2" />
            Loading leads…
          </div>
        ) : leadsError ? (
          <div className="flex items-center justify-center py-16 text-[#F55600]">
            <AlertCircle className="w-5 h-5 mr-2" />
            {leadsError}
            <button
              onClick={fetchLeads}
              className="ml-3 text-sm underline hover:no-underline"
            >
              Retry
            </button>
          </div>
        ) : filteredLeads.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-center px-6">
            <Inbox className="w-10 h-10 text-[#2B2926]/30 mb-3" />
            <p className="text-[#2B2926] font-medium">No leads yet</p>
            <p className="text-sm text-[#2B2926]/60 mt-1 max-w-sm">
              Run Autonomous Discover to fan out across 6 sources, or Quick Add a known prospect.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-[#2B2926]/40 border-b border-[#2B2926]/10">
                  <th className="px-4 py-3 font-medium">Name</th>
                  <th className="px-4 py-3 font-medium">Company</th>
                  <th className="px-4 py-3 font-medium">ICP Score</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium">Last touched</th>
                  <th className="px-4 py-3 font-medium text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredLeads.map((lead) => {
                  const domain = lead?.global_lead?.company_domain;
                  return (
                    <tr key={lead.id} className="border-b border-[#2B2926]/5 hover:bg-[#2B2926]/[0.02] transition-colors">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-3">
                          <div className="w-9 h-9 rounded-full bg-[#F55600]/10 text-[#F55600] text-xs font-semibold flex items-center justify-center flex-shrink-0">
                            {initials(lead)}
                          </div>
                          <div className="min-w-0">
                            <div className="flex items-center gap-1.5 min-w-0">
                              <p className="text-[#2B2926] font-medium truncate">{fullName(lead)}</p>
                              {lead?.global_lead?.linkedin_url ? (
                                <a
                                  href={lead.global_lead.linkedin_url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  onClick={(e) => e.stopPropagation()}
                                  className="inline-flex items-center justify-center w-4 h-4 rounded text-[#0077b5] hover:text-[#F55600] flex-shrink-0"
                                  title="Open LinkedIn profile"
                                  aria-label="Open LinkedIn profile"
                                >
                                  <Linkedin className="w-3.5 h-3.5" />
                                </a>
                              ) : null}
                            </div>
                            <p className="text-xs text-[#2B2926]/50 truncate">
                              {lead?.global_lead?.role || lead?.global_lead?.email || '—'}
                            </p>
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2 min-w-0">
                          {domain ? (
                            <img
                              src={`https://logo.clearbit.com/${domain}`}
                              alt=""
                              className="w-6 h-6 rounded object-contain bg-white border border-[#2B2926]/5 flex-shrink-0"
                              onError={(e) => {
                                e.currentTarget.style.display = 'none';
                                const next = e.currentTarget.nextSibling;
                                if (next) next.style.display = 'inline-flex';
                              }}
                            />
                          ) : null}
                          <span
                            className="w-6 h-6 rounded bg-[#2B2926]/5 text-[#2B2926]/40 items-center justify-center flex-shrink-0"
                            style={{ display: domain ? 'none' : 'inline-flex' }}
                          >
                            <Globe className="w-3 h-3" />
                          </span>
                          <div className="min-w-0">
                            <p className="text-[#2B2926] truncate">
                              {lead?.global_lead?.company_name || domain || '—'}
                            </p>
                            {domain && (
                              <p className="text-xs text-[#2B2926]/40 truncate">{domain}</p>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs font-semibold ${scoreColor(lead.icp_score)}`}>
                          {lead.icp_score ?? '—'}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium ${STATUS_STYLES[lead.status] || 'bg-[#2B2926]/5 text-[#2B2926]/60'}`}>
                          {lead.status || 'new'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-[#2B2926]/60 text-xs">
                        {timeAgo(lead.last_touched_at || lead.updated_at || lead.created_at)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-1">
                          <button
                            onClick={() => openDetail(lead)}
                            className="p-1.5 rounded-md text-[#2B2926]/60 hover:text-[#F55600] hover:bg-[#F55600]/5"
                            title="View"
                          >
                            <Eye className="w-4 h-4" />
                          </button>
                          <button
                            onClick={() => deleteLead(lead)}
                            className="p-1.5 rounded-md text-[#2B2926]/60 hover:text-[#F55600] hover:bg-[#F55600]/5"
                            title="Delete"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Pagination */}
      {!leadsLoading && !leadsError && filteredLeads.length > 0 && (
        <div className="flex items-center justify-between mt-4 text-sm text-[#2B2926]/60">
          <span>
            Showing {offset + 1}–{offset + filteredLeads.length}
          </span>
          <div className="flex items-center gap-2">
            <button
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - limit))}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-md border border-[#2B2926]/10 text-[#2B2926] disabled:opacity-40 disabled:cursor-not-allowed hover:bg-[#2B2926]/5"
            >
              <ChevronLeft className="w-4 h-4" /> Prev
            </button>
            <button
              disabled={!hasMore}
              onClick={() => setOffset(offset + limit)}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-md border border-[#2B2926]/10 text-[#2B2926] disabled:opacity-40 disabled:cursor-not-allowed hover:bg-[#2B2926]/5"
            >
              Next <ChevronRight className="w-4 h-4" />
            </button>
            <button
              onClick={fetchLeads}
              title="Refresh"
              className="p-1.5 rounded-md text-[#2B2926]/60 hover:text-[#F55600] hover:bg-[#F55600]/5"
            >
              <RefreshCw className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}

      {/* Autonomous discovery modal */}
      {autoOpen && (
        <div className="fixed inset-0 z-50 flex">
          <div
            className="absolute inset-0 bg-[#2B2926]/40"
            onClick={() => !autoRunning && closeAutoModal()}
          />
          <div className="relative ml-auto h-full w-full max-w-md bg-white border-l border-[#2B2926]/10 shadow-2xl flex flex-col">
            <div className="flex items-center justify-between p-4 border-b border-[#2B2926]/10">
              <div className="flex items-center gap-2">
                <Sparkles className="w-5 h-5 text-[#F55600]" />
                <h2 className="text-lg font-semibold text-[#2B2926]">Autonomous Discover</h2>
              </div>
              <button
                onClick={closeAutoModal}
                className="p-1 rounded-md text-[#2B2926]/60 hover:text-[#2B2926] hover:bg-[#2B2926]/5"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="p-4 space-y-4 flex-1 overflow-y-auto">
              <div className="flex items-center justify-between text-sm">
                <span className="text-[#2B2926]/60">Target count</span>
                <span className="text-[#2B2926] font-medium">{autoTargetCount}</span>
              </div>
              <input
                type="range"
                min="20"
                max="500"
                step="10"
                value={autoTargetCount}
                disabled={autoRunning}
                onChange={(e) => setAutoTargetCount(Number(e.target.value))}
                className="w-full accent-[#F55600]"
              />

              <div className="rounded-lg bg-[#F55600]/5 border border-[#F55600]/20 p-4">
                <p className="text-xs text-[#2B2926]/60 uppercase tracking-wide">Leads found</p>
                <p className="text-3xl font-semibold text-[#F55600] mt-1">
                  {autoLeadsFound}
                </p>
                {autoTotal > 0 && (
                  <p className="text-xs text-[#2B2926]/60 mt-1">of {autoTotal} expected</p>
                )}
              </div>

              <div className="space-y-2">
                <p className="text-xs uppercase tracking-wide text-[#2B2926]/40">Sources</p>
                {SOURCES.map((src) => {
                  const count = autoSourceCounts[src.value] || autoSourceCounts[src.value.toLowerCase()] || 0;
                  const active = autoRunning && count === 0;
                  return (
                    <div key={src.value} className="flex items-center justify-between p-2 rounded-md border border-[#2B2926]/5 bg-white">
                      <div className="flex items-center gap-2 text-sm">
                        {active ? (
                          <Loader2 className="w-3 h-3 animate-spin text-[#F55600]" />
                        ) : count > 0 ? (
                          <span className="w-2 h-2 rounded-full bg-[#10B981]" />
                        ) : (
                          <span className="w-2 h-2 rounded-full bg-[#2B2926]/20" />
                        )}
                        <span className="text-[#2B2926]">{src.label}</span>
                      </div>
                      <span className="text-xs text-[#2B2926]/60">{count}</span>
                    </div>
                  );
                })}
              </div>

              {autoError && (
                <div className="rounded-md bg-[#F55600]/10 border border-[#F55600]/30 text-[#F55600] p-3 text-sm">
                  <AlertCircle className="w-4 h-4 inline mr-1" />
                  {autoError}
                </div>
              )}

              {autoLog.length > 0 && (
                <div className="rounded-md bg-[#2B2926]/[0.02] border border-[#2B2926]/5 p-3 max-h-40 overflow-y-auto">
                  <p className="text-[11px] uppercase tracking-wide text-[#2B2926]/40 mb-1">Log</p>
                  {autoLog.slice(-30).map((line, idx) => (
                    <p key={idx} className="text-xs text-[#2B2926]/60 font-mono">
                      {line}
                    </p>
                  ))}
                </div>
              )}
            </div>

            <div className="p-4 border-t border-[#2B2926]/10 flex items-center justify-end gap-2">
              <button
                onClick={closeAutoModal}
                className="px-4 py-2 text-sm rounded-lg border border-[#2B2926]/10 text-[#2B2926] hover:bg-[#2B2926]/5"
              >
                {autoRunning ? 'Stop' : 'Close'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Quick add modal */}
      {quickOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0 bg-[#2B2926]/40"
            onClick={() => setQuickOpen(false)}
          />
          <form
            onSubmit={submitQuickAdd}
            className="relative bg-white rounded-xl border border-[#2B2926]/10 shadow-2xl w-full max-w-md p-6 space-y-4"
          >
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold text-[#2B2926]">Quick Add Lead</h2>
              <button
                type="button"
                onClick={() => setQuickOpen(false)}
                className="p-1 rounded-md text-[#2B2926]/60 hover:text-[#2B2926] hover:bg-[#2B2926]/5"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div>
              <label className="block text-xs text-[#2B2926]/60 mb-1">Campaign *</label>
              <select
                required
                value={quickForm.campaign_id}
                onChange={(e) => setQuickForm({ ...quickForm, campaign_id: e.target.value })}
                className="w-full px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600] bg-white"
              >
                <option value="">Pick a campaign…</option>
                {campaigns.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name || c.title || `Campaign ${c.id}`}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-xs text-[#2B2926]/60 mb-1">Email *</label>
              <input
                required
                type="email"
                value={quickForm.email}
                onChange={(e) => setQuickForm({ ...quickForm, email: e.target.value })}
                className="w-full px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600]"
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-[#2B2926]/60 mb-1">First name</label>
                <input
                  type="text"
                  value={quickForm.first_name}
                  onChange={(e) => setQuickForm({ ...quickForm, first_name: e.target.value })}
                  className="w-full px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600]"
                />
              </div>
              <div>
                <label className="block text-xs text-[#2B2926]/60 mb-1">Last name</label>
                <input
                  type="text"
                  value={quickForm.last_name}
                  onChange={(e) => setQuickForm({ ...quickForm, last_name: e.target.value })}
                  className="w-full px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600]"
                />
              </div>
            </div>

            <div>
              <label className="block text-xs text-[#2B2926]/60 mb-1">Role / Title</label>
              <input
                type="text"
                value={quickForm.role}
                onChange={(e) => setQuickForm({ ...quickForm, role: e.target.value })}
                className="w-full px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600]"
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs text-[#2B2926]/60 mb-1">Company</label>
                <input
                  type="text"
                  value={quickForm.company_name}
                  onChange={(e) => setQuickForm({ ...quickForm, company_name: e.target.value })}
                  className="w-full px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600]"
                />
              </div>
              <div>
                <label className="block text-xs text-[#2B2926]/60 mb-1">Domain</label>
                <input
                  type="text"
                  placeholder="acme.com"
                  value={quickForm.company_domain}
                  onChange={(e) => setQuickForm({ ...quickForm, company_domain: e.target.value })}
                  className="w-full px-3 py-2 rounded-lg border border-[#2B2926]/10 text-sm focus:outline-none focus:border-[#F55600]"
                />
              </div>
            </div>

            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setQuickOpen(false)}
                className="px-4 py-2 text-sm rounded-lg border border-[#2B2926]/10 text-[#2B2926] hover:bg-[#2B2926]/5"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={quickSubmitting}
                className="inline-flex items-center gap-2 px-4 py-2 text-sm rounded-lg bg-[#F55600] text-white hover:bg-[#F55600]/90 disabled:opacity-50"
              >
                {quickSubmitting && <Loader2 className="w-4 h-4 animate-spin" />}
                Add Lead
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Lead detail drawer */}
      {detail && (
        <div className="fixed inset-0 z-40 flex">
          <div
            className="absolute inset-0 bg-[#2B2926]/40"
            onClick={() => setDetail(null)}
          />
          <div className="relative ml-auto h-full w-full max-w-md bg-white border-l border-[#2B2926]/10 shadow-2xl flex flex-col">
            <div className="flex items-center justify-between p-4 border-b border-[#2B2926]/10">
              <h2 className="text-lg font-semibold text-[#2B2926]">Lead detail</h2>
              <button
                onClick={() => setDetail(null)}
                className="p-1 rounded-md text-[#2B2926]/60 hover:text-[#2B2926] hover:bg-[#2B2926]/5"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-4 flex-1 overflow-y-auto space-y-3">
              {detail.loading ? (
                <div className="flex items-center text-[#2B2926]/60">
                  <Loader2 className="w-4 h-4 animate-spin mr-2" />
                  Loading…
                </div>
              ) : detail.error ? (
                <div className="text-[#F55600] text-sm">{detail.error}</div>
              ) : (
                <>
                  <div>
                    <p className="text-xs text-[#2B2926]/40 uppercase tracking-wide">Name</p>
                    <p className="text-[#2B2926] font-medium">{fullName(detail.lead)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-[#2B2926]/40 uppercase tracking-wide">Email</p>
                    <p className="text-[#2B2926]">{detail.lead?.global_lead?.email || '—'}</p>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <p className="text-xs text-[#2B2926]/40 uppercase tracking-wide">Role</p>
                      <p className="text-[#2B2926]">{detail.lead?.global_lead?.role || '—'}</p>
                    </div>
                    <div>
                      <p className="text-xs text-[#2B2926]/40 uppercase tracking-wide">Company</p>
                      <p className="text-[#2B2926]">{detail.lead?.global_lead?.company_name || '—'}</p>
                    </div>
                  </div>
                  <div>
                    <p className="text-xs text-[#2B2926]/40 uppercase tracking-wide">ICP Score</p>
                    <span className={`inline-flex px-2 py-0.5 rounded-md text-xs font-semibold ${scoreColor(detail.lead?.icp_score)}`}>
                      {detail.lead?.icp_score ?? '—'}
                    </span>
                  </div>
                  <div>
                    <p className="text-xs text-[#2B2926]/40 uppercase tracking-wide">Status</p>
                    <span className={`inline-flex px-2 py-0.5 rounded-md text-xs font-medium ${STATUS_STYLES[detail.lead?.status] || 'bg-[#2B2926]/5 text-[#2B2926]/60'}`}>
                      {detail.lead?.status || 'new'}
                    </span>
                  </div>
                  {detail.lead?.signals && Object.keys(detail.lead.signals).length > 0 && (
                    <div>
                      <p className="text-xs text-[#2B2926]/40 uppercase tracking-wide mb-1">Signals</p>
                      <pre className="text-xs text-[#2B2926]/70 bg-[#2B2926]/[0.03] rounded-md p-3 whitespace-pre-wrap break-words font-mono">
                        {JSON.stringify(detail.lead.signals, null, 2)}
                      </pre>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default NexusLeads;
