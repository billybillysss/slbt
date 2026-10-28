"""Local scalar expansion, generic-test checks, and deferred dbt runtime calls."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from jinja2 import StrictUndefined, TemplateError, nodes
from jinja2.sandbox import SandboxedEnvironment
from sqlglot import exp
from sqlglot.dialects import Dialect

from slbt.schema import SpecError, from_dotted, identifier, load_yaml, mapping, relation_key, text, yaml_files
from slbt.sql import read_query, render_relation, scalar_tree, sql


BLOCK = re.compile(r"\{%[-+]?\s*(macro|test)\s+([a-zA-Z_][a-zA-Z_0-9]*)\s*\(.*?\{%[-+]?\s*end\1\s*[-+]?%\}", re.S)
RUNTIME = {"adapter", "execute", "return", "exceptions", "run_query", "statement", "load_result", "ref", "source", "config", "var", "env_var", "context", "graph", "model", "target", "this", "modules", "flags", "dbt_version", "invocation_id", "run_started_at", "builtins", "api"}


@dataclass
class Definition:
    path: Path
    name: str
    kind: str
    expression: str
    node: nodes.Macro
    calls: set[str]
    runtime: bool

    @property
    def key(self):
        return (self.path, self.kind, self.name)


class Registry:
    def __init__(self, root: Path, overrides=None, *, dialect="spark"):
        self.root = root
        self.dialect = dialect
        self.environment = SandboxedEnvironment(undefined=StrictUndefined, extensions=["jinja2.ext.do", "jinja2.ext.loopcontrols"])
        self.definitions: dict[str, Definition] = {}
        self.descriptions: dict[str, str] = {}
        property_paths = set(yaml_files(root)) | {p for p in (overrides or {}) if p.suffix in {".yml", ".yaml"} and p.is_relative_to(root)}
        self.property_files = {path: load_yaml(path, overrides) for path in sorted(property_paths)}
        for path, properties in self.property_files.items():
            if not isinstance(properties.get("macros", []), list):
                raise SpecError(f"{path}: macros properties must be a list")
            for item in properties.get("macros", []):
                mapping(item, str(path))
                name = text(item.get("name"), str(path))
                if name in self.descriptions:
                    raise SpecError(f"duplicate macro properties: {name}")
                self.descriptions[name] = text(item.get("description", ""), str(path), empty=True)
        files = set(root.rglob("*.sql")) | {p for p in (overrides or {}) if p.suffix == ".sql" and p.is_relative_to(root)}
        for path in sorted(files):
            content = (overrides[path] if overrides and path in overrides else path.read_bytes()).decode("utf-8")
            self.add_file(path, content)
        # Runtime dependence propagates through wrappers before rendering anything.
        changed = True
        while changed:
            changed = False
            for definition in self.definitions.values():
                if not definition.runtime and any(self.definitions[name].runtime for name in definition.calls if name in self.definitions):
                    definition.runtime = changed = True
        active, done = [], set()

        def visit(name):
            if name in active:
                raise SpecError("recursive macro expansion: " + " -> ".join(active + [name]))
            if name in done:
                return
            active.append(name)
            for called in self.definitions[name].calls:
                if called in self.definitions:
                    visit(called)
            active.pop()
            done.add(name)

        for name, definition in self.definitions.items():
            visit(name)
            if definition.kind == "macro" and not definition.runtime and name.upper() in Dialect.get_or_raise(dialect).parser_class.FUNCTIONS:
                raise SpecError(f"scalar macro {name} cannot shadow a {dialect} SQL function")
        source = "\n".join(self.jinja(d.expression) for d in self.definitions.values())
        try:
            self.module = self.environment.from_string(source).make_module()
        except TemplateError as exc:
            raise SpecError(f"invalid macro definitions: {exc}") from exc

    @staticmethod
    def jinja(value):
        value = re.sub(r"(\{%[-+]?\s*)test\s+", r"\1macro test_", value)
        return re.sub(r"(\{%[-+]?\s*)endtest(\s*[-+]?%\})", r"\1endmacro\2", value)

    def add_file(self, path, content):
        # Keep definitions consistent with text-mode reads and Excel/XML newlines.
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        try:
            tree = self.environment.parse(self.jinja(content))
            blocks = list(BLOCK.finditer(content))
            macro_nodes = list(tree.find_all(nodes.Macro))
            if len(blocks) != len(macro_nodes):
                raise SpecError(f"{path}: unsupported or nested macro definitions")
            for block, node in zip(blocks, macro_nodes, strict=True):
                kind, name = block.group(1, 2)
                identifier(name, str(path))
                key = "test_" + name if kind == "test" else name
                if key in self.definitions:
                    raise SpecError(f"duplicate macro/test: {name}")
                names = {n.name for n in node.find_all(nodes.Name)} - {arg.name for arg in node.args}
                calls = {call.node.name for call in node.find_all(nodes.Call) if isinstance(call.node, nodes.Name)}
                self.definitions[key] = Definition(path, name, kind, block.group(), node, calls, bool(names & RUNTIME))
        except TemplateError as exc:
            raise SpecError(f"{path}: invalid macro: {exc}") from exc

    def argument(self, node, *, runtime=False):
        if isinstance(node, exp.Literal):
            if node.is_string:
                return node.this
            number = node.this
            return float(number) if "." in number or "e" in number.lower() else int(number)
        if isinstance(node, exp.Boolean):
            return node.this
        if isinstance(node, exp.Null):
            return None
        if isinstance(node, exp.Array):
            return [self.argument(item, runtime=runtime) for item in node.expressions]
        if isinstance(node, (exp.Map, exp.VarMap)):
            keys, values = node.args.get("keys"), node.args.get("values")
            if isinstance(keys, exp.Array) and isinstance(values, exp.Array):
                return dict(zip(self.argument(keys), self.argument(values), strict=True))
        return sql(node, self.dialect)

    def expand(self, tree, context, compiler, stack=()):
        # Bottom-up expansion also handles scalar calls nested in ordinary SQL functions.
        for child in list(tree.iter_expressions()):
            child.replace(self.expand(child, context, compiler, stack))
        if not isinstance(tree, exp.Anonymous):
            return tree
        name = tree.name.lower()
        definition = self.definitions.get(name)
        if definition is None or definition.kind != "macro":
            return tree
        if name in stack:
            raise SpecError(f"{context}: recursive macro expansion: {name}")
        positional, keyword, runtime_parts = [], {}, []
        seen_keyword = False
        arguments = [arg.name for arg in definition.node.args]
        for index, argument in enumerate(tree.expressions):
            key = None
            if isinstance(argument, (exp.Kwarg, exp.PropertyEQ)):
                key, argument = argument.this.name, argument.expression
                if key not in arguments or key in keyword:
                    raise SpecError(f"{context}: invalid or duplicate macro argument {key}")
                seen_keyword = True
            elif seen_keyword:
                raise SpecError(f"{context}: positional arguments must precede named arguments")
            value = self.argument(argument, runtime=definition.runtime)
            if key:
                keyword[key] = value
            else:
                positional.append(value)
            if definition.runtime and isinstance(argument, exp.Column) and len(argument.parts) > 1 and argument.parts[0].name in {"step", "ref", "source"}:
                dep = from_dotted(".".join(part.name for part in argument.parts), context)
                if "step" in dep:
                    raise SpecError(f"{context}: runtime macro relation must be external; local steps cannot be introspected")
                compiler.runtime_dependencies[relation_key(dep)] = dep
                rendered = render_relation(dep, self.dialect)[3:-3].strip()
            else:
                rendered = repr(value)
            runtime_parts.append((key + "=" if key else "") + rendered)
        supplied = set(arguments[:len(positional)])
        required = set(arguments[:len(arguments) - len(definition.node.defaults)])
        if len(positional) > len(arguments) or supplied & keyword.keys() or required - (supplied | keyword.keys()):
            raise SpecError(f"{context}: invalid arguments to macro {name}")
        if definition.runtime:
            compiler.runtime_macros.add(name)
            if getattr(compiler, "logical_runtime", False):
                if not hasattr(compiler, "deferred_refs"):
                    compiler.deferred_refs = set()
                compiler.deferred_refs.add(name)
                return tree
            return compiler.token("{{ " + name + "(" + ", ".join(runtime_parts) + ") }}")
        try:
            result = getattr(self.module, name)(*positional, **keyword)
            expanded = scalar_tree(str(result), f"{context}: macro {name}", self.dialect)
            if getattr(compiler, "preserve_calls", False):
                return compiler.token("{{ " + name + "(" + ", ".join(runtime_parts) + ") }}")
            return self.expand(expanded, context, compiler, (*stack, name))
        except (TemplateError, TypeError, ValueError) as exc:
            if isinstance(exc, SpecError):
                raise
            raise SpecError(f"{context}: macro {name}: {exc}") from exc

    def validate_tests(self, values, context, column=None):
        for value in values:
            name, options = (value, {}) if isinstance(value, str) else next(iter(value.items()))
            definition = self.definitions.get("test_" + name)
            if definition is None or definition.runtime:
                continue
            args = {"model": "__slbt_test_model__", **({"column_name": column} if column else {}), **options.get("arguments", {})}
            required = {arg.name for arg in definition.node.args[:len(definition.node.args) - len(definition.node.defaults)]}
            allowed = {arg.name for arg in definition.node.args}
            if required - args.keys() or args.keys() - allowed:
                raise SpecError(f"{context}: invalid generic test arguments: {name}")
            try:
                result = getattr(self.module, "test_" + name)(**args)
                read_query(result, f"{context}: test {name}", self.dialect)
            except TemplateError as exc:
                raise SpecError(f"{context}: invalid generic test {name}: {exc}") from exc
