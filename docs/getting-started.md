# Getting started

## Install from a checkout

Use Git, Python 3.11+ and [uv](https://docs.astral.sh/uv/) on Windows, macOS or Linux.
Clone the repository, then run the remaining commands from its root. See
[compatibility](compatibility.md) for CI coverage. Dependency installation requires
network access.

```sh
git clone https://github.com/billybillysss/slbt.git
cd slbt
uv sync --locked
uv run slbt --version
uv run slbt --help
```

The development environment includes pytest and the DuckDB adapter used by the
demo and handwritten integration fixture. A compiler-only
environment can use `uv sync --locked --no-dev`; it will not include the demo's
dbt adapter or pytest. These instructions install from source, not PyPI.

## Inspect without a database

```sh
uv run slbt --project-dir examples/jaffle_shop validate
uv run slbt --project-dir examples/jaffle_shop inspect gld_customers --columns customer_lifetime_value
uv run slbt --project-dir examples/jaffle_shop inspect gld_customers --columns customer_lifetime_value --view plan
```

Validation reports `Validated 21 models and 3 source tables offline`. Inspection
prints SQL or an offline logical plan; it never executes or changes authored
files. Logical source/ref leaves need physical bindings before execution.

## Generate the build outputs

```sh
uv run slbt --project-dir examples/jaffle_shop export dbt
uv run slbt --project-dir examples/jaffle_shop export catalog --format json
```

Look in:

- `examples/jaffle_shop/dbt/models/`: generated SQL, source definitions and dbt properties.
- `examples/jaffle_shop/dbt/macros/` and `dbt/snapshots/`: generated macro and snapshot files.
- `examples/jaffle_shop/catalog/catalog.json`: resolved semantic context for tools or AI assistants.

`export catalog` without `--format` regenerates the configured Excel workbook.
Import any edits you want to keep before exporting over it.

## Execute with dbt (optional)

```sh
uv run dbt build --project-dir examples/jaffle_shop/dbt --profiles-dir examples/jaffle_shop/dbt
uv run dbt docs generate --project-dir examples/jaffle_shop/dbt --profiles-dir examples/jaffle_shop/dbt
```

The same `dbt/` project contains the tracked configuration, profile, seeds and
handwritten tests alongside the generated subfolders. Export populates those
subfolders; it does not run dbt.

Only these separate dbt commands connect to a database. The demo uses bundled
seeds and local DuckDB, with no cloud credentials or dbt packages. Its default
`jaffle_shop.duckdb` is relative to your working directory; use `SLBT_DEMO_DB_PATH`
to choose another file. `dbt docs generate` consumes the descriptions and metadata
exported into dbt properties and may query the database catalog.

The [full walkthrough](../examples/jaffle_shop/README.md) explains the 21 models,
3 sources, customer snapshot and business-level checks. `analytics/` is a separate
handwritten integration fixture, not the export destination for this example.

## Start a separate project

```sh
uv run slbt init my_semantic_project
uv run slbt --project-dir my_semantic_project validate
uv run slbt --project-dir my_semantic_project export dbt --dry-run
```

Initialization creates a sample model, classifications registry and project
configuration without replacing existing canonical inputs. Its default dialect
is `spark`; set `optimization.dialect` for your intended engine before authoring
dialect-specific SQL. Configure your own dbt project and profile separately.

Next: [concepts](concepts.md), [configuration](reference/configuration.md),
[AI-assisted workflow](guides/ai-assisted-workflow.md) or
[troubleshooting](troubleshooting.md).
