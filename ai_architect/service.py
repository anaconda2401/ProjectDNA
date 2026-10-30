"""
ai_architect/service.py
Service orchestrator for ProjectDNA AI Architect.
Coordinates Groq AI client and deterministic LocalReviewer, manages fallback,
validates source references, deduplicates findings, and persists reviews.
"""

import os
import logging
from typing import Dict, Any, List, Optional, Set

from .schemas import (
    ReviewResponse,
    ReviewFinding,
    SourceLocation,
    ChangePlanResponse,
    AskQuestionResponse,
)
from .local_reviewer import LocalReviewer
from .groq_client import (
    GroqClient,
    GroqProviderError,
    GroqAuthError,
    GroqRateLimitError,
    GroqTimeoutError,
    GroqConnectionError,
    GroqResponseError,
)
from .context_builder import build_review_context
from .prompts import (
    SYSTEM_REVIEW_PROMPT,
    SYSTEM_PLANNER_PROMPT,
    SYSTEM_ASK_PROMPT,
    build_review_user_message,
    build_plan_user_message,
    build_ask_user_message,
)

logger = logging.getLogger("projectdna.ai_architect")


class AIArchitectService:
    """
    Unified service managing architectural reviews, change impact planning,
    and natural-language codebase Q&A.
    """

    def __init__(
        self,
        groq_client: Optional[GroqClient] = None,
        local_reviewer: Optional[LocalReviewer] = None,
    ):
        self.groq_client = groq_client or GroqClient()
        self.local_reviewer = local_reviewer or LocalReviewer()

    def run_review(
        self,
        result: Dict[str, Any],
        project_root: Optional[str] = None,
        mode: str = "auto",
    ) -> ReviewResponse:
        """
        Execute codebase review under the requested mode: 'auto', 'groq', or 'local'.
        Gracefully falls back to deterministic local review on any Groq failure.
        """
        mode = (mode or "auto").strip().lower()
        if mode not in ("auto", "groq", "local"):
            mode = "auto"

        # Mode: 'local' - run deterministic engine directly
        if mode == "local":
            return self.local_reviewer.review(result)

        # Modes: 'auto' or 'groq' - attempt Groq if configured
        if not self.groq_client.is_available():
            fallback = self.local_reviewer.review(result)
            fallback.review_mode = mode
            fallback.status = "fallback_local"
            fallback.status_message = "Groq review unavailable (API key not configured or AI disabled). Showing local static-analysis recommendations."
            return fallback

        # Attempt Groq review
        try:
            context_json = build_review_context(result, project_root=project_root)
            user_msg = build_review_user_message(context_json)
            raw_groq_resp = self.groq_client.chat_completion_json(SYSTEM_REVIEW_PROMPT, user_msg)

            # Validate and normalize Groq response
            groq_findings = self._normalize_groq_findings(raw_groq_resp.get("findings", []), result)

            # Always run local reviewer to establish verified static facts
            local_resp = self.local_reviewer.review(result)

            # Merge Groq hypotheses with verified static findings
            merged_findings = self._merge_findings(local_resp.findings, groq_findings)

            review_summary = raw_groq_resp.get("review_summary") or local_resp.review_summary
            arch_overview = raw_groq_resp.get("architecture_overview") or local_resp.architecture_overview

            provider_name = "combined" if any(f.origin == "ai_hypothesis" for f in merged_findings) else "local"

            return ReviewResponse(
                provider=provider_name,
                review_mode=mode,
                status="completed",
                status_message="Review generated successfully with Groq AI assistance and verified static analysis.",
                review_summary=review_summary,
                architecture_overview=arch_overview,
                findings=merged_findings,
                model=self.groq_client.model,
            )

        except (GroqAuthError, GroqRateLimitError, GroqTimeoutError, GroqConnectionError, GroqResponseError, GroqProviderError, Exception) as exc:
            logger.warning("Groq review failed (%s): %s. Falling back to local reviewer.", type(exc).__name__, exc)
            fallback = self.local_reviewer.review(result)
            fallback.review_mode = mode
            fallback.status = "fallback_local"
            error_msg = getattr(exc, "message", str(exc))
            fallback.status_message = f"Groq review unavailable ({error_msg}). Showing local static-analysis recommendations."
            return fallback

    def plan_change(
        self,
        proposed_change: str,
        result: Dict[str, Any],
        project_root: Optional[str] = None,
        mode: str = "auto",
    ) -> ChangePlanResponse:
        """Generate architectural change plan using Groq with deterministic local fallback."""
        if mode == "local" or not self.groq_client.is_available():
            return self.local_reviewer.plan_change_local(proposed_change, result)

        try:
            context_json = build_review_context(result, project_root=project_root)
            user_msg = build_plan_user_message(proposed_change, context_json)
            raw = self.groq_client.chat_completion_json(SYSTEM_PLANNER_PROMPT, user_msg)

            # Validate affected files against real modules in project
            known_files = self._get_known_files(result)
            raw_affected = raw.get("affected_files", [])
            valid_affected = [f for f in raw_affected if any(kf in f or f in kf for kf in known_files)]

            # If Groq produced empty or invalid files, incorporate local analyzer files
            if not valid_affected:
                local_plan = self.local_reviewer.plan_change_local(proposed_change, result)
                valid_affected = local_plan.affected_files

            return ChangePlanResponse(
                proposed_change=proposed_change,
                summary=raw.get("summary", "Architectural impact assessment."),
                affected_files=valid_affected,
                dependency_paths=raw.get("dependency_paths", []),
                implementation_sequence=raw.get("implementation_sequence", []),
                potential_risks=raw.get("potential_risks", []),
                recommended_tests=raw.get("recommended_tests", []),
                unknowns=raw.get("unknowns", []),
                provider="groq",
            )
        except Exception as exc:
            logger.warning("Groq change planning failed: %s. Using local change planner.", exc)
            return self.local_reviewer.plan_change_local(proposed_change, result)

    def ask_question(
        self,
        question: str,
        result: Dict[str, Any],
        project_root: Optional[str] = None,
        mode: str = "auto",
    ) -> AskQuestionResponse:
        """Answer project-scoped question using Groq with deterministic local fallback."""
        if mode == "local" or not self.groq_client.is_available():
            return self.local_reviewer.ask_question_local(question, result)

        try:
            context_json = build_review_context(result, project_root=project_root)
            user_msg = build_ask_user_message(question, context_json)
            raw = self.groq_client.chat_completion_json(SYSTEM_ASK_PROMPT, user_msg)

            return AskQuestionResponse(
                question=question,
                answer=raw.get("answer", "No answer generated."),
                evidence_sources=raw.get("evidence_sources", []),
                grounded_facts=raw.get("grounded_facts", []),
                is_grounded=bool(raw.get("is_grounded", True)),
                provider="groq",
            )
        except Exception as exc:
            logger.warning("Groq question answering failed: %s. Using local Q&A engine.", exc)
            return self.local_reviewer.ask_question_local(question, result)

    def _normalize_groq_findings(self, raw_findings: List[Dict], result: Dict[str, Any]) -> List[ReviewFinding]:
        """Validate source references against verified project files and cast to ReviewFinding."""
        known_files = self._get_known_files(result)
        valid_findings: List[ReviewFinding] = []

        for idx, item in enumerate(raw_findings):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            if not title:
                continue

            # Validate source locations against real project files
            raw_locs = item.get("source_locations", [])
            valid_locs = []
            for loc in raw_locs:
                if isinstance(loc, dict) and "file" in loc:
                    file_str = str(loc["file"]).strip()
                    # Only accept file if it matches or resembles a known file in the project
                    if any(kf in file_str or file_str in kf for kf in known_files):
                        valid_locs.append(SourceLocation(
                            file=file_str,
                            line=loc.get("line"),
                            end_line=loc.get("end_line"),
                            symbol=loc.get("symbol"),
                        ))

            finding_id = item.get("id") or f"AI-ARCH-{idx + 1:03d}"
            valid_findings.append(ReviewFinding(
                id=finding_id,
                title=title,
                severity=item.get("severity", "medium"),
                category=item.get("category", "architecture"),
                evidence=item.get("evidence") if isinstance(item.get("evidence"), list) else [str(item.get("evidence", ""))],
                explanation=item.get("explanation", "Potential architectural concern identified by AI interpretation."),
                recommendation=item.get("recommendation", "Review module dependencies and structure."),
                tradeoffs=item.get("tradeoffs", []) if isinstance(item.get("tradeoffs"), list) else [],
                validation_steps=item.get("validation_steps", []) if isinstance(item.get("validation_steps"), list) else [],
                source_locations=valid_locs,
                origin="ai_hypothesis",
                rule_id=item.get("rule_id"),
            ))
        return valid_findings

    def _merge_findings(
        self,
        local_findings: List[ReviewFinding],
        groq_findings: List[ReviewFinding],
    ) -> List[ReviewFinding]:
        """
        Merge local verified findings with Groq findings, eliminating duplicate issues.
        Preserves verified static evidence while augmenting explanations.
        """
        merged: List[ReviewFinding] = list(local_findings)

        for gf in groq_findings:
            # Check for duplicate finding by comparing title or target symbol
            is_dup = False
            for lf in merged:
                if self._are_findings_duplicates(lf, gf):
                    # Combine: keep static evidence, enrich with AI recommendations if helpful
                    lf.origin = "combined"
                    if gf.tradeoffs and not lf.tradeoffs:
                        lf.tradeoffs = gf.tradeoffs
                    if len(gf.explanation) > len(lf.explanation):
                        lf.explanation = f"{lf.explanation}\n\nAI Insight: {gf.explanation}"
                    is_dup = True
                    break

            if not is_dup:
                merged.append(gf)

        return merged

    def _are_findings_duplicates(self, f1: ReviewFinding, f2: ReviewFinding) -> bool:
        """Determine if two findings describe the same underlying issue."""
        t1, t2 = f1.title.lower(), f2.title.lower()
        if f1.rule_id and f2.rule_id and f1.rule_id == f2.rule_id:
            return True
        if "circular" in t1 and "circular" in t2:
            return True
        if "god module" in t1 and ("god module" in t2 or "large module" in t2):
            return True

        # Check overlapping source locations
        locs1 = {loc.file for loc in f1.source_locations}
        locs2 = {loc.file for loc in f2.source_locations}
        if locs1 and locs2 and locs1.intersection(locs2):
            if f1.category == f2.category:
                return True

        return False

    def _get_known_files(self, result: Dict[str, Any]) -> Set[str]:
        """Extract set of relative and canonical file paths from result modules."""
        files = set()
        for m in result.get("modules", []):
            p = m.get("path")
            if p:
                files.add(p)
                files.add(os.path.basename(p))
            name = m.get("module_name")
            if name:
                files.add(name)
        return files
