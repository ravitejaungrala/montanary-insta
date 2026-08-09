/**
 * NexusReports — daily / weekly digest archive.
 *
 * Legacy label: "Reports". Distinct from Performance (AI insights) and
 * Analytics (raw metrics). Triggered by the daily_report.py background
 * job; users see their archive here.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertCircle,
  Calendar,
  Download,
  FileText,
  Loader2,
  Mail,
  RefreshCw,
  Send,
} from 'lucide-react';

const fmtDate = (iso) => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  } catch {
    return iso;
  }
};

const NexusReports = ({ authAxios, apiBase, user, setMessage }) => {
  const [reports, setReports] = useState([]);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');

  const loadReports = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      // Reports endpoint may not exist yet — fall back to empty.
      const res = await authAxios.get(`${apiBase}/reports`).catch(() => ({ data: { reports: [] } }));
      setReports(res.data?.reports || []);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  }, [authAxios, apiBase]);

  useEffect(() => {
    loadReports();
  }, [loadReports]);

  const sendDailyNow = async () => {
    setSending(true);
    try {
      // Manual trigger via scheduler endpoint.
      await authAxios.post(`${apiBase}/scheduler/daily-report`).catch(() => null);
      await loadReports();
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="bg-white">
      <div className="px-5 py-4 border-b border-[#2B2926]/10 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-black text-[#2B2926] tracking-tight">Reports</h1>
          <p className="text-xs text-[#2B2926]/60 mt-0.5">
            Daily + weekly performance digests delivered to your inbox.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={sendDailyNow}
            disabled={sending}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#2B2926]/10 text-xs font-bold text-[#2B2926] hover:bg-[#F55600]/5 disabled:opacity-50"
          >
            {sending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
            Send today's digest
          </button>
          <button
            type="button"
            onClick={loadReports}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#2B2926]/10 text-xs font-bold text-[#2B2926] hover:bg-[#F55600]/5 disabled:opacity-50"
          >
            <RefreshCw className={['w-3.5 h-3.5', loading ? 'animate-spin' : ''].join(' ')} />
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="mx-5 mt-3 px-3 py-2 rounded-lg bg-[#F55600]/10 border border-[#F55600]/30 inline-flex items-center gap-2 text-xs text-[#F55600]">
          <AlertCircle className="w-3.5 h-3.5" />
          {error}
        </div>
      )}

      <div className="p-5">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-5">
          <div className="border border-[#2B2926]/10 rounded-lg p-3">
            <div className="text-[10px] uppercase tracking-wider text-[#2B2926]/40 font-bold">Daily digest</div>
            <div className="text-sm font-bold text-[#2B2926] mt-1 inline-flex items-center gap-1.5">
              <Mail className="w-3.5 h-3.5 text-[#F55600]" />
              Sent at 08:00 local
            </div>
            <p className="text-[10px] text-[#2B2926]/60 mt-1">
              Yesterday's leads, replies, opens, demos.
            </p>
          </div>
          <div className="border border-[#2B2926]/10 rounded-lg p-3">
            <div className="text-[10px] uppercase tracking-wider text-[#2B2926]/40 font-bold">Weekly summary</div>
            <div className="text-sm font-bold text-[#2B2926] mt-1 inline-flex items-center gap-1.5">
              <Calendar className="w-3.5 h-3.5 text-[#F55600]" />
              Mondays 09:00 local
            </div>
            <p className="text-[10px] text-[#2B2926]/60 mt-1">
              Top wins, top stalls, week-over-week trends.
            </p>
          </div>
          <div className="border border-[#2B2926]/10 rounded-lg p-3">
            <div className="text-[10px] uppercase tracking-wider text-[#2B2926]/40 font-bold">Custom export</div>
            <div className="text-sm font-bold text-[#2B2926] mt-1 inline-flex items-center gap-1.5">
              <Download className="w-3.5 h-3.5 text-[#F55600]" />
              CSV / PDF
            </div>
            <p className="text-[10px] text-[#2B2926]/60 mt-1">
              Detailed per-lead workspace report.
            </p>
            <CustomReportDownload authAxios={authAxios} setMessage={setMessage} />
          </div>
        </div>

        {/* Archive */}
        <h3 className="text-xs font-bold text-[#2B2926] mb-2 uppercase tracking-wider">
          Past reports
        </h3>
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-[#2B2926]/60">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading…
          </div>
        ) : reports.length === 0 ? (
          <div className="text-center py-10 border border-[#2B2926]/10 border-dashed rounded-lg">
            <FileText className="w-8 h-8 mx-auto text-[#2B2926]/15 mb-2" />
            <p className="text-xs text-[#2B2926]/60">No past reports yet.</p>
            <p className="text-[10px] text-[#2B2926]/40 mt-1">
              Daily digests start arriving once daily_report job is wired.
            </p>
          </div>
        ) : (
          <div className="border border-[#2B2926]/10 rounded-lg divide-y divide-black/5">
            {reports.map((r) => (
              <div key={r.id || r._id} className="px-3 py-2 flex items-center justify-between gap-2">
                <div className="inline-flex items-center gap-2 text-xs">
                  <FileText className="w-3.5 h-3.5 text-[#F55600]" />
                  <span className="font-bold text-[#2B2926]">{fmtDate(r.period_start)}</span>
                  <span className="text-[#2B2926]/40">— {r.kind || 'daily'}</span>
                </div>
                <button
                  type="button"
                  className="text-[10px] font-bold text-[#F55600] hover:opacity-70"
                >
                  View
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

// ─────────────────────────────────────────────────────────────────────────────
// CustomReportDownload — format picker (CSV / PDF) + download button.
// Hits GET /nexus/reports/export?format=<fmt> with responseType=blob and
// triggers a browser download via the standard URL.createObjectURL pattern.
// ─────────────────────────────────────────────────────────────────────────────
function CustomReportDownload({ authAxios, setMessage }) {
  const [format, setFormat] = useState('csv');
  const [downloading, setDownloading] = useState(false);

  const download = useCallback(async () => {
    if (!authAxios) return;
    setDownloading(true);
    try {
      const res = await authAxios.get(`/nexus/reports/export?format=${format}`, {
        responseType: 'blob',
      });
      const blob = new Blob([res.data], {
        type: format === 'pdf' ? 'application/pdf' : 'text/csv;charset=utf-8',
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      const stamp = new Date().toISOString().slice(0, 10);
      a.href = url;
      a.download = `nexus-report-${stamp}.${format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setMessage && setMessage(`Downloaded nexus-report-${stamp}.${format}`);
    } catch (err) {
      setMessage &&
        setMessage(
          err?.response?.data?.detail || err?.message || 'Report download failed',
        );
    } finally {
      setDownloading(false);
    }
  }, [authAxios, format, setMessage]);

  return (
    <div className="mt-2.5 flex items-center gap-1.5">
      <select
        value={format}
        onChange={(e) => setFormat(e.target.value)}
        disabled={downloading}
        className="text-[11px] font-bold px-2 py-1 rounded border border-[#2B2926]/10 bg-white text-[#2B2926] focus:outline-none focus:border-[#F55600]"
      >
        <option value="csv">CSV</option>
        <option value="pdf">PDF</option>
      </select>
      <button
        type="button"
        onClick={download}
        disabled={downloading}
        className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-[#F55600] text-white text-[11px] font-black hover:opacity-90 disabled:opacity-50"
      >
        {downloading ? (
          <Loader2 className="w-3 h-3 animate-spin" />
        ) : (
          <Download className="w-3 h-3" />
        )}
        Download
      </button>
    </div>
  );
}

export default NexusReports;
