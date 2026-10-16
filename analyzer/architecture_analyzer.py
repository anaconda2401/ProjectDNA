"""
architecture_analyzer.py
Infers high-level architectural layers from module names and import patterns.
"""

import re
from typing import Dict, List, Tuple, Set
import networkx as nx

from .project_model import ProjectModel, ModuleInfo

# Common layer keywords – ordered from lowest to highest abstraction
LAYER_PATTERNS: List[Tuple[str, str]] = [
    (r"(db|database|models?|schema|orm|entity|entities|repository|repo|dao|storage|migrations?)", "data"),
    (r"(util|utils|helper|helpers|common|shared|base|core|lib|libs)", "utility"),
    (r"(service|services|business|logic|domain|use_?case)", "service"),
    (r"(api|routes?|views?|controllers?|handler|handlers|endpoints?|blueprint)", "presentation"),
    (r"(test|tests|spec|specs|fixtures?|mock|mocks)", "test"),
    (r"(config|settings?|conf|env)", "config"),
    (r"(cli|cmd|commands?|scripts?|tasks?)", "cli"),
]


def _infer_layer(module_name: str) -> str:
    """Return a layer label for *module_name* based on naming conventions."""
    name = module_name.lower().replace(".", "_")
    for pattern, label in LAYER_PATTERNS:
        if re.search(pattern, name):
            return label
    return "other"


def build_layer_graph(
    model: ProjectModel,
    import_graph: nx.DiGraph,
) -> Tuple[nx.DiGraph, Dict[str, str]]:
    """
    Assign each module a layer and return:
      - a DiGraph of layer → layer edges (aggregated)
      - a dict module_name → layer
    """
    layer_map: Dict[str, str] = {
        m.module_name: _infer_layer(m.module_name) for m in model.modules
    }

    G = nx.DiGraph()
    layers = set(layer_map.values())
    for layer in layers:
        G.add_node(layer)

    for src, dst in import_graph.edges():
        src_layer = layer_map.get(src, "other")
        dst_layer = layer_map.get(dst, "other")
        if src_layer != dst_layer:
            if G.has_edge(src_layer, dst_layer):
                G[src_layer][dst_layer]["weight"] += 1
            else:
                G.add_edge(src_layer, dst_layer, weight=1)

    return G, layer_map
