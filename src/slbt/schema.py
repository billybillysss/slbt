"""Canonical YAML parsing, with no database or dbt-project dependency."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import yaml


class SpecError(ValueError):
    """An invalid mapping, catalog, or unsafe filesystem operation."""


class UniqueLoader(getattr(yaml, "CSafeLoader", yaml.SafeLoader)):
    pass


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str):
            raise SpecError("YAML mapping keys must be strings")
        try:
            if key in result:
                raise SpecError(f"duplicate YAML key: {key!r}")
            result[key] = loader.construct_object(value_node, deep=True)
        except TypeError as exc:
            raise SpecError("YAML keys must be scalar values") from exc
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)
UniqueLoader.yaml_implicit_resolvers = {
    key: [(tag, pattern) for tag, pattern in values if tag != "tag:yaml.org,2002:bool"]
    for key, values in UniqueLoader.yaml_implicit_resolvers.items()
}
UniqueLoader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$", re.I), list("tTfF"))
IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
LAYERS = {"bronze", "silver", "gold"}


def mapping(value: Any, context: str) -> dict:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise SpecError(f"{context}: expected a mapping with string keys")
    return value


def fields(value: dict, allowed: set, required: set, context: str) -> None:
    unknown, missing = value.keys() - allowed, required - value.keys()
    if unknown or missing:
        raise SpecError(f"{context}: unknown fields {sorted(map(str, unknown))}; missing fields {sorted(missing)}")


def text(value: Any, context: str, *, empty=False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise SpecError(f"{context}: expected a {'string' if empty else 'nonempty string'}")
    return value


def literal(value, context="configuration"):
    """Validate the portable, finite YAML/JSON literal subset recursively."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, list):
        for item in value:
            literal(item, context)
        return value
    if isinstance(value, dict):
        mapping(value, context)
        for item in value.values():
            literal(item, context)
        return value
    raise SpecError(f"{context}: expected finite configuration literals")


def classification(value, context):
    text(value, context)
    if value != value.strip():
        raise SpecError(f"{context}: surrounding whitespace is not allowed")
    return value


def identifier(value: Any, context: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise SpecError(f"{context}: expected a lowercase SQL identifier")
    return value


def version(value: Any, context: str) -> None:
    if type(value) is not int or value != 1:
        raise SpecError(f"{context}: version must be integer 1")


def load_yaml(path: Path, overrides: dict | None = None) -> dict:
    try:
        content = overrides[path] if overrides is not None and path in overrides else path.read_bytes()
        return mapping(yaml.load(content, Loader=UniqueLoader), str(path))
    except (OSError, yaml.YAMLError, UnicodeError) as exc:
        raise SpecError(f"{path}: {exc}") from exc


class LiteralDumper(getattr(yaml, "CSafeDumper", yaml.SafeDumper)):
    pass


def _string(dumper, value):
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|" if "\n" in value else None)


LiteralDumper.add_representer(str, _string)


class ModelDumper(yaml.SafeDumper):
    """Match canonical boolean rules and indent nested lists in model documents."""

    yaml_implicit_resolvers = UniqueLoader.yaml_implicit_resolvers

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, indentless=False)


def _model_string(dumper, value):
    # The Python emitter folds bare NEL characters into spaces, even in blocks.
    if "\u0085" in value:
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style='"')
    return _string(dumper, value)


ModelDumper.add_representer(str, _model_string)


def dump_yaml(value: dict, *, model=False) -> bytes:
    dumper = ModelDumper if model else LiteralDumper
    return yaml.dump(value, Dumper=dumper, sort_keys=False, allow_unicode=True).encode("utf-8")


def yaml_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.suffix.lower() in {".yml", ".yaml"})


def relation(value: Any, context: str, *, alias=True) -> dict:
    if isinstance(value, str):
        return from_dotted(value, context)
    value = mapping(value, context)
    fields(value, {"step", "ref", "source"} | ({"alias"} if alias else set()), set(), context)
    kinds = value.keys() & {"step", "ref", "source"}
    if len(kinds) != 1:
        raise SpecError(f"{context}: specify exactly one step, ref, or source")
    kind = next(iter(kinds))
    result = dict(value)
    if kind == "source":
        parts = value[kind].split(".") if isinstance(value[kind], str) else None
        if not isinstance(parts, list) or len(parts) != 2:
            raise SpecError(f"{context}: source requires name.table")
        result[kind] = [identifier(p, context) for p in parts]
    else:
        identifier(value[kind], context)
    if "alias" in value:
        identifier(value["alias"], context)
    return result


