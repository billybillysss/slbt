"""Copy the runnable example without generated or local runtime artifacts."""

from pathlib import Path
import shutil

import pytest


EXAMPLE = Path(__file__).resolve().parents[1] / "examples/jaffle_shop"


@pytest.fixture
def examples(tmp_path):
    def ignored(directory, names):
        skipped = set(shutil.ignore_patterns("target", "logs", "dbt_packages", "*.duckdb", "*.duckdb.wal", ".user.yml")(directory, names))
        if Path(directory) == EXAMPLE:
            skipped.update({"catalog", "demo"})
        elif Path(directory) == EXAMPLE / "dbt":
            skipped.update({"models", "macros", "snapshots"})
        return skipped

    destination = tmp_path / "examples"
    shutil.copytree(EXAMPLE, destination, ignore=ignored)
    return destination
