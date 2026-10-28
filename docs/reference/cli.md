# CLI reference

```text
slbt [--version] [--project-dir PATH] COMMAND [OPTIONS]
```

Use `uv run slbt` from a development checkout. `slbt --help` and
`slbt COMMAND --help` provide the installed version's option syntax. Project
resolution and the case-sensitive `slbt_PROJECT_DIR` environment variable are
documented in [configuration](configuration.md).

## Commands and write boundaries

| Command | Main options | Effect without `--dry-run` |
| --- | --- | --- |
| `init [DIRECTORY]` | Default directory `.` | Creates a sample project; refuses existing canonical inputs. No dry-run option. |
| `validate` | None | Read-only offline validation of the whole project. |
| `inspect TABLE` | `--columns NAME ...`, `--optimize true\|false`, `--view sql\|plan` | Read-only expanded SQL/logical plan. No dry-run option. |
| `export dbt` | `--path PATH`, `--subpath FOLDER`, `--prune`, `--dry-run` | Reconciles generated dbt outputs. |
| `export catalog` | `--catalog NAME ...`, `--path PATH`, `--format xlsx\|csv\|json`, `--prune`, `--dry-run` | Reconciles generated catalogs. |
| `import PATH` | `--catalog NAME`, `--dry-run` | Writes canonical edits from XLSX or a CSV directory. JSON import is unsupported. |
| `optimize` | `-s/--select NAME ...`, `--format pretty\|compact`, `--preserve-marco true\|false`, `--enrich true\|false`, `--dry-run` | Rewrites selected canonical models; all models by default. |
| `enrich` | `-s/--select NAME ...`, `--dry-run` | Fills missing canonical types/descriptions from known lineage. |
| `sync-classifications` | `--dry-run` | Syncs Silver domains into Bronze in the registry. |

Options accepting names can be repeated or accept several space-separated names.
Import's `--catalog` accepts one name and is required when selection is ambiguous.
Boolean options take explicit `true` or `false`, rather than acting as toggles.

## Read-only investigation

```sh
uv run slbt --project-dir examples/jaffle_shop inspect gld_customers
uv run slbt --project-dir examples/jaffle_shop inspect gld_customer_leaderboard --columns ranked_revenue average_amount
uv run slbt --project-dir examples/jaffle_shop inspect gld_customer_leaderboard --columns ranked_revenue --view plan
uv run slbt --project-dir examples/jaffle_shop inspect gld_customers --optimize false
```

Inspection defaults to every final column, optimized SQL and `--view sql`. More
than one requested column produces one query in that order. SQL headers are
comments, so stdout can be redirected to a file. Logical source/ref leaves still
require physical bindings. A plan is not a warehouse execution plan.

Generated relation aliases use `a, b, c, …, z, aa, ab, …`, skipping collisions
and preserving authored aliases and output column names. SQL and plan views use
the same naming, including with `--optimize false`.

## Preview before writing

```sh
uv run slbt --project-dir examples/jaffle_shop export dbt --dry-run
uv run slbt --project-dir examples/jaffle_shop export dbt --subpath gld/optimization --dry-run
uv run slbt --project-dir examples/jaffle_shop export catalog --format json --dry-run
uv run slbt --project-dir examples/jaffle_shop import examples/jaffle_shop/catalog/catalog.xlsx --dry-run
uv run slbt --project-dir examples/jaffle_shop optimize -s gld_customer_lifetime_value gld_optimizer_constants --preserve-marco false --dry-run
uv run slbt --project-dir examples/jaffle_shop enrich -s slv_payments --dry-run
uv run slbt --project-dir examples/jaffle_shop sync-classifications --dry-run
```

`--dry-run` performs validation and reports file changes without writing. It is a
file-action preview, not a textual diff; inspect the Git diff after a deliberate
write in a working branch. Do not pass `--dry-run` to `validate` or `inspect`.

`--subpath` is a relative folder filter, not a model selector or dependency graph
selection. It does not automatically include upstream models; all inputs are
still validated. `--prune` permits deletion in managed scope, including files
you may have handwritten there. Use dedicated output directories.

Expected validation/filesystem failures return a nonzero CLI error. A successful
exit means that operation completed, not that data tests passed or every model
was optimized: runtime macro skips are reported separately on stderr.
