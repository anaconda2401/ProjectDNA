"""
drift_detector.py
Detects architectural drift and structural shifts between two codebase snapshots.

Compares:
  - Added, removed, modified, and renamed modules.
  - Added and removed dependency relationships.
  - Newly introduced and resolved circular dependencies.
  - Changes in complexity, lines of code, and coupling.
  - New and resolved architectural issues.
"""

from typing import Dict, List, Set, Tuple, Any, Optional
import networkx as nx
from networkx.readwrite import json_graph


def _normalize_cycle(cycle: List[str]) -> Tuple[str, ...]:
    """Rotate cycle list so the lexicographically smallest element is first."""
    if not cycle:
        return ()
    min_idx = cycle.index(min(cycle))
    rotated = cycle[min_idx:] + cycle[:min_idx]
    return tuple(rotated)


def _extract_cycles(graph_data: Dict[str, Any]) -> Set[Tuple[str, ...]]:
    """Extract normalized cycle tuples from node-link graph data."""
    if not graph_data:
        return set()
    links_key = "links" if "links" in graph_data else "edges"
    try:
        G = json_graph.node_link_graph(graph_data, directed=True, multigraph=False, edges=links_key)
        cycles = nx.simple_cycles(G)
        return {_normalize_cycle(c) for c in cycles if len(c) > 1}
    except Exception:
        return set()


def _extract_edges(graph_data: Dict[str, Any]) -> Set[Tuple[str, str]]:
    """Extract (source, target) tuples from graph data."""
    if not graph_data:
        return set()
    links = graph_data.get("links", []) or graph_data.get("edges", [])
    edges = set()
    for lnk in links:
        src = lnk.get("source")
        dst = lnk.get("target")
        if src and dst:
            edges.add((src, dst))
    return edges


def _issue_key(issue: Dict[str, Any]) -> Tuple[str, str, str]:
    """Create a stable hashable key for an architectural issue."""
    return (
        issue.get("category", ""),
        issue.get("title", ""),
        issue.get("location", ""),
    )


