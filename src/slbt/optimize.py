"""Explicit, offline SQLGlot optimization and canonical model reconstruction."""

from __future__ import annotations

import copy
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer import optimize
from sqlglot.optimizer.scope import Scope, build_scope, traverse_scope
from sqlglot.optimizer.simplify import simplify

from slbt.catalog import apply_changes
from slbt.naming import alphabetic_names as _alphabetic_names, name_generated_relations
from slbt.project import dependency_order, load_project, optimization_settings, transformation_tree
from slbt.schema import SpecError, compact_columns, dotted, dump_yaml, identifier
from slbt.sql import Compiler, minimal_quotes, query_dependencies, read_query, scalar_tree, sql, structured_query, table_relation


@dataclass
class OptimizationResult:
    changed: list[Path] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    graphs: dict[str, list[tuple[str, ...]]] = field(default_factory=dict)


def _unique(name, taken):
    candidate, index = name, 2
    while candidate in taken:
        candidate = f"{name}_{index}"
        index += 1
    taken.add(candidate)
    return candidate


def _name_generated_relations(tree, original, model):
    name_generated_relations(tree, original, reserved=model.steps, context=model.name)


def _model_tree(model, macros, *, preserve_macro=True):
    compiler = Compiler(macros)
    trees = {name: transformation_tree(value, compiler, f"{model.name}.{name}")
             for name, value in [*model.steps.items(), ("slbt_output", model.output)]}
    if compiler.runtime_macros:
        names = ", ".join(repr(name) for name in sorted(compiler.runtime_macros))
        subject, verb = ("macro", "requires") if len(compiler.runtime_macros) == 1 else ("macros", "require")
        return None, {}, f"{subject} {names} {verb} dbt runtime and cannot be expanded offline"
    if preserve_macro:
        # Validate/identify runtime calls above before building an opaque macro AST.
        # Keeping column arguments visible lets qualification update their aliases.
        compiler = Compiler(macros, preserve_macro=True)
        trees = {name: transformation_tree(value, compiler, f"{model.name}.{name}")
                 for name, value in [*model.steps.items(), ("slbt_output", model.output)]}
    if any(with_.args.get("recursive") for tree in trees.values() for with_ in tree.find_all(exp.With)):
        raise SpecError(f"{model.name}: cannot optimize: recursive queries are not supported by canonical optimization")

    # Raw SQL has its own scoped CTE names. Never capture or shadow those names.
    taken = {cte.alias for tree in trees.values() for cte in tree.find_all(exp.CTE)}
    taken.update(table.name for tree in trees.values() for table in tree.find_all(exp.Table) if table.db != "step")
    taken.add("slbt_output")
    names = {name: _unique(name, taken) for name in model.steps}
    for tree in trees.values():
        for scope in traverse_scope(tree):
            for table in scope.tables:
                if len(table.parts) == 1 and isinstance(scope.sources.get(table.alias_or_name), Scope):
                    continue
                dependency = table_relation(table, model.name)
                if "step" in dependency:
                    old_name = dependency["step"]
                    if not table.args.get("alias"):
                        table.set("alias", exp.TableAlias(this=table.this.copy()))
                    table.set("this", exp.to_identifier(names[old_name], quoted=True))
                    table.set("db", None)
                    table.set("catalog", None)
                    for column in scope.columns:
                        if column.db == "step" and column.table == old_name:
                            column.set("db", None)
                else:
                    # The canonical namespaces are case-sensitive identifiers even
                    # when the selected SQL dialect normally folds names to uppercase.
                    for part in table.parts:
                        part.set("quoted", True)

    final = trees.pop("slbt_output")
    # Protect the declared final interface from dialect identifier normalization.
    for projection, name in zip(final.selects, model.output["columns"], strict=True):
        if isinstance(projection, exp.Alias):
            projection.set("alias", exp.to_identifier(name, quoted=True))
        else:
            projection.replace(exp.Alias(this=projection.copy(), alias=exp.to_identifier(name, quoted=True)))
    ctes = [exp.CTE(this=tree, alias=exp.TableAlias(this=exp.to_identifier(names[name], quoted=True))) for name, tree in trees.items()]
    if ctes:
        with_ = final.args.get("with_")
        final.set("with_", exp.With(expressions=[*ctes, *(with_.expressions if with_ else [])]))
    return final, {alias: model.steps[name] for name, alias in names.items()}, None


