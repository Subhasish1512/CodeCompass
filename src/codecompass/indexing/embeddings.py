"""
Local embedding generation using sentence-transformers.

Generates vector embeddings for symbols and chunks.
Runs entirely locally - no external API calls.
"""

import hashlib
import json
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

from codecompass.config import get_settings

# Global model cache
_model = None


def get_embedding_model():
    """Lazy-load the sentence-transformers model."""
    global _model
    if _model is None:
        settings = get_settings()
        try:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer(
                settings.embedding_model,
                device="cpu",  # Use CPU by default
                cache_folder=os.path.join(os.getcwd(), ".cache", "embeddings")
            )
        except ImportError:
            raise RuntimeError(
                "sentence-transformers not installed. "
                "Install with: pip install sentence-transformers"
            )
    return _model


def compute_embedding(text: str) -> List[float]:
    """Compute embedding for a single text."""
    model = get_embedding_model()
    embedding = model.encode(text, normalize_embeddings=True)
    return embedding.tolist()


def compute_embeddings_batch(texts: List[str]) -> List[List[float]]:
    """Compute embeddings for multiple texts (batched for efficiency)."""
    model = get_embedding_model()
    settings = get_settings()
    embeddings = model.encode(
        texts,
        batch_size=settings.embedding_batch_size,
        normalize_embeddings=True,
        show_progress_bar=False
    )
    return embeddings.tolist()


def symbol_to_text(symbol: dict) -> str:
    """
    Convert a symbol to text for embedding.
    Combines name, signature, and docstring.
    """
    parts = [symbol.get("name", "")]
    if symbol.get("signature"):
        parts.append(symbol["signature"])
    if symbol.get("docstring"):
        parts.append(symbol["docstring"])
    return " ".join(parts)


def chunk_to_text(chunk: dict) -> str:
    """
    Convert a chunk to text for embedding.
    Prefixes with qualified name and signature for context.
    """
    parts = []
    if chunk.get("qualified_name"):
        parts.append(f"Symbol: {chunk['qualified_name']}")
    if chunk.get("signature"):
        parts.append(f"Signature: {chunk['signature']}")
    if chunk.get("text"):
        parts.append(chunk["text"])
    return " ".join(parts)


def embed_symbols(symbols: List[dict]) -> List[Tuple[int, List[float]]]:
    """
    Generate embeddings for a list of symbols.

    Returns list of (symbol_id, embedding) tuples.
    """
    if not symbols:
        return []

    texts = [symbol_to_text(s) for s in symbols]
    embeddings = compute_embeddings_batch(texts)

    return [(s["id"], emb) for s, emb in zip(symbols, embeddings)]


def embed_chunks(chunks: List[dict]) -> List[Tuple[int, List[float]]]:
    """
    Generate embeddings for a list of chunks.

    Returns list of (chunk_id, embedding) tuples.
    """
    if not chunks:
        return []

    texts = [chunk_to_text(c) for c in chunks]
    embeddings = compute_embeddings_batch(texts)

    return [(c["id"], emb) for c, emb in zip(chunks, embeddings)]

# ============================================================================
# CODE-AWARE TOKENIZER FOR BM25
# ============================================================================

# Regex patterns for code tokenization
CAMEL_CASE_RE = re.compile(r'(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')
SNAKE_CASE_RE = re.compile(r'_')
DOT_RE = re.compile(r'\.')


def code_tokenize(text: str) -> List[str]:
    """
    Tokenize code text with awareness of camelCase, snake_case, and dot notation.

    Splits:
    - camelCase: getUserName -> get, User, Name
    - snake_case: get_user_name -> get, user, name
    - dotted: module.Class.method -> module, Class, method

    Returns list of tokens (lowercased).
    """
    if not text:
        return []

    # Replace dots with spaces first
    text = DOT_RE.sub(' ', text)
    # Replace underscores with spaces
    text = SNAKE_CASE_RE.sub(' ', text)
    # Split camelCase
    text = CAMEL_CASE_RE.sub(' ', text)

    # Split on non-alphanumeric
    tokens = re.findall(r'[a-zA-Z0-9]+', text.lower())

    # Filter out very short tokens and common stopwords for code
    stopwords = {'the', 'and', 'or', 'for', 'in', 'to', 'a', 'an', 'of', 'is', 'as', 'by', 'on', 'with', 'from', 'get', 'set', 'has', 'can', 'use', 'new', 'old'}
    return [t for t in tokens if len(t) > 1 and t not in stopwords]


def build_code_tsvector(text: str) -> str:
    """
    Build a tsvector-compatible string with code-aware tokenization.

    This can be used to manually construct tsvectors or as input to to_tsvector.
    """
    tokens = code_tokenize(text)
    return ' '.join(tokens)


# ============================================================================
# DISK CACHE FOR EMBEDDINGS
# ============================================================================

class EmbeddingCache:
    """Disk-based cache for embeddings keyed by content hash."""

    def __init__(self, cache_dir: str = ".embeddings_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(self, text: str, model_name: str) -> str:
        """Generate cache key from text and model."""
        content = f"{model_name}:{text}"
        return hashlib.sha256(content.encode()).hexdigest()[:32]

    def get(self, text: str, model_name: str) -> Optional[List[float]]:
        """Get cached embedding if exists."""
        key = self._cache_key(text, model_name)
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            try:
                with open(cache_file) as f:
                    data = json.load(f)
                    return data["embedding"]
            except Exception:
                pass
        return None

    def set(self, text: str, model_name: str, embedding: List[float]) -> None:
        """Cache an embedding."""
        key = self._cache_key(text, model_name)
        cache_file = self.cache_dir / f"{key}.json"
        try:
            with open(cache_file, "w") as f:
                json.dump({"embedding": embedding, "model": model_name}, f)
        except Exception:
            pass

    def get_batch(self, texts: List[str], model_name: str) -> Tuple[List[Optional[List[float]]], List[int]]:
        """
        Get cached embeddings for a batch.

        Returns: (embeddings list with None for misses, list of miss indices)
        """
        results = []
        misses = []
        for i, text in enumerate(texts):
            emb = self.get(text, model_name)
            results.append(emb)
            if emb is None:
                misses.append(i)
        return results, misses

    def set_batch(self, texts: List[str], model_name: str, embeddings: List[List[float]]) -> None:
        """Cache multiple embeddings."""
        for text, emb in zip(texts, embeddings):
            self.set(text, model_name, emb)


# Global cache instance
_embedding_cache: Optional[EmbeddingCache] = None


def get_embedding_cache() -> EmbeddingCache:
    """Get global embedding cache."""
    global _embedding_cache
    if _embedding_cache is None:
        settings = get_settings()
        cache_dir = os.path.join(os.path.dirname(settings.database_url) if "sqlite" in settings.database_url else ".cache", "embeddings")
        _embedding_cache = EmbeddingCache(cache_dir)
    return _embedding_cache


def embed_symbols_cached(symbols: List[dict]) -> List[Tuple[int, List[float]]]:
    """Generate embeddings with disk caching."""
    if not symbols:
        return []

    cache = get_embedding_cache()
    settings = get_settings()
    model_name = settings.embedding_model

    texts = [symbol_to_text(s) for s in symbols]

    # Check cache
    cached_embeddings, miss_indices = cache.get_batch(texts, model_name)

    # Compute missing
    if miss_indices:
        miss_texts = [texts[i] for i in miss_indices]
        computed = compute_embeddings_batch(miss_texts)
        cache.set_batch(miss_texts, model_name, computed)

        # Fill in misses
        for idx, emb in zip(miss_indices, computed):
            cached_embeddings[idx] = emb

    return [(s["id"], emb) for s, emb in zip(symbols, cached_embeddings)]


def embed_chunks_cached(chunks: List[dict]) -> List[Tuple[int, List[float]]]:
    """Generate embeddings for chunks with disk caching."""
    if not chunks:
        return []

    cache = get_embedding_cache()
    settings = get_settings()
    model_name = settings.embedding_model

    texts = [chunk_to_text(c) for c in chunks]

    # Check cache
    cached_embeddings, miss_indices = cache.get_batch(texts, model_name)

    # Compute missing
    if miss_indices:
        miss_texts = [texts[i] for i in miss_indices]
        computed = compute_embeddings_batch(miss_texts)
        cache.set_batch(miss_texts, model_name, computed)

        # Fill in misses
        for idx, emb in zip(miss_indices, computed):
            cached_embeddings[idx] = emb

    return [(c["id"], emb) for c, emb in zip(chunks, cached_embeddings)]