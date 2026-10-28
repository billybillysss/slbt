"""Pure dbt SQL/properties renderers; reconciliation is a separate boundary."""

import copy
from pathlib import Path, PurePosixPath

from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from slbt.config import render_config
from slbt.definitions.model import registry, resolved
from slbt.definitions.source import dbt_source, validate as validate_source
from slbt.compiler.snapshot import compile_snapshot
from slbt.schema import SpecError, dump_yaml
from slbt.sql import Compiler, read_query, relation_tables, render_relation


def properties(model):
    result = {"name": model.name}
    for key in ("description", "constraints", "data_tests"):
        if key in model.authored:
            result[key] = copy.deepcopy(model.authored[key])
    if "meta" in model.config:
        result["config"] = {"meta": copy.deepcopy(model.config["meta"])}
    result["columns"] = [{"name": name, **{k: copy.deepcopy(v) for k, v in column.items() if k != "expression"}} for name, column in model.output["columns"].items()]
    return result


def _source_sql(model, compiler):
    """Translate actual relation tokens, never strings/comments or SQL-local CTEs."""
    source = model.output["sql"]
    tree = read_query(source, model.name, model.dialect)
    if any(isinstance(node, exp.Anonymous) and node.name.lower() in compiler.macros.definitions for node in tree.walk()):
        return None
    edits = []
    from slbt.sql import table_relation
    for table in relation_tables(tree):
        parts = table.parts
        if any("start" not in part.meta or "end" not in part.meta for part in parts):
            return None
        edits.append((min(p.meta["start"] for p in parts), max(p.meta["end"] for p in parts) + 1, render_relation(table_relation(table, model.name), model.dialect)))
    for start, end, replacement in sorted(edits, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source.rstrip() + "\n"


def render_model(model, macros, *, pretty=False, preserve_source=False, preserve_macros=False):
    from slbt.project import transformation_tree
    compiler = Compiler(macros)
    compiler.preserve_calls = preserve_macros
    if preserve_source and not model.steps and "sql" in model.output:
        source = _source_sql(model, compiler)
        if source is not None:
            return render_config(model.config, pretty=pretty) + source
    trees = {name: transformation_tree(value, compiler, f"{model.name}.{name}") for name, value in [*model.steps.items(), ("slbt_output", model.output)]}
    taken = {cte.alias.casefold() for tree in trees.values() for cte in tree.find_all(exp.CTE)}
    names = {}
    for name in model.steps:
        candidate, index = "__slbt_" + name, 2
        while candidate.casefold() in taken:
            candidate, index = "__slbt_" + name + "_" + str(index), index + 1
        names[name] = candidate
        taken.add(candidate.casefold())
    for tree in trees.values():
        for scope in traverse_scope(tree):
            for table in scope.tables:
                if len(table.parts) == 1 and isinstance(scope.sources.get(table.alias_or_name), Scope):
                    continue
                if table.db == "step":
                    original = table.name
                    if not table.args.get("alias"):
                        table.set("alias", exp.TableAlias(this=exp.to_identifier(original, quoted=True)))
                    table.set("this", exp.to_identifier(names[original], quoted=True))
                    table.set("db", None)
                    for column in scope.columns:
                        if column.db == "step" and column.table == original:
                            column.set("db", None)
    final = trees.pop("slbt_output")
    # Lower external tables before inserting generated CTEs (their local names
    # deliberately do not use the authored relation namespace).
    def lower(tree):
        for table in list(relation_tables(tree)):
            if table.name in names.values() and not table.db:
                continue
            from slbt.sql import table_relation
            dep = table_relation(table, model.name)
            table.set("this", compiler.token(render_relation(dep, model.dialect)))
            table.set("db", None)
            table.set("catalog", None)
        return tree
    final = lower(final)
    ctes = [exp.CTE(this=lower(tree), alias=exp.TableAlias(this=exp.to_identifier(names[name], quoted=True))) for name, tree in trees.items()]
    if ctes:
        with_ = final.args.get("with_")
        final.set("with_", exp.With(expressions=[*ctes, *(with_.expressions if with_ else [])], recursive=bool(with_ and with_.args.get("recursive"))))
    config = render_config(model.config, pretty=pretty)
    hints = "".join("-- depends_on: " + render_relation(dep, model.dialect) + "\n" for dep in model.runtime_dependencies)
    return config + hints + compiler.finish(final, pretty=pretty) + "\n"


def compile_sql(model, macros=None, *, pretty=False, dialect="spark", preserve_source=False, preserve_macros=False):
    macros = registry(macros, dialect)
    return render_model(resolved(model, macros, dialect=dialect), macros, pretty=pretty, preserve_source=preserve_source, preserve_macros=preserve_macros)


def schema(models, macros=None, *, dialect="spark"):
    return dump_yaml({"version": 2, "models": [properties(resolved(value, macros, dialect=dialect)) for value in models.values()]}).decode("utf-8")


def dbt_outputs(configuration, subpath=None):
    project = configuration
    files, schemas = {}, {}
    def put(path, content):
        path = PurePosixPath(path).as_posix()
        if path in files:
            raise SpecError(f"conflicting generated output: {path}")
        files[path] = content
    for model in project.models:
        folder = PurePosixPath(model.relative.parent.as_posix())
        put(PurePosixPath("models") / folder / (model.name + ".sql"), render_model(model, project.macros, pretty=project.authored["dbt"].get("format", "pretty") != "compact", preserve_source=project.authored["dbt"].get("format") == "source", preserve_macros=project.optimization["preserve_macro"]).encode())
        target = PurePosixPath("models") / folder / ((folder.name if folder.name else "models") + ".yml")
        schemas.setdefault(target, {"version": 2}).setdefault("models", []).append(properties(model))
        if model.snapshot:
            put(PurePosixPath("snapshots") / folder / (model.snapshot["name"] + ".sql"), compile_snapshot(model, pretty=project.authored["dbt"].get("format", "pretty") != "compact").encode())
    for doc in project.documents:
        if not doc.sources:
            continue
        target = PurePosixPath("models") / doc.path.relative_to(project.model_root).with_suffix(".yml").as_posix()
        content = schemas.setdefault(target, {"version": 2})
        content.setdefault("sources", []).extend(dbt_source(validate_source(group)) for group in doc.sources)
    for target, value in schemas.items():
        put(target, dump_yaml(value))
    for file in sorted(project.macro_root.rglob("*.sql")):
        put(PurePosixPath("macros") / file.relative_to(project.macro_root).as_posix(), file.read_bytes())
    for file, value in project.macros.property_files.items():
        if "macros" in value:
            put(PurePosixPath("macros") / file.relative_to(project.macro_root).as_posix(), dump_yaml({"version": 2, "macros": value["macros"]}))
    if subpath is not None:
        scope = PurePosixPath(subpath)
        files = {path: content for path, content in files.items() if PurePosixPath(*PurePosixPath(path).parts[1:]).is_relative_to(scope)}
    return dict(sorted(files.items()))
