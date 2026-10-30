"""
ai_architect/local_reviewer.py
Deterministic rule-based review engine for ProjectDNA.
Analyzes verified static analysis data without network access or external APIs.
"""

from typing import Dict, Any, List, Optional, Set
import re
from .schemas import (
    ReviewResponse,
    ReviewFinding,
    SourceLocation,
    ChangePlanResponse,
    AskQuestionResponse,
)


class LocalReviewer:
    """
    Deterministic architecture review engine based on measured AST, dependency graph,
    and complexity metrics. Zero external dependencies, fully repeatable.
    """

    def __init__(self, thresholds: Optional[Dict[str, Any]] = None):
        self.thresholds = {
            "max_complexity_warn": 8,
            "max_complexity_high": 12,
            "max_efferent_coupling": 7,
            "max_afferent_coupling": 10,
            "max_module_code_lines": 250,
            "max_module_functions": 15,
        }
        if thresholds:
            self.thresholds.update(thresholds)

    def review(self, result: Dict[str, Any]) -> ReviewResponse:
        """Run all deterministic static rules and return a validated ReviewResponse."""
        findings: List[ReviewFinding] = []
        modules = result.get("modules", [])
        issues = result.get("issues", [])
        module_metrics = result.get("module_metrics", {})
        summary = result.get("summary", {})
        all_functions = result.get("all_functions", [])
        layer_map = result.get("layer_map", {})
        risks = result.get("architecture_risks", {}).get("risk_backlog", [])

        # 1. Circular Dependencies
        cycle_findings = self._review_cycles(issues, modules)
        findings.extend(cycle_findings)

        # 2. High Coupling & Fragile Dependencies
        coupling_findings = self._review_coupling(module_metrics, modules)
        findings.extend(coupling_findings)

        # 3. High Cyclomatic Complexity
        complexity_findings = self._review_complexity(all_functions, modules)
        findings.extend(complexity_findings)

        # 4. God Modules / Excessively Large Modules
        god_findings = self._review_god_modules(modules, issues)
        findings.extend(god_findings)

        # 5. Architectural Layer Violations
        layer_findings = self._review_layer_violations(issues, layer_map)
        findings.extend(layer_findings)

        # 6. Unused Imports
        unused_findings = self._review_unused_imports(modules)
        findings.extend(unused_findings)

        # 7. Missing Documentation for Complex Components
        doc_findings = self._review_missing_docs(all_functions, modules)
        findings.extend(doc_findings)

        # 8. Testing Gaps for Critical / Core Modules
        test_gap_findings = self._review_testing_gaps(modules, module_metrics, layer_map)
        findings.extend(test_gap_findings)

        # Build summary narrative
        review_summary = self._build_summary_narrative(summary, findings)
        architecture_overview = self._build_architecture_overview(summary, modules, layer_map)

        return ReviewResponse(
            provider="local",
            review_mode="local",
            status="completed",
            status_message="Local static-analysis review generated deterministically from measured facts.",
            review_summary=review_summary,
            architecture_overview=architecture_overview,
            findings=findings,
            model=None,
        )

    def _review_cycles(self, issues: List[Dict], modules: List[Dict]) -> List[ReviewFinding]:
        findings = []
        cycle_issues = [iss for iss in issues if iss.get("rule") == "circular_dependency"]
        seen_cycles: Set[str] = set()

        for iss in cycle_issues:
            msg = iss.get("message", "")
            if msg in seen_cycles:
                continue
            seen_cycles.add(msg)

            parts = [p.strip() for p in re.split(r"->|\u2192", msg) if p.strip()]
            cycle_mods = list(dict.fromkeys(parts))

            locations = []
            for m_name in cycle_mods:
                mod_match = next((m for m in modules if m.get("module_name") == m_name), None)
                if mod_match:
                    locations.append(SourceLocation(
                        file=mod_match.get("path", m_name),
                        symbol=m_name,
                    ))

            severity = "critical" if len(cycle_mods) >= 3 else "high"
            findings.append(ReviewFinding(
                id=f"RULE-CYCLE-{len(findings) + 1}",
                title=f"Circular Dependency: {' \u2192 '.join(cycle_mods[:4])}",
                severity=severity,
                category="architecture",
                evidence=[
                    f"Dependency cycle detected: {msg}",
                    f"Participating modules: {', '.join(cycle_mods)}",
                ],
                explanation="Circular dependencies entangle modules into a single tightly coupled unit, preventing isolated testing, hindering parallel development, and complicating refactoring.",
                recommendation="Break the cycle by extracting shared types/interfaces into an abstraction module, or apply dependency injection to decouple direct imports.",
                tradeoffs=[
                    "Requires introducing an additional common or interface module.",
                    "May require refactoring caller instantiation logic.",
                ],
                validation_steps=[
                    "Run ProjectDNA dependency analyzer to verify zero remaining cycles.",
                    f"Execute automated tests for cycle participants: {', '.join(cycle_mods)}.",
                ],
                source_locations=locations,
                origin="static_rule",
                rule_id="circular_dependency",
            ))
        return findings

    def _review_coupling(self, module_metrics: Any, modules: List[Dict]) -> List[ReviewFinding]:
        findings = []
        metrics_dict: Dict[str, Dict] = {}
        if isinstance(module_metrics, dict):
            metrics_dict = module_metrics
        elif isinstance(module_metrics, list):
            for item in module_metrics:
                if isinstance(item, dict) and "module" in item:
                    metrics_dict[item["module"]] = item

        for mod_name, m in metrics_dict.items():
            ce = m.get("efferent_coupling", 0)
            ca = m.get("afferent_coupling", 0)
            instability = m.get("instability", 0.0)

            mod_match = next((mod for mod in modules if mod.get("module_name") == mod_name), None)
            file_path = mod_match.get("path", mod_name) if mod_match else mod_name

            if ce >= self.thresholds["max_efferent_coupling"]:
                severity = "high" if ce >= 10 else "medium"
                findings.append(ReviewFinding(
                    id=f"RULE-COUPLING-EFF-{mod_name.replace('.', '_')}",
                    title=f"High Efferent Coupling (Fan-out) in {mod_name}",
                    severity=severity,
                    category="coupling",
                    evidence=[
                        f"Efferent coupling (Ce / Fan-out): {ce} outbound dependencies (threshold: {self.thresholds['max_efferent_coupling']})",
                        f"Afferent coupling (Ca / Fan-in): {ca}",
                        f"Instability index: {instability:.2f}",
                    ],
                    explanation=f"Module '{mod_name}' depends directly on {ce} external modules. High fan-out indicates that this module is fragile and susceptible to ripple effects whenever any of its dependencies change.",
                    recommendation="Introduce a facade or adapter to encapsulate external interactions, and ensure dependencies follow clear layer boundaries.",
                    tradeoffs=[
                        "Creating facade adapters adds indirection layers.",
                    ],
                    validation_steps=[
                        f"Measure efferent coupling for '{mod_name}' after refactoring to ensure Ce < {self.thresholds['max_efferent_coupling']}.",
                        "Verify contract stability with integration tests.",
                    ],
                    source_locations=[SourceLocation(file=file_path, symbol=mod_name)],
                    origin="static_rule",
                    rule_id="high_efferent_coupling",
                ))

            if ca >= self.thresholds["max_afferent_coupling"]:
                findings.append(ReviewFinding(
                    id=f"RULE-COUPLING-AFF-{mod_name.replace('.', '_')}",
                    title=f"High Afferent Coupling (High-Impact Hub): {mod_name}",
                    severity="medium",
                    category="architecture",
                    evidence=[
                        f"Afferent coupling (Ca / Fan-in): {ca} incoming dependents (threshold: {self.thresholds['max_afferent_coupling']})",
                        f"Instability index: {instability:.2f} (highly stable/rigid)",
                    ],
                    explanation=f"Module '{mod_name}' is a high-traffic hub depended upon by {ca} modules. Any breaking API changes or defects here will propagate widely across the entire system.",
                    recommendation="Ensure this module has comprehensive automated test coverage, a frozen public API, and high backwards compatibility.",
                    tradeoffs=[
                        "Freezing public APIs limits rapid internal changes.",
                    ],
                    validation_steps=[
                        f"Run Change Impact Analysis on '{mod_name}' to verify all {ca} dependents pass tests.",
                        "Enforce strict unit test coverage on all public functions in this module.",
                    ],
                    source_locations=[SourceLocation(file=file_path, symbol=mod_name)],
                    origin="static_rule",
                    rule_id="high_afferent_coupling",
                ))
        return findings

    def _review_complexity(self, all_functions: List[Dict], modules: List[Dict]) -> List[ReviewFinding]:
        findings = []
        for fn in all_functions:
            cc = fn.get("complexity", 1)
            if cc >= self.thresholds["max_complexity_warn"]:
                severity = "high" if cc >= self.thresholds["max_complexity_high"] else "medium"
                qname = fn.get("qualified", fn.get("name", "unknown"))
                mod_name = fn.get("module", "")
                mod_match = next((m for m in modules if m.get("module_name") == mod_name), None)
                file_path = fn.get("path") or (mod_match.get("path") if mod_match else mod_name)

                findings.append(ReviewFinding(
                    id=f"RULE-CC-{qname.replace('.', '_')}",
                    title=f"High Cyclomatic Complexity in {qname} (CC: {cc})",
                    severity=severity,
                    category="complexity",
                    evidence=[
                        f"McCabe cyclomatic complexity: {cc} (threshold: {self.thresholds['max_complexity_warn']})",
                        f"Function: {qname}",
                        f"Location: lines {fn.get('lineno', '?')} - {fn.get('end_lineno', '?')}",
                        f"Arguments: {len(fn.get('args', []))}",
                    ],
                    explanation=f"Cyclomatic complexity of {cc} indicates numerous branching paths, deeply nested conditionals, or complex loops. Such functions have high defect density and are difficult to cover fully with tests.",
                    recommendation="Decompose function using extract-method refactoring, guard clauses, or dispatch dictionaries.",
                    tradeoffs=[
                        "Splitting functions into smaller units requires passing context or creating class helper methods.",
                    ],
                    validation_steps=[
                        f"Write parameterized unit tests for all logical branches of '{qname}'.",
                        "Verify cyclomatic complexity drops below 8 using radon/AST metrics.",
                    ],
                    source_locations=[SourceLocation(
                        file=file_path,
                        line=fn.get("lineno"),
                        end_line=fn.get("end_lineno"),
                        symbol=qname,
                    )],
                    origin="static_rule",
                    rule_id="high_complexity",
                ))
                if len(findings) >= 8:  # Cap top 8 most complex to prevent noise
                    break
        return findings

    def _review_god_modules(self, modules: List[Dict], issues: List[Dict]) -> List[ReviewFinding]:
        findings = []
        for m in modules:
            code_lines = m.get("code_lines", 0)
            func_count = len(m.get("functions", []))
            class_count = len(m.get("classes", []))
            mod_name = m.get("module_name", "")
            file_path = m.get("path", mod_name)

            is_god_issue = any(
                iss.get("rule") == "god_module" and iss.get("module") == mod_name
                for iss in issues
            )

            if code_lines >= self.thresholds["max_module_code_lines"] or func_count >= self.thresholds["max_module_functions"] or is_god_issue:
                severity = "high" if code_lines >= 400 else "medium"
                findings.append(ReviewFinding(
                    id=f"RULE-GOD-{mod_name.replace('.', '_')}",
                    title=f"Large / God Module Candidate: {mod_name}",
                    severity=severity,
                    category="maintainability",
                    evidence=[
                        f"Source code lines (SLOC): {code_lines} (threshold: {self.thresholds['max_module_code_lines']})",
                        f"Functions declared: {func_count}",
                        f"Classes declared: {class_count}",
                    ],
                    explanation=f"Module '{mod_name}' concentrates disproportionate responsibilities. Large multi-purpose modules violate the Single Responsibility Principle and become development bottlenecks.",
                    recommendation="Split the module into sub-modules organized by domain responsibility, creating a clean package structure.",
                    tradeoffs=[
                        "Requires updating import statements across other modules in the codebase.",
                    ],
                    validation_steps=[
                        f"Run regression tests to verify module exports remain intact.",
                        "Inspect dependency graph to confirm cohesive smaller modules.",
                    ],
                    source_locations=[SourceLocation(file=file_path, symbol=mod_name)],
                    origin="static_rule",
                    rule_id="god_module",
                ))
        return findings

    def _review_layer_violations(self, issues: List[Dict], layer_map: Dict[str, str]) -> List[ReviewFinding]:
        findings = []
        layer_issues = [iss for iss in issues if iss.get("rule") == "layer_violation"]
        for idx, iss in enumerate(layer_issues):
            mod = iss.get("module", "")
            msg = iss.get("message", "")
            findings.append(ReviewFinding(
                id=f"RULE-LAYER-{idx + 1}",
                title=f"Architectural Layer Inversion: {mod}",
                severity="high",
                category="architecture",
                evidence=[
                    f"Violation message: {msg}",
                    f"Module layer assignment: {layer_map.get(mod, 'unknown')}",
                ],
                explanation="Architectural boundaries require dependencies to flow in a single direction (e.g. presentation \u2192 service \u2192 data \u2192 utility). Reverse or upward dependencies violate layering.",
                recommendation="Invert dependency flow using interfaces, callbacks, or move shared data structures down to a lower utility/domain layer.",
                tradeoffs=[
                    "May require abstract base classes or dependency injection wiring.",
                ],
                validation_steps=[
                    "Check layer graph in ProjectDNA to confirm zero upward edges.",
                    "Verify clean separation by running integration tests.",
                ],
                source_locations=[SourceLocation(file=mod, symbol=mod)],
                origin="static_rule",
                rule_id="layer_violation",
            ))
        return findings

    def _review_unused_imports(self, modules: List[Dict]) -> List[ReviewFinding]:
        """Detect imports declared in a module whose imported names are never referenced in AST."""
        findings = []
        for m in modules:
            mod_name = m.get("module_name", "")
            file_path = m.get("path", mod_name)
            imports = m.get("imports", [])

            # Aggregate all tokens in module functions and classes
            referenced_tokens: Set[str] = set()
            for fn in m.get("functions", []):
                referenced_tokens.update(fn.get("calls", []))
                referenced_tokens.update(fn.get("args", []))
            for cls in m.get("classes", []):
                referenced_tokens.update(cls.get("bases", []))
                for meth in cls.get("methods", []):
                    referenced_tokens.update(meth.get("calls", []))
                    referenced_tokens.update(meth.get("args", []))

            unused_names = []
            for imp in imports:
                names = imp.get("names", [])
                for name in names:
                    clean_name = name.split(" as ")[-1].strip()
                    if clean_name and clean_name not in referenced_tokens and clean_name != "*":
                        unused_names.append((clean_name, imp.get("line")))

            if unused_names and len(unused_names) >= 2:
                sample_names = [f"'{n}' (line {ln})" for n, ln in unused_names[:4]]
                findings.append(ReviewFinding(
                    id=f"RULE-UNUSED-{mod_name.replace('.', '_')}",
                    title=f"Potentially Unused Imports in {mod_name}",
                    severity="low",
                    category="maintainability",
                    evidence=[
                        f"Unreferenced imported symbols: {', '.join(sample_names)}",
                    ],
                    explanation=f"Module '{mod_name}' imports symbols that appear unreferenced in its AST function calls, class bases, and method signatures.",
                    recommendation="Remove unused imports to avoid unnecessary dependencies and clean up namespace pollution.",
                    tradeoffs=[
                        "Verify whether imports are used for type checking or re-exported via __all__.",
                    ],
                    validation_steps=[
                        f"Run linter (e.g. flake8 or ruff) or tests on '{file_path}' after removing imports.",
                    ],
                    source_locations=[SourceLocation(file=file_path, symbol=mod_name)],
                    origin="static_rule",
                    rule_id="unused_imports",
                ))
                if len(findings) >= 5:
                    break
        return findings

    def _review_missing_docs(self, all_functions: List[Dict], modules: List[Dict]) -> List[ReviewFinding]:
        findings = []
        for fn in all_functions:
            cc = fn.get("complexity", 1)
            doc = fn.get("docstring", "").strip()
            name = fn.get("name", "")
            if cc >= 5 and not doc and not name.startswith("_"):
                qname = fn.get("qualified", name)
                mod_name = fn.get("module", "")
                mod_match = next((m for m in modules if m.get("module_name") == mod_name), None)
                file_path = fn.get("path") or (mod_match.get("path") if mod_match else mod_name)

                findings.append(ReviewFinding(
                    id=f"RULE-DOC-{qname.replace('.', '_')}",
                    title=f"Missing Documentation for Complex Public Symbol: {qname}",
                    severity="info",
                    category="maintainability",
                    evidence=[
                        f"Public function '{qname}' has cyclomatic complexity {cc} but lacks a docstring.",
                        f"Line: {fn.get('lineno')}",
                    ],
                    explanation="Public functions with significant branching logic require clear documentation of parameters, return types, expected errors, and side effects.",
                    recommendation=f"Add a standard Python docstring explaining purpose, parameters, and invariants for '{qname}'.",
                    tradeoffs=[],
                    validation_steps=[
                        f"Verify docstring conforms to PEP 257.",
                    ],
                    source_locations=[SourceLocation(
                        file=file_path,
                        line=fn.get("lineno"),
                        symbol=qname,
                    )],
                    origin="static_rule",
                    rule_id="missing_docstring",
                ))
                if len(findings) >= 5:
                    break
        return findings

    def _review_testing_gaps(self, modules: List[Dict], module_metrics: Any, layer_map: Dict[str, str]) -> List[ReviewFinding]:
        findings = []
        metrics_dict: Dict[str, Dict] = {}
        if isinstance(module_metrics, dict):
            metrics_dict = module_metrics
        elif isinstance(module_metrics, list):
            for item in module_metrics:
                if isinstance(item, dict) and "module" in item:
                    metrics_dict[item["module"]] = item

        for m in modules:
            mod_name = m.get("module_name", "")
            layer = layer_map.get(mod_name, "other")
            if layer in ("service", "data", "domain"):
                m_info = metrics_dict.get(mod_name, {})
                ca = m_info.get("afferent_coupling", 0)
                funcs = len(m.get("functions", [])) + sum(len(c.get("methods", [])) for c in m.get("classes", []))
                # Check if any incoming caller has 'test' in its name
                has_test_caller = False
                for other_m in modules:
                    if "test" in other_m.get("module_name", "").lower():
                        for imp in other_m.get("imports", []):
                            if mod_name in imp.get("module", ""):
                                has_test_caller = True
                                break

                if funcs >= 3 and not has_test_caller:
                    file_path = m.get("path", mod_name)
                    findings.append(ReviewFinding(
                        id=f"RULE-TESTGAP-{mod_name.replace('.', '_')}",
                        title=f"Untested Core Architecture Component: {mod_name}",
                        severity="medium",
                        category="testing",
                        evidence=[
                            f"Assigned architectural layer: '{layer}'",
                            f"Contains {funcs} functions/methods across {m.get('code_lines', 0)} code lines.",
                            "No test module in the scanned codebase directly imports or exercises this component.",
                        ],
                        explanation=f"Core architectural component '{mod_name}' contains substantial domain/service logic but lacks discovered automated test references.",
                        recommendation=f"Add unit test suite under tests/ targeting '{mod_name}' to establish baseline test coverage.",
                        tradeoffs=[
                            "Initial test setup may require mocking external dependencies.",
                        ],
                        validation_steps=[
                            f"Create tests/test_{mod_name.split('.')[-1]}.py and verify passing assertions.",
                        ],
                        source_locations=[SourceLocation(file=file_path, symbol=mod_name)],
                        origin="static_rule",
                        rule_id="untested_core_module",
                    ))
                    if len(findings) >= 4:
                        break
        return findings

    def _build_summary_narrative(self, summary: Dict, findings: List[ReviewFinding]) -> str:
        total_files = summary.get("total_files", 0)
        total_lines = summary.get("total_lines", 0)
        crit_count = sum(1 for f in findings if f.severity == "critical")
        high_count = sum(1 for f in findings if f.severity == "high")
        med_count = sum(1 for f in findings if f.severity == "medium")

        health = "Healthy" if crit_count == 0 and high_count == 0 else ("Action Required" if crit_count > 0 else "Moderate Attention")
        return (
            f"Deterministic architecture review evaluated {total_files} files ({total_lines} lines). "
            f"Overall Architecture Status: {health}. Detected {len(findings)} measurable findings "
            f"({crit_count} Critical, {high_count} High, {med_count} Medium). "
            "Prioritized improvements focus on eliminating dependency cycles, decomposing high-complexity functions, "
            "and reducing excessive coupling."
        )

    def _build_architecture_overview(self, summary: Dict, modules: List[Dict], layer_map: Dict[str, str]) -> str:
        layer_counts: Dict[str, int] = {}
        for l in layer_map.values():
            layer_counts[l] = layer_counts.get(l, 0) + 1
        layer_summary = ", ".join(f"{k}: {v}" for k, v in sorted(layer_counts.items()))
        return (
            f"System comprises {len(modules)} modules spanning architectural layers ({layer_summary or 'unclassified'}). "
            f"Average cyclomatic complexity is {summary.get('avg_complexity', 0):.2f}. "
            "Static boundaries follow standard Python package conventions."
        )

    def _extract_impact_dict(self, result: Dict[str, Any]) -> Dict[str, Dict]:
        raw = result.get("impact_summaries", {})
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, list):
            res = {}
            for item in raw:
                if isinstance(item, dict):
                    key = item.get("module") or item.get("target")
                    if key:
                        res[key] = item
            return res
        return {}

    def plan_change_local(self, proposed_change: str, result: Dict[str, Any]) -> ChangePlanResponse:
        """Deterministic Change Planner grounded in actual project import graph and metrics."""
        modules = result.get("modules", [])
        impact_dict = self._extract_impact_dict(result)
        query = proposed_change.lower()

        # Identify candidate modules mentioned or relevant to the proposed change
        matched_mods = []
        for m in modules:
            m_name = m.get("module_name", "")
            base = m_name.split(".")[-1].lower()
            if base in query or any(w in query for w in base.split("_")):
                matched_mods.append(m_name)

        if not matched_mods and modules:
            # Fallback to top modules by lines if no direct keyword match
            matched_mods = [modules[0].get("module_name")]

        affected_files: List[str] = []
        dependency_paths: List[str] = []
        recommended_tests: List[str] = []

        for m_name in matched_mods:
            mod_match = next((m for m in modules if m.get("module_name") == m_name), None)
            if mod_match:
                affected_files.append(mod_match.get("path", m_name))
            imp = impact_dict.get(m_name, {})
            for dep in imp.get("direct_dependents", []):
                dep_match = next((m for m in modules if m.get("module_name") == dep), None)
                if dep_match and dep_match.get("path") not in affected_files:
                    affected_files.append(dep_match.get("path", dep))
                dependency_paths.append(f"{m_name} \u2192 {dep} (direct dependent)")

            for t in imp.get("affected_tests", []):
                if t not in recommended_tests:
                    recommended_tests.append(t)

        if not recommended_tests:
            recommended_tests = ["tests/test_pipeline_e2e.py", "Run full project test suite"]

        sequence = [
            f"1. Extract and define shared interfaces/dataclasses for {', '.join(matched_mods)}.",
            f"2. Implement refactored internal logic in {', '.join(matched_mods)} without altering public signatures.",
            f"3. Incrementally update dependent call sites in {', '.join(affected_files[1:4]) if len(affected_files) > 1 else 'caller modules'}.",
            "4. Run unit and integration regression test suite to verify contract compatibility.",
        ]

        risks = [
            f"Breaking public API contracts for {len(affected_files)} identified downstream files.",
            "Potential runtime import errors if circular dependencies are inadvertently introduced.",
            "Regression in untested caller pathways.",
        ]

        unknowns = [
            "Are there dynamic `getattr` or reflection call sites not visible in static AST?",
            "Do external clients outside this repository depend on modified symbols?",
        ]

        return ChangePlanResponse(
            proposed_change=proposed_change,
            summary=f"Deterministic impact analysis identified {len(matched_mods)} primary target modules and {len(affected_files)} potentially affected files across the project dependency graph.",
            affected_files=affected_files,
            dependency_paths=dependency_paths[:6],
            implementation_sequence=sequence,
            potential_risks=risks,
            recommended_tests=recommended_tests,
            unknowns=unknowns,
            provider="local",
        )

    def ask_question_local(self, question: str, result: Dict[str, Any]) -> AskQuestionResponse:
        """Deterministic, grounded Q&A engine based on measured facts."""
        q = question.lower().strip()
        modules = result.get("modules", [])
        issues = result.get("issues", [])
        summary = result.get("summary", {})
        all_functions = result.get("all_functions", [])
        impact_dict = self._extract_impact_dict(result)
        risks = result.get("architecture_risks", {}).get("risk_backlog", [])

        grounded_facts: List[str] = []
        evidence_sources: List[str] = []
        answer = ""
        is_grounded = True

        # Q: Cycles / Circular dependencies
        if "cycle" in q or "circular" in q:
            cycle_issues = [iss for iss in issues if iss.get("rule") == "circular_dependency"]
            if cycle_issues:
                messages = [iss.get("message", "") for iss in cycle_issues]
                answer = f"Detected {len(cycle_issues)} circular dependency cycles: {'; '.join(messages)}."
                grounded_facts = messages
                evidence_sources = ["Issue detector (circular_dependency)"]
            else:
                answer = "Static analysis verified that there are zero circular dependencies in this project."
                grounded_facts = ["0 dependency cycles detected"]
                evidence_sources = ["Dependency graph cycle detection"]

        # Q: What depends on <module>?
        elif "depend" in q or "depends" in q or "who uses" in q:
            # find module
            target = next((m.get("module_name") for m in modules if m.get("module_name", "").lower() in q or m.get("module_name", "").split(".")[-1].lower() in q), None)
            if target:
                imp = impact_dict.get(target, {})
                direct = imp.get("direct_dependents", [])
                transitive = imp.get("transitive_dependents", [])
                answer = f"Module '{target}' is directly imported by {len(direct)} modules ({', '.join(direct) if direct else 'None'}). Transitive downstream dependents total {len(transitive)} modules."
                grounded_facts = [f"Direct dependents: {direct}", f"Transitive dependents: {transitive}"]
                evidence_sources = [f"Dependency graph analysis for {target}"]
            else:
                answer = "Please specify a module name present in the analyzed codebase to inspect its dependents."
                is_grounded = False

        # Q: Complex functions
        elif "complex" in q or "complexity" in q:
            top_fn = all_functions[:5]
            fn_strs = [f"{f.get('qualified')} (CC: {f.get('complexity')}, lines {f.get('lineno')}-{f.get('end_lineno')})" for f in top_fn]
            answer = f"The most complex functions measured by cyclomatic complexity are: {'; '.join(fn_strs)}."
            grounded_facts = fn_strs
            evidence_sources = ["Radon / AST Cyclomatic Complexity metrics"]

        # Q: Tests
        elif "test" in q:
            target = next((m.get("module_name") for m in modules if m.get("module_name", "").lower() in q or m.get("module_name", "").split(".")[-1].lower() in q), None)
            if target:
                imp = impact_dict.get(target, {})
                tests = imp.get("affected_tests", [])
                answer = f"For module '{target}', the recommended tests to run are: {', '.join(tests) if tests else 'Run full test suite (no targeted test module found)'}."
                grounded_facts = [f"Affected tests: {tests}"]
                evidence_sources = ["Impact analyzer test mapping"]
            else:
                test_mods = [m.get("module_name") for m in modules if "test" in m.get("module_name", "").lower()]
                answer = f"Scanned test modules in project: {', '.join(test_mods) if test_mods else 'No test modules found'}."
                grounded_facts = test_mods
                evidence_sources = ["Project module scanner"]

        else:
            answer = (
                f"ProjectDNA scanned {summary.get('total_files', 0)} files ({summary.get('total_lines', 0)} lines, "
                f"{summary.get('total_classes', 0)} classes, {summary.get('total_functions', 0)} functions). "
                f"Average cyclomatic complexity is {summary.get('avg_complexity', 0):.2f}. "
                "You can ask about specific module dependents, complexity, architectural risks, cycles, or recommended tests."
            )
            grounded_facts = [f"Total files: {summary.get('total_files')}", f"Total lines: {summary.get('total_lines')}"]
            evidence_sources = ["Project summary metrics"]

        return AskQuestionResponse(
            question=question,
            answer=answer,
            evidence_sources=evidence_sources,
            grounded_facts=grounded_facts,
            is_grounded=is_grounded,
            provider="local",
        )
