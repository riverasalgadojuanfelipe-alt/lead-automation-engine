"""Modelos de datos (validados con pydantic)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class LeadIn(BaseModel):
    """Lo que llega por el webhook/formulario."""

    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    company: str = Field(default="", max_length=200)
    job_title: str = Field(default="", max_length=200)
    country: str = Field(default="", max_length=100)
    message: str = Field(default="", max_length=3000)
    source: str = Field(default="web_form", max_length=100)

    @field_validator("email")
    @classmethod
    def _email_ok(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or v.startswith("@") or v.endswith("@") or " " in v:
            raise ValueError("email inválido")
        return v


Tier = Literal["hot", "warm", "cold"]


class Enrichment(BaseModel):
    """Salida estructurada que debe devolver el LLM (se valida siempre)."""

    industry: str
    company_size_band: Literal["1-10", "11-50", "51-200", "201-1000", "1000+", "unknown"]
    seniority: Literal["c_level", "director", "manager", "individual", "unknown"]
    erp_or_system_mentioned: Optional[str] = None
    pain_points: list[str] = Field(default_factory=list, max_length=5)
    priority_score: int = Field(ge=0, le=100)
    tier: Tier
    fit_reasoning: str
    talking_points: list[str] = Field(default_factory=list, max_length=5)
    seller_brief: str
    enrichment_source: str = "unknown"  # "llm:<proveedor>" | "mock" | "fallback_rules"
