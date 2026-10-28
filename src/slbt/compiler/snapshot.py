"""Explicit-column dbt snapshots owned by models."""

from slbt.config import render_config
from slbt.sql import quoted


def compile_snapshot(model, *, pretty=False):
    snapshot = model.snapshot
    columns = ", ".join(quoted(name, model.dialect) for name in snapshot["columns"])
    return ("{% snapshot " + snapshot["name"] + " %}\n"
            + render_config(snapshot["config"], pretty=pretty)
            + "SELECT " + columns + " FROM {{ ref('" + model.name + "') }}\n"
            + "{% endsnapshot %}\n")
