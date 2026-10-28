# Contributing to slbt

slbt is a Semantic Layer Build Tool. Changes should keep transformation logic,
business metadata and compiled outputs inspectable, with dbt responsible for
database execution. Read the [concepts](docs/concepts.md) and
[architecture](docs/development/architecture.md) first.

## Set up and test

Use Python 3.11+ and [uv](https://docs.astral.sh/uv/), then run from the repository root:

```sh
uv sync --locked
uv run pytest
uv run slbt --project-dir examples/jaffle_shop validate
uv run slbt --project-dir examples/jaffle_shop export dbt --dry-run
uv run slbt --project-dir examples/jaffle_shop export catalog --dry-run
uv build
```

The suite includes documentation/link checks and DuckDB result-equivalence tests.
See [testing](docs/development/testing.md) for the executable dbt checks and
clean-install package smoke tests. Do not run write-mode optimization against
bundled teaching inputs just to test a change; copy them to a temporary project.

## Propose a change

- For significant behavior/schema changes, open an issue describing the use case
  before building an incompatible API. Small documentation fixes can go straight
  to a pull request.
- Keep changes focused. Include a minimal reproduction and regression test for
  bugs; include expected inputs/outputs for new features.
- Preserve output-column order, declared metadata, validation-before-write
  behavior, unchanged-file bytes and managed-path protections.
- Prefer explicit errors to guessed schema information. No warehouse access may
  be introduced into offline compiler commands without an explicit design change.
- Update relevant docs and [CHANGELOG](CHANGELOG.md) for user-visible changes.
  Follow [compatibility](docs/compatibility.md) for schema/API changes.
- Write block-style YAML and plain GitHub-readable Markdown. Link reference docs
  rather than duplicating their full content in the README.
- Do not commit credentials, local databases, dbt logs/targets or generated demo
  dbt output. The example workbook and seed data are intentional fixtures.

In your pull request, state what changed, why, commands tested, limitations and
any migration required. If AI helped, review its output and verify business
assumptions yourself; do not submit untested generated logic as verified behavior.

## Report bugs and security issues

For ordinary bugs, include slbt/Python/OS versions, the exact command, a minimal
redacted project and expected versus actual behavior. Never post private
catalogs, credentials or production data. Use the [security policy](SECURITY.md)
for potential vulnerabilities rather than disclosing exploit details publicly.

## License

Contributions to slbt code and documentation are under the [MIT license](LICENSE).
Preserve third-party notices and the separate Jaffle Shop seed attribution.
