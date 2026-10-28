"""Project resolution, defaults, canonical model validation, and local graphs."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path

from sqlglot import exp
from sqlglot.dialects import Dialect

from slbt.macros import Registry
from slbt.paths import configured_path, overlaps, safe_path, validate_destinations
from slbt.schema import SpecError, classification, columns, constraints, fields, identifier, literal, load_yaml, mapping, relation, relation_key, tests, text, version, yaml_files
from slbt.definitions import classifications, defaults, document, source, snapshot
from slbt.sql import Compiler, order_tree, query_dependencies, relation_tree, scalar_tree


@dataclass
class Model:
    path: Path
    relative: Path
    authored: dict
    config: dict
    steps: dict
    output: dict
    queries: dict[str, str]
    dependencies: list[dict]
    dialect: str = "spark"
    inputs: dict[str, list[dict]] = field(default_factory=dict)
    # Only macro relation arguments absent from SQL table inputs need dbt hints.
    runtime_dependencies: list[dict] = field(default_factory=list)

    @property
    def name(self):
        return self.authored["name"]


@dataclass(init=False)
class Project:
    path: Path
    authored: dict
    model_root: Path
    macro_root: Path
    catalog_root: Path
    dbt_root: Path
    models: list[Model]
    macros: Registry
    optimization: dict

    def __init__(self, path):
        self.__dict__.update(load_project(Path(path)).__dict__)

    @property
    def authored_models(self):
        return {model.name: copy.deepcopy(model.authored) for model in self.models}

    def destination(self, kind, path=None):
        destination = configured_path(self.path.parent, str(path), "export path", output=True, override=True) if path is not None else (self.dbt_root if kind == "dbt" else self.catalog_root)
        validate_destinations([self.path, self.model_root, self.macro_root], destination if kind == "dbt" else self.dbt_root, destination if kind == "catalog" else self.catalog_root)
        if destination.exists() and not destination.is_dir():
            raise SpecError("output destination must be a directory")
        return destination

    def rewrite_models(self, models):
        return {doc.path: document.render(doc, models=models) for doc in self.documents if any(model["name"] in models for model in doc.models)}

    @property
    def dialect(self):
        return self.macros.dialect


def resolve_project(project_dir: Path | None = None) -> Path:
    value = project_dir if project_dir is not None else os.environ.get("slbt_PROJECT_DIR")
    if value is not None:
        return Path(value).absolute() / "slbt_project.yaml"
    current = Path.cwd()
    for directory in (current, *current.parents):
        candidate = directory / "slbt_project.yaml"
        if candidate.is_file():
            return candidate
    return current / "slbt_project.yaml"


def default_rules(value: dict | None) -> list:
    if value is None:
        return []
    mapping(value, "model_defaults")
    fields(value, {"name", "configs"}, {"name", "configs"}, "model_defaults")
    identifier(value["name"], "model_defaults.name")
    if not isinstance(value["configs"], list):
        raise SpecError("model_defaults.configs must be a list")
    names, result = set(), []
    for item in value["configs"]:
        if not isinstance(item, dict) or len(item) != 1:
            raise SpecError("each defaults rule must have one name")
        name, rule = next(iter(item.items()))
        identifier(name, "defaults rule name")
        if name in names:
            raise SpecError(f"duplicate defaults rule: {name}")
        names.add(name)
        mapping(rule, name)
        fields(rule, {"config", "apply_to"}, {"config", "apply_to"}, name)
        mapping(rule["config"], name)
        selectors = mapping(rule["apply_to"], name)
        fields(selectors, {"layer", "domain", "tables"}, set(), name)
        for key, values in selectors.items():
            if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
                raise SpecError(f"{name}: default selectors require lists of exact text values")
            if key == "layer" and any(v not in LAYERS for v in values):
                raise SpecError(f"{name}: invalid default layer")
        result.append(rule)
    return result


MISSING = object()


def merge_defaults(values: list, explicit=MISSING, context="config"):
    if explicit is not MISSING:
        if not isinstance(explicit, dict) or not explicit:
            return copy.deepcopy(explicit)
        dictionaries = [value for value in values if isinstance(value, dict)]
        keys = set(explicit) | {key for value in dictionaries for key in value}
        return {key: merge_defaults([value[key] for value in dictionaries if key in value], explicit.get(key, MISSING), f"{context}.{key}") for key in sorted(keys)}
    if not values:
        return {}
    if all(isinstance(value, dict) for value in values):
        keys = {key for value in values for key in value}
        return {key: merge_defaults([value[key] for value in values if key in value], context=f"{context}.{key}") for key in sorted(keys)}
    if any(type(value) is not type(values[0]) or value != values[0] for value in values[1:]):
        raise SpecError(f"{context}: conflicting defaults require an explicit model override")
    return copy.deepcopy(values[0])


def effective_config(data: dict, rules: list) -> dict:
    explicit = literal(mapping(data["config"], "config")) if "config" in data else defaults.MISSING
    config = defaults.resolve(data, rules, explicit=explicit)
    if config.keys() & {"version", "layer", "domain", "name", "description", "steps", "output"}:
        raise SpecError("model fields cannot be placed inside config")
    if "materialized" in config:
        identifier(config["materialized"], "config.materialized")
    if "meta" in config:
        mapping(config["meta"], "config.meta")
    if "domain" in data:
        meta = config.setdefault("meta", {})
        if "domain" in meta and meta["domain"] != data["domain"]:
            raise SpecError("config.meta.domain conflicts with the model domain")
        meta["domain"] = data["domain"]
    if "contract" in config:
        mapping(config["contract"], "config.contract")
        if "enforced" in config["contract"] and type(config["contract"]["enforced"]) is not bool:
            raise SpecError("config.contract.enforced must be boolean")
    return config


def transformation(value, context: str, *, output: bool):
    value = copy.deepcopy(mapping(value, context))
    fields(value, {"from", "joins", "where", "columns", "group_by", "having", "distinct", "order_by", "limit", "description", "sql"}, {"columns"} if output else set(), context)
    if ("sql" in value) == ("from" in value):
        raise SpecError(f"{context}: specify either structured from or raw sql")
    if "description" in value:
        text(value["description"], context, empty=True)
    if "sql" in value:
        if value.keys() & {"joins", "where", "group_by", "having", "distinct", "order_by", "limit"}:
            raise SpecError(f"{context}: raw SQL cannot have structured fields")
        text(value["sql"], context)
    else:
        if "distinct" in value and type(value["distinct"]) is not bool:
            raise SpecError(f"{context}: distinct must be boolean")
        if "columns" not in value:
            raise SpecError(f"{context}: structured transformations require columns")
        value["from"] = relation(value["from"], context)
        inputs = [value["from"]]
        if "joins" in value:
            if not isinstance(value["joins"], list):
                raise SpecError(f"{context}: joins must be a list")
            for join in value["joins"]:
                mapping(join, context)
                fields(join, {"ref", "source", "step", "alias", "type", "on"}, {"type"}, context)
                dep = relation({k: v for k, v in join.items() if k not in {"type", "on"}}, context)
                inputs.append(dep)
                if join["type"] not in ("inner", "left", "right", "full", "cross"):
                    raise SpecError(f"{context}: unsupported join type")
                if join["type"] == "cross" and "on" in join:
                    raise SpecError(f"{context}: cross joins must not have on")
                if join["type"] != "cross":
                    text(join.get("on"), f"{context}: non-cross joins require on")
                join.update(dep)
        aliases = [dep["alias"] for dep in inputs if "alias" in dep]
        if len(aliases) != len(set(aliases)):
            raise SpecError(f"{context}: duplicate relation aliases")
        for key in ("where", "having"):
            if key in value:
                text(value[key], context)
        for key in ("group_by", "order_by"):
            if key in value:
                if not isinstance(value[key], list):
                    raise SpecError(f"{context}: {key} must be a list")
                for expression in value[key]:
                    text(expression, context)
        if "order_by" in value and not value["order_by"]:
            raise SpecError(f"{context}: order_by must be nonempty")
        if "order_by" in value and not output and "limit" not in value:
            raise SpecError(f"{context}: intermediate ORDER BY requires a limit")
        if "limit" in value and (type(value["limit"]) is not int or value["limit"] <= 0):
            raise SpecError(f"{context}: limit must be a positive integer")
    value["columns"] = columns(value["columns"], context, structured="from" in value, output=output) if "columns" in value else {}
    return value


def transformation_tree(value, compiler, context):
    """Build canonical SQL before lowering relations or runtime Jinja."""
    if "sql" in value:
        return compiler.query_tree(value["sql"], value["columns"], context)

    def scalar(expression):
        return compiler.expand(scalar_tree(expression, context, compiler.dialect), context)

    query = exp.Select(expressions=[exp.Alias(this=scalar(column["expression"]), alias=exp.to_identifier(name, quoted=True)) for name, column in value["columns"].items()])
    query.set("from_", exp.From(this=relation_tree(value["from"])))
    if value.get("distinct", False):
        query.set("distinct", exp.Distinct())
    joins = []
    for join in value.get("joins", []):
        node = exp.Join(this=relation_tree(join))
        node.set("side" if join["type"] in {"left", "right", "full"} else "kind", join["type"].upper())
        if "on" in join:
            node.set("on", scalar(join["on"]))
        joins.append(node)
    if joins:
        query.set("joins", joins)
    for key, wrapper in (("where", exp.Where), ("having", exp.Having)):
        if key in value:
            query.set(key, wrapper(this=scalar(value[key])))
    if value.get("group_by"):
        query.set("group", exp.Group(expressions=[scalar(item) for item in value["group_by"]]))
    if value.get("order_by"):
        query.set("order", exp.Order(expressions=[compiler.expand(order_tree(item, context, compiler.dialect), context) for item in value["order_by"]]))
    if "limit" in value:
        query.set("limit", exp.Limit(expression=exp.Literal.number(value["limit"])))
    return query


def dependency_order(dependencies, context):
    """Stable local graph order, with the final output last."""
    active, ordered = [], []

    def visit(name):
        if name in active:
            raise SpecError(f"{context}: local step cycle: " + " -> ".join(active + [name]))
        if name not in dependencies:
            raise SpecError(f"{context}: unknown local step {name}")
        if name in ordered:
            return
        active.append(name)
        for dependency in sorted({item["step"] for item in dependencies[name] if "step" in item}):
            visit(dependency)
        active.pop()
        ordered.append(name)

    visit("slbt_output")
    if set(dependencies) != set(ordered):
        raise SpecError(f"{context}: unused intermediate steps {sorted(set(dependencies) - set(ordered))}")
    return ordered


def parse_model(path, root, rules, macros, overrides=None, *, data=None):
    data = copy.deepcopy(data) if data is not None else load_yaml(path, overrides)
    fields(data, {"version", "name", "layer", "domain", "description", "config", "constraints", "data_tests", "steps", "output", "snapshot"}, {"version", "name", "layer", "output"}, str(path))
    version(data["version"], str(path))
    name = identifier(data["name"], str(path))
    classification(data["layer"], f"{name}.layer")
    if "domain" in data:
        classification(data["domain"], name)
    if "description" in data:
        text(data["description"], name, empty=True)
    config = effective_config(data, rules)
    steps = mapping(data.get("steps", {}), f"{name}.steps")
    parsed = {}
    for step_name, value in steps.items():
        identifier(step_name, name)
        if step_name == "slbt_output":
            raise SpecError(f"{name}: slbt_output is reserved")
        parsed[step_name] = transformation(value, f"{name}.{step_name}", output=False)
    parsed["slbt_output"] = transformation(data["output"], f"{name}.output", output=True)
    compiler = Compiler(macros)
    trees, dependencies, runtime, tables = {}, {}, {}, {}
    for key, value in parsed.items():
        context = f"{name}.{key}"
        compiler.runtime_dependencies = {}
        trees[key] = transformation_tree(value, compiler, context)
        inferred = {relation_key(dep): dep for dep in query_dependencies(trees[key], context)}
        tables.update(inferred)
        runtime.update(compiler.runtime_dependencies)
        inferred.update(compiler.runtime_dependencies)
        dependencies[key] = [inferred[item] for item in sorted(inferred)]
    ordered = dependency_order(dependencies, name)
    output = parsed["slbt_output"]
    for key, tree in trees.items():
        if tree.args.get("order") and not tree.args.get("limit"):
            if key != "slbt_output":
                raise SpecError(f"{name}.{key}: intermediate ORDER BY requires a limit")
            if macros.dialect == "tsql" and config.get("materialized", "view") == "view" and not tree.args.get("offset"):
                raise SpecError(f"{name}: T-SQL view ORDER BY requires TOP/limit or OFFSET")
    model_constraints = constraints(data.get("constraints", []), name, model=True)
    primary_keys = [r for r in model_constraints if r["type"] == "primary_key"] + [r for c in output["columns"].values() for r in c.get("constraints", []) if r["type"] == "primary_key"]
    if len(primary_keys) > 1:
        raise SpecError(f"{name}: at most one primary-key constraint is allowed")
    for rule in model_constraints:
        if set(rule.get("columns", [])) - output["columns"].keys():
            raise SpecError(f"{name}: constraint references unknown output columns")
    if config.get("contract", {}).get("enforced") and any(not c.get("data_type") for c in output["columns"].values()):
        raise SpecError(f"{name}: enforced contracts require data_type on every output column")
    macros.validate_tests(tests(data.get("data_tests", []), name), name)
    for column_name, column in output["columns"].items():
        macros.validate_tests(column.get("data_tests", []), name, column_name)
    effective_snapshot = None
    if "snapshot" in data:
        effective_snapshot = copy.deepcopy(data["snapshot"])
        mapping(effective_snapshot, "snapshot")
        effective_snapshot["config"] = defaults.resolve(data, rules, "snapshot_configs", effective_snapshot.get("config", defaults.MISSING))
        snapshot.validate(effective_snapshot, output["columns"])
    queries = {key: compiler.lower(trees[key], f"{name}.{key}", pretty=True) for key in ordered}
    external = {relation_key(dep): dep for values in dependencies.values() for dep in values if "step" not in dep}
    result = Model(path, path.relative_to(root), copy.deepcopy(data), config, {key: parsed[key] for key in ordered if key != "slbt_output"}, output, queries, [external[key] for key in sorted(external)], macros.dialect,
                  inputs=dependencies, runtime_dependencies=[runtime[key] for key in sorted(runtime.keys() - tables.keys())])
    result.snapshot = effective_snapshot
    return result


def optimization_settings(value):
    value = mapping(value, "optimization")
    fields(value, {"dialect", "format", "preserve_macro", "preserve_marco", "enrich"}, set(), "optimization")
    dialect = text(value.get("dialect", "spark"), "optimization.dialect")
    try:
        Dialect.get_or_raise(dialect)
    except ValueError as exc:
        raise SpecError(f"optimization.dialect: {exc}") from exc
    format = text(value.get("format", "pretty"), "optimization.format")
    if format not in ("pretty", "compact"):
        raise SpecError("optimization.format: expected pretty or compact")
    if "preserve_macro" in value and "preserve_marco" in value:
        raise SpecError("optimization: use preserve_macro or its legacy alias preserve_marco, not both")
    key = "preserve_marco" if "preserve_marco" in value else "preserve_macro"
    preserve_macro = value.get(key, True)
    if type(preserve_macro) is not bool:
        raise SpecError(f"optimization.{key}: expected a boolean")
    enrich = value.get("enrich", False)
    if type(enrich) is not bool:
        raise SpecError("optimization.enrich: expected a boolean")
    return {"dialect": dialect, "format": format, "preserve_macro": preserve_macro, "enrich": enrich}


def load_project(path: Path, overrides: dict | None = None) -> Project:
    path = path.absolute()
    safe_path(path)
    path = path.resolve()
    data = load_yaml(path, overrides)
    fields(data, {"version", "model_path", "macro_path", "catalog_path", "optimization", "dbt", "catalog"}, {"version", "model_path", "macro_path", "catalog_path", "dbt", "catalog"}, str(path))
    version(data["version"], str(path))
    dbt = mapping(data["dbt"], "dbt")
    fields(dbt, {"path", "format", "default_configs"}, {"path"}, "dbt")
    if dbt.get("format", "pretty") not in {"pretty", "compact", "source"}:
        raise SpecError("dbt.format must be pretty, compact, or source")
    roots = [configured_path(path.parent, data[key], key, output=key == "catalog_path") for key in ("model_path", "macro_path", "catalog_path")]
    model_root, macro_root, catalog_root = roots
    dbt_root = configured_path(path.parent, dbt["path"], "dbt.path", output=True)
    if any(not root.is_dir() for root in (model_root, macro_root)):
        raise SpecError("model_path and macro_path must be existing directories")
    if overlaps(model_root, macro_root):
        raise SpecError("model and macro input directories must not overlap")
    validate_destinations([path, model_root, macro_root], dbt_root, catalog_root)
    for output in (dbt_root, catalog_root):
        if output.exists() and not output.is_dir():
            raise SpecError("output destination must be a directory")
    for root in (model_root, macro_root):
        safe_path(root, tree=True)
    optimization = optimization_settings(data.get("optimization", {}))
    dialect = optimization["dialect"]
    rules = defaults.validate(dbt.get("default_configs"))
    if "default_configs" in dbt and rules is None:
        raise SpecError("dbt.default_configs must be a mapping")
    macros = Registry(macro_root, overrides, dialect=dialect)
    registry_path = model_root / "classifications.yaml"
    registry = classifications.validate(load_yaml(registry_path, overrides))
    documents = [document.load(file, overrides) for file in yaml_files(model_root) if file != registry_path]
    models = [parse_model(doc.path, model_root, rules, macros, data=model) for doc in documents for model in doc.models]
    by_name = {}
    for model in models:
        if model.name in by_name:
            raise SpecError(f"duplicate model name/filename stem: {model.name}")
        by_name[model.name] = model
    sources, source_paths, snapshots = {}, {}, set(by_name)
    for model in models:
        classifications.check(registry, model.authored["layer"], model.authored.get("domain"))
        if model.snapshot:
            name = model.snapshot["name"]
            if name.casefold() in snapshots:
                raise SpecError(f"snapshot/model name collision: {name}")
            snapshots.add(name.casefold())
    for doc in documents:
        for authored in doc.sources:
            group = source.validate(authored)
            name = group["name"]
            existing = next((n for n in sources if n.casefold() == name.casefold()), None)
            if existing is not None and existing != name:
                raise SpecError("case-insensitive source group collision")
            if existing is not None and not defaults.equal({k: v for k, v in sources[name].items() if k != "tables"}, {k: v for k, v in group.items() if k != "tables"}):
                raise SpecError(f"conflicting source group settings: {name}")
            target = sources.setdefault(name, {**group, "tables": {}})
            for identity, table in group["tables"].items():
                if identity.casefold() in {n.casefold() for n in source_paths}:
                    raise SpecError(f"duplicate source table: {identity}")
                classifications.check(registry, table["layer"], table["domain"])
                target["tables"][identity] = table
                source_paths[identity] = doc.path
    project = object.__new__(Project)
    project.__dict__.update(path=path, authored=copy.deepcopy(data), model_root=model_root, macro_root=macro_root, catalog_root=catalog_root, dbt_root=dbt_root, models=models, macros=macros, optimization=optimization,
                            documents=documents, sources=sources, source_paths=source_paths, classifications=registry, registry_path=registry_path, default_configs=rules)
    from slbt.catalog import definitions

    definitions(project)
    return project
