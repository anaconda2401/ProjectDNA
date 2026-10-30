"""
module_discovery.py
Automatic module discovery, logical clustering, and architecture refactoring recommendations.

Techniques:
  - Multi-factor affinity: Structural import coupling, shared dependencies, and domain vocabulary similarity.
  - Deterministic clustering using graph modularity / connected components.
  - Detection of misplaced modules and split-responsibility candidates.
  - Safeguards: small project threshold, generic stopword filtering, non-destructive recommendations only.
"""

import re
from typing import Dict, List, Set, Tuple, Any, Optional
import networkx as nx

from .project_model import ProjectModel, ModuleInfo


# Generic software engineering tokens that do not indicate domain cohesion
GENERIC_STOPWORDS: Set[str] = {
    "self", "cls", "init", "none", "true", "false", "return", "def", "class",
    "get", "set", "utils", "util", "helper", "helpers", "common", "base", "core",
    "manager", "data", "file", "test", "tests", "main", "run", "app", "model",
    "item", "obj", "value", "key", "result", "args", "kwargs", "list", "dict",
    "str", "int", "bool", "type", "error", "exception", "handler", "config",
    "import", "from", "as", "is", "in", "for", "while", "if", "else", "elif",
    "try", "except", "finally", "with", "yield", "pass", "break", "continue",
}


def _tokenize(text: str) -> Set[str]:
    """Extract domain tokens from identifier text, splitting snake_case and camelCase."""
    # Split camelCase
    s1 = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', text)
    s2 = re.sub(r'([a-z\d])([A-Z])', r'\1_\2', s1)
    # Split by non-alphanumeric
    words = re.split(r'[^a-zA-Z0-9]+', s2.lower())
    return {w for w in words if len(w) > 2 and w not in GENERIC_STOPWORDS}


def _extract_module_tokens(m: ModuleInfo) -> Set[str]:
    """Collect domain-specific vocabulary from module name, classes, and functions."""
    tokens: Set[str] = set()

    # Module name
    tokens.update(_tokenize(m.module_name))

    # Classes and methods
    for cls in m.classes:
        tokens.update(_tokenize(cls.name))
        for meth in cls.methods:
            tokens.update(_tokenize(meth.name))

    # Standalone functions
    for fn in m.functions:
        tokens.update(_tokenize(fn.name))

    return tokens


def _extract_module_imports(m: ModuleInfo) -> Set[str]:
    """Collect set of external and internal targets imported by this module."""
    imported = set()
    for imp in m.imports:
        if imp.module:
            imported.add(imp.module.split(".")[0])
        for name in imp.names:
            imported.add(name)
    return imported


def _jaccard(s1: Set[str], s2: Set[str]) -> float:
    """Calculate Jaccard similarity index between two sets."""
    if not s1 or not s2:
        return 0.0
    intersection = len(s1 & s2)
    union = len(s1 | s2)
    return round(intersection / union, 3) if union > 0 else 0.0


def compute_module_affinity(
    m1: ModuleInfo,
    m2: ModuleInfo,
    G_imports: nx.DiGraph,
) -> Tuple[float, Dict[str, Any]]:
    """
    Compute affinity between two modules based on:
      - Direct structural coupling (import edges)
      - Shared dependencies
      - Domain vocabulary similarity
    """
    # 1. Structural coupling (0.4)
    has_m1_to_m2 = G_imports.has_edge(m1.module_name, m2.module_name)
    has_m2_to_m1 = G_imports.has_edge(m2.module_name, m1.module_name)

    if has_m1_to_m2 and has_m2_to_m1:
        coupling_score = 1.0
    elif has_m1_to_m2 or has_m2_to_m1:
        coupling_score = 0.7
    else:
        coupling_score = 0.0

    # 2. Shared dependencies (0.3)
    deps1 = _extract_module_imports(m1)
    deps2 = _extract_module_imports(m2)
    shared_deps = sorted(list(deps1 & deps2))
    dep_score = _jaccard(deps1, deps2)

    # 3. Domain vocabulary similarity (0.3)
    tokens1 = _extract_module_tokens(m1)
    tokens2 = _extract_module_tokens(m2)
    shared_tokens = sorted(list(tokens1 & tokens2))
    vocab_score = _jaccard(tokens1, tokens2)

    total_affinity = round(
        0.4 * coupling_score + 0.3 * dep_score + 0.3 * vocab_score, 3
    )

    evidence = {
        "direct_coupling": has_m1_to_m2 or has_m2_to_m1,
        "mutual_coupling": has_m1_to_m2 and has_m2_to_m1,
        "shared_dependencies": shared_deps[:8],
        "shared_domain_tokens": shared_tokens[:8],
        "coupling_score": coupling_score,
        "dep_score": dep_score,
        "vocab_score": vocab_score,
    }

    return total_affinity, evidence


