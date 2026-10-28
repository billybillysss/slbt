"""Readable dbt configuration without changing native Jinja values."""

import pytest
from jinja2 import Environment

from slbt.config import render_config, render_literal


def test_pretty_config_layout():
    value = {"materialized": "view", "meta": {"owner": "payments", "domain": "sales"}}
    assert render_config(value, pretty=True) == """{{
  config({
    'materialized': 'view',
    'meta': {
      'owner': 'payments',
      'domain': 'sales'
    }
  })
}}

"""
    assert render_config(value) == "{{ config({'materialized': 'view', 'meta': {'owner': 'payments', 'domain': 'sales'}}) }}\n\n"


@pytest.mark.parametrize("pretty", [False, True])
def test_config_preserves_native_values_and_expressions(pretty):
    value = {
        "enabled": True,
        "meta": {
            "values": [None, False, 2, 1.5, {"quoted": "owner's \\ path\nnext line"}],
            "empty_dict": {},
            "empty_list": [],
        },
        "tags": ["payments", "{{ var('domain') }}"],
        "schema": "{{ var('schema') }}",
    }
    rendered = render_config(value, pretty=pretty)
    received = []
    Environment().from_string(rendered).render(config=received.append, var=lambda name: "resolved_" + name)
    assert received == [{**value, "tags": ["payments", "resolved_domain"], "schema": "resolved_schema"}]
    expected_list = "'values': [\n        None,\n        False," if pretty else "'values': [None, False,"
    assert expected_list in rendered


@pytest.mark.parametrize("pretty", [False, True])
def test_empty_config_omitted(pretty):
    assert render_config({}, pretty=pretty) == ""
    assert render_literal({}, pretty=pretty) == "{}"
    assert render_literal([], pretty=pretty) == "[]"
