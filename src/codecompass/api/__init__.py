"""
FastAPI REST API for CodeCompass.

Provides endpoints for search, graph traversal, and repository management.
Swagger docs available at /docs when running.
"""

from .routes import router

__all__ = ["router"]