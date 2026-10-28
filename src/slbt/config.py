"""Strict configuration loading and native dbt literal rendering."""

import re

from slbt.schema import literal, load_yaml


def render_literal(value, *, pretty=False, indent=0):
    literal(value)
    if isinstance(value, str):
        match = re.fullmatch(r"\s*\{\{\s*(.*?)\s*\}\}\s*", value, re.S)
        return match.group(1) if match else repr(value)
    if isinstance(value, dict):
        opening, closing = "{", "}"
        items = [repr(k) + ": " + render_literal(v, pretty=pretty, indent=indent + 2) for k, v in value.items()]
    elif isinstance(value, list):
        opening, closing = "[", "]"
        items = [render_literal(v, pretty=pretty, indent=indent + 2) for v in value]
    else:
        return repr(value)
    if pretty and items:
        padding = " " * (indent + 2)
        return opening + "\n" + padding + (",\n" + padding).join(items) + "\n" + " " * indent + closing
    return opening + ", ".join(items) + closing


def render_config(value, *, pretty=False):
    if not value:
        return ""
    if pretty:
        return "{{\n  config(" + render_literal(value, pretty=True, indent=2) + ")\n}}\n\n"
    return "{{ config(" + render_literal(value) + ") }}\n\n"


__all__ = ["load_yaml", "render_literal", "render_config"]