def discover_refactoring_recommendations(
    model: ProjectModel,
    G_imports: nx.DiGraph,
    min_project_modules: int = 4,
) -> Dict[str, Any]:
    """
    Analyzes project modules and produces explainable refactoring recommendations.
    Safeguards:
      - Reports INSUFFICIENT_EVIDENCE if project has fewer than min_project_modules.
      - Uses non-destructive suggestions only.
    """
    valid_modules = [m for m in model.modules if not m.parse_error]
    total_valid = len(valid_modules)

    if total_valid < min_project_modules:
        return {
            "status": "INSUFFICIENT_EVIDENCE",
            "message": f"Project contains only {total_valid} modules. Automatic clustering and package discovery requires at least {min_project_modules} modules to be meaningful.",
            "candidate_groups": [],
            "misplaced_modules": [],
            "split_module_candidates": [],
            "package_coupling_warnings": [],
        }

    # Build pairwise affinity graph
    affinity_graph = nx.Graph()
    for m in valid_modules:
        affinity_graph.add_node(m.module_name, path=m.path)

    pairwise_evidence: Dict[Tuple[str, str], Dict[str, Any]] = {}
    n = len(valid_modules)

    for i in range(n):
        for j in range(i + 1, n):
            m1 = valid_modules[i]
            m2 = valid_modules[j]
            aff, ev = compute_module_affinity(m1, m2, G_imports)
            pairwise_evidence[(m1.module_name, m2.module_name)] = ev
            pairwise_evidence[(m2.module_name, m1.module_name)] = ev

            # Add edge if affinity is meaningful
            if aff >= 0.25:
                affinity_graph.add_edge(m1.module_name, m2.module_name, weight=aff)

    # -----------------------------------------------------------------------
    # 1. Candidate Package / Module Clusters
    # -----------------------------------------------------------------------
    candidate_groups: List[Dict[str, Any]] = []

    # Detect cohesive clusters using connected components or community detection
    components = list(nx.connected_components(affinity_graph))

    for comp in components:
        if len(comp) < 2 or len(comp) > 15:
            continue

        comp_list = sorted(list(comp))
        # Compute internal cohesion
        internal_weights = []
        for i in range(len(comp_list)):
            for j in range(i + 1, len(comp_list)):
                u, v = comp_list[i], comp_list[j]
                if affinity_graph.has_edge(u, v):
                    internal_weights.append(affinity_graph[u][v]["weight"])

        avg_cohesion = round(sum(internal_weights) / max(len(internal_weights), 1), 2)
        if avg_cohesion < 0.28:
            continue

        # Common domain tokens across the cluster
        comp_tokens: Dict[str, int] = {}
        candidate_files = []
        for mod_name in comp_list:
            mod_info = model.module_by_name.get(mod_name)
            if mod_info:
                candidate_files.append(mod_info.path)
                for t in _extract_module_tokens(mod_info):
                    comp_tokens[t] = comp_tokens.get(t, 0) + 1

        top_tokens = [
            t for t, count in sorted(comp_tokens.items(), key=lambda x: x[1], reverse=True)
            if count >= 2
        ][:5]

        # Suggested group name
        suggested_name = top_tokens[0] if top_tokens else comp_list[0].split(".")[-1]

        # Confidence rating
        if avg_cohesion >= 0.45:
            confidence = "HIGH"
        elif avg_cohesion >= 0.28:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        # Construct evidence list
        evidence_items = [
            f"Average internal architectural affinity of {avg_cohesion:.2f}.",
            f"{len(internal_weights)} direct structural or dependency connections within the cluster.",
        ]
        if top_tokens:
            evidence_items.append(f"Shared domain concepts: {', '.join(top_tokens)}.")

        candidate_groups.append({
            "suggested_group": suggested_name,
            "candidate_modules": comp_list,
            "candidate_files": candidate_files,
            "cohesion_score": avg_cohesion,
            "confidence": confidence,
            "shared_tokens": top_tokens,
            "evidence": evidence_items,
            "benefits": "Colocating these modules promotes high cohesion, clarifies domain boundaries, and simplifies dependency tracking.",
            "limitations": "Ensure interface boundaries remain distinct to avoid circular dependencies between group members.",
        })

    candidate_groups.sort(key=lambda g: g["cohesion_score"], reverse=True)

    # -----------------------------------------------------------------------
    # 2. Misplaced Modules (Files physically distant from their logical cluster)
    # -----------------------------------------------------------------------
    misplaced_modules: List[Dict[str, Any]] = []

    for grp in candidate_groups:
        cluster_mods = grp["candidate_modules"]
        if len(cluster_mods) < 2:
            continue

        # Count packages in this cluster
        pkg_counts: Dict[str, int] = {}
        for mod_name in cluster_mods:
            pkg = ".".join(mod_name.split(".")[:-1]) or "root"
            pkg_counts[pkg] = pkg_counts.get(pkg, 0) + 1

        # Find dominant package
        dominant_pkg, dominant_count = max(pkg_counts.items(), key=lambda x: x[1])

        # Flag any module in this cluster located in a different package
        if dominant_count >= 2:
            for mod_name in cluster_mods:
                current_pkg = ".".join(mod_name.split(".")[:-1]) or "root"
                if current_pkg != dominant_pkg:
                    mod_info = model.module_by_name.get(mod_name)
                    file_path = mod_info.path if mod_info else mod_name

                    # Calculate affinity with dominant package members
                    peers = [p for p in cluster_mods if (".".join(p.split(".")[:-1]) or "root") == dominant_pkg]
                    peer_aff = sum(
                        affinity_graph[mod_name][p]["weight"]
                        for p in peers
                        if affinity_graph.has_edge(mod_name, p)
                    ) / max(len(peers), 1)

                    misplaced_modules.append({
                        "module_name": mod_name,
                        "file_path": file_path,
                        "current_package": current_pkg,
                        "recommended_group": grp["suggested_group"],
                        "recommended_package": dominant_pkg,
                        "cluster_affinity": round(peer_aff, 2),
                        "confidence": grp["confidence"],
                        "evidence": [
                            f"Physically located in package '{current_pkg}', but strongly coupled with logical '{grp['suggested_group']}' cluster in '{dominant_pkg}'.",
                            f"Has affinity score of {peer_aff:.2f} with {len(peers)} modules in '{dominant_pkg}'.",
                            f"Shares domain concepts ({', '.join(grp['shared_tokens'])}) with target package.",
                        ],
                        "benefits": f"Relocating into package '{dominant_pkg}' aligns physical directory structure with logical architecture.",
                        "limitations": "Ensure any external imports referencing the old path are updated upon relocation.",
                    })

    # -----------------------------------------------------------------------
    # 3. Split Module Candidates (Low Internal Cohesion / God Modules)
    # -----------------------------------------------------------------------
    split_module_candidates: List[Dict[str, Any]] = []

    for m in valid_modules:
        if m.code_lines >= 250 and (len(m.classes) >= 3 or len(m.functions) >= 8):
            # Check internal callers connectivity
            all_fns = list(m.functions) + [meth for c in m.classes for meth in c.methods]
            fn_names = {f.name for f in all_fns}
            cross_calls = 0
            for f in all_fns:
                for c in f.calls:
                    if c in fn_names or c.split(".")[-1] in fn_names:
                        cross_calls += 1

            if cross_calls <= 2 and len(all_fns) >= 6:
                split_module_candidates.append({
                    "module_name": m.module_name,
                    "file_path": m.path,
                    "code_lines": m.code_lines,
                    "classes_count": len(m.classes),
                    "functions_count": len(m.functions),
                    "internal_calls": cross_calls,
                    "confidence": "MEDIUM",
                    "evidence": [
                        f"{m.code_lines} lines with {len(m.classes)} classes and {len(m.functions)} functions.",
                        f"Only {cross_calls} internal call relationship(s) among {len(all_fns)} callable members.",
                        "Functions and classes appear to handle disparate responsibilities.",
                    ],
                    "recommendation": f"Consider decomposing `{m.path}` into 2 or more specialized submodules organized by functional domain.",
                    "benefits": "Improves single-responsibility compliance, reduces merge conflicts, and simplifies unit testing.",
                })

    # -----------------------------------------------------------------------
    # 4. Excessive Cross-Package Coupling
    # -----------------------------------------------------------------------
    package_coupling_warnings: List[Dict[str, Any]] = []
    pkg_edges: Dict[Tuple[str, str], int] = {}

    for src, dst in G_imports.edges():
        src_pkg = ".".join(src.split(".")[:-1]) or "root"
        dst_pkg = ".".join(dst.split(".")[:-1]) or "root"
        if src_pkg != dst_pkg:
            pair = (src_pkg, dst_pkg)
            pkg_edges[pair] = pkg_edges.get(pair, 0) + 1

    for (p1, p2), count in sorted(pkg_edges.items(), key=lambda x: x[1], reverse=True):
        if count >= 6:
            # Check if bidirectional
            reverse_count = pkg_edges.get((p2, p1), 0)
            package_coupling_warnings.append({
                "source_package": p1,
                "target_package": p2,
                "dependency_count": count,
                "is_bidirectional": reverse_count > 0,
                "reverse_count": reverse_count,
                "severity": "HIGH" if reverse_count > 0 else "MEDIUM",
                "evidence": f"{count} imports from '{p1}' into '{p2}'" + (f" and {reverse_count} reverse imports." if reverse_count > 0 else "."),
                "recommendation": f"High coupling between package '{p1}' and '{p2}'. Consider introducing a shared interface or facade.",
            })

    return {
        "status": "ANALYZED",
        "total_modules_analyzed": total_valid,
        "candidate_groups": candidate_groups,
        "misplaced_modules": misplaced_modules,
        "split_module_candidates": split_module_candidates,
        "package_coupling_warnings": package_coupling_warnings,
    }


