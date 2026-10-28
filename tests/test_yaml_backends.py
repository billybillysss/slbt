"""Safe YAML behavior with LibYAML present, absent, or partially available."""

import importlib.util
from pathlib import Path

import pytest
import yaml

from slbt import schema


@pytest.fixture(params=[(), ("CSafeLoader",), ("CSafeDumper",), ("CSafeLoader", "CSafeDumper")],
                ids=["available", "no-c-loader", "no-c-dumper", "no-libyaml"])
def backend(request, monkeypatch):
    for name in request.param:
        monkeypatch.delattr(yaml, name, raising=False)
    # Load a separate module so existing imports keep their classes and SpecError.
    spec = importlib.util.spec_from_file_location("yaml_backend_under_test", schema.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load(backend, content):
    path = Path("in-memory.yaml")
    return backend.load_yaml(path, {path: content})


def test_selects_available_safe_backends(backend):
    assert backend.UniqueLoader.__bases__ == (getattr(yaml, "CSafeLoader", yaml.SafeLoader),)
    assert backend.LiteralDumper.__bases__ == (getattr(yaml, "CSafeDumper", yaml.SafeDumper),)


def test_custom_boolean_rules_do_not_change_pyyaml(backend):
    content = "on: yes\nvalue: TRUE\nother: false\nword: OFF\n"
    assert load(backend, content) == {"on": "yes", "value": True, "other": False, "word": "OFF"}
    assert yaml.safe_load("value: yes") == {"value": True}
    assert yaml.safe_load("value: 1\nvalue: 2") == {"value": 2}


@pytest.mark.parametrize("content", [
    "value: 1\nvalue: 2",
    "nested:\n  value: 1\n  value: 2",
    "1: value",
    "true: value",
    "? [a, b]\n: value",
    "value: !!python/object/apply:builtins.str [unsafe]",
    "value: [unclosed",
    "value: 1\n---\nother: 2",
    "",
    "- not-a-mapping",
    "value: \x00",
])
def test_rejects_invalid_or_unsafe_yaml(backend, content):
    with pytest.raises(backend.SpecError):
        load(backend, content)


def test_dump_preserves_order_unicode_and_literal_sql(backend):
    value = {
        "sql": "SELECT id, amount\nFROM orders\n",
        "description": "Caf\u00e9 \u4e2d\u6587 \U0001f600",
        "strings": ["on", "yes", "true", "null", "01", "2026-10-27"],
        "nested": {"enabled": True, "empty": [], "missing": None},
    }
    rendered = backend.dump_yaml(value)
    assert rendered.startswith(b"sql: |\n  SELECT id, amount\n  FROM orders\n")
    assert list(load(backend, rendered)) == list(value)
    assert load(backend, rendered) == value
    assert backend.dump_yaml(load(backend, rendered)) == rendered


def test_c_dumper_preserves_unicode_line_separators(backend):
    if backend.LiteralDumper.__bases__ == (yaml.SafeDumper,):
        pytest.skip("The Python emitter has a pre-existing NEL round-trip limitation")
    value = {"description": "NEL\u0085line\u2028paragraph\u2029end"}
    assert load(backend, backend.dump_yaml(value)) == value


def test_model_dump_indents_lists_and_leaves_on_unquoted(backend):
    value = {"tables": [{"name": "example", "constraints": [{"type": "primary_key", "columns": ["id"]}],
                         "output": {"joins": [{"on": "a.id = b.id"}]}}]}
    rendered = backend.dump_yaml(value, model=True)
    assert b"tables:\n  - name: example\n    constraints:\n      - type: primary_key\n        columns:\n          - id\n" in rendered
    assert b"        - on: a.id = b.id\n" in rendered
    assert b"'on':" not in rendered
    assert load(backend, rendered) == value
    assert backend.dump_yaml(load(backend, rendered), model=True) == rendered


def test_model_dump_round_trips_boolean_like_strings_and_sql(backend):
    value = {"on": "yes", "off": "no", "boolean": True, "false": "false", "TRUE": "TRUE",
             "strings": ["null", "01", "2026-10-27"], "description": "Caf\u00e9 \u4e2d\u6587 \U0001f600",
             "sql": "SELECT id\nFROM items\n"}
    rendered = backend.dump_yaml(value, model=True)
    assert rendered.startswith(b"on: yes\noff: no\nboolean: true\n")
    assert b"sql: |\n  SELECT id\n  FROM items\n" in rendered
    assert load(backend, rendered) == value
    # dbt and other non-model outputs still use standard PyYAML string quoting.
    generic = backend.dump_yaml(value)
    assert b"'on': 'yes'" in generic
    assert yaml.safe_load(generic) == value


def test_model_dump_preserves_unicode_line_separators(backend):
    value = {"description": "NEL\u0085line\u2028paragraph\u2029end"}
    assert load(backend, backend.dump_yaml(value, model=True)) == value