def relation_key(value: dict) -> str:
    return json.dumps({k: v for k, v in value.items() if k != "alias"}, sort_keys=True)


def dotted(value: dict) -> str:
    kind = next(k for k in ("step", "ref", "source") if k in value)
    return kind + "." + (".".join(value[kind]) if kind == "source" else value[kind])


def from_dotted(value: str, context: str) -> dict:
    parts = value.split(".")
    if len(parts) == 2 and parts[0] in {"ref", "step"}:
        return relation({parts[0]: parts[1]}, context, alias=False)
    if len(parts) == 3 and parts[0] == "source":
        return relation({"source": ".".join(parts[1:])}, context, alias=False)
    raise SpecError(f"{context}: expected step.name, ref.name, or source.name.table")


def tests(value: Any, context: str) -> list:
    if not isinstance(value, list):
        raise SpecError(f"{context}: data_tests must be a list")
    for test in value:
        if isinstance(test, str):
            text(test, context)
        elif isinstance(test, dict) and len(test) == 1:
            name, options = next(iter(test.items()))
            text(name, context)
            mapping(options, context)
            for key in ("arguments", "config"):
                if key in options:
                    mapping(options[key], context)
        else:
            raise SpecError(f"{context}: each test must be a name or single-key mapping")
    return value


def constraints(value: Any, context: str, *, model=False) -> list:
    if not isinstance(value, list):
        raise SpecError(f"{context}: constraints must be a list")
    for rule in value:
        mapping(rule, context)
        literal(rule, context)
        text(rule.get("type"), context)
        for key in ("name", "expression", "to"):
            if key in rule:
                text(rule[key], context)
        for key in ("columns", "to_columns"):
            if key in rule:
                names = rule[key]
                if not isinstance(names, list) or not names or any(not isinstance(n, str) for n in names) or len(set(names)) != len(names):
                    raise SpecError(f"{context}: {key} requires distinct column names")
                for name in names:
                    identifier(name, context)
        if not model and "columns" in rule:
            raise SpecError(f"{context}: columns belongs on model constraints")
        if model and rule["type"] == "primary_key" and not rule.get("columns"):
            raise SpecError(f"{context}: model constraint requires columns")
        for key in ("warn_unenforced", "warn_unsupported"):
            if key in rule and type(rule[key]) is not bool:
                raise SpecError(f"{context}: {key} must be boolean")
    return value


def compact_columns(value: dict) -> dict:
    """Use scalar shorthand only when there is no column metadata to preserve."""
    return {name: column["expression"] if isinstance(column, dict) and column.keys() == {"expression"} else column for name, column in value.items()}


def columns(value: Any, context: str, *, structured: bool, output: bool) -> dict:
    mapping(value, context)
    if not value:
        raise SpecError(f"{context}: columns must be nonempty")
    result = {}
    for name, definition in value.items():
        identifier(name, context)
        definition = {"expression": definition} if isinstance(definition, str) else definition
        mapping(definition, context)
        fields(definition, {"expression", "description", "data_type", "config", "constraints", "data_tests"}, {"expression"} if structured else set(), context)
        if not structured and "expression" in definition:
            raise SpecError(f"{context}: raw SQL columns are metadata only")
        for key in ("expression", "description", "data_type"):
            if key in definition:
                if key != "expression" and definition[key] is None:
                    continue
                text(definition[key], context, empty=key != "expression")
        if "config" in definition:
            mapping(definition["config"], context)
            literal(definition["config"], context)
            if "meta" in definition["config"]:
                mapping(definition["config"]["meta"], context)
                business = definition["config"]["meta"].get("business_key")
                if "business_key" in definition["config"]["meta"] and type(business) is not bool:
                    raise SpecError(f"{context}: business_key must be boolean")
        if not output and ("data_tests" in definition or "constraints" in definition):
            raise SpecError(f"{context}: tests and constraints belong only to final output columns")
        tests(definition.get("data_tests", []), context)
        constraints(definition.get("constraints", []), context)
        result[name] = dict(definition)
    return result