def _canonical(tree, original, dialect, context, format):
    """Prefer the structured subset, but never discard unsupported SQL clauses."""
    tree = minimal_quotes(tree, dialect)
    names = [projection.output_name for projection in tree.selects]
    metadata = {name: {key: copy.deepcopy(value) for key, value in original.get("columns", {}).get(name, {}).items() if key != "expression"} for name in names}
    pretty = format == "pretty"
    rendered = sql(tree, dialect, pretty=pretty)
    result = {"sql": rendered + ("\n" if pretty and "\n" in rendered else "")}
    valid_columns = len(set(names)) == len(names) and bool(names)
    try:
        for name in names:
            identifier(name, context)
    except SpecError:
        valid_columns = False
    if valid_columns:
        result["columns"] = metadata

    try:
        if format == "source" and "sql" in original:
            raise SpecError("retain authored raw SQL representation")
        if not valid_columns or not isinstance(tree, exp.Select) or any(isinstance(node, exp.Query) for node in tree.walk() if node is not tree):
            raise SpecError("query requires raw SQL")
        structured = structured_query(tree, context, dialect, pretty=pretty)
        for relation in [structured["from"], *structured.get("joins", [])]:
            if "step" in relation and relation.get("alias") == relation["step"]:
                del relation["alias"]
        if "distinct" not in structured and "distinct" in original:
            structured["distinct"] = False
        scalars = [structured[key] for key in ("where", "having") if key in structured]
        scalars += structured.get("group_by", [])
        scalars += [join["on"] for join in structured.get("joins", []) if "on" in join]
        for value in scalars:
            scalar_tree(value, context, dialect)
        structured["columns"] = {}
        for name, projection in zip(names, tree.selects, strict=True):
            value = projection.this if isinstance(projection, exp.Alias) else projection
            expression = sql(value, dialect, pretty=pretty)
            scalar_tree(expression, context, dialect)
            structured["columns"][name] = {"expression": expression, **metadata[name]}
        structured["columns"] = compact_columns(structured["columns"])
        result = structured
    except SpecError:
        pass
    if "description" in original:
        result["description"] = original["description"]
    return result


def _extract_scalar_aggregates(tree):
    """Expose safe one-row projection subqueries as CTEs and CROSS JOINs.

    Only uncorrelated, ungrouped aggregates with no row-removing clauses are
    eligible. Other scalar queries may return zero/multiple rows, so replacing
    them with joins would change NULL, cardinality, or error semantics.
    """
    root = build_scope(tree)
    if root is None:
        return
    top_scopes = {id(root), *(id(scope) for scope in root.cte_scopes)}
    cte_scopes = {id(scope) for scope in root.cte_scopes}
    taken = {"slbt_output"}
    taken.update(cte.alias for cte in tree.find_all(exp.CTE))
    taken.update(table.alias_or_name for table in tree.find_all(exp.Table))
    taken.update(column.table for column in tree.find_all(exp.Column) if column.table)
    ctes = {}
    for scope in list(root.traverse()):
        query, outer = scope.expression, scope.parent
        subquery = query.parent
        if (not scope.is_subquery or not isinstance(query, exp.Select)
                or not isinstance(subquery, exp.Subquery) or outer is None
                or id(outer) not in top_scopes or not isinstance(outer.expression, exp.Select)
                or scope.external_columns or len(query.selects) != 1):
            continue
        if any(value is not None for key, value in query.args.items() if key not in {"expressions", "from_", "joins", "where"}):
            continue
        if (not query.find(exp.AggFunc) or query.find(exp.Window, exp.UDTF, exp.GenerateSeries)
                or any(isinstance(node, exp.Query) for node in query.walk() if node is not query)):
            continue
        # Hoisting must not move a reference outside its local CTE scope.
        if any(isinstance(source, Scope) and id(source) not in cte_scopes for _, source in scope.selected_sources.values()):
            continue
        parent = outer.expression
        if (not parent.args.get("from_") or parent.args.get("group") or parent.args.get("having")
                or any(agg.find_ancestor(exp.Window) is None for agg in outer.find_all(exp.AggFunc))):
            continue
        if not any(node is subquery for projection in parent.selects for node in projection.walk()):
            continue

        name = _unique("scalar_aggregate", taken)
        projection = query.selects[0]
        column = projection.output_name
        try:
            identifier(column, "scalar aggregate")
        except SpecError:
            column = "value"
        body = query.copy()
        value = projection.this if isinstance(projection, exp.Alias) else projection
        body.set("expressions", [exp.alias_(value.copy(), column, quoted=True)])
        ctes.setdefault(id(outer), []).append(exp.CTE(this=body, alias=exp.TableAlias(this=exp.to_identifier(name, quoted=True))))
        subquery.replace(exp.column(column, table=name, quoted=True))
        parent.append("joins", exp.Join(this=exp.table_(name, quoted=True), kind="CROSS"))
    if ctes:
        with_ = tree.args.get("with_")
        ordered = []
        for scope, cte in zip(root.cte_scopes, with_.expressions if with_ else [], strict=True):
            ordered.extend([*ctes.get(id(scope), []), cte])
        ordered.extend(ctes.get(id(root), []))
        tree.set("with_", exp.With(expressions=ordered))


