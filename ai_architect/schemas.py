"""
ai_architect/schemas.py
Pydantic schemas for AI Architect reviews, refactoring recommendations,
change plans, and architecture Q&A.
"""

from typing import List, Optional, Literal, Dict, Any
import time
from pydantic import BaseModel, Field, field_validator


SEVERITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}

VALID_SEVERITIES = set(SEVERITY_ORDER.keys())
VALID_CATEGORIES = {
    "architecture",
    "coupling",
    "complexity",
    "maintainability",
    "testing",
    "security",
    "reliability",
}
VALID_ORIGINS = {"static_rule", "ai_hypothesis", "combined"}


class SourceLocation(BaseModel):
    file: str
    line: Optional[int] = None
    end_line: Optional[int] = None
    symbol: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "end_line": self.end_line,
            "symbol": self.symbol,
        }


class ReviewFinding(BaseModel):
    id: str = Field(description="Unique finding identifier, e.g. ARCH-001 or rule id")
    title: str = Field(description="Short descriptive title of the finding")
    severity: str = Field(default="medium", description="critical | high | medium | low | info")
    category: str = Field(default="architecture", description="architecture | coupling | complexity | maintainability | testing | security | reliability")
    evidence: List[str] = Field(default_factory=list, description="Concrete metric values, cycle paths, or observed code constructs")
    explanation: str = Field(description="Why this is a risk or problem in the codebase")
    recommendation: str = Field(description="Specific actionable architectural or code improvement")
    tradeoffs: List[str] = Field(default_factory=list, description="Architectural trade-offs or migration costs of the recommendation")
    validation_steps: List[str] = Field(default_factory=list, description="Verification and test steps to validate the fix")
    source_locations: List[SourceLocation] = Field(default_factory=list, description="Validated files and line numbers")
    origin: str = Field(default="static_rule", description="static_rule | ai_hypothesis | combined")
    rule_id: Optional[str] = Field(default=None, description="Static rule code if applicable")

    @field_validator("severity", mode="before")
    @classmethod
    def normalize_severity(cls, v: str) -> str:
        s = (v or "medium").strip().lower()
        return s if s in VALID_SEVERITIES else "medium"

    @field_validator("category", mode="before")
    @classmethod
    def normalize_category(cls, v: str) -> str:
        c = (v or "architecture").strip().lower()
        return c if c in VALID_CATEGORIES else "architecture"

    @field_validator("origin", mode="before")
    @classmethod
    def normalize_origin(cls, v: str) -> str:
        o = (v or "static_rule").strip().lower()
        return o if o in VALID_ORIGINS else "static_rule"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "severity": self.severity,
            "category": self.category,
            "evidence": self.evidence,
            "explanation": self.explanation,
            "recommendation": self.recommendation,
            "tradeoffs": self.tradeoffs,
            "validation_steps": self.validation_steps,
            "source_locations": [loc.to_dict() for loc in self.source_locations],
            "origin": self.origin,
            "rule_id": self.rule_id,
        }


class ReviewResponse(BaseModel):
    provider: str = Field(description="groq | local | combined")
    review_mode: str = Field(default="auto", description="auto | groq | local")
    status: str = Field(default="completed", description="completed | fallback_local | failed")
    status_message: Optional[str] = Field(default=None, description="User-facing status note regarding provider or fallback")
    review_summary: str = Field(description="Executive summary of the codebase architecture health")
    architecture_overview: Optional[str] = Field(default=None, description="High-level narrative of system boundaries and patterns")
    findings: List[ReviewFinding] = Field(default_factory=list)
    model: Optional[str] = Field(default=None, description="LLM model identifier used, if any")
    timestamp: float = Field(default_factory=time.time)

    def get_prioritized_findings(self) -> List[ReviewFinding]:
        return sorted(self.findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 99))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "review_mode": self.review_mode,
            "status": self.status,
            "status_message": self.status_message,
            "review_summary": self.review_summary,
            "architecture_overview": self.architecture_overview,
            "findings": [f.to_dict() for f in self.get_prioritized_findings()],
            "model": self.model,
            "timestamp": self.timestamp,
        }


class ChangePlanRequest(BaseModel):
    proposed_change: str = Field(min_length=3, description="Proposed refactoring or architectural modification")


class ChangePlanResponse(BaseModel):
    proposed_change: str
    summary: str
    affected_files: List[str] = Field(default_factory=list)
    dependency_paths: List[str] = Field(default_factory=list)
    implementation_sequence: List[str] = Field(default_factory=list)
    potential_risks: List[str] = Field(default_factory=list)
    recommended_tests: List[str] = Field(default_factory=list)
    unknowns: List[str] = Field(default_factory=list)
    provider: str = Field(default="local")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposed_change": self.proposed_change,
            "summary": self.summary,
            "affected_files": self.affected_files,
            "dependency_paths": self.dependency_paths,
            "implementation_sequence": self.implementation_sequence,
            "potential_risks": self.potential_risks,
            "recommended_tests": self.recommended_tests,
            "unknowns": self.unknowns,
            "provider": self.provider,
        }


class AskQuestionRequest(BaseModel):
    question: str = Field(min_length=2, description="Project-scoped architecture question")


class AskQuestionResponse(BaseModel):
    question: str
    answer: str
    evidence_sources: List[str] = Field(default_factory=list)
    grounded_facts: List[str] = Field(default_factory=list)
    is_grounded: bool = Field(default=True)
    provider: str = Field(default="local")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "evidence_sources": self.evidence_sources,
            "grounded_facts": self.grounded_facts,
            "is_grounded": self.is_grounded,
            "provider": self.provider,
        }
