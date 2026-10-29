"""Static symbol graph built from Python AST with NetworkX.

Nodes are modules and symbols (classes, functions, methods). Edge types:
``contains``, ``imports``, ``calls``, ``inherits``.

Resolution is deliberately conservative: when a target cannot be determined
statically, or a name has several possible bindings, the relationship is left
unresolved and counted rather than guessed.
"""

from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import networkx as nx

from .indexer import IndexResult, SourceFile

CONTAINS = "contains"
IMPORTS = "imports"
CALLS = "calls"
INHERITS = "inherits"

TEST_DIR_NAMES = {"tests", "test"}


@dataclass
class Symbol:
    qname: str
    name: str
    kind: str  # "class" | "function" | "method"
    module: str
    file: str
    lineno: int
    end_lineno: int
    node: ast.AST
    parent_class: str | None = None
    is_test: bool = False


@dataclass
class CodeGraph:
    graph: nx.MultiDiGraph
    modules: dict[str, SourceFile]
    symbols: dict[str, Symbol]
    files: dict[str, SourceFile]  # rel_path -> file
    stats: dict = field(default_factory=dict)

    def symbols_in_file(self, rel_path: str) -> list[Symbol]:
        return [s for s in self.symbols.values() if s.file == rel_path]

    def innermost_symbol(self, rel_path: str, line: int) -> Symbol | None:
        best: Symbol | None = None
        for sym in self.symbols_in_file(rel_path):
            if sym.lineno <= line <= sym.end_lineno:
                if best is None or (sym.end_lineno - sym.lineno) < (best.end_lineno - best.lineno):
                    best = sym
        return best

    def tests(self) -> list[Symbol]:
        return sorted((s for s in self.symbols.values() if s.is_test), key=lambda s: (s.file, s.lineno))


def is_test_file(rel_path: str) -> bool:
    parts = PurePosixPath(rel_path).parts
    name = parts[-1]
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    return any(p in TEST_DIR_NAMES for p in parts[:-1]) and name != "__init__.py"


def _dotted(expr: ast.AST) -> list[str] | None:
    """Flatten Name/Attribute chains: a.b.c -> ['a', 'b', 'c']."""
    parts: list[str] = []
    while isinstance(expr, ast.Attribute):
        parts.append(expr.attr)
        expr = expr.value
    if isinstance(expr, ast.Name):
        parts.append(expr.id)
        return list(reversed(parts))
    return None


def _package_of(sf: SourceFile) -> str:
    if sf.is_package:
        return sf.module
    return sf.module.rsplit(".", 1)[0] if "." in sf.module else ""


def _resolve_relative(sf: SourceFile, level: int, module: str | None) -> str | None:
    if level == 0:
        return module
    base = _package_of(sf).split(".") if _package_of(sf) else []
    if level - 1 > len(base):
        return None
    base = base[: len(base) - (level - 1)]
    if module:
        base = base + module.split(".")
    return ".".join(base) if base else None