def _reconstruct(tree, model, originals, format):
    root = build_scope(tree)
    with_ = root.expression.args.get("with_") if root else None
    ctes = with_.expressions if with_ else []
    # Materialization hints and CTE column alias lists cannot be represented by a
    # canonical step. Preserve the complete query instead of weakening them.
    splittable = all(not cte.args.get("alias").columns and not any(value is not None for key, value in cte.args.items() if key not in {"this", "alias"}) for cte in ctes)
    steps, dependencies = {}, {}
    if ctes and splittable:
        taken = {"slbt_output"}
        names = {}
        for scope, cte in zip(root.cte_scopes, ctes, strict=True):
            name = re.sub(r"[^a-z0-9_]", "_", cte.alias.lower())
            if not name or name[0].isdigit():
                name = "subquery_" + name
            names[id(scope)] = _unique(name, taken)
        for scope in root.traverse():
            for table in scope.tables:
                source = scope.sources.get(table.alias_or_name)
                if len(table.parts) == 1 and isinstance(source, Scope) and id(source) in names:
                    if not table.args.get("alias"):
                        table.set("alias", exp.TableAlias(this=table.this.copy()))
                    table.set("this", exp.to_identifier(names[id(source)], quoted=True))
                    table.set("db", exp.to_identifier("step", quoted=True))
        root.expression.set("with_", None)
        for scope, cte in zip(root.cte_scopes, ctes, strict=True):
            name = names[id(scope)]
            steps[name] = _canonical(cte.this, originals.get(cte.alias, {}), model.dialect, f"{model.name}.{name}", format)
            dependencies[name] = query_dependencies(cte.this, f"{model.name}.{name}")

    names = [projection.output_name for projection in tree.selects]
    if names != list(model.output["columns"]):
        raise SpecError(f"{model.name}: optimization changed final projection names or order: {names}")
    output = _canonical(tree, model.output, model.dialect, f"{model.name}.output", format)
    data = {key: copy.deepcopy(value) for key, value in model.authored.items() if key not in {"steps", "output"}}
    if steps:
        dependencies["slbt_output"] = query_dependencies(tree, f"{model.name}.output")
        order = dependency_order(dependencies, model.name)
        data["steps"] = {name: steps[name] for name in order if name != "slbt_output"}
    data["output"] = output
    return data


GRAPH_HEADERS = ("Order", "Type", "Name", "Inputs", "Operation", "Distinct")


def graph_rows(model):
    """Inferred query edges, shared with model validation and catalog export."""
    nodes, sources = [], set()
    for name, value in [*model.steps.items(), ("output", model.output)]:
        tree = read_query(value["sql"], model.name, model.dialect) if "sql" in value else None
        inputs = model.inputs["slbt_output" if value is model.output else name]
        inputs = sorted({dotted(item) for item in inputs})
        sources.update(item for item in inputs if not item.startswith("step."))
        if isinstance(tree, exp.SetOperation):
            operation = tree.key.upper() + (" ALL" if tree.args.get("distinct") is False else "")
        else:
            operation = "SELECT DISTINCT" if value.get("distinct") or tree is not None and tree.args.get("distinct") else "SELECT"
        distinct = str(value["distinct"]).upper() if "distinct" in value else ""
        nodes.append(("Output" if value is model.output else "Step", name, ", ".join(inputs), operation, distinct))
    nodes = [("Source", name, "", "READ", "") for name in sorted(sources)] + nodes
    return [(str(index), *row) for index, row in enumerate(nodes, 1)]


