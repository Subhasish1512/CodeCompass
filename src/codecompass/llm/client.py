"""
LLM Client: Abstract interface and OpenRouter implementation.

Features:
- Disk cache keyed by prompt hash
- Retries with exponential backoff
- Temperature 0 for deterministic outputs
- Reads only choices[0].message.content
"""

import hashlib
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

from codecompass.config import get_settings


@dataclass
class LLMResponse:
    """Structured LLM response."""
    content: str
    model: str
    usage: Optional[dict] = None
    cached: bool = False


class LLMClient(ABC):
    """Abstract LLM client interface."""

    @abstractmethod
    def generate(self, prompt: str, **kwargs) -> str:
        """Generate a response from the LLM. Returns content string."""
        pass

    @abstractmethod
    def generate_structured(self, prompt: str, **kwargs) -> LLMResponse:
        """Generate a structured response with metadata."""
        pass


class OpenRouterClient(LLMClient):
    """OpenRouter API client with caching and retries."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.0,
        max_retries: int = 3,
        retry_backoff: float = 1.0,
        timeout: float = 60.0,
        cache_dir: str = ".cache/llm"
    ):
        settings = get_settings()
        llm_settings = settings.llm

        self.api_key = api_key or llm_settings.api_key
        self.model = model or llm_settings.model
        self.base_url = base_url or llm_settings.base_url
        self.temperature = temperature
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.timeout = timeout

        # Setup cache
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        if not self.api_key:
            raise ValueError(
                "OPENROUTER_API_KEY not set. "
                "Set it in .env or environment variable."
            )

    def _cache_key(self, prompt: str, **kwargs) -> str:
        """Generate cache key from prompt and parameters."""
        # Include model and temperature in key
        key_data = f"{self.model}:{self.temperature}:{prompt}"
        for k, v in sorted(kwargs.items()):
            key_data += f":{k}={v}"
        return hashlib.sha256(key_data.encode()).hexdigest()[:32]

    def _get_cached(self, key: str) -> Optional[LLMResponse]:
        """Get cached response if exists."""
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            try:
                with open(cache_file) as f:
                    data = json.load(f)
                    return LLMResponse(**data, cached=True)
            except Exception:
                pass
        return None

    def _save_cache(self, key: str, response: LLMResponse) -> None:
        """Save response to cache."""
        cache_file = self.cache_dir / f"{key}.json"
        try:
            data = {
                "content": response.content,
                "model": response.model,
                "usage": response.usage,
                "cached": False
            }
            with open(cache_file, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _make_request(self, prompt: str, **kwargs) -> LLMResponse:
        """Make HTTP request to OpenRouter API."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/codecompass",
            "X-Title": "CodeCompass",
        }

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            **kwargs
        }

        last_error = None
        for attempt in range(self.max_retries):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=payload
                    )
                    resp.raise_for_status()
                    data = resp.json()

                    content = data["choices"][0]["message"]["content"]
                    usage = data.get("usage")

                    return LLMResponse(
                        content=content,
                        model=self.model,
                        usage=usage,
                        cached=False
                    )

            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code >= 500:
                    # Server error - retry
                    time.sleep(self.retry_backoff * (2 ** attempt))
                    continue
                elif e.response.status_code == 429:
                    # Rate limited - longer backoff
                    time.sleep(self.retry_backoff * (2 ** attempt) * 2)
                    continue
                else:
                    # Client error - don't retry
                    raise
            except (httpx.RequestError, httpx.TimeoutException) as e:
                last_error = e
                time.sleep(self.retry_backoff * (2 ** attempt))
                continue

        raise RuntimeError(f"LLM request failed after {self.max_retries} retries: {last_error}")

    def generate(self, prompt: str, **kwargs) -> str:
        """Generate response (returns content only)."""
        return self.generate_structured(prompt, **kwargs).content

    def generate_structured(self, prompt: str, **kwargs) -> LLMResponse:
        """Generate response with metadata and caching."""
        key = self._cache_key(prompt, **kwargs)

        # Check cache
        cached = self._get_cached(key)
        if cached:
            return cached

        # Make request
        response = self._make_request(prompt, **kwargs)

        # Cache result
        self._save_cache(key, response)

        return response


class CachedLLMClient(LLMClient):
    """Wrapper that adds disk caching to any LLM client."""

    def __init__(self, inner: LLMClient, cache_dir: str = ".cache/llm"):
        self.inner = inner
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_key(self, prompt: str, **kwargs) -> str:
        """Generate cache key from prompt and parameters."""
        # Include model from inner client if available
        model = getattr(self.inner, 'model', 'unknown')
        temperature = getattr(self.inner, 'temperature', 0.0)
        key_data = f"{model}:{temperature}:{prompt}"
        for k, v in sorted(kwargs.items()):
            key_data += f":{k}={v}"
        return hashlib.sha256(key_data.encode()).hexdigest()[:32]

    def _get_cached(self, key: str) -> Optional[LLMResponse]:
        """Get cached response if exists."""
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            try:
                with open(cache_file) as f:
                    data = json.load(f)
                    return LLMResponse(**data, cached=True)
            except Exception:
                pass
        return None

    def _save_cache(self, key: str, response: LLMResponse) -> None:
        """Save response to cache."""
        cache_file = self.cache_dir / f"{key}.json"
        try:
            data = {
                "content": response.content,
                "model": response.model,
                "usage": response.usage,
                "cached": False
            }
            with open(cache_file, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    def generate(self, prompt: str, **kwargs) -> str:
        return self.generate_structured(prompt, **kwargs).content

    def generate_structured(self, prompt: str, **kwargs) -> LLMResponse:
        key = self._cache_key(prompt, **kwargs)

        # Check cache
        cached = self._get_cached(key)
        if cached:
            return cached

        # Make request
        response = self.inner.generate_structured(prompt, **kwargs)

        # Cache result
        self._save_cache(key, response)

        return response


class FakeLLMClient(LLMClient):
    """Fake client for offline development - returns deterministic responses."""

    def __init__(self, responses: Optional[dict] = None):
        self.responses = responses or {
            "default": "Based on the provided code snippets, I cannot find a definitive answer. The snippets do not contain sufficient information to answer this question.\n\nNot found in the indexed code."
        }
        self.call_count = 0

    def generate(self, prompt: str, **kwargs) -> str:
        return self.generate_structured(prompt, **kwargs).content

    def generate_structured(self, prompt: str, **kwargs) -> LLMResponse:
        self.call_count += 1

        # Check for custom responses based on prompt content
        for key, response in self.responses.items():
            if key != "default" and key.lower() in prompt.lower():
                return LLMResponse(
                    content=response,
                    model="fake",
                    cached=False
                )

        return LLMResponse(
            content=self.responses.get("default", "No response configured"),
            model="fake",
            cached=False
        )


# Global client instance
_llm_client: Optional[LLMClient] = None


def get_llm_client() -> Optional[LLMClient]:
    """Get global LLM client, creating it if needed."""
    global _llm_client
    if _llm_client is None:
        settings = get_settings()
        provider = settings.llm.provider.lower()

        if provider == "fake":
            _llm_client = FakeLLMClient()
        elif provider == "openrouter":
            try:
                _llm_client = CachedLLMClient(OpenRouterClient())
            except ValueError:
                # API key not set - LLM is optional
                return None
        else:
            raise ValueError(f"Unknown LLM provider: {provider}")

    return _llm_client


def set_llm_client(client: LLMClient) -> None:
    """Set global LLM client (for testing)."""
    global _llm_client
    _llm_client = client