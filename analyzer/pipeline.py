"""
pipeline.py
Orchestrates the full analysis pipeline.
Returns a serialisable result dict.
"""

import os
import sys
import time
from typing import Dict, List, Any, Set

import networkx as nx
from networkx.readwrite import json_graph

from .scanner import scan_project
from .ast_parser import parse_file
from .project_model import ProjectModel
from .dependency_analyzer import build_graphs
from .architecture_analyzer import build_layer_graph, _infer_layer
from .metrics import compute_module_metrics, compute_project_summary
from .issue_detector import detect_issues

# ---------------------------------------------------------------------------
# stdlib detection
# ---------------------------------------------------------------------------
try:
    _STDLIB: Set[str] = sys.stdlib_module_names  # type: ignore  (Python 3.10+)
except AttributeError:
    _STDLIB = {
        'abc', 'ast', 'asyncio', 'base64', 'builtins', 'collections',
        'contextlib', 'copy', 'csv', 'dataclasses', 'datetime', 'decimal',
        'enum', 'functools', 'gc', 'glob', 'hashlib', 'http', 'inspect',
        'io', 'itertools', 'json', 'logging', 'math', 'operator', 'os',
        'pathlib', 'pickle', 'platform', 'pprint', 'queue', 'random',
        're', 'shutil', 'signal', 'socket', 'sqlite3', 'stat', 'statistics',
        'string', 'struct', 'subprocess', 'sys', 'tempfile', 'textwrap',
        'threading', 'time', 'traceback', 'types', 'typing', 'unittest',
        'urllib', 'uuid', 'warnings', 'weakref', 'xml', 'zipfile', 'zlib',
        'argparse', 'configparser', 'fnmatch', 'getpass', 'heapq',
        'importlib', 'multiprocessing', 'pathlib', 'pdb', 'profile',
        'pty', 'pwd', 'readline', 'resource', 'rlcompleter', 'runpy',
        'sched', 'secrets', 'selectors', 'shelve', 'shlex',
        'tarfile', 'token', 'tokenize', 'tomllib', 'trace',
    }


def _detect_external_libs(model: ProjectModel) -> List[Dict]:
    """Return list of {name, usage_count, used_by} for third-party imports."""
    known_tops = {m.module_name.split('.')[0] for m in model.modules}
    # Also treat every file-level relative import as internal
    # (relative imports have empty module or start with '.')
    ext: Dict[str, List[str]] = {}
    for m in model.modules:
        if m.parse_error:
            continue
        seen_in_module = set()
        for imp in m.imports:
            # Skip relative imports (module is '' or starts with '.')
            if not imp.module or not imp.module.strip('.'):
                continue
            top = imp.module.lstrip('.').split('.')[0]
            if not top:
                continue
            # Skip internal modules (any top-level package in the project)
            if top in known_tops:
                continue
            # Skip stdlib
            if top in _STDLIB:
                continue
            if top not in ext:
                ext[top] = []
            if m.module_name not in seen_in_module:
                ext[top].append(m.module_name)
                seen_in_module.add(m.module_name)
    return sorted(
        [{'name': k, 'usage_count': len(v), 'used_by': v} for k, v in ext.items()],
        key=lambda x: x['usage_count'], reverse=True
    )


def _build_callers_map(model: ProjectModel) -> Dict[str, List[str]]:
    """Map callee name -> list of caller qualified names."""
    callers: Dict[str, List[str]] = {}
    for m in model.modules:
        if m.parse_error:
            continue
        all_fns = list(m.functions) + [
            meth for cls in m.classes for meth in cls.methods
        ]
        for fn in all_fns:
            if fn.is_method:
                caller_id = f"{fn.parent_class}.{fn.name}"
            else:
                caller_id = f"{m.module_name}.{fn.name}"
            for callee in fn.calls:
                callers.setdefault(callee, []).append(caller_id)
    return callers


def _build_subclasses_map(model: ProjectModel) -> Dict[str, List[str]]:
    sub: Dict[str, List[str]] = {}
    for m in model.modules:
        if m.parse_error:
            continue
        for cls in m.classes:
            for base in cls.bases:
                if base and base != 'object':
                    sub.setdefault(base, []).append(f"{m.module_name}.{cls.name}")
    return sub