def format_graph(rows):
    values = [GRAPH_HEADERS, *rows]
    widths = [max(len(row[index]) for row in values) for index in range(len(GRAPH_HEADERS))]
    lines = [" | ".join(value.ljust(width) for value, width in zip(row, widths, strict=True)).rstrip() for row in values]
    lines.insert(1, "-+-".join("-" * width for width in widths))
    return "\n".join(lines)


def optimize_project(project, *, models=None, dry_run=False, format=None, preserve_macro=None, enrich=None, preserve_marco=None) -> OptimizationResult:
    """Validate every proposed rewrite before staging any selected model files."""
    if preserve_macro is not None and preserve_marco is not None:
        raise SpecError("use preserve_macro or its legacy alias preserve_marco, not both")
    if preserve_marco is not None:
        preserve_macro = preserve_marco
    settings = dict(project.optimization)
    if format is not None:
        settings["format"] = format
    if preserve_macro is not None:
        settings["preserve_macro"] = preserve_macro
    if enrich is not None:
        settings["enrich"] = enrich
    settings = optimization_settings(settings)
    by_name = {model.name: model for model in project.models}
    selected = list(dict.fromkeys(models)) if models is not None else list(by_name)
    unknown = set(selected) - by_name.keys()
    if unknown:
        raise SpecError(f"unknown models: {sorted(unknown)}")
    candidates, result = {}, OptimizationResult()
    for name in selected:
        model = by_name[name]
        tree, originals, reason = _model_tree(model, project.macros, preserve_macro=settings["preserve_macro"])
        if reason:
            result.skipped[name] = reason
            continue
        if settings["preserve_macro"]:
            _check_preserved_macros(model, project.macros)
        try:
            optimized = optimize(tree, dialect=project.dialect)
        except SqlglotError as exc:
            raise SpecError(f"{name}: SQLGlot optimization failed (offline schemas may be insufficient): {exc}") from exc
        _extract_scalar_aggregates(optimized)
        _name_generated_relations(optimized, tree, model)
        # Comparison/connector ordering depends on alias spelling. Re-canonicalize
        # after renaming so the next optimize run does not make cosmetic changes.
        optimized = simplify(optimized, dialect=project.dialect)
        data = _reconstruct(optimized, model, originals, settings["format"])
        if (data != model.authored or list(data) != list(model.authored)
                or list(data.get("steps", {})) != list(model.authored.get("steps", {}))):
            candidates[model.name] = data
    changes = project.rewrite_models(candidates)
    proposed = load_project(project.path, changes)
    if settings["enrich"]:
        from slbt.enrich import enrichment_candidates
        eligible = [name for name in selected if name not in result.skipped]
        enriched, result.report = enrichment_candidates(proposed, eligible) if eligible else ({}, [])
        candidates.update(enriched)
        changes = project.rewrite_models(candidates)
        proposed = load_project(project.path, changes)
    proposed_models = {model.name: model for model in proposed.models}
    result.graphs = {name: graph_rows(proposed_models[name]) for name in selected if name not in result.skipped}
    changes = {path: content for path, content in changes.items() if path.read_bytes() != content}
    if not dry_run:
        apply_changes(changes)
    result.changed = sorted(changes)
    return result


def _check_preserved_macros(model, macros):
    """Opaque calls must expose dependencies and may not hide query semantics."""
    def semantics(trees):
        return Counter(sql(node, model.dialect) for tree in trees for node in tree.find_all(exp.AggFunc, exp.Window, exp.Query))

    for value in [*model.steps.values(), model.output]:
        compiler = Compiler(macros, preserve_macro=True)
        tree = transformation_tree(value, compiler, model.name)
        for call in tree.find_all(exp.Anonymous):
            definition = macros.definitions.get(call.name.lower())
            if definition is None:
                continue
            expanded = macros.expand(call.copy(), model.name, Compiler(macros))
            # Expanding argument copies normalizes nested static calls. They are
            # checked separately below, so only semantics changed by this macro
            # are hidden. Counts also catch duplicated or discarded aggregates.
            arguments = [macros.expand(argument.copy(), model.name, Compiler(macros)) for argument in call.expressions]
            if semantics([expanded]) != semantics(arguments):
                raise SpecError(f"{model.name}: preserved macro {call.name} hides aggregate/window/query semantics")
            visible = {column.sql() for argument in call.expressions for column in argument.find_all(exp.Column)}
            hidden = {column.sql() for column in expanded.find_all(exp.Column)} - visible
            if hidden:
                raise SpecError(f"{model.name}: preserved macro {call.name} hides column dependencies: {sorted(hidden)}")
