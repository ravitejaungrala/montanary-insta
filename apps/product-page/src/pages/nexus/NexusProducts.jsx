/**
 * NexusProducts — products library + "What's your product?" wizard entry.
 *
 * Legacy parity: top-level Products page in GTM Journey.
 * State names match legacy:
 *   products, loading, error, productUrl, working, scrapePreview
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  ArrowLeft,
  Briefcase,
  CheckCircle2,
  ExternalLink,
  Globe,
  Loader2,
  Mail,
  PlayCircle,
  Plus,
  Send,
  Sparkles,
  Target,
  Users,
} from 'lucide-react';

const NexusProducts = ({ authAxios, apiBase, user, setMessage }) => {
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [productUrl, setProductUrl] = useState('');
  const [working, setWorking] = useState(false);
  const [scrapePreview, setScrapePreview] = useState(null);

  // Product detail view state
  const [selectedProductId, setSelectedProductId] = useState(null);
  const [detailTab, setDetailTab] = useState('runs');
  const [detailRuns, setDetailRuns] = useState([]);
  const [detailProspects, setDetailProspects] = useState([]);
  const [detailLoading, setDetailLoading] = useState(false);

  const selectedProduct = useMemo(
    () => products.find((p) => String(p.id || p._id) === String(selectedProductId)),
    [products, selectedProductId],
  );

  const loadProducts = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authAxios.get(`${apiBase}/products`);
      setProducts(res.data?.products || res.data || []);
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  }, [authAxios, apiBase]);

  useEffect(() => {
    loadProducts();
  }, [loadProducts]);

  const loadDetail = useCallback(
    async (productId) => {
      if (!productId) return;
      setDetailLoading(true);
      try {
        const [runsRes, prospectsRes] = await Promise.all([
          authAxios
            .get(`${apiBase}/campaigns`, { params: { product_id: productId } })
            .catch(() => ({ data: { campaigns: [] } })),
          authAxios
            .get(`${apiBase}/leads`, { params: { product_id: productId, limit: 100 } })
            .catch(() => ({ data: { leads: [] } })),
        ]);
        setDetailRuns(runsRes.data?.campaigns || runsRes.data || []);
        setDetailProspects(prospectsRes.data?.leads || prospectsRes.data || []);
      } catch (err) {
        setError(err?.response?.data?.detail || err.message);
      } finally {
        setDetailLoading(false);
      }
    },
    [authAxios, apiBase],
  );

  useEffect(() => {
    if (selectedProductId) loadDetail(selectedProductId);
  }, [selectedProductId, loadDetail]);

  const handleAnalyze = async () => {
    if (!productUrl) return;
    setWorking(true);
    setError('');
    try {
      const res = await authAxios.post(`${apiBase}/analyze`, { url: productUrl });
      setScrapePreview(res.data || null);
      await loadProducts();
      setProductUrl('');
    } catch (err) {
      setError(err?.response?.data?.detail || err.message);
    } finally {
      setWorking(false);
    }
  };

  // ── Product detail view ────────────────────────────────────────────────
  if (selectedProductId && selectedProduct) {
    const stats = {
      runs: detailRuns.length,
      prospects: detailProspects.length,
      sent: detailProspects.filter((l) => (l.status || '') === 'contacted' || (l.status || '') === 'replied' || (l.status || '') === 'demo_scheduled').length,
      replied: detailProspects.filter((l) => (l.status || '') === 'replied' || (l.status || '') === 'demo_scheduled').length,
    };
    const replyRate = stats.sent > 0 ? Math.round((stats.replied / stats.sent) * 100) : 0;

    return (
      <div className="bg-white">
        <div className="px-5 py-4 border-b border-[#2B2926]/10 flex items-center justify-between">
          <button
            type="button"
            onClick={() => setSelectedProductId(null)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-[#2B2926]/10 text-xs font-bold text-[#2B2926] hover:bg-[#F55600]/5"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            Back to Products
          </button>
        </div>

        {/* Product header card */}
        <div className="px-5 py-5 border-b border-[#2B2926]/10">
          <div className="flex items-start gap-4">
            <div className="w-14 h-14 rounded-xl bg-[#F55600]/10 text-[#F55600] flex items-center justify-center shrink-0">
              <Briefcase className="w-7 h-7" />
            </div>
            <div className="min-w-0 flex-1">
              <h2 className="text-xl font-black text-[#2B2926] tracking-tight">{selectedProduct.name || 'Unnamed product'}</h2>
              {selectedProduct.source_url && (
                <a
                  href={selectedProduct.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs text-[#2B2926]/60 inline-flex items-center gap-1 mt-0.5 hover:text-[#F55600]"
                >
                  <Globe className="w-3 h-3" />
                  {selectedProduct.source_url}
                  <ExternalLink className="w-3 h-3" />
                </a>
              )}
              {selectedProduct.value_proposition && (
                <p className="text-sm text-[#2B2926]/70 mt-2 leading-relaxed">
                  {selectedProduct.value_proposition}
                </p>
              )}
            </div>
          </div>

          {/* Stat pills */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4">
            <div className="border border-[#2B2926]/10 rounded-lg p-3">
              <div className="text-[10px] uppercase tracking-wider text-[#2B2926]/40 font-bold">Runs</div>
              <div className="text-lg font-black text-[#2B2926] mt-1">{stats.runs}</div>
            </div>
            <div className="border border-[#2B2926]/10 rounded-lg p-3">
              <div className="text-[10px] uppercase tracking-wider text-[#2B2926]/40 font-bold">Prospects</div>
              <div className="text-lg font-black text-[#2B2926] mt-1">{stats.prospects}</div>
            </div>
            <div className="border border-[#2B2926]/10 rounded-lg p-3">
              <div className="text-[10px] uppercase tracking-wider text-[#2B2926]/40 font-bold">Sent</div>
              <div className="text-lg font-black text-[#2B2926] mt-1">{stats.sent}</div>
            </div>
            <div className="border border-[#2B2926]/10 rounded-lg p-3">
              <div className="text-[10px] uppercase tracking-wider text-[#2B2926]/40 font-bold">Reply rate</div>
              <div className="text-lg font-black text-[#10B981] mt-1">{replyRate}%</div>
            </div>
          </div>
        </div>

        {/* Detail tabs */}
        <div className="px-5 py-3 border-b border-[#2B2926]/10 flex items-center gap-2">
          <button
            type="button"
            onClick={() => setDetailTab('runs')}
            className={[
              'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-all',
              detailTab === 'runs'
                ? 'bg-[#F55600] text-white'
                : 'text-[#2B2926]/60 border border-[#2B2926]/10 hover:bg-[#F55600]/5',
            ].join(' ')}
          >
            <PlayCircle className="w-3.5 h-3.5" />
            Runs ({stats.runs})
          </button>
          <button
            type="button"
            onClick={() => setDetailTab('prospects')}
            className={[
              'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-all',
              detailTab === 'prospects'
                ? 'bg-[#F55600] text-white'
                : 'text-[#2B2926]/60 border border-[#2B2926]/10 hover:bg-[#F55600]/5',
            ].join(' ')}
          >
            <Users className="w-3.5 h-3.5" />
            Prospects ({stats.prospects})
          </button>
        </div>

        <div className="p-5">
          {detailLoading ? (
            <div className="flex items-center gap-2 text-xs text-[#2B2926]/60">
              <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading…
            </div>
          ) : detailTab === 'runs' ? (
            detailRuns.length === 0 ? (
              <p className="text-xs text-[#2B2926]/40 italic">No runs for this product yet.</p>
            ) : (
              <div className="space-y-2">
                {detailRuns.map((r) => (
                  <div key={r.id || r._id} className="border border-[#2B2926]/10 rounded-lg p-3 hover:bg-[#F55600]/5">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-bold text-[#2B2926]">{r.name || 'Unnamed'}</span>
                      <span className="inline-flex px-1.5 py-0.5 rounded text-[9px] font-bold uppercase bg-[#2B2926]/5 text-[#2B2926]/60">
                        {r.status || 'draft'}
                      </span>
                    </div>
                    <div className="text-[10px] text-[#2B2926]/60 mt-1">
                      {r.created_at ? new Date(r.created_at).toLocaleDateString() : ''}
                    </div>
                  </div>
                ))}
              </div>
            )
          ) : detailProspects.length === 0 ? (
            <p className="text-xs text-[#2B2926]/40 italic">No prospects yet.</p>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              {detailProspects.slice(0, 60).map((lead) => {
                const g = lead.global_lead || lead;
                const name = `${g.first_name || ''} ${g.last_name || ''}`.trim() || g.name || g.email || 'Unknown';
                return (
                  <div key={lead.id || lead._id} className="border border-[#2B2926]/10 rounded-lg p-3">
                    <div className="flex items-center gap-2">
                      <div className="w-8 h-8 rounded-lg bg-[#F55600]/10 text-[#F55600] flex items-center justify-center text-xs font-bold shrink-0">
                        {name.charAt(0).toUpperCase()}
                      </div>
                      <div className="min-w-0">
                        <div className="text-sm font-bold text-[#2B2926] truncate">{name}</div>
                        <div className="text-[10px] text-[#2B2926]/60 truncate inline-flex items-center gap-1">
                          <Mail className="w-2.5 h-2.5" />
                          {g.email || '—'}
                        </div>
                      </div>
                    </div>
                    {(g.company_name || g.company) && (
                      <div className="text-[10px] text-[#2B2926]/60 mt-1.5">{g.company_name || g.company}</div>
                    )}
                    {g.status && (
                      <span className="inline-flex mt-2 px-1.5 py-0.5 rounded text-[9px] font-bold uppercase bg-[#2B2926]/5 text-[#2B2926]/60">
                        {g.status.replace('_', ' ')}
                      </span>
                    )}
                  </div>
                );
              })}
              {detailProspects.length > 60 && (
                <p className="text-[10px] text-[#2B2926]/40 col-span-full">
                  Showing first 60 of {detailProspects.length} prospects.
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    );
  }

  // ── Library view (default) ─────────────────────────────────────────────
  return (
    <div className="bg-white">
      <div className="px-5 py-4 border-b border-[#2B2926]/10">
        <h1 className="text-lg font-black text-[#2B2926] tracking-tight">Products</h1>
        <p className="text-xs text-[#2B2926]/60 mt-0.5">
          Paste your URL — we'll research your product and write the brief for you.
        </p>
      </div>

      {error && (
        <div className="mx-5 mt-3 px-3 py-2 rounded-lg bg-[#F55600]/10 border border-[#F55600]/30 inline-flex items-center gap-2 text-xs text-[#F55600]">
          <AlertCircle className="w-3.5 h-3.5" />
          {error}
        </div>
      )}

      {/* Hero input */}
      <div className="px-5 py-8 max-w-3xl mx-auto">
        <h2 className="text-2xl font-black text-[#2B2926] text-center">
          What's your product?
        </h2>
        <p className="text-xs text-[#2B2926]/60 text-center mt-2">
          Paste your URL below — we'll research your product and write the brief for you.
        </p>

        <div className="mt-6 flex items-center gap-2 px-3 py-2 rounded-xl border-2 border-[#F55600] bg-white">
          <Globe className="w-4 h-4 text-[#2B2926]/40" />
          <input
            type="text"
            value={productUrl}
            onChange={(e) => setProductUrl(e.target.value)}
            placeholder="https://yourproduct.com"
            className="flex-1 px-2 py-1 text-sm bg-transparent focus:outline-none"
            onKeyDown={(e) => e.key === 'Enter' && handleAnalyze()}
          />
          <button
            type="button"
            onClick={handleAnalyze}
            disabled={working || !productUrl}
            className="inline-flex items-center justify-center w-9 h-9 rounded-lg bg-[#F55600] text-white disabled:opacity-50 hover:opacity-90"
          >
            {working ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
          </button>
        </div>
        <p className="text-[10px] text-[#2B2926]/40 text-center mt-2">
          Takes ~15 seconds — we'll scrape your site and generate a summary.
        </p>

        {scrapePreview && (
          <div className="mt-4 p-3 rounded-lg bg-[#10B981]/5 border border-[#10B981]/30 text-xs text-[#2B2926]/70">
            <div className="inline-flex items-center gap-1 font-bold text-[#10B981]">
              <Sparkles className="w-3.5 h-3.5" />
              Analysis complete
            </div>
            <pre className="mt-2 text-[10px] whitespace-pre-wrap text-[#2B2926]/60 max-h-40 overflow-auto">
              {JSON.stringify(scrapePreview, null, 2).slice(0, 400)}
            </pre>
          </div>
        )}
      </div>

      {/* Product library */}
      <div className="px-5 py-4 border-t border-[#2B2926]/10">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-xs font-bold text-[#2B2926] uppercase tracking-wider">
            Your Products ({products.length})
          </h3>
        </div>
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-[#2B2926]/60">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading…
          </div>
        ) : products.length === 0 ? (
          <p className="text-xs text-[#2B2926]/40 italic">No products yet. Paste a URL above to get started.</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {products.map((p) => (
              <div
                key={p.id || p._id}
                onClick={() => setSelectedProductId(String(p.id || p._id))}
                className="border border-[#2B2926]/10 rounded-lg p-3 hover:bg-[#F55600]/5 transition-all cursor-pointer"
              >
                <div className="flex items-start gap-2">
                  <div className="w-8 h-8 rounded-lg bg-[#F55600]/10 text-[#F55600] flex items-center justify-center shrink-0">
                    <Briefcase className="w-4 h-4" />
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-bold text-[#2B2926] truncate">{p.name || 'Unnamed'}</div>
                    <div className="text-[10px] text-[#2B2926]/60 truncate">{p.source_url || ''}</div>
                    {p.value_proposition && (
                      <p className="text-[11px] text-[#2B2926]/70 mt-1 line-clamp-2">{p.value_proposition}</p>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default NexusProducts;
