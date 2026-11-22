// API client with typed responses for CodeCompass FastAPI endpoints

const API_BASE = '/api/v1';

// ---- Types ----

export interface HealthResponse {
  status: string;
  repository_count: number;
  symbol_count: number;
  edge_count: number;
}

export interface Repository {
  id: number;
  name: string;
  url: string | null;
  root_path: string;
  last_indexed_commit: string | null;
  created_at: string;
}

export interface Symbol {
  id: number;
  file_id: number;
  file_path: string;
  name: string;
  qualified_name: string;
  kind: string;
  start_line: number;
  end_line: number;
  signature: string | null;
  docstring: string | null;
  repo_id: number;
}

export interface SearchResult {
  symbol: Symbol;
  bm25_rank: number | null;
  vector_rank: number | null;
  fused_score: number;
  search_type: string;
  snippet: string | null;
}

export interface SearchResponse {
  results: SearchResult[];
  total: number;
  query: string;
  mode: string;
  took_ms: number;
}

export interface CallerCalleeResult {
  symbol: Symbol;
  depth: number;
  confidence: number;
  path: number[];
}

export interface CallersResponse {
  callers: CallerCalleeResult[];
  total: number;
  symbol_id: number;
  took_ms: number;
}

export interface CalleesResponse {
  callees: CallerCalleeResult[];
  total: number;
  symbol_id: number;
  took_ms: number;
}

export interface ImpactResponse {
  callers: CallerCalleeResult[];
  callees: CallerCalleeResult[];
  total_callers: number;
  total_callees: number;
  symbol_id: number;
  took_ms: number;
}

export interface DefinitionResponse {
  symbols: Symbol[];
  total: number;
  took_ms: number;
}

export interface AskResponse {
  answer: string;
  citations: SearchResult[];
  took_ms: number;
}

// ---- HTTP Helper ----

async function fetchJson<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${endpoint}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

// ---- Endpoints ----

export async function getHealth(): Promise<HealthResponse> {
  return fetchJson<HealthResponse>('/health');
}

export async function getRepositories(): Promise<Repository[]> {
  return fetchJson<Repository[]>('/repositories');
}

export async function searchCode(params: {
  query: string;
  repository_id?: number;
  mode?: 'bm25' | 'vector' | 'hybrid' | 'hybrid+graph';
  limit?: number;
  min_confidence?: number;
}): Promise<SearchResponse> {
  const sp = new URLSearchParams();
  sp.set('query', params.query);
  if (params.repository_id) sp.set('repository_id', String(params.repository_id));
  if (params.mode) sp.set('mode', params.mode);
  if (params.limit) sp.set('limit', String(params.limit));
  if (params.min_confidence) sp.set('min_confidence', String(params.min_confidence));
  return fetchJson<SearchResponse>(`/search?${sp.toString()}`);
}

export async function findDefinition(params: {
  name: string;
  repository_id?: number;
  kind?: string;
}): Promise<DefinitionResponse> {
  const sp = new URLSearchParams();
  sp.set('name', params.name);
  if (params.repository_id) sp.set('repository_id', String(params.repository_id));
  if (params.kind) sp.set('kind', params.kind);
  return fetchJson<DefinitionResponse>(`/symbols/definition?${sp.toString()}`);
}

export async function getCallers(symbolId: number, depth = 3, minConfidence = 0.0): Promise<CallersResponse> {
  return fetchJson<CallersResponse>(`/symbols/${symbolId}/callers?depth=${depth}&min_confidence=${minConfidence}`);
}

export async function getCallees(symbolId: number, depth = 3, minConfidence = 0.0): Promise<CalleesResponse> {
  return fetchJson<CalleesResponse>(`/symbols/${symbolId}/callees?depth=${depth}&min_confidence=${minConfidence}`);
}

export async function getImpact(symbolId: number, depth = 5, minConfidence = 0.0): Promise<ImpactResponse> {
  return fetchJson<ImpactResponse>(`/symbols/${symbolId}/impact?depth=${depth}&min_confidence=${minConfidence}`);
}

export async function askQuestion(params: {
  question: string;
  repository_id?: number;
  max_context?: number;
}): Promise<AskResponse> {
  return fetchJson<AskResponse>('/ask', {
    method: 'POST',
    body: JSON.stringify(params),
  });
}