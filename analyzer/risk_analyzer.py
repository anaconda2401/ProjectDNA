"""
risk_analyzer.py
Explainable Architecture Risk and Technical Debt Analysis (Phase 4).

Combines static metrics, graph centrality, circular imports, layer violations,
change impact blast radius, and test relationships into a prioritized, explainable risk backlog.
"""

from typing import Dict, List, Set, Tuple, Any, Optional
import networkx as nx

from .project_model import ProjectModel, ModuleInfo


DEFAULT_RISK_WEIGHTS: Dict[str, float] = {
    "cycles": 0.25,
    "complexity": 0.25,
    "coupling": 0.20,
    "layer_violations": 0.15,
    "test_absence": 0.15,
}


def _normalize_weights(weights: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """Validate and normalize weights so they sum to 1.0."""
    w = dict(weights or DEFAULT_RISK_WEIGHTS)
    total = sum(w.values())
    if total <= 0:
        return dict(DEFAULT_RISK_WEIGHTS)
    return {k: round(v / total, 4) for k, v in w.items()}


def compute_project_risk_score(
    model: ProjectModel,
    G_imports: nx.DiGraph,
    layer_map: Optional[Dict[str, str]] = None,
    issues: Optional[List[Dict[str, Any]]] = None,
    impact_summaries: Optional[List[Dict[str, Any]]] = None,
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    Computes an explainable, transparent heuristic architecture risk score (0-100)
    with a complete breakdown of normalized contributing factors.
    """
    layer_map = layer_map or {}
    issues = issues or []
    impact_summaries = impact_summaries or []
    norm_weights = _normalize_weights(weights)

    total_modules = max(len([m for m in model.modules if not m.parse_error]), 1)

    # 1. Cycles Factor (0.0 to 1.0)
    cycle_nodes: Set[str] = set()
    num_cycles = 0
    try:
        cycles = list(nx.simple_cycles(G_imports))
        for c in cycles:
            if len(c) > 1:
                num_cycles += 1
                cycle_nodes.update(c)
    except Exception:
        pass

    if num_cycles > 0:
        cycles_factor = min(1.0, 0.4 + (num_cycles * 0.15) + (len(cycle_nodes) / (total_modules * 2)))
    else:
        cycles_factor = 0.0

    # 2. Complexity Factor (0.0 to 1.0)
    high_comp_modules = sum(
        1 for m in model.modules
        if not m.parse_error and (m.complexity >= 20 or any(fn.complexity >= 10 for fn in m.functions))
    )
    complexity_factor = min(1.0, round(high_comp_modules / max(total_modules * 0.6, 1), 3))

    # 3. Coupling Factor (0.0 to 1.0)
    hub_count = sum(
        1 for node in G_imports.nodes()
        if G_imports.in_degree(node) >= 5 or G_imports.out_degree(node) >= 6
    )
    coupling_factor = min(1.0, round(hub_count / max(total_modules * 0.4, 1), 3))

    # 4. Layer Violations Factor (0.0 to 1.0)
    layer_violations = sum(
        1 for iss in issues
        if iss.get("category") == "architecture" and "layer" in iss.get("title", "").lower()
    )
    layer_factor = min(1.0, round(layer_violations / max(total_modules * 0.3, 1), 3))

    # 5. Test Absence Factor (0.0 to 1.0)
    core_modules = [
        m.module_name for m in model.modules
        if not m.parse_error and layer_map.get(m.module_name) in {"service", "data", "presentation"}
    ]
    untested_core = 0
    if core_modules:
        for s in impact_summaries:
            if s.get("module_name") in core_modules and s.get("affected_tests_count", 0) == 0:
                untested_core += 1
        test_absence_factor = min(1.0, round(untested_core / len(core_modules), 3))
    else:
        test_absence_factor = 0.0

    factors = {
        "cycles": round(cycles_factor, 3),
        "complexity": round(complexity_factor, 3),
        "coupling": round(coupling_factor, 3),
        "layer_violations": round(layer_factor, 3),
        "test_absence": round(test_absence_factor, 3),
    }

    # Calculate weighted composite score (0-100)
    raw_score = sum(norm_weights[k] * factors[k] for k in norm_weights) * 100
    risk_score = round(min(100.0, max(0.0, raw_score)), 1)

    if risk_score >= 70:
        risk_level = "CRITICAL"
    elif risk_score >= 45:
        risk_level = "HIGH"
    elif risk_score >= 20:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    # Factor contribution breakdown for transparency
    contributions = {
        k: round(norm_weights[k] * factors[k] * 100, 1)
        for k in norm_weights
    }

    return {
        "risk_score": risk_score,
        "risk_level": risk_level,
        "factors": factors,
        "weights": norm_weights,
        "contributions": contributions,
        "cycles_count": num_cycles,
        "cycle_nodes_count": len(cycle_nodes),
        "high_complexity_modules": high_comp_modules,
        "hub_modules_count": hub_count,
        "layer_violations_count": layer_violations,
        "untested_core_modules": untested_core,
    }


def analyze_component_risks(
    model: ProjectModel,
    G_imports: nx.DiGraph,
    layer_map: Optional[Dict[str, str]] = None,
    issues: Optional[List[Dict[str, Any]]] = None,
    impact_summaries: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Evaluates risk for every individual module and returns a prioritized,
    explainable list of architectural risk backlog items.
    """
    layer_map = layer_map or {}
    issues = issues or []
    impact_summaries_map = {s["module_name"]: s for s in (impact_summaries or [])}

    # Find cycle membership
    cycle_nodes: Set[str] = set()
    try:
        for c in nx.simple_cycles(G_imports):
            if len(c) > 1:
                cycle_nodes.update(c)
    except Exception:
        pass

    component_risks: List[Dict[str, Any]] = []

    for m in model.modules:
        if m.parse_error:
            # Parse error is an automatic Critical risk
            component_risks.append({
                "component": m.module_name,
                "path": m.path,
                "priority": "CRITICAL",
                "score": 95,
                "rule": "Syntax/Parse Error Integrity",
                "evidence": f"File failed to parse: {m.parse_error}",
                "why_it_matters": "Components with syntax errors cannot be executed or analyzed statically.",
                "recommended_action": "Fix syntax error immediately to restore AST analysis and compilation.",
                "location": f"{m.path}:1",
            })
            continue

        mod_name = m.module_name
        impact = impact_summaries_map.get(mod_name, {})
        in_cycle = mod_name in cycle_nodes
        in_deg = G_imports.in_degree(mod_name) if G_imports.has_node(mod_name) else 0
        out_deg = G_imports.out_degree(mod_name) if G_imports.has_node(mod_name) else 0
        total_dependents = impact.get("total_dependents", in_deg)
        affected_tests = impact.get("affected_tests_count", 0)

        # Find module-specific issues
        mod_issues = [iss for iss in issues if iss.get("location", "").startswith(m.path) or iss.get("location") == mod_name]
        layer_viol = any("layer" in iss.get("title", "").lower() for iss in mod_issues)

        # Multi-factor component score
        mod_score = 0
        evidence_points: List[str] = []
        action_points: List[str] = []

        # Complexity (up to 30)
        if m.complexity >= 25:
            mod_score += 30
            evidence_points.append(f"High cyclomatic complexity ({m.complexity}).")
            action_points.append("Decompose complex functions into smaller modular units.")
        elif m.complexity >= 12:
            mod_score += 15
            evidence_points.append(f"Moderate complexity ({m.complexity}).")

        # Downstream Blast Radius / Coupling (up to 30)
        if total_dependents >= 6:
            mod_score += 30
            evidence_points.append(f"High blast radius with {total_dependents} downstream dependents.")
            action_points.append("Introduce an abstract interface to decouple downstream callers.")
        elif total_dependents >= 3:
            mod_score += 15
            evidence_points.append(f"{total_dependents} downstream dependents.")

        # Dependency Cycle (up to 25)
        if in_cycle:
            mod_score += 25
            evidence_points.append("Participates in a circular dependency cycle.")
            action_points.append("Break cycle using dependency injection or extracting shared models.")

        # Layer violation (up to 15)
        if layer_viol:
            mod_score += 15
            evidence_points.append("Violates architectural layer boundaries.")
            action_points.append("Refactor imports so lower layers do not import higher layers.")

        # Test coverage absence for core module (up to 15)
        layer = layer_map.get(mod_name, "other")
        if layer in {"service", "data", "presentation"} and affected_tests == 0 and total_dependents > 0:
            mod_score += 15
            evidence_points.append("No downstream automated test modules cover this component.")
            action_points.append("Write unit/integration tests before modifying this component.")

        mod_score = min(100, mod_score)

        if mod_score >= 65:
            priority = "CRITICAL"
        elif mod_score >= 45:
            priority = "HIGH"
        elif mod_score >= 25:
            priority = "MEDIUM"
        else:
            priority = "LOW"

        # Only list components that exhibit at least moderate risk or issues
        if mod_score >= 20 or in_cycle or layer_viol:
            why = (
                "The combination of high coupling, complexity, and structural entanglement increases "
                "the likelihood of unintended regressions during maintenance."
            )
            rec_action = " ".join(action_points) if action_points else "Monitor component metrics during future feature additions."

            component_risks.append({
                "component": mod_name,
                "path": m.path,
                "layer": layer,
                "priority": priority,
                "score": mod_score,
                "rule": "Heuristic Composite Risk Formula (Complexity + Coupling + Cycles + Layers)",
                "evidence": " ".join(evidence_points),
                "why_it_matters": why,
                "recommended_action": rec_action,
                "location": f"{m.path}:1",
            })

    component_risks.sort(key=lambda x: (x["score"], x["priority"] == "CRITICAL"), reverse=True)
    return component_risks


def analyze_architecture_risks(
    model: ProjectModel,
    G_imports: nx.DiGraph,
    layer_map: Optional[Dict[str, str]] = None,
    issues: Optional[List[Dict[str, Any]]] = None,
    impact_summaries: Optional[List[Dict[str, Any]]] = None,
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    Main entry point for Phase 4 architecture risk and technical debt analysis.
    """
    summary = compute_project_risk_score(
        model, G_imports, layer_map, issues, impact_summaries, weights
    )
    items = analyze_component_risks(
        model, G_imports, layer_map, issues, impact_summaries
    )

    return {
        "status": "ANALYZED",
        "project_risk": summary,
        "prioritized_risks": items,
        "total_risks_found": len(items),
        "critical_count": sum(1 for i in items if i["priority"] == "CRITICAL"),
        "high_count": sum(1 for i in items if i["priority"] == "HIGH"),
        "medium_count": sum(1 for i in items if i["priority"] == "MEDIUM"),
        "low_count": sum(1 for i in items if i["priority"] == "LOW"),
    }


def analyze_architecture_risks_from_result(
    result: Dict[str, Any], weights: Optional[Dict[str, float]] = None
) -> Dict[str, Any]:
    """
    Convenience wrapper to run risk analysis on a serialized analysis result.
    """
    from networkx.readwrite import json_graph
    from .project_model import ClassInfo, FunctionInfo

    # Reconstruct G_imports
    g_data = result.get("graph_imports", {})
    links_key = "links" if "links" in g_data else "edges"
    try:
        G_imports = json_graph.node_link_graph(
            g_data, directed=True, multigraph=False, edges=links_key
        )
    except Exception:
        G_imports = nx.DiGraph()
        for link in g_data.get("links", []) or g_data.get("edges", []):
            src = link.get("source")
            dst = link.get("target")
            if src and dst:
                G_imports.add_edge(src, dst)

    # Reconstruct ProjectModel
    model = ProjectModel(name=result.get("project_name", ""), root="")
    for m_data in result.get("modules", []):
        functions = [
            FunctionInfo(
                name=f.get("name", ""),
                lineno=f.get("lineno", 0),
                end_lineno=f.get("end_lineno", 0),
                complexity=f.get("complexity", 1),
            )
            for f in m_data.get("functions", [])
        ]
        m_info = ModuleInfo(
            path=m_data.get("path", ""),
            abs_path="",
            module_name=m_data.get("module_name", ""),
            lines=m_data.get("lines", 0),
            code_lines=m_data.get("code_lines", 0),
            complexity=m_data.get("complexity", 0),
            functions=functions,
            parse_error=m_data.get("parse_error"),
        )
        model.modules.append(m_info)
    model.build_lookup()

    return analyze_architecture_risks(
        model=model,
        G_imports=G_imports,
        layer_map=result.get("layer_map", {}),
        issues=result.get("issues", []),
        impact_summaries=result.get("impact_summaries", []),
        weights=weights,
    )
