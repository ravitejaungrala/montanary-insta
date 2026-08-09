/**
 * NexusAllLeads — workspace-wide leads view (legacy label: "All Leads").
 *
 * Distinct from NexusLeads.jsx (campaign-scoped). This page lists every
 * global lead in the workspace, with filter by status / channel / source.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  Building2,
  CheckCircle2,
  Inbox,
  Loader2,
  Mail,
  RefreshCw,
  Search,
  Shield,
  Users,
} from 'lucide-react';

const STATUS_PILL = {
  new: 'bg-[#F55600]/10 text-[#F55600]',
  contacted: 'bg-[#2B2926]/5 text-[#2B2926]/60',
  replied: 'bg-[#10B981]/15 text-[#10B981]',
  demo_scheduled: 'bg-[#10B981]/15 text-[#10B981]',
  unsubscribed: 'bg-[#2B2926]/5 text-[#2B2926]/40',
  bounced: 'bg-[#2B2926]/5 text-[#2B2926]/40',
};

const NexusAllLeads = ({ authAxios, apiBase, user, setMessage }) => {
  const [leads, setLeads] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');

  const loadLeads = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      // Reuse the journey/leads endpoint (returns workspace-wide list).
      const res = await authAxios.get(`${apiBase}/journey/leads`, {
        params: { view: 'active' },
      });
      setLeads(res.data?.leads || []);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  }, [authAxios, apiBase]);

  useEffect(() => {
    loadLeads();
  }, [loadLeads]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return leads.filter((l) => {
      if (statusFilter !== 'all' && l.status !== statusFilter) return false;
      if (!q) return true;
      return [l.name, l.email, l.company, l.company_domain, l.job_title]
        .filter(Boolean)
        .some((v) => String(v).toLowerCase().includes(q));
    });
  }, [leads, search, statusFilter]);

  const statusCounts = useMemo(() => {
    const counts = {};
    for (const l of leads) {
      counts[l.status] = (counts[l.status] || 0) + 1;
    }
    return counts;
  }, [leads]);

  return (
    <div className="bg-white">
      <div className="px-5 py-4 border-b border-[#2B2926]/10 flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-lg font-black text-[#2B2926] tracking-tight">All Leads</h1>
          <p className="text-xs text-[#2B2926]/60 mt-0.5">
            <span className="font-bold text-[#2B2926]">{leads.length}</span> total
            {statusCounts.replied ? ` · ${statusCounts.replied} replied` : ''}
            {statusCounts.demo_scheduled ? ` · ${statusCounts.demo_scheduled} demos` : ''}
          </p>
        </div>
        <button
          type="button"
          onClick={loadLeads}
          disabled={loading}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#2B2926]/10 text-xs font-bold text-[#2B2926] hover:bg-[#F55600]/5 disabled:opacity-50"
        >
          <RefreshCw className={['w-3.5 h-3.5', loading ? 'animate-spin' : ''].join(' ')} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="mx-5 mt-3 px-3 py-2 rounded-lg bg-[#F55600]/10 border border-[#F55600]/30 inline-flex items-center gap-2 text-xs text-[#F55600]">
          <AlertCircle className="w-3.5 h-3.5" />
          {error}
        </div>
      )}

      {/* Filters */}
      <div className="px-5 py-3 border-b border-[#2B2926]/10 flex items-center gap-2 flex-wrap">
        <div className="relative flex-1 min-w-[200px] max-w-md">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[#2B2926]/40" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search name, email, company…"
            className="w-full pl-7 pr-3 py-1.5 rounded-lg border border-[#2B2926]/10 text-xs focus:outline-none focus:border-[#F55600]"
          />
        </div>
        <div className="flex items-center gap-1">
          {['all', 'new', 'contacted', 'replied', 'demo_scheduled', 'unsubscribed', 'bounced'].map(
            (s) => (
              <button
                key={s}
                type="button"
                onClick={() => setStatusFilter(s)}
                className={[
                  'px-2.5 py-1 rounded-lg text-[10px] font-bold transition-all',
                  statusFilter === s
                    ? 'bg-[#F55600] text-white'
                    : 'bg-white text-[#2B2926]/60 border border-[#2B2926]/10 hover:bg-[#F55600]/5',
                ].join(' ')}
              >
                {s === 'all' ? 'All' : s.replace('_', ' ')}
                {statusCounts[s] ? ` (${statusCounts[s]})` : ''}
              </button>
            ),
          )}
        </div>
      </div>

      {/* Lead grid */}
      <div className="p-5">
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-[#2B2926]/60">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading leads…
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-12">
            <Inbox className="w-10 h-10 mx-auto text-[#2B2926]/15 mb-2" />
            <p className="text-xs text-[#2B2926]/60">
              {search || statusFilter !== 'all'
                ? 'No leads match your filters.'
                : 'No leads yet — add a campaign to start discovering.'}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3">
            {filtered.map((lead) => {
              const status = lead.status || 'new';
              return (
                <div
                  key={lead._id}
                  className="border border-[#2B2926]/10 rounded-lg p-3 hover:bg-[#F55600]/5 transition-all"
                >
                  <div className="flex items-start gap-3">
                    <div
                      className={[
                        'w-9 h-9 rounded-lg flex items-center justify-center text-[11px] font-bold shrink-0',
                        status === 'replied' || status === 'demo_scheduled'
                          ? 'bg-[#10B981]/15 text-[#10B981]'
                          : 'bg-[#F55600]/10 text-[#F55600]',
                      ].join(' ')}
                    >
                      {(lead.name || lead.email || '?').charAt(0).toUpperCase()}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1 justify-between">
                        <span className="text-sm font-bold text-[#2B2926] truncate">
                          {lead.name || lead.email || 'Unknown'}
                        </span>
                        {(lead.email_verify_status === 'verified' || lead.email_verified) && (
                          <Shield className="w-3 h-3 text-[#10B981] shrink-0" title="Email verified" />
                        )}
                      </div>
                      {lead.job_title && (
                        <div className="text-[10px] text-[#2B2926]/60 truncate">{lead.job_title}</div>
                      )}
                      {(lead.company || lead.company_domain) && (
                        <div className="text-[10px] text-[#2B2926]/60 inline-flex items-center gap-1 mt-0.5">
                          <Building2 className="w-2.5 h-2.5" />
                          {lead.company || lead.company_domain}
                        </div>
                      )}
                      {lead.email && (
                        <div className="text-[10px] text-[#2B2926]/40 truncate inline-flex items-center gap-1 mt-0.5">
                          <Mail className="w-2.5 h-2.5" />
                          {lead.email}
                        </div>
                      )}
                      <div className="mt-2 flex items-center justify-between gap-2">
                        <span
                          className={[
                            'inline-flex px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider',
                            STATUS_PILL[status] || 'bg-[#2B2926]/5 text-[#2B2926]/60',
                          ].join(' ')}
                        >
                          {status.replace('_', ' ')}
                        </span>
                        {lead.attempt_count_total > 0 && (
                          <span className="text-[10px] text-[#2B2926]/40">
                            {lead.attempt_count_total} touch{lead.attempt_count_total === 1 ? '' : 'es'}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

export default NexusAllLeads;
