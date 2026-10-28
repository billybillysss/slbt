"""Create a small version-one project without replacing existing inputs."""

from pathlib import Path

from slbt.paths import safe_path
from slbt.schema import SpecError, dump_yaml


def initialize(directory):
    directory = Path(directory).absolute()
    safe_path(directory)
    for name in ("slbt_project.yaml", "models", "macros"):
        if (directory / name).exists():
            raise SpecError(f"initialization refuses to replace {directory / name}")
    project = {"version": 1, "model_path": "models/", "macro_path": "macros/", "catalog_path": "catalog/", "optimization": {"dialect": "spark", "format": "pretty", "preserve_macro": True, "enrich": False}, "dbt": {"path": "dbt/", "format": "pretty"}, "catalog": [{"name": "catalog", "sheets": [{"name": "Models", "type": "model"}]}]}
    registry = {"version": 1, "layers": [{"name": "silver", "description": "Curated models.", "domains": [{"name": "example", "description": "Example domain."}]}]}
    model = {"version": 1, "layer": "silver", "domain": "example", "tables": [{"name": "example", "output": {"sql": "SELECT 1 AS example_id", "columns": {"example_id": {"data_type": "bigint"}}}}]}
    (directory / "models").mkdir(parents=True)
    (directory / "macros").mkdir()
    (directory / "slbt_project.yaml").write_bytes(dump_yaml(project))
    (directory / "models" / "classifications.yaml").write_bytes(dump_yaml(registry))
    (directory / "models" / "example.yml").write_bytes(dump_yaml(model))
