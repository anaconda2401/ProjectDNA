"""
dependency_analyzer.py
Builds NetworkX graphs representing:
  - module import relationships
  - class inheritance
  - function call relationships (cross-module where identifiable)
"""

import os
import networkx as nx
from typing import Dict, List, Tuple, Set

from .project_model import ProjectModel, ModuleInfo


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _top_level_package(module_name: str) -> str:
    return module_name.split(".")[0]


def _is_project_module(name: str, known: Set[str]) -> bool:
    """Return True if *name* (or its top-level package) belongs to the project."""
    if name in known:
        return True
    top = _top_level_package(name)
    return any(k == top or k.startswith(top + ".") for k in known)


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def build_graphs(model: ProjectModel) -> Dict[str, nx.DiGraph]:
    """
    Return a dict of named NetworkX directed graphs:
      - 'imports'   : module → module (import edges)
      - 'calls'     : function → function (call edges, best-effort)
      - 'inherits'  : child class → parent class
    """
    known_modules: Set[str] = {m.module_name for m in model.modules}

    # ---- import graph -------------------------------------------------------
    G_imports = nx.DiGraph()
    for m in model.modules:
        G_imports.add_node(m.module_name, path=m.path, lines=m.lines,
                           complexity=m.complexity)

    for m in model.modules:
        for imp in m.imports:
            target = imp.module
            if _is_project_module(target, known_modules):
                # resolve partial "from .foo" style with prefix
                if not _is_project_module(target, known_modules):
                    pkg = ".".join(m.module_name.split(".")[:-1])
                    target = f"{pkg}.{target}" if pkg else target
                if target in known_modules:
                    G_imports.add_edge(m.module_name, target)
                else:
                    # add external stub node
                    top = _top_level_package(target)
                    if top in known_modules or any(
                        k.startswith(top + ".") for k in known_modules
                    ):
                        G_imports.add_edge(m.module_name, target)

    # ---- inheritance graph --------------------------------------------------
    G_inherits = nx.DiGraph()
    for m in model.modules:
        for cls in m.classes:
            G_inherits.add_node(cls.name, module=m.module_name)
            for base in cls.bases:
                if base and base != "object":
                    G_inherits.add_node(base)
                    G_inherits.add_edge(cls.name, base)

    # ---- call graph (intra-project, best effort) ----------------------------
    # Build a simple name → module_name index for functions & methods
    func_index: Dict[str, str] = {}
    for m in model.modules:
        for fn in m.functions:
            func_index[fn.name] = m.module_name
            func_index[f"{m.module_name}.{fn.name}"] = m.module_name
        for cls in m.classes:
            for meth in cls.methods:
                func_index[f"{cls.name}.{meth.name}"] = m.module_name

    G_calls = nx.DiGraph()
    for m in model.modules:
        all_fns = list(m.functions) + [
            meth for cls in m.classes for meth in cls.methods
        ]
        for fn in all_fns:
            caller = f"{m.module_name}.{fn.name}"
            G_calls.add_node(caller, module=m.module_name)
            for callee_name in fn.calls:
                callee_key = callee_name
                if callee_key in func_index:
                    callee_node = f"{func_index[callee_key]}.{callee_name.split('.')[-1]}"
                    G_calls.add_node(callee_node)
                    if caller != callee_node:
                        G_calls.add_edge(caller, callee_node)

    return {
        "imports": G_imports,
        "calls": G_calls,
        "inherits": G_inherits,
    }


def get_import_edges(model: ProjectModel) -> List[Tuple[str, str]]:
    """Return list of (from_module, to_module) import pairs for internal modules."""
    graphs = build_graphs(model)
    return list(graphs["imports"].edges())
