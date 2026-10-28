"""Registered physical sources and native dbt projection."""

import copy

from slbt.schema import SpecError, classification, fields, literal, mapping, text

GROUP = {"name", "tables", "layer", "domain", "description", "database", "schema", "loader", "quoting", "config"}
TABLE = {"name", "layer", "domain", "columns", "identifier", "data_type", "description", "config", "constraints", "docs", "data_tests", "quoting", "external"}
COLUMN = {"name", "data_type", "source_data_type", "description", "config", "constraints", "docs", "data_tests", "quote"}


def properties(data, allowed, context):
    mapping(data, context)
    fields(data, allowed, {"name"}, context)
    literal(data, context)
    for key, value in data.items():
        if key in {"name", "description", "database", "schema", "loader", "identifier", "data_type", "source_data_type"}:
            text(value, f"{context}.{key}")
        elif key in {"config", "quoting", "docs", "external"}:
            mapping(value, f"{context}.{key}")
        elif key in {"constraints", "data_tests"}:
            if not isinstance(value, list):
                raise SpecError(f"{context}.{key} must be a list")
        elif key == "quote" and type(value) is not bool:
            raise SpecError("source quote must be boolean")
    config = data.get("config", {})
    if "materialized" in config:
        raise SpecError("sources cannot declare config.materialized")
    if "enabled" in config and type(config["enabled"]) is not bool:
        raise SpecError("source config.enabled must be boolean")
    if "meta" in config:
        mapping(config["meta"], "source config.meta")
    for test in data.get("data_tests", []):
        if isinstance(test, str):
            text(test, context)
        elif isinstance(test, dict) and len(test) == 1:
            name, options = next(iter(test.items()))
            text(name, context)
            if options is not None:
                mapping(options, context)
        else:
            raise SpecError("source test must be a name or single-key mapping")


def validate(group):
    properties(group, GROUP, "source group")
    result = {k: copy.deepcopy(v) for k, v in group.items() if k not in {"tables", "layer", "domain"}}
    result["tables"] = {}
    seen = set()
    for table in group["tables"]:
        properties(table, TABLE, "source table")
        identity = group["name"] + "." + table["name"]
        if identity.casefold() in seen:
            raise SpecError("duplicate source table")
        seen.add(identity.casefold())
        for key in ("layer", "domain"):
            classification(table.get(key), f"{identity}.{key}")
        if table.get("config", {}).get("meta", {}).keys() & {"layer", "domain", "resource_name"}:
            raise SpecError("source designer fields must use dedicated properties")
        resolved = copy.deepcopy(table)
        resolved["columns"] = {}
        columns = table.get("columns", [])
        if not isinstance(columns, list):
            raise SpecError("source columns must be a list")
        column_names = set()
        for column in columns:
            properties(column, COLUMN, "source column")
            name = column["name"]
            if name.casefold() in column_names:
                raise SpecError("duplicate source column")
            column_names.add(name.casefold())
            if "source_data_type" in column.get("config", {}).get("meta", {}):
                raise SpecError("source_data_type must use its dedicated property")
            resolved["columns"][name] = {k: copy.deepcopy(v) for k, v in column.items() if k != "name"}
        result["tables"][identity] = resolved
    return result


def dbt_source(group):
    result = {k: copy.deepcopy(v) for k, v in group.items() if k != "tables"}
    result["tables"] = []
    for table in group["tables"].values():
        item = {k: copy.deepcopy(v) for k, v in table.items() if k not in {"layer", "domain", "columns"}}
        item.setdefault("config", {}).setdefault("meta", {}).update({k: table[k] for k in ("layer", "domain")})
        item["columns"] = []
        for name, column in table["columns"].items():
            value = {"name": name, **copy.deepcopy(column)}
            if "source_data_type" in value:
                value.setdefault("config", {}).setdefault("meta", {})["source_data_type"] = value.pop("source_data_type")
            item["columns"].append(value)
        result["tables"].append(item)
    return result
