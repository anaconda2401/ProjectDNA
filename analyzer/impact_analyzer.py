"""
impact_analyzer.py
Performs change impact analysis on Python codebases.

Direction convention:
  In G_imports, an edge (A, B) represents "A imports B" (A depends on B).
  Therefore, if module B is modified:
    - Direct dependents: nodes A having an edge A -> B (in-edges of B).
    - Transitive dependents: all nodes that reach B via directed import paths.
      In the reversed graph G_rev, these are all nodes reachable from B.
"""

from typing import Dict, List, Set, Any, Optional, Tuple
import networkx as nx

from .project_model import ProjectModel, ModuleInfo, ClassInfo, FunctionInfo


def find_direct_dependents(target: str, G_imports: nx.DiGraph) -> List[str]:
    """Return sorted list of modules that directly import *target*."""
    if not G_imports.has_node(target):
        return []
    # In G_imports, (u, target) means u imports target
    return sorted(list(G_imports.predecessors(target)))


def find_transitive_dependents(
    target: str, G_imports: nx.DiGraph
) -> Tuple[List[str], Dict[str, List[str]]]:
    """
    Find indirect (depth >= 2) transitive dependents of *target* and dependency paths.
    Direct dependents (depth 1) are excluded from the transitive list to maintain
    a clear distinction between direct and transitive impact.
    Terminates safely even in the presence of circular dependencies.

    Returns:
      (transitive_dependents_list, paths_dict)
      where paths_dict contains formatted chains for all downstream dependents.
    """
    if not G_imports.has_node(target):
        return [], {}

    direct_set = set(find_direct_dependents(target, G_imports))

    # G_rev has edge (B, A) if A imports B.
    # Paths from target in G_rev: target -> ... -> dependent
    G_rev = G_imports.reverse()

    visited: Set[str] = {target}
    queue: List[str] = [target]
    paths: Dict[str, List[str]] = {target: [target]}
    all_downstream: List[str] = []

    while queue:
        curr = queue.pop(0)
        curr_path = paths[curr]

        for neighbor in G_rev.neighbors(curr):
            if neighbor not in visited:
                visited.add(neighbor)
                neighbor_path = curr_path + [neighbor]
                paths[neighbor] = neighbor_path
                all_downstream.append(neighbor)
                queue.append(neighbor)

    # Format paths nicely: dependent -> ... -> target (chain of who imports whom)
    formatted_paths: Dict[str, List[str]] = {}
    for dep in all_downstream:
        formatted_paths[dep] = list(reversed(paths[dep]))

    # Transitive dependents are those beyond depth 1 (i.e. not direct)
    indirect_transitive = [d for d in all_downstream if d not in direct_set]

    return sorted(indirect_transitive), formatted_paths