def discover_refactoring_from_result(
    result: Dict[str, Any], min_project_modules: int = 4
) -> Dict[str, Any]:
    """
    Convenience wrapper to run module discovery directly on a serialized analysis result.
    """
    from networkx.readwrite import json_graph
    from .project_model import ImportInfo, ClassInfo, FunctionInfo

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
        classes = []
        for c in m_data.get("classes", []):
            methods = [
                FunctionInfo(
                    name=meth.get("name", ""),
                    lineno=meth.get("lineno", 0),
                    end_lineno=meth.get("end_lineno", 0),
                    calls=meth.get("calls", []),
                    is_method=True,
                )
                for meth in c.get("methods", [])
            ]
            classes.append(ClassInfo(
                name=c.get("name", ""),
                lineno=c.get("lineno", 0),
                end_lineno=c.get("end_lineno", 0),
                methods=methods,
            ))

        functions = [
            FunctionInfo(
                name=f.get("name", ""),
                lineno=f.get("lineno", 0),
                end_lineno=f.get("end_lineno", 0),
                calls=f.get("calls", []),
                is_method=False,
            )
            for f in m_data.get("functions", [])
        ]

        imports = [
            ImportInfo(
                module=imp.get("module", ""),
                names=imp.get("names", []),
            )
            for imp in m_data.get("imports", [])
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
            imports=imports,
            parse_error=m_data.get("parse_error"),
        )
        model.modules.append(m_info)
    model.build_lookup()

    return discover_refactoring_recommendations(model, G_imports, min_project_modules=min_project_modules)
