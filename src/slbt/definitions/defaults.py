"""Named conditional defaults, conflict detection, and authored reduction."""

import copy
import re

from slbt.schema import SpecError, fields, literal, mapping, text

MISSING = object()


def selector(value):
    text(value, "selector")
    if value.startswith("regex:"):
        if not value[6:]:
            raise SpecError("empty selector regex")
        try:
            re.compile(value[6:])
        except re.error as exc:
            raise SpecError(f"invalid selector regex: {exc}") from exc
    return value


def matches(actual, pattern):
    selector(pattern)
    return actual is not None and (bool(re.search(pattern[6:], actual)) if pattern.startswith("regex:") else actual == pattern)


def validate_target(target):
    values = target if isinstance(target, list) else [target]
    if not values:
        raise SpecError("target list must be nonempty")
    for value in values:
        mapping(value, "target")
        fields(value, {"layer", "domain", "tables"}, set(), "target")
        for key, patterns in value.items():
            patterns = patterns if key == "tables" and isinstance(patterns, list) else [patterns]
            if not patterns:
                raise SpecError("table selectors must be nonempty")
            for pattern in patterns:
                selector(pattern)


def validate(value):
    if value is None:
        return None
    mapping(value, "dbt.default_configs")
    fields(value, {"name", "configs", "snapshot_configs"}, set(), "dbt.default_configs")
    if not value.keys() & {"configs", "snapshot_configs"}:
        raise SpecError("default_configs requires configs and/or snapshot_configs")
    if "name" in value:
        text(value["name"], "defaults name")
    for group in ("configs", "snapshot_configs"):
        rules = value.get(group, [])
        if not isinstance(rules, list):
            raise SpecError(f"{group} must be a list")
        seen = set()
        for rule in rules:
            mapping(rule, "defaults rule")
            fields(rule, {"name", "target", "config"}, {"name", "target"}, "defaults rule")
            name = text(rule["name"], "rule name")
            if name in seen:
                raise SpecError(f"duplicate defaults rule: {name}")
            seen.add(name)
            validate_target(rule["target"])
            literal(mapping(rule.get("config", {}), name), name)
    return value


def applies(model, target):
    targets = target if isinstance(target, list) else [target]
    return any(all(any(matches(model["name"] if key == "tables" else model.get(key), p) for p in (patterns if isinstance(patterns, list) else [patterns])) for key, patterns in item.items()) for item in targets)


def merge(values, explicit=MISSING, *, context="config", names=()):
    """Every multiply defined leaf requires an explicit override, equal or not."""
    if explicit is not MISSING and not isinstance(explicit, dict):
        return copy.deepcopy(explicit)
    if values and not all(isinstance(value, dict) for value in values):
        if explicit is not MISSING:
            return copy.deepcopy(explicit)
        if len(values) > 1:
            raise SpecError(f"{context}: overlapping defaults {', '.join(names)} require an explicit override")
        return copy.deepcopy(values[0])
    keys = {key for value in values for key in value} | (set(explicit) if explicit is not MISSING else set())
    return {key: merge([v[key] for v in values if key in v], explicit.get(key, MISSING) if explicit is not MISSING else MISSING, context=f"{context}.{key}", names=names) for key in sorted(keys)}


def resolve(model, defaults, group="configs", explicit=MISSING):
    rules = [r for r in (defaults or {}).get(group, []) if applies(model, r["target"])]
    return merge([r.get("config", {}) for r in rules], explicit, context=f"{model['name']}.{'snapshot.config' if group == 'snapshot_configs' else 'config'}", names=[r["name"] for r in rules])


def equal(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    return a == b


def reduce(before, after, authored=MISSING):
    if equal(before, after):
        return copy.deepcopy(authored) if authored is not MISSING else MISSING
    if not isinstance(after, dict):
        return copy.deepcopy(after)
    result = {}
    for key, value in after.items():
        item = reduce(before.get(key, MISSING) if isinstance(before, dict) else MISSING, value, authored.get(key, MISSING) if isinstance(authored, dict) else MISSING)
        if item is not MISSING:
            result[key] = item
    return result
