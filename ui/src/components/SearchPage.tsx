import { useState } from 'react';
import { searchCode, type SearchResponse, type SearchResult } from '../api';

// Search page: query input, mode selector, results list with snippets and score breakdown
export default function SearchPage({ repoId }: { repoId?: number }) {
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<'bm25' | 'vector' | 'hybrid' | 'hybrid+graph'>('hybrid+graph');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [tookMs, setTookMs] = useState(0);

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || !repoId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await searchCode({ query: query.trim(), repository_id: repoId, mode, limit: 20 });
      setResults(res.results);
      setTotal(res.total);
      setTookMs(res.took_ms);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Search failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <form onSubmit={handleSearch} className="section">
        <h3 className="section-title">Search Code</h3>
        <div className="form-group">
          <label htmlFor="search-query">Query</label>
          <input
            id="search-query"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g., create order, user authentication, error handling"
            disabled={loading}
          />
          <label htmlFor="search-mode" style={{ minWidth: 'auto' }}>Mode</label>
          <select
            id="search-mode"
            value={mode}
            onChange={(e) => setMode(e.target.value as typeof mode)}
            disabled={loading}
          >
            <option value="bm25">BM25 (keyword)</option>
            <option value="vector">Vector (semantic)</option>
            <option value="hybrid">Hybrid (RRF)</option>
            <option value="hybrid+graph">Hybrid + Graph</option>
          </select>
          <button type="submit" className="btn btn-primary" disabled={loading || !query.trim() || !repoId}>
            {loading ? <><span className="spinner" /> Searching...</> : 'Search'}
          </button>
        </div>
      </form>

      {error && <div className="error" role="alert">{error}</div>}

      <div className="section">
        <h3 className="section-title">
          Results
          {total > 0 && <span style={{ fontWeight: 'normal', color: 'var(--text-muted)', fontSize: '13px' }}>
            ({total} total, {tookMs}ms)
          </span>}
        </h3>

        {results.length === 0 && !loading && (
          <div className="empty-state">
            {query.trim() ? 'No results found. Try a different query or mode.' : 'Enter a query above to search.'}
          </div>
        )}

        <div className="results-list">
          {results.map((r, i) => (
            <ResultItem key={r.symbol.id} result={r} rank={i + 1} />
          ))}
        </div>
      </div>
    </div>
  );
}

// Individual result item with file path, lines, score breakdown, and code snippet
function ResultItem({ result, rank }: { result: SearchResult; rank: number }) {
  const { symbol, bm25_rank, vector_rank, fused_score, search_type, snippet } = result;
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="result-item">
      <div className="result-header">
        <span className="result-name">{symbol.name}</span>
        <span className="result-kind">{symbol.kind}</span>
        <span className="result-path">{symbol.file_path}</span>
        <span className="result-lines">{symbol.start_line}–{symbol.end_line}</span>
        <span style={{ marginLeft: 'auto', fontSize: '12px', color: 'var(--text-muted)' }}>
          #{rank} · {search_type}
        </span>
      </div>

      <div className="score-breakdown">
        {bm25_rank != null && (
          <span className="score-item">
            <span className="score-label">BM25 rank:</span>
            <span className="score-value">#{bm25_rank}</span>
          </span>
        )}
        {vector_rank != null && (
          <span className="score-item">
            <span className="score-label">Vector rank:</span>
            <span className="score-value">#{vector_rank}</span>
          </span>
        )}
        <span className="score-item">
          <span className="score-label">Fused:</span>
          <span className="score-value" style={{ color: 'var(--accent)' }}>{fused_score.toFixed(4)}</span>
        </span>
      </div>

      <button
        className="btn"
        style={{ padding: '4px 10px', fontSize: '12px' }}
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? 'Hide snippet' : 'Show snippet'}
      </button>

      {expanded && snippet && (
        <pre className="code-snippet">{snippet}</pre>
      )}
    </div>
  );
}