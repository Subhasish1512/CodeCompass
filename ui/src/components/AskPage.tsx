import { useState } from 'react';
import { askQuestion, type AskResponse, type SearchResult } from '../api';

// Ask page: question input, answer with clickable citations
export default function AskPage({ repoId }: { repoId?: number }) {
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [openCitation, setOpenCitation] = useState<number | null>(null);

  const handleAsk = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!question.trim() || !repoId) return;
    setLoading(true);
    setError(null);
    setAnswer(null);
    setOpenCitation(null);
    try {
      const res = await askQuestion({ question: question.trim(), repository_id: repoId, max_context: 10 });
      setAnswer(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Question failed');
    } finally {
      setLoading(false);
    }
  };

  // Render answer text with clickable citation markers [1], [2], etc.
  function renderAnswer(text: string, citations: SearchResult[]) {
    const parts = text.split(/(\[\d+\])/);
    return (
      <div className="answer">
        {parts.map((part, i) => {
          const match = part.match(/^\[(\d+)\]$/);
          if (match) {
            const idx = parseInt(match[1], 10) - 1;
            return (
              <CitationMarker
                key={i}
                index={idx + 1}
                citation={citations[idx]}
                isOpen={openCitation === idx}
                onClick={() => setOpenCitation(openCitation === idx ? null : idx)}
              />
            );
          }
          return <span key={i}>{part}</span>;
        })}
      </div>
    );
  }

  return (
    <div>
      <form onSubmit={handleAsk} className="section">
        <h3 className="section-title">Ask a Question</h3>
        <div className="form-group" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
          <label htmlFor="ask-question">Question</label>
          <textarea
            id="ask-question"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="e.g., How does the order creation flow work? What does UserService.authenticate do?"
            disabled={loading}
            rows={3}
          />
          <button type="submit" className="btn btn-primary" disabled={loading || !question.trim() || !repoId} style={{ alignSelf: 'flex-start' }}>
            {loading ? <><span className="spinner" /> Thinking...</> : 'Ask'}
          </button>
        </div>
      </form>

      {error && <div className="error" role="alert">{error}</div>}

      {answer && (
        <div className="section">
          <h3 className="section-title">Answer <span style={{ fontWeight: 'normal', color: 'var(--text-muted)', fontSize: '13px' }}>({answer.took_ms}ms)</span></h3>
          <div style={{ background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '16px' }}>
            {renderAnswer(answer.answer, answer.citations)}
          </div>

          {answer.citations.length > 0 && (
            <div style={{ marginTop: '16px' }}>
              <h4 style={{ margin: '0 0 12px', fontSize: '13px', color: 'var(--text-muted)', textTransform: 'uppercase' }}>
                Citations ({answer.citations.length})
              </h4>
              {answer.citations.map((c, i) => (
                <CitationDetail
                  key={c.symbol.id}
                  index={i + 1}
                  citation={c}
                  isOpen={openCitation === i}
                  onToggle={() => setOpenCitation(openCitation === i ? null : i)}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {!answer && !loading && !error && (
        <div className="section empty-state">Ask a question about the codebase above.</div>
      )}
    </div>
  );
}

// Clickable citation marker [n]
function CitationMarker({ index, citation, isOpen, onClick }: {
  index: number;
  citation?: SearchResult;
  isOpen: boolean;
  onClick: () => void;
}) {
  return (
    <span
      className="citation"
      onClick={onClick}
      title={citation ? `${citation.symbol.file_path}:${citation.symbol.start_line}–${citation.symbol.end_line}` : ''}
      style={{ background: isOpen ? 'var(--accent)' : 'var(--bg-elevated)', borderColor: isOpen ? 'var(--accent)' : 'var(--border)', color: isOpen ? 'white' : 'var(--text)' }}
    >
      <span className="citation-number">[{index}]</span>
    </span>
  );
}

// Expandable citation detail with snippet
function CitationDetail({ index, citation, isOpen, onToggle }: {
  index: number;
  citation: SearchResult;
  isOpen: boolean;
  onToggle: () => void;
}) {
  const { symbol, snippet, search_type } = citation;
  return (
    <div className="citation-detail">
      <div className="citation-detail-header">
        <button className="btn" style={{ padding: '4px 8px', fontSize: '12px' }} onClick={onToggle}>
          {isOpen ? 'Hide' : 'Show'} details
        </button>
        <span className="citation-detail-name">[{index}] {symbol.name}</span>
        <span className="result-kind">{symbol.kind}</span>
        <span className="citation-detail-path">{symbol.file_path}:{symbol.start_line}–{symbol.end_line}</span>
        <span style={{ marginLeft: 'auto', fontSize: '11px', color: 'var(--text-muted)' }}>{search_type}</span>
      </div>
      {isOpen && snippet && (
        <pre className="citation-detail-snippet">{snippet}</pre>
      )}
    </div>
  );
}