def run_pipeline(project_root: str, project_name: str) -> Dict[str, Any]:
    t0 = time.time()

    # 1. Scan
    py_files, scan_warnings = scan_project(project_root)

    # 2. Parse
    model = ProjectModel(name=project_name, root=project_root)
    for abs_path in py_files:
        mi = parse_file(abs_path, project_root)
        model.modules.append(mi)
    model.build_lookup()

    # 3. Build graphs
    graphs = build_graphs(model)
    G_imports = graphs["imports"]

    # 4. Architecture
    G_layers, layer_map = build_layer_graph(model, G_imports)

    # 5. Post-processing
    callers_map = _build_callers_map(model)
    subclasses_map = _build_subclasses_map(model)
    external_libraries = _detect_external_libs(model)

    # 6. Metrics
    module_metrics = compute_module_metrics(model, G_imports)
    summary = compute_project_summary(model, G_imports)
    # Augment summary
    summary.update({
        'total_blank_lines': model.total_blank_lines,
        'total_comment_lines': model.total_comment_lines,
        'total_code_lines': model.total_code_lines,
        'total_methods': model.total_methods,
        'total_imports': model.total_imports,
        'external_library_count': len(external_libraries),
    })

    # 7. Issues
    issues = detect_issues(model, G_imports, layer_map)

    elapsed = round(time.time() - t0, 2)

    # 8. Serialise graphs to node-link JSON.
    #    NetworkX 3.2+ uses 'edges' as the key; older versions used 'links'.
    #    Normalise to 'links' so the frontend always gets a consistent format.
    def _gdata(G):
        data = json_graph.node_link_data(G)
        if 'edges' in data and 'links' not in data:
            data['links'] = data.pop('edges')
        return data

    # 9. Build flat all_classes list
    all_classes = []
    for m in model.modules:
        if m.parse_error:
            continue
        for cls in m.classes:
            all_classes.append({
                'name': cls.name,
                'module': m.module_name,
                'path': m.path,
                'lineno': cls.lineno,
                'end_lineno': cls.end_lineno,
                'method_count': len(cls.methods),
                'bases': cls.bases,
                'subclasses': subclasses_map.get(cls.name, []),
                'docstring': (cls.docstring or '')[:200],
                'methods': [
                    {
                        'name': meth.name,
                        'lineno': meth.lineno,
                        'complexity': meth.complexity,
                        'args': meth.args,
                    }
                    for meth in cls.methods
                ],
            })
    all_classes.sort(key=lambda c: c['method_count'], reverse=True)

    # 10. Build flat all_functions list (capped at 1000 for storage)
    all_functions = []
    for m in model.modules:
        if m.parse_error:
            continue
        for fn in m.functions:
            fn_key = fn.name
            all_functions.append({
                'name': fn.name,
                'qualified': f"{m.module_name}.{fn.name}",
                'module': m.module_name,
                'path': m.path,
                'lineno': fn.lineno,
                'end_lineno': fn.end_lineno,
                'complexity': fn.complexity,
                'args': fn.args,
                'calls': fn.calls[:15],
                'callers': list(set(callers_map.get(fn_key, [])))[:10],
                'is_method': False,
                'parent_class': None,
                'docstring': (fn.docstring or '')[:200],
            })
        for cls in m.classes:
            for meth in cls.methods:
                meth_key = f"{cls.name}.{meth.name}"
                all_functions.append({
                    'name': meth.name,
                    'qualified': f"{m.module_name}.{cls.name}.{meth.name}",
                    'module': m.module_name,
                    'path': m.path,
                    'lineno': meth.lineno,
                    'end_lineno': meth.end_lineno,
                    'complexity': meth.complexity,
                    'args': meth.args,
                    'calls': meth.calls[:15],
                    'callers': list(set(callers_map.get(meth_key, [])))[:10],
                    'is_method': True,
                    'parent_class': cls.name,
                    'docstring': (meth.docstring or '')[:200],
                })
    all_functions.sort(key=lambda f: f['complexity'], reverse=True)

    # 11. Module list for frontend
    modules_list = []
    for m in model.modules:
        modules_list.append({
            'path': m.path,
            'module_name': m.module_name,
            'lines': m.lines,
            'blank_lines': m.blank_lines,
            'comment_lines': m.comment_lines,
            'code_lines': m.code_lines,
            'classes': [
                {
                    'name': c.name,
                    'lineno': c.lineno,
                    'end_lineno': c.end_lineno,
                    'bases': c.bases,
                    'subclasses': subclasses_map.get(c.name, []),
                    'docstring': (c.docstring or '')[:200],
                    'methods': [
                        {
                            'name': fn.name,
                            'lineno': fn.lineno,
                            'end_lineno': fn.end_lineno,
                            'complexity': fn.complexity,
                            'args': fn.args,
                            'calls': fn.calls[:10],
                            'callers': list(set(callers_map.get(f"{c.name}.{fn.name}", [])))[:8],
                            'docstring': (fn.docstring or '')[:200],
                        }
                        for fn in c.methods
                    ],
                }
                for c in m.classes
            ],
            'functions': [
                {
                    'name': fn.name,
                    'lineno': fn.lineno,
                    'end_lineno': fn.end_lineno,
                    'complexity': fn.complexity,
                    'args': fn.args,
                    'calls': fn.calls[:10],
                    'callers': list(set(callers_map.get(fn.name, [])))[:8],
                    'docstring': (fn.docstring or '')[:200],
                }
                for fn in m.functions
            ],
            'imports': [
                {
                    'module': imp.module,
                    'names': imp.names,
                    'is_from': imp.is_from,
                    'line': imp.line,
                }
                for imp in m.imports
            ],
            'parse_error': m.parse_error,
            'complexity': m.complexity,
            'layer': layer_map.get(m.module_name, 'other'),
        })

    return {
        'project_name': project_name,
        'elapsed': elapsed,
        'scan_warnings': scan_warnings,
        'summary': summary,
        'modules': modules_list,
        'module_metrics': module_metrics,
        'issues': issues,
        'all_classes': all_classes,
        'all_functions': all_functions[:1000],
        'external_libraries': external_libraries,
        'graph_imports': _gdata(G_imports),
        'graph_layers': _gdata(G_layers),
        'layer_map': layer_map,
    }
