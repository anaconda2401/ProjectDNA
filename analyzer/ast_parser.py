"""
ast_parser.py
Parses each Python file with the built-in `ast` module and populates ModuleInfo objects.
"""

import ast
import os
from typing import List, Optional

from .project_model import ModuleInfo, ClassInfo, FunctionInfo, ImportInfo


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _dotted_name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted_name(node.value)}.{node.attr}"
    return "<expr>"


def _get_docstring(node) -> Optional[str]:
    if (
        node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, (ast.Constant, ast.Str))
    ):
        val = node.body[0].value
        return val.s if isinstance(val, ast.Str) else val.value
    return None


def _args_names(args: ast.arguments) -> List[str]:
    return [a.arg for a in args.args + args.posonlyargs + args.kwonlyargs]


def _count_lines(src: str):
    """Return (total, blank, comment, code) line counts."""
    total = blank = comment = code = 0
    for line in src.splitlines():
        total += 1
        stripped = line.strip()
        if not stripped:
            blank += 1
        elif stripped.startswith('#'):
            comment += 1
        else:
            code += 1
    return total, blank, comment, code


# ---------------------------------------------------------------------------
# visitor
# ---------------------------------------------------------------------------

class _ModuleVisitor(ast.NodeVisitor):
    def __init__(self):
        self.imports: List[ImportInfo] = []
        self.classes: List[ClassInfo] = []
        self.functions: List[FunctionInfo] = []
        self._current_class: Optional[ClassInfo] = None

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.imports.append(ImportInfo(
                module=alias.name,
                alias=alias.asname,
                is_from=False,
                line=node.lineno,
            ))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        module = node.module or ""
        names = [a.name for a in node.names]
        self.imports.append(ImportInfo(
            module=module,
            names=names,
            is_from=True,
            line=node.lineno,
        ))
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        bases = [_dotted_name(b) for b in node.bases]
        cls = ClassInfo(
            name=node.name,
            lineno=node.lineno,
            end_lineno=getattr(node, "end_lineno", node.lineno),
            bases=bases,
            decorators=[_dotted_name(d) for d in node.decorator_list],
            docstring=_get_docstring(node),
        )
        prev = self._current_class
        self._current_class = cls
        self.generic_visit(node)
        self._current_class = prev
        self.classes.append(cls)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._handle_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._handle_func(node)

    def _handle_func(self, node):
        calls = list(set(_collect_calls(node)))
        fn = FunctionInfo(
            name=node.name,
            lineno=node.lineno,
            end_lineno=getattr(node, "end_lineno", node.lineno),
            args=_args_names(node.args),
            decorators=[_dotted_name(d) for d in node.decorator_list],
            calls=calls,
            complexity=_cyclomatic(node),
            docstring=_get_docstring(node),
            is_method=self._current_class is not None,
            parent_class=self._current_class.name if self._current_class else None,
        )
        if self._current_class:
            self._current_class.methods.append(fn)
        else:
            self.functions.append(fn)


# ---------------------------------------------------------------------------
# call collection
# ---------------------------------------------------------------------------

def _collect_calls(func_node) -> List[str]:
    calls = []
    for n in ast.walk(func_node):
        if isinstance(n, ast.Call):
            name = _dotted_name(n.func)
            if name != "<expr>":
                calls.append(name)
    return calls


# ---------------------------------------------------------------------------
# McCabe complexity
# ---------------------------------------------------------------------------

_DECISION_NODES = (
    ast.If, ast.For, ast.While, ast.ExceptHandler,
    ast.With, ast.Assert, ast.comprehension,
)

def _cyclomatic(func_node) -> int:
    complexity = 1
    for n in ast.walk(func_node):
        if isinstance(n, _DECISION_NODES):
            complexity += 1
        elif isinstance(n, ast.BoolOp):
            complexity += len(n.values) - 1
    return complexity


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def parse_file(abs_path: str, root: str) -> ModuleInfo:
    rel = os.path.relpath(abs_path, root).replace("\\", "/")
    module_name = rel.replace("/", ".").removesuffix(".py")
    if module_name.endswith(".__init__"):
        module_name = module_name[: -len(".__init__")]

    info = ModuleInfo(path=rel, abs_path=abs_path, module_name=module_name)

    try:
        src = open(abs_path, encoding="utf-8", errors="replace").read()
        info.lines, info.blank_lines, info.comment_lines, info.code_lines = _count_lines(src)
        tree = ast.parse(src, filename=abs_path)
    except SyntaxError as e:
        info.parse_error = f"SyntaxError line {e.lineno}: {e.msg}"
        return info
    except Exception as e:
        info.parse_error = str(e)
        return info

    visitor = _ModuleVisitor()
    visitor.visit(tree)

    info.imports = visitor.imports
    info.classes = visitor.classes
    info.functions = visitor.functions
    info.complexity = sum(
        fn.complexity for cls in info.classes for fn in cls.methods
    ) + sum(fn.complexity for fn in info.functions)

    return info
