import { useState, useEffect } from 'react';
import { getHealth, getRepositories, type Repository } from './api';
import SearchPage from './components/SearchPage';
import SymbolPage from './components/SymbolPage';
import AskPage from './components/AskPage';

type Page = 'search' | 'symbol' | 'ask';

function App() {
  const [page, setPage] = useState<Page>('search');
  const [repos, setRepos] = useState<Repository[]>([]);
  const [selectedRepo, setSelectedRepo] = useState<number | undefined>(undefined);
  const [healthy, setHealthy] = useState(false);
  const [loading, setLoading] = useState(true);

  // Load health and repositories on mount
  useEffect(() => {
    async function init() {
      try {
        const health = await getHealth();
        setHealthy(health.status === 'ok');
        const repos = await getRepositories();
        setRepos(repos);
        if (repos.length > 0) setSelectedRepo(repos[0].id);
      } catch {
        setHealthy(false);
      } finally {
        setLoading(false);
      }
    }
    init();
  }, []);

  if (loading) {
    return (
      <div className="app">
        <div className="main" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '60vh' }}>
          <div className="loading"><span className="spinner" /> Loading...</div>
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="header">
        <h1>CodeCompass</h1>
        <nav className="nav">
          <button className={page === 'search' ? 'active' : ''} onClick={() => setPage('search')}>Search</button>
          <button className={page === 'symbol' ? 'active' : ''} onClick={() => setPage('symbol')}>Symbol Explorer</button>
          <button className={page === 'ask' ? 'active' : ''} onClick={() => setPage('ask')}>Ask</button>
        </nav>
      </header>
      <main className="main">
        <div className="page">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
            <h2 className="page-title">{page === 'search' ? 'Search' : page === 'symbol' ? 'Symbol Explorer' : 'Ask'}</h2>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
              <span style={{ fontSize: '12px', color: healthy ? 'var(--success)' : 'var(--error)' }}>
                {healthy ? '● Connected' : '● Disconnected'}
              </span>
              <select
                value={selectedRepo ?? ''}
                onChange={(e) => setSelectedRepo(e.target.value ? Number(e.target.value) : undefined)}
                disabled={repos.length === 0}
                style={{ minWidth: '180px' }}
              >
                <option value="">Select repository</option>
                {repos.map((r) => (
                  <option key={r.id} value={r.id}>{r.name}</option>
                ))}
              </select>
            </div>
          </div>

          {page === 'search' && <SearchPage repoId={selectedRepo} />}
          {page === 'symbol' && <SymbolPage repoId={selectedRepo} />}
          {page === 'ask' && <AskPage repoId={selectedRepo} />}
        </div>
      </main>
    </div>
  );
}

export default App;