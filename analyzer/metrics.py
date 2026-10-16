"""
metrics.py
Computes per-module and project-wide code metrics.
"""

from typing import Dict, List
import networkx as nx

from .project_model import ProjectModel

HIGH_COMPLEXITY_THRESHOLD = 10
HIGH_COUPLING_THRESHOLD = 10


def compute_module_metrics(model: ProjectModel, import_graph: nx.DiGraph) -> List[Dict]:
    rows = []
    for m in model.modules:
        if m.parse_error:
            continue
        in_deg = import_graph.in_degree(m.module_name) if import_graph.has_node(m.module_name) else 0
        out_deg = import_graph.out_degree(m.module_name) if import_graph.has_node(m.module_name) else 0

        all_fns = list(m.functions) + [meth for cls in m.classes for meth in cls.methods]
        max_fn_complexity = max((fn.complexity for fn in all_fns), default=0)
        avg_fn_complexity = (
            round(sum(fn.complexity for fn in all_fns) / len(all_fns), 2)
            if all_fns else 0
        )
        total_methods = sum(len(c.methods) for c in m.classes)

        rows.append({
            'module': m.module_name,
            'path': m.path,
            'lines': m.lines,
            'code_lines': m.code_lines,
            'blank_lines': m.blank_lines,
            'comment_lines': m.comment_lines,
            'classes': len(m.classes),
            'functions': len(m.functions),
            'methods': total_methods,
            'imports': len(m.imports),
            'complexity': m.complexity,
            'max_fn_complexity': max_fn_complexity,
            'avg_fn_complexity': avg_fn_complexity,
            'in_degree': in_deg,
            'out_degree': out_deg,
            'coupling': in_deg + out_deg,
        })

    rows.sort(key=lambda r: r['complexity'], reverse=True)
    return rows


def compute_project_summary(model: ProjectModel, import_graph: nx.DiGraph) -> Dict:
    avg_complexity = round(model.total_complexity / max(model.parsed_files, 1), 2)
    densely_coupled = [
        n for n in import_graph.nodes()
        if import_graph.degree(n) >= HIGH_COUPLING_THRESHOLD
    ]

    # Count isolated nodes
    isolated = [
        n for n in import_graph.nodes()
        if import_graph.degree(n) == 0
    ]

    return {
        'total_files': model.total_files,
        'parsed_files': model.parsed_files,
        'error_files': model.error_files,
        'total_lines': model.total_lines,
        'total_classes': model.total_classes,
        'total_functions': model.total_functions,
        'total_complexity': model.total_complexity,
        'avg_complexity': avg_complexity,
        'densely_coupled_count': len(densely_coupled),
        'isolated_module_count': len(isolated),
    }
