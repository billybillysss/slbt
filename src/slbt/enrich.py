"""Local, conservative metadata inference; no warehouse or network access."""

import copy

from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.annotate_types import annotate_types
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, build_scope, traverse_scope

from slbt.project import load_project, transformation_tree
from slbt.schema import SpecError
from slbt.sql import Compiler, relation_tables, table_relation


def enrichment_candidates(project, names=()):
    by_name = {model.name: model for model in project.models}
    selected = list(dict.fromkeys(names)) if names else list(by_name)
    if set(selected) - by_name.keys():
        raise SpecError(f"unknown models: {sorted(set(selected) - by_name.keys())}")
    completed, active, report = {}, set(), []

    def infer(name):
        if name in completed:
            return completed[name]
        if name in active:
            return {}
        active.add(name)
        model = by_name[name]
        values, authored = {}, copy.deepcopy(model.authored)
        for step, transformation in [*model.steps.items(), ("slbt_output", model.output)]:
            metadata = copy.deepcopy(transformation["columns"])
            known, schema, origins = {}, {}, {}
            reason = "unavailable or ambiguous expression metadata"
            try:
                compiler = Compiler(project.macros)
                tree = transformation_tree(transformation, compiler, name)
                if compiler.tokens:
                    raise SpecError("runtime macro metadata is unavailable offline")
                for table in list(relation_tables(tree)):
                    dep = table_relation(table, name)
                    if "step" in dep:
                        columns = values.get(dep["step"], {})
                    elif "ref" in dep:
                        columns = infer(dep["ref"]) if dep["ref"] in by_name else {}
                    else:
                        identity = ".".join(dep["source"])
                        columns = project.sources.get(dep["source"][0], {}).get("tables", {}).get(identity, {}).get("columns", {})
                    fake = "__slbt_relation_" + str(len(schema))
                    schema[fake] = {n: c.get("data_type") or "UNKNOWN" for n, c in columns.items()}
                    known[fake] = columns
                    if not table.args.get("alias"):
                        table.set("alias", exp.TableAlias(this=table.this.copy()))
                    table.set("this", exp.to_identifier(fake))
                    table.set("db", None)
                    table.set("catalog", None)
                for column in tree.find_all(exp.Column):
                    column.set("db", None)
                    column.set("catalog", None)
                qualified = qualify(tree, dialect=project.dialect, schema=schema, validate_qualify_columns=False, identify=False, quote_identifiers=False)
                annotated = annotate_types(qualified, schema=schema, dialect=project.dialect)
                root = build_scope(annotated)

                def description(scope, expression, seen=None):
                    expression = expression.this if isinstance(expression, exp.Alias) else expression
                    if not isinstance(expression, exp.Column):
                        return None
                    source = scope.sources.get(expression.table)
                    if isinstance(source, exp.Table):
                        return known.get(source.name, {}).get(expression.name, {}).get("description")
                    if isinstance(source, Scope):
                        seen = set() if seen is None else seen
                        key = (id(source), expression.name)
                        if key in seen:
                            return None
                        seen.add(key)
                        for projection in source.expression.selects:
                            if projection.output_name == expression.name:
                                return description(source, projection, seen)
                    return None

                for projection in annotated.selects:
                    typ = projection.type
                    dtype = typ.sql(dialect=project.dialect).lower() if typ is not None and typ.this not in {exp.DataType.Type.UNKNOWN, exp.DataType.Type.NULL} else None
                    origins[projection.output_name] = {"data_type": dtype, "description": description(root, projection) if root else None}
            except (SpecError, SqlglotError, ValueError, TypeError) as exc:
                reason = str(exc)
            target = authored["output"] if step == "slbt_output" else authored["steps"][step]
            for column_name, column in metadata.items():
                for key in ("data_type", "description"):
                    if column.get(key):
                        continue
                    value = origins.get(column_name, {}).get(key)
                    entry = {"model": name, "step": "output" if step == "slbt_output" else step, "column": column_name, "property": key}
                    if value:
                        column[key] = value
                        raw = target.setdefault("columns", {}).get(column_name, {})
                        raw = {"expression": raw} if isinstance(raw, str) else copy.deepcopy(raw)
                        raw[key] = value
                        target["columns"][column_name] = raw
                        entry["value"] = value
                    else:
                        entry["reason"] = reason
                    if name in selected:
                        report.append(entry)
            values[step] = metadata
        active.remove(name)
        completed[name] = values["slbt_output"]
        candidates[name] = authored
        return completed[name]

    candidates = {}
    for name in selected:
        infer(name)
    return {name: value for name, value in candidates.items() if name in selected and value != by_name[name].authored}, report


def enrich_project(configuration, names=(), *, dry_run=False):
    from slbt.catalog.review import apply_changes
    candidates, report = enrichment_candidates(configuration, names)
    changes = configuration.rewrite_models(candidates)
    load_project(configuration.path, changes)
    changes = {p: c for p, c in changes.items() if p.read_bytes() != c}
    if not dry_run:
        apply_changes(changes)
    return sorted(changes), report
