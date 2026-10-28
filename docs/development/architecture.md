# Architecture

slbt is an offline compiler with explicit file mutation boundaries. The CLI does
not run dbt or access a warehouse.

## Main modules

| Module | Responsibility |
| --- | --- |
| `src/slbt/cli.py` | Click command orchestration, summaries and expected error reporting. |
| `schema.py`, `paths.py`, `config.py` | Strict YAML/literal loading, portable path checks and config rendering. |
| `project.py`, `definitions/` | Load canonical resources, classifications/defaults, resolve models and validate a project. |
| `sql.py`, `macros.py` | SQLGlot parsing/rendering, logical relations and static/runtime macro handling. |
| `compiler/` | Pure dbt model/property/snapshot renderers. |
| `inspection.py` | Read-only expanded query and selected-column logical inspection. |
| `optimize.py`, `enrich.py` | Proposed canonical SQL/metadata rewrites with validation. |
| `catalog/export.py` | Pure XLSX/CSV/resolved JSON output generation. |
| `catalog/review.py` | Editable catalog validation/import and staged canonical writes. |
| `export.py` | Generated-file comparison, preflight, writes and explicit optional pruning. |
| `scaffold.py` | Initialize a small project without replacing existing inputs. |

Paths above are relative to `src/slbt/` unless fully qualified.

## Build and write flow

1. Resolve `slbt_project.yaml` and validate configured paths.
2. Load strict versioned YAML, classifications, defaults, sources and macros.
3. Resolve models, SQL structure, ordered columns and inferred dependencies.
4. Produce inspection output, proposed canonical changes or generated artifacts.
5. Validate proposed changes/output paths before applying writes.

A loaded `Project` is an operation-local snapshot. Reload after canonical edits;
do not reuse it as though it were a live view of the filesystem.

Canonical import/optimization/enrichment stage files and attempt rollback after
replacement errors. Output reconciliation writes/deletes individual files after
preflight. Classification sync uses a direct write. None of these provides a
crash-safe multi-file transaction or a concurrency lock.

## Design invariants

- Offline commands never infer unknown physical schemas by querying a warehouse.
- Canonical inputs are distinct from generated outputs; dependencies come from logic.
- Explicit columns and order survive compilation and supported optimization.
- Metadata, constraints, tests and configuration remain attached to definitions.
- Unchanged canonical documents retain original bytes; rewritten YAML is normalized.
- Runtime macro limitations are explicit, not silently substituted.
- dbt owns profiles, materialization, snapshots, data tests and execution.

The independent `analytics/` project is a handwritten integration fixture.
`examples/jaffle_shop/` is the canonical executable demo; never export it over
`analytics/`.
