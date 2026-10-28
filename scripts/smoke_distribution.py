"""Check built archives and exercise an isolated installed CLI, without dbt."""

import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def check_archives():
    version = importlib.metadata.version("slbt")
    wheel = ROOT / "dist" / f"slbt-{version}-py3-none-any.whl"
    source = ROOT / "dist" / f"slbt-{version}.tar.gz"
    license_text = (ROOT / "LICENSE").read_bytes()
    with zipfile.ZipFile(wheel) as archive:
        metadata = archive.read(f"slbt-{version}.dist-info/METADATA").decode()
        assert "License-Expression: MIT\n" in metadata
        assert "License-File: LICENSE\n" in metadata
        assert archive.read(f"slbt-{version}.dist-info/licenses/LICENSE") == license_text
    with tarfile.open(source) as archive:
        prefix = f"slbt-{version}/"
        names = set(archive.getnames())
        metadata = archive.extractfile(prefix + "PKG-INFO").read().decode()
        assert "License-Expression: MIT\n" in metadata
        assert "License-File: LICENSE\n" in metadata
        for path in (
            "LICENSE", "README.md", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md",
            "pyproject.toml", "uv.lock", "docs/index.md", "tests/test_documentation.py",
            "scripts/smoke_distribution.py", "examples/jaffle_shop/slbt_project.yaml",
            "examples/jaffle_shop/dbt/dbt_project.yml",
            "examples/jaffle_shop/dbt/profiles.yml",
            "examples/jaffle_shop/dbt/seeds/LICENSE",
            "examples/jaffle_shop/dbt/seeds/README.md",
            "examples/jaffle_shop/dbt/seeds/raw_customers.csv",
            "examples/jaffle_shop/dbt/seeds/raw_orders.csv",
            "examples/jaffle_shop/dbt/seeds/raw_payments.csv",
            "examples/jaffle_shop/dbt/tests/jaffle_shop_reconciliation.sql",
            "docs/logo.md", "tests/test_logo.py",
            "docs/assets/slbt-logo.svg", "docs/assets/slbt-logo-dark.svg",
            "docs/assets/slbt-mark.svg", "docs/assets/slbt-mark-dark.svg",
            "docs/assets/slbt-mark-mono.svg",
        ):
            assert prefix + path in names, f"sdist missing {path}"
        assert archive.extractfile(prefix + "LICENSE").read() == license_text
        assert not any(
            part in {".venv", "target", "logs", "__pycache__"} or part.endswith((".duckdb", ".duckdb.wal"))
            for name in names for part in Path(name).parts
        ), "sdist includes local build/runtime artifacts"
        for category in ("models", "macros", "snapshots"):
            assert not any(name.startswith(prefix + f"examples/jaffle_shop/dbt/{category}/") for name in names)
        assert not any(name.startswith(prefix + "examples/jaffle_shop/demo/") for name in names)
    return version


def smoke_cli(version):
    with tempfile.TemporaryDirectory(prefix="slbt-package-smoke-") as directory:
        def run(*arguments):
            result = subprocess.run(
                [sys.executable, "-I", "-m", "slbt", *arguments], cwd=directory,
                text=True, capture_output=True,
            )
            print(result.stdout.strip())
            if result.returncode:
                print(result.stderr, file=sys.stderr)
                result.check_returncode()
            return result.stdout

        assert version in run("--version")
        run("init", "project")
        run("--project-dir", "project", "validate")
        run("--project-dir", "project", "export", "dbt")
        run("--project-dir", "project", "export", "catalog", "--format", "json")
        project = Path(directory) / "project"
        assert (project / "dbt/models/example.sql").is_file()
        catalog = json.loads((project / "catalog/catalog.json").read_text(encoding="utf-8"))
        assert (catalog["format"], catalog["format_version"], catalog["representation"]) == ("slbt.catalog", 1, "resolved")
        assert catalog["models"][0]["name"] == "example"


if __name__ == "__main__":
    smoke_cli(check_archives())
    print("Distribution checks passed")
