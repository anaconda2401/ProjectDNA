"""
project_model.py
Defines the data structures used to represent a Python project's architecture.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict


@dataclass
class ImportInfo:
    module: str
    names: List[str] = field(default_factory=list)
    alias: Optional[str] = None
    is_from: bool = False
    line: int = 0


@dataclass
class FunctionInfo:
    name: str
    lineno: int
    end_lineno: int
    args: List[str] = field(default_factory=list)
    decorators: List[str] = field(default_factory=list)
    calls: List[str] = field(default_factory=list)
    complexity: int = 1
    docstring: Optional[str] = None
    is_method: bool = False
    parent_class: Optional[str] = None
    # populated by pipeline after all files are parsed
    callers: List[str] = field(default_factory=list)


@dataclass
class ClassInfo:
    name: str
    lineno: int
    end_lineno: int
    bases: List[str] = field(default_factory=list)
    methods: List[FunctionInfo] = field(default_factory=list)
    decorators: List[str] = field(default_factory=list)
    docstring: Optional[str] = None
    # populated by pipeline after all files are parsed
    subclasses: List[str] = field(default_factory=list)


@dataclass
class ModuleInfo:
    path: str           # relative path inside the project
    abs_path: str       # absolute filesystem path
    module_name: str    # dotted module name (a.b.c)
    imports: List[ImportInfo] = field(default_factory=list)
    classes: List[ClassInfo] = field(default_factory=list)
    functions: List[FunctionInfo] = field(default_factory=list)
    parse_error: Optional[str] = None
    lines: int = 0
    blank_lines: int = 0
    comment_lines: int = 0
    code_lines: int = 0
    complexity: int = 0
    file_hash: str = ""


@dataclass
class ProjectModel:
    name: str
    root: str
    modules: List[ModuleInfo] = field(default_factory=list)
    module_by_path: Dict[str, ModuleInfo] = field(default_factory=dict)
    module_by_name: Dict[str, ModuleInfo] = field(default_factory=dict)

    def build_lookup(self):
        self.module_by_path = {m.path: m for m in self.modules}
        self.module_by_name = {m.module_name: m for m in self.modules}

    @property
    def total_files(self):
        return len(self.modules)

    @property
    def parsed_files(self):
        return sum(1 for m in self.modules if m.parse_error is None)

    @property
    def error_files(self):
        return sum(1 for m in self.modules if m.parse_error is not None)

    @property
    def total_classes(self):
        return sum(len(m.classes) for m in self.modules)

    @property
    def total_functions(self):
        return sum(len(m.functions) for m in self.modules)

    @property
    def total_methods(self):
        return sum(
            sum(len(c.methods) for c in m.classes)
            for m in self.modules
        )

    @property
    def total_lines(self):
        return sum(m.lines for m in self.modules)

    @property
    def total_code_lines(self):
        return sum(m.code_lines for m in self.modules)

    @property
    def total_blank_lines(self):
        return sum(m.blank_lines for m in self.modules)

    @property
    def total_comment_lines(self):
        return sum(m.comment_lines for m in self.modules)

    @property
    def total_complexity(self):
        return sum(m.complexity for m in self.modules)

    @property
    def total_imports(self):
        return sum(len(m.imports) for m in self.modules)
