"""
LLM client interface with OpenRouter implementation.

The LLM is OPTIONAL - only used for natural language query understanding
and answer synthesis. All core functionality works without it.
"""

from .client import (
    CachedLLMClient,
    FakeLLMClient,
    LLMClient,
    LLMResponse,
    OpenRouterClient,
    get_llm_client,
    set_llm_client,
)

__all__ = [
    "LLMClient",
    "OpenRouterClient",
    "CachedLLMClient",
    "FakeLLMClient",
    "LLMResponse",
    "get_llm_client",
    "set_llm_client",
]