def compare_snapshots(
    base: Dict[str, Any],
    target: Dict[str, Any],
    base_label: str = "Base",
    target_label: str = "Target",
) -> Dict[str, Any]:
    """
    Deterministically compares two project analysis snapshots.
    Returns a comprehensive drift report.
    """
    # 1. Module comparison
    base_mods = {m.get("module_name", ""): m for m in base.get("modules", []) if m.get("module_name")}
    target_mods = {m.get("module_name", ""): m for m in target.get("modules", []) if m.get("module_name")}

    base_names = set(base_mods.keys())
    target_names = set(target_mods.keys())

    raw_added_names = sorted(list(target_names - base_names))
    raw_removed_names = sorted(list(base_names - target_names))
    common_names = sorted(list(base_names & target_names))

    # Detect renames via file hash matching where available
    renamed_modules: List[Dict[str, Any]] = []
    added_names = list(raw_added_names)
    removed_names = list(raw_removed_names)

    # Check for identical file_hashes between removed and added
    for rem_name in list(removed_names):
        rem_mod = base_mods[rem_name]
        rem_hash = rem_mod.get("file_hash")
        if not rem_hash:
            continue
        for add_name in list(added_names):
            add_mod = target_mods[add_name]
            add_hash = add_mod.get("file_hash")
            if add_hash and rem_hash == add_hash:
                renamed_modules.append({
                    "old_module": rem_name,
                    "new_module": add_name,
                    "old_path": rem_mod.get("path", ""),
                    "new_path": add_mod.get("path", ""),
                    "file_hash": rem_hash,
                })
                removed_names.remove(rem_name)
                added_names.remove(add_name)
                break

    added_modules = [
        {
            "module_name": name,
            "path": target_mods[name].get("path", ""),
            "lines": target_mods[name].get("lines", 0),
            "complexity": target_mods[name].get("complexity", 0),
            "layer": target_mods[name].get("layer", "other"),
        }
        for name in sorted(added_names)
    ]

    removed_modules = [
        {
            "module_name": name,
            "path": base_mods[name].get("path", ""),
            "lines": base_mods[name].get("lines", 0),
            "complexity": base_mods[name].get("complexity", 0),
            "layer": base_mods[name].get("layer", "other"),
        }
        for name in sorted(removed_names)
    ]

    modified_modules: List[Dict[str, Any]] = []
    for name in common_names:
        bm = base_mods[name]
        tm = target_mods[name]

        b_hash = bm.get("file_hash")
        t_hash = tm.get("file_hash")

        # Determine if modified
        is_mod = False
        if b_hash and t_hash:
            is_mod = (b_hash != t_hash)
        else:
            # Fallback if hash not available
            is_mod = (
                bm.get("lines") != tm.get("lines")
                or bm.get("complexity") != tm.get("complexity")
                or bm.get("code_lines") != tm.get("code_lines")
            )

        if is_mod:
            modified_modules.append({
                "module_name": name,
                "path": tm.get("path", ""),
                "old_lines": bm.get("lines", 0),
                "new_lines": tm.get("lines", 0),
                "lines_diff": tm.get("lines", 0) - bm.get("lines", 0),
                "old_complexity": bm.get("complexity", 0),
                "new_complexity": tm.get("complexity", 0),
                "complexity_diff": tm.get("complexity", 0) - bm.get("complexity", 0),
                "old_hash": b_hash or "N/A",
                "new_hash": t_hash or "N/A",
            })

    modified_modules.sort(key=lambda m: abs(m["complexity_diff"]), reverse=True)

    # 2. Dependency Edge changes
    base_edges = _extract_edges(base.get("graph_imports", {}))
    target_edges = _extract_edges(target.get("graph_imports", {}))

    added_edges = sorted(list(target_edges - base_edges))
    removed_edges = sorted(list(base_edges - target_edges))

    # 3. Circular Dependency shifts
    base_cycles = _extract_cycles(base.get("graph_imports", {}))
    target_cycles = _extract_cycles(target.get("graph_imports", {}))

    new_cycles_raw = target_cycles - base_cycles
    resolved_cycles_raw = base_cycles - target_cycles

    new_cycles = [
        " → ".join(c) + " → " + c[0]
        for c in sorted(list(new_cycles_raw))
    ]
    resolved_cycles = [
        " → ".join(c) + " → " + c[0]
        for c in sorted(list(resolved_cycles_raw))
    ]

    # 4. Metric shifts
    base_summary = base.get("summary", {})
    target_summary = target.get("summary", {})

    b_files = base_summary.get("total_files", len(base_mods))
    t_files = target_summary.get("total_files", len(target_mods))

    b_lines = base_summary.get("total_lines", sum(m.get("lines", 0) for m in base_mods.values()))
    t_lines = target_summary.get("total_lines", sum(m.get("lines", 0) for m in target_mods.values()))

    b_code = base_summary.get("total_code_lines", 0)
    t_code = target_summary.get("total_code_lines", 0)

    b_comp = base_summary.get("total_complexity", sum(m.get("complexity", 0) for m in base_mods.values()))
    t_comp = target_summary.get("total_complexity", sum(m.get("complexity", 0) for m in target_mods.values()))

    b_avg_comp = base_summary.get("avg_complexity", round(b_comp / max(b_files, 1), 2))
    t_avg_comp = target_summary.get("avg_complexity", round(t_comp / max(t_files, 1), 2))

    metrics_diff = {
        "files_diff": t_files - b_files,
        "lines_diff": t_lines - b_lines,
        "code_lines_diff": t_code - b_code,
        "complexity_diff": t_comp - b_comp,
        "avg_complexity_diff": round(t_avg_comp - b_avg_comp, 2),
        "base_avg_complexity": b_avg_comp,
        "target_avg_complexity": t_avg_comp,
    }

    # Coupling changes per module
    base_metrics = {m.get("module", ""): m for m in base.get("module_metrics", []) if m.get("module")}
    target_metrics = {m.get("module", ""): m for m in target.get("module_metrics", []) if m.get("module")}

    coupling_shifts: List[Dict[str, Any]] = []
    for name in common_names:
        bm_metric = base_metrics.get(name, {})
        tm_metric = target_metrics.get(name, {})
        b_c = bm_metric.get("coupling", 0)
        t_c = tm_metric.get("coupling", 0)
        diff = t_c - b_c
        if abs(diff) >= 1:
            coupling_shifts.append({
                "module_name": name,
                "old_coupling": b_c,
                "new_coupling": t_c,
                "diff": diff,
            })
    coupling_shifts.sort(key=lambda s: abs(s["diff"]), reverse=True)

    # 5. Issue changes
    base_issues = {_issue_key(i): i for i in base.get("issues", [])}
    target_issues = {_issue_key(i): i for i in target.get("issues", [])}

    base_issue_keys = set(base_issues.keys())
    target_issue_keys = set(target_issues.keys())

    new_issue_keys = target_issue_keys - base_issue_keys
    resolved_issue_keys = base_issue_keys - target_issue_keys
    retained_issue_keys = target_issue_keys & base_issue_keys

    new_issues = [target_issues[k] for k in sorted(list(new_issue_keys))]
    resolved_issues = [base_issues[k] for k in sorted(list(resolved_issue_keys))]
    retained_issues = [target_issues[k] for k in sorted(list(retained_issue_keys))]

    # 6. Overall verdict and summary explanation
    structural_change_count = (
        len(added_modules)
        + len(removed_modules)
        + len(renamed_modules)
        + len(added_edges)
        + len(removed_edges)
        + len(new_cycles)
        + len(resolved_cycles)
    )

    is_identical = (
        structural_change_count == 0
        and len(modified_modules) == 0
        and metrics_diff["complexity_diff"] == 0
        and metrics_diff["lines_diff"] == 0
        and len(new_issues) == 0
        and len(resolved_issues) == 0
    )

    verdict_tags: List[str] = []
    if is_identical:
        verdict = "IDENTICAL"
        explanation = "Both scans represent an identical codebase state. No structural, metric, or issue drift detected."
    else:
        if new_cycles:
            verdict_tags.append("DEGRADATION")
            explanation_core = f"Detected {len(new_cycles)} newly introduced circular dependency cycle(s)."
        elif resolved_cycles:
            verdict_tags.append("REFACTORED")
            explanation_core = f"Resolved {len(resolved_cycles)} circular dependency cycle(s)."
        elif added_modules or added_edges:
            verdict_tags.append("EXPANSION")
            explanation_core = f"Project expanded with {len(added_modules)} new module(s) and {len(added_edges)} new dependency edge(s)."
        elif removed_modules or removed_edges:
            verdict_tags.append("REDUCTION")
            explanation_core = f"Removed {len(removed_modules)} module(s) and {len(removed_edges)} dependency edge(s)."
        else:
            verdict_tags.append("MODIFIED")
            explanation_core = f"{len(modified_modules)} file(s) modified."

        verdict = verdict_tags[0] if verdict_tags else "MODIFIED"

        details = []
        if metrics_diff["complexity_diff"] > 0:
            details.append(f"complexity increased by +{metrics_diff['complexity_diff']}")
        elif metrics_diff["complexity_diff"] < 0:
            details.append(f"complexity decreased by {metrics_diff['complexity_diff']}")

        if len(new_issues) > 0:
            details.append(f"{len(new_issues)} new architectural issue(s) introduced")
        if len(resolved_issues) > 0:
            details.append(f"{len(resolved_issues)} issue(s) resolved")

        if details:
            explanation = f"{explanation_core} ({', '.join(details)})."
        else:
            explanation = explanation_core

    return {
        "base_label": base_label,
        "target_label": target_label,
        "is_identical": is_identical,
        "verdict": verdict,
        "explanation": explanation,
        "structural_changes": {
            "added_modules": added_modules,
            "removed_modules": removed_modules,
            "modified_modules": modified_modules,
            "renamed_modules": renamed_modules,
            "added_dependencies": [{"source": s, "target": t} for s, t in added_edges],
            "removed_dependencies": [{"source": s, "target": t} for s, t in removed_edges],
            "new_cycles": new_cycles,
            "resolved_cycles": resolved_cycles,
            "total_structural_events": structural_change_count,
        },
        "metric_changes": {
            "metrics_diff": metrics_diff,
            "coupling_shifts": coupling_shifts,
        },
        "issue_changes": {
            "new_issues": new_issues,
            "resolved_issues": resolved_issues,
            "retained_issues": retained_issues,
            "new_count": len(new_issues),
            "resolved_count": len(resolved_issues),
        },
    }
