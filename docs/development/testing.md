# Testing

Run from the repository root after `uv sync --locked`:

```sh
uv run pytest
```

Pytest uses four workers by default. For easier debugging, run one file with
`uv run pytest -n 0 tests/test_documentation.py`. Tests that need DuckDB use the
development environment; a skipped optional dependency is not execution coverage.
Do not run dbt against the demo while pytest is running: read-only tests snapshot
the fixture tree, including any existing dbt logs and outputs.

## What the suite covers

- `test_slbt.py`: loading, validation, compilation, macros, catalogs, defaults,
  CLI selection, no-op imports, path protections and export preflight/pruning.
- `test_inspection.py`: column pruning and SQL semantics, including joins,
  DISTINCT, grouping, windows, cycles and ambiguous inputs.
- `test_jaffle_shop.py`: seeded results, independent business calculations,
  optimization equivalence and the committed workbook's contents.
- `test_yaml_backends.py`: supported Python/C YAML backend behavior.
- `test_yaml_style.py`: block-style repository and documentation YAML.
- `test_documentation.py`: local Markdown paths/anchors, parsed JSON examples,
  real README export excerpts and runnable documented authoring examples.
- `test_logo.py`: accessible, self-contained SVGs, consistent variant geometry
  and theme-aware README image references.
- `test_write_failures.py`: injected staging/replacement failures, rollback and
  cleanup behavior, including the nontransactional boundary of export writes.

Documentation checks are offline. They validate local targets and examples, not
the availability or content of external websites. Do not update example output
just to silence a failure without reviewing the semantic change.

## Executable integration checks

```sh
uv run slbt --project-dir examples/jaffle_shop validate
uv run slbt --project-dir examples/jaffle_shop export dbt
uv run slbt --project-dir examples/jaffle_shop export catalog --dry-run
uv run dbt build --project-dir examples/jaffle_shop/dbt --profiles-dir examples/jaffle_shop/dbt
uv run dbt docs generate --project-dir examples/jaffle_shop/dbt --profiles-dir examples/jaffle_shop/dbt
```

The handwritten `analytics/` fixture additionally runs `dbt parse`, `dbt seed`,
`dbt build` and `dbt test` with `--project-dir analytics --profiles-dir analytics`.
These checks do not replace adapter-specific production acceptance tests.

## Distribution checks

```sh
uv build
uv venv .venv-package-smoke
uv pip install --python .venv-package-smoke dist/slbt-0.2.0-py3-none-any.whl
```

Run the following with **that environment's Python**, not `uv run` (which would
select the checkout environment):

```sh
# POSIX
.venv-package-smoke/bin/python scripts/smoke_distribution.py
```

```powershell
# Windows PowerShell
.venv-package-smoke/Scripts/python.exe scripts/smoke_distribution.py
```

The script checks wheel/sdist license metadata and source documentation/fixtures,
then invokes the installed CLI from a temporary directory to initialize, validate
and export dbt/JSON. Repeat after installing the source archive to test sdist
installation too. The versioned wheel path above must match the version built.
CI uses an isolated environment and does not publish packages.

The package job also installs pytest and DuckDB into the wheel environment and
runs the full suite against freshly resolved runtime dependencies. This is
separate from the locked checkout tests: a simple initialization smoke test does
not exercise SQL optimization or prove the entire dependency range works.
