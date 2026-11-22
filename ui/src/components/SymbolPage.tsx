import { useState } from 'react';
import { findDefinition, getCallers, getCallees, getImpact, type Symbol, type CallerCalleeResult, type ImpactResponse } from '../api';

// Symbol explorer: enter symbol name, show definition, callers, callees, impact list
export default function SymbolPage({ repoId }: { repoId?: number }) {
  const [query, setQuery] = useState('');
  const [symbol, setSymbol] = useState<Symbol | null>(null);
  const [callers, setCallers] = useState<CallerCalleeResult[]>([]);
  const [callees, setCallees] = useState<CallerCalleeResult[]>([]);
  const [impact, setImpact] = useState<ImpactResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'definition' | 'callers' | 'callees' | 'impact'>('definition');

  const handleFind = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || !repoId) return;
    setLoading(true);
    setError(null);
    setSymbol(null);
    setCallers([]);
    setCallees([]);
    setImpact(null);
    try {
      const def = await findDefinition({ name: query.trim(), repository_id: repoId });
      if (def.symbols.length === 0) throw new Error(`No symbol found matching "${query}"`);
      const sym = def.symbols[0];
      setSymbol(sym);
      // Load graph data in parallel
      const [c, ca, i] = await Promise.all([
        getCallers(sym.id),
        getCallees(sym.id),
        getImpact(sym.id),
      ]);
      setCallers(c.callers);
      setCallees(ca.callees);
      setImpact(i);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Lookup failed');
    } finally {
      setLoading(false);
    }
  };

  const handleResultClick = (sym: Symbol) => {
    setQuery(sym.qualified_name);
    handleFind(new Event('submit'));
  };

  return (
    <div>
      <form onSubmit={handleFind} className="section">
        <h3 className="section-title">Find Symbol</h3>
        <div className="form-group">
          <label htmlFor="symbol-query">Symbol name</label>
          <input
            id="symbol-query"
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g., create_order, UserService, handle_request"
            disabled={loading}
          />
          <button type="submit" className="btn btn-primary" disabled={loading || !query.trim() || !repoId}>
            {loading ? <><span className="spinner" /> Finding...</> : 'Find'}
          </button>
        </div>
      </form>

      {error && <div className="error" role="alert">{error}</div>}

      {symbol && (
        <div className="section">
          <h3 className="section-title">{symbol.qualified_name} <span className="result-kind">{symbol.kind}</span></h3>
          <div style={{ fontSize: '13px', color: 'var(--text-muted)', marginBottom: '8px' }}>
            {symbol.file_path}:{symbol.start_line}–{symbol.end_line}
          </div>
          {symbol.signature && (
            <pre className="code-snippet">{symbol.signature}</pre>
          )}
          {symbol.docstring && (
            <div style={{ marginTop: '8px', lineHeight: 1.6 }}>{symbol.docstring}</div>
          )}

          <div style={{ display: 'flex', gap: '8px', marginTop: '12px', borderBottom: '1px solid var(--border)', paddingBottom: '8px' }}>
            {(['definition', 'callers', 'callees', 'impact'] as const).map((tab) => (
              <button
                key={tab}
                className={`btn ${activeTab === tab ? 'btn-primary' : ''}`}
                onClick={() => setActiveTab(tab)}
                style={{ padding: '6px 12px', fontSize: '12px' }}
              >
                {tab.charAt(0).toUpperCase() + tab.slice(1)}
                {tab === 'callers' && ` (${callers.length})`}
                {tab === 'callees' && ` (${callees.length})`}
                {tab === 'impact' && impact && ` (${impact.total_callers + impact.total_callees})`}
              </button>
            ))}
          </div>

          {activeTab === 'definition' && (
            <div className="empty-state">Symbol definition shown above.</div>
          )}

          {activeTab === 'callers' && (
            <CallTree
              title="Callers (who calls this)"
              items={callers}
              onItemClick={handleResultClick}
              emptyMessage="No callers found."
            />
          )}

          {activeTab === 'callees' && (
            <CallTree
              title="Callees (what this calls)"
              items={callees}
              onItemClick={handleResultClick}
              emptyMessage="No callees found."
            />
          )}

          {activeTab === 'impact' && impact && (
            <ImpactView impact={impact} onItemClick={handleResultClick} />
          )}
        </div>
      )}

      {!symbol && !loading && !error && (
        <div className="section empty-state">Enter a symbol name above to explore its call graph.</div>
      )}
    </div>
  );
}

// Indented call tree list for callers/callees
function CallTree({
  title,
  items,
  onItemClick,
  emptyMessage,
}: {
  title: string;
  items: CallerCalleeResult[];
  onItemClick: (sym: Symbol) => void;
  emptyMessage: string;
}) {
  if (items.length === 0) return <div className="empty-state">{emptyMessage}</div>;

  return (
    <div className="call-tree">
      <div style={{ fontWeight: 600, marginBottom: '8px', color: 'var(--text-muted)', fontSize: '12px', textTransform: 'uppercase' }}>
        {title}
      </div>
      {items.map((item) => (
        <CallTreeItem key={`${item.symbol.id}-${item.depth}`} item={item} onClick={onItemClick} />
      ))}
    </div>
  );
}

function CallTreeItem({ item, onClick }: { item: CallerCalleeResult; onClick: (sym: Symbol) => void }) {
  const depthClass = `call-tree-depth-${Math.min(item.depth, 5)}`;
  return (
    <div className={`call-tree-item ${depthClass}`} onClick={() => onClick(item.symbol)} style={{ cursor: 'pointer' }}>
      <span className="call-tree-indent">{'│  '.repeat(item.depth - 1)}└─</span>
      <span className="call-tree-name">{item.symbol.name}</span>
      <span className="call-tree-confidence">({(item.confidence * 100).toFixed(0)}%)</span>
      <span style={{ marginLeft: 'auto', fontSize: '11px', color: 'var(--text-muted)' }}>
        {item.symbol.file_path}:{item.symbol.start_line}
      </span>
    </div>
  );
}

// Impact view: combined callers + callees
function ImpactView({ impact, onItemClick }: { impact: ImpactResponse; onItemClick: (sym: Symbol) => void }) {
  return (
    <div>
      <div className="call-tree">
        <div style={{ fontWeight: 600, marginBottom: '8px', color: 'var(--text-muted)', fontSize: '12px', textTransform: 'uppercase' }}>
          Callers ({impact.total_callers})
        </div>
        {impact.callers.length === 0 ? (
          <div className="empty-state" style={{ padding: '16px' }}>No callers found.</div>
        ) : (
          impact.callers.map((item) => (
            <CallTreeItem key={item.symbol.id} item={item} onClick={onItemClick} />
          ))
        )}
      </div>
      <div className="call-tree" style={{ marginTop: '16px' }}>
        <div style={{ fontWeight: 600, marginBottom: '8px', color: 'var(--text-muted)', fontSize: '12px', textTransform: 'uppercase' }}>
          Callees ({impact.total_callees})
        </div>
        {impact.callees.length === 0 ? (
          <div className="empty-state" style={{ padding: '16px' }}>No callees found.</div>
        ) : (
          impact.callees.map((item) => (
            <CallTreeItem key={item.symbol.id} item={item} onClick={onItemClick} />
          ))
        )}
      </div>
    </div>
  );
}