class _Builder:
    def __init__(self, index: IndexResult) -> None:
        self.index = index
        self.g = nx.MultiDiGraph()
        self.modules: dict[str, SourceFile] = {}
        self.symbols: dict[str, Symbol] = {}
        self.bindings: dict[str, dict[str, set[str]]] = {}
        self.unresolved_calls = 0
        self.external_calls = 0
        self.ambiguous_bindings = 0
        self.external_bases: dict[str, list[str]] = {}

    # -- pass 1: definitions ------------------------------------------------
    def collect(self) -> None:
        for sf in self.index.files:
            if sf.module in self.modules:  # e.g. duplicate src/ and top-level layouts
                continue
            self.modules[sf.module] = sf
            self.g.add_node(sf.module, kind="module", file=sf.rel_path, lineno=1,
                            end_lineno=max(1, len(sf.lines)), is_test=is_test_file(sf.rel_path))
            for node in sf.tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self._add_symbol(sf, node, "function", None)
                elif isinstance(node, ast.ClassDef):
                    cls = self._add_symbol(sf, node, "class", None)
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            self._add_symbol(sf, item, "method", cls.qname)

    def _add_symbol(self, sf: SourceFile, node, kind: str, parent: str | None) -> Symbol:
        qname = f"{parent or sf.module}.{node.name}"
        sym = Symbol(
            qname=qname, name=node.name, kind=kind, module=sf.module, file=sf.rel_path,
            lineno=node.lineno, end_lineno=getattr(node, "end_lineno", node.lineno) or node.lineno,
            node=node, parent_class=parent,
        )
        self.symbols[qname] = sym
        self.g.add_node(qname, kind=kind, file=sf.rel_path, lineno=sym.lineno,
                        end_lineno=sym.end_lineno, is_test=False)
        self.g.add_edge(parent or sf.module, qname, key=CONTAINS, type=CONTAINS)
        return sym

    # -- pass 2: name bindings and import edges ------------------------------
    def bind(self) -> None:
        for mod, sf in self.modules.items():
            table: dict[str, set[str]] = {}
            for node in sf.tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    table.setdefault(node.name, set()).add(f"{mod}.{node.name}")
            for node in ast.walk(sf.tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.asname:
                            table.setdefault(alias.asname, set()).add(alias.name)
                        else:
                            head = alias.name.split(".")[0]
                            table.setdefault(head, set()).add(head)
                        self._import_edge(mod, alias.name, node.lineno)
                elif isinstance(node, ast.ImportFrom):
                    base = _resolve_relative(sf, node.level, node.module)
                    if base is None:
                        continue
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        target = f"{base}.{alias.name}"
                        table.setdefault(alias.asname or alias.name, set()).add(target)
                        if not self._import_edge(mod, target, node.lineno):
                            self._import_edge(mod, base, node.lineno)
            self.ambiguous_bindings += sum(1 for v in table.values() if len(v) > 1)
            self.bindings[mod] = table

    def _import_edge(self, mod: str, target: str, line: int) -> bool:
        if target in self.modules or target in self.symbols:
            if target != mod:
                self.g.add_edge(mod, target, key=IMPORTS, type=IMPORTS, line=line)
            return True
        return False

    def _binding(self, mod: str, name: str) -> str | None:
        options = self.bindings.get(mod, {}).get(name)
        if not options or len(options) != 1:
            return None
        return next(iter(options))

    def resolve_qualified(self, qname: str, depth: int = 0) -> str | None:
        """Map a dotted name to a local node, following re-exports a few levels."""
        if qname in self.symbols or qname in self.modules:
            return qname
        if depth > 3 or "." not in qname:
            return None
        # Walk prefixes: longest module prefix, then attribute path.
        parts = qname.split(".")
        for i in range(len(parts) - 1, 0, -1):
            prefix = ".".join(parts[:i])
            rest = parts[i:]
            if prefix in self.symbols and self.symbols[prefix].kind == "class" and len(rest) == 1:
                return self.lookup_method(prefix, rest[0])
            if prefix in self.modules:
                target = self._binding(prefix, rest[0])
                if target and target != qname:
                    return self.resolve_qualified(".".join([target, *rest[1:]]), depth + 1)
                return None
        return None

    def resolve_dotted(self, mod: str, parts: list[str]) -> tuple[str | None, bool]:
        """Return (local target, is_external)."""
        target = self._binding(mod, parts[0])
        if target is None:
            return None, False
        full = ".".join([target, *parts[1:]])
        resolved = self.resolve_qualified(full)
        if resolved:
            return resolved, False
        root = target.split(".")[0]
        is_local_root = any(m == root or m.startswith(root + ".") for m in self.modules)
        return None, not is_local_root

    # -- inheritance ----------------------------------------------------------
    def inherit(self) -> None:
        for sym in list(self.symbols.values()):
            if sym.kind != "class":
                continue
            for base in sym.node.bases:
                parts = _dotted(base)
                if not parts:
                    continue
                target, _ = self.resolve_dotted(sym.module, parts)
                if target and target in self.symbols and self.symbols[target].kind == "class":
                    self.g.add_edge(sym.qname, target, key=INHERITS, type=INHERITS)
                else:
                    self.external_bases.setdefault(sym.qname, []).append(parts[-1])

    def mro(self, cls: str) -> list[str]:
        order: list[str] = []
        stack = [cls]
        while stack:
            current = stack.pop(0)
            if current in order:
                continue
            order.append(current)
            bases = [v for _, v, k in self.g.out_edges(current, keys=True) if k == INHERITS]
            stack.extend(bases)
        return order

    def lookup_method(self, cls: str, name: str, skip_self: bool = False) -> str | None:
        chain = self.mro(cls)
        if skip_self:
            chain = chain[1:]
        for c in chain:
            candidate = f"{c}.{name}"
            if candidate in self.symbols:
                return candidate
        return None

    # -- calls ------------------------------------------------------------------
    def calls(self) -> None:
        for sym in list(self.symbols.values()):
            if sym.kind == "class":
                continue
            self._calls_for(sym)

    def _local_names(self, fn) -> set[str]:
        names = {a.arg for a in fn.args.args + fn.args.kwonlyargs + fn.args.posonlyargs}
        if fn.args.vararg:
            names.add(fn.args.vararg.arg)
        if fn.args.kwarg:
            names.add(fn.args.kwarg.arg)
        for node in ast.walk(fn):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
        return names

    def _var_types(self, sym: Symbol) -> dict[str, str]:
        """Local variables assigned exactly one statically known local class."""
        seen: dict[str, set[str | None]] = {}
        for node in ast.walk(sym.node):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if not isinstance(tgt, ast.Name):
                        continue
                    cls = None
                    if isinstance(node.value, ast.Call):
                        parts = _dotted(node.value.func)
                        if parts:
                            t, _ = self.resolve_dotted(sym.module, parts)
                            if t and t in self.symbols and self.symbols[t].kind == "class":
                                cls = t
                    seen.setdefault(tgt.id, set()).add(cls)
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.For, ast.With)):
                for tgt in ast.walk(node):
                    if isinstance(tgt, ast.Name) and isinstance(tgt.ctx, ast.Store):
                        seen.setdefault(tgt.id, set()).add(None)
        return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1 and None not in v}

    def _calls_for(self, sym: Symbol) -> None:
        fn = sym.node
        locals_ = self._local_names(fn)
        var_types = self._var_types(sym)
        self_name = None
        if sym.kind == "method" and fn.args.args:
            self_name = fn.args.args[0].arg
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            target = self._resolve_call(sym, node.func, locals_, var_types, self_name)
            if target == "external":
                self.external_calls += 1
            elif target is None:
                self.unresolved_calls += 1
                self.g.nodes[sym.qname]["unresolved_calls"] = self.g.nodes[sym.qname].get("unresolved_calls", 0) + 1
            else:
                self.g.add_edge(sym.qname, target, key=CALLS, type=CALLS, line=node.lineno)
                if target in self.symbols and self.symbols[target].kind == "class":
                    init = self.lookup_method(target, "__init__")
                    if init:
                        self.g.add_edge(sym.qname, init, key=CALLS, type=CALLS, line=node.lineno)

    def _resolve_call(self, sym, func, locals_, var_types, self_name):
        # super().method(...)
        if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call)
                and isinstance(func.value.func, ast.Name) and func.value.func.id == "super"
                and sym.parent_class):
            return self.lookup_method(sym.parent_class, func.attr, skip_self=True)
        parts = _dotted(func)
        if not parts:
            return None
        head = parts[0]
        if self_name and head == self_name and sym.parent_class:
            if len(parts) == 2:
                return self.lookup_method(sym.parent_class, parts[1])
            return None
        if head in var_types and len(parts) == 2:
            return self.lookup_method(var_types[head], parts[1])
        if head in locals_:
            return None  # local variable of unknown type
        target, external = self.resolve_dotted(sym.module, parts)
        if target:
            return target
        if external:
            return "external"
        if len(parts) == 1 and head not in self.bindings.get(sym.module, {}):
            return "external"  # builtins and other globals
        return None

    # -- tests --------------------------------------------------------------------
    def mark_tests(self) -> None:
        for sym in self.symbols.values():
            if not is_test_file(sym.file):
                continue
            if sym.kind == "function" and sym.name.startswith("test"):
                sym.is_test = True
            elif sym.kind == "method" and sym.name.startswith("test") and sym.parent_class:
                cls = self.symbols[sym.parent_class]
                bases = self.external_bases.get(cls.qname, [])
                if cls.name.startswith("Test") or "TestCase" in bases:
                    sym.is_test = True
            if sym.is_test:
                self.g.nodes[sym.qname]["is_test"] = True

    def build(self) -> CodeGraph:
        self.collect()
        self.bind()
        self.inherit()
        self.calls()
        self.mark_tests()
        kinds = Counter(s.kind for s in self.symbols.values())
        edges = Counter(k for _, _, k in self.g.edges(keys=True))
        test_files = {s.file for s in self.symbols.values() if s.is_test}
        stats = {
            "modules": len(self.modules),
            "classes": kinds.get("class", 0),
            "functions": kinds.get("function", 0),
            "methods": kinds.get("method", 0),
            "edges": {t: edges.get(t, 0) for t in (CONTAINS, IMPORTS, CALLS, INHERITS)},
            "unresolved_calls": self.unresolved_calls,
            "external_or_builtin_calls": self.external_calls,
            "ambiguous_bindings": self.ambiguous_bindings,
            "test_files": len(test_files),
            "test_functions": sum(1 for s in self.symbols.values() if s.is_test),
        }
        return CodeGraph(
            graph=self.g,
            modules=self.modules,
            symbols=self.symbols,
            files={sf.rel_path: sf for sf in self.modules.values()},
            stats=stats,
        )


def build_graph(index: IndexResult) -> CodeGraph:
    return _Builder(index).build()
