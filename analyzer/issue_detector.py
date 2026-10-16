"""
issue_detector.py
Detects common structural/quality issues in Python projects.
"""

from typing import List, Dict, Optional
import networkx as nx

from .project_model import ProjectModel

COMPLEXITY_HIGH = 10
COMPLEXITY_VERY_HIGH = 20
FAN_IN_HIGH = 6
FAN_OUT_HIGH = 8
LARGE_MODULE_LINES = 400
MANY_CLASSES = 8
MANY_FUNCTIONS = 20

ISSUE_LEVELS = {"error", "warning", "info"}

# Layer order for architectural violation detection
# Lower number = more fundamental / lower layer
LAYER_LEVEL = {
    'config': 0,
    'utility': 1,
    'data': 2,
    'service': 3,
    'presentation': 4,
    'cli': 4,
    'test': 5,
    'other': -1,
}


def _issue(level: str, category: str, title: str, detail: str, location: str = '') -> Dict:
    assert level in ISSUE_LEVELS
    return {'level': level, 'category': category, 'title': title, 'detail': detail, 'location': location}


def detect_issues(
    model: ProjectModel,
    import_graph: nx.DiGraph,
    layer_map: Optional[Dict[str, str]] = None,
) -> List[Dict]:
    issues: List[Dict] = []
    layer_map = layer_map or {}

    # ------------------------------------------------------------------ #
    # 1. Circular imports
    # ------------------------------------------------------------------ #
    try:
        cycles = list(nx.simple_cycles(import_graph))
        for cycle in cycles:
            if len(cycle) > 1:
                cycle_str = " → ".join(cycle) + " → " + cycle[0]
                issues.append(_issue(
                    'error', 'circular_dependency',
                    'Circular import detected',
                    f'Cycle: {cycle_str}',
                    cycle[0],
                ))
    except Exception:
        pass

    # ------------------------------------------------------------------ #
    # 2. High cyclomatic complexity
    # ------------------------------------------------------------------ #
    for m in model.modules:
        if m.parse_error:
            continue
        all_fns = list(m.functions) + [
            meth for cls in m.classes for meth in cls.methods
        ]
        for fn in all_fns:
            if fn.complexity >= COMPLEXITY_VERY_HIGH:
                issues.append(_issue(
                    'error', 'complexity',
                    f'Very high complexity in `{fn.name}`',
                    f'Cyclomatic complexity = {fn.complexity}. Consider refactoring.',
                    f'{m.path}:{fn.lineno}',
                ))
            elif fn.complexity >= COMPLEXITY_HIGH:
                issues.append(_issue(
                    'warning', 'complexity',
                    f'High complexity in `{fn.name}`',
                    f'Cyclomatic complexity = {fn.complexity}.',
                    f'{m.path}:{fn.lineno}',
                ))

    # ------------------------------------------------------------------ #
    # 3. High fan-in (central dependency hub)
    # ------------------------------------------------------------------ #
    for node in import_graph.nodes():
        in_deg = import_graph.in_degree(node)
        if in_deg >= FAN_IN_HIGH:
            issues.append(_issue(
                'warning', 'coupling',
                f'High fan-in for `{node}`',
                f'{in_deg} modules import this — it is a central dependency hub.',
                node,
            ))

    # ------------------------------------------------------------------ #
    # 4. High fan-out (too many dependencies)
    # ------------------------------------------------------------------ #
    for node in import_graph.nodes():
        out_deg = import_graph.out_degree(node)
        if out_deg >= FAN_OUT_HIGH:
            issues.append(_issue(
                'warning', 'coupling',
                f'High fan-out for `{node}`',
                f'This module imports {out_deg} other project modules.',
                node,
            ))

    # ------------------------------------------------------------------ #
    # 5. Parse errors
    # ------------------------------------------------------------------ #
    for m in model.modules:
        if m.parse_error:
            issues.append(_issue(
                'error', 'parse_error',
                f'Parse error in `{m.path}`',
                m.parse_error,
                m.path,
            ))

    # ------------------------------------------------------------------ #
    # 6. Large modules
    # ------------------------------------------------------------------ #
    for m in model.modules:
        if m.parse_error:
            continue
        if m.code_lines >= LARGE_MODULE_LINES:
            issues.append(_issue(
                'warning', 'size',
                f'Large module: `{m.path}`',
                f'{m.code_lines} lines of code. Consider splitting into smaller modules.',
                m.path,
            ))

    # ------------------------------------------------------------------ #
    # 7. God module (too many classes AND too many functions)
    # ------------------------------------------------------------------ #
    for m in model.modules:
        if m.parse_error:
            continue
        if len(m.classes) >= MANY_CLASSES and len(m.functions) >= MANY_FUNCTIONS:
            issues.append(_issue(
                'warning', 'design',
                f'Possible god module: `{m.path}`',
                f'{len(m.classes)} classes and {len(m.functions)} functions in one file.',
                m.path,
            ))

    # ------------------------------------------------------------------ #
    # 8. Orphan / isolated modules (no dependencies and no dependents)
    # ------------------------------------------------------------------ #
    entry_point_names = {
        'main', '__main__', 'app', 'run', 'manage', 'wsgi', 'asgi',
        'cli', 'server', 'start', 'launch', '__init__',
    }
    for m in model.modules:
        if m.parse_error:
            continue
        node = m.module_name
        if not import_graph.has_node(node):
            continue
        in_deg = import_graph.in_degree(node)
        out_deg = import_graph.out_degree(node)
        parts = set(node.split('.'))
        if in_deg == 0 and out_deg == 0 and m.lines > 50:
            if not parts & entry_point_names:
                issues.append(_issue(
                    'info', 'architecture',
                    f'Isolated module `{node}`',
                    f'{m.lines} lines but no detected project dependencies or dependents.',
                    m.path,
                ))

    # ------------------------------------------------------------------ #
    # 9. Architectural layer violations
    # ------------------------------------------------------------------ #
    if layer_map:
        for src, dst in import_graph.edges():
            src_layer = layer_map.get(src, 'other')
            dst_layer = layer_map.get(dst, 'other')
            src_lvl = LAYER_LEVEL.get(src_layer, -1)
            dst_lvl = LAYER_LEVEL.get(dst_layer, -1)
            if src_lvl == -1 or dst_lvl == -1:
                continue
            if src_lvl < dst_lvl and src_layer != 'test':
                issues.append(_issue(
                    'warning', 'architecture',
                    f'Possible layer violation: `{src}` → `{dst}`',
                    f'`{src_layer}` layer importing `{dst_layer}` layer '
                    f'(lower layer importing higher layer).',
                    src,
                ))

    # ------------------------------------------------------------------ #
    # 10. Empty __init__.py
    # ------------------------------------------------------------------ #
    for m in model.modules:
        if m.path.endswith('__init__.py') and m.lines <= 2 and not m.parse_error:
            issues.append(_issue(
                'info', 'style',
                f'Empty `__init__.py`',
                'No content. Consider adding package-level imports or a docstring.',
                m.path,
            ))

    # ------------------------------------------------------------------ #
    # 11. Missing docstrings on large functions
    # ------------------------------------------------------------------ #
    for m in model.modules:
        if m.parse_error:
            continue
        all_fns = list(m.functions) + [
            meth for cls in m.classes for meth in cls.methods
        ]
        for fn in all_fns:
            length = fn.end_lineno - fn.lineno
            if length >= 20 and not fn.docstring:
                issues.append(_issue(
                    'info', 'documentation',
                    f'Missing docstring in `{fn.name}`',
                    f'Function is {length} lines but has no docstring.',
                    f'{m.path}:{fn.lineno}',
                ))

    return issues