def find_affected_symbols(
    target: str,
    dependents: Set[str],
    model: ProjectModel,
    callers_map: Optional[Dict[str, List[str]]] = None,
    subclasses_map: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """
    Identify classes, functions, and tests in downstream dependents
    that statically interact with symbols in the target module.
    """
    callers_map = callers_map or {}
    subclasses_map = subclasses_map or {}

    target_mod: Optional[ModuleInfo] = model.module_by_name.get(target)
    if not target_mod:
        # Check by path
        target_mod = model.module_by_path.get(target)

    affected_classes: List[Dict[str, Any]] = []
    affected_functions: List[Dict[str, Any]] = []

    if target_mod and not target_mod.parse_error:
        # Check downstream subclasses
        for cls in target_mod.classes:
            subclasses = subclasses_map.get(cls.name, [])
            for sub in subclasses:
                sub_mod = sub.rsplit(".", 1)[0] if "." in sub else ""
                if sub_mod in dependents or sub_mod == target:
                    affected_classes.append({
                        "parent_class": cls.name,
                        "subclass": sub,
                        "subclass_module": sub_mod,
                        "parent_module": target_mod.module_name,
                        "line": cls.lineno,
                    })

        # Check downstream callers of target functions
        all_target_fns = list(target_mod.functions)
        for cls in target_mod.classes:
            all_target_fns.extend(cls.methods)

        for fn in all_target_fns:
            fn_key = fn.name
            callers = callers_map.get(fn_key, [])
            for caller in callers:
                caller_mod = caller.rsplit(".", 1)[0] if "." in caller else ""
                # caller might be Class.method or module.func
                if any(caller.startswith(f"{dep}.") for dep in dependents):
                    affected_functions.append({
                        "target_function": fn.name,
                        "caller": caller,
                        "line": fn.lineno,
                        "is_method": fn.is_method,
                    })

    return {
        "affected_classes": affected_classes,
        "affected_functions": affected_functions,
    }


def assess_change_risk(
    target: str,
    direct_count: int,
    transitive_count: int,
    affected_tests_count: int,
    in_cycle: bool,
    is_hub: bool,
    centrality: float,
) -> Tuple[str, str, int]:
    """
    Calculates a heuristic risk score (0-100), risk level (LOW/MEDIUM/HIGH/CRITICAL),
    and an explainable textual rationale.
    """
    score = 0
    reasons: List[str] = []

    # Direct dependents factor (up to 35 points)
    if direct_count == 0:
        score += 5
        reasons.append("No direct project dependents import this module.")
    elif direct_count <= 2:
        score += 15
        reasons.append(f"{direct_count} module(s) directly import this file.")
    elif direct_count <= 5:
        score += 25
        reasons.append(f"Moderate blast radius with {direct_count} direct dependents.")
    else:
        score += 35
        reasons.append(f"High direct coupling with {direct_count} direct dependents.")

    # Transitive dependents factor (up to 30 points)
    if transitive_count == 0:
        pass
    elif transitive_count <= 3:
        score += 10
        reasons.append(f"Small ripple effect ({transitive_count} transitive dependent(s)).")
    elif transitive_count <= 8:
        score += 20
        reasons.append(f"Substantial ripple effect across {transitive_count} downstream modules.")
    else:
        score += 30
        reasons.append(f"Broad cascading risk affecting {transitive_count} downstream modules.")

    # Structural hub & centrality (up to 15 points)
    if is_hub or centrality >= 0.2:
        score += 15
        reasons.append("Identified as a central architectural dependency hub.")

    # Circular dependency participation (up to 10 points)
    if in_cycle:
        score += 10
        reasons.append("Participates in a circular dependency cycle, creating tight coupling.")

    # Test coverage awareness (up to 10 points)
    if affected_tests_count > 0:
        reasons.append(f"{affected_tests_count} test suite(s) depend on this component for regression testing.")
    elif direct_count > 0:
        score += 10
        reasons.append("Warning: No downstream test modules detected directly covering this component.")

    # Determine level
    score = min(100, max(0, score))
    if score >= 75:
        level = "CRITICAL"
    elif score >= 50:
        level = "HIGH"
    elif score >= 25:
        level = "MEDIUM"
    else:
        level = "LOW"

    explanation = " ".join(reasons)
    return level, explanation, score


def analyze_change_impact(
    target_module: str,
    model: ProjectModel,
    G_imports: nx.DiGraph,
    layer_map: Optional[Dict[str, str]] = None,
    callers_map: Optional[Dict[str, List[str]]] = None,
    subclasses_map: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """
    Perform a complete change impact analysis for a chosen module.
    """
    layer_map = layer_map or {}
    callers_map = callers_map or {}
    subclasses_map = subclasses_map or {}

    # Match target module either by dotted name or by file path
    matched_name = target_module
    if not G_imports.has_node(matched_name):
        # Try finding by path in model
        for m in model.modules:
            if m.path == target_module or m.module_name == target_module:
                matched_name = m.module_name
                break

    if not G_imports.has_node(matched_name):
        return {
            "target": target_module,
            "found": False,
            "error": f"Module '{target_module}' not found in dependency graph.",
            "direct_dependents": [],
            "transitive_dependents": [],
            "dependency_paths": {},
            "affected_tests": [],
            "affected_classes": [],
            "affected_functions": [],
            "risk_level": "LOW",
            "risk_score": 0,
            "explanation": "Module is disconnected or not recognized as part of this project.",
            "graph_data": {"nodes": [], "links": []},
        }

    direct_deps = find_direct_dependents(matched_name, G_imports)
    transitive_deps, dep_paths = find_transitive_dependents(matched_name, G_imports)
    all_dependents_set = set(direct_deps) | set(transitive_deps)

    # Identify tests among dependents
    affected_tests: List[str] = []
    for dep in all_dependents_set:
        layer = layer_map.get(dep, "")
        if layer == "test" or "test" in dep.lower():
            affected_tests.append(dep)
    affected_tests.sort()

    # Identify affected symbols (classes & functions)
    symbols = find_affected_symbols(
        matched_name, all_dependents_set, model, callers_map, subclasses_map
    )

    # Graph centrality & cycle detection
    try:
        in_degree = G_imports.in_degree(matched_name)
        total_nodes = max(len(G_imports.nodes()), 1)
        centrality = round(in_degree / total_nodes, 3)
    except Exception:
        centrality = 0.0

    is_hub = in_degree >= 5

    # Check if in cycle
    in_cycle = False
    try:
        for cycle in nx.simple_cycles(G_imports):
            if matched_name in cycle and len(cycle) > 1:
                in_cycle = True
                break
    except Exception:
        in_cycle = False

    risk_level, explanation, risk_score = assess_change_risk(
        matched_name,
        len(direct_deps),
        len(transitive_deps),
        len(affected_tests),
        in_cycle,
        is_hub,
        centrality,
    )

    # Target module details
    target_mod = model.module_by_name.get(matched_name)
    target_info = {
        "module_name": matched_name,
        "path": target_mod.path if target_mod else "",
        "lines": target_mod.lines if target_mod else 0,
        "complexity": target_mod.complexity if target_mod else 0,
        "layer": layer_map.get(matched_name, "other"),
        "in_degree": in_degree,
        "out_degree": G_imports.out_degree(matched_name),
    }

    # Build local subgraph for visualization (target + all dependents + edges between them)
    impact_nodes_set = {matched_name} | all_dependents_set
    subgraph = G_imports.subgraph(impact_nodes_set)

    graph_nodes = []
    for node in subgraph.nodes():
        node_mod = model.module_by_name.get(node)
        graph_nodes.append({
            "id": node,
            "name": node.split(".")[-1],
            "path": node_mod.path if node_mod else "",
            "layer": layer_map.get(node, "other"),
            "role": "target" if node == matched_name else ("direct" if node in direct_deps else "transitive"),
            "is_test": node in affected_tests,
        })

    graph_links = []
    for src, dst in subgraph.edges():
        graph_links.append({
            "source": src,
            "target": dst,
            "label": "imports",
        })

    return {
        "target": matched_name,
        "found": True,
        "target_info": target_info,
        "direct_dependents": direct_deps,
        "transitive_dependents": transitive_deps,
        "total_affected_count": len(all_dependents_set),
        "dependency_paths": dep_paths,
        "affected_tests": affected_tests,
        "affected_classes": symbols["affected_classes"],
        "affected_functions": symbols["affected_functions"],
        "risk_level": risk_level,
        "risk_score": risk_score,
        "explanation": explanation,
        "is_hub": is_hub,
        "in_cycle": in_cycle,
        "centrality": centrality,
        "subgraph_data": {
            "nodes": graph_nodes,
            "links": graph_links,
        },
    }


def compute_all_impact_summaries(
    model: ProjectModel,
    G_imports: nx.DiGraph,
    layer_map: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """
    Computes lightweight change impact summaries for all modules in the project,
    sorted by risk score descending.
    """
    layer_map = layer_map or {}
    summaries: List[Dict[str, Any]] = []

    # Pre-calculate cycles once for efficiency
    cycle_nodes: Set[str] = set()
    try:
        for c in nx.simple_cycles(G_imports):
            if len(c) > 1:
                cycle_nodes.update(c)
    except Exception:
        pass

    total_nodes = max(len(G_imports.nodes()), 1)

    for m in model.modules:
        if m.parse_error:
            continue
        mod_name = m.module_name
        if not G_imports.has_node(mod_name):
            continue

        direct_deps = find_direct_dependents(mod_name, G_imports)
        transitive_deps, _ = find_transitive_dependents(mod_name, G_imports)
        all_deps = set(direct_deps) | set(transitive_deps)

        affected_tests = [
            dep for dep in all_deps
            if layer_map.get(dep) == "test" or "test" in dep.lower()
        ]

        in_deg = G_imports.in_degree(mod_name)
        out_deg = G_imports.out_degree(mod_name)
        centrality = round(in_deg / total_nodes, 3)
        is_hub = in_deg >= 5
        in_cycle = mod_name in cycle_nodes

        risk_level, explanation, risk_score = assess_change_risk(
            mod_name,
            len(direct_deps),
            len(transitive_deps),
            len(affected_tests),
            in_cycle,
            is_hub,
            centrality,
        )

        summaries.append({
            "module_name": mod_name,
            "path": m.path,
            "layer": layer_map.get(mod_name, "other"),
            "direct_count": len(direct_deps),
            "transitive_count": len(transitive_deps),
            "total_dependents": len(all_deps),
            "affected_tests_count": len(affected_tests),
            "risk_level": risk_level,
            "risk_score": risk_score,
            "explanation": explanation,
            "in_cycle": in_cycle,
            "in_degree": in_deg,
            "out_degree": out_deg,
        })

    summaries.sort(key=lambda s: (s["risk_score"], s["total_dependents"]), reverse=True)
    return summaries


def analyze_change_impact_from_result(
    result: Dict[str, Any], target_module: str
) -> Dict[str, Any]:
    """
    Convenience function to perform change impact analysis using a serialized
    pipeline result dictionary (e.g. loaded from the SQLite database).
    """
    from networkx.readwrite import json_graph

    # Reconstruct G_imports as directed DiGraph
    g_data = result.get("graph_imports", {})
    links_key = "links" if "links" in g_data else "edges"
    try:
        G_imports = json_graph.node_link_graph(
            g_data, directed=True, multigraph=False, edges=links_key
        )
    except Exception:
        # Fallback to empty directed graph if graph data is malformed
        G_imports = nx.DiGraph()
        for node in g_data.get("nodes", []):
            nid = node.get("id") or node.get("name")
            if nid:
                G_imports.add_node(nid)
        for link in g_data.get("links", []) or g_data.get("edges", []):
            src = link.get("source")
            dst = link.get("target")
            if src and dst:
                G_imports.add_edge(src, dst)

    layer_map = result.get("layer_map", {})

    # Reconstruct callers_map
    callers_map: Dict[str, List[str]] = {}
    for fn in result.get("all_functions", []):
        fn_name = fn.get("name", "")
        callers = fn.get("callers", [])
        if fn_name and callers:
            callers_map.setdefault(fn_name, []).extend(callers)

    # Reconstruct subclasses_map
    subclasses_map: Dict[str, List[str]] = {}
    for cls in result.get("all_classes", []):
        cls_name = cls.get("name", "")
        subclasses = cls.get("subclasses", [])
        if cls_name and subclasses:
            subclasses_map.setdefault(cls_name, []).extend(subclasses)

    # Reconstruct lightweight ProjectModel for symbol lookups
    model = ProjectModel(name=result.get("project_name", ""), root="")
    for m_data in result.get("modules", []):
        classes = []
        for c in m_data.get("classes", []):
            methods = [
                FunctionInfo(
                    name=meth.get("name", ""),
                    lineno=meth.get("lineno", 0),
                    end_lineno=meth.get("end_lineno", 0),
                    complexity=meth.get("complexity", 1),
                    is_method=True,
                    parent_class=c.get("name"),
                )
                for meth in c.get("methods", [])
            ]
            classes.append(ClassInfo(
                name=c.get("name", ""),
                lineno=c.get("lineno", 0),
                end_lineno=c.get("end_lineno", 0),
                bases=c.get("bases", []),
                methods=methods,
            ))

        functions = [
            FunctionInfo(
                name=f.get("name", ""),
                lineno=f.get("lineno", 0),
                end_lineno=f.get("end_lineno", 0),
                complexity=f.get("complexity", 1),
                is_method=False,
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
            classes=classes,
            functions=functions,
            parse_error=m_data.get("parse_error"),
        )
        model.modules.append(m_info)
    model.build_lookup()

    return analyze_change_impact(
        target_module=target_module,
        model=model,
        G_imports=G_imports,
        layer_map=layer_map,
        callers_map=callers_map,
        subclasses_map=subclasses_map,
    )
