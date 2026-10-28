"""Authored envelopes, classification inheritance, and whole-document edits."""

import copy
from dataclasses import dataclass
from pathlib import Path

from slbt.schema import SpecError, classification, dump_yaml, fields, load_yaml, mapping, text, version


@dataclass
class Document:
    path: Path
    authored: dict
    models: list[dict]
    sources: list[dict]
    content: bytes


def inherited(table, *parents):
    result = copy.deepcopy(table)
    for key in ("layer", "domain"):
        for scope in (table, *parents):
            if key in scope:
                result[key] = classification(scope[key], key)
                break
    return result


def load(path, overrides=None):
    data = load_yaml(path, overrides)
    fields(data, {"version", "layer", "domain", "tables", "sources"}, {"version"}, str(path))
    version(data["version"], str(path))
    for key in ("layer", "domain"):
        if key in data:
            classification(data[key], key)
    tables, groups = data.get("tables", []), data.get("sources", [])
    if not isinstance(tables, list) or not isinstance(groups, list):
        raise SpecError(f"{path}: tables and sources must be lists")
    models, sources, names, group_names = [], [], set(), set()
    for table in tables:
        mapping(table, "model table")
        if "version" in table:
            raise SpecError("version belongs to the resource document")
        name = text(table.get("name"), "model name")
        if name.casefold() in names:
            raise SpecError("duplicate model table")
        names.add(name.casefold())
        models.append({"version": 1, **inherited(table, data)})
    for group in groups:
        mapping(group, "source group")
        name = text(group.get("name"), "source group name")
        if name.casefold() in group_names:
            raise SpecError("duplicate source group")
        group_names.add(name.casefold())
        if not isinstance(group.get("tables"), list) or not group["tables"]:
            raise SpecError("source group tables must be nonempty")
        resolved = copy.deepcopy(group)
        resolved["tables"] = [inherited(mapping(table, "source table"), group, data) for table in group["tables"]]
        sources.append(resolved)
    if not models and not sources:
        raise SpecError(f"{path}: a resource document requires model or source tables")
    content = overrides[path] if overrides is not None and path in overrides else path.read_bytes()
    return Document(path, data, models, sources, content)


def _same_content(left, right):
    """Include mapping order, since projection and step ordering are meaningful."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return list(left) == list(right) and all(_same_content(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_content(a, b) for a, b in zip(left, right))
    return left == right


def render(document, models=None, sources=None, groups=None):
    """Replace selected identities only, retaining unchanged classification scopes."""
    data = copy.deepcopy(document.authored)
    models, sources, groups = models or {}, sources or {}, groups or {}
    for index, table in enumerate(data.get("tables", [])):
        if table["name"] not in models:
            continue
        result = copy.deepcopy(models[table["name"]])
        result.pop("version", None)
        for transformation in [*result.get("steps", {}).values(), result["output"]]:
            for relation in [transformation.get("from"), *transformation.get("joins", [])]:
                if isinstance(relation, dict) and isinstance(relation.get("source"), list):
                    relation["source"] = ".".join(relation["source"])
        for key in ("layer", "domain"):
            if key not in table and result.get(key) == data.get(key):
                result.pop(key, None)
        data["tables"][index] = result
    for group in data.get("sources", []):
        if group["name"] in groups:
            settings = groups[group["name"]]
            for key in list(group):
                if key not in {"name", "tables", "layer", "domain"}:
                    del group[key]
            group.update(copy.deepcopy(settings))
        for index, table in enumerate(group["tables"]):
            identity = group["name"] + "." + table["name"]
            if identity not in sources:
                continue
            result = copy.deepcopy(sources[identity])
            if isinstance(result.get("columns"), dict):
                result["columns"] = [{"name": n, **v} for n, v in result["columns"].items()]
            for key in ("layer", "domain"):
                if key not in table and result.get(key) == group.get(key, data.get(key)):
                    result.pop(key, None)
            group["tables"][index] = result
    return document.content if _same_content(data, document.authored) else dump_yaml(data, model=True)
