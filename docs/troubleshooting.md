# Troubleshooting

## No `slbt_project.yaml` found

Run from the right directory or use
`uv run slbt --project-dir examples/jaffle_shop validate`. The global option comes
before the command. The environment variable is exactly `slbt_PROJECT_DIR`.
Create your own project with `slbt init DIRECTORY`, not by exporting into inputs.

## `uv sync --locked` fails

Check Python 3.11+, network access and the actual dependency-resolution error.
Do not bypass the lock silently in CI. After an intentional dependency/metadata
change, maintainers run `uv lock` and review the diff. A compiler-only environment
can use `--no-dev`, but cannot run the adapter-dependent demo/tests without their
dependencies.

## Unknown classification or field

Register the exact layer/domain in the configured model root's
`classifications.yaml`. Use canonical version-1 fields and block-style YAML.
Duplicate keys are errors. The setting is spelled `preserve_marco`, not
`preserve_macro` or `preserve_macros`. See [configuration](reference/configuration.md).

## Inspection cannot resolve a query

Check explicit column names, relation aliases and loaded upstream definitions.
Runtime-dependent macros cannot expand offline. Ambiguous columns, cycles and
unsafe relation modifiers report errors rather than using warehouse guesses.
Even successful SQL output retains logical source/external-ref leaves; bind them
or use generated dbt models to execute. A logical plan is not warehouse EXPLAIN.

## Optimization succeeds but a model is unchanged

It may already be normalized or depend on a runtime macro. Read stderr for skips.
Other eligible models can still be optimized. A successful exit does not mean
every model changed, every metadata property resolved, or a speedup was measured.

## Excel import/export fails

Close Excel first to release file locks. Use plain-text SQL/JSON, not formulas or
error cells. Preserve identities, selected resources, hierarchy and output order.
Select `--catalog NAME` when ambiguous. Import CSV as a directory and never try
to import resolved JSON. See the [round-trip guide](guides/catalog-round-trip.md).

## Stale generated files remain

Default exports do not delete them. Review an intentional `--prune --dry-run`
before using `--prune`, or export into a clean dedicated build directory. Prune
may remove handwritten files within managed scope. Do not mix ownership.

## dbt cannot find generated models or macros

Export first, then check dbt's model/macro/snapshot paths and name collisions.
slbt does not create profiles, download dbt packages or execute dbt. Use the
[integration guide](guides/existing-dbt-project.md). The demo's project/profile
directory is `examples/jaffle_shop/dbt`. Its tracked configuration uses the local
generated `models/`, `macros/` and `snapshots/` subfolders.

## A command failed while writing

Stop other writers. Review Git diffs and canonical inputs, restore only changes
you deliberately choose from version control/backups, revalidate, then regenerate
outputs in a clean directory. Do not assume all mutations rolled back after a
filesystem error or process interruption. See the
[write-boundary checklist](guides/production-workflow.md#file-mutation-and-deployment).

For an issue, include `slbt --version`, Python/OS, the command, the error and a
minimal redacted reproduction. Do not attach credentials, private catalog data
or live database files. Follow [security reporting](../SECURITY.md) for vulnerabilities.
