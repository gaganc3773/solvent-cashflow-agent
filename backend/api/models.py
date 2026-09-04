"""Shared Pydantic request/response models used by main.py.

Endpoint-specific models live inside api/cashflow.py and api/agent.py.
This file only holds the cross-cutting types (Explain, LLM status, Audit).
"""
from __future__ import annotations

from pydantic import BaseModel


class ExplainRequest(BaseModel):
    question_key: str
    use_llm: bool = False


class FreeTextExplainRequest(BaseModel):
    question: str
    use_llm: bool = False


class ExplainResponse(BaseModel):
    question: str
    answer: str
    evidence: list[list[str]] | None = None
    engine: str = "deterministic"          # "deterministic" | "llm" | "llm_fallback"
    llm_model: str | None = None
    llm_latency_ms: float | None = None
    fallback_reason: str | None = None


class LLMStatus(BaseModel):
    available: bool
    models: list[str]
    default_model: str


class AuditEntry(BaseModel):
    action: str
    logged_at: str | None = None
    detail: str | None = None
