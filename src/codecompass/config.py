"""
Configuration management for CodeCompass.

Loads settings from environment variables with sensible defaults.
All secrets must come from environment, never hardcoded.
"""

from functools import lru_cache
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    """LLM-specific settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="llm_"
    )

    provider: str = Field(
        default="openrouter",
        description="LLM provider: openrouter, fake"
    )
    api_key: Optional[str] = Field(
        default=None,
        description="OpenRouter API key for LLM access"
    )
    model: str = Field(
        default="anthropic/claude-3.5-sonnet",
        description="OpenRouter model identifier"
    )
    base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="OpenRouter API base URL"
    )
    temperature: float = Field(
        default=0.0,
        description="Temperature for generation (0 = deterministic)"
    )
    max_retries: int = Field(
        default=3,
        description="Max retries for failed requests"
    )
    retry_backoff: float = Field(
        default=1.0,
        description="Base backoff seconds for retries"
    )
    timeout: float = Field(
        default=60.0,
        description="Request timeout in seconds"
    )
    cache_dir: str = Field(
        default=".cache/llm",
        description="Disk cache directory for LLM responses"
    )


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # Database
    database_url: str = Field(
        default="postgresql://codecompass:codecompass@localhost:5432/codecompass",
        description="PostgreSQL connection URL"
    )

    # LLM
    llm: LLMSettings = Field(default_factory=LLMSettings)

    # Embeddings
    embedding_model: str = Field(
        default="BAAI/bge-small-en-v1.5",
        description="Sentence-transformers model name"
    )
    embedding_dim: int = Field(
        default=384,
        description="Embedding dimension (must match model)"
    )
    embedding_batch_size: int = Field(
        default=32,
        description="Batch size for embedding generation"
    )

    # Indexing
    index_batch_size: int = Field(
        default=1000,
        description="Number of symbols to process per batch"
    )
    max_file_size_mb: int = Field(
        default=1,
        description="Maximum file size to index"
    )
    excluded_dirs: List[str] = Field(
        default_factory=lambda: [".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv", "dist", "build", ".mypy_cache", ".ruff_cache"],
        description="Directories to exclude from indexing"
    )
    excluded_extensions: List[str] = Field(
        default_factory=lambda: [".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".bin", ".dat", ".db", ".sqlite", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".ico", ".svg", ".mp3", ".mp4", ".avi", ".mov", ".zip", ".tar", ".gz", ".rar", ".7z"],
        description="File extensions to exclude from indexing"
    )

    # Search
    bm25_k1: float = Field(default=1.2, description="BM25 k1 parameter")
    bm25_b: float = Field(default=0.75, description="BM25 b parameter")
    rrf_k: int = Field(default=60, description="RRF k parameter")
    default_limit: int = Field(default=20, description="Default search result limit")
    bm25_limit: int = Field(default=50, description="BM25 candidate limit for hybrid search")
    vector_limit: int = Field(default=50, description="Vector candidate limit for hybrid search")
    min_retrieval_score: float = Field(
        default=0.15,
        description="Minimum fused score threshold before calling LLM"
    )

    # API
    api_host: str = Field(default="0.0.0.0", description="API host")
    api_port: int = Field(default=8000, description="API port")
    api_workers: int = Field(default=1, description="API workers")

    # Logging
    log_level: str = Field(default="INFO", description="Log level")